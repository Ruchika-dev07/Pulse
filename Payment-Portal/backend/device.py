"""
Device layer for Pulse.

Two modes, picked with the DEVICE_MODE env var:
  sim    (default) fake wearable that speaks the same JSON protocol
  serial real ESP32 over USB serial (needs SERIAL_PORT)

Protocol: one JSON object per line, 115200 baud. See PORTAL.md for the full contract.

Laptop -> device
  {"type":"payment_request","id":"<uuid>","provider":"Amazon.com","requester":"Ruchika","amount":500.0}
  {"type":"session_request","id":"<uuid>"}            # unlock the protected site
  {"type":"cancel","id":"<uuid>"}

Device -> laptop
  {"type":"check","id":"<uuid>","check":"presence","ok":true}      # optional progress per check
  {"type":"result","id":"<uuid>","ok":true,"status":"authorized"}
  {"type":"result","id":"<uuid>","ok":false,"status":"denied","reason":"wrong_finger"}
  {"type":"status","worn":true,"live":true,"bpm":72}               # every ~1 s, continuous check
  {"type":"session_end","reason":"removed"}                        # optional, wearer took it off

Reasons: not_present | no_heartbeat | wrong_finger | user_rejected | device_error
Any line that isn't JSON (boot logs, debug prints) is logged and ignored.
"""

import json
import logging
import os
import random
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("pulse.device")

SIM_OUTCOMES = ("authorized", "not_present", "no_heartbeat", "wrong_finger", "timeout")
CHECK_ORDER = ("presence", "liveness", "identity")
FAILS_AT = {"not_present": "presence", "no_heartbeat": "liveness", "wrong_finger": "identity"}


