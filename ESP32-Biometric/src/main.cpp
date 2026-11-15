/**
 * Pulse — fingerprint registration + authentication with ECG (MAX30003).
 *
 * Registration (menu): enroll R503 with 4 place/lift samples.
 * Authentication (default once at least one finger is stored):
 *   1) detect finger  2) verify match  3) request ECG
 *   4–5) MAX30003 R–R / BPM (as milestones land)  6) report over USB serial
 *
 * ECG milestones in this build:
 *   1) SPI detect via INFO register  — implemented
 *   2) configureEcg()                — implemented, used after match
 *   3) electrode / leadsOn()         — best-effort
 *   4) BPM from RTOR                 — best-effort after short wait
 */

#include <Arduino.h>
#include <R503Lib.h>
#include <MAX30003.h>

static const uint8_t R503_RX_PIN = 44;
static const uint8_t R503_TX_PIN = 43;
static const uint8_t ENROLL_SAMPLES = 4;
static const uint32_t AUTH_FINGER_TIMEOUT_MS = 30000;
static const uint32_t ECG_BPM_WAIT_MS = 6000;

// Feather Click Shield seating + continuity-verified SPI map (Kevin, 2026-11-14):
//   GPIO36 → ECG SCK, GPIO35 → SDI, GPIO37 → SDO, GPIO3 → CS
// (Shield docs say CS1=A5 / CS2=D5; measured CS net is GPIO3 on this FeatherS3.)
static const uint8_t ECG_SCK = 36;
static const uint8_t ECG_MOSI = 35;
static const uint8_t ECG_MISO = 37;
static const uint8_t ECG_CS_MEASURED = 3;       // continuity: Feather GPIO3 → ECG CS
static const uint8_t ECG_CS_SOCKET2 = 33;       // silk D5 / CS2 (doc)
static const uint8_t ECG_CS_SOCKET1_SILK = 5;   // silk A5 / CS1 (doc)
static const uint8_t ECG_CS_ARDUINO_A5 = A5;    // GPIO6
static uint8_t ecgCsPin = ECG_CS_MEASURED;
MAX30003 ecg(ECG_CS_MEASURED, ECG_SCK, ECG_MOSI, ECG_MISO);
R503Lib fps(&Serial1, R503_RX_PIN, R503_TX_PIN, 0xFFFFFFFF);

bool probeEcgCs(uint8_t csPin);

enum Mode : uint8_t { MODE_AUTH = 0, MODE_MENU = 1 };
Mode mode = MODE_MENU;
bool fpsPresent = false;
bool ecgPresent = false;
bool ecgConfigured = false;

void printHelp();
void enterMenu();
void enterAuth();
void runAuthOnce();
void handleMenuCommand(char action);
void enrollFinger();
void searchFinger();
void deleteFinger();
void printStoredIds();
void clearLibrary();
void ecgStatusDump();
bool waitForFinger(uint8_t sampleIndex);
bool waitForFingerLift();
int16_t nextFreeLocation();
uint16_t templateCount();
String readLine();
void reportAuth(bool fingerOk, uint16_t fingerId, uint16_t confidence, bool ecgOk,
                uint16_t bpm, uint16_t rrMs, const char *detail);

