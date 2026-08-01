"""Phases 1-3: bit-op tables, memory bus + mappers, 6502 CPU core."""
from lib import Emu, Reporter
import tables6502 as T


# =====================================================================
# Phase 1 - lookup tables
# =====================================================================
def phase1_tables(e):
    AND = [0] * 65536
    OR = [0] * 65536
    XOR = [0] * 65536
    for a in range(256):
        base = a * 256
        for b in range(256):
            AND[base + b] = a & b
            OR[base + b] = a | b
            XOR[base + b] = a ^ b
    e.lst("T_AND", AND)
    e.lst("T_OR", OR)
    e.lst("T_XOR", XOR)
    e.lst("T_BIT7", [(v >> 7) & 1 for v in range(256)])
    e.lst("T_BIT6", [(v >> 6) & 1 for v in range(256)])
    e.lst("T_SHL", [(v << 1) & 0xFF for v in range(256)])
    e.lst("T_SHLC", [(v >> 7) & 1 for v in range(256)])
    e.lst("T_SHR", [v >> 1 for v in range(256)])
    e.lst("T_SHRC", [v & 1 for v in range(256)])
    e.lst("T_NOT", [(~v) & 0xFF for v in range(256)])


# NES master palette (2C02), decimal RGB values for the pen
NES_PALETTE = [
    0x666666, 0x002A88, 0x1412A7, 0x3B00A4, 0x5C007E, 0x6E0040, 0x6C0600, 0x561D00,
    0x333500, 0x0B4800, 0x005200, 0x004F08, 0x00404D, 0x000000, 0x000000, 0x000000,
    0xADADAD, 0x155FD9, 0x4240FF, 0x7527FE, 0xA01ACC, 0xB71E7B, 0xB53120, 0x994E00,
    0x6B6D00, 0x388700, 0x0C9300, 0x008F32, 0x007C8D, 0x000000, 0x000000, 0x000000,
    0xFFFEFF, 0x64B0FF, 0x9290FF, 0xC676FF, 0xF36AFF, 0xFE6ECC, 0xFE8170, 0xEA9E22,
    0xBCBE00, 0x88D800, 0x5CE430, 0x45E082, 0x48CDDE, 0x4F4F4F, 0x000000, 0x000000,
    0xFFFEFF, 0xC0DFFF, 0xD3D2FF, 0xE8C8FF, 0xFBC2FF, 0xFEC4EA, 0xFECCC5, 0xF7D8A5,
    0xE4E594, 0xCFEF96, 0xBDF4AB, 0xB3F3CC, 0xB5EBF2, 0xB8B8B8, 0x000000, 0x000000,
]


# =====================================================================
# Phase 2 - memory bus, mappers, PPU memory
# =====================================================================
def declare_state(e):
    for v in ["A", "X", "Y", "SP", "PC", "FC", "FZ", "FI", "FD", "FB", "FV", "FN",
              "RESULT", "EFF", "VAL", "PAGEX", "OPC", "MODE", "OPID", "CYCLES",
              "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9",
              "U1", "U2", "U3", "U4", "U5", "U6", "U7", "U8", "U9",
              "MAPPER", "PRGBANKS", "CHRBANKS", "MIRROR", "CHRRAM",
              "PRGB0", "PRGB1", "CHRB0", "CHRB1",
              "M1_SR", "M1_CNT", "M1_CTRL", "M1_CHR0", "M1_CHR1", "M1_PRG",
              "P_CTRL", "P_MASK", "P_STATUS", "P_OAMADDR", "P_V", "P_T", "P_X",
              "P_W", "P_BUF", "NMI_PENDING", "IRQ_PENDING",
              "CTRL_STROBE", "CTRL_SHIFT", "CTRL_STATE",
              "SCANLINE", "FRAME", "DECMODE", "HALT", "TRACEPC", "CYCLEACC",
              "SPR0_THIS", "RUN",
              "BG_PATBASE", "BGTILE", "BGPALSEL", "BGP0", "BGP1", "BGCOL"]:
        e.var(v)
    e.lst("RAM", [0] * 2048)
    e.lst("VRAM", [0] * 2048)
    e.lst("PAL", [0] * 32)
    e.lst("OAM", [0] * 256)
    e.lst("PRG", [0])
    e.lst("CHR", [0])
    e.lst("PALRGB", NES_PALETTE)
    e.lst("SLCOL", [0] * 256)    # scanline palette-index buffer
    e.lst("SLBG", [0] * 256)     # background opaque flags
    e.lst("SPRX", [0] * 8)
    e.lst("SPRLO", [0] * 8)
    e.lst("SPRHI", [0] * 8)
    e.lst("SPRAT", [0] * 8)
    e.lst("SPRID", [0] * 8)
    e.var("SPRN")
    # framebuffer: 256x240, 1-indexed, pixel(x,y) = FB[y*256+x+1], stores the
    # resolved NES palette index (0-63) for that pixel, ready for a PALRGB
    # lookup at pen-flush time.
    e.lst("FB", [0] * (256 * 240))
    # bit-extraction table for pattern-table tile decode: PIXBIT_T[byte*8+bitpos+1]
    # = bit (7-bitpos) of byte, i.e. bitpos 0 is the LEFTMOST pixel of the row
    # (matches the CHR-ROM convention: bit 7 of the plane byte is the leftmost
    # pixel). Same lookup-table philosophy as Phase 1's bitwise-op tables.
    PIXBIT = [0] * (256 * 8)
    for byte in range(256):
        for bitpos in range(8):
            PIXBIT[byte * 8 + bitpos] = (byte >> (7 - bitpos)) & 1
    e.lst("PIXBIT_T", PIXBIT)


