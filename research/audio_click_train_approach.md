# Audio approach prototype: "click train" pitch approximation

**Status: UNVERIFIED PROTOTYPE.** This document describes a technique and a
standalone test build (`code/audio_prototype.py` ->
`progress/audio_prototype.sb3`), NOT a completed or working feature. Nothing
in this project has confirmed it sounds right — that requires a human to
open the `.sb3` in TurboWarp/Scratch and actually listen. See "What needs
human verification" at the end.

## The core idea

Scratch has no way to synthesize a raw waveform sample-by-sample in real
time (no audio-buffer-writing block), so a full NES APU emulation can't
"just" generate a square/triangle/noise wave directly the way the CPU/PPU
work generates pixel data into `FB`. The workaround under test here is the
classic one-bit-PC-speaker "click train" technique: approximate a tone at
frequency `f` by playing a very short click/pop sound repeatedly, `f` times
per second. A rapid enough click train is audibly perceived as a pitch at
that repetition rate (this part of the technique itself is old and well
established outside Scratch — the question is specifically whether it's
achievable *inside* Scratch with adequate timing precision).

## Why NOT use a script loop for timing

The obvious first approach — `forever: play sound click; wait (1/f) seconds`
— doesn't work: Scratch's `wait` block and its script scheduler are
frame-quantized (tied to the project's tick rate, typically ~30-60Hz even
in TurboWarp's fastest modes), nowhere near precise enough to space clicks
at audible-pitch rates (110Hz-4000Hz, i.e. gaps of 0.25ms-9ms) accurately.
This would produce audibly wrong, drifting, or completely absent pitch.

## The technique under test: bake the timing into the asset

Instead of timing the gap between clicks via script logic, **bake the
silence directly into the WAV asset itself**, so one asset = one full click
cycle at a fixed frequency: a short click/pop transient at the start,
padded with digital silence (sample value 0) so the **total asset duration
equals exactly `1/f` seconds**. The driving script becomes just:

```
forever:
    play sound <note_asset> until done
```

The theory: `sound_playuntildone` (`sound_playuntildone` opcode) is a
**yielding** block — the script thread genuinely suspends until that
specific sound instance reports it has finished playing, and the actual
audio scheduling/mixing/timing happens in the browser's real audio engine
(Web Audio API under the hood, in both scratch-vm and TurboWarp), not in
Scratch's own frame-quantized script scheduler. If that's true, the
resulting click rate should be sample-accurate to the baked-in asset
length, essentially independent of Scratch's own tick rate or performance.

**This assumption is exactly what's unverified and needs a real listening
test before any further investment in a full APU**, because if it's wrong
(e.g. if there's measurable per-call overhead in triggering/resolving each
`sound_playuntildone` call — script-yield/resume overhead, audio-graph
node creation, etc. — even a fraction of a millisecond of average overhead
per call would be a huge fraction of the ~0.25ms period at the top of the
NES's frequency range) the whole approach needs rethinking.

## WAV generation details (`code/audio_prototype.py`)

- **Sample rate**: 44,100 Hz (standard; matches `add_sound_from_file`'s
  hardcoded `"rate": 44100` sound-asset metadata field in `sb3_builder.py`,
  though that field is likely just informational since the WAV header
  itself carries the authoritative rate).
- **Bit depth**: 16-bit signed PCM, mono, written via Python's stdlib
  `wave` module (no external dependencies).
- **Total asset length**: `total_samples = round(sample_rate / freq_hz)`
  — i.e. rounded to the nearest whole sample. This introduces a small
  frequency error (typically well under 0.3% at the tested frequencies —
  see the table below), unavoidable since audio samples are discrete; NES
  hardware itself has similar quantization (its APU frequency dividers are
  also integer-based), so this isn't a departure from how real hardware
  behaves, just a different quantization grid.
- **Click/pop transient**: a decaying-sine burst — `amplitude * exp(-6*i /
  click_samples) * sin(2*pi*1500*t)` — i.e. an exponentially-decaying
  1500Hz "carrier" tone. The 1500Hz carrier is an arbitrary, fixed "click
  timbre" completely unrelated to the note's own frequency `freq_hz` — the
  note's pitch is expressed ONLY by how often the whole asset repeats
  (`1/freq_hz` seconds), never by anything inside the click waveform
  itself. Amplitude clamped to 85% of full 16-bit range to leave headroom.
- **Silence padding**: the remainder of the asset after the click is
  literal digital silence (sample value 0).

### Click duration vs. period — a real finding worth flagging

Initially clamped the click to "as long as requested (3ms), unless that
would exceed the whole period, in which case shrink to fit" (leaving as
little as 1 silent sample at very high frequencies). This turned out to
bite at **much lower frequencies than expected**: a 3ms click is already
longer than a full period above ~330Hz (`period = 1/330 ≈ 3.03ms`), so
above that pitch the naive clamp left essentially no silence gap at all —
not just at the very top of the tested range (4000Hz) as originally
assumed, but starting well within the middle of the NES pulse-channel
range. Changed the clamp to cap the click at a fixed **40% of the total
period**, guaranteeing at least 60% silence at every tested frequency, at
the cost of the click itself getting proportionally shorter (and likely
quieter/less distinctly "poppy") as pitch rises:

| Test note | Period (total samples) | Click samples (40%-capped) | Asset duration | Frequency error |
|---|---|---|---|---|
| 110 Hz | 401 | 132 (3ms, under the 40% cap) | 9.093 ms | +0.023% |
| 440 Hz | 100 | 40 | 2.268 ms | -0.227% |
| 880 Hz | 50 | 20 | 1.134 ms | -0.227% |
| 2000 Hz | 22 | 8 | 0.499 ms | -0.227% |
| 4000 Hz | 11 | 4 | 0.249 ms | -0.227% |

**This is a real, generator-level open question, not just a theoretical
one**: at 4000Hz the click is only 4 samples long — barely enough to be a
"click" at all rather than an abrupt single-sample-ish pop, and at 44.1kHz
sample rate there just isn't much room to work with in an 11-sample total
period. Whether this still sounds like a clean, identifiable pitch (as
opposed to a faint/inconsistent tick) at the higher end of the NES's
frequency range is itself part of what the human listening test needs to
establish — it may turn out a different click design (shorter carrier
period, different envelope shape, or even just a 1-2 sample impulse
instead of a shaped decay) works better at high frequencies, or that this
technique is simply better suited to the NES's lower/mid pulse-channel
range and something else is needed for its highest notes.

## The prototype build

`code/audio_prototype.py` generates 5 WAV assets (110/440/880/2000/4000 Hz)
and builds a single-sprite ("Speaker") `.sb3`:

- `green flag clicked` → `forever: (check CURRENT, play the matching sound
  until done)` — the loop under test.
- `when key [1-5] pressed` → sets the `CURRENT` variable (0-4), switching
  which note plays. Since the `forever` loop only re-checks `CURRENT` at
  each `sound_playuntildone` boundary, switching latency is at most one
  period of whichever note is currently playing (imperceptible except
  possibly at 110Hz's ~9ms).

Saved to `progress/audio_prototype.sb3`. Validated structurally with
`validate_sb3.py` (clean) — **structural validity is the only thing
confirmed programmatically here.** This is deliberately NOT integrated
into the main `nes_emulator.sb3` build; it's a standalone feasibility test.

## What needs human verification

Open `progress/audio_prototype.sb3` in TurboWarp (or scratch-gui), click
the green flag, and press keys 1-5 to switch between the 5 test notes.
Listen for, and please report back:

1. **Does each note sound like a clean, roughly steady pitch** — i.e. does
   it actually sound like a tone at approximately the labeled frequency
   (110/440/880/2000/4000 Hz), or does it sound more like an unpitched
   buzz/noise/rattle?
2. **Is it choppy / are there audible gaps or stutters** between repeats —
   this would suggest `sound_playuntildone`'s resume timing has enough
   real-world overhead/jitter to break the "sample-accurate to the baked
   asset length" assumption this whole technique depends on.
3. **Does the pitch drift** over several seconds of sustained playback (a
   symptom of accumulating small timing errors), or does it stay locked to
   one pitch?
4. **How does it compare across the 5 test frequencies** — does it get
   worse (choppier, less clearly pitched) at the high end (2000/4000Hz)
   where the click itself is only 4-8 samples long, consistent with the
   "click duration vs. period" concern above? Does the low end (110Hz)
   sound noticeably more like a real "pop train" than the higher notes
   (since it has the most headroom for the click transient)?
5. **General subjective quality** — even if the pitch/timing hold up
   correctly, does the 1500Hz-decaying-sine click timbre actually sound
   reasonably close to an NES pulse-channel tone, or would a different
   click waveform be worth trying (this is a much lower-stakes question
   than 1-3 above, since click timbre is easy to iterate on once the core
   timing assumption is confirmed one way or the other)?

**Until this feedback comes back, no claim is being made that the click-
train technique "works."** If it turns out `sound_playuntildone` has too
much overhead/jitter for this to sound clean, the full Phase 9 APU (all 4
channels driven by real `$4000-$4013` register writes, envelope/sweep/
length-counter approximation) should NOT be built on top of this
technique without first finding/testing an alternative.
