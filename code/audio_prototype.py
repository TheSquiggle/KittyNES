"""Standalone prototype: tests the "click train" audio approximation
technique in isolation, BEFORE building the full APU (Phase 9). NOT
integrated into the main nes_emulator build.

Technique being tested: instead of using a Scratch script loop's own timing
to space out repeated "clicks" (bounded by Scratch's frame-quantized
scheduler, nowhere near precise enough for audible pitch), each click's
silence-padding is baked directly into the WAV asset itself, so the asset's
total duration equals exactly 1/frequency seconds. The driving script is
then just:

    forever:
        play sound <current_note_asset> until done

`sound_playuntildone` is a yielding block -- the script genuinely blocks
until that sound finishes -- and actual sample-accurate playback timing is
handled by the browser's real audio engine (Web Audio), not Scratch's own
tick rate. So (in theory) the resulting click rate should be sample-accurate
to the baked-in asset length, independent of Scratch's frame rate. This is
UNVERIFIED -- see research/audio_click_train_approach.md for the writeup and
open questions, and PROGRESS_LOG.md for status. A human needs to actually
listen to this in TurboWarp/Scratch; nothing here confirms it sounds right.
"""
import math
import os
import struct
import sys
import tempfile
import wave

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu, Reporter

SAMPLE_RATE = 44100
BIT_DEPTH = 16
CLICK_MS = 3.0            # target click/pop transient length, before clamping
CLICK_CARRIER_HZ = 1500   # the "pop" timbre -- an arbitrary short decaying tone,
                           # not related to the note frequency itself
AMPLITUDE = 0.85           # fraction of full 16-bit range, leaves headroom

TEST_FREQS = [110, 440, 880, 2000, 4000]  # spans the NES pulse-channel range


def generate_click_train_wav(freq_hz, sample_rate=SAMPLE_RATE, click_ms=CLICK_MS,
                              carrier_hz=CLICK_CARRIER_HZ, amplitude=AMPLITUDE,
                              click_fraction=0.40):
    """Return (wav_bytes, total_samples, click_samples, period_seconds) for one
    "note": a short decaying-sine click/pop at the start, padded with silence
    so the TOTAL asset length is exactly 1/freq_hz seconds (rounded to the
    nearest whole sample -- see research doc for the precision implication).

    click_fraction: the click is capped at this fraction of the period (see
    the reasoning below) -- defaults to 0.40 (v1's value); v3 raises this to
    0.65 to test whether a fuller-sounding click at the cost of less silence
    margin addresses the "higher pitches sound thin" v1 listening-test
    feedback."""
    total_samples = max(1, round(sample_rate / freq_hz))
    click_samples_wanted = max(1, round(sample_rate * click_ms / 1000.0))
    # Clamp so the click never eats the whole period. Naively clamping to
    # just "total_samples - 1" (leaving only 1 silent sample) turned out to
    # matter at MUCH lower frequencies than expected during prototyping: a
    # 3ms click is already longer than a whole period above ~330Hz (period
    # < 3ms), so a "just barely fits" clamp would leave essentially no
    # silence gap for most of the test range, not just the very top of it.
    # Instead cap the click at `click_fraction` of the period -- this makes
    # the click-vs-silence structure comparable across all test notes, at
    # the cost of the click itself getting proportionally shorter (and
    # therefore quieter/less "poppy") as frequency rises. See the research
    # doc's "click duration vs. period" open question for what this implies
    # for a real APU.
    click_samples = min(click_samples_wanted, max(1, int(total_samples * click_fraction)))

    samples = [0] * total_samples
    for i in range(click_samples):
        # exponential decay envelope over the click's own duration, carrier
        # oscillation at CLICK_CARRIER_HZ (the click's internal "pop" timbre,
        # deliberately unrelated to freq_hz -- freq_hz is expressed ONLY by
        # how often this whole asset repeats, not by anything inside it)
        t = i / sample_rate
        decay = math.exp(-6.0 * i / max(1, click_samples - 1))
        osc = math.sin(2 * math.pi * carrier_hz * t)
        samples[i] = decay * osc

    pcm = bytearray()
    maxval = (2 ** (BIT_DEPTH - 1)) - 1
    for s in samples:
        v = int(max(-1.0, min(1.0, s * amplitude)) * maxval)
        pcm += struct.pack("<h", v)

    buf = bytearray()
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp_path = tmp.name
    try:
        with wave.open(tmp_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(BIT_DEPTH // 8)
            w.setframerate(sample_rate)
            w.writeframes(bytes(pcm))
        with open(tmp_path, "rb") as f:
            buf = f.read()
    finally:
        os.unlink(tmp_path)

    return bytes(buf), total_samples, click_samples, total_samples / sample_rate


def build():
    e = Emu("Speaker")
    sprite = e.t  # the single sprite Emu created

    print("Generating click-train WAV assets:")
    sound_names = []
    tmp_paths = []
    for freq in TEST_FREQS:
        wav_bytes, total_samples, click_samples, period_s = generate_click_train_wav(freq)
        name = "note_%dhz" % freq
        sound_names.append(name)
        print("  %-12s total_samples=%-6d click_samples=%-4d asset_duration=%.6fs "
              "(target 1/%d=%.6fs, error=%.3f%%)" % (
                  name, total_samples, click_samples, period_s, freq, 1.0 / freq,
                  100.0 * (period_s - 1.0 / freq) / (1.0 / freq)))
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name
        tmp_paths.append(tmp_path)
        e.proj.add_sound_from_file(sprite, tmp_path, name=name)

    for p in tmp_paths:
        os.unlink(p)

    # ---- CURRENT: which test note is selected (0-4, index into TEST_FREQS) ----
    e.var("CURRENT", 0)

    # ---- key-press scripts: press 1-5 to switch which note is playing.
    # (the forever-loop below re-checks CURRENT every iteration, i.e. on
    # every "play sound until done" boundary -- switching latency is at
    # most one period of whichever note is currently playing) ----
    key_names = ["1", "2", "3", "4", "5"]
    for i, k in enumerate(key_names):
        s = e.script("event_whenkeypressed", fields={"KEY_OPTION": [k]})
        e.setv(s, "CURRENT", i)
        s.finalize()

    # ---- main driving script: forever play the currently-selected note,
    # "play sound until done" (the yielding block under test) ----
    s = e.script("event_whenflagclicked")
    e.setv(s, "CURRENT", 0)
    with e.FOREVER(s) as body:
        for i, name in enumerate(sound_names):
            cond = e.EQ(e.V("CURRENT"), i)
            with e.IF(body, cond) as branch:
                menu = e._op("sound_sounds_menu", fields={"SOUND_MENU": [name]})
                branch.stack("sound_playuntildone", SOUND_MENU=Reporter(menu.block_id))
    s.finalize()

    out = r"D:\KittyNES\progress\audio_prototype.sb3"
    e.save(out)
    print("\nSaved", out)
    print("Sprite: 'Speaker' with %d sound assets (%s)" % (len(sound_names), ", ".join(sound_names)))
    print("Controls: press 1-5 to switch test note, green-flag to start the click-train loop.")


if __name__ == "__main__":
    build()
