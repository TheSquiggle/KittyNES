# KittyNES — NES Emulator in Vanilla Scratch 3.0

A full NES (Nintendo Entertainment System) emulator built as a real, loadable
`.sb3` Scratch 3.0 project — pure block logic, no TurboWarp JS extension.

**v1 status: all 8 planned phases done and verified**, plus real-ROM smoke
tests against `NEStress.NES` (a well-known CPU/PPU/input test ROM — 50M+
interpreter steps across 7+ full frames, framebuffer fully populated,
NMI/vblank firing and serviced correctly) and a real user-owned copy of
"Super Mario Bros. + Duck Hunt (USA)" (mapper 66/GxROM — 60M+ steps, stable
fully-rendered framebuffer by frame 11, live in-game mapper bank-switching
observed) — see `docs/real_rom_testing.md`. Definitive artifact:
[`progress/nes_emulator.sb3`](progress/nes_emulator.sb3). To build a `.sb3`
for a specific ROM: `python code/build_final.py path\to\game.nes`.

## Directory layout

- `code/` — Python generation scripts (the actual generator source of truth) and
  the `lib.py` library (Emu wrapper over the raw sb3_builder primitives) used to
  construct `.sb3` files programmatically. Canonical generator: `code/build_core.py`
  (all 8 phases' block-graph generation) driven by `code/gen_build.py`, plus
  `code/tables6502.py` (opcode table) and `code/ines_loader.py` (cartridge
  loader, Phase 7). Every phase has a matching `code/test_*.py` verification
  suite run via `code/interp.py` (a from-scratch Python re-implementation of
  the subset of Scratch VM behavior this project emits — walks the *actual*
  generated block graph, not a re-derivation of the logic; it caught every
  real bug found during this build, see `progress/PROGRESS_LOG.md`).
  `code/gen_full.py`/`code/gen_phase1_2.py` are an earlier, simpler,
  self-contained generator (phases 1-4 only, no mapper/PPU scaffolding) kept
  for reference but superseded by `build_core.py`.
- `progress/` — intermediate per-milestone `.sb3` checkpoints, the final
  `nes_emulator.sb3`, and `PROGRESS_LOG.md` (detailed phase-by-phase log,
  including every bug found and fixed along the way).
- `docs/` — design docs: `NES_ARCHITECTURE_AND_EMULATION.md` and
  `6502_opcode_table.md` (reference material), `mapper_specs.md`,
  `nes_ppu_notes.md`, `cartridge_loader.md`, `main_loop.md` (per-phase design
  writeups with the "what's NOT implemented / known limitations" for each),
  and `real_rom_testing.md` (findings from smoke-testing against a real ROM).
- `test_roms/` — `NEStress.NES`, a well-known freeware/public-domain CPU/PPU/
  input test ROM used for real-ROM smoke testing (see
  `docs/real_rom_testing.md`). No commercial ROMs are used anywhere in this
  project.
- `research/` — Scratch-specific workaround research (currently empty; the
  workarounds that emerged — lookup tables for bitwise ops, flat 1-indexed
  lists for 2D data, `RESULT`-style globals for return values, disjoint
  scratch-temp-variable namespaces per call-chain "layer" — are documented
  inline in `docs/` and `PROGRESS_LOG.md` instead).

## Status by phase

See [`progress/PROGRESS_LOG.md`](progress/PROGRESS_LOG.md) for full detail,
bug-by-bug. Summary:

| Phase | What | Status |
|---|---|---|
| 1 | Bitwise-op lookup tables (AND/OR/XOR/shifts/rotates via precomputed lists) | DONE |
| 2 | Memory bus, PPU register plumbing ($2000-$2007), mapper dispatch scaffolding | DONE |
| 3 | 6502 CPU core: all 151 official opcodes, 13 addressing modes, 7 flags, reset/NMI/IRQ | DONE, verified (36 checks) |
| 4 | CPU correctness verification (hand-authored suite, real block graph) | DONE |
| 5 | Mappers: NROM, UxROM, CNROM, MMC1 (5-write serial-shift protocol), GxROM/66, **MMC3/4 (incl. scanline IRQ)** — all on a shared fine-grained bank-window model (four 8K PRG windows, eight 1K CHR windows) | DONE, verified (mapper suite + 63-check MMC3 suite) |
| 6a | PPU background rendering (pattern-table decode, attribute palettes, framebuffer, Pen flush) | DONE, verified (11 checks) |
| 6b | Sprites (OAM, priority, sprite-0-hit, overflow) + loopy scroll registers, **including fine-X sub-tile scrolling** | DONE, verified (25+22 checks across 2 files) |
| 7 | Cartridge (`.nes`) loader — iNES 1.0 **and NES 2.0** headers, incl. four-screen mirroring | DONE, verified (48 checks) |
| 8 | Main loop: scanline-granularity CPU/PPU timing, vblank/NMI, Pen flush | DONE, verified (15 checks) |
| 9 (audio) | APU — NOT built yet. A "click-train" pitch approximation technique is being prototyped/iterated (v1→v2→v3→v4) in `progress/audio_prototype*.sb3`, standalone and unintegrated. v2/v3's warp-mode timing fix is confirmed correctly applied but the gap persists — currently investigating a possible audio-engine startup-latency floor (see below) | PROTOTYPE, unverified, possible hard limitation under investigation |

Every phase's generator code, design rationale, and test results are written
up in detail in `progress/PROGRESS_LOG.md` and the relevant `docs/*.md` file.

**Open issue — real-ROM sprites/tiles (SMB+Duck Hunt) show color
corruption** (correct shape, wrong/speckled/flat-black color — a real
screenshot showed a garbled brick pyramid texture and a Goomba rendering
as a flat black block). Four rounds of exhaustive, targeted testing —
positional/scrolling, tile-identity/CHR-bank-switching, palette/attribute/
color resolution (all 4 quadrants, all 4 sprite palettes, palette RAM
mirroring, the master color table), and finally a brand-new capability to
actually verify the Pen-flush drawing algorithm itself (previously
untestable) — **all came back clean; the bug has not been reproduced or
found.** Leading remaining hypothesis: real Scratch/TurboWarp's Pen
rendering fidelity (antialiasing on short line strokes), the one part of
the pipeline that cannot be verified without a real browser — unconfirmed.
See `progress/PROGRESS_LOG.md`'s most recent entries and
`docs/nes_ppu_notes.md` for the full investigation.

**Open issue — audio click-train still not fast enough after the warp
fix:** the warp-mode mutation was verified correct in the raw generated
JSON (not a serialization bug); the leading remaining hypothesis is a
fixed per-cycle audio-engine startup-latency floor that no Scratch-level
fix can eliminate. `progress/audio_prototype_v4_bassfloor.sb3` tests a
falsifiable prediction of that hypothesis (low frequencies should sound
comparatively cleaner if it's right) — awaiting listening-test feedback.
See `research/audio_click_train_approach.md` for full reasoning and
proposed alternatives if the limitation is confirmed real.

## What v1 is and isn't

**Structurally verified**, every phase, via `interp.py` walking the real
generated block graph (not a re-derivation — the actual `.sb3` logic):
full 6502 CPU, NROM/UxROM/CNROM/MMC1/GxROM(66)/MMC3(4) mapper support, PPU background+sprite
rendering with priority/sprite-0-hit and coarse (8px-granularity) scrolling,
a real iNES 1.0 / NES 2.0 header parser + loader (including four-screen
mirroring with its full 4KB of nametable VRAM), and a scanline-driven main loop with
correct vblank/NMI timing.

**Not verified, and not achievable in this environment:** booting and
playing an actual commercial game inside real Scratch/TurboWarp.
`interp.py` is a legitimate, load-bearing verification tool — but it is a
from-scratch Python re-implementation of the opcode subset this project
emits, not the real Scratch VM, and no real `.nes` ROM was used anywhere in
this build (by design — see `docs/cartridge_loader.md`). Loading
`progress/nes_emulator.sb3` into actual Scratch/TurboWarp with a real,
legally-obtained ROM and confirming it renders/plays is the one verification
step this project cannot do for you.

**Explicitly out of scope for v1** (see each phase's `docs/*.md` for
details):
- Fine-X sub-tile pixel-level horizontal scrolling (coarse 8px scrolling
  works; real hardware's 16-bit background shift-register pixel blending
  across tile boundaries doesn't yet).
- Per-dot cycle-accurate PPU/CPU interleaving — timing is scanline-
  granularity, so mid-scanline raster-split effects (specific to certain
  games) won't render correctly, though between-scanline effects work fine.
- The real 2C02's specific *buggy* sprite-overflow evaluation algorithm
  (a simpler, always-correct overflow flag is implemented instead).
- MMC3's IRQ timing is scanline-granularity, not PPU-A12-edge exact: the
  counter is clocked once per rendered scanline. Between-scanline effects
  (status bars, split screens) work; a raster split landing *mid*-scanline
  will not. See `docs/mapper_specs.md`.
- MMC3 PRG-RAM write protection ($A001) is stored but not enforced, and MMC6
  / MMC3 submapper variants are not implemented.
- APU (audio) — not part of any phase in this project's plan.

## Build system

Everything is generated programmatically from Python — never hand-edit the
`.sb3` JSON directly. `code/lib.py` provides the `Emu` wrapper (variables,
lists, custom-block/procedure definitions, control-flow sugar, binary-search
opcode dispatch) over the raw `sb3_builder.py` primitives (from the
`scratch-sb3` skill). `code/build_core.py` is organized as one function per
phase (`phase1_tables`, `phase2_bus`, `phase3_cpu`, `phase6_ppu_bg`,
`phase6b_sprites`, `phase8_main_loop`) plus `code/ines_loader.py` for Phase
7, all driven by `code/gen_build.py` to produce
`progress/nes_emulator_wip_phase3_full.sb3` (the working build) —
`progress/nes_emulator.sb3` is a copy of that file taken at the v1
milestone.

To rebuild: `python code/gen_build.py`. To reverify: run each
`code/test_*.py` script (they all exit non-zero on any failing check and
print a PASS/FAIL line per check). Always validate any `.sb3` output with
the `scratch-sb3` skill's `validate_sb3.py` structural checker before
treating it as good — this project's workflow ran it after every phase.
