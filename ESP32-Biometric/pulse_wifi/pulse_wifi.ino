/*
  Pulse Wi-Fi wearable (FeatherS3 / ESP32-S3)

  - Polls https://noheartnocart.biz/api/device/next for a payment or unlock to approve
  - Heartbeat (ECG) = "device is worn" + "live heartbeat"; then fingerprint = identity
  - Signs approvals with the ATECC608: SHA-256(line + "|authorized")
  - Sends status every second (continuous lock) and shows clean text on the 128x32 OLED
  - When idle, shows the latest payment on the OLED

  Backend: DEVICE_MODE=wifi in Payment-Portal/backend/.env
  Libraries: ArduinoJson (v7), Adafruit SSD1306, Adafruit GFX, SparkFun ATECCX08a
  PLUS whatever your ECG + fingerprint code already uses.

  TEAMMATE: only fill in section 2 (copy from your working sensor code). Nothing else needs changing.
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <SparkFun_ATECCX08a_Arduino_Library.h>

// ---------------- 1. settings ----------------
const char* WIFI_SSID  = "YOUR_WIFI_NAME";
const char* WIFI_PASS  = "YOUR_WIFI_PASSWORD";
const char* BASE_URL   = "https://noheartnocart.biz";
const char* DEVICE_KEY = "";          // same as DEVICE_TOKEN in backend .env (leave "" if not set)
const unsigned long FINGER_TIMEOUT_MS = 20000;

// ---------------- 2. SENSORS: paste your working code here ----------------
// Put your ECG (MAX30003) and fingerprint (R503 / Fingerprint 3 Click) #includes,
// objects and pin numbers above or here, and their begin() calls in sensorsBegin().

void sensorsBegin() {
  // e.g. ecg.begin(); fingerSerial.begin(57600, SERIAL_8N1, RX_PIN, TX_PIN); finger.begin(57600);
}

// true if a real heartbeat is detected right now (leads on, beats in a sane range)
bool hasHeartbeat() {
  return false;  // REPLACE with your ECG code
}

// latest heart rate, or 0 if none
int currentBpm() {
  return 0;      // REPLACE with your ECG code
}

// wait up to timeoutMs for a finger; true only if it matches an enrolled print
bool fingerprintMatches(unsigned long timeoutMs) {
  return false;  // REPLACE with your fingerprint code
}

// ---------------- 3. OLED (128x32) ----------------
Adafruit_SSD1306 display(128, 32, &Wire, -1);
bool oledReady = false;

void showLines(const String& a, const String& b = "", const String& c = "") {
  if (!oledReady) return;
  display.clearDisplay();          // wipes the old pixels: this is what removes the garbage
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setTextWrap(false);
  display.setCursor(0, 0);  display.print(a.substring(0, 21));
  display.setCursor(0, 11); display.print(b.substring(0, 21));
  display.setCursor(0, 22); display.print(c.substring(0, 21));
  display.display();
}

// ---------------- 4. security chip ----------------
ATECCX08A atecc;
bool ateccReady = false;

// hex signature of SHA-256(line + "|authorized"), or "" if the chip isn't ready
String signApproval(const String& line) {
  if (!ateccReady) return "";
  String msg = line + "|authorized";
  uint8_t digest[32];
  atecc.sha256((uint8_t*)msg.c_str(), msg.length(), digest);
  if (!atecc.createSignature(digest)) return "";
  char out[129];
  for (int i = 0; i < 64; i++) sprintf(out + 2 * i, "%02X", atecc.signature[i]);
  out[128] = 0;
  return String(out);
}

// ---------------- 5. HTTP ----------------
WiFiClientSecure tls;

String httpGet(const String& path) {
  HTTPClient http;
  http.setTimeout(4000);
  if (!http.begin(tls, String(BASE_URL) + path)) return "";
  if (strlen(DEVICE_KEY)) http.addHeader("X-Device-Key", DEVICE_KEY);
  int code = http.GET();
  String body = (code == 200) ? http.getString() : "";
  if (code != 200) Serial.printf("GET %s -> %d\n", path.c_str(), code);
  http.end();
  return body;
}

void sendEvent(JsonDocument& doc) {
  String body;
  serializeJson(doc, body);
  HTTPClient http;
  http.setTimeout(4000);
  if (!http.begin(tls, String(BASE_URL) + "/api/device/event")) return;
  if (strlen(DEVICE_KEY)) http.addHeader("X-Device-Key", DEVICE_KEY);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  if (code != 200) Serial.printf("POST event -> %d\n", code);
  http.end();
}

void sendCheck(const String& id, const char* check, bool ok) {
  JsonDocument d;
  d["type"] = "check"; d["id"] = id; d["check"] = check; d["ok"] = ok;
  sendEvent(d);
}

void sendResult(const String& id, bool ok, const char* reason, const String& sig) {
  JsonDocument d;
  d["type"] = "result"; d["id"] = id; d["ok"] = ok;
  d["status"] = ok ? "authorized" : "denied";
  if (!ok) d["reason"] = reason;
  if (ok && sig.length()) d["sig"] = sig;
  sendEvent(d);
}

void sendStatus() {
  bool live = hasHeartbeat();
  JsonDocument d;
  d["type"] = "status";
  d["worn"] = live;          // the heartbeat proves the band is worn
  d["live"] = live;
  if (live) d["bpm"] = currentBpm(); else d["bpm"] = nullptr;
  sendEvent(d);
}

// ---------------- 6. approving a request ----------------
void approve(JsonDocument& req) {
  String id   = req["id"].as<String>();
  String line = req["line"].as<String>();
  String type = req["type"].as<String>();

  if (type == "payment_request") {
    showLines("Approve payment?", req["provider"].as<String>(), "$" + String(req["amount"].as<float>(), 2));
  } else {
    showLines("Unlock request", "Checking heartbeat...");
  }
  delay(800);

  // 1+2. heartbeat = worn + alive (give the ECG up to 8 s to lock on)
  showLines("Checking heartbeat", "Keep the pads on");
  bool live = false;
  unsigned long start = millis();
  while (millis() - start < 8000) {
    if (hasHeartbeat()) { live = true; break; }
    delay(200);
  }
  sendCheck(id, "liveness", live);   // backend also marks "device is worn"
  if (!live) {
    showLines("DENIED", "No heartbeat");
    sendResult(id, false, "no_heartbeat", "");
    delay(2000);
    return;
  }

  // 3. fingerprint
  showLines("Heartbeat " + String(currentBpm()) + " bpm", "Place your finger", "on the sensor");
  bool match = fingerprintMatches(FINGER_TIMEOUT_MS);
  sendCheck(id, "identity", match);
  if (!match) {
    showLines("DENIED", "Fingerprint", "didn't match");
    sendResult(id, false, "wrong_finger", "");
    delay(2000);
    return;
  }

  // sign and approve
  String sig = signApproval(line);
  sendResult(id, true, "", sig);
  showLines("APPROVED", sig.length() ? "Signed by chip" : "(not signed)");
  delay(2000);
}

// ---------------- 7. idle screen: latest payment ----------------
void showLatestPayment() {
  String body = httpGet("/api/payments?limit=1");
  if (!body.length()) { showLines("Pulse", "Waiting for server"); return; }
  JsonDocument doc;
  if (deserializeJson(doc, body) || !doc.is<JsonArray>() || doc.as<JsonArray>().size() == 0) {
    showLines("Pulse", "No payments yet");
    return;
  }
  JsonObject p = doc[0];
  String status = p["status"].as<String>();
  status.toUpperCase();
  showLines(p["provider"].as<String>(), "$" + String(p["amount"].as<float>(), 2), status);
}

// ---------------- setup / loop ----------------
void setup() {
  Serial.begin(115200);
  Wire.begin();

  oledReady = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  if (oledReady) { display.clearDisplay(); display.display(); }
  showLines("Pulse", "Starting...");

  if (atecc.begin()) {
    atecc.readConfigZone(false);
    ateccReady = atecc.configLockStatus && atecc.dataOTPLockStatus && atecc.slot0LockStatus;
  }
  Serial.println(ateccReady ? "atecc ready" : "atecc not ready: approvals unsigned");

  sensorsBegin();

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  showLines("Pulse", "Connecting Wi-Fi...");
  while (WiFi.status() != WL_CONNECTED) delay(300);
  tls.setInsecure();   // fine for a hackathon demo; the approval itself is signed by the chip
  showLines("Pulse", "Online", WiFi.localIP().toString());
  Serial.println("Wi-Fi connected");
}

unsigned long lastStatus = 0, lastPoll = 0, lastIdle = 0;

void loop() {
  if (WiFi.status() != WL_CONNECTED) { WiFi.reconnect(); delay(1000); return; }

  if (millis() - lastStatus > 1000) { lastStatus = millis(); sendStatus(); }

  if (millis() - lastPoll > 1000) {
    lastPoll = millis();
    String body = httpGet("/api/device/next");
    if (body.length()) {
      JsonDocument req;
      if (!deserializeJson(req, body) && strcmp(req["type"] | "none", "none") != 0) {
        approve(req);
        lastIdle = 0;   // refresh the idle screen right after
        return;
      }
    }
  }

  if (millis() - lastIdle > 3000) { lastIdle = millis(); showLatestPayment(); }
}
