# The NES, Component by Component — and How KittyNES Emulates Each Piece

This document explains, in detail, every subsystem of the Nintendo Entertainment
System (NES) hardware, and the specific technique KittyNES (this project) uses to
emulate it inside vanilla Scratch 3.0. It assumes no prior emulator-writing
knowledge but does assume basic familiarity with binary/hex numbers.

Console model covered: the original NTSC NES / Famicom (RP2A03 CPU + RP2C02 PPU).
PAL timing differences are noted where relevant but not implemented in v1.

---

## 1. System overview

The NES is built around three custom Ricoh chips glued together over a shared bus:

- **RP2A03** — a modified MOS 6502 CPU (no decimal mode in hardware, though the
  opcode/flag exists) with an integrated APU (audio).
- **RP2C02** — the PPU (Picture Processing Unit), a dedicated tile/sprite graphics
  chip. It has its own address space (VRAM) separate from CPU RAM, connected to the
  CPU only through 8 memory-mapped registers.
- **The cartridge** — contains PRG-ROM (program code), often CHR-ROM/CHR-RAM
  (graphics tile data), and a **mapper** chip that lets games exceed the CPU's
  64KB and PPU's 16KB native address spaces via bank switching.

The CPU runs at 1.789773 MHz (NTSC). The PPU runs at exactly 3× the CPU clock
(5.369318 MHz) and renders one pixel per PPU cycle, 341 PPU cycles per scanline,
262 scanlines per frame (of which 240 are visible). This 3:1 ratio is the
fundamental timing relationship the whole emulator's main loop is built around.

```
1 CPU cycle = 3 PPU cycles = 3 pixels of PPU work
1 scanline  = 341 PPU cycles ≈ 113.67 CPU cycles
1 frame     = 262 scanlines = 89,342 PPU cycles ≈ 29,780.67 CPU cycles
```

---

## 2. The 6502 CPU (RP2A03)

### 2.1 Registers

| Register | Width | Purpose |
|---|---|---|
| A (accumulator) | 8-bit | arithmetic/logic operand and result |
| X | 8-bit | index register, also used for stack ops on some illegal opcodes |
| Y | 8-bit | index register |
| SP (stack pointer) | 8-bit | offset into the fixed page $0100–$01FF; stack grows *downward* |
| PC (program counter) | 16-bit | address of the next instruction |
| P (status/flags) | 8-bit | packed flag byte, see below |

**KittyNES representation:** `A`, `X`, `Y`, `SP`, `PC` are Scratch global variables
holding plain integers. The status register is *not* kept packed at all times —
each flag is its own global variable (`FLAG_C`, `FLAG_Z`, `FLAG_I`, `FLAG_D`,
`FLAG_B`, `FLAG_V`, `FLAG_N`, each 0 or 1). This is far cheaper in Scratch than
repeatedly packing/unpacking bits from a byte, since Scratch has no native bit
ops (see [`../research/scratch_workarounds.md`](../research/scratch_workarounds.md)).
The packed byte `P` is only *materialized* on demand — when `PHP`/`BRK` pushes it
to the stack, or when `PLP`/`RTI` pulls it back and needs to unpack it into the
individual flag variables.

### 2.2 The status register (P) bit layout

```
Bit:  7 6 5 4 3 2 1 0
      N V 1 B D I Z C
```

- **C (Carry)** — set by ADC/SBC/shifts/compares when there's a carry/borrow out.
- **Z (Zero)** — set when the result of an operation is 0.
- **I (Interrupt disable)** — when set, IRQs are masked (NMI is never maskable).
- **D (Decimal mode)** — selects BCD arithmetic on real 6502; the NES's RP2A03
  has this flag but the *hardware silently ignores it* — ADC/SBC are always
  binary. KittyNES therefore tracks the D flag bit faithfully (so `SED`/`CLD`
  and flag-reading code behave correctly) but ADC/SBC never branch behavior on it.
- **B (Break)** — not a real stored flag; it's a bit pattern quirk. It reads as 1
  when P is pushed by BRK or PHP, and as 0 when pushed by a hardware IRQ/NMI. Bit
  5 (unused) is always pushed as 1. KittyNES models this as: `PHP`/`BRK` always
  push bit 5 = 1; `BRK` pushes bit 4 (B) = 1; NMI/IRQ push bit 4 (B) = 0.
- **V (Overflow)** — set on signed arithmetic overflow (ADC/SBC) or by the `BIT`
  instruction (copies bit 6 of the tested memory value).
- **N (Negative)** — set to bit 7 of the result (sign bit).

### 2.3 Addressing modes

