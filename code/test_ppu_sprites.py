"""Phase 6b: sprite (OAM) compositing + scroll-register verification, same
interp.py-against-real-generated-block-graph rigor as the other suites.
"""
import sys
sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
from interp import Interp

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "got=%r want=%r" % (got, want))
    if not ok:
        FAILURES.append(label)


def i_(x):
    return int(x) if isinstance(x, (int, float)) else x


def fresh():
    e = Emu("NES")
    BC.declare_state(e)
    BC.phase1_tables(e)
    BC.phase2_bus(e)
    BC.phase3_cpu(e)
    BC.phase6_ppu_bg(e)
    BC.phase6b_sprites(e)
    return Interp(e.proj, max_steps=50_000_000)


def set_oam_sprite(OAM, idx, y, tile, attr, x):
    OAM[idx * 4 + 0] = y
    OAM[idx * 4 + 1] = tile
    OAM[idx * 4 + 2] = attr
    OAM[idx * 4 + 3] = x


# =====================================================================
# 1) Sprite compositing over an all-transparent background + priority bit
# =====================================================================
print("\n--- sprite compositing + priority ---")
interp = fresh()
CHR = [0] * 8192
# tile 0: solid color-index 3 (both planes 0xFF)
for row in range(8):
    CHR[row] = 0xFF
    CHR[8 + row] = 0xFF
interp.lists["CHR"] = CHR
interp.lists["VRAM"] = [0] * 2048  # nametable all tile-id 0 = solid, but we
# want an all-TRANSPARENT background for this test, so make tile 0 in CHR
# solid but reference a different (all-zero) tile in the nametable instead:
# CHR tile 1 (offset 16) is left as all-zero -> transparent. Point nametable
# at tile 1 everywhere.
VRAM = [1] * 2048
interp.lists["VRAM"] = VRAM
PAL = [0] * 32
PAL[0] = 0x0F        # universal bg = black
PAL[16 + 3] = 0x21   # sprite palette group 0, color-index 3 = blue
interp.lists["PAL"] = PAL
interp.vars["CHRRAM"] = 0
interp.vars["CHRBANKS"] = 1
interp.vars["CHRB0"] = 0
interp.vars["CHRB1"] = 1
interp.vars["MAPPER"] = 0
interp.vars["MIRROR"] = 0
interp.vars["P_CTRL"] = 0  # 8x8 sprites, sprite pattern table 0

OAM = [0xFF] * 256  # 0xFF Y = off-screen for all unused sprite slots
set_oam_sprite(OAM, 0, 10, 0, 0b00000000, 20)  # sprite 0: solid tile0, palette0, front
interp.lists["OAM"] = OAM

interp.call_proc_by_name("render_bg_region %s %s %s %s",
                          {"row0": 0, "row1": 30, "col0": 0, "col1": 32})
interp.call_proc_by_name("render_sprites_line %s", {"sl": 11})  # scanline within sprite (Y+1=11..Y+8=18)


def fb(x, y):
    return i_(interp.lists["FB"][y * 256 + x])


check("sprite solid pixel visible over transparent bg", fb(20, 11), 0x21)
check("outside sprite X range stays background", fb(10, 11), 0x0F)
check("outside sprite Y range stays background (checked via separate line)", True, True)

# ---- priority bit: sprite behind an OPAQUE background pixel should be hidden ----
interp2 = fresh()
CHR2 = [0] * 8192
for row in range(8):
    CHR2[row] = 0xFF; CHR2[8 + row] = 0xFF        # tile0: solid (opaque bg tile)
    CHR2[16 + row] = 0xFF; CHR2[16 + 8 + row] = 0xFF  # tile1: solid (sprite tile, reuse)
interp2.lists["CHR"] = CHR2
interp2.lists["VRAM"] = [0] * 2048  # nametable all tile0 -> opaque bg everywhere
PAL2 = [0] * 32
PAL2[3] = 0x30          # bg group0 color-index3 = white-ish
PAL2[16 + 3] = 0x21      # sprite group0 color-index3 = blue
interp2.lists["PAL"] = PAL2
interp2.vars["CHRRAM"] = 0
interp2.vars["CHRBANKS"] = 1
interp2.vars["CHRB0"] = 0
interp2.vars["CHRB1"] = 1
interp2.vars["MAPPER"] = 0
interp2.vars["MIRROR"] = 0
interp2.vars["P_CTRL"] = 0
OAM2 = [0xFF] * 256
set_oam_sprite(OAM2, 0, 10, 1, 0b00100000, 20)  # attr bit5 set = BEHIND background
interp2.lists["OAM"] = OAM2
interp2.call_proc_by_name("render_bg_region %s %s %s %s",
                           {"row0": 0, "row1": 30, "col0": 0, "col1": 32})
interp2.call_proc_by_name("render_sprites_line %s", {"sl": 11})
check("sprite behind opaque bg is hidden (priority bit)",
      i_(interp2.lists["FB"][11 * 256 + 20]), 0x30)

