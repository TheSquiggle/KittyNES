import sys, os
SKILL_SCRIPTS = r"C:\Users\silas\AppData\Roaming\Claude\local-agent-mode-sessions\skills-plugin\4ecba4d7-edc6-4795-9198-a90cec018a97\d10d29f8-5542-45a2-bb46-fca0b76b88f9\skills\scratch-sb3\scripts"
sys.path.insert(0, SKILL_SCRIPTS)
from sb3_builder import (
    Project, Script, Reporter, make_reporter,
    define_custom_block, call_custom_block, CustomBlockArg,
    num_input, text_input, var_input, list_input, broadcast_input,
)

proj = Project()
cpu = proj.add_sprite("CPU", x=0, y=0)

print("Building bit-op lookup tables...")
AND_T = [0]*65536
OR_T = [0]*65536
XOR_T = [0]*65536
for a in range(256):
    base = a*256
    for b in range(256):
        AND_T[base+b] = a & b
        OR_T[base+b] = a | b
        XOR_T[base+b] = a ^ b

SHL_T = [((v<<1) & 0xFF) for v in range(256)]
SHL_CARRY_T = [ (1 if (v & 0x80) else 0) for v in range(256)]
SHR_T = [ (v>>1) for v in range(256)]
SHR_CARRY_T = [ (v & 1) for v in range(256)]
BIT7_T = [ (1 if (v & 0x80) else 0) for v in range(256)]
BIT6_T = [ (1 if (v & 0x40) else 0) for v in range(256)]

AND_L = proj.add_list("AND_T", AND_T)
OR_L = proj.add_list("OR_T", OR_T)
XOR_L = proj.add_list("XOR_T", XOR_T)
SHL_L = proj.add_list("SHL_T", SHL_T)
SHLC_L = proj.add_list("SHL_CARRY_T", SHL_CARRY_T)
SHR_L = proj.add_list("SHR_T", SHR_T)
SHRC_L = proj.add_list("SHR_CARRY_T", SHR_CARRY_T)
BIT7_L = proj.add_list("BIT7_T", BIT7_T)
BIT6_L = proj.add_list("BIT6_T", BIT6_T)

RESULT = proj.add_variable("RESULT", 0)

def make_lookup_block(name, list_id, list_name, two_arg=True):
    if two_arg:
        def_id, call = define_custom_block(cpu, f"{name} %s %s", args=[
            CustomBlockArg("a","string_number"), CustomBlockArg("b","string_number")])
        s = Script(cpu); s._hat_id = def_id; s._tail_id = def_id
        arg_a = cpu.blocks_arg_a if False else None
        # get argument reporter block ids from prototype inputs order - capture directly
        return def_id, call
    else:
        def_id, call = define_custom_block(cpu, f"{name} %s", args=[
            CustomBlockArg("a","string_number")])
        return def_id, call

# Build AND/OR/XOR as custom blocks: bitop_and(a,b) -> sets RESULT
def build_binop(name, list_id, list_name):
    def_id, call = define_custom_block(cpu, f"{name} %s %s", args=[
        CustomBlockArg("a","string_number"), CustomBlockArg("b","string_number")])
    # find the argument reporter block ids (created inside define_custom_block, attached as proto inputs shadow)
    proto = None
    for bid, b in cpu.blocks.items():
        if b.opcode == "procedures_prototype" and b.mutation and b.mutation.get("proccode") == f"{name} %s %s":
            proto = b
            proto_id = bid
    arg_ids = call["argument_ids"]
    a_rep_id = proto.inputs[arg_ids[0]][1]
    b_rep_id = proto.inputs[arg_ids[1]][1]

    idx = make_reporter(cpu, "operator_add",
        NUM1=Reporter(make_reporter(cpu, "operator_multiply",
            NUM1=Reporter(a_rep_id), NUM2=num_input(256)).block_id),
        NUM2=Reporter(b_rep_id))
    item = make_reporter(cpu, "data_itemoflist", INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(idx.block_id),NUM2=num_input(1)).block_id),
                          fields={"LIST":[list_name, list_id]})
    s = Script(cpu); s._hat_id = def_id; s._tail_id = def_id
    s.stack("data_setvariableto", VALUE=Reporter(item.block_id), fields={"VARIABLE":["RESULT", RESULT]})
    s.finalize()
    return call