The 6502 has 13 addressing modes. Every opcode's operand-fetch logic reduces to
one of these, each implemented in KittyNES as a shared custom block that computes
an **effective address** into the global `EFF_ADDR`, and (where the mode reads
memory) the **effective value** into `EFF_VALUE`. Opcodes then call the shared
mode block once, then execute their own logic against `EFF_ADDR`/`EFF_VALUE` —
this is what keeps ~151 opcodes from becoming ~151 independent memory-fetch
implementations.

| Mode | Syntax | Bytes | Effective address computation |
|---|---|---|---|
| Implied | `CLC` | 1 | none — operand is implicit (often the accumulator or a flag) |
| Accumulator | `ASL A` | 1 | operand is register A itself, not memory |
| Immediate | `LDA #$10` | 2 | operand is the literal byte following the opcode |
| Zero Page | `LDA $10` | 2 | address = byte operand (0–255), i.e. page 0 |
| Zero Page,X | `LDA $10,X` | 2 | address = `(byte operand + X) mod 256` (wraps within page 0) |
| Zero Page,Y | `LDX $10,Y` | 2 | same, with Y (only used by LDX/STX-family ops) |
| Absolute | `LDA $1234` | 3 | address = 16-bit operand, little-endian in the instruction stream |
| Absolute,X | `LDA $1234,X` | 3 | address = 16-bit operand + X (can cross a page — extra cycle) |
| Absolute,Y | `LDA $1234,Y` | 3 | same, with Y |
| Indirect | `JMP ($1234)` | 3 | address = the 16-bit value stored at the given address (JMP only; has the famous page-wrap bug, see 2.5) |
| (Indirect,X) | `LDA ($10,X)` | 2 | pointer = `(byte operand + X) mod 256` (zero page, wraps); address = 16-bit value stored at that zero-page pointer |
| (Indirect),Y | `LDA ($10),Y` | 2 | pointer = 16-bit value stored at zero-page byte operand; address = pointer + Y (can cross a page — extra cycle) |
| Relative | `BEQ label` | 2 | used only by branches: address = PC (after the 2-byte instruction) + signed 8-bit offset |

**Zero-page wraparound** is a real hardware quirk (not a bug to "fix"): `LDA
$FF,X` with X=$02 reads from `$01`, not `$0101`. KittyNES's zeropage,X/Y mode
blocks compute `(base + reg) mod 256` explicitly for this reason — a naive
`base + reg` would be wrong.

### 2.4 Cycle counts and page-crossing penalties

Every opcode has a base cycle count from the official reference table (baked
into KittyNES's Python-side opcode table, see
[`6502_opcode_table.md`](6502_opcode_table.md)). Two extra-cycle rules apply on
top of the base count:

1. **Page-crossing on indexed/indirect-indexed reads**: Absolute,X / Absolute,Y /
   (Indirect),Y cost **+1 cycle** if adding the index register carries into a
   new page (i.e. `(base & 0xFF00) != (base+reg) & 0xFF00`). This does *not*
   apply to write instructions (STA/STX/STY/etc.) or to read-modify-write
   instructions (ASL/ROL/INC/etc. in these modes always pay the extra cycle
   unconditionally) — the CPU can't know until the read that it needs the extra
   cycle, so writes/RMW ops are simply defined to always take it.
2. **Branch timing**: a branch instruction (BEQ, BNE, BCC, BCS, BPL, BMI, BVC,
   BVS) costs 2 cycles if not taken, 3 if taken (no page cross), 4 if taken and
   the target is on a different page than the instruction after the branch.

KittyNES computes page-crossing by comparing the high byte of the base address to
the high byte of `base + index`, using integer division by 256 (`floor`) rather
than any bit trick — this doesn't need a lookup table, ordinary Scratch math
works fine since it's just `floor(a/256) = floor(b/256)`.

Cycle-accurate timing matters because the PPU/CPU relationship (3 PPU cycles per
CPU cycle) is how the emulator knows when to trigger vblank/NMI and when
scanline-based effects (mid-frame palette/scroll changes some games rely on)
would occur — v1 targets *instruction-level* cycle accuracy (correct total count
per instruction) rather than sub-instruction/mid-opcode PPU-CPU interleaving.

### 2.5 The indirect JMP page-wrap bug

`JMP ($xxFF)` — an indirect jump whose pointer is at the last byte of a page —
reads the low byte from `$xxFF` correctly but incorrectly reads the high byte
from `$xx00` (the *start* of the same page) instead of `$(xx+1)00`. This is a
famous hardware bug that real games/tools sometimes depend on (or a test ROM
explicitly checks for). KittyNES's `JMP` indirect-mode block reproduces it
exactly: the high-byte fetch address is computed as `(ptr & 0xFF00) | ((ptr+1) &
0x00FF)`, not `ptr+1` outright.

### 2.6 The full opcode set