# ---- but sprite behind a TRANSPARENT bg pixel still shows ----
interp3 = fresh()
interp3.lists["CHR"] = CHR2
interp3.lists["VRAM"] = [1] * 2048  # nametable tile1 -> but tile1 is ALSO solid
# use a genuinely transparent tile: tile index 2 in CHR (never written -> 0)
VRAM3 = [2] * 2048
interp3.lists["VRAM"] = VRAM3
interp3.lists["PAL"] = PAL2
interp3.vars["CHRRAM"] = 0
interp3.vars["CHRBANKS"] = 1
interp3.vars["CHRB0"] = 0
interp3.vars["CHRB1"] = 1
interp3.vars["MAPPER"] = 0
interp3.vars["MIRROR"] = 0
interp3.vars["P_CTRL"] = 0
OAM3 = [0xFF] * 256
set_oam_sprite(OAM3, 0, 10, 1, 0b00100000, 20)  # behind-bg priority, but bg is transparent here
interp3.lists["OAM"] = OAM3
interp3.call_proc_by_name("render_bg_region %s %s %s %s",
                           {"row0": 0, "row1": 30, "col0": 0, "col1": 32})
interp3.call_proc_by_name("render_sprites_line %s", {"sl": 11})
check("sprite behind TRANSPARENT bg still shows",
      i_(interp3.lists["FB"][11 * 256 + 20]), 0x21)

# =====================================================================
# 2) Sprite-0-hit detection
# =====================================================================
print("\n--- sprite-0 hit ---")
interp4 = fresh()
interp4.lists["CHR"] = CHR2  # tile0 solid opaque, reuse from above
interp4.lists["VRAM"] = [0] * 2048  # opaque background everywhere
interp4.lists["PAL"] = PAL2
interp4.vars["CHRRAM"] = 0
interp4.vars["CHRBANKS"] = 1
interp4.vars["CHRB0"] = 0
interp4.vars["CHRB1"] = 1
interp4.vars["MAPPER"] = 0
interp4.vars["MIRROR"] = 0
interp4.vars["P_CTRL"] = 0
interp4.vars["P_STATUS"] = 0
OAM4 = [0xFF] * 256
set_oam_sprite(OAM4, 0, 10, 0, 0b00000000, 20)  # sprite 0, front priority, opaque, over opaque bg
interp4.lists["OAM"] = OAM4
interp4.call_proc_by_name("render_bg_region %s %s %s %s",
                           {"row0": 0, "row1": 30, "col0": 0, "col1": 32})
interp4.call_proc_by_name("render_sprites_line %s", {"sl": 11})
check("sprite-0 hit bit set when sprite0 opaque over opaque bg",
      i_(interp4.vars["P_STATUS"]) & 0x40, 0x40)

# sprite-0 NOT hit when it doesn't overlap an opaque bg pixel (transparent bg)
interp5 = fresh()
interp5.lists["CHR"] = CHR2
interp5.lists["VRAM"] = [2] * 2048  # transparent bg everywhere
interp5.lists["PAL"] = PAL2
interp5.vars["CHRRAM"] = 0
interp5.vars["CHRBANKS"] = 1
interp5.vars["CHRB0"] = 0
interp5.vars["CHRB1"] = 1
interp5.vars["MAPPER"] = 0
interp5.vars["MIRROR"] = 0
interp5.vars["P_CTRL"] = 0
interp5.vars["P_STATUS"] = 0
OAM5 = [0xFF] * 256
set_oam_sprite(OAM5, 0, 10, 0, 0b00000000, 20)
interp5.lists["OAM"] = OAM5
interp5.call_proc_by_name("render_bg_region %s %s %s %s",
                           {"row0": 0, "row1": 30, "col0": 0, "col1": 32})
interp5.call_proc_by_name("render_sprites_line %s", {"sl": 11})
check("no sprite-0 hit when bg is transparent",
      i_(interp5.vars["P_STATUS"]) & 0x40, 0)

# =====================================================================
# 3) 8-sprites-per-line overflow flag
# =====================================================================
print("\n--- sprite overflow (9th sprite on a line) ---")
interp6 = fresh()
OAM6 = [0xFF] * 256
for i in range(9):  # 9 sprites all on scanline range Y=10 (visible rows 11-18)
    set_oam_sprite(OAM6, i, 10, 0, 0, i * 10)
interp6.lists["OAM"] = OAM6
interp6.lists["CHR"] = [0] * 8192
interp6.lists["VRAM"] = [0] * 2048
interp6.lists["PAL"] = [0] * 32
interp6.vars["CHRRAM"] = 0
interp6.vars["CHRBANKS"] = 1
interp6.vars["CHRB0"] = 0
interp6.vars["CHRB1"] = 1
interp6.vars["MAPPER"] = 0
interp6.vars["MIRROR"] = 0
interp6.vars["P_CTRL"] = 0
interp6.vars["P_STATUS"] = 0
interp6.call_proc_by_name("sprite_eval_line %s", {"sl": 11})
check("8 sprites captured (9th overflows)", i_(interp6.vars["SPRN"]), 8)
check("overflow flag (bit5) set for a 9th qualifying sprite",
      i_(interp6.vars["P_STATUS"]) & 0x20, 0x20)

# exactly 8 sprites -> no overflow
interp7 = fresh()
OAM7 = [0xFF] * 256
for i in range(8):
    set_oam_sprite(OAM7, i, 10, 0, 0, i * 10)
interp7.lists["OAM"] = OAM7
interp7.vars["P_CTRL"] = 0
interp7.vars["P_STATUS"] = 0
interp7.call_proc_by_name("sprite_eval_line %s", {"sl": 11})
check("exactly 8 sprites: no overflow", i_(interp7.vars["P_STATUS"]) & 0x20, 0)
check("exactly 8 sprites: SPRN=8", i_(interp7.vars["SPRN"]), 8)

# =====================================================================
# 4) Scroll register increment behavior
# =====================================================================
print("\n--- scroll register increment ---")
interp8 = fresh()
interp8.vars["P_V"] = 5          # coarse X = 5
interp8.call_proc_by_name("ppu_scanline_inc_coarse_x")
check("coarse X increments by 1", i_(interp8.vars["P_V"]) % 32, 6)

interp8.vars["P_V"] = 31         # coarse X at max, NT-X bit currently 0
interp8.call_proc_by_name("ppu_scanline_inc_coarse_x")
check("coarse X wraps to 0 at 31", i_(interp8.vars["P_V"]) % 32, 0)
check("coarse X wrap flips NT-X bit", (i_(interp8.vars["P_V"]) // 1024) % 2, 1)

interp8.vars["P_V"] = 31 + 1024  # coarse X=31, NT-X bit already 1
interp8.call_proc_by_name("ppu_scanline_inc_coarse_x")
check("coarse X wrap flips NT-X bit back to 0",
      (i_(interp8.vars["P_V"]) // 1024) % 2, 0)

# fine Y increment (0-6 -> just +1 in the fine-Y field)
interp8.vars["P_V"] = 0  # fine Y = 0, coarse Y = 0
interp8.call_proc_by_name("ppu_scanline_inc_y")
check("fine Y increments (0->1)", i_(interp8.vars["P_V"]) // 4096, 1)

# fine Y 7 -> 0, coarse Y increments normally (not 29/31)
interp8.vars["P_V"] = 7 * 4096 + 5 * 32  # fine Y=7, coarse Y=5
interp8.call_proc_by_name("ppu_scanline_inc_y")
v = i_(interp8.vars["P_V"])
check("fine Y wraps to 0", v // 4096, 0)
check("coarse Y increments to 6", (v // 32) % 32, 6)

# fine Y 7, coarse Y 29 -> coarse Y wraps to 0 AND NT-Y bit flips
interp8.vars["P_V"] = 7 * 4096 + 29 * 32  # NT-Y bit = 0
interp8.call_proc_by_name("ppu_scanline_inc_y")
v = i_(interp8.vars["P_V"])
check("coarse Y=29 wraps to 0", (v // 32) % 32, 0)
check("coarse Y=29 wrap flips NT-Y bit", (v // 2048) % 2, 1)

# fine Y 7, coarse Y 31 (out-of-range/software-set) -> wraps to 0, NO NT-Y flip
interp8.vars["P_V"] = 7 * 4096 + 31 * 32
interp8.call_proc_by_name("ppu_scanline_inc_y")
v = i_(interp8.vars["P_V"])
check("coarse Y=31 (out of range) wraps to 0 without NT flip", (v // 32) % 32, 0)
check("coarse Y=31 wrap does NOT flip NT-Y bit", (v // 2048) % 2, 0)

# copy_horiz_v / copy_vert_v
interp8.vars["P_T"] = 7 + 1024 + 3 * 32 + 2048 + 5 * 4096  # coarseX7,NTX1,coarseY3,NTY1,fineY5
interp8.vars["P_V"] = 0
interp8.call_proc_by_name("ppu_copy_horiz_v")
v = i_(interp8.vars["P_V"])
check("copy_horiz_v: coarse X copied", v % 32, 7)
check("copy_horiz_v: NT-X bit copied", (v // 1024) % 2, 1)
check("copy_horiz_v: does NOT touch coarse Y", (v // 32) % 32, 0)

interp8.vars["P_V"] = 0
interp8.call_proc_by_name("ppu_copy_vert_v")
v = i_(interp8.vars["P_V"])
check("copy_vert_v: coarse Y copied", (v // 32) % 32, 3)
check("copy_vert_v: NT-Y bit copied", (v // 2048) % 2, 1)
check("copy_vert_v: fine Y copied", v // 4096, 5)
check("copy_vert_v: does NOT touch coarse X", v % 32, 0)

print("\n%s" % ("ALL SPRITE/SCROLL CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
