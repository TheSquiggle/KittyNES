# KittyNES Progress Log

Running phase-by-phase log of the NES-emulator-in-Scratch build. Newest entries at
the bottom. Each phase is validated with `validate_sb3.py` before moving on.

---

## 2026-07-31 — Project kickoff

Task specified: build a full NES emulator as a vanilla Scratch 3.0 (.sb3) project.
Located and loaded the `scratch-sb3` skill (builder library `sb3_builder.py`,
validator `validate_sb3.py`, opcode reference `opcodes.md`).

Confirmed critical Scratch-workaround patterns before starting:
- **No bitwise ops** → precompute AND/OR/XOR as flat 256x256 lookup lists
  (`index = a*256+b`), baked directly as Scratch list data at build time (zero
  runtime "computation" — O(1) list lookup). Shifts/rotates via 256-entry tables
  with paired carry-out tables (old bit 7 for SHL, old bit 0 for SHR). Also
  precompute bit-7 (N flag) and bit-6 (BIT instruction V flag) extraction tables.
- **No 2D arrays** → RAM/VRAM/OAM as flat 1-indexed Scratch lists (address `n` =
  list item `n+1`).
- **No function return values** → route results through global `RESULT`-style
  variables per procedure family (e.g. `bus_read` sets global `RESULT`; caller
  reads it immediately after the call).
- **Argument reporter capture** → capture `argument_reporter_string_number/_boolean`
  block IDs immediately after creating each custom block definition, not by
  scanning for them later.

Build plan: 8 phases (bit-op tables → memory bus → 6502 CPU core → CPU
verification against a functional test ROM → mappers → PPU → cartridge loader →
main loop), validating structurally after each phase.

## 2026-07-31 — Phase 1: Bitwise-op lookup tables — DONE

Generated in Python and baked as Scratch lists on the `CPU` sprite:

| List | Size | Purpose |
|---|---|---|
| `AND_T` | 65,536 | `a & b`, indexed `a*256+b` |
| `OR_T` | 65,536 | `a \| b`, indexed `a*256+b` |
| `XOR_T` | 65,536 | `a ^ b`, indexed `a*256+b` |
| `SHL_T` | 256 | `(a << 1) & 0xFF` |
| `SHL_CARRY_T` | 256 | old bit 7 of `a` (carry out of SHL/ROL) |
| `SHR_T` | 256 | `a >> 1` |
| `SHR_CARRY_T` | 256 | old bit 0 of `a` (carry out of SHR/ROR) |
| `BIT7_T` | 256 | bit 7 of `a` (N flag source) |
| `BIT6_T` | 256 | bit 6 of `a` (BIT instruction V flag source) |

Custom blocks wrapping these as callable ops (write result to global `RESULT`):
`bitop_and %s %s`, `bitop_or %s %s`, `bitop_xor %s %s`, `bitop_shl %s`,
`bitop_shl_carry %s`, `bitop_shr %s`, `bitop_shr_carry %s`, `bitop_bit7 %s`,
`bitop_bit6 %s`.

Validated with `validate_sb3.py`: structural checks pass (zip integrity, JSON
validity, no dangling next/parent refs, asset md5 consistency).

## 2026-07-31 — Phase 2: Memory bus — DONE

Added to the same project:

- `RAM` list, 2048 entries (internal 2KB work RAM)
- `PRG_ROM` list, 32768-entry placeholder (cartridge PRG-ROM, populated for real
  in Phase 7 by the .nes loader)
- Globals: `PRG_ROM_SIZE`, `MAPPER_NUM`, `PRG_BANKS`

Custom blocks:
- `bus_read %s` (addr) → writes `RESULT`:
  - addr < $2000 → RAM mirror, `RAM[(addr mod 2048) + 1]`
  - $2000–$401F → PPU/APU register space — **stub, returns 0** (real PPU/APU
    register wiring lands in Phase 6)
  - addr ≥ $8000 → NROM-style PRG-ROM read with mirroring by `PRG_ROM_SIZE`
    (correct for mapper 0; other mappers override in Phase 5)
