#include "MAX30003.h"

MAX30003::MAX30003(uint8_t csPin, uint8_t sckPin, uint8_t mosiPin, uint8_t misoPin)
    : csPin_(csPin),
      sckPin_(sckPin),
      mosiPin_(mosiPin),
      misoPin_(misoPin),
      spiMode_(0),
      lastInfo_(0),
      halfPeriodUs_(10) {}  // ~50 kHz — gentle on TXB0106

bool MAX30003::begin() {
  pinMode(csPin_, OUTPUT);
  pinMode(sckPin_, OUTPUT);
  pinMode(mosiPin_, OUTPUT);
  pinMode(misoPin_, INPUT);

  digitalWrite(csPin_, HIGH);
  digitalWrite(sckPin_, (spiMode_ == 3) ? HIGH : LOW);
  digitalWrite(mosiPin_, LOW);
  delay(200);

  // Never use INFO as the first command after power-up
  spiMode_ = 0;
  digitalWrite(sckPin_, LOW);
  (void)readReg(MAX30003_STATUS);
  delay(20);
  writeReg(MAX30003_SW_RST, 0x000000);
  delay(150);
  (void)readReg(MAX30003_STATUS);
  delay(20);

  if (detect()) {
    return true;
  }

  // Retry Mode 3 (also legal per Maxim datasheet)
  spiMode_ = 3;
  digitalWrite(sckPin_, HIGH);
  delay(20);
  (void)readReg(MAX30003_STATUS);
  delay(20);
  return detect();
}

void MAX30003::setCsPin(uint8_t csPin) {
  pinMode(csPin_, OUTPUT);
  digitalWrite(csPin_, HIGH);
  csPin_ = csPin;
  pinMode(csPin_, OUTPUT);
  digitalWrite(csPin_, HIGH);
}

void MAX30003::setSpiMode(uint8_t mode) {
  spiMode_ = (mode == 3) ? 3 : 0;
  digitalWrite(sckPin_, (spiMode_ == 3) ? HIGH : LOW);
}

void MAX30003::delayHalf() { delayMicroseconds(halfPeriodUs_); }

void MAX30003::csLow() {
  digitalWrite(csPin_, LOW);
  delayHalf();
  delayHalf();
}

void MAX30003::csHigh() {
  delayHalf();
  digitalWrite(csPin_, HIGH);
  delayHalf();
}

uint8_t MAX30003::xferByte(uint8_t out) {
  uint8_t in = 0;
  if (spiMode_ == 0) {
    // Mode 0: CPOL=0 CPHA=0 — sample on rising edge
    for (int i = 7; i >= 0; i--) {
      digitalWrite(mosiPin_, (out >> i) & 0x01);
      delayHalf();
      digitalWrite(sckPin_, HIGH);
      delayHalf();
      in |= (digitalRead(misoPin_) ? 1 : 0) << i;
      digitalWrite(sckPin_, LOW);
    }
  } else {
    // Mode 3: CPOL=1 CPHA=1 — sample on rising edge, idle high
    for (int i = 7; i >= 0; i--) {
      digitalWrite(sckPin_, LOW);
      digitalWrite(mosiPin_, (out >> i) & 0x01);
      delayHalf();
      digitalWrite(sckPin_, HIGH);
      delayHalf();
      in |= (digitalRead(misoPin_) ? 1 : 0) << i;
    }
  }
  return in;
}

uint32_t MAX30003::readReg(uint8_t reg) {
  uint8_t cmd = (uint8_t)((reg << 1) | 0x01);
  csLow();
  xferByte(cmd);
  uint8_t b0 = xferByte(0xFF);
  uint8_t b1 = xferByte(0xFF);
  uint8_t b2 = xferByte(0xFF);
  csHigh();
  return ((uint32_t)b0 << 16) | ((uint32_t)b1 << 8) | (uint32_t)b2;
}

