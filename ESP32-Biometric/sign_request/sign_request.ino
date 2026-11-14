#include <SparkFun_ATECCX08a_Arduino_Library.h>
#include <Wire.h>

ATECCX08A atecc;

const size_t MAX_LEN = 512;
uint8_t buf[MAX_LEN + 16];
size_t len = 0;
uint8_t myHash[32];

bool checkPresence() { return true; }
bool checkLiveness() { return true; }
bool checkIdentity() { return true; }

void printHex(const uint8_t *d, size_t n) {
  for (size_t i = 0; i < n; i++) {
    if (d[i] < 0x10) Serial.print("0");
    Serial.print(d[i], HEX);
  }
}

bool extractId(char *out, size_t outSize) {
  buf[len] = 0;
  char *p = strstr((char *)buf, "\"id\":\"");
  if (!p) return false;
  p += 6;
  char *e = strchr(p, '"');
  if (!e || (size_t)(e - p) >= outSize) return false;
  memcpy(out, p, e - p);
  out[e - p] = 0;
  return true;
}

void signRequest() {
  char id[64];
  if (!extractId(id, sizeof(id))) {
    Serial.println("{\"type\":\"result\",\"ok\":false,\"status\":\"bad_request\"}");
    return;
  }
  if (!(checkPresence() && checkLiveness() && checkIdentity())) {
    Serial.print("{\"type\":\"result\",\"id\":\"");
    Serial.print(id);
    Serial.println("\",\"ok\":false,\"status\":\"denied\"}");
    return;
  }
  const char *suffix = "|authorized";
  memcpy(buf + len, suffix, strlen(suffix));
  atecc.sha256(buf, len + strlen(suffix), myHash);
  if (!atecc.createSignature(myHash)) {
    Serial.print("{\"type\":\"result\",\"id\":\"");
    Serial.print(id);
    Serial.println("\",\"ok\":false,\"status\":\"sign_failed\"}");
    return;
  }
  Serial.print("{\"type\":\"result\",\"id\":\"");
  Serial.print(id);
  Serial.print("\",\"ok\":true,\"status\":\"authorized\",\"sig\":\"");
  printHex(atecc.signature, 64);
  Serial.println("\"}");
}

void setup() {
  Serial.begin(115200);
  unsigned long t = millis();
  while (!Serial && millis() - t < 3000) delay(10);
#if defined(ARDUINO_ARCH_ESP32)
  Wire.setBufferSize(256);
#endif
  Wire.begin();
  Wire.setClock(100000);
  delay(500);

  bool ok = false;
  for (int i = 0; i < 5 && !ok; i++) {
    ok = atecc.begin();
    if (!ok) delay(300);
  }
  if (!ok) {
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
      if (len > 0) signRequest();
      len = 0;
    } else if (len < MAX_LEN) {
      buf[len++] = c;
    }
  }
}