"""Phase 5: mapper-switching correctness tests, same rigor/approach as
code/test_cpu.py -- call the actual generated custom-block procs (via
interp.py, which walks the real block graph) with controlled PRG/CHR list
contents and check the bus reflects the expected bank after a mapper write.

Covers: NROM (implicit baseline via mapper_read's linear PRGB0/PRGB1 math,
already exercised by test_cpu.py's reset-vector fetch), UxROM (mapper 2),
CNROM (mapper 3), and MMC1 (mapper 1, including the 5-write serial-shift
protocol and the reset-via-bit7 case).
"""
import sys
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
from interp import Interp

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "got=%r want=%r" % (got, want))
    if not ok:
        FAILURES.append(label)


def fresh_interp():
    e = Emu("CPU")
    BC.declare_state(e)
    BC.phase1_tables(e)
    BC.phase2_bus(e)
    interp = Interp(e.proj, max_steps=2_000_000)
    return interp


def i_(x):
    return int(x) if isinstance(x, (int, float)) else x


def bus_read(interp, a):
    interp.call_proc_by_name("bus_read %s", {"a": a})
    return i_(interp.vars["RESULT"])


def bus_write(interp, a, v):
    interp.call_proc_by_name("bus_write %s %s", {"a": a, "v": v})


def ppu_read(interp, a):
    interp.call_proc_by_name("ppu_read %s", {"a": a})
    return i_(interp.vars["RESULT"])


# =====================================================================
# UxROM (mapper 2): 8x 16K PRG banks, each byte = bank id. $8000-$BFFF is
# switchable via bus_write to any $8000-$FFFF address (low bits = bank #);
# $C000-$FFFF stays fixed to whatever PRGB1 was set to (a real loader fixes
# it to the last bank; we set that up explicitly here since Phase 7 doesn't
# exist yet).
# =====================================================================
print("\n--- UxROM (mapper 2) ---")
interp = fresh_interp()
NBANKS = 8
PRG = [0] * (16384 * NBANKS)
for bank in range(NBANKS):
    for i in range(16384):
        PRG[bank * 16384 + i] = bank
interp.lists["PRG"] = PRG
interp.vars["MAPPER"] = 2
interp.vars["PRGBANKS"] = NBANKS
interp.vars["PRGB0"] = 0
interp.vars["PRGB1"] = NBANKS - 1  # fixed last bank, as a real loader would set

check("UxROM initial $8000 bank", bus_read(interp, 0x8000), 0)
check("UxROM initial $C000 bank (fixed last)", bus_read(interp, 0xC000), NBANKS - 1)

bus_write(interp, 0x8000, 5)  # select bank 5 for the switchable window
check("UxROM after select bank5: $8000", bus_read(interp, 0x8000), 5)
check("UxROM after select bank5: $BFFF (still bank5)", bus_read(interp, 0xBFFF), 5)
check("UxROM after select bank5: $C000 unchanged (fixed)", bus_read(interp, 0xC000), NBANKS - 1)
check("UxROM after select bank5: $FFFF unchanged (fixed)", bus_read(interp, 0xFFFF), NBANKS - 1)

bus_write(interp, 0xFFF0, 2)  # bank-select write can be to ANY $8000-$FFFF addr
check("UxROM select via high address: $8000", bus_read(interp, 0x8000), 2)

bus_write(interp, 0x8000, 99)  # out-of-range value must wrap mod PRGBANKS
check("UxROM bank value wraps mod PRGBANKS", bus_read(interp, 0x8000), 99 % NBANKS)


# =====================================================================
# CNROM (mapper 3): CHR is bank-switched in 8K units (= two 4K CHRB0/CHRB1
# banks internally). PRG is fixed (CNROM boards are NROM-128/256 for PRG).
# =====================================================================
print("\n--- CNROM (mapper 3) ---")
interp = fresh_interp()
CHR_BANKS_8K = 4
CHR = [0] * (8192 * CHR_BANKS_8K)
for bank in range(CHR_BANKS_8K):
    for i in range(8192):
        CHR[bank * 8192 + i] = bank