void setup() {
  Serial.begin(115200);
  // USB CDC: wait until the host opens the port so the boot banner isn't lost
  unsigned long serialWait = millis();
  while (!Serial && (millis() - serialWait < 5000)) {
    delay(10);
  }
  delay(500);

  Serial.println();
  Serial.println("================================");
  Serial.println("PULSE - auth + ECG bring-up");
  Serial.println("================================");
  Serial.println("(serial ready — type h for help)");
  Serial.println("Fingerprint is OPTIONAL — menu works without R503 for ECG bring-up.");

  // --- ECG first so fingerprint absence never blocks SPI bring-up ---
  Serial.println();
  Serial.println("ECG 3 Click hardware checklist:");
  Serial.println("  CLK SEL jumper  -> INT (right, onboard crystal)");
  Serial.println("  PWR SEL jumper  -> 3V3 (left)");
  Serial.println("  Seating         -> ECG on mikroBUS2 (preferred)");
  Serial.printf("Bit-bang SPI SCK=%u MOSI=%u MISO=%u\n", ECG_SCK, ECG_MOSI, ECG_MISO);

  ecgPresent = false;
  if (probeEcgCs(ECG_CS_MEASURED)) {
    ecgPresent = true;
    Serial.printf(" >> MAX30003 on measured CS GPIO%u  INFO=0x%06lX\n", ecgCsPin,
                  (unsigned long)ecg.info());
  } else if (probeEcgCs(ECG_CS_SOCKET2)) {
    ecgPresent = true;
    Serial.printf(" >> MAX30003 on CS2/D5 (GPIO%u)  INFO=0x%06lX\n", ecgCsPin,
                  (unsigned long)ecg.info());
  } else if (probeEcgCs(ECG_CS_SOCKET1_SILK)) {
    ecgPresent = true;
    Serial.printf(" >> MAX30003 on CS1/silk-A5 (GPIO%u)  INFO=0x%06lX\n", ecgCsPin,
                  (unsigned long)ecg.info());
  } else if (probeEcgCs(ECG_CS_ARDUINO_A5)) {
    ecgPresent = true;
    Serial.printf(" >> MAX30003 on Arduino-A5 alias (GPIO%u)  INFO=0x%06lX\n", ecgCsPin,
                  (unsigned long)ecg.info());
  } else {
    Serial.println(" [!] MAX30003 not detected (tried GPIO 3, 33, 5, 6)");
  }

  if (ecgPresent) {
    ecgConfigured = ecg.configureEcg();
    Serial.println(ecgConfigured ? " >> MAX30003 configured" : " [!] configure failed");
  }

  // --- Fingerprint optional ---
  uint8_t rc = fps.begin(57600, 0x0);
  fpsPresent = (rc == R503_OK);
  if (fpsPresent) {
    Serial.println(" >> R503 ready");
    fps.setAuraLED(aLEDBreathing, aLEDBlue, 255, 1);
  } else {
    Serial.printf(" [!] R503 not found (0x%02X) — continuing without fingerprint\n", rc);
    Serial.println("     Plug Fingerprint Click when ready; menu + ECG still work.");
  }

  uint16_t count = fpsPresent ? templateCount() : 0;
  Serial.printf("    Stored fingerprints: %u\n", count);
  Serial.println("Type m for menu, g for ECG status, a for auth, h for help.");

  // Always land in menu when no finger sensor / no templates — ECG bring-up friendly
  if (!fpsPresent || count == 0) {
    enterMenu();
  } else {
    enterAuth();
  }
}

void loop() {
  if (Serial.available()) {
    String line = readLine();
    if (line.length() >= 1) {
      char c = tolower(line[0]);
      if (c == 'm') {
        enterMenu();
        return;
      }
      if (c == 'a') {
        enterAuth();
        return;
      }
      if (c == 'h' || c == '?') {
        printHelp();
        return;
      }
      if (mode == MODE_MENU) {
        handleMenuCommand(c);
        return;
      }
      // In auth mode, other keys show help
      printHelp();
    }
  }

  if (mode == MODE_AUTH) {
    runAuthOnce();
  } else {
    delay(20);
  }
}

void printHelp() {
  Serial.println();
  Serial.println("== HELP =================================");
  Serial.println("[a] Authentication mode (default)");
  Serial.println("[m] Registration / management menu");
  Serial.println("Menu keys: e enroll | s search | d delete | p print | c clear | g ECG status");
  Serial.println("Auth flow: finger -> verify -> ECG INFO/BPM -> USB report");
}

