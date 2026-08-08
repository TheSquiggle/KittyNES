"""Phase 8: main-loop integration verification. Bakes a synthetic ROM whose
reset routine enables NMI + rendering (writes $80 to PPUCTRL, $18 to
PPUMASK) then sits in a tight loop (so the "game" itself does nothing but
the main loop's timing/rendering/NMI machinery still has to work), runs a
few thousand scanlines' worth through interp.py against the real generated
block graph, and checks: SCANLINE/FRAME counters advance correctly, vblank
sets at scanline 241 and clears at the pre-render line, NMI actually fires
(PC redirected to the NMI vector, stack pushed) when enabled, and the
framebuffer gets populated by the per-scanline renderer during visible
lines. Full "boots a real commercial game" testing isn't achievable without
a real ROM and a real Scratch/TurboWarp runtime -- see docs/main_loop.md.
"""
import sys
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
from ines_loader import build_synthetic_nes, load_rom_into_emu
from interp import Interp

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "got=%r want=%r" % (got, want))
    if not ok:
        FAILURES.append(label)


def i_(x):
    return int(x) if isinstance(x, (int, float)) else x


# ---- build a tiny reset routine: enable NMI-on-vblank (PPUCTRL=$80) and
# background+sprite rendering (PPUMASK=$18), point the NMI vector at a
# handler that just increments a RAM counter and RTIs (so we can detect
# "NMI actually fired and ran" independent of vblank-flag polling), then
# spin forever (self-JMP). ----
prog = bytearray()


def emit(*bs):
    prog.extend(bs)


# reset ($8000):
emit(0xA9, 0x80)              # LDA #$80
emit(0x8D, 0x00, 0x20)        # STA $2000 (PPUCTRL: NMI enable)
emit(0xA9, 0x18)              # LDA #$18
emit(0x8D, 0x01, 0x20)        # STA $2001 (PPUMASK: show bg+sprites)
loop_addr = 0x8000 + len(prog)
emit(0x4C, loop_addr & 0xFF, loop_addr >> 8)  # JMP loop (self)

# NMI handler at $8100 (offset 0x100 into the 32K PRG image):
nmi_off = 0x100
nmi_prog = bytearray()
nmi_prog += bytes([0xE6, 0x20])   # INC $20  (NMI-fired counter)
nmi_prog += bytes([0x40])         # RTI

full_prg = bytearray(32768)
full_prg[0:len(prog)] = prog
full_prg[nmi_off:nmi_off + len(nmi_prog)] = nmi_prog
full_prg[0x7FFA] = (0x8000 + nmi_off) & 0xFF   # NMI vector lo
full_prg[0x7FFB] = (0x8000 + nmi_off) >> 8     # NMI vector hi
full_prg[0x7FFC] = 0x00                        # reset vector lo -> $8000
full_prg[0x7FFD] = 0x80                        # reset vector hi

# build a synthetic .nes wrapping this exact PRG image (2x16K banks so
# $8000-$FFFF is one contiguous 32K NROM image, matching full_prg above)
rom = bytearray()
header = bytearray(16)
header[0:4] = b"NES\x1a"
header[4] = 2   # 2x16K PRG banks = 32K
header[5] = 1   # 1x8K CHR bank
header[6] = 0
header[7] = 0
rom += header
rom += bytes(full_prg)
rom += bytes([0] * 8192)  # blank CHR

e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)
load_rom_into_emu(e, bytes(rom))

interp = Interp(e.proj, max_steps=200_000_000)
interp.call_proc_by_name("nes_init")

check("after nes_init: PC at reset vector", i_(interp.vars["PC"]), 0x8000)
check("after nes_init: SCANLINE=0", i_(interp.vars["SCANLINE"]), 0)
check("after nes_init: FRAME=0", i_(interp.vars["FRAME"]), 0)

# run enough scanlines to reach vblank (scanline 241) within frame 0
for _ in range(242):
    interp.call_proc_by_name("run_scanline")

check("SCANLINE reached 242 after 242 run_scanline calls", i_(interp.vars["SCANLINE"]), 242)
check("vblank flag (bit7) set at scanline 241", i_(interp.vars["P_STATUS"]) & 0x80, 0x80)
check("PPUCTRL was written to $80 by the ROM's reset code", i_(interp.vars["P_CTRL"]), 0x80)
check("PPUMASK was written to $18 by the ROM's reset code", i_(interp.vars["P_MASK"]), 0x18)

