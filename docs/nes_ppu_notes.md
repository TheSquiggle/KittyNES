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

## Not yet implemented (deferred to a later Phase 6 sub-pass)

- **Scrolling** (loopy `v`/`t`/`x`/`w` registers already exist as globals and
  are correctly updated by the PPUSCROLL/PPUADDR register-write logic in
  Phase 2, but `render_bg_region`/`bg_setup_tile` don't read `P_V`/`P_X` yet
  -- rendering is currently always nametable 0, zero-scroll, coarse-tile-
  aligned only).
- **Multiple nametables** (only nametable 0's tile range is read; the
  `MIRROR` logic in `nt_index` is fully implemented and already used by
  `ppu_read`, so once scrolling reads across nametable boundaries the
  existing mirroring will "just work" -- this is a rendering-loop gap, not
  a missing-data gap).
- **PPUMASK bits** (background-rendering-enable, left-column-clip,
  greyscale, emphasis) are not consulted -- background always renders.
- **Sprites/OAM** (`OAM`/`SPRX`/`SPRLO`/`SPRHI`/`SPRAT`/`SPRID`/`SPRN` globals
  and lists already exist from Phase 2's `declare_state`, unused so far).
- **Per-scanline timing** (the real PPU renders one scanline of 8 pixels'
  worth of fetches interleaved with CPU cycles at a fixed 3:1 PPU:CPU clock
  ratio; the current renderer is a single "render the whole frame at once"
  batch operation with no cycle-accurate timing, no mid-frame register-write
  effects like split-scroll raster tricks, and no vblank-timing interaction
  -- that's Phase 8 (main loop) territory).
