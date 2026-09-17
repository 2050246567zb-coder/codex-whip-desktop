"""State-driven blur/alpha-threshold text morph, inspired by Magic UI.

Native Pillow/Tk implementation, not a React runtime or an automatic text loop.
"""
import time
import math
from pathlib import Path
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops, ImageTk


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
    outgoing = old.filter(ImageFilter.GaussianBlur(min(100,blur_unit*p/(1-p))))
    incoming = new.filter(ImageFilter.GaussianBlur(min(100,blur_unit*(1-p)/p)))
    outgoing = outgoing.point([round(x*(1-p)**.4) for x in range(256)])
    incoming = incoming.point([round(x*p**.4) for x in range(256)])
    alpha = ImageChops.screen(outgoing,incoming)
    fused = alpha.point([max(0,min(255,(x-140)*255)) for x in range(256)])
    return fused.filter(ImageFilter.GaussianBlur(.6*2))


class MorphingTitle(tk.Label):
    def __init__(self,parent,reduce_motion=lambda:False):
        self._ready = False
        self._timer = None
        self._reduce_motion = reduce_motion
        self._mask = Image.new('L',(880,104))
        self._old = self._new = self._mask
        super().__init__(parent,text='',bg=parent.cget('bg'),bd=0,padx=0,pady=0)
        candidates = ['C:/Windows/Fonts/msyhbd.ttc',
                      '/System/Library/Fonts/PingFang.ttc',
                      '/System/Library/Fonts/STHeiti Medium.ttc',
                      '/System/Library/Fonts/Supplemental/Songti.ttc',
                      '/System/Library/Fonts/Supplemental/Arial Bold.ttf']
        size = round(self.winfo_fpixels('23p')*2)
        path = next((p for p in candidates if Path(p).exists()),None)
        self._font = ImageFont.truetype(path,size) if path else ImageFont.load_default(size=size)
        self._ready = True
        self._show(self._mask)

    def _raster(self,text):
        mask = Image.new('L',(880,104))
        draw = ImageDraw.Draw(mask)
        font = self._font
        box = draw.textbbox((0,0),text,font=font)
        if box[2]-box[0]>840:
            font = font.font_variant(size=max(12,round(font.size*840/(box[2]-box[0]))))
            box = draw.textbbox((0,0),text,font=font)
        width,height = box[2]-box[0],box[3]-box[1]
        draw.text(((880-width)/2-box[0],(104-height)/2-box[1]),text,font=font,fill=255)
        return mask

    def configure(self,cnf=None,**kwargs):
        if isinstance(cnf,dict):
            kwargs = {**cnf,**kwargs}
            cnf = None
        changed = self._ready and 'text' in kwargs and kwargs['text'] != self.cget('text')
        result = super().configure(cnf,**kwargs)
        if changed:
            if self._timer:
                self.after_cancel(self._timer)
                self._timer = None
            self._old,self._new = self._mask.copy(),self._raster(str(kwargs['text']))
            self._at = time.monotonic()
            self._frame()
        return result

    config = configure

    def _show(self,mask):
        self._mask = mask
        surface = Image.new('RGB',mask.size,self.cget('bg'))
        surface.paste('#1D1D1F',(0,0,*mask.size),mask)
        self._photo = ImageTk.PhotoImage(surface.resize((440,52),Image.Resampling.LANCZOS),master=self)
        super().configure(image=self._photo)

    def _frame(self):
        self._timer = None
        p = min(1.,(time.monotonic()-self._at)/.8)
        if self._reduce_motion():
            p = 1.
        self._show(blend_masks(self._old,self._new,p))
        if p<1:
            self._timer = self.after(16,self._frame)

    def destroy(self):
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        super().destroy()