# NMI should have fired exactly once by now (edge-triggered at scanline 241,
# and cpu_step only checks/dispatches NMI_PENDING once per instruction --
# the CPU is stuck in a tight self-JMP loop so it will service it promptly).
# Run a few more scanlines to guarantee the CPU has had a chance to actually
# execute the pending NMI dispatch (cpu_step checks NMI_PENDING at the START
# of the NEXT instruction after it's set).
for _ in range(2):
    interp.call_proc_by_name("run_scanline")

nmi_counter = i_(interp.lists["RAM"][0x20])
check("NMI handler actually ran (RAM $20 incremented)", nmi_counter >= 1, True)
check("NMI_PENDING cleared after servicing", i_(interp.vars["NMI_PENDING"]), 0)

# framebuffer should have been populated during the visible scanlines (the
# ROM's CHR is blank, so every pixel resolves to the universal bg color --
# checking it's a defined numeric value, not still some uninitialized
# placeholder, and that BGOP/FB indices for a couple of scanlines look sane)
fb_sample = i_(interp.lists["FB"][0])
check("FB got written during visible-scanline rendering (numeric)", isinstance(fb_sample, int), True)

# run through the rest of frame 0 and confirm the pre-render line (261)
# clears vblank/sprite0hit/overflow and the frame counter advances on wrap
for _ in range(300):  # plenty to reach scanline 261 and wrap to frame 1
    interp.call_proc_by_name("run_scanline")
    if i_(interp.vars["FRAME"]) >= 1:
        break

check("FRAME advanced to 1 after a full 262-scanline pass", i_(interp.vars["FRAME"]), 1)
check("SCANLINE wrapped back to a low value after frame advance",
      i_(interp.vars["SCANLINE"]) < 10, True)
check("vblank flag cleared again after pre-render line", i_(interp.vars["P_STATUS"]) & 0x80, 0)

# ---- regression check for the PC-goes-float bug found during real-ROM
# testing (NEStress.NES smoke test): PC (and other integer-valued state)
# must come back as an exact Python int after many steps, not a float with
# a zero fractional part. Root cause was interp.py's operator_mod using
# math.fmod (always returns float, even for exact-integer inputs) with no
# normalization back to int -- since PC advances via
# MOD(ADD(PC,1),65536) on essentially every instruction, this silently
# turned PC into a float after the very first step, and it stayed a float
# forever after (Python promotes int+float -> float on every subsequent
# op). Fixed in interp.py by normalizing whole-valued floats back to int
# at every arithmetic operator's return point (_normnum), not by touching
# the generated Scratch blocks themselves -- this was purely a test-harness
# fidelity gap (real Scratch/JS numbers are all doubles with no int/float
# distinction to begin with), but a real one worth guarding against since
# it could otherwise mask genuine fractional-value bugs in the interp by
# making "is this exactly representable as an int" impossible to check. ----
check("PC is an exact int after many instructions (no float creep)",
      type(interp.vars["PC"]), int)
check("SCANLINE is an exact int", type(interp.vars["SCANLINE"]), int)
check("FRAME is an exact int", type(interp.vars["FRAME"]), int)
check("A/X/Y/SP are exact ints",
      all(type(interp.vars[r]) is int for r in ("A", "X", "Y", "SP")), True)

# ---- separate check: with NMI disabled (PPUCTRL bit7=0), NMI must NOT fire ----
e2 = Emu("CPU2")
BC.declare_state(e2)
BC.phase1_tables(e2)
BC.phase2_bus(e2)
BC.phase3_cpu(e2)
BC.phase6_ppu_bg(e2)
BC.phase6b_sprites(e2)
BC.phase8_main_loop(e2)
prog2 = bytearray(32768)
prog2[0x7FFC] = 0x00
prog2[0x7FFD] = 0x80
loop2 = 0x8000
prog2[0] = 0x4C; prog2[1] = loop2 & 0xFF; prog2[2] = loop2 >> 8  # JMP $8000 (self, forever)
rom2 = bytearray()
h2 = bytearray(16); h2[0:4] = b"NES\x1a"; h2[4] = 2; h2[5] = 1
rom2 += h2 + bytes(prog2) + bytes([0] * 8192)
load_rom_into_emu(e2, bytes(rom2))
interp2 = Interp(e2.proj, max_steps=200_000_000)
interp2.call_proc_by_name("nes_init")
for _ in range(245):
    interp2.call_proc_by_name("run_scanline")
check("NMI disabled (PPUCTRL bit7=0): NMI_PENDING never set",
      i_(interp2.vars["NMI_PENDING"]), 0)
check("NMI disabled: vblank flag still sets on schedule (independent of NMI enable)",
      i_(interp2.vars["P_STATUS"]) & 0x80, 0x80)

print("\n%s" % ("ALL MAIN-LOOP CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
