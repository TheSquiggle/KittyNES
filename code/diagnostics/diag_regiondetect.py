"""Trace exactly what happens around Famidash's region-detection loop:
watch RAM[$00], print the exact CPU-cycle gap between successive increments,
and note whether NMI/IRQ fired in between. This replaces guessing with a
direct measurement of what our emulator actually produces for the interval
the game uses to compute Y (the region code), so we can tell whether the
timing is plausible or genuinely wrong.
"""
import sys

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
import ines_loader as INES
from interp import Interp

e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)
with open(r"D:\KittyNES\test_roms\Famidash - Huge Man v1.2.8.nes", "rb") as f:
    INES.load_rom_into_emu(e, f.read())

it = Interp(e.proj, max_steps=None)

events = []
cum_cycles = [0.0]
last_ram0 = [None]

orig = Interp.exec_block


def hooked(self, bid, frame):
    b = self.blocks[bid]
    op = b["opcode"]
    if op == "procedures_call":
        pc_name = b["mutation"]["proccode"]
        if pc_name == "do_nmi":
            events.append((cum_cycles[0], "NMI", self.vars.get("PC")))
        elif pc_name == "do_irq":
            events.append((cum_cycles[0], "IRQ", self.vars.get("PC")))
    r = orig(self, bid, frame)
    if op == "procedures_call" and b["mutation"]["proccode"] == "cpu_step":
        c = self.vars.get("CYCLES") or 0
        try:
            c = float(c)
        except (TypeError, ValueError):
            c = 0
        cum_cycles[0] += c
        ram = self.lists.get("RAM")
        if ram:
            v0 = ram[0]
            if last_ram0[0] is not None and v0 != last_ram0[0]:
                events.append((cum_cycles[0], "RAM0_CHANGE", "%r->%r" % (last_ram0[0], v0)))
            last_ram0[0] = v0
    return r


Interp.exec_block = hooked

it.call_proc_by_name("nes_init")
print("post-init PC=%s" % it.vars.get("PC"))

MAXFR = 12
for fr in range(MAXFR):
    it.call_proc_by_name("run_frame")

print(f"\ntotal events: {len(events)}  (showing all)")
last_change_cyc = 0.0
for cyc, kind, info in events:
    if kind == "RAM0_CHANGE":
        gap = cyc - last_change_cyc
        print(f"  cyc={cyc:10.1f}  RAM0_CHANGE {info}   gap_since_last_change={gap:8.1f}")
        last_change_cyc = cyc
    else:
        print(f"  cyc={cyc:10.1f}  {kind} at PC={info}")

print("\nfinal Y (region) if reachable: check RAM near where the routine stores its result")
print("A=%s X=%s Y=%s PC=%s" % (it.vars.get("A"), it.vars.get("X"), it.vars.get("Y"), it.vars.get("PC")))
