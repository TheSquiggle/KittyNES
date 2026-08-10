"""Phase 7: cartridge loader verification. Builds a synthetic .nes file
in-memory (no real ROM needed/looked for -- per project scope, real-ROM
testing is left to the user once they supply one, see docs), parses it,
bakes it into a full Emu build via load_rom_into_emu, and checks the
resulting PRG/CHR list contents and MAPPER/MIRROR/bank globals end-to-end
through interp.py against the real generated block graph (bus_read/
ppu_read), same rigor as the other suites.
"""
import sys
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
from ines_loader import parse_ines, build_synthetic_nes, load_rom_into_emu, INesError
from interp import Interp

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "got=%r want=%r" % (got, want))
    if not ok:
        FAILURES.append(label)


def i_(x):
    return int(x) if isinstance(x, (int, float)) else x


# =====================================================================
# 1) Header parsing correctness (pure Python, no block graph needed)
# =====================================================================
print("--- header parsing ---")
rom = build_synthetic_nes(prg_banks=4, chr_banks=2, mapper=1, mirror=1, battery=True)
parsed = parse_ines(rom)
check("PRG size", len(parsed["prg"]), 4 * 16384)
check("CHR size", len(parsed["chr"]), 2 * 8192)
check("mapper", parsed["mapper"], 1)
check("mirror", parsed["mirror"], 1)
check("battery", parsed["battery"], True)
check("chr_is_ram", parsed["chr_is_ram"], False)

rom_chrram = build_synthetic_nes(prg_banks=2, chr_banks=0, mapper=2, mirror=0)
parsed2 = parse_ines(rom_chrram)
check("CHR-RAM board: chr_is_ram", parsed2["chr_is_ram"], True)
check("CHR-RAM board: chr bytes empty", len(parsed2["chr"]), 0)

rom_4s = build_synthetic_nes(prg_banks=1, chr_banks=1, mapper=4, four_screen=True)
parsed3 = parse_ines(rom_4s)
check("four-screen mirror -> MIRROR=4", parsed3["mirror"], 4)

rom_trainer = build_synthetic_nes(prg_banks=1, chr_banks=1, has_trainer=True)
parsed4 = parse_ines(rom_trainer)
check("trainer present and 512 bytes", len(parsed4["trainer"]), 512)
check("PRG size unaffected by trainer", len(parsed4["prg"]), 16384)

# mapper number combines flags6 high nibble + flags7 high nibble
rom_hi = build_synthetic_nes(prg_banks=1, chr_banks=1, mapper=0x21)
check("high mapper number (0x21) round-trips", parse_ines(rom_hi)["mapper"], 0x21)

try:
    parse_ines(b"not a nes file")
    check("bad magic raises INesError", False, True)
except INesError:
    check("bad magic raises INesError", True, True)

try:
    parse_ines(build_synthetic_nes(prg_banks=2)[:-100])  # truncate PRG data
    check("truncated file raises INesError", False, True)
except INesError:
    check("truncated file raises INesError", True, True)


# =====================================================================
# 2) End-to-end: bake a synthetic ROM into a real Emu build, verify via
# bus_read/ppu_read through interp.py
# =====================================================================
print("\n--- end-to-end load into Emu + bus verification ---")


def prg_fill(bank, off):
    # deterministic, position-dependent so a wrong bank OR wrong offset
    # inside a bank is both detectable, not just "any byte from the ROM"
    return (bank * 7 + off % 251) & 0xFF


def chr_fill(bank, off):
    return (bank * 11 + off % 253) & 0xFF


rom = build_synthetic_nes(prg_banks=4, chr_banks=2, mapper=0, mirror=0,
                           prg_fill=prg_fill, chr_fill=chr_fill)

e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
parsed = load_rom_into_emu(e, rom)

interp = Interp(e.proj, max_steps=5_000_000)

check("MAPPER global baked", i_(interp.vars["MAPPER"]), 0)
check("MIRROR global baked", i_(interp.vars["MIRROR"]), 0)
check("PRGBANKS global baked", i_(interp.vars["PRGBANKS"]), 4)
check("CHRBANKS global baked", i_(interp.vars["CHRBANKS"]), 2)
check("P8 defaults: 16K bank 0 at $8000, LAST 16K bank at $C000",
      [int(v) for v in interp.lists["P8"]], [0, 1, 6, 7])
