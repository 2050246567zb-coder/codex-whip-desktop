"""Fixed-size native settings button with an interruptible hover glyph."""
import math
import time
import sys
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk
from .hover_clock import ease


class GearButton(tk.Label if sys.platform == 'darwin' else tk.Button):
    def __init__(self,parent,command,reduce_motion=lambda:False):
        self._scale = self._from = self._target = 1.
        self._at = 0.
        self._timer = None
        self._reduce_motion = reduce_motion
        self._command = command
        options = {} if sys.platform == 'darwin' else {'command': command}
        super().__init__(parent,**options,text='设置',bg=parent.cget('bg'),
                         activebackground=parent.cget('bg'),bd=0,relief='flat',
                         highlightthickness=0,takefocus=True,cursor='hand2',padx=0,pady=0)
        self.bind('<Enter>',lambda e:self._retarget(1.2))
        self.bind('<Leave>',lambda e:self._retarget(1.))
        self.bind('<FocusIn>',lambda e:self._retarget(1.2))
        self.bind('<FocusOut>',lambda e:self._retarget(1.))
        if sys.platform == 'darwin':
            # Aqua draws native Button chrome even with relief=flat/bd=0.
            self.bind('<ButtonPress-1>', lambda e: self.focus_set())
            self.bind('<ButtonRelease-1>', lambda e: self.invoke()
                      if 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height() else None)
            self.bind('<space>', lambda e: self.invoke())
            self.bind('<Return>', lambda e: self.invoke())
        self._paint()

    def invoke(self):
        if self.cget('state') != 'disabled':
            return self._command()

    def _retarget(self,target):
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        self._from,self._target,self._at = self._scale,target,time.monotonic()
        self._frame()

    def _frame(self):
        self._timer = None
        p = min(1.,(time.monotonic()-self._at)/.16)
        if self._reduce_motion():
            p = 1.
        self._scale = self._from+(self._target-self._from)*ease(p,.23,.32,1.,1.)
        self._paint()
        if p<1:
            self._timer = self.after(16,self._frame)

    def _paint(self):
        image = Image.new('RGB',(132,114),self.cget('bg'))
        draw = ImageDraw.Draw(image)
        points=[]
        for i in range(64):
            radius = (7.6 if i%8 in (2,3,4,5) else 6.1)*self._scale*3
            a = math.tau*i/64
            points.append((66+math.cos(a)*radius,57+math.sin(a)*radius))
        draw.polygon(points,fill='#1D1D1F')
        radius=3.4*self._scale*3
        draw.ellipse((66-radius,57-radius,66+radius,57+radius),fill=self.cget('bg'))
        self._photo = ImageTk.PhotoImage(image.resize((44,38),Image.Resampling.LANCZOS),master=self)
        self.configure(image=self._photo)

    def destroy(self):
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        super().destroy()
