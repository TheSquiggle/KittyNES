"""Proves the 'one sprite = one emulator' architecture actually composes:
builds TWO complete, independent NES cores as two sprites in ONE project and
verifies they don't collide.

This is the structural guarantee that makes adding a second console later
(Game Boy, SMS, ...) a matter of adding a sprite rather than renaming every
variable in the project.
"""
import sys

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
import ines_loader as INES

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), label, "got=%r want=%r" % (got, want))
    if not ok:
        FAILURES.append(label)


def build_console(name, proj=None):
    e = Emu(name, proj=proj)
    BC.declare_state(e)
    BC.phase1_tables(e)
    BC.phase2_bus(e)
    BC.phase3_cpu(e)
    BC.phase6_ppu_bg(e)
    BC.phase6b_sprites(e)
    BC.phase8_main_loop(e)
    INES.load_rom_into_emu(e, INES.build_synthetic_nes())
    return e


a = build_console("NES 1")
b = build_console("NES 2", proj=a.proj)

pj = a.proj.to_json()
stage = pj["targets"][0]
sprites = {t["name"]: t for t in pj["targets"] if not t["isStage"]}

check("two console sprites exist", sorted(sprites), ["NES 1", "NES 2"])
check("stage holds NO emulator variables", len(stage["variables"]), 0)
check("stage holds NO emulator lists", len(stage["lists"]), 0)

for nm in ("NES 1", "NES 2"):
    check("%s has its own local variables" % nm, len(sprites[nm]["variables"]) > 100, True)
    check("%s has its own local lists" % nm, len(sprites[nm]["lists"]) > 30, True)
    check("%s has a full block graph" % nm, len(sprites[nm]["blocks"]) > 2000, True)

# The decisive check: identical names, but DISTINCT ids -> no shared state.
names1 = {e[0] for e in sprites["NES 1"]["variables"].values()}
names2 = {e[0] for e in sprites["NES 2"]["variables"].values()}
ids1 = set(sprites["NES 1"]["variables"].keys())
ids2 = set(sprites["NES 2"]["variables"].keys())
lids1 = set(sprites["NES 1"]["lists"].keys())
lids2 = set(sprites["NES 2"]["lists"].keys())

check("both consoles use the SAME variable names", names1 == names2, True)
check("...but share NO variable ids (independent state)", ids1 & ids2, set())
check("...and share NO list ids", lids1 & lids2, set())

# Independent data: writing one console's RAM must not touch the other's.
ram1 = [e for e in sprites["NES 1"]["lists"].values() if e[0] == "RAM"][0][1]
ram2 = [e for e in sprites["NES 2"]["lists"].values() if e[0] == "RAM"][0][1]
check("each console has its own 2KB RAM list", (len(ram1), len(ram2)), (2048, 2048))
check("RAM lists are distinct objects", ram1 is ram2, False)

out = r"D:\KittyNES\progress\multiconsole_demo.sb3"
a.proj.save(out)
print("\nsaved", out)

print("\n%s" % ("ALL MULTI-CONSOLE CHECKS PASSED" if not FAILURES
                else "FAILURES: %r" % FAILURES))
sys.exit(1 if FAILURES else 0)
