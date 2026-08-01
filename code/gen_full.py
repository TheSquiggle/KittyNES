import sys, os, json
SKILL_SCRIPTS = r"C:\Users\silas\AppData\Roaming\Claude\local-agent-mode-sessions\skills-plugin\4ecba4d7-edc6-4795-9198-a90cec018a97\d10d29f8-5542-45a2-bb46-fca0b76b88f9\skills\scratch-sb3\scripts"
sys.path.insert(0, SKILL_SCRIPTS)
from sb3_builder import (
    Project, Script, Reporter, make_reporter,
    define_custom_block, call_custom_block, CustomBlockArg,
    num_input, text_input, var_input, list_input, broadcast_input,
)

proj = Project()
cpu = proj.add_sprite("CPU", x=0, y=0)

# ============================================================
# PHASE 1: bit-op lookup tables
# ============================================================
print("Phase 1: bit-op tables...")
AND_T=[0]*65536; OR_T=[0]*65536; XOR_T=[0]*65536
for a in range(256):
    base=a*256
    for b in range(256):
        AND_T[base+b]=a&b; OR_T[base+b]=a|b; XOR_T[base+b]=a^b
SHL_T=[((v<<1)&0xFF) for v in range(256)]
SHL_CARRY_T=[(1 if (v&0x80) else 0) for v in range(256)]
SHR_T=[(v>>1) for v in range(256)]
SHR_CARRY_T=[(v&1) for v in range(256)]
BIT7_T=[(1 if (v&0x80) else 0) for v in range(256)]
BIT6_T=[(1 if (v&0x40) else 0) for v in range(256)]

AND_L=proj.add_list("AND_T",AND_T); OR_L=proj.add_list("OR_T",OR_T); XOR_L=proj.add_list("XOR_T",XOR_T)
SHL_L=proj.add_list("SHL_T",SHL_T); SHLC_L=proj.add_list("SHL_CARRY_T",SHL_CARRY_T)
SHR_L=proj.add_list("SHR_T",SHR_T); SHRC_L=proj.add_list("SHR_CARRY_T",SHR_CARRY_T)
BIT7_L=proj.add_list("BIT7_T",BIT7_T); BIT6_L=proj.add_list("BIT6_T",BIT6_T)

RESULT=proj.add_variable("RESULT",0)

def find_proto(proccode):
    for bid,b in cpu.blocks.items():
        if b.opcode=="procedures_prototype" and b.mutation and b.mutation.get("proccode")==proccode:
            return b
    raise KeyError(proccode)

def build_binop(name,list_id,list_name):
    proccode=f"{name} %s %s"
    def_id,call=define_custom_block(cpu,proccode,args=[CustomBlockArg("a","string_number"),CustomBlockArg("b","string_number")],warp=True)
    proto=find_proto(proccode); aid=call["argument_ids"]
    a_rep=proto.inputs[aid[0]][1]; b_rep=proto.inputs[aid[1]][1]
    mul=make_reporter(cpu,"operator_multiply",NUM1=Reporter(a_rep),NUM2=num_input(256))
    idx=make_reporter(cpu,"operator_add",NUM1=Reporter(mul.block_id),NUM2=Reporter(b_rep))
    item=make_reporter(cpu,"data_itemoflist",INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(idx.block_id),NUM2=num_input(1)).block_id),fields={"LIST":[list_name,list_id]})
    s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
    s.stack("data_setvariableto",VALUE=Reporter(item.block_id),fields={"VARIABLE":["RESULT",RESULT]})
    s.finalize()
    return call

def build_unop(name,list_id,list_name):
    proccode=f"{name} %s"
    def_id,call=define_custom_block(cpu,proccode,args=[CustomBlockArg("a","string_number")],warp=True)
    proto=find_proto(proccode); aid=call["argument_ids"]
    a_rep=proto.inputs[aid[0]][1]
    item=make_reporter(cpu,"data_itemoflist",INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(a_rep),NUM2=num_input(1)).block_id),fields={"LIST":[list_name,list_id]})
    s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
    s.stack("data_setvariableto",VALUE=Reporter(item.block_id),fields={"VARIABLE":["RESULT",RESULT]})
    s.finalize()
    return call

CALL_AND=build_binop("bitop_and",AND_L,"AND_T")
CALL_OR=build_binop("bitop_or",OR_L,"OR_T")
CALL_XOR=build_binop("bitop_xor",XOR_L,"XOR_T")
CALL_SHL=build_unop("bitop_shl",SHL_L,"SHL_T")
CALL_SHLC=build_unop("bitop_shl_carry",SHLC_L,"SHL_CARRY_T")
CALL_SHR=build_unop("bitop_shr",SHR_L,"SHR_T")
CALL_SHRC=build_unop("bitop_shr_carry",SHRC_L,"SHR_CARRY_T")
CALL_BIT7=build_unop("bitop_bit7",BIT7_L,"BIT7_T")
CALL_BIT6=build_unop("bitop_bit6",BIT6_L,"BIT6_T")
print("  blocks so far:",len(cpu.blocks))

# ============================================================
# PHASE 2: memory bus
# ============================================================
print("Phase 2: memory bus...")
RAM=proj.add_list("RAM",[0]*2048)
PRG_ROM=proj.add_list("PRG_ROM",[0]*32768)
PRG_ROM_SIZE=proj.add_variable("PRG_ROM_SIZE",32768)
MAPPER_NUM=proj.add_variable("MAPPER_NUM",0)
PRG_BANKS=proj.add_variable("PRG_BANKS",2)
CHR_BANKS=proj.add_variable("CHR_BANKS",1)
PRG_BANK_LO=proj.add_variable("PRG_BANK_LO",0)   # selected 16K bank index for $8000-$BFFF (UxROM/MMC1)
PRG_BANK_HI=proj.add_variable("PRG_BANK_HI",1)   # selected bank for $C000-$FFFF
CHR_BANK_SEL=proj.add_variable("CHR_BANK_SEL",0)
MAPPER_SHIFT=proj.add_variable("MAPPER_SHIFT",0)  # MMC1 serial shift register
MAPPER_SHIFT_COUNT=proj.add_variable("MAPPER_SHIFT_COUNT",0)
MMC1_CTRL=proj.add_variable("MMC1_CTRL",12)

def_read_id,call_read=define_custom_block(cpu,"bus_read %s",args=[CustomBlockArg("addr","string_number")],warp=True)
proto_read=find_proto("bus_read %s")
addr_rep_read=proto_read.inputs[call_read["argument_ids"][0]][1]

s=Script(cpu); s._hat_id=def_read_id; s._tail_id=def_read_id
lt_2000=make_reporter(cpu,"operator_lt",OPERAND1=Reporter(addr_rep_read),OPERAND2=num_input(8192))
ctx1=s.c_block("control_if_else",CONDITION=Reporter(lt_2000.block_id))
with ctx1 as then1:
    mod_idx=make_reporter(cpu,"operator_mod",NUM1=Reporter(addr_rep_read),NUM2=num_input(2048))
    item=make_reporter(cpu,"data_itemoflist",INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(mod_idx.block_id),NUM2=num_input(1)).block_id),fields={"LIST":["RAM",RAM]})
    then1.stack("data_setvariableto",VALUE=Reporter(item.block_id),fields={"VARIABLE":["RESULT",RESULT]})