def phase2_bus(e):
    # ---------------- chr_read / chr_write --------------------------
    s = e.defproc("chr_read", ["a"])
    a = lambda: e.ARG("a")
    # 4K bank select
    ctx = e.IFELSE(s, e.LT(a(), 4096))
    with ctx as b:
        e.setv(b, "U1", e.ADD(e.MUL(e.V("CHRB0"), 4096), a()))
    with ctx.substack2() as b:
        e.setv(b, "U1", e.ADD(e.MUL(e.V("CHRB1"), 4096), e.SUB(a(), 4096)))
    e.setv(s, "RESULT", e.IT("CHR", e.ADD(e.MOD(e.V("U1"), e.LEN("CHR")), 1)))
    s.finalize()

    s = e.defproc("chr_write", ["a", "v"])
    with e.IF(s, e.EQ(e.V("CHRRAM"), 1)) as b:
        ctx = e.IFELSE(b, e.LT(e.ARG("a"), 4096))
        with ctx as c:
            e.setv(c, "U1", e.ADD(e.MUL(e.V("CHRB0"), 4096), e.ARG("a")))
        with ctx.substack2() as c:
            e.setv(c, "U1", e.ADD(e.MUL(e.V("CHRB1"), 4096), e.SUB(e.ARG("a"), 4096)))
        e.repl(b, "CHR", e.ADD(e.MOD(e.V("U1"), e.LEN("CHR")), 1), e.ARG("v"))
    s.finalize()

    # ---------------- nametable index --------------------------------
    # nt_index a(0x2000-0x2FFF) -> RESULT = index into VRAM (0..2047)
    s = e.defproc("nt_index", ["a"])
    e.setv(s, "U1", e.MOD(e.ARG("a"), 4096))          # 0..4095
    e.setv(s, "U2", e.IDIV(e.V("U1"), 1024))          # table 0..3
    e.setv(s, "U3", e.MOD(e.V("U1"), 1024))           # offset
    ctx = e.IFELSE(s, e.EQ(e.V("MIRROR"), 0))         # horizontal
    with ctx as b:
        e.setv(b, "U4", e.IDIV(e.V("U2"), 2))
    with ctx.substack2() as b:
        c2 = e.IFELSE(b, e.EQ(e.V("MIRROR"), 1))      # vertical
        with c2 as c:
            e.setv(c, "U4", e.MOD(e.V("U2"), 2))
        with c2.substack2() as c:
            c3 = e.IFELSE(c, e.EQ(e.V("MIRROR"), 2))  # single-screen A
            with c3 as d:
                e.setv(d, "U4", 0)
            with c3.substack2() as d:
                c4 = e.IFELSE(d, e.EQ(e.V("MIRROR"), 3))  # single-screen B
                with c4 as f:
                    e.setv(f, "U4", 1)
                with c4.substack2() as f:              # four-screen (2KB only)
                    e.setv(f, "U4", e.MOD(e.V("U2"), 2))
    e.setv(s, "RESULT", e.ADD(e.MUL(e.V("U4"), 1024), e.V("U3")))
    s.finalize()

    # ---------------- ppu_read / ppu_write ---------------------------
    s = e.defproc("ppu_read", ["a"])
    e.setv(s, "U9", e.MOD(e.ARG("a"), 16384))
    ctx = e.IFELSE(s, e.LT(e.V("U9"), 8192))
    with ctx as b:
        e.call(b, "chr_read", a=e.V("U9"))
    with ctx.substack2() as b:
        c2 = e.IFELSE(b, e.LT(e.V("U9"), 16128))       # < 0x3F00 -> nametables
        with c2 as c:
            e.call(c, "nt_index", a=e.V("U9"))
            e.setv(c, "RESULT", e.IT("VRAM", e.ADD(e.V("RESULT"), 1)))
        with c2.substack2() as c:
            e.setv(c, "U8", e.MOD(e.V("U9"), 32))
            with e.IF(c, e.AND(e.EQ(e.MOD(e.V("U8"), 4), 0), e.GT(e.V("U8"), 15))) as d:
                e.setv(d, "U8", e.SUB(e.V("U8"), 16))
            e.setv(c, "RESULT", e.IT("PAL", e.ADD(e.V("U8"), 1)))
    s.finalize()

    s = e.defproc("ppu_write", ["a", "v"])
    e.setv(s, "U9", e.MOD(e.ARG("a"), 16384))
    ctx = e.IFELSE(s, e.LT(e.V("U9"), 8192))
    with ctx as b:
        e.call(b, "chr_write", a=e.V("U9"), v=e.ARG("v"))
    with ctx.substack2() as b:
        c2 = e.IFELSE(b, e.LT(e.V("U9"), 16128))
        with c2 as c:
            e.call(c, "nt_index", a=e.V("U9"))
            e.repl(c, "VRAM", e.ADD(e.V("RESULT"), 1), e.ARG("v"))
        with c2.substack2() as c:
            e.setv(c, "U8", e.MOD(e.V("U9"), 32))
            with e.IF(c, e.AND(e.EQ(e.MOD(e.V("U8"), 4), 0), e.GT(e.V("U8"), 15))) as d:
                e.setv(d, "U8", e.SUB(e.V("U8"), 16))
            e.repl(c, "PAL", e.ADD(e.V("U8"), 1), e.MOD(e.ARG("v"), 64))
    s.finalize()

    e.lst("PRGRAM", [0] * 8192)

    # ---------------- mapper_read ------------------------------------
    # PRG banking is expressed via PRGB0 (16K bank at $8000) and PRGB1 ($C000)
    s = e.defproc("mapper_read", ["a"])
    ctx = e.IFELSE(s, e.LT(e.ARG("a"), 32768))
    with ctx as b:                    # $4020-$7FFF: PRG-RAM at $6000-$7FFF
        c2 = e.IFELSE(b, e.GT(e.ARG("a"), 24575))
        with c2 as c:
            e.setv(c, "RESULT", e.IT("PRGRAM", e.SUB(e.ARG("a"), 24575)))
        with c2.substack2() as c:
            e.setv(c, "RESULT", 0)
    with ctx.substack2() as b:
        c2 = e.IFELSE(b, e.LT(e.ARG("a"), 49152))
        with c2 as c:
            e.setv(c, "U1", e.ADD(e.MUL(e.V("PRGB0"), 16384), e.SUB(e.ARG("a"), 32768)))
        with c2.substack2() as c:
            e.setv(c, "U1", e.ADD(e.MUL(e.V("PRGB1"), 16384), e.SUB(e.ARG("a"), 49152)))
        e.setv(b, "RESULT", e.IT("PRG", e.ADD(e.MOD(e.V("U1"), e.LEN("PRG")), 1)))
    s.finalize()

    # ---------------- mapper bank recompute ---------------------------
    s = e.defproc("mmc1_apply", [])
    # PRG mode = bits 2-3 of control
    e.setv(s, "U1", e.MOD(e.IDIV(e.V("M1_CTRL"), 4), 4))
    e.setv(s, "U2", e.MOD(e.V("M1_PRG"), 16))
    ctx = e.IFELSE(s, e.LT(e.V("U1"), 2))
    with ctx as b:                     # 32KB switch
        e.setv(b, "PRGB0", e.MUL(e.IDIV(e.V("U2"), 2), 2))
        e.setv(b, "PRGB1", e.ADD(e.V("PRGB0"), 1))
    with ctx.substack2() as b:
        c2 = e.IFELSE(b, e.EQ(e.V("U1"), 2))
        with c2 as c:                  # fix first bank at $8000
            e.setv(c, "PRGB0", 0)
            e.setv(c, "PRGB1", e.V("U2"))
        with c2.substack2() as c:      # fix last bank at $C000
            e.setv(c, "PRGB0", e.V("U2"))
            e.setv(c, "PRGB1", e.SUB(e.V("PRGBANKS"), 1))
    # CHR mode = bit 4
    e.setv(s, "U3", e.MOD(e.IDIV(e.V("M1_CTRL"), 16), 2))
    ctx = e.IFELSE(s, e.EQ(e.V("U3"), 1))
    with ctx as b:                     # two 4K banks
        e.setv(b, "CHRB0", e.V("M1_CHR0"))
        e.setv(b, "CHRB1", e.V("M1_CHR1"))
    with ctx.substack2() as b:         # one 8K bank
        e.setv(b, "CHRB0", e.MUL(e.IDIV(e.V("M1_CHR0"), 2), 2))
        e.setv(b, "CHRB1", e.ADD(e.V("CHRB0"), 1))
    # mirroring from bits 0-1
    e.setv(s, "U4", e.MOD(e.V("M1_CTRL"), 4))
    ctx = e.IFELSE(s, e.EQ(e.V("U4"), 0))
    with ctx as b:
        e.setv(b, "MIRROR", 2)
    with ctx.substack2() as b:
        c2 = e.IFELSE(b, e.EQ(e.V("U4"), 1))
        with c2 as c:
            e.setv(c, "MIRROR", 3)
        with c2.substack2() as c:
            c3 = e.IFELSE(c, e.EQ(e.V("U4"), 2))
            with c3 as d:
                e.setv(d, "MIRROR", 1)     # vertical
            with c3.substack2() as d:
                e.setv(d, "MIRROR", 0)     # horizontal
    s.finalize()

    # ---------------- mapper_write -----------------------------------
    s = e.defproc("mapper_write", ["a", "v"])
    ctx = e.IFELSE(s, e.LT(e.ARG("a"), 32768))
    with ctx as b:
        with e.IF(b, e.GT(e.ARG("a"), 24575)) as c:
            e.repl(c, "PRGRAM", e.SUB(e.ARG("a"), 24575), e.ARG("v"))
    with ctx.substack2() as b:
        # UxROM (2)
        with e.IF(b, e.EQ(e.V("MAPPER"), 2)) as c:
            e.setv(c, "PRGB0", e.MOD(e.ARG("v"), e.V("PRGBANKS")))
        # CNROM (3)
        with e.IF(b, e.EQ(e.V("MAPPER"), 3)) as c:
            # e.OR is LOGICAL/boolean or (operator_or) not numeric-default --
            # using it here always forced the divisor to 1 (both operands
            # truthy), so CHRB0 never changed. Real "default to 1 if zero"
            # guard: CHRBANKS + (CHRBANKS==0 ? 1 : 0), same boolean-coercion
            # idiom setnz uses (EQ(...) coerces to 0/1 in a numeric slot).
            e.setv(c, "U1", e.MUL(e.MOD(e.ARG("v"),
                                        e.ADD(e.V("CHRBANKS"), e.EQ(e.V("CHRBANKS"), 0))), 2))
            e.setv(c, "CHRB0", e.V("U1"))
            e.setv(c, "CHRB1", e.ADD(e.V("U1"), 1))
        # MMC1 (1)
        with e.IF(b, e.EQ(e.V("MAPPER"), 1)) as c:
            cc = e.IFELSE(c, e.EQ(e.BIT7(e.ARG("v")), 1))
            with cc as d:                       # reset shift register
                e.setv(d, "M1_SR", 0)
                e.setv(d, "M1_CNT", 0)
                e.setv(d, "M1_CTRL", e.BOR(e.V("M1_CTRL"), 12))
                e.call(d, "mmc1_apply")
            with cc.substack2() as d:
                e.setv(d, "M1_SR", e.ADD(e.IDIV(e.V("M1_SR"), 2),
                                         e.MUL(e.MOD(e.ARG("v"), 2), 16)))
                e.chg(d, "M1_CNT", 1)
                with e.IF(d, e.EQ(e.V("M1_CNT"), 5)) as f:
                    e.setv(f, "U2", e.MOD(e.IDIV(e.ARG("a"), 8192), 4))
                    cd = e.IFELSE(f, e.EQ(e.V("U2"), 0))
                    with cd as g:
                        e.setv(g, "M1_CTRL", e.V("M1_SR"))
                    with cd.substack2() as g:
                        ce = e.IFELSE(g, e.EQ(e.V("U2"), 1))
                        with ce as h:
                            e.setv(h, "M1_CHR0", e.V("M1_SR"))
                        with ce.substack2() as h:
                            cf = e.IFELSE(h, e.EQ(e.V("U2"), 2))
                            with cf as i:
                                e.setv(i, "M1_CHR1", e.V("M1_SR"))
                            with cf.substack2() as i:
                                e.setv(i, "M1_PRG", e.V("M1_SR"))
                    e.setv(f, "M1_SR", 0)
                    e.setv(f, "M1_CNT", 0)
                    e.call(f, "mmc1_apply")
    s.finalize()

    # ---------------- PPU register access ----------------------------
    ppu_regs(e)
    controller(e)

    # ---------------- bus_read / bus_write ---------------------------
    s = e.defproc("bus_read", ["a"])
    ctx = e.IFELSE(s, e.LT(e.ARG("a"), 8192))
    with ctx as b:
        e.setv(b, "RESULT", e.IT("RAM", e.ADD(e.MOD(e.ARG("a"), 2048), 1)))
    with ctx.substack2() as b:
        c2 = e.IFELSE(b, e.LT(e.ARG("a"), 16384))
        with c2 as c:
            e.call(c, "ppu_reg_read", r=e.MOD(e.ARG("a"), 8))
        with c2.substack2() as c:
            c3 = e.IFELSE(c, e.LT(e.ARG("a"), 16416))     # $4000-$401F
            with c3 as d:
                cd = e.IFELSE(d, e.EQ(e.ARG("a"), 16406))  # $4016
                with cd as f:
                    e.call(f, "ctrl_read")
                with cd.substack2() as f:
                    e.setv(f, "RESULT", 0)
            with c3.substack2() as d:
                e.call(d, "mapper_read", a=e.ARG("a"))
    s.finalize()

    s = e.defproc("bus_write", ["a", "v"])
    ctx = e.IFELSE(s, e.LT(e.ARG("a"), 8192))
    with ctx as b:
        e.repl(b, "RAM", e.ADD(e.MOD(e.ARG("a"), 2048), 1), e.ARG("v"))
    with ctx.substack2() as b:
        c2 = e.IFELSE(b, e.LT(e.ARG("a"), 16384))
        with c2 as c:
            e.call(c, "ppu_reg_write", r=e.MOD(e.ARG("a"), 8), v=e.ARG("v"))
        with c2.substack2() as c:
            c3 = e.IFELSE(c, e.LT(e.ARG("a"), 16416))
            with c3 as d:
                cd = e.IFELSE(d, e.EQ(e.ARG("a"), 16404))   # $4014 OAM DMA
                with cd as f:
                    e.setv(f, "U5", e.MUL(e.ARG("v"), 256))
                    e.setv(f, "U6", 0)
                    with e.REPEAT(f, 256) as g:
                        e.call(g, "bus_read", a=e.ADD(e.V("U5"), e.V("U6")))
                        e.repl(g, "OAM", e.ADD(e.MOD(e.ADD(e.V("P_OAMADDR"), e.V("U6")), 256), 1),
                               e.V("RESULT"))
                        e.chg(g, "U6", 1)
                    e.chg(f, "CYCLES", 513)
                with cd.substack2() as f:
                    with e.IF(f, e.EQ(e.ARG("a"), 16406)) as g:   # $4016 strobe
                        e.call(g, "ctrl_write", v=e.ARG("v"))
            with c3.substack2() as d:
                e.call(d, "mapper_write", a=e.ARG("a"), v=e.ARG("v"))
    s.finalize()


