/*
  Pulse serial protocol: reference sketch for the ESP32 (FeatherS3).

  This handles the laptop <-> wearable protocol so the web app works end to end.
  The four sensor functions at the top are placeholders: replace their bodies with the
  real drivers (touch pad, ECG 3 Click, R503 fingerprint). Everything else can stay.

  Library: ArduinoJson v7 (Library Manager: "ArduinoJson" by Benoit Blanchon)
           SparkFun ATECCX08a Arduino Library (for V2 signing)
  Serial:  115200 baud, one JSON object per line.

  V2 signing (USE_ATECC 1): when all three checks pass, the ATECC608 signs
      SHA-256( <the exact request line received, no newline> + "|authorized" )
  and the result line carries "sig": 128 hex chars (r||s). The laptop checks it with the
  chip's public key (backend/device_key.py). Set USE_ATECC 0 to run without the chip.
*/

#define USE_ATECC 1

#include <ArduinoJson.h>
#if USE_ATECC
#include <Wire.h>
#include <SparkFun_ATECCX08a_Arduino_Library.h>
ATECCX08A atecc;
bool ateccReady = false;
#endif

// ---------------- replace these with the real sensors ----------------

bool isTouching()        { return true; }   // copper pad: skin contact (touchRead below threshold)
bool hasHeartbeat()      { return true; }   // ECG: 3 beats in 6 s, 40-180 bpm, leads on
int  currentBpm()        { return 72; }     // ECG: latest heart rate
bool fingerprintMatches(unsigned long timeoutMs) {   // R503: wait for a finger, true if enrolled match
  // While waiting for the finger, keep calling sendStatusIfDue() so the laptop keeps getting
  // status lines. More than 4 s of silence locks an open session (SIGNAL_TIMEOUT on the laptop).
  delay(1500);
  return true;
}

// ---------------------------------------------------------------------

const unsigned long STATUS_EVERY_MS = 1000;
unsigned long lastStatus = 0;
String line;

void sendStatus();
void sendStatusIfDue() {
  if (millis() - lastStatus >= STATUS_EVERY_MS) { lastStatus = millis(); sendStatus(); }
}

void sendJson(JsonDocument& doc) {
  serializeJson(doc, Serial);
  Serial.println();
}

void sendCheck(const char* id, const char* check, bool ok) {
  JsonDocument d;
  d["type"] = "check"; d["id"] = id; d["check"] = check; d["ok"] = ok;
  sendJson(d);
}

// Sign SHA-256(requestLine + "|authorized") with the chip. Writes 128 hex chars into out.
bool signApproval(const String& requestLine, char* out) {
#if USE_ATECC
  if (!ateccReady) return false;
  String msg = requestLine + "|authorized";
  uint8_t digest[32];
  atecc.sha256((uint8_t*)msg.c_str(), msg.length(), digest);
  if (!atecc.createSignature(digest)) return false;
  for (int i = 0; i < 64; i++) sprintf(out + 2 * i, "%02X", atecc.signature[i]);
  out[128] = '\0';
  return true;
#else
  return false;
#endif
}

void sendResult(const char* id, bool ok, const char* reason, const String& requestLine) {
  JsonDocument d;
  d["type"] = "result"; d["id"] = id; d["ok"] = ok;
  d["status"] = ok ? "authorized" : "denied";
  if (!ok) d["reason"] = reason;
  if (ok) {
    char sig[129];
    if (signApproval(requestLine, sig)) d["sig"] = sig;   // only approvals are signed
  }
  sendJson(d);
}

void sendStatus() {
  bool worn = isTouching();
  bool live = worn && hasHeartbeat();
  JsonDocument d;
  d["type"] = "status"; d["worn"] = worn; d["live"] = live;
  if (live) d["bpm"] = currentBpm(); else d["bpm"] = nullptr;
  sendJson(d);
}

// Runs presence -> liveness -> identity, reporting each step, then the (signed) result.
// requestLine is the exact line received; the signature covers it.
void runChecks(const char* id, const String& requestLine) {
  bool worn = isTouching();
  sendCheck(id, "presence", worn);
  if (!worn) { sendResult(id, false, "not_present", requestLine); return; }

  bool live = hasHeartbeat();
  sendCheck(id, "liveness", live);
  if (!live) { sendResult(id, false, "no_heartbeat", requestLine); return; }

  bool match = fingerprintMatches(20000);
  sendCheck(id, "identity", match);
  if (!match) { sendResult(id, false, "wrong_finger", requestLine); return; }

  sendResult(id, true, nullptr, requestLine);
}

void handleLine(const String& text) {
  JsonDocument in;
  if (deserializeJson(in, text)) return;          // not JSON: ignore
  const char* type = in["type"] | "";
  const char* id = in["id"] | "";

  if (strcmp(type, "payment_request") == 0) {
    // TODO (screen): show amount + provider on the OLED before asking for the finger
    // e.g. "Approve $500.00 Amazon.com"
    float amount = in["amount"] | 0.0;
    const char* provider = in["provider"] | "";
    Serial.printf("# payment %.2f to %s\n", amount, provider);   // '#' lines are ignored by the laptop
    runChecks(id, text);
  } else if (strcmp(type, "session_request") == 0) {
    // TODO (screen): show "Unlock site?"
    runChecks(id, text);
  } else if (strcmp(type, "cancel") == 0) {
    // TODO: abort a fingerprint wait in progress
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
#if USE_ATECC
  Wire.begin();
  if (atecc.begin()) {
    atecc.readConfigZone(false);
    ateccReady = atecc.configLockStatus && atecc.dataOTPLockStatus && atecc.slot0LockStatus;
  }
  Serial.println(ateccReady ? "# atecc ready: approvals will be signed" : "# atecc not found or not locked: approvals unsigned");
#endif
  Serial.println("# pulse ready");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handleLine(line); line = ""; }
    else if (c != '\r') line += c;
    if (line.length() > 512) line = "";           // guard against garbage
  }

  sendStatusIfDue();                               // continuous check: one status line per second
}