void enterMenu() {
  mode = MODE_MENU;
  Serial.println();
  Serial.println("== REGISTRATION MENU ====================");
  Serial.println("[e] Enroll fingerprint (4 place/lift)");
  Serial.println("[s] Search / match");
  Serial.println("[d] Delete one ID");
  Serial.println("[p] Print stored IDs");
  Serial.println("[c] Clear library (type YES)");
  Serial.println("[g] ECG SPI / status dump");
  Serial.println("[t] ECG MISO activity test (CS low + clocks)");
  Serial.println("[a] Return to authentication");
  Serial.println("== ENTER ACTION =========================");
}

void enterAuth() {
  if (!fpsPresent) {
    Serial.println("[X] No R503 — auth needs Fingerprint Click. Staying in menu.");
    enterMenu();
    return;
  }
  if (templateCount() == 0) {
    Serial.println("[X] Enroll a finger first (type m, then e)");
    enterMenu();
    return;
  }
  mode = MODE_AUTH;
  fps.setAuraLED(aLEDBreathing, aLEDBlue, 50, 255);
  Serial.println();
  Serial.println("== AUTH MODE ============================");
  Serial.println("Place enrolled finger on the sensor...");
}

void runAuthOnce() {
  if (!fpsPresent) {
    enterMenu();
    return;
  }
  unsigned long start = millis();
  uint8_t rc = R503_NO_FINGER;

  while (millis() - start < AUTH_FINGER_TIMEOUT_MS) {
    if (Serial.available()) {
      return;  // let loop() handle menu switch
    }
    rc = fps.takeImage();
    if (rc == R503_NO_FINGER) {
      delay(80);
      continue;
    }
    break;
  }

  if (rc != R503_OK) {
    // Quiet timeout — keep waiting
    return;
  }

  Serial.println(" >> Finger detected");
  fps.setAuraLED(aLEDBreathing, aLEDYellow, 150, 255);

  rc = fps.extractFeatures(1);
  if (rc != R503_OK) {
    Serial.printf("[X] extractFeatures failed (0x%02X)\n", rc);
    fps.setAuraLED(aLEDFlash, aLEDRed, 50, 3);
    reportAuth(false, 0, 0, false, 0, 0, "feature_fail");
    delay(500);
    enterAuth();
    return;
  }

  uint16_t location = 0;
  uint16_t confidence = 0;
  rc = fps.searchFinger(1, location, confidence);

  if (rc != R503_OK) {
    Serial.println(" >> Fingerprint REJECTED");
    fps.setAuraLED(aLEDBreathing, aLEDRed, 255, 1);
    reportAuth(false, 0, 0, false, 0, 0, "no_match");
    waitForFingerLift();
    enterAuth();
    return;
  }

  Serial.printf(" >> Fingerprint OK  id=%u  conf=%u\n", location, confidence);
  fps.setAuraLED(aLEDBreathing, aLEDGreen, 255, 1);

  // --- ECG request after successful fingerprint ---
  bool ecgOk = false;
  uint16_t bpm = 0;
  uint16_t rrMs = 0;
  const char *detail = "finger_ok";

  if (!ecgPresent) {
    detail = "finger_ok_ecg_missing";
    Serial.println(" >> ECG skipped (sensor not on SPI)");
  } else {
    // Milestone 1: re-check INFO
    if (!ecg.detect()) {
      detail = "finger_ok_ecg_spi_fail";
      Serial.println(" [X] ECG SPI lost");
    } else {
      Serial.printf(" >> ECG SPI OK  INFO=0x%06lX\n", (unsigned long)ecg.info());
      if (!ecgConfigured) {
        ecgConfigured = ecg.configureEcg();
      }

      // Milestone 3: leads
      bool leads = ecg.leadsOn();
      Serial.println(leads ? " >> Electrodes look connected" : " [!] Lead-off / weak electrode contact");

      // Milestone 4: wait briefly for RTOR
      Serial.println(" >> Measuring heart rate...");
      unsigned long t0 = millis();
      while (millis() - t0 < ECG_BPM_WAIT_MS) {
        if (ecg.readHeartRate(bpm, rrMs)) {
          ecgOk = true;
          break;
        }
        delay(200);
      }

      if (ecgOk) {
        Serial.printf(" >> Heart rate %u BPM  (R-R %u ms)\n", bpm, rrMs);
        detail = leads ? "ok" : "ok_lead_warn";
      } else {
        detail = leads ? "finger_ok_ecg_no_rr" : "finger_ok_ecg_no_leads";
        Serial.println(" [!] No R-R / BPM yet (check electrodes, wait longer)");
      }
    }
  }

  reportAuth(true, location, confidence, ecgOk, bpm, rrMs, detail);
  waitForFingerLift();
  delay(400);
  enterAuth();
}

