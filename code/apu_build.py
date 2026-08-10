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

# Single source of truth: BASE_HZ is the frequency the pulse/triangle assets
# were actually rendered at, so pitch effect 0 reproduces it exactly. Importing
# it (rather than re-deriving it here) means regenerating the assets at a
# different rate/period can't silently put every note out of tune.
from audio_assets import BASE_HZ, BASE_NOISE_HZ  # noqa: E402


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


NOISE_PERIODS = [4, 8, 16, 32, 64, 96, 128, 160,
                 202, 254, 380, 508, 762, 1016, 2034, 4068]

# Channel indices in the shared APU_* lists (1-based, Scratch style).
CH_PULSE1, CH_PULSE2, CH_TRIANGLE, CH_NOISE = 1, 2, 3, 4


def _pitch_expr(e, hz_reporter, base_hz):
    """120 * log2(hz / base_hz), via ln since Scratch has no log2.

    `base_hz` is whatever frequency the PLAYING asset was rendered at, so
    pitch 0 reproduces it exactly: BASE_HZ for the tonal (pulse/triangle)
    assets, BASE_NOISE_HZ for noise's two LFSR-mode assets (see
    audio_assets.py's noise_base() for why noise needs its own base -- using
    one alias-free reference asset per mode, pitch-shifted, instead of 32
    separately-rendered (and mostly aliased) per-period assets)."""
    lnr = e._op("operator_mathop", NUM=e.DIVR(hz_reporter, base_hz),
                fields={"OPERATOR": ["ln"]})
    ln2 = e._op("operator_mathop", NUM=2, fields={"OPERATOR": ["ln"]})
    return e.MUL(e.DIVR(Reporter(lnr.block_id), Reporter(ln2.block_id)), 120)