All 151 official (documented) opcodes are implemented — every legal combination
of the 56 mnemonics below with their valid addressing mode(s).

**Load/Store:** LDA, LDX, LDY, STA, STX, STY
**Transfer:** TAX, TAY, TXA, TYA, TSX, TXS
**Stack:** PHA, PHP, PLA, PLP
**Arithmetic:** ADC, SBC
**Increment/Decrement:** INC, INX, INY, DEC, DEX, DEY
**Shifts/Rotates:** ASL, LSR, ROL, ROR
**Logic:** AND, ORA, EOR, BIT
**Compare:** CMP, CPX, CPY
**Branches:** BCC, BCS, BEQ, BNE, BMI, BPL, BVC, BVS
**Jumps/Calls:** JMP, JSR, RTS, BRK, RTI
**Flags:** CLC, CLD, CLI, CLV, SEC, SED, SEI
**Other:** NOP

Full per-opcode data (byte value, mode, cycles, flags affected) lives in
[`6502_opcode_table.md`](6502_opcode_table.md), generated directly from the
Python source table that drives KittyNES's code generator — the single source of
truth, not hand-transcribed.

**Illegal/undocumented opcodes** (e.g. `LAX`, `SAX`, `DCP`, `SLO`...) are *not*
implemented in v1. Some commercial games and many test ROMs use them, but they're
out of scope for the initial build; NOP is substituted for undefined byte values
so the CPU doesn't crash, it just doesn't do anything for that byte (this is a
documented limitation, not silent — see the top-level README's limitations list).

### 2.7 Interrupts: RESET, NMI, IRQ, BRK

The 6502 has three interrupt-like entry points, each reading a 16-bit vector
from a fixed location and jumping there:

| Vector | Address | Triggered by |
|---|---|---|
| RESET | `$FFFC`–`$FFFD` | power-on / reset button |
| NMI | `$FFFA`–`$FFFB` | PPU entering vblank (non-maskable — always fires) |
| IRQ/BRK | `$FFFE`–`$FFFF` | mapper IRQ, APU frame IRQ, or the `BRK` instruction (maskable by the I flag, except BRK itself always executes) |

**RESET** sets `SP -= 3` (real hardware quirk — it doesn't actually push
anything on cold reset, it just decrements SP as if it had), sets the I flag,
and loads `PC` from `$FFFC/$FFFD`.

**NMI** pushes `PC` (high byte then low byte) and `P` (with B=0, bit5=1) onto
the stack, sets the I flag, and loads `PC` from `$FFFA/$FFFB`. In KittyNES, NMI
is polled once per CPU-instruction boundary against a `NMI_PENDING` flag that the
PPU sets when it enters vblank (scanline 241) — this is the mechanism games use
to synchronize game logic to the 60Hz frame rate, since almost every game's main
loop is "do game logic, then wait for NMI."

**IRQ** behaves like NMI but is ignored if `FLAG_I = 1`, and uses the shared
`$FFFE/$FFFF` vector. Sources in v1: none yet fire real IRQs (APU frame IRQ and
mapper IRQs — e.g. MMC3's scanline counter, not in v1's mapper list — are future
work); the vector and dispatch mechanism exists and is tested via BRK.

**BRK** is a 1-byte instruction that's actually treated as 2 bytes wide (the
byte after BRK is skipped, a historical quirk so BRK can be used as a padding-
safe software breakpoint) — pushes `PC+2`, pushes `P` with B=1, sets I=1, and
jumps through the same `$FFFE/$FFFF` vector as IRQ.

### 2.8 The stack

A push-only-downward stack living at addresses `$0100 + SP`, `SP` starts at
`$FD` after reset (top of stack is `$01FD`, decrementing toward `$0100` on
pushes, wrapping around within the single page — SP is always 8-bit and never
carries out of page 1). `PHA`/`PLA`, `PHP`/`PLP`, `JSR`/`RTS` (push/pull the
return address), and interrupt entry/`RTI` are the only stack users. KittyNES
implements push/pull as two tiny custom blocks (`stack_push %s` /
`stack_pull`→`RESULT`) that write/read `RAM` at `$0100+SP` and inc/dec `SP` with
wraparound via `mod 256`.

---

## 3. Memory map (CPU address space, $0000–$FFFF)

