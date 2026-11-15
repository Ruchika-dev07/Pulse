"""
Capital One Nessie v2 integration ("You are the bank").

Pulse becomes the bank's human-approval step:

  1. A payment is opened as a real Nessie purchase on a checking account. Nessie checks funds
     (insufficient_funds is refused up front) and puts a hold on the money (pending).
  2. The wearable runs touch + heartbeat + fingerprint.
  3. Approved -> Pulse tells Nessie "approve" and the purchase settles.
     Denied / timed out / cancelled -> Pulse tells Nessie "decline" with the reason, the hold is
     released and no money moves.

Two modes, chosen automatically:
  hook   NESSIE_HOOK_URL is set (a public https URL, e.g. https://noheartnocart.biz/api/nessie/hook).
         Pulse registers itself as Nessie's *decision hook* for purchases, so the bank waits for the
         wearable. Purchases made anywhere on the key (the Nessie simulator, curl, another app) also
         reach the wearable.
  post   No hook URL. The purchase is only created at Nessie after the wearable approves.

No NESSIE_API_KEY at all -> a demo bank in memory, so the app always runs.

Settings (backend/.env)
  NESSIE_API_KEY   nsk_... from https://v2.nessieisreal.com/account
  NESSIE_URL       default https://v2-prod-api.nessieisreal.com
  NESSIE_HOOK_URL  optional public URL of /api/nessie/hook
"""

import hashlib
import hmac
import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("pulse.bank")

IDS_FILE = Path(__file__).with_name(".nessie_ids.json")
MARKER = "PULSE "  # purchase descriptions start with this + the Pulse payment id


class BankError(Exception):
    def __init__(self, message: str, kind: str = "bank_error", status: int = 502):
        super().__init__(message)
        self.kind = kind
        self.status = status


def _mask(number: str) -> str:
    digits = "".join(c for c in str(number or "") if c.isdigit())
    return f"•••• {digits[-4:]}" if digits else "••••"


def _cents(dollars: float) -> int:
    return int(round(float(dollars) * 100))


