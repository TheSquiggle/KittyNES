# Real-ROM testing notes

First test of the built emulator against a real, non-synthetic NES ROM,
using [NEStress.NES](https://github.com/christopherpow/nes-test-roms) — a
well-known freeware/public-domain test ROM designed specifically to
exercise CPU, PPU (background + sprites), and controller-input behavior in
one cartridge. Located at `D:\KittyNES\test_roms\NEStress.NES`.

## Tooling

- `code/build_final.py path\to\rom.nes [out.sb3]` — the reusable driver
  that assembles all 8 phases and bakes a given ROM in via
  `ines_loader.load_rom_into_emu`. This is what a user runs to get a
  playable `.sb3` for a specific game.
- `code/run_nestress_smoke.py [steps]` — builds the same way but instead of
  saving, drives the build through `interp.py` for a bounded step count and
  reports final CPU/PPU state (PC/registers/SCANLINE/FRAME/NMI_PENDING,
  framebuffer non-transparent pixel count). This is a sanity check that a
  real ROM *executes sensibly* through the real generated block graph, not
  just that the build succeeds structurally.

Both scripts use `ines_loader.load_rom_into_emu` — no special-casing for
this or any other ROM; the loader doesn't know or care which game it's
loading.

## `interp.py` additions needed for real-ROM testing

`interp.py` had no `sensing_keypressed`/`sensing_mousedown`/`sensing_timer`
support before this — it's a headless test harness and had never needed to
evaluate input-sensing blocks in any synthetic test (the synthetic tests
all drove state directly rather than going through the real controller-
polling code path). Real ROMs do read input (even if only to detect "no
input" and proceed), so `ctrl_poll`'s `sensing_keypressed` calls needed
*something* to evaluate to. Added a minimal `self.keys` dict on `Interp`
(defaults to nothing pressed) plus straightforward stub returns for
`sensing_mousedown`/`sensing_timer`. This is a permanent, reasonable
addition to the test harness, not a workaround for a real project bug.

## Bug found and fixed: PC silently became a float

The first real-ROM run (300k steps) came back with `PC = 32799.0` — a
Python `float`, not an `int`. Traced and fixed; full writeup in the
`interp.py` fix commit and `docs/main_loop.md`'s cross-reference, short
version: `interp.py`'s `operator_mod` used `math.fmod`, which always
returns a `float` in Python even for exact-integer inputs, and since PC
advances via `MOD(ADD(PC,1),65536)` on essentially every CPU step, PC
silently became (and permanently stayed) a Python float after the very
first instruction. **This was a test-harness fidelity gap, not a bug in
the generated Scratch project** — real Scratch/JS numbers are all
IEEE-754 doubles with no int/float type distinction to begin with, so
`32799` and `32799.0` are the literal same value on the real VM. But it
was worth fixing for real: an un-normalized float creeping through the
harness could mask genuine fractional-value bugs by making "is this
exactly representable as an int" impossible to check. Fixed by
normalizing whole-valued floats back to `int` at every arithmetic
operator's return point; added a regression check to `test_main_loop.py`.

## What the smoke test shows

Running `run_nestress_smoke.py` for increasing step budgets:

| Steps | Elapsed | Final SCANLINE | Final FRAME | FB non-transparent pixels |
|---|---|---|---|---|
| 300,000 | <1s | 46 (frame 0) | 0 | 0 / 61,440 |
| 1,000,000 | ~1.1s | 155 (frame 0) | 0 | 0 / 61,440 |
| 50,000,000 | ~41s | 63 (frame 7) | 7 | **61,440 / 61,440 (fully populated)** |

At 300k-1M steps the game is still early in its own init code (well before
its first vblank/NMI at scanline 241 of frame 0), so an all-transparent
framebuffer at that point is expected/correct, not a bug — confirmed by
checking `P_MASK` at that point (background/sprite-rendering-enable bits
not yet set, since the ROM's own init routine hasn't reached its
`STA $2001` yet).

By 50M steps: **FRAME has advanced to 7** (meaning at least 7 full
262-scanline passes completed, so vblank/NMI fired and got serviced
repeatedly across those frames — `NMI_PENDING` reads back `0`, i.e. not
stuck pending, consistent with the CPU actually entering and returning
from the NMI handler each time rather than the flag just sitting unset),
and **the entire 256x240 framebuffer is non-transparent** — background
and/or sprite rendering is active and produced real pixel data. This is
strong evidence the CPU, bus, mappers, PPU rendering, and main-loop timing
are all cooperating correctly against a real, unmodified commercial-grade
test ROM — not just passing hand-authored synthetic checks.

## What this does and doesn't prove

**Does show:** the emulator's core machinery (CPU/bus/mapper/PPU-render/
main-loop-timing/NMI) runs a real ROM's actual code for millions of
instructions across multiple frames without crashing, getting PC-lost, or
stalling — and that rendering activates and produces framebuffer output
consistent with the ROM's own init sequence enabling it. This is
meaningfully more evidence of correctness than the 100+ synthetic/
hand-authored checks alone, since NEStress's actual instruction stream,
addressing-mode mix, and PPU register sequencing were never seen or
special-cased during development.

**Doesn't show (and can't, in this environment):** whether NEStress's
*specific test patterns* (the actual visual test screens it's designed to
display) render pixel-correctly, since that requires either decoding
NEStress's own pass/fail signaling convention (not attempted here — out of
scope for this pass) or, more practically, loading the built `.sb3` into
real Scratch/TurboWarp and looking at the screen, which needs a real
Scratch runtime this environment doesn't have. `interp.py` proves the block
graph *executes* correctly at the mechanism level (right instructions run,
right timing, right rendering-trigger conditions), not that the visual
output is a byte-perfect match to what real NES hardware would show.

