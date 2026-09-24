"""Per-pixel-alpha text surface above the Windows color-key whip overlay.

Tk's transparent-color window cannot blend partially transparent glyph edges.
Keep the whip on that window, but paint the small moving title as a separate
click-through layered window so Pillow's oversampled text remains smooth.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import tkinter as tk

from PIL import Image


class _Point(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


class _Size(ctypes.Structure):
    _fields_ = (("cx", wintypes.LONG), ("cy", wintypes.LONG))


class _BlendFunction(ctypes.Structure):
    _fields_ = (("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE), ("AlphaFormat", wintypes.BYTE))


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = (("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD))


class _RgbQuad(ctypes.Structure):
    _fields_ = (("rgbBlue", wintypes.BYTE), ("rgbGreen", wintypes.BYTE),
                ("rgbRed", wintypes.BYTE), ("rgbReserved", wintypes.BYTE))


class _BitmapInfo(ctypes.Structure):
    _fields_ = (("bmiHeader", _BitmapInfoHeader), ("bmiColors", _RgbQuad * 1))


def premultiplied_bgra(image: Image.Image) -> bytes:
    """Return the 32-bit top-down DIB bytes required by UpdateLayeredWindow."""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = Image.composite(rgba.convert("RGB"), Image.new("RGB", rgba.size), alpha)
    red, green, blue = rgb.split()
    return Image.merge("RGBA", (blue, green, red, alpha)).tobytes()


class WindowsAlphaTextOverlay:
    _GWL_EXSTYLE = -20
    _WS_EX_TRANSPARENT = 0x20
    _WS_EX_TOOLWINDOW = 0x80
    _WS_EX_LAYERED = 0x80000
    _WS_EX_NOACTIVATE = 0x08000000
    _SW_HIDE = 0
    _SW_SHOWNOACTIVATE = 4
    _SWP_NOSIZE = 0x0001
    _SWP_NOACTIVATE = 0x0010
    _ULW_ALPHA = 0x00000002

    def __init__(self, parent: tk.Misc, z_anchor=None) -> None:
        if os.name != "nt":
            raise OSError("Windows layered text is unavailable on this platform")
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg="#000000")
        self.window.update_idletasks()
        self._user32 = ctypes.windll.user32
        self._gdi32 = ctypes.windll.gdi32
        self._configure_api()
        self.hwnd = self._user32.GetAncestor(self.window.winfo_id(), 2)
        styles = self._user32.GetWindowLongPtrW(self.hwnd, self._GWL_EXSTYLE)
        styles |= (self._WS_EX_TRANSPARENT | self._WS_EX_TOOLWINDOW |
                   self._WS_EX_LAYERED | self._WS_EX_NOACTIVATE)
        self._user32.SetWindowLongPtrW(self.hwnd, self._GWL_EXSTYLE, styles)
        self._visible = False
        self._size = (0, 0)
        self._z_anchor = z_anchor or (lambda: -1)

    def _configure_api(self) -> None:
        user32, gdi32 = self._user32, self._gdi32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetDC.argtypes = (wintypes.HWND,)
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
        user32.ReleaseDC.restype = ctypes.c_int
        user32.UpdateLayeredWindow.argtypes = (
            wintypes.HWND, wintypes.HDC, ctypes.POINTER(_Point), ctypes.POINTER(_Size),
            wintypes.HDC, ctypes.POINTER(_Point), wintypes.COLORREF,
            ctypes.POINTER(_BlendFunction), wintypes.DWORD)
        user32.UpdateLayeredWindow.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.ShowWindow.restype = wintypes.BOOL
        gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = (wintypes.HDC,)
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.CreateDIBSection.argtypes = (
            wintypes.HDC, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
        gdi32.CreateDIBSection.restype = wintypes.HANDLE
        gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HANDLE)
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = (wintypes.HANDLE,)
        gdi32.DeleteObject.restype = wintypes.BOOL

    def paint(self, image: Image.Image, left: int, top: int) -> None:
        width, height = image.size
        if width <= 0 or height <= 0:
            self.hide()
            return
        pixels = premultiplied_bgra(image)
        screen_dc = self._user32.GetDC(None)
        if not screen_dc:
            raise ctypes.WinError()
        memory_dc = None
        bitmap = None
        previous = None
        try:
            memory_dc = self._gdi32.CreateCompatibleDC(screen_dc)
            if not memory_dc:
                raise ctypes.WinError()
            info = _BitmapInfo()
            info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            bits = ctypes.c_void_p()
            bitmap = self._gdi32.CreateDIBSection(
                screen_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bitmap or not bits.value:
                raise ctypes.WinError()
            previous = self._gdi32.SelectObject(memory_dc, bitmap)
            ctypes.memmove(bits, pixels, len(pixels))
            position = _Point(round(left), round(top))
            size = _Size(width, height)
            source = _Point(0, 0)
            blend = _BlendFunction(0, 0, 255, 1)
            if not self._user32.UpdateLayeredWindow(
                    self.hwnd, screen_dc, ctypes.byref(position), ctypes.byref(size),
                    memory_dc, ctypes.byref(source), 0, ctypes.byref(blend), self._ULW_ALPHA):
                raise ctypes.WinError()
            self._size = (width, height)
            self._show(left, top)
        finally:
            if previous and memory_dc:
                self._gdi32.SelectObject(memory_dc, previous)
            if bitmap:
                self._gdi32.DeleteObject(bitmap)
            if memory_dc:
                self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(None, screen_dc)

    def _show(self, left: int, top: int) -> None:
        if not self._visible:
            self._user32.ShowWindow(self.hwnd, self._SW_SHOWNOACTIVATE)
            self._visible = True
        width, height = self._size
        if not self._user32.SetWindowPos(self.hwnd, self._z_anchor(), round(left), round(top),
                                         width, height, self._SWP_NOACTIVATE):
            raise ctypes.WinError()

    def move(self, left: int, top: int) -> None:
        if self._size == (0, 0):
            return
        self._show(left, top)

    def hide(self) -> None:
        if self._visible:
            self._user32.ShowWindow(self.hwnd, self._SW_HIDE)
            self._visible = False

    def close(self) -> None:
        self.hide()
        self.window.destroy()
