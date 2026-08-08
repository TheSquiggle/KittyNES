# APU architecture: findings from a working reference emulator

Studied a third-party, complete NES emulator for TurboWarp (16,284 blocks) that
has **working audio**, to settle the question our own click-train prototypes
(v1–v4) could not. The reference file itself is local-only and gitignored — only
the technique is recorded here, no code or assets were copied.

**Headline: the click-train approach is the wrong architecture, and this
explains why v1–v4 all sounded too slow.** The reference does not re-trigger a
sound per wave cycle at all. It plays *one long continuous sample* and changes
its **pitch**.

---

## 1. Overall structure — one sprite per channel, sound played by a clone

The reference has a separate sprite per channel, exactly as we'd guessed was
necessary (Scratch plays at most one sound per sprite):

| Sprite | Sounds | Purpose |
|---|---|---|
| `APU Pulse 1` | 4 (`pulse0`–`pulse3`) | square wave, one asset per duty cycle |
| `APU Pulse 2` | 4 | second square channel |
| `APU Triangle` | 1 (`triangle`) | triangle wave |
| `APU Noise` | 32 (`noise{mode}_{period}`) | noise LFSR, pre-rendered |
| `APU DMC Hack` | 2 | sample channel |
| `VRC6 Pulse 1/2`, `VRC6 Saw`, `5B Channel 1–3` | 1–8 each | expansion audio (out of scope for us) |

Crucially the *sprite itself never plays the sound* — it creates a **clone**, and
the clone runs the playback loop (`control_start_as_clone`). Restarting a note is
then just "delete this clone, create a new clone," which gives a clean retrigger
without fighting a `play until done` already in flight.

## 2. The sound assets are LONG sustained waveforms, not clicks

| Asset | Rate | Samples | Duration |
|---|---|---|---|
| `pulse0`–`pulse3` | 48 kHz | 240,000 | **5.0 s** |
| `triangle` | 48 kHz | 480,000 | **10.0 s** |
| `noise*_*` | 48 kHz | 240,000–3,552,000 | 5.0 s – 74.0 s |

Five *seconds* per asset, not five milliseconds. The playback loop is:

```
when I start as a clone:
  Update Frequency
  Update Volume
  forever:
    play sound (join "pulse" <duty cycle for this channel>) until done
```

Because the asset is 5 seconds long, the `play until done` re-trigger happens
roughly **once every 5 seconds** instead of thousands of times per second. The
browser audio-engine startup latency that killed our click-train — the fixed
per-play cost we hypothesised in `audio_click_train_approach.md` and could not
eliminate with warp mode — is simply amortised away to inaudibility. That
hypothesis was correct; the fix was to stop re-triggering, not to re-trigger
faster.

## 3. Frequency comes from the PITCH effect, not from asset length

```
Update Frequency:
  set [pitch] effect to ( ln( APU_Frequencies[ch] / 440.3968996062992 )
                          / ln(2) ) * 120
```

That is `120 × log2(target_freq / 440.3969)`. Scratch's pitch effect is 10 units
per semitone → 120 units per octave, so this is exactly "how many octaves above
the sample's own base pitch, scaled to Scratch's units."

The constant **440.3968996062992 Hz is the base frequency the pulse assets were
rendered at** — pitch effect 0 reproduces that frequency. Any equivalent
generated asset must either match that base or substitute its own constant.

Note `ln(x)/ln(2)` is used because Scratch's `[ ] of ( )` operator offers `ln`
and `log` (base 10) but no base-2 log.

## 4. Volume

```
Update Volume:
  set volume to  APU_Mixed_Volumes[ch]
                 × (NES_Output_Volume × 5)
                 × ([not paused] of NES)
```

Per-channel mixed volume × a global output-volume control × a pause gate (so
pausing the emulator mutes it without tearing down the clones).

## 5. Noise is pre-rendered per (mode, period), not pitch-shifted

32 assets named `noise{mode}_{period}`: LFSR modes 0 and 1 × the NES noise
channel's 16 hardware period values. Noise cannot be meaningfully pitch-shifted
(shifting changes the perceived character, not just the rate), so each hardware
setting gets its own pre-rendered asset. Longer periods need longer assets (up
to 74 s) so the loop point stays inaudible.

## 6. Coordination is by broadcast

The CPU/APU-register side never touches sound blocks directly. It broadcasts:

- `update frequency for channel N`
- `update volume for channel N`
- `restart channel N`  → delete-clone / create-clone (a note retrigger)
- `start APU`
- `delete NES audio clones`

Each channel sprite has a `when I receive` hat guarded by
`if <channel number = N>`, so one broadcast can be shared across sprites that
distinguish themselves by a `channel number` variable. Shared state travels
through global lists indexed by channel: `APU Frequencies`,
`APU Mixed Volumes`, `APU Pulse Duty Cycles`.

## 7. A preload trick worth copying

On green flag the sprite does:

```
set [pitch] effect to -1000
set volume to 0
play sound pulse0        (all four, in sequence, non-blocking)
play sound pulse1
play sound pulse2
play sound pulse3
stop all sounds
```

Silent, inaudible, and instantaneous — but it forces the browser to fetch and
**decode every audio asset up front**, so the first real note doesn't stall
while a 5-second 48 kHz WAV is decoded. Worth replicating verbatim.

---

## What this means for KittyNES

Our Phase 9 plan should be rewritten around this architecture:

1. Generate 4 pulse-duty waveform assets + 1 triangle + 32 noise assets in
   Python at build time (multi-second, 48 kHz, looping cleanly at the seam).
2. One sprite per channel; a clone per sprite runs
   `forever: play sound <asset> until done`.
3. Map `$4000`–`$4013` register writes → per-channel entries in
   `APU Frequencies` / `APU Mixed Volumes` / `APU Pulse Duty Cycles` lists,
   then broadcast the corresponding update.
4. Frequency via the pitch-effect formula above; volume via `set volume`.
5. Copy the silent-preload trick on startup.

The click-train prototypes (`progress/audio_prototype*.sb3`) should be retained
as a documented negative result — the technique is sound in principle for very
low frequencies but is capped by per-play audio-engine latency, and this
reference shows the standard way around it.

Sweep, envelope, and length-counter behaviour still have to be emulated on the
CPU side and folded into the frequency/volume values written to those lists;
the reference's structure supports that but this document does not cover its
frame-sequencer implementation.