**Audio:** NEStress also exercises the APU, which is explicitly out of
scope for this project (v1 has no APU implementation — sound-related
register writes land as no-ops via the PPU/APU register stub range in
`bus_read`/`bus_write`, same as before this real-ROM test). Not a bug,
a documented v1 scope limitation.

**Input:** confirmed the main loop's `ctrl_poll` (called once per frame,
before `run_frame`, in Phase 8's top-level script) uses real Scratch
`sensing_keyoptions`/`sensing_keypressed` blocks (x/z/a/s/arrow keys mapped
to A/B/Select/Start/D-pad) — this is genuine keyboard-input wiring that
works in an actual Scratch/TurboWarp runtime, not a stub. The stub added to
`interp.py` this session (`self.keys`, defaulting to nothing pressed) exists
purely so the *headless test harness* can evaluate those blocks at all;
real Scratch never needed a stub since it has real keyboard state.

## Second real ROM: "Super Mario Bros. + Duck Hunt (USA)"

A user tried building with their own legally-owned copy of "Super Mario
Bros. + Duck Hunt (USA).nes" and got a permanent grey box. Root cause:
this cartridge uses **mapper 66 (GxROM/MHROM)** — 64K PRG-ROM (4x16K
banks), 16K CHR-ROM (2x8K banks), vertical mirroring — which wasn't one of
the 4 mappers implemented at the time (0/NROM, 1/MMC1, 2/UxROM, 3/CNROM).
With no dispatch branch for mapper 66, `mapper_write` was a silent no-op
and `mapper_read`/`chr_read` never returned real ROM data — hence the grey
box (not a crash, just nothing ever gets drawn). Added mapper 66 support
(full writeup in `docs/mapper_specs.md`), verified with a 16-check
extension to `code/test_mappers.py`, then rebuilt the user's actual ROM:

```
python code/build_final.py "C:\Users\silas\Documents\ROMS\NES\Super Mario Bros. + Duck Hunt (USA).nes" "D:\KittyNES\progress\nes_emulator_smb_duckhunt.sb3"
```