with ctx1.substack2() as else1:
    lt_4020=make_reporter(cpu,"operator_lt",OPERAND1=Reporter(addr_rep_read),OPERAND2=num_input(16416))
    ctx2=else1.c_block("control_if_else",CONDITION=Reporter(lt_4020.block_id))
    with ctx2 as then2:
        then2.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["RESULT",RESULT]})
    with ctx2.substack2() as else2:
        ge_8000=make_reporter(cpu,"operator_gt",OPERAND1=Reporter(addr_rep_read),OPERAND2=num_input(32767))
        ctx3=else2.c_block("control_if_else",CONDITION=Reporter(ge_8000.block_id))
        with ctx3 as then3:
            off=make_reporter(cpu,"operator_subtract",NUM1=Reporter(addr_rep_read),NUM2=num_input(32768))
            offmod=make_reporter(cpu,"operator_mod",NUM1=Reporter(off.block_id),NUM2=Reporter(make_reporter(cpu,"data_variable",fields={"VARIABLE":["PRG_ROM_SIZE",PRG_ROM_SIZE]}).block_id))
            item2=make_reporter(cpu,"data_itemoflist",INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(offmod.block_id),NUM2=num_input(1)).block_id),fields={"LIST":["PRG_ROM",PRG_ROM]})
            then3.stack("data_setvariableto",VALUE=Reporter(item2.block_id),fields={"VARIABLE":["RESULT",RESULT]})
        with ctx3.substack2() as else3:
            else3.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["RESULT",RESULT]})
s.finalize()

def_write_id,call_write=define_custom_block(cpu,"bus_write %s %s",args=[CustomBlockArg("addr","string_number"),CustomBlockArg("val","string_number")],warp=True)
proto_write=find_proto("bus_write %s %s")
aw=call_write["argument_ids"]; addr_rep_w=proto_write.inputs[aw[0]][1]; val_rep_w=proto_write.inputs[aw[1]][1]
s2=Script(cpu); s2._hat_id=def_write_id; s2._tail_id=def_write_id
lt_2000w=make_reporter(cpu,"operator_lt",OPERAND1=Reporter(addr_rep_w),OPERAND2=num_input(8192))
wctx1=s2.c_block("control_if",CONDITION=Reporter(lt_2000w.block_id))
with wctx1 as wthen1:
    mod_idx_w=make_reporter(cpu,"operator_mod",NUM1=Reporter(addr_rep_w),NUM2=num_input(2048))
    wthen1.stack("data_replaceitemoflist",INDEX=Reporter(make_reporter(cpu,"operator_add",NUM1=Reporter(mod_idx_w.block_id),NUM2=num_input(1)).block_id),ITEM=Reporter(val_rep_w),fields={"LIST":["RAM",RAM]})
s2.finalize()
print("  blocks so far:",len(cpu.blocks))

print("  blocks so far:",len(cpu.blocks))

# ============================================================
# PHASE 3: 6502 CPU core
# ============================================================
print("Phase 3: CPU core...")

A=proj.add_variable("A",0); X=proj.add_variable("X",0); Y=proj.add_variable("Y",0)
SP=proj.add_variable("SP",0xFD); PC=proj.add_variable("PC",0)
FLAG_C=proj.add_variable("FLAG_C",0); FLAG_Z=proj.add_variable("FLAG_Z",0)
FLAG_I=proj.add_variable("FLAG_I",1); FLAG_D=proj.add_variable("FLAG_D",0)
FLAG_B=proj.add_variable("FLAG_B",0); FLAG_V=proj.add_variable("FLAG_V",0)
FLAG_N=proj.add_variable("FLAG_N",0)
FLAGVARS={"C":FLAG_C,"Z":FLAG_Z,"I":FLAG_I,"D":FLAG_D,"B":FLAG_B,"V":FLAG_V,"N":FLAG_N}

EFF_ADDR=proj.add_variable("EFF_ADDR",0)
EFF_VALUE=proj.add_variable("EFF_VALUE",0)
ACC_MODE=proj.add_variable("ACC_MODE",0)
OPCODE=proj.add_variable("OPCODE",0)
TEMP1=proj.add_variable("TEMP1",0)
TEMP2=proj.add_variable("TEMP2",0)
BRANCH_TAKEN=proj.add_variable("BRANCH_TAKEN",0)
CPU_CYCLES=proj.add_variable("CPU_CYCLES",0)
HALTED=proj.add_variable("HALTED",0)
LAST_PC=proj.add_variable("LAST_PC",-1)
SAME_PC_COUNT=proj.add_variable("SAME_PC_COUNT",0)

def var_rep(vid,name):
    return make_reporter(cpu,"data_variable",fields={"VARIABLE":[name,vid]})

def setvar(script,name,vid,value):
    script.stack("data_setvariableto",VALUE=value,fields={"VARIABLE":[name,vid]})

def changevar(script,name,vid,delta):
    script.stack("data_changevariableby",VALUE=delta,fields={"VARIABLE":[name,vid]})

def call_bus_read(script,addr_input):
    call_custom_block(script,call_read,addr=addr_input)

def call_bus_write(script,addr_input,val_input):
    call_custom_block(script,call_write,addr=addr_input,val=val_input)

def add(a,b): return make_reporter(cpu,"operator_add",NUM1=a,NUM2=b)
def sub(a,b): return make_reporter(cpu,"operator_subtract",NUM1=a,NUM2=b)
def mul(a,b): return make_reporter(cpu,"operator_multiply",NUM1=a,NUM2=b)
def mod(a,b): return make_reporter(cpu,"operator_mod",NUM1=a,NUM2=b)
def eq(a,b): return make_reporter(cpu,"operator_equals",OPERAND1=a,OPERAND2=b)
def lt(a,b): return make_reporter(cpu,"operator_lt",OPERAND1=a,OPERAND2=b)
def gt(a,b): return make_reporter(cpu,"operator_gt",OPERAND1=a,OPERAND2=b)
def R(x): return Reporter(x.block_id) if hasattr(x,"block_id") else Reporter(x)

def mod65536(a): return mod(a,num_input(65536))
def mod256(a): return mod(a,num_input(256))

# ---- set N,Z flags from an accumulator/value reporter (call after storing into TEMP1) ----
def set_nz_from_temp1(script):
    # Z = (TEMP1==0)?1:0 ; N = bit7(TEMP1)
    zc = script.c_block("control_if_else",CONDITION=R(eq(R(var_rep(TEMP1,"TEMP1")),num_input(0))))
    with zc as t: t.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_Z",FLAG_Z]})
    with zc.substack2() as e: e.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_Z",FLAG_Z]})
    call_custom_block(script,CALL_BIT7,a=R(var_rep(TEMP1,"TEMP1")))
    script.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["FLAG_N",FLAG_N]})

# ---- addressing mode blocks: set EFF_ADDR / EFF_VALUE, advance PC ----
def new_mode_block(name):
    def_id,call=define_custom_block(cpu,f"mode_{name}",args=[],warp=True)
    s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
    if name!="acc":
        s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["ACC_MODE",ACC_MODE]})
    return s,call

def fetch_pc_byte(script):
    """bus_read(PC); PC+=1; RESULT holds the byte."""
    call_bus_read(script,R(var_rep(PC,"PC")))
    changevar(script,"PC",PC,num_input(1))

modes={}

s,c=new_mode_block("imm")
fetch_pc_byte(s)
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["EFF_VALUE",EFF_VALUE]})
s.finalize(); modes["imm"]=c

s,c=new_mode_block("zp")
fetch_pc_byte(s)
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["EFF_ADDR",EFF_ADDR]})
s.finalize(); modes["zp"]=c

s,c=new_mode_block("zpx")
fetch_pc_byte(s)
s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(RESULT,"RESULT")),R(var_rep(X,"X"))))),fields={"VARIABLE":["EFF_ADDR",EFF_ADDR]})
s.finalize(); modes["zpx"]=c

s,c=new_mode_block("zpy")
fetch_pc_byte(s)
s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(RESULT,"RESULT")),R(var_rep(Y,"Y"))))),fields={"VARIABLE":["EFF_ADDR",EFF_ADDR]})
s.finalize(); modes["zpy"]=c

