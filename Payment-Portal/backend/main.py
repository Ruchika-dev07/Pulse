"""
Pulse backend: biometric access control + payment approval.

Run:
  uvicorn main:app --reload --port 8000

Settings go in backend/.env (see .env.example):
  MONGODB_URI, MONGODB_DB             MongoDB Atlas. Unset = in-memory storage
  DEVICE_MODE, SERIAL_PORT, SERIAL_BAUD
  APPROVAL_TIMEOUT                    seconds to approve a payment or unlock (default 30)
  CONTINUOUS_CHECK                    true (default) = lock the site when the wearable is removed
  SIGNAL_TIMEOUT, REMOVAL_GRACE       seconds (defaults 4 and 1.5)
  SESSION_MAX_MINUTES                 default 30
  REQUIRE_SIGNATURE                   true = refuse approvals not signed by the ATECC608 (V2)
  DEVICE_PUBKEY                       chip public key, 128 hex chars (defaults to chip 1)
  NESSIE_API_KEY                      Capital One Nessie v2 key (nsk_...). Unset = demo bank
  NESSIE_HOOK_URL                     public URL of /api/nessie/hook, e.g. https://noheartnocart.biz/api/nessie/hook
  ALLOWED_ORIGINS                     default http://localhost:5173,http://127.0.0.1:5173
"""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request, Response  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

import device_key  # noqa: E402
from bank import Bank, BankError  # noqa: E402
import pages  # noqa: E402
import storage  # noqa: E402
from access import AccessManager  # noqa: E402
from device import CHECK_ORDER, DeviceLink  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("pulse.api")

APPROVAL_TIMEOUT = int(os.getenv("APPROVAL_TIMEOUT", "30"))
COOKIE = "pulse_session"
REASON_TO_CHECK = {"not_present": "presence", "no_heartbeat": "liveness", "wrong_finger": "identity"}
FINAL = {"authorized", "denied", "timeout", "cancelled"}
REQUIRE_SIGNATURE = os.getenv("REQUIRE_SIGNATURE", "false").strip().lower() == "true"
SimOutcome = Literal["authorized", "not_present", "no_heartbeat", "wrong_finger", "timeout"]

store = storage.open_store()
bank = Bank()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def finish_payment(payment: Optional[dict]) -> None:
    """Runs once when a payment reaches a final state: settle or decline it at the bank, write its receipt."""
    if not payment or payment.get("status") not in FINAL:
        return
    if payment.get("bank") or bank.mode == "post":
        try:
            info = bank.close_payment(payment)
        except Exception as exc:
            info = {**(payment.get("bank") or {}), "error": str(exc)}
        payment["bank"] = info
        store.set_fields(payment["id"], {"bank": info})
    try:
        r = storage.append_receipt(store, storage.payment_event(payment))
        store.set_fields(payment["id"], {"receipt_seq": r["seq"], "receipt_hash": r["hash"]})
        log.info("Payment %s -> %s, receipt #%d", payment["id"], payment["status"], r["seq"])
    except Exception:
        log.exception("Could not write receipt for %s", payment.get("id"))


def handle_payment_message(msg: dict) -> None:
    pid, kind = msg.get("id"), msg.get("type")
    if kind == "check":
        name = msg.get("check")
        if name in CHECK_ORDER:
            store.update_pending(pid, {f"checks.{name}": "pass" if msg.get("ok") else "fail", "updated_at": now_iso()})
    elif kind == "result":
        t = now_iso()
        if msg.get("ok"):
            fields = {"status": "authorized", "completed_at": t, "updated_at": t, "signature": msg.get("signature")}
            fields.update({f"checks.{c}": "pass" for c in CHECK_ORDER})
        else:
            reason = msg.get("reason") or "device_error"
            fields = {"status": "denied", "reason": reason, "completed_at": t, "updated_at": t,
                      "signature": msg.get("signature")}
            if reason in REASON_TO_CHECK:
                fields[f"checks.{REASON_TO_CHECK[reason]}"] = "fail"
        updated = store.update_pending(pid, fields)
        if updated is None:
            log.info("Ignoring result for %s (unknown or already finished)", pid)
        finish_payment(updated)


