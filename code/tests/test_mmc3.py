"""MMC3 (mapper 4) verification -- same rigor and approach as test_mappers.py:
every check drives the REAL generated block graph through interp.py, never a
Python re-derivation of the logic.

Spec source: https://www.nesdev.org/wiki/MMC3 (fetched, not recalled).

  $8000-$9FFE even  bank select   bits 0-2 = target register R0-R7
                                  bit 6    = PRG mode
                                  bit 7    = CHR A12 inversion
  $8001-$9FFF odd   bank data     -> the selected R register
  $A000-$BFFE even  mirroring     bit 0: 0 = vertical, 1 = horizontal
  $A001-$BFFF odd   PRG-RAM protect
  $C000-$DFFE even  IRQ latch
  $C001-$DFFF odd   IRQ reload
  $E000-$FFFE even  IRQ disable + acknowledge
  $E001-$FFFF odd   IRQ enable

  PRG mode 0: $8000=R6  $A000=R7  $C000=2nd-last  $E000=last
  PRG mode 1: $8000=2nd-last  $A000=R7  $C000=R6  $E000=last
  CHR inv 0 : 2K R0 @ $0000, 2K R1 @ $0800, 1K R2-R5 @ $1000-$1FFF
  CHR inv 1 : 2K R0 @ $1000, 2K R1 @ $1800, 1K R2-R5 @ $0000-$0FFF

EVERY value used below is deliberately ASYMMETRIC (all eight R registers get
distinct, non-interchangeable values; the two PRG modes and two CHR inversion
states are checked against *different* expected layouts). A swapped field, a
swapped mode, or a swapped inversion half fails loudly. This is a direct
response to the mapper-66 bug that survived a 16-check suite because every
check used the symmetric value $11 -- see PROGRESS_LOG.md.
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


def i_(x):
    return int(x) if isinstance(x, (int, float)) else x


def p8(it):
    return [int(v) for v in it.lists["P8"]]


def c1(it):
    return [int(v) for v in it.lists["C1"]]


# ---------------------------------------------------------------------
# A cart big enough that every bank number used below is distinct and real:
# 32x 16K PRG banks (= 64x 8K banks, so second-last = 62, last = 63) and
# 64x 1K CHR banks. Each PRG 8K bank is filled with its own 8K-bank index and
# each CHR 1K bank with its own 1K-bank index, so a bus read tells you exactly
# which bank a window resolved to.
# ---------------------------------------------------------------------
PRG16 = 32
PRG8 = PRG16 * 2          # 64 8K banks: 0..63
CHR1K = 64                # 64 1K banks: 0..63
SECOND_LAST = PRG8 - 2    # 62
LAST = PRG8 - 1           # 63


def fresh(prg_mode=0, chr_inv=0):
    e = Emu("NES")
    BC.declare_state(e)
    BC.phase1_tables(e)
    BC.phase2_bus(e)
    it = Interp(e.proj, max_steps=5_000_000)
    it.lists["PRG"] = [b // 8192 for b in range(8192 * PRG8)]
    it.lists["CHR"] = [b // 1024 for b in range(1024 * CHR1K)]
    it.vars["MAPPER"] = 4
    it.vars["PRGBANKS"] = PRG16
    it.vars["CHRBANKS"] = CHR1K // 8
    it.vars["CHRRAM"] = 0
    it.vars["MIRROR"] = 0
    it.lists["M3R"] = [0] * 8
    it.vars["M3_SEL"] = 0
    it.vars["M3_PRGMODE"] = prg_mode
    it.vars["M3_CHRINV"] = chr_inv
    it.vars["M3_IRQLATCH"] = 0
    it.vars["M3_IRQCNT"] = 0
    it.vars["M3_IRQRELOAD"] = 0
    it.vars["M3_IRQEN"] = 0
    it.vars["IRQ_PENDING"] = 0
    return it


def bus_write(it, a, v):
    it.call_proc_by_name("bus_write %s %s", {"a": a, "v": v})


def bus_read(it, a):
    it.call_proc_by_name("bus_read %s", {"a": a})
    return i_(it.vars["RESULT"])


def ppu_read(it, a):
    it.call_proc_by_name("ppu_read %s", {"a": a})
    return i_(it.vars["RESULT"])


def set_reg(it, reg, value, prg_mode=0, chr_inv=0):
    """Do a real bank-select + bank-data write pair, exactly as a game does."""
    sel = (reg & 7) | (0x40 if prg_mode else 0) | (0x80 if chr_inv else 0)
    bus_write(it, 0x8000, sel)   # even address -> bank select
    bus_write(it, 0x8001, value)  # odd address -> bank data


# Distinct, mutually non-interchangeable values for R0..R7. R0/R1 are given
# ODD values on purpose so the "2K banks ignore the low bit" rule is exercised
# (R0=0x0B -> 2K bank starting at 1K bank 10; R1=0x15 -> starts at 20).
R = [0x0B, 0x15, 0x21, 0x22, 0x23, 0x24, 0x11, 0x1A]
R0_EVEN, R1_EVEN = R[0] & ~1, R[1] & ~1   # 10, 20


# =====================================================================
print("\n--- bank select / bank data register pair ---")
# =====================================================================
it = fresh()
for reg in range(8):
    set_reg(it, reg, R[reg])
check("all 8 R registers latched to their own distinct values",
      [int(v) for v in it.lists["M3R"]], R)

# An odd-address write with NO preceding select must go to whatever register
# was last selected -- it is not addressed by the write address itself.
bus_write(it, 0x8000, 3)      # select R3
bus_write(it, 0x9FFF, 0x37)   # odd address anywhere in $8001-$9FFF = bank data
check("bank data honours the LAST bank-select, not the write address",
      int(it.lists["M3R"][3]), 0x37)
check("bank select via $9FFE (even) also works",
      (bus_write(it, 0x9FFE, 5), bus_write(it, 0x8001, 0x29),
       int(it.lists["M3R"][5]))[2], 0x29)
set_reg(it, 3, R[3])  # restore

# R6/R7 have only 6 address lines -> the top two bits of the value are dropped.
set_reg(it, 6, 0xC5)   # 0b1100_0101 -> 0b00_0101 = 5
check("R6 masks value to 6 bits (0xC5 -> 5)", int(it.lists["M3R"][6]), 5)
set_reg(it, 7, 0xFF)
check("R7 masks value to 6 bits (0xFF -> 63)", int(it.lists["M3R"][7]), 63)
# R0-R5 are CHR registers and must NOT be masked to 6 bits.
set_reg(it, 2, 0xC5)
check("R2 (CHR) is NOT masked to 6 bits", int(it.lists["M3R"][2]), 0xC5)


# =====================================================================
print("\n--- PRG mode 0: R6 @ $8000, R7 @ $A000, 2nd-last @ $C000, last @ $E000 ---")
# =====================================================================
it = fresh()
for reg in range(8):
    set_reg(it, reg, R[reg], prg_mode=0, chr_inv=0)
check("PRG mode 0 window layout", p8(it), [R[6], R[7], SECOND_LAST, LAST])
# R6=0x11=17 and R7=0x1A=26 are distinct and neither equals 62 or 63, so this
# fails if ANY pair of the four windows is swapped.
check("PRG mode 0: $8000 reads R6's bank", bus_read(it, 0x8000), R[6])
check("PRG mode 0: $9FFF still R6's bank", bus_read(it, 0x9FFF), R[6])
check("PRG mode 0: $A000 reads R7's bank", bus_read(it, 0xA000), R[7])
check("PRG mode 0: $BFFF still R7's bank", bus_read(it, 0xBFFF), R[7])
check("PRG mode 0: $C000 reads the FIXED second-last bank", bus_read(it, 0xC000), SECOND_LAST)
check("PRG mode 0: $E000 reads the FIXED last bank", bus_read(it, 0xE000), LAST)
check("PRG mode 0: $FFFF (reset vector page) is the last bank", bus_read(it, 0xFFFF), LAST)


# =====================================================================
print("\n--- PRG mode 1: 2nd-last @ $8000, R7 @ $A000, R6 @ $C000, last @ $E000 ---")
# =====================================================================
it = fresh()
for reg in range(8):
    set_reg(it, reg, R[reg], prg_mode=1, chr_inv=0)
check("PRG mode 1 window layout", p8(it), [SECOND_LAST, R[7], R[6], LAST])
check("PRG mode 1: $8000 is now the FIXED second-last bank", bus_read(it, 0x8000), SECOND_LAST)
check("PRG mode 1: $A000 is STILL R7 (unaffected by the mode bit)",
      bus_read(it, 0xA000), R[7])
check("PRG mode 1: $C000 is now R6's switchable bank", bus_read(it, 0xC000), R[6])
check("PRG mode 1: $E000 is STILL the last bank (fixed in BOTH modes)",
      bus_read(it, 0xE000), LAST)

# Flipping only the mode bit (no bank-data write) must re-lay the windows.
bus_write(it, 0x8000, 0x00)   # select R0, PRG mode 0, no inversion
check("clearing the PRG-mode bit swaps $8000/$C000 back", p8(it),
      [R[6], R[7], SECOND_LAST, LAST])
bus_write(it, 0x8000, 0x40)   # mode 1 again
check("setting the PRG-mode bit swaps them again", p8(it),
      [SECOND_LAST, R[7], R[6], LAST])


# =====================================================================
print("\n--- CHR A12 inversion = 0: 2K R0/R1 low, 1K R2-R5 high ---")
# =====================================================================
it = fresh()
for reg in range(8):
    set_reg(it, reg, R[reg], prg_mode=0, chr_inv=0)
expect0 = [R0_EVEN, R0_EVEN + 1, R1_EVEN, R1_EVEN + 1, R[2], R[3], R[4], R[5]]
check("CHR inv 0 window layout", c1(it), expect0)
check("CHR inv 0: $0000 = R0's 2K bank (low bit of R0 ignored: 0x0B -> 10)",
      ppu_read(it, 0x0000), R0_EVEN)
check("CHR inv 0: $0400 = second half of R0's 2K bank", ppu_read(it, 0x0400), R0_EVEN + 1)
check("CHR inv 0: $0800 = R1's 2K bank (0x15 -> 20)", ppu_read(it, 0x0800), R1_EVEN)
check("CHR inv 0: $0C00 = second half of R1's 2K bank", ppu_read(it, 0x0C00), R1_EVEN + 1)
check("CHR inv 0: $1000 = R2", ppu_read(it, 0x1000), R[2])
check("CHR inv 0: $1400 = R3", ppu_read(it, 0x1400), R[3])
check("CHR inv 0: $1800 = R4", ppu_read(it, 0x1800), R[4])
check("CHR inv 0: $1C00 = R5", ppu_read(it, 0x1C00), R[5])


# =====================================================================
print("\n--- CHR A12 inversion = 1: 1K R2-R5 low, 2K R0/R1 high (exact swap) ---")
# =====================================================================
it = fresh()
for reg in range(8):
    set_reg(it, reg, R[reg], prg_mode=0, chr_inv=1)
expect1 = [R[2], R[3], R[4], R[5], R0_EVEN, R0_EVEN + 1, R1_EVEN, R1_EVEN + 1]
check("CHR inv 1 window layout", c1(it), expect1)
check("CHR inv 1: $0000 = R2 (was R0's 2K bank when inv=0)", ppu_read(it, 0x0000), R[2])
check("CHR inv 1: $0400 = R3", ppu_read(it, 0x0400), R[3])
check("CHR inv 1: $0800 = R4", ppu_read(it, 0x0800), R[4])
check("CHR inv 1: $0C00 = R5", ppu_read(it, 0x0C00), R[5])
check("CHR inv 1: $1000 = R0's 2K bank", ppu_read(it, 0x1000), R0_EVEN)
check("CHR inv 1: $1400 = second half of R0's 2K bank", ppu_read(it, 0x1400), R0_EVEN + 1)
check("CHR inv 1: $1800 = R1's 2K bank", ppu_read(it, 0x1800), R1_EVEN)
check("CHR inv 1: $1C00 = second half of R1's 2K bank", ppu_read(it, 0x1C00), R1_EVEN + 1)
check("inv 0 and inv 1 layouts are genuinely different (not a symmetric test)",
      expect0 != expect1, True)

# Toggling only the inversion bit must re-lay the CHR windows with no data write.
bus_write(it, 0x8000, 0x00)
check("clearing the inversion bit restores the inv-0 layout", c1(it), expect0)
bus_write(it, 0x8000, 0x80)
check("setting the inversion bit restores the inv-1 layout", c1(it), expect1)

# The PRG-mode and CHR-inversion bits are independent fields of the same byte:
# $C0 sets both. If they were ever conflated this would show up here.
bus_write(it, 0x8000, 0xC0)
check("$C0 sets BOTH mode bits: PRG layout is mode 1", p8(it),
      [SECOND_LAST, R[7], R[6], LAST])
check("$C0 sets BOTH mode bits: CHR layout is inv 1", c1(it), expect1)


# =====================================================================
print("\n--- mirroring ($A000 even) ---")
# =====================================================================
it = fresh()
it.vars["MIRROR"] = 0
bus_write(it, 0xA000, 0)
check("mirroring bit 0 = 0 -> VERTICAL (MIRROR=1)", i_(it.vars["MIRROR"]), 1)
bus_write(it, 0xA000, 1)
check("mirroring bit 0 = 1 -> HORIZONTAL (MIRROR=0)", i_(it.vars["MIRROR"]), 0)
bus_write(it, 0xBFFE, 0)   # any even address in the range
check("mirroring write via $BFFE also works", i_(it.vars["MIRROR"]), 1)
bus_write(it, 0xA000, 0xFE)  # only bit 0 matters; 0xFE has bit0 = 0
check("only bit 0 of the mirroring value is used ($FE -> vertical)",
      i_(it.vars["MIRROR"]), 1)
bus_write(it, 0xA000, 0xFF)
check("only bit 0 of the mirroring value is used ($FF -> horizontal)",
      i_(it.vars["MIRROR"]), 0)

# Four-screen carts have their own VRAM; the mirroring register is ignored.
it.vars["MIRROR"] = 4
bus_write(it, 0xA000, 1)
check("four-screen cart IGNORES the mirroring register", i_(it.vars["MIRROR"]), 4)
bus_write(it, 0xA000, 0)
check("four-screen cart ignores it in the other direction too",
      i_(it.vars["MIRROR"]), 4)

# The ODD address in the same range is PRG-RAM protect and must NOT touch mirroring.
it.vars["MIRROR"] = 0
bus_write(it, 0xA001, 0xC0)
check("$A001 (odd) is PRG-RAM protect, leaves MIRROR alone", i_(it.vars["MIRROR"]), 0)
check("$A001 value stored in the PRG-RAM protect register",
      i_(it.vars["M3_PRGRAMPROT"]), 0xC0)


# =====================================================================
print("\n--- IRQ counter: latch / reload / enable / disable ---")
# =====================================================================
def clock(it, n=1):
    for _ in range(n):
        it.call_proc_by_name("mmc3_clock_irq")


it = fresh()
bus_write(it, 0xC000, 5)     # even: IRQ latch = 5
check("IRQ latch stored", i_(it.vars["M3_IRQLATCH"]), 5)
bus_write(it, 0xC001, 0)     # odd: reload
check("IRQ reload clears the counter", i_(it.vars["M3_IRQCNT"]), 0)
check("IRQ reload sets the reload flag", i_(it.vars["M3_IRQRELOAD"]), 1)
bus_write(it, 0xE001, 0)     # odd: enable IRQs
check("IRQ enable flag set", i_(it.vars["M3_IRQEN"]), 1)

# First clock consumes the reload: counter <- latch (5), reload flag cleared.
clock(it)
check("clock 1 reloads counter from latch", i_(it.vars["M3_IRQCNT"]), 5)
check("clock 1 clears the reload flag", i_(it.vars["M3_IRQRELOAD"]), 0)
check("clock 1 does not fire an IRQ", i_(it.vars["IRQ_PENDING"]), 0)
# Then it decrements once per clock: 4,3,2,1,0 -- IRQ on the 5th decrement.
for n in (4, 3, 2, 1):
    clock(it)
    check("counter decrements to %d, no IRQ yet" % n,
          (i_(it.vars["M3_IRQCNT"]), i_(it.vars["IRQ_PENDING"])), (n, 0))
clock(it)
check("counter reaches 0 after latch+1 clocks -> IRQ_PENDING set",
      (i_(it.vars["M3_IRQCNT"]), i_(it.vars["IRQ_PENDING"])), (0, 1))

# Latch 5 with an asymmetric value: total clocks to first IRQ must be 6
# (1 reload + 5 decrements), not 5 and not 7.
it2 = fresh()
bus_write(it2, 0xC000, 5)
bus_write(it2, 0xC001, 0)
bus_write(it2, 0xE001, 0)
clock(it2, 5)
check("after exactly 5 clocks the IRQ has NOT fired yet", i_(it2.vars["IRQ_PENDING"]), 0)
clock(it2)
check("after exactly 6 clocks (1 reload + 5 decrements) it HAS fired",
      i_(it2.vars["IRQ_PENDING"]), 1)

# A different latch value must give a proportionally different count -- this
# is the asymmetry check that a hardcoded/off-by-one reload would fail.
it3 = fresh()
bus_write(it3, 0xC000, 2)
bus_write(it3, 0xC001, 0)
bus_write(it3, 0xE001, 0)
clock(it3, 2)
check("latch=2: not fired after 2 clocks", i_(it3.vars["IRQ_PENDING"]), 0)
clock(it3)
check("latch=2: fired after 3 clocks", i_(it3.vars["IRQ_PENDING"]), 1)

# Disable ($E000, even) both masks and acknowledges.
bus_write(it3, 0xE000, 0)
check("$E000 disables IRQs", i_(it3.vars["M3_IRQEN"]), 0)
check("$E000 also ACKNOWLEDGES the pending IRQ", i_(it3.vars["IRQ_PENDING"]), 0)
# While disabled the counter still runs but must never set IRQ_PENDING.
bus_write(it3, 0xC000, 1)
bus_write(it3, 0xC001, 0)
clock(it3, 10)
check("while disabled, 10 more clocks raise NO IRQ", i_(it3.vars["IRQ_PENDING"]), 0)
bus_write(it3, 0xE001, 0)   # re-enable
clock(it3, 2)
check("after re-enabling, the counter fires again", i_(it3.vars["IRQ_PENDING"]), 1)

# The counter is only clocked for mapper 4 -- a non-MMC3 cart must be untouched.
it4 = fresh()
bus_write(it4, 0xC000, 1)
bus_write(it4, 0xC001, 0)
bus_write(it4, 0xE001, 0)
it4.vars["MAPPER"] = 3
clock(it4, 10)
check("mmc3_clock_irq is a no-op for a non-MMC3 mapper",
      i_(it4.vars["IRQ_PENDING"]), 0)


# =====================================================================
print("\n--- IRQ actually fires through the real main loop (scanline clocking) ---")
# =====================================================================
# Build a FULL core (CPU + PPU + main loop) so run_scanline's own MMC3 clock
# call is what advances the counter -- not the unit-level clock() above.
e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)
full = Interp(e.proj, max_steps=200_000_000)
full.lists["PRG"] = [0xEA] * (8192 * PRG8)   # NOP everywhere
full.lists["CHR"] = [0] * (1024 * CHR1K)
full.vars["MAPPER"] = 4
full.vars["PRGBANKS"] = PRG16
full.vars["CHRBANKS"] = CHR1K // 8
full.vars["CHRRAM"] = 0
full.vars["MIRROR"] = 0
full.lists["M3R"] = [0] * 8
for name, v in [("M3_SEL", 0), ("M3_PRGMODE", 0), ("M3_CHRINV", 0),
                ("M3_IRQLATCH", 0), ("M3_IRQCNT", 0), ("M3_IRQRELOAD", 0),
                ("M3_IRQEN", 0), ("IRQ_PENDING", 0)]:
    full.vars[name] = v
full.call_proc_by_name("nes_init")
full.vars["FI"] = 1          # mask CPU IRQ dispatch so IRQ_PENDING stays observable
full.vars["P_MASK"] = 0x18   # background + sprites enabled -> A12 toggles
full.vars["P_CTRL"] = 0
full.vars["SCANLINE"] = 0
bus_write(full, 0xC000, 3)   # latch 3 (asymmetric: not 0, not 1)
bus_write(full, 0xC001, 0)   # reload
bus_write(full, 0xE001, 0)   # enable

for n in range(3):
    full.call_proc_by_name("run_scanline")
check("main loop: no IRQ after 3 rendered scanlines (latch=3 needs 4)",
      i_(full.vars["IRQ_PENDING"]), 0)
full.call_proc_by_name("run_scanline")
check("main loop: IRQ_PENDING set after the 4th rendered scanline",
      i_(full.vars["IRQ_PENDING"]), 1)

# With rendering DISABLED there is no A12 activity, so the counter must not clock.
full.vars["IRQ_PENDING"] = 0
full.vars["P_MASK"] = 0x00
bus_write(full, 0xC000, 1)
bus_write(full, 0xC001, 0)
for n in range(20):
    full.call_proc_by_name("run_scanline")
check("main loop: rendering disabled -> counter never clocks, no IRQ",
      i_(full.vars["IRQ_PENDING"]), 0)


print("\n%s" % ("ALL MMC3 CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