def build_unop(name, list_id, list_name):
    def_id, call = define_custom_block(cpu, f"{name} %s", args=[CustomBlockArg("a","string_number")])
    proto = None
    for bid, b in cpu.blocks.items():
        if b.opcode == "procedures_prototype" and b.mutation and b.mutation.get("proccode") == f"{name} %s":
            proto = b
    arg_ids = call["argument_ids"]
    a_rep_id = proto.inputs[arg_ids[0]][1]
    item = make_reporter(cpu, "data_itemoflist",
        INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(a_rep_id),NUM2=num_input(1)).block_id),
        fields={"LIST":[list_name, list_id]})
    s = Script(cpu); s._hat_id = def_id; s._tail_id = def_id
    s.stack("data_setvariableto", VALUE=Reporter(item.block_id), fields={"VARIABLE":["RESULT", RESULT]})
    s.finalize()
    return call

CALL_AND = build_binop("bitop_and", AND_L, "AND_T")
CALL_OR  = build_binop("bitop_or",  OR_L,  "OR_T")
CALL_XOR = build_binop("bitop_xor", XOR_L, "XOR_T")
CALL_SHL = build_unop("bitop_shl", SHL_L, "SHL_T")
CALL_SHLC = build_unop("bitop_shl_carry", SHLC_L, "SHL_CARRY_T")
CALL_SHR = build_unop("bitop_shr", SHR_L, "SHR_T")
CALL_SHRC = build_unop("bitop_shr_carry", SHRC_L, "SHR_CARRY_T")
CALL_BIT7 = build_unop("bitop_bit7", BIT7_L, "BIT7_T")
CALL_BIT6 = build_unop("bitop_bit6", BIT6_L, "BIT6_T")

print("Bit-op tables + custom blocks done. Blocks so far:", len(cpu.blocks))

# ---------------- Phase 2: Memory bus ----------------
print("Building memory bus...")
RAM = proj.add_list("RAM", [0]*2048)
# Cartridge PRG ROM list (placeholder, filled later at cartridge-load phase)
PRG_ROM = proj.add_list("PRG_ROM", [0]*32768)
PRG_ROM_SIZE = proj.add_variable("PRG_ROM_SIZE", 32768)
MAPPER_NUM = proj.add_variable("MAPPER_NUM", 0)
PRG_BANKS = proj.add_variable("PRG_BANKS", 2)

ADDR = proj.add_variable("ADDR", 0)
VALUE = proj.add_variable("VALUE", 0)

# bus_read %s -> RESULT
def_read_id, call_read = define_custom_block(cpu, "bus_read %s", args=[CustomBlockArg("addr","string_number")], warp=True)
proto_read = None
for bid,b in cpu.blocks.items():
    if b.opcode=="procedures_prototype" and b.mutation and b.mutation.get("proccode")=="bus_read %s":
        proto_read = b
addr_rep_read = proto_read.inputs[call_read["argument_ids"][0]][1]

s = Script(cpu); s._hat_id = def_read_id; s._tail_id = def_read_id
# if addr < 0x2000: RAM mirror -> index = addr mod 2048
lt_2000 = make_reporter(cpu, "operator_lt", OPERAND1=Reporter(addr_rep_read), OPERAND2=num_input(8192))
ctx1 = s.c_block("control_if_else", CONDITION=Reporter(lt_2000.block_id))
with ctx1 as then1:
    mod_idx = make_reporter(cpu, "operator_mod", NUM1=Reporter(addr_rep_read), NUM2=num_input(2048))
    item = make_reporter(cpu, "data_itemoflist",
        INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(mod_idx.block_id),NUM2=num_input(1)).block_id),
        fields={"LIST":["RAM", RAM]})
    then1.stack("data_setvariableto", VALUE=Reporter(item.block_id), fields={"VARIABLE":["RESULT", RESULT]})
