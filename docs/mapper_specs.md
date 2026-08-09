# Mapper implementation notes (KittyNES)

Implemented inside `code/build_core.py`'s `phase2_bus()`, dispatched from
`mapper_read %s` / `mapper_write %s %s` on the global `MAPPER` variable.
Verified by `code/test_mappers.py` (run via `interp.py` against the real
generated block graph, same rigor as `code/test_cpu.py`).

All PRG and CHR banking is expressed through a single **fine-grained window
model shared by every mapper**, so adding a new board never requires touching
the bus again:

- `P8` — a 4-entry list of **8K PRG bank indices**, one per CPU window:
  `P8[0]`=$8000-$9FFF, `P8[1]`=$A000-$BFFF, `P8[2]`=$C000-$DFFF, `P8[3]`=$E000-$FFFF
- `C1` — an 8-entry list of **1K CHR bank indices**, one per PPU pattern-table
  window: `C1[0]`=$0000-$03FF … `C1[7]`=$1C00-$1FFF

`mapper_read` computes the `PRG` list index as
`P8[(addr - $8000) div 8192] * 8192 + ((addr - $8000) mod 8192)`, wrapped
`mod len(PRG)` as a defensive measure. `chr_read`/`chr_write` compute
`C1[addr div 1024] * 1024 + (addr mod 1024)`, wrapped `mod len(CHR)`.

Coarser boards express their coarser windows in these units through a small
set of shared helper procs (`bank_helpers()` in `build_core.py`), so no
mapper writes `P8`/`C1` by hand:

| Helper | Meaning |
|---|---|
| `bank_prg32 b`   | whole $8000-$FFFF 32K window ← 32K bank `b` |
| `bank_prg16 w b` | 16K window `w` (0=$8000, 1=$C000) ← 16K bank `b` |
| `bank_prg8 w b`  | 8K window `w` (0-3) ← 8K bank `b` |
| `bank_chr8 b`    | whole 8K pattern-table space ← 8K bank `b` |
| `bank_chr4 w b`  | 4K window `w` (0=$0000, 1=$1000) ← 4K bank `b` |
| `bank_chr2 w b`  | 2K window `w` (0-3) ← 1K bank `b` and `b+1` |
| `bank_chr1 w b`  | 1K window `w` (0-7) ← 1K bank `b` |

(This replaced an earlier two-variable model — `PRGB0`/`PRGB1` as 16K PRG
windows and `CHRB0`/`CHRB1` as 4K CHR windows — which could not express
MMC3's four 8K PRG banks and eight 1K CHR banks. Those four variables no
longer exist; the fine-grained representation was an original design
requirement finally paid off when mapper 4 landed.)

`ines_loader.py`'s `load_rom_into_emu` sets the *initial* `P8`/`C1` contents
plus `PRGBANKS`/`CHRBANKS`/`MAPPER`/`MIRROR`/`CHRRAM` at load time — none of
the mapper `_write` handlers below rely on power-on defaults they don't set
themselves. The universal default is `P8 = [0, 1, last16*2, last16*2+1]`
(16K bank 0 at $8000, the LAST 16K bank at $C000 — which for a 16K NROM-128
cart correctly degenerates to the mirrored `[0,1,0,1]`) and `C1 = [0..7]`.

## Mapper 0 — NROM

No mapper registers; `mapper_write` for MAPPER=0 falls through all the
`e.IF` branches (none match) as a no-op. PRG banking is whatever the loader
initialized `P8` to: `[0,1,2,3]` for a 32K NROM-256, `[0,1,0,1]` for a 16K
NROM-128 (bank 0 mirrored into both halves). CHR is `C1 = [0..7]`.
Implicitly exercised by every CPU test
(the reset-vector fetch and all instruction fetches go through this path).

## Mapper 2 — UxROM

One write-only register, any address $8000-$FFFF:

```
bank_prg16 0 (value mod PRGBANKS)     # switchable 16K at $8000
bank_prg16 1 (PRGBANKS - 1)           # fixed LAST 16K at $C000
```

