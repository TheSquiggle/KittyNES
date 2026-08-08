"""Pick a .nes file via a file-select dialog, then build KittyNES with it baked in.

Just double-click this (or `python build_from_rom.py`) -- no command-line
arguments needed. It opens a native file picker, then calls
code/build_final.py's build logic with whatever ROM you chose, saving the
result to progress/nes_emulator.sb3 (or wherever you choose in the optional
save dialog).
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

ROOT = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = os.path.join(ROOT, "code")
DEFAULT_OUT = os.path.join(ROOT, "progress", "nes_emulator.sb3")

sys.path.insert(0, CODE_DIR)


def main():
    root = tk.Tk()
    root.withdraw()

    nes_path = filedialog.askopenfilename(
        title="Select a .nes ROM to bake into KittyNES",
        filetypes=[("NES ROM", "*.nes"), ("All files", "*.*")],
    )
    if not nes_path:
        print("No ROM selected, aborting.")
        return

    out_path = filedialog.asksaveasfilename(
        title="Save built .sb3 as...",
        initialdir=os.path.join(ROOT, "progress"),
        initialfile=os.path.splitext(os.path.basename(nes_path))[0] + ".sb3",
        defaultextension=".sb3",
        filetypes=[("Scratch project", "*.sb3")],
    )
    if not out_path:
        out_path = DEFAULT_OUT
        print(f"No save location chosen, defaulting to {out_path}")

    from lib import Emu
    import build_core as BC
    import ines_loader as INES

    print(f"Building with ROM: {nes_path}")
    e = Emu("NES")
    BC.declare_state(e)
    BC.phase1_tables(e)
    BC.phase2_bus(e)
    BC.phase3_cpu(e)
    BC.phase6_ppu_bg(e)
    BC.phase6b_sprites(e)
    BC.phase8_main_loop(e)

    with open(nes_path, "rb") as f:
        nes_bytes = f.read()
    INES.load_rom_into_emu(e, nes_bytes)

    print("total blocks:", len(e.t.blocks))
    e.save(out_path)
    print("saved", out_path)

    try:
        messagebox.showinfo("KittyNES build complete", f"Saved:\n{out_path}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