| Range | Size | Contents |
|---|---|---|
| `$0000`–`$07FF` | 2KB | Internal work RAM |
| `$0800`–`$1FFF` | — | Mirrors of `$0000`–`$07FF`, repeated 3×  (`addr mod 2048`) |
| `$2000`–`$2007` | 8 bytes | PPU registers |
| `$2008`–`$3FFF` | — | Mirrors of `$2000`–`$2007`, repeated every 8 bytes (`addr mod 8`) |
| `$4000`–`$4017` | 24 bytes | APU registers + joypad I/O (`$4016`/`$4017`) |
| `$4018`–`$401F` | 8 bytes | APU/IO test-mode registers, normally disabled |
| `$4020`–`$5FFF` | — | Expansion area (rarely used; some mappers put registers here) |
| `$6000`–`$7FFF` | 8KB | Cartridge SRAM / battery-backed save RAM (if present) |
| `$8000`–`$FFFF` | 32KB | Cartridge PRG-ROM, bank-switched by the mapper |

KittyNES's `bus_read`/`bus_write` custom blocks are the single chokepoint every
other component goes through — the CPU never touches `RAM` or `PRG_ROM` lists
directly, always via `bus_read %s` / `bus_write %s %s`, so that mapper and PPU
register logic stay centralized in one place. See
[`memory_map.md`](memory_map.md) for the exact branch structure of these two
custom blocks as implemented.

---

## 4. The PPU (RP2C02)

The PPU is a separate chip with its own 14-bit address bus (16KB addressable,
`$0000`–`$3FFF`), connected to the CPU only via 8 registers mapped at
`$2000`–`$2007` (mirrored through `$3FFF`).

### 4.1 PPU registers

| Addr | Name | R/W | Purpose |
|---|---|---|---|
| `$2000` | PPUCTRL | W | base nametable, VRAM increment, sprite/BG pattern table select, sprite size, NMI enable |
| `$2001` | PPUMASK | W | rendering enable (BG/sprites), greyscale, left-column clipping, color emphasis |
| `$2002` | PPUSTATUS | R | vblank flag, sprite 0 hit, sprite overflow; **reading clears vblank flag and the address latch** |
| `$2003` | OAMADDR | W | address into the 256-byte sprite attribute memory (OAM) |
| `$2004` | OAMDATA | R/W | read/write OAM byte at OAMADDR, auto-increments on write |
| `$2005` | PPUSCROLL | W×2 | fine scroll X then Y (write-twice register, uses the shared address latch) |
| `$2006` | PPUADDR | W×2 | VRAM address high byte then low byte (write-twice register) |
| `$2007` | PPUDATA | R/W | read/write VRAM at the current PPUADDR, auto-increments by 1 or 32 |

The "write-twice" registers ($2005, $2006) share a single latch flip-flop `w`
that's reset to 0 by reading `$2002`. KittyNES models this with a global
`PPU_ADDR_LATCH` (0 or 1) exactly mirroring hardware behavior.

### 4.2 VRAM address space ($0000–$3FFF, PPU-internal)

| Range | Contents |
|---|---|
| `$0000`–`$0FFF` | Pattern table 0 (tile bitmaps, "left" table) |
| `$1000`–`$1FFF` | Pattern table 1 ("right" table) |
| `$2000`–`$23FF` | Nametable 0 |
| `$2400`–`$27FF` | Nametable 1 |
| `$2800`–`$2BFF` | Nametable 2 |
| `$2C00`–`$2FFF` | Nametable 3 |
| `$3000`–`$3EFF` | Mirror of `$2000`–`$2EFF` |
| `$3F00`–`$3F1F` | Palette RAM indexes (background + sprite palettes) |
| `$3F20`–`$3FFF` | Mirrors of `$3F00`–`$3F1F` |

The NES only has physical RAM for **2 nametables** (2KB), not 4 — the cartridge
wiring determines whether nametables 0–3 mirror **horizontally**, **vertically**,
or (rare, mapper-controlled) some other layout. This mirroring mode comes from
the iNES header (or is switchable by MMC1). KittyNES's nametable-read logic
applies the configured mirroring the same way the bus applies RAM mirroring —
via modular address translation before the list lookup.

### 4.3 Pattern tables and the 2-bitplane tile format

Each 8×8 tile occupies **16 bytes** in a pattern table: the first 8 bytes are
"bitplane 0", the next 8 are "bitplane 1". For each row `y` (0–7), the pixel's
2-bit color index (0–3) is:

```
bit0 = bitplane0_byte[y] bit (7-x)
bit1 = bitplane1_byte[y] bit (7-x)
color_index = bit0 + bit1*2
```

This is exactly a bit-extraction problem — KittyNES reuses the `BIT7_T`-style
precomputed tables (generalized to a per-bit-position extraction table) rather
than doing shifts per pixel, since this runs 8×8×(number of visible tiles) times
per frame and is the single hottest loop in the whole emulator. See
[`research/scratch_workarounds.md`](../research/scratch_workarounds.md) for the
exact table layout used for per-pixel bitplane decode.

