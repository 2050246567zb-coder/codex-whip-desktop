from pathlib import Path

from PIL import Image

from codex_whip.interface import asset_path


def test_approved_windows_logo_has_transparency_and_taskbar_sizes():
    png = asset_path("codex-whip.png", group="icon")
    ico = asset_path("codex-whip.ico", group="icon")
    svg = asset_path("codex-whip.svg", group="icon")
    assert Path(svg).is_file()
    with Image.open(png) as image:
        assert image.size == (1024, 1024)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((42 * 4, 132 * 4))[3] == 255
    with Image.open(ico) as image:
        assert {(16, 16), (32, 32), (256, 256)} <= image.ico.sizes()
