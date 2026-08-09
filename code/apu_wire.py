"""Phase 9 integration: wires real $4000-$4015 CPU writes to the APU sprite
graph built by apu_build.py. See docs/apu_registers.md for the register specs
this implements (verified against nesdev.org, not recalled).

Shared state (APU_FREQ/APU_VOL/APU_DUTY/APU_NOISENAMES/APU_NOISEIDX) must be
created ONCE and shared between the NES core and every channel sprite -- see
the apu_build.py docstring for why a naive glob=True per-sprite call doesn't
do that. `wire_apu(nes_emu, proj)` owns creating them and returns the shared
ids so apu_build.build_apu() can be told to reuse them instead of minting its
own duplicate copies.
"""
import math
import sys

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Reporter, broadcast_input

CPU_HZ = 1789773.0


def create_shared_apu_state(e):
    """Create the cross-sprite APU globals via `e` (any Emu instance) and
    return {name: id} for both lists and the one shared var. Call this
    EXACTLY ONCE per project; pass the result to both wire_apu() and
    apu_build.build_apu(proj, shared=...)."""
    ids = {}
    ids["APU_FREQ"] = e.lst("APU_FREQ", [0, 0, 0, 0], glob=True)
    ids["APU_VOL"] = e.lst("APU_VOL", [0, 0, 0, 0], glob=True)
    ids["APU_DUTY"] = e.lst("APU_DUTY", [0, 0], glob=True)
    noise_names = ["noise%d_%d" % (m, p) for m in (0, 1)
                   for p in (4, 8, 16, 32, 64, 96, 128, 160,
                             202, 254, 380, 508, 762, 1016, 2034, 4068)]
    ids["APU_NOISENAMES"] = e.lst("APU_NOISENAMES", noise_names, glob=True)
    ids["APU_NOISEIDX"] = e.var("APU_NOISEIDX", 1, glob=True)
    return ids


def _inject(e, shared_ids):
    """Make a fresh Emu instance see already-created shared globals as its
    own, instead of minting duplicates (the exact bug this pattern exists to
    avoid -- see apu_build.py / PROGRESS_LOG)."""
    for nm in ("APU_FREQ", "APU_VOL", "APU_DUTY", "APU_NOISENAMES"):
        e.lists[nm] = shared_ids[nm]
        e._global_lists.add(nm)
    e.vars["APU_NOISEIDX"] = shared_ids["APU_NOISEIDX"]
    e._global_vars.add("APU_NOISEIDX")


