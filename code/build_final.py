"""Final build driver -- assembles all 8 phases into one .sb3, optionally
baking in a real .nes ROM file.

Usage:
    python build_final.py                          # synthetic test ROM (no real game)
    python build_final.py path\\to\\game.nes         # bakes that ROM in
    python build_final.py path\\to\\game.nes out.sb3  # custom output path

This is the script that should be re-run any time you want a KittyNES build
for a specific cartridge -- each .sb3 is one ROM, baked in at build time
(Scratch can't read binary files at runtime). See docs/NES_ARCHITECTURE_AND_EMULATION.md
section 6.2 for why.
"""
import sys
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
import ines_loader as INES
from black_backdrop import set_black_backdrop

nes_path = sys.argv[1] if len(sys.argv) > 1 else None
out_path = sys.argv[2] if len(sys.argv) > 2 else r"D:\KittyNES\progress\nes_emulator.sb3"

e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)

if nes_path:
    with open(nes_path, "rb") as f:
        nes_bytes = f.read()
    print(f"Loading real ROM: {nes_path} ({len(nes_bytes)} bytes)")
    INES.load_rom_into_emu(e, nes_bytes)
else:
    print("No ROM given -- baking in the synthetic test ROM (no real game).")
    synth = INES.build_synthetic_nes(prg_banks=2, chr_banks=1, mapper=0, mirror=0)
    INES.load_rom_into_emu(e, synth)

set_black_backdrop(e.proj)
print("total blocks:", len(e.t.blocks))
e.save(out_path)
print("saved", out_path)
