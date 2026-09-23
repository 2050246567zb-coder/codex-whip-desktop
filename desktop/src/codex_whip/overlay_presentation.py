"""Reuse home animation primitives without feeding presentation into IMU physics."""
import tkinter as tk
import time
import sys
from PIL import Image, ImageTk, ImageChops
from .effects import WhipPose
from .interface import Hero, BG
from .morphing_title import MorphingTitle
from .sand_countdown import SandCountdownTitle, sand_surface
from .hover_clock import pointer_tilt


class OverlayPresentation:
    def __init__(self, effects):
        self.effects = effects
        self.canvas = effects.canvas
        # Offscreen widgets own the same animation algorithms as the home page.
        # Only their vector geometry/text masks are painted onto the transparent overlay.
        self.host = tk.Frame(self.canvas.master, bg=BG)
        self.host.place(x=10000, y=10000, width=440, height=520)
        self.hero = Hero(self.host, size=400, frame_provider=self.frame,
                         interactive=False, direct_pose=True, external_clock=True)
        self.hero.place(x=0,y=0,width=400,height=400)
        self.title = MorphingTitle(self.host, point_size=16)
        self.subtitle = SandCountdownTitle(self.host, point_size=10,height=40)
        self.items = []
        self.text_items = [self.canvas.create_image(0,0,anchor='n') for _ in range(2)]
        self.photos = [None,None]
        self.text_keys = [None,None]
        self.canvas._native_image_sizes = getattr(self.canvas, "_native_image_sizes", {})
        self.normal_title = 'just beat it'
        self.offset = (0.,0.)
        self.hit_pose = effects.IDLE
        self._strike_until = 0.

    @property
    def owns_geometry(self):
        if time.monotonic() < self._strike_until:
            return False
        return (self.hero.mode != 'whip' or self.hero._clock_hover
                or self.hero._clock_source is not None or self.hero._voice_source is not None
                or self.hero._voice_amount > 0)

    def begin_strike(self):
        self._strike_until = time.monotonic()+.35
        self.hero._set_clock(False)
        self.hero._clock_source = self.hero._voice_source = None
        self.hero._clock_alpha = self.hero._voice_amount = 0.
        self.hero._voice_from = 0.
        self.hero._voice_at = time.monotonic()+.35
        for _,item in self.items:
            self.canvas.itemconfigure(item,state='hidden')

    def frame(self):
        pose = self.effects._preview_pose
        self.offset = (pose.handle_start[0]-200, pose.handle_start[1]-120)
        def local(p):
            return p[0]-self.offset[0], p[1]-self.offset[1]
        return WhipPose(local(pose.handle_start),local(pose.handle_end),tuple(map(local,pose.cord))), (0.,0.)

    def update(self, *, mode, title, subtitle, deadline, clock_enabled, reduce_motion, level):
        self.hero.reduce_motion = reduce_motion
        self.hero.clock_enabled = clock_enabled
        self.hero.set_mode(mode)
        self.hero.audio_level(level)
        # The home hover clock does not open the overlay clock.
        self.normal_title = 'just beat it' if title == "Don't waste time on AI" else title
        self.title.configure(text="Don't waste time on AI" if self.hero._clock_hover else self.normal_title)
        self.subtitle.configure(text=subtitle)
        self.subtitle.set_countdown(deadline if subtitle == 'beat it, then send' else None)

    def toggle_clock(self):
        if self.hero.clock_enabled and self.hero.mode == 'whip':
            self.hero._set_clock(not self.hero._clock_hover)
            self.title.configure(text="Don't waste time on AI" if self.hero._clock_hover else self.normal_title)
        return 'break'

    def render(self, cursor=None):
        if self.hero._clock_hover and cursor is not None:
            ox,oy = self.effects._visual_origin
            self.hero._retarget_tilt(pointer_tilt(cursor[0]-ox-self.offset[0],
                                                 cursor[1]-oy-self.offset[1],400,400))
        if self.owns_geometry:
            self.hero._draw_live_whip()
        else:
            self.hero._display_pose = self.frame()[0]
        ox,oy = self.offset
        pose = self.hero._display_pose
        if pose is None:
            return
        def target(p):
            return p[0]+ox,p[1]+oy
        self.hit_pose = WhipPose(target(pose.handle_start), target(pose.handle_end),tuple(map(target,pose.cord)))
        slot = 0
        for source in self.hero.find_all() if self.owns_geometry else ():
            if self.hero.itemcget(source,'state') == 'hidden':
                continue
            kind = self.hero.type(source)
            if kind not in {'line','oval','text'}:
                continue
            if slot == len(self.items):
                self.items.append((kind, getattr(self.canvas,'create_'+kind)(0,0, *( (0,0) if kind!='text' else ()))))
            elif self.items[slot][0] != kind:
                self.canvas.delete(self.items[slot][1])
                self.items[slot] = (kind,getattr(self.canvas,'create_'+kind)(0,0,*((0,0) if kind!='text' else ())))
            dest = self.items[slot][1]
            coords = self.hero.coords(source)
            self.canvas.coords(dest,*[v+(ox if i%2==0 else oy) for i,v in enumerate(coords)])
            names = ('fill','width','capstyle','joinstyle','smooth') if kind=='line' else (
                ('fill','outline','width') if kind=='oval' else ('fill','text','font','anchor'))
            options = {name:self.hero.itemcget(source,name) for name in names}
            # Fully faded geometry must not paint a background-colored silhouette.
            invisible = options.get('fill','').lower() == BG.lower()
            self.canvas.itemconfigure(dest,**options,state='hidden' if invisible else 'normal')
            self.canvas.tag_raise(dest)
            slot += 1
        for _,item in self.items[slot:]:
            self.canvas.itemconfigure(item,state='hidden')
        bottom = max(p[1] for p in (pose.handle_start,pose.handle_end,*pose.cord)) + oy + 20
        dial = self.hero._clock_alpha
        bottom = bottom*(1-dial)+(400+oy)*dial
        ordinary = (self.hero.mode == 'whip' and not self.hero._clock_hover
                    and not self.subtitle.cget('text') and self.normal_title == 'just beat it')
        for index,label in enumerate((self.title,self.subtitle)):
            self.canvas.itemconfigure(self.text_items[index], state='hidden' if ordinary else 'normal')
            if ordinary:
                continue
            mask = label._mask
            deadline = getattr(label,'_deadline',None)
            key = (id(mask),deadline, getattr(label,'_fade_gray',False))
            if key != self.text_keys[index] or deadline is not None:
                surface = Image.new('RGBA',mask.size,'#1D1D1F')
                alpha = mask
                if deadline is not None or getattr(label,'_fade_gray',False):
                    surface = sand_surface(mask,1 if deadline is None else 1-(deadline-time.monotonic())/10,
                                           BG,particles=not self.hero.reduce_motion).convert('RGBA')
                    alpha = ImageChops.difference(surface.convert('RGB'),Image.new('RGB',mask.size,BG)).convert('L').point(lambda v:min(255,v*3))
                surface.putalpha(alpha)
                # Color-key windows cannot display partial alpha reliably. Render
                # a clean ink mask, with no magenta/white rectangle around text.
                logical_size = (360, round(mask.height*360/mask.width))
                if sys.platform == 'darwin':
                    # Keep the original oversampled glyph mask. AppKit maps it
                    # to logical points and samples at the display's backing scale.
                    self.canvas._native_image_sizes[self.text_items[index]] = logical_size
                else:
                    surface = surface.resize(logical_size,Image.Resampling.LANCZOS)
                    surface.putalpha(surface.getchannel('A').point(lambda v:255 if v>=100 else 0))
                self.photos[index] = ImageTk.PhotoImage(surface,master=self.canvas)
                self.canvas.itemconfigure(self.text_items[index],image=self.photos[index])
                self.text_keys[index] = key
            self.canvas.coords(self.text_items[index],200+ox,bottom+index*44)
            self.canvas.tag_raise(self.text_items[index])

    def close(self):
        for _,item in self.items:
            self.canvas.delete(item)
        for item in self.text_items:
            self.canvas._native_image_sizes.pop(item, None)
            self.canvas.delete(item)
        self.hero.close()
        self.title.destroy()
        self.subtitle.destroy()
        self.host.destroy()