def controller(e):
    # poll the keyboard -> CTRL_STATE (bit0=A .. bit7=Right)
    keys = ["x", "z", "a", "s", "up arrow", "down arrow", "left arrow", "right arrow"]
    s = e.defproc("ctrl_poll", [])
    e.setv(s, "CTRL_STATE", 0)
    for i, k in enumerate(keys):
        menu = e._op("sensing_keyoptions", fields={"KEY_OPTION": [k]})
        pressed = e._op("sensing_keypressed", KEY_OPTION=Reporter(menu.block_id))
        with e.IF(s, pressed) as b:
            e.chg(b, "CTRL_STATE", 1 << i)
    s.finalize()

    s = e.defproc("ctrl_write", ["v"])
    e.setv(s, "CTRL_STROBE", e.MOD(e.ARG("v"), 2))
    with e.IF(s, e.EQ(e.V("CTRL_STROBE"), 1)) as b:
        e.call(b, "ctrl_poll")
        e.setv(b, "CTRL_SHIFT", e.V("CTRL_STATE"))
    s.finalize()

    s = e.defproc("ctrl_read", [])
    with e.IF(s, e.EQ(e.V("CTRL_STROBE"), 1)) as b:
        e.call(b, "ctrl_poll")
        e.setv(b, "CTRL_SHIFT", e.V("CTRL_STATE"))
    e.setv(s, "RESULT", e.ADD(64, e.MOD(e.V("CTRL_SHIFT"), 2)))
    e.setv(s, "CTRL_SHIFT", e.ADD(e.IDIV(e.V("CTRL_SHIFT"), 2), 128))
    s.finalize()


