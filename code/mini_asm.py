"""Tiny two-pass 6502 assembler for baking test programs. Supports just the
addressing modes/mnemonics used by the CPU test suite."""

OPC = {
    ("LDA", "imm"): 0xA9, ("LDA", "zp"): 0xA5, ("LDA", "zpx"): 0xB5, ("LDA", "abs"): 0xAD,
    ("LDX", "imm"): 0xA2, ("LDX", "zp"): 0xA6,
    ("LDY", "imm"): 0xA0, ("LDY", "zp"): 0xA4,
    ("STA", "zp"): 0x85, ("STA", "zpx"): 0x95, ("STA", "abs"): 0x8D,
    ("STX", "zp"): 0x86, ("STY", "zp"): 0x84,
    ("ADC", "imm"): 0x69, ("ADC", "zp"): 0x65,
    ("SBC", "imm"): 0xE9, ("SBC", "zp"): 0xE5,
    ("AND", "imm"): 0x29, ("ORA", "imm"): 0x09, ("EOR", "imm"): 0x49,
    ("CMP", "imm"): 0xC9, ("CPX", "imm"): 0xE0, ("CPY", "imm"): 0xC0,
    ("BIT", "zp"): 0x24,
    ("INC", "zp"): 0xE6, ("DEC", "zp"): 0xC6,
    ("INX", "imp"): 0xE8, ("INY", "imp"): 0xC8, ("DEX", "imp"): 0xCA, ("DEY", "imp"): 0x88,
    ("TAX", "imp"): 0xAA, ("TAY", "imp"): 0xA8, ("TXA", "imp"): 0x8A, ("TYA", "imp"): 0x98,
    ("TSX", "imp"): 0xBA, ("TXS", "imp"): 0x9A,
    ("PHA", "imp"): 0x48, ("PLA", "imp"): 0x68, ("PHP", "imp"): 0x08, ("PLP", "imp"): 0x28,
    ("CLC", "imp"): 0x18, ("SEC", "imp"): 0x38, ("CLV", "imp"): 0xB8,
    ("CLI", "imp"): 0x58, ("SEI", "imp"): 0x78, ("CLD", "imp"): 0xD8, ("SED", "imp"): 0xF8,
    ("ASL", "acc"): 0x0A, ("LSR", "acc"): 0x4A, ("ROL", "acc"): 0x2A, ("ROR", "acc"): 0x6A,
    ("ASL", "zp"): 0x06, ("LSR", "zp"): 0x46,
    ("NOP", "imp"): 0xEA,
    ("JMP", "abs"): 0x4C,
    ("JSR", "abs"): 0x20,
    ("RTS", "imp"): 0x60,
    ("RTI", "imp"): 0x40,
    ("BRK", "imp"): 0x00,
    ("BEQ", "rel"): 0xF0, ("BNE", "rel"): 0xD0, ("BCC", "rel"): 0x90, ("BCS", "rel"): 0xB0,
    ("BMI", "rel"): 0x30, ("BPL", "rel"): 0x10, ("BVC", "rel"): 0x50, ("BVS", "rel"): 0x70,
}


class Asm:
    def __init__(self, origin):
        self.origin = origin
        self.lines = []  # list of (mnemonic, mode, operand)
        self.labels = {}

    def label(self, name):
        self.lines.append(("LABEL", name, None))

    def __call__(self, mnem, mode="imp", operand=None):
        self.lines.append((mnem, mode, operand))

    def assemble(self):
        # pass 1: sizes
        sizes = {"imp": 1, "acc": 1, "imm": 2, "zp": 2, "zpx": 2, "abs": 3, "rel": 2}
        addr = self.origin
        layout = []
        for mnem, mode, operand in self.lines:
            if mnem == "LABEL":
                self.labels[mode] = addr
                continue
            sz = sizes[mode]
            layout.append((addr, mnem, mode, operand, sz))
            addr += sz
        # pass 2: emit
        out = bytearray()
        for addr, mnem, mode, operand, sz in layout:
            op = OPC[(mnem, mode)]
            out.append(op)
            if mode == "imm" or mode == "zp" or mode == "zpx":
                val = operand if isinstance(operand, int) else self.labels[operand]
                out.append(val & 0xFF)
            elif mode == "abs":
                val = operand if isinstance(operand, int) else self.labels[operand]
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
            elif mode == "rel":
                target = operand if isinstance(operand, int) else self.labels[operand]
                off = target - (addr + 2)
                if off < -128 or off > 127:
                    raise ValueError("branch out of range: %s -> %s (%d)" % (mnem, operand, off))
                out.append(off & 0xFF)
        return bytes(out)