# ---------------------------------------------------------------------------
# Device message router
# ---------------------------------------------------------------------------

access: Optional[AccessManager] = None


def check_signature(msg: dict) -> dict:
    """V2: an approval only counts if the wearable's ATECC608 chip signed it.

    Adds "signature": verified | invalid | missing | simulated. An invalid signature is always
    refused. A missing one is refused only when REQUIRE_SIGNATURE=true.
    """
    if msg.get("type") != "result" or not msg.get("ok"):
        return msg
    if device.mode == "sim":
        return {**msg, "signature": "simulated"}

    line = device.sent_line(msg.get("id", ""))
    sig = msg.get("sig")
    if sig and line and device_key.verify_approval(line, sig):
        return {**msg, "signature": "verified"}

    state = "invalid" if sig else "missing"
    if state == "invalid" or REQUIRE_SIGNATURE:
        log.warning("Refusing approval for %s: signature %s", msg.get("id"), state)
        return {
            **msg,
            "ok": False,
            "status": "denied",
            "reason": "bad_signature" if state == "invalid" else "unsigned",
            "signature": state,
        }
    return {**msg, "signature": state}


def on_device_message(msg: dict) -> None:
    kind = msg.get("type")
    msg = check_signature(msg)
    try:
        if kind == "status":
            if access:
                access.handle_status(msg)
        elif kind == "session_end":
            if access:
                access.handle_session_end(msg.get("reason") or "removed")
        elif kind in ("check", "result") and msg.get("id"):
            if access and access.owns(msg["id"]):
                access.handle_device(msg)
            else:
                handle_payment_message(msg)
    except Exception:
        log.exception("Error handling device message %s", msg)


device = DeviceLink(on_message=on_device_message)
access = AccessManager(store, device)


def payment_timeouts() -> None:
    while True:
        time.sleep(1)
        try:
            for pid in store.pending_older_than(time.time() - APPROVAL_TIMEOUT):
                t = now_iso()
                finish_payment(
                    store.update_pending(pid, {"status": "timeout", "reason": "timeout", "completed_at": t, "updated_at": t})
                )
        except Exception:
            log.exception("Payment timeout watcher error")


threading.Thread(target=payment_timeouts, daemon=True, name="payment-timeouts").start()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Pulse", version="2.0.0")

origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "timeout_seconds": APPROVAL_TIMEOUT,
        "device": device.status(),
        "storage": {"kind": store.kind, "error": store.error},
        "bank": bank.status(),
        "signature": {"required": REQUIRE_SIGNATURE, "chip": "ATECC608 chip 1 (01238D68A84D0601EE)"},
    }


# ---------------- access control ----------------


class UnlockIn(BaseModel):
    sim_outcome: Optional[SimOutcome] = None


class ClaimIn(BaseModel):
    request_id: str
    claim: str


@app.get("/api/session")
def session_state(request: Request):
    return access.public_state(request.cookies.get(COOKIE))


@app.post("/api/session/unlock")
def session_unlock(body: UnlockIn):
    try:
        return access.unlock(body.sim_outcome)
    except ValueError:
        raise HTTPException(409, "The site is already unlocked. Lock it first.")


@app.post("/api/session/cancel")
def session_cancel():
    access.cancel_unlock()
    return {"ok": True}


@app.post("/api/session/claim")
def session_claim(body: ClaimIn, request: Request, response: Response):
    token = access.claim(body.request_id, body.claim)
    if not token:
        raise HTTPException(403, "This unlock can't be claimed from here.")
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/", max_age=12 * 3600)
    return access.public_state(token)


@app.post("/api/session/lock")
def session_lock(response: Response):
    access.lock("locked")
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/sessions")
def list_sessions(limit: int = 20):
    return store.list_sessions(max(1, min(limit, 200)))