def ppu_regs(e):
    # ppu_incaddr must be defined before ppu_reg_read/ppu_reg_write call it
    s = e.defproc("ppu_incaddr", [])
    ctx = e.IFELSE(s, e.EQ(e.MOD(e.IDIV(e.V("P_CTRL"), 4), 2), 1))
    with ctx as b:
        e.setv(b, "P_V", e.MOD(e.ADD(e.V("P_V"), 32), 32768))
    with ctx.substack2() as b:
        e.setv(b, "P_V", e.MOD(e.ADD(e.V("P_V"), 1), 32768))
    s.finalize()

    # ---- read ----
    s = e.defproc("ppu_reg_read", ["r"])
    e.setv(s, "RESULT", 0)
    with e.IF(s, e.EQ(e.ARG("r"), 2)) as b:          # $2002 PPUSTATUS
        e.setv(b, "RESULT", e.V("P_STATUS"))
        e.setv(b, "P_STATUS", e.BAND(e.V("P_STATUS"), 127))
        e.setv(b, "P_W", 0)
    with e.IF(s, e.EQ(e.ARG("r"), 4)) as b:          # $2004 OAMDATA
        e.setv(b, "RESULT", e.IT("OAM", e.ADD(e.V("P_OAMADDR"), 1)))
    with e.IF(s, e.EQ(e.ARG("r"), 7)) as b:          # $2007 PPUDATA
        e.setv(b, "U7", e.MOD(e.V("P_V"), 16384))
        ctx = e.IFELSE(b, e.LT(e.V("U7"), 16128))
        with ctx as c:
            e.setv(c, "U2", e.V("P_BUF"))          # save old buffered value
            e.call(c, "ppu_read", a=e.V("U7"))     # fetch new value into RESULT
            e.setv(c, "P_BUF", e.V("RESULT"))      # buffer the new value
            e.setv(c, "RESULT", e.V("U2"))         # return the OLD buffered value
        with ctx.substack2() as c:
            e.call(c, "ppu_read", a=e.V("U7"))
            e.setv(c, "U2", e.V("RESULT"))
            e.call(c, "ppu_read", a=e.SUB(e.V("U7"), 4096))
            e.setv(c, "P_BUF", e.V("RESULT"))
            e.setv(c, "RESULT", e.V("U2"))
        e.call(b, "ppu_incaddr")
    s.finalize()

    # ---- write ----
    s = e.defproc("ppu_reg_write", ["r", "v"])
    v = lambda: e.ARG("v")
    with e.IF(s, e.EQ(e.ARG("r"), 0)) as b:      # $2000 PPUCTRL
        e.setv(b, "P_CTRL", v())
        e.setv(b, "P_T", e.ADD(e.SUB(e.V("P_T"), e.MUL(e.MOD(e.IDIV(e.V("P_T"), 1024), 4), 1024)),
                               e.MUL(e.MOD(v(), 4), 1024)))
    with e.IF(s, e.EQ(e.ARG("r"), 1)) as b:      # $2001 PPUMASK
        e.setv(b, "P_MASK", v())
    with e.IF(s, e.EQ(e.ARG("r"), 3)) as b:      # $2003 OAMADDR
        e.setv(b, "P_OAMADDR", v())
    with e.IF(s, e.EQ(e.ARG("r"), 4)) as b:      # $2004 OAMDATA
        e.repl(b, "OAM", e.ADD(e.V("P_OAMADDR"), 1), v())
        e.setv(b, "P_OAMADDR", e.MOD(e.ADD(e.V("P_OAMADDR"), 1), 256))
    with e.IF(s, e.EQ(e.ARG("r"), 5)) as b:      # $2005 PPUSCROLL
        ctx = e.IFELSE(b, e.EQ(e.V("P_W"), 0))
        with ctx as c:
            e.setv(c, "P_X", e.MOD(v(), 8))
            e.setv(c, "P_T", e.ADD(e.SUB(e.V("P_T"), e.MOD(e.V("P_T"), 32)), e.IDIV(v(), 8)))
            e.setv(c, "P_W", 1)
        with ctx.substack2() as c:
            # clear fineY (bits12-14) and coarseY (bits5-9), then set
            e.setv(c, "U1", e.SUB(e.V("P_T"), e.MUL(e.MOD(e.IDIV(e.V("P_T"), 4096), 8), 4096)))
            e.setv(c, "U1", e.SUB(e.V("U1"), e.MUL(e.MOD(e.IDIV(e.V("U1"), 32), 32), 32)))
            e.setv(c, "P_T", e.ADD(e.V("U1"), e.ADD(e.MUL(e.MOD(v(), 8), 4096),
                                                    e.MUL(e.IDIV(v(), 8), 32))))
            e.setv(c, "P_W", 0)
    with e.IF(s, e.EQ(e.ARG("r"), 6)) as b:      # $2006 PPUADDR
        ctx = e.IFELSE(b, e.EQ(e.V("P_W"), 0))
        with ctx as c:
            e.setv(c, "P_T", e.ADD(e.MOD(e.V("P_T"), 256), e.MUL(e.MOD(v(), 64), 256)))
            e.setv(c, "P_W", 1)
        with ctx.substack2() as c:
            e.setv(c, "P_T", e.ADD(e.MUL(e.IDIV(e.V("P_T"), 256), 256), v()))
            e.setv(c, "P_V", e.V("P_T"))
            e.setv(c, "P_W", 0)
    with e.IF(s, e.EQ(e.ARG("r"), 7)) as b:      # $2007 PPUDATA
        e.call(b, "ppu_write", a=e.MOD(e.V("P_V"), 16384), v=v())
        e.call(b, "ppu_incaddr")
    s.finalize()


# =====================================================================
# Phase 3 - 6502 CPU
# =====================================================================
def phase3_cpu(e):
    modes, ops, cycs, pages = T.build_tables()
    e.lst("OPMODE", modes)
    e.lst("OPID_T", ops)
    e.lst("OPCYC", cycs)
    e.lst("OPPAGE", pages)

    # ---- small helpers ----
    # BOOL maps false->0(index1) / true->1(index2). Must be declared with real
    # data BEFORE any e.IT("BOOL", ...) call, since e.lst()/e.IT() lazily
    # create an empty list on first reference and a later e.lst() with items
    # is then a no-op (list already exists) -- this bit the setnz proc badly.
    e.lst("BOOL", [0, 1])

    s = e.defproc("setnz", ["v"])
    e.setv(s, "FZ", e.IT("BOOL", e.ADD(e.EQ(e.ARG("v"), 0), 1)))
    e.setv(s, "FN", e.BIT7(e.ARG("v")))
    s.finalize()

    s = e.defproc("push", ["v"])
    e.call(s, "bus_write", a=e.ADD(256, e.V("SP")), v=e.ARG("v"))
    e.setv(s, "SP", e.MOD(e.SUB(e.V("SP"), 1), 256))
    s.finalize()

    s = e.defproc("pull", [])
    e.setv(s, "SP", e.MOD(e.ADD(e.V("SP"), 1), 256))
    e.call(s, "bus_read", a=e.ADD(256, e.V("SP")))
    s.finalize()

    s = e.defproc("getP", [])
    e.setv(s, "RESULT", e.ADD(e.ADD(e.ADD(e.MUL(e.V("FN"), 128), e.MUL(e.V("FV"), 64)),
                                    e.ADD(32, e.MUL(e.V("FB"), 16))),
                              e.ADD(e.ADD(e.MUL(e.V("FD"), 8), e.MUL(e.V("FI"), 4)),
                                    e.ADD(e.MUL(e.V("FZ"), 2), e.V("FC")))))
    s.finalize()

    s = e.defproc("setP", ["v"])
    e.setv(s, "FC", e.MOD(e.ARG("v"), 2))
    e.setv(s, "FZ", e.MOD(e.IDIV(e.ARG("v"), 2), 2))
    e.setv(s, "FI", e.MOD(e.IDIV(e.ARG("v"), 4), 2))
    e.setv(s, "FD", e.MOD(e.IDIV(e.ARG("v"), 8), 2))
    e.setv(s, "FV", e.MOD(e.IDIV(e.ARG("v"), 64), 2))
    e.setv(s, "FN", e.MOD(e.IDIV(e.ARG("v"), 128), 2))
    s.finalize()

    # fetch byte at PC, advance
    s = e.defproc("fetch", [])
    e.call(s, "bus_read", a=e.V("PC"))
    e.setv(s, "PC", e.MOD(e.ADD(e.V("PC"), 1), 65536))
    s.finalize()

    _addr_modes(e)
    _ops(e)

    # ---- interrupts (defined before cpu_step, which calls them) ----
    for name, vec, brk in (("do_nmi", 0xFFFA, 0), ("do_irq", 0xFFFE, 0)):
        s = e.defproc(name, [])
        e.call(s, "push", v=e.IDIV(e.V("PC"), 256))
        e.call(s, "push", v=e.MOD(e.V("PC"), 256))
        e.setv(s, "FB", 0)
        e.call(s, "getP")
        e.call(s, "push", v=e.V("RESULT"))
        e.setv(s, "FI", 1)
        e.call(s, "bus_read", a=vec)
        e.setv(s, "T1", e.V("RESULT"))
        e.call(s, "bus_read", a=vec + 1)
        e.setv(s, "PC", e.ADD(e.MUL(e.V("RESULT"), 256), e.V("T1")))
        e.chg(s, "CYCLES", 7)
        s.finalize()

    # ---- cpu_step ----
    s = e.defproc("cpu_step", [])
    e.setv(s, "TRACEPC", e.V("PC"))
    with e.IF(s, e.AND(e.EQ(e.V("NMI_PENDING"), 1), e.TRUE())) as b:
        e.setv(b, "NMI_PENDING", 0)
        e.call(b, "do_nmi")
    with e.IF(s, e.AND(e.EQ(e.V("IRQ_PENDING"), 1), e.EQ(e.V("FI"), 0))) as b:
        e.setv(b, "IRQ_PENDING", 0)
        e.call(b, "do_irq")
    e.call(s, "fetch")
    e.setv(s, "OPC", e.V("RESULT"))
    e.setv(s, "MODE", e.IT("OPMODE", e.ADD(e.V("OPC"), 1)))
    e.setv(s, "OPID", e.IT("OPID_T", e.ADD(e.V("OPC"), 1)))
    e.setv(s, "CYCLES", e.IT("OPCYC", e.ADD(e.V("OPC"), 1)))
    e.setv(s, "PAGEX", 0)
    e.call(s, "addr_mode")
    with e.IF(s, e.AND(e.EQ(e.V("PAGEX"), 1), e.EQ(e.IT("OPPAGE", e.ADD(e.V("OPC"), 1)), 1))) as b:
        e.chg(b, "CYCLES", 1)
    e.call(s, "exec_op")
    s.finalize()

    s = e.defproc("cpu_reset", [])
    e.setv(s, "A", 0); e.setv(s, "X", 0); e.setv(s, "Y", 0)
    e.setv(s, "SP", 253)
    e.setv(s, "FC", 0); e.setv(s, "FZ", 0); e.setv(s, "FI", 1); e.setv(s, "FD", 0)
    e.setv(s, "FB", 0); e.setv(s, "FV", 0); e.setv(s, "FN", 0)
    e.call(s, "bus_read", a=0xFFFC)
    e.setv(s, "T1", e.V("RESULT"))
    e.call(s, "bus_read", a=0xFFFD)
    e.setv(s, "PC", e.ADD(e.MUL(e.V("RESULT"), 256), e.V("T1")))
    e.setv(s, "NMI_PENDING", 0)
    e.setv(s, "IRQ_PENDING", 0)
    s.finalize()


