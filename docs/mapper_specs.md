# Mapper implementation notes (KittyNES)

Implemented inside `code/build_core.py`'s `phase2_bus()`, dispatched from
`mapper_read %s` / `mapper_write %s %s` on the global `MAPPER` variable.
Verified by `code/test_mappers.py` (run via `interp.py` against the real
generated block graph, same rigor as `code/test_cpu.py`).

All PRG banking is expressed through two global "window" variables:

- `PRGB0` — 16K-bank index currently mapped at CPU $8000-$BFFF
- `PRGB1` — 16K-bank index currently mapped at CPU $C000-$FFFF

`mapper_read` computes the actual `PRG` list index as
`PRGB0_or_1 * 16384 + (addr - window_base)`, then wraps mod `len(PRG)` as a
defensive measure. This is mapper-agnostic; each mapper's `mapper_write` only
needs to update `PRGB0`/`PRGB1` (and `CHRB0`/`CHRB1` for CHR banking, same
scheme but 4K windows: `CHRB0` = $0000-$0FFF, `CHRB1` = $1000-$1FFF).

A cartridge loader (Phase 7, not yet built) is expected to set the *initial*
`PRGB0`/`PRGB1`/`CHRB0`/`CHRB1`/`PRGBANKS`/`CHRBANKS`/`MAPPER` values at load
time — none of the mapper `_write` handlers below set up power-on defaults
for boards where that matters (UxROM/CNROM don't fix `PRGB1` themselves; a
test or the future loader must set it once at startup, matching real UxROM
hardware where $C000 is hardwired to the last bank and never changes).

## Mapper 0 — NROM

No mapper registers; `mapper_write` for MAPPER=0 falls through all the
`e.IF` branches (none match) as a no-op. PRG banking is whatever `PRGB0`/
`PRGB1` were initialized to (typically 0/1 for a 32K ROM, or 0/0 for a 16K
ROM mirrored into both windows). Implicitly exercised by every CPU test
(the reset-vector fetch and all instruction fetches go through this path).

## Mapper 2 — UxROM

One write-only register, any address $8000-$FFFF:

```
PRGB0 = value mod PRGBANKS
```

$C000-$FFFF ($PRGB1$) is fixed and never touched by mapper_write — a real
UxROM board hardwires it to the last bank; our bus code doesn't enforce that
automatically, so callers (tests, and eventually the ROM loader) must set
`PRGB1 = PRGBANKS - 1` once at load time.

Verified: selecting each of several banks correctly changes $8000-$BFFF
reads while $C000-$FFFF stays fixed; bank values `>= PRGBANKS` wrap via mod
(e.g. writing 99 with 8 banks selects bank `99 % 8 = 3`).

## Mapper 3 — CNROM

One write-only register, any address $8000-$FFFF, selects an 8K CHR bank
(internally split into the two 4K `CHRB0`/`CHRB1` windows the bus already
uses):

```
bank4k = (value mod max(CHRBANKS, 1)) * 2
CHRB0 = bank4k
CHRB1 = bank4k + 1
```

PRG is fixed (CNROM boards are NROM-128/256 on the PRG side; no PRG register
exists). CHR is typically CHR-ROM (not RAM) on CNROM boards, so `CHRRAM`
should be 0.

**Bug found and fixed during Phase 5 verification:** the divisor-guard used
`e.OR(CHRBANKS, 1)`, but `Emu.OR` wraps Scratch's `operator_or` — **logical**
boolean OR, not a numeric default/clamp. Since any nonzero `CHRBANKS` and the
literal `1` are both truthy, `operator_or` always evaluated to `true`, which
`operator_mod` then casts to `1` — so the divisor was *always* 1 regardless
of the real `CHRBANKS` value, meaning `value mod 1 == 0` always: `CHRB0`
could never become anything but bank 0. Fixed by using the same
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

- **PRG mode** (`M1_CTRL` bits 2-3): `0`/`1` = 32K mode (`PRGB0`/`PRGB1` set
  to a consecutive even/odd pair from `M1_PRG / 2 * 2`); `2` = fix first bank
  at $8000, switch $C000 via `M1_PRG`; `3` = switch $8000 via `M1_PRG`, fix
  last bank at $C000 (the power-on default, `M1_CTRL = 0x0C`).
- **CHR mode** (`M1_CTRL` bit 4): `0` = single 8K bank (`CHRB0`/`CHRB1` from
  `M1_CHR0 / 2 * 2`, ignoring `M1_CHR1`); `1` = two independent 4K banks
  (`CHRB0 = M1_CHR0`, `CHRB1 = M1_CHR1` directly).
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
PRG bank (32K) = value & 0x03    (bits 0-1)
CHR bank (8K)  = (value >> 4) & 0x03   (bits 4-5)
```

**Key difference from UxROM/CNROM/MMC1: the bank granularity is whole-window,
not split.** UxROM switches only the $8000-$BFFF half (keeping $C000-$FFFF
fixed to the last bank); MMC1 has an explicit "fixed bank" PRG mode. GxROM
has neither — selecting PRG bank N maps the **entire** $8000-$FFFF 32K
window to that bank, all at once. Implemented by writing straight into the
existing `PRGB0`/`PRGB1` (16K-window) bus variables as a consecutive pair —
`PRGB0 = bank*2`, `PRGB1 = bank*2+1` — the same "32K bank = two consecutive
16K banks" trick MMC1's own 32K PRG mode already uses in `mmc1_apply`. CHR
banking is the same idea one level down: `CHRB0 = chr_bank*2`, `CHRB1 =
chr_bank*2+1` (an 8K CHR bank = two consecutive 4K banks). **No new bus
state was needed at all** — `mapper_read`/`ppu_read`/`chr_read` already
read generically off `PRGB0`/`PRGB1`/`CHRB0`/`CHRB1`, so GxROM's
`mapper_write` branch is the only new code; everything downstream of it
(bank-window math, mirroring by list length, etc.) was already correct
mapper-agnostic logic from Phase 2.

`ines_loader.py`'s `load_rom_into_emu` has a mapper-66-specific power-on
default (bank 0 selected for both PRG and CHR) instead of reusing the
UxROM/CNROM/MMC1 default branch's "PRGB1 = last bank" assumption, since
GxROM has no fixed-last-bank concept for that assumption to apply to.

**Scope note**: the standard/full GxROM register also supports more PRG/CHR
bits for larger carts than the 64K-PRG/16K-CHR case this was built and
tested against; the implementation already uses the full 2-bit PRG / 2-bit
CHR field (not hardcoded to fewer bits), so larger GxROM carts should work
without further changes, though only the smaller size has actually been
tested (both against a synthetic test in `test_mappers.py` and the real
"Super Mario Bros. + Duck Hunt" ROM, which is exactly this 64K/16K size).

Verified by `code/test_mappers.py`: initial (power-on-default) window
reads, a combined PRG+CHR bank-select write updating `PRGB0`/`PRGB1`/
`CHRB0`/`CHRB1` correctly and both `bus_read`/`ppu_read` reflecting the new
banks, explicit confirmation that the *entire* $8000-$FFFF window moves
together (not split like UxROM), and that a write via a non-conventional
address in the $8000-$FFFF range still takes effect. See
`docs/real_rom_testing.md` for real-ROM (SMB+Duck Hunt) execution results.