A `color_index` of 0 is always transparent (shows the backdrop color) for both
background and sprite tiles. The other 3 indices select into a 4-color palette
chosen by the tile's **attribute** (background) or the sprite's attribute byte
(sprites).

### 4.4 Nametables and attribute tables

A nametable is a 32×30 grid of tile indices (960 bytes) into the currently
selected pattern table, followed by a 64-byte **attribute table**. Each
attribute byte covers a 4×4-tile (32×32 pixel) region, split into four 2×2-tile
quadrants, 2 bits per quadrant, selecting one of 4 background palettes for every
tile in that quadrant:

```
attribute byte bits: 33 22 11 00   (bottom-right, bottom-left, top-right, top-left quadrant)
```

KittyNES extracts the relevant 2-bit field per tile using `floor`/`mod`
arithmetic on the quadrant position (which quadrant a given tile falls in is
purely a function of `(tile_x mod 4, tile_y mod 4)`, no bit ops needed there) and
one small 4-entry lookup for the final 2-bit shift-and-mask, driven off the same
kind of precomputed table approach as pattern-table decode.

### 4.5 The palette

`$3F00` is the universal background color; `$3F01`–`$3F03` background palette 0's
3 non-transparent colors; `$3F04` is unused (mirrors `$3F00` conceptually, some
games write it anyway); the pattern repeats for background palettes 1–3 at
`$3F05`–`$3F0F`, then sprite palettes 0–3 at `$3F11`–`$3F1F` (`$3F10`/`$3F14`/
`$3F18`/`$3F1C` mirror the universal background color). Each byte is an index
0–63 into the NES's fixed 64-color master palette (a hardware-defined RGB table,
not configurable) — KittyNES bakes this 64-entry RGB table as a Python-generated
Scratch list once, used to translate a palette index into an actual pen color
when a pixel is plotted.

### 4.6 OAM (sprites)

256 bytes = 64 sprites × 4 bytes each:

| Byte | Meaning |
|---|---|
| 0 | Y position (top of sprite, minus 1 — a well-known off-by-one in hardware) |
| 1 | Tile index (pattern-table tile number; bit 0 selects pattern table in 8×8 mode) |
| 2 | Attributes: bits 0–1 = palette (sprite palettes 0–3), bit 5 = priority (behind/in-front of background), bit 6 = horizontal flip, bit 7 = vertical flip |
| 3 | X position |

Real hardware only has room to render **8 sprites per scanline**; a 9th
overlapping sprite triggers the "sprite overflow" flag (`$2002` bit 5) and that
sprite doesn't render on that line — a deliberate hardware limitation some games
rely on/work around, implemented in KittyNES's per-scanline sprite-evaluation
pass by simply capping the per-line sprite list at 8 and setting the overflow
flag when a 9th would have qualified.

**Sprite 0 hit** (`$2002` bit 6) is set when a non-transparent pixel of sprite 0
overlaps a non-transparent background pixel during rendering — many games poll
this to detect a specific scanline (e.g. to split status-bar vs. scrolling
playfield). Implemented in the per-pixel compositing step: when plotting sprite
0's pixel, if the background pixel at that same coordinate was non-transparent,
set the flag.

### 4.7 Scrolling — the loopy registers

Real PPU hardware implements scrolling via two internal 15-bit registers
(commonly called `v` — current VRAM address — and `t` — temporary/latched
address, both with the same bit layout: fine-Y(3) / nametable-select(2) /
coarse-Y(5) / coarse-X(5)), a 3-bit fine-X register `x`, and the single write
toggle `w` shared with PPUADDR. This scheme (reverse-engineered by Loopy, hence
"loopy registers") is what makes `$2005`/`$2006` writes and rendering-time
increments compose correctly, including **mid-frame** scroll changes (the
classic split-screen status-bar trick).

