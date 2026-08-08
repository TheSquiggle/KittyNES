"""Phase 9: APU channel sprites, built on the sustained-sample + PITCH
architecture documented in research/audio_reference_findings.md.

Also provides `add_wav()`, a correct replacement for sb3_builder's
`add_sound_from_file()`: that helper hardcodes rate=44100 and sampleCount=0,
which is wrong for our 48kHz assets AND fatal for this architecture --
`play sound ... until done` derives its duration from sampleCount/rate, so a
zero sampleCount breaks the one block the whole design depends on.

Run directly to produce a standalone, audible demo:
    python apu_build.py
-> progress/apu_demo.sb3   (green flag plays a scale on the pulse channel)
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, r"D:\KittyNES\code")
from lib import Emu, Reporter

ASSET_DIR = r"D:\KittyNES\assets\audio"

# Must match code/audio_assets.py -- pitch 0 reproduces exactly this frequency.
BASE_HZ = 48000 / 109.0


def wav_info(path):
    """Return (rate, sample_count) by reading the RIFF header for real."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file: %s" % path)
    pos, rate, bits, chans, nframes = 12, 44100, 16, 1, 0
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            chans = struct.unpack("<H", body[2:4])[0]
            rate = struct.unpack("<I", body[4:8])[0]
            bits = struct.unpack("<H", body[14:16])[0]
        elif cid == b"data":
            nframes = size // max(1, (bits // 8) * chans)
        pos += 8 + size + (size & 1)
    return rate, nframes


def add_wav(proj, target, path, name=None):
    """Register a WAV as a sound on `target` with a TRUTHFUL rate/sampleCount."""
    with open(path, "rb") as f:
        data = f.read()
    md5 = hashlib.md5(data).hexdigest()
    fname = "%s.wav" % md5
    proj.register_asset_bytes(fname, data)
    rate, nframes = wav_info(path)
    target.sounds.append({
        "assetId": md5,
        "name": name or os.path.splitext(os.path.basename(path))[0],
        "md5ext": fname,
        "dataFormat": "wav",
        "rate": rate,
        "sampleCount": nframes,
    })
    return rate, nframes


def _preload(e, s, names):
    """The reference's trick: play every asset silently and far out of range,
    then stop, so the browser decodes them up front. Without this the FIRST
    real note stalls while a multi-second 48kHz WAV is decoded."""
    s.stack("sound_seteffectto", VALUE=-1000, fields={"EFFECT": ["PITCH"]})
    s.stack("sound_setvolumeto", VOLUME=0)
    for n in names:
        menu = e._op("sound_sounds_menu", fields={"SOUND_MENU": [n]})
        s.stack("sound_play", SOUND_MENU=Reporter(menu.block_id))
    s.stack("sound_stopallsounds")


def build_demo(out_path=r"D:\KittyNES\progress\apu_demo.sb3"):
    """Standalone audible proof of the technique: hold one sustained pulse
    sample looping and step its PITCH through a scale. If this sounds like
    clean, continuous, in-tune notes (no clicking, no gaps), the architecture
    is validated and can be wired to real $4000-$4013 register writes."""
    e = Emu("APU Pulse 1")
    names = []
    for i in range(4):
        p = os.path.join(ASSET_DIR, "pulse%d.wav" % i)
        rate, n = add_wav(e.proj, e.t, p, "pulse%d" % i)
        names.append("pulse%d" % i)
        print("  added pulse%d  rate=%d samples=%d (%.3fs)" % (i, rate, n, n / rate))

    e.var("target_hz", 440)
    e.var("duty", 0)
    e.lst("SCALE", [261.63, 293.66, 329.63, 349.23, 392.00, 440.00, 493.88, 523.25])
    e.var("i", 1)

    # --- green flag: preload, then spawn the sounding clone ---
    s = e.script("event_whenflagclicked")
    _preload(e, s, names)
    s.stack("control_create_clone_of",
            CLONE_OPTION=Reporter(
                e._op("control_create_clone_of_menu",
                      fields={"CLONE_OPTION": ["_myself_"]}).block_id))
    # step through a scale so the pitch math is audibly verifiable
    e.setv(s, "i", 1)
    with e.FOREVER(s) as body:
        e.setv(body, "target_hz", e.IT("SCALE", e.V("i")))
        body.stack("control_wait", DURATION=0.6)
        e.setv(body, "i", e.ADD(e.MOD(e.V("i"), 8), 1))
    s.finalize()

    # --- the clone: sustain the sample, tracking pitch/volume continuously ---
    s2 = e.script("control_start_as_clone")
    s2.stack("sound_setvolumeto", VOLUME=70)
    with e.FOREVER(s2) as body:
        # pitch = 120 * log2(target_hz / BASE_HZ) = 120 * ln(hz/BASE)/ln(2)
        ratio = e.DIVR(e.V("target_hz"), BASE_HZ)
        lnr = e._op("operator_mathop", NUM=ratio, fields={"OPERATOR": ["ln"]})
        ln2 = e._op("operator_mathop", NUM=2, fields={"OPERATOR": ["ln"]})
        body.stack("sound_seteffectto",
                   VALUE=e.MUL(e.DIVR(Reporter(lnr.block_id), Reporter(ln2.block_id)), 120),
                   fields={"EFFECT": ["PITCH"]})
        menu = e._op("sound_sounds_menu", fields={"SOUND_MENU": ["pulse2"]})
        body.stack("sound_playuntildone", SOUND_MENU=Reporter(menu.block_id))
    s2.finalize()

    e.save(out_path)
    print("saved", out_path)
    return out_path


if __name__ == "__main__":
    build_demo()
