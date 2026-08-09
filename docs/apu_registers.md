# APU register reference and the CPU→channel mapping

Everything below was **verified against nesdev.org** while writing this, not
recalled from memory — the mapper-66 bug in this project came from a
confidently mis-stated spec, so register layouts here are quoted from source.

This document is the bridge between the CPU side (`$4000`–`$4017` writes, which
land in `build_core.py`'s `bus_write`) and the channel sprites built by
`code/apu_build.py`. Implementing the integration should be mechanical from
here.

---

## The shared state the channel sprites read

Created once on the Stage by `apu_build.build_apu()`:

| Global | Shape | Meaning |
|---|---|---|
| `APU_FREQ[ch]` | 4 entries | target frequency in Hz (0 ⇒ silent) |
| `APU_VOL[ch]` | 4 entries | 0–100 channel volume |
| `APU_DUTY[ch]` | 2 entries | pulse duty index 0–3 (pulse channels only) |
| `APU_NOISEIDX` | scalar | which of the 32 noise assets (1-based) |
| `APU_NOISENAMES` | 32 entries | asset names, `noise{mode}_{period}` |

Channel indices: `1` = Pulse 1, `2` = Pulse 2, `3` = Triangle, `4` = Noise.

After changing `APU_FREQ`/`APU_VOL`, broadcast `apu_update_<ch>` — it applies
pitch/volume **without interrupting playback**. After changing which *asset*
should sound (`APU_DUTY`, `APU_NOISEIDX`), broadcast `apu_restart_<ch>`, which
respawns the clone (unavoidable: `play sound until done` cannot be interrupted
mid-sample).

`fCPU` = **1789773 Hz** (NTSC) throughout.

---

## Pulse 1 (`$4000`–`$4003`) and Pulse 2 (`$4004`–`$4007`)

```
$4000/$4004   DDlc.vvvv
              DD   duty cycle (0-3)
              l    length counter halt / envelope loop
              c    constant volume flag
              vvvv volume, or envelope divider period
$4002/$4006   LLLL.LLLL   timer low 8 bits
$4003/$4007   llll.lHHH   length counter load (llll.l) + timer high 3 bits
```

Timer `t` = `HHH << 8 | LLLLLLLL` (11 bits).

```
f_pulse = fCPU / (16 * (t + 1))
```

**Silencing rule (important):** `t < 8` silences the channel. Do not compute a
frequency in that case — write `APU_FREQ[ch] = 0`. Max output is ~12.4 kHz NTSC.

Duty sequences (our generated assets match these):

| DD | Sequence | Our asset |
|---|---|---|
| 0 | `0 1 0 0 0 0 0 0` — 12.5% | `pulse0` |
| 1 | `0 1 1 0 0 0 0 0` — 25% | `pulse1` |
| 2 | `0 1 1 1 1 0 0 0` — 50% | `pulse2` |
| 3 | `1 0 0 1 1 1 1 1` — 25% negated (i.e. 75% high) | `pulse3` |

Mapping: `APU_DUTY[ch] = DD` then broadcast `apu_restart_<ch>` **only if DD
changed** (a restart is audible, so don't fire it on every `$4000` write).

Volume: with `c = 1`, `vvvv` is the volume directly (0–15). With `c = 0` the
envelope generates it — for a first pass, treating `vvvv` as the volume is a
reasonable approximation; a proper envelope needs the frame sequencer (below).
Scale to Scratch's 0–100: `APU_VOL[ch] = vvvv * 100 / 15`.

## Triangle (`$4008`–`$400B`)

```
$4008   CRRR.RRRR   control flag / length halt (C) + linear counter reload (RRRRRRR)
$400A   LLLL.LLLL   timer low 8 bits
$400B   llll.lHHH   length counter load + timer high 3 bits (sets linear reload flag)
```

```
f_triangle = fCPU / (32 * (t + 1))
```

Note it is `32`, **not** `16` — the triangle sounds an octave below a pulse at
the same timer value. The linear counter must be non-zero for the channel to
sound; writing `$80` to `$4008` (control set, reload 0) effectively silences it.
Very low `t` (0 or 1) produces an ultrasonic pop that some games (Mega Man 1–2)
use deliberately; we can safely mute `t < 2` instead of reproducing that.

Triangle has no volume control on hardware — it is on or off. Set
`APU_VOL[3]` to a fixed level when sounding, 0 when silenced.

## Noise (`$400C`–`$400F`)

```
$400C   --lc.vvvv   length halt, constant volume flag, volume/envelope period
$400E   M---.PPPP   mode flag (M) + period index (PPPP, 0-15)
$400F   llll.l---   length counter load + envelope restart
```

NTSC period table (index `PPPP` → timer value), verified:

```
4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068
```

Noise is **not pitch-shifted** — the LFSR pattern itself differs per period, so
each `(mode, period)` pair has its own pre-rendered asset. Select it:

```
APU_NOISEIDX = M * 16 + PPPP + 1        (1-based index into APU_NOISENAMES)
```

then broadcast `apu_restart_4` if it changed. Volume as for pulse.

LFSR (matches `code/audio_assets.py`): 15-bit, initialised to 1, feedback =
`bit0 XOR bit1` when M = 0 and `bit0 XOR bit6` when M = 1. Sequence length is
32767 steps for mode 0 and **93** steps for mode 1 from the standard init value
— which is why short mode sounds tonal.

## DMC (`$4010`–`$4013`) — out of scope

Sample playback. Not implemented; accept the writes so games don't hang.

## Status / enable (`$4015`)

```
write: ---D.NT21   enable DMC / Noise / Triangle / Pulse2 / Pulse1
read:  IF-D.NT21   frame IRQ, DMC IRQ, and channel length-counter status
```

Clearing a channel's enable bit zeroes its length counter and silences it →
set that channel's `APU_VOL` to 0 and broadcast its update. Reads should report
which channels still have a non-zero length counter; returning 0 is a safe stub
until length counters exist.

## Frame counter (`$4017`)

```
MI--.----   mode (0 = 4-step, 1 = 5-step), IRQ inhibit
```

Drives the ~240 Hz frame sequencer that clocks envelopes, sweeps, and length
counters. Not needed for a first pass that treats `vvvv` as a static volume,
but required for correct decay/sustain behaviour and for the frame IRQ.

---

## Suggested integration order

1. Route `$4000`–`$4017` writes in `bus_write` to a new `apu_write` proc
   (they currently fall into the APU/IO stub range).
2. Pulse 1 + 2 only: timer → `APU_FREQ`, `vvvv` → `APU_VOL`, `DD` →
   `APU_DUTY`, honouring the `t < 8` silence rule. Broadcast update on every
   write, restart only on a duty change.
3. Triangle (remember `32 *`, and the linear-counter silence case).
4. Noise via the period table and `APU_NOISEIDX`.
5. `$4015` enable/disable → force volume 0.
6. Only then consider the frame sequencer for envelopes/sweeps/length counters.

Steps 2–5 are the ones that make games recognisably audible; step 6 is polish.
