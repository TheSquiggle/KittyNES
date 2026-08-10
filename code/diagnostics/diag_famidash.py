import sys, time
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
I = lambda x: int(x or 0)
for fr in range(1, int(sys.argv[2]) + 1):
    it.call_proc_by_name("run_frame")
    V, L = it.vars, it.lists
    vram = [I(x) for x in L["VRAM"]]
    fb = [I(x) for x in L["FB"]]
    print(f"f{fr:3d} PC={I(V['PC']):04X} P_CTRL={I(V['P_CTRL'])} P_MASK={I(V['P_MASK'])} "
          f"P_V={I(V['P_V'])} MIRROR={I(V['MIRROR'])} "
          f"VRAMnz={sum(1 for x in vram if x)} PAL={[I(x) for x in L['PAL']][:8]} "
          f"FBset={sorted(set(fb))[:6]} P8={[I(x) for x in L['P8']]} "
          f"C1={[I(x) for x in L['C1']]} IRQen={I(V['M3_IRQEN'])} "
          f"IRQlatch={I(V['M3_IRQLATCH'])} IRQP={I(V['IRQ_PENDING'])} FI={I(V['FI'])}",
          flush=True)