- `bus_write %s %s` (addr, value):
  - addr < $2000 → RAM mirror write
  - PPU/APU/mapper-register write branches are present as no-op placeholders,
    intentionally deferred to Phase 5 (mapper registers) and Phase 6 (PPU
    registers) — not silently dropped, just not yet reached in build order.

Validated with `validate_sb3.py`: structural checks pass, no issues.

File at this checkpoint: `progress/nes_emulator_wip_phase2.sb3` (93 blocks on the
`CPU` sprite). Generator source: `code/gen_phase1_2.py`.

## 2026-07-31 — Phase 3: 6502 CPU core — IN PROGRESS

Work started on the full opcode set. Approach: a Python-side data table
(opcode byte → mnemonic, addressing mode, cycle count) drives programmatic
generation of addressing-mode custom blocks (which set global `EFF_ADDR` /
`EFF_VALUE`) and all ~151 official opcodes, rather than hand-writing each one.

Status: build in progress, not yet validated. This log will be updated with the
addressing-mode list, register/flag variable layout, and opcode-table dump once
the generator script is checked in to `code/`.

## 2026-08-01 — Session resume after interruption: real-state assessment

Picked up after a machine shutdown killed the previous session mid-Phase-3.
Found TWO parallel, partially-overlapping generator lineages on disk:

- `code/gen_full.py` (+ `gen_phase1_2.py`): older, self-contained, hand-written
  generator. Runs standalone (imports the skill's canonical `sb3_builder.py`
  directly). Produced the phase2/phase3/phase4 checkpoint `.sb3` files that
  were already committed. Its Phase 2 bus is minimal (RAM only, no PPU
  registers, no mappers). Its Phase 4 is a tiny 64-byte smoke-test program.
