"""Phase 9 groundwork: generate the APU waveform assets as 16-bit mono WAVs.

Architecture (see research/audio_reference_findings.md): each channel plays ONE
long, seamlessly-looping waveform sample and gets its frequency from Scratch's
PITCH effect, rather than re-triggering a short click per wave cycle. That
sidesteps the per-play audio-engine startup latency that capped our earlier
click-train prototypes.

Pitch at runtime:      pitch = 120 * log2(target_hz / BASE_HZ)
(Scratch pitch is 10 units/semitone -> 120 units/octave.)

Seamless looping is the one hard requirement here: the asset is played in a
`forever: play until done` loop, so any discontinuity at the seam becomes an
audible click at the loop rate. We therefore choose an INTEGER number of
samples per wave cycle and an INTEGER number of cycles, so the last sample
joins the first cleanly.

Run:  python audio_assets.py [outdir]
"""
import math
import os
import struct
import sys

SR = 48000                     # sample rate for the tonal (pitch-shifted) channels
SAMPLES_PER_CYCLE = 109        # integer -> seam-free loop
BASE_HZ = SR / SAMPLES_PER_CYCLE   # 440.3669... Hz
TONE_CYCLES = 2202             # ~5.0 s  (2202 * 109 = 240018 samples)
TRI_CYCLES = 4404              # ~10.0 s

# NTSC noise period table (NESdev): timer values for the 16 noise settings.
NOISE_PERIODS = [4, 8, 16, 32, 64, 96, 128, 160,
                 202, 254, 380, 508, 762, 1016, 2034, 4068]
CPU_HZ = 1789773.0
LFSR_PERIOD = 32767            # 15-bit maximal-length sequence


def write_wav(path, samples, rate):
    """16-bit signed mono PCM."""
    data = b"".join(struct.pack("<h", max(-32768, min(32767, int(s)))) for s in samples)
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    hdr += b"data" + struct.pack("<I", len(data))
    with open(path, "wb") as f:
        f.write(hdr + data)
    return len(data) + 44


def pulse(duty_frac, cycles, amp=0.25):
    """Square wave with the given duty cycle, exact-integer period."""
    hi = int(round(SAMPLES_PER_CYCLE * duty_frac))
    one = [amp * 32767 if i < hi else -amp * 32767 for i in range(SAMPLES_PER_CYCLE)]
    return one * cycles


def triangle(cycles, amp=0.30):
    """NES triangle is a 32-step staircase, not a smooth ramp -- reproduce the
    staircase so the timbre matches the real chip rather than a pure triangle."""
    one = []
    for i in range(SAMPLES_PER_CYCLE):
        phase = i / SAMPLES_PER_CYCLE
        step = int(phase * 32) % 32           # 0..31
        val = step if step < 16 else 31 - step  # 0..15..0
        one.append((val / 15.0 * 2.0 - 1.0) * amp * 32767)
    return one * cycles


MIN_ASSET_SECS = 4.0   # keep `play until done` re-triggers rare (see module docstring)


def lfsr_cycle_len(mode):
    """Measure the LFSR's TRUE repeat length for this mode.

    Mode 0 (bit1 feedback) is the maximal-length 15-bit sequence: 32767 steps.
    Mode 1 (bit6 feedback) is NOT -- it collapses to a 93-step cycle, which is
    why short-mode noise sounds tonal on real hardware. Assuming 32767 for both
    would make every mode-1 asset ~350x longer than it needs to be.
    """
    reg, n = 1, 0
    while True:
        bit = (reg & 1) ^ ((reg >> (6 if mode else 1)) & 1)
        reg = (reg >> 1) | (bit << 14)
        n += 1
        if reg == 1:
            return n


def noise(mode, period, amp=0.22):
    """Real 15-bit NES noise LFSR, resampled to a practical rate.

    SUPERSEDED for runtime use by noise_base() below -- kept only because the
    aliasing bug this docstring originally warned wasn't a problem turned out
    to BE the exact cause of a real reported bug ("the noise channel is too
    low pitched"). Explaining that for the record:

    This function renders EVERY period at a fixed-ish sample rate up to SR
    (48kHz). For short periods (fast noise, e.g. period=4) the LFSR's native
    toggle rate is CPU_HZ/4 ~= 447kHz -- vastly above 48kHz's Nyquist limit
    (24kHz). That's severe aliasing: high-frequency content folds DOWN into
    the audible range as spurious low-frequency content, which is exactly
    "too low pitched," not a rendering preference. Rendering 32 separate
    assets, most of them aliased, was the root design mistake -- not a bug in
    this specific averaging loop.

    Feedback = bit0 XOR bit1 (mode 0, 'long') or bit0 XOR bit6 (mode 1,
    'short'/tonal). Output is bit0 inverted.
    """
    native = CPU_HZ / period                       # LFSR steps per second
    rate = int(max(8000, min(SR, native * 2.2)))   # Nyquist headroom, capped
    cyclen = lfsr_cycle_len(mode)
    one_secs = cyclen / native
    reps = max(1, math.ceil(MIN_ASSET_SECS / one_secs))
    nsamples = int(one_secs * reps * rate)

    reg = 1
    out = []
    acc = 0.0
    step_per_sample = native / rate
    cur = 0
    for _ in range(nsamples):
        acc += step_per_sample
        while acc >= 1.0:
            acc -= 1.0
            bit = (reg & 1) ^ ((reg >> (6 if mode else 1)) & 1)
            reg = (reg >> 1) | (bit << 14)
            cur = (~reg) & 1
        out.append((amp * 32767) if cur else (-amp * 32767))
    return out, rate