s,c=new_mode_block("abs")
fetch_pc_byte(s); s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
fetch_pc_byte(s)
s.stack("data_setvariableto",VALUE=R(add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1")))),fields={"VARIABLE":["EFF_ADDR",EFF_ADDR]})
s.finalize(); modes["abs"]=c

def abs_indexed(name,idxvar,idxname):
    s,c=new_mode_block(name)
    fetch_pc_byte(s); s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
    fetch_pc_byte(s)
    base=add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1")))
    s.stack("data_setvariableto",VALUE=R(mod65536(add(base,R(var_rep(idxvar,idxname))))),fields={"VARIABLE":["EFF_ADDR",EFF_ADDR]})
    s.finalize(); modes[name]=c

abs_indexed("absx",X,"X")
abs_indexed("absy",Y,"Y")

s,c=new_mode_block("ind")  # JMP indirect, with page-wrap bug
fetch_pc_byte(s); s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
fetch_pc_byte(s)
s.stack("data_setvariableto",VALUE=R(add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1")))),fields={"VARIABLE":["TEMP2",TEMP2]})
call_bus_read(s,R(var_rep(TEMP2,"TEMP2")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
# hi byte address: if low byte of ptr is 0xFF, wraps within page
ptr_lo=mod(R(var_rep(TEMP2,"TEMP2")),num_input(256))
hi_ctx=s.c_block("control_if_else",CONDITION=R(eq(R(ptr_lo),num_input(255))))
with hi_ctx as t:
    call_bus_read(t,R(sub(R(var_rep(TEMP2,"TEMP2")),num_input(255))))
with hi_ctx.substack2() as e:
    call_bus_read(e,R(add(R(var_rep(TEMP2,"TEMP2")),num_input(1))))
s.stack("data_setvariableto",VALUE=R(add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1")))),fields={"VARIABLE":["EFF_ADDR",EFF_ADDR]})
s.finalize(); modes["ind"]=c

s,c=new_mode_block("indx")  # (zp,X)
fetch_pc_byte(s)
zp=mod256(add(R(var_rep(RESULT,"RESULT")),R(var_rep(X,"X"))))
s.stack("data_setvariableto",VALUE=R(zp),fields={"VARIABLE":["TEMP2",TEMP2]})
call_bus_read(s,R(var_rep(TEMP2,"TEMP2")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
call_bus_read(s,R(mod256(add(R(var_rep(TEMP2,"TEMP2")),num_input(1)))))
s.stack("data_setvariableto",VALUE=R(add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1")))),fields={"VARIABLE":["EFF_ADDR",EFF_ADDR]})
s.finalize(); modes["indx"]=c

s,c=new_mode_block("indy")  # (zp),Y
fetch_pc_byte(s)
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP2",TEMP2]})
call_bus_read(s,R(var_rep(TEMP2,"TEMP2")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
call_bus_read(s,R(mod256(add(R(var_rep(TEMP2,"TEMP2")),num_input(1)))))
base=add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1")))
s.stack("data_setvariableto",VALUE=R(mod65536(add(base,R(var_rep(Y,"Y"))))),fields={"VARIABLE":["EFF_ADDR",EFF_ADDR]})
s.finalize(); modes["indy"]=c

s,c=new_mode_block("rel")
fetch_pc_byte(s)
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
sc=s.c_block("control_if",CONDITION=R(gt(R(var_rep(TEMP1,"TEMP1")),num_input(127))))
with sc as t:
    t.stack("data_changevariableby",VALUE=num_input(-256),fields={"VARIABLE":["TEMP1",TEMP1]})
s.stack("data_setvariableto",VALUE=R(mod65536(add(R(var_rep(PC,"PC")),R(var_rep(TEMP1,"TEMP1"))))),fields={"VARIABLE":["EFF_ADDR",EFF_ADDR]})
s.finalize(); modes["rel"]=c

s,c=new_mode_block("acc")
s.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["ACC_MODE",ACC_MODE]})
s.finalize(); modes["acc"]=c

s,c=new_mode_block("impl")
s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["ACC_MODE",ACC_MODE]})
s.finalize(); modes["impl"]=c

print("  addressing modes done. blocks so far:",len(cpu.blocks))
proj.save(r"D:\KittyNES\progress\nes_emulator_wip_phase3_modes.sb3")
print("  checkpoint saved (addressing modes)")

# ---- fetch_effvalue / store_effvalue: honor ACC_MODE ----
def_id,call=define_custom_block(cpu,"fetch_effvalue",args=[],warp=True)
s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
fc=s.c_block("control_if_else",CONDITION=R(eq(R(var_rep(ACC_MODE,"ACC_MODE")),num_input(1))))
with fc as t:
    t.stack("data_setvariableto",VALUE=R(var_rep(A,"A")),fields={"VARIABLE":["EFF_VALUE",EFF_VALUE]})
with fc.substack2() as e:
    call_bus_read(e,R(var_rep(EFF_ADDR,"EFF_ADDR")))
    e.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["EFF_VALUE",EFF_VALUE]})
s.finalize(); CALL_FETCH=call

def_id,call=define_custom_block(cpu,"store_effvalue %s",args=[CustomBlockArg("v","string_number")],warp=True)
proto=find_proto("store_effvalue %s"); v_rep=proto.inputs[call["argument_ids"][0]][1]
s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
sc=s.c_block("control_if_else",CONDITION=R(eq(R(var_rep(ACC_MODE,"ACC_MODE")),num_input(1))))
with sc as t:
    t.stack("data_setvariableto",VALUE=Reporter(v_rep),fields={"VARIABLE":["A",A]})
with sc.substack2() as e:
    call_bus_write(e,R(var_rep(EFF_ADDR,"EFF_ADDR")),Reporter(v_rep))
s.finalize(); CALL_STORE=call

# ---- stack push/pull (SP-relative RAM at $0100+SP) ----
def_id,call=define_custom_block(cpu,"push %s",args=[CustomBlockArg("v","string_number")],warp=True)
proto=find_proto("push %s"); v_rep=proto.inputs[call["argument_ids"][0]][1]
s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
call_bus_write(s,R(add(num_input(256),R(var_rep(SP,"SP")))),Reporter(v_rep))
s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(SP,"SP")),num_input(-1)))),fields={"VARIABLE":["SP",SP]})
s.finalize(); CALL_PUSH=call

def_id,call=define_custom_block(cpu,"pull",args=[],warp=True)
s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(SP,"SP")),num_input(1)))),fields={"VARIABLE":["SP",SP]})
call_bus_read(s,R(add(num_input(256),R(var_rep(SP,"SP")))))
s.finalize(); CALL_PULL=call  # RESULT holds pulled byte

# ---- status byte P: compose/decompose using weighted sums (no bitwise needed) ----
# P = N*128 + V*64 + 1*32 + B*16 + D*8 + I*4 + Z*2 + C*1
def_id,call=define_custom_block(cpu,"compose_P",args=[],warp=True)
s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
expr=num_input(32)
expr=add(R(expr),mul(R(var_rep(FLAG_N,"FLAG_N")),num_input(128)))
expr=add(R(expr),mul(R(var_rep(FLAG_V,"FLAG_V")),num_input(64)))
expr=add(R(expr),mul(R(var_rep(FLAG_B,"FLAG_B")),num_input(16)))
expr=add(R(expr),mul(R(var_rep(FLAG_D,"FLAG_D")),num_input(8)))
expr=add(R(expr),mul(R(var_rep(FLAG_I,"FLAG_I")),num_input(4)))
expr=add(R(expr),mul(R(var_rep(FLAG_Z,"FLAG_Z")),num_input(2)))
expr=add(R(expr),mul(R(var_rep(FLAG_C,"FLAG_C")),num_input(1)))
s.stack("data_setvariableto",VALUE=R(expr),fields={"VARIABLE":["RESULT",RESULT]})
s.finalize(); CALL_COMPOSE_P=call

# decompose_P %s : sets flags from a status byte value, using divmod-by-power-of-2 (no bitwise table needed
# since we only need 8 discrete bit tests -> reuse BIT-table style via mod/floor arithmetic)
def bit_of(value_rep,bitpow):
    # ((value // bitpow) mod 2)
    fl=make_reporter(cpu,"operator_mathop",OPERAND=R(make_reporter(cpu,"operator_divide",NUM1=value_rep,NUM2=num_input(bitpow))),fields={"OPERATOR":["floor"]})
    return mod(R(fl),num_input(2))

