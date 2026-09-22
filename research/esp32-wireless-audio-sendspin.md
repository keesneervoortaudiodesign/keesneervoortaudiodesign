# ESP32 wireless audio transmitters: Sendspin feasibility and low-latency options

**Goal:** several ESP32 wireless audio *transmitters* (capture → Wi-Fi), one multichannel *receiver* (PC or app), end-to-end latency **< 20 ms, ideally < 5 ms**.

**Short answer**

| Target | Sendspin as-is (`source@v1`) | Custom UDP over Wi-Fi (AP, PC wired) | ESP-NOW / raw 802.11 → ESP32-S3 USB dongle |
|---|---|---|---|
| < 20 ms | Only on a clean network, and a TCP stall still causes a glitch | Yes (typically 6–12 ms) | Yes |
| < 5 ms | **No.** The spec's minimum chunk is 5 ms, plus TCP and a jitter buffer | Hard: a PC Wi-Fi stack and OS jitter get in the way | **Borderline possible (~4–7 ms)** on a clean channel, with loss concealment |
| Multichannel | Yes: the server gets N streams and resamples each | Yes: your own receiver app | Yes: shows up as one N-channel USB audio device |
| Effort | Low (existing firmware and plugin) | Medium | Medium to high |

Sendspin fits well as the **control, discovery, pairing, clock-sync and "into Music Assistant" layer**. It does not fit as the transport for sub-5 ms monitoring. The recommended architecture below uses both.

