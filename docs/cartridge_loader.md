# Cartridge (.nes) loader notes (KittyNES)

`code/ines_loader.py` — pure Python, build-time only (not a Scratch block
generator itself; it hands parsed bytes to `Emu` to bake into the project).

## `parse_ines(data: bytes) -> dict`

Parses the 16-byte iNES 1.0 header plus PRG-ROM/CHR-ROM payload:

- `magic` check (`NES\x1A`), raises `INesError` if missing.
- `prg`/`chr`: raw bytes (`chr` is empty if the board has no CHR-ROM —
  `chr_is_ram` is `True` in that case, meaning the board uses 8K of
  CHR-RAM instead, which the loader provisions as blank).
- `mapper`: combines flags6's high nibble (low 4 bits of the mapper number)
  and flags7's high nibble (high 4 bits), matching the standard iNES 1.0
  convention. **NES 2.0 files are not specially detected** — if byte 7's
  low 2 bits equal `10` (marking NES 2.0 format, which repurposes some
  header bytes), this loader silently mis-parses those extra fields; it
  falls back to plain iNES 1.0 interpretation, which is correct for the
  base PRG/CHR/mapper/mirroring fields on essentially all real-world ROMs
  but would miss NES 2.0-only extensions (submappers, PRG-RAM sizes >
  iNES 1.0's range, etc.).
- `mirror`: 0=horizontal, 1=vertical, matching flags6 bit 0 — **unless**
  flags6 bit 3 (four-screen) is set, in which case `mirror = 4`. This
  matches the `MIRROR` global's convention already established by
  `nt_index` in `build_core.py`'s Phase 2 bus code (0/1/2/3 = horizontal/
  vertical/single-A/single-B); four-screen (`4`) falls through
  `nt_index`'s default branch, which is a **known limitation** — real
  four-screen boards need an additional 2KB of VRAM beyond the standard
  2KB `VRAM` list this project provisions, which the loader does NOT
  allocate. Four-screen ROMs will load but mirror incorrectly.
- `trainer`: the 512-byte trainer block if flags6 bit 2 is set, otherwise
  `None`. The loader correctly skips past it before reading PRG data but
  does not do anything else with its contents (real trainers are tiny
  boot-time PRG-RAM patches, rare in practice, and out of scope here).
- Raises `INesError` if the file is too short for its own declared PRG/CHR
  sizes (a structural sanity check, not a full validator).

## `build_synthetic_nes(...) -> bytes`

Constructs a minimal, structurally-valid synthetic `.nes` file in memory
for testing — **no real commercial ROM is used or looked for**, per project
scope. Supports configurable PRG/CHR bank counts, mapper number, mirroring,
battery flag, four-screen flag, trainer presence, and custom fill functions
for PRG/CHR bytes (`prg_fill(bank, offset) -> byte`), which
`code/test_ines_loader.py` uses to bake a deterministic, position-dependent
byte pattern so a wrong-bank-selected or wrong-offset-within-bank bug would
actually be caught (not just "any byte came from the ROM").

**Real-ROM testing is left to the user.** Once you have your own legally-
obtained `.nes` file, call `load_rom_into_emu(e, open(path,'rb').read())`
in place of the synthetic-ROM test and rebuild — the loader itself doesn't
care whether the bytes came from `build_synthetic_nes` or a real file.

## `load_rom_into_emu(e, nes_bytes) -> dict`

Bakes a parsed ROM into an already-`declare_state`-initialized `Emu`
instance:

- `e.set_list_items("PRG", ...)` / `("CHR", ...)` — overwrites the
  placeholder `[0]` lists from `declare_state` with the real ROM bytes (or
  a blank 8K CHR-RAM buffer for CHR-RAM boards).
- Sets `MAPPER`, `MIRROR`, `PRGBANKS`, `CHRBANKS`, `CHRRAM` globals directly
  via `e.proj.stage.variables[...][1] = value` (a build-time default-value
  overwrite, same mechanism the `Emu.var()`-declared globals already use —
  this is NOT a Scratch block/runtime write, it's baking the *initial*
  value the variable holds when the project loads).
- Sets power-on-default banking: `PRGB0=0` (first 16K bank at $8000),
  `PRGB1 = PRGBANKS-1` (last bank fixed at $C000 — matches what
  `docs/mapper_specs.md` already documents UxROM/CNROM as needing a loader
  to set up), `CHRB0=0`, `CHRB1=1` (first two 4K CHR banks, i.e. the whole
  first 8K CHR bank) if there's more than one 4K bank available, else `0`.

**Known limitation, verified by the test suite:** for boards with more
than 8K of CHR-ROM and no mapper that switches CHR banks in response to
gameplay (e.g. a hypothetical 16K-CHR NROM-ish board, which isn't a real
iNES mapper-0 configuration but is possible to construct with
`build_synthetic_nes`), only the first 8K is ever reachable — this matches
real hardware behavior (a board without CHR-bank-switching logic genuinely
can't reach CHR beyond its fixed window) and is not a bug, but is worth
knowing if a synthetic test ROM is built with more CHR than the mapper in
use can actually switch between.

## Verification

`code/test_ines_loader.py`: 22 checks split into (1) pure-Python header
parsing (PRG/CHR sizes, mapper number combining both header bytes including
a high mapper number that needs both nibbles, mirror mode including
four-screen, battery flag, CHR-RAM detection, trainer handling, and that
malformed/truncated files raise `INesError`) and (2) end-to-end baking into
a real Emu build, verified via `bus_read`/`ppu_read` through `interp.py`
against the actual generated block graph (correct bank contents at both
ends of the switchable PRG window and the fixed-last-bank window, correct
CHR contents, and that a CHR-RAM board gets a properly-provisioned blank
8K buffer rather than an empty/missing list).
