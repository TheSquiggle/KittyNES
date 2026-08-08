"""Smoke test: build the emulator with the real NEStress.NES test ROM baked in,
run it for a bounded number of steps through interp.py (the Python walker of
the REAL generated Scratch block graph -- not a separate reimplementation),
and report CPU/PPU state so we can sanity-check that a real commercial-grade
test ROM actually runs sensibly (PC advances through plausible code, NMIs
fire, framebuffer gets touched) rather than just that the build succeeds.
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

with open(r"D:\KittyNES\test_roms\NEStress.NES", "rb") as f:
    nes_bytes = f.read()
INES.load_rom_into_emu(e, nes_bytes)

STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 500_000

interp = Interp(e.proj, max_steps=STEPS)

pcs_seen = set()
nmi_fires = 0
last_frame = -1

try:
    interp.run_hat("event_whenflagclicked")
except Exception as ex:
    print("STOPPED:", type(ex).__name__, ex)

v = interp.vars
print("=== final state ===")
for name in ["PC", "A", "X", "Y", "SP", "SCANLINE", "FRAME", "NMI_PENDING", "MAPPER", "PRGBANKS"]:
    print(f"  {name} = {v.get(name)}")

fb = interp.lists.get("FB")
if fb:
    nonzero = sum(1 for px in fb if px not in (0, None))
    print(f"  FB nonzero/transparent-index pixels: {nonzero} / {len(fb)}")
else:
    print("  FB list not found")

print("done")