A real UxROM board hardwires $C000-$FFFF to the last bank, so the handler now
re-asserts it on every write (it is also the loader's power-on default). In
`P8` terms a 16K bank N occupies windows `[2N, 2N+1]`.

Verified: selecting each of several banks correctly changes $8000-$BFFF
reads while $C000-$FFFF stays fixed; bank values `>= PRGBANKS` wrap via mod
(e.g. writing 99 with 8 banks selects bank `99 % 8 = 3`).

## Mapper 3 — CNROM

One write-only register, any address $8000-$FFFF, selects an 8K CHR bank
(internally eight consecutive 1K `C1` windows):

```
bank_chr8 (value mod max(CHRBANKS, 1))   # -> C1[i] = bank8k*8 + i
```

PRG is fixed (CNROM boards are NROM-128/256 on the PRG side; no PRG register
exists). CHR is typically CHR-ROM (not RAM) on CNROM boards, so `CHRRAM`
should be 0.

**Bug found and fixed during Phase 5 verification:** the divisor-guard used
`e.OR(CHRBANKS, 1)`, but `Emu.OR` wraps Scratch's `operator_or` — **logical**
boolean OR, not a numeric default/clamp. Since any nonzero `CHRBANKS` and the
literal `1` are both truthy, `operator_or` always evaluated to `true`, which
`operator_mod` then casts to `1` — so the divisor was *always* 1 regardless
of the real `CHRBANKS` value, meaning `value mod 1 == 0` always: the CHR
windows could never become anything but bank 0. Fixed by using the same
boolean-coerced-to-number idiom already used in `setnz` for flag values:
`CHRBANKS + (CHRBANKS == 0 ? 1 : 0)` expressed as
`e.ADD(e.V("CHRBANKS"), e.EQ(e.V("CHRBANKS"), 0))` (a real Scratch boolean
reporter dropped into a numeric input slot casts via `Cast.toNumber`, which
maps `true`->1/`false`->0 — this is legitimate Scratch VM behavior, not a
workaround hack).

## Mapper 1 — MMC1 (SxROM)

Five-write serial shift register, same physical register for all of
$8000-$FFFF but the *address range* written to (specifically bits 13-14,
i.e. `(addr / 8192) mod 4`) determines which of 4 internal registers the
accumulated 5-bit value latches into once the 5th write completes:

| Address range | Div-8192-mod-4 | Register     |
|----------------|-----------------|---------------|
| $8000-$9FFF    | 0               | Control (`M1_CTRL`) |
| $A000-$BFFF    | 1               | CHR bank 0 (`M1_CHR0`) |
| $C000-$DFFF    | 2               | CHR bank 1 (`M1_CHR1`) — only used in 4K CHR mode |
| $E000-$FFFF    | 3               | PRG bank (`M1_PRG`) |

Each write's bit 0 shifts into the serial register **LSB-first**:
`M1_SR = (M1_SR / 2, floored) + (value mod 2) * 16` (i.e. new bit goes into
bit 4, existing bits shift right — after 5 writes bit0 of the first write
ends up as bit0 of the final value, bit0 of the 5th/last write ends up as
bit4). `M1_CNT` counts writes 1-5; on the 5th, the target register (from the
table above, computed from the *triggering write's* address) is set to
`M1_SR`, then `M1_SR`/`M1_CNT` reset to 0 and `mmc1_apply` recomputes the
derived state.

**Any write with bit 7 set (value >= 0x80) immediately resets the shift
register** (`M1_SR = 0`, `M1_CNT = 0`) **and forces `M1_CTRL` bits 2-3 to
`11`** (PRG mode 3: $C000 fixed to the last bank, $8000 switchable) —
regardless of how many bits had already been shifted in. This matches real
MMC1 behavior (the "reset" write is how software recovers from a
partial/interrupted write sequence, e.g. if an NMI fires mid-sequence with
non-consecutive writes, which would otherwise corrupt the shift count).

`mmc1_apply` derives, from `M1_CTRL`/`M1_CHR0`/`M1_CHR1`/`M1_PRG`:

- **PRG mode** (`M1_CTRL` bits 2-3): `0`/`1` = 32K mode
  (`bank_prg32 (M1_PRG div 2)`, i.e. the low bit of the 16K bank number is
  ignored); `2` = fix first bank at $8000 (`bank_prg16 0 0`), switch $C000
  via `M1_PRG`; `3` = switch $8000 via `M1_PRG`, fix last bank at $C000 (the
  power-on default, `M1_CTRL = 0x0C`).
- **CHR mode** (`M1_CTRL` bit 4): `0` = single 8K bank
  (`bank_chr8 (M1_CHR0 div 2)`, ignoring `M1_CHR1`); `1` = two independent 4K
  banks (`bank_chr4 0 M1_CHR0`, `bank_chr4 1 M1_CHR1`).
- **Mirroring** (`M1_CTRL` bits 0-1): `0`/`1` = single-screen A/B (`MIRROR`
  2/3), `2` = vertical (`MIRROR` 1), `3` = horizontal (`MIRROR` 0). Note the
  bit-value-to-`MIRROR`-constant mapping is intentionally NOT identity — see
  the `MIRROR` constant convention already established for `nt_index` in
  Phase 2 (0=horizontal, 1=vertical, 2=single A, 3=single B, which is the
  reverse convention from MMC1's own control bits).

Verified by `code/test_mappers.py`: power-on defaults, a PRG-bank select via
the 5-write protocol landing correctly, a CTRL write switching to 4K CHR
mode, independent CHR0/CHR1 bank selects reflected in `ppu_read`, the
bit7-reset behavior (including that it forces PRG mode 3), and that a fresh
5-write sequence works correctly immediately after a reset.

## Mapper 66 — GxROM / MHROM

Added to support a real user ROM ("Super Mario Bros. + Duck Hunt (USA)")
which turned out to use it and rendered as a permanent grey box before this
mapper existed (no dispatch branch meant PRG/CHR reads never returned real
ROM data). Much simpler than MMC1: a single write-only register, any
address $8000-$FFFF (hardware doesn't care about the exact address in that
range — games conventionally use $8000, but this project's implementation
follows the real spec and accepts a write anywhere in the window, verified
by `code/test_mappers.py` writing to $FFF0 and confirming it still takes
effect):

```
7  bit  0
---- ----
xxPP xxCC
  ||   ++- 8K CHR bank  (bits 1-0)
  ++------ 32K PRG bank (bits 5-4)
```

**PRG is the HIGH field and CHR the LOW one.** This document previously
stated it backwards, and the implementation matched the wrong statement — see
`PROGRESS_LOG.md`'s 2026-08-08 entry for the resulting real-ROM rendering bug
and the asymmetric `$10`/`$01` regression checks that now pin the order down.

**Key difference from UxROM/CNROM/MMC1: the bank granularity is whole-window,
not split.** UxROM switches only the $8000-$BFFF half (keeping $C000-$FFFF
fixed to the last bank); MMC1 has an explicit "fixed bank" PRG mode. GxROM
has neither — selecting PRG bank N maps the **entire** $8000-$FFFF 32K
window to that bank, all at once. Implemented as `bank_prg32` +
`bank_chr8`. **No new bus state was needed at all** —
`mapper_read`/`ppu_read`/`chr_read` already read generically off `P8`/`C1`, so GxROM's
`mapper_write` branch is the only new code; everything downstream of it
(bank-window math, mirroring by list length, etc.) was already correct
mapper-agnostic logic from Phase 2.

`ines_loader.py`'s `load_rom_into_emu` has a mapper-66-specific power-on
default (`P8 = [0,1,2,3]`, i.e. 32K bank 0 selected) instead of reusing the
UxROM/CNROM/MMC1 default branch's "last 16K bank at $C000" assumption, since
GxROM has no fixed-last-bank concept for that assumption to apply to.

**Scope note**: the standard/full GxROM register also supports more PRG/CHR
bits for larger carts than the 64K-PRG/16K-CHR case this was built and
tested against; the implementation already uses the full 2-bit PRG / 2-bit
CHR field (not hardcoded to fewer bits), so larger GxROM carts should work
without further changes, though only the smaller size has actually been
tested (both against a synthetic test in `test_mappers.py` and the real
"Super Mario Bros. + Duck Hunt" ROM, which is exactly this 64K/16K size).

Verified by `code/test_mappers.py`: initial (power-on-default) window
reads, a combined PRG+CHR bank-select write updating `P8`/`C1`
correctly and both `bus_read`/`ppu_read` reflecting the new
banks, explicit confirmation that the *entire* $8000-$FFFF window moves
together (not split like UxROM), and that a write via a non-conventional
address in the $8000-$FFFF range still takes effect. See
`docs/real_rom_testing.md` for real-ROM (SMB+Duck Hunt) execution results.

## Mapper 4 — MMC3 (TxROM)

Added to support `Famidash - Huge Man v1.2.8.nes` (mapper 4, 2048K PRG,
256K CHR, NES 2.0 header, battery, four-screen). MMC3 is the most common
NES mapper after NROM/UxROM, so this is the highest-value board to support
after the simple ones. Spec followed: <https://www.nesdev.org/wiki/MMC3>
(fetched at implementation time and followed literally — the mapper-66 bug
came from an incorrectly *recalled* register layout, so recall is no longer
trusted for mapper work).

MMC3 exposes four register *pairs*, selected by address range and by the
**parity of the address** (even vs odd):

| Address | Parity | Function |
|---|---|---|
| $8000-$9FFE | even | Bank select |
| $8001-$9FFF | odd  | Bank data |
| $A000-$BFFE | even | Mirroring |
| $A001-$BFFF | odd  | PRG-RAM protect |
| $C000-$DFFE | even | IRQ latch |
| $C001-$DFFF | odd  | IRQ reload |
| $E000-$FFFE | even | IRQ disable + acknowledge |
| $E001-$FFFF | odd  | IRQ enable |

The implementation dispatches on `(addr div 8192)` (4/5/6/7) and
`(addr mod 2)`, so *any* address in a range works, not just the conventional
one — verified by writing to $9FFE/$9FFF/$BFFE.

### Bank select / bank data

```
7  bit  0
---- ----
CPxx xRRR
||     +++- R0-R7: which bank register the NEXT $8001 write updates
|+--------- PRG mode  (bit 6)
+---------- CHR A12 inversion (bit 7)
```

The bank-data write goes to whichever register the **last** bank-select write
named — the data write's own address carries no register information. State
lives in `M3_SEL` and the 8-entry `M3R` list; `M3_PRGMODE`/`M3_CHRINV` hold
the two mode bits. Every write to either register calls `mmc3_apply`, which
recomputes all of `P8` and `C1` from scratch — so toggling only a mode bit
(no data write) correctly re-lays the windows, as real hardware does.

Value masking: R6/R7 are PRG registers with only 6 address lines, so their
value is taken `mod 64`. R0-R5 are CHR registers and are **not** masked.
R0/R1 select 2K banks and ignore their low bit — that masking happens in
`mmc3_apply` (`floor(R/2)*2`), not at write time, so the register still reads
back the raw written value.

### PRG layout

| Window | PRG mode 0 | PRG mode 1 |
|---|---|---|
| $8000-$9FFF (`P8[0]`) | R6 | second-last 8K bank |
| $A000-$BFFF (`P8[1]`) | R7 | R7 |
| $C000-$DFFF (`P8[2]`) | second-last 8K bank | R6 |
| $E000-$FFFF (`P8[3]`) | last 8K bank | last 8K bank |

$A000 is always R7 and **$E000 is always the last 8K bank in both modes** —
only which of $8000/$C000 holds R6 vs the fixed second-last bank swaps. (The
"last bank" is `PRGBANKS*2 - 1`, since `PRGBANKS` counts 16K banks.)

### CHR layout (A12 inversion)

| Window | inversion 0 | inversion 1 |
|---|---|---|
| $0000-$07FF (`C1[0..1]`) | R0 (2K) | R2, R3 (1K each) |
| $0800-$0FFF (`C1[2..3]`) | R1 (2K) | R4, R5 (1K each) |
| $1000-$17FF (`C1[4..5]`) | R2, R3 (1K each) | R0 (2K) |
| $1800-$1FFF (`C1[6..7]`) | R4, R5 (1K each) | R1 (2K) |

i.e. the inversion bit swaps which half of pattern-table space gets the two
2K banks and which gets the four 1K banks.

### Mirroring

`$A000` even, bit 0 only: **0 = vertical mirroring, 1 = horizontal**
(the NESdev table states this as nametable *arrangement*, which is the
inverse wording — "0: horizontal arrangement (CIRAM A10 = PA10)" *is*
vertical mirroring). Our `MIRROR` convention is the other way round
(0=horizontal, 1=vertical), so the handler stores `1 - (value mod 2)`.

**Four-screen carts ignore this register entirely** — they have their own
extra VRAM and no mirroring to configure. The handler skips the write when
`MIRROR == 4`.

`$A001` (odd) is PRG-RAM protect; the value is stored in `M3_PRGRAMPROT` but
is not currently enforced (PRG-RAM writes always succeed). Harmless for
games that only use it defensively; noted as a limitation.

### Scanline IRQ counter — and its known approximation

Real MMC3 clocks its counter on *filtered PPU A12 rising edges*. During
rendering with a normal background/sprite pattern-table split, that works out
to one clock per scanline; the exact dot it lands on depends on the game's
pattern-table configuration and mid-scanline PPU addressing.

**This emulator's main loop is scanline-granularity** (see `docs/main_loop.md`),
so `run_scanline` calls `mmc3_clock_irq` **once per rendered scanline**, after
that scanline's background and sprite rendering, and only while rendering is
enabled (`P_MASK` bit 3 or 4 — with rendering off there is no A12 activity and
the counter must not clock). The counter logic itself is exact:

```
if counter == 0 or reload_requested:  counter = latch ; reload_requested = 0
else:                                 counter = counter - 1
if counter == 0 and irq_enabled:      IRQ_PENDING = 1
```

so a latch of N fires after N+1 clocks (1 reload + N decrements), and
`$E000` both masks and acknowledges. `IRQ_PENDING` feeds the CPU's existing
IRQ dispatch (respecting the I flag), which was already built in Phase 3.

**What this approximation costs, honestly:** the IRQ fires at a scanline
boundary rather than at the exact dot the real chip would. Games that use
MMC3 IRQs the normal way — trigger a scroll change or bank swap for a status
bar / split screen at scanline N — will behave correctly, because the effect
lands between scanlines either way. Games that rely on the IRQ landing
*mid-scanline* (a raster split partway across a line), or that toggle
rendering mid-frame in ways that change A12 activity, will not be
cycle-accurate. This is the same class of limitation the whole main loop
already has, not a new one specific to MMC3 — but MMC3 games exercise it far
more than the previously-supported mappers did, so it is more likely to be
visible here.

### Verification

`code/test_mmc3.py` (63 checks, all driving the real generated block graph
via `interp.py`): register-pair addressing including non-conventional
addresses; all 8 R registers latching distinct values; R6/R7 6-bit masking
and R0-R5 *not* being masked; both PRG modes' full window layouts plus
`bus_read` confirmation at $8000/$A000/$C000/$E000/$FFFF; both CHR inversion
states' full layouts plus `ppu_read` confirmation at all eight 1K windows;
mode bits toggling layouts with no data write, and `$C0` setting both
independently; mirroring in both directions, via a second address, with only
bit 0 mattering, and being ignored for four-screen carts; `$A001` not
touching mirroring; the IRQ counter's latch/reload/enable/disable/acknowledge
semantics with two different latch values (5 and 2, to catch off-by-one and
hardcoding); no IRQ while disabled; no clocking for non-MMC3 mappers; and
finally the IRQ firing through the **real `run_scanline`** after exactly the
right number of rendered scanlines, and never firing with rendering disabled.

**Every value in that suite is deliberately asymmetric** — R0-R7 all get
distinct values, R0/R1 get odd values so the "ignore low bit" rule is
exercised, and the two PRG modes / two CHR inversion states are checked
against *different* expected layouts. This is a direct response to the
mapper-66 bug that survived 16 checks because every one used the symmetric
value `$11`.

**Not implemented / not verified:** MMC3 submapper variants (MMC6, MMC3A vs
MMC3C IRQ-at-zero-latch differences — a latch of 0 here re-fires every
scanline, matching Sharp MMC3 behavior, not NEC's), PRG-RAM write protection
enforcement, and the A12-filtering timing subtleties described above.