**Update (confirmed requirements: 8 × stereo I2S → DAW, 48 kHz/24-bit, hard < 5 ms):** see **section 5**. Sendspin leaves the audio path. The design is ESP32-C5 transmitters on **4–8 separate 5 GHz channels** (one channel can't carry 8 low-latency streams), dedicated receiver radios, and an **ESP32-P4 USB-HS hub** presenting 16 inputs to the DAW. Estimated total ≈ 3.9–5.0 ms, subject to a transmit-timing go/no-go test.

---

## 1. What Sendspin is (and isn't)

Sendspin (formerly "Resonate") is an open protocol from the Music Assistant / ESPHome ecosystem. It is built for **synchronised multi-room music playback**. Key facts from the spec ([Sendspin/spec](https://github.com/Sendspin/spec)):

- **Transport:** WebSocket over **TCP**, with a Noise-protocol encryption layer (`KKpsk2`, ChaChaPoly/AES-GCM). mDNS discovery and pairing.
- **Roles:** `player`, `source`, `controller`, `metadata`, `artwork`, `visualizer`, `color`.
- **`source@v1`** is the relevant role: *"captures audio from a local input and streams it to the server"*.
  - Codecs: the server MUST accept `pcm` and `flac`, and MAY accept `opus`.
  - Any `channels` / `sample_rate` / `bit_depth` is allowed. The server resamples and transcodes centrally.
  - Binary chunk = 1-byte type + 8-byte capture timestamp (server clock, µs) + audio.
  - **"A source MUST NOT send a chunk longer than 150 ms, and SHOULD NOT send one shorter than 5 ms."**
  - After a stall, the client should drop backlog and resume live.
- **Clock sync:** every client runs a Kalman-style time filter (offset and drift) against the server's monotonic clock. Sources timestamp chunks in server time, so the server knows when each sample was captured. This part is good, and useful for aligning several transmitters.
- **The server stays in charge:** it starts and stops sources, and then *"distributes the audio to players with its normal buffering and synchronization strategy"* (`min_buffer_ms`, `required_lead_time_ms`, etc.). That buffering is sized for glitch-free music, not for live monitoring.

### Existing implementations
- ESPHome `sendspin` component (player role, ESP32 only, experimental; ESP32-S3 with PSRAM recommended).
- Music Assistant **Sendspin Source** plugin (receives `source@v1` streams) and `aiosendspin` ([issue #296](https://github.com/Sendspin/aiosendspin/issues/296) tracks source-role support).
- [raine-works/sendspin-a1s-source](https://github.com/raine-works/sendspin-a1s-source): ESP32-A1S line-in → Sendspin source. It uses **40 ms Opus chunks and a 2 s PSRAM ring buffer** (Opus encoding takes about 15 ms per chunk on the classic ESP32). That is typical: current Sendspin sources trade latency for robustness.

### Why Sendspin cannot reach < 5 ms
1. **Chunking alone costs 5 ms or more**, because of the spec's SHOULD-NOT-go-below-5 ms rule.
2. **TCP over Wi-Fi.** Wi-Fi MAC retries hide most loss. When a segment is lost anyway, TCP retransmission blocks every later chunk behind it (head-of-line blocking). The lwIP and Linux minimum RTO is about 200 ms. The receiver needs a jitter buffer of tens of ms to hide normal jitter, and still glitches on a real loss.
3. **Server-side buffering and resampling** before anything reaches an output.

A tuned Sendspin chain (PCM, 5 ms chunks, small server buffer, custom receiving server) could plausibly reach **~15–30 ms**. It will not be deterministic.

---

## 2. Physics and budget: what Wi-Fi-class radios can do

### Latency budget (48 kHz, 1 transmitter → PC)

| Stage | Typical | Notes |
|---|---|---|
| ADC digital filter (group delay) | 0.2–0.9 ms | Codec dependent. Choose a low-latency filter ADC (check the datasheet's "group delay" figure) |
| I2S DMA block / packet size | 1.0 ms (48 samples) | Halve it with 0.5 ms packets or 96 kHz, at the cost of 2× the packet rate |
| Encode | 0 ms | Use **PCM** (Opus/FLAC add latency and CPU time). Mono 24-bit 48 k = 1.15 Mbit/s |
| Air time + MAC contention | 0.2–1 ms typical, tails of 5–20 ms+ | The tail is the whole problem |
| Jitter buffer (receiver) | 1–3 ms | Sets the trade-off between latency and dropouts |
| Receiver resampling/mixing | ~0.1–0.3 ms | |
| Host audio I/O (USB/ASIO/JACK/CoreAudio) | 1–3 ms | 32–64-sample buffers |
| **Total** | **≈ 4–8 ms** | **< 5 ms** only with small packets, a clean channel and a small jitter buffer that accepts occasional concealed losses |

### Airtime with several transmitters
Each Wi-Fi frame costs roughly 100–200 µs of airtime (preamble, contention, SIFS, ACK), whatever the payload.

- 1 ms packets = 1000 frames/s per transmitter ≈ **10–20 % airtime each**.
- With an **access point and a PC also on Wi-Fi**, every packet crosses the air twice (STA→AP→PC). **4 transmitters with 1 ms packets saturate the channel.**
- **Conclusions:** the receiver must be **wired** (PC on Ethernet to the AP) or be the radio endpoint itself (ESP-NOW dongle). Use 1–2 ms packets. Use a **dedicated channel**, preferably 5 GHz (**ESP32-C5** is dual-band Wi-Fi 6). Disable power save (`WIFI_PS_NONE`) on every transmitter.

### Clock drift and multichannel alignment
Every ESP32 has its own crystal (±10–20 ppm), so each stream drifts about 1 ms per minute relative to the others. The receiver must either:
- run an **ASRC per channel**, locked to its own output clock and steered by jitter-buffer fill level (standard practice), **or**
- discipline each transmitter's I2S clock. The classic ESP32 and ESP32-S2 have a fine-tunable **APLL**, which can be steered to a shared reference.

For **sample-aligned** channels (for example multi-mic phase), timestamp every packet in a common timebase. Options: Sendspin's time filter (sub-ms), or the Wi-Fi **TSF timer** (`esp_wifi_get_tsf_time`), which all stations in one BSS share to about µs level.

### Loss handling without adding latency
Retransmission costs latency. Better alternatives:
- **Redundancy:** each packet also carries the previous block (2× bandwidth, zero added latency).
- **Short, capped MAC retries.** ESP-NOW broadcast has no retries. With unicast, limit the retry count.
- **Packet-loss concealment** at the receiver (repeat and fade, or simple waveform extrapolation) for the rare hole.

---

## 3. Candidate architectures

### A. Pure Sendspin (lowest effort, ~30 ms to 2 s)
ESP32 (ESPHome + `source@v1`) → Music Assistant / aiosendspin server → players or a recorder. Good for "line-in into the house audio". Does **not** meet < 20 ms reliably and cannot meet < 5 ms.

### B. Custom UDP/RTP over Wi-Fi infrastructure → PC app (~6–12 ms)
- TX: ESP32-C5/S3, I2S ADC → 1–2 ms PCM packets over UDP (optionally RTP/AES67-style headers with a timestamp and sequence number) → AP on a dedicated 5 GHz channel.
- RX: PC on **wired Ethernet**. A receiver app (C++/Rust) keeps one jitter buffer and ASRC per stream and exposes an **N-channel virtual audio device**: JACK/PipeWire on Linux, BlackHole-style on macOS, and on Windows a virtual ASIO/WDM driver (the hard part on Windows).
- Sendspin can still run alongside for discovery, pairing and time sync. The audio path is simply not Sendspin.

### C. ESP-NOW / raw 802.11 → ESP32-S3 USB receiver dongle (**best shot at < 5 ms**)
- TX: ESP32 (C5 for 5 GHz, or S3), PCM in 1 ms (or 0.5 ms) packets via **ESP-NOW v2** (up to 1470-byte payload, IDF ≥ 5.4). **Set the PHY rate explicitly** (`esp_now_set_peer_rate_config`), because the 1 Mbit/s default is far too slow. Use broadcast plus redundancy, or unicast with capped retries.
- RX: **ESP32-S3** (native USB OTG) as a **class-compliant USB Audio Class 2 device** with N input channels. No driver is needed on Windows, macOS, Linux or iPad. The dongle is the clock master, and per-channel ASRC is done on the dongle.
- No AP and no OS network stack in the path, so jitter is the lowest achievable with Espressif radios.
- Community reports for naive ESP-NOW streaming show 4–5 ms average with 10–20 ms spikes ([pschatzmann](https://www.pschatzmann.ch/home/2022/04/27/low-latency-streaming-of-audio-data-using-esp-now/), [esp32.com thread](https://esp32.com/viewtopic.php?t=30615)). Those used defaults (low PHY rate, ACK waits, large blocks). With a fixed high PHY rate, small blocks, redundancy and a dedicated channel, **~4–7 ms end to end is a realistic target**.
- If it must also feed Music Assistant, the dongle or PC can re-publish the channels as a Sendspin `source`.

### D. For guaranteed < 5 ms: non-Wi-Fi radio
Professional wireless audio at 2–4 ms (for example digital wireless mics and IEMs) uses **proprietary TDMA radios**, not CSMA Wi-Fi. If the < 5 ms requirement is hard, consider:
- Nordic **nRF5340 / nRF54** with a custom 2.4 GHz TDMA link or the nRF audio stack. Note that LE Audio (LC3) is ~20–30 ms with standard settings.
- Dedicated 5.8 GHz audio modules.

The ESP32 can then remain the "smart" part (Sendspin, control, Wi-Fi config).

### Phone app as receiver
Android (AAudio low-latency path) is roughly 10–20 ms round trip on good devices. iOS is roughly 5–10 ms. Phone Wi-Fi power-save adds jitter. **< 20 ms is plausible on iOS and borderline on Android. < 5 ms is not realistic on a phone.** Use a phone for monitoring and control, not as the low-latency sink.

---

## 4. Recommendation (general)

> For the confirmed requirements (8 × stereo I2S → DAW, hard < 5 ms), see **section 5**. The ESP32-S3 dongle below cannot carry 16 × 24-bit channels over Full-Speed USB, and one radio channel cannot carry 8 low-latency transmitters.

1. **Hardware:** transmitters on **ESP32-C5** (5 GHz Wi-Fi 6) or **ESP32-S3**, with a low-group-delay I2S ADC. Receiver dongle on **ESP32-S3** (USB UAC2) or, for more channels and processing, an ESP32-P4 (USB HS) paired with a C5/C6 radio.
2. **Low-latency path:** architecture **C** (ESP-NOW / raw 802.11 → USB multichannel dongle). PCM, 1 ms packets, 1-block redundancy, 2 ms jitter buffer, per-channel ASRC, TSF or Sendspin-timestamp alignment. Target: **4–7 ms**, with **< 5 ms** at 96 kHz / 0.5 ms packets on a clean channel.
3. **Sendspin's role:** discovery, pairing, control, clock sync, and a **`source@v1` path into Music Assistant** for non-critical listening and recording. Contribute a low-latency profile upstream later if useful. A UDP audio sub-protocol or a "live" hint that lets the server bypass buffering would be natural spec extensions.
4. **Prototype plan:**
   1. 1 TX → 1 RX ESP-NOW loop. Measure latency with a scope: a pulse on the ADC input against the DAC/USB output.
   2. Log the jitter histogram (99.9th and 99.99th percentiles). This sets the jitter buffer size.
   3. Add redundancy and PLC. Scale to 4 and then 8 transmitters, and measure airtime and loss.
   4. USB UAC2 N-channel on the S3 receiver.
   5. Sendspin source integration in parallel.

## 5. Concrete design: 8 × stereo I2S → DAW, 48 kHz / 24-bit, < 5 ms (hard)

Requirements: **8 transmitters, each one stereo I2S stream, 48 kHz / 24-bit, into a DAW, hard < 5 ms.** This supersedes the general recommendation in section 4. Sendspin drops out of the audio path completely.

### 5.1 Bandwidth and airtime: one channel is not enough
- Payload: 2 ch × 24 bit × 48 kHz = **2.3 Mbit/s per transmitter, 18.4 Mbit/s total**.
- Wi-Fi's cost is dominated by **fixed per-packet overhead**: DIFS, backoff, preamble and ACK come to ~150–200 µs per frame, whatever the payload. Low latency means small, frequent packets, which is exactly the expensive case.

Airtime calculated for 5 GHz, HT20 MCS7 (65 Mbit/s), ESP-NOW frame overhead, average CSMA backoff:

| Packet period | Redundancy | ACK | Air/pkt | 1 TX | **8 TX on one channel** | 2 TX per channel |
|---|---|---|---|---|---|---|
| 0.5 ms | none | no | 166 µs | 33 % | **265 %** | 66 % |
| 1 ms | none | no | 182 µs | 18 % | **145 %** | 36 % |
| 1 ms | 1 block | no | 218 µs | 22 % | **174 %** | 44 % |
| 1 ms | 1 block | yes | 262 µs | 26 % | **209 %** | 52 % |
| 2 ms | none | no | 218 µs | 11 % | 87 % | 22 % |

HT40 only saves ~10–20 % because the overhead is fixed, not payload-bound.

**Conclusion:** at the packet sizes a < 5 ms budget allows (≤ 1 ms), **8 transmitters cannot share one channel.** CSMA also degrades sharply above ~50 % load, through collisions and tail latency. Spread the transmitters across radio channels:
- **Baseline: 4 channels × 2 transmitters** (~44 % load each, with redundancy), **TDMA-scheduled** so the two transmitters on a channel never contend.
- **Maximum robustness: 8 channels × 1 transmitter** (~22 % load each).

The 5 GHz band has enough 20 MHz channels. Use non-DFS channels (36–48, 149–165) so radar events can't force a channel change mid-show. Check local rules: in the EU, 149–165 are SRD channels limited to 25 mW, which is fine at stage distances.

### 5.2 Architecture

```
 I2S source ─► ESP32-C5 TX #1 ─┐  ch 36 (TDMA: TX1, TX2)
 I2S source ─► ESP32-C5 TX #2 ─┤                          ┌─ ESP32-C5 radio A ─┐
 ...                           ├── 4 × 5 GHz channels ──► ├─ ESP32-C5 radio B ─┤ SPI  ┌───────────────┐  USB 2.0 HS   ┌─────┐
 I2S source ─► ESP32-C5 TX #8 ─┘                          ├─ ESP32-C5 radio C ─┼────► │  ESP32-P4 hub │ ────────────► │ DAW │
                                                          └─ ESP32-C5 radio D ─┘      │ jitter buf,   │  UAC2, 16 in  └─────┘
                                                                                      │ ASRC, PLC     │
                                                                                      └───────────────┘
```

**Transmitters: ESP32-C5**
- 5 GHz capable (dual-band Wi-Fi 6), with one I2S port.
- **I2S slave** to the source, so the ESP32 follows the source's clock. The source's clock domain is then handled by ASRC in the hub.
- PCM only. **1 ms blocks** (48 frames × 6 bytes = 288 B). Each packet also carries the previous block (576 B payload) and a sequence number and timestamp header.
- ESP-NOW (or `esp_wifi_80211_tx` raw frames) at a **fixed PHY rate of HT20 MCS7**, broadcast or unicast with ACK/retries off. Loss is handled by redundancy, not retransmission.
- Wi-Fi power save off. Send in its **TDMA slot**, timed from the hub radio's sync beacon, and aligned so the slot follows the I2S DMA block completion.

**Receiver radios: 4 (or 8) × ESP32-C5**
- Each radio is on a fixed channel and sends a short sync beacon every ~10 ms (slot timing and timebase).
- Each forwards received packets to the hub over **SPI**, adding a hardware-timestamped arrival time.

**Hub: ESP32-P4**
- It needs **USB 2.0 High-Speed**: 16 channels × 32-bit slots × 48 kHz = 24.6 Mbit/s, which Full-Speed USB (12 Mbit/s, as on the ESP32-S3) cannot carry.
- **Jitter buffer:** 1.25–1.5 ms per stream. That is 1 packet plus margin, so a lost packet is recovered from the next packet's redundant copy.
- **ASRC per stereo stream:** 8 transmitters on 8 independent source clocks → the hub's USB/audio clock. Polyphase, ~0.2–0.3 ms group delay. About 25 M MAC/s for 16 channels, which is comfortable on the P4.
- **Packet-loss concealment** for double losses: repeat and crossfade, with a counter exposed for diagnostics.
- **Sample alignment across transmitters:** all transmitters stamp block times in the hub's beacon timebase, and ASRC places each block at its capture time. Inter-transmitter phase error is then ~1 sample, and each stereo pair is sample-exact because it comes from one I2S port.
- **USB Audio Class 2, 16 inputs** (TinyUSB UAC2 on the P4 HS controller).
  - macOS, Linux and iPadOS: class-compliant low latency out of the box.
  - **Windows:** the in-box UAC2 driver is not ASIO. A DAW needs an ASIO path, such as a custom/licensed UAC2 ASIO driver, or FlexASIO/ASIO4ALL over WASAPI (higher latency). Budget time for this if the DAW runs on Windows.

### 5.3 Latency budget (I2S in at transmitter → sample in DAW)

| Stage | Budget |
|---|---|
| I2S DMA block fill (48 frames) | 1.00 ms |
| Wait for TDMA slot (aligned to block end) | 0.05–0.15 ms |
| Air time (576 B at MCS7 + preamble) | 0.10 ms |
| Radio RX → SPI → hub | 0.15–0.25 ms |
| Jitter buffer (covers redundancy recovery) | 1.25 ms |
| ASRC group delay | 0.25 ms |
| **Wireless link subtotal (to hub USB buffer)** | **≈ 2.9–3.1 ms** |
| USB HS transfer and device buffer | 0.25–0.5 ms |
| Host driver + DAW input buffer (32–64 samples at 48 kHz) | 0.7–1.3 ms (+ driver safety offset) |
| **Total into the DAW** | **≈ 3.9–5.0 ms** |

A **< 5 ms total is achievable, but only just.** It needs a 32-sample DAW buffer, a lean driver (macOS/Linux, or a good ASIO driver on Windows), and **one condition this design does not control: the ESP32 Wi-Fi driver's transmit timing jitter.** Community measurements of `esp_now_send` show 0.6 ms up to 20 ms+ with default settings. If the transmit path cannot hold its slot to within ~0.3 ms at the 99.99th percentile, the 1.25 ms jitter buffer is too small and the < 5 ms target fails.

Where the remaining margin could come from, if needed:
- 0.5 ms blocks with **8 channels × 1 transmitter** (33 % load). This saves ~1 ms but doubles the packet rate per radio.
- 96 kHz (smaller blocks per ms). This doubles bandwidth, so the fit must be recalculated.

### 5.4 Prototype plan (go/no-go first)
1. **Go/no-go: transmit determinism.** One C5 transmitter → one C5 receiver on a clean 5 GHz channel, 1 ms / 576 B frames at fixed MCS7. GPIO-toggle at I2S DMA done, and again at RX callback. Scope-measure the latency histogram over hours, including the 99.99th percentile. **Pass: 99.99th percentile < ~0.5 ms.** Then repeat with 2 transmitters in TDMA on one channel, and with a busy neighbouring Wi-Fi network.
2. If step 1 fails: test raw `esp_wifi_80211_tx`, and different ESP-IDF queue/task priorities. If it still fails, the ESP32 radio is the wrong part (see 5.5).
3. P4 hub: SPI link to 4 radios, jitter buffer, ASRC, PLC, UAC2 16 inputs (TinyUSB). Measure loopback latency with a DAW round-trip test.
4. Scale to 8 transmitters, 4 channels. Soak test at a real venue. Log the loss counters.
5. Windows ASIO strategy, if the DAW is on Windows.

### 5.5 Plan B if the ESP32 radio can't hold timing
- **Nordic 2.4 GHz proprietary radios can't carry this uncompressed.** The 2 Mbit/s PHY is below the 2.3 Mbit/s stereo 24-bit payload, and low-delay compression costs latency or quality. It would need newer higher-rate PHY modes, 16-bit audio, or two radios per transmitter.
- **An FPGA/SDR or a dedicated 5 GHz audio module with a true TDMA MAC** is the professional route. Digital wireless mic and IEM systems reach ~2–3 ms this way. It is much more effort. Keep ESP32 for control/UI only.

### 5.6 Where Sendspin fits now
Not in the audio path. It could optionally be used on the hub for discovery and control from Music Assistant, or to tap a monitor mix into the house system via `source@v1`. Neither is needed for the DAW use case.

### Sources
- Sendspin spec: https://github.com/Sendspin/spec (`roles/source/v1.md`, `roles/player/v1.md`)
- ESPHome Sendspin component: https://esphome.io/components/sendspin/
- Music Assistant Sendspin Source plugin: https://www.music-assistant.io/plugins/sendspin-source/
- aiosendspin source role: https://github.com/Sendspin/aiosendspin/issues/296
- ESP32-A1S Sendspin source: https://github.com/raine-works/sendspin-a1s-source
- ESP-NOW (IDF, v2 payload): https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/network/esp_now.html
- ESP-NOW audio latency: https://esp32.com/viewtopic.php?t=30615 , https://www.pschatzmann.ch/home/2022/04/27/low-latency-streaming-of-audio-data-using-esp-now/
- Sendspin overview: https://www.xda-developers.com/sendspin-esphome-multi-room-audio/
