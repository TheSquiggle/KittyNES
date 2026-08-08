"""Run a real ROM through interp.py and dump the FB framebuffer as PNG images.

This is the decisive diagnostic for the "sprites/tiles show wrong colors"
bug: if the Python-side FB renders CORRECTLY here, the emulation logic is
fine and the corruption is happening in real Scratch/TurboWarp's pen
rendering. If FB renders WRONG here, the bug is in our logic and we can
see it directly instead of guessing.

Usage: python dump_frames.py <rom.nes> <max_frames> [outdir]
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
maxframes = int(sys.argv[2]) if len(sys.argv) > 2 else 20
outdir = sys.argv[3] if len(sys.argv) > 3 else r"D:\KittyNES\progress\framedumps"
os.makedirs(outdir, exist_ok=True)


def write_png(path, w, h, rows):
    """rows = list of h rows, each a flat list of 3*w ints (RGB)."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def to_rgb(val):
    """PALRGB entries -> (r,g,b). Accept int 0xRRGGBB or '#rrggbb' string."""
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("#"):
            s = s[1:]
        try:
            val = int(s, 16)
        except ValueError:
            try:
                val = int(float(s))
            except ValueError:
                return (255, 0, 255)
    try:
        v = int(val)
    except (TypeError, ValueError):
        return (255, 0, 255)
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


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

interp = Interp(e.proj, max_steps=None)
palrgb = interp.lists.get("PALRGB", [])
print("PALRGB len:", len(palrgb), "sample:", palrgb[:4])

interp.call_proc_by_name("nes_init")
print("after nes_init: PC=", interp.vars.get("PC"))

import time
t0 = time.time()
for fr in range(1, maxframes + 1):
    interp.call_proc_by_name("run_frame")
    fb = interp.lists.get("FB", [])
    distinct = len(set(fb))
    pmask = interp.vars.get("P_MASK")
    print(f"frame {fr:4d}  t={time.time()-t0:6.1f}s  distinct_FB_vals={distinct:3d}  P_MASK={pmask}", flush=True)

    rows = []
    for y in range(240):
        row = []
        for x in range(256):
            idx = fb[y * 256 + x] if y * 256 + x < len(fb) else 0
            try:
                pi = int(idx)
            except (TypeError, ValueError):
                pi = 0
            entry = palrgb[pi] if 0 <= pi < len(palrgb) else 0
            r, g, b = to_rgb(entry)
            row += [r, g, b]
        rows.append(row)
    write_png(os.path.join(outdir, f"frame_{fr:04d}.png"), 256, 240, rows)

print("done, wrote PNGs to", outdir)
