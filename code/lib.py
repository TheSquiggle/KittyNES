"""Ergonomic helper layer over sb3_builder for the NES emulator build."""
import sys, os, json

SKILL = r"C:\Users\silas\AppData\Roaming\Claude\local-agent-mode-sessions\skills-plugin\4ecba4d7-edc6-4795-9198-a90cec018a97\d10d29f8-5542-45a2-bb46-fca0b76b88f9\skills\scratch-sb3\scripts"
sys.path.insert(0, SKILL)

from sb3_builder import (  # noqa
    Project, Script, Reporter, make_reporter, Block,
    define_custom_block, call_custom_block, CustomBlockArg,
    num_input, text_input, var_input, list_input, broadcast_input, color_input,
    new_id, NO_SHADOW, SHADOW,
)


class Emu:
    """Holds the project + one sprite, and name->id maps for vars/lists/procs."""

    def __init__(self, sprite_name="NES", proj=None):
        """proj=None creates a fresh Project. Pass an EXISTING Project to add
        this console as an additional sprite alongside others -- because all
        state is sprite-local (see var/lst below), two consoles can coexist in
        one project using identical internal names with no collisions. That's
        what makes 'one sprite = one emulator' the unit of composition."""
        self.proj = proj if proj is not None else Project()
        self.t = self.proj.add_sprite(sprite_name)
        self.vars = {}
        self.lists = {}
        self.procs = {}
        self._global_vars = set()
        self._global_lists = set()
        self._px = 0
        self._py = 0

    # ---- declarations ------------------------------------------------
    # Emulator state is SPRITE-LOCAL by default ("for this sprite only"), so
    # the whole console lives self-contained in one sprite and a second
    # console sprite can reuse the same names without colliding. Pass
    # glob=True only for state that genuinely must cross sprite boundaries
    # (e.g. APU channel sprites reading per-channel frequency/volume).
    def var(self, name, value=0, glob=False):
        if name not in self.vars:
            self.vars[name] = self.proj.add_variable(
                name, value, target=None if glob else self.t)
            if glob:
                self._global_vars.add(name)
        return self.vars[name]

    def lst(self, name, items=None, glob=False):
        if name not in self.lists:
            self.lists[name] = self.proj.add_list(
                name, list(items or []), target=None if glob else self.t)
            if glob:
                self._global_lists.add(name)
        return self.lists[name]

    def _list_target(self, name):
        return self.proj.stage if name in self._global_lists else self.t

    def _var_target(self, name):
        return self.proj.stage if name in self._global_vars else self.t

    def set_list_items(self, name, items):
        lid = self.lists[name]
        self._list_target(name).lists[lid] = [name, list(items)]

    def set_var_value(self, name, value):
        """Set a variable's INITIAL (saved) value, wherever it actually lives."""
        vid = self.var(name)
        self._var_target(name).variables[vid][1] = value

    # ---- reporters ---------------------------------------------------
    def V(self, name):
        return make_reporter(self.t, "data_variable", fields={"VARIABLE": [name, self.var(name)]})

    def LEN(self, name):
        return make_reporter(self.t, "data_lengthoflist", fields={"LIST": [name, self.lst(name)]})

    def IT(self, name, index):
        return make_reporter(self.t, "data_itemoflist", INDEX=index,
                             fields={"LIST": [name, self.lst(name)]})

    def ARG(self, name):
        return make_reporter(self.t, "argument_reporter_string_number", fields={"VALUE": [name]})

    def BARG(self, name):
        return make_reporter(self.t, "argument_reporter_boolean", fields={"VALUE": [name]})

    def _op(self, opcode, **kw):
        return make_reporter(self.t, opcode, **kw)

    def ADD(self, a, b):  return self._op("operator_add", NUM1=a, NUM2=b)
    def SUB(self, a, b):  return self._op("operator_subtract", NUM1=a, NUM2=b)
    def MUL(self, a, b):  return self._op("operator_multiply", NUM1=a, NUM2=b)
    def DIVR(self, a, b): return self._op("operator_divide", NUM1=a, NUM2=b)
    def MOD(self, a, b):  return self._op("operator_mod", NUM1=a, NUM2=b)
    def FLOOR(self, a):   return self._op("operator_mathop", NUM=a, fields={"OPERATOR": ["floor"]})
    def IDIV(self, a, b): return self.FLOOR(self.DIVR(a, b))
    def EQ(self, a, b):   return self._op("operator_equals", OPERAND1=a, OPERAND2=b)
    def LT(self, a, b):   return self._op("operator_lt", OPERAND1=a, OPERAND2=b)
    def GT(self, a, b):   return self._op("operator_gt", OPERAND1=a, OPERAND2=b)
    def AND(self, a, b):  return self._op("operator_and", OPERAND1=a, OPERAND2=b)
    def OR(self, a, b):   return self._op("operator_or", OPERAND1=a, OPERAND2=b)
    def NOT(self, a):     return self._op("operator_not", OPERAND=a)
    def JOIN(self, a, b): return self._op("operator_join", STRING1=a, STRING2=b)
    def ROUND(self, a):   return self._op("operator_round", NUM=a)

    # bit ops via lookup tables
    def BAND(self, a, b): return self.IT("T_AND", self.ADD(self.MUL(a, 256), self.ADD(b, 1)))
    def BOR(self, a, b):  return self.IT("T_OR", self.ADD(self.MUL(a, 256), self.ADD(b, 1)))
    def BXOR(self, a, b): return self.IT("T_XOR", self.ADD(self.MUL(a, 256), self.ADD(b, 1)))
    def BIT7(self, a):    return self.IT("T_BIT7", self.ADD(a, 1))
    def BIT6(self, a):    return self.IT("T_BIT6", self.ADD(a, 1))
    def SHL(self, a):     return self.IT("T_SHL", self.ADD(a, 1))
    def SHLC(self, a):    return self.IT("T_SHLC", self.ADD(a, 1))
    def SHR(self, a):     return self.IT("T_SHR", self.ADD(a, 1))
    def SHRC(self, a):    return self.IT("T_SHRC", self.ADD(a, 1))

    def TRUE(self):  return self.EQ(1, 1)

    # ---- statements --------------------------------------------------
    def setv(self, s, name, value):
        s.stack("data_setvariableto", VALUE=value, fields={"VARIABLE": [name, self.var(name)]})

    def chg(self, s, name, value):
        s.stack("data_changevariableby", VALUE=value, fields={"VARIABLE": [name, self.var(name)]})

    def repl(self, s, name, index, item):
        s.stack("data_replaceitemoflist", INDEX=index, ITEM=item,
                fields={"LIST": [name, self.lst(name)]})

    def addl(self, s, name, item):
        s.stack("data_addtolist", ITEM=item, fields={"LIST": [name, self.lst(name)]})

    def dellall(self, s, name):
        s.stack("data_deletealloflist", fields={"LIST": [name, self.lst(name)]})

    # ---- procedures --------------------------------------------------
    def defproc(self, name, args=(), warp=True):
        """name: bare name; args: list of arg names (string_number). Returns body Script."""
        proccode = name + "".join(" %s" % "%s" for _ in args)
        cargs = [CustomBlockArg(a, "string_number") for a in args]
        self._py += 600
        def_id, info = define_custom_block(self.t, proccode, args=cargs, warp=warp,
                                           x=0, y=self._py)
        self.procs[name] = info
        s = Script(self.t)
        s._hat_id = def_id
        s._tail_id = def_id
        return s

    def call(self, s, name, **vals):
        info = self.procs[name]
        inputs = {}
        for aid, aname in zip(info["argument_ids"], info["arg_names"]):
            if aname in vals:
                inputs[aid] = s._resolve_input(vals[aname])
        mutation = {
            "tagName": "mutation", "children": [],
            "proccode": info["proccode"],
            "argumentids": json.dumps(info["argument_ids"]),
            "warp": json.dumps(info.get("warp", False)),
        }
        bid = s._add_block("procedures_call", inputs=inputs, mutation=mutation)
        blk = s.target.blocks[bid]
        if s._tail_id is None:
            if getattr(s, "_is_c_body", False):
                blk.parent = s._c_parent_id
                s.target.blocks[s._c_parent_id].inputs[s._c_slot] = [NO_SHADOW, bid]
            else:
                blk.top_level = True
                blk.x, blk.y = s.x, s.y
                s._hat_id = bid
        else:
            blk.parent = s._tail_id
            s.target.blocks[s._tail_id].next = bid
        s._tail_id = bid
        s._link_reporter_parents(inputs, bid)
        return s

    def script(self, hat="event_whenflagclicked", fields=None):
        self._py += 600
        s = Script(self.t, x=800, y=self._py)
        s.hat(hat, fields=fields)
        return s

    # ---- control sugar -----------------------------------------------
    def IF(self, s, cond):
        return s.c_block("control_if", CONDITION=cond)

    def IFELSE(self, s, cond):
        return s.c_block("control_if_else", CONDITION=cond)

    def REPEAT(self, s, times):
        return s.c_block("control_repeat", TIMES=times)

    def UNTIL(self, s, cond):
        return s.c_block("control_repeat_until", CONDITION=cond)

    def FOREVER(self, s):
        return s.c_block("control_forever")

    # ---- binary dispatch ---------------------------------------------
    def dispatch(self, s, value_fn, keys, emit):
        """Balanced binary dispatch on an integer selector.
        value_fn() -> fresh Reporter for the selector.
        keys: sorted list of ints. emit(body_script, key)."""
        keys = sorted(keys)

        def rec(sc, lo, hi):
            if lo == hi:
                emit(sc, keys[lo])
                return
            mid = (lo + hi) // 2
            ctx = sc.c_block("control_if_else", CONDITION=self.LT(value_fn(), keys[mid + 1]))
            with ctx as a:
                rec(a, lo, mid)
            with ctx.substack2() as b:
                rec(b, mid + 1, hi)

        rec(s, 0, len(keys) - 1)

    def save(self, path):
        return self.proj.save(path)
