"""Run the real ROM to frame N, then render the raw nametables DIRECTLY in
pure Python (bypassing the entire block-graph scroll/fetch pipeline).

Purpose: split the bug in half. If the directly-rendered nametable looks like
a correct SMB screen, then VRAM content is right and the bug lives in the
scrolled fetch path (render_bg_line_scrolled / bg_setup_tile_v / loopy regs).
If the nametable is already garbage, the bug is upstream in CPU/PPU writes.
"""
import os
import struct
import sys
import zlib

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
import ines_loader as INES
from interp import Interp

rom = sys.argv[1]
target = int(sys.argv[2]) if len(sys.argv) > 2 else 40
outdir = r"D:\KittyNES\progress\framedumps"
os.makedirs(outdir, exist_ok=True)


def write_png(path, w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)
with open(rom, "rb") as f:
    INES.load_rom_into_emu(e, f.read())

it = Interp(e.proj, max_steps=None)
it.call_proc_by_name("nes_init")
for fr in range(target):
    it.call_proc_by_name("run_frame")
print("reached frame", target)

V = it.vars
L = it.lists
vram = [int(x or 0) for x in L["VRAM"]]
chr_ = [int(x or 0) for x in L["CHR"]]
pal = [int(x or 0) for x in L["PAL"]]
palrgb = [int(x or 0) for x in L["PALRGB"]]

print("P_CTRL=%s P_MASK=%s P_V=%s P_T=%s P_X=%s" %
      (V.get("P_CTRL"), V.get("P_MASK"), V.get("P_V"), V.get("P_T"), V.get("P_X")))
print("MIRROR=%s CHRBANKS=%s C1=%s P8=%s" %
      (V.get("MIRROR"), V.get("CHRBANKS"),
       [int(x or 0) for x in L["C1"]], [int(x or 0) for x in L["P8"]]))
print("PAL RAM:", pal)
print("nametable0 first 64 tiles:", vram[0:64])
print("nametable0 attr (last 64 of page0):", vram[0x3C0:0x400])

patbase = 4096 if (int(V.get("P_CTRL") or 0) // 16) % 2 == 1 else 0
print("BG patbase =", patbase)

c1 = [int(x or 0) for x in L["C1"]]


def chr_read(a):
    i = c1[a // 1024] * 1024 + (a % 1024)
    return chr_[i] if 0 <= i < len(chr_) else 0


def rgb(pi):
    v = palrgb[pi] if 0 <= pi < len(palrgb) else 0
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


for page in (0, 1):
    base = page * 1024
    rows = []
    for y in range(240):
        row = []
        trow, py = y // 8, y % 8
        for x in range(256):
            tcol, px = x // 8, x % 8
            tile = vram[base + trow * 32 + tcol]
            attr = vram[base + 0x3C0 + (trow // 4) * 8 + (tcol // 4)]
            shift = ((trow // 2) % 2) * 4 + ((tcol // 2) % 2) * 2
            palsel = (attr >> shift) & 3
            p0 = chr_read(patbase + tile * 16 + py)
            p1 = chr_read(patbase + tile * 16 + py + 8)
            ci = ((p0 >> (7 - px)) & 1) + (((p1 >> (7 - px)) & 1) << 1)
            pi = pal[0] if ci == 0 else pal[palsel * 4 + ci]
            r, g, b = rgb(pi & 0x3F)
            row += [r, g, b]
        rows.append(row)
    p = os.path.join(outdir, f"nt{page}_direct_f{target}.png")
    write_png(p, 256, 240, rows)
    print("wrote", p)
