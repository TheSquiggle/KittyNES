"""Driver: builds phases 1-3 (+ mapper/PPU-register scaffolding already in
phase2_bus) via build_core.py's Emu-based generator, then bakes a CPU
self-test program (phase 4) and saves a checkpoint .sb3."""
import sys
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC

e = Emu("CPU")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)

print("blocks after phase1-3+6a+6b+8:", len(e.t.blocks))

out = r"D:\KittyNES\progress\nes_emulator_wip_phase3_full.sb3"
e.save(out)
print("saved", out)
