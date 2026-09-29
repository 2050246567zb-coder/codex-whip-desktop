"""Render the approved whip mark for preview and Windows icon resources."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "design" / "logo-proposal-v2-preview.png"
ASSETS = ROOT / "desktop" / "assets" / "icon"
SCALE = 4
BLACK = (25, 25, 27, 255)


def cubic(start, control_one, control_two, end, steps=64):
    return [
        tuple(SCALE * (
            (1 - t) ** 3 * start[axis]
            + 3 * (1 - t) ** 2 * t * control_one[axis]
            + 3 * (1 - t) * t ** 2 * control_two[axis]
            + t ** 3 * end[axis]
        ) for axis in (0, 1))
        for t in (index / steps for index in range(steps + 1))
    ]


def render():
    image = Image.new("RGBA", (256 * SCALE, 256 * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    upper = (
        cubic((60, 151), (75, 176), (91, 181), (110, 170))
        + cubic((110, 170), (143, 148), (181, 115), (222, 82))[1:]
    )
    lower = (
        cubic((222, 82), (184, 118), (147, 155), (114, 177))
        + cubic((114, 177), (93, 189), (74, 182), (59, 161))[1:]
    )
    draw.polygon(upper + lower, fill=BLACK)
    points = [(42 * SCALE, 132 * SCALE), (62 * SCALE, 156 * SCALE)]
    width = 14 * SCALE
    radius = width / 2
    draw.line(points, fill=BLACK, width=width)
    for x, y in points:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=BLACK)
    preview = Image.new("RGBA", image.size, "white")
    preview.alpha_composite(image)
    preview.resize((512, 512), Image.Resampling.LANCZOS).convert("RGB").save(
        OUTPUT, optimize=True)
    image.save(ASSETS / "codex-whip.png", optimize=True)
    image.save(ASSETS / "codex-whip.ico", format="ICO",
               sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])


if __name__ == "__main__":
    render()
