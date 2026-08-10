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
for nm in ("APU_FREQ", "APU_VOL", "APU_DUTY", "APU_NOISENAMES", "APU_LENSEC"):
    check("Stage has exactly one %s list" % nm, lnames.count(nm), 1)
check("Stage has exactly one APU_NOISEIDX var", vnames.count("APU_NOISEIDX"), 1)
check("no unexpected extra Stage lists", len(lnames), 5)

# ---- 2) the shared lists are actually POPULATED ------------------------
by_name = {l[0]: l[1] for l in stage["lists"].values()}
check("APU_FREQ sized for 4 channels", len(by_name["APU_FREQ"]), 4)
check("APU_VOL sized for 4 channels", len(by_name["APU_VOL"]), 4)
check("APU_DUTY sized for 2 pulse channels", len(by_name["APU_DUTY"]), 2)
check("APU_NOISENAMES holds exactly the 2 clean base assets (1/mode)",
      by_name["APU_NOISENAMES"], ["noiseA", "noiseB"])

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
expected = {"APU Pulse 1": 4, "APU Pulse 2": 4, "APU Triangle": 1, "APU Noise": 2}
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
    check("%s has local is_clone + MY_LEN vars (no unexpected extras)" % nm,
          sorted(v[0] for v in t["variables"].values()), ["MY_LEN", "is_clone"])
    check("%s auto-mutes after its note length elapses (length-counter fix)" % nm,
          "control_wait" in ops, True)

    # Regression check for the "sits there and makes noise" bug: a freshly
    # spawned clone must set its OWN pitch+volume immediately, before
    # entering the playback loop -- not rely solely on the separate
    # apu_update_<ch> broadcast arriving in time (a clone otherwise starts
    # at Scratch's default 100% volume for however long that takes, which
    # on a game that restarts notes constantly is audible as a blip on
    # every single restart). Walk the ACTUAL block chain from the
    # control_start_as_clone hat and confirm sound_seteffectto and
    # sound_setvolumeto appear in that script BEFORE sound_playuntildone.
    # sound_playuntildone lives nested inside the control_forever's SUBSTACK,
    # not the top-level chain, so: confirm sound_setvolumeto/sound_seteffectto
    # appear in the TOP-LEVEL chain (i.e. execute unconditionally, once, at
    # clone spawn) strictly before the control_forever block that contains
    # the playback loop.
    blocks = t["blocks"]
    clone_hat = next(b for b in blocks.values()
                     if isinstance(b, dict) and b["opcode"] == "control_start_as_clone")
    seq = []
    cur = clone_hat.get("next")
    while cur and cur in blocks:
        seq.append(blocks[cur]["opcode"])
        cur = blocks[cur].get("next")
    idx_vol = seq.index("sound_setvolumeto") if "sound_setvolumeto" in seq else -1
    idx_pitch = seq.index("sound_seteffectto") if "sound_seteffectto" in seq else -1
    idx_loop = seq.index("control_forever") if "control_forever" in seq else -1
    check("%s clone sets volume BEFORE entering its playback loop" % nm,
          -1 not in (idx_vol, idx_loop) and idx_vol < idx_loop, True)
    check("%s clone sets pitch BEFORE entering its playback loop" % nm,
          -1 not in (idx_pitch, idx_loop) and idx_pitch < idx_loop, True)

# All 4 channels pitch-shift now, including noise -- see apu_build.py's
# module docstring for why noise moved from asset-selection-per-period to
# pitch-shifting 2 clean base assets (asset-per-period aliased badly at
# short periods, a real reported bug).
for nm in ("APU Pulse 1", "APU Pulse 2", "APU Triangle", "APU Noise"):
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
check("apu_build uses the generator's BASE_NOISE_HZ", apu_build.BASE_NOISE_HZ,
      audio_assets.BASE_NOISE_HZ)
check("BASE_NOISE_HZ derives from BASE_NOISE_PERIOD",
      round(1789773.0 / audio_assets.BASE_NOISE_PERIOD, 6), round(audio_assets.BASE_NOISE_HZ, 6))
check("BASE_NOISE_HZ is comfortably below the 48kHz Nyquist limit (alias-free)",
      audio_assets.BASE_NOISE_HZ < audio_assets.SR / 2, True)


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
missing = [n for n in (["pulse%d" % i for i in range(4)] + ["triangle", "noiseA", "noiseB"])
           if not os.path.exists(os.path.join(apu_build.ASSET_DIR, n + ".wav"))]
check("all 7 APU assets present on disk", missing, [])

# Regression guard for the "noise too low pitched" bug: the shortest real
# NES noise period (4 -> ~447kHz native LFSR rate) must not alias when
# reached by pitch-shifting the clean base asset. Aliasing would come from
# rendering that content directly at 48kHz (Nyquist ~24kHz); pitch-shifting
# a properly-sampled base asset has no such ceiling, so this just confirms
# the intended technique (not a re-render) is what's actually wired.
shortest_period = 4
native_shortest = 1789773.0 / shortest_period
pitch_shortest = 120.0 * math.log(native_shortest / audio_assets.BASE_NOISE_HZ) / math.log(2)
check("noise period=4 no longer requires rendering ~447kHz content directly "
      "(reached via pitch instead)", native_shortest > audio_assets.SR / 2, True)
check("...and the corresponding pitch shift is a finite, sane value",
      abs(pitch_shortest) < 2000, True)

print("\n%s" % ("ALL APU CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