def_id,call=define_custom_block(cpu,"decompose_P %s",args=[CustomBlockArg("p","string_number")],warp=True)
proto=find_proto("decompose_P %s"); p_rep=proto.inputs[call["argument_ids"][0]][1]
s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
s.stack("data_setvariableto",VALUE=R(bit_of(Reporter(p_rep),128)),fields={"VARIABLE":["FLAG_N",FLAG_N]})
s.stack("data_setvariableto",VALUE=R(bit_of(Reporter(p_rep),64)),fields={"VARIABLE":["FLAG_V",FLAG_V]})
s.stack("data_setvariableto",VALUE=R(bit_of(Reporter(p_rep),16)),fields={"VARIABLE":["FLAG_B",FLAG_B]})
s.stack("data_setvariableto",VALUE=R(bit_of(Reporter(p_rep),8)),fields={"VARIABLE":["FLAG_D",FLAG_D]})
s.stack("data_setvariableto",VALUE=R(bit_of(Reporter(p_rep),4)),fields={"VARIABLE":["FLAG_I",FLAG_I]})
s.stack("data_setvariableto",VALUE=R(bit_of(Reporter(p_rep),2)),fields={"VARIABLE":["FLAG_Z",FLAG_Z]})
s.stack("data_setvariableto",VALUE=R(bit_of(Reporter(p_rep),1)),fields={"VARIABLE":["FLAG_C",FLAG_C]})
s.finalize(); CALL_DECOMPOSE_P=call

print("  push/pull/status done. blocks so far:",len(cpu.blocks))

# ============================================================
# Mnemonic implementations. Each is a no-arg custom block "op_XXX".
# Convention: for read-group ops, caller has already called fetch_effvalue
# so EFF_VALUE is ready; for RMW-group, EFF_ADDR/ACC_MODE ready and op
# itself does fetch+store; for write-group, EFF_ADDR ready (STA/STX/STY);
# for implied/branch/jump/stack ops, op does everything itself.
# ============================================================
MNEM={}
def new_op(name):
    def_id,call=define_custom_block(cpu,f"op_{name}",args=[],warp=True)
    s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
    MNEM[name]=call
    return s

def set_nz_generic(script,val_reporter):
    script.stack("data_setvariableto",VALUE=val_reporter,fields={"VARIABLE":["TEMP1",TEMP1]})
    set_nz_from_temp1(script)

# ---- Load/Store ----
s=new_op("LDA"); s.stack("data_setvariableto",VALUE=R(var_rep(EFF_VALUE,"EFF_VALUE")),fields={"VARIABLE":["A",A]}); set_nz_generic(s,R(var_rep(A,"A"))); s.finalize()
s=new_op("LDX"); s.stack("data_setvariableto",VALUE=R(var_rep(EFF_VALUE,"EFF_VALUE")),fields={"VARIABLE":["X",X]}); set_nz_generic(s,R(var_rep(X,"X"))); s.finalize()
s=new_op("LDY"); s.stack("data_setvariableto",VALUE=R(var_rep(EFF_VALUE,"EFF_VALUE")),fields={"VARIABLE":["Y",Y]}); set_nz_generic(s,R(var_rep(Y,"Y"))); s.finalize()
s=new_op("STA"); call_bus_write(s,R(var_rep(EFF_ADDR,"EFF_ADDR")),R(var_rep(A,"A"))); s.finalize()
s=new_op("STX"); call_bus_write(s,R(var_rep(EFF_ADDR,"EFF_ADDR")),R(var_rep(X,"X"))); s.finalize()
s=new_op("STY"); call_bus_write(s,R(var_rep(EFF_ADDR,"EFF_ADDR")),R(var_rep(Y,"Y"))); s.finalize()

# ---- Transfers ----
s=new_op("TAX"); s.stack("data_setvariableto",VALUE=R(var_rep(A,"A")),fields={"VARIABLE":["X",X]}); set_nz_generic(s,R(var_rep(X,"X"))); s.finalize()
s=new_op("TAY"); s.stack("data_setvariableto",VALUE=R(var_rep(A,"A")),fields={"VARIABLE":["Y",Y]}); set_nz_generic(s,R(var_rep(Y,"Y"))); s.finalize()
s=new_op("TXA"); s.stack("data_setvariableto",VALUE=R(var_rep(X,"X")),fields={"VARIABLE":["A",A]}); set_nz_generic(s,R(var_rep(A,"A"))); s.finalize()
s=new_op("TYA"); s.stack("data_setvariableto",VALUE=R(var_rep(Y,"Y")),fields={"VARIABLE":["A",A]}); set_nz_generic(s,R(var_rep(A,"A"))); s.finalize()
s=new_op("TSX"); s.stack("data_setvariableto",VALUE=R(var_rep(SP,"SP")),fields={"VARIABLE":["X",X]}); set_nz_generic(s,R(var_rep(X,"X"))); s.finalize()
s=new_op("TXS"); s.stack("data_setvariableto",VALUE=R(var_rep(X,"X")),fields={"VARIABLE":["SP",SP]}); s.finalize()

# ---- Flag ops ----
s=new_op("CLC"); s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_C",FLAG_C]}); s.finalize()
s=new_op("SEC"); s.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_C",FLAG_C]}); s.finalize()
s=new_op("CLI"); s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_I",FLAG_I]}); s.finalize()
s=new_op("SEI"); s.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_I",FLAG_I]}); s.finalize()
s=new_op("CLD"); s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_D",FLAG_D]}); s.finalize()
s=new_op("SED"); s.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_D",FLAG_D]}); s.finalize()
s=new_op("CLV"); s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_V",FLAG_V]}); s.finalize()

# ---- Increment/decrement (register) ----
s=new_op("INX"); s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(X,"X")),num_input(1)))),fields={"VARIABLE":["X",X]}); set_nz_generic(s,R(var_rep(X,"X"))); s.finalize()
s=new_op("INY"); s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(Y,"Y")),num_input(1)))),fields={"VARIABLE":["Y",Y]}); set_nz_generic(s,R(var_rep(Y,"Y"))); s.finalize()
s=new_op("DEX"); s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(X,"X")),num_input(255)))),fields={"VARIABLE":["X",X]}); set_nz_generic(s,R(var_rep(X,"X"))); s.finalize()
s=new_op("DEY"); s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(Y,"Y")),num_input(255)))),fields={"VARIABLE":["Y",Y]}); set_nz_generic(s,R(var_rep(Y,"Y"))); s.finalize()

# ---- INC/DEC memory (RMW) ----
s=new_op("INC")
call_custom_block(s,CALL_FETCH)
s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(EFF_VALUE,"EFF_VALUE")),num_input(1)))),fields={"VARIABLE":["TEMP1",TEMP1]})
call_custom_block(s,CALL_STORE,v=R(var_rep(TEMP1,"TEMP1")))
set_nz_from_temp1(s); s.finalize()

s=new_op("DEC")
call_custom_block(s,CALL_FETCH)
s.stack("data_setvariableto",VALUE=R(mod256(add(R(var_rep(EFF_VALUE,"EFF_VALUE")),num_input(255)))),fields={"VARIABLE":["TEMP1",TEMP1]})
call_custom_block(s,CALL_STORE,v=R(var_rep(TEMP1,"TEMP1")))
set_nz_from_temp1(s); s.finalize()

# ---- Logical (read-group, EFF_VALUE ready) ----
s=new_op("AND")
call_custom_block(s,CALL_AND,a=R(var_rep(A,"A")),b=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["A",A]})
set_nz_generic(s,R(var_rep(A,"A"))); s.finalize()

s=new_op("ORA")
call_custom_block(s,CALL_OR,a=R(var_rep(A,"A")),b=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["A",A]})
set_nz_generic(s,R(var_rep(A,"A"))); s.finalize()

s=new_op("EOR")
call_custom_block(s,CALL_XOR,a=R(var_rep(A,"A")),b=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["A",A]})
set_nz_generic(s,R(var_rep(A,"A"))); s.finalize()

