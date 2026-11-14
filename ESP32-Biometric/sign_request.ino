#include <SparkFun_ATECCX08a_Arduino_Library.h>
#include <Wire.h>

#define DEMO_ALL_PASS 1   // set to 0 once the biometric checks are real

ATECCX08A atecc;

const size_t MAX_LEN = 512;
const char SUFFIX[] = "|authorized";
uint8_t buf[MAX_LEN + sizeof(SUFFIX)];
size_t len = 0;
bool overflow = false;
uint8_t myHash[32];

// TODO: biometric teammate replaces these three
bool checkPresence() { return DEMO_ALL_PASS; }
bool checkLiveness() { return DEMO_ALL_PASS; }
bool checkIdentity() { return DEMO_ALL_PASS; }

void printHex(const uint8_t *d, size_t n) {
  for (size_t i = 0; i < n; i++) {
    if (d[i] < 0x10) Serial.print("0");
    Serial.print(d[i], HEX);
  }
}

void extractId(char *out, size_t outSize) {
  out[0] = 0;
  buf[len] = 0;
  const char *p = strstr((const char *)buf, "\"id\":\"");
  if (!p) return;
  p += 6;
  size_t i = 0;
  while (*p && *p != '"' && i < outSize - 1) out[i++] = *p++;
  out[i] = 0;
}

void sendResult(const char *id, bool ok, const char *status, bool withSig) {
  Serial.print("{\"type\":\"result\",\"id\":\"");
  Serial.print(id);
  Serial.print("\",\"ok\":");
  Serial.print(ok ? "true" : "false");
  Serial.print(",\"status\":\"");
  Serial.print(status);
  Serial.print("\"");
  if (withSig) {
    Serial.print(",\"sig\":\"");
    printHex(atecc.signature, 64);
    Serial.print("\"");
  }
  Serial.println("}");
}

void handleRequest() {
  char id[65];
  extractId(id, sizeof(id));

  bool presence = checkPresence();
  bool liveness = checkLiveness();
  bool identity = checkIdentity();

  if (!(presence && liveness && identity)) {
    sendResult(id, false, "denied", false);
    return;
  }

  size_t sl = sizeof(SUFFIX) - 1;
  memcpy(buf + len, SUFFIX, sl);
  atecc.sha256(buf, len + sl, myHash);

  if (!atecc.createSignature(myHash)) {
    sendResult(id, false, "sign_failed", false);
    return;
  }
  sendResult(id, true, "authorized", true);
}

void setup() {
  Wire.begin();
  Serial.begin(115200);
  while (!Serial) delay(10);

  if (!atecc.begin()) {
    Serial.println("Device not found. Check wiring.");
    while (1);
  }
  atecc.readConfigZone(false);
  if (!(atecc.configLockStatus && atecc.dataOTPLockStatus && atecc.slot0LockStatus)) {
    Serial.println("Device not configured.");
    while (1);
  }
  Serial.println("READY");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (overflow) {
        Serial.println("{\"type\":\"result\",\"id\":\"\",\"ok\":false,\"status\":\"too_long\"}");
      } else if (len > 0) {
        handleRequest();
      }
      len = 0;
      overflow = false;
    } else if (len < MAX_LEN) {
      buf[len++] = c;
    } else {
      overflow = true;
    }
  }
}