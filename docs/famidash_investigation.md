# Famidash blank-screen investigation — status

`test_roms/Famidash - Huge Man v1.2.8.nes` (mapper 4/MMC3, NES 2.0 header,
four-screen mirroring, 2MB PRG / 256KB CHR) still renders a blank/black
screen after 220 simulated frames (~3.7 game-seconds). This document records
what's been directly measured and ruled out, and what's still genuinely
unknown — no unverified claims either way.

## Confirmed correct (measured, not assumed)

- **MMC3 mapper**: PRG bank layout settles correctly (`P8=[254,58,0,255]`,
  matching PRG mode 1 with the last bank fixed at `$E000`), `R7` swaps live
  during execution, mirroring/PRG-RAM-protect/IRQ registers all implemented
  per the nesdev-verified spec.
- **MMC3 scanline IRQ**: fires and is serviced — the game's own IRQ handler
  at `$F8F5` (which does `STA $E000` to acknowledge) runs repeatedly per
  frame once rendering is enabled.
- **CPU cycle accounting**: directly measured (not modeled) via
  `code/diag_cycles.py` — every opcode's charged cycles matched the official
  6502 table exactly on both this ROM and a control ROM (AccuracyCoin), and
  total cycles/frame landed within ±5 of the exact NTSC target (29780.5).
  This corrects an earlier diagnosis that blamed a ~5000-cycle/frame gap —
  see `docs/cycle_accounting_audit.md`.
- **NMI timing**: `code/diag_regiondetect.py` traced real NMI dispatch
  cycle-by-cycle — gaps between successive NMIs measured 29778–29782
  cycles, i.e. exact NTSC frame timing, across 9 consecutive frames.
- **Four-screen VRAM/mirroring**: `VRAM` is correctly sized to 4096 entries,
  `nt_index`'s four-screen branch (`MIRROR==4`, the catch-all fallthrough)
  maps each logical nametable to its own physical 1KB page as designed.
  Verified this ROM's header does select four-screen (`MIRROR=4` observed
  at runtime) and that nametable writes DO land (4096 writes counted in the
  first 100 frames — exactly 4×1024 bytes, consistent with a VRAM-clear
  routine at boot, not a bug).
- **CPU is not stuck or crashed**: 12,651 distinct PC values hit across 100
  frames — real, varied execution, not a tight infinite loop.
- **CHR data loaded**: non-zero bytes present in the loaded CHR set (3618
  of the first 4096 checked).
- **Rendering does get enabled**: `PPUMASK` (`P_MASK`) shows background+
  sprites on (`P_MASK=6`) by around frame 87.

## Still unknown

Despite all of the above, all four nametable pages remain entirely zero
(no real tile indices, just the boot-time clear) through frame 220, and the
framebuffer stays a single solid color the whole time. Two explanations
remain live, and this investigation could not distinguish between them
within a practical amount of simulated time:

1. **This is normal** — a 2MB MMC3 game may simply have a longer
   boot/decompression/logo sequence than smaller ROMs like SMB (which
   needed ~40 frames) or AccuracyCoin (~90 frames) needed before drawing
   anything, and it just hasn't gotten there yet in 220 frames.
2. **There is a real bug further downstream** that prevents the game from
   ever reaching its actual tile-upload/level-draw code, which 220 frames
   of simulation wasn't enough to reach or reveal.

The Python-based verification harness (`interp.py`) runs at roughly
2–4 seconds of wall-clock time PER SIMULATED FRAME, making it impractical
to push much further this way — reaching even 10 real game-seconds would
take on the order of an hour. **Real Scratch/TurboWarp execution is orders
of magnitude faster** and is the practical way to actually resolve this:
load `progress/nes_emulator_famidash.sb3` (gitignored, not in the public
repo, since it bakes in a homebrew ROM) and observe directly whether it
eventually draws something, or hangs on a genuinely blank screen for much
longer than any reasonable boot sequence would take.

## Diagnostic tooling added this round (kept, reusable)

- `code/diag_regiondetect.py` — traces `RAM[$00]` changes and NMI/IRQ
  dispatch cycle-by-cycle; useful for any future timing-sensitive
  investigation, not specific to this ROM.
- Extended `code/dump_frames.py` runs and ad-hoc VRAM/OAM/CHR inspection
  scripts used interactively during this investigation (not checked in as
  standalone files, but the technique — hook `interp.py`'s `exec_block`
  to watch specific procs/addresses — is documented here for reuse).