s=new_op("BIT")
call_custom_block(s,CALL_AND,a=R(var_rep(A,"A")),b=R(var_rep(EFF_VALUE,"EFF_VALUE")))
zc=s.c_block("control_if_else",CONDITION=R(eq(R(var_rep(RESULT,"RESULT")),num_input(0))))
with zc as t: t.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_Z",FLAG_Z]})
with zc.substack2() as e: e.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_Z",FLAG_Z]})
call_custom_block(s,CALL_BIT7,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["FLAG_N",FLAG_N]})
call_custom_block(s,CALL_BIT6,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["FLAG_V",FLAG_V]})
s.finalize()

# ---- Arithmetic: ADC/SBC (binary mode only -- correct for NES 2A03, D flag has no HW effect) ----
s=new_op("ADC_UNUSED")
raw=add(add(R(var_rep(A,"A")),R(var_rep(EFF_VALUE,"EFF_VALUE"))),R(var_rep(FLAG_C,"FLAG_C")))
s.stack("data_setvariableto",VALUE=R(raw),fields={"VARIABLE":["TEMP2",TEMP2]})  # unclamped sum 0..510
s.stack("data_setvariableto",VALUE=R(mod256(R(var_rep(TEMP2,"TEMP2")))),fields={"VARIABLE":["TEMP1",TEMP1]})
carryc=s.c_block("control_if_else",CONDITION=R(gt(R(var_rep(TEMP2,"TEMP2")),num_input(255))))
with carryc as t: t.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_C",FLAG_C]})
with carryc.substack2() as e: e.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_C",FLAG_C]})
# overflow: (A^result) & (M^result) & 0x80 != 0  <=>  signs of A and M match, but result sign differs
call_custom_block(s,CALL_BIT7,a=R(var_rep(A,"A")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP2",TEMP2]})  # reuse TEMP2 as A_sign temporarily (ADC carry calc done)
a_sign_rep=var_rep(TEMP2,"TEMP2")
call_custom_block(s,CALL_BIT7,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
m_sign=make_reporter(cpu,"data_variable",fields={"VARIABLE":["RESULT",RESULT]})
call_custom_block(s,CALL_BIT7,a=R(var_rep(TEMP1,"TEMP1")))
r_sign=make_reporter(cpu,"data_variable",fields={"VARIABLE":["RESULT",RESULT]})
# overflow if a_sign==m_sign_saved AND a_sign != r_sign  -- need m_sign captured before overwritten; redo carefully below instead
s.finalize()
# NOTE: overflow computation above is unreliable because RESULT gets overwritten between captures.
# The op_ADC_UNUSED definition above is dead code (harmless orphan custom block, never called).
# Rebuild ADC cleanly under the real proccode:
def_id,call=define_custom_block(cpu,"op_ADC",args=[],warp=True)
s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
MNEM["ADC"]=call
s.stack("data_setvariableto",VALUE=R(add(add(R(var_rep(A,"A")),R(var_rep(EFF_VALUE,"EFF_VALUE"))),R(var_rep(FLAG_C,"FLAG_C")))),fields={"VARIABLE":["TEMP2",TEMP2]})
carryc=s.c_block("control_if_else",CONDITION=R(gt(R(var_rep(TEMP2,"TEMP2")),num_input(255))))
with carryc as t: t.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_C",FLAG_C]})
with carryc.substack2() as e: e.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_C",FLAG_C]})
s.stack("data_setvariableto",VALUE=R(mod256(R(var_rep(TEMP2,"TEMP2")))),fields={"VARIABLE":["TEMP1",TEMP1]})
# overflow = NOT((A xor M) has bit7) AND ((A xor result) has bit7)
call_custom_block(s,CALL_XOR,a=R(var_rep(A,"A")),b=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(make_reporter(cpu,"data_variable",fields={"VARIABLE":["RESULT",RESULT]})),fields={"VARIABLE":["TEMP2",TEMP2]})  # TEMP2 = A^M (0..255)
call_custom_block(s,CALL_BIT7,a=R(var_rep(TEMP2,"TEMP2")))
am_bit7=make_reporter(cpu,"data_variable",fields={"VARIABLE":["RESULT",RESULT]})
s.stack("data_setvariableto",VALUE=R(am_bit7),fields={"VARIABLE":["FLAG_B",FLAG_B]})  # stash in FLAG_B temporarily (recomputed on every BRK/PHP anyway)
call_custom_block(s,CALL_XOR,a=R(var_rep(A,"A")),b=R(var_rep(TEMP1,"TEMP1")))
s.stack("data_setvariableto",VALUE=R(make_reporter(cpu,"data_variable",fields={"VARIABLE":["RESULT",RESULT]})),fields={"VARIABLE":["TEMP2",TEMP2]})
call_custom_block(s,CALL_BIT7,a=R(var_rep(TEMP2,"TEMP2")))
ar_bit7=make_reporter(cpu,"data_variable",fields={"VARIABLE":["RESULT",RESULT]})
notc=s.c_block("control_if_else",CONDITION=R(eq(R(var_rep(FLAG_B,"FLAG_B")),num_input(0))))
with notc as t:
    ovc=t.c_block("control_if_else",CONDITION=R(eq(Reporter(ar_bit7.block_id),num_input(1))))
    with ovc as t2: t2.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_V",FLAG_V]})
    with ovc.substack2() as e2: e2.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_V",FLAG_V]})
with notc.substack2() as e:
    e.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_V",FLAG_V]})
s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_B",FLAG_B]})  # restore B (NES B flag only meaningful on push)
s.stack("data_setvariableto",VALUE=R(var_rep(TEMP1,"TEMP1")),fields={"VARIABLE":["A",A]})
set_nz_generic(s,R(var_rep(A,"A")))
s.finalize()

s=new_op("SBC")
# SBC(M) = ADC(255-M): reuse identical logic with value = 255-EFF_VALUE
inv=sub(num_input(255),R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(inv),fields={"VARIABLE":["EFF_VALUE",EFF_VALUE]})
call_custom_block(s,MNEM["ADC"])
s.finalize()

# ---- Compare group ----
def make_compare(name,regvid,regname):
    s=new_op(name)
    diff=add(sub(R(var_rep(regvid,regname)),R(var_rep(EFF_VALUE,"EFF_VALUE"))),num_input(256))
    s.stack("data_setvariableto",VALUE=R(mod256(diff)),fields={"VARIABLE":["TEMP1",TEMP1]})
    cc=s.c_block("control_if_else",CONDITION=R(lt(R(var_rep(regvid,regname)),R(var_rep(EFF_VALUE,"EFF_VALUE")))))
    with cc as t: t.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_C",FLAG_C]})
    with cc.substack2() as e: e.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_C",FLAG_C]})
    set_nz_from_temp1(s)
    s.finalize()
make_compare("CMP",A,"A")
make_compare("CPX",X,"X")
make_compare("CPY",Y,"Y")

# ---- Shifts/rotates (RMW, honors ACC_MODE via fetch/store) ----
s=new_op("ASL")
call_custom_block(s,CALL_FETCH)
call_custom_block(s,CALL_SHLC,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["FLAG_C",FLAG_C]})
call_custom_block(s,CALL_SHL,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
call_custom_block(s,CALL_STORE,v=R(var_rep(TEMP1,"TEMP1")))
set_nz_from_temp1(s); s.finalize()

s=new_op("LSR")
call_custom_block(s,CALL_FETCH)
call_custom_block(s,CALL_SHRC,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["FLAG_C",FLAG_C]})
call_custom_block(s,CALL_SHR,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
call_custom_block(s,CALL_STORE,v=R(var_rep(TEMP1,"TEMP1")))
set_nz_from_temp1(s); s.finalize()

s=new_op("ROL")
call_custom_block(s,CALL_FETCH)
call_custom_block(s,CALL_SHLC,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP2",TEMP2]})  # new carry-out
call_custom_block(s,CALL_SHL,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(add(R(var_rep(RESULT,"RESULT")),R(var_rep(FLAG_C,"FLAG_C")))),fields={"VARIABLE":["TEMP1",TEMP1]})
call_custom_block(s,CALL_STORE,v=R(var_rep(TEMP1,"TEMP1")))
s.stack("data_setvariableto",VALUE=R(var_rep(TEMP2,"TEMP2")),fields={"VARIABLE":["FLAG_C",FLAG_C]})
set_nz_from_temp1(s); s.finalize()

s=new_op("ROR")
call_custom_block(s,CALL_FETCH)
call_custom_block(s,CALL_SHRC,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP2",TEMP2]})
call_custom_block(s,CALL_SHR,a=R(var_rep(EFF_VALUE,"EFF_VALUE")))
s.stack("data_setvariableto",VALUE=R(add(R(var_rep(RESULT,"RESULT")),mul(R(var_rep(FLAG_C,"FLAG_C")),num_input(128)))),fields={"VARIABLE":["TEMP1",TEMP1]})
call_custom_block(s,CALL_STORE,v=R(var_rep(TEMP1,"TEMP1")))
s.stack("data_setvariableto",VALUE=R(var_rep(TEMP2,"TEMP2")),fields={"VARIABLE":["FLAG_C",FLAG_C]})
set_nz_from_temp1(s); s.finalize()

# ---- Jumps / calls / returns ----
s=new_op("JMP"); s.stack("data_setvariableto",VALUE=R(var_rep(EFF_ADDR,"EFF_ADDR")),fields={"VARIABLE":["PC",PC]}); s.finalize()

s=new_op("JSR")
retaddr=sub(R(var_rep(PC,"PC")),num_input(1))
s.stack("data_setvariableto",VALUE=R(retaddr),fields={"VARIABLE":["TEMP1",TEMP1]})
call_custom_block(s,CALL_PUSH,v=R(make_reporter(cpu,"operator_mathop",OPERAND=R(make_reporter(cpu,"operator_divide",NUM1=R(var_rep(TEMP1,"TEMP1")),NUM2=num_input(256))),fields={"OPERATOR":["floor"]})))
call_custom_block(s,CALL_PUSH,v=R(mod256(R(var_rep(TEMP1,"TEMP1")))))
s.stack("data_setvariableto",VALUE=R(var_rep(EFF_ADDR,"EFF_ADDR")),fields={"VARIABLE":["PC",PC]})
s.finalize()

s=new_op("RTS")
call_custom_block(s,CALL_PULL)
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
call_custom_block(s,CALL_PULL)
s.stack("data_setvariableto",VALUE=R(add(add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1"))),num_input(1))),fields={"VARIABLE":["PC",PC]})
s.finalize()

s=new_op("RTI")
call_custom_block(s,CALL_PULL)
call_custom_block(s,CALL_DECOMPOSE_P,p=R(var_rep(RESULT,"RESULT")))
call_custom_block(s,CALL_PULL)
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
call_custom_block(s,CALL_PULL)
s.stack("data_setvariableto",VALUE=R(add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1")))),fields={"VARIABLE":["PC",PC]})
s.finalize()

s=new_op("BRK")
changevar(s,"PC",PC,num_input(1))
call_custom_block(s,CALL_PUSH,v=R(make_reporter(cpu,"operator_mathop",OPERAND=R(make_reporter(cpu,"operator_divide",NUM1=R(var_rep(PC,"PC")),NUM2=num_input(256))),fields={"OPERATOR":["floor"]})))
call_custom_block(s,CALL_PUSH,v=R(mod256(R(var_rep(PC,"PC")))))
s.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_B",FLAG_B]})
call_custom_block(s,CALL_COMPOSE_P)
call_custom_block(s,CALL_PUSH,v=R(var_rep(RESULT,"RESULT")))
s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_B",FLAG_B]})
s.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_I",FLAG_I]})
call_bus_read(s,num_input(0xFFFE)); s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
call_bus_read(s,num_input(0xFFFF))
s.stack("data_setvariableto",VALUE=R(add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1")))),fields={"VARIABLE":["PC",PC]})
s.finalize()

# ---- Stack ops ----
s=new_op("PHA"); call_custom_block(s,CALL_PUSH,v=R(var_rep(A,"A"))); s.finalize()
s=new_op("PHP")
s.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["FLAG_B",FLAG_B]})
call_custom_block(s,CALL_COMPOSE_P)
call_custom_block(s,CALL_PUSH,v=R(var_rep(RESULT,"RESULT")))
s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["FLAG_B",FLAG_B]})
s.finalize()
s=new_op("PLA")
call_custom_block(s,CALL_PULL)
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["A",A]})
set_nz_generic(s,R(var_rep(A,"A"))); s.finalize()
s=new_op("PLP")
call_custom_block(s,CALL_PULL)
call_custom_block(s,CALL_DECOMPOSE_P,p=R(var_rep(RESULT,"RESULT")))
s.finalize()

# ---- Branches: EFF_ADDR already computed by mode_rel; each branch tests its flag ----
def make_branch(name,flagvid,flagname,want):
    s=new_op(name)
    cc=s.c_block("control_if",CONDITION=R(eq(R(var_rep(flagvid,flagname)),num_input(1 if want else 0))))
    with cc as t:
        t.stack("data_setvariableto",VALUE=R(var_rep(EFF_ADDR,"EFF_ADDR")),fields={"VARIABLE":["PC",PC]})
        t.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["BRANCH_TAKEN",BRANCH_TAKEN]})
    s.finalize()
make_branch("BCC",FLAG_C,"FLAG_C",False)
make_branch("BCS",FLAG_C,"FLAG_C",True)
make_branch("BEQ",FLAG_Z,"FLAG_Z",True)
make_branch("BNE",FLAG_Z,"FLAG_Z",False)
make_branch("BMI",FLAG_N,"FLAG_N",True)
make_branch("BPL",FLAG_N,"FLAG_N",False)
make_branch("BVC",FLAG_V,"FLAG_V",False)
make_branch("BVS",FLAG_V,"FLAG_V",True)

# ---- NOP ----
s=new_op("NOP"); s.finalize()

print("  mnemonics done:",len(MNEM),"blocks so far:",len(cpu.blocks))

# ============================================================
# Opcode table: byte -> (mnemonic, mode, cycles). Standard NMOS 6502.
# mode is one of: imm,zp,zpx,zpy,abs,absx,absy,ind,indx,indy,rel,acc,impl,none(implied,no mode call)
# READ_GROUP ops need fetch_effvalue called before op; RMW/WRITE/other ops call op directly.
# ============================================================
OPS = {
 0x69:("ADC","imm",2), 0x65:("ADC","zp",3), 0x75:("ADC","zpx",4), 0x6D:("ADC","abs",4),
 0x7D:("ADC","absx",4), 0x79:("ADC","absy",4), 0x61:("ADC","indx",6), 0x71:("ADC","indy",5),
 0x29:("AND","imm",2), 0x25:("AND","zp",3), 0x35:("AND","zpx",4), 0x2D:("AND","abs",4),
 0x3D:("AND","absx",4), 0x39:("AND","absy",4), 0x21:("AND","indx",6), 0x31:("AND","indy",5),
 0x0A:("ASL","acc",2), 0x06:("ASL","zp",5), 0x16:("ASL","zpx",6), 0x0E:("ASL","abs",6), 0x1E:("ASL","absx",7),
 0x90:("BCC","rel",2), 0xB0:("BCS","rel",2), 0xF0:("BEQ","rel",2),
 0x24:("BIT","zp",3), 0x2C:("BIT","abs",4),
 0x30:("BMI","rel",2), 0xD0:("BNE","rel",2), 0x10:("BPL","rel",2),
 0x00:("BRK","none",7),
 0x50:("BVC","rel",2), 0x70:("BVS","rel",2),
 0x18:("CLC","none",2), 0xD8:("CLD","none",2), 0x58:("CLI","none",2), 0xB8:("CLV","none",2),
 0xC9:("CMP","imm",2), 0xC5:("CMP","zp",3), 0xD5:("CMP","zpx",4), 0xCD:("CMP","abs",4),
 0xDD:("CMP","absx",4), 0xD9:("CMP","absy",4), 0xC1:("CMP","indx",6), 0xD1:("CMP","indy",5),
 0xE0:("CPX","imm",2), 0xE4:("CPX","zp",3), 0xEC:("CPX","abs",4),
 0xC0:("CPY","imm",2), 0xC4:("CPY","zp",3), 0xCC:("CPY","abs",4),
 0xC6:("DEC","zp",5), 0xD6:("DEC","zpx",6), 0xCE:("DEC","abs",6), 0xDE:("DEC","absx",7),
 0xCA:("DEX","none",2), 0x88:("DEY","none",2),
 0x49:("EOR","imm",2), 0x45:("EOR","zp",3), 0x55:("EOR","zpx",4), 0x4D:("EOR","abs",4),
 0x5D:("EOR","absx",4), 0x59:("EOR","absy",4), 0x41:("EOR","indx",6), 0x51:("EOR","indy",5),
 0xE6:("INC","zp",5), 0xF6:("INC","zpx",6), 0xEE:("INC","abs",6), 0xFE:("INC","absx",7),
 0xE8:("INX","none",2), 0xC8:("INY","none",2),
 0x4C:("JMP","abs",3), 0x6C:("JMP","ind",5),
 0x20:("JSR","abs",6),
 0xA9:("LDA","imm",2), 0xA5:("LDA","zp",3), 0xB5:("LDA","zpx",4), 0xAD:("LDA","abs",4),
 0xBD:("LDA","absx",4), 0xB9:("LDA","absy",4), 0xA1:("LDA","indx",6), 0xB1:("LDA","indy",5),
 0xA2:("LDX","imm",2), 0xA6:("LDX","zp",3), 0xB6:("LDX","zpy",4), 0xAE:("LDX","abs",4), 0xBE:("LDX","absy",4),
 0xA0:("LDY","imm",2), 0xA4:("LDY","zp",3), 0xB4:("LDY","zpx",4), 0xAC:("LDY","abs",4), 0xBC:("LDY","absx",4),
 0x4A:("LSR","acc",2), 0x46:("LSR","zp",5), 0x56:("LSR","zpx",6), 0x4E:("LSR","abs",6), 0x5E:("LSR","absx",7),
 0xEA:("NOP","none",2),
 0x09:("ORA","imm",2), 0x05:("ORA","zp",3), 0x15:("ORA","zpx",4), 0x0D:("ORA","abs",4),
 0x1D:("ORA","absx",4), 0x19:("ORA","absy",4), 0x01:("ORA","indx",6), 0x11:("ORA","indy",5),
 0x48:("PHA","none",3), 0x08:("PHP","none",3), 0x68:("PLA","none",4), 0x28:("PLP","none",4),
 0x2A:("ROL","acc",2), 0x26:("ROL","zp",5), 0x36:("ROL","zpx",6), 0x2E:("ROL","abs",6), 0x3E:("ROL","absx",7),
 0x6A:("ROR","acc",2), 0x66:("ROR","zp",5), 0x76:("ROR","zpx",6), 0x6E:("ROR","abs",6), 0x7E:("ROR","absx",7),
 0x40:("RTI","none",6), 0x60:("RTS","none",6),
 0xE9:("SBC","imm",2), 0xE5:("SBC","zp",3), 0xF5:("SBC","zpx",4), 0xED:("SBC","abs",4),
 0xFD:("SBC","absx",4), 0xF9:("SBC","absy",4), 0xE1:("SBC","indx",6), 0xF1:("SBC","indy",5),
 0x38:("SEC","none",2), 0xF8:("SED","none",2), 0x78:("SEI","none",2),
 0x85:("STA","zp",3), 0x95:("STA","zpx",4), 0x8D:("STA","abs",4), 0x9D:("STA","absx",5),
 0x99:("STA","absy",5), 0x81:("STA","indx",6), 0x91:("STA","indy",6),
 0x86:("STX","zp",3), 0x96:("STX","zpy",4), 0x8E:("STX","abs",4),
 0x84:("STY","zp",3), 0x94:("STY","zpx",4), 0x8C:("STY","abs",4),
 0xAA:("TAX","none",2), 0xA8:("TAY","none",2), 0xBA:("TSX","none",2),
 0x8A:("TXA","none",2), 0x9A:("TXS","none",2), 0x98:("TYA","none",2),
}
# Illegal/undocumented opcodes: treat as 1-byte NOP (documented limitation, functional test avoids these
# for the "trap on illegal opcode" style tests; real games essentially never rely on undocumented opcodes
# except a few (LAX/SAX etc.) which are out of scope for v1).
for byte in range(256):
    if byte not in OPS:
        OPS[byte] = ("NOP","none",2)

READ_GROUP = {"LDA","LDX","LDY","AND","ORA","EOR","BIT","ADC","SBC","CMP","CPX","CPY"}

import json as _json
with open(r"D:\KittyNES\docs\6502_opcode_table.md","w") as f:
    f.write("# 6502 Opcode Table (generated)\n\n| Opcode (hex) | Mnemonic | Mode | Cycles |\n|---|---|---|---|\n")
    for byte in sorted(OPS):
        m,mode,cy = OPS[byte]
        f.write(f"| ${byte:02X} | {m} | {mode} | {cy} |\n")

print("Building CPU step dispatch (256-way)...")
def_id,call=define_custom_block(cpu,"step_cpu",args=[],warp=False)
s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
CALL_STEP=call
# fetch opcode
call_bus_read(s,R(var_rep(PC,"PC")))
s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["OPCODE",OPCODE]})
changevar(s,"PC",PC,num_input(1))
s.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["BRANCH_TAKEN",BRANCH_TAKEN]})