- `code/build_core.py` + `code/tables6502.py`: a cleaner, data-driven rewrite
  using an `Emu` helper class (`e.defproc`, `e.dispatch`, binary-search opcode
  dispatch, etc.) that also already includes PPU register read/write
  ($2000-$2007, including correct PPUDATA buffered-read semantics), NROM/UxROM/
  CNROM mapper write dispatch, and a real MMC1 shift-register implementation.
  This was **never successfully run** — it imports `from lib import Emu,
  Reporter`, but no `code/lib.py` existed. The `Emu` class actually lived in
  `code/sb3_builder.py` (a misleadingly-named helper layer, NOT a copy of the
  skill's canonical `sb3_builder.py`).

Decision: continue from `build_core.py`, since it's the more complete/capable
lineage (has the Phase 5/6 scaffolding the other doesn't). Renamed
`code/sb3_builder.py` -> `code/lib.py` (its actual role) to fix the import.

### Bugs found and fixed while getting `build_core.py` to actually build/run

All of these were latent, never-before-executed code — this is real
first-time verification, not a re-check of working code:

1. **Forward-referenced custom-block calls** (three places): `ppu_reg_read`
   called `ppu_incaddr` before it was `defproc`'d; `controller()`'s
   `ctrl_write`/`ctrl_read` called `ctrl_poll` before its definition;
   `cpu_step` called `do_nmi`/`do_irq` before the interrupt-handler `defproc`
   block. `Emu.call()` looks up the target in a dict populated at `defproc`
   time, so calling a not-yet-defined proc raises `KeyError`. Fixed by
   reordering each definition before its first caller.
2. **PPUDATA buffered-read bug** (real logic bug, not just an ordering issue):
   in `ppu_reg_read`'s VRAM branch (address < $3F00), the old buffered value
   was being fetched into `RESULT`, then immediately overwritten by the fresh
   `ppu_read` call before being saved anywhere, then a nonexistent temp (`T2`,
   never set in that branch) was returned instead. Fixed to stash the old
   buffer value in a temp first, then return that after updating the buffer.
3. **Global scratch-temp collision between CPU and bus code (serious,
   would have silently corrupted every ROM-space ABS/ABX/ABY/IND/IZX/IZY fetch
   and every reset/NMI/IRQ vector fetch):** `cpu_reset`/`do_nmi`/`do_irq` and
   all the 16-bit addressing modes stash a fetched low byte in global `T1`,
   then fetch the high byte via another `bus_read` call — but `bus_read`'s
   call chain (`mapper_read`, `chr_read`, `nt_index`, `ppu_read`/`ppu_write`,
   `ppu_reg_read`) *also* used `T1`-`T9` as scratch, clobbering the CPU side's
   saved low byte before it could be combined into the 16-bit address. Fixed
   by renaming every temp used inside the bus/mapper/PPU-register/controller
   family to `U1`-`U9`, a disjoint namespace from the CPU-side `T1`-`T9`, and
   declaring `U1`-`U9` alongside `T1`-`T9` in `declare_state`. This is the
   kind of bug that would have been very hard to catch without instruction-
   level verification against real expected values (Phase 4).
4. **Lazy-list initialization-order bug** (bit us twice): `Emu.lst(name,
   items)` only actually populates the list if `name` isn't already registered
   — but `Emu.IT(name, idx)` (list-item read) calls `self.lst(name)` internally
   with no items, silently pre-registering an *empty* list. Two places
   referenced a list via `IT()` before the real `e.lst(name, [real data])`
   call: the `BOOL` list (`[0, 1]`, used by every `setnz` flag-setting call —
   this meant Z/N flags were silently broken for literally every opcode) and
   `PRGRAM` (used by `mapper_read` for $6000-$7FFF PRG-RAM reads). Fixed by
   moving both real `e.lst(...)` declarations before their first `IT()`
   reference. This class of bug is worth watching for elsewhere.

### Phase 3 (6502 CPU core) — now genuinely DONE

`code/gen_build.py` runs `declare_state` + `phase1_tables` + `phase2_bus` +
`phase3_cpu` from `build_core.py` end-to-end, producing
`progress/nes_emulator_wip_phase3_full.sb3` (1,931 blocks on the `CPU`
sprite). `validate_sb3.py` structural check: clean, no issues.

Implements: all ~151 official 6502 opcodes (illegal opcodes execute as 2-cycle
NOPs) via `tables6502.py`'s `OPCODES` dict, all 13 addressing modes (IMP/ACC/
IMM/ZP/ZPX/ZPY/ABS/ABX/ABY/IND/IZX/IZY/REL, including the 6502's page-wrap bug
on indirect-JMP's high-byte fetch), all 7 status flags (C/Z/I/D/B/V/N) with a
real (not stubbed) decimal-mode ADC/SBC path gated by a `DECMODE` global (NES's
2A03 has decimal mode disabled in hardware, but the flag/opcode still exists —
`DECMODE` defaults unset so behaves like real NES; can be flipped for testing),
page-crossing cycle penalties on indexed absolute/indirect-indexed reads,
branch-taken/page-cross cycle penalties, and reset/NMI/IRQ vectoring including
BRK's software-interrupt push sequence.

Key global variables: `A X Y SP PC` (registers), `FC FZ FI FD FB FV FN` (flags,
each 0/1), `EFF VAL` (effective address / operand set by the addressing-mode
dispatcher), `PAGEX` (page-cross flag), `OPC MODE OPID CYCLES` (per-instruction
decode state), `T1-T9` (CPU-side scratch, safe to clobber across `bus_*`
calls now), `U1-U9` (bus/mapper/PPU-register-side scratch, disjoint namespace).
Key lists: `OPMODE OPID_T OPCYC OPPAGE` (256-entry opcode decode tables),
`BOOL` (`[0,1]`, used to convert boolean reporters to 0/1 for flag storage).

### Phase 4 (CPU correctness verification) — DONE

Klaus Dormann's real `6502_functional_test.bin` needs ~30M executed
instructions to complete; running that through `interp.py` (a Python
re-implementation of the Scratch VM block-graph walker, since no local
Node/scratch-vm environment is available) would take far too long to be
practical here. Instead wrote `code/test_cpu.py` + `code/mini_asm.py` (a tiny
two-pass 6502 assembler): a 36-check hand-authored program covering every
addressing mode, ADC/SBC with carry-in and signed-overflow cases, CMP/CPX/CPY,
AND/ORA/EOR, INC/DEC, register transfers, all 4 shift/rotate ops with carry,
PHA/PLA, PHP/PLP (flag round-trip through the stack), JSR/RTS, BIT (N/V/Z from
memory), and absolute addressing — each check does an immediate CMP-or-
branch-on-condition assertion that jumps to a FAIL trap (writes $FF to RAM $00
and self-loops) on any mismatch; success writes $AA to RAM $00 and self-loops.

Baked into a 32KB NROM-style PRG-ROM image at $8000 with the reset vector
pointed at it, loaded directly into `interp.py`'s list/var state (bypassing
the need for a real .nes/mapper loader, which is Phase 7), then ran
`cpu_reset` followed by `cpu_step` in a loop until RAM $00 became $AA or $FF.

**Result: all 36 checks pass** (`RESULT: ALL 36 CHECKS PASSED`, final PC
0x81E0, 230 CPU steps executed). Along the way, also found and fixed two bugs
in the *test* itself (not the emulator): (a) test checkpoints were originally
implemented as `LDA #n; STA zp` which clobbered the accumulator value under
test — changed to a flag-preserving `PHP; INC zp; PLP` sequence; (b) value
(`CMP`)-based checks were sometimes placed before flag-based (`BCC`/`BMI`/...)
checks for the *same* instruction, and since `CMP` itself sets C/Z/N, it was
clobbering the flags before they could be tested — reordered so flag checks
always run first, value checks last, per instruction under test.

Checkpoint file: `progress/nes_emulator_wip_phase3_full.sb3` (same file as
Phase 3 — Phase 4 doesn't change the built emulator, it's a separate
Python-side verification harness that loads the same block graph).

### Phase 5 (mappers) — partially done already, unverified

`build_core.py`'s `phase2_bus` already implements: NROM (mapper 0, default
linear PRGB0/PRGB1 banking), UxROM (mapper 2, single 16K switchable bank at
$8000, fixed last bank at $C000), CNROM (mapper 3, 8K CHR bank switch), and a
real MMC1 (mapper 1) shift-register/control-register implementation
(`mmc1_apply` recomputes PRG/CHR banking and mirroring from `M1_CTRL`/
`M1_CHR0`/`M1_CHR1`/`M1_PRG` on every 5th shift-register write, matching real
MMC1 behavior including the reset-on-bit7 case). This has NOT been exercised
by a dedicated test yet — flagged as a follow-up before relying on it.

