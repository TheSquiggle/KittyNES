"""Follow-up bug hunt: the user clarified the real SMB+Duck Hunt symptom is
WRONG TILE GRAPHICS on sprites (wrong items displayed), not misplacement --
so the fine-X scrolling fix almost certainly didn't address it. Re-focus on
sprite pattern-table (CHR) fetch correctness, specifically:

1. Does OAM tile-index N actually pull back CHR tile N's real bitplane
   bytes, across a spread of distinct tile indices (not just adjacent pairs
   like the 8x16 test used), both pattern tables, both 8x8 and 8x16 modes?
2. THE likely culprit per the coordinator: does sprite CHR fetch correctly
   apply the CURRENT CHR windows (the C1 1K bank registers) the same way background CHR
   fetch does? This ROM uses mapper 66 (GxROM), which bank-switches CHR --
   if sprite fetch used stale/different bank state than background fetch,
   sprites would show tiles from the wrong bank ("wrong items displayed"
   matches this exactly). Directly compares the sprite fetch path
   (spr_fetch_planes) against the background fetch path (ppu_read, used
   identically by both bg_setup_tile/bg_row_planes AND spr_fetch_planes) at
   the SAME CHR address under a mapper-66-triggered bank switch.
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


def ppu_read(interp, a):
    interp.call_proc_by_name("ppu_read %s", {"a": a})
    return i_(interp.vars["RESULT"])


# =====================================================================
# 1) Distinct-tile spread check: put a UNIQUE marker byte in many tiles
# across BOTH pattern tables, fetch each via a sprite referencing that
# exact tile index, confirm no cross-contamination between tiles/banks.
# =====================================================================
print("--- distinct tile index -> distinct CHR data (8x8 mode) ---")
interp = fresh()
NTILES_PER_BANK = 8  # test tiles 0-7 in bank0 ($0000) and 0-7 (as tile 128-135, i.e. bank1 via PPUCTRL bit3) in bank1 ($1000)
CHR = [0] * 8192
for t in range(NTILES_PER_BANK):
    marker = 0x10 + t  # distinct, recognizable marker per tile, bank0
    for row in range(8):
        CHR[t * 16 + row] = marker
for t in range(NTILES_PER_BANK):
    marker = 0x80 + t  # distinct marker per tile, bank1 ($1000, offset 4096)
    for row in range(8):
        CHR[4096 + t * 16 + row] = marker
interp.lists["CHR"] = CHR
interp.lists["VRAM"] = [0] * 2048
interp.lists["PAL"] = [0] * 32
interp.vars["CHRRAM"] = 0
interp.vars["CHRBANKS"] = 1
interp.lists["C1"] = list(range(8))
interp.vars["MAPPER"] = 0
interp.vars["MIRROR"] = 0

OAM = [0xFF] * 256
for t in range(NTILES_PER_BANK):
    # PPUCTRL bit3=0 -> sprite pattern table = bank0
    interp.vars["P_CTRL"] = 0x00
    OAM[0] = 10
    OAM[1] = t
    OAM[2] = 0x00
    OAM[3] = 20
    interp.lists["OAM"] = list(OAM)
    interp.call_proc_by_name("sprite_eval_line %s", {"sl": 11})
    check("8x8 bank0 tile %d -> correct SPRLO marker" % t,
          i_(interp.lists["SPRLO"][0]), 0x10 + t)

    # PPUCTRL bit3=1 -> sprite pattern table = bank1, SAME tile index t
    interp.vars["P_CTRL"] = 0x08
    interp.call_proc_by_name("sprite_eval_line %s", {"sl": 11})
    check("8x8 bank1 (PPUCTRL bit3=1) tile %d -> correct SPRLO marker" % t,
          i_(interp.lists["SPRLO"][0]), 0x80 + t)


# =====================================================================
# 2) THE likely culprit: sprite CHR fetch vs background CHR fetch under a
# real mapper-66-triggered CHR bank switch. Both should read from the SAME
# bank state (the C1 window registers), since both call ppu_read/chr_read.
# =====================================================================
print("\n--- sprite CHR fetch vs background CHR fetch, same CHR bank switch ---")
interp2 = fresh()
NBANK4K = 8  # 4x 8K GxROM CHR banks = 8x 4K sub-banks total (32K CHR -- larger than SMB's 16K, to stress-test more bank values)
CHR2 = [0] * (4096 * NBANK4K)
for b4 in range(NBANK4K):
    marker = 0x40 + b4  # each 4K sub-bank filled with its own index as a marker
    for i in range(4096):
        CHR2[b4 * 4096 + i] = marker
interp2.lists["CHR"] = CHR2
interp2.lists["VRAM"] = [0] * 2048
interp2.lists["PAL"] = [0] * 32
interp2.vars["CHRRAM"] = 0
interp2.vars["MAPPER"] = 66
interp2.vars["CHRBANKS"] = NBANK4K // 2  # 8K units
interp2.vars["PRGBANKS"] = 2
interp2.lists["P8"] = [0, 1, 2, 3]
interp2.lists["C1"] = list(range(8))
interp2.vars["P_CTRL"] = 0x00  # 8x8 sprites, sprite pattern table = bank0 ($0000-$0FFF window)

OAM2 = [0xFF] * 256
OAM2[0] = 10
OAM2[1] = 5  # arbitrary tile index; what matters is which 4K CHR sub-bank it lands in
OAM2[2] = 0x00
OAM2[3] = 20
interp2.lists["OAM"] = OAM2

for chrbank_8k in range(NBANK4K // 2):
    # Select this 8K CHR bank via the real GxROM register write. Per the
    # NESdev spec the register is `xxPP xxCC` -- CHR is the LOW field
    # (bits 1-0), PRG the HIGH field (bits 5-4). This test originally wrote
    # the bank into bits 5-4, matching an implementation that had the two
    # fields swapped; both were wrong together, so the suite passed while
    # real ROMs rendered the wrong tileset. See PROGRESS_LOG.md.
    reg_value = chrbank_8k & 0x03
    bus_write(interp2, 0x8000, reg_value)

    expected_sub_bank0 = chrbank_8k * 2       # first 4K sub-bank after this select
    expected_marker0 = 0x40 + expected_sub_bank0

    # background fetch path: ppu_read at PPU address 5*16=80 (tile5, row0),
    # within the $0000-$0FFF window -> should read the first 4K sub-bank
    bg_val = ppu_read(interp2, 5 * 16 + 0)
    check("CHR bank %d: background ppu_read reads correct sub-bank marker" % chrbank_8k,
          bg_val, expected_marker0)

    # sprite fetch path: spr_fetch_planes for the same tile/row, via the
    # real sprite_eval_line entry point (exactly what rendering calls)
    interp2.call_proc_by_name("sprite_eval_line %s", {"sl": 11})
    sprite_val = i_(interp2.lists["SPRLO"][0])
    check("CHR bank %d: sprite SPRLO reads correct sub-bank marker" % chrbank_8k,
          sprite_val, expected_marker0)

    check("CHR bank %d: sprite and background AGREE on which bank's data they see" % chrbank_8k,
          sprite_val, bg_val)


print("\n%s" % ("ALL SPRITE-CHR-BANK CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