# =====================================================================
# Phase 6a - PPU background rendering (pattern table -> framebuffer)
# =====================================================================
def phase6_ppu_bg(e):
    # ---- bg_update_patbase: BG pattern-table base from PPUCTRL bit 4 ----
    s = e.defproc("bg_update_patbase", [])
    ctx = e.IFELSE(s, e.EQ(e.MOD(e.IDIV(e.V("P_CTRL"), 16), 2), 1))
    with ctx as b:
        e.setv(b, "BG_PATBASE", 4096)
    with ctx.substack2() as b:
        e.setv(b, "BG_PATBASE", 0)
    s.finalize()

    # ---- bg_setup_tile col row -> BGTILE (pattern id), BGPALSEL (0-3) ----
    s = e.defproc("bg_setup_tile", ["col", "row"])
    nt_addr = e.ADD(0x2000, e.ADD(e.MUL(e.ARG("row"), 32), e.ARG("col")))
    e.call(s, "ppu_read", a=nt_addr)
    e.setv(s, "BGTILE", e.V("RESULT"))
    # attribute table: one byte per 4x4-tile (32x32px) block, 8 bytes/row
    attr_addr = e.ADD(0x23C0,
                       e.ADD(e.MUL(e.IDIV(e.ARG("row"), 4), 8), e.IDIV(e.ARG("col"), 4)))
    e.call(s, "ppu_read", a=attr_addr)
    e.setv(s, "U1", e.V("RESULT"))  # attribute byte (bus-family scratch is fine here, no CPU nesting)
    # quadrant shift: 0/2/4/6 depending on which 2x2-tile quadrant (col,row) is in
    e.setv(s, "U2", e.ADD(e.MUL(e.MOD(e.IDIV(e.ARG("row"), 2), 2), 4),
                          e.MUL(e.MOD(e.IDIV(e.ARG("col"), 2), 2), 2)))
    # pal_select = (attr_byte >> shift) & 3, done via IDIV/MOD (shift in {0,2,4,6}
    # so 2^shift in {1,4,16,64} -- precompute via repeated doubling, no bit ops needed)
    ctx = e.IFELSE(s, e.EQ(e.V("U2"), 0))
    with ctx as b:
        e.setv(b, "U3", 1)
    with ctx.substack2() as b:
        c2 = e.IFELSE(b, e.EQ(e.V("U2"), 2))
        with c2 as c:
            e.setv(c, "U3", 4)
        with c2.substack2() as c:
            c3 = e.IFELSE(c, e.EQ(e.V("U2"), 4))
            with c3 as d:
                e.setv(d, "U3", 16)
            with c3.substack2() as d:
                e.setv(d, "U3", 64)
    e.setv(s, "BGPALSEL", e.MOD(e.IDIV(e.V("U1"), e.V("U3")), 4))
    s.finalize()

    # ---- bg_row_planes py -> BGP0, BGP1 (the two bitplane bytes for tile row py) ----
    s = e.defproc("bg_row_planes", ["py"])
    e.call(s, "ppu_read", a=e.ADD(e.V("BG_PATBASE"),
                                   e.ADD(e.MUL(e.V("BGTILE"), 16), e.ARG("py"))))
    e.setv(s, "BGP0", e.V("RESULT"))
    e.call(s, "ppu_read", a=e.ADD(e.V("BG_PATBASE"),
                                   e.ADD(e.MUL(e.V("BGTILE"), 16), e.ADD(e.ARG("py"), 8))))
    e.setv(s, "BGP1", e.V("RESULT"))
    s.finalize()

    # ---- bg_pixel_val px -> RESULT = resolved NES palette index (0-63) ----
    s = e.defproc("bg_pixel_val", ["px"])
    e.setv(s, "U4", e.ADD(e.MUL(e.V("BGP0"), 8), e.ARG("px")))
    e.setv(s, "U5", e.ADD(e.MUL(e.V("BGP1"), 8), e.ARG("px")))
    e.setv(s, "BGCOL", e.ADD(e.IT("PIXBIT_T", e.ADD(e.V("U4"), 1)),
                             e.MUL(e.IT("PIXBIT_T", e.ADD(e.V("U5"), 1)), 2)))
    ctx = e.IFELSE(s, e.EQ(e.V("BGCOL"), 0))
    with ctx as b:
        e.setv(b, "U6", 0)  # universal background color = palette RAM index 0
    with ctx.substack2() as b:
        e.setv(b, "U6", e.ADD(e.MUL(e.V("BGPALSEL"), 4), e.V("BGCOL")))
    e.call(s, "ppu_read", a=e.ADD(0x3F00, e.V("U6")))
    s.finalize()

    # ---- render_bg_region row0 row1 col0 col1 -> fills FB for that tile range.
    # Procedure args are call-time-only values (not writable loop variables in
    # this Emu model), so loop counters live in dedicated globals instead. ----
    e.var("RB_ROW"); e.var("RB_COL"); e.var("RB_ROW1"); e.var("RB_COL0"); e.var("RB_COL1")
    e.var("RB_PY"); e.var("RB_PX")
    s = e.defproc("render_bg_region", ["row0", "row1", "col0", "col1"])
    e.call(s, "bg_update_patbase")
    e.setv(s, "RB_ROW", e.ARG("row0"))
    e.setv(s, "RB_ROW1", e.ARG("row1"))
    e.setv(s, "RB_COL0", e.ARG("col0"))
    e.setv(s, "RB_COL1", e.ARG("col1"))
    with e.UNTIL(s, e.EQ(e.V("RB_ROW"), e.V("RB_ROW1"))) as rowbody:
        e.setv(rowbody, "RB_COL", e.V("RB_COL0"))
        with e.UNTIL(rowbody, e.EQ(e.V("RB_COL"), e.V("RB_COL1"))) as colbody:
            e.call(colbody, "bg_setup_tile", col=e.V("RB_COL"), row=e.V("RB_ROW"))
            e.setv(colbody, "RB_PY", 0)
            with e.UNTIL(colbody, e.EQ(e.V("RB_PY"), 8)) as pyloop:
                e.call(pyloop, "bg_row_planes", py=e.V("RB_PY"))
                e.setv(pyloop, "RB_PX", 0)
                with e.UNTIL(pyloop, e.EQ(e.V("RB_PX"), 8)) as pxloop:
                    e.call(pxloop, "bg_pixel_val", px=e.V("RB_PX"))
                    # FB index = (row*8+py)*256 + (col*8+px) + 1
                    fb_idx = e.ADD(
                        e.MUL(e.ADD(e.MUL(e.V("RB_ROW"), 8), e.V("RB_PY")), 256),
                        e.ADD(e.ADD(e.MUL(e.V("RB_COL"), 8), e.V("RB_PX")), 1))
                    e.repl(pxloop, "FB", fb_idx, e.V("RESULT"))
                    e.chg(pxloop, "RB_PX", 1)
                e.chg(pyloop, "RB_PY", 1)
            e.chg(colbody, "RB_COL", 1)
        e.chg(rowbody, "RB_ROW", 1)
    s.finalize()

    # ---- render_bg_frame: the full 32x30-tile (256x240px) nametable 0 ----
    s = e.defproc("render_bg_frame", [])
    e.call(s, "render_bg_region", row0=0, row1=30, col0=0, col1=32)
    s.finalize()

    # ---- flush_fb_to_pen: draw FB to the stage via Pen, batched as one
    # horizontal pen-line per same-color run per row (not a stamp per pixel --
    # 256 pixels/row collapse to however many color-change runs exist, which
    # for typical tile-based NES content is usually << 256). Stage mapping:
    # FB x(0..255) -> stage x(-128..127), FB y(0..239) -> stage y(119..-120)
    # (NES y grows downward, Scratch y grows upward). ----
    e.var("FL_ROW"); e.var("FL_X"); e.var("FL_RUNSTART"); e.var("FL_CUR")
    s = e.defproc("flush_fb_row", ["row"])
    fb_base = e.MUL(e.ARG("row"), 256)
    e.setv(s, "FL_CUR", e.IT("FB", e.ADD(fb_base, 1)))
    e.setv(s, "FL_RUNSTART", 0)
    e.setv(s, "FL_X", 1)
    stage_y = e.SUB(119, e.ARG("row"))
    with e.UNTIL(s, e.EQ(e.V("FL_X"), 256)) as body:
        e.setv(body, "U8", e.IT("FB", e.ADD(fb_base, e.ADD(e.V("FL_X"), 1))))
        with e.IF(body, e.NOT(e.EQ(e.V("U8"), e.V("FL_CUR")))) as diff:
            # close out the run [FL_RUNSTART, FL_X-1] in color FL_CUR
            diff.stack("pen_setPenColorToColor",
                       COLOR=e.IT("PALRGB", e.ADD(e.V("FL_CUR"), 1)))
            diff.stack("motion_gotoxy", X=e.SUB(e.V("FL_RUNSTART"), 128), Y=stage_y)
            diff.stack("pen_penDown")
            diff.stack("motion_gotoxy", X=e.SUB(e.SUB(e.V("FL_X"), 1), 128), Y=stage_y)
            diff.stack("pen_penUp")
            e.setv(diff, "FL_RUNSTART", e.V("FL_X"))
            e.setv(diff, "FL_CUR", e.V("U8"))
        e.chg(body, "FL_X", 1)
    # final run to the end of the row (x=255)
    s.stack("pen_setPenColorToColor", COLOR=e.IT("PALRGB", e.ADD(e.V("FL_CUR"), 1)))
    s.stack("motion_gotoxy", X=e.SUB(e.V("FL_RUNSTART"), 128), Y=stage_y)
    s.stack("pen_penDown")
    s.stack("motion_gotoxy", X=e.SUB(255, 128), Y=stage_y)
    s.stack("pen_penUp")
    s.finalize()

    s = e.defproc("flush_fb_to_pen", [])
    s.stack("pen_clear")
    s.stack("pen_setPenSizeTo", SIZE=1)
    e.setv(s, "FL_ROW", 0)
    with e.UNTIL(s, e.EQ(e.V("FL_ROW"), 240)) as body:
        e.call(body, "flush_fb_row", row=e.V("FL_ROW"))
        e.chg(body, "FL_ROW", 1)
    s.finalize()


