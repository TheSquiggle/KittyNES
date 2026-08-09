"""Measure real cycle accounting: instructions/frame, cycles/frame, and the
per-opcode cost charged in the hot loop.

NTSC truth: 262 scanlines * 341/3 = 29780.5 CPU cycles per frame.
If cycles/frame matches but a game's timing loop counts short, then specific
opcodes are being overcharged (or interrupt dispatch is being mischarged).

Usage: python diag_cycles.py <rom.nes> <frames>
"""
import collections
import sys

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
import ines_loader as INES
from interp import Interp
import tables6502

rom = sys.argv[1]
frames = int(sys.argv[2]) if len(sys.argv) > 2 else 3

e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)
with open(rom, "rb") as f:
    INES.load_rom_into_emu(e, f.read())

it = Interp(e.proj, max_steps=None)

stats = {"instr": 0, "cycles": 0.0}
pc_hist = collections.Counter()
op_hist = collections.Counter()
op_cycles = collections.Counter()
irq_calls = collections.Counter()

orig = Interp.exec_block


def hooked(self, bid, frame):
    b = self.blocks[bid]
    if b["opcode"] == "procedures_call":
        pc = b["mutation"]["proccode"]
        if pc == "cpu_step":
            stats["instr"] += 1
            pc_hist[int(self.vars.get("PC") or 0)] += 1
        elif pc in ("do_irq", "do_nmi"):
            irq_calls[pc] += 1
    r = orig(self, bid, frame)
    # after a cpu_step returns, CYCLES holds what that instruction was charged
    if b["opcode"] == "procedures_call" and b["mutation"]["proccode"] == "cpu_step":
        c = self.vars.get("CYCLES") or 0
        try:
            c = float(c)
        except (TypeError, ValueError):
            c = 0
        stats["cycles"] += c
        opc = int(self.vars.get("OPC") or 0)
        op_hist[opc] += 1
        op_cycles[opc] += c
    return r


Interp.exec_block = hooked

it.call_proc_by_name("nes_init")
base = dict(stats)
for fr in range(frames):
    i0, c0 = stats["instr"], stats["cycles"]
    pc_hist.clear()
    it.call_proc_by_name("run_frame")
    di, dc = stats["instr"] - i0, stats["cycles"] - c0
    print(f"frame {fr+1}: instructions={di:6d}  cycles={dc:10.1f}  "
          f"(NTSC target 29780.5, delta {dc-29780.5:+.1f})  "
          f"avg cyc/instr={dc/max(di,1):.2f}")

print("\nIRQ/NMI dispatches (total):", dict(irq_calls))
print("\nhottest PCs in final frame:")
for pc, n in pc_hist.most_common(8):
    print(f"   ${pc:04X}  x{n}")

print("\nhottest opcodes (charged cycles vs official table):")
_, _, cycs, pages = tables6502.build_tables()
for opc, n in op_hist.most_common(10):
    charged = op_cycles[opc] / n
    official = cycs[opc]
    flag = "" if abs(charged - official) < 1.01 else "   <-- CHECK"
    print(f"   ${opc:02X} x{n:<7} charged_avg={charged:5.2f} "
          f"base_table={official} page_penalty={pages[opc]}{flag}")
