"""Small native, keyboard-operable direction onboarding wizard."""
from __future__ import annotations

import tkinter as tk
import uuid
from typing import Callable
from PIL import Image, ImageTk
from .interface import asset_path, button, FONT

from .sensor_pose import GripStability, SensorPose


class MountCalibrationWindow:
    BG, CARD, TEXT, MUTED = "#F5F5F7", "#FFFFFF", "#1D1D1F", "#68686F"
    ACCENT, ERROR = "#0066CC", "#BC3434"
    STEPS = {
        "neutral": (1, "舒服地握住手柄", "像平时使用一样，把手柄大致水平指向屏幕。\n轻微手抖没关系，也不用分清开发板正反面。", "记录这个姿势", "neutral"),
        "right_ready": (2, "接下来，向右转", "先让手柄朝向屏幕。点击开始后，\n自然地向右转手腕，大约 20–40°。", "开始向右转", "begin"),
        "right_capture": (2, "向右转，然后录入", "转到舒服的位置后，点击录入。\n允许伴随上下晃动；录入前先别转回来。", "录入刚才动作", "finish"),
        "up_ready": (3, "再试试向上抬", "回到舒服、朝向屏幕的握姿。点击开始后，\n向上抬手腕，大约 20–40°。", "开始向上抬", "begin"),
        "up_capture": (3, "向上抬，然后录入", "抬到舒服的位置后，点击录入。\n允许伴随左右转动；录入前先别放下来。", "录入刚才动作", "finish"),
        "review": (4, "看看方向对不对", "恢复平时的握姿，先归中，再左右转、上下抬。\n圆点和你同向移动，就可以保存。", "方向正确，保存", "save"),
    }

    def __init__(self, root: tk.Tk, send: Callable[[str, str], None],
                 dismissed: Callable[[], None]) -> None:
        self.token = uuid.uuid4().hex
        self._send, self._dismissed = send, dismissed
        self.stage = "neutral"
        self._action = "neutral"
        self._closed = False
        self.window = tk.Toplevel(root)
        self.window.title("认识你的握持方向 · Codex 鞭子")
        self.window.geometry("560x710")
        self.window.minsize(520, 680)
        self.window.configure(bg=self.BG)
        self.window.transient(root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _e: self.close())
        with Image.open(asset_path("whip.png")) as image:
            self._illustration = ImageTk.PhotoImage(
                image.convert("RGBA").resize((210, 210), Image.Resampling.LANCZOS),
                master=self.window)
        outer = tk.Frame(self.window, bg=self.BG, padx=32, pady=24)
        outer.pack(fill="both", expand=True)
        self.step_label = tk.Label(outer, bg=self.BG, fg=self.ACCENT,
                                  font=(FONT, 10))
        self.step_label.pack(pady=(8, 14))
        self.title_label = tk.Label(outer, bg=self.BG, fg=self.TEXT,
                                   font=(FONT, 23, "bold"))
        self.title_label.pack(pady=(0, 6))
        self.preview = tk.Canvas(outer, height=240, bg=self.BG, highlightthickness=0)
        self.preview.pack(fill="both", expand=True, pady=12)
        self.preview.bind("<Configure>", lambda _e: self.show_pose(SensorPose(0, 0, 0, 0)))
        self.instructions = tk.Label(outer, bg=self.BG, fg=self.TEXT, justify="center",
                                     wraplength=450, font=(FONT, 11))
        self.instructions.pack(fill="x", pady=(0, 14))
        self.stability_label = tk.Label(outer, text="正在等待手柄数据…", bg=self.BG,
                                       fg=self.MUTED, justify="center", wraplength=450,
                                       font=(FONT, 9))
        self.stability_label.pack(fill="x", pady=(0, 10))
        self.status = tk.StringVar(master=self.window, value="")
        self.status_label = tk.Label(outer, textvariable=self.status, bg=self.BG,
                                    fg=self.ERROR, justify="center", wraplength=450,
                                    font=(FONT, 9))
        self.status_label.pack(fill="x", pady=(0, 10))
        self.controls = tk.Frame(outer, bg=self.BG, pady=4)
        self.controls.pack(fill="x")
        self.primary = self._button(self.controls, "记录这个姿势", self.advance, primary=True)
        self.primary.pack(side="right")
        self.retry_button = self._button(self.controls, "重录这一步", lambda: self.command("retry"))
        self.center_button = self._button(self.controls, "再次归中", lambda: self.command("center"))
        footer = tk.Frame(outer, bg=self.BG)
        footer.pack(fill="x", pady=(14, 5))
        self._button(footer, "从头开始", lambda: self.command("restart")).pack(side="left")
        self._button(footer, "稍后继续", self.close).pack(side="right")
        tk.Label(outer, text="校准时暂停发送 · 已有动作与语音学习不会被改动",
                 bg=self.BG, fg=self.MUTED, font=(FONT, 8)).pack(pady=(12, 0))
        self.update_state({"stage": "neutral", "detail": "正在连接校准服务…"})
        self.primary.configure(state="disabled")
        self.window.after_idle(self.primary.focus_set)

    def _button(self, parent, label, action, *, primary=False):
        return button(parent, label, action, primary=primary)

    def advance(self) -> None:
        self.command(self._action)

    def command(self, action: str) -> None:
        self.primary.configure(state="disabled")
        self._send(action, self.token)

    def update_state(self, state: dict) -> None:
        if self._closed:
            return
        self.stage = state["stage"]
        number, title, help_text, button, self._action = self.STEPS[self.stage]
        self.step_label.configure(text=f"步骤 {number} / 4  ·  手柄方向学习")
        self.title_label.configure(text=title)
        self.instructions.configure(text=help_text)
        self.primary.configure(text=button, state="normal")
        self.retry_button.pack_forget()
        self.center_button.pack_forget()
        if self.stage.endswith("_capture"):
            self.retry_button.pack(side="left")
        if self.stage == "review":
            if not state.get("centered", False):
                self._action = "center"
                self.primary.configure(text="归中并试方向")
            else:
                self.center_button.pack(side="left")
        self.status.set(state.get("detail", "") if state.get("error") else "")
        self.status_label.configure(fg=self.ERROR if state.get("error") else self.MUTED)
        self.show_pose(SensorPose(0, 0, 0, 0))

    def show_pose(self, pose: SensorPose) -> None:
        if self._closed:
            return
        c = self.preview
        w, h = max(100, c.winfo_width()), max(100, c.winfo_height())
        c.delete("all")
        if self.stage != "review":
            c.create_image(w / 2 - 25, h / 2, image=self._illustration)
            if self.stage != "neutral":
                arrow = "→" if self.stage.startswith("right") else "↑"
                c.create_text(w / 2 + 112, h / 2, text=arrow, fill=self.ACCENT,
                              font=(FONT, 38))
            return
        c.create_line(w/2, 20, w/2, h-20, fill="#DEDEE3")
        c.create_line(30, h/2, w-30, h/2, fill="#DEDEE3")
        c.create_text(22, h/2, text="左", fill=self.MUTED)
        c.create_text(w-22, h/2, text="右", fill=self.MUTED)
        c.create_text(w/2, 12, text="上", fill=self.MUTED)
        c.create_text(w/2, h-12, text="下", fill=self.MUTED)
        x = w/2 + pose.offset_x / 190 * (w/2-45)
        y = h/2 - pose.offset_y / 130 * (h/2-30)
        c.create_oval(x-7, y-7, x+7, y+7, fill=self.ACCENT, outline="")

    def show_angle(self, angle: float) -> None:
        if self.stage.endswith("_capture"):
            self.preview.delete("angle")
            self.preview.create_text(max(100, self.preview.winfo_width())/2,
                                     max(100, self.preview.winfo_height()) - 15,
                                     text=f"{angle:.0f}° · 建议 20–40°",
                                     fill=self.ACCENT, tags="angle", font=(FONT, 10))

    def show_stability(self, stability: GripStability) -> None:
        if not self._closed:
            text = stability.detail
            if self.stage.endswith("_capture") and stability.ready:
                text = "正在采集 · 没有倒计时，动作完成就能录入。"
            self.stability_label.configure(text=text,
                                           fg="#237C4B" if stability.ready else self.MUTED)

    def close(self, *, notify=True) -> None:
        if self._closed:
            return
        self._closed = True
        if notify:
            self._send("cancel", self.token)
        self.window.destroy()
        self._dismissed()
