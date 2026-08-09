"""Full integration build: NES core + wired APU, all in one project.

Build order matters: apu_write must be DEFINED before phase2_bus's
bus_write references it (this Emu model does immediate name->id lookups, no
forward declarations -- see PROGRESS_LOG's earlier note on this exact class
of bug). So: create shared APU state -> define apu_write -> THEN build the
bus/CPU/PPU/main-loop -> THEN attach the channel sprites (order-independent
relative to the NES core beyond apu_write existing first).

Usage: python build_with_audio.py [rom.nes] [out.sb3]
"""
import sys

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu
import build_core as BC
import ines_loader as INES
import apu_build
import apu_wire
from black_backdrop import set_black_backdrop

rom = sys.argv[1] if len(sys.argv) > 1 else None
out = sys.argv[2] if len(sys.argv) > 2 else r"D:\KittyNES\progress\nes_emulator_audio.sb3"

e = Emu("NES")
BC.declare_state(e)
BC.phase1_tables(e)

shared_ids = apu_wire.create_shared_apu_state(e)

# apu_write needs the broadcast ids up front, but the channel sprites (which
# own the broadcasts) don't exist yet -- and can't be built before the NES
# sprite without breaking sprite-index assumptions elsewhere. Pre-allocate
# the broadcasts directly on the project (cheap, just ids+names) so both
# sides can reference the same ones regardless of build order.
CHANS = [1, 2, 3, 4]
broadcasts = {
    "update": {ch: e.proj.add_broadcast("apu_update_%d" % ch) for ch in CHANS},
    "restart": {ch: e.proj.add_broadcast("apu_restart_%d" % ch) for ch in CHANS},
    "stop": e.proj.add_broadcast("apu_stop_all"),
}

apu_wire.wire_apu(e, shared_ids, broadcasts)

BC.phase2_bus(e)
BC.phase3_cpu(e)
BC.phase6_ppu_bg(e)
BC.phase6b_sprites(e)
BC.phase8_main_loop(e)

# Attach the channel sprites, reusing the broadcasts already created above
# (build_apu creates its own if given a proj with none registered under
# these names -- so we monkey-patch add_broadcast briefly to hand back the
# existing ids instead of minting duplicates).
_seen = {}
_orig_add_broadcast = e.proj.add_broadcast


def _dedupe_add_broadcast(name):
    if name not in _seen:
        for bid, nm in e.proj.stage.broadcasts.items():
            if nm == name:
                _seen[name] = bid
                break
    if name in _seen:
        return _seen[name]
    bid = _orig_add_broadcast(name)
    _seen[name] = bid
    return bid


e.proj.add_broadcast = _dedupe_add_broadcast
info = apu_build.build_apu(e.proj, shared=shared_ids)
e.proj.add_broadcast = _orig_add_broadcast

assert info["update"] == broadcasts["update"], "broadcast id mismatch: %r vs %r" % (
    info["update"], broadcasts["update"])
assert info["restart"] == broadcasts["restart"]
assert info["stop"] == broadcasts["stop"]

if rom:
    with open(rom, "rb") as f:
        INES.load_rom_into_emu(e, f.read())
    print("loaded ROM:", rom)
else:
    synth = INES.build_synthetic_nes()
    INES.load_rom_into_emu(e, synth)
    print("loaded synthetic test ROM")

set_black_backdrop(e.proj)
print("total blocks (NES sprite):", len(e.t.blocks))
e.save(out)
print("saved", out)