for byte in sorted(OPS):
    m,mode,cy = OPS[byte]
    cc = s.c_block("control_if",CONDITION=R(eq(R(var_rep(OPCODE,"OPCODE")),num_input(byte))))
    with cc as body:
        if mode!="none":
            call_custom_block(body,modes[mode])
        if m in READ_GROUP:
            call_custom_block(body,CALL_FETCH)
        call_custom_block(body,MNEM[m])
        body.stack("data_changevariableby",VALUE=num_input(cy),fields={"VARIABLE":["CPU_CYCLES",CPU_CYCLES]})
s.finalize()
print("  step_cpu dispatch done. blocks so far:",len(cpu.blocks))

proj.save(r"D:\KittyNES\progress\nes_emulator_wip_phase3_cpu.sb3")
print("checkpoint saved (phase3 CPU core, pre-reset/loop)")

# ============================================================
# PHASE 4: reset + a small hand-written 6502 self-test program burned
# into PRG-ROM at reset vector, run to completion, PC/A reported via
# on-stage monitors so it can be visually verified after clicking the
# green flag. (Full Klaus Dormann functional-test ROM is much larger;
# this is a targeted smoke test covering LDA/STA/ADC/SBC/AND/branches/
# JSR/RTS/loops/flags -- see docs/cpu_test_notes.md for exact program.)
# ============================================================
print("Phase 4: CPU self-test program + reset/main loop...")