`validate_sb3.py`: structurally clean. `code/ines_loader.parse_ines` on the
real file confirms mapper 66 / mirror 1 (vertical) / 4x16K PRG / 2x8K CHR,
exactly matching the header inspection that identified the missing mapper
in the first place.

### Smoke test: `code/run_smb_smoke.py`

Same approach as `run_nestress_smoke.py` (drives the real generated block
graph through `interp.py`, reports state once per frame), adapted to bake
in this specific ROM by path. Results at 60M steps (~49s):

| Frame | Steps (cum.) | PPUCTRL | PPUMASK | Distinct PCs visited | FB non-transparent pixels |
|---|---|---|---|---|---|
| 1-3 | ~2.7M-8M | 0x10 | 0x00 | 2 | 0 |
| 4 | ~10.7M | 0x80 | 0x00 | 4 | 0 |
| 5-9 | ~13M-24M | varies | 0x00 | 13 → 63 | 0 |
| 10 | ~26.6M | 0xff | 0x18 | 66 | 0 |
| 11 | ~37.5M | 0x7e | 0x1e | 79 | **61,440 (fully populated)** |
| 12-13 | ~48M-59M | 0x7f | 0x1e | 88 → 93 | 61,440 (stays fully populated) |

Reading this: frames 1-3 show only 2 distinct PC values — the CPU is in a
classic NES reset routine's "wait for two vblanks" polling loop (extremely
common pattern, e.g. `BIT $2002 / BPL loop`), not stuck/broken. Starting
frame 4, PPUCTRL changes and the distinct-PC count climbs steadily (13, 24,
54, 63...) as the reset code finishes and real game init/setup code starts
executing a much wider range of addresses. By frame 10, PPUMASK gets its
rendering-enable bits set (0x18 = background + sprites on), and by frame 11
the entire 256x240 framebuffer is populated with real rendered pixel data
and **stays** fully populated through frame 13 — i.e. this isn't a one-off
render, it's stable frame-over-frame rendering, consistent with the game
having reached its title-screen/attract-mode render loop.

**Also notable**: `PRGB0`/`PRGB1` were observed changing over the course of
the run (e.g. `(2,3)` at 3M steps, `(0,1)` by 60M steps) — direct evidence
the game is actively writing to the GxROM bank-select register during
normal execution, not just once at boot. This particular cartridge is a
two-game combo pack (Super Mario Bros. + Duck Hunt share one board), so
PRG bank-switching between the two games' code is exactly the kind of
behavior this ROM's mapper usage should exhibit — seeing it happen
organically, driven by the game's own code rather than a synthetic test
writing the register directly, is a meaningful independent confirmation
that mapper 66 works correctly end-to-end.

**Rough timing expectation for the real Scratch/TurboWarp build**: 60M
Python interpreter steps (a rough proxy for block-graph traversal work, not
directly convertible to real Scratch execution speed — TurboWarp's
compiled runtime is dramatically faster per-block than this from-scratch
Python walker) took ~49 seconds and covered about 13 frames' worth of the
game's own boot sequence up through a stable rendered screen. This suggests
the real build should reach a visible, rendering title screen within a
comparable number of frames once loaded in an actual Scratch/TurboWarp
project — how many real wall-clock seconds that translates to depends
entirely on TurboWarp's block-execution throughput, which cannot be
measured from this environment.

**Not verified** (same caveats as the NEStress test): pixel-perfect visual
correctness against real hardware, audio (APU out of scope), and actual
gameplay/input response — all would need a real Scratch/TurboWarp runtime
to confirm. What IS verified: the CPU executes a real, unmodified
commercial ROM's actual boot sequence for tens of millions of instructions
without crashing or losing track of PC, the previously-missing mapper now
works (confirmed both synthetically and by the real game's own bank-switch
writes taking effect), and rendering activates and stabilizes exactly the
way a working NES boot sequence should.
