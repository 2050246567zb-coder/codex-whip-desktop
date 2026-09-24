import ctypes
import os

from PIL import Image
import pytest

from codex_whip.windows_alpha_overlay import WindowsAlphaTextOverlay, premultiplied_bgra
from test_interface import host


def test_premultiplied_bgra_keeps_soft_edges():
    image = Image.new('RGBA',(2,1),(200,100,50,128))
    image.putpixel((1,0),(8,16,24,0))
    pixels = premultiplied_bgra(image)
    assert pixels[:4] == bytes((25,50,100,128))
    assert pixels[4:] == bytes((0,0,0,0))


@pytest.mark.skipif(os.name != 'nt', reason='Win32 layered window')
def test_native_layer_accepts_soft_alpha_and_moves(host):
    layer = WindowsAlphaTextOverlay(host)
    try:
        layer.paint(Image.new('RGBA',(36,12),(29,29,31,128)),100,100)
        host.update()
        assert layer._visible
        assert ctypes.windll.user32.IsWindowVisible(layer.hwnd)
        assert layer._size == (36,12)
        layer.move(105,106)
        layer.hide()
        assert not layer._visible
    finally:
        layer.close()