# The period this project's noise channel is pitch-shifted RELATIVE TO (see
# apu_wire.py/apu_build.py). Its native LFSR toggle rate --
# CPU_HZ/BASE_NOISE_PERIOD ~= 7047 Hz -- sits safely below the 48kHz render
# rate's Nyquist limit, so the base asset is genuinely alias-free. Every
# other period is reached by pitch-shifting THIS clean asset (same technique
# as pulse/triangle), not by re-rendering at whatever rate that period's
# native frequency would need -- which is what caused the original bug.
BASE_NOISE_PERIOD = 254
BASE_NOISE_HZ = CPU_HZ / BASE_NOISE_PERIOD


def noise_base(mode, amp=0.22):
    """One alias-free asset per LFSR mode, rendered at BASE_NOISE_PERIOD.
    Loops on a whole number of full LFSR cycles, tiled to MIN_ASSET_SECS."""
    native = BASE_NOISE_HZ
    rate = SR
    cyclen = lfsr_cycle_len(mode)
    one_secs = cyclen / native
    reps = max(1, math.ceil(MIN_ASSET_SECS / one_secs))
    nsamples = int(one_secs * reps * rate)

    reg = 1
    out = []
    acc = 0.0
    step_per_sample = native / rate   # < 1.0 here since rate > native: OVERsampled, alias-free
    cur = 0
    for _ in range(nsamples):
        acc += step_per_sample
        while acc >= 1.0:
            acc -= 1.0
            bit = (reg & 1) ^ ((reg >> (6 if mode else 1)) & 1)
            reg = (reg >> 1) | (bit << 14)
            cur = (~reg) & 1
        out.append((amp * 32767) if cur else (-amp * 32767))
    return out, rate


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else r"D:\KittyNES\assets\audio"
    os.makedirs(outdir, exist_ok=True)
    total = 0
    manifest = []

    for i, duty in enumerate([0.125, 0.25, 0.50, 0.75]):
        n = write_wav(os.path.join(outdir, f"pulse{i}.wav"), pulse(duty, TONE_CYCLES), SR)
        total += n
        manifest.append((f"pulse{i}.wav", SR, TONE_CYCLES * SAMPLES_PER_CYCLE, n))

    n = write_wav(os.path.join(outdir, "triangle.wav"), triangle(TRI_CYCLES), SR)
    total += n
    manifest.append(("triangle.wav", SR, TRI_CYCLES * SAMPLES_PER_CYCLE, n))

    # Two CLEAN base assets (one per LFSR mode), alias-free by construction
    # since they're rendered at BASE_NOISE_PERIOD's native rate (~7047Hz),
    # safely below the 48kHz Nyquist limit. Every other period is reached at
    # runtime by pitch-shifting these, not by rendering 32 separate assets --
    # see noise_base()'s docstring for why the old per-period approach caused
    # a real reported bug ("noise channel is too low pitched": severe
    # aliasing at short periods folding high frequencies down into audible
    # low ones).
    for mode, name in ((0, "noiseA"), (1, "noiseB")):
        samples, rate = noise_base(mode)
        n = write_wav(os.path.join(outdir, name + ".wav"), samples, rate)
        total += n
        manifest.append((name + ".wav", rate, len(samples), n))

    print(f"BASE_HZ = {BASE_HZ!r}  (pitch = 120*log2(hz/BASE_HZ))")
    print(f"BASE_NOISE_HZ = {BASE_NOISE_HZ!r}  (period={BASE_NOISE_PERIOD}, "
          f"noise pitch = 120*log2(APU_FREQ/BASE_NOISE_HZ))")
    print(f"{len(manifest)} assets, {total/1048576:.2f} MB total\n")
    for name, rate, ns, nbytes in manifest:
        print(f"  {name:22} rate={rate:5}  samples={ns:8}  {ns/rate:7.3f}s  {nbytes/1024:8.1f} KB")
    print(f"\nwrote -> {outdir}")


if __name__ == "__main__":
    main()