def _addr_modes(e):
    s = e.defproc("addr_mode", [])

    def emit(b, m):
        if m == T.IMP or m == T.ACC:
            e.setv(b, "EFF", 0)
        elif m == T.IMM:
            e.setv(b, "EFF", e.V("PC"))
            e.setv(b, "PC", e.MOD(e.ADD(e.V("PC"), 1), 65536))
        elif m == T.ZP:
            e.call(b, "fetch")
            e.setv(b, "EFF", e.V("RESULT"))
        elif m == T.ZPX:
            e.call(b, "fetch")
            e.setv(b, "EFF", e.MOD(e.ADD(e.V("RESULT"), e.V("X")), 256))
        elif m == T.ZPY:
            e.call(b, "fetch")
            e.setv(b, "EFF", e.MOD(e.ADD(e.V("RESULT"), e.V("Y")), 256))
        elif m == T.ABS:
            e.call(b, "fetch")
            e.setv(b, "T1", e.V("RESULT"))
            e.call(b, "fetch")
            e.setv(b, "EFF", e.ADD(e.MUL(e.V("RESULT"), 256), e.V("T1")))
        elif m in (T.ABX, T.ABY):
            reg = "X" if m == T.ABX else "Y"
            e.call(b, "fetch")
            e.setv(b, "T1", e.V("RESULT"))
            e.call(b, "fetch")
            e.setv(b, "T2", e.ADD(e.MUL(e.V("RESULT"), 256), e.V("T1")))
            e.setv(b, "EFF", e.MOD(e.ADD(e.V("T2"), e.V(reg)), 65536))
            with e.IF(b, e.GT(e.ADD(e.V("T1"), e.V(reg)), 255)) as c:
                e.setv(c, "PAGEX", 1)
        elif m == T.IND:
            e.call(b, "fetch")
            e.setv(b, "T1", e.V("RESULT"))
            e.call(b, "fetch")
            e.setv(b, "T2", e.ADD(e.MUL(e.V("RESULT"), 256), e.V("T1")))
            e.call(b, "bus_read", a=e.V("T2"))
            e.setv(b, "T3", e.V("RESULT"))
            # 6502 page-wrap bug on the high byte fetch
            e.call(b, "bus_read", a=e.ADD(e.SUB(e.V("T2"), e.MOD(e.V("T2"), 256)),
                                          e.MOD(e.ADD(e.V("T2"), 1), 256)))
            e.setv(b, "EFF", e.ADD(e.MUL(e.V("RESULT"), 256), e.V("T3")))
        elif m == T.IZX:
            e.call(b, "fetch")
            e.setv(b, "T1", e.MOD(e.ADD(e.V("RESULT"), e.V("X")), 256))
            e.call(b, "bus_read", a=e.V("T1"))
            e.setv(b, "T2", e.V("RESULT"))
            e.call(b, "bus_read", a=e.MOD(e.ADD(e.V("T1"), 1), 256))
            e.setv(b, "EFF", e.ADD(e.MUL(e.V("RESULT"), 256), e.V("T2")))
        elif m == T.IZY:
            e.call(b, "fetch")
            e.setv(b, "T1", e.V("RESULT"))
            e.call(b, "bus_read", a=e.V("T1"))
            e.setv(b, "T2", e.V("RESULT"))
            e.call(b, "bus_read", a=e.MOD(e.ADD(e.V("T1"), 1), 256))
            e.setv(b, "T3", e.ADD(e.MUL(e.V("RESULT"), 256), e.V("T2")))
            e.setv(b, "EFF", e.MOD(e.ADD(e.V("T3"), e.V("Y")), 65536))
            with e.IF(b, e.GT(e.ADD(e.V("T2"), e.V("Y")), 255)) as c:
                e.setv(c, "PAGEX", 1)
        elif m == T.REL:
            e.call(b, "fetch")
            e.setv(b, "T1", e.V("RESULT"))
            with e.IF(b, e.GT(e.V("T1"), 127)) as c:
                e.chg(c, "T1", -256)
            e.setv(b, "EFF", e.MOD(e.ADD(e.V("PC"), e.V("T1")), 65536))

    e.dispatch(s, lambda: e.V("MODE"), list(range(13)), emit)
    s.finalize()


