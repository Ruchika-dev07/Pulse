"""
Continuous-presence access control for the protected site.

Flow
  1. Browser asks to unlock -> server sends {"type":"session_request"} to the wearable.
  2. Wearable runs presence, liveness, identity -> {"type":"result","ok":true}.
  3. Server opens a session. Only the browser that asked (it holds a one-time claim secret)
     can claim it and receives an HttpOnly cookie.
  4. While the session is open the wearable sends {"type":"status","worn":..,"live":..,"bpm":..}
     about once a second. The session ends the moment the wearable is taken off, the pulse
     stops, or the signal goes quiet. The cookie is then worthless and the site returns 403.

One wearable, one session at a time.
"""

import hashlib
import logging
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import storage
from device import CHECK_ORDER

log = logging.getLogger("pulse.access")

UNLOCK_TIMEOUT = int(os.getenv("APPROVAL_TIMEOUT", "30"))
CONTINUOUS_CHECK = os.getenv("CONTINUOUS_CHECK", "true").strip().lower() != "false"
SIGNAL_TIMEOUT = float(os.getenv("SIGNAL_TIMEOUT", "4"))      # no status for this long -> lock
REMOVAL_GRACE = float(os.getenv("REMOVAL_GRACE", "1.5"))     # bad readings must last this long
SESSION_MAX = int(os.getenv("SESSION_MAX_MINUTES", "30")) * 60

