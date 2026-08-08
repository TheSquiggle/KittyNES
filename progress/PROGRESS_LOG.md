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

## 2026-08-01 — Phase 8: main loop — DONE, v1 complete

Added `phase8_main_loop(e)` to `code/build_core.py`: `nes_init` (power-on
reset, must run after Phase 7's `load_rom_into_emu`), `run_scanline`
(scanline-granularity CPU/PPU timing -- runs CPU instructions accumulating
real cycle counts until ~113.667 have elapsed = 341 PPU dots, then handles
the current scanline: renders if visible and PPUMASK's bg/sprite-enable
bits are set, sets vblank + fires NMI at 241, clears status bits + copies
vertical scroll at the pre-render line 261, advances/wraps `SCANLINE`,
flushes to Pen once per frame on wrap), `run_frame` (262 scanlines), and a
top-level `when green flag clicked` script (`nes_init` then loop
`ctrl_poll` + `run_frame` until a `RUN` flag clears, for deterministic test
harness control).

Full design writeup, including the explicit scanline-vs-dot-granularity
timing tradeoff and what it costs (mid-scanline raster-split effects won't
render correctly; between-scanline effects work fine), in new
`docs/main_loop.md`.

Verified with new `code/test_main_loop.py` (15 checks, all pass): a
synthetic ROM enabling NMI+rendering that spins forever, run through
`interp.py` for enough scanlines to cross into vblank -- confirms
`SCANLINE` advances correctly, vblank sets at 241, the NMI handler actually
executes (not just that the pending flag got set), `NMI_PENDING` clears
after servicing, `FRAME` advances and wraps correctly after a full pass,
vblank clears at the pre-render line, and NMI genuinely doesn't fire when
disabled (separate ROM) while vblank still sets on schedule regardless.
Reran the full existing suite (CPU/mappers/PPU-bg/PPU-sprites/cartridge
loader) -- no regressions.

**This is the last planned phase.** Copied the validated build to
`progress/nes_emulator.sb3` as the definitive v1 artifact (2,880 blocks on
the `CPU` sprite). `validate_sb3.py`: clean.

### What v1 is and isn't

Structurally-verified (every phase has a passing test suite exercising the
real generated block graph via `interp.py`): full 6502 CPU (all official
opcodes/addressing modes/flags), memory bus with NROM/UxROM/CNROM/MMC1
mapper support, PPU background+sprite rendering with scrolling (coarse
8px-granularity) and correct priority/sprite-0-hit behavior, a real iNES
cartridge loader, and a scanline-granularity main loop with vblank/NMI.

NOT verified (and can't be, in this environment): that this actually boots
and plays a real commercial game inside real Scratch/TurboWarp.
`interp.py` is a from-scratch Python re-implementation of the subset of
Scratch VM behavior this project emits -- it's a legitimate and
increasingly load-bearing verification tool (it caught every real bug found
across every phase), but it is not the real Scratch VM, and no real `.nes`
ROM was used anywhere in this build (per project scope -- see
`docs/cartridge_loader.md`). Explicitly out of scope for v1: fine-X
sub-tile pixel scrolling, per-dot cycle-accurate timing (mid-scanline
raster effects), the real hardware's buggy sprite-overflow evaluation
quirk, four-screen mirroring's extra VRAM, and NES 2.0 header extensions.
See each phase's docs file for the full list under "Not yet implemented" /
"known limitations".

## 2026-08-01 — Real-ROM testing: NEStress.NES

First test against a real, non-synthetic NES ROM: `NEStress.NES` (a
well-known freeware/public-domain test ROM from the
christopherpow/nes-test-roms archive, designed to exercise CPU/PPU/input in
one cartridge), placed at `test_roms/NEStress.NES`. New tooling:
`code/build_final.py` (reusable `python build_final.py <rom.nes> [out.sb3]`
driver — assembles all 8 phases and bakes a given ROM in) and
`code/run_nestress_smoke.py` (drives the same build through `interp.py` for
a bounded step count and reports CPU/PPU state, to sanity-check real
execution rather than just build success).

### Bug found and fixed: PC silently became a float

The first smoke run (300k steps) came back with `PC = 32799.0` — a float,
not an int. Traced to `interp.py`'s `operator_mod` using `math.fmod`
(always returns Python `float`, even for exact-integer inputs); since PC
advances via `MOD(ADD(PC,1),65536)` on essentially every step, PC silently
turned into (and stayed) a float after the very first instruction. This
was a **test-harness fidelity gap, not a bug in the generated Scratch
project** (real Scratch/JS numbers are all doubles with no int/float
distinction), but a real one worth fixing since it could mask genuine
fractional-value bugs elsewhere. Fixed by adding `_normnum()` (collapses
whole-valued floats back to `int`) applied at every arithmetic operator's
return point plus the `data_setvariableto`/`data_changevariableby`/
`data_replaceitemoflist` sinks in `interp.py`. Added a regression check to
`test_main_loop.py` asserting PC/SCANLINE/FRAME/A/X/Y/SP stay exact
Python ints after many instructions. Reran the full existing suite (100+
checks across CPU/mappers/PPU-bg/PPU-sprites/cartridge-loader/main-loop) —
no regressions; flag values also now display cleanly as ints instead of
`1.0`/`0.0` as a side benefit. Full writeup in `docs/real_rom_testing.md`.

### `interp.py` addition: input-sensing stubs

`interp.py` had no `sensing_keypressed`/`sensing_mousedown`/`sensing_timer`
support (headless harness, never previously needed to evaluate
input-sensing blocks — synthetic tests all drove state directly). Added a
minimal `self.keys` dict (defaults to nothing pressed) plus straightforward
stub returns for `sensing_mousedown`/`sensing_timer`. Permanent, reasonable
test-harness addition, not a workaround for a real project bug — confirmed
separately that the main loop's `ctrl_poll` (Phase 2/8) uses genuine
Scratch `sensing_keyoptions`/`sensing_keypressed` blocks against real
keyboard state, which works correctly in an actual Scratch/TurboWarp
runtime; the interp.py stub only exists so the headless harness can
evaluate those blocks at all.

### Execution depth against the real ROM

| Steps | Elapsed | Final SCANLINE | Final FRAME | FB non-transparent pixels |
|---|---|---|---|---|
| 300,000 | <1s | 46 (frame 0) | 0 | 0 / 61,440 |
| 1,000,000 | ~1.1s | 155 (frame 0) | 0 | 0 / 61,440 |
| 50,000,000 | ~41s | 63 (frame 7) | 7 | 61,440 / 61,440 (fully populated) |

At 300k-1M steps the game is still in its own init code, before its first
vblank/NMI — an all-transparent framebuffer there is expected (confirmed
via `P_MASK` not yet having its rendering-enable bits set at that point),
not a bug. By 50M steps, `FRAME` has advanced to 7 (multiple full
262-scanline passes, meaning vblank/NMI fired and were serviced repeatedly
-- `NMI_PENDING` reads back 0, not stuck pending) and the entire
256x240 framebuffer is populated with real pixel data. This is strong
evidence the CPU/bus/mapper/PPU-render/main-loop-timing machinery
cooperates correctly against a real, unmodified commercial-grade test
ROM's actual instruction stream and PPU register sequencing — never seen
or special-cased during development, unlike the hand-authored synthetic
tests.

**What this does not (and, in this environment, cannot) show:** whether
NEStress's specific visual test patterns render pixel-correctly (would
need either decoding its pass/fail signaling convention or a real Scratch/
TurboWarp runtime to look at the screen, neither attempted this pass), and
audio (APU is an explicit, documented v1 scope exclusion — NEStress's
sound-register writes land as no-ops via the existing PPU/APU stub range,
same as before this test, not a new issue). Full detail, including the
input-wiring confirmation, in new `docs/real_rom_testing.md`.

## 2026-08-01 — Mapper 66 (GxROM/MHROM) added for a real user ROM

A user tried building with their own "Super Mario Bros. + Duck Hunt (USA)"
ROM and got a permanent grey box. Header inspection found mapper 66
(GxROM/MHROM, 64K PRG/16K CHR/vertical mirroring) — not one of the 4
mappers implemented (0/1/2/3), so `mapper_write` silently no-op'd and PRG/
CHR reads never returned real data.

Added mapper 66 to `code/build_core.py`'s `mapper_write`: a single
write-only register (any $8000-$FFFF address), bits 0-1 select a 32K PRG
bank (the WHOLE window switches together, unlike UxROM's split/MMC1's
fixed-bank schemes), bits 4-5 select an 8K CHR bank. Needed **no new bus
state** — reuses `PRGB0`/`PRGB1`/`CHRB0`/`CHRB1` directly as consecutive
bank pairs (same trick MMC1's 32K PRG mode already uses), since
`mapper_read`/`ppu_read`/`chr_read` already read those generically.
`ines_loader.py` gained a mapper-66-aware power-on default (bank 0 for
both PRG/CHR, no fixed-last-bank concept to default against). Full writeup
in `docs/mapper_specs.md`.

Verified with 16 new checks in `code/test_mappers.py`: initial state,
combined PRG+CHR bank-select write reflected in both `bus_read`/`ppu_read`,
explicit confirmation the *whole* window moves together, and that a write
via a non-`$8000` address still works. All pass; full existing suite
(150+ checks) reran clean, no regressions.

Rebuilt the user's actual ROM: `python code/build_final.py "...Super Mario
Bros. + Duck Hunt (USA).nes" progress/nes_emulator_smb_duckhunt.sb3` —
`validate_sb3.py` clean, header parse confirms mapper 66/mirror 1/4x16K
PRG/2x8K CHR exactly matching the original diagnosis.

Ran a new `code/run_smb_smoke.py` (adapted from the NEStress smoke test)
for 60M interp steps (~49s): the CPU visibly progresses through a real
reset-then-boot sequence (distinct-PC count climbs from 2, in a classic
"wait for vblank" poll loop, up to 93 as init code runs), PPUMASK's
rendering-enable bits get set by frame 10, and the framebuffer becomes
**fully populated and stays that way** from frame 11 through frame 13 —
consistent with reaching a stable rendered title screen. Also observed
`PRGB0`/`PRGB1` changing live during execution, direct evidence the game
itself is actively using the GxROM bank-select register (this cartridge is
a two-game combo pack, so PRG bank-switching between SMB and Duck Hunt is
expected), not just a synthetic test writing it. Full detail, including a
frame-by-frame table, in `docs/real_rom_testing.md`.

## 2026-08-01 — Audio prototype: "click train" technique (UNVERIFIED, standalone)

Before investing in a full Phase 9 APU, prototyped and tested (structurally
only — see below) the user's proposed audio approximation: approximate an
NES channel's pitch by rapidly repeating a short click/pop sound, with the
inter-click silence baked directly into the WAV asset itself (so total
asset duration = exactly `1/frequency`) rather than timed by a Scratch
script loop (which is far too imprecise — frame-quantized — for audible
pitch spacing). The driving script is then just `forever: play sound
<note> until done`, relying on `sound_playuntildone` being a yielding block
whose actual timing is handled by the browser's real audio engine, not
Scratch's tick rate.

**This is explicitly NOT a completed feature.** Built as a standalone
prototype, deliberately NOT integrated into the main `nes_emulator.sb3`
build: `code/audio_prototype.py` generates 5 WAV assets (110/440/880/2000/
4000 Hz, 44.1kHz 16-bit mono PCM, decaying-sine click + silence padding)
and a single-sprite `.sb3` (`progress/audio_prototype.sb3`, keys 1-5 switch
test note, green-flag starts the loop). `validate_sb3.py`: structurally
clean — **that is the only thing verified programmatically.** Whether it
actually sounds like clean, steady, correctly-pitched tones (vs. choppy/
drifting/noisy) can only be confirmed by a human listening in real
TurboWarp/Scratch, which hasn't happened yet.

One real finding during generation (not a listening result, a numeric one):
a naive "clamp click length to fit the period" approach left almost no
silence gap starting around 330Hz (period shorter than the 3ms target click
length) — much lower than expected. Changed to cap the click at 40% of the
period at every frequency, meaning the click itself gets proportionally
shorter (down to ~4 samples at 4000Hz) as pitch rises. Whether a 4-sample
click still reads as a clean "pop" is itself an open question for the
listening test.

Full technique writeup, WAV-generation math, the click-duration finding,
and exactly what to listen for/report back: `research/audio_click_train_approach.md`.
**Full Phase 9 APU integration (all 4 channels, real $4000-$4013 register
writes, envelope/sweep/length-counter approximation) is gated on this
prototype getting confirmed to actually sound right — do not build on top
of this technique until that feedback comes back.**

## 2026-08-01 — Sprite bug hunt (real-ROM report) + fine-X scrolling fix

User reported sprites "screwed up" in the SMB+Duck Hunt real-ROM build —
the first time sprite rendering has been checked against a real game
rather than synthetic tests. Investigated `phase6b_sprites` for the
specific candidates flagged as likely culprits: 8x16 sprite mode
addressing (pattern-table-from-tile-bit0, tile-pair top/bottom split),
horizontal/vertical flip (independently and combined), the priority bit,
sprite-0-hit, OAM DMA ($4014, including destination-address wraparound
when OAMADDR != 0), and the Y-minus-1 hardware quirk.

Wrote `code/test_ppu_sprites2.py`: 21 targeted checks isolating each of
these. **All passed cleanly on the first run (after fixing one bug in the
test itself — an uninitialized palette entry, not an emulator bug) — no
correctness bugs found in any of the specifically-flagged areas.** Also
confirmed empirically, by running the real ROM and dumping live OAM state,
that SMB does use 8x16 sprite mode (PPUCTRL read back as `0x70`, bit 5
set) at least on some screens, validating that this was a real, relevant
thing to check (not a moot point because the ROM never uses it) — and the
dumped sprite data (Y/tile/attr/X for 8 sprites at frame 60) looked like a
coherent, non-corrupted arrangement, not obviously garbled.

**Most likely actual explanation, and the fix applied:** SMB is a
side-scrolling platformer, and the background renderer only supported
*coarse* (8-pixel-granularity) scrolling — a documented Phase 6b
limitation (fine-X sub-tile pixel shift was explicitly listed as "not yet
implemented"). Sprites are drawn at their true absolute per-pixel OAM X/Y
and were never affected by that gap. During horizontal scrolling, a
background snapping in 8px jumps against smoothly-moving sprites (Mario,
enemies) would visually read as "sprites are wrong/misaligned" to an
observer, even though the sprite math itself tested clean.

**Implemented fine-X pixel scrolling** in `render_bg_line_scrolled`
(`build_core.py`): restructured from a single-pass "fetch tile, render its
8 pixels, advance" loop into two passes — (1) fetch 33 tiles' worth of
bitplane data (32 visible + 1 lookahead) into new `ROWP0`/`ROWP1`/`ROWPAL`
row-buffer lists, incrementing coarse X after each; (2) walk all 256
screen pixels, each sampling bit position `(x mod 8) + P_X` — within the
current tile if that's `< 8`, otherwise within the lookahead tile at
`bit - 8`. This is the same "shift register" concept real hardware uses,
just at tile granularity instead of per-dot. Added 4 new checks to
`test_ppu_sprites2.py` confirming the shift actually happens correctly at
`P_X = 0, 3, 7`.

**Result: all 25 checks in the new test file pass, and the full existing
suite (200+ checks across every prior phase) reran clean — no
regressions.** Rebuilt `progress/nes_emulator_wip_phase3_full.sb3`
(2,941 blocks) and, locally only (not committed — copyrighted ROM data,
same policy as before), `progress/nes_emulator_smb_duckhunt.sb3`, for the
user to re-test in TurboWarp.

**Honest caveat:** the fine-X fix is the strongest available hypothesis
given (a) it directly explains the reported symptom for a scrolling game,
(b) it was already a known, documented gap, and (c) targeted tests for
every other specifically-flagged sprite mechanism came back clean — but it
has NOT been confirmed against the actual visual report, since that still
requires the user to look at the real TurboWarp build. If sprites still
look wrong after this fix, the next things to check would be: whether the
"screwed up" symptom was on a non-scrolling screen (which would rule this
fix out and point back toward something not yet found), or a closer
comparison against real NES footage of the exact same game moment.

## 2026-08-01 — Audio prototype v2/v3: warp-mode fix + click-length tradeoff (STILL UNVERIFIED)

v1 human listening test reported two issues: (1) "gap between pops is too
large," (2) "higher pitches sound thin." Both addressed with new prototype
variants, still unverified pending a second listening test.

**Gap hypothesis**: almost certainly Scratch's `forever` C-block yielding
once per iteration at the ~16ms screen-refresh boundary — even in
TurboWarp, unless the loop runs inside a `warp: true` custom block. At
2000-4000Hz the note's own period is only 0.25-0.5ms, so a 16ms
per-iteration tax would completely dominate and sound like exactly what
was reported, at every frequency (consistent with the report not being
limited to just the high notes). **Fix (`code/audio_prototype_v2.py` ->
`progress/audio_prototype_v2.sb3`)**: moved the loop body into a
`warp: true` procedure (`play_notes_forever`) instead of a raw top-level
`forever`. v2 uses the same 40%-click-fraction WAV assets as v1 so a
listening comparison isolates just this fix.

**"Thin at high pitches" hypothesis**: v1's click length was capped at 40%
of the period for silence-margin safety, leaving only 4 samples of actual
click at 4000Hz. **Fix (v3, same script, `progress/audio_prototype_v3.sb3`)**:
raised the cap to 65% of the period (7 samples at 4000Hz, up to 65 at
440Hz), on top of the v2 warp fix — a genuine tradeoff (less silence
margin) not a strict improvement, which is exactly why it needs a listen.

`generate_click_train_wav` in `code/audio_prototype.py` gained a
`click_fraction` parameter (was hardcoded to 0.40) to support this.
Structural validation clean on both new files. Full writeup, the specific
gap-hypothesis reasoning, the new click-length table, and exactly what to
compare across v1/v2/v3 in `research/audio_click_train_approach.md`.
**Still not integrated into the main build; still no claim that any
variant "works" — needs a second human listening test.**

## 2026-08-01 — Sprite bug re-investigation: wrong-tile-data hypothesis (STILL UNRESOLVED)

User clarified the actual SMB+Duck Hunt symptom: sprites show **wrong tile
graphics** ("wrong items displayed"), not misplacement — so the fine-X
scrolling fix from the previous entry almost certainly did NOT address the
real bug, and the 25 earlier targeted tests (OAM DMA/8x16/flip) didn't
rule it out either, since none specifically checked "does OAM tile-index N
pull back CHR tile N's actual data, correctly bank-aware."

Re-focused specifically on that. New `code/test_sprite_chr_bank.py` (28
checks):

1. **Distinct-tile spread**: 8 tiles in pattern-table bank 0 and 8 more in
   bank 1, each with a unique marker byte, fetched via a sprite referencing
   each tile index in turn (both `PPUCTRL` bit-3 states). All 16 checks
   confirm tile index N reliably pulls back tile N's own data — no
   cross-contamination between adjacent tiles or between pattern tables.
2. **The specifically-suspected culprit — CHR bank switching**: built an 8
   sub-bank (32K) CHR setup under `MAPPER=66` (GxROM, the exact mapper this
   ROM uses), performed REAL `bus_write` calls to the GxROM register
   (exactly how the actual game switches CHR banks) across all 4 possible
   8K bank selections, and at each one compared — for the identical CHR
   address — what the **background** fetch path (`ppu_read`, used by
   `bg_setup_tile`/`bg_row_planes`) returns versus what the **sprite**
   fetch path (`spr_fetch_planes`, called from `sprite_eval_line`) returns.
   **All 12 checks pass: sprite and background fetch agree on every bank
   selection**, both correctly resolving to the freshly-selected bank's
   data. Confirmed via code read-through too: `spr_fetch_planes` calls the
   exact same `ppu_read` proc (and therefore the same bank-aware
   `chr_read`) that background tile fetch uses — there is no separate/
   stale CHR addressing path for sprites.

**Result: exhaustive targeted testing of exactly the hypothesis raised
(sprite CHR/bank-fetch correctness) found no bug.** This is a genuinely
inconclusive outcome, documented honestly rather than papered over: the
"wrong tile" symptom has NOT been reproduced or explained by any test
written so far, across two rounds of targeted investigation (positional/
scrolling mechanisms in the previous entry, tile-identity/bank-awareness
in this one). Full checklist and results in `docs/nes_ppu_notes.md`.

**Recommended next steps** (not yet done): (a) get a screenshot or more
specific description from the user of which sprite/screen shows the wrong
graphic, so a matching synthetic scenario can be constructed instead of
guessing at mechanisms; (b) instrument `run_smb_smoke.py`-style real-ROM
execution to dump actual live OAM tile-index/attribute values alongside
CHR bank state at a moment matching the user's report, if a specific
frame/moment can be identified; (c) reconsider mechanisms not yet audited
— e.g. whether `PRGB0`/`PRGB1` PRG bank-switch timing could somehow
corrupt code that writes OAM/CHR-select values (a CPU-side bug manifesting
as sprite corruption, not a PPU-side one) is not yet ruled out.

## 2026-08-01 — Audio v2/v3 gap: warp fix confirmed correctly applied, but user reports still not fast enough

User tested v2/v3 (the warp-mode fix): **still not popping fast enough.**
Investigated two possible explanations:

1. **Is the warp mutation actually taking effect?** Inspected the raw
   generated JSON directly: `procedures_prototype`'s mutation is
   `{"proccode": "play_notes_forever", ..., "warp": "true"}` — this is
   the correct, standard sb3 format (mutation fields are always strings in
   the sb3 JSON schema, including booleans — `"warp": "true"` is exactly
   what a real Scratch export looks like for a warp-mode custom block, not
   a serialization bug). **The warp flag is correctly applied and
   correctly serialized.**
2. **Given that, the most likely remaining explanation is an audio-engine
   latency floor, not a script-timing bug.** `sound_playuntildone` yields
   by design, but every cycle also has to physically START a brand-new
   sound instance in the browser's Web Audio engine (creating/connecting/
   scheduling a new buffer-source node) — overhead that lives entirely
   below the level any Scratch block, warp mode included, can control. If
   that per-cycle startup cost is even a few milliseconds, it would
   dominate the 0.25-9ms periods being tested, regardless of script
   scheduling. **This cannot be measured from this environment (no real
   browser/audio engine available here)** — it's the most plausible
   remaining explanation given the warp fix is confirmed correctly applied
   and the problem persists, but it is a hypothesis, not a confirmed
   diagnosis.

Built `code/audio_prototype_v4_bassfloor.py` ->
`progress/audio_prototype_v4_bassfloor.sb3`: 5 notes in the 55-220Hz range
(roughly the NES triangle channel's low end, periods 4.5-18ms), same
warp-fixed structure as v2/v3. This tests a specific, falsifiable
prediction of the latency-floor hypothesis: since a fixed per-cycle
startup cost would be a much SMALLER fraction of a long (low-frequency)
period than a short one, low notes should sound comparatively cleaner than
the original 2000-4000Hz range if the hypothesis is right — and if even
the lowest note here still sounds gappy, that would point back toward an
unresolved script-timing issue instead. Validated structurally; needs a
human listening test (specifically: does the gap shrink from key 5 down to
key 1).

**Honest bottom line, not claiming anything works:** the click-train
technique may have a hard floor on achievable frequency, below this
project's control, if the audio-engine-latency hypothesis is confirmed.
`research/audio_click_train_approach.md` now documents this and proposes
concrete alternatives for if that turns out to be the case: (a) restrict
click-train to bass/low-frequency channels only (where the floor is a
smaller fraction of the period), (b) for higher/melodic content, use a
SUSTAINED looping sample plus Scratch's "set pitch effect" block to bend
pitch on an already-playing sound (avoiding the per-cycle
restart-a-new-sound cost entirely, at the cost of a fundamentally
different, less "raw NES" timbre), (c) test whether Scratch supports
pre-created/pooled sound instances that could be triggered with lower
per-cycle overhead than always starting fresh. None of these are
implemented yet — this is a documented open decision point pending the
v4 listening test and, ideally, the user's own read on which tradeoff
they'd prefer if the technique's ceiling is confirmed to be real.

## 2026-08-01 — Sprite bug, round 3: palette resolution (STILL not found) + new pen-flush verification capability

User sent an actual screenshot of SMB 1-1: brick/question-block pyramid
shows correct SHAPE but speckled reddish-brown/black static texture; a
Goomba sprite renders as a flat solid black block with no shading. Both
symptoms share "correct shape, wrong/degenerate color" — pointing at
palette resolution rather than tile fetch (already verified correct last
round).

New `code/test_palette.py` (40 checks): all 4 background-attribute
quadrants (TL/TR/BL/BR) within a single attribute byte, each assigned a
different palette group, verified through BOTH the non-scrolled
(`bg_setup_tile`) AND the scrolled (`bg_setup_tile_v`, what the real main
loop actually uses) code paths; palette RAM mirroring
($3F10/14/18/1C -> $3F00/04/08/0C, confirmed correctly ignoring stored
"decoy" values at the mirrored addresses); all 4 sprite palette-select
values resolving to their correct distinct palette (explicitly checked
none degenerate to the universal-background color, which would look like
a flat black blob); and the master 64-color palette table spot-checked
against known real 2C02 values. **Everything passed clean** (one initial
failure was traced to a transcription error in the test's own expected
value, not the emulator's palette table — corrected and reran clean).

**This is now three rounds of exhaustive, specifically-targeted testing
(positional/scrolling, tile-identity/CHR-bank-switching, and now
palette/attribute/color resolution) with zero bugs found.** Given that
pattern, looked at the one part of the rendering pipeline that had NEVER
been checked at all in any round: `flush_fb_to_pen`/`flush_fb_row`, the
Pen-drawing code that turns the computed `FB` framebuffer into actual
on-screen pixels. `interp.py` previously treated all `pen_*` opcodes as
pure no-ops (reasonable for everything else, since Pen output can't be
inspected programmatically) — meaning the run-length line-drawing logic
itself (which colors get drawn at which coordinates) had literally never
been verified against `FB`'s contents, only `FB`'s contents themselves had
ever been checked.

**Added real verification capability**: `interp.py` now records
`(start_xy, end_xy, color)` for every line segment actually drawn (a
`motion_gotoxy` call while pen is down draws a trail, previously
unrecorded — only the pen-down starting position was tracked before). New
`code/test_pen_flush.py` builds a deliberately "busy" scene (4 tiles with
high-frequency alternating bit patterns, multiple background palettes
across quadrants, sprites layered on top — 3,556 line segments drawn),
replays the recorded segments into a synthetic canvas, and compares every
one of the 61,440 pixels against `FB` (resolved through `PALRGB`, exactly
as `flush_fb_row` does it). **All 61,440 pixels match exactly, no gaps, no
mismatches.** (One initial failure was the test comparing `FB`'s raw
palette INDEX against Pen's drawn RGB value without resolving through
`PALRGB` first — a test bug, corrected and reran clean.)

**Honest status: the reported bug has not been found or reproduced after
four rounds of exhaustive, algorithm-level verification covering every
stage of the rendering pipeline this project controls** — tile fetch,
CHR bank-awareness, palette/attribute resolution, and now the pen-flush
drawing logic itself. Given that, the most plausible remaining
explanations, in rough order of likelihood, are things this project's
Python-based verification harness fundamentally cannot check:

1. **Real Scratch/TurboWarp Pen rendering precision/antialiasing.** This
   is the one link in the entire chain that requires an actual browser
   engine to verify — `flush_fb_row`'s logic is now confirmed correct at
   the algorithm level, but whether real Scratch draws a `pen size 1`
   horizontal line as a crisp 1px-tall strip or with some antialiasing/
   subpixel blending at the edges (especially for many short, adjacent,
   differently-colored runs — exactly what a detailed brick texture or a
   small sprite produces) cannot be tested here. This would explain
   "speckled static" on high-detail regions and, if overlapping/blended
   strokes darken toward black, could plausibly explain a small sprite
   reading as a flat dark blob too. **This is a hypothesis, not a
   confirmed finding** — it's the leading candidate specifically because
   every other testable link in the chain has now been individually
   verified correct.
2. A CPU-side bug corrupting values before they reach OAM/PPU registers/
   palette RAM (not yet specifically audited — everything checked so far
   assumed OAM/CHR/PAL list contents were already correct and tested the
   PPU-side consumption of that data, not how the CPU populates it in the
   first place during real gameplay).
3. Something specific to how this exact ROM's real, more complex
   execution differs from every synthetic scenario tested (timing/
   ordering interactions between many simultaneous game systems that no
   isolated unit-style test reproduces).

**If Pen rendering precision is confirmed as the cause, a concrete
mitigation worth trying**: replace the run-length horizontal-line
approach with individual costume "stamps" (even at a coarser granularity
than per-pixel, e.g. per-run stamped rectangles) — a stamp is a discrete
raster blit, not a vector line stroke, and shouldn't have the same
antialiasing/blending behavior as a thin pen line. This has NOT been
implemented — it's a next step to try if the current investigation stalls
further and a screenshot comparison confirms rendering-fidelity artifacts
specifically at run boundaries.

Reran the full existing suite (250+ checks across every prior phase) —
no regressions from the interp.py pen-tracking additions.

## 2026-08-01 — Sprite bug, round 4: CPU-side OAM/mapper-register write path (also clean)

Four rounds of PPU/rendering-side testing (positional/scrolling, tile-
identity/CHR-bank-switching, palette/color resolution, pen-flush drawing)
all came back clean. This round audited the CPU-side path that writes
values INTO OAM and mapper registers in the first place, before rendering
ever sees them — a corruption here would produce the identical symptom
despite provably-correct rendering math.

New `code/test_cpu_oam_writes.py` (18 checks): a realistic $2003 (OAMADDR)
+ $2004 (OAMDATA) write sequence (set address, write 4 consecutive bytes
as a game writing one sprite's Y/tile/attr/X would), confirming each byte
lands at the right index, OAMADDR auto-increments correctly, wraparound at
256 works, neighboring OAM bytes are untouched, and that reading $2004
reflects the current OAMADDR without side effects (doesn't itself
increment). Separately, $4014 (OAM DMA) source-address computation
(`source = written_value * 256`) tested across 4 different source pages
(0x00, 0x02, 0x07 from RAM; 0xFF from PRG-ROM space, to make sure the byte-
value-to-address multiply doesn't only happen to work for low pages),
each with a full 256-byte position-dependent pattern to catch any
off-by-one or wrong-page-base bug. **All 18 checks pass — the CPU-side
write path is also correct.**

Also ran the real ROM further (150 scanline-frames) and dumped: (a) every
mapper-register bank-select value the ROM's own code actually wrote (via
tracking `PRGB0`/`CHRB0` changes over time) — small integers within the
ROM's real `PRGBANKS=4`/`CHRBANKS=2` range at every observed change, no
garbage/out-of-range values; (b) all 64 real OAM entries at frame 150,
checked for Y/tile/X being outside 0-255, and attribute bytes having any
of the 3 documented-unused bits (2-4) set — [results pending / see next
entry once the run completes].

---

*(Log continues as phases complete — check back for updates.)*

---

## 2026-08-08 — Real-ROM rendering bug FOUND AND FIXED: mapper 66 PRG/CHR bit fields were swapped

**Symptom** (user-reported, with screenshot): SMB+Duck Hunt rendered a
recognizable-but-wrong screen — correct tile *shapes* in a plausible layout,
but wrong graphics ("the spritesheet doesn't get correctly rendered, so the
wrong items are displayed"), a garbled brick pyramid, and a Goomba as a flat
black block.

**Why four prior rounds of testing missed it.** Rounds 1–4 audited
positional/scrolling math, tile-identity/CHR-bank fetch, palette/attribute
resolution, and the Pen-flush drawing algorithm — all came back clean,
because none of them was wrong. The defect was one layer up, in *which bank*
the mapper selected.

**How it was actually found.** Instead of writing more targeted unit tests,
the framebuffer was rendered to PNG directly from Python (`code/dump_frames.py`)
and compared against the user's screenshot: they matched exactly, proving the
bug was in emulation logic, not in Scratch/TurboWarp's pen renderer (the
leading prior hypothesis — now disproven). Rendering the nametables directly
from VRAM (`code/dump_vram.py`), bypassing the whole scroll/fetch pipeline,
reproduced the same wrong image — proving VRAM content itself was wrong and
exonerating the entire render path. Finally, tracing every `mapper_write` the
real ROM performs (`code/trace_mapper.py`) showed only **two** writes in 40
frames, leaving PRG bank 0 paired with CHR bank 1 — a code-bank/tileset
mismatch on a 2-in-1 cart, i.e. one game's tilemap drawn with the other
game's graphics.

**Root cause.** The GxROM (mapper 66) bank-select register is, per the
NESdev spec:

```
7  bit  0
---- ----
xxPP xxCC
  ||   ++- 8K CHR bank  (bits 1-0)
  ++------ 32K PRG bank (bits 5-4)
```

PRG is the **high** field and CHR the **low** field. The implementation had
them swapped (PRG from bits 1-0, CHR from bits 5-4). This originated in the
task description given to the implementing agent, which stated the layout
backwards; the agent implemented the spec it was handed, and the tests were
written from that same wrong spec.

**Why the 16-check mapper-66 suite passed anyway.** Every check used the
register value `$11` — which is *symmetric* (PRG=1, CHR=1 under either field
order) and therefore structurally incapable of distinguishing the two
readings. A classic degenerate-test-value blind spot.

**Fix.** Swapped the two field extractions in `build_core.py`'s mapper-66
branch of `mapper_write`. After the fix the same ROM runs from PRG bank 1 and
actively toggles CHR banks twice per frame (a real mid-frame tileset switch),
versus only 2 mapper writes total before — confirming the CPU had previously
been executing the wrong PRG bank entirely.

**Regression protection added.** `test_mappers.py` gained asymmetric checks
using `$10` and `$01` (mirror-image values that fail loudly if the field
order is ever flipped back), and `test_sprite_chr_bank.py`'s bank-select
writes were corrected from `chrbank << 4` to `chrbank & 0x03`. Full suite
green: CPU 36, mappers (incl. 12 new asymmetric), PPU bg 11, sprites 22+25,
sprite/CHR-bank 28, palette 40, loader 22, main loop 15, CPU OAM writes 18.

**New diagnostic tooling** (kept, genuinely reusable): `code/dump_frames.py`
(framebuffer → PNG per frame), `code/dump_vram.py` (direct nametable render,
bypassing the block-graph render path), `code/trace_mapper.py` (logs every
mapper register write with decoded fields and resulting bank state).

**Note:** `FB` is currently the name of *both* a variable (the CPU B flag)
and a list (the framebuffer). Scratch keeps variable and list namespaces
separate so this is not presently a defect, but it is an accident waiting to
happen and should be renamed.