def _ops(e):
    # branch helper
    s = e.defproc("branch", ["take"])
    with e.IF(s, e.EQ(e.ARG("take"), 1)) as b:
        e.chg(b, "CYCLES", 1)
        with e.IF(b, e.NOT(e.EQ(e.IDIV(e.V("EFF"), 256), e.IDIV(e.V("PC"), 256)))) as c:
            e.chg(c, "CYCLES", 1)
        e.setv(b, "PC", e.V("EFF"))
    s.finalize()

    # load operand into VAL
    s = e.defproc("loadval", [])
    e.call(s, "bus_read", a=e.V("EFF"))
    e.setv(s, "VAL", e.V("RESULT"))
    s.finalize()

    # compare helper: reg vs VAL
    s = e.defproc("docmp", ["r"])
    e.setv(s, "T4", e.SUB(e.ARG("r"), e.V("VAL")))
    ctx = e.IFELSE(s, e.LT(e.V("T4"), 0))
    with ctx as b:
        e.setv(b, "FC", 0)
        e.chg(b, "T4", 256)
    with ctx.substack2() as b:
        e.setv(b, "FC", 1)
    e.call(s, "setnz", v=e.V("T4"))
    s.finalize()

    _adc_sbc(e)
    _shifts(e)

    s = e.defproc("exec_op", [])

    def emit(b, oid):
        m = T.MNEMONICS[oid]
        _emit_op(e, b, m)

    e.dispatch(s, lambda: e.V("OPID"), list(range(len(T.MNEMONICS))), emit)
    s.finalize()


def _adc_sbc(e):
    # ---- ADC ----
    s = e.defproc("op_adc", [])
    ctx = e.IFELSE(s, e.AND(e.EQ(e.V("FD"), 1), e.EQ(e.V("DECMODE"), 1)))
    with ctx as b:
        # binary result for Z
        e.setv(b, "T1", e.MOD(e.ADD(e.ADD(e.V("A"), e.V("VAL")), e.V("FC")), 256))
        e.setv(b, "FZ", e.IT("BOOL", e.ADD(e.EQ(e.V("T1"), 0), 1)))
        # low nibble
        e.setv(b, "T2", e.ADD(e.ADD(e.MOD(e.V("A"), 16), e.MOD(e.V("VAL"), 16)), e.V("FC")))
        with e.IF(b, e.GT(e.V("T2"), 9)) as c:
            e.setv(c, "T2", e.ADD(e.MOD(e.ADD(e.V("T2"), 6), 16), 16))
        e.setv(b, "T3", e.ADD(e.ADD(e.SUB(e.V("A"), e.MOD(e.V("A"), 16)),
                                    e.SUB(e.V("VAL"), e.MOD(e.V("VAL"), 16))), e.V("T2")))
        e.setv(b, "FN", e.MOD(e.IDIV(e.MOD(e.V("T3"), 65536), 128), 2))
        e.setv(b, "T4", e.MOD(e.V("T3"), 256))
        e.setv(b, "FV", e.BIT7(e.BAND(e.BXOR(e.V("A"), e.V("T4")), e.BXOR(e.V("VAL"), e.V("T4")))))
        with e.IF(b, e.GT(e.V("T3"), 159)) as c:
            e.chg(c, "T3", 96)
        e.setv(b, "FC", e.IT("BOOL", e.ADD(e.GT(e.V("T3"), 255), 1)))
        e.setv(b, "A", e.MOD(e.V("T3"), 256))
    with ctx.substack2() as b:
        e.setv(b, "T1", e.ADD(e.ADD(e.V("A"), e.V("VAL")), e.V("FC")))
        e.setv(b, "FC", e.IT("BOOL", e.ADD(e.GT(e.V("T1"), 255), 1)))
        e.setv(b, "T2", e.MOD(e.V("T1"), 256))
        e.setv(b, "FV", e.BIT7(e.BAND(e.BXOR(e.V("A"), e.V("T2")), e.BXOR(e.V("VAL"), e.V("T2")))))
        e.setv(b, "A", e.V("T2"))
        e.call(b, "setnz", v=e.V("A"))
    s.finalize()

    # ---- SBC ----
    s = e.defproc("op_sbc", [])
    # binary flags always computed from the binary difference
    e.setv(s, "T1", e.SUB(e.SUB(e.V("A"), e.V("VAL")), e.SUB(1, e.V("FC"))))
    e.setv(s, "T5", e.V("FC"))
    e.setv(s, "T2", e.MOD(e.V("T1"), 256))
    e.setv(s, "FV", e.BIT7(e.BAND(e.BXOR(e.V("A"), e.V("T2")),
                                  e.BXOR(e.BXOR(e.V("VAL"), 255), e.V("T2")))))
    e.setv(s, "FC", e.IT("BOOL", e.ADD(e.NOT(e.LT(e.V("T1"), 0)), 1)))
    e.call(s, "setnz", v=e.V("T2"))
    ctx = e.IFELSE(s, e.AND(e.EQ(e.V("FD"), 1), e.EQ(e.V("DECMODE"), 1)))
    with ctx as b:
        e.setv(b, "T3", e.SUB(e.SUB(e.MOD(e.V("A"), 16), e.MOD(e.V("VAL"), 16)),
                              e.SUB(1, e.V("T5"))))
        with e.IF(b, e.LT(e.V("T3"), 0)) as c:
            e.setv(c, "T3", e.SUB(e.MOD(e.SUB(e.V("T3"), 6), 16), 16))
        e.setv(b, "T4", e.ADD(e.SUB(e.SUB(e.V("A"), e.MOD(e.V("A"), 16)),
                                    e.SUB(e.V("VAL"), e.MOD(e.V("VAL"), 16))), e.V("T3")))
        with e.IF(b, e.LT(e.V("T4"), 0)) as c:
            e.chg(c, "T4", -96)
        e.setv(b, "A", e.MOD(e.V("T4"), 256))
    with ctx.substack2() as b:
        e.setv(b, "A", e.V("T2"))
    s.finalize()


