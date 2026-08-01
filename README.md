# KittyNES — NES Emulator in Vanilla Scratch 3.0

Goal: a full NES (Nintendo Entertainment System) emulator built as a real, loadable
`.sb3` Scratch 3.0 project — pure block logic, no TurboWarp JS extension.

This directory is the durable home for all progress reports, generated code,
research notes, and intermediate/final build artifacts for the project. It is
updated continuously as the background build agent works through phases.

## Directory layout

- `code/` — Python generation scripts (the actual generator source of truth) and
  the `sb3_builder.py` library used to construct `.sb3` files programmatically.
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
- **Phase 2 (memory bus): DONE, validated**
- **Phase 3 (6502 CPU core): IN PROGRESS**
- Phases 4–8 (CPU verification, mappers, PPU, cartridge loader, main loop): not started

## Build system

Everything is generated programmatically from Python — never hand-edit the `.sb3`
JSON directly. See `code/sb3_builder.py` for the block-graph construction API and
`code/gen_phase1_2.py` for the first working generator script (bit-op tables +
memory bus). Later phases extend this same approach: a Python-side opcode/data
table drives programmatic generation of the (very repetitive) Scratch blocks,
rather than hand-writing each one.

Validate any `.sb3` output with the skill's `validate_sb3.py` structural checker
before treating it as good.