interp.lists["CHR"] = CHR
interp.vars["MAPPER"] = 3
interp.vars["CHRBANKS"] = CHR_BANKS_8K
interp.vars["CHRB0"] = 0
interp.vars["CHRB1"] = 1
interp.vars["CHRRAM"] = 0
# PRG side doesn't matter for this test; give it something so bus_read for
# $8000+ doesn't error (mapper_write's PRG-RAM branch below $8000 is separate).
interp.lists["PRG"] = [0] * 32768
interp.vars["PRGBANKS"] = 2
interp.vars["PRGB0"] = 0
interp.vars["PRGB1"] = 1

check("CNROM initial CHR $0000 bank", ppu_read(interp, 0x0000), 0)
check("CNROM initial CHR $1000 bank", ppu_read(interp, 0x1000), 0)

bus_write(interp, 0x8000, 2)  # select 8K CHR bank 2
check("CNROM after select bank2: $0000", ppu_read(interp, 0x0000), 2)
check("CNROM after select bank2: $1FFF (still bank2)", ppu_read(interp, 0x1FFF), 2)

bus_write(interp, 0xC000, 1)  # select bank 1 (write can be anywhere in $8000+)
check("CNROM after select bank1: $0000", ppu_read(interp, 0x0000), 1)


# =====================================================================
# MMC1 (mapper 1): 5-write serial shift register. Bit 0 of each write goes
# into the shift register LSB-first; on the 5th write the accumulated 5-bit
# value latches into CTRL/CHR0/CHR1/PRG depending on which $8000-$FFFF
# address range (bits 13-14) was written to. A write with bit7 set resets
# the shift register AND forces CTRL bits 2-3 to 11 (PRG mode 3: fix last
# bank at $C000, switch $8000).
# =====================================================================
print("\n--- MMC1 (mapper 1) ---")


def mmc1_write_serial(interp, addr, value5):
    """Perform the real 5-write serial protocol: one bit per bus_write,
    LSB first, all writes targeting the same $8000-$FFFF address range."""
    for i in range(5):
        bit = (value5 >> i) & 1
        bus_write(interp, addr, bit)


interp = fresh_interp()
NPRG16 = 16
PRG = [0] * (16384 * NPRG16)
for bank in range(NPRG16):
    for i in range(16384):
        PRG[bank * 16384 + i] = bank
interp.lists["PRG"] = PRG
NCHR4 = 8
CHR = [0] * (4096 * NCHR4)
for bank in range(NCHR4):
    for i in range(4096):
        CHR[bank * 4096 + i] = bank
interp.lists["CHR"] = CHR
interp.vars["CHRRAM"] = 0
interp.vars["MAPPER"] = 1
interp.vars["PRGBANKS"] = NPRG16
interp.vars["CHRBANKS"] = NCHR4 // 2  # in 8K units, matching mapper's own convention
interp.vars["M1_CTRL"] = 0x0C  # power-on default: PRG mode 3, CHR mode 0
interp.vars["M1_SR"] = 0
interp.vars["M1_CNT"] = 0
interp.vars["M1_CHR0"] = 0
interp.vars["M1_CHR1"] = 0
interp.vars["M1_PRG"] = 0
interp.call_proc_by_name("mmc1_apply")

check("MMC1 power-on PRGB0", i_(interp.vars["PRGB0"]), 0)
check("MMC1 power-on PRGB1 (fixed last)", i_(interp.vars["PRGB1"]), NPRG16 - 1)

# ---- write PRG register (address range 3: $E000-$FFFF) with bank 5 ----
mmc1_write_serial(interp, 0xE000, 5)
check("MMC1 PRG-select bank5: PRGB0", i_(interp.vars["PRGB0"]), 5)
check("MMC1 PRG-select bank5: PRGB1 (still fixed last)", i_(interp.vars["PRGB1"]), NPRG16 - 1)
check("MMC1 PRG-select bank5: bus_read $8000", bus_read(interp, 0x8000), 5)
check("MMC1 PRG-select bank5: bus_read $C000 (fixed)", bus_read(interp, 0xC000), NPRG16 - 1)

# ---- CHR mode 1 (two separate 4K banks): write CTRL (range 0: $8000-$9FFF) ----
mmc1_write_serial(interp, 0x8000, 0x1C)  # PRG mode 3 (bits2-3=11), CHR mode 1 (bit4=1)
check("MMC1 CTRL write latched", i_(interp.vars["M1_CTRL"]), 0x1C)

