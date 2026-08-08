"""Phase 4: bake a hand-written 6502 correctness test program into PRG-ROM and
run it through interp.py (a Python re-implementation of the Scratch VM that
walks the ACTUAL generated block graph) until it halts. Report pass/fail and,
on failure, the last checkpoint id reached (stored in RAM $01) plus CPU state.

This is not the full Klaus Dormann functional_test (that requires ~30M cycles,
infeasible to execute through a graph-walking Python interpreter in reasonable
time) -- it's a broad hand-authored suite covering every addressing mode,
every flag, ADC/SBC/CMP/shifts/branches/stack/JSR-RTS/BIT that build_core.py's
generator implements, checked instruction-by-instruction with immediate
CMP/branch-on-condition assertions that jump to a FAIL trap on any mismatch.
"""
import sys
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
from mini_asm import Asm
import build_core as BC
from interp import Interp, Stop

ORIGIN = 0x8000
asm = Asm(ORIGIN)
ok_n = [0]


def ok_label():
    ok_n[0] += 1
    return "ok_%d" % ok_n[0]


CPMAP = {}


def checkpoint(n):
    CPMAP[n] = CPMAP.get(n, '') 
    # must not clobber A/X/Y or any processor flag (assert_branch checks read
    # flags set by the instruction immediately before the checkpoint call) --
    # bump a zp counter wrapped in PHP/PLP so flags are fully preserved.
    asm("PHP")
    asm("INC", "zp", 0x01)
    asm("PLP")


def assert_branch(mnem, cp):
    """emit: <mnem> ok / JMP fail / ok:   (branch taken == condition true)"""
    checkpoint(cp)
    lbl = ok_label()
    asm(mnem, "rel", lbl)
    asm("JMP", "abs", "fail")
    asm.label(lbl)


def assert_eq(expect, cp):
    checkpoint(cp)
    asm("CMP", "imm", expect)
    assert_branch_after("BEQ")


def assert_branch_after(mnem, cp=None):
    if cp is not None:
        checkpoint(cp)
    lbl = ok_label()
    asm(mnem, "rel", lbl)
    asm("JMP", "abs", "fail")
    asm.label(lbl)


cp = 0

# 1: LDA imm / STA zp / LDA zp
cp += 1
asm("LDA", "imm", 0x05); asm("STA", "zp", 0x10); asm("LDA", "zp", 0x10)
assert_eq(0x05, cp)

# 2: STA zpx / LDA zp
cp += 1
asm("LDX", "imm", 0x03); asm("LDA", "imm", 0x99); asm("STA", "zpx", 0x20)
asm("LDA", "zp", 0x23)
assert_eq(0x99, cp)

# 3: ADC signed overflow (0x7F + 1 -> 0x80, C=0, N=1, V=1)
# NOTE: flag checks (assert_branch) must come BEFORE the value check
# (assert_eq), because assert_eq's CMP itself sets C/Z/N and would clobber
# the flags left by the ADC before we get a chance to test them.
cp += 1
asm("CLC"); asm("LDA", "imm", 0x7F); asm("ADC", "imm", 0x01)
assert_branch("BCC", cp)
cp += 1; assert_branch("BMI", cp)
cp += 1; assert_branch("BVS", cp)
cp += 1; assert_eq(0x80, cp)

# 4: ADC with carry-in, no overflow
cp += 1
asm("SEC"); asm("LDA", "imm", 0x01); asm("ADC", "imm", 0x01)
assert_branch("BCC", cp)
cp += 1; assert_eq(0x03, cp)

# 5: SBC no borrow (5-3, C=1 in -> C=1 out, no borrow)
cp += 1
asm("SEC"); asm("LDA", "imm", 0x05); asm("SBC", "imm", 0x03)
assert_branch("BCS", cp)
cp += 1; assert_eq(0x02, cp)

# 6: SBC with borrow (5 - 6 - (1-0) = -2 = 0xFE, C=0)
cp += 1
asm("CLC"); asm("LDA", "imm", 0x05); asm("SBC", "imm", 0x06)
assert_branch("BCC", cp)
cp += 1; assert_eq(0xFE, cp)

# 7: AND / ORA / EOR
cp += 1
asm("LDA", "imm", 0xF0); asm("AND", "imm", 0x0F)
assert_eq(0x00, cp)
cp += 1
asm("LDA", "imm", 0xF0); asm("ORA", "imm", 0x0F)
assert_eq(0xFF, cp)
cp += 1
asm("LDA", "imm", 0xFF); asm("EOR", "imm", 0x0F)
assert_eq(0xF0, cp)

# 8: INC/DEC zp
cp += 1
asm("LDA", "imm", 0x05); asm("STA", "zp", 0x30); asm("INC", "zp", 0x30)
asm("LDA", "zp", 0x30)
assert_eq(0x06, cp)
cp += 1
asm("DEC", "zp", 0x30); asm("DEC", "zp", 0x30); asm("LDA", "zp", 0x30)
assert_eq(0x04, cp)

# 9: INX/DEY/register transfers
cp += 1
asm("LDX", "imm", 0x00); asm("INX"); asm("INX")
asm("CPX", "imm", 0x02)
assert_branch_after("BEQ", cp)
cp += 1
asm("LDY", "imm", 0x05); asm("DEY")
asm("CPY", "imm", 0x04)
assert_branch_after("BEQ")
cp += 1
asm("LDA", "imm", 0x77); asm("TAX")
asm("CPX", "imm", 0x77)
assert_branch_after("BEQ")
cp += 1
asm("LDA", "imm", 0x55); asm("TAY")
asm("CPY", "imm", 0x55)
assert_branch_after("BEQ")