VAULT = {
    "message": "Hello world, you’re in.",
    "project": "Pulse internal tracker",
    "issues": [
        {"key": "PUL-14", "title": "ECG lead-off flag flickers when the wearer sweats", "area": "Hardware", "state": "In progress"},
        {"key": "PUL-12", "title": "Fingerprint enrolment needs a second scan to confirm", "area": "Firmware", "state": "To do"},
        {"key": "PUL-11", "title": "Sign payment requests with the ATECC608 key (V2)", "area": "Security", "state": "To do"},
        {"key": "PUL-9", "title": "Lock the site within 2 seconds of removing the band", "area": "Web", "state": "Done"},
        {"key": "PUL-7", "title": "Store approvals and sessions in MongoDB Atlas", "area": "Web", "state": "Done"},
    ],
}


@app.get("/api/vault")
def vault(request: Request):
    if not access.is_valid(request.cookies.get(COOKIE)):
        raise HTTPException(403, "Forbidden: unlock with your Pulse wearable to see this.")
    return VAULT


@app.get("/secret", response_class=HTMLResponse)
def secret_page(request: Request):
    """Plain server-rendered protected page. Shows a real 403 in the browser without the wearable."""
    state = access.public_state(request.cookies.get(COOKIE))
    if state["has_access"]:
        return HTMLResponse(pages.granted(state, VAULT), status_code=200)
    return HTMLResponse(pages.forbidden(state), status_code=403)


# ---------------- payments ----------------


class PaymentIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    provider: str = Field(min_length=1, max_length=60)
    requester: str = Field(min_length=1, max_length=60)
    amount: float = Field(gt=0, le=1_000_000)
    memo: Optional[str] = Field(default=None, max_length=120)
    sim_outcome: Optional[SimOutcome] = None


@app.get("/api/bank")
def bank_summary():
    try:
        return bank.summary()
    except Exception as exc:
        raise HTTPException(503, f"Bank unavailable: {exc}") from exc


@app.post("/api/payments", status_code=201)
def create_payment(body: PaymentIn):
    amount = round(body.amount, 2)
    if bank.mode == "post":  # nothing is held at the bank yet, so check the balance here
        available = bank.available()
        if available is not None and amount > available:
            raise HTTPException(402, f"Insufficient funds: ${available:,.2f} available.")
    ts = time.time()
    p = {
        "id": str(uuid.uuid4()),
        "provider": body.provider,
        "requester": body.requester,
        "amount": amount,
        "memo": body.memo or None,
        "status": "pending",
        "reason": None,
        "checks": {c: "waiting" for c in CHECK_ORDER},
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "completed_at": None,
        "expires_at": datetime.fromtimestamp(ts + APPROVAL_TIMEOUT, timezone.utc).isoformat(),
        "device_mode": device.mode,
        "receipt_seq": None,
        "receipt_hash": None,
        "signature": None,
        "bank": None,
        "created_ts": ts,
    }
    try:
        store.create_payment(p)
    except Exception as exc:
        log.exception("Could not save payment")
        raise HTTPException(503, f"Database error: {exc}") from exc

    # Open the purchase at the bank first, so the money is held while the wearable decides.
    try:
        info = bank.open_payment(p)
    except BankError as exc:
        if exc.kind == "insufficient_funds":
            store.update_pending(p["id"], {"status": "cancelled", "reason": "insufficient_funds", "completed_at": now_iso()})
            available = bank.available()
            msg = f"Insufficient funds: ${available:,.2f} available." if available is not None else "Insufficient funds."
            raise HTTPException(402, msg) from None
        info = {"provider": bank.provider, "mode": bank.mode, "posted": False, "transaction_id": None, "status": "error", "error": str(exc)}
        log.error("Bank refused to open the purchase: %s", exc)
    if info:
        p["bank"] = info
        store.set_fields(p["id"], {"bank": info})

    log.info("New payment %s: $%.2f to %s from %s", p["id"], p["amount"], p["provider"], p["requester"])
    device.send_payment(p, body.sim_outcome)
    return store.get_payment(p["id"])


# ---------------- Capital One Nessie decision hook ----------------