class Bank:
    def __init__(self):
        self.key = os.getenv("NESSIE_API_KEY", "").strip()
        self.base = os.getenv("NESSIE_URL", "https://v2-prod-api.nessieisreal.com").rstrip("/")
        self.hook_url = os.getenv("NESSIE_HOOK_URL", "").strip()
        self.provider = "nessie" if self.key else "demo"
        self.mode = ("hook" if self.hook_url else "post") if self.key else "demo"
        self.error: Optional[str] = None
        self._lock = threading.Lock()
        self._ids: dict = {}
        self._ready = False
        self._merchant_names: dict[str, str] = {}  # id -> name

        # demo bank
        self._demo = {
            "holder": "Jordan Rivera",
            "nickname": "Everyday Checking",
            "number": "".join(random.choice("0123456789") for _ in range(16)),
            "balance_cents": 248650,
            "pending_cents": 0,
            "tx": [],
        }

        if self.key:
            threading.Thread(target=self._setup_safely, daemon=True, name="nessie-setup").start()
        else:
            log.info("NESSIE_API_KEY not set: using the demo bank")

    # ------------------------------------------------------------------ public

    def status(self) -> dict:
        return {
            "provider": self.provider,
            "mode": self.mode,
            "ready": self.provider == "demo" or self._ready,
            "error": self.error,
        }

    def summary(self) -> dict:
        if self.provider == "demo":
            d = self._demo
            with self._lock:
                return {
                    "provider": "demo",
                    "mode": "demo",
                    "holder": d["holder"],
                    "nickname": d["nickname"],
                    "type": "Checking",
                    "number": _mask(d["number"]),
                    "balance": d["balance_cents"] / 100,
                    "available": (d["balance_cents"] - d["pending_cents"]) / 100,
                    "pending": d["pending_cents"] / 100,
                    "transactions": list(reversed(d["tx"]))[:8],
                }
        self._ensure_ready()
        acct = self._get(f"/accounts/{self._ids['account_id']}")
        txs = (self._get(f"/accounts/{self._ids['account_id']}/transactions?limit=8") or {}).get("items") or []
        return {
            "provider": "nessie",
            "mode": self.mode,
            "holder": self._ids.get("holder", ""),
            "nickname": acct.get("nickname"),
            "type": acct.get("type"),
            "number": _mask(acct.get("account_number")),
            "balance": acct.get("balance_cents", 0) / 100,
            "available": acct.get("available_cents", 0) / 100,
            "pending": acct.get("pending_debits_cents", 0) / 100,
            "transactions": [self._tx_view(t) for t in txs],
        }

    def available(self) -> Optional[float]:
        try:
            return float(self.summary()["available"])
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("Could not read balance: %s", self.error)
            return None

    def open_payment(self, payment: dict) -> Optional[dict]:
        """Before the wearable: in hook mode, open the purchase at the bank so funds are held.

        Returns bank info for the payment, or None when the purchase is created after approval.
        Raises BankError(kind="insufficient_funds") if the bank refuses it.
        """
        if self.provider == "demo":
            with self._lock:
                d = self._demo
                cents = _cents(payment["amount"])
                if cents > d["balance_cents"] - d["pending_cents"]:
                    raise BankError("Insufficient funds", "insufficient_funds", 402)
                d["pending_cents"] += cents
                tx = {
                    "id": f"txn_demo_{payment['id'][:8]}",
                    "kind": "purchase",
                    "merchant": payment["provider"],
                    "amount": cents / 100,
                    "status": "pending",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                d["tx"].append(tx)
            return {"provider": "demo", "mode": "demo", "posted": False, "transaction_id": tx["id"], "status": "pending", "error": None}

        if self.mode != "hook":
            return None
        tx = self._create_purchase(payment)
        return {"provider": "nessie", "mode": "hook", "posted": False, "transaction_id": tx.get("id"), "status": tx.get("status"), "error": None}

    def close_payment(self, payment: dict) -> dict:
        """After the wearable: approve or decline at the bank. Returns updated bank info."""
        approved = payment["status"] == "authorized"
        reason = None if approved else f"Pulse: {payment.get('reason') or payment['status']}"
        info = dict(payment.get("bank") or {})

        if self.provider == "demo":
            with self._lock:
                d = self._demo
                cents = _cents(payment["amount"])
                d["pending_cents"] = max(0, d["pending_cents"] - cents)
                if approved:
                    d["balance_cents"] -= cents
                for t in d["tx"]:
                    if t["id"] == info.get("transaction_id"):
                        t["status"] = "completed" if approved else "declined"
            info.update(status="completed" if approved else "declined", posted=approved,
                        decision="approve" if approved else "decline")
            return info

        try:
            if self.mode == "hook" and info.get("transaction_id"):
                self._decide(info["transaction_id"], approved, reason)
                info.update(status="completed" if approved else "declined", posted=approved,
                            decision="approve" if approved else "decline")
            elif approved:
                tx = self._create_purchase(payment)
                info = {"provider": "nessie", "mode": "post", "transaction_id": tx.get("id"), "error": None}
                self._settle(tx.get("id"))
                info.update(status="completed", posted=True, decision="approve")
            else:
                info = {"provider": "nessie", "mode": "post", "transaction_id": None, "status": "not_sent",
                        "posted": False, "decision": "decline", "error": None}
        except Exception as exc:
            info["error"] = f"{type(exc).__name__}: {exc}"
            log.error("Bank close failed: %s", info["error"])
        return info

    def payment_id_from_tx(self, tx: dict) -> Optional[str]:
        desc = tx.get("description") or ""
        if desc.startswith(MARKER):
            return desc[len(MARKER):].split(" ")[0]
        return None

    def merchant_name(self, merchant_id: Optional[str]) -> str:
        if not merchant_id:
            return "Merchant"
        if merchant_id not in self._merchant_names and self.provider == "nessie":
            try:
                m = self._get(f"/merchants/{merchant_id}")
                self._merchant_names[merchant_id] = m.get("name") or "Merchant"
            except Exception:
                return "Merchant"
        return self._merchant_names.get(merchant_id, "Merchant")

    def verify_hook(self, header: str, body: bytes) -> bool:
        secret = self._ids.get("hook_secret")
        if not secret:
            return True  # hook registered elsewhere; nothing to check against
        try:
            parts = dict(p.split("=", 1) for p in header.split(","))
            expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, parts["v1"])
        except Exception:
            return False

    # ------------------------------------------------------------------ Nessie internals

    def _setup_safely(self) -> None:
        try:
            self._ensure_ready()
            log.info("Nessie ready (%s mode): account %s, holder %s", self.mode, self._ids["account_id"], self._ids["holder"])
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log.error("Nessie setup failed: %s", self.error)

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            if IDS_FILE.exists():
                try:
                    self._ids = json.loads(IDS_FILE.read_text())
                except Exception:
                    self._ids = {}
            if self._ids.get("key_tail") != self.key[-6:]:
                self._ids = {"key_tail": self.key[-6:]}  # a different key means different data

            if not self._ids.get("account_id"):
                accounts = (self._get("/accounts?limit=100") or {}).get("items") or []
                if not any(a.get("type") == "Checking" for a in accounts):
                    log.info("Seeding a demo bank in Nessie")
                    self._post("/data/seed", None)
                    accounts = (self._get("/accounts?limit=100") or {}).get("items") or []
                checking = [a for a in accounts if a.get("type") == "Checking"]
                if not checking:
                    raise BankError("No checking account in Nessie")
                acct = max(checking, key=lambda a: a.get("available_cents", 0))
                cust = self._get(f"/accounts/{acct['id']}/customer")
                self._ids.update(
                    account_id=acct["id"],
                    customer_id=acct.get("customer_id"),
                    holder=f"{cust.get('first_name', '')} {cust.get('last_name', '')}".strip(),
                    merchants={},
                )
            self._ids.setdefault("merchants", {})

            if self.mode == "hook":
                self._ensure_hook()
            self._save()
            self._ready = True
            self.error = None

    def _ensure_hook(self) -> None:
        hooks = (self._get("/webhooks?limit=100") or {}).get("items") or []
        for h in hooks:
            if h.get("mode") == "decision" and "purchase" in (h.get("kinds") or []):
                if h.get("url") == self.hook_url and self._ids.get("hook_secret"):
                    return
                self._request("DELETE", f"/webhooks/{h['id']}")
        created = self._post(
            "/webhooks",
            {
                "mode": "decision",
                "url": self.hook_url,
                "kinds": ["purchase"],
                "max_wait_seconds": 120,
                "on_error": "decline",
            },
        )
        self._ids["hook_id"] = created.get("id")
        self._ids["hook_secret"] = created.get("secret")
        log.info("Registered Pulse as Nessie's decision hook for purchases at %s", self.hook_url)

    def _merchant_id(self, name: str) -> str:
        key = name.strip().lower()
        if key in self._ids["merchants"]:
            return self._ids["merchants"][key]
        m = self._post("/merchants", {"name": name.strip()[:80], "category": "online"})
        self._ids["merchants"][key] = m["id"]
        self._merchant_names[m["id"]] = m.get("name", name)
        self._save()
        return m["id"]

    def _create_purchase(self, payment: dict) -> dict:
        self._ensure_ready()
        merchant_id = self._merchant_id(payment["provider"])
        try:
            tx = self._post(
                f"/accounts/{self._ids['account_id']}/purchases",
                {
                    "merchant_id": merchant_id,
                    "amount_cents": _cents(payment["amount"]),
                    "description": f"{MARKER}{payment['id']} {payment['provider']}"[:200],
                },
            )
        except BankError as exc:
            if exc.kind in ("insufficient_funds", "credit_limit_exceeded"):
                raise BankError("Insufficient funds", "insufficient_funds", 402) from None
            raise
        log.info("Opened Nessie purchase %s: $%.2f to %s (%s)", tx.get("id"), payment["amount"], payment["provider"], tx.get("status"))
        return tx

    def _decide(self, tx_id: str, approve: bool, reason: Optional[str]) -> None:
        body = {"decision": "approve" if approve else "decline"}
        if reason:
            body["reason"] = reason[:200]
        self._post(f"/transactions/{tx_id}/decision", body)
        log.info("Told Nessie to %s %s", body["decision"], tx_id)

    def _settle(self, tx_id: Optional[str]) -> None:
        if not tx_id:
            return
        try:
            self._post(f"/transactions/{tx_id}/settle", None)
        except Exception as exc:  # settling early is a convenience; the window closes on its own
            log.info("Early settle skipped for %s: %s", tx_id, exc)

    def _tx_view(self, t: dict) -> dict:
        kind = t.get("kind")
        incoming = kind in ("deposit", "refund", "interest_credit") or (
            kind == "transfer" and t.get("payee_id") == self._ids.get("account_id")
        )
        if kind == "purchase":
            label = self.merchant_name(t.get("merchant_id"))
        else:
            label = (t.get("description") or kind or "Transaction").replace(MARKER, "")[:40]
        return {
            "id": t.get("id"),
            "kind": kind,
            "merchant": label,
            "amount": t.get("amount_cents", 0) / 100,
            "incoming": incoming,
            "status": t.get("status"),
            "decision_source": t.get("decision_source"),
            "created_at": t.get("created_at"),
            "pulse": (t.get("description") or "").startswith(MARKER),
        }

    def _save(self) -> None:
        try:
            IDS_FILE.write_text(json.dumps(self._ids, indent=2))
        except Exception:
            log.exception("Could not save Nessie ids")

    # ------------------------------------------------------------------ HTTP

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    text = resp.read().decode()
                    return json.loads(text) if text.strip() else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode(errors="replace")
                if exc.code == 429 and attempt < 2:
                    time.sleep(float(exc.headers.get("Retry-After", "1")))
                    continue
                kind = "bank_error"
                try:
                    problem = json.loads(raw)
                    kind = (problem.get("type") or "").rstrip("/").split("/")[-1] or kind
                    raw = problem.get("detail") or problem.get("title") or raw
                except Exception:
                    pass
                raise BankError(f"Nessie {method} {path} -> {exc.code}: {raw[:200]}", kind, exc.code) from None

    def _get(self, path: str):
        return self._request("GET", path)

    def _post(self, path: str, body: Optional[dict]):
        return self._request("POST", path, body if body is not None else {})
