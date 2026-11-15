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

## 5. V2: signed approvals (ATECC608)

The wearable's ATECC608 chip holds a private key that can never be read out. When all three
checks pass, the firmware signs:

```
SHA-256( <exact request line it received, without the newline> + "|authorized" )
```

and adds the signature to the result line:

```json
{"type":"result","id":"<uuid>","ok":true,"status":"authorized","sig":"<128 hex chars, r||s>"}
```

The backend (`backend/device_key.py`, adapted from `ESP32-Biometric/verify.py`) recomputes the
hash from the line it sent and verifies it with chip 1's public key.

- The signed line includes the amount and payee, so a signature for $50 can't approve $5,000.
- The request `id` is a fresh random nonce, and only pending requests accept a result, so a
  recorded reply can't be replayed.
- A wrong signature is always refused. `REQUIRE_SIGNATURE=true` also refuses unsigned replies.
- The site shows **Verified by the security chip** on approved payments and unlocked sessions.
- Firmware: `firmware/pulse_protocol/pulse_protocol.ino` has this built in (`USE_ATECC 1`).
- Honest limit: the once-a-second `status` lines aren't signed yet, and the chip protects the
  key, not the sensor wires.


## 6. API

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
| GET  | `/api/bank` | Capital One account: holder, balance, held funds, recent transactions |
| POST | `/api/nessie/hook` | Nessie decision hook (called by Nessie, verified by HMAC) |
| POST | `/api/sim/device` | Simulator only: `{worn, live}` |

Interactive docs: http://localhost:8000/docs

## 7. Capital One Nessie (banking)

Payments run against a real Nessie v2 checking account, so Pulse acts as the bank's
human-approval step:

1. The payment opens as a Nessie **purchase**. Nessie checks funds (insufficient funds is
   refused before anything else happens) and holds the money.
2. The wearable runs touch, heartbeat and fingerprint.
3. Approved: Pulse tells Nessie `approve` and the purchase settles. Denied, timed out or
   cancelled: Pulse tells Nessie `decline` with the reason, and the hold is released.

The Payments tab shows the account holder, masked account number, available balance, holds
and recent transactions straight from Nessie.

Setup (backend/.env):
```
NESSIE_API_KEY=nsk_...            # from https://v2.nessieisreal.com/account (GitHub sign-in)
```
On first start the backend seeds a demo bank if the key has no checking account
(`POST /data/seed`), picks the checking account, and remembers it in `backend/.nessie_ids.json`
(gitignored, one per laptop).

**Decision-hook mode (best for judging).** On the laptop that runs the Cloudflare tunnel, add:
```
NESSIE_HOOK_URL=https://noheartnocart.biz/api/nessie/hook
```
Pulse registers itself as Nessie's decision hook for purchases (`on_error: decline`, fail
closed). Then *any* purchase on the key, including ones from the Nessie traffic simulator or
plain curl, waits for the wearable. Only set this where the tunnel is running; if Nessie can't
reach the hook it declines purchases.

Without `NESSIE_HOOK_URL` the purchase is created only after the wearable approves. Without a
key at all, a demo bank stands in so the app always runs.

## Why 403 and not 404

403 Forbidden means the page exists but you're not allowed in right now, which is exactly
the case. 404 would claim the page doesn't exist. (401 is for missing credentials in an
HTTP auth header; the wearable session isn't that kind of login.)

## Security notes for judges

- The session cookie is HttpOnly and only issued to the browser that started the unlock
  (one-time claim secret), so another tab or machine can't pick it up.
- Access is re-checked on every request: once the wearable reports it's off, the existing
  cookie stops working immediately.
- Approvals are signed by the ATECC608 chip (ECDSA P-256) and verified on the laptop, so
  software on the laptop can't fake an approval. Status lines aren't signed yet.

## Next

- Live at https://noheartnocart.biz through a Cloudflare Tunnel; Capital One Nessie as the bank.