void reportAuth(bool fingerOk, uint16_t fingerId, uint16_t confidence, bool ecgOk,
                uint16_t bpm, uint16_t rrMs, const char *detail) {
  // One JSON object per line for laptop / Pulse bridge
  Serial.printf(
      "{\"type\":\"auth\",\"finger_ok\":%s,\"finger_id\":%u,\"confidence\":%u,"
      "\"ecg_ok\":%s,\"bpm\":%u,\"rr_ms\":%u,\"ecg_info\":\"0x%06lX\",\"detail\":\"%s\"}\n",
      fingerOk ? "true" : "false", fingerId, confidence, ecgOk ? "true" : "false", bpm,
      rrMs, (unsigned long)ecg.info(), detail);
}

void handleMenuCommand(char action) {
  Serial.printf(" > %c\n", action);
  Serial.println("----------------------------------------");
  switch (action) {
    case 'e':
    case 's':
    case 'd':
    case 'p':
    case 'c':
      if (!fpsPresent) {
        Serial.println("[X] Fingerprint Click not present — ECG-only mode. Use [g].");
        break;
      }
      if (action == 'e') enrollFinger();
      else if (action == 's') searchFinger();
      else if (action == 'd') deleteFinger();
      else if (action == 'p') printStoredIds();
      else clearLibrary();
      break;
    case 'g':
      ecgStatusDump();
      break;
    case 't': {
      Serial.println("MISO activity test (PSRAM disabled in this build):");
      ecg.setCsPin(ECG_CS_MEASURED);
      ecg.dumpPins(Serial);
      for (uint8_t mode : {0, 3}) {
        ecg.setSpiMode(mode);
        uint16_t highs = ecg.countMisoHighs(64);
        Serial.printf("  mode %u: MISO high on %u/64 clocks\n", mode, highs);
      }
      // Also try INFO again after the hammer
      ecg.setSpiMode(0);
      bool ok = ecg.detect();
      Serial.printf("  re-detect INFO=0x%06lX %s\n", (unsigned long)ecg.info(),
                    ok ? "OK" : "fail");
      if (!ok) {
        Serial.println("  If highs=0: GPIO37 likely stuck low (measure Click 3V3 with");
        Serial.println("  power on — must be ~3.3V). Or jumper SDO to a free pin");
        Serial.println("  (e.g. GPIO12) and we will remap MISO.");
      }
      break;
    }
    default:
      Serial.printf("[X] Unknown '%c'\n", action);
      break;
  }
  enterMenu();
}

bool probeEcgCs(uint8_t csPin) {
  ecg.setCsPin(csPin);
  static bool spiStarted = false;
  bool ok;
  if (!spiStarted) {
    ok = ecg.begin();
    spiStarted = true;
  } else {
    (void)ecg.readReg(MAX30003_STATUS);
    ok = ecg.detect();
  }
  uint32_t info = ecg.info();
  uint32_t st = ecg.status();
  Serial.printf("  probe CS GPIO%u -> INFO=0x%06lX STATUS=0x%06lX MISO=%d %s\n",
                csPin, (unsigned long)info, (unsigned long)st, ecg.misoLevel(),
                ok ? "OK" : "fail");
  if (ok) {
    ecgCsPin = csPin;
  }
  return ok;
}