# ---- select CHR0 = bank 3, CHR1 = bank 6 ----
mmc1_write_serial(interp, 0xA000, 3)  # CHR0 register (range 1: $A000-$BFFF)
mmc1_write_serial(interp, 0xC000, 6)  # CHR1 register (range 2: $C000-$DFFF)
check("MMC1 CHR mode1: CHRB0", i_(interp.vars["CHRB0"]), 3)
check("MMC1 CHR mode1: CHRB1", i_(interp.vars["CHRB1"]), 6)
check("MMC1 CHR mode1: ppu_read $0000 (bank3)", ppu_read(interp, 0x0000), 3)
check("MMC1 CHR mode1: ppu_read $1000 (bank6)", ppu_read(interp, 0x1000), 6)

# ---- reset-via-bit7: any write with bit7 set mid-sequence resets SR and
# forces CTRL bits 2-3 to 11, regardless of how many bits were shifted in ----
interp.vars["M1_SR"] = 0x1F  # pretend some garbage was shifted in
interp.vars["M1_CNT"] = 3
bus_write(interp, 0x8000, 0x80)
check("MMC1 bit7-reset: M1_SR cleared", i_(interp.vars["M1_SR"]), 0)
check("MMC1 bit7-reset: M1_CNT cleared", i_(interp.vars["M1_CNT"]), 0)
check("MMC1 bit7-reset: CTRL bits2-3 forced to 11",
      i_(interp.vars["M1_CTRL"]) & 0x0C, 0x0C)

# ---- a fresh 5-write sequence after the reset should still work normally ----
mmc1_write_serial(interp, 0xE000, 9)
check("MMC1 post-reset PRG-select bank9: PRGB0", i_(interp.vars["PRGB0"]), 9)


# =====================================================================
# GxROM/MHROM (mapper 66): single register, whole-window bank switching.
# Register layout per the NESdev GxROM spec:
#     7  bit  0
#     ---- ----
#     xxPP xxCC
#       ||   ++- 8K CHR bank  (bits 1-0)
#       ++------ 32K PRG bank (bits 5-4)
# The 32K PRG window switches as a whole -- no fixed-last-bank behavior like
# UxROM/MMC1. NOTE the field order: PRG is the HIGH field, CHR the LOW one.
# This was originally implemented backwards, and the bug survived a 16-check
# suite because every check used the value $11, which is SYMMETRIC (PRG=1,
# CHR=1 under either reading) and therefore cannot distinguish them. The
# asymmetric cases below ($10 / $01) are the ones that actually pin the
# field order down -- see the regression note in PROGRESS_LOG.md.
# This is exactly the mapper on "Super Mario Bros. + Duck
# Hunt (USA).nes" (64K PRG = 4x 16K banks = 2x 32K GxROM banks, 16K CHR =
# 2x 8K banks), which is what prompted adding mapper 66 support at all.
# =====================================================================
print("\n--- GxROM/MHROM (mapper 66) ---")
interp = fresh_interp()
NPRG32 = 2  # 64K PRG = 2x 32K banks (4x 16K banks internally)
PRG = [0] * (16384 * NPRG32 * 2)
for bank16 in range(NPRG32 * 2):
    for i in range(16384):
        PRG[bank16 * 16384 + i] = bank16
interp.lists["PRG"] = PRG
NCHR8 = 2  # 16K CHR = 2x 8K banks (4x 4K banks internally)
CHR = [0] * (4096 * NCHR8 * 2)
for bank4 in range(NCHR8 * 2):
    for i in range(4096):
        CHR[bank4 * 4096 + i] = bank4
interp.lists["CHR"] = CHR
interp.vars["MAPPER"] = 66
interp.vars["PRGBANKS"] = NPRG32 * 2  # bus code counts PRGBANKS in 16K units
interp.vars["CHRBANKS"] = NCHR8       # CHRBANKS is in 8K units (matches CNROM's convention)
interp.vars["CHRRAM"] = 0
interp.vars["PRGB0"] = 0
interp.vars["PRGB1"] = 1
interp.vars["CHRB0"] = 0
interp.vars["CHRB1"] = 1

