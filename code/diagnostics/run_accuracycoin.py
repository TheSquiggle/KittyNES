"""Boot AccuracyCoin, press a button to start the test run, and dump frames
so the on-screen pass/fail results can be read.

Controller mapping (see build_core.controller): A=x, B=z, Select=a, Start=s.
`interp.keys` is the simulated keyboard the harness's sensing_keypressed
reads, so setting it here is genuine input, not a bypass of the input path.

Usage: python run_accuracycoin.py <press_at_frame> <hold_frames> <total_frames> [key]
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


# NOTE: deliberately NOT imported from dump_frames -- that module runs its
# whole script (including argv parsing) at import time.
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


def to_rgb(val):
    try:
        v = int(val)
    except (TypeError, ValueError):
        return (255, 0, 255)
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)

press_at = int(sys.argv[1]) if len(sys.argv) > 1 else 45
hold = int(sys.argv[2]) if len(sys.argv) > 2 else 6
total = int(sys.argv[3]) if len(sys.argv) > 3 else 140
key = sys.argv[4] if len(sys.argv) > 4 else "s"  # Start

outdir = r"D:\KittyNES\progress\framedumps_acc_run"
os.makedirs(outdir, exist_ok=True)

e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)
BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)
with open(r"D:\KittyNES\test_roms\AccuracyCoin.nes", "rb") as f:
    INES.load_rom_into_emu(e, f.read())

it = Interp(e.proj, max_steps=None)
palrgb = it.lists.get("PALRGB", [])
it.call_proc_by_name("nes_init")

import time
t0 = time.time()
for fr in range(1, total + 1):
    if press_at <= fr < press_at + hold:
        it.keys[key] = True
    else:
        it.keys[key] = False

    it.call_proc_by_name("run_frame")
    fb = it.lists.get("FB", [])
    print(f"frame {fr:4d} t={time.time()-t0:6.1f}s distinct={len(set(fb)):3d} "
          f"P_MASK={it.vars.get('P_MASK')} key={'DOWN' if it.keys.get(key) else '.'}",
          flush=True)

    if fr % 5 == 0 or fr == total:
        rows = []
        for y in range(240):
            row = []
            for x in range(256):
                i = y * 256 + x
                try:
                    pi = int(fb[i]) if i < len(fb) else 0
                except (TypeError, ValueError):
                    pi = 0
                r, g, b = to_rgb(palrgb[pi] if 0 <= pi < len(palrgb) else 0)
                row += [r, g, b]
            rows.append(row)
        write_png(os.path.join(outdir, f"acc_{fr:04d}.png"), 256, 240, rows)

print("done ->", outdir)
