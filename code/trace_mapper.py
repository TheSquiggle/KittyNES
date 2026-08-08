"""Trace every mapper_write the real ROM performs, plus the resulting bank
state, to confirm whether the mapper-66 bank selection is correct.

Usage: python trace_mapper.py <rom.nes> <frames>
"""
import sys

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
import ines_loader as INES
import interp as I
from interp import Interp

rom = sys.argv[1]
frames = int(sys.argv[2]) if len(sys.argv) > 2 else 40

e = Emu("CPU")
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

orig = Interp.exec_block
seen = []


def hooked(self, bid, frame):
    b = self.blocks[bid]
    if b["opcode"] == "procedures_call" and b["mutation"]["proccode"] == "mapper_write %s %s":
        import json as _j
        argids = b["mutation"]["argumentids"]
        if isinstance(argids, str):
            argids = _j.loads(argids)
        vals = []
        for aid in argids:
            i = b["inputs"].get(aid)
            vals.append(self._inp_val(i, frame) if i else None)
        seen.append((self.vars.get("FRAME"), vals[0], vals[1],
                     self.vars.get("PRGB0"), self.vars.get("CHRB0")))
    return orig(self, bid, frame)


Interp.exec_block = hooked

it.call_proc_by_name("nes_init")
print("post-init: PRGB0=%s PRGB1=%s CHRB0=%s CHRB1=%s" %
      (it.vars.get("PRGB0"), it.vars.get("PRGB1"),
       it.vars.get("CHRB0"), it.vars.get("CHRB1")))

for fr in range(frames):
    it.call_proc_by_name("run_frame")

print(f"\nmapper_write calls seen: {len(seen)}")
for rec in seen[:40]:
    frn, a, v, prgb0, chrb0 = rec
    try:
        ai = int(a)
        vi = int(v)
        print(f"  frame={frn} addr=${ai:04X} val=${vi:02X} "
              f"(PRGsel={vi & 3} CHRsel={(vi >> 4) & 3})  "
              f"-> PRGB0={prgb0} CHRB0={chrb0}")
    except (TypeError, ValueError):
        print("  ", rec)

print("\nfinal: PRGB0=%s PRGB1=%s CHRB0=%s CHRB1=%s PRGBANKS=%s CHRBANKS=%s" %
      (it.vars.get("PRGB0"), it.vars.get("PRGB1"), it.vars.get("CHRB0"),
       it.vars.get("CHRB1"), it.vars.get("PRGBANKS"), it.vars.get("CHRBANKS")))
print("PRG list len:", len(it.lists["PRG"]), " CHR list len:", len(it.lists["CHR"]))