check("GxROM initial: bus_read $8000 (bank0)", bus_read(interp, 0x8000), 0)
check("GxROM initial: bus_read $C000 (still bank0's 2nd 16K half)", bus_read(interp, 0xC000), 1)
check("GxROM initial: ppu_read $0000 (CHR bank0)", ppu_read(interp, 0x0000), 0)
check("GxROM initial: ppu_read $1000 (CHR bank1)", ppu_read(interp, 0x1000), 1)

# select PRG bank 1 (32K bank -> 16K banks 2,3), CHR bank 1 (8K -> 4K banks 2,3):
# register value = PRG_bits(5-4)=1, CHR_bits(1-0)=1 -> 0b00010001 = 0x11
# (symmetric value -- passes under either field order, see header note)
bus_write(interp, 0x8000, 0x11)
check("GxROM after select: PRGB0", i_(interp.vars["PRGB0"]), 2)
check("GxROM after select: PRGB1", i_(interp.vars["PRGB1"]), 3)
check("GxROM after select: bus_read $8000 (bank1's 1st 16K half)", bus_read(interp, 0x8000), 2)
check("GxROM after select: bus_read $C000 (bank1's 2nd 16K half)", bus_read(interp, 0xC000), 3)
check("GxROM after select: whole window switched together (no fixed-last-bank)",
      bus_read(interp, 0xFFFF), 3)
check("GxROM after select: CHRB0", i_(interp.vars["CHRB0"]), 2)
check("GxROM after select: CHRB1", i_(interp.vars["CHRB1"]), 3)
check("GxROM after select: ppu_read $0000 (CHR bank1's 1st 4K half)", ppu_read(interp, 0x0000), 2)
check("GxROM after select: ppu_read $1000 (CHR bank1's 2nd 4K half)", ppu_read(interp, 0x1000), 3)

# a write to ANY address in $8000-$FFFF (not just $8000) takes effect --
# hardware doesn't care about the exact address in that range
bus_write(interp, 0xFFF0, 0x00)  # back to bank 0 for both PRG and CHR
check("GxROM: write via a different address ($FFF0) still works", i_(interp.vars["PRGB0"]), 0)
check("GxROM: write via a different address also updates CHR", i_(interp.vars["CHRB0"]), 0)

# ---- ASYMMETRIC field-order checks (the ones that actually caught the bug) ----
# $10 = 0b0001_0000 -> PRG field (bits 5-4) = 1, CHR field (bits 1-0) = 0.
# Under the WRONG (swapped) reading this would give PRGB0=0 / CHRB0=2, so
# these checks fail loudly if the field order is ever flipped back.
bus_write(interp, 0x8000, 0x10)
check("GxROM $10: PRG field is bits 5-4 -> PRGB0=2", i_(interp.vars["PRGB0"]), 2)
check("GxROM $10: PRGB1=3", i_(interp.vars["PRGB1"]), 3)
check("GxROM $10: CHR field is bits 1-0 -> CHRB0=0", i_(interp.vars["CHRB0"]), 0)
check("GxROM $10: CHRB1=1", i_(interp.vars["CHRB1"]), 1)
check("GxROM $10: bus_read $8000 reads PRG 32K bank 1", bus_read(interp, 0x8000), 2)
check("GxROM $10: ppu_read $0000 reads CHR 8K bank 0", ppu_read(interp, 0x0000), 0)

# $01 = 0b0000_0001 -> PRG field = 0, CHR field = 1 (the exact mirror image).
bus_write(interp, 0x8000, 0x01)
check("GxROM $01: PRG field -> PRGB0=0", i_(interp.vars["PRGB0"]), 0)
check("GxROM $01: PRGB1=1", i_(interp.vars["PRGB1"]), 1)
check("GxROM $01: CHR field -> CHRB0=2", i_(interp.vars["CHRB0"]), 2)
check("GxROM $01: CHRB1=3", i_(interp.vars["CHRB1"]), 3)
check("GxROM $01: bus_read $8000 reads PRG 32K bank 0", bus_read(interp, 0x8000), 0)
check("GxROM $01: ppu_read $0000 reads CHR 8K bank 1", ppu_read(interp, 0x0000), 2)

print("\n%s" % ("ALL MAPPER CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
