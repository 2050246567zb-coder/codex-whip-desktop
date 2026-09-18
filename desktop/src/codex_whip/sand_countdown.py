"""A right-to-left, real-time voice expiry indicator; never owns send state."""
import math
import time
from PIL import Image, ImageDraw, ImageTk
from .morphing_title import MorphingTitle


def sand_surface(mask, progress, background, *, particles=True):
    progress = max(0., min(1., progress))
    surface = Image.new('RGB', mask.size, background)
    surface.paste('#B6B6BA', (0,0,*mask.size), mask)
    bounds = mask.getbbox()
    if bounds is None:
        return surface
    left, top, right, bottom = bounds
    edge = right-(right-left)*progress
    dark = mask.copy()
    ImageDraw.Draw(dark).rectangle((max(0,round(edge)),0,mask.width,mask.height),fill=0)
    surface.paste('#1D1D1F', (0,0,*mask.size), dark)
    if particles and 0 < progress < 1:
        draw = ImageDraw.Draw(surface)
        bg = Image.new('RGB',(1,1),background).getpixel((0,0))
        # Each sampled ink grain is released once as the time boundary passes.
        for x in range(left,right,4):
            age = progress*10-(right-x)/max(1,right-left)*10
            if not 0 <= age < .7:
                continue
            for y in range(top,bottom,4):
                if mask.getpixel((x,y)) < 128:
                    continue
                seed = (x*13+y*7)%17
                px = x + math.sin(seed)*age*18
                py = y + age*12 + age*age*65
                opacity = (1-age/.7)*.7
                color = tuple(round(v*(1-opacity)+55*opacity) for v in bg)
                draw.ellipse((px,py,px+2,py+2), fill=color)
    return surface


class SandCountdownTitle(MorphingTitle):
    def __init__(self,*args,**kwargs):
        self._deadline = None
        self._sand_timer = None
        self._fade_gray = False
        super().__init__(*args,**kwargs)

    def set_countdown(self, deadline):
        if deadline == self._deadline:
            return
        self._fade_gray = deadline is None and self._deadline is not None and time.monotonic() >= self._deadline
        self._deadline = deadline
        if self._sand_timer is not None:
            self.after_cancel(self._sand_timer)
            self._sand_timer = None
        if deadline is not None:
            self._sand_tick()

    def _show(self, mask):
        if self._deadline is None and not self._fade_gray:
            return super()._show(mask)
        self._mask = mask
        surface = sand_surface(mask, 1 if self._deadline is None else 1-(self._deadline-time.monotonic())/10,
                               self.cget('bg'),particles=not self._reduce_motion())
        self._photo = ImageTk.PhotoImage(surface.resize(self._output_size,Image.Resampling.LANCZOS),master=self)
        # Avoid triggering another text transition.
        super().configure(image=self._photo)

    def _sand_tick(self):
        self._sand_timer = None
        self._show(self._mask)
        if self._deadline is not None and time.monotonic() < self._deadline:
            self._sand_timer = self.after(100 if self._reduce_motion() else 33,self._sand_tick)

    def destroy(self):
        if self._sand_timer is not None:
            self.after_cancel(self._sand_timer)
            self._sand_timer = None
        super().destroy()
