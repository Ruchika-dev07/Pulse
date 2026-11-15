# ESP32 Biometric (FeatherS3)

## Hardware

| Part | Role |
|------|------|
| FeatherS3 | MCU |
| Feather Click Shield | mikroBUS ×2 |
| Fingerprint 3 Click (R503) | Identity (UART on TX/RX) |
| ECG 3 Click (MAX30003) | Liveness (SPI) |
| ATECC608 | Signing (later) |

### Click seating (both boards together — OK)

Safe to plug both at once: Fingerprint is UART-only (SPI pins NC); ECG is SPI-only (UART pins NC). Shared TX/RX and SPI rails do not fight.

| Socket | Board | Chip-select / notes |
|--------|--------|---------------------|
| **mikroBUS1** | Fingerprint 3 Click | UART on TX/RX; RST=ring LED enable, INT=wake |
| **mikroBUS2** | ECG 3 Click | SPI **CS = GPIO3** (measured continuity; silk CS1/CS2 labels may not match) |

SPI on FeatherS3: SCK=36, MOSI=35, MISO=37 (PSRAM must stay off so these pins stay free). Firmware probes GPIO **3 → 33 → 5 → 6** at boot.

### ECG 3 Click jumpers (required for SPI detect)

| Jumper | Set to | Meaning |
|--------|--------|---------|
| **CLK SEL** | **INT** | Onboard 32.768 kHz crystal (not EXT) |
| **PWR SEL** | **3.3V** | Logic/power at 3.3V |

If CLK SEL is on **EXT** and the MCU is not driving the CLK/PWM pin, the MAX30003 will not run and INFO reads often fail (`detect=no`).

R503 UART: RX=44, TX=43 @ 57600.

## Workflows

### Registration (menu)

1. Type `m`
2. Type `e` — 4× place/lift
3. Optional: `p` list, `s` search, `d` delete

### Authentication (default once a finger exists)

1. R503 detects finger  
2. Verifies enrolled match  
3. Requests ECG (MAX30003)  
4–5. R–R / BPM when electrodes + RTOR are ready  
6. One JSON line on USB serial, e.g.

```json
{"type":"auth","finger_ok":true,"finger_id":0,"confidence":120,"ecg_ok":true,"bpm":72,"rr_ms":833,"ecg_info":"0x5xxxxx","detail":"ok"}
```

Keys anytime: `a` auth · `m` menu · `h` help · menu `g` = ECG SPI dump.

## ECG milestones

| # | Goal | Status in firmware |
|---|------|--------------------|
| 1 | SPI detect (INFO read) | `ecg.detect()` / boot + auth |
| 2 | Init + status | `ecg.configureEcg()` |
| 3 | Electrode / lead-off | `ecg.leadsOn()` |
| 4 | R–R → BPM | `ecg.readHeartRate()` |
| 5–6 | Pulse integration after fingerprint | auth path in `src/main.cpp` |

Driver: `lib/MAX30003/` (Arduino SPI port of MikroE register protocol). Vendor tree: `vendor/ECG_3_click/`.

## Build

COM port is auto-detected.

```bash
cd Pulse/ESP32-Biometric
pio run -e feathers3 -t upload
pio device monitor -b 115200
```

Bootloader tip: hold BOOT, tap RESET, release BOOT, then upload.
