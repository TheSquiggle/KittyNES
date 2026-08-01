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

e = Emu("CPU")
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
check("PRGB0 defaults to bank 0", i_(interp.vars["PRGB0"]), 0)
check("PRGB1 defaults to last bank", i_(interp.vars["PRGB1"]), 3)
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
# NROM/no mapper-write means CHRB0/CHRB1 stay at their loader defaults (the
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

print("\n%s" % ("ALL CARTRIDGE-LOADER CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
