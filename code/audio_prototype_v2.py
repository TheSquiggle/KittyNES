"""Click-train audio prototype v2/v3 -- fixes for the two complaints from
the v1 human listening test:

1. "Gap between pops is too large." Hypothesis: this is NOT the WAV asset's
   baked-in silence padding being wrong -- it's almost certainly Scratch's
   `forever` C-block yielding once per iteration at the screen-refresh
   boundary (~16ms in vanilla Scratch, and even TurboWarp's compiler still
   respects a yield-per-frame for a `forever` loop unless it's inside a
   `warp: true` custom block). For a 2000-4000Hz tone the whole period is
   only 0.25-0.5ms -- a 16ms scheduler tax stacked on top of that would
   completely dominate and sound exactly like "pops too far apart" at
   every frequency, not just the high ones (which matches what was
   reported). FIX (v2 and v3): move the `forever: play sound until done`
   loop's body into a `warp: true` custom block (a "run without screen
   refresh" procedure) instead of a raw top-level `forever`. Warp mode
   suppresses the per-iteration screen-refresh yield; only
   `sound_playuntildone`'s own natural yield (waiting for the sound to
   actually finish) should govern timing then -- which is the
   sample-accurate part we actually want driving the rate.

2. "Higher pitches sound thin." Hypothesis: v1 capped the click transient
   at 40% of the period to guarantee silence margin at every tested
   frequency, which made the click itself very short (e.g. only 4 samples
   at 4000Hz) -- possibly too short to read as a full "pop" rather than a
   faint tick. FIX (v3 only, v2 keeps v1's 40% for an apples-to-apples
   comparison against JUST the warp fix): raise the click proportion to
   65% of the period. This trades away silence margin for a fuller-
   sounding click, which may or may not be an improvement -- that's
   exactly the open tradeoff question for the listening test.

Still UNVERIFIED. Still not integrated into the main nes_emulator build.
See research/audio_click_train_approach.md for the full writeup and what
to listen for/report back.
"""
import os
import sys
import tempfile

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu, Reporter
from audio_prototype import generate_click_train_wav, TEST_FREQS


def build(out_path, click_fraction, label):
    e = Emu("Speaker")
    sprite = e.t

    print("Building %s (click_fraction=%.0f%%):" % (label, click_fraction * 100))
    sound_names = []
    tmp_paths = []
    for freq in TEST_FREQS:
        wav_bytes, total_samples, click_samples, period_s = generate_click_train_wav(
            freq, click_fraction=click_fraction)
        name = "note_%dhz" % freq
        sound_names.append(name)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name
        tmp_paths.append(tmp_path)
        e.proj.add_sound_from_file(sprite, tmp_path, name=name)
        print("  %-12s total_samples=%-6d click_samples=%-4d" %
              (name, total_samples, click_samples))

    for p in tmp_paths:
        os.unlink(p)

    e.var("CURRENT", 0)

    key_names = ["1", "2", "3", "4", "5"]
    for i, k in enumerate(key_names):
        s = e.script("event_whenkeypressed", fields={"KEY_OPTION": [k]})
        e.setv(s, "CURRENT", i)
        s.finalize()

    # ---- THE FIX: the forever-loop body now lives inside a warp:true
    # custom block, so only sound_playuntildone's own yield governs timing,
    # not a per-iteration screen-refresh tax. ----
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

    e.save(out_path)
    print("Saved", out_path)
    print("Sprite: 'Speaker', warp-mode driving loop, keys 1-5 switch note.\n")


if __name__ == "__main__":
    # v2: warp fix only, same 40%-click-fraction WAV assets as v1 (so a
    # human comparing v1 vs v2 isolates JUST the yield-per-iteration fix)
    build(r"D:\KittyNES\progress\audio_prototype_v2.sb3", 0.40,
          "v2 (warp fix, same 40% clicks as v1)")
    # v3: warp fix + fatter clicks (65% of period), addressing "thin at
    # high pitches" at the cost of less silence margin
    build(r"D:\KittyNES\progress\audio_prototype_v3.sb3", 0.65,
          "v3 (warp fix + 65% clicks, addresses 'thin' complaint)")
