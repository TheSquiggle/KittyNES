"""Smoke test for the user's real "Super Mario Bros. + Duck Hunt (USA)"
ROM (mapper 66/GxROM). Same approach as run_nestress_smoke.py: builds the
emulator with the real ROM baked in via ines_loader, drives it through
interp.py (the real generated block graph) for a bounded step count, and
reports CPU/PPU state periodically so we can see how far a real game
progresses and how long that takes.
"""
import sys
import time
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
import ines_loader as INES
from interp import Interp, Stop

ROM_PATH = r"C:\Users\silas\Documents\ROMS\NES\Super Mario Bros. + Duck Hunt (USA).nes"
STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000_000
REPORT_EVERY_SCANLINES = 262  # once per frame

e = Emu("CPU")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)

with open(ROM_PATH, "rb") as f:
    nes_bytes = f.read()
print("Loaded ROM:", ROM_PATH, "(%d bytes)" % len(nes_bytes))
parsed = INES.load_rom_into_emu(e, nes_bytes)
print("mapper=%d mirror=%d prg_banks_16k=%d chr_banks_8k=%d" %
      (parsed["mapper"], parsed["mirror"], parsed["prg_banks_16k"], parsed["chr_banks_8k"]))

interp = Interp(e.proj, max_steps=None)
interp.call_proc_by_name("nes_init")

def i_(x):
    return int(x) if isinstance(x, (int, float)) else x


def fb_nonzero():
    fb = interp.lists["FB"]
    return sum(1 for px in fb if px)


t0 = time.time()
scanline_count = 0
stopped_reason = None
pcs = set()
try:
    while interp.steps < STEPS:
        interp.call_proc_by_name("run_scanline")
        scanline_count += 1
        pcs.add(i_(interp.vars.get("PC")))
        if scanline_count % REPORT_EVERY_SCANLINES == 0:
            elapsed = time.time() - t0
            print("frame=%d scanline=%d PPUCTRL=%s PPUMASK=%s FBnonzero=%d "
                  "distinctPCs=%d PC=%s steps=%d elapsed=%.1fs" % (
                      i_(interp.vars.get("FRAME")), i_(interp.vars.get("SCANLINE")),
                      hex(i_(interp.vars.get("P_CTRL", 0))), hex(i_(interp.vars.get("P_MASK", 0))),
                      fb_nonzero(), len(pcs), hex(i_(interp.vars.get("PC"))),
                      interp.steps, elapsed))
except Stop as ex:
    stopped_reason = str(ex)

elapsed = time.time() - t0
print("=== final state ===")
print("stopped:", stopped_reason or "step budget exhausted mid-scanline-loop")
print("total interp steps:", interp.steps, "elapsed: %.1fs" % elapsed)
print("scanlines run:", scanline_count)
for name in ["PC", "A", "X", "Y", "SP", "SCANLINE", "FRAME", "NMI_PENDING", "MAPPER",
             "PRGBANKS", "CHRBANKS", "PRGB0", "PRGB1"]:
    print("  %s = %s" % (name, interp.vars.get(name)))
print("  distinct PC values visited:", len(pcs))
print("  FB nonzero pixels: %d / %d" % (fb_nonzero(), len(interp.lists["FB"])))
print("done")
