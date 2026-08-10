"""Follow-up bug hunt #4: after three rounds of exhaustive testing (tile
position/scrolling, tile identity/CHR-bank-awareness, and palette/color
resolution) all came back clean, re-examine the ONE part of the rendering
pipeline that has never actually been verified at all: flush_fb_to_pen /
flush_fb_row, the code that draws the computed FB framebuffer to the stage
via Pen. interp.py previously treated all pen_* opcodes as pure no-ops
(needed for building/running everything else, since pen output can't be
inspected programmatically), which means the run-length line-drawing logic
itself was NEVER checked against the FB contents it's supposed to
reproduce -- only FB's own contents were ever verified. A bug in the
run-length scan (which pen_setPenColorToColor/motion_gotoxy/pen_penDown/
pen_penUp calls actually happen and in what order/at what coordinates)
would look exactly like "correct tile shape, wrong/speckled color" on
screen, since FB itself would be right but what gets DRAWN from it would
be wrong.

New in interp.py this session: motion_gotoxy while pen is down now records
a (start_xy, end_xy, color) segment in self.pen_runs (previously only
pen_penDown's start position was recorded, with no way to know where a
line actually ended). This test replays those recorded segments into a
synthetic canvas and compares pixel-by-pixel against FB itself -- the
first real verification of this code path.
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
    return Interp(e.proj, max_steps=200_000_000)


def replay_pen_runs_to_canvas(pen_runs, palrgb):
    """Reconstruct a 256x240 canvas (NES pixel index -> resolved RGB) from
    the recorded pen line segments, using the SAME coordinate mapping
    flush_fb_row itself uses (FB x(0..255) -> stage x(-128..127), FB
    y(0..239) -> stage y(119..-120)), inverted back to FB space."""
    canvas = [None] * (256 * 240)
    for (x0, y0), (x1, y1), color in pen_runs:
        row = 119 - int(round(y0))
        if not (0 <= row < 240):
            continue
        # our runs are always horizontal (fixed y, x0..x1) -- but don't
        # assume that; just fill the x-range at this row either way.
        xs = int(round(x0)) + 128
        xe = int(round(x1)) + 128
        lo, hi = min(xs, xe), max(xs, xe)
        for x in range(lo, hi + 1):
            if 0 <= x < 256:
                canvas[row * 256 + x] = i_(color)
    return canvas


# =====================================================================
# Build a deliberately "busy" scene: lots of short same-row color runs
# (mimicking a detailed, high-color-frequency texture like a brick
# pattern), several different background palettes across attribute
# quadrants, AND sprites on top -- then flush to Pen and verify every
# single pixel the run-length algorithm actually draws matches FB.
# =====================================================================
print("--- pen-flush run-length reconstruction vs FB, busy/high-frequency scene ---")
interp = fresh()

# 4 distinct 8x8 tiles, each with a different fine per-pixel checker-ish
# bit pattern (so color changes happen frequently within and across tiles
# -- much busier than any prior test's flat/simple patterns)
CHR = [0] * 8192
patterns = [0b10101010, 0b01010101, 0b11001100, 0b00110011]
for t, p0 in enumerate(patterns):
    for row in range(8):
        CHR[t * 16 + row] = p0 ^ (row & 1) * 0xFF  # varies per row too
        CHR[t * 16 + 8 + row] = (~p0) & 0xFF
interp.lists["CHR"] = CHR

VRAM = [0] * 2048
for row in range(4):
    for col in range(8):
        VRAM[row * 32 + col] = (row * 8 + col) % 4  # cycle through the 4 tiles
# vary the attribute bytes across this region so multiple background
# palettes are in play (block(0,0) covers tile rows/cols 0-3)
VRAM[0x3C0 + 0] = 0b11100100  # block col0: 4 distinct quadrant palettes
VRAM[0x3C0 + 1] = 0b00011011  # block col1: different assignment
interp.lists["VRAM"] = VRAM

PAL = [0] * 32
for g in range(4):
    PAL[g * 4 + 1] = 0x10 + g * 4   # color-index1 per group
    PAL[g * 4 + 2] = 0x20 + g * 4   # color-index2 per group
    PAL[g * 4 + 3] = 0x30 + g * 4   # color-index3 per group
interp.lists["PAL"] = PAL
interp.vars["CHRRAM"] = 0
interp.vars["CHRBANKS"] = 1
interp.lists["C1"] = list(range(8))
interp.vars["MAPPER"] = 0
interp.vars["MIRROR"] = 0
interp.vars["P_CTRL"] = 0

interp.call_proc_by_name("render_bg_region %s %s %s %s",
                          {"row0": 0, "row1": 4, "col0": 0, "col1": 16})

# add a couple of sprites on top too, for good measure
OAM = [0xFF] * 256
OAM[0] = 10
OAM[1] = 1
OAM[2] = 0x01
OAM[3] = 5
OAM[4] = 15
OAM[5] = 2
OAM[6] = 0x02
OAM[7] = 40
interp.lists["OAM"] = OAM
for sl in range(32):
    interp.call_proc_by_name("render_sprites_line %s", {"sl": sl})

# ---- flush to Pen and reconstruct what actually got drawn ----
interp.call_proc_by_name("flush_fb_to_pen")

canvas = replay_pen_runs_to_canvas(interp.pen_runs, interp.lists["PALRGB"])
fb = interp.lists["FB"]
palrgb = interp.lists["PALRGB"]

mismatches = []
undrawn = []
for idx in range(256 * 240):
    # FB stores a raw palette INDEX (0-63); flush_fb_row resolves it
    # through PALRGB before drawing, so the pen-drawn color is an RGB
    # value -- resolve FB the same way before comparing.
    fb_val = i_(palrgb[i_(fb[idx])])
    canvas_val = canvas[idx]
    if canvas_val is None:
        undrawn.append(idx)
    elif canvas_val != fb_val:
        mismatches.append((idx % 256, idx // 256, fb_val, canvas_val))

check("pen-flush reconstruction: every pixel got drawn (no gaps)", len(undrawn), 0)
check("pen-flush reconstruction: every drawn pixel matches FB exactly",
      len(mismatches), 0)
if mismatches:
    print("  first 10 mismatches (x,y,fb_expected,pen_drew):", mismatches[:10])
if undrawn:
    print("  first 10 undrawn pixel indices (x,y):",
          [(i % 256, i // 256) for i in undrawn[:10]])

check("pen_runs recorded at least one segment (sanity: flush actually ran)",
      len(interp.pen_runs) > 0, True)
print("  total pen line segments drawn for this scene:", len(interp.pen_runs))

print("\n%s" % ("ALL PEN-FLUSH CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
