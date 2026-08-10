"""Phase 6a: PPU background-rendering verification, same approach as the CPU
and mapper suites -- set up a known nametable/attribute-table/pattern-table/
palette-RAM configuration, call the real generated render_bg_region/
render_bg_frame procs via interp.py (walks the actual block graph), and check
specific FB pixels against hand-computed expected palette indices.
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


e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
interp = Interp(e.proj, max_steps=50_000_000)

# ---- CHR-ROM: tile 0 = solid color-index 3 (both plane bits set on every
# pixel); tile 1 = a vertical-stripe pattern (left half color1, right half
# color2, using only plane0 or plane1 set); tile 2 = all-transparent (index 0).
CHR = [0] * 8192
# tile 0: 16 bytes at offset 0. plane0 bytes = 0xFF (all bits 1), plane1 = 0xFF.
for row in range(8):
    CHR[0 * 16 + row] = 0xFF        # plane0
    CHR[0 * 16 + 8 + row] = 0xFF    # plane1
# tile 1: left nibble (pixels 0-3, the high nibble/bit7-4) plane0=1 plane1=0
# -> color index 1; right nibble (pixels 4-7) plane0=0 plane1=1 -> color index 2.
for row in range(8):
    CHR[1 * 16 + row] = 0xF0        # plane0: bits7-4 set -> pixels 0-3 have bit0=1
    CHR[1 * 16 + 8 + row] = 0x0F    # plane1: bits3-0 set -> pixels 4-7 have bit1=1
# tile 2: all zero (already zeroed) -> color index 0 (transparent/universal bg)
interp.lists["CHR"] = CHR
interp.vars["CHRRAM"] = 0
interp.vars["CHRBANKS"] = 1
interp.lists["C1"] = list(range(8))
interp.vars["MAPPER"] = 0
interp.vars["MIRROR"] = 0  # horizontal
interp.vars["P_CTRL"] = 0  # BG pattern table = $0000

# ---- palette RAM: universal bg = NES palette entry 0x0F (black); palette
# group 0 (used by attribute quadrant with pal_select=0) = [0x0F, 0x16, 0x27, 0x30];
# palette group 2 (pal_select=2) = [0x0F, 0x11, 0x21, 0x31].
PAL = [0] * 32
PAL[0] = 0x0F
PAL[1], PAL[2], PAL[3] = 0x16, 0x27, 0x30      # group 0 (indices 1-3)
PAL[9], PAL[10], PAL[11] = 0x11, 0x21, 0x31    # group 2 (indices 9-11, i.e. palsel 2)
interp.lists["PAL"] = PAL

# ---- nametable (VRAM): tile (0,0) = tile-id 0 (solid color3), tile (1,0) =
# tile-id 1 (stripe 1/2), tile (0,1) = tile-id 2 (transparent/universal bg).
# All other tiles = tile-id 2 as well (keep the rest of the screen quiet).
VRAM = [2] * 2048
def nt_set(col, row, tileid):
    VRAM[row * 32 + col] = tileid
nt_set(0, 0, 0)
nt_set(1, 0, 1)
nt_set(0, 1, 2)
# attribute table at $23C0 (VRAM offset 0x3C0): tile(0,0)/(1,0) are in the
# top-left 4x4-tile block (quadrant shift 0, since row<2,col<2) -> use
# palette group 0 (attr bits 0-1 = 00). Force the whole attribute byte to 0
# except we want a DIFFERENT palette group visible somewhere for a second
# check: tile (0,1) is also in the same 4x4 block (row=1<2) so it shares the
# same attribute byte -- but tile2 is transparent so palette group doesn't
# matter there (always resolves to the universal bg color). Set a second
# attribute byte (for tile block starting at col 4) to select group 2, and
# place a solid tile-3-style tile... simplify: just verify group-0 resolution
# here; a second nametable check below covers a different attribute quadrant.
VRAM[0x3C0 + 0] = 0b00000000  # attribute byte for tile-block (0,0): all quadrants group0
# tile at col=4,row=0 (still within tile-block col 0-3? col4 is block-col1) ->
# attribute byte at (0x3C0 + block_row*8 + block_col) with block_col=1
nt_set(4, 0, 0)  # solid color-3 tile again, but in a block using group 2
VRAM[0x3C0 + 1] = 0b00000010  # bits0-1 = 10 = 2 -> quadrant(0,0) of this block = group2
interp.lists["VRAM"] = VRAM

# ---- render a small region covering the tiles we set up (rows 0-1, cols 0-5) ----
interp.call_proc_by_name("render_bg_region %s %s %s %s",
                          {"row0": 0, "row1": 2, "col0": 0, "col1": 6})


def fb_pixel(x, y):
    return i_(interp.lists["FB"][y * 256 + x])


# tile(0,0) = solid tile-id 0 (color index 3 everywhere), group 0 -> PAL[0*4+3]=PAL[3]=0x30
check("tile(0,0) top-left pixel", fb_pixel(0, 0), 0x30)
check("tile(0,0) bottom-right pixel (7,7)", fb_pixel(7, 7), 0x30)

# tile(1,0) = stripe tile-id1, group0: left half (px0-3) color-index1 -> PAL[1]=0x16;
# right half (px4-7) color-index2 -> PAL[2]=0x27. Tile starts at x=8.
check("tile(1,0) left-stripe pixel (x=8,y=0)", fb_pixel(8, 0), 0x16)
check("tile(1,0) left-stripe pixel (x=11,y=3)", fb_pixel(11, 3), 0x16)
check("tile(1,0) right-stripe pixel (x=12,y=0)", fb_pixel(12, 0), 0x27)
check("tile(1,0) right-stripe pixel (x=15,y=7)", fb_pixel(15, 7), 0x27)

# tile(0,1) = transparent tile-id2 -> universal bg color regardless of palette
# group. Tile starts at y=8.
check("tile(0,1) transparent pixel -> universal bg", fb_pixel(0, 8), 0x0F)
check("tile(0,1) transparent pixel 2 -> universal bg", fb_pixel(5, 12), 0x0F)

# tile(4,0) = solid tile-id0 but in attribute quadrant selecting group 2 ->
# PAL[2*4+3] = PAL[11] = 0x31. Tile starts at x=32.
check("tile(4,0) solid pixel, palette group2", fb_pixel(32, 0), 0x31)
check("tile(4,0) solid pixel group2 (39,7)", fb_pixel(39, 7), 0x31)

# a tile we never touched (col2,row0 = default tile-id2, transparent) should
# still resolve to the universal bg color -- sanity check the "untouched
# default" path works too.
check("untouched default tile -> universal bg", fb_pixel(16, 0), 0x0F)

print("\n%s" % ("ALL PPU BG CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
