# PPU implementation notes (KittyNES)

## Register plumbing (Phase 2, already done)

`build_core.py`'s `ppu_regs(e)` implements the CPU-visible $2000-$2007
register window (`ppu_reg_read %s` / `ppu_reg_write %s %s`, dispatched from
`bus_read`/`bus_write` for $2000-$3FFF, mirrored every 8 bytes):

- `$2000` PPUCTRL -> `P_CTRL`, also updates the nametable-select bits of the
  internal `P_T` ("t") loopy register.
- `$2001` PPUMASK -> `P_MASK` (rendering-enable/greyscale/emphasis bits;
  not yet consulted by the renderer -- background rendering always runs
  regardless of the mask bits for now, see "Not yet implemented" below).
- `$2002` PPUSTATUS -> read returns `P_STATUS` then clears bit 7 (vblank)
  and the write-latch `P_W`.
- `$2003`/`$2004` OAMADDR/OAMDATA -> direct `OAM` list read/write.
- `$2005` PPUSCROLL -> the loopy `t`/`x`/`w` two-write protocol (fine-X on
  first write, fine-Y + coarse-Y on second).
- `$2006` PPUADDR -> the loopy `t`/`v`/`w` two-write protocol (high byte
  masked to 6 bits first write, low byte + `v=t` second write).
- `$2007` PPUDATA -> `ppu_read`/`ppu_write` through `P_V`, with the correct
  buffered-read semantics (reads below $3F00 return the *previous* buffered
  value and prime the buffer with the newly-fetched byte; palette reads
  $3F00+ return the fresh value immediately while still priming the buffer
  from the mirrored nametable byte underneath -- this quirk is real 2C02
  behavior, not a bug), and honors the PPUCTRL bit-2 address-increment-by-32
  vs by-1 setting via `ppu_incaddr`.
- `$4014` OAM DMA -> handled in `bus_write` directly (256-byte copy from
  `$XX00-$XXFF` into `OAM` starting at `P_OAMADDR`, `CYCLES += 513`).

`ppu_read`/`ppu_write` (the PPU-address-space, not CPU-register, functions)
dispatch on the 14-bit PPU address: `< $2000` -> `chr_read`/`chr_write`
(pattern tables, mapper-banked); `$2000-$3EFF` -> `nt_index` + `VRAM`
(nametables, with horizontal/vertical/single-screen-A/B/four-screen mirroring
already implemented in `nt_index`); `$3F00+` -> `PAL` (palette RAM, with the
$3F10/$3F14/$3F18/$3F1C -> $3F00/$3F04/$3F08/$3F0C sprite-palette mirroring
already implemented).

## Phase 6a: background rendering (pattern table -> framebuffer)

New in `build_core.py`'s `phase6_ppu_bg(e)`. Renders nametable 0 (no
scrolling yet -- see "Not yet implemented") into the `FB` list: 256x240,
1-indexed, pixel `(x,y) = FB[y*256+x+1]`, storing the **resolved NES palette
index (0-63)**, i.e. what you'd look up in `PALRGB` to get an actual RGB
color, not a raw 2-bit tile color index.

### Pattern-table tile decode

