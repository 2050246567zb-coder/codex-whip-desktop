"""Reusable monochrome slider with labeled ticks for new settings controls."""
import math
import sys
import time
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk
from .hover_clock import ease


def render_track(width, fraction, background='#FFFFFF', disabled=False, thumb_scale=1.):
    scale = 3
    image = Image.new('RGB', (width*scale, 44*scale), background)
    draw = ImageDraw.Draw(image)
    left, right, y = 16*scale, (width-16)*scale, 22*scale
    x = left+(right-left)*max(0, min(1, fraction))
    black = '#A3A3A8' if disabled else '#19191B'
    draw.rounded_rectangle((left-7*scale,y-7*scale,right+7*scale,y+7*scale),
                           radius=7*scale, fill='#E7E7E9')
    draw.rounded_rectangle((left-11*scale,y-11*scale,x+11*scale,y+11*scale),
                           radius=11*scale, fill=black)
    outer, inner = (7*thumb_scale+4)*scale, 7*thumb_scale*scale
    draw.ellipse((x-outer,y-outer,x+outer,y+outer), fill=black)
    draw.ellipse((x-inner,y-inner,x+inner,y+inner), fill='white')
    return image.resize((width,44), Image.Resampling.LANCZOS)


class TickSlider(tk.Canvas):
    def __init__(self, parent, *, minimum=0.2, maximum=1.0, step=None,
                 variable=None, command=None, ticks=None, formatter=None, reduce_motion=False, **kwargs):
        if not minimum < maximum or (step is not None and step <= 0):
            raise ValueError('Invalid slider range')
        self.minimum, self.maximum, self.step = minimum, maximum, step
        self.variable = variable if variable is not None else tk.DoubleVar(master=parent, value=minimum)
        self.command = command
        self.ticks = ticks if ticks is not None else [minimum+(maximum-minimum)*i/4 for i in range(5)]
        self.formatter = formatter or (lambda value: f'{value:.1f}')
        self._last = float(self.variable.get())
        self._drag_offset = 0
        self.reduce_motion = reduce_motion
        self._thumb_scale = self._thumb_from = self._thumb_target = 1.
        self._thumb_at = 0.
        self._animation = None
        super().__init__(parent, width=320, height=88, bd=0, highlightthickness=0,
                         highlightbackground=kwargs.get('bg', parent.cget('bg')),
                         highlightcolor='#19191B', takefocus=True,
                         cursor='hand2', **kwargs)
        self.bind('<Configure>', self._draw)
        self.bind('<Button-1>', self._press)
        self.bind('<B1-Motion>', self._drag)
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Unmap>', lambda _e: self._animate_thumb(1.))
        increment = step or .01
        for key, delta in [('Left',-increment), ('Down',-increment), ('Right',increment), ('Up',increment)]:
            self.bind(f'<{key}>', lambda e, d=delta: self._key(self.get()+d))
        self.bind('<Home>', lambda e: self._key(minimum))
        self.bind('<End>', lambda e: self._key(maximum))
        self._trace = self.variable.trace_add('write', self._changed)
        self._draw()

    def get(self):
        return float(self.variable.get())

    def set(self, value):
        value = float(value)
        if not math.isfinite(value):
            return
        snapped = (self.minimum+round((value-self.minimum)/self.step)*self.step
                   if self.step else value)
        self.variable.set(round(max(self.minimum, min(self.maximum, snapped)), 6))

    def _changed(self, *_):
        value = self.get()
        if not math.isfinite(value) or not self.minimum <= value <= self.maximum:
            self.variable.set(self._last)
            return
        changed = value != self._last
        self._last = value
        self._draw()
        if changed and self.command:
            self.command(value)

    def _key(self, value):
        if str(self.cget('state')) != 'disabled':
            self.set(value)
        return 'break'

    def _press(self, event):
        if str(self.cget('state')) == 'disabled':
            return
        self.focus_set()
        width = max(60, self.winfo_width())
        x = 16+(width-32)*(self.get()-self.minimum)/(self.maximum-self.minimum)
        self._drag_offset = event.x-x if abs(event.x-x) <= 14 else 0
        if abs(event.x-x) <= 14 and abs(getattr(event, 'y', 22)-22) <= 14:
            self._animate_thumb(1.45)
        self._drag(event)

    def _release(self, event):
        self._drag(event)
        self._animate_thumb(1.)

    def _animate_thumb(self, target):
        if self._animation is not None:
            self.after_cancel(self._animation)
            self._animation = None
        self._thumb_from, self._thumb_target = self._thumb_scale, target
        self._thumb_at = time.monotonic()
        self._animate_frame()

    def _animate_frame(self):
        self._animation = None
        progress = min(1., (time.monotonic()-self._thumb_at)/.16)
        if self.reduce_motion:
            progress = 1.
        self._thumb_scale = self._thumb_from+(self._thumb_target-self._thumb_from)*ease(progress)
        self._draw()
        if progress < 1:
            self._animation = self.after(16, self._animate_frame)

    def _drag(self, event):
        if str(self.cget('state')) == 'disabled':
            return
        fraction = (event.x-self._drag_offset-16)/max(1,self.winfo_width()-32)
        self.set(self.minimum+fraction*(self.maximum-self.minimum))

    def _draw(self, *_):
        width = max(60, self.winfo_width() if self.winfo_width() > 1 else int(self.cget('width')))
        self.delete('all')
        fraction = (self.get()-self.minimum)/(self.maximum-self.minimum)
        self._image = ImageTk.PhotoImage(render_track(width, fraction, self.cget('bg'),
                          str(self.cget('state')) == 'disabled', self._thumb_scale), master=self)
        self.create_image(0,0,anchor='nw',image=self._image)
        font = ('Helvetica Neue' if sys.platform == 'darwin' else 'Microsoft YaHei UI', 9)
        for value in self.ticks:
            x = 16+(width-32)*(value-self.minimum)/(self.maximum-self.minimum)
            self.create_line(x,47,x,59,fill='#19191B')
            self.create_text(x,74,text=self.formatter(value),fill='#24262B',font=font)

    def destroy(self):
        self.unbind('<Unmap>')
        if self._animation is not None:
            self.after_cancel(self._animation)
            self._animation = None
        self.variable.trace_remove('write', self._trace)
        super().destroy()
