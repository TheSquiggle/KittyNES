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

---

*(Log continues as phases complete — check back for updates.)*