void ecgStatusDump() {
  Serial.println("ECG SPI status (bit-bang, no fingerprint needed):");
  Serial.println("  CLK SEL=INT (right), PWR SEL=3V3 (left)");
  ecg.dumpPins(Serial);

  const uint8_t pins[] = {ECG_CS_MEASURED, ECG_CS_SOCKET2, ECG_CS_SOCKET1_SILK,
                          ECG_CS_ARDUINO_A5};
  const char *names[] = {"measured-GPIO3", "CS2/D5", "CS1/silk-A5", "Arduino-A5-alias"};
  bool any = false;
  for (int i = 0; i < 4; i++) {
    ecg.setCsPin(pins[i]);
    bool ok = ecg.detect();
    Serial.printf("  %s GPIO%u INFO=0x%06lX STATUS=0x%06lX MISO=%d %s\n", names[i],
                  pins[i], (unsigned long)ecg.info(), (unsigned long)ecg.status(),
                  ecg.misoLevel(), ok ? "OK" : "fail");
    if (ok && !any) {
      ecgCsPin = pins[i];
      any = true;
    }
  }

  if (any) {
    ecg.setCsPin(ecgCsPin);
  }
  ecgPresent = any;

  if (!any) {
    Serial.println();
    Serial.println("SPI still dead. Next hardware check:");
    Serial.println("  1) Power Feather Click Shield from its USB-C (blue PWR LED),");
    Serial.println("     not only the Feather USB, so Click rails are solid.");
    Serial.println("  2) Move ECG Click to mikroBUS1, run g again (look for GPIO5 OK).");
    Serial.println("  3) Bypass shield: jumpers from ECG Click to Feather:");
    Serial.println("       Click CS  -> Feather pin 5 (GPIO5) or 33");
    Serial.println("       Click SCK -> Feather SCK (GPIO36)");
    Serial.println("       Click SDI -> Feather MOSI (GPIO35)");
    Serial.println("       Click SDO -> Feather MISO (GPIO37)");
    Serial.println("       Click 3V3/GND -> Feather 3V3/GND");
  } else {
    uint16_t bpm = 0, rr = 0;
    if (ecg.readHeartRate(bpm, rr)) {
      Serial.printf("  RTOR bpm=%u rr_ms=%u\n", bpm, rr);
    }
  }
}

String readLine() {
  String line = Serial.readStringUntil('\n');
  line.trim();
  return line;
}

uint16_t templateCount() {
  if (!fpsPresent) {
    return 0;
  }
  uint16_t count = 0;
  fps.getTemplateCount(count);
  return count;
}

bool waitForFinger(uint8_t sampleIndex) {
  fps.setAuraLED(aLEDBreathing, aLEDBlue, 50, 255);
  Serial.printf("\n >> Place finger for sample %u/%u...\n", sampleIndex, ENROLL_SAMPLES);
  while (true) {
    uint8_t rc = fps.takeImage();
    if (rc == R503_NO_FINGER) {
      delay(80);
      continue;
    }
    if (rc != R503_OK) {
      Serial.printf("[X] takeImage failed (0x%02X)\n", rc);
      fps.setAuraLED(aLEDFlash, aLEDRed, 50, 2);
      delay(400);
      continue;
    }
    fps.setAuraLED(aLEDBreathing, aLEDYellow, 255, 255);
    rc = fps.extractFeatures(sampleIndex);
    if (rc != R503_OK) {
      Serial.printf("[X] extractFeatures failed (0x%02X)\n", rc);
      fps.setAuraLED(aLEDFlash, aLEDRed, 50, 2);
      waitForFingerLift();
      continue;
    }
    Serial.printf(" >> Sample %u captured\n", sampleIndex);
    fps.setAuraLED(aLEDBreathing, aLEDGreen, 255, 255);
    return true;
  }
}

bool waitForFingerLift() {
  Serial.println(" >> Lift your finger");
  while (fps.takeImage() != R503_NO_FINGER) {
    delay(80);
  }
  delay(200);
  return true;
}

int16_t nextFreeLocation() {
  uint8_t table[32] = {0};
  if (fps.readIndexTable(table, 0) != R503_OK) {
    return -1;
  }
  for (int i = 0; i < 32; i++) {
    for (int b = 0; b < 8; b++) {
      if (((table[i] >> b) & 0x01) == 0) {
        return (int16_t)(i * 8 + b);
      }
    }
  }
  return -1;
}

