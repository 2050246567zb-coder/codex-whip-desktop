"""State-driven blur/alpha-threshold text morph, inspired by Magic UI.

Native Pillow/Tk implementation, not a React runtime or an automatic text loop.
"""
import time
import math
from pathlib import Path
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, ImageTk
from .motion_clock import ACTIVE_FRAME_MS, RenderClock


def blend_masks(old,new,progress):
    p = max(0.,min(1.,progress))
    if p == 0:
        return old.copy()
    if p == 1:
        return new.copy()
    # Magic UI's two reciprocal blurs, opacity^0.4, source-over alpha,
    # then feColorMatrix alpha = 255*alpha - 140. No extra timing ease or cooldown.
    # Scale blur to our 23pt font (reference base 40pt), on the 2x render surface.
    blur_unit = 8*(23/40)*2
    old_blank = old.getbbox() is None
    new_blank = new.getbbox() is None
    if old_blank and new_blank:
        return old.copy()
    outgoing = None if old_blank else old.filter(
        ImageFilter.GaussianBlur(min(100,blur_unit*p/(1-p))))
    incoming = None if new_blank else new.filter(
        ImageFilter.GaussianBlur(min(100,blur_unit*(1-p)/p)))
    if outgoing is not None:
        outgoing = outgoing.point([round(x*(1-p)**.4) for x in range(256)])
    if incoming is not None:
        incoming = incoming.point([round(x*p**.4) for x in range(256)])
    alpha = (ImageChops.screen(outgoing,incoming) if outgoing is not None and incoming is not None
             else outgoing if outgoing is not None else incoming)
    fused = alpha.point([max(0,min(255,(x-140)*255)) for x in range(256)])
    return fused.filter(ImageFilter.GaussianBlur(.6*2))


class MorphingTitle(tk.Label):
    def __init__(self,parent,reduce_motion=lambda:False,point_size=23,height=52,
                 duration=.8,raster_only=False):
        self._ready = False
        self._timer = None
        self._reduce_motion = reduce_motion
        self._duration = duration
        self._motion = None
        self._raster_only = raster_only
        self._raster_size = (880,height*2)
        self._output_size = (440,height)
        self._mask = Image.new('L',self._raster_size)
        self._old = self._new = self._mask
        self._photo = None
        super().__init__(parent,text='',bg=parent.cget('bg'),bd=0,padx=0,pady=0)
        candidates = ['C:/Windows/Fonts/msyhbd.ttc',
                      '/System/Library/Fonts/PingFang.ttc',
                      '/System/Library/Fonts/STHeiti Medium.ttc',
                      '/System/Library/Fonts/Supplemental/Songti.ttc',
                      '/System/Library/Fonts/Supplemental/Arial Bold.ttf']
        size = round(self.winfo_fpixels(f'{point_size}p')*2)
        path = next((p for p in candidates if Path(p).exists()),None)
        self._font = ImageFont.truetype(path,size) if path else ImageFont.load_default(size=size)
        self._ready = True
        self._show(self._mask)

    def _raster(self,text):
        mask = Image.new('L',self._raster_size)
        draw = ImageDraw.Draw(mask)
        font = self._font
        # Speech can be longer than a status label. Wrap by measured glyph width,
        # reduce size only as needed; retain the full source string in cget(text).
        def wrap(value, selected):
            rows, line = [], ''
            for char in value:
                if char == '\n' or (line and draw.textlength(line+char,font=selected)>840):
                    rows.append(line)
                    line = ''
                if char != '\n':
                    line += char
            return '\n'.join(rows+[line])
        rendered = wrap(text,font)
        box = draw.multiline_textbbox((0,0),rendered,font=font,spacing=4)
        while box[3]-box[1] > self._raster_size[1]-8 and font.size > 24:
            font = font.font_variant(size=max(24,font.size-2))
            rendered = wrap(text,font)
            box = draw.multiline_textbbox((0,0),rendered,font=font,spacing=4)
        if box[3]-box[1] > self._raster_size[1]-8:
            rows = rendered.split('\n')
            while len(rows)>1 and draw.multiline_textbbox((0,0),'\n'.join(rows),font=font,spacing=4)[3] > self._raster_size[1]-8:
                rows.pop()
            rows[-1] = rows[-1][:-1]+'…'
            rendered = '\n'.join(rows)
            box = draw.multiline_textbbox((0,0),rendered,font=font,spacing=4)
        width,height = box[2]-box[0],box[3]-box[1]
        draw.multiline_text(((880-width)/2-box[0],(self._raster_size[1]-height)/2-box[1]),rendered,font=font,fill=255,align='center',spacing=4)
        return mask

    def configure(self,cnf=None,**kwargs):
        if isinstance(cnf,dict):
            kwargs = {**cnf,**kwargs}
            cnf = None
        old_text = str(self.cget('text'))
        new_text = str(kwargs.get('text', old_text))
        changed = self._ready and 'text' in kwargs and new_text != old_text
        sleep_dots_only = (
            changed
            and old_text.rstrip('.') == new_text.rstrip('.') == 'deep sleep'
            and old_text[len('deep sleep'):] == '.' * len(old_text[len('deep sleep'):])
            and new_text[len('deep sleep'):] == '.' * len(new_text[len('deep sleep'):])
        )
        result = super().configure(cnf,**kwargs)
        if changed:
            if self._timer:
                self.after_cancel(self._timer)
                self._timer = None
            self._old,self._new = self._mask.copy(),self._raster(new_text)
            if sleep_dots_only:
                # The sleeping state is persistent; only its ellipsis ticks.
                # Re-morphing the entire phrase every cycle looks like loading.
                self._old = self._new
                self._motion = None
                self._show(self._new)
                return result
            self._at = time.monotonic()
            self._motion = RenderClock(self._duration)
            self._frame()
        return result

    config = configure

    @property
    def animation_complete(self):
        return self._motion is None

    def _show(self,mask):
        self._mask = mask
        if self._raster_only:
            return
        surface = Image.new('RGB',mask.size,self.cget('bg'))
        surface.paste('#1D1D1F',(0,0,*mask.size),mask)
        self._paint_surface(surface)

    def _paint_surface(self, surface):
        frame = surface.resize(self._output_size,Image.Resampling.LANCZOS)
        if self._photo is None:
            self._photo = ImageTk.PhotoImage(frame,master=self)
            super().configure(image=self._photo)
        else:
            # Replacing the Tk image on every frame forces a costly widget
            # reconfiguration and photo deletion. Update its pixels in place.
            self._photo.paste(frame)

    def _frame(self):
        started = time.perf_counter()
        self._timer = None
        p = (1. if self._reduce_motion() else self._motion.sample(time.monotonic())
             if self._motion is not None else 1.)
        self._show(blend_masks(self._old,self._new,p))
        if p<1:
            delay = max(1,math.ceil(ACTIVE_FRAME_MS-(time.perf_counter()-started)*1000))
            self._timer = self.after(delay,self._frame)
        else:
            self._motion = None

    def destroy(self):
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        super().destroy()