void MAX30003::writeReg(uint8_t reg, uint32_t value) {
  csLow();
  xferByte((uint8_t)(reg << 1));
  xferByte((uint8_t)((value >> 16) & 0xFF));
  xferByte((uint8_t)((value >> 8) & 0xFF));
  xferByte((uint8_t)(value & 0xFF));
  csHigh();
}

bool MAX30003::detect() {
  (void)readReg(MAX30003_NO_OP);
  (void)readReg(MAX30003_STATUS);
  delay(5);
  lastInfo_ = readReg(MAX30003_INFO);

  if ((lastInfo_ & 0xF00000) == 0x500000) {
    return true;
  }
  if (lastInfo_ != 0x000000 && lastInfo_ != 0xFFFFFF) {
    return true;
  }
  return false;
}

bool MAX30003::softReset() {
  writeReg(MAX30003_SW_RST, 0x000000);
  delay(100);
  writeReg(MAX30003_FIFO_RST, 0x000000);
  delay(50);
  (void)readReg(MAX30003_STATUS);
  delay(10);
  return detect();
}

bool MAX30003::configureEcg() {
  if (!softReset()) {
    return false;
  }
  writeReg(MAX30003_CNFG_GEN,
           MAX30003_FMSTR_32768_ECG_512 | MAX30003_ECG_CHANN_EN |
               MAX30003_DCLOFF_EN | MAX30003_RBIAS_100M | MAX30003_RBIASP_EN |
               MAX30003_RBIASN_EN);
  writeReg(MAX30003_CNFG_EMUX, 0x000000);
  writeReg(MAX30003_CNFG_ECG, 0x800000 | MAX30003_DHPF_500MILIHZ | MAX30003_DLPF_40HZ);
  writeReg(MAX30003_CNFG_RTOR1,
           MAX30003_WNDW_12 | MAX30003_RRGAIN_AUTO | MAX30003_RTOR_EN |
               MAX30003_PAVG_8 | 0x000600);
  writeReg(MAX30003_SYNC, 0x000000);
  delay(50);
  return true;
}

uint32_t MAX30003::status() { return readReg(MAX30003_STATUS); }

bool MAX30003::leadsOn() {
  uint32_t st = status();
  const uint32_t leadOff =
      MAX30003_STAT_LDOFF_PH | MAX30003_STAT_LDOFF_PL | MAX30003_STAT_LDOFF_NH |
      MAX30003_STAT_LDOFF_NL | MAX30003_STAT_DCLOFF;
  return (st & leadOff) == 0;
}

bool MAX30003::readHeartRate(uint16_t &bpm, uint16_t &rrMs) {
  uint32_t rtor = readReg(MAX30003_RTOR);
  uint32_t interval = (rtor >> 10) & 0x3FFF;
  if (interval == 0) {
    bpm = 0;
    rrMs = 0;
    return false;
  }
  float hr = 60.0f / ((float)interval * 0.008f);
  bpm = (uint16_t)(hr + 0.5f);
  rrMs = (uint16_t)(interval * 8);
  return bpm > 0 && bpm <= 250;
}

uint16_t MAX30003::countMisoHighs(uint16_t clocks) {
  uint16_t highs = 0;
  csLow();
  for (uint16_t i = 0; i < clocks; i++) {
    digitalWrite(mosiPin_, HIGH);
    delayHalf();
    digitalWrite(sckPin_, (spiMode_ == 3) ? LOW : HIGH);
    delayHalf();
    if (digitalRead(misoPin_)) {
      highs++;
    }
    digitalWrite(sckPin_, (spiMode_ == 3) ? HIGH : LOW);
    delayHalf();
  }
  csHigh();
  return highs;
}

void MAX30003::dumpPins(Stream &out) const {
  out.printf("  pins CS=%u SCK=%u MOSI=%u MISO=%u  mode=%u  MISO_level=%d\n",
             csPin_, sckPin_, mosiPin_, misoPin_, spiMode_, digitalRead(misoPin_));
}
