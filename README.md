# KittyNES — NES Emulator in Vanilla Scratch 3.0

Goal: a full NES (Nintendo Entertainment System) emulator built as a real, loadable
`.sb3` Scratch 3.0 project — pure block logic, no TurboWarp JS extension.

This directory is the durable home for all progress reports, generated code,
research notes, and intermediate/final build artifacts for the project. It is
updated continuously as the background build agent works through phases.

## Directory layout

- `code/` — Python generation scripts (the actual generator source of truth) and
  the `lib.py` library (Emu wrapper over the raw sb3_builder primitives) used to
  construct `.sb3` files programmatically. Current canonical generator is
  `code/build_core.py` (driven by `code/gen_build.py`) + `code/tables6502.py`
  (opcode table) + `code/test_cpu.py` (Phase 4 correctness suite, run via
  `code/interp.py`, a Python re-implementation of the Scratch VM that walks the
  real generated block graph). `code/gen_full.py` and `code/gen_phase1_2.py` are
  an earlier, simpler, self-contained generator (phases 1-4, no mapper/PPU
  register scaffolding) kept for reference/checkpoints but superseded by
  `build_core.py` going forward.
- `progress/` — intermediate and final `.sb3` build artifacts, one per validated
  milestone, plus `PROGRESS_LOG.md` with a running phase-by-phase log.
- `research/` — notes on the 6502 ISA, NES memory map, PPU behavior, mapper specs,
  and any Scratch-specific workaround research (bitwise ops, 2D-array emulation,
  function-return-value emulation, etc.).
- `docs/` — design docs: architecture decisions, data layouts (list schemas),
  opcode tables, known limitations.

## Status

See [`progress/PROGRESS_LOG.md`](progress/PROGRESS_LOG.md) for the current
phase-by-phase status. As of the last update:

- **Phase 1 (bitwise-op lookup tables): DONE, validated**
- **Phase 2 (memory bus + PPU register stubs + mapper dispatch scaffolding): DONE, validated**
- **Phase 3 (6502 CPU core, all 151 official opcodes/13 addressing modes/7 flags/NMI+IRQ+reset): DONE, validated**
- **Phase 4 (CPU correctness verification): DONE — 36-check hand-authored test suite
  (all addressing modes, ADC/SBC w/ signed-overflow, CMP/CPX/CPY, shifts/rotates,
  stack, JSR/RTS, BIT, branches) passes 100% against the actual generated block
  graph via `code/interp.py`.**
- **Phase 5 (mappers: NROM/UxROM/CNROM/MMC1): DONE, verified** — dedicated
  `code/test_mappers.py` suite (same interp.py-against-real-block-graph
  approach as the CPU suite) passes for all 4 mappers, including MMC1's
  5-write serial-shift protocol and bit7-reset case. Found and fixed a real
  bug in CNROM bank selection along the way (see `docs/mapper_specs.md`).
- **Phase 6a (PPU background rendering): DONE, verified** — pattern-table tile
  decode (lookup-table bitplane extraction), attribute-table palette-group
  resolution, and a `render_bg_frame` proc filling a 256x240 `FB` framebuffer
  list, plus a batched (per-run, not per-pixel) `flush_fb_to_pen` Pen output.
  11-check verification suite in `code/test_ppu_bg.py`, full-frame stress test
  (960 tiles, 61,440 pixels) completes cleanly. See `docs/nes_ppu_notes.md`.
- **Phase 6b (sprites + scrolling): DONE, verified** — OAM sprite evaluation
  (<=8/scanline, overflow flag), 8x8/8x16 tile decode with flip support,
  background compositing with correct priority-bit and sprite-0-hit
  behavior, and the loopy v/t/x/w coarse-scroll register increment/copy
  logic (PPUSCROLL/PPUADDR write-twice-latch semantics were already done in
  Phase 2). 22-check verification suite in `code/test_ppu_sprites.py`, all
  pass. See `docs/nes_ppu_notes.md`. **Not yet done:** fine-X sub-tile pixel
  scroll (coarse 8px scrolling works), PPUMASK bits, per-scanline
  cycle-accurate timing (Phase 8 territory).
- **Phase 7 (cartridge/.nes loader): DONE, verified** — `code/ines_loader.py`
  parses a real iNES 1.0 header + PRG/CHR data and bakes it into an `Emu`
  build. Tested against a synthetic in-memory `.nes` file (no real ROM
  used/needed to build this); real-ROM testing is up to the user once they
  supply one — see `docs/cartridge_loader.md`. 22-check verification suite,
  all pass.
- Phase 8 (main loop, CPU/PPU timing, NMI-on-vblank, framebuffer flush): not started.

See `progress/PROGRESS_LOG.md` for the detailed bug-fix history from this
session (several real correctness bugs were found and fixed while getting
Phase 3/4 to actually pass: forward-referenced custom-block calls, global
scratch-temp-variable collisions between CPU and bus code, and a lazy-list
initialization-order bug that silently zeroed out the `BOOL`/`PRGRAM` lists).

## Build system

Everything is generated programmatically from Python — never hand-edit the `.sb3`
JSON directly. See `code/sb3_builder.py` for the block-graph construction API and
`code/gen_phase1_2.py` for the first working generator script (bit-op tables +
memory bus). Later phases extend this same approach: a Python-side opcode/data
table drives programmatic generation of the (very repetitive) Scratch blocks,
rather than hand-writing each one.

Validate any `.sb3` output with the skill's `validate_sb3.py` structural checker
before treating it as good.