TEST_PASS=proj.add_variable("TEST_PASS",0)
TEST_FAIL_AT=proj.add_variable("TEST_FAIL_AT",0)

# Hand-assembled 6502 program, loaded at $8000 (reset vector -> $8000):
#   LDA #$05         A9 05
#   STA $10          85 10
#   LDA #$07         A9 07
#   ADC $10          65 10        ; A = 5+7 = 12 ($0C), C=0
#   STA $11          85 11
#   LDX #$00
#   loop: INX               ; X = 1..3
#   CPX #$03
#   BNE loop
#   STX $12                  ; $12 = 3
#   JSR sub
#   STA $13                  ; result of subroutine
#   LDA $11                  ; reload 12
#   CMP #$0C
#   BNE fail
#   LDA $12
#   CMP #$03
#   BNE fail
#   LDA $13
#   CMP #$09
#   BNE fail
#   ; success:
#   LDA #$01
#   STA TEST_PASS_ADDR ($0020)
#   pass_loop: JMP pass_loop
#   fail: LDA #$00
#   STA TEST_PASS_ADDR
#   fail_loop: JMP fail_loop
#   sub: LDA $12       ; A = 3
#        CLC
#        ADC #$06       ; A = 9
#        RTS
prog = []
def emit(*b): prog.extend(b)
# addresses computed manually below relative to base 0x8000
base=0x8000
emit(0xA9,0x05)            # 8000 LDA #5
emit(0x85,0x10)            # 8002 STA $10
emit(0xA9,0x07)            # 8004 LDA #7
emit(0x65,0x10)            # 8006 ADC $10   -> A=12
emit(0x85,0x11)            # 8008 STA $11
emit(0xA2,0x00)            # 800A LDX #0
# loop at 800C
emit(0xE8)                 # 800C INX
emit(0xE0,0x03)            # 800D CPX #3
emit(0xD0,0xFB)            # 800F BNE loop (back to 800C: offset = 0x0C - 0x11 = -5 = 0xFB)
emit(0x86,0x12)            # 8011 STX $12   -> $12=3
emit(0x20,0x00,0x00)       # 8013 JSR sub (placeholder, patched below)
emit(0x85,0x13)            # 8016 STA $13
emit(0xA5,0x11)            # 8018 LDA $11
emit(0xC9,0x0C)            # 801A CMP #$0C
emit(0xD0,0x00)            # 801C BNE fail (placeholder)
emit(0xA5,0x12)            # 801E LDA $12
emit(0xC9,0x03)            # 8020 CMP #$03
emit(0xD0,0x00)            # 8022 BNE fail (placeholder)
emit(0xA5,0x13)            # 8024 LDA $13
emit(0xC9,0x09)            # 8026 CMP #$09
emit(0xD0,0x00)            # 8028 BNE fail (placeholder)
emit(0xA9,0x01)            # 802A LDA #1
emit(0x8D,0x20,0x00)       # 802C STA $0020  (TEST_PASS mirror byte in RAM)
pass_loop_addr = base+0x002F
emit(0x4C, pass_loop_addr&0xFF, pass_loop_addr>>8)  # 802F JMP pass_loop (self)
fail_addr = base+0x0032
emit(0xA9,0x00)            # 8032 LDA #0
emit(0x8D,0x20,0x00)       # 8034 STA $0020
fail_loop_addr = base+0x0037
emit(0x4C, fail_loop_addr&0xFF, fail_loop_addr>>8)  # 8037 JMP fail_loop (self)
sub_addr = base+0x003A
emit(0xA5,0x12)            # 803A LDA $12
emit(0x18)                 # 803C CLC
emit(0x69,0x06)            # 803D ADC #6
emit(0x60)                 # 803F RTS

