"""Phase 7: iNES (.nes) cartridge loader.

Pure Python, build-time only: parses a real .nes file's header + PRG/CHR
data and either (a) returns a plain dict for inspection/testing, or (b)
bakes the parsed PRG/CHR bytes directly into an already-built Emu project's
`PRG`/`CHR` lists plus the MAPPER/PRGBANKS/CHRBANKS/MIRROR/CHRRAM globals,
via `Emu.set_list_items` (a build-time list-content overwrite -- NOT a
runtime bus_write sequence; the cartridge's contents are baked into the
.sb3 the same way the bit-op/opcode tables are).

iNES header layout (16 bytes, trainer immediately after if present):
  0-3   "NES\x1A" magic
  4     PRG-ROM size in 16KB units
  5     CHR-ROM size in 8KB units (0 = board uses CHR-RAM instead)
  6     flags6: bit0 mirroring (0=horizontal,1=vertical), bit1 battery-backed
        PRG-RAM, bit2 512-byte trainer present, bit3 four-screen VRAM
        (overrides bit0), bits4-7 mapper number low nibble
  7     flags7: bits4-7 mapper number high nibble; bits 2-3 == 2 means the
        file is NES 2.0 (see below)
  8-15  further NES2.0 / rarely-used iNES fields

NES 2.0 (flags7 bits 2-3 == 2) additionally uses:
  8     bits 0-3 mapper number bits 8-11; bits 4-7 submapper
  9     bits 0-3 PRG-ROM size MSB nibble; bits 4-7 CHR-ROM size MSB nibble.
        A nibble of $F switches that size field to *exponent notation*: the
        LSB byte is then EEEEEEMM, meaning size = 2^EEEEEE * (MM*2+1) BYTES
        (not banks). Handled explicitly below.

MIRROR convention matches nt_index in build_core.py: 0=horizontal,
1=vertical, 2=single-screen-A, 3=single-screen-B, 4=four-screen. VRAM is
4096 entries, and nt_index's four-screen branch gives each of the four
logical nametables its own 1KB physical page.
"""

MAGIC = b"NES\x1a"


class INesError(Exception):
    pass


def parse_ines(data):
    if len(data) < 16 or data[0:4] != MAGIC:
        raise INesError("not a valid iNES file (bad magic/too short)")
    prg_banks = data[4]
    chr_banks = data[5]
    flags6 = data[6]
    flags7 = data[7]

    has_trainer = bool(flags6 & 0x04)
    four_screen = bool(flags6 & 0x08)
    battery = bool(flags6 & 0x02)
    mirror_bit = flags6 & 0x01

    mapper = (flags7 & 0xF0) | (flags6 >> 4)

    nes2 = ((flags7 >> 2) & 0x03) == 2
    submapper = 0
    prg_size_bytes = None
    chr_size_bytes = None
    if nes2:
        byte8 = data[8]
        byte9 = data[9]
        mapper |= (byte8 & 0x0F) << 8
        submapper = (byte8 >> 4) & 0x0F
        prg_msb = byte9 & 0x0F
        chr_msb = (byte9 >> 4) & 0x0F
        if prg_msb == 0x0F:
            prg_size_bytes = _exponent_size(prg_banks, "PRG")
        else:
            prg_banks = (prg_msb << 8) | prg_banks
        if chr_msb == 0x0F:
            chr_size_bytes = _exponent_size(chr_banks, "CHR")
        else:
            chr_banks = (chr_msb << 8) | chr_banks

    if four_screen:
        mirror = 4
    else:
        mirror = 1 if mirror_bit else 0  # 1=vertical, 0=horizontal

    offset = 16
    trainer = None
    if has_trainer:
        trainer = data[offset:offset + 512]
        if len(trainer) != 512:
            raise INesError("trainer flag set but file too short for 512-byte trainer")
        offset += 512

    prg_size = prg_size_bytes if prg_size_bytes is not None else prg_banks * 16384
    prg = data[offset:offset + prg_size]
    if len(prg) != prg_size:
        raise INesError("file too short for declared PRG-ROM size (expected %d, got %d)"
                         % (prg_size, len(prg)))
    offset += prg_size

    chr_size = chr_size_bytes if chr_size_bytes is not None else chr_banks * 8192
    chr_data = data[offset:offset + chr_size]
    if len(chr_data) != chr_size:
        raise INesError("file too short for declared CHR-ROM size (expected %d, got %d)"
                         % (chr_size, len(chr_data)))

    return {
        "prg": prg,
        "chr": chr_data,          # empty bytes if the board uses CHR-RAM
        "chr_is_ram": chr_size == 0,
        "mapper": mapper,
        "submapper": submapper,
        "nes2": nes2,
        "mirror": mirror,
        "four_screen": four_screen,
        # sizes in whole banks, rounded UP so an exponent-notation (or simply
        # non-power-of-two) size still yields a bank count the bus math can use
        "prg_banks_16k": (prg_size + 16383) // 16384,
        "chr_banks_8k": (chr_size + 8191) // 8192,
        "prg_size": prg_size,
        "chr_size": chr_size,
        "battery": battery,
        "trainer": trainer,
    }


def _exponent_size(lsb, which):
    """NES 2.0 exponent-notation ROM size: the size LSB byte is EEEEEEMM,
    giving size = 2^EEEEEE * (MM*2 + 1) BYTES."""
    exponent = (lsb >> 2) & 0x3F
    multiplier = (lsb & 0x03) * 2 + 1
    if exponent > 40:
        raise INesError("NES 2.0 %s exponent-notation size is absurdly large "
                        "(exponent=%d)" % (which, exponent))
    return (1 << exponent) * multiplier


