# Cycle-accounting audit: correcting the Famidash diagnosis

A prior investigation into why `Famidash - Huge Man v1.2.8.nes` renders a
blank screen concluded the CPU was under-billing roughly 5000 cycles/frame
(observed ~2501 loop iterations in the game's region-detection routine vs.
an expected ~2950), and characterized this as a "CPU cycle-accounting
fidelity gap." This document re-measures that claim directly and finds it
does not hold up — **per-opcode and per-frame cycle accounting is correct.**

## Method

`code/diag_cycles.py` hooks `interp.py`'s block executor to record, for every
`cpu_step` call: the charged `CYCLES` value, which opcode it was, and running
per-frame totals. This measures the REAL generated block graph's actual
billing, not a model of what it should do.

## Result: AccuracyCoin (control — mapper 0, no MMC3 involved)

```
frame 1: instructions= 8515  cycles=29784.0  (NTSC target 29780.5, delta +3.5)
frame 2: instructions= 8525  cycles=29779.0  (delta -1.5)
frame 3: instructions= 9122  cycles=29781.0  (delta +0.5)
```

## Result: Famidash (the ROM in question)

```
frame 1: instructions= 8370  cycles=29785.0  (delta +4.5)
frame 2: instructions= 7669  cycles=29777.0  (delta -3.5)
frame 3: instructions= 9936  cycles=29782.0  (delta +1.5)
```

Both land within ±5 cycles of the exact NTSC target (262 × 341/3 = 29780.5)
— well inside normal frame-to-frame variance from where the fractional
scanline remainder happens to land. This is a direct measurement of total
billed cycles per `run_frame`, with no room for the ~5000-cycle-per-frame gap
the prior diagnosis described.

## Per-opcode audit (Famidash's actual hot instructions)

Every opcode's charged average was compared against the official 6502 base
cycle table:

```
$2C (BIT abs)      charged=4.00  table=4   OK
$10 (BPL rel)      charged=3.00  table=2   OK (branch-taken +1, expected)
$D0 (BNE rel)      charged=2.99  table=2   OK
$E8 (INX)          charged=2.00  table=2   OK
$8D (STA abs)      charged=4.00  table=4   OK
$9D (STA abs,X)    charged=5.00  table=5   OK
$95 (STA zp,X)     charged=4.00  table=4   OK
$CA (DEX)          charged=2.00  table=2   OK
$A9 (LDA imm)      charged=2.00  table=2   OK
$88 (DEY)          charged=2.00  table=2   OK
```

Zero mismatches. Every value the CPU actually bills matches the spec exactly,
including the branch-taken +1 cycle showing up correctly as an average near 3
for `$10`/`$D0` (mix of taken/not-taken across the sample).

## What this means

The CPU's cycle accounting — both the per-instruction table and the
per-frame scanline budget (`CYCLES_PER_SCANLINE = 341/3`) — is correct. The
prior diagnosis's ~2950-vs-2501-iterations discrepancy is real (it was
measured against actual PC hotspot data), but its cause is **not** a cycle
billing bug here. Two more likely explanations, neither chased down yet:

1. **The prior loop-cycle assumption was wrong.** The diagnosis assumed the
   loop at `$800C` costs ~10 cycles/iteration to derive its "~2950 expected"
   figure. If the loop's real per-iteration cost is measured directly
   (the same technique used here) rather than assumed, the expected iteration
   count would change — quite possibly enough to close the gap without any
   emulator fix at all.
2. **The loop's actual behavior differs from what was modeled** — e.g. if it
   isn't a pure fixed-cost loop (a conditional branch inside it takes a
   data-dependent path some iterations), instruction-level timing precision
   won't show up in a "cycles per frame" aggregate check like this one, only
   in a cycle-by-cycle trace of that exact loop.

## Honest scope of this audit

This confirms cycle accounting is NOT the bug, using real measurement rather
than re-asserting the original claim. It does **not** identify why Famidash's
region-detection loop lands on the wrong iteration count — that requires
tracing the exact loop at `$800C` (bank 254) with the same instrumentation
used here, narrowed to that specific address range and frame window, which is
follow-up work.
