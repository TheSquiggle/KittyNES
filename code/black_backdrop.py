"""Sets the Stage's backdrop to solid black.

Why this is the right (and only) fix available from inside the .sb3: the
player's viewport scaling -- stretching the 480x360 canvas to fill the
window while preserving aspect ratio -- is something Scratch/TurboWarp's
PLAYER already does automatically; a .sb3 has no "fullscreen/stretch" flag of
its own to set (that's an embed/player-level setting, not project data). What
IS ours to control is what fills that canvas: a black backdrop makes the
area the 256x240 NES framebuffer doesn't cover (the Stage is 480x360, wider
than the 256px-wide NES image once scaled) render as black letterboxing
instead of Scratch's default white, which is what actually reads as "the
screen is black behind my image."
"""
import hashlib
import sys

sys.path.insert(0, r"D:\KittyNES\code")

BLACK_BACKDROP_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360" '
    b'viewBox="0 0 480 360"><rect width="480" height="360" fill="#000000"/></svg>'
)


def set_black_backdrop(proj):
    """proj: an sb3_builder Project. Replaces the Stage's (transparent
    placeholder) backdrop with an opaque black one, sized to the standard
    Scratch stage (480x360) so it fully covers the canvas at any scale."""
    md5 = hashlib.md5(BLACK_BACKDROP_SVG).hexdigest()
    fname = "%s.svg" % md5
    proj.register_asset_bytes(fname, BLACK_BACKDROP_SVG)
    proj.stage.costumes = [{
        "assetId": md5,
        "name": "black",
        "md5ext": fname,
        "dataFormat": "svg",
        "rotationCenterX": 240,
        "rotationCenterY": 180,
    }]


if __name__ == "__main__":
    # Smoke test: apply to a fresh project and confirm it validates.
    from lib import Emu
    e = Emu("Test")
    set_black_backdrop(e.proj)
    out = r"D:\KittyNES\progress\black_backdrop_test.sb3"
    e.save(out)
    print("saved", out)