check("C1 defaults: linear 1K banks 0-7", [int(v) for v in interp.lists["C1"]], list(range(8)))
check("PRG list length", len(interp.lists["PRG"]), 4 * 16384)
check("CHR list length", len(interp.lists["CHR"]), 2 * 8192)


def bus_read(a):
    interp.call_proc_by_name("bus_read %s", {"a": a})
    return i_(interp.vars["RESULT"])


def ppu_read(a):
    interp.call_proc_by_name("ppu_read %s", {"a": a})
    return i_(interp.vars["RESULT"])


check("bus_read $8000 (bank0, offset0)", bus_read(0x8000), prg_fill(0, 0))
check("bus_read $BFFF (bank0, offset 0x3FFF)", bus_read(0xBFFF), prg_fill(0, 0x3FFF))
check("bus_read $C000 (fixed last bank, bank3, offset0)", bus_read(0xC000), prg_fill(3, 0))
check("bus_read $FFFF (bank3, offset 0x3FFF)", bus_read(0xFFFF), prg_fill(3, 0x3FFF))
check("ppu_read $0000 (CHR bank0, offset0)", ppu_read(0x0000), chr_fill(0, 0))
# NROM/no mapper-write means the C1 windows stay at their loader defaults (the
# two 4K halves of the FIRST 8K bank) -- the second 8K bank in a >1-CHR-bank
# ROM is simply unreachable without a mapper (matches real NROM/CNROM boards
# with more CHR than a single window: only bank-switching makes it visible).
check("ppu_read $1FFF (still within first 8K CHR bank, offset 0x1FFF)",
      ppu_read(0x1FFF), chr_fill(0, 0x1FFF))

# ---- CHR-RAM board: CHR list should be provisioned (blank) not empty ----
rom_ram = build_synthetic_nes(prg_banks=1, chr_banks=0, mapper=0)
e2 = Emu("CPU2")
BC.declare_state(e2)
BC.phase1_tables(e2)
BC.phase2_bus(e2)
BC.phase3_cpu(e2)
load_rom_into_emu(e2, rom_ram)
interp2 = Interp(e2.proj, max_steps=1_000_000)
check("CHR-RAM board: CHRRAM flag set", i_(interp2.vars["CHRRAM"]), 1)
check("CHR-RAM board: CHR list provisioned (8K blank)", len(interp2.lists["CHR"]), 8192)


# =====================================================================
# 3) NES 2.0 headers (flags7 bits 2-3 == 2)
# =====================================================================
print("\n--- NES 2.0 header parsing ---")
# An iNES 1.0 file must NOT be misdetected as NES 2.0.
check("iNES 1.0 file is not flagged nes2", parse_ines(
    build_synthetic_nes(prg_banks=2, chr_banks=1, mapper=4))["nes2"], False)

# Deliberately asymmetric: PRG banks 0x104 (260 x 16K = 4160K) vs CHR banks
# 0x203 (515 x 8K). Both exceed the 8-bit iNES 1.0 fields and the two MSB
# nibbles live in opposite halves of byte 9, so a swapped nibble fails loudly.
n2 = build_synthetic_nes(prg_banks=0x104, chr_banks=0x203, mapper=4, nes2=True)
# (that would be a ~8MB synthetic file; check the header math only)
hdr = parse_ines(n2[:16] + bytes(0x104 * 16384 + 0x203 * 8192))
check("NES 2.0 detected", hdr["nes2"], True)
check("NES 2.0 PRG size uses byte 9 low nibble as MSB",
      hdr["prg_size"], 0x104 * 16384)
check("NES 2.0 CHR size uses byte 9 HIGH nibble as MSB",
      hdr["chr_size"], 0x203 * 8192)
check("NES 2.0 prg_banks_16k", hdr["prg_banks_16k"], 0x104)
check("NES 2.0 chr_banks_8k", hdr["chr_banks_8k"], 0x203)