_external_tx: dict[str, str] = {}  # Nessie tx id -> Pulse payment id, for purchases made outside Pulse


@app.post("/api/nessie/hook")
async def nessie_hook(request: Request):
    """Nessie asks: approve or decline this pending purchase? Pulse answers once the wearable has."""
    raw = await request.body()
    if not bank.verify_hook(request.headers.get("Nessie-Signature", ""), raw):
        raise HTTPException(401, "Bad Nessie signature")
    try:
        tx = (json.loads(raw or b"{}") or {}).get("transaction") or {}
    except ValueError:
        raise HTTPException(400, "Bad JSON") from None
    tx_id = tx.get("id")
    if not tx_id:
        return {"decision": "decline", "reason": "Pulse: no transaction"}

    pid = bank.payment_id_from_tx(tx) or _external_tx.get(tx_id)
    if pid is None:
        # A purchase made outside Pulse (simulator, curl, another app): it still needs the wearable.
        ts = time.time()
        p = {
            "id": str(uuid.uuid4()),
            "provider": bank.merchant_name(tx.get("merchant_id")),
            "requester": "Nessie purchase",
            "amount": round(tx.get("amount_cents", 0) / 100, 2),
            "memo": tx.get("description"),
            "status": "pending",
            "reason": None,
            "checks": {c: "waiting" for c in CHECK_ORDER},
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "completed_at": None,
            "expires_at": datetime.fromtimestamp(ts + APPROVAL_TIMEOUT, timezone.utc).isoformat(),
            "device_mode": device.mode,
            "receipt_seq": None,
            "receipt_hash": None,
            "signature": None,
            "bank": {"provider": "nessie", "mode": "hook", "posted": False, "transaction_id": tx_id, "status": "pending", "external": True, "error": None},
            "created_ts": ts,
        }
        store.create_payment(p)
        _external_tx[tx_id] = p["id"]
        log.info("Nessie purchase %s needs the wearable: $%.2f to %s", tx_id, p["amount"], p["provider"])
        device.send_payment(p, None)
        return JSONResponse({"retry_after": 5}, status_code=202, headers={"Retry-After": "5"})

    p = store.get_payment(pid)
    if p is None or p["status"] == "pending":
        return JSONResponse({"retry_after": 5}, status_code=202, headers={"Retry-After": "5"})
    if p["status"] == "authorized":
        return {"decision": "approve"}
    return {"decision": "decline", "reason": f"Pulse: {p.get('reason') or p['status']}"[:200]}


@app.get("/api/payments")
def list_payments(limit: int = 25):
    return store.list_payments(max(1, min(limit, 200)))


@app.get("/api/payments/{payment_id}")
def get_payment(payment_id: str):
    p = store.get_payment(payment_id)
    if p is None:
        raise HTTPException(404, "Payment not found")
    return p


@app.post("/api/payments/{payment_id}/cancel")
def cancel_payment(payment_id: str):
    t = now_iso()
    updated = store.update_pending(payment_id, {"status": "cancelled", "reason": "cancelled", "completed_at": t, "updated_at": t})
    if updated is None:
        p = store.get_payment(payment_id)
        if p is None:
            raise HTTPException(404, "Payment not found")
        raise HTTPException(409, f"Payment is already {p['status']}")
    device.cancel(payment_id)
    finish_payment(updated)
    return store.get_payment(payment_id)


# ---------------- receipts ----------------


@app.get("/api/receipts")
def list_receipts(limit: int = 50):
    return store.list_receipts(max(1, min(limit, 500)))


@app.get("/api/receipts/verify")
def verify_receipts():
    return storage.verify_chain(store)


# ---------------- simulator controls ----------------


class SimIn(BaseModel):
    worn: Optional[bool] = None
    live: Optional[bool] = None


@app.post("/api/sim/device")
def sim_device(body: SimIn):
    if device.mode != "sim":
        raise HTTPException(400, "Simulator controls only work with DEVICE_MODE=sim")
    return device.set_sim(body.worn, body.live)
