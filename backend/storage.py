"""
Storage for Pulse.

If MONGODB_URI is set and reachable, everything is stored in MongoDB Atlas.
Otherwise it falls back to memory so the app still runs (data is lost on restart).

Collections
  payments   one document per payment request, with live check states
  sessions   one document per unlock of the protected site (start, end, why it ended)
  receipts   append-only, hash-chained log of every finished payment and every access event

Each receipt stores the hash of the previous one, so editing or deleting any receipt
breaks the chain and /api/receipts/verify reports where.
"""

import copy
import hashlib
import json
import logging
import os
import threading
from typing import Optional

log = logging.getLogger("pulse.storage")

HIDDEN = {"_id": 0, "created_ts": 0}
GENESIS = "0" * 64


def receipt_hash(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _clean(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    return {k: copy.deepcopy(v) for k, v in doc.items() if k not in ("_id", "created_ts")}


# ---------------------------------------------------------------------------
# In-memory fallback
# ---------------------------------------------------------------------------


class MemoryStore:
    kind = "memory"

    def __init__(self, error: Optional[str] = None):
        self.error = error
        self._payments: dict[str, dict] = {}
        self._sessions: dict[str, dict] = {}
        self._receipts: list[dict] = []
        self._lock = threading.Lock()

    # payments
    def create_payment(self, p: dict) -> dict:
        with self._lock:
            self._payments[p["id"]] = copy.deepcopy(p)
            return _clean(self._payments[p["id"]])

    def get_payment(self, pid: str) -> Optional[dict]:
        with self._lock:
            return _clean(self._payments.get(pid))

    def list_payments(self, limit: int) -> list[dict]:
        with self._lock:
            items = sorted(self._payments.values(), key=lambda p: p["created_ts"], reverse=True)[:limit]
            return [_clean(p) for p in items]

    def update_pending(self, pid: str, fields: dict) -> Optional[dict]:
        """Apply $set-style fields (dotted keys allowed) only if the payment is still pending."""
        with self._lock:
            p = self._payments.get(pid)
            if p is None or p["status"] != "pending":
                return None
            self._apply(p, fields)
            return _clean(p)

    def set_fields(self, pid: str, fields: dict) -> None:
        with self._lock:
            if pid in self._payments:
                self._apply(self._payments[pid], fields)

    def pending_older_than(self, cutoff_ts: float) -> list[str]:
        with self._lock:
            return [p["id"] for p in self._payments.values() if p["status"] == "pending" and p["created_ts"] < cutoff_ts]

    # sessions
    def create_session(self, doc: dict) -> None:
        with self._lock:
            self._sessions[doc["session_id"]] = copy.deepcopy(doc)

    def update_session(self, sid: str, fields: dict) -> None:
        with self._lock:
            if sid in self._sessions:
                self._sessions[sid].update(fields)

    def list_sessions(self, limit: int) -> list[dict]:
        with self._lock:
            items = sorted(self._sessions.values(), key=lambda d: d.get("started_at") or "", reverse=True)
            return [copy.deepcopy(d) for d in items[:limit]]

    # receipts
    def last_receipt(self) -> Optional[dict]:
        with self._lock:
            return copy.deepcopy(self._receipts[-1]) if self._receipts else None

    def insert_receipt(self, r: dict) -> None:
        with self._lock:
            self._receipts.append(copy.deepcopy(r))

    def list_receipts(self, limit: int, ascending: bool = False) -> list[dict]:
        with self._lock:
            items = self._receipts if ascending else list(reversed(self._receipts))
            return [copy.deepcopy(r) for r in items[:limit]]

    @staticmethod
    def _apply(doc: dict, fields: dict) -> None:
        for key, value in fields.items():
            target = doc
            parts = key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value


# ---------------------------------------------------------------------------
# MongoDB Atlas
# ---------------------------------------------------------------------------


class MongoStore:
    kind = "mongodb"
    error = None

    def __init__(self, uri: str, db_name: str):
        import certifi
        from pymongo import ASCENDING, DESCENDING, MongoClient

        self._asc, self._desc = ASCENDING, DESCENDING
        self.client = MongoClient(uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=6000)
        self.client.admin.command("ping")  # fail fast if Atlas is unreachable
        db = self.client[db_name]
        self.payments = db["payments"]
        self.sessions = db["sessions"]
        self.receipts = db["receipts"]

        self.payments.create_index("id", unique=True)
        self.payments.create_index([("created_ts", DESCENDING)])
        self.payments.create_index([("status", ASCENDING), ("created_ts", ASCENDING)])
        self.sessions.create_index("session_id", unique=True)
        self.sessions.create_index([("started_at", DESCENDING)])
        self.receipts.create_index("seq", unique=True)

    # payments
    def create_payment(self, p: dict) -> dict:
        self.payments.insert_one(dict(p))  # insert_one adds _id to the copy, not to p
        return _clean(p)

    def get_payment(self, pid: str) -> Optional[dict]:
        return self.payments.find_one({"id": pid}, HIDDEN)

    def list_payments(self, limit: int) -> list[dict]:
        return list(self.payments.find({}, HIDDEN).sort("created_ts", self._desc).limit(limit))

    def update_pending(self, pid: str, fields: dict) -> Optional[dict]:
        from pymongo import ReturnDocument

        return self.payments.find_one_and_update(
            {"id": pid, "status": "pending"},
            {"$set": fields},
            projection=HIDDEN,
            return_document=ReturnDocument.AFTER,
        )

    def set_fields(self, pid: str, fields: dict) -> None:
        self.payments.update_one({"id": pid}, {"$set": fields})

    def pending_older_than(self, cutoff_ts: float) -> list[str]:
        cur = self.payments.find({"status": "pending", "created_ts": {"$lt": cutoff_ts}}, {"id": 1, "_id": 0})
        return [d["id"] for d in cur]

    # sessions
    def create_session(self, doc: dict) -> None:
        self.sessions.insert_one(dict(doc))

    def update_session(self, sid: str, fields: dict) -> None:
        self.sessions.update_one({"session_id": sid}, {"$set": fields})

    def list_sessions(self, limit: int) -> list[dict]:
        return list(self.sessions.find({}, {"_id": 0}).sort("started_at", self._desc).limit(limit))

    # receipts
    def last_receipt(self) -> Optional[dict]:
        return self.receipts.find_one({}, {"_id": 0}, sort=[("seq", self._desc)])

    def insert_receipt(self, r: dict) -> None:
        self.receipts.insert_one(dict(r))

    def list_receipts(self, limit: int, ascending: bool = False) -> list[dict]:
        order = self._asc if ascending else self._desc
        return list(self.receipts.find({}, {"_id": 0}).sort("seq", order).limit(limit))


# ---------------------------------------------------------------------------
# Factory + receipt chain
# ---------------------------------------------------------------------------


def open_store():
    uri = os.getenv("MONGODB_URI", "").strip()
    db_name = os.getenv("MONGODB_DB", "pulse").strip() or "pulse"
    if not uri:
        log.warning("MONGODB_URI not set: using in-memory storage (data is lost on restart)")
        return MemoryStore()
    try:
        store = MongoStore(uri, db_name)
        log.info("Connected to MongoDB, database '%s'", db_name)
        return store
    except Exception as exc:
        msg = f"{type(exc).__name__}: {str(exc)[:200]}"
        log.error("MongoDB unavailable (%s). Falling back to memory.", msg)
        return MemoryStore(error=msg)


_receipt_lock = threading.Lock()


def append_receipt(store, event: dict) -> dict:
    """Append one event to the hash chain. event includes "kind": "payment" or "access"."""
    with _receipt_lock:
        last = store.last_receipt()
        body = {
            **event,
            "seq": (last["seq"] + 1) if last else 1,
            "prev_hash": last["hash"] if last else GENESIS,
        }
        receipt = {**body, "hash": receipt_hash(body)}
        store.insert_receipt(receipt)
        return receipt


def payment_event(p: dict) -> dict:
    return {
        "kind": "payment",
        "payment_id": p["id"],
        "provider": p["provider"],
        "requester": p["requester"],
        "amount": p["amount"],
        "result": p["status"],
        "reason": p.get("reason"),
        "checks": p.get("checks"),
        "at": p.get("completed_at"),
    }


def verify_chain(store, limit: int = 10_000) -> dict:
    receipts = store.list_receipts(limit, ascending=True)
    prev = GENESIS
    for i, r in enumerate(receipts):
        body = {k: v for k, v in r.items() if k != "hash"}
        expected_seq = i + 1
        if r.get("seq") != expected_seq:
            return {"ok": False, "count": len(receipts), "broken_at": expected_seq, "problem": "a receipt is missing or out of order"}
        if r.get("prev_hash") != prev:
            return {"ok": False, "count": len(receipts), "broken_at": r["seq"], "problem": "its link to the previous receipt is broken"}
        if receipt_hash(body) != r.get("hash"):
            return {"ok": False, "count": len(receipts), "broken_at": r["seq"], "problem": "its contents were edited"}
        prev = r["hash"]
    return {"ok": True, "count": len(receipts), "broken_at": None, "problem": None}