### What's left (not started this session)

- Phase 6: PPU background rendering (nametable/pattern-table/palette lookup ->
  pen framebuffer) and sprite/OAM rendering + scrolling (loopy v/t/x/w — the
  register plumbing for these already exists in `ppu_reg_read`/`ppu_reg_write`,
  just not the per-scanline/per-pixel rendering loop).
- Phase 7: iNES header parser (Python-side, build-time) emitting PRG_ROM/CHR_ROM
  Scratch lists + mapper config from a real `.nes` file.
- Phase 8: main loop tying CPU/PPU/bus together with 3:1 PPU:CPU timing, NMI on
  vblank, framebuffer flush to the stage each frame.
- A dedicated Phase 5 mapper-switching test (bank-switch a UxROM/MMC1 ROM and
  verify the bus reads the newly-selected bank).

## 2026-08-01 — Phase 5 (mapper) verification — DONE

Wrote `code/test_mappers.py`, same approach/rigor as `code/test_cpu.py`: call
the real generated `mapper_read`/`mapper_write`/`mmc1_apply`/`bus_read`/
`bus_write`/`ppu_read` procs via `interp.py` (walks the actual block graph)
with controlled PRG/CHR list contents (each bank filled with its own bank
index as a marker byte) and check bank-selection behavior end-to-end.

Covered: UxROM (mapper 2) bank select + fixed-last-bank window + mod-wrap on
out-of-range bank values; CNROM (mapper 3) 8K CHR bank select; MMC1
(mapper 1) power-on defaults, the 5-write serial-shift protocol for both the
PRG register and CTRL/CHR0/CHR1 registers, 4K-vs-8K CHR mode switching, and
the bit7 shift-register-reset case (including that it forces PRG mode 3).
NROM (mapper 0) is implicitly covered by every CPU test's reset-vector fetch.