NES tiles are 8x8, 2 bits per pixel, stored as two separate 8-byte bitplanes
(plane 0 = low bit, plane 1 = high bit of the color index), 16 bytes/tile
total. Decoding "bit N of byte" without native bitwise ops uses the same
lookup-table philosophy as Phase 1's `T_AND`/`T_OR`/etc: a precomputed
`PIXBIT_T` list, 256*8 = 2048 entries, `PIXBIT_T[byte*8 + bitpos + 1] = bit
(7-bitpos) of byte` (bitpos 0 = the **leftmost** pixel of the row, matching
CHR-ROM's bit7-is-leftmost convention). A tile-row's 8 pixel color indices
are then `bit0 = PIXBIT_T[plane0*8+bitpos+1]`, `bit1 =
PIXBIT_T[plane1*8+bitpos+1]`, `color_index = bit0 + bit1*2` (0-3).

### Attribute-table palette selection

Each attribute-table byte covers a 4x4-tile (32x32px) block and packs four
2-bit palette-group selectors, one per 2x2-tile quadrant of that block:
bits 0-1 = top-left, 2-3 = top-right, 4-5 = bottom-left, 6-7 = bottom-right.
`bg_setup_tile` computes which quadrant `(col,row)` falls in
(`shift = ((row/2)%2)*4 + ((col/2)%2)*2`) and extracts the 2-bit group via
`(attr_byte / 2^shift) mod 4` -- since `shift` only ever takes the 4 values
`{0,2,4,6}`, `2^shift` is resolved with a 4-way `if/elif` (`{1,4,16,64}`)
rather than needing a general bit-shift table.

### Final palette index resolution

`color_index == 0` always means "background pixel" (transparent for
sprites, and for the background layer itself it just means "show the
universal background color") regardless of palette group -- this is real
2C02 behavior (palette RAM entries $3F04/$3F08/$3F0C etc. are per-group
background colors, but hardware always uses $3F00 when color_index is 0).
For `color_index != 0`, the palette RAM address is `pal_group*4 +
color_index` (0-15, the background half of the 32-entry `PAL` list), read
through `ppu_read` at `$3F00 + that offset` so it goes through the real
palette-mirroring logic already in `ppu_read`.

### Rendering procs

- `bg_update_patbase` — sets `BG_PATBASE` (0 or $1000) from PPUCTRL bit 4.
- `bg_setup_tile col row` — nametable + attribute lookups -> `BGTILE`,
  `BGPALSEL`.
- `bg_row_planes py` — fetches the two bitplane bytes for tile-row `py` of
  the current `BGTILE` -> `BGP0`, `BGP1`.
- `bg_pixel_val px` — resolves one pixel's final palette index -> `RESULT`.
- `render_bg_region row0 row1 col0 col1` — the real workhorse: nested
  row/col/py/px loops (using dedicated `RB_*` globals as loop counters,
  since procedure arguments are call-time-only values in this Emu model, not
  writable loop variables) calling the above per-pixel and writing into
  `FB`.
- `render_bg_frame` — `render_bg_region(0, 30, 0, 32)`, the full 256x240
  nametable-0 background.

### Pen flush

`flush_fb_to_pen` draws `FB` to the stage. Batched per-row into
same-color horizontal runs (`flush_fb_row`) rather than one pen stamp per
pixel: for each row, scan left-to-right accumulating a run while the color
doesn't change, and emit one `pen_setPenColorToColor` + pen-down +
`motion_gotoxy` (end of run) + pen-up per run boundary. For typical
tile-based NES content (large flat-color regions) this is a large
reduction in pen operations vs. 61,440 individual pixel stamps. Stage
coordinate mapping: `FB x(0..255) -> stage x(-128..127)`, `FB y(0..239) ->
stage y(119..-120)` (NES Y grows downward on screen, Scratch stage Y grows
upward, hence the flip).

### Verification

`code/test_ppu_bg.py`: hand-built CHR (a solid tile, a left/right vertical-
stripe tile, a transparent tile), nametable, and attribute-table setup,
checked against hand-computed expected palette indices for specific pixels
across 3 tiles spanning 2 different attribute quadrants (11 checks, all
pass) -- covers solid-color tiles, per-pixel color-index-1-vs-2 resolution
within one tile, the transparent/universal-background-color special case,
and that two different tiles sharing/not-sharing an attribute quadrant
correctly get different resolved palettes.

Also ran a full `render_bg_frame` (all 960 tiles / 61,440 pixels) through
`interp.py` as a stress/smoke test: completes in ~3.4s (4.86M interpreted
block-steps) with no errors, confirming the nested-loop structure actually
terminates and doesn't blow up in either infinite-loop or step-count terms
for the full-screen case (not just the small hand-picked region used for
pixel-level checks).

**Sanity-check test pattern** (not literally screenshotted -- `interp.py`
treats `pen_*` opcodes as no-ops since it's a headless block-graph walker,
not a real renderer; this describes the `FB` contents that `flush_fb_to_pen`
would draw): an 8x8-tile checkerboard (nametable tiles alternating between a
solid "color-index 3" tile and a fully-transparent tile, attribute table all
zeroed to palette group 0, palette RAM entry 0 = black / entry 3 = a blue)
renders to `FB` as the expected alternating pattern -- verified
programmatically: `FB(0,0)=blue, FB(8,0)=black, FB(0,8)=black, FB(8,8)=blue,
FB(16,0)=blue, FB(0,16)=blue`, i.e. classic checkerboard corners. Once
`flush_fb_to_pen` runs in a real Scratch/TurboWarp environment this would
draw as an 8x8-tile blue/black checkerboard covering the full 256x240 stage
area (mapped to the centered -128..127 / -120..119 pen-coordinate window).

## Phase 6b: sprites (OAM) + scrolling

New in `phase6b_sprites(e)`.

### Sprite evaluation and rendering

`sprite_eval_line sl` scans all 64 OAM entries (4 bytes each: Y, tile,
attribute, X) and picks up to 8 whose vertical range covers scanline `sl`
(`sl - Y - 1` in `[0, height)`, height 8 or 16 from PPUCTRL bit 5 — the
real hardware's "Y byte is one less than the sprite's first visible
scanline" quirk). The 9th qualifying sprite on a line sets the PPUSTATUS
overflow bit (bit 5) via `BOR`; real hardware has a well-known *buggy*
overflow-evaluation algorithm (it doesn't actually scan cleanly starting from
OAM entry 0 after the first 8 -- see nesdev wiki) which we do NOT replicate,
since almost nothing depends on the buggy specifics and a always-correct
overflow flag is a reasonable/common emulator simplification.

For 8x16 sprites, `spr_fetch_planes` implements the real addressing: pattern
table bank = tile bit 0, tile number = tile with bit 0 cleared, and the tile
is treated as two stacked 8x8 half-tiles (`tile_num` for rows 0-7,
`tile_num+1` for rows 8-15) — vertical flip (attribute bit 7) is applied
*before* this split (flipping `row` across the full 0-15 range first), which
correctly swaps which half-tile is on top when flipped, matching real
hardware.

`composite_pixel sl x` composes one final `FB` pixel: scans the (already
`sprite_eval_line`-populated) up-to-8 sprites in OAM-index priority order
(lower OAM index wins ties, matching real hardware), finds the first opaque
sprite pixel covering `x` (with horizontal flip via `attr bit 6` handled by
mirroring the sub-tile pixel offset before the `PIXBIT_T` lookup), and
decides sprite-vs-background: sprite wins if its priority bit (attribute
bit 5) is 0 (front) OR the background pixel at `(x,sl)` is transparent
(`BGOP == 0`); otherwise the background (already in `FB`) is left alone.
Sprite-0-hit (PPUSTATUS bit 6) is checked independently of the display
decision -- it fires whenever OAM sprite 0 specifically has an opaque pixel
at `(x,sl)` *and* the background is opaque there too, `x != 255` (the real
hardware quirk that suppresses hit detection at the last pixel column), and
is a **separate** condition from which sprite wins the priority contest
(exactly matching real 2C02 behavior: sprite-0-hit can fire even on a frame
where sprite 0 is itself hidden behind an opaque background pixel).

`render_sprites_line`/`render_sprites_frame` are the driver loops (call
`render_bg_frame`/`render_bg_region` FIRST, since compositing reads the
already-rendered `FB`/`BGOP`).

### Loopy v/t/x/w scroll registers

The PPUSCROLL/PPUADDR write-twice-with-shared-latch (`P_W`) protocol was
already implemented in Phase 2's `ppu_reg_write`. New in Phase 6b: the
actual *use* of `P_V`/`P_T` during rendering.

`P_V`/`P_T` can hold up to 15 bits (`fine_Y(3) | NT_Y(1) | NT_X(1) |
coarse_Y(5) | coarse_X(5)`), which is out of range for the 8-bit-operand
`BAND`/`BOR` lookup tables (`T_AND`/`T_OR` are indexed `a*256+b` for
`a,b` in `0..255`) -- so all the scroll-register bit manipulation uses the
same "subtract the old field, add the new field" arithmetic pattern Phase
2's PPUCTRL/PPUSCROLL/PPUADDR write handlers already established, not
bitwise tables:

- `ppu_copy_horiz_v` / `ppu_copy_vert_v` — copy the horizontal
  (coarse-X + NT-X-bit) or vertical (coarse-Y + NT-Y-bit + fine-Y) fields
  from `P_T` into `P_V`, matching the real PPU's dot-257 (horizontal) and
  pre-render-line dot-280-304 (vertical) copy timing points.
- `ppu_scanline_inc_coarse_x` — increments coarse X, wrapping 31->0 with an
  NT-X-bit flip (real hardware's per-tile-fetch increment, called here once
  per rendered tile).
- `ppu_scanline_inc_y` — increments fine Y, and on fine-Y wrap (7->0)
  increments coarse Y with the well-known special cases: coarse Y 29 wraps
  to 0 *and* flips the NT-Y bit (this is the actual nametable-below
  boundary); coarse Y 31 (only reachable if software wrote an out-of-range
  value directly) wraps to 0 with *no* NT-Y flip (matches the real hardware
  quirk); otherwise coarse Y just increments.
- `bg_setup_tile_v` — like Phase 6a's `bg_setup_tile`, but derives the
  nametable-tile and attribute-table addresses from the current `P_V`
  (`tile_addr = 0x2000 | (v & 0xFFF)`, `attr_addr = 0x23C0 | (v & 0xC00) |
  ((coarseY/4)*8) | (coarseX/4)`) instead of explicit nametable-0 literals
  -- this is what makes rendering nametable-select- and scroll-aware.
- `render_bg_line_scrolled sl` / `render_bg_frame_scrolled` — the
  scroll-aware renderer: horizontal-copy once per scanline, coarse-X
  increment once per tile (32 tiles/scanline), Y-increment once per
  scanline, vertical-copy once per frame (from the caller's `P_T`, which is
  expected to already reflect whatever PPUSCROLL/PPUADDR writes happened).
  Because the horizontal copy and per-scanline rendering are two separate
  proc calls per scanline (not fused into one big batch), a future Phase 8
  main loop that mutates `P_T` between scanline calls (e.g. a raster
  split-scroll effect) will have that reflected correctly -- the mechanism
  is real, only the per-cycle CPU/PPU interleaving that would *drive* such
  mid-frame writes is Phase 8 territory.

**Scope note:** this implements *coarse* (8-pixel-granularity) scrolling
via tile-address selection. Fine-X sub-tile pixel blending (shifting the
visible window by `P_X` pixels within/across tile boundaries, which real
hardware does via 16-bit background shift registers) is NOT yet
implemented -- `render_bg_line_scrolled` reads `SC_FINEY` (from `P_V`) for
the vertical fine offset but does not yet apply `P_X` to shift pixels
horizontally across tile boundaries. Flagged as a follow-up.

### Verification

`code/test_ppu_sprites.py` (22 checks, all pass): sprite compositing over a
transparent background, the priority bit (both "hidden behind opaque bg"
and "still visible over transparent bg" cases), sprite-0-hit (both the
positive case and the "no hit when bg transparent" negative case), the
8-sprites-per-line overflow flag (both a 9-sprite line setting it and an
exactly-8-sprite line NOT setting it), and scroll-register increment/copy
behavior (coarse-X increment + wrap + NT-bit flip in both directions,
fine-Y increment, the coarse-Y-29-flips-NT-bit vs. coarse-Y-31-no-flip
special cases, and that `ppu_copy_horiz_v`/`ppu_copy_vert_v` each only
touch their own half of the register). Also spot-checked
`render_bg_line_scrolled` directly against a known stripe-tile pattern.

## Real-ROM sprite investigation (SMB+Duck Hunt, UNRESOLVED as of this writing)

A user reported sprites showing wrong tile graphics ("wrong items
displayed") against a real "Super Mario Bros. + Duck Hunt" build. Two
rounds of targeted, code-graph-level testing have NOT reproduced or
explained this:

- **Round 1** (positional/scrolling hypothesis, see PROGRESS_LOG.md): 25
  checks in `code/test_ppu_sprites2.py` covering OAM DMA (including
  destination-address wraparound), 8x16 sprite mode addressing (tile-bit0
  pattern-table select, tile-pair split, PPUCTRL bit3 correctly ignored in
  8x16 mode), and horizontal/vertical flip (independently and combined) —
  all passed clean. Implemented fine-X sub-tile scrolling as the best
  available hypothesis at the time (a real, separately-justified fix for a
  documented gap), but the user later clarified the symptom is wrong
  *tile content*, not misplacement, so this fix likely wasn't the answer.
- **Round 2** (tile-identity / CHR-bank-awareness hypothesis): 28 more
  checks in `code/test_sprite_chr_bank.py` — a spread of 16 distinctly-
  marked tiles across both pattern tables (confirming tile index N always
  pulls tile N's own data, no cross-contamination), and, more pointedly, a
  real `MAPPER=66` (GxROM, the exact mapper this ROM uses) CHR-bank-switch
  test performed via actual `bus_write` register writes (not just setting
  `CHRB0`/`CHRB1` directly), comparing the background fetch path
  (`ppu_read`) against the sprite fetch path (`spr_fetch_planes`) at the
  identical CHR address across all 4 bank selections. **Sprite and
  background fetch agree in every case** — `spr_fetch_planes` calls the
  exact same bank-aware `ppu_read`/`chr_read` chain background tile fetch
  uses; there is no separate or stale CHR addressing logic for sprites.

**As of this writing, the reported "wrong tile graphics" bug has not been
reproduced by any test.** This is reported honestly rather than assumed
fixed. See PROGRESS_LOG.md's most recent entries for the recommended next
diagnostic steps (a specific screenshot/frame from the user to construct a
matching synthetic reproduction, or a CPU-side rather than PPU-side
investigation, since sprite corruption downstream of a CPU bug that writes
wrong values into OAM/mapper registers hasn't been ruled out).

## Not yet implemented (deferred to a later Phase 6 sub-pass or Phase 8)

- **Fine-X pixel-level horizontal scroll** (see scope note above -- coarse
  8px-granularity scrolling works, sub-tile pixel shift doesn't yet).
- **PPUMASK bits** (background/sprite-rendering-enable, left-column-clip,
  greyscale, emphasis) are not consulted -- background and sprites always
  render.
- **Per-scanline cycle-accurate timing** (the current renderers are
  "render the whole frame/scanline at once" batch operations, not driven by
  actual CPU-cycle-interleaved PPU dot advancement; no vblank-timing
  interaction yet either) -- Phase 8 (main loop) territory.
- The real hardware's specific (buggy) sprite-overflow evaluation algorithm
  is not replicated -- see the sprite-evaluation section above.
