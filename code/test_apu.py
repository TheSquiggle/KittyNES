"""Structural verification of the APU sprite graph (Phase 9).

These check the class of bug the .sb3 structural validator CANNOT see: a
project can be perfectly well-formed and still be silently broken because two
sprites reference same-named-but-different variables, or a list was created
empty before its real declaration. All three bugs asserted against here were
real and were caught by hand during the initial build -- these lock them in.
"""
import json
import sys

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import apu_build

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "got=%r want=%r" % (got, want))
    if not ok:
        FAILURES.append(label)


e = Emu("Driver")
info = apu_build.build_apu(e.proj)
pj = json.loads(json.dumps(e.proj.to_json()))
stage = pj["targets"][0]
sprites = {t["name"]: t for t in pj["targets"] if not t["isStage"]}

# ---- 1) shared globals exist EXACTLY once ------------------------------
lnames = [l[0] for l in stage["lists"].values()]
vnames = [v[0] for v in stage["variables"].values()]
for nm in ("APU_FREQ", "APU_VOL", "APU_DUTY", "APU_NOISENAMES"):
    check("Stage has exactly one %s list" % nm, lnames.count(nm), 1)
check("Stage has exactly one APU_NOISEIDX var", vnames.count("APU_NOISEIDX"), 1)
check("no unexpected extra Stage lists", len(lnames), 4)

# ---- 2) the shared lists are actually POPULATED ------------------------
by_name = {l[0]: l[1] for l in stage["lists"].values()}
check("APU_FREQ sized for 4 channels", len(by_name["APU_FREQ"]), 4)
check("APU_VOL sized for 4 channels", len(by_name["APU_VOL"]), 4)
check("APU_DUTY sized for 2 pulse channels", len(by_name["APU_DUTY"]), 2)
check("APU_NOISENAMES populated (2 modes x 16 periods)",
      len(by_name["APU_NOISENAMES"]), 32)
check("APU_NOISENAMES holds real asset names",
      by_name["APU_NOISENAMES"][0], "noise0_4")

# ---- 3) every channel sprite references the SAME global ids -------------
# (the original bug: each sprite minted its own same-named list)
list_ids_used = {}
for nm, t in sprites.items():
    for b in t["blocks"].values():
        if not isinstance(b, dict):
            continue
        for f, val in b.get("fields", {}).items():
            if f == "LIST" and len(val) > 1:
                list_ids_used.setdefault(val[0], set()).add(val[1])
for lname, ids in list_ids_used.items():
    check("all sprites share ONE id for list %s" % lname, len(ids), 1)

# ---- 4) each channel sprite is wired -----------------------------------
expected = {"APU Pulse 1": 4, "APU Pulse 2": 4, "APU Triangle": 1, "APU Noise": 32}
for nm, nsounds in expected.items():
    check("%s exists" % nm, nm in sprites, True)
    t = sprites[nm]
    check("%s has %d sound assets" % (nm, nsounds), len(t["sounds"]), nsounds)
    check("%s sounds have nonzero sampleCount" % nm,
          all(s["sampleCount"] > 0 for s in t["sounds"]), True)
    check("%s sounds declare a real rate" % nm,
          all(s["rate"] >= 8000 for s in t["sounds"]), True)
    ops = [b["opcode"] for b in t["blocks"].values() if isinstance(b, dict)]
    check("%s loops a sustained sample (play until done)" % nm,
          "sound_playuntildone" in ops, True)
    check("%s spawns a sounding clone" % nm, "control_start_as_clone" in ops, True)
    check("%s has broadcast hats" % nm,
          ops.count("event_whenbroadcastreceived") >= 3, True)
    check("%s sets volume" % nm, "sound_setvolumeto" in ops, True)
    check("%s has a local is_clone flag" % nm,
          [v[0] for v in t["variables"].values()], ["is_clone"])

# pitch is used by the tonal channels, and NOT by noise (asset-selected)
for nm in ("APU Pulse 1", "APU Pulse 2", "APU Triangle"):
    ops = [b["opcode"] for b in sprites[nm]["blocks"].values() if isinstance(b, dict)]
    check("%s sets the PITCH effect" % nm, "sound_seteffectto" in ops, True)

# ---- 5) broadcasts ------------------------------------------------------
bnames = set(stage["broadcasts"].values())
for ch in (1, 2, 3, 4):
    check("broadcast apu_update_%d exists" % ch, "apu_update_%d" % ch in bnames, True)
    check("broadcast apu_restart_%d exists" % ch, "apu_restart_%d" % ch in bnames, True)
check("broadcast apu_stop_all exists", "apu_stop_all" in bnames, True)
check("build_apu returned update ids for 4 channels", sorted(info["update"]), [1, 2, 3, 4])

# ---- 6) pitch math is consistent with the assets actually generated -----
# BASE_HZ must be the frequency the assets were rendered at, or every note is
# transposed. These two modules must never drift apart.
import math
import audio_assets

check("apu_build uses the generator's BASE_HZ", apu_build.BASE_HZ, audio_assets.BASE_HZ)
check("BASE_HZ matches an exact integer samples-per-cycle asset",
      round(audio_assets.SR / audio_assets.SAMPLES_PER_CYCLE, 9),
      round(audio_assets.BASE_HZ, 9))


def pitch_for(hz):
    return 120.0 * math.log(hz / apu_build.BASE_HZ) / math.log(2)


check("pitch 0 reproduces the base frequency", round(pitch_for(apu_build.BASE_HZ), 9), 0.0)
check("one octave up = +120 pitch units",
      round(pitch_for(apu_build.BASE_HZ * 2), 6), 120.0)
check("one octave down = -120 pitch units",
      round(pitch_for(apu_build.BASE_HZ / 2), 6), -120.0)
check("one semitone up = +10 pitch units",
      round(pitch_for(apu_build.BASE_HZ * 2 ** (1 / 12.0)), 6), 10.0)

# A real NES pulse period should land on a sane pitch: f = fCPU/(16*(t+1)).
t = 253
hz = 1789773.0 / (16 * (t + 1))
check("NES timer t=253 is within Scratch's usable pitch range",
      -360 < pitch_for(hz) < 360, True)

# Assets must actually exist on disk for a build to succeed.
import os
missing = [n for n in (["pulse%d" % i for i in range(4)] + ["triangle"] +
                       ["noise%d_%d" % (m, p) for m in (0, 1)
                        for p in apu_build.NOISE_PERIODS])
           if not os.path.exists(os.path.join(apu_build.ASSET_DIR, n + ".wav"))]
check("all 37 APU assets present on disk", missing, [])

print("\n%s" % ("ALL APU CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
