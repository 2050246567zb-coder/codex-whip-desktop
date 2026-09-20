"""Render actual title masks at evenly spaced animation times for inspection."""
from pathlib import Path
import tkinter as tk
from PIL import Image, ImageDraw
from codex_whip.morphing_title import MorphingTitle, blend_masks

root = tk.Tk()
root.withdraw()
root.configure(bg='#F5F5F7')
title = MorphingTitle(root)
old = title._raster('just beat it')
new = title._raster("Don't waste time on AI")
sheet = Image.new('RGB',(880,9*130),'#F5F5F7')
for i in range(9):
    mask = blend_masks(old,new,i/8)
    sheet.paste('#1D1D1F',(0,i*130,880,i*130+104),mask)
    ImageDraw.Draw(sheet).text((8,i*130+105),f'{i/10:.1f} s',fill='black')
path = Path('outputs/desktop-2.2.18/text-fusion-frames.png')
path.parent.mkdir(parents=True,exist_ok=True)
sheet.save(path)
title.destroy()
root.destroy()
print(path.resolve())