**Bug found and fixed:** CNROM's bank-divisor guard used `e.OR(CHRBANKS, 1)`
— `Emu.OR` is Scratch's *logical* `operator_or`, not a numeric default/clamp.
Both operands were always truthy, so the divisor was always coerced to `1`,
meaning `value mod 1 == 0` always — CNROM's CHR bank select was silently a
no-op, always selecting bank 0 regardless of what was written. Fixed using
the same boolean-coerced-to-number idiom `setnz` already relies on:
`CHRBANKS + (CHRBANKS==0 ? 1 : 0)`. Full writeup in `docs/mapper_specs.md`.

Result: **all mapper checks pass** (`ALL MAPPER CHECKS PASSED`). Rebuilt
`progress/nes_emulator_wip_phase3_full.sb3` (1,933 blocks now) with the fix;
`validate_sb3.py` clean. New doc: `docs/mapper_specs.md` (per-mapper register
layout, bit-level protocol details, and the bug found).

## 2026-08-01 — Phase 6a: PPU background rendering — DONE (rendering, no scroll yet)

Added `phase6_ppu_bg(e)` to `code/build_core.py`. New global lists: `FB`
(256*240=61,440 entries, 1-indexed, `FB[y*256+x+1]` = resolved NES palette
index 0-63) and `PIXBIT_T` (256*8=2,048 entries, pattern-table bitplane
bit-extraction table, same lookup-table philosophy as Phase 1's AND/OR/XOR
tables). New procs: `bg_update_patbase`, `bg_setup_tile`, `bg_row_planes`,
`bg_pixel_val`, `render_bg_region` (row0/row1/col0/col1, uses dedicated
`RB_*` globals as loop counters since proc args aren't writable loop vars in
this Emu model), `render_bg_frame` (`render_bg_region(0,30,0,32)`, the full
nametable-0 background), `flush_fb_row`/`flush_fb_to_pen` (Pen output,
batched into one horizontal `pen_setPenColorToColor`+line per same-color
run per row rather than per-pixel stamps).

Full design writeup, including the attribute-table quadrant/palette-group
math and the buffered-read-quirk-preserving `ppu_read` integration, in the
new `docs/nes_ppu_notes.md`.

Verified with new `code/test_ppu_bg.py` (11 checks: solid tiles, a
stripe tile testing per-pixel color-index-1-vs-2 resolution, the
transparent/universal-bg special case, and 2 different attribute quadrants
resolving to different palette groups correctly) -- **all pass**. Also ran
a full 960-tile/61,440-pixel `render_bg_frame` through `interp.py` as a
stress test: completes in ~3.4s / 4.86M interpreted steps, no errors. A
hand-verified 8x8-tile checkerboard pattern (described in
`docs/nes_ppu_notes.md`) confirms the expected alternating-color layout
programmatically (screenshot not possible -- `interp.py` is a headless
Python re-implementation and treats `pen_*` as no-ops by design; the real
visual only exists once loaded into actual Scratch/TurboWarp).

Rebuilt `progress/nes_emulator_wip_phase3_full.sb3`: 2,179 blocks now.
`validate_sb3.py`: clean. Reran `test_cpu.py` (36/36) and `test_mappers.py`
(all pass) to confirm nothing regressed.