KittyNES implements `v`, `t`, `x`, `w` as plain integer globals (`PPU_V`,
`PPU_T`, `PPU_X`, `PPU_ADDR_LATCH`), decomposing/recomposing the packed 15-bit
field via `floor`/`mod` arithmetic (never bit ops — this is one of the few
places in the whole project where ordinary division-based bit-field packing is
simpler than a lookup table, since the fields aren't byte-aligned). The
coarse-X/coarse-Y increment-with-wrap-and-nametable-flip logic (incrementing
`v` at each tile during background rendering, including the wraparound into the
next nametable) is implemented faithfully rather than approximated with a
simpler "camera offset" model, specifically so mid-frame scroll splits work.

### 4.8 Rendering pipeline as implemented

v1's approach, per the phased build plan:

1. **Background-only pass** (Phase 6a): for each of the 240 visible scanlines,
   for each of 32 visible tile columns, look up the nametable byte → pattern
   table tile → decode 8 pixels using the bitplane tables → look up attribute
   palette → write pixel colors into a **framebuffer list** (256×240 flat,
   1-indexed like everything else: pixel `(x,y)` = list item `y*256+x+1`).
   Actual on-screen drawing uses the Pen extension: rather than one `pen down` +
   stamp per pixel (way too slow), KittyNES draws using `pen_setPenColorToColor`
   + a single 1×1 costume stamped per pixel run, batched, at the *end* of each
   scanline or frame — see [`docs/rendering_perf_notes.md`](rendering_perf_notes.md)
   (once written) for the exact batching strategy and its measured Scratch
   performance tradeoffs.
2. **Sprite pass** (Phase 6b): per-scanline OAM evaluation (find ≤8 sprites
   whose Y range covers this scanline), decode each sprite's 8×8 (or 8×16)
   tile the same way as background tiles, composite over the background pixel
   buffer respecting priority bit and sprite-0-hit detection, before the
   scanline's buffer is committed to the framebuffer.
3. **Scrolling** (Phase 6b): full loopy-register implementation as above, so
   both simple full-screen scrolling and mid-frame split-scroll games render
   correctly.

### 4.9 PPU/CPU timing relationship

The PPU free-runs independently, 3 PPU cycles per CPU cycle, across 262
scanlines/frame. Key scanline events:

- Scanlines 0–239: visible frame, rendering happens.
- Scanline 240: idle ("post-render").
- Scanline 241 (specifically PPU cycle 1 of it): **vblank flag set**, and if
  PPUCTRL's NMI-enable bit is set, an NMI fires — this is the "go" signal almost
  every game's main loop waits for.
- Scanlines 242–260: vblank period, CPU is free to write to PPU registers/OAM
  without tearing (this is *why* games do their PPU updates here).
- Scanline 261 (pre-render): vblank flag and sprite-0-hit/overflow flags are
  cleared near the start; internal rendering-position registers (v ← t)
  refresh here to prepare for the new frame.

KittyNES's main loop runs the CPU one instruction at a time, accumulates CPU
cycles spent, converts to PPU cycles owed (`×3`), and advances a
`PPU_SCANLINE`/`PPU_CYCLE` pair of globals accordingly, firing the vblank
flag/NMI and end-of-frame framebuffer flush at the right points — see
[`docs/main_loop_timing.md`](main_loop_timing.md) (written once Phase 8 lands).

---

## 5. The APU (audio) — v1 stub

The RP2A03's APU has 5 channels (2 pulse/square, 1 triangle, 1 noise, 1 DMC
sample-playback) plus a frame sequencer that generates envelope/sweep/length
updates and an optional IRQ. **v1 implements none of this** — APU register
writes at `$4000`–`$4013`/`$4015`/`$4017` are accepted (so games don't hang
waiting on them) but produce no sound; reads from `$4015` (status) report all
channels silent/disabled. This is a deliberate, documented scope cut, not an
oversight — audio synthesis inside vanilla Scratch block logic, sample-accurate,
is a substantial project on its own and is out of scope until CPU/PPU
correctness is solid.

---

## 6. Cartridges, iNES format, and mappers

### 6.1 The iNES file format

A `.nes` file starts with a 16-byte header:

```
Bytes 0-3:  "NES\x1A" (magic number)
Byte  4:    PRG-ROM size in 16KB units
Byte  5:    CHR-ROM size in 8KB units (0 = uses CHR-RAM instead)
Byte  6:    flags 6: mirroring bit, battery-backed SRAM bit, trainer-present bit,
            four-screen VRAM bit, low nibble of mapper number
Byte  7:    flags 7: NES 2.0 identifier bits, high nibble of mapper number
Bytes 8-15: (various, mostly unused in the simple iNES 1.0 format; NES 2.0
            extends this but v1's loader targets plain iNES 1.0)
```

Followed by: an optional 512-byte trainer (if flags6 bit 2 set), then PRG-ROM
data, then CHR-ROM data (if any).

### 6.2 The Python-side loader (build-time, not runtime)

Scratch cannot read arbitrary binary files at runtime, so **cartridge loading
happens entirely at .sb3 build time**: a Python script (`code/nes_loader.py`,
written in Phase 7) parses the iNES header, extracts PRG-ROM and CHR-ROM byte
arrays, and bakes them directly into the generated project as Scratch lists
(`PRG_ROM`, `CHR_ROM`) — exactly the same "precompute at build time, O(1) lookup
at runtime" philosophy used for the bitwise-op tables. The mapper number and
mirroring mode from the header also get baked in as global variable initial
values (`MAPPER_NUM`, `MIRROR_MODE`), so the runtime mapper-dispatch logic
(which *is* generic Scratch block logic, not baked per-game) knows how to treat
this specific ROM.