def _shifts(e):
    """Read-modify-write shift/rotate ops. mode ACC(1) acts on A."""
    for name, body in (("asl", "asl"), ("lsr", "lsr"), ("rol", "rol"), ("ror", "ror")):
        s = e.defproc("op_" + name, [])
        ctx = e.IFELSE(s, e.EQ(e.V("MODE"), 1))
        with ctx as b:
            e.setv(b, "T1", e.V("A"))
        with ctx.substack2() as b:
            e.call(b, "bus_read", a=e.V("EFF"))
            e.setv(b, "T1", e.V("RESULT"))
        if name == "asl":
            e.setv(s, "T2", e.SHLC(e.V("T1")))
            e.setv(s, "T3", e.SHL(e.V("T1")))
        elif name == "lsr":
            e.setv(s, "T2", e.SHRC(e.V("T1")))
            e.setv(s, "T3", e.SHR(e.V("T1")))
        elif name == "rol":
            e.setv(s, "T2", e.SHLC(e.V("T1")))
            e.setv(s, "T3", e.ADD(e.SHL(e.V("T1")), e.V("FC")))
        else:  # ror
            e.setv(s, "T2", e.SHRC(e.V("T1")))
            e.setv(s, "T3", e.ADD(e.SHR(e.V("T1")), e.MUL(e.V("FC"), 128)))
        e.setv(s, "FC", e.V("T2"))
        e.call(s, "setnz", v=e.V("T3"))
        ctx = e.IFELSE(s, e.EQ(e.V("MODE"), 1))
        with ctx as b:
            e.setv(b, "A", e.V("T3"))
        with ctx.substack2() as b:
            e.call(b, "bus_write", a=e.V("EFF"), v=e.V("T3"))
        s.finalize()


def _emit_op(e, b, m):
    """Emit the body of one mnemonic into script `b`."""
    V = e.V
    if m == "ADC":
        e.call(b, "loadval"); e.call(b, "op_adc")
    elif m == "SBC":
        e.call(b, "loadval"); e.call(b, "op_sbc")
    elif m == "AND":
        e.call(b, "loadval")
        e.setv(b, "A", e.BAND(V("A"), V("VAL")))
        e.call(b, "setnz", v=V("A"))
    elif m == "ORA":
        e.call(b, "loadval")
        e.setv(b, "A", e.BOR(V("A"), V("VAL")))
        e.call(b, "setnz", v=V("A"))
    elif m == "EOR":
        e.call(b, "loadval")
        e.setv(b, "A", e.BXOR(V("A"), V("VAL")))
        e.call(b, "setnz", v=V("A"))
    elif m in ("ASL", "LSR", "ROL", "ROR"):
        e.call(b, "op_" + m.lower())
    elif m == "BIT":
        e.call(b, "loadval")
        e.setv(b, "FZ", e.IT("BOOL", e.ADD(e.EQ(e.BAND(V("A"), V("VAL")), 0), 1)))
        e.setv(b, "FN", e.BIT7(V("VAL")))
        e.setv(b, "FV", e.BIT6(V("VAL")))
    elif m in ("BCC", "BCS", "BEQ", "BNE", "BMI", "BPL", "BVC", "BVS"):
        flag, want = {"BCC": ("FC", 0), "BCS": ("FC", 1), "BEQ": ("FZ", 1), "BNE": ("FZ", 0),
                      "BMI": ("FN", 1), "BPL": ("FN", 0), "BVC": ("FV", 0), "BVS": ("FV", 1)}[m]
        e.call(b, "branch", take=e.IT("BOOL", e.ADD(e.EQ(V(flag), want), 1)))
    elif m == "BRK":
        e.setv(b, "PC", e.MOD(e.ADD(V("PC"), 1), 65536))
        e.call(b, "push", v=e.IDIV(V("PC"), 256))
        e.call(b, "push", v=e.MOD(V("PC"), 256))
        e.setv(b, "FB", 1)
        e.call(b, "getP")
        e.call(b, "push", v=V("RESULT"))
        e.setv(b, "FB", 0)
        e.setv(b, "FI", 1)
        e.call(b, "bus_read", a=0xFFFE)
        e.setv(b, "T1", V("RESULT"))
        e.call(b, "bus_read", a=0xFFFF)
        e.setv(b, "PC", e.ADD(e.MUL(V("RESULT"), 256), V("T1")))
    elif m == "CLC": e.setv(b, "FC", 0)
    elif m == "CLD": e.setv(b, "FD", 0)
    elif m == "CLI": e.setv(b, "FI", 0)
    elif m == "CLV": e.setv(b, "FV", 0)
    elif m == "SEC": e.setv(b, "FC", 1)
    elif m == "SED": e.setv(b, "FD", 1)
    elif m == "SEI": e.setv(b, "FI", 1)
    elif m in ("CMP", "CPX", "CPY"):
        e.call(b, "loadval")
        e.call(b, "docmp", r=V({"CMP": "A", "CPX": "X", "CPY": "Y"}[m]))
    elif m == "DEC":
        e.call(b, "loadval")
        e.setv(b, "T4", e.MOD(e.SUB(V("VAL"), 1), 256))
        e.call(b, "bus_write", a=V("EFF"), v=V("T4"))
        e.call(b, "setnz", v=V("T4"))
    elif m == "INC":
        e.call(b, "loadval")
        e.setv(b, "T4", e.MOD(e.ADD(V("VAL"), 1), 256))
        e.call(b, "bus_write", a=V("EFF"), v=V("T4"))
        e.call(b, "setnz", v=V("T4"))
    elif m in ("DEX", "DEY", "INX", "INY"):
        reg = "X" if m[-1] == "X" else "Y"
        d = 1 if m[0] == "I" else -1
        e.setv(b, reg, e.MOD(e.ADD(V(reg), d), 256))
        e.call(b, "setnz", v=V(reg))
    elif m == "JMP":
        e.setv(b, "PC", V("EFF"))
    elif m == "JSR":
        e.setv(b, "T4", e.MOD(e.SUB(V("PC"), 1), 65536))
        e.call(b, "push", v=e.IDIV(V("T4"), 256))
        e.call(b, "push", v=e.MOD(V("T4"), 256))
        e.setv(b, "PC", V("EFF"))
    elif m == "RTS":
        e.call(b, "pull"); e.setv(b, "T1", V("RESULT"))
        e.call(b, "pull")
        e.setv(b, "PC", e.MOD(e.ADD(e.ADD(e.MUL(V("RESULT"), 256), V("T1")), 1), 65536))
    elif m == "RTI":
        e.call(b, "pull")
        e.call(b, "setP", v=V("RESULT"))
        e.call(b, "pull"); e.setv(b, "T1", V("RESULT"))
        e.call(b, "pull")
        e.setv(b, "PC", e.ADD(e.MUL(V("RESULT"), 256), V("T1")))
    elif m in ("LDA", "LDX", "LDY"):
        reg = m[2]
        e.call(b, "loadval")
        e.setv(b, reg, V("VAL"))
        e.call(b, "setnz", v=V(reg))
    elif m in ("STA", "STX", "STY"):
        e.call(b, "bus_write", a=V("EFF"), v=V(m[2]))
    elif m == "NOP":
        pass
    elif m == "PHA":
        e.call(b, "push", v=V("A"))
    elif m == "PHP":
        e.setv(b, "FB", 1)
        e.call(b, "getP")
        e.call(b, "push", v=V("RESULT"))
        e.setv(b, "FB", 0)
    elif m == "PLA":
        e.call(b, "pull")
        e.setv(b, "A", V("RESULT"))
        e.call(b, "setnz", v=V("A"))
    elif m == "PLP":
        e.call(b, "pull")
        e.call(b, "setP", v=V("RESULT"))
    elif m == "TAX":
        e.setv(b, "X", V("A")); e.call(b, "setnz", v=V("X"))
    elif m == "TAY":
        e.setv(b, "Y", V("A")); e.call(b, "setnz", v=V("Y"))
    elif m == "TXA":
        e.setv(b, "A", V("X")); e.call(b, "setnz", v=V("A"))
    elif m == "TYA":
        e.setv(b, "A", V("Y")); e.call(b, "setnz", v=V("A"))
    elif m == "TSX":
        e.setv(b, "X", V("SP")); e.call(b, "setnz", v=V("X"))
    elif m == "TXS":
        e.setv(b, "SP", V("X"))
    else:
        raise RuntimeError("unhandled mnemonic " + m)