REASON_TO_CHECK = {"not_present": "presence", "no_heartbeat": "liveness", "wrong_finger": "identity"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class AccessManager:
    def __init__(self, store, device):
        self.store = store
        self.device = device
        self._lock = threading.Lock()

        self.request: Optional[dict] = None   # pending unlock
        self.session: Optional[dict] = None   # open session
        self.live = {"worn": None, "live": None, "bpm": None, "last_seen": None}
        self.bad_since: Optional[float] = None
        self.last_end: Optional[dict] = None
        self.last_failure: Optional[dict] = None

        threading.Thread(target=self._watch, daemon=True, name="access-watch").start()

    # ---------------- browser actions ----------------

    def unlock(self, sim_outcome: Optional[str]) -> dict:
        with self._lock:
            if self.session:
                raise ValueError("already_unlocked")
            if self.request:
                self.device.cancel(self.request["id"])
            claim = secrets.token_urlsafe(24)
            ts = time.time()
            self.request = {
                "id": str(uuid.uuid4()),
                "claim_hash": _hash(claim),
                "created_ts": ts,
                "expires_at": datetime.fromtimestamp(ts + UNLOCK_TIMEOUT, timezone.utc).isoformat(),
                "checks": {c: "waiting" for c in CHECK_ORDER},
            }
            self.last_failure = None
            req_id, expires = self.request["id"], self.request["expires_at"]
        log.info("Unlock requested (%s)", req_id)
        self.device.send_session_request(req_id, sim_outcome)
        return {"request_id": req_id, "claim": claim, "expires_at": expires}

    def cancel_unlock(self) -> None:
        with self._lock:
            req = self.request
            self.request = None
        if req:
            self.device.cancel(req["id"])
            self._fail(req["id"], "cancelled", record=False)

    def claim(self, request_id: str, claim: str) -> Optional[str]:
        """Hand the session token to the browser that started the unlock. Works once."""
        with self._lock:
            s = self.session
            if not s or s["request_id"] != request_id or s["claimed"]:
                return None
            if not secrets.compare_digest(s["claim_hash"], _hash(claim)):
                return None
            s["claimed"] = True
            return s["token"]

    def lock(self, reason: str = "locked") -> bool:
        return self._end(reason)

    def is_valid(self, token: Optional[str]) -> bool:
        if not token:
            return False
        with self._lock:
            s = self.session
            return bool(s and secrets.compare_digest(s["token"], token))

    # ---------------- device messages ----------------

    def owns(self, request_id: Optional[str]) -> bool:
        with self._lock:
            return bool(self.request and request_id == self.request["id"])

    def handle_device(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "check":
            with self._lock:
                if self.request and msg.get("id") == self.request["id"] and msg.get("check") in CHECK_ORDER:
                    self.request["checks"][msg["check"]] = "pass" if msg.get("ok") else "fail"
        elif kind == "result":
            if msg.get("ok"):
                self._start(msg.get("id"), msg.get("signature"))
            else:
                self._fail(msg.get("id"), msg.get("reason") or "device_error")

    def handle_status(self, msg: dict) -> None:
        worn = bool(msg.get("worn"))
        live = bool(msg.get("live"))
        bpm = msg.get("bpm")
        with self._lock:
            self.live = {"worn": worn, "live": live, "bpm": bpm, "last_seen": time.time()}
            if worn and live:
                self.bad_since = None
            elif self.bad_since is None:
                self.bad_since = time.time()
        if CONTINUOUS_CHECK and not (worn and live):
            self._check_bad_readings()

    def handle_session_end(self, reason: str) -> None:
        self._end(reason or "removed")

    # ---------------- state ----------------

    def public_state(self, token: Optional[str]) -> dict:
        with self._lock:
            now = time.time()
            s, r = self.session, self.request
            last_seen = self.live["last_seen"]
            state = {
                "status": "active" if s else ("unlocking" if r else "locked"),
                "has_access": bool(s and token and secrets.compare_digest(s["token"], token)),
                "continuous_check": CONTINUOUS_CHECK,
                "wearable": {
                    "worn": self.live["worn"],
                    "live": self.live["live"],
                    "bpm": self.live["bpm"],
                    "signal_age": round(now - last_seen, 1) if last_seen else None,
                },
                "request": None,
                "session": None,
                "last_end": self.last_end,
                "last_failure": self.last_failure,
            }
            if r:
                state["request"] = {"id": r["id"], "checks": dict(r["checks"]), "expires_at": r["expires_at"]}
            if s:
                state["session"] = {
                    "session_id": s["session_id"],
                    "request_id": s["request_id"],
                    "started_at": s["started_at"],
                    "seconds": int(now - s["started_ts"]),
                    "claimed": s["claimed"],
                    "signature": s.get("signature"),
                }
            return state

    # ---------------- internals ----------------

    def _start(self, request_id: Optional[str], signature: Optional[str] = None) -> None:
        with self._lock:
            r = self.request
            if not r or r["id"] != request_id:
                return
            ts = time.time()
            self.session = {
                "session_id": str(uuid.uuid4()),
                "request_id": r["id"],
                "claim_hash": r["claim_hash"],
                "token": secrets.token_urlsafe(32),
                "claimed": False,
                "started_at": now_iso(),
                "started_ts": ts,
                "signature": signature,
            }
            self.request = None
            self.bad_since = None
            # a fresh unlock counts as a fresh reading
            self.live["last_seen"] = ts
            s = dict(self.session)
        log.info("Session %s opened", s["session_id"])
        self._persist_start(s)

    def _fail(self, request_id: Optional[str], reason: str, record: bool = True) -> None:
        with self._lock:
            if record:
                if not self.request or self.request["id"] != request_id:
                    return
                failed = REASON_TO_CHECK.get(reason)
                if failed:
                    self.request["checks"][failed] = "fail"
                checks = dict(self.request["checks"])
                self.request = None
            else:
                checks = None
            self.last_failure = {"reason": reason, "at": now_iso(), "checks": checks}
        log.info("Unlock failed: %s", reason)
        if record:
            self._receipt({"kind": "access", "result": "denied", "reason": reason, "checks": checks, "at": now_iso()})

    def _end(self, reason: str) -> bool:
        with self._lock:
            s = self.session
            if not s:
                return False
            self.session = None
            duration = int(time.time() - s["started_ts"])
            self.last_end = {"reason": reason, "at": now_iso(), "session_id": s["session_id"], "seconds": duration}
        log.info("Session %s closed: %s after %ds", s["session_id"], reason, duration)
        try:
            self.store.update_session(
                s["session_id"], {"ended_at": now_iso(), "end_reason": reason, "duration_s": duration}
            )
        except Exception:
            log.exception("Could not save session end")
        self._receipt(
            {
                "kind": "access",
                "result": "ended",
                "session_id": s["session_id"],
                "reason": reason,
                "duration_s": duration,
                "at": now_iso(),
            }
        )
        return True

    def _persist_start(self, s: dict) -> None:
        try:
            self.store.create_session(
                {
                    "session_id": s["session_id"],
                    "started_at": s["started_at"],
                    "ended_at": None,
                    "end_reason": None,
                    "duration_s": None,
                    "token_hash": _hash(s["token"]),  # never the token itself
                    "device_mode": self.device.mode,
                    "signature": s.get("signature"),
                }
            )
        except Exception:
            log.exception("Could not save session start")
        self._receipt(
            {
                "kind": "access",
                "result": "granted",
                "session_id": s["session_id"],
                "signature": s.get("signature"),
                "at": s["started_at"],
            }
        )

    def _receipt(self, event: dict) -> None:
        try:
            storage.append_receipt(self.store, event)
        except Exception:
            log.exception("Could not write access receipt")

    def _check_bad_readings(self) -> None:
        with self._lock:
            if not self.session or self.bad_since is None:
                return
            if time.time() - self.bad_since < REMOVAL_GRACE:
                return
            reason = "removed" if self.live["worn"] is False else "no_heartbeat"
        self._end(reason)

    def _watch(self) -> None:
        while True:
            time.sleep(0.5)
            try:
                now = time.time()
                with self._lock:
                    r, s = self.request, self.session
                    expired_req = r["id"] if r and now - r["created_ts"] > UNLOCK_TIMEOUT else None
                    last_seen = self.live["last_seen"]
                    lost = bool(s and CONTINUOUS_CHECK and (last_seen is None or now - last_seen > SIGNAL_TIMEOUT))
                    too_long = bool(s and now - s["started_ts"] > SESSION_MAX)
                if expired_req:
                    self._fail(expired_req, "timeout")
                if lost:
                    self._end("signal_lost")
                elif too_long:
                    self._end("expired")
                elif CONTINUOUS_CHECK:
                    self._check_bad_readings()
            except Exception:
                log.exception("Access watcher error")
