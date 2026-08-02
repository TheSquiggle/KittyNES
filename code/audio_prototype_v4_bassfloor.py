"""Diagnostic prototype v4: isolates whether the click-train technique's
remaining "not popping fast enough" problem (reported even after the v2/v3
warp-mode fix) is a SCRIPT-TIMING issue or an AUDIO-ENGINE-LATENCY floor.

Verified so far (see research/audio_click_train_approach.md):
- The warp mutation IS correctly applied and serialized (`"warp": "true"`
  in the generated JSON, matching real Scratch's own sb3 export convention
  -- this is not a serialization bug).
- The user reports v2/v3 (warp-fixed) STILL doesn't pop fast enough.

This points at a different, harder-to-fix bottleneck: `sound_playuntildone`
is a yielding block by design, but EVERY cycle it also has to physically
START a brand-new sound playback in the browser's audio engine (Web Audio
API under the hood) -- creating/connecting/scheduling a new
AudioBufferSourceNode. That overhead lives entirely below the level Scratch
blocks (or warp mode) can control; no amount of script-scheduler fixing can
eliminate it if it's the actual bottleneck.

This prototype tests the specific, falsifiable prediction that follows from
that hypothesis: since the overhead per cycle should be roughly CONSTANT
(a fixed number of milliseconds to start a new sound, regardless of that
sound's own length), it should be a much smaller fraction of the period at
LOW frequencies (long periods) than at high ones. If bass-range notes
(55/82/110/165/220 Hz -- roughly the NES triangle channel's low end) sound
clean and steady while the original 2000-4000Hz notes don't, that's strong
evidence for a fixed per-cycle audio-engine floor rather than a remaining
script-timing bug. If even the LOWEST frequency here still sounds gappy,
that points back toward something else (worth re-opening the script-timing
investigation).

Same warp-mode-fixed structure as v2/v3. Still unverified -- needs a human
listening test, specifically comparing "does the gap shrink as frequency
drops" rather than just "does it sound clean or not."
"""
import os
import sys
import tempfile

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu, Reporter
from audio_prototype import generate_click_train_wav

BASS_FREQS = [55, 82, 110, 165, 220]  # roughly NES triangle-channel low range


def build():
    e = Emu("Speaker")
    sprite = e.t

    print("Building v4 bass-floor diagnostic (frequencies: %s):" % BASS_FREQS)
    sound_names = []
    tmp_paths = []
    for freq in BASS_FREQS:
        wav_bytes, total_samples, click_samples, period_s = generate_click_train_wav(
            freq, click_fraction=0.40)
        name = "note_%dhz" % freq
        sound_names.append(name)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name
        tmp_paths.append(tmp_path)
        e.proj.add_sound_from_file(sprite, tmp_path, name=name)
        print("  %-12s total_samples=%-6d click_samples=%-4d period=%.2fms" %
              (name, total_samples, click_samples, period_s * 1000))

    for p in tmp_paths:
        os.unlink(p)

    e.var("CURRENT", 0)
    key_names = ["1", "2", "3", "4", "5"]
    for i, k in enumerate(key_names):
        s = e.script("event_whenkeypressed", fields={"KEY_OPTION": [k]})
        e.setv(s, "CURRENT", i)
        s.finalize()

    s = e.defproc("play_notes_forever", [], warp=True)
    with e.FOREVER(s) as body:
        for i, name in enumerate(sound_names):
            cond = e.EQ(e.V("CURRENT"), i)
            with e.IF(body, cond) as branch:
                menu = e._op("sound_sounds_menu", fields={"SOUND_MENU": [name]})
                branch.stack("sound_playuntildone", SOUND_MENU=Reporter(menu.block_id))
    s.finalize()

    s = e.script("event_whenflagclicked")
    e.setv(s, "CURRENT", 0)
    e.call(s, "play_notes_forever")
    s.finalize()

    out = r"D:\KittyNES\progress\audio_prototype_v4_bassfloor.sb3"
    e.save(out)
    print("Saved", out)
    print("Keys 1-5 = %s Hz. Listen for whether the gap SHRINKS as you go from key 5 (220Hz) down to key 1 (55Hz)." % BASS_FREQS)


if __name__ == "__main__":
    build()