def build_apu(proj, shared=None):
    """Add the APU channel sprites to `proj`.

    Returns a dict of the broadcast ids the NES core should fire when the CPU
    writes $4000-$4013:
        {"update": {ch: bcast_id}, "restart": {ch: bcast_id}, "stop": id}

    Cross-sprite state lives in Stage-global lists (this is the one place
    `glob=True` is warranted -- the channel sprites must read values the NES
    sprite writes):
        APU_FREQ[ch]  target frequency in Hz (0 = silent). For noise (ch=4)
                      this holds the LFSR's native clock rate (CPU_HZ/period),
                      pitch-shifted the same way as the tonal channels rather
                      than looked up as a separate per-period asset.
        APU_VOL[ch]   0-100 channel volume
        APU_DUTY[ch]  pulse duty index 0-3 (pulse channels only)
        APU_NOISEIDX  which noise MODE asset (1=noiseA/mode0, 2=noiseB/mode1)
                      the noise channel should play -- period no longer
                      selects an asset, only pitch (see audio_assets.py's
                      noise_base()/BASE_NOISE_HZ for why: the old
                      one-asset-per-period design aliased badly at short
                      periods, which is what caused noise to sound too low).

    Why both an "update" and a "restart" broadcast per channel: `play sound
    until done` blocks for the whole multi-second asset, so a plain forever
    loop could not react to a register write mid-sample. Broadcast hats run as
    SEPARATE concurrent scripts, so pitch/volume changes apply immediately
    while the sample keeps sounding. Changing which *asset* plays (noise
    period, pulse duty) can't be done mid-sample, so that path deletes the
    clone and makes a new one instead.
    """
    chans = [
        ("APU Pulse 1", CH_PULSE1, ["pulse%d" % i for i in range(4)]),
        ("APU Pulse 2", CH_PULSE2, ["pulse%d" % i for i in range(4)]),
        ("APU Triangle", CH_TRIANGLE, ["triangle"]),
        ("APU Noise", CH_NOISE, ["noiseA", "noiseB"]),
    ]

    bc_update = {ch: proj.add_broadcast("apu_update_%d" % ch) for _, ch, _ in chans}
    bc_restart = {ch: proj.add_broadcast("apu_restart_%d" % ch) for _, ch, _ in chans}
    bc_stop = proj.add_broadcast("apu_stop_all")

    noise_names = ["noiseA", "noiseB"]  # index 1=mode0, 2=mode1
    # Shared Stage-globals are created ONCE and then injected into each
    # sprite's name->id map. Calling e.lst(..., glob=True) per sprite would
    # mint a SEPARATE list per sprite that merely shares a display name, so
    # the channels would never see what the NES core writes.
    #
    # If `shared` is given (ids already created elsewhere -- e.g. by the NES
    # core, so apu_write can reference them directly), seed from it instead
    # of minting new ones, so the channel sprites and the CPU agree on the
    # SAME list/var, not just the same display name.
    shared_lists = {"APU_FREQ": shared["APU_FREQ"], "APU_VOL": shared["APU_VOL"],
                    "APU_DUTY": shared["APU_DUTY"],
                    "APU_NOISENAMES": shared["APU_NOISENAMES"],
                    "APU_LENSEC": shared["APU_LENSEC"]} if shared else {}
    shared_vars = {"APU_NOISEIDX": shared["APU_NOISEIDX"]} if shared else {}

    def _share(e):
        for nm, items in (("APU_FREQ", [0, 0, 0, 0]), ("APU_VOL", [0, 0, 0, 0]),
                          ("APU_DUTY", [0, 0]), ("APU_NOISENAMES", noise_names),
                          ("APU_LENSEC", [0, 0, 0, 0])):
            if nm not in shared_lists:
                shared_lists[nm] = e.lst(nm, items, glob=True)
            else:
                e.lists[nm] = shared_lists[nm]
                e._global_lists.add(nm)
        if "APU_NOISEIDX" not in shared_vars:
            shared_vars["APU_NOISEIDX"] = e.var("APU_NOISEIDX", 1, glob=True)
        else:
            e.vars["APU_NOISEIDX"] = shared_vars["APU_NOISEIDX"]
            e._global_vars.add("APU_NOISEIDX")

    for sprite_name, ch, assets in chans:
        e = Emu(sprite_name, proj=proj)
        _share(e)
        # Distinguishes the sounding clone from the template sprite, so a
        # restart broadcast can mean "die" to the clone and "spawn" to the
        # template. Sprite-local: each channel tracks its own.
        e.var("is_clone", 0)

        for nm in assets:
            add_wav(proj, e.t, os.path.join(ASSET_DIR, nm + ".wav"), nm)

        # ---- green flag: preload every asset, then start sounding ----
        s = e.script("event_whenflagclicked")
        e.setv(s, "is_clone", 0)
        _preload(e, s, assets)
        s.stack("control_create_clone_of",
                CLONE_OPTION=Reporter(
                    e._op("control_create_clone_of_menu",
                          fields={"CLONE_OPTION": ["_myself_"]}).block_id))
        s.finalize()

        # ---- the sounding clone ----
        if ch == CH_NOISE:
            # Selected by MODE only now (1=noiseA, 2=noiseB) -- period is
            # pitch, same as every other channel. See module docstring.
            sound_sel = e.IT("APU_NOISENAMES", e.V("APU_NOISEIDX"))
        elif ch in (CH_PULSE1, CH_PULSE2):
            # +1: APU_DUTY holds 0-3, asset names are pulse0..pulse3.
            sound_sel = e.JOIN("pulse", e.IT("APU_DUTY", ch))
        else:
            sound_sel = "triangle"

        s2 = e.script("control_start_as_clone")
        e.setv(s2, "is_clone", 1)
        # Set pitch/volume HERE, immediately at spawn, rather than relying
        # solely on the separate apu_update_<ch> broadcast to arrive in
        # time. A freshly-created clone otherwise starts at whatever
        # volume/pitch Scratch defaults a new clone to (100%) until that
        # broadcast's receiving script gets its turn -- broadcast delivery
        # order relative to a same-tick clone spawn isn't something this
        # architecture can rely on. Since notes now restart the clone on
        # every $4003/$4007/$400B/$400F write (the length-counter fix), a
        # real reported symptom -- audio "just sits there and makes noise"
        # on a game that restarts notes constantly -- traces directly to
        # this: a full-volume blip on every single restart. Reading
        # APU_FREQ/APU_VOL here closes that gap; the CPU already writes
        # them before broadcasting restart, so the value is correct by the
        # time this hat fires.
        base_hz = BASE_NOISE_HZ if ch == CH_NOISE else BASE_HZ
        s2.stack("sound_seteffectto",
                 VALUE=_pitch_expr(e, e.IT("APU_FREQ", ch), base_hz),
                 fields={"EFFECT": ["PITCH"]})
        s2.stack("sound_setvolumeto", VOLUME=e.IT("APU_VOL", ch))
        with e.FOREVER(s2) as body:
            if isinstance(sound_sel, str):
                menu = e._op("sound_sounds_menu", fields={"SOUND_MENU": [sound_sel]})
                body.stack("sound_playuntildone", SOUND_MENU=Reporter(menu.block_id))
            else:
                # A reporter in SOUND_MENU selects the sound by name at runtime.
                body.stack("sound_playuntildone", SOUND_MENU=sound_sel)
        s2.finalize()

        # ---- auto-mute timer: a SEPARATE concurrent "start as clone"
        # script (Scratch runs every matching hat as its own thread within
        # the same clone instance). Snapshots the note's length in a
        # sprite-local var -- clones get their OWN independent copy of
        # sprite-local variables, so concurrent notes on the same channel
        # can't stomp each other's snapshot the way a shared/global var
        # would. If a NEWER note arrives before this timer elapses, the CPU
        # broadcasts a restart, which deletes THIS clone outright -- killing
        # this thread along with it -- so there's no race to reconcile, this
        # timer simply never fires for a superseded note. This is the actual
        # fix for "sounds play too long": real hardware auto-mutes via a
        # length counter, which nothing here implemented before now. ----
        s2b = e.script("control_start_as_clone")
        e.setv(s2b, "MY_LEN", e.IT("APU_LENSEC", ch))
        s2b.stack("control_wait", DURATION=e.V("MY_LEN"))
        s2b.stack("sound_setvolumeto", VOLUME=0)
        s2b.finalize()

        # ---- update hat: applies pitch+volume WITHOUT interrupting playback ----
        s3 = e.script("event_whenbroadcastreceived",
                      fields={"BROADCAST_OPTION": ["apu_update_%d" % ch, bc_update[ch]]})
        base_hz = BASE_NOISE_HZ if ch == CH_NOISE else BASE_HZ
        s3.stack("sound_seteffectto",
                 VALUE=_pitch_expr(e, e.IT("APU_FREQ", ch), base_hz),
                 fields={"EFFECT": ["PITCH"]})
        s3.stack("sound_setvolumeto", VOLUME=e.IT("APU_VOL", ch))
        s3.finalize()

        # ---- restart hat: swap which asset is playing (duty/noise period) ----
        s4 = e.script("event_whenbroadcastreceived",
                      fields={"BROADCAST_OPTION": ["apu_restart_%d" % ch, bc_restart[ch]]})
        # Both the template sprite and its clones receive this. The clone
        # kills itself; the template spawns a fresh one -- net effect is a
        # clean retrigger with the newly-selected asset.
        ctx = e.IFELSE(s4, e.EQ(e.V("is_clone"), 1))
        with ctx as b:
            b.stack("control_delete_this_clone")
        with ctx.substack2() as b:
            b.stack("control_create_clone_of",
                    CLONE_OPTION=Reporter(
                        e._op("control_create_clone_of_menu",
                              fields={"CLONE_OPTION": ["_myself_"]}).block_id))
        s4.finalize()

        # ---- stop-all hat ----
        s5 = e.script("event_whenbroadcastreceived",
                      fields={"BROADCAST_OPTION": ["apu_stop_all", bc_stop]})
        s5.stack("sound_stopallsounds")
        s5.finalize()

    return {"update": bc_update, "restart": bc_restart, "stop": bc_stop}


def build_apu_demo(out_path=r"D:\KittyNES\progress\apu_full_demo.sb3"):
    """All four channel sprites in one project, wired but driven by nothing --
    proves the structure builds and validates before it's hooked to the CPU."""
    e = Emu("Driver")
    info = build_apu(e.proj)
    e.save(out_path)
    print("saved", out_path, "broadcasts:", sorted(info["update"]))
    return out_path


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