This means: **each game ROM produces its own separate .sb3 file** — the loader
is a build step you re-run per ROM, not a runtime "insert cartridge" feature
inside one universal project. (A universal "pick a ROM from a list of pre-baked
carts" project is possible as a future enhancement — multiple ROMs' PRG/CHR data
baked as separate lists, selected by a menu — but v1 targets one ROM per build.)

### 6.3 Mappers — why they exist

The CPU can only address 32KB of cartridge space (`$8000`–`$FFFF`) and the PPU
only 8KB of pattern-table space (`$0000`–`$1FFF`), but many games ship far more
ROM than that (some MMC1 games have 512KB PRG-ROM). A mapper is cartridge-side
logic, triggered by the CPU writing to special addresses in `$8000`–`$FFFF` (or
sometimes reading has side effects), that swaps which physical ROM bank is
currently visible at a given CPU/PPU address window. KittyNES's `bus_read`/
`bus_write` route any cartridge-space access through a `mapper_read %s` /
`mapper_write %s %s` dispatch pair that branches on the baked-in `MAPPER_NUM`,
so each mapper is a self-contained set of custom blocks that only needs to
implement "given this address and (for writes) this value, which PRG/CHR bank
is now active."

### 6.4 Mapper 0 — NROM

The simplest case, no bank switching at all:

- PRG-ROM: either 16KB (mirrored to fill the full `$8000`–`$FFFF` window — reads
  at `$C000`–`$FFFF` return the same bytes as `$8000`–`$BFFF`) or 32KB (fills the
  window exactly, no mirroring).
- CHR-ROM: fixed 8KB, no switching (or CHR-RAM if the header says 0 CHR banks —
  the PPU can freely write to it, used by games that generate tiles at runtime).
- No mapper registers — writes to `$8000`–`$FFFF` are simply ignored (some NROM
  games do write there, e.g. from bugs or region-detection code; hardware
  ignores it and so does KittyNES).

This is what Phase 2's `bus_read` already implements for the ≥$8000 case (with
`PRG_ROM_SIZE`-based mirroring), and it's also the baseline the functional test
ROM runs against in Phase 4.

### 6.5 Mapper 2 — UxROM

- PRG-ROM: switchable 16KB bank at `$8000`–`$BFFF`, **fixed** to the *last*
  16KB bank at `$C000`–`$FFFF` (this fixed-last-bank convention is why UxROM
  games put their reset/interrupt vectors and core engine in the last bank).
- A write to any address in `$8000`–`$FFFF` (games conventionally use `$8000`)
  sets the low bits of the written value as the selected PRG bank index for the
  switchable window (typically 3–4 bits depending on total ROM size — the exact
  mask is `PRG_BANKS - 1` computed at build/load time from the header's bank
  count, not a fixed hardware constant).
- CHR is fixed 8KB CHR-RAM (UxROM carts never have CHR-ROM).

### 6.6 Mapper 3 — CNROM

- PRG-ROM: fixed (16KB mirrored or 32KB, exactly like NROM — no PRG switching).
- CHR-ROM: switchable 8KB bank, selected by the low 2 bits (sometimes more, for
  larger CNROM carts) of any value written to `$8000`–`$FFFF`.
- This is the simplest possible *graphics* bank switcher — good "second mapper"
  to implement after NROM since it only touches the PPU-side read path.

### 6.7 Mapper 1 — MMC1

Considerably more complex — a **serial shift register** interface. Every write
to `$8000`–`$FFFF` shifts one bit (bit 0 of the written value) into a 5-bit
shift register, LSB first; a write with bit 7 set instead *resets* the shift
register (clearing it and forcing 16KB PRG mode). After the 5th bit shift, the
accumulated 5-bit value is written into one of 4 **internal MMC1 registers**,
selected by which address range (`$8000`–`$9FFF` / `$A000`–`$BFFF` /
`$C000`–`$DFFF` / `$E000`–`$FFFF`) the *5th* (triggering) write landed in:

| Register | Address range | Bits | Meaning |
|---|---|---|---|
| Control | `$8000`–`$9FFF` | mirroring mode (2 bits: single-low/single-high/vertical/horizontal), PRG bank mode (2 bits), CHR bank mode (1 bit) |
| CHR bank 0 | `$A000`–`$BFFF` | selects 4KB CHR bank (or the low half of an 8KB bank, depending on CHR mode) |
| CHR bank 1 | `$C000`–`$DFFF` | selects the other 4KB CHR bank (only used in 4KB CHR mode) |
| PRG bank | `$E000`–`$FFFF` | selects 16KB PRG bank (or, in 32KB mode, bits map differently); bit 4 can also gate PRG-RAM enable on some boards |

