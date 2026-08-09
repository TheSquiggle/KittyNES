"""End-to-end check: does a real CPU write to $4000-$400F actually update the
shared APU_FREQ/APU_VOL/APU_DUTY/APU_NOISEIDX state the channel sprites read?

This is the integration test the structural checks above can't provide: it
calls apu_write DIRECTLY (the same proc bus_write dispatches to) and checks
the resulting shared state, using the same interp.py-walks-the-real-graph
technique as every other suite in this project.
"""
import sys

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
import apu_build
import apu_wire
from interp import Interp

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "got=%r want=%r" % (got, want))
    if not ok:
        FAILURES.append(label)


e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
shared_ids = apu_wire.create_shared_apu_state(e)
CHANS = [1, 2, 3, 4]
broadcasts = {
    "update": {ch: e.proj.add_broadcast("apu_update_%d" % ch) for ch in CHANS},
    "restart": {ch: e.proj.add_broadcast("apu_restart_%d" % ch) for ch in CHANS},
    "stop": e.proj.add_broadcast("apu_stop_all"),
}
apu_wire.wire_apu(e, shared_ids, broadcasts)
BC.phase2_bus(e)

it = Interp(e.proj, max_steps=2_000_000)


def w(addr, val):
    it.call_proc_by_name("apu_write %s %s", {"a": addr, "v": val})


def i_(x):
    return int(x) if isinstance(x, (int, float)) else x


CPU_HZ = 1789773.0

# ---- Pulse 1: duty + volume ($4000) ----
w(0x4000, 0b10_0_0_1010)  # DDlc.vvvv: DD=10(=2,50%), l=0, c=0, vvvv=1010(=10)
check("pulse1 duty set from $4000", i_(it.lists["APU_DUTY"][0]), 2)
check("pulse1 volume set from $4000 (10/15*100)", i_(it.lists["APU_VOL"][0]), int(10 * 100 / 15))

# ---- Pulse 1: timer low then high ($4002/$4003) -> frequency ----
t = 253  # a normal, audible period
w(0x4002, t & 0xFF)
w(0x4003, (t >> 8) & 0x07)
expect_hz = CPU_HZ / (16 * (t + 1))
got_hz = float(it.lists["APU_FREQ"][0])
check("pulse1 frequency from timer t=253 within 0.01Hz",
      abs(got_hz - expect_hz) < 0.01, True)

# ---- Pulse 1: silence rule (t<8) ----
w(0x4002, 5)
w(0x4003, 0)
check("pulse1 silenced when t<8", i_(it.lists["APU_FREQ"][0]), 0)

# ---- Pulse 2 is independent of Pulse 1 ----
w(0x4004, 0b01_0_0_0101)  # DDlc.vvvv: DD=01(25%), l=0, c=0, vvvv=0101(=5)
w(0x4006, 100)
w(0x4007, 0)
check("pulse2 duty independent of pulse1", i_(it.lists["APU_DUTY"][1]), 1)
check("pulse1 duty unchanged by pulse2 write", i_(it.lists["APU_DUTY"][0]), 2)
expect_hz2 = CPU_HZ / (16 * 101)
check("pulse2 frequency correct and independent",
      abs(float(it.lists["APU_FREQ"][1]) - expect_hz2) < 0.01, True)

# ---- Triangle: /32 not /16, and the linear-counter silence rule ----
w(0x400A, 100)
w(0x400B, 0)
expect_tri = CPU_HZ / (32 * 101)
check("triangle divides by 32 (not 16)",
      abs(float(it.lists["APU_FREQ"][2]) - expect_tri) < 0.01, True)
w(0x4008, 0x80)  # control set, reload=0 -> silence
check("triangle silenced by $4008=$80", i_(it.lists["APU_VOL"][2]), 0)
w(0x4008, 0x7F)  # nonzero reload -> sounding
check("triangle sounding after nonzero linear reload", i_(it.lists["APU_VOL"][2]) > 0, True)

# ---- Noise: mode selects the ASSET (1=noiseA, 2=noiseB); period is PITCH,
# same technique as every other channel now (see apu_build.py's module
# docstring -- the old per-period-asset design aliased badly at short
# periods, which is what caused the "noise too low pitched" bug). ----
NOISE_PERIOD_TABLE = [4, 8, 16, 32, 64, 96, 128, 160,
                      202, 254, 380, 508, 762, 1016, 2034, 4068]
BASE_NOISE_HZ = 1789773.0 / 254  # must match audio_assets.BASE_NOISE_PERIOD=254