void enrollFinger() {
  Serial.println("Enroll — Enter for next free ID, or type an ID:");
  String line = readLine();
  int16_t location;
  if (line.length() == 0) {
    location = nextFreeLocation();
    if (location < 0) {
      Serial.println("[X] No free slot");
      return;
    }
    Serial.printf(" << using free ID %d\n", location);
  } else {
    location = (int16_t)line.toInt();
    Serial.printf(" << ID %d\n", location);
  }

  for (uint8_t i = 1; i <= ENROLL_SAMPLES; i++) {
    waitForFinger(i);
    waitForFingerLift();
  }

  Serial.println(" >> Creating template...");
  fps.setAuraLED(aLEDBreathing, aLEDPurple, 100, 255);
  uint8_t rc = fps.createTemplate();
  if (rc != R503_OK) {
    Serial.printf("[X] createTemplate failed (0x%02X)\n", rc);
    fps.setAuraLED(aLEDFlash, aLEDRed, 50, 3);
    return;
  }
  rc = fps.storeTemplate(1, (uint16_t)location);
  if (rc != R503_OK) {
    Serial.printf("[X] storeTemplate failed (0x%02X)\n", rc);
    fps.setAuraLED(aLEDFlash, aLEDRed, 50, 3);
    return;
  }
  fps.setAuraLED(aLEDBreathing, aLEDGreen, 255, 1);
  Serial.printf(" >> Fingerprint recorded at ID %d\n", location);
}

void searchFinger() {
  Serial.println(" >> Place finger to search (30 s)...");
  fps.setAuraLED(aLEDBreathing, aLEDBlue, 50, 255);
  unsigned long start = millis();
  uint8_t rc = R503_NO_FINGER;
  while (millis() - start < 30000) {
    rc = fps.takeImage();
    if (rc != R503_NO_FINGER) {
      break;
    }
    delay(100);
  }
  if (rc != R503_OK) {
    Serial.println("[X] No image");
    return;
  }
  rc = fps.extractFeatures(1);
  if (rc != R503_OK) {
    Serial.printf("[X] extractFeatures failed (0x%02X)\n", rc);
    return;
  }
  uint16_t location = 0, confidence = 0;
  rc = fps.searchFinger(1, location, confidence);
  if (rc == R503_OK) {
    Serial.printf(" >> MATCH id=%u conf=%u\n", location, confidence);
    fps.setAuraLED(aLEDBreathing, aLEDGreen, 255, 1);
  } else {
    Serial.println(" >> No match");
    fps.setAuraLED(aLEDBreathing, aLEDRed, 255, 1);
  }
}

void deleteFinger() {
  Serial.println("Enter Finger ID to delete:");
  String line = readLine();
  if (line.length() < 1) {
    return;
  }
  uint16_t location = (uint16_t)line.toInt();
  uint8_t rc = fps.deleteTemplate(location);
  Serial.println(rc == R503_OK ? " >> Deleted" : "[X] Delete failed");
}

void printStoredIds() {
  Serial.printf("Templates stored: %u\nIDs: ", templateCount());
  uint8_t table[32] = {0};
  if (fps.readIndexTable(table, 0) != R503_OK) {
    Serial.println("(index read failed)");
    return;
  }
  bool any = false;
  for (int i = 0; i < 32; i++) {
    for (int b = 0; b < 8; b++) {
      if ((table[i] >> b) & 0x01) {
        Serial.printf("%d ", i * 8 + b);
        any = true;
      }
    }
  }
  Serial.println(any ? "" : "(none)");
}

void clearLibrary() {
  Serial.println("Type YES to wipe fingerprint library:");
  if (readLine() != "YES") {
    Serial.println(" >> Cancelled");
    return;
  }
  Serial.println(fps.emptyLibrary() == R503_OK ? " >> Cleared" : "[X] Clear failed");
}