# Mapper number extends to 12 bits via byte 8's low nibble; submapper is the
# high nibble. 0x354 / submapper 6 is asymmetric in every nibble.
n2m = build_synthetic_nes(prg_banks=1, chr_banks=1, mapper=0x354, submapper=6,
                          nes2=True)
hm = parse_ines(n2m)
check("NES 2.0 12-bit mapper number", hm["mapper"], 0x354)
check("NES 2.0 submapper", hm["submapper"], 6)

# Exponent notation: byte 9 nibble == $F means the LSB byte is EEEEEEMM and
# the size is 2^E * (MM*2+1) BYTES. E=17, MM=1 -> 131072*3 = 393216 bytes.
exp_hdr = bytearray(build_synthetic_nes(prg_banks=1, chr_banks=1, nes2=True)[:16])
exp_hdr[4] = (17 << 2) | 1
exp_hdr[9] = (exp_hdr[9] & 0xF0) | 0x0F
exp = parse_ines(bytes(exp_hdr) + bytes(393216 + 8192))
check("NES 2.0 exponent-notation PRG size (2^17 * 3)", exp["prg_size"], 393216)
check("NES 2.0 exponent-notation CHR size unaffected", exp["chr_size"], 8192)

# An absurd exponent must ERROR rather than silently mis-size the ROM.
bad = bytearray(exp_hdr)
bad[4] = (60 << 2) | 0
try:
    parse_ines(bytes(bad) + bytes(8192))
    check("absurd exponent raises INesError", False, True)
except INesError:
    check("absurd exponent raises INesError", True, True)


# =====================================================================
# 4) Four-screen mirroring: 4KB VRAM, each logical nametable its own page
# =====================================================================
print("\n--- four-screen mirroring ---")
rom4s = build_synthetic_nes(prg_banks=2, chr_banks=1, mapper=4, four_screen=True)
e3 = Emu("NES4S")
BC.declare_state(e3)
BC.phase1_tables(e3)
BC.phase2_bus(e3)
p4 = load_rom_into_emu(e3, rom4s)
i3 = Interp(e3.proj, max_steps=5_000_000)
check("four_screen flag parsed", p4["four_screen"], True)
check("MIRROR baked as 4", i_(i3.vars["MIRROR"]), 4)
check("VRAM is 4096 entries (the extra 2KB a four-screen board provides)",
      len(i3.lists["VRAM"]), 4096)


def nt_index(a):
    i3.call_proc_by_name("nt_index %s", {"a": a})
    return i_(i3.vars["RESULT"])


# Each of the four logical nametables gets its OWN physical page. Offsets are
# asymmetric (0x11, 0x22, 0x33, 0x44) so a page/offset mix-up is visible.
for nt, off in enumerate((0x11, 0x22, 0x33, 0x44)):
    check("4-screen: NT%d ($%04X) -> its own page" % (nt, 0x2000 + nt * 0x400 + off),
          nt_index(0x2000 + nt * 0x400 + off), nt * 1024 + off)

# MMC3 power-on defaults for this cart
check("MMC3 loader default P8 (bank0, bank1, 2nd-last, last)",
      [int(v) for v in i3.lists["P8"]], [0, 1, 2, 3])
check("MMC3 loader default M3R (R6=0, R7=1)",
      [int(v) for v in i3.lists["M3R"]], [0, 0, 0, 0, 0, 0, 0, 1])

# Modes 0-3 must behave EXACTLY as before (only the low 2KB is ever used).
for mode, expected in [
        (0, [0, 0, 1024, 1024]),          # horizontal: NT0/1 -> page0, NT2/3 -> page1
        (1, [0, 1024, 0, 1024]),          # vertical:   NT0/2 -> page0, NT1/3 -> page1
        (2, [0, 0, 0, 0]),                # single-screen A
        (3, [1024, 1024, 1024, 1024])]:   # single-screen B
    i3.vars["MIRROR"] = mode
    got = [nt_index(0x2000 + nt * 0x400) for nt in range(4)]
    check("MIRROR mode %d unchanged by the four-screen work" % mode, got, expected)

print("\n%s" % ("ALL CARTRIDGE-LOADER CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