with ctx1.substack2() as else1:
    lt_4020 = make_reporter(cpu, "operator_lt", OPERAND1=Reporter(addr_rep_read), OPERAND2=num_input(16416))
    ctx2 = else1.c_block("control_if_else", CONDITION=Reporter(lt_4020.block_id))
    with ctx2 as then2:
        # PPU/APU/IO register stub: return 0 for now (real PPU passthrough added in Phase 6)
        then2.stack("data_setvariableto", VALUE=num_input(0), fields={"VARIABLE":["RESULT", RESULT]})
    with ctx2.substack2() as else2:
        # Cartridge space $4020-$FFFF -> NROM mapping for now: PRG_ROM[(addr-0x8000) mod PRG_ROM_SIZE]
        ge_8000 = make_reporter(cpu, "operator_gt", OPERAND1=Reporter(addr_rep_read), OPERAND2=num_input(32767))
        ctx3 = else2.c_block("control_if_else", CONDITION=Reporter(ge_8000.block_id))
        with ctx3 as then3:
            off = make_reporter(cpu, "operator_subtract", NUM1=Reporter(addr_rep_read), NUM2=num_input(32768))
            offmod = make_reporter(cpu, "operator_mod", NUM1=Reporter(off.block_id), NUM2=Reporter(make_reporter(cpu,"data_variable",fields={"VARIABLE":["PRG_ROM_SIZE",PRG_ROM_SIZE]}).block_id))
            item2 = make_reporter(cpu, "data_itemoflist",
                INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(offmod.block_id),NUM2=num_input(1)).block_id),
                fields={"LIST":["PRG_ROM", PRG_ROM]})
            then3.stack("data_setvariableto", VALUE=Reporter(item2.block_id), fields={"VARIABLE":["RESULT", RESULT]})
        with ctx3.substack2() as else3:
            else3.stack("data_setvariableto", VALUE=num_input(0), fields={"VARIABLE":["RESULT", RESULT]})
s.finalize()

# bus_write %s %s (addr, value)
def_write_id, call_write = define_custom_block(cpu, "bus_write %s %s", args=[
    CustomBlockArg("addr","string_number"), CustomBlockArg("val","string_number")], warp=True)
proto_write = None
for bid,b in cpu.blocks.items():
    if b.opcode=="procedures_prototype" and b.mutation and b.mutation.get("proccode")=="bus_write %s %s":
        proto_write = b
aw_ids = call_write["argument_ids"]
addr_rep_w = proto_write.inputs[aw_ids[0]][1]
val_rep_w = proto_write.inputs[aw_ids[1]][1]

s2 = Script(cpu); s2._hat_id = def_write_id; s2._tail_id = def_write_id
lt_2000w = make_reporter(cpu, "operator_lt", OPERAND1=Reporter(addr_rep_w), OPERAND2=num_input(8192))
wctx1 = s2.c_block("control_if", CONDITION=Reporter(lt_2000w.block_id))
with wctx1 as wthen1:
    mod_idx_w = make_reporter(cpu, "operator_mod", NUM1=Reporter(addr_rep_w), NUM2=num_input(2048))
    wthen1.stack("data_replaceitemoflist",
        INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(mod_idx_w.block_id),NUM2=num_input(1)).block_id),
        ITEM=Reporter(val_rep_w), fields={"LIST":["RAM", RAM]})
# else branches (PPU/APU/mapper writes) will be filled in Phase 6/5; leave as no-op for now (safe, non-fatal)
s2.finalize()

print("Bus done. Total blocks on CPU sprite:", len(cpu.blocks))

out_path = r"C:\Users\silas\AppData\Local\Temp\claude\D--\8c0daad2-162d-4082-854d-eda434b02951\scratchpad\nes_emulator_wip.sb3"
proj.save(out_path)
print("Saved:", out_path)

# stash refs for next phase script via a small pickle-free approach: just reprint ids needed
print("AND_L", AND_L, "OR_L", OR_L, "XOR_L", XOR_L)
print("RAM", RAM, "PRG_ROM", PRG_ROM)