def build_synthetic_nes(prg_banks=2, chr_banks=1, mapper=0, mirror=0,
                         battery=False, four_screen=False, has_trainer=False,
                         prg_fill=None, chr_fill=None, nes2=False, submapper=0):
    """Construct a minimal, structurally-valid synthetic .nes file for
    testing the loader end-to-end without needing a real commercial ROM.
    prg_fill/chr_fill: optional callables (bank_index, offset_in_bank) ->
    byte value; defaults to a deterministic pattern (bank index repeated)
    so the loader test can verify byte-for-byte correctness per bank."""
    if prg_fill is None:
        prg_fill = lambda bank, off: bank & 0xFF
    if chr_fill is None:
        chr_fill = lambda bank, off: (0x80 + bank) & 0xFF

    flags6 = (mapper & 0x0F) << 4
    if mirror == 1:
        flags6 |= 0x01
    if battery:
        flags6 |= 0x02
    if has_trainer:
        flags6 |= 0x04
    if four_screen:
        flags6 |= 0x08
    flags7 = (mapper & 0xF0)

    header = bytearray(16)
    header[0:4] = MAGIC
    header[4] = prg_banks & 0xFF
    header[5] = chr_banks & 0xFF
    header[6] = flags6
    header[7] = flags7
    # bytes 8-15 left as 0 (PRG-RAM size / TV system / unused, iNES 1.0)
    if nes2:
        header[7] = flags7 | 0x08          # bits 2-3 = 0b10 -> NES 2.0
        header[8] = ((mapper >> 8) & 0x0F) | ((submapper & 0x0F) << 4)
        header[9] = ((prg_banks >> 8) & 0x0F) | (((chr_banks >> 8) & 0x0F) << 4)

    out = bytearray(header)
    if has_trainer:
        out += bytes([0xAA] * 512)

    for b in range(prg_banks):
        out += bytes(prg_fill(b, o) for o in range(16384))
    for b in range(chr_banks):
        out += bytes(chr_fill(b, o) for o in range(8192))

    return bytes(out)


def load_rom_into_emu(e, nes_bytes):
    """Parse nes_bytes (an iNES file's raw bytes) and bake it into an
    already-`declare_state`-initialized Emu `e`: overwrites the PRG/CHR
    lists (padding CHR to at least 8192 bytes of CHR-RAM if the board has
    none, since chr_read/chr_write always index into a real list) and sets
    MAPPER/PRGBANKS/CHRBANKS/MIRROR/CHRRAM plus the fine-grained bank-window
    registers P8 (four 8K PRG windows) and C1 (eight 1K CHR windows) to their
    post-load power-on defaults -- matching what mapper_write's various board
    handlers assume a loader has already set up, per docs/mapper_specs.md."""
    parsed = parse_ines(nes_bytes)

    e.set_list_items("PRG", list(parsed["prg"]))
    if parsed["chr_is_ram"]:
        chr_bytes = [0] * 8192  # 8K CHR-RAM, blank
        chr_ram_flag = 1
    else:
        chr_bytes = list(parsed["chr"])
        chr_ram_flag = 0
    e.set_list_items("CHR", chr_bytes)

    prg_banks_16k = max(parsed["prg_banks_16k"], 1)
    chr_banks_8k = max(parsed["chr_banks_8k"], 1)

    e.set_var_value("MAPPER", parsed["mapper"])
    e.set_var_value("MIRROR", parsed["mirror"])
    e.set_var_value("PRGBANKS", prg_banks_16k)
    e.set_var_value("CHRBANKS", chr_banks_8k)
    e.set_var_value("CHRRAM", chr_ram_flag)
    # CHR: eight 1K windows, linear (banks 0-7) at power-on for every board.
    e.set_list_items("C1", list(range(8)))

    last16 = prg_banks_16k - 1
    total8 = prg_banks_16k * 2
    if parsed["mapper"] == 4:
        # MMC3: R6=0 at $8000, R7=1 at $A000, second-last fixed at $C000, last
        # fixed at $E000 (PRG mode 0, CHR inversion off) -- the standard
        # power-on state MMC3 games' reset code assumes before it programs the
        # bank registers itself.
        e.set_list_items("P8", [0, 1, total8 - 2, total8 - 1])
        e.set_list_items("M3R", [0, 0, 0, 0, 0, 0, 0, 1])
        e.set_var_value("M3_SEL", 0)
        e.set_var_value("M3_PRGMODE", 0)
        e.set_var_value("M3_CHRINV", 0)
        e.set_var_value("M3_PRGRAMPROT", 0)
        e.set_var_value("M3_IRQLATCH", 0)
        e.set_var_value("M3_IRQCNT", 0)
        e.set_var_value("M3_IRQRELOAD", 0)
        e.set_var_value("M3_IRQEN", 0)
    elif parsed["mapper"] == 66:
        # GxROM/MHROM: the PRG/CHR windows switch as whole 32K/8K units (one
        # register controls both), so there's no "fixed last bank" the way
        # UxROM/CNROM/MMC1's power-on defaults below assume -- default to
        # register value 0 (bank 0 selected for both PRG and CHR), matching
        # real hardware's typical power-on register state and what the
        # cartridge's own reset code will immediately overwrite via its
        # first $8000-$FFFF write anyway.
        e.set_list_items("P8", [0, 1, 2, 3])   # 32K bank 0 selected
    else:
        # NROM / UxROM / CNROM / MMC1: 16K bank 0 at $8000, LAST 16K bank at
        # $C000. For a 16K (NROM-128) cart last16 == 0, which correctly gives
        # the mirrored [0,1,0,1]; for 32K NROM-256 it gives [0,1,2,3].
        e.set_list_items("P8", [0, 1, last16 * 2, last16 * 2 + 1])

    return parsed
