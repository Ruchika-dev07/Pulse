# Pulse: web app and backend

Pulse is a wearable that proves a specific, present, living person is behind an action.
This repo part has two demos that use the same wearable checks (touch, heartbeat, fingerprint):

1. **Secure site.** A protected page that answers **403** until you unlock it with the wearable.
   While it's open, the wearable reports every second. Take the band off (or lose the pulse)
   and the page locks itself within about 2 seconds.
2. **Payments.** A payment, like an agent buying your Amazon cart, only goes through after the
   wearer approves that exact amount and payee on the device.

Every unlock, lock and payment is written to a hash-chained receipt log in MongoDB Atlas.

```
backend/    FastAPI + pyserial + MongoDB   main.py, access.py, device.py, storage.py, pages.py
frontend/   React + Vite                   src/App.jsx, SecureSite.jsx, Payments.jsx, shared.jsx
firmware/   ESP32 reference sketch         pulse_protocol/pulse_protocol.ino
```

## 1. Run it (simulator, no hardware needed)

Terminal 1, backend:
```bash
cd backend
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # Windows: copy .env.example .env
uvicorn main:app --reload --port 8000
```

Terminal 2, frontend:
```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

The **Simulator** panel (bottom right) plays the wearable: switch **Wearing the band** and
**Heartbeat** on and off, and pick what the next fingerprint check does.

### Demo script

1. Secure site tab: sad heart, **403 No access**. Header heart is sad.
2. **Unlock with Pulse**: touch, heartbeat and fingerprint light up one by one.
3. Happy heart beating at your bpm, **Hello world, you’re in**, internal tracker visible.
   Header heart is happy.
4. Switch **Wearing the band** off (or take the real band off): within ~2 s it flips back to
   403 with "You took the wearable off, so the site locked itself."
5. **Open /secret in a new tab**: the plain server page returns a real HTTP 403 (check the
   network tab) and auto-refreshes, so it flips the same way.
6. Try **Fail: wrong finger**: unlock is refused and the fingerprint row shows Failed.
7. Payments tab: send $500 to Amazon.com, approve on the device, receipt appears.
8. **Verify receipt log**. Then edit any receipt in Atlas and verify again: it names the
   receipt that was tampered with.

## 2. MongoDB Atlas

Without this the app runs on in-memory storage (pill says **In-memory storage**).

1. Free M0 cluster at mongodb.com/atlas.
2. Database Access: add user `pulse-app` with a generated password.
3. Network Access: add your IP. For the hackathon `0.0.0.0/0` lets every teammate connect.
   Lock it down after the event.
4. Connect > Drivers > Python: copy the connection string into `MONGODB_URI` in `backend/.env`.
5. Restart the backend. The terminal prints `Connected to MongoDB` and the pill turns green.
   If Atlas can't be reached it says why and falls back to memory.

Database `pulse`:
- `payments`: every payment request, live check states, final result
- `sessions`: every unlock of the secure site, when and why it ended, duration
  (stores a hash of the session token, never the token)
- `receipts`: append-only log of payments and access events. Each receipt holds the SHA-256
  of the previous one, so any edit or deletion breaks the chain.

No biometric data is stored anywhere: only pass or fail per check, and the bpm number.

`.env` is gitignored. Share it with teammates by DM, never in the repo.

## 3. Real ESP32

Run the backend on the laptop the ESP32 is plugged into. Close the Arduino Serial Monitor
first; only one program can hold the port.

Find the port: Windows Device Manager > Ports (e.g. `COM5`); Mac `ls /dev/tty.usb*`.

In `backend/.env`:
```
DEVICE_MODE=serial
SERIAL_PORT=COM5
```
Restart the backend. The pill shows **Device on COM5**. If the cable is pulled it retries
every 3 s. Every line sent and received is printed in the backend terminal.

If the firmware doesn't send `status` lines yet, set `CONTINUOUS_CHECK=false` so sessions
don't lock after 4 s of silence. They then end on **Lock now**, a `session_end` message, or
after `SESSION_MAX_MINUTES`.

## 4. Serial protocol (firmware must match)

115200 baud, one JSON object per line ending in `\n`.
`firmware/pulse_protocol/pulse_protocol.ino` implements all of it; only the four sensor
functions at the top need the real drivers.

Laptop to wearable:
```json
{"type":"payment_request","id":"<uuid>","provider":"Amazon.com","requester":"Ruchika","amount":500.0}
{"type":"session_request","id":"<uuid>"}
{"type":"cancel","id":"<uuid>"}
```

Wearable to laptop:
```json
{"type":"check","id":"<uuid>","check":"presence","ok":true}
{"type":"check","id":"<uuid>","check":"liveness","ok":true}
{"type":"check","id":"<uuid>","check":"identity","ok":false}
{"type":"result","id":"<uuid>","ok":true,"status":"authorized"}
{"type":"result","id":"<uuid>","ok":false,"status":"denied","reason":"wrong_finger"}
{"type":"status","worn":true,"live":true,"bpm":72}
{"type":"session_end","reason":"removed"}
```

- Always echo back the same `id`.
- `check` lines are optional; they light up each row live.
- `reason`: `not_present` | `no_heartbeat` | `wrong_finger` | `user_rejected` | `device_error`
- `status` about once a second, also while waiting for the fingerprint. If `worn` or `live`
  is false for 1.5 s, or nothing arrives for 4 s, the open session locks.
- Non-JSON lines are ignored, so debug prints are fine.

## 5. API

| Method | Path | What it does |
|---|---|---|
| GET  | `/api/health` | Backend, device and database status |
| GET  | `/api/session` | Lock state, live wearable readings, last end reason |
| POST | `/api/session/unlock` | Ask the wearable to unlock; returns a one-time claim secret |
| POST | `/api/session/claim` | Browser that asked trades the claim for an HttpOnly cookie |
| POST | `/api/session/cancel` | Cancel a pending unlock |
| POST | `/api/session/lock` | Lock now |
| GET  | `/api/vault` | Protected data: **403** without a live session |
| GET  | `/secret` | Protected server-rendered page: **403** or 200 |
| GET  | `/api/sessions` | Session history |
| POST | `/api/payments` | Create a payment and send it to the wearable |
| GET  | `/api/payments`, `/api/payments/{id}` | List, or one payment with live checks |
| POST | `/api/payments/{id}/cancel` | Cancel a pending payment |
| GET  | `/api/receipts`, `/api/receipts/verify` | Receipt log, and chain verification |
| POST | `/api/sim/device` | Simulator only: `{worn, live}` |

Interactive docs: http://localhost:8000/docs

## Why 403 and not 404

403 Forbidden means the page exists but you're not allowed in right now, which is exactly
the case. 404 would claim the page doesn't exist. (401 is for missing credentials in an
HTTP auth header; the wearable session isn't that kind of login.)

## Security notes for judges

- The session cookie is HttpOnly and only issued to the browser that started the unlock
  (one-time claim secret), so another tab or machine can't pick it up.
- Access is re-checked on every request: once the wearable reports it's off, the existing
  cookie stops working immediately.
- Honest limits for V1: the serial link is not yet signed, so anything on the laptop that can
  write to the serial port could fake a reply. V2 signs every reply with the ATECC608 key
  (ECDSA P-256) and the backend verifies it.

## Next

- V2: device signs each result (ECDSA P-256, ATECC608); backend verifies with the device public key.
- V3: HTTPS hosting on Vultr, GoDaddy domain, Solana devnet transfer behind the payment approval.