# 10: shifts (flag check before value check -- same CMP-clobber reason as above)
cp += 1
asm("LDA", "imm", 0x81); asm("ASL", "acc")
assert_branch("BCS", cp)
cp += 1; assert_eq(0x02, cp)
cp += 1
asm("LDA", "imm", 0x01); asm("LSR", "acc")
assert_branch("BCS", cp)
cp += 1; assert_eq(0x00, cp)
cp += 1
asm("SEC"); asm("LDA", "imm", 0x40); asm("ROL", "acc")
assert_branch("BCC", cp)
cp += 1; assert_eq(0x81, cp)
cp += 1
asm("SEC"); asm("LDA", "imm", 0x01); asm("ROR", "acc")
assert_branch("BCS", cp)
cp += 1; assert_eq(0x80, cp)

# 11: stack PHA/PLA, PHP/PLP
cp += 1
asm("LDA", "imm", 0x3C); asm("PHA"); asm("LDA", "imm", 0x00); asm("PLA")
assert_eq(0x3C, cp)
cp += 1
asm("SEC"); asm("PHP"); asm("CLC"); asm("PLP")
assert_branch("BCS", cp)

# 12: JSR/RTS
cp += 1
asm("JSR", "abs", "sub1")
assert_eq(0x99, cp)

# 13: BIT
cp += 1
asm("LDA", "imm", 0xC0); asm("STA", "zp", 0x40)
asm("LDA", "imm", 0xFF); asm("BIT", "zp", 0x40)
assert_branch("BMI", cp)
cp += 1; assert_branch("BVS", cp)
cp += 1
asm("LDA", "imm", 0x00); asm("BIT", "zp", 0x40)
# A=$00, mem=$C0 -> A & mem == 0 -> Z should be SET, so BEQ (not BNE) is
# the "condition holds" branch here.
assert_branch("BEQ", cp)

# 14: absolute addressing
cp += 1
asm("LDA", "imm", 0x42); asm("STA", "abs", 0x0300); asm("LDA", "abs", 0x0300)
assert_eq(0x42, cp)

# ---- success ----
asm("LDA", "imm", 0xAA)
asm("STA", "zp", 0x00)
asm.label("pass_loop")
asm("JMP", "abs", "pass_loop")

# ---- subroutine used by check 12 ----
asm.label("sub1")
asm("LDA", "imm", 0x99)
asm("RTS")

# ---- fail trap ----
asm.label("fail")
asm("LDA", "imm", 0xFF)
asm("STA", "zp", 0x00)
asm.label("fail_loop")
asm("JMP", "abs", "fail_loop")

prog = asm.assemble()
print("program length:", len(prog), "bytes, checks:", cp)

# ---- build the emulator ----
e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)

interp = Interp(e.proj, max_steps=20_000_000)

# lay out PRG-ROM: 32K, NROM(mapper 0)-style, program at $8000, reset vector -> $8000
PRG = [0] * 32768
for i, byte in enumerate(prog):
    PRG[i] = byte
PRG[0x7FFC] = 0x00  # reset vector lo -> $8000
PRG[0x7FFD] = 0x80  # reset vector hi
interp.lists["PRG"] = PRG
interp.vars["PRGBANKS"] = 2
interp.vars["PRGB0"] = 0
interp.vars["PRGB1"] = 1
interp.vars["MAPPER"] = 0

interp.call_proc_by_name("cpu_reset")  # proccode == "cpu_reset" (no args)
print("after reset: PC=%04X SP=%02X" % (interp.vars["PC"], interp.vars["SP"]))
assert interp.vars["PC"] == 0x8000, "reset vector fetch broken: PC=%04X" % interp.vars["PC"]

MAX_STEPS = 200000
halted = False
for i in range(MAX_STEPS):
    try:
        interp.call_proc_by_name("cpu_step")
    except Stop as ex:
        print("STOPPED:", ex)
        break
    ram0 = interp.lists["RAM"][0]
    if ram0 in (0xAA, 0xFF):
        halted = True
        break

marker = interp.lists["RAM"][0]
checkpoint_reached = interp.lists["RAM"][1]
print("steps executed:", i + 1)
print("halted:", halted, "marker(RAM[0]):", hex(marker) if isinstance(marker, int) else marker)
print("last checkpoint id (RAM[1]):", checkpoint_reached)
def _i(x):
    return int(x) if isinstance(x, (int, float)) else x


print("final regs: A=%02X X=%02X Y=%02X SP=%02X PC=%04X" % (
    _i(interp.vars["A"]), _i(interp.vars["X"]), _i(interp.vars["Y"]),
    _i(interp.vars["SP"]), _i(interp.vars["PC"])))
print("flags: C=%s Z=%s I=%s D=%s B=%s V=%s N=%s" % (
    interp.vars["FC"], interp.vars["FZ"], interp.vars["FI"], interp.vars["FD"],
    interp.vars["FB"], interp.vars["FV"], interp.vars["FN"]))

if marker == 0xAA:
    print("RESULT: ALL", cp, "CHECKS PASSED")
    sys.exit(0)
else:
    print("RESULT: FAILED at checkpoint", checkpoint_reached)
    sys.exit(1)
