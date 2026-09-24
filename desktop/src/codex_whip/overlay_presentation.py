"""Reuse home animation primitives without feeding presentation into IMU physics."""
import tkinter as tk
import time
import os
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
        self.host = tk.Frame(effects.window, bg=BG)
        self.host.place(x=10000, y=10000, width=440, height=520)
        self.hero = Hero(self.host, size=400, frame_provider=self.frame,
                         interactive=False, direct_pose=True, external_clock=True)
        log = getattr(effects, '_log', None)
        if callable(log):
            self.hero.transition_observer = lambda report: log(
                f'Codex 动画帧：{report.mode} {report.frames} 帧 / {report.duration_ms} ms，'
                f'首帧等待 {report.first_frame_wait_ms} ms，'
                f'间隔 P95 {report.interval_p95_ms} ms、最大 {report.interval_max_ms} ms，'
                f'绘制 P95 {report.draw_p95_ms} ms')
        self.hero.place(x=0,y=0,width=400,height=400)
        self.title = MorphingTitle(self.host, point_size=16, duration=1.0,
                                   raster_only=True)
        self.subtitle = SandCountdownTitle(self.host, point_size=10,height=40,
                                           raster_only=True)
        self.items = []
        self.text_items = [self.canvas.create_image(0,0,anchor='n') for _ in range(2)]
        self.photos = [None,None]
        self.text_keys = [None,None]
        self.text_surfaces = [None,None]
        self._text_revision = 0
        self._native_revision = -1
        self.canvas._native_image_sizes = getattr(self.canvas, '_native_image_sizes', {})
        self._native_text = None
        if os.name == 'nt' and hasattr(effects, '_root'):
            try:
                from .windows_alpha_overlay import WindowsAlphaTextOverlay
                self._native_text = WindowsAlphaTextOverlay(
                    effects._root, z_anchor=effects._overlay_z_anchor)
            except (OSError, tk.TclError):
                # Keep the existing color-key text if native layering fails.
                self._native_text = None
        self.normal_title = ''
        self._requested_title = ''
        self._requested_subtitle = ''
        self._requested_deadline = None
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

    @property
    def transition_active(self):
        return self.hero.transition_active or not self.title.animation_complete

    @property
    def transition_complete(self):
        return not self.transition_active

    def begin_strike(self):
        self._strike_until = time.monotonic()+.35
        self.hero._set_clock(False)
        self.hero._clock_source = self.hero._voice_source = None
        self.hero._clock_motion = self.hero._voice_motion = self.hero._loading_motion = None
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
        if not clock_enabled:
            self.hero._set_clock(False, force=True)
        self.hero.set_mode(mode)
        self.hero.audio_level(level)
        # The home hover clock does not open the overlay clock.
        self.normal_title = '' if title == "Don't waste time on AI" else title
        # The title should not start morphing before the geometry's first
        # frame has actually been copied onto the visible overlay.
        self._requested_title = "Don't waste time on AI" if self.hero._clock_hover else self.normal_title
        self._requested_subtitle = subtitle
        self._requested_deadline = deadline if subtitle == 'beat it, then send' else None

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
        self.title.configure(text=self._requested_title)
        self.subtitle.configure(text=self._requested_subtitle)
        self.subtitle.set_countdown(self._requested_deadline)
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
                    and not self.subtitle.cget('text') and not self.normal_title)
        if ordinary:
            self.hide()
            return
        for index,label in enumerate((self.title,self.subtitle)):
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
                logical_size = (360,round(mask.height*360/mask.width))
                if sys.platform == 'darwin':
                    # AppKit composites the full glyph mask at Retina density.
                    self.canvas._native_image_sizes[self.text_items[index]] = logical_size
                else:
                    surface = surface.resize(logical_size,Image.Resampling.LANCZOS)
                    if self._native_text is None:
                        # Color-key windows have no partial alpha. Keep a safe
                        # binary fallback if the native Windows layer is absent.
                        surface.putalpha(surface.getchannel('A').point(lambda v:255 if v>=100 else 0))
                self.text_surfaces[index] = surface
                self._text_revision += 1
                if self._native_text is None:
                    self.photos[index] = ImageTk.PhotoImage(surface,master=self.canvas)
                    self.canvas.itemconfigure(self.text_items[index],image=self.photos[index])
                self.text_keys[index] = key
            self.canvas.coords(self.text_items[index],200+ox,bottom+index*44)
            self.canvas.itemconfigure(self.text_items[index],
                                      state='hidden' if self._native_text is not None else 'normal')
            self.canvas.tag_raise(self.text_items[index])

        if self._native_text is not None:
            left = round(self.effects._visual_origin[0]+200+ox-180)
            top = round(self.effects._visual_origin[1]+bottom)
            try:
                if self._native_revision != self._text_revision:
                    height = max(44+self.text_surfaces[1].height, self.text_surfaces[0].height)
                    combined = Image.new('RGBA',(360,height),(0,0,0,0))
                    for index,surface in enumerate(self.text_surfaces):
                        combined.alpha_composite(surface,(0,index*44))
                    self._native_text.paint(combined,left,top)
                    self._native_revision = self._text_revision
                else:
                    self._native_text.move(left,top)
            except (OSError, tk.TclError):
                self._native_text.close()
                self._native_text = None
                self.text_keys = [None,None]

    def hide(self):
        for item in self.text_items:
            self.canvas.itemconfigure(item,state='hidden')
        if self._native_text is not None:
            self._native_text.hide()

    def close(self):
        if self._native_text is not None:
            self._native_text.close()
        for _,item in self.items:
            self.canvas.delete(item)
        for item in self.text_items:
            self.canvas._native_image_sizes.pop(item,None)
            self.canvas.delete(item)
        self.hero.close()
        self.title.destroy()
        self.subtitle.destroy()
        self.host.destroy()
