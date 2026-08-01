"""6502 official opcode table: opcode -> (mnemonic, addressing mode, base cycles, +page)."""

# addressing mode ids
IMP, ACC, IMM, ZP, ZPX, ZPY, ABS, ABX, ABY, IND, IZX, IZY, REL = range(13)
MODE_NAMES = ["IMP", "ACC", "IMM", "ZP", "ZPX", "ZPY", "ABS", "ABX", "ABY",
              "IND", "IZX", "IZY", "REL"]

# opcode: (mnemonic, mode, cycles, page_penalty)
OPCODES = {
    0x69: ("ADC", IMM, 2, 0), 0x65: ("ADC", ZP, 3, 0), 0x75: ("ADC", ZPX, 4, 0),
    0x6D: ("ADC", ABS, 4, 0), 0x7D: ("ADC", ABX, 4, 1), 0x79: ("ADC", ABY, 4, 1),
    0x61: ("ADC", IZX, 6, 0), 0x71: ("ADC", IZY, 5, 1),

    0x29: ("AND", IMM, 2, 0), 0x25: ("AND", ZP, 3, 0), 0x35: ("AND", ZPX, 4, 0),
    0x2D: ("AND", ABS, 4, 0), 0x3D: ("AND", ABX, 4, 1), 0x39: ("AND", ABY, 4, 1),
    0x21: ("AND", IZX, 6, 0), 0x31: ("AND", IZY, 5, 1),

    0x0A: ("ASL", ACC, 2, 0), 0x06: ("ASL", ZP, 5, 0), 0x16: ("ASL", ZPX, 6, 0),
    0x0E: ("ASL", ABS, 6, 0), 0x1E: ("ASL", ABX, 7, 0),

    0x90: ("BCC", REL, 2, 0), 0xB0: ("BCS", REL, 2, 0), 0xF0: ("BEQ", REL, 2, 0),
    0x30: ("BMI", REL, 2, 0), 0xD0: ("BNE", REL, 2, 0), 0x10: ("BPL", REL, 2, 0),
    0x50: ("BVC", REL, 2, 0), 0x70: ("BVS", REL, 2, 0),

    0x24: ("BIT", ZP, 3, 0), 0x2C: ("BIT", ABS, 4, 0),

    0x00: ("BRK", IMP, 7, 0),

    0x18: ("CLC", IMP, 2, 0), 0xD8: ("CLD", IMP, 2, 0), 0x58: ("CLI", IMP, 2, 0),
    0xB8: ("CLV", IMP, 2, 0),

    0xC9: ("CMP", IMM, 2, 0), 0xC5: ("CMP", ZP, 3, 0), 0xD5: ("CMP", ZPX, 4, 0),
    0xCD: ("CMP", ABS, 4, 0), 0xDD: ("CMP", ABX, 4, 1), 0xD9: ("CMP", ABY, 4, 1),
    0xC1: ("CMP", IZX, 6, 0), 0xD1: ("CMP", IZY, 5, 1),

    0xE0: ("CPX", IMM, 2, 0), 0xE4: ("CPX", ZP, 3, 0), 0xEC: ("CPX", ABS, 4, 0),
    0xC0: ("CPY", IMM, 2, 0), 0xC4: ("CPY", ZP, 3, 0), 0xCC: ("CPY", ABS, 4, 0),

    0xC6: ("DEC", ZP, 5, 0), 0xD6: ("DEC", ZPX, 6, 0), 0xCE: ("DEC", ABS, 6, 0),
    0xDE: ("DEC", ABX, 7, 0),
    0xCA: ("DEX", IMP, 2, 0), 0x88: ("DEY", IMP, 2, 0),

    0x49: ("EOR", IMM, 2, 0), 0x45: ("EOR", ZP, 3, 0), 0x55: ("EOR", ZPX, 4, 0),
    0x4D: ("EOR", ABS, 4, 0), 0x5D: ("EOR", ABX, 4, 1), 0x59: ("EOR", ABY, 4, 1),
    0x41: ("EOR", IZX, 6, 0), 0x51: ("EOR", IZY, 5, 1),

    0xE6: ("INC", ZP, 5, 0), 0xF6: ("INC", ZPX, 6, 0), 0xEE: ("INC", ABS, 6, 0),
    0xFE: ("INC", ABX, 7, 0),
    0xE8: ("INX", IMP, 2, 0), 0xC8: ("INY", IMP, 2, 0),

    0x4C: ("JMP", ABS, 3, 0), 0x6C: ("JMP", IND, 5, 0),
    0x20: ("JSR", ABS, 6, 0),

    0xA9: ("LDA", IMM, 2, 0), 0xA5: ("LDA", ZP, 3, 0), 0xB5: ("LDA", ZPX, 4, 0),
    0xAD: ("LDA", ABS, 4, 0), 0xBD: ("LDA", ABX, 4, 1), 0xB9: ("LDA", ABY, 4, 1),
    0xA1: ("LDA", IZX, 6, 0), 0xB1: ("LDA", IZY, 5, 1),

    0xA2: ("LDX", IMM, 2, 0), 0xA6: ("LDX", ZP, 3, 0), 0xB6: ("LDX", ZPY, 4, 0),
    0xAE: ("LDX", ABS, 4, 0), 0xBE: ("LDX", ABY, 4, 1),

    0xA0: ("LDY", IMM, 2, 0), 0xA4: ("LDY", ZP, 3, 0), 0xB4: ("LDY", ZPX, 4, 0),
    0xAC: ("LDY", ABS, 4, 0), 0xBC: ("LDY", ABX, 4, 1),

    0x4A: ("LSR", ACC, 2, 0), 0x46: ("LSR", ZP, 5, 0), 0x56: ("LSR", ZPX, 6, 0),
    0x4E: ("LSR", ABS, 6, 0), 0x5E: ("LSR", ABX, 7, 0),

    0xEA: ("NOP", IMP, 2, 0),

    0x09: ("ORA", IMM, 2, 0), 0x05: ("ORA", ZP, 3, 0), 0x15: ("ORA", ZPX, 4, 0),
    0x0D: ("ORA", ABS, 4, 0), 0x1D: ("ORA", ABX, 4, 1), 0x19: ("ORA", ABY, 4, 1),
    0x01: ("ORA", IZX, 6, 0), 0x11: ("ORA", IZY, 5, 1),

    0x48: ("PHA", IMP, 3, 0), 0x08: ("PHP", IMP, 3, 0),
    0x68: ("PLA", IMP, 4, 0), 0x28: ("PLP", IMP, 4, 0),

    0x2A: ("ROL", ACC, 2, 0), 0x26: ("ROL", ZP, 5, 0), 0x36: ("ROL", ZPX, 6, 0),
    0x2E: ("ROL", ABS, 6, 0), 0x3E: ("ROL", ABX, 7, 0),

    0x6A: ("ROR", ACC, 2, 0), 0x66: ("ROR", ZP, 5, 0), 0x76: ("ROR", ZPX, 6, 0),
    0x6E: ("ROR", ABS, 6, 0), 0x7E: ("ROR", ABX, 7, 0),

    0x40: ("RTI", IMP, 6, 0), 0x60: ("RTS", IMP, 6, 0),

    0xE9: ("SBC", IMM, 2, 0), 0xE5: ("SBC", ZP, 3, 0), 0xF5: ("SBC", ZPX, 4, 0),
    0xED: ("SBC", ABS, 4, 0), 0xFD: ("SBC", ABX, 4, 1), 0xF9: ("SBC", ABY, 4, 1),
    0xE1: ("SBC", IZX, 6, 0), 0xF1: ("SBC", IZY, 5, 1),

    0x38: ("SEC", IMP, 2, 0), 0xF8: ("SED", IMP, 2, 0), 0x78: ("SEI", IMP, 2, 0),

    0x85: ("STA", ZP, 3, 0), 0x95: ("STA", ZPX, 4, 0), 0x8D: ("STA", ABS, 4, 0),
    0x9D: ("STA", ABX, 5, 0), 0x99: ("STA", ABY, 5, 0), 0x81: ("STA", IZX, 6, 0),
    0x91: ("STA", IZY, 6, 0),

    0x86: ("STX", ZP, 3, 0), 0x96: ("STX", ZPY, 4, 0), 0x8E: ("STX", ABS, 4, 0),
    0x84: ("STY", ZP, 3, 0), 0x94: ("STY", ZPX, 4, 0), 0x8C: ("STY", ABS, 4, 0),

    0xAA: ("TAX", IMP, 2, 0), 0xA8: ("TAY", IMP, 2, 0), 0xBA: ("TSX", IMP, 2, 0),
    0x8A: ("TXA", IMP, 2, 0), 0x9A: ("TXS", IMP, 2, 0), 0x98: ("TYA", IMP, 2, 0),
}

MNEMONICS = sorted({v[0] for v in OPCODES.values()})
MNEM_ID = {m: i for i, m in enumerate(MNEMONICS)}

# Illegal/undocumented opcodes are treated as 2-cycle NOPs (mnemonic "NOP", IMP).
def build_tables():
    modes, ops, cycs, pages = [], [], [], []
    for op in range(256):
        if op in OPCODES:
            m, md, cy, pg = OPCODES[op]
        else:
            m, md, cy, pg = "NOP", IMP, 2, 0
        modes.append(md)
        ops.append(MNEM_ID[m])
        cycs.append(cy)
        pages.append(pg)
    return modes, ops, cycs, pages


assert len(OPCODES) == 151, len(OPCODES)
assert len(MNEMONICS) == 56, (len(MNEMONICS), MNEMONICS)