class DeviceLink:
    def __init__(self, on_message: Callable[[dict], None]):
        self.on_message = on_message
        requested = os.getenv("DEVICE_MODE", "sim").strip().lower()
        self.mode = requested if requested in ("serial", "wifi") else "sim"
        self.port = os.getenv("SERIAL_PORT", "").strip() or None
        self.baud = int(os.getenv("SERIAL_BAUD", "115200"))
        self.connected = False
        self.error: Optional[str] = None
        self._ser = None
        self._write_lock = threading.Lock()
        self._cancelled: set[str] = set()
        self._sent: dict[str, str] = {}  # request id -> exact line sent (for signature checks)

        # Wi-Fi mode: requests wait here until the wearable polls GET /api/device/next
        self._queue: dict[str, dict] = {}  # id -> {"msg", "line", "created", "delivered"}
        self._queue_lock = threading.Lock()
        self.last_seen: Optional[float] = None

        # simulator wearable state
        self.sim_worn = True
        self.sim_live = True
        self._sim_bpm = 72.0

        if self.mode == "serial":
            threading.Thread(target=self._serial_loop, daemon=True, name="serial-reader").start()
        elif self.mode == "wifi":
            log.info("Device mode: Wi-Fi (wearable polls /api/device/next and posts to /api/device/event)")
        else:
            log.info("Device mode: simulator")
            threading.Thread(target=self._sim_status_loop, daemon=True, name="sim-status").start()

    # ---------- public ----------

    def status(self) -> dict:
        out = {
            "mode": self.mode,
            "port": self.port,
            "baud": self.baud,
            "connected": self._is_connected(),
            "error": self.error,
        }
        if self.mode == "wifi":
            out["last_seen_seconds"] = round(time.time() - self.last_seen, 1) if self.last_seen else None
        if self.mode == "sim":
            out["sim"] = {"worn": self.sim_worn, "live": self.sim_live}
        return out

    def send_payment(self, payment: dict, sim_outcome: Optional[str] = None) -> None:
        self._send(
            {
                "type": "payment_request",
                "id": payment["id"],
                "provider": payment["provider"],
                "requester": payment["requester"],
                "amount": payment["amount"],
            },
            sim_outcome,
        )

    def send_session_request(self, request_id: str, sim_outcome: Optional[str] = None) -> None:
        self._send({"type": "session_request", "id": request_id}, sim_outcome)

    def cancel(self, request_id: str) -> None:
        if self.mode == "serial":
            self._write({"type": "cancel", "id": request_id})
        elif self.mode == "wifi":
            with self._queue_lock:
                self._queue.pop(request_id, None)
        else:
            self._cancelled.add(request_id)

    # ---------- Wi-Fi mode ----------

    def _is_connected(self) -> bool:
        if self.mode == "serial":
            return self.connected
        if self.mode == "wifi":
            return bool(self.last_seen and time.time() - self.last_seen < 10)
        return True

    def mark_seen(self) -> None:
        self.last_seen = time.time()

    def next_request(self) -> Optional[dict]:
        """The oldest request waiting for the wearable. Handed out once; re-offered after 20 s
        without a result (e.g. the wearable rebooted mid-check)."""
        self.mark_seen()
        now = time.time()
        with self._queue_lock:
            for rid in list(self._queue):
                if now - self._queue[rid]["created"] > 120:
                    self._queue.pop(rid, None)  # long expired
            for rid, item in sorted(self._queue.items(), key=lambda kv: kv[1]["created"]):
                if item["delivered"] is None or now - item["delivered"] > 20:
                    item["delivered"] = now
                    return {**item["msg"], "line": item["line"]}
        return None

    def complete(self, request_id: Optional[str]) -> None:
        if request_id:
            with self._queue_lock:
                self._queue.pop(request_id, None)

    def sent_line(self, request_id: str) -> Optional[str]:
        """The exact bytes sent for a request, without the newline. The chip signs these."""
        return self._sent.get(request_id)

    def set_sim(self, worn: Optional[bool] = None, live: Optional[bool] = None) -> dict:
        if worn is not None:
            self.sim_worn = worn
        if live is not None:
            self.sim_live = live
        log.info("Simulator: worn=%s live=%s", self.sim_worn, self.sim_live)
        self._emit_sim_status()  # react immediately, not on the next tick
        return {"worn": self.sim_worn, "live": self.sim_live}

    # ---------- shared ----------

    def _send(self, msg: dict, sim_outcome: Optional[str]) -> None:
        if self.mode == "serial":
            self._write(msg)
            return
        if self.mode == "wifi":
            line = json.dumps(msg, separators=(",", ":"))
            self._sent[msg["id"]] = line
            with self._queue_lock:
                self._queue[msg["id"]] = {"msg": msg, "line": line, "created": time.time(), "delivered": None}
            log.info("Queued for the wearable: %s", line)
            return
        outcome = sim_outcome if sim_outcome in SIM_OUTCOMES else "authorized"
        # a simulated wearable that's off or has no pulse can't pass, whatever the demo setting
        if not self.sim_worn:
            outcome = "not_present"
        elif not self.sim_live and outcome == "authorized":
            outcome = "no_heartbeat"
        threading.Thread(target=self._simulate, args=(msg["id"], outcome), daemon=True, name="sim").start()

    # ---------- serial ----------

    def _serial_loop(self) -> None:
        """Open the port, read lines forever, reconnect if the cable is pulled."""
        try:
            import serial  # pyserial
        except ImportError:
            self.error = "pyserial is not installed (pip install pyserial)"
            log.error(self.error)
            return

        if not self.port:
            self.error = "SERIAL_PORT is not set"
            log.error(self.error)
            return

        while True:
            try:
                log.info("Opening %s at %d baud", self.port, self.baud)
                self._ser = serial.Serial(self.port, self.baud, timeout=1)
                time.sleep(2)  # most ESP32 boards reset when the port opens
                self._ser.reset_input_buffer()
                self.connected = True
                self.error = None
                log.info("Connected to device on %s", self.port)

                while True:
                    raw = self._ser.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        log.info("device (text) -> %s", line)
                        continue
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("type") != "status":  # status arrives every second; keep logs readable
                        log.info("device -> %s", line)
                    self.on_message(msg)
            except Exception as exc:  # port missing, cable pulled, permission denied
                self.connected = False
                self.error = f"{type(exc).__name__}: {exc}"
                log.error("Serial error: %s. Retrying in 3 s", self.error)
                try:
                    if self._ser:
                        self._ser.close()
                except Exception:
                    pass
                self._ser = None
                time.sleep(3)

    def _write(self, msg: dict) -> None:
        body = json.dumps(msg, separators=(",", ":"))
        line = body + "\n"
        if msg.get("type") in ("payment_request", "session_request"):
            self._sent[msg["id"]] = body
            if len(self._sent) > 500:  # keep memory bounded
                for old in list(self._sent)[:250]:
                    self._sent.pop(old, None)
        with self._write_lock:
            if not (self._ser and self.connected):
                log.error("Not connected, dropping: %s", line.strip())
                return
            self._ser.write(line.encode("utf-8"))
            self._ser.flush()
        log.info("laptop -> %s", line.strip())

    # ---------- simulator ----------

    def _emit_sim_status(self) -> None:
        if self.sim_worn and self.sim_live:
            self._sim_bpm += random.uniform(-1.5, 1.5)
            self._sim_bpm = max(64.0, min(84.0, self._sim_bpm))
            bpm = round(self._sim_bpm)
        else:
            bpm = None
        self.on_message({"type": "status", "worn": self.sim_worn, "live": self.sim_worn and self.sim_live, "bpm": bpm})

    def _sim_status_loop(self) -> None:
        while True:
            time.sleep(1)
            try:
                self._emit_sim_status()
            except Exception:
                log.exception("Simulator status error")

    def _simulate(self, request_id: str, outcome: str) -> None:
        """Mimic the real device: checks light up one by one, then a result."""
        time.sleep(1.2)
        if outcome == "timeout":
            return  # say nothing; the server times the request out

        fail_at = FAILS_AT.get(outcome)
        for check in CHECK_ORDER:
            if request_id in self._cancelled:
                return
            time.sleep(1.2)
            ok = check != fail_at
            self.on_message({"type": "check", "id": request_id, "check": check, "ok": ok})
            if not ok:
                time.sleep(0.4)
                self.on_message(
                    {"type": "result", "id": request_id, "ok": False, "status": "denied", "reason": outcome}
                )
                return

        time.sleep(0.5)
        if request_id not in self._cancelled:
            self.on_message({"type": "result", "id": request_id, "ok": True, "status": "authorized"})
