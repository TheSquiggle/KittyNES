# Main loop notes (KittyNES, Phase 8)

New in `code/build_core.py`'s `phase8_main_loop(e)`.

## Timing model: per-scanline, not per-dot

The real 2C02 PPU advances one "dot" (pixel-clock cycle) at a time, 341 dots
per scanline, 3 PPU dots per CPU cycle. A fully cycle-accurate emulator
interleaves CPU instruction execution with PPU dot advancement at that
granularity, because games rely on precise mid-scanline register writes for
effects like split-scroll and raster timing tricks tied to specific PPU
dots.

KittyNES's main loop instead runs at **scanline granularity**: `run_scanline`
executes CPU instructions (via the existing `cpu_step`) accumulating their
real cycle counts into `CPU_TOTAL` until at least `341/3 ≈ 113.667` CPU
cycles have elapsed (the fractional remainder carries over to the next
scanline, so cycle counts stay correct on average across many scanlines even
though no single scanline is dot-exact), then processes "what happens at
this scanline" as one atomic step: renders the scanline's background+sprites
if visible (0-239), sets vblank + fires NMI at 241, and clears
vblank/sprite-0-hit/overflow + re-copies the vertical scroll bits at the
pre-render line (261).

**What this costs:** any game that relies on a *specific mid-scanline CPU
cycle* to change PPU state (raster splits triggered by counting cycles
within a scanline, not just "once per scanline") will not render correctly
-- the state change will be visible either for the whole scanline or not at
all, not partway across it. Games that only change PPU state between
scanlines (the vast majority of "simple" scroll/palette effects, and
anything that isn't a specific raster-trick effect) work fine under this
model. This is a deliberate, documented scope tradeoff for a first working
version, not an oversight -- moving to per-dot granularity would mean
interleaving `cpu_step` with a PPU dot-stepper inside the CPU's own
addressing-mode/opcode execution, which is a much larger restructuring left
for a future pass if mid-scanline accuracy turns out to matter for a
specific game.

## `nes_init`

Power-on reset: calls `cpu_reset` (already existing, sets registers/flags
and fetches PC from the reset vector), zeroes `SCANLINE`/`FRAME`/
`CPU_TOTAL`/`P_STATUS`/`P_CTRL`/`P_MASK`/the loopy `P_V`/`P_T`/`P_X`/`P_W`
registers, and clears `NMI_PENDING`/`IRQ_PENDING`. Must be called once,
after `load_rom_into_emu` (Phase 7) has already populated `PRG`/`CHR` and
the mapper globals -- `cpu_reset` reads the reset vector from `PRG` via the
bus, so the cartridge has to be loaded first.

## `run_scanline`

1. Run CPU instructions, accumulating `CYCLES` into `CPU_TOTAL`, until
   `CPU_TOTAL >= 341/3`.
2. Subtract `341/3` from `CPU_TOTAL` (carries the fractional remainder,
   e.g. if an instruction pushed `CPU_TOTAL` to 115, the next scanline
   starts already "owing" `115 - 113.667 = 1.333` cycles less work).
3. If `SCANLINE < 240` (visible): render that scanline's background
   (`render_bg_line_scrolled`, gated on PPUMASK bit 3 -- background-rendering
   enable) and sprites (`render_sprites_line`, gated on PPUMASK bit 4 --
   sprite-rendering enable). Both gates were **not** consulted by the Phase
   6a/6b renderers themselves (documented as a gap there); the main loop is
   where that gate is actually applied.
4. If `SCANLINE == 241`: set the vblank flag (PPUSTATUS bit 7) via `BOR`;
   if PPUCTRL bit 7 (NMI-on-vblank enable) is set, set `NMI_PENDING = 1`
   (the CPU's existing `cpu_step` already checks/dispatches
   `NMI_PENDING` at the start of the next instruction it executes -- this
   plumbing was built in Phase 3 and just needed something to actually set
   the flag, which is what Phase 8 adds).
5. If `SCANLINE == 261` (the pre-render line): clear PPUSTATUS bits 5-7
   (overflow/sprite-0-hit/vblank) in one step via `MOD(P_STATUS, 32)`, and
   call `ppu_copy_vert_v` (copies the vertical scroll fields from `P_T`
   into `P_V`, matching the real hardware's pre-render-line vertical-copy
   timing point, so a fresh frame starts scrolled to wherever the game last
   set via PPUSCROLL/PPUADDR).
6. Advance `SCANLINE` (wrapping 262 -> 0); on wrap, increment `FRAME` and
   call `flush_fb_to_pen` (the Phase 6a Pen output, once per frame -- not
   once per scanline, since redrawing the whole framebuffer 240 times/frame
   would be enormously wasteful and Pen drawing doesn't need to happen
   mid-frame for a non-raster-accurate renderer anyway).

## `run_frame` / top-level entry point

`run_frame` is exactly 262 `run_scanline` calls (one full NTSC frame,
including vblank). The top-level `when green flag clicked` script calls
`nes_init` once, then loops `ctrl_poll` (Phase 2's keyboard-to-joypad-shift-
register polling, called once per frame so a held key stays reflected in
`CTRL_STATE` for the game to read via $4016 within that frame) + `run_frame`
until the `RUN` global is cleared -- `RUN` exists so a test harness can stop
the loop deterministically (real usage just never sets it to 0, so the
green-flag script effectively runs forever, which is what Scratch projects
normally do).

## Verification

`code/test_main_loop.py` (15 checks, all pass): builds a synthetic ROM
(via Phase 7's `build_synthetic_nes`) whose reset code writes PPUCTRL=$80
(enable NMI) and PPUMASK=$18 (enable background+sprite rendering), installs
a real NMI handler that increments a RAM counter and `RTI`s, then spins in
a self-`JMP` loop forever. Runs `run_scanline` through `interp.py` (walking
the real generated block graph) enough times to cross into vblank and
checks: `SCANLINE` advances by exactly one per call, the vblank flag sets
at scanline 241, the NMI handler actually executes (RAM counter increments,
not just that `NMI_PENDING` got set), `NMI_PENDING` clears after being
serviced, `FRAME` advances and `SCANLINE` wraps after a full 262-scanline
pass, vblank clears again at the pre-render line, and — as a separate ROM
with PPUCTRL bit 7 left clear — that NMI does NOT fire when disabled while
vblank still sets on schedule regardless (the two are independent, matching
real hardware). Also spot-checked that `FB` gets populated during visible
scanlines.

**What this does NOT verify** (acknowledged limitation, not achievable in
this environment): booting an actual commercial game ROM and confirming it
displays/plays correctly. That needs a real Scratch/TurboWarp runtime
(`interp.py` is a from-scratch Python re-implementation covering the opcode
subset this project emits, not a full Scratch VM) and a real `.nes` file
(none used per project scope — see `docs/cartridge_loader.md`). The
synthetic-ROM test above verifies the main loop's *mechanism* (timing,
vblank, NMI, per-scanline rendering hookup) is wired correctly and produces
the right block-graph behavior; it does not and cannot substitute for
loading `progress/nes_emulator.sb3` into actual Scratch/TurboWarp with a
real ROM and watching it run.
