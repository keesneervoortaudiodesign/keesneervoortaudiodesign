import math
# 5 GHz OFDM timing
SLOT=9;SIFS=16;DIFS=SIFS+2*SLOT;BACKOFF=7.5*SLOT;PRE=36  # HT-mixed preamble
ACK=SIFS+20+4*math.ceil((16+14*8+6)/96)  # legacy 24 Mbps ACK
OVH=43+8  # ESP-NOW vendor action frame overhead + our seq/timestamp header
def air(payload,bps_per_sym,ack):
    bits=16+8*(payload+OVH)+6
    t=DIFS+BACKOFF+PRE+4*math.ceil(bits/bps_per_sym)
    return t+(ACK if ack else 0)
fs,ch,B=48000,2,3
for name,bps in [("HT20 MCS7",260),("HT40 MCS7",540)]:
  for per_ms in [0.5,1,2]:
    for red in [1,2]:
      pl=int(fs*per_ms/1000)*ch*B*red
      for ack in [False,True]:
        t=air(pl,bps,ack); pps=1000/per_ms
        print(f"{name} pkt={per_ms}ms red={red} ack={ack} payload={pl}B air={t:.0f}us  per-TX={t*pps/1e4:.1f}%  8TX={8*t*pps/1e4:.0f}%  2TX={2*t*pps/1e4:.0f}%")
print("payload rate per TX Mbit/s", fs*ch*B*8/1e6, " total 8TX", 8*fs*ch*B*8/1e6)
print("USB: 16ch x 24bit(in 32-bit slots) =", 16*4*fs*8/1e6, "Mbit/s; FS USB max ~12 (practically less for iso)")
