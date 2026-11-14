#include <SparkFun_ATECCX08a_Arduino_Library.h>
#include <Wire.h>

ATECCX08A atecc;

uint8_t message[32] = {
  0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,
  0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F
};

void setup() {
  Wire.begin();
  Serial.begin(115200);
  while (!Serial) delay(10);

  if (atecc.begin() == true) {
    Serial.println("Successful wakeUp(). I2C connections are good.");
  } else {
    Serial.println("Device not found. Check wiring.");
    while (1);
  }

  atecc.readConfigZone(false);
  if (!(atecc.configLockStatus && atecc.dataOTPLockStatus && atecc.slot0LockStatus)) {
    Serial.println("Device not configured. Please use the configuration sketch.");
    while (1);
  }

  atecc.createSignature(message);
}

void loop() {
}