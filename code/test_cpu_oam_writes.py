"""Follow-up bug hunt #5: audit the CPU-side path writing values INTO OAM
and mapper registers, before rendering ever sees them (four rounds of
rendering-side verification have all come back clean). A corruption here
-- wrong OAM bytes, wrong DMA source address -- would produce exactly the
same "wrong colors/garbled tiles in specific spots" symptom despite
provably-correct rendering math.
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


def bus_read(interp, a):
    interp.call_proc_by_name("bus_read %s", {"a": a})
    return i_(interp.vars["RESULT"])


# =====================================================================
# 1) $2003 (OAMADDR) / $2004 (OAMDATA): a realistic sequence -- set
# OAMADDR, then write several consecutive OAMDATA bytes (how a game
# writing individual sprites without DMA would actually do it), verify
# each lands at the right index and OAMADDR auto-increments, INCLUDING
# wraparound at 256.
# =====================================================================
print("--- $2003/$2004 direct OAM writes, realistic sequence ---")
interp = fresh()
interp.lists["OAM"] = [0xEE] * 256  # 0xEE = "untouched" sentinel, easy to spot

# set OAMADDR=10, then write 4 bytes (Y,tile,attr,X for one sprite)
bus_write(interp, 0x2003, 10)
bus_write(interp, 0x2004, 0x50)  # Y
bus_write(interp, 0x2004, 0x11)  # tile
bus_write(interp, 0x2004, 0x02)  # attr
bus_write(interp, 0x2004, 0x60)  # X
oam = interp.lists["OAM"]
check("OAMDATA seq: byte0 (Y) landed at OAM[10]", i_(oam[10]), 0x50)
check("OAMDATA seq: byte1 (tile) landed at OAM[11]", i_(oam[11]), 0x11)
check("OAMDATA seq: byte2 (attr) landed at OAM[12]", i_(oam[12]), 0x02)
check("OAMDATA seq: byte3 (X) landed at OAM[13]", i_(oam[13]), 0x60)
check("OAMDATA seq: OAMADDR auto-incremented to 14", i_(interp.vars["P_OAMADDR"]), 14)
check("OAMDATA seq: untouched neighbor OAM[9] still sentinel", i_(oam[9]), 0xEE)
check("OAMDATA seq: untouched neighbor OAM[14] still sentinel", i_(oam[14]), 0xEE)

# ---- wraparound at 256: set OAMADDR near the end, write past 255 ----
interp2 = fresh()
interp2.lists["OAM"] = [0xEE] * 256
bus_write(interp2, 0x2003, 254)
bus_write(interp2, 0x2004, 0xA1)  # -> OAM[254]
bus_write(interp2, 0x2004, 0xA2)  # -> OAM[255]
bus_write(interp2, 0x2004, 0xA3)  # -> OAM[0] (wrapped)
bus_write(interp2, 0x2004, 0xA4)  # -> OAM[1] (wrapped)
oam2 = interp2.lists["OAM"]
check("OAMDATA wraparound: OAM[254]", i_(oam2[254]), 0xA1)
check("OAMDATA wraparound: OAM[255]", i_(oam2[255]), 0xA2)
check("OAMDATA wraparound: OAM[0] (wrapped from 256)", i_(oam2[0]), 0xA3)
check("OAMDATA wraparound: OAM[1]", i_(oam2[1]), 0xA4)
check("OAMDATA wraparound: OAMADDR ends at 2", i_(interp2.vars["P_OAMADDR"]), 2)

# ---- $2004 read should reflect the CURRENT OAMADDR without side effects ----
interp3 = fresh()
interp3.lists["OAM"] = [0] * 256
interp3.lists["OAM"][50] = 0x77
bus_write(interp3, 0x2003, 50)
readback = bus_read(interp3, 0x2004)
check("OAMDATA read reflects OAM[OAMADDR] correctly", readback, 0x77)
check("OAMDATA read does NOT change OAMADDR", i_(interp3.vars["P_OAMADDR"]), 50)


# =====================================================================
# 2) $4014 (OAM DMA) source address computation: source = written_value
# * 256 (i.e. a full 256-byte page starting at $XX00). Test several
# different page values, including ones where a page-arithmetic off-by-
# one would show up clearly (page 0 = edge case, page 7 = an arbitrary
# non-zero page, page 255 = the highest possible page).
# =====================================================================
print("\n--- $4014 OAM DMA source-address computation ---")
for page in [0x00, 0x02, 0x07, 0xFF]:
    interp4 = fresh()
    RAM = [0] * 2048 if page <= 0x07 else None
    # RAM list only covers $0000-$07FF (2048 bytes) = pages $00-$07;
    # bus_read of higher pages goes through PPU-register/mapper space
    # instead (real hardware allows DMA from anywhere in the CPU's
    # 16-bit space including PRG-ROM, which is legal but unusual --
    # game code overwhelmingly uses a RAM page). Test what our bus
    # actually returns for both a RAM-range page and a non-RAM page,
    # confirming the SOURCE ADDRESS math (not just "did SOME data move")
    # is exactly page*256, byte-for-byte across the whole 256-byte range.
    if page <= 0x07:
        RAM = [0] * 2048
        base = page * 256
        for i in range(256):
            RAM[base + i] = (i * 13 + page) % 256
        interp4.lists["RAM"] = RAM
        interp4.vars["P_OAMADDR"] = 0
        bus_write(interp4, 0x4014, page)
        oam4 = interp4.lists["OAM"]
        mism = [i for i in range(256) if i_(oam4[i]) != (i * 13 + page) % 256]
        check("DMA source page 0x%02X: all 256 bytes match RAM[page*256 : page*256+256]" % page,
              mism, [])
    else:
        # page 0xFF ($FF00-$FFFF) is PRG-ROM space on NROM -- just verify
        # the DMA reads from bus_read(0xFF00 + i) for i in 0..255, i.e.
        # the source base really is page*256 = 0xFF00, not e.g. 0xFE00 or
        # 0x00FF (an off-by-one in the multiply, or reading the byte
        # value directly as an address without the *256 scale).
        interp4.lists["PRG"] = [0] * 32768
        for i in range(256):
            interp4.lists["PRG"][0x7F00 + i] = (i * 7 + 3) % 256  # $FF00 = PRG offset 0x7F00 (NROM, $8000 base)
        interp4.vars["MAPPER"] = 0
        interp4.vars["PRGBANKS"] = 2
        interp4.vars["PRGB0"] = 0
        interp4.vars["PRGB1"] = 1
        interp4.vars["P_OAMADDR"] = 0
        bus_write(interp4, 0x4014, page)
        oam4 = interp4.lists["OAM"]
        mism = [i for i in range(256) if i_(oam4[i]) != (i * 7 + 3) % 256]
        check("DMA source page 0x%02X ($FF00, PRG-ROM space): all 256 bytes correct" % page,
              mism, [])

print("\n%s" % ("ALL CPU OAM-WRITE CHECKS PASSED" if not FAILURES else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
