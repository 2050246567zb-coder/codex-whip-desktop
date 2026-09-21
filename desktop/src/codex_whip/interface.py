"""Quiet native UI. This adapter observes events and invokes existing commands.

Sensor fusion, gesture recognition, audio transcription and overlay physics live
in their original modules. The home whip mirrors the overlay; the microphone is
a generated raster asset.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from pathlib import Path
import sys
import math
import time
import tkinter as tk
from types import SimpleNamespace
from tkinter import messagebox

from PIL import Image, ImageTk

from .interface_state import InterfacePreferences
from .mount_profile import load_mounting_profile
from .paths import user_data_dir
from . import __version__
from .effects import CodexWhipEffects, WhipPose
from .whip_drawing import WhipDrawing
from .gear_button import GearButton
from .battery_indicator import BatteryIndicator
from .morphing_title import MorphingTitle
from .sand_countdown import SandCountdownTitle
from .hover_clock import clock_pose, morph, ease, near_whip, project, project_pose, pointer_tilt, loading_pose, recognizing_pose, cord_rotation

BG, CARD, SOFT = "#F5F5F7", "#FFFFFF", "#EAEAED"
TEXT, MUTED, LINE = "#1D1D1F", "#68686F", "#DEDEE3"
BLUE, GREEN, RED = "#0066CC", "#237C4B", "#BC3434"
FONT = "Helvetica Neue" if sys.platform == "darwin" else "Microsoft YaHei UI"


def asset_path(name: str) -> Path:
    root = (Path(sys._MEIPASS) if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[2])
    return root / "assets" / "interface" / name


def label(parent, text="", *, size=10, color=TEXT, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=(FONT, size, "bold" if bold else "normal"), **kwargs)


def button(parent, text, command, *, primary=False, **kwargs):
    value = tk.Button(parent, text=text, command=command,
                      bg=BLUE if primary else SOFT, fg="white" if primary else TEXT,
                      activebackground="#0055AA" if primary else LINE,
                      activeforeground="white" if primary else TEXT,
                      disabledforeground="#939399", relief="flat", bd=0,
                      padx=18, pady=10, cursor="hand2", takefocus=True,
                      highlightthickness=1, highlightbackground=parent.cget("bg"),
                      highlightcolor=BLUE, font=(FONT, 10), **kwargs)
    # Native active state gives immediate pressed feedback; no animation lockout.
    return value


class Hero(tk.Canvas):
    """Read-only overlay mirror plus the generated microphone and audio meter."""
    def __init__(self, parent, *, reduce_motion=False, size=290, frame_provider=None, interactive=True, direct_pose=False, external_clock=False):
        super().__init__(parent, width=size, height=size, bg=parent.cget("bg"),
                         highlightthickness=0)
        self.reduce_motion = reduce_motion
        self.mode = "whip"
        self._closed = False
        self._timer = None
        self._strike_at = -100.0
        self._levels = deque([0.0] * 35, maxlen=35)
        self._level = 0.0
        self._audio_at = -100.0
        self._wave_at = 0.0
        self._photo = None
        self._image_key = None
        self._transition_at = -100.0
        self._from_surface = None
        self._surface = None
        self._sources = {}
        self._frame_provider = frame_provider
        self.direct_pose = direct_pose
        self.external_clock = external_clock
        self._whip_drawing = WhipDrawing(self)
        self._preview_key = None
        self.clock_enabled = False
        self._clock_hover = False
        self._clock_started = -100.
        self._clock_source = None
        self._cord_turn = None
        self._display_pose = None
        self._clock_alpha = 0.
        self._clock_from_alpha = 0.
        self._clock_items = []
        self._loading_at = time.monotonic()
        self._recognizing_at = time.monotonic()
        self._loading_alpha = self._loading_from = 0.
        self._loading_transition_at = -100.
        self._loading_dots = []
        self._dial_edges = []
        self._tilt = self._tilt_from = self._tilt_target = (0.,0.)
        self._tilt_at = 0.
        self._voice_amount = self._voice_from = self._voice_target = 0.
        self._voice_at = -100.
        self._voice_source = None
        self._voice_scale = 1.
        for key, file in (("microphone", "microphone.png"),):
            with Image.open(asset_path(file)) as source:
                self._sources[key] = source.convert("RGBA")
        self.bind("<Configure>", lambda _e: self._wake())
        self.bind("<Destroy>", lambda _e: self.close())
        self._pointer_host = self.winfo_toplevel()
        self._pointer_binding = self._leave_binding = None
        if interactive:
            self.bind("<Motion>", self._hover_motion)
            self.bind("<Leave>", self._outer_motion)
            self._pointer_binding = self._pointer_host.bind('<Motion>', self._outer_motion, add='+')
            self._leave_binding = self._pointer_host.bind('<Leave>', self._outer_motion, add='+')
        self._wake()

    def _set_clock(self, active):
        if active == self._clock_hover:
            return
        self._clock_hover = active
        callback = getattr(self, 'clock_title_changed', None)
        if callback:
            callback(active)
        if not active:
            self._retarget_tilt((0.,0.))
        self._clock_source = self._display_pose
        self._cord_turn = None
        self._clock_from_alpha = self._clock_alpha
        self._clock_started = time.monotonic()
        self._preview_key = None
        self._wake()

    def _hover_motion(self, event):
        if not self.clock_enabled or self.mode != "whip":
            self._set_clock(False)
            return
        w,h = max(120,self.winfo_width()),max(120,self.winfo_height())
        if self._clock_hover:
            if math.hypot(event.x-w/2,event.y-h/2) > min(w,h)*.49*1.5:
                self._set_clock(False)
            else:
                self._retarget_tilt(pointer_tilt(event.x,event.y,w,h))
            return
        if self._display_pose and near_whip(self._display_pose, event.x, event.y):
            self._set_clock(True)
            self._retarget_tilt(pointer_tilt(event.x,event.y,w,h))

    def _outer_motion(self, event):
        # The expanded hit circle extends outside the canvas; use window events
        # in canvas coordinates, rather than treating canvas leave as clock exit.
        if self._clock_hover and not self._closed:
            self._hover_motion(SimpleNamespace(x=event.x_root-self.winfo_rootx(),
                                                y=event.y_root-self.winfo_rooty()))

    def _retarget_tilt(self, target):
        self._update_tilt()
        self._tilt_from = self._tilt
        self._tilt_target = target
        self._tilt_at = time.monotonic()

    def _update_tilt(self):
        amount = ease((time.monotonic()-self._tilt_at)/.20, .23, .32, 1., 1.)
        self._tilt = (tuple(a+(b-a)*amount for a,b in zip(self._tilt_from,self._tilt_target))
                      if not self.reduce_motion else (0.,0.))

    def _draw_dial(self, w, h, alpha):
        if alpha < .001:
            for item in self._clock_items + self._dial_edges:
                self.itemconfigure(item, state="hidden")
            return
        if not self._clock_items:
            self._clock_items = [self.create_line(0,0,0,0) for _ in range(60)]
            self._clock_items += [self.create_text(0,0, text=str(i), font=(FONT,9))
                                  for i in range(1,13)]
            for item in self._clock_items:
                self.tag_lower(item)
            for item in self._dial_edges:
                self.tag_lower(item)
        bg = self.winfo_rgb(self.cget("bg"))
        color = "#" + "".join(f"{round(v/257*(1-alpha)+105*alpha):02x}" for v in bg)
        r = min(w,h)*.38
        def point(angle, radius):
            return project((w/2+math.sin(angle)*radius, h/2-math.cos(angle)*radius),w,h,self._tilt)
        for i,item in enumerate(self._clock_items[:60]):
            a = i*math.tau/60
            self.coords(item, *point(a,r), *point(a,r-(8 if i%5==0 else 3)))
            self.itemconfigure(item, fill=color, width=1.5 if i%5==0 else 1, state="normal")
        for i,item in enumerate(self._clock_items[60:],1):
            self.coords(item,*point(i*math.tau/12,r+14))
            self.itemconfigure(item,fill=color,state="normal")

    def _wake(self):
        if self.external_clock:
            return
        if not self._closed and self._timer is None:
            self._timer = self.after(16, self._draw)

    def set_mode(self, mode):
        if self.mode != mode:
            voice_target = 1. if mode == 'recording' else 0.
            if voice_target != self._voice_target or self._voice_source is not None:
                self._voice_source = self._display_pose
                self._voice_from = self._voice_amount
                self._voice_target = voice_target
                self._voice_at = time.monotonic()
                self._preview_key = None
            if mode != 'whip':
                self._set_clock(False)
            if mode == 'recognizing' or self.mode == 'recognizing':
                self._clock_source = self._display_pose
                self._cord_turn = None
                self._clock_started = time.monotonic()
                self._clock_from_alpha = self._clock_alpha
                if mode == 'recognizing':
                    self._recognizing_at = time.monotonic()
                self._preview_key = None
            if mode == 'connecting' or self.mode == 'connecting':
                self._set_clock(False)
                self._clock_source = self._display_pose
                self._cord_turn = None
                self._clock_started = time.monotonic()
                self._loading_from = self._loading_alpha
                self._loading_transition_at = time.monotonic()
                if mode == 'connecting':
                    self._loading_at = time.monotonic()
                self._preview_key = None
            self._from_surface = self._surface
            self._transition_at = time.monotonic()
            self.mode = mode
            self._levels = deque([0.0] * 35, maxlen=35)
            self._level = 0.0
            self._image_key = None
        self._wake()

    def pose(self, x, y):
        # The overlay supplies the actual frame; raw pose never drives a second simulation.
        self._wake()

    def strike(self):
        self._strike_at = time.monotonic()
        self._wake()

    def audio_level(self, level):
        self._level = max(0.0, min(1.0, level))
        self._audio_at = time.monotonic()
        self._wake()

    def _draw(self):
        if self._timer is not None:
            self.after_cancel(self._timer)
        self._timer = None
        if self._closed:
            return
        self._draw_live_whip()
        self._timer = self.after(100 if self.reduce_motion else 16, self._draw)
    def _draw_live_whip(self):
        self.delete("voice_art")
        frame = self._frame_provider() if self._frame_provider else None
        if not (isinstance(frame, tuple) and len(frame) == 2 and isinstance(frame[0], WhipPose)):
            frame = (CodexWhipEffects.IDLE, (0.0, 0.0))
        pose, position = frame
        w, h = max(120, self.winfo_width()), max(120, self.winfo_height())
        key = (pose, position, w, h)
        if not self.clock_enabled:
            self._set_clock(False)
        animated = (self._clock_hover or self._clock_source is not None or self.mode in {'connecting', 'recognizing'}
                    or self._loading_alpha > 0 or self._voice_source is not None or self._voice_amount > 0)
        if key == self._preview_key and not animated:
            return
        self._preview_key = key
        scale = min(w / 1000, h / 780)
        anchor = (w * (0.5 + max(-0.45, min(0.45, position[0])) * 0.7),
                  h * (0.27 + max(-0.4, min(0.4, position[1])) * 0.55))
        offset = (anchor[0] - pose.handle_start[0] * scale,
                  anchor[1] - pose.handle_start[1] * scale)
        def screen(point):
            return point[0]*scale+offset[0], point[1]*scale+offset[1]
        live = WhipPose(screen(pose.handle_start), screen(pose.handle_end),
                        tuple(screen(p) for p in pose.cord))
        if self.direct_pose:
            live, scale = pose, 1.0
        self._update_tilt()
        target = project_pose(clock_pose(w,h,len(pose.cord)),w,h,self._tilt) if self._clock_hover else live
        if self.mode == 'connecting':
            target = loading_pose(w,h,len(pose.cord),0 if self.reduce_motion else time.monotonic()-self._loading_at)
        elif self.mode == 'recognizing':
            target = recognizing_pose(w,h,len(pose.cord),
                                      0 if self.reduce_motion else time.monotonic()-self._recognizing_at)
        elapsed = (time.monotonic()-self._clock_started)/.28
        progress = ease(elapsed)
        if self.reduce_motion:
            progress = 1.
        if self._clock_source is not None:
            self._cord_turn = cord_rotation(self._clock_source,target,self._cord_turn)
        self._display_pose = (morph(self._clock_source,target,progress,self._cord_turn)
                              if self._clock_source is not None and elapsed < 1 else target)
        self._clock_alpha = self._clock_from_alpha + ((1. if self._clock_hover else 0.)-self._clock_from_alpha)*progress
        if elapsed >= 1:
            self._clock_source = None
        voice_progress = 1. if self.reduce_motion else ease((time.monotonic()-self._voice_at)/.28)
        self._voice_amount = self._voice_from + (self._voice_target-self._voice_from)*voice_progress
        if self._voice_source is not None or self._voice_amount > 0:
            # The rope joint is the microphone head; it settles at canvas center.
            mic = WhipPose((w/2,h/2+min(w,h)*.32), (w/2,h/2),
                           tuple((w/2,h/2-i*.01) for i in range(len(pose.cord))))
            destination = mic if self._voice_target else self._display_pose
            if self._voice_source is not None and voice_progress < 1:
                self._display_pose = morph(self._voice_source,destination,voice_progress)
            else:
                self._display_pose = destination
                self._voice_source = None
            self._clock_alpha *= 1-self._voice_amount
        self._draw_dial(w,h,self._clock_alpha)
        # Geometry is in screen pixels; retain the shared whip stroke scale.
        voice_scale = .4 if self.direct_pose else 2.5
        clock_scale = 1-.5*self._clock_alpha if self.direct_pose else 1.
        self._whip_drawing._draw_pose(self._display_pose, scale*clock_scale*(1+voice_scale*self._voice_amount))
        self._whip_drawing.fade_cord(1-self._voice_amount, self.cget('bg'))
        self._draw_voice(w,h)
        self._draw_loading(w,h)

    def _draw_voice(self,w,h):
        alpha = self._voice_amount
        if alpha < .001:
            return
        x,y = self._display_pose.handle_end
        # Audio changes ring strength, never the hand or rope physics.
        now = time.monotonic()
        level = self._level if now-self._audio_at < .25 else 0.
        if self.mode == 'recording' and not self.reduce_motion:
            for index in range(3):
                phase = ((now-self._voice_at)/1.5-index/3)%1
                radius = 16+phase*min(w,h)*.35
                opacity = alpha*(1-phase)**2*(.18+.25*level)
                bg = self.winfo_rgb(self.cget('bg'))
                color = '#' + ''.join(f'{round(v/257*(1-opacity)+35*opacity):02x}' for v in bg)
                ring = self.create_oval(x-radius,y-radius,x+radius,y+radius,
                                       outline=color,width=1.4,tags='voice_art')
                self.tag_lower(ring)
        # Flat black capsule: the same visual language as the cartoon handle.
        size = min(w,h)*.075
        # Grow from the existing handle tip, never fade a full-size ghost head.
        width = 2. + (size-2.)*alpha
        start = self._display_pose.handle_start
        length = max(.001, math.hypot(x-start[0], y-start[1]))
        dx,dy = (x-start[0])/length, (y-start[1])/length
        self.create_line(x+dx*size*.65*alpha,y+dy*size*.65*alpha,
                         x-dx*size*.15*alpha,y-dy*size*.15*alpha,width=width,
                         fill='#171717',capstyle='round',tags='voice_art')

    def _draw_loading(self,w,h):
        amount = ease((time.monotonic()-self._loading_transition_at)/.28)
        self._loading_alpha = self._loading_from + ((1. if self.mode=='connecting' else 0.)-self._loading_from)*amount
        if not self._loading_dots:
            self._loading_dots = [self.create_oval(0,0,0,0,outline='') for _ in range(3)]
        phase = (time.monotonic()-self._loading_at)%1.5
        bg = self.winfo_rgb(self.cget('bg'))
        for i,item in enumerate(self._loading_dots):
            opacity = ease((phase-i*.35)/.16) * (1-ease((phase-1.25)/.25))
            if self.reduce_motion:
                opacity = 1.
            opacity *= self._loading_alpha
            color = '#' + ''.join(f'{round(v/257*(1-opacity)+20*opacity):02x}' for v in bg)
            x,y = w/2+(i-1)*15,h/2
            self.coords(item,x-3,y-3,x+3,y+3)
            self.itemconfigure(item,fill=color,state='normal' if opacity>.001 else 'hidden')

    def close(self):
        self._closed = True
        for event, binding in (('<Motion>', self._pointer_binding), ('<Leave>', self._leave_binding)):
            if binding:
                try:
                    self._pointer_host.unbind(event, binding)
                except tk.TclError:
                    pass
        self._pointer_binding = self._leave_binding = None
        if self._timer is not None:
            try:
                self.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None


class Interface:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.path = user_data_dir() / "interface-preferences.json"
        calibrated = load_mounting_profile(app.mounting_path) is not None
        self.preferences = InterfacePreferences.load(self.path, already_calibrated=calibrated)
        self.stage = "ready" if self.preferences.setup_complete else (
            "choices" if calibrated else "connect")
        self._sensor_at = -100.0
        self._closed = False
        self._timer = None
        self._voice_state = ""
        self._pending = ""
        self._pending_until = 0.
        self._notice = ""
        self._notice_until = 0.0
        self._render_key = None
        self._learning_queue = []
        self._learning_kind = ""
        self._tap_count = 0
        self._tap_done = False
        self._battery_percent = None
        self._battery_charging = False
        self._advanced_section = "general"
        self._build_home()
        self._build_preferences()
        if not calibrated and not self.path.exists():
            self._persist()  # Resume an interrupted first-use setup after calibration.
        self.root.bind("<Control-comma>", lambda _e: self.open_preferences())
        self.root.bind("<Command-comma>", lambda _e: self.open_preferences()) if sys.platform == "darwin" else None
        self._refresh()

    def _build_home(self):
        self.root.title("Codex 鞭子")
        self.root.geometry("560x660")
        self.root.minsize(500, 620)
        self.root.configure(bg=BG)
        shell = tk.Frame(self.root, bg=BG, padx=28, pady=20)
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg=BG)
        header.pack(fill="x")
        self.connection_label = label(header, "●", color=MUTED, size=9)
        self.battery_indicator = BatteryIndicator(header, font=FONT)
        self.battery_indicator.pack(side="left")
        self.app.settings_button = GearButton(header, self.app.open_settings,
                                              lambda: self.preferences.reduce_motion)
        self.app.settings_button.pack(side="right")
        self.step_label = label(shell, color=BLUE, size=9)
        self.step_label.pack(pady=(20, 0))
        self.hero = Hero(shell, reduce_motion=self.preferences.reduce_motion, size=275,
                         frame_provider=self._whip_frame)
        self.hero.pack(fill="both", expand=True, pady=(0, 0))
        self.title = MorphingTitle(shell, lambda: self.preferences.reduce_motion)
        self.hero.clock_title_changed = self._clock_title_changed
        self.title.pack(pady=(0, 9))
        self.subtitle = SandCountdownTitle(shell, lambda: self.preferences.reduce_motion,point_size=10,height=44)
        self.subtitle.pack(fill="x")
        self.choices = tk.Frame(shell, bg=BG)
        self.whip_choice = tk.BooleanVar(master=self.root, value=False)
        self.tap_choice = tk.BooleanVar(master=self.root, value=False)
        for text, variable in (("抽打动作 · 15 次", self.whip_choice),
                               ("双敲力度范围 · 测量一次，并启用语音", self.tap_choice)):
            tk.Checkbutton(self.choices, text=text, variable=variable, bg=BG, fg=TEXT,
                           activebackground=BG, selectcolor=CARD, font=(FONT, 10),
                           cursor="hand2").pack(anchor="w", pady=3)
        self.actions = tk.Frame(shell, bg=BG)
        self.primary = button(self.actions, "开始方向校准", self.advance, primary=True)
        self.primary.pack(side="right")
        self.skip = button(self.actions, "以后再录入", self.skip_learning)
        self.skip_setup_button = button(self.actions, "跳过引导", self.skip_setup)
        self.progress = label(shell, size=9, color=MUTED, wraplength=440, justify="center")
        self.progress.pack(pady=(12, 0))
        self.pending = label(shell, size=11, color=TEXT, wraplength=430, justify="center",
                             cursor="hand2")
        self.pending.bind("<Button-1>", lambda _e: self.open_preferences("general"))
        self.footer = tk.Frame(shell, bg=BG, height=16)
        self.footer.pack(side="bottom", pady=(18, 0))

    def _whip_frame(self):
        effects = getattr(self.app, "effects", None)
        return effects.preview_frame() if effects is not None else None

    def _build_preferences(self):
        from . import settings_style as style
        from .disclosure import Disclosure
        BG, CARD, SOFT = style.BG, style.CARD, style.SIDEBAR
        button = style.ActionButton
        a = self.app
        self.settings = tk.Toplevel(self.root)
        self.settings.withdraw()
        self.settings.title("设置 · Codex 鞭子")
        self.settings.geometry("1060x820")
        self.settings.minsize(970, 760)
        self.settings.configure(bg=BG)
        self.settings.transient(self.root)
        self.settings.protocol("WM_DELETE_WINDOW", self.hide_preferences)
        self.settings.bind("<Escape>", lambda _e: self.hide_preferences())
        sidebar = tk.Frame(self.settings, bg=SOFT, width=184, padx=16, pady=28)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        label(sidebar, "Codex Whip", size=13, bold=True).pack(anchor="w", padx=10)
        label(sidebar, "偏好设置", size=9, color=MUTED).pack(anchor="w", padx=10, pady=(5, 28))
        self.nav = {}
        for key, title in (("general", "通用"), ("calibration", "手柄"), ("input", "输入")):
            b = button(sidebar, title, lambda k=key: self.open_preferences(k), navigation=True, bg=SOFT)
            b.configure(anchor="center")
            b.pack(fill="x", pady=5)
            self.nav[key] = b
        label(sidebar, f"Codex Whip\n{__version__}", size=9, color=MUTED, justify="left").pack(side="bottom", anchor="w", padx=10)
        self.host = tk.Frame(self.settings, bg=BG)
        self.host.pack(side="right", fill="both", expand=True)
        self.general = tk.Frame(self.host, bg=BG)
        # Scroll all general controls together so small screens never hide actions.
        canvas = tk.Canvas(self.general, bg=BG, highlightthickness=0)
        scroll = tk.Scrollbar(self.general, command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        body = self._general_body = tk.Frame(self.host, bg=BG)
        self._advanced_panel = tk.Frame(self.host, bg=BG)
        advanced_canvas = tk.Canvas(self._advanced_panel, bg=BG, highlightthickness=0)
        advanced_scroll = tk.Scrollbar(self._advanced_panel, command=advanced_canvas.yview)
        advanced_scroll.pack(side="right", fill="y")
        advanced_canvas.pack(side="left", fill="both", expand=True)
        advanced_canvas.configure(yscrollcommand=advanced_scroll.set)
        self.advanced_host = tk.Frame(advanced_canvas, bg=BG)
        advanced_item = advanced_canvas.create_window(0, 0, anchor="nw", window=self.advanced_host)
        def resize_advanced(_event=None):
            top = advanced_canvas.canvasy(0)
            advanced_canvas.itemconfigure(advanced_item, width=advanced_canvas.winfo_width(),
                                          height=max(advanced_canvas.winfo_height(),
                                                     self.advanced_host.winfo_reqheight()))
            advanced_canvas.configure(scrollregion=advanced_canvas.bbox("all"))
            height = max(1, self.advanced_host.winfo_reqheight(), advanced_canvas.winfo_height())
            advanced_canvas.yview_moveto(max(0, top) / height)
        self._resize_advanced = resize_advanced
        self._advanced_canvas = advanced_canvas
        advanced_canvas.bind("<Configure>", resize_advanced)
        self.advanced_host.bind("<Configure>", resize_advanced)

        def group(title):
            frame = style.RoundedCard(body, padx=24, pady=24)
            frame.pack(fill="x", pady=(0, 14))
            label(frame, title, size=11, bold=True).pack(anchor="w", pady=(0, 12))
            return frame

        device = group("连接")
        for text, value in (("手柄", a.ble_value), ("目标", a.codex_value)):
            row = tk.Frame(device, bg=CARD)
            row.pack(fill="x", pady=4)
            if text == '目标':
                from tkinter import ttk
                self.target_app = tk.StringVar(value=a.settings.codex.target_app)
                self.target_selector = ttk.Combobox(row, textvariable=self.target_app,
                    values=('Codex', 'Claude'), state='readonly', width=12)
                self.target_selector.pack(side='left')
                def choose_target(_event):
                    if not a.select_target_app(self.target_app.get()):
                        self.target_app.set(a.settings.codex.target_app)
                self.target_selector.bind('<<ComboboxSelected>>', choose_target)
            else:
                label(row, text).pack(side="left")
            label(row, textvariable=value, color=MUTED, size=9).pack(side="right")
        row = tk.Frame(device, bg=CARD)
        row.pack(fill="x", pady=(12, 0))
        a.listen_button = button(row, "停止监听", a.stop_listening)
        self._direction_card = style.RoundedCard(self.host, padx=24,pady=24)
        label(self._direction_card,'方向与归中',size=12,bold=True).pack(anchor='w',pady=(0,16))
        row = tk.Frame(self._direction_card,bg=CARD)
        row.pack(fill='x')
        a.sensor_calibrate_button = button(row, "立即归中", a.calibrate_sensor_neutral)
        a.sensor_calibrate_button.pack(side="left", padx=8)
        button(row, "方向引导…", a.open_mount_calibration).pack(side="left")

        sending = self._sending_card = style.RoundedCard(self.host, padx=24, pady=24)
        label(sending, "发送控制", size=12, bold=True).pack(anchor="w", pady=(0,12))
        a.arm_check = style.Switch(sending, text=f"允许挥动后向 {a.settings.codex.target_app} 发送消息",
                                    variable=a.arm_value, command=a.toggle_arm,
                                    bg=CARD, activebackground=CARD, fg=TEXT, selectcolor=CARD,
                                    font=(FONT, 10), cursor="hand2", takefocus=True)
        a.arm_check.pack(anchor="w")
        label(sending, "每次启动默认关闭；目标不明确或有未发送草稿时会拒绝发送。",
              color=MUTED, size=9, wraplength=650, justify="left").pack(anchor="w", pady=(6, 0))

        speech_disclosure = self._speech_panel = Disclosure(self.host, "下一鞭的语音文字", bg=BG)
        speech = style.RoundedCard(speech_disclosure.body, padx=20, pady=16)
        speech.pack(fill="x")
        label(speech, textvariable=a.voice_status_value, color=BLUE, size=9).pack(anchor="w")
        a.voice_text = tk.Text(speech, height=3, bg=BG, fg=TEXT, insertbackground=TEXT,
                               relief="flat", padx=10, pady=10, wrap="word", font=(FONT, 10))
        a.voice_text.pack(fill="x", pady=9)
        row = tk.Frame(speech, bg=CARD)
        row.pack(fill="x")
        button(row, "保存编辑", a.save_voice_pending).pack(side="left")
        button(row, "清除候选", a.clear_voice_pending).pack(side="left", padx=8)

        experience = self._experience_card = style.RoundedCard(self.host, padx=24, pady=24)
        label(experience, "外观与反馈", size=12, bold=True).pack(anchor="w", pady=(0,12))
        self.reduce_motion = tk.BooleanVar(master=self.root, value=self.preferences.reduce_motion)
        style.Switch(experience, text="减少主界面动态效果", variable=self.reduce_motion,
                       command=self.set_reduced_motion, bg=CARD, fg=TEXT, selectcolor=CARD,
                       activebackground=CARD, font=(FONT, 10)).pack(anchor="w")
        self.wounds_enabled = tk.BooleanVar(master=self.root, value=a.visual_store.settings.wounds_enabled)
        self.sound_enabled = tk.BooleanVar(master=self.root, value=a.visual_store.settings.sound_enabled)
        def save_feedback():
            from dataclasses import replace
            value = replace(a.visual_store.settings, wounds_enabled=self.wounds_enabled.get(),
                            sound_enabled=self.sound_enabled.get())
            if not a.apply_visual_settings(value):
                self.wounds_enabled.set(a.visual_store.settings.wounds_enabled)
                self.sound_enabled.set(a.visual_store.settings.sound_enabled)
        for text, variable in (("显示 PCB 伤口", self.wounds_enabled), ("鞭子音效", self.sound_enabled)):
            style.Switch(experience, text=text, variable=variable, command=save_feedback,
                         bg=CARD, fg=TEXT, selectcolor=CARD, activebackground=CARD,
                         font=(FONT, 10)).pack(anchor="w", pady=(12,0))


        from .disclosure import Disclosure
        self.diagnostics = Disclosure(self.host, "帮助与诊断", bg=BG)

        diagnostics = self.diagnostics.body
        button(diagnostics, "重新查看入门引导", self.restart_setup).pack(anchor="w", pady=(0,12))
        label(diagnostics, textvariable=a.last_event_value, color=MUTED, size=9,
              wraplength=630, justify="left").pack(anchor="w")
        row = tk.Frame(diagnostics, bg=CARD)
        row.pack(fill="x", pady=9)
        a.test_button = button(row, "模拟挥动", a.simulate_whip)
        a.test_button.pack(side="left")
        a.check_button = button(row, f"重新检查 {a.settings.codex.target_app}", a.check_codex)
        a.check_button.pack(side="left", padx=8)
        a.log_text = tk.Text(diagnostics, height=8, bg=BG, fg=MUTED, wrap="word",
                             relief="flat", padx=10, pady=10, font=("Consolas", 9), state="disabled")
        a.log_text.pack(fill="x")
        def scroll_general(event):
            if event.widget.winfo_class() not in {"Text", "Scale", "Listbox"}:
                target = advanced_canvas
                target.yview_scroll(-int(event.delta / 120), "units")
        self.settings.bind("<MouseWheel>", scroll_general, add="+")
        style.restyle_fields(self.settings)

    def open_preferences(self, section="general"):
        section = {'messages':'input','voice':'input','detector':'calibration','visual':'general'}.get(section,section)
        if self.stage == "learning" and section != 'calibration':
            messagebox.showinfo("正在录入动作", "请先完成或跳过当前动作录入，再打开设置。", parent=self.root)
            return
        self._advanced_section = section
        self.general.pack_forget()
        self._advanced_panel.pack_forget()
        for panel in (self._general_body,self._direction_card,self._speech_panel,
                      self._sending_card,self._experience_card,self.diagnostics):
            panel.pack_forget()
        self.app._ensure_settings()
        window = self.app.settings_window
        window.section_router = self.open_preferences
        self._advanced_panel.pack(fill="both", expand=True)
        window.show_group(section)
        if section == 'general':
            self._general_body.pack(in_=window._content,fill='x',before=window._visual_panel)
            self._experience_card.pack(in_=window._content,fill='x',pady=(0,16),before=window._visual_panel)
            self.diagnostics.pack(in_=window._content,fill='x',pady=(0,16))
        elif section == 'calibration':
            self._direction_card.pack(in_=window._content,fill='x',pady=(0,16),before=window._detector_panel)
        else:
            self._sending_card.pack(in_=window._content,fill='x',pady=(0,16),before=window._messages_panel)
            self._speech_panel.pack(in_=window._content,fill='x',pady=(0,16),before=window._voice_panel)
        for panel in (self._general_body,self._direction_card,self._speech_panel,
                      self._sending_card,self._experience_card,self.diagnostics):
            panel.lift()
        self.settings.update_idletasks()
        self._resize_advanced()
        self._advanced_canvas.yview_moveto(0)
        for key, b in self.nav.items():
            from . import settings_style as style
            b.configure(bg=style.SELECTED if key == section else style.SIDEBAR,
                        fg=style.BLUE if key == section else style.TEXT)
        if hasattr(self.app.effects, "set_settings_open"):
            self.app.effects.set_settings_open(True)
        self.settings.deiconify()
        self.settings.lift()

    def hide_preferences(self):
        self.settings.withdraw()
        if hasattr(self.app.effects, "set_settings_open"):
            self.app.effects.set_settings_open(False)
        if self.stage != "learning":
            self._close_advanced()

    def _close_advanced(self):
        window = self.app.settings_window
        if window is not None and window.window.winfo_exists():
            window.close()
        self.app.settings_window = None

    def _persist(self):
        try:
            self.preferences.save(self.path)
            return True
        except OSError as exc:
            messagebox.showerror("无法保存界面偏好", str(exc), parent=self.root)
            return False

    def set_reduced_motion(self):
        self.preferences.reduce_motion = self.reduce_motion.get()
        self.hero.reduce_motion = self.preferences.reduce_motion
        self.hero._wake()
        self._persist()

    def restart_setup(self):
        self.hide_preferences()
        self.app.armed.clear()
        self.app._arm_generation += 1
        self.app.arm_value.set(False)
        self.app.mode_value.set("安全监听")
        self.stage = "connect"
        self._render_key = None

    def skip_setup(self):
        previous = self.preferences.setup_complete
        self.preferences.setup_complete = True
        if not self._persist():
            self.preferences.setup_complete = previous
            return
        self._learning_queue = []
        self._learning_kind = ''
        self._close_advanced()  # Cancels unfinished capture; never saves a draft.
        self.app.armed.clear()
        self.app._arm_generation += 1
        self.app.arm_value.set(False)
        self.app.mode_value.set('安全监听')
        self.stage = 'ready'
        self._render_key = None

    def advance(self):
        if self.stage in {"connect", "calibrate"}:
            if self.app.ble_connected and time.monotonic() - self._sensor_at < 1.5:
                self.app.open_mount_calibration()
        elif self.stage == "choices":
            self._learning_queue = (["whip"] if self.whip_choice.get() else []) + (["tap"] if self.tap_choice.get() else [])
            self._next_learning()
        elif self.stage == "learning":
            if not self.app.ble_connected:
                return
            window = self.app.settings_window
            if self._learning_kind == "whip":
                if window._stage == "positive_done":
                    window.skip_negatives()
                    if window._stage == "complete":
                        if not window._use_raw_v3:
                            window.save_and_apply()
                            if not window.apply_status.get().startswith("已保存"):
                                return
                        self._next_learning()
                elif window._stage == "positive":
                    window.request_record()
                else:
                    window.start_positive_learning()
            elif self._tap_done:
                self._next_learning()
            elif window._voice_calibrating:
                self.open_preferences('calibration')
            else:
                if self.app.voice_store.settings.enabled or self.app.apply_voice_settings(
                        replace(self.app.voice_store.settings, enabled=True)):
                    window._begin_voice_calibration()
        self._render_key = None

    def _next_learning(self):
        self._close_advanced()
        if not self._learning_queue:
            self.preferences.setup_complete = True
            self.stage = "choices"
            if self._persist():
                self.stage = "ready"
                self._notice = "准备好了。发送权限可在设置中开启。"
                self._notice_until = time.monotonic() + 8
            return
        self._learning_kind = self._learning_queue.pop(0)
        self.stage = "learning"
        self._tap_count, self._tap_done = 0, False
        self.app._ensure_settings()
        window = self.app.settings_window
        if self._learning_kind == "whip":
            window.start_positive_learning()
        else:
            if not self.app.voice_store.settings.enabled:
                # The choice explicitly says this enables the optional voice module.
                if not self.app.apply_voice_settings(replace(self.app.voice_store.settings, enabled=True)):
                    return
            window._begin_voice_calibration()
            self.open_preferences('calibration')
        self._render_key = None

    def skip_learning(self):
        if self.stage == "choices":
            self._learning_queue = []
        self._next_learning()  # Does not restore defaults or erase a saved profile.
        self._render_key = None

    def observe(self, kind, payload):
        if kind == "battery":
            self._battery_percent = int(payload["percent"])
            self._battery_charging = bool(payload["charging"])
            self.battery_indicator.set_status(
                self._battery_percent, self._battery_charging
            )
        elif kind == "sensor_pose":
            self._sensor_at = time.monotonic()
            self.hero.pose(payload.offset_x, payload.offset_y)
        elif kind == "whip":
            self.hero.strike()
            self._notice, self._notice_until = "收到一鞭", time.monotonic() + 1.3
        elif kind == "mount_closed" and payload.get("saved"):
            if self.stage != "ready" and load_mounting_profile(self.app.mounting_path) is not None:
                self.stage = "choices"
                self._render_key = None
        elif kind == "voice_state":
            self._voice_state = str(payload.get("state", ""))
            if self._voice_state == "empty":
                self._voice_state = ""
                self._notice, self._notice_until = "没听清，再敲两下试试", time.monotonic() + 3
            elif self._voice_state in {"recording", "recognizing", "ready"}:
                self._notice_until = 0.0
        elif kind == "ui_audio_level":
            self.hero.audio_level(float(payload))
        elif kind == "voice_pending":
            self._pending = str(payload or "")
            self._pending_until = time.monotonic()+10. if self._pending else 0.
            if not self._pending and self._voice_state == "ready":
                self._voice_state = ""
        elif kind in {"voice_error", "voice_model_error", "send_error"}:
            self._voice_state = "" if kind != "send_error" else self._voice_state
            self._notice = ("发送已暂停，请在设置中查看原因" if kind == "send_error"
                            else "录音没完成，再敲两下试试；详情见设置")
            self._notice_until = time.monotonic() + (10 if kind == "send_error" else 3)
        elif kind == "voice_calibration":
            self._tap_count = int(payload.get("done", 0))
        elif kind in {"voice_calibration_done", "tap_calibration_saved"}:
            self._tap_count, self._tap_done = 5, True
        elif kind == "ble" and payload != "connected":
            self._sensor_at = -100.0
            self._voice_state = ""
            self._battery_percent = None
            self._battery_charging = False
            self.battery_indicator.set_status(None)
        elif kind == "worker_stopped":
            self._sensor_at = -100.0
            self._voice_state = ""

    def _clock_title_changed(self, active):
        if self.stage == 'ready' and self.hero.mode == 'whip' and not self._pending and self._voice_state not in {'recording','recognizing'}:
            self.title.configure(text="Don't waste time on AI" if active else 'just beat it')

    def _refresh(self):
        if self._closed:
            return
        a = self.app
        a.voice_module.expire_pending()
        if self._pending and time.monotonic() >= self._pending_until:
            self.observe('voice_pending', None)
        connected = a.ble_connected and self.app.worker_loop is not None
        fresh = connected and time.monotonic() - self._sensor_at < 1.5
        if self.stage in {"connect", "calibrate"}:
            self.stage = "calibrate" if fresh else "connect"
        notice = self._notice if time.monotonic() < self._notice_until else ""
        title, subtitle, step, primary, progress = "", "", "", "", ""
        mode = "whip"
        enabled = True
        if self.stage == "connect":
            step, title = "01  /  03 · 连接", "先连接你的手柄"
            subtitle = "给手柄通电，并打开电脑蓝牙。\n连接成功后，我们一起确认握持方向。"
            if connected:
                subtitle = "手柄已连接，正在等待姿态数据。\n若长时间没有变化，请在设置中检查固件与连接。"
            primary, enabled = "等待连接…", False
        elif self.stage == "calibrate":
            step, title = "02  /  03 · 方向", "用你舒服的方式握住"
            subtitle = "跟着提示向右转、向上抬，就能认识手柄的方向。\n允许轻微手抖，不用保持完全静止。"
            primary = "开始方向校准"
        elif self.stage == "choices":
            step, title = "03  /  03 · 可选", "让它更懂你的动作"
            subtitle = "可以现在录入，也可以以后再做。\n未录入使用默认参数；已有学习数据会保留。"
            primary = "继续"
        elif self.stage == "learning":
            window = a.settings_window
            if window is not None:
                if self._learning_kind == "whip":
                    samples = window._positive_motion if window._use_raw_v3 else window._positive_samples
                    step, title = f"动作录入 · {len(samples):02d} / 15", "挥动一下，再点录入"
                    subtitle = "按平时的力度挥鞭一次，随即点击下方按钮。"
                    progress = window.learning_status.get()
                    primary = ("保存并继续" if window._stage == "positive_done"
                               else "录入刚刚动作" if window._stage == "positive" else "开始录入")
                    enabled = connected and not window._awaiting_record
                else:
                    mode = "voice_ready"
                    step, title = "双敲力度", "设定允许的冲击范围"
                    subtitle = "可直接拖动最轻和最重力度，也可以双敲一次自动设置。"
                    progress = window.tap_range_status.get()
                    primary = "完成并继续" if self._tap_done else "打开力度设置"
                    if not self._tap_done and not window._voice_calibrating:
                        primary = "双敲一次自动设置"
                    enabled = connected
            if not connected:
                progress = "连接已断开。重新连接后可以继续，已录入的样本仍在。"
        else:
            title = "Don't waste time on AI" if self.hero._clock_hover else "just beat it"
            subtitle = "" if connected else "等待手柄连接"
            if notice:
                subtitle = notice
            if self._voice_state == "recording":
                mode, title, subtitle = "recording", "recording", ""
            elif self._voice_state == "recognizing":
                mode, title, subtitle = "recognizing", "recognizing voice", ""
            elif self._pending:
                mode, title = "whip", self._pending
                subtitle = "beat it, then send"
        if not connected:
            mode = 'connecting'
            title = 'Connecting'
        if self.stage == 'ready' and not (connected and self._pending and self._voice_state not in {'recording','recognizing'}):
            subtitle = ''
        key = (self.stage, title, subtitle, step, primary, progress, enabled,
               a.ble_value.get(), a.mode_value.get(), mode, self._pending)
        if key != self._render_key:
            old_stage = self._render_key[0] if self._render_key else None
            self._render_key = key
            if self.stage != "ready":
                a.arm_check.configure(state="disabled")
            elif old_stage != "ready":
                a.arm_check.configure(state="normal")
            self.title.configure(text=title)
            self.subtitle.configure(text=subtitle)
            self.subtitle.set_countdown(self._pending_until if subtitle == 'beat it, then send' else None)
            self.step_label.configure(text=step)
            self.hero.clock_enabled = self.stage == "ready" and connected and not self._pending and mode == 'whip'
            self.hero.set_mode(mode)
            # Reserve room for first-run choices/actions at the minimum window
            # size. The hero yields space before any primary action can clip.
            self.hero.configure(height=(150 if self.stage == "choices" else
                                        175 if self.stage == "learning" else
                                        210 if self.stage != "ready" else
                                        275))
            self.primary.configure(text=primary, state="normal" if enabled else "disabled")
            self.progress.configure(text=progress)
            if self.stage == "choices":
                self.choices.pack(before=self.progress, pady=(15, 4))
            else:
                self.choices.pack_forget()
            if self.stage == "ready":
                self.actions.pack_forget()
                self.skip_setup_button.pack_forget()
            else:
                self.actions.pack(before=self.progress, pady=(16, 0))
                self.skip_setup_button.pack(side='left', padx=(0,8))
            if self.stage in {"choices", "learning"}:
                self.skip.configure(text="跳过这组" if self.stage == "learning" else "以后再录入")
                self.skip.pack(side="left", padx=(0, 12))
            else:
                self.skip.pack_forget()
            if self.stage == "ready" and self._pending:
                self.pending.configure(text=self._pending)
                self.pending.pack_forget()
            else:
                self.pending.pack_forget()
        sync_presentation = getattr(getattr(a, 'effects', None), 'set_presentation', None)
        if sync_presentation:
            sync_presentation(mode=mode, title=title, subtitle=subtitle,
                              deadline=self._pending_until if self._pending else None,
                              clock_enabled=self.hero.clock_enabled,
                              reduce_motion=self.hero.reduce_motion,
                              level=self.hero._level)
        self._timer = self.root.after(100, self._refresh)

    def close(self):
        self._closed = True
        self.hero.close()
        if self._timer is not None:
            try:
                self.root.after_cancel(self._timer)
            except tk.TclError:
                pass
        if self.settings.winfo_exists():
            self.settings.destroy()