w(0x400E, 0x05)              # mode 0, period index 5 (real period value 96)
check("noise index = mode+1 (mode0 -> noiseA=1)", i_(it.vars["APU_NOISEIDX"]), 1)
expect_noise_hz = CPU_HZ / NOISE_PERIOD_TABLE[5]
check("noise frequency = fCPU/period from the real period table",
      abs(float(it.lists["APU_FREQ"][3]) - expect_noise_hz) < 0.01, True)

w(0x400E, 0x80 | 0x05)       # mode 1, SAME period index -> asset changes, freq doesn't
check("noise index changes with mode bit (mode1 -> noiseB=2)", i_(it.vars["APU_NOISEIDX"]), 2)
check("frequency unchanged by a mode-only switch (same period index)",
      abs(float(it.lists["APU_FREQ"][3]) - expect_noise_hz) < 0.01, True)

w(0x400E, 0x80 | 0x00)       # mode 1, period index 0 (shortest, real period=4)
expect_short_hz = CPU_HZ / NOISE_PERIOD_TABLE[0]
check("noise period index 0 (shortest/highest freq) computed correctly",
      abs(float(it.lists["APU_FREQ"][3]) - expect_short_hz) < 0.01, True)
check("that frequency is now WAY above BASE_NOISE_HZ (reached via pitch, "
      "not by rendering ultrasonic content directly)",
      expect_short_hz / BASE_NOISE_HZ > 50, True)

# ---- $4015 channel enable/disable ----
w(0x4000, 0b00_0_1111)  # pulse1 loud again
check("pulse1 audible before disable", i_(it.lists["APU_VOL"][0]) > 0, True)
w(0x4015, 0b0000)       # disable all 4 channels (bits 0-3 clear)
check("pulse1 silenced by $4015 bit0=0", i_(it.lists["APU_VOL"][0]), 0)
check("pulse2 silenced by $4015 bit1=0", i_(it.lists["APU_VOL"][1]), 0)
check("triangle silenced by $4015 bit2=0", i_(it.lists["APU_VOL"][2]), 0)
check("noise silenced by $4015 bit3=0", i_(it.lists["APU_VOL"][3]), 0)

# ---- no-op registers must not raise or corrupt state ----
before = list(it.lists["APU_FREQ"])
w(0x4001, 0x00)  # pulse1 sweep -- not implemented, must be a safe no-op
w(0x4017, 0x00)  # frame counter -- not implemented, must be a safe no-op
check("unimplemented registers don't corrupt APU_FREQ", list(it.lists["APU_FREQ"]), before)

# ---- Length counter: the actual fix for "notes play too long" ----
LEN_TABLE = [10, 254, 20, 2, 40, 4, 80, 6, 160, 8, 60, 10, 14, 12, 26, 14,
            12, 16, 24, 18, 48, 20, 96, 22, 192, 24, 72, 26, 16, 28, 32, 30]

# $4003's top 5 bits (llll l) select the length-table index. v=0x08 -> index
# IDIV(8,8)=1 -> table[1]=254 -> 254/120 seconds.
w(0x4002, 0)
w(0x4003, 0x08)
expect_len = LEN_TABLE[1] / 120.0
check("pulse1 length-table lookup (index 1 -> 254 half-frame ticks)",
      abs(float(it.lists["APU_LENSEC"][0]) - expect_len) < 0.001, True)

# A different index picks a different, independently-computed duration.
w(0x4003, 0x08 | (5 << 3))  # index = IDIV(0x28,8)=5 -> table[5]=4
expect_len2 = LEN_TABLE[5] / 120.0
check("a different length index gives a different duration",
      abs(float(it.lists["APU_LENSEC"][0]) - expect_len2) < 0.001, True)
check("...and it's shorter than the first (254 vs 4 ticks)", expect_len2 < expect_len, True)

# Triangle ($400B) and noise ($400F) use the same table independently.
w(0x400A, 0)
w(0x400B, 0x10)  # index=IDIV(0x10,8)=2 -> table[2]=20
expect_tri_len = LEN_TABLE[2] / 120.0
check("triangle length independent of pulse1's",
      abs(float(it.lists["APU_LENSEC"][2]) - expect_tri_len) < 0.001, True)

w(0x400F, 0x18)  # index=IDIV(0x18,8)=3 -> table[3]=2
expect_noise_len = LEN_TABLE[3] / 120.0
check("noise length independent of pulse1/triangle's",
      abs(float(it.lists["APU_LENSEC"][3]) - expect_noise_len) < 0.001, True)
check("pulse1's length untouched by triangle/noise length writes",
      abs(float(it.lists["APU_LENSEC"][0]) - expect_len2) < 0.001, True)

print("\n%s" % ("ALL APU INTEGRATION CHECKS PASSED" if not FAILURES
                else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
