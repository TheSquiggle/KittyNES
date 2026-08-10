import sys, collections
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC, ines_loader as INES
from interp import Interp
import json as _j

e = Emu("NES")
BC.declare_state(e); BC.phase1_tables(e); BC.phase2_bus(e); BC.phase3_cpu(e)
BC.phase6_ppu_bg(e); BC.phase6b_sprites(e); BC.phase8_main_loop(e)
with open(sys.argv[1], "rb") as f:
    INES.load_rom_into_emu(e, f.read())
it = Interp(e.proj, max_steps=None)

counts = collections.Counter()
orig = Interp.exec_block
def hooked(self, bid, frame):
    b = self.blocks[bid]
    if b["opcode"] == "procedures_call":
        pc = b.get("mutation", {}).get("proccode", "")
        if pc in ("ppu_reg_write %s %s", "ppu_write %s %s", "nt_index %s"):
            argids = b.get("mutation", {}).get("argumentids")
            if isinstance(argids, str): argids = _j.loads(argids)
            vals = []
            for aid in argids:
                i = b["inputs"].get(aid)
                vals.append(self._inp_val(i, frame) if i else None)
            try:
                a = int(vals[0])
            except (TypeError, ValueError):
                a = -1
            if pc.startswith("ppu_reg_write"): counts["reg%d" % a] += 1
            elif pc.startswith("nt_index"): counts["nt_index"] += 1
            else: counts["ppu_write_%X000" % (a // 4096)] += 1
    return orig(self, bid, frame)
Interp.exec_block = hooked

it.call_proc_by_name("nes_init")
for fr in range(int(sys.argv[2])):
    it.call_proc_by_name("run_frame")
    if (fr + 1) % 5 == 0:
        print(fr + 1, dict(counts), flush=True)