def wire_apu(e, shared_ids, broadcasts):
    """Build `apu_write %s %s` on the NES core sprite `e` and route
    $4000-$4015 there from bus_write. broadcasts = apu_build.build_apu()'s
    return value: {"update": {ch: id}, "restart": {ch: id}, "stop": id}."""
    _inject(e, shared_ids)
    e.var("AW_T", 0)     # scratch: assembled 11-bit timer value
    e.var("AW_DD", 0)    # scratch: duty/mode field
    e.var("AW_VV", 0)    # scratch: volume field
    e.var("AW_HZ", 0)    # scratch: computed frequency
    e.var("AW_OLD", 0)   # scratch: previous duty/noise-index, to gate restarts
    # Low bytes of the 11-bit timers, latched by the $4002/$4006/$400A
    # writes and combined when the matching high-byte register is written.
    e.var("AW_TLO1", 0)
    e.var("AW_TLO2", 0)
    e.var("AW_TLO3", 0)

    # Reverse-map broadcast id -> name (apu_build.py's exact naming
    # convention) so call sites can just pass the id, as before.
    bname = {}
    for ch, bid in broadcasts["update"].items():
        bname[bid] = "apu_update_%d" % ch
    for ch, bid in broadcasts["restart"].items():
        bname[bid] = "apu_restart_%d" % ch
    bname[broadcasts["stop"]] = "apu_stop_all"

    def bcast(body, bid):
        body.stack("event_broadcast", BROADCAST_INPUT=broadcast_input(bid, bname[bid]))

    def set_pulse_duty_vol(body, ch, v):
        e.setv(body, "AW_DD", e.MOD(e.IDIV(v, 64), 4))
        e.setv(body, "AW_VV", e.MOD(v, 16))
        e.setv(body, "AW_OLD", e.IT("APU_DUTY", ch))
        e.repl(body, "APU_DUTY", ch, e.V("AW_DD"))
        e.repl(body, "APU_VOL", ch, e.IDIV(e.MUL(e.V("AW_VV"), 100), 15))
        with e.IF(body, e.NOT(e.EQ(e.V("AW_OLD"), e.V("AW_DD")))) as r:
            bcast(r, broadcasts["restart"][ch])
        bcast(body, broadcasts["update"][ch])

    def set_pulse_timer_hi(body, ch, v, tlo_name):
        e.setv(body, "AW_T", e.ADD(e.MUL(e.MOD(v, 8), 256), e.V(tlo_name)))
        # f = fCPU / (16*(t+1)); t<8 silences the channel (nesdev spec).
        ctx = e.IFELSE(body, e.LT(e.V("AW_T"), 8))
        with ctx as b:
            e.repl(b, "APU_FREQ", ch, 0)
        with ctx.substack2() as b:
            e.setv(b, "AW_HZ", e.DIVR(CPU_HZ, e.MUL(16, e.ADD(e.V("AW_T"), 1))))
            e.repl(b, "APU_FREQ", ch, e.V("AW_HZ"))
        bcast(body, broadcasts["update"][ch])

    def emit(body, addr):
        if addr == 0x4000:
            set_pulse_duty_vol(body, 1, e.ARG("v"))
        elif addr == 0x4002:
            e.setv(body, "AW_TLO1", e.ARG("v"))
        elif addr == 0x4003:
            set_pulse_timer_hi(body, 1, e.ARG("v"), "AW_TLO1")
        elif addr == 0x4004:
            set_pulse_duty_vol(body, 2, e.ARG("v"))
        elif addr == 0x4006:
            e.setv(body, "AW_TLO2", e.ARG("v"))
        elif addr == 0x4007:
            set_pulse_timer_hi(body, 2, e.ARG("v"), "AW_TLO2")
        elif addr == 0x4008:
            # CRRRRRRR: control(halt)+linear-reload. RRRRRRR==0 (with C set)
            # silences the channel; otherwise treat as "sounding" at a fixed
            # level -- triangle has no real volume control on hardware.
            ctx = e.IFELSE(body, e.EQ(e.MOD(e.ARG("v"), 128), 0))
            with ctx as b:
                e.repl(b, "APU_VOL", 3, 0)
            with ctx.substack2() as b:
                e.repl(b, "APU_VOL", 3, 70)
            bcast(body, broadcasts["update"][3])
        elif addr == 0x400A:
            e.setv(body, "AW_TLO3", e.ARG("v"))
        elif addr == 0x400B:
            e.setv(body, "AW_T", e.ADD(e.MUL(e.MOD(e.ARG("v"), 8), 256), e.V("AW_TLO3")))
            # f = fCPU / (32*(t+1)); mute t<2 (real hardware ultrasonics we
            # don't try to reproduce -- see docs/apu_registers.md).
            ctx = e.IFELSE(body, e.LT(e.V("AW_T"), 2))
            with ctx as b:
                e.repl(b, "APU_FREQ", 3, 0)
            with ctx.substack2() as b:
                e.setv(b, "AW_HZ", e.DIVR(CPU_HZ, e.MUL(32, e.ADD(e.V("AW_T"), 1))))
                e.repl(b, "APU_FREQ", 3, e.V("AW_HZ"))
            bcast(body, broadcasts["update"][3])
        elif addr == 0x400C:
            e.setv(body, "AW_VV", e.MOD(e.ARG("v"), 16))
            e.repl(body, "APU_VOL", 4, e.IDIV(e.MUL(e.V("AW_VV"), 100), 15))
            bcast(body, broadcasts["update"][4])
        elif addr == 0x400E:
            e.setv(body, "AW_DD", e.MOD(e.IDIV(e.ARG("v"), 128), 2))    # mode bit
            e.setv(body, "AW_VV", e.MOD(e.ARG("v"), 16))                # period index
            e.setv(body, "AW_T", e.ADD(e.ADD(e.MUL(e.V("AW_DD"), 16), e.V("AW_VV")), 1))
            e.setv(body, "AW_OLD", e.V("APU_NOISEIDX"))
            e.setv(body, "APU_NOISEIDX", e.V("AW_T"))
            with e.IF(body, e.NOT(e.EQ(e.V("AW_OLD"), e.V("AW_T")))) as r:
                bcast(r, broadcasts["restart"][4])
            bcast(body, broadcasts["update"][4])
        elif addr == 0x4015:
            # ---D.NT21: enable bits. A cleared bit force-silences that
            # channel (zeroing length counters on hardware; we approximate
            # by just zeroing volume, which is the audible effect).
            for bit, ch in ((0, 1), (1, 2), (2, 3), (3, 4)):
                with e.IF(body, e.EQ(e.MOD(e.IDIV(e.ARG("v"), 2 ** bit), 2), 0)) as m:
                    e.repl(m, "APU_VOL", ch, 0)
                    bcast(m, broadcasts["update"][ch])
        # else ($4001/$4005 sweep, $4009/$400D unused, $4010-$4013 DMC,
        # $4017 frame counter): accepted, intentionally no-op for now.

    s = e.defproc("apu_write", ["a", "v"])
    e.dispatch(s, lambda: e.ARG("a"),
               [0x4000, 0x4002, 0x4003, 0x4004, 0x4006, 0x4007, 0x4008,
                0x400A, 0x400B, 0x400C, 0x400E, 0x4015,
                # filler keys so dispatch's binary search has real bounds for
                # the no-op ranges between real registers; each maps to a
                # body that does nothing (handled by the `else` in emit()).
                0x4001, 0x4005, 0x4009, 0x400D, 0x400F, 0x4010, 0x4011,
                0x4012, 0x4013, 0x4014, 0x4016, 0x4017],
               emit)
    s.finalize()
    return s
