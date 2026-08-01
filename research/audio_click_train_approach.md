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

## v1 human listening test results: two complaints, two fixes tried (v2/v3, STILL UNVERIFIED)

The user listened to v1 and reported two issues:

1. **"Gap between pops is too large."**
2. **"Higher pitches sound thin."**

### Complaint 1: the yield-per-iteration hypothesis

**This is very likely NOT a problem with the WAV asset's baked-in silence
padding.** It's almost certainly Scratch's `forever` C-block itself
yielding once per iteration at the screen-refresh boundary — roughly 16ms
in vanilla Scratch, and **even TurboWarp's compiler still respects a
yield-per-frame for a `forever` loop unless the loop is inside a `warp:
true` custom block.** v1's driving script was a raw top-level `forever`
containing the `play sound until done` calls — exactly the case that gets
the per-iteration yield tax.

Why this would produce exactly the reported symptom: at 2000-4000Hz the
whole period (the WAV asset's own baked-in duration) is only 0.25-0.5ms.
Stack a ~16ms scheduler yield on top of that on *every single iteration*
and the 16ms completely dominates — the actual gap between pops would be
close to 16ms regardless of which note is selected, which sounds exactly
like "pops too far apart," and would affect ALL the test notes (even
110Hz, where 16ms is still ~1.76x the note's own ~9ms period), not just
the high ones. This matches "higher pitches sound thin" being a SEPARATE
complaint from the timing one — if timing were fine but clicks were just
short, low notes would sound fine and only high notes would sound off;
instead the report was about *gaps*, most consistent with a fixed
per-iteration overhead dominating every note.

**Fix (`code/audio_prototype_v2.py` -> `progress/audio_prototype_v2.sb3`):**
moved the `forever: (check CURRENT, play sound until done)` loop's body
into a `warp: true` custom block (`play_notes_forever`), called once from
the green-flag script, instead of a raw top-level `forever`. Warp mode
suppresses the per-iteration screen-refresh yield; the theory is that only
`sound_playuntildone`'s own natural yield (waiting for the sound to
actually finish playing) should govern timing once the artificial
per-iteration tax is removed — which is the sample-accurate part the
whole technique depends on. v2 uses the exact same 40%-click-fraction WAV
assets as v1, so a human comparing v1 vs v2 isolates JUST this fix (no
confound from also changing the click sound).

**This is still unverified — same as everything else in this document.**
It's a strong, specific hypothesis (this is a well-known Scratch/TurboWarp
gotcha, not a guess pulled from nowhere), but only a listening test can
confirm the gap actually tightens up in v2.

### Complaint 2: click duration vs. period tradeoff

v1's writeup already flagged this as an open question: capping the click
at 40% of the period (chosen to guarantee silence margin at every tested
frequency) made the click itself very short at high frequencies — only 4
samples at 4000Hz — plausibly too short to read as a full "pop" rather
than a faint tick, which would explain "higher pitches sound thin."

**Fix (`code/audio_prototype_v3.py`... actually generated by the same
`code/audio_prototype_v2.py` script -> `progress/audio_prototype_v3.sb3`):**
raised the click-length cap from 40% to 65% of the period (also with the
warp-mode fix from v2, since there's no reason to re-introduce the timing
problem while testing this). New click-length table:

| Test note | Period (total samples) | v1/v2 click (40%) | v3 click (65%) |
|---|---|---|---|
| 110 Hz | 401 | 132 (3ms cap, under 40%) | 132 (3ms cap, under 65% too) |
| 440 Hz | 100 | 40 | 65 |
| 880 Hz | 50 | 20 | 32 |
| 2000 Hz | 22 | 8 | 14 |
| 4000 Hz | 11 | 4 | 7 |

This is a genuine tradeoff, not a strict improvement: a longer click
means less silence margin (down to just ~35% of the period at every
frequency instead of ~60%), so v3 could plausibly sound "fuller" but ALSO
more like a continuous tone with less distinct "click-train" character, or
could reintroduce some of the choppiness concern if the shorter silence
gap interacts badly with the yield timing. This needs to be evaluated by
ear, which is exactly what the fix is for.

### What to compare, v1 vs v2 vs v3

Three files now exist side by side:
`progress/audio_prototype.sb3` (v1, original — kept for comparison, NOT
overwritten), `progress/audio_prototype_v2.sb3` (warp fix only, same
clicks as v1), `progress/audio_prototype_v3.sb3` (warp fix + fatter
clicks). Please listen to all three (same 1-5 key controls, same 5 test
frequencies in each) and report:

1. **v1 vs v2, same click sound**: does v2's gap between pops sound
   noticeably tighter/faster than v1's, at each of the 5 frequencies?
   This isolates whether the warp-mode fix actually addresses the timing
   complaint. If v2 still sounds gappy, the yield-per-iteration hypothesis
   is wrong (or incomplete) and needs more investigation before any
   further audio work.
2. **v2 vs v3, both warp-fixed**: does v3's fatter click sound fuller/more
   like a real "pop," especially at 2000/4000Hz where the click was only
   4 samples in v1/v2? Does it come at a noticeable cost — more
   continuous/less distinctly "clicky," or any new choppiness?
3. **Overall**: after both fixes (v3), does the whole technique now sound
   like a viable building block for a real APU (clean steady pitches, no
   noticeable per-note gap tax), or is there still a fundamental problem
   worth flagging before Phase 9 work begins?