Explicitly deferred (see `docs/nes_ppu_notes.md`'s "Not yet implemented"):
scrolling (loopy v/t/x/w register *plumbing* already exists from Phase 2,
just not read by the renderer yet), multi-nametable rendering, PPUMASK
bits, sprites/OAM, and per-scanline cycle-accurate timing (Phase 8
territory).

## 2026-08-01 — Phase 6b: sprites (OAM) + scrolling — DONE

Added `phase6b_sprites(e)` to `code/build_core.py`. New lists/vars: `BGOP`
(256*240, raw pre-palette bg color-index 0-3 per pixel, needed for sprite
priority/sprite-0-hit independent of what the resolved color happens to be),
plus sprite-eval scratch (`SPRLO`/`SPRHI`/`SPRAT`/`SPRID`/`SPRX`/`SPRN`
already existed from Phase 2, now actually used) and three previously-unused
namespaces of scratch temps (`SPR_T1-3`, `SC_T1-4`) kept disjoint from the
CPU (`T1-9`) and bus (`U1-9`) families per the lesson learned in Phase 3/4.

New procs: `spr_update_patbase`, `spr_fetch_planes` (8x8 and 8x16
addressing, vertical flip), `sprite_eval_line` (<=8 sprites/scanline,
overflow flag), `composite_pixel` (priority bit, sprite-0-hit),
`render_sprites_line`/`render_sprites_frame`; and for scrolling:
`ppu_copy_horiz_v`/`ppu_copy_vert_v`, `ppu_scanline_inc_coarse_x`,
`ppu_scanline_inc_y`, `bg_setup_tile_v`, `render_bg_line_scrolled`/
`render_bg_frame_scrolled`. All scroll-register math uses arithmetic
(subtract-old-field/add-new-field), not the 8-bit-operand `BAND`/`BOR`
lookup tables, since `P_V`/`P_T` hold up to 15 bits.

Full design writeup in `docs/nes_ppu_notes.md` (updated): sprite evaluation
semantics, 8x16 addressing, priority/sprite-0-hit logic, and the loopy
register increment algorithms including the coarse-Y-29-vs-31 special case.

Verified with new `code/test_ppu_sprites.py` (22 checks) -- **all pass**:
sprite compositing over transparent background, priority-bit behavior in
both directions, sprite-0-hit (positive + negative cases), 8-sprite overflow
(9-sprite line sets it, exactly-8 doesn't), and scroll-register
increment/copy correctness including both wrap special cases. Also spot
verified `render_bg_line_scrolled` against a known pattern. Reran
`test_cpu.py`/`test_mappers.py`/`test_ppu_bg.py` — no regressions.

Rebuilt `progress/nes_emulator_wip_phase3_full.sb3`: 2,790 blocks.
`validate_sb3.py`: clean.

Explicitly deferred (see doc's final section): fine-X sub-tile pixel-level
horizontal scroll (coarse 8px-granularity scrolling works), PPUMASK bits,
per-scanline cycle-accurate timing (Phase 8), and real hardware's buggy
sprite-overflow evaluation quirk (we implement a simpler always-correct
version).

## 2026-08-01 — Phase 7: cartridge (.nes) loader — DONE

New `code/ines_loader.py`: `parse_ines` (iNES 1.0 header parser: magic
check, PRG/CHR sizes, mapper number from flags6+flags7, mirroring including
four-screen, battery flag, trainer handling, truncated-file detection),
`build_synthetic_nes` (constructs a minimal valid `.nes` file in memory for
testing -- no real ROM used or searched for, per project scope), and
`load_rom_into_emu` (bakes a parsed ROM's PRG/CHR bytes into an `Emu`
build's `PRG`/`CHR` lists via `set_list_items`, plus
MAPPER/MIRROR/PRGBANKS/CHRBANKS/CHRRAM/PRGB0/PRGB1/CHRB0/CHRB1 power-on
defaults).

Full details, including the known four-screen-mirroring limitation (needs
extra VRAM this project doesn't provision) and the NES-2.0-not-specially-
handled caveat, in new `docs/cartridge_loader.md`.

Verified with new `code/test_ines_loader.py` (22 checks: header-parsing
correctness including a high mapper number needing both header-byte
nibbles, CHR-RAM detection, four-screen mirroring, trainer handling,
malformed-file rejection, and an end-to-end bake-then-bus-read/ppu-read
check through `interp.py` with deterministic position-dependent PRG/CHR
fill patterns so a wrong-bank or wrong-offset bug would actually be
caught) -- **all pass**. Reran the full existing suite (CPU/mappers/PPU
bg/sprites) -- no regressions.

Real-ROM testing is explicitly left to the user once they supply their own
legally-obtained `.nes` file (documented in `docs/cartridge_loader.md`).

---

*(Log continues as phases complete — check back for updates.)*
