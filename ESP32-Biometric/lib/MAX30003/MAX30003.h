/**
 * MAX30003 (ECG 3 Click) — bit-bang SPI (Mode 0 / Mode 3).
 */

#ifndef MAX30003_H
#define MAX30003_H

#include <Arduino.h>

static const uint8_t MAX30003_NO_OP = 0x00;
static const uint8_t MAX30003_STATUS = 0x01;
static const uint8_t MAX30003_SW_RST = 0x08;
static const uint8_t MAX30003_SYNC = 0x09;
static const uint8_t MAX30003_FIFO_RST = 0x0A;
static const uint8_t MAX30003_INFO = 0x0F;
static const uint8_t MAX30003_CNFG_GEN = 0x10;
static const uint8_t MAX30003_CNFG_EMUX = 0x14;
static const uint8_t MAX30003_CNFG_ECG = 0x15;
static const uint8_t MAX30003_CNFG_RTOR1 = 0x1D;
static const uint8_t MAX30003_ECG_FIFO = 0x21;
static const uint8_t MAX30003_RTOR = 0x25;

static const uint32_t MAX30003_FMSTR_32768_ECG_512 = 0x000000;
static const uint32_t MAX30003_ECG_CHANN_EN = 0x080000;
static const uint32_t MAX30003_DCLOFF_EN = 0x001000;
static const uint32_t MAX30003_RBIAS_100M = 0x000004;
static const uint32_t MAX30003_RBIASP_EN = 0x000002;
static const uint32_t MAX30003_RBIASN_EN = 0x000001;
static const uint32_t MAX30003_DHPF_500MILIHZ = 0x004000;
static const uint32_t MAX30003_DLPF_40HZ = 0x001000;
static const uint32_t MAX30003_WNDW_12 = 0x300000;
static const uint32_t MAX30003_RRGAIN_AUTO = 0x0F0000;
static const uint32_t MAX30003_RTOR_EN = 0x008000;
static const uint32_t MAX30003_PAVG_8 = 0x002000;

static const uint32_t MAX30003_STAT_DCLOFF = 0x100000;
static const uint32_t MAX30003_STAT_LDOFF_PH = 0x000008;
static const uint32_t MAX30003_STAT_LDOFF_PL = 0x000004;
static const uint32_t MAX30003_STAT_LDOFF_NH = 0x000002;
static const uint32_t MAX30003_STAT_LDOFF_NL = 0x000001;

class MAX30003 {
 public:
  MAX30003(uint8_t csPin, uint8_t sckPin, uint8_t mosiPin, uint8_t misoPin);

  bool begin();
  void setCsPin(uint8_t csPin);
  void setSpiMode(uint8_t mode);  // 0 or 3
  uint8_t csPin() const { return csPin_; }
  bool detect();
  bool softReset();
  bool configureEcg();

  uint32_t readReg(uint8_t reg);
  void writeReg(uint8_t reg, uint32_t value);

  uint32_t info() const { return lastInfo_; }
  uint32_t status();
  bool leadsOn();
  bool readHeartRate(uint16_t &bpm, uint16_t &rrMs);

  int misoLevel() const { return digitalRead(misoPin_); }
  // Clock 32 times with CS low; return how many samples saw MISO high.
  uint16_t countMisoHighs(uint16_t clocks = 32);
  void dumpPins(Stream &out) const;

 private:
  uint8_t csPin_, sckPin_, mosiPin_, misoPin_;
  uint8_t spiMode_;  // 0 or 3
  uint32_t lastInfo_;
  uint16_t halfPeriodUs_;

  void csLow();
  void csHigh();
  uint8_t xferByte(uint8_t out);
  void delayHalf();
};

#endif
