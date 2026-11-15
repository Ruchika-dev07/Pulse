# Wi-Fi wearable protocol

Set in `backend/.env`:
```
DEVICE_MODE=wifi
DEVICE_TOKEN=pick-a-long-random-string     # optional but recommended
```
Base URL: `https://noheartnocart.biz` (or `http://<laptop-ip>:8000` on the same network).
Every request sends header `X-Device-Key: <DEVICE_TOKEN>`.

## 1. Poll for work (about once a second)
`GET /api/device/next`

Nothing waiting:
```json
{"type":"none"}
```
A payment waiting:
```json
{"type":"payment_request","id":"<uuid>","provider":"Amazon.com","requester":"Ruchika","amount":20.0,
 "line":"{\"type\":\"payment_request\",\"id\":\"<uuid>\",\"provider\":\"Amazon.com\",\"requester\":\"Ruchika\",\"amount\":20.0}"}
```
An unlock waiting:
```json
{"type":"session_request","id":"<uuid>","line":"{\"type\":\"session_request\",\"id\":\"<uuid>\"}"}
```
Each request is handed out once (offered again after 20 s if no result arrives).
Show amount + provider on the OLED, then run presence -> liveness -> identity.

## 2. Report back
`POST /api/device/event` with `Content-Type: application/json`. Same messages as the USB protocol:

Optional progress (lights up each row on the website):
```json
{"type":"check","id":"<uuid>","check":"presence","ok":true}
```
Final result, approved. `sig` = ATECC608 signature over **SHA-256(line + "|authorized")**,
where `line` is the exact string from step 1:
```json
{"type":"result","id":"<uuid>","ok":true,"status":"authorized","sig":"<128 hex>"}
```
Final result, denied (no sig):
```json
{"type":"result","id":"<uuid>","ok":false,"status":"denied","reason":"wrong_finger"}
```
reason: `not_present` | `no_heartbeat` | `wrong_finger` | `user_rejected` | `device_error`

Continuous check, every second (locks the site when the band comes off):
```json
{"type":"status","worn":true,"live":true,"bpm":72}
```

## Arduino sketch outline (ESP32, HTTPClient + ArduinoJson + SparkFun ATECC)
```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>

const char* BASE = "https://noheartnocart.biz";
const char* DEVICE_KEY = "pick-a-long-random-string";
WiFiClientSecure tls;

String httpGet(const String& path) {
  HTTPClient http; http.begin(tls, String(BASE) + path);
  http.addHeader("X-Device-Key", DEVICE_KEY);
  int code = http.GET(); String body = code == 200 ? http.getString() : "";
  http.end(); return body;
}
void httpPostJson(const String& path, JsonDocument& doc) {
  String body; serializeJson(doc, body);
  HTTPClient http; http.begin(tls, String(BASE) + path);
  http.addHeader("X-Device-Key", DEVICE_KEY);
  http.addHeader("Content-Type", "application/json");
  http.POST(body); http.end();
}
void sendEvent(JsonDocument& doc) { httpPostJson("/api/device/event", doc); }

void setup() { /* WiFi.begin(...); */ tls.setInsecure(); /* atecc.begin(); sensors... */ }

unsigned long lastStatus = 0, lastPoll = 0;
void loop() {
  if (millis() - lastStatus > 1000) {           // continuous check
    lastStatus = millis();
    JsonDocument s; s["type"] = "status"; s["worn"] = isTouching();
    s["live"] = isTouching() && hasHeartbeat(); s["bpm"] = currentBpm();
    sendEvent(s);
  }
  if (millis() - lastPoll > 1000) {             // look for work
    lastPoll = millis();
    JsonDocument req; if (deserializeJson(req, httpGet("/api/device/next"))) return;
    if (strcmp(req["type"] | "none", "none") == 0) return;
    String id = req["id"].as<String>(); String line = req["line"].as<String>();
    // show req["amount"], req["provider"] on the OLED here
    runChecksAndReport(id, line);               // presence -> liveness -> identity, then result (+ sig)
  }
}
// In runChecksAndReport: on success, sign SHA-256(line + "|authorized") with the ATECC608
// (same as pulse_protocol.ino's signApproval) and send {"type":"result","id":id,"ok":true,"status":"authorized","sig":hex}.
```
