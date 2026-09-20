"""Render the actual stroke/rope implementation without any desktop input."""
from pathlib import Path
import math
from PIL import Image, ImageDraw
from codex_whip.effects import CartoonWhipPhysics, ManualWhipStroke, catmull_rom_points

destination = Path('outputs/motion-v2.1.1')
destination.mkdir(parents=True, exist_ok=True)
click = (260, 300)
physics = CartoonWhipPhysics(click, scale=0.58)
stroke = ManualWhipStroke(physics, click)


def render(pose, caption):
    canvas = Image.new('RGB', (600, 600), '#e9edf1')
    draw = ImageDraw.Draw(canvas)
    draw.text((20, 20), caption, fill='#243342')
    draw.line((click[0]-12, click[1], click[0]+12, click[1]), fill='#d54141', width=2)
    draw.line((click[0], click[1]-12, click[0], click[1]+12), fill='#d54141', width=2)
    smoothed = catmull_rom_points(pose.cord, 7)
    for i in range(len(smoothed)-1):
        draw.line((*smoothed[i], *smoothed[i+1]), fill='#101010', width=max(2, round(6-4*i/len(smoothed))))
    draw.line((*pose.handle_start, *pose.handle_end), fill='#000000', width=9)
    return canvas


sheet = Image.new('RGB', (1800, 1200), 'white')
for index, ms in enumerate((0, 65, 115, 155, 215, 280)):
    sheet.paste(render(stroke.sample(ms/1000, click), f'{ms} ms | red cross = click'),
                ((index%3)*600, (index//3)*600))
sheet.save(destination / 'mouse-stroke-contact-sheet.png')
frames = []
for ms in range(0, 281, 8):
    frames.append(render(stroke.sample(ms/1000, click), f'{ms} ms (4x slow review)'))
frames[19] = render(stroke.strike, '155 ms | TIP CONTACT (4x slow review)')
frames[0].save(destination/'mouse-stroke-slow.gif', save_all=True,
               append_images=frames[1:], duration=32, loop=0)

# Measured settling, not an assertion that the hand-held product feels right.
old_gravity = CartoonWhipPhysics((500, 100))
old_gravity.GRAVITY_PER_STEP = 0.48
old_gravity.INERTIAL_DAMPING = 0.84
new_gravity = CartoonWhipPhysics((500, 100))
for step in range(60):
    old = old_gravity.step((500, 100), kinematic=True)
    new = new_gravity.step((500, 100), kinematic=True)
    if step in (14, 29, 59):
        print(f'{(step+1)/60:.2f}s tip old={old.cord[-1]}, new={new.cord[-1]}')
print(destination.resolve())