MMC1's PRG bank mode (from the Control register) selects among: 32KB switch (one
bank, ignore low bit of PRG bank register), fix-first-bank/switch-16KB-at-$C000,
or fix-last-bank/switch-16KB-at-$8000 (the last is what most MMC1 games use, and
is the "resume in the fixed bank" convention like UxROM). **This is the mapper
implemented last in v1's list precisely because of this register-mode
complexity** — KittyNES models the shift register as a `MMC1_SHIFT` (accumulator)
+ `MMC1_SHIFT_COUNT` global pair, and the 4 internal registers as their own
globals (`MMC1_CTRL`, `MMC1_CHR0`, `MMC1_CHR1`, `MMC1_PRG`), with the PRG/CHR
read paths branching on `MMC1_CTRL`'s decoded mode bits to pick which physical
bank(s) map into the CPU/PPU windows.

Full bit-level register layouts, decoded via the same `floor`/`mod`
bit-field-extraction approach as the PPU's loopy registers (not lookup tables —
these are infrequent register writes, not a hot per-pixel path, so plain
arithmetic is fine), are documented as they're implemented in
[`mapper_specs.md`](mapper_specs.md).

### 6.8 Adding future mappers

Because every mapper implementation only needs to provide a `mapperN_read` /
`mapperN_write` pair conforming to the same `bus`-level contract (given an
address, produce a value / apply a bank-switch side effect), adding mapper 4
(MMC3, the next most-common after these 4), mapper 7 (AxROM), etc. later is a
matter of writing one more self-contained generator function in the Python build
script and adding one more branch to the top-level `mapper_read`/`mapper_write`
dispatch blocks — no rework of the CPU, PPU, or bus code is needed. This
extensibility was a deliberate structural requirement from the start (see the
original task spec), not an afterthought.

---

## 7. Putting it together — the main loop

Pseudocode for the steady-state run loop (implemented for real in Phase 8):

```
on green flag:
  run RESET sequence (load PC from $FFFC/$FFFD, SP -= 3, set I flag)
  forever:
    execute one CPU instruction at PC (fetch opcode byte via bus_read,
      dispatch to the matching opcode custom block, which itself performs
      the addressing-mode fetch, the operation, and returns its cycle count)
    add that cycle count × 3 to PPU_CYCLE_DEBT
    while PPU_CYCLE_DEBT > 0:
      advance PPU by 1 cycle (advance PPU_CYCLE; on wrap, advance PPU_SCANLINE;
        during visible scanlines, render this cycle's pixel(s) into the
        framebuffer; at scanline 241 cycle 1, set vblank flag and NMI_PENDING
        if enabled; at scanline 261, clear vblank/sprite flags and reload
        scroll-Y bits from t into v)
      PPU_CYCLE_DEBT -= 1
    if NMI_PENDING: service NMI (push PC/P, jump through $FFFA/$FFFB), clear NMI_PENDING
    else if IRQ_PENDING and FLAG_I == 0: service IRQ (push PC/P, jump through $FFFE/$FFFF)
    once per PPU frame boundary (scanline wraps past 261 back to 0):
      flush the framebuffer to the screen via Pen (see 4.8)
      poll keyboard state into the joypad shift registers ($4016/$4017 reads)
```

The framebuffer flush is deliberately *not* done pixel-by-pixel as rendering
happens, both for correctness (mid-scanline pen operations would be far slower
than Scratch can sustain at anything resembling 60fps) and to decouple "PPU
simulates a pixel" (writes to the framebuffer list) from "Scratch actually draws
something on screen" (a batched Pen pass) — see
[`rendering_perf_notes.md`](rendering_perf_notes.md) for the specific batching
scheme once it's implemented and measured.

---

## 8. Known/expected v1 limitations (tracked honestly, not hidden)

- Only mappers 0, 1, 2, 3 (NROM, MMC1, UxROM, CNROM) — covers a large fraction of
  early-to-mid NES library but not everything (no MMC3, MMC5, etc. yet).
- APU is silent (register writes accepted, no sound synthesis).
- No illegal/undocumented 6502 opcodes.
- PAL timing not implemented (NTSC only — 60Hz/262-scanline timing).
- Sub-instruction (mid-opcode) CPU/PPU interleaving is not modeled — timing
  granularity is per-instruction, which is correct for the vast majority of
  games but can differ from real hardware in a handful of cycle-exact-dependent
  effects (e.g. certain raster-timing tricks timed to a specific CPU cycle
  *within* an instruction rather than at an instruction boundary).
- Four-screen VRAM mirroring (rare, needs extra cartridge RAM) not implemented;
  only horizontal/vertical/single-screen mirroring modes are.

This list will be kept up to date in the top-level README as phases complete.
