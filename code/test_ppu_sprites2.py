"""Phase 6b follow-up: targeted correctness checks prompted by real-ROM
sprite garbling reported against Super Mario Bros. + Duck Hunt. Covers
things a hand-picked synthetic OAM scenario (test_ppu_sprites.py) may not
have exercised thoroughly: OAM DMA ($4014), 8x16 sprite mode addressing,
and horizontal/vertical flip in combination -- each isolated so a bug in
one doesn't mask/confuse a bug in another. Same interp.py-against-real-
block-graph rigor as every other suite.
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
    e = Emu("CPU")
    BC.declare_state(e)
    BC.phase1_tables(e)
    BC.phase2_bus(e)
    BC.phase3_cpu(e)
    BC.phase6_ppu_bg(e)
    BC.phase6b_sprites(e)
    return Interp(e.proj, max_steps=50_000_000)


def bus_write(interp, a, v):
    interp.call_proc_by_name("bus_write %s %s", {"a": a, "v": v})


def bus_read(interp, a):
    interp.call_proc_by_name("bus_read %s", {"a": a})
    return i_(interp.vars["RESULT"])


# =====================================================================
# 1) OAM DMA ($4014): copy a full 256-byte page from CPU RAM into OAM
# =====================================================================
print("--- OAM DMA ($4014) ---")
interp = fresh()
interp.lists["VRAM"] = [0] * 2048
interp.lists["CHR"] = [0] * 8192
interp.lists["PAL"] = [0] * 32
interp.vars["CHRRAM"] = 0
interp.vars["MAPPER"] = 0
interp.vars["MIRROR"] = 0

# fill CPU RAM page 2 ($0200-$02FF) with a recognizable, position-dependent
# pattern so a byte-offset bug or truncated copy is actually detectable
RAM = [0] * 2048
for i in range(256):
    RAM[0x200 + i] = (i * 3 + 7) % 256
interp.lists["RAM"] = RAM

interp.vars["P_OAMADDR"] = 0
bus_write(interp, 0x4014, 0x02)  # DMA source page $02 -> $0200-$02FF

oam = interp.lists["OAM"]
mismatches = [i for i in range(256) if i_(oam[i]) != (i * 3 + 7) % 256]
check("OAM DMA copied all 256 bytes correctly (OAMADDR=0)", mismatches, [])
check("CYCLES charged for DMA (513)", i_(interp.vars["CYCLES"]), 513)

# ---- DMA with a nonzero starting OAMADDR must wrap the destination ----
interp2 = fresh()
interp2.lists["VRAM"] = [0] * 2048
interp2.lists["CHR"] = [0] * 8192
interp2.lists["PAL"] = [0] * 32
interp2.vars["CHRRAM"] = 0
interp2.vars["MAPPER"] = 0
interp2.vars["MIRROR"] = 0
RAM2 = [0] * 2048
for i in range(256):
    RAM2[0x300 + i] = (i * 5 + 1) % 256
interp2.lists["RAM"] = RAM2
interp2.vars["P_OAMADDR"] = 200  # DMA should start writing at OAM[200], wrapping past 255
bus_write(interp2, 0x4014, 0x03)
oam2 = interp2.lists["OAM"]
ok = True
for i in range(256):
    dest = (200 + i) % 256
    if i_(oam2[dest]) != (i * 5 + 1) % 256:
        ok = False
        break
check("OAM DMA wraps destination correctly when OAMADDR != 0", ok, True)


# =====================================================================
# 2) 8x16 sprite mode addressing: bit0 of tile index selects the pattern
# table (NOT PPUCTRL bit3, which only applies in 8x8 mode); the top 7 bits
# select a tile PAIR (even=top half, odd+1=bottom half).
# =====================================================================
print("\n--- 8x16 sprite mode addressing ---")
interp3 = fresh()
CHR = [0] * 8192
# bank 0 (pattern table $0000): tile 4 (even) = "top" marker (plane0=0xAA
# every row), tile 5 (odd, = tile4+1) = "bottom" marker (plane0=0x55)
for row in range(8):
    CHR[4 * 16 + row] = 0xAA
    CHR[5 * 16 + row] = 0x55
# bank 1 (pattern table $1000, offset 4096): tile 6 (even) = 0xCC, tile 7 = 0x33
for row in range(8):
    CHR[4096 + 6 * 16 + row] = 0xCC
    CHR[4096 + 7 * 16 + row] = 0x33
interp3.lists["CHR"] = CHR
interp3.lists["VRAM"] = [0] * 2048
interp3.lists["PAL"] = [0] * 32
interp3.vars["CHRRAM"] = 0
interp3.vars["CHRBANKS"] = 1
interp3.vars["CHRB0"] = 0
interp3.vars["CHRB1"] = 1
interp3.vars["MAPPER"] = 0
interp3.vars["MIRROR"] = 0
interp3.vars["P_CTRL"] = 0x20  # bit5 set -> 8x16 sprites (bit3=0, must be IGNORED in this mode)

OAM = [0xFF] * 256
# sprite 0: Y=10 (visible rows 11-26 for a 16px-tall sprite), tile index=4
# (EVEN -> bit0=0 -> pattern table BANK 0 per the spec), attr=0 (no flip)
OAM[0] = 10
OAM[1] = 4
OAM[2] = 0x00
OAM[3] = 20
interp3.lists["OAM"] = OAM

interp3.call_proc_by_name("sprite_eval_line %s", {"sl": 11})  # row 0 of the sprite (top half, tile4)
check("8x16 top half (row0) uses tile4's plane0 (bank0, even tile)",
      i_(interp3.lists["SPRLO"][0]), 0xAA)

interp3.call_proc_by_name("sprite_eval_line %s", {"sl": 19})  # row 8 of the sprite (bottom half, tile5)
check("8x16 bottom half (row8) uses tile5's plane0 (bank0, odd=even+1)",
      i_(interp3.lists["SPRLO"][0]), 0x55)

# ---- now an ODD tile index (bit0=1) -> pattern table BANK 1, tile pair
# (tile&0xFE, (tile&0xFE)+1) = (6,7), regardless of PPUCTRL bit3 ----
OAM2 = [0xFF] * 256
OAM2[0] = 10
OAM2[1] = 7  # odd -> bank1 (bit0=1); tile pair = 6 (top), 7 (bottom)
OAM2[2] = 0x00
OAM2[3] = 20
interp3.lists["OAM"] = OAM2
interp3.call_proc_by_name("sprite_eval_line %s", {"sl": 11})  # top half -> tile 6
check("8x16 odd tile index: bit0 selects bank1 (not PPUCTRL bit3)",
      i_(interp3.lists["SPRLO"][0]), 0xCC)
interp3.call_proc_by_name("sprite_eval_line %s", {"sl": 19})  # bottom half -> tile 7
check("8x16 odd tile index: bottom half uses tile+1 within bank1",
      i_(interp3.lists["SPRLO"][0]), 0x33)

# ---- PPUCTRL bit3 must be IGNORED in 8x16 mode: flip it and confirm no change ----
interp3.vars["P_CTRL"] = 0x28  # bit5 (8x16) + bit3 (would normally mean bank1 in 8x8 mode)
interp3.lists["OAM"] = OAM  # back to the even-tile (bank0) sprite
interp3.call_proc_by_name("sprite_eval_line %s", {"sl": 11})
check("8x16 mode ignores PPUCTRL bit3 (still bank0 for even tile)",
      i_(interp3.lists["SPRLO"][0]), 0xAA)


# =====================================================================
# 3) Horizontal + vertical flip, independently and combined
# =====================================================================
print("\n--- flip (horizontal/vertical/both) ---")
interp4 = fresh()
CHR2 = [0] * 8192
# tile 0: an asymmetric pattern so flips are unambiguously detectable.
# plane0 row0 = 0b10000000 (only leftmost pixel set), row7 = 0b00000001
# (only rightmost pixel set) -- flipping vertically swaps which row has
# which pattern; flipping horizontally swaps which column within a row.
CHR2[0] = 0b10000000  # row 0
for r in range(1, 7):
    CHR2[r] = 0
CHR2[7] = 0b00000001  # row 7
for row in range(8):
    CHR2[8 + row] = 0  # plane1 all zero -> color index is 0 or 1 only
interp4.lists["CHR"] = CHR2
interp4.lists["VRAM"] = [0] * 2048
PAL4 = [0] * 32
PAL4[0] = 0x0F  # universal bg = black
PAL4[16 + 1] = 0x21  # sprite palette group0, color-index1 = blue marker
interp4.lists["PAL"] = PAL4
interp4.vars["CHRRAM"] = 0
interp4.vars["CHRBANKS"] = 1
interp4.vars["CHRB0"] = 0
interp4.vars["CHRB1"] = 1
interp4.vars["MAPPER"] = 0
interp4.vars["MIRROR"] = 0
interp4.vars["P_CTRL"] = 0  # 8x8 sprites


def render_one_sprite(interp, attr):
    OAM = [0xFF] * 256
    OAM[0] = 10
    OAM[1] = 0
    OAM[2] = attr
    OAM[3] = 20
    interp.lists["OAM"] = OAM
    interp.vars["P_STATUS"] = 0
    interp.call_proc_by_name("render_bg_region %s %s %s %s",
                              {"row0": 0, "row1": 30, "col0": 0, "col1": 32})
    for sl in range(11, 19):
        interp.call_proc_by_name("render_sprites_line %s", {"sl": sl})


def fb(interp, x, y):
    return i_(interp.lists["FB"][y * 256 + x])


# no flip: row0(top, sl=11) has the marker pixel at LEFTMOST column (x=20);
# row7(bottom, sl=18) has it at RIGHTMOST column (x=27)
render_one_sprite(interp4, 0x00)
check("no flip: top row, marker at leftmost pixel", fb(interp4, 20, 11), 0x21)
check("no flip: top row, no marker at rightmost pixel", fb(interp4, 27, 11), 0x0F)
check("no flip: bottom row, marker at rightmost pixel", fb(interp4, 27, 18), 0x21)
check("no flip: bottom row, no marker at leftmost pixel", fb(interp4, 20, 18), 0x0F)

# horizontal flip only (bit6): top row's marker should move to RIGHTMOST
render_one_sprite(interp4, 0x40)
check("hflip: top row, marker moves to rightmost pixel", fb(interp4, 27, 11), 0x21)
check("hflip: top row, no marker at leftmost pixel", fb(interp4, 20, 11), 0x0F)
check("hflip: bottom row unaffected in Y, marker still rightmost-flipped-to-leftmost",
      fb(interp4, 20, 18), 0x21)

# vertical flip only (bit7): the ROW patterns swap (row0's pattern now
# appears at the sprite's LAST scanline, and vice versa)
render_one_sprite(interp4, 0x80)
check("vflip: sprite's top scanline (sl=11) now shows row7's pattern (rightmost)",
      fb(interp4, 27, 11), 0x21)
check("vflip: sprite's bottom scanline (sl=18) now shows row0's pattern (leftmost)",
      fb(interp4, 20, 18), 0x21)

# both flips combined (0xC0): row7's pattern (normally rightmost) appears
# at the top scanline AND horizontally mirrored -> leftmost
render_one_sprite(interp4, 0xC0)
check("hflip+vflip: top scanline shows row7's pattern, horizontally mirrored to leftmost",
      fb(interp4, 20, 11), 0x21)
check("hflip+vflip: bottom scanline shows row0's pattern, horizontally mirrored to rightmost",
      fb(interp4, 27, 18), 0x21)

# =====================================================================
# 4) Fine-X sub-tile horizontal scrolling (the actual fix landed while
# investigating this report -- SMB is a side-scroller, and the background
# renderer previously only supported coarse 8px-granularity scrolling
# (documented Phase 6b limitation). Sprites are drawn at their true
# absolute per-pixel OAM X/Y and were NEVER affected by that gap, so a
# scrolling background snapping in 8px jumps against smoothly-moving
# sprites would visually read as "sprites are wrong" even though the
# sprite math itself (parts 1-3 above) checks out clean. Verifies
# render_bg_line_scrolled actually shifts the visible pixels by P_X.
# =====================================================================
print("\n--- fine-X sub-tile horizontal scroll ---")
interp5 = fresh()
CHR3 = [0] * 8192
for row in range(8):
    CHR3[16 + row] = 0xF0        # tile1 plane0: left nibble set -> color-index1 on bits0-3
    CHR3[16 + 8 + row] = 0x0F    # tile1 plane1: right nibble set -> color-index2 on bits4-7
VRAM5 = [1] * 2048
VRAM5[0x3C0] = 0
interp5.lists["CHR"] = CHR3
interp5.lists["VRAM"] = VRAM5
PAL5 = [0] * 32
PAL5[0] = 0x0F
PAL5[1] = 0x16  # color-index1 marker
PAL5[2] = 0x27  # color-index2 marker
interp5.lists["PAL"] = PAL5
interp5.vars["CHRRAM"] = 0
interp5.vars["CHRBANKS"] = 1
interp5.vars["CHRB0"] = 0
interp5.vars["CHRB1"] = 1
interp5.vars["MAPPER"] = 0
interp5.vars["MIRROR"] = 0
interp5.vars["P_CTRL"] = 0

# baseline (P_X=0): pattern repeats every 8px as [1,1,1,1,2,2,2,2]
interp5.vars["P_T"] = 0
interp5.vars["P_V"] = 0
interp5.vars["P_X"] = 0
interp5.call_proc_by_name("render_bg_line_scrolled %s", {"sl": 0})
baseline = [fb(interp5, x, 0) for x in range(16)]
check("fine-X=0 baseline pattern", baseline,
      [0x16, 0x16, 0x16, 0x16, 0x27, 0x27, 0x27, 0x27,
       0x16, 0x16, 0x16, 0x16, 0x27, 0x27, 0x27, 0x27])

# P_X=3: every screen pixel should show the tile bit 3 positions further
# right (i.e. the whole pattern shifts LEFT by 3 relative to the baseline)
interp5.vars["P_T"] = 0
interp5.vars["P_V"] = 0
interp5.vars["P_X"] = 3
interp5.call_proc_by_name("render_bg_line_scrolled %s", {"sl": 0})
shifted = [fb(interp5, x, 0) for x in range(16)]
check("fine-X=3 shifts the pattern left by 3px (matches bits[3:] of the baseline)",
      shifted, baseline[3:] + [0x16, 0x16, 0x16])  # tile repeats, so bits wrap to color1 again

# P_X=7 (max fine-X): shifts by 7, should pull almost an entire tile ahead
interp5.vars["P_T"] = 0
interp5.vars["P_V"] = 0
interp5.vars["P_X"] = 7
interp5.call_proc_by_name("render_bg_line_scrolled %s", {"sl": 0})
shifted7 = [fb(interp5, x, 0) for x in range(16)]
check("fine-X=7 shifts the pattern left by 7px", shifted7, baseline[7:] + baseline[:7])

print("\n%s" % ("ALL SPRITE-BUG-HUNT CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