# patch JSR target and BNE fail offsets
def patch(off_index, *vals):
    for i,v in enumerate(vals): prog[off_index+i]=v
patch(0x0013-0, )  # noop, JSR patched directly below by address math
prog[0x13+1]=sub_addr&0xFF; prog[0x13+2]=sub_addr>>8
# BNE fail offsets: branch target = fail_addr; offset = fail_addr - (branch_instr_addr+2)
def bne_offset(instr_addr):
    off = fail_addr - (instr_addr+2)
    return off & 0xFF
prog[0x1C+1] = bne_offset(base+0x1C)
prog[0x22+1] = bne_offset(base+0x22)
prog[0x28+1] = bne_offset(base+0x28)

print("  test program length:",len(prog),"bytes, ends at",hex(base+len(prog)))

# ---- Reset routine: set up PRG_ROM with test program + reset vector, init CPU state ----
def_id,call=define_custom_block(cpu,"cpu_reset",args=[],warp=True)
CALL_RESET=call
s=Script(cpu); s._hat_id=def_id; s._tail_id=def_id
for i,byte in enumerate(prog):
    addr = 0x8000+i
    idx = (addr-0x8000)  # PRG_ROM is 32K starting at $8000 mirror (NROM: PRG_ROM[0]=$8000)
    s.stack("data_replaceitemoflist",INDEX=num_input(idx+1),ITEM=num_input(byte),fields={"LIST":["PRG_ROM",PRG_ROM]})
# reset vector at $FFFC/$FFFD -> point to $8000 (index 0x7FFC/0x7FFD in 32K PRG_ROM)
s.stack("data_replaceitemoflist",INDEX=num_input(0x7FFC+1),ITEM=num_input(0x00),fields={"LIST":["PRG_ROM",PRG_ROM]})
s.stack("data_replaceitemoflist",INDEX=num_input(0x7FFD+1),ITEM=num_input(0x80),fields={"LIST":["PRG_ROM",PRG_ROM]})
setvar(s,"A",A,num_input(0)); setvar(s,"X",X,num_input(0)); setvar(s,"Y",Y,num_input(0))
setvar(s,"SP",SP,num_input(0xFD))
setvar(s,"FLAG_I",FLAG_I,num_input(1))
setvar(s,"FLAG_C",FLAG_C,num_input(0)); setvar(s,"FLAG_D",FLAG_D,num_input(0))
setvar(s,"FLAG_Z",FLAG_Z,num_input(0)); setvar(s,"FLAG_N",FLAG_N,num_input(0)); setvar(s,"FLAG_V",FLAG_V,num_input(0))
call_bus_read(s,num_input(0xFFFC)); s.stack("data_setvariableto",VALUE=R(var_rep(RESULT,"RESULT")),fields={"VARIABLE":["TEMP1",TEMP1]})
call_bus_read(s,num_input(0xFFFD))
s.stack("data_setvariableto",VALUE=R(add(mul(R(var_rep(RESULT,"RESULT")),num_input(256)),R(var_rep(TEMP1,"TEMP1")))),fields={"VARIABLE":["PC",PC]})
setvar(s,"CPU_CYCLES",CPU_CYCLES,num_input(0))
setvar(s,"HALTED",HALTED,num_input(0))
setvar(s,"LAST_PC",LAST_PC,num_input(-1))
setvar(s,"SAME_PC_COUNT",SAME_PC_COUNT,num_input(0))
setvar(s,"TEST_PASS",TEST_PASS,num_input(0))
s.finalize()

# ---- main green-flag script: reset, then run step_cpu in a loop, halting when PC stops
# advancing usefully (stuck in a 1-2 instruction self-loop = program finished) ----
main=Script(cpu,x=0,y=-200)
main.hat("event_whenflagclicked")
call_custom_block(main,CALL_RESET)
with main.c_block("control_repeat_until",CONDITION=R(eq(R(var_rep(HALTED,"HALTED")),num_input(1)))) as body:
    call_custom_block(body,CALL_STEP)
    eqc=body.c_block("control_if_else",CONDITION=R(eq(R(var_rep(PC,"PC")),R(var_rep(LAST_PC,"LAST_PC")))))
    with eqc as t:
        t.stack("data_changevariableby",VALUE=num_input(1),fields={"VARIABLE":["SAME_PC_COUNT",SAME_PC_COUNT]})
        sc=t.c_block("control_if",CONDITION=R(gt(R(var_rep(SAME_PC_COUNT,"SAME_PC_COUNT")),num_input(2))))
        with sc as t2:
            t2.stack("data_setvariableto",VALUE=num_input(1),fields={"VARIABLE":["HALTED",HALTED]})
    with eqc.substack2() as e:
        e.stack("data_setvariableto",VALUE=num_input(0),fields={"VARIABLE":["SAME_PC_COUNT",SAME_PC_COUNT]})
    body.stack("data_setvariableto",VALUE=R(var_rep(PC,"PC")),fields={"VARIABLE":["LAST_PC",LAST_PC]})
main.stack("looks_say",MESSAGE=text_input("CPU test done"))
main.finalize()

print("  reset+main loop done. blocks so far:",len(cpu.blocks))

out1 = r"D:\KittyNES\progress\nes_emulator_wip_phase4_cputest.sb3"
proj.save(out1)
print("Saved phase4 checkpoint:", out1)






