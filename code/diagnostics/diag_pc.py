import sys, collections
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC, ines_loader as INES
from interp import Interp

e = Emu("NES")
BC.declare_state(e); BC.phase1_tables(e); BC.phase2_bus(e); BC.phase3_cpu(e)
BC.phase6_ppu_bg(e); BC.phase6b_sprites(e); BC.phase8_main_loop(e)
with open(sys.argv[1], "rb") as f:
    INES.load_rom_into_emu(e, f.read())
it = Interp(e.proj, max_steps=None)
it.call_proc_by_name("nes_init")
for fr in range(int(sys.argv[2])):
    it.call_proc_by_name("run_frame")
pcs = collections.Counter()
orig = Interp.exec_block
def hooked(self, bid, frame):
    b = self.blocks[bid]
    if b["opcode"] == "procedures_call" and b.get("mutation", {}).get("proccode") == "cpu_step":
        try: pcs[int(self.vars["PC"])] += 1
        except Exception: pass
    return orig(self, bid, frame)
Interp.exec_block = hooked
it.call_proc_by_name("run_frame")
print("distinct PCs in one frame:", len(pcs), " total steps:", sum(pcs.values()))
for pc, n in pcs.most_common(25):
    print("  $%04X  x%d" % (pc, n))
