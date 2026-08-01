"""Minimal Scratch-VM-alike interpreter used to VERIFY generated block graphs in Python.

Supports the subset of opcodes the NES emulator generator emits: data_*, operator_*,
control_if/if_else/repeat/repeat_until/forever, procedures_*, argument_reporter_*.
Pen/looks/motion blocks are executed as no-ops (optionally recorded).
"""
import math
import sys


def _num(v):
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).strip()
        if s == "":
            return 0
        f = float(s)
        return int(f) if f == int(f) else f
    except Exception:
        return 0


def _str(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)


def _truthy(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).lower()
    return s not in ("", "0", "false")


class Stop(Exception):
    pass


class Interp:
    def __init__(self, project, sprite_index=0, max_steps=None):
        pj = project.to_json()
        self.stage = pj["targets"][0]
        self.sprite = pj["targets"][1 + sprite_index]
        self.blocks = dict(self.sprite["blocks"])
        self.blocks.update(self.stage["blocks"])
        # variables/lists by name (globals live on stage)
        self.vars = {}
        self.lists = {}
        for t in pj["targets"]:
            for vid, entry in t["variables"].items():
                self.vars[entry[0]] = entry[1]
            for lid, (name, items) in t["lists"].items():
                self.lists[name] = list(items)
        # procedure map
        self.procs = {}
        for bid, b in self.blocks.items():
            if b["opcode"] == "procedures_definition":
                proto_id = b["inputs"]["custom_block"][1]
                proto = self.blocks[proto_id]
                self.procs[proto["mutation"]["proccode"]] = (bid, proto["mutation"])
        self.steps = 0
        self.max_steps = max_steps
        self.pen = []
        self.log = []
        self.keys = {}  # simulated keyboard state for sensing_keypressed, e.g. {"space": True}

    # ---------------- helpers ----------------
    def _tick(self):
        self.steps += 1
        if self.max_steps and self.steps > self.max_steps:
            raise Stop("max_steps exceeded")

    def inp(self, block, name, frame, default=0):
        i = block["inputs"].get(name)
        if i is None:
            return default
        return self._inp_val(i, frame, default)

    def _inp_val(self, i, frame, default=0):
        kind = i[0]
        payload = i[1]
        if kind == 2 or kind == 3:
            if kind == 3:
                # obscured shadow: [3, blockid_or_prim, shadowprim]
                pass
            if isinstance(payload, str):
                return self.eval(payload, frame)
            if isinstance(payload, list):
                return self._prim(payload)
            return default
        # shadow
        if isinstance(payload, list):
            return self._prim(payload)
        if isinstance(payload, str):
            return self.eval(payload, frame)
        return default

    def _prim(self, p):
        t = p[0]
        if t in (4, 5, 6, 7, 8):
            return _num(p[1])
        if t in (9, 10):
            return p[1]
        if t == 12:
            return self.vars.get(p[1], 0)
        if t == 13:
            return self.lists.get(p[1], [])
        return p[1]

    def cond(self, block, name, frame):
        i = block["inputs"].get(name)
        if i is None:
            return False
        if isinstance(i[1], str):
            return _truthy(self.eval(i[1], frame))
        return False

    # ---------------- execution ----------------
    def run_hat(self, opcode="event_whenflagclicked"):
        for bid, b in self.blocks.items():
            if b["opcode"] == opcode and b.get("topLevel"):
                self.exec_stack(b.get("next"), {})
                return
        raise RuntimeError("hat not found: " + opcode)

    def call_proc_by_name(self, proccode, args=None):
        did, mut = self.procs[proccode]
        frame = dict(args or {})
        self.exec_stack(self.blocks[did].get("next"), frame)

    def exec_stack(self, bid, frame):
        while bid is not None:
            bid = self.exec_block(bid, frame)

    def exec_block(self, bid, frame):
        self._tick()
        b = self.blocks[bid]
        op = b["opcode"]
        nxt = b.get("next")

        if op == "data_setvariableto":
            self.vars[b["fields"]["VARIABLE"][0]] = self.inp(b, "VALUE", frame)
        elif op == "data_changevariableby":
            n = b["fields"]["VARIABLE"][0]
            self.vars[n] = _num(self.vars.get(n, 0)) + _num(self.inp(b, "VALUE", frame))
        elif op == "data_addtolist":
            self.lists.setdefault(b["fields"]["LIST"][0], []).append(self.inp(b, "ITEM", frame))
        elif op == "data_deletealloflist":
            self.lists[b["fields"]["LIST"][0]] = []
        elif op == "data_deleteoflist":
            l = self.lists[b["fields"]["LIST"][0]]
            idx = int(_num(self.inp(b, "INDEX", frame)))
            if 1 <= idx <= len(l):
                del l[idx - 1]
        elif op == "data_replaceitemoflist":
            l = self.lists[b["fields"]["LIST"][0]]
            idx = int(_num(self.inp(b, "INDEX", frame)))
            val = self.inp(b, "ITEM", frame)
            if 1 <= idx <= len(l):
                l[idx - 1] = val
        elif op == "data_insertatlist":
            l = self.lists[b["fields"]["LIST"][0]]
            idx = int(_num(self.inp(b, "INDEX", frame)))
            l.insert(idx - 1, self.inp(b, "ITEM", frame))
        elif op == "control_if":
            if self.cond(b, "CONDITION", frame):
                sub = b["inputs"].get("SUBSTACK")
                if sub:
                    self.exec_stack(sub[1], frame)
        elif op == "control_if_else":
            key = "SUBSTACK" if self.cond(b, "CONDITION", frame) else "SUBSTACK2"
            sub = b["inputs"].get(key)
            if sub:
                self.exec_stack(sub[1], frame)
        elif op == "control_repeat":
            n = int(_num(self.inp(b, "TIMES", frame)))
            sub = b["inputs"].get("SUBSTACK")
            for _ in range(max(0, n)):
                if sub:
                    self.exec_stack(sub[1], frame)
        elif op == "control_repeat_until":
            sub = b["inputs"].get("SUBSTACK")
            while not self.cond(b, "CONDITION", frame):
                self._tick()
                if sub:
                    self.exec_stack(sub[1], frame)
        elif op == "control_forever":
            sub = b["inputs"].get("SUBSTACK")
            while True:
                self._tick()
                if sub:
                    self.exec_stack(sub[1], frame)
        elif op == "control_wait" or op == "control_wait_until":
            pass
        elif op == "control_stop":
            raise Stop("control_stop")
        elif op == "procedures_call":
            mut = b["mutation"]
            proccode = mut["proccode"]
            argids = mut["argumentids"]
            if isinstance(argids, str):
                import json as _j
                argids = _j.loads(argids)
            did, dmut = self.procs[proccode]
            import json as _j
            names = _j.loads(dmut["argumentnames"])
            newframe = {}
            for aid, nm in zip(argids, names):
                i = b["inputs"].get(aid)
                newframe[nm] = self._inp_val(i, frame, "") if i is not None else ""
            self.exec_stack(self.blocks[did].get("next"), newframe)
        elif op.startswith(("pen_", "looks_", "motion_", "sound_", "event_", "sensing_")):
            if op == "pen_setPenColorToColor":
                self._pen_color = self.inp(b, "COLOR", frame)
            elif op == "motion_gotoxy":
                self._pen_xy = (self.inp(b, "X", frame), self.inp(b, "Y", frame))
            elif op == "pen_penDown":
                self.pen.append((getattr(self, "_pen_xy", (0, 0)), getattr(self, "_pen_color", 0)))
        else:
            raise RuntimeError("unsupported stack opcode: " + op)
        return nxt

    # ---------------- reporters ----------------
    def eval(self, bid, frame):
        self._tick()
        b = self.blocks[bid]
        op = b["opcode"]
        I = lambda n, d=0: self.inp(b, n, frame, d)

        if op == "data_variable":
            return self.vars.get(b["fields"]["VARIABLE"][0], 0)
        if op == "data_itemoflist":
            l = self.lists[b["fields"]["LIST"][0]]
            idx = int(_num(I("INDEX")))
            return l[idx - 1] if 1 <= idx <= len(l) else ""
        if op == "data_lengthoflist":
            return len(self.lists[b["fields"]["LIST"][0]])
        if op == "data_itemnumoflist":
            l = self.lists[b["fields"]["LIST"][0]]
            v = I("ITEM")
            for i, x in enumerate(l):
                if _str(x) == _str(v):
                    return i + 1
            return 0
        if op == "data_listcontainsitem":
            l = self.lists[b["fields"]["LIST"][0]]
            v = _str(I("ITEM"))
            return any(_str(x) == v for x in l)
        if op == "argument_reporter_string_number":
            return frame.get(b["fields"]["VALUE"][0], "")
        if op == "argument_reporter_boolean":
            return _truthy(frame.get(b["fields"]["VALUE"][0], False))
        if op == "operator_add":
            return _num(I("NUM1")) + _num(I("NUM2"))
        if op == "operator_subtract":
            return _num(I("NUM1")) - _num(I("NUM2"))
        if op == "operator_multiply":
            return _num(I("NUM1")) * _num(I("NUM2"))
        if op == "operator_divide":
            d = _num(I("NUM2"))
            n = _num(I("NUM1"))
            return (n / d) if d != 0 else (float("inf") if n > 0 else (float("-inf") if n < 0 else float("nan")))
        if op == "operator_mod":
            n, d = _num(I("NUM1")), _num(I("NUM2"))
            if d == 0:
                return float("nan")
            r = math.fmod(n, d)
            if r != 0 and ((r < 0) != (d < 0)):
                r += d
            return r
        if op == "operator_round":
            return round(_num(I("NUM")))
        if op == "operator_mathop":
            o = b["fields"]["OPERATOR"][0]
            n = _num(I("NUM"))
            return {"abs": abs, "floor": math.floor, "ceiling": math.ceil,
                    "sqrt": lambda x: math.sqrt(x) if x >= 0 else float("nan")}[o](n)
        if op == "operator_lt":
            return _cmp(I("OPERAND1"), I("OPERAND2")) < 0
        if op == "operator_gt":
            return _cmp(I("OPERAND1"), I("OPERAND2")) > 0
        if op == "operator_equals":
            return _cmp(I("OPERAND1"), I("OPERAND2")) == 0
        if op == "operator_and":
            return self.cond(b, "OPERAND1", frame) and self.cond(b, "OPERAND2", frame)
        if op == "operator_or":
            return self.cond(b, "OPERAND1", frame) or self.cond(b, "OPERAND2", frame)
        if op == "operator_not":
            return not self.cond(b, "OPERAND", frame)
        if op == "operator_join":
            return _str(I("STRING1", "")) + _str(I("STRING2", ""))
        if op == "operator_length":
            return len(_str(I("STRING", "")))
        if op == "operator_letter_of":
            s = _str(I("STRING", ""))
            i = int(_num(I("LETTER")))
            return s[i - 1] if 1 <= i <= len(s) else ""
        if op == "operator_contains":
            return _str(I("STRING2", "")).lower() in _str(I("STRING1", "")).lower()
        if op == "sensing_keypressed":
            key_block = b["inputs"].get("KEY_OPTION")
            key_name = None
            if key_block and key_block[1] in self.blocks:
                menu = self.blocks[key_block[1]]
                key_name = menu.get("fields", {}).get("KEY_OPTION", [None])[0]
            return bool(self.keys.get(key_name, False))
        if op == "sensing_mousedown":
            return False
        if op == "sensing_timer":
            return 0
        raise RuntimeError("unsupported reporter opcode: " + op)


def _cmp(a, b):
    na, nb = _tonum_or_none(a), _tonum_or_none(b)
    if na is not None and nb is not None:
        return -1 if na < nb else (1 if na > nb else 0)
    sa, sb = _str(a).lower(), _str(b).lower()
    return -1 if sa < sb else (1 if sa > sb else 0)


def _tonum_or_none(v):
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).strip()
        if s == "":
            return None
        return float(s)
    except Exception:
        return None
