#include <SparkFun_ATECCX08a_Arduino_Library.h>
#include <Wire.h>

ATECCX08A atecc;

const size_t MAX_LEN = 512;
uint8_t buf[MAX_LEN];
size_t len = 0;
uint8_t myHash[32];

void printHex(const uint8_t *d, size_t n) {
  for (size_t i = 0; i < n; i++) {
    if (d[i] < 0x10) Serial.print("0");
    Serial.print(d[i], HEX);
  }
}

void signRequest() {
  atecc.sha256(buf, len, myHash);
  if (!atecc.createSignature(myHash)) {
    Serial.println("{\"error\":\"sign_failed\"}");
    return;
  }
  Serial.print("{\"hash\":\"");
  printHex(myHash, 32);
  Serial.print("\",\"sig\":\"");
  printHex(atecc.signature, 64);
  Serial.println("\"}");
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
      if (len > 0) signRequest();
      len = 0;
    } else if (len < MAX_LEN) {
      buf[len++] = c;
    }
  }
}