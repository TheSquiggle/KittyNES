"""Follow-up bug hunt #3: a real screenshot of SMB 1-1 shows correct tile
SHAPE but wrong/degenerate COLOR -- the brick/question-block pyramid has
speckled static-like corruption, and a Goomba renders as a flat solid
black block instead of a shaded sprite. Both point at palette resolution,
not tile fetch (already verified correct in the previous round). Audits,
with real synthetic scenes rendered through the actual generated block
graph via interp.py:

1. Background attribute-quadrant math: all 4 quadrants (TL/TR/BL/BR)
   within a single attribute byte, each assigned a DIFFERENT background
   palette group, confirming each quadrant's tiles pick up the correct
   one -- via BOTH the non-scrolled bg_setup_tile (Phase 6a) path AND the
   scrolled bg_setup_tile_v (Phase 6b, what the real main loop actually
   uses) path, since a bug could exist in one but not the other.
2. Palette RAM $3F10/$3F14/$3F18/$3F1C -> $3F00/$3F04/$3F08/$3F0C mirroring.
3. Sprite attribute palette-select bits -> all 4 sprite palettes ($3F11-
   $3F1F), each with 3 real (non-transparent) colors, confirming no
   sprite degenerates to a flat/wrong color.
4. Spot-checks of the master 64-color NES palette table (PALRGB) against
   known real hardware values, in case of a transcription error.
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


def bus_write(interp, a, v):
    interp.call_proc_by_name("bus_write %s %s", {"a": a, "v": v})


def fb(interp, x, y):
    return i_(interp.lists["FB"][y * 256 + x])


# =====================================================================
# 1) Background attribute-quadrant math: all 4 quadrants, distinct
# palettes, via BOTH the non-scrolled and the scrolled (real-main-loop)
# tile-fetch paths.
# =====================================================================
print("--- background attribute quadrant math: all 4 quadrants ---")


def setup_quadrant_scene(interp):
    # a single solid tile (id 0, color-index 3 everywhere) placed at all
    # 16 tile positions of one 4x4-tile attribute block (tile cols 0-3,
    # rows 0-3), so every rendered pixel in that block comes from the
    # SAME tile data -- any color difference between quadrants can only
    # come from the attribute/palette lookup, not the tile bitmap itself.
    CHR = [0] * 8192
    for row in range(8):
        CHR[row] = 0xFF
        CHR[8 + row] = 0xFF
    interp.lists["CHR"] = CHR
    VRAM = [0] * 2048  # tile 0 (solid) everywhere in this block
    # attribute byte for block (0,0): TL=group0(00), TR=group1(01),
    # BL=group2(10), BR=group3(11) -> 0b11_10_01_00 = 0xE4
    VRAM[0x3C0] = 0b11100100
    interp.lists["VRAM"] = VRAM
    PAL = [0] * 32
    PAL[0] = 0x0F                      # universal bg (unused here, tile is always opaque)
    PAL[0 * 4 + 3] = 0x21              # group0 color-index3 = blue
    PAL[1 * 4 + 3] = 0x16              # group1 color-index3 = red
    PAL[2 * 4 + 3] = 0x2A              # group2 color-index3 = green
    PAL[3 * 4 + 3] = 0x30              # group3 color-index3 = white
    interp.lists["PAL"] = PAL
    interp.vars["CHRRAM"] = 0
    interp.vars["CHRBANKS"] = 1
    interp.lists["C1"] = list(range(8))
    interp.vars["MAPPER"] = 0
    interp.vars["MIRROR"] = 0
    interp.vars["P_CTRL"] = 0


# ---- 1a: non-scrolled path (bg_setup_tile / render_bg_region) ----
interp = fresh()
setup_quadrant_scene(interp)
interp.call_proc_by_name("render_bg_region %s %s %s %s",
                          {"row0": 0, "row1": 4, "col0": 0, "col1": 4})
# TL quadrant = tile cols0-1,rows0-1 -> pixel (0,0) is in tile(0,0)
check("non-scrolled: TL quadrant (tile 0,0) -> group0 blue", fb(interp, 0, 0), 0x21)
check("non-scrolled: TL quadrant (tile 1,1) -> group0 blue", fb(interp, 12, 12), 0x21)
# TR quadrant = tile cols2-3,rows0-1 -> tile(2,0) starts at x=16
check("non-scrolled: TR quadrant (tile 2,0) -> group1 red", fb(interp, 16, 0), 0x16)
check("non-scrolled: TR quadrant (tile 3,1) -> group1 red", fb(interp, 28, 12), 0x16)
# BL quadrant = tile cols0-1,rows2-3 -> tile(0,2) starts at y=16
check("non-scrolled: BL quadrant (tile 0,2) -> group2 green", fb(interp, 0, 16), 0x2A)
check("non-scrolled: BL quadrant (tile 1,3) -> group2 green", fb(interp, 12, 28), 0x2A)
# BR quadrant = tile cols2-3,rows2-3 -> tile(2,2) starts at (16,16)
check("non-scrolled: BR quadrant (tile 2,2) -> group3 white", fb(interp, 16, 16), 0x30)
check("non-scrolled: BR quadrant (tile 3,3) -> group3 white", fb(interp, 28, 28), 0x30)

# ---- 1b: scrolled path (bg_setup_tile_v / render_bg_line_scrolled) --
# this is what the REAL main loop actually calls at runtime.
interp2 = fresh()
setup_quadrant_scene(interp2)
interp2.vars["P_T"] = 0
interp2.vars["P_V"] = 0
interp2.vars["P_X"] = 0
for sl in range(32):
    interp2.call_proc_by_name("render_bg_line_scrolled %s", {"sl": sl})
check("scrolled: TL quadrant -> group0 blue", fb(interp2, 0, 0), 0x21)
check("scrolled: TL quadrant -> group0 blue (tile1,1)", fb(interp2, 12, 12), 0x21)
check("scrolled: TR quadrant -> group1 red", fb(interp2, 16, 0), 0x16)
check("scrolled: TR quadrant -> group1 red (tile3,1)", fb(interp2, 28, 12), 0x16)
check("scrolled: BL quadrant -> group2 green", fb(interp2, 0, 16), 0x2A)
check("scrolled: BL quadrant -> group2 green (tile1,3)", fb(interp2, 12, 28), 0x2A)
check("scrolled: BR quadrant -> group3 white", fb(interp2, 16, 16), 0x30)
check("scrolled: BR quadrant -> group3 white (tile3,3)", fb(interp2, 28, 28), 0x30)


# =====================================================================
# 2) Palette RAM mirroring: $3F10/$3F14/$3F18/$3F1C -> $3F00/$3F04/$3F08/$3F0C
# =====================================================================
print("\n--- palette RAM mirroring ($3F10/14/18/1C -> $3F00/04/08/0C) ---")
interp3 = fresh()
interp3.lists["CHR"] = [0] * 8192
interp3.lists["VRAM"] = [0] * 2048
PAL3 = [0] * 32
PAL3[0x00] = 0x0F  # $3F00
PAL3[0x04] = 0x11  # $3F04
PAL3[0x08] = 0x21  # $3F08
PAL3[0x0C] = 0x31  # $3F0C
PAL3[0x10] = 0x99  # $3F10 -- should be IGNORED, mirror of $3F00 applies
PAL3[0x14] = 0x99
PAL3[0x18] = 0x99
PAL3[0x1C] = 0x99
interp3.lists["PAL"] = PAL3
interp3.vars["CHRRAM"] = 0
interp3.vars["MAPPER"] = 0
interp3.vars["MIRROR"] = 0


def ppu_read(interp, a):
    interp.call_proc_by_name("ppu_read %s", {"a": a})
    return i_(interp.vars["RESULT"])


check("$3F00 direct read", ppu_read(interp3, 0x3F00), 0x0F)
check("$3F10 mirrors $3F00 (not its own stored 0x99)", ppu_read(interp3, 0x3F10), 0x0F)
check("$3F04 direct read", ppu_read(interp3, 0x3F04), 0x11)
check("$3F14 mirrors $3F04", ppu_read(interp3, 0x3F14), 0x11)
check("$3F08 direct read", ppu_read(interp3, 0x3F08), 0x21)
check("$3F18 mirrors $3F08", ppu_read(interp3, 0x3F18), 0x21)
check("$3F0C direct read", ppu_read(interp3, 0x3F0C), 0x31)
check("$3F1C mirrors $3F0C", ppu_read(interp3, 0x3F1C), 0x31)
# non-mirrored sprite-palette entries must NOT be affected by this rule
PAL3[0x11] = 0x25
check("$3F11 (sprite group0 color1, NOT a mirror slot) reads its own value",
      ppu_read(interp3, 0x3F11), 0x25)


# =====================================================================
# 3) Sprite attribute palette-select bits -> all 4 sprite palettes
# =====================================================================
print("\n--- sprite palette-select bits -> all 4 sprite palettes ---")
interp4 = fresh()
CHR4 = [0] * 8192
for row in range(8):
    CHR4[row] = 0xFF
    CHR4[8 + row] = 0xFF  # tile0: solid, color-index3 everywhere
interp4.lists["CHR"] = CHR4
interp4.lists["VRAM"] = [2] * 2048  # transparent background (tile2, all-zero CHR data)
PAL4 = [0] * 32
PAL4[0] = 0x0F  # universal bg
# sprite palette group0 ($3F11-13), group1 ($3F15-17), group2 ($3F19-1B), group3 ($3F1D-1F)
PAL4[16 + 0 * 4 + 3] = 0x21  # group0 color3 = blue
PAL4[16 + 1 * 4 + 3] = 0x16  # group1 color3 = red
PAL4[16 + 2 * 4 + 3] = 0x2A  # group2 color3 = green
PAL4[16 + 3 * 4 + 3] = 0x30  # group3 color3 = white
interp4.lists["PAL"] = PAL4
interp4.vars["CHRRAM"] = 0
interp4.vars["CHRBANKS"] = 1
interp4.lists["C1"] = list(range(8))
interp4.vars["MAPPER"] = 0
interp4.vars["MIRROR"] = 0
interp4.vars["P_CTRL"] = 0

interp4.call_proc_by_name("render_bg_region %s %s %s %s",
                           {"row0": 0, "row1": 30, "col0": 0, "col1": 32})

expected = [0x21, 0x16, 0x2A, 0x30]
for group in range(4):
    OAM = [0xFF] * 256
    OAM[0] = 10
    OAM[1] = 0
    OAM[2] = group  # attr bits0-1 = palette select, rest 0 (front priority, no flip)
    OAM[3] = 20 + group * 10
    interp4.lists["OAM"] = OAM
    interp4.vars["P_STATUS"] = 0
    interp4.call_proc_by_name("render_sprites_line %s", {"sl": 11})
    got = fb(interp4, 20 + group * 10, 11)
    check("sprite palette-select=%d -> group%d color (not flat/wrong)" % (group, group),
          got, expected[group])
    # explicitly rule out the "degenerates to universal bg / flat black" failure mode
    check("sprite palette-select=%d does NOT resolve to universal-bg PAL[0]" % group,
          got != 0x0F, True)


# =====================================================================
# 4) Master NES palette table (PALRGB) spot-checks against known real
# hardware values (standard 2C02 palette, decimal RGB).
# =====================================================================
print("\n--- master palette table (PALRGB) spot-checks ---")
interp5 = fresh()
palrgb = interp5.lists["PALRGB"]
check("PALRGB length", len(palrgb), 64)
known = {
    0x00: 0x666666,  # a mid-grey
    0x0F: 0x000000,  # black
    0x20: 0xFFFEFF,  # near-white
    0x21: 0x64B0FF,  # a light blue (used as our "blue" marker throughout these tests)
    0x16: 0xB53120,  # a dark red/brick (used as our "red" marker)
    0x2A: 0x5CE430,  # a green (used as our "green" marker)
    0x30: 0xFFFEFF,  # white
}
for idx, want_rgb in known.items():
    got_rgb = i_(palrgb[idx])
    check("PALRGB[0x%02X] matches known 2C02 value" % idx, got_rgb, want_rgb)

print("\n%s" % ("ALL PALETTE CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
