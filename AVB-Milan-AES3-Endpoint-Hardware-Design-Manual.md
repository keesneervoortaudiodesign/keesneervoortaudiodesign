# Industrial AVB/Milan & AES3 Audio Endpoint — Hardware Design Manual

| Parameter | Specification |
| --- | --- |
| **Host MCU** | RP2350B (dual-core) |
| **Network Controller** | Microchip LAN9360 |
| **Audio Interface** | 2-Channel isolated AES3 with hardware ASRC |
| **Network Media** | 100 Mbps AVB/Milan over RJ45, PoE powered |
| **Operating Temperature** | −40 °C to +85 °C (industrial) |

---

## Table of Contents

1. [System Functional Description & Signal Flow](#1-system-functional-description--signal-flow)
2. [Integrated System Protection Circuitry](#2-integrated-system-protection-circuitry)
3. [Comprehensive Architectural System Schematic](#3-comprehensive-architectural-system-schematic)
4. [Professional 10-Unit Bill of Materials (BOM)](#4-professional-10-unit-bill-of-materials-bom)
5. [Critical PCB Layout Design Directives](#5-critical-pcb-layout-design-directives)

---

## 1. System Functional Description & Signal Flow

This hardware module operates as a bi-directional bridging endpoint between a professional AVB/Milan deterministic network and a local, physical AES3 digital audio interface.

```
[RJ45 AVB Grid 100 Mbps]
        <=>
[LAN9360 Network Endpoint]
        <=>
      [I2S Bus]
        <=>
[SRC4392 Transceiver / ASRC]
        <=>
[XLR AES3 Balanced Audio]
```

Management and startup initialization are handled by the RP2350B host MCU over a shared I²C control bus.

### 1.1 Network Layer & gPTP Alignment (LAN9360)

- Executes IEEE 802.1AS gPTP time tracking using an embedded physical-layer Hardware Time Stamp Unit (TSU) to eliminate packet jitter.
- Instantiates and handles IEEE 1722 (AVTP) audio packet transport and IEEE 1722.1 (ATDECC) connection discovery without consuming host CPU processing cycles.
- Re-aligns the local digital audio clock plane using an internal digital PLL (dPLL) to trace the network's elected clock master.

### 1.2 Audio Frontend & Clock Domains (SRC4392)

- **Bi-directional ASRC integration:** the integrated hardware Asynchronous Sample Rate Converter continuously recalculates sample transitions to translate the incoming physical audio stream directly into the network clock phase, preventing buffer dropouts, clicks, and pops.
- Includes an on-chip Digital Audio Receiver (DIR) to decode the balanced line signals, and a Digital Audio Transmitter (DIT) to construct Milan-synchronized frames back into physical AES3 format.

### 1.3 Dual-Role Interface Clocking

#### Scenario A — Network Master (standard Milan mode)

The AVB network acts as the absolute time authority.

- The LAN9360 locks its dPLL to the network gPTP timeline and drives the local I²S `MCLK`, `BCLK`, and `LRCLK` output pins.
- The SRC4392 locks its serial slave port to these pins, using its ASRC to shift incoming physical AES3 data onto this grid.

#### Scenario B — Local AES3 Master (external clock mode)

The physical device connected to the AES3 input acts as the clock master.

- The SRC4392 DIR extracts the embedded clock from the incoming XLR line and routes it via a dedicated hardware line (`DIR_RECOV_CLK`) to the LAN9360's master clock input.
- The LAN9360 then acts as the network gPTP Grandmaster, forcing the rest of the AVB network to follow the local hardware input clock.

| Mode | Clock Authority | LAN9360 Role | SRC4392 Serial Port |
| --- | --- | --- | --- |
| A — Network Master | AVB network gPTP | dPLL slave, I²S clock source | Slave (ASRC to network grid) |
| B — Local AES3 Master | Incoming XLR AES3 line | gPTP Grandmaster (fed by `DIR_RECOV_CLK`) | DIR recovers and exports clock |

---

## 2. Integrated System Protection Circuitry

```
[RJ45 Cable]   -> [TVS Diode Array]   -> [2 kV Cap]     -> [MagJack Transformers]     -> [LAN9360 PHY]
[AES3 XLR IN]  -> [TVS Clamp Array]   -> [Shunt Caps]   -> [1:1 Pulse XFMR]           -> [SRC4392 DIR]
[48 V PoE Rail]-> [Schottky Bridge]   -> [Pi-Filter]    -> [AG9905-LPB PoE Module]    -> [Clean Power Plane]
```

### 2.1 Ethernet & PoE Power Protection

- **Galvanic Ethernet boundary:** the Abracon MagJack houses internal isolation transformers rated to clear 1500 Vrms of differential potential.
- **Transient surge suppressors:** high-speed, ultra-low-capacitance Littelfuse SP3012 TVS arrays protect the differential TX/RX signal lines.
- **PoE input Pi-filter:** an inductive-capacitive (LC Pi) network suppresses high-frequency ripple on the input lines of the Silvertel AG9905-LPB, preventing switching harmonics from injecting noise into the audio ground planes.

### 2.2 Balanced Audio Protection

- **AES3 isolation transformers:** dual Pulse Electronics 1:1 digital audio transformers break ground loops on the incoming and outgoing physical lines, shielding internal digital planes from stray external chassis currents.
- **XLR clamp protection:** bi-directional TVS diode arrays clamp physical line spikes caused by electrostatic discharge (ESD) during field hot-plugging.

---

## 3. Comprehensive Architectural System Schematic

```
[RJ45 + PoE Connector: Abracon ARJP11A]
        |-> [Data TVS Array]  ------------------------------------+
        |-> [PoE Pi-Filter]                                       |
                |                                                 |
                v                                                 v
[PoE Module: Silvertel AG9905-LPB, 5 V out]        [AVB Controller: LAN9360A]
                |                                    ^         |         ^
                v                                    |         |         |
[3.3 V RF LDO: TI TPS7A47] -> [Audio Clean Rail]     |         |         |
                                                     |         |         |
        [25 MHz Low-Jitter TXC Crystal] -------------+         |         |
        [24AA256 I2C EEPROM  (config)] --------------+         |         |
                                                               |         |
                          MCLK / BCLK / LRCLK / I2C  <=========+         |
                                    |                                    |
                                    v                                    |
                     [ASRC Transceiver: TI SRC4392]                      |
                                    |                                    |
                       DIR_RECOV_CLK (clock sync back) ------------------+
                                    |
                        Tx / Rx differential audio
                                    v
                  [1:1 Pulse Transformers: PE-65612NL]
                                    v
                 [XLR Inputs / Outputs with TVS Clamps]

Management Interface:
[Host MCU: RP2350B] <- firmware from 16 MB Winbond QSPI Flash
        <=> Control I2C bus to LAN9360 and SRC4392
```

---

## 4. Professional 10-Unit Bill of Materials (BOM)

Quantities are **per unit**; extended totals are for a **10-unit build**.

| # | Qty/Unit | Ref | Description | Manufacturer | Unit Price | Ext. (10 units) | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | U1 | LAN9360A-I/CQBT-100 AVB Endpoint | Microchip | $14.10 | $141.00 | DigiKey sourcing |
| 2 | 1 | U2 | RP2350B dual-core host MCU | Raspberry Pi | $1.15 | $11.50 | DigiKey sourcing |
| 3 | 1 | U3 | SRC4392IPFBR AES transceiver + ASRC | Texas Instruments | $15.40 | $154.00 | Mouser sourcing |
| 4 | 1 | MOD1 | AG9905-LPB SMT 5 V 7 W PoE module | Silvertel | $9.25 | $92.50 | Mouser sourcing |
| 5 | 1 | J1 | ARJP11A-MASA-B-A-EMU2 PoE MagJack | Abracon | $9.01 | $90.10 | DigiKey sourcing |
| 6 | 2 | T1, T2 | PE-65612NL 1:1 audio pulse transformer | Pulse Electronics | $3.10 | $62.00 | XLR galvanic break |
| 7 | 1 | U4 | W25Q128JVSIQ 16 MB QSPI flash | Winbond | $1.50 | $15.00 | Host application boot |
| 8 | 1 | U5 | 24AA256-I/SN 256 K I²C EEPROM | Microchip | $0.51 | $5.10 | LAN9360 routing database |
| 9 | 1 | U6 | TPS7A4700RGWR ultra-low-noise 3.3 V LDO | Texas Instruments | $4.20 | $42.00 | Audio clean regulator |
| 10 | 1 | Y1 | 7M-25.000MAAJ-T 25 MHz crystal (±20 ppm) | TXC Corp | $1.88 | $18.80 | gPTP network sync clock |
| 11 | 1 | Y2 | CS325S12000000ABJT 12 MHz crystal | Citizen | $0.65 | $6.50 | RP2350 clock reference |
| 12 | 3 | D1–D3 | SP3012-04UTG low-capacitance TVS array | Littelfuse | $0.45 | $13.50 | Interface ESD clamping |
| 13 | 1 kit | — | Unified passive kit (X7R caps, 1 % resistors) | Panasonic / Yageo | $5.00 | $50.00 | Power plane filtering |
| | | | | | **Batch total (10 units)** | **$702.00** | Net component inventory sourcing cost |

> **Note on the batch total:** the source document quoted **$662.00**. Summing the extended line items above gives **$702.00** ($70.20 per unit), so the corrected figure is used here. Verify against live distributor pricing before release, as component pricing and stock move frequently.

---

## 5. Critical PCB Layout Design Directives

1. **Physical AES3 digital lines** — route from the physical XLR pins to the pulse transformers as a 110 Ω balanced differential pair. Do not run digital clocks underneath these tracks.
2. **Ethernet MDI tracks** — route links from the Abracon MagJack interface directly to the PHY pins of the LAN9360 as a matched 100 Ω differential pair.
3. **Master clock trace layer** — keep the 50 Ω single-ended master clock (`MCLK`) lines short. Shield them using a unified ground trace ring architecture to prevent noise from entering the linear power planes.
