from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, fields, replace
from tkinter import messagebox, ttk
from typing import Callable
from .disclosure import Disclosure
from . import settings_style as style
from .tick_slider import TickSlider
from .whip_sensitivity import WhipSensitivity, scaled_profile

from .calibration import (
    NEGATIVE_SAMPLE_COUNT,
    POSITIVE_SAMPLE_COUNT,
    DetectorProfile,
    LearningSample,
    learn_profile,
    profile_score,
    sample_averages,
)
from .motion_v3 import MotionEngine, MotionTemplate
from .messages import MessageProfile, MessageProfileStore, reorder_messages
from .voice import (
    MAX_HARDWARE_TAP_G,
    MIN_HARDWARE_TAP_G,
    VoiceSettings,
    VoiceSettingsStore,
)
from .visual_settings import (
    MAX_SCARE_BLACKOUT_MS,
    MAX_SCARE_EYES_MS,
    MAX_STRIKES_PER_WOUND,
    MIN_SCARE_BLACKOUT_MS,
    MIN_SCARE_EYES_MS,
    MIN_STRIKES_PER_WOUND,
    VisualSettings,
    VisualSettingsStore,
)


SendCommand = Callable[[str], bool]
ApplyProfile = Callable[[DetectorProfile], bool]
ApplyVoiceSettings = Callable[[VoiceSettings], bool]
ApplyVisualSettings = Callable[[VisualSettings], bool]
StartVoiceCalibration = Callable[[], bool]
RecordVoiceCalibration = Callable[[], bool]
CancelVoiceCalibration = Callable[[], None]


@dataclass(frozen=True, slots=True)
class ThresholdField:
    name: str
    label: str
    unit: str
    digits: int


THRESHOLD_FIELDS = (
    ThresholdField("start_gyro_dps", "启动角速度", "dps", 1),
    ThresholdField("start_dynamic_accel_g", "启动动态加速度", "g", 3),
    ThresholdField("confirm_gyro_dps", "确认角速度", "dps", 1),
    ThresholdField("confirm_dynamic_accel_g", "确认动态加速度", "g", 3),
    ThresholdField("hard_dynamic_accel_g", "冲击加速度", "g", 3),
    ThresholdField("hard_accel_min_gyro_dps", "冲击最低角速度", "dps", 1),
    ThresholdField("confirm_samples", "连续确认帧数", "帧", 0),
    ThresholdField("minimum_angular_travel_deg", "最小角位移", "°", 1),
    ThresholdField("minimum_direction_consistency", "方向一致性", "0–1", 3),
    ThresholdField("minimum_dominant_axis_ratio", "主轴集中度", "0–1", 3),
    ThresholdField("maximum_peak_gap_ms", "峰值最大间隔", "ms", 0),
    ThresholdField("minimum_duration_ms", "动作最短时间", "ms", 0),
    ThresholdField("maximum_event_ms", "动作最长时间", "ms", 0),
    ThresholdField("cooldown_ms", "触发冷却时间", "ms", 0),
)


class DetectorSettingsWindow:
    BG, CARD, CARD_ALT = style.BG, style.CARD, style.FIELD
    TEXT, MUTED, LINE = style.TEXT, style.MUTED, style.LINE
    ACCENT, GREEN, RED, BLUE = style.BLUE, style.BLUE, style.RED, style.BLUE

    def __init__(
        self,
        root: tk.Tk,
        profile: DetectorProfile,
        send_command: SendCommand,
        apply_profile: ApplyProfile,
        motion_engine: MotionEngine | None = None,
        raw_supported: bool = False,
        message_store: MessageProfileStore | None = None,
        visual_store: VisualSettingsStore | None = None,
        apply_visual_settings: ApplyVisualSettings | None = None,
        voice_store: VoiceSettingsStore | None = None,
        apply_voice_settings: ApplyVoiceSettings | None = None,
        start_voice_calibration: StartVoiceCalibration | None = None,
        record_voice_calibration: RecordVoiceCalibration | None = None,
        cancel_voice_calibration: CancelVoiceCalibration | None = None,
        voice_model_ready: Callable[[], bool] | None = None,
        voice_tap_model_ready: Callable[[], bool] | None = None,
        calibrate_mounting: Callable[[], None] | None = None,
        embedded_parent: tk.Frame | None = None,
        save_tap_calibration: Callable[[], bool] | None = None,
        set_tap_interval: Callable[[int], None] | None = None,
        power_enabled: bool = False,
        apply_power_settings: Callable[[bool], bool] | None = None,
        power_status: str = "连接手柄后同步",
    ) -> None:
        self._send_command = send_command
        self._apply_profile = apply_profile
        self._sensitivity = WhipSensitivity(profile)
        self._positive_samples: list[LearningSample] = []
        self._negative_samples: list[LearningSample] = []
        self._motion_engine = motion_engine
        self._use_raw_v3 = raw_supported and motion_engine is not None
        self._positive_motion: list[MotionTemplate] = []
        self._negative_motion: list[MotionTemplate] = []
        self._stage = "idle"
        self._awaiting_record = False
        self._record_timeout_after: str | None = None
        self._variables: dict[str, tk.StringVar] = {}
        self._message_store = message_store
        self._message_values = list(
            message_store.profile.messages if message_store is not None else ()
        )
        self._message_widgets: list[tk.Text] = []
        self._message_cards: list[tk.Frame] = []
        self._message_drag_index: int | None = None
        self._section_buttons: dict[str, tk.Button] = {}
        self._visual_store = visual_store
        self._apply_visual_settings = apply_visual_settings
        self._voice_store = voice_store
        self._apply_voice_settings = apply_voice_settings
        self._start_voice_calibration = start_voice_calibration
        self._record_voice_calibration = record_voice_calibration
        self._cancel_voice_calibration = cancel_voice_calibration
        self._voice_calibrating = False
        self._save_tap_calibration = save_tap_calibration
        self._set_tap_interval = set_tap_interval
        self._tap_stage = 'idle'
        self._voice_model_ready = voice_model_ready or (lambda: False)
        self._voice_tap_model_ready = voice_tap_model_ready or (lambda: False)
        self._voice_variables: dict[str, tk.StringVar] = {}
        self._calibrate_mounting = calibrate_mounting
        self._apply_power_settings = apply_power_settings
        self._initial_power_enabled = power_enabled
        self._initial_power_status = power_status

        self._embedded = embedded_parent is not None
        self.window = tk.Frame(embedded_parent) if self._embedded else tk.Toplevel(root)
        if self._embedded:
            self.window.pack(fill="both", expand=True)
        else:
            self.window.title("设置 · Codex 鞭子")
            self.window.geometry("920x820")
            self.window.minsize(840, 740)
        self.window.configure(bg=self.BG)
        if not self._embedded:
            self.window.transient(root)
            self.window.protocol("WM_DELETE_WINDOW", self.close)
            self.window.bind("<Escape>", lambda _event: self.close())

        self._build(profile)
        style.restyle_fields(self.window)
        self.window.after_idle(self.window.focus_set)

    def _build(self, profile: DetectorProfile) -> None:
        shell = tk.Frame(self.window, bg=self.BG, padx=32, pady=28)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=self.BG)
        header.pack(fill="x", pady=(0, 16))
        self.page_title = tk.Label(
            header,
            text="设置",
            bg=self.BG,
            fg=self.TEXT,
            font=("Microsoft YaHei UI", 20, "bold"),
        )
        self.page_title.pack(anchor="w")
        self.page_description = tk.Label(
            header,
            text="按你的习惯调整；不需要修改的项目保持默认即可。",
            bg=self.BG,
            fg=self.MUTED,
            font=("Microsoft YaHei UI", 9),
        )
        # The product page is intentionally self-explanatory. Detailed status
        # and raw values live only in the hidden developer view.

        navigation = tk.Frame(shell, bg=self.BG)
        navigation.pack(fill="x", pady=(0, 14))
        if self._embedded:
            navigation.pack_forget()
        if self._calibrate_mounting is not None:
            self._button(navigation, "手柄方向校准", self._calibrate_mounting,
                         self.ACCENT, self.BG).pack(side="right")
        if self._message_store is not None:
            sections = [("messages", "发送消息"), ("detector", "动作检测")]
            if self._voice_store is not None:
                sections.insert(1, ("voice", "语音输入"))
                sections.insert(1, ("calibration", "校准"))
            if self._visual_store is not None:
                sections.insert(-1, ("visual", "视觉效果"))
            for key, label in sections:
                button = self._button(
                    navigation,
                    label,
                    lambda section=key: self._select_section(section),
                    self.CARD_ALT,
                    self.MUTED,
                )
                button.pack(side="left", padx=(0, 8))
                self._section_buttons[key] = button

        self._content = tk.Frame(shell, bg=self.BG)
        self._content.pack(fill="both", expand=True)
        self._messages_panel = tk.Frame(self._content, bg=self.BG)
        self._voice_panel = tk.Frame(self._content, bg=self.BG)
        self._visual_panel = tk.Frame(self._content, bg=self.BG)
        self._detector_panel = tk.Frame(self._content, bg=self.BG)
        self._calibration_panel = tk.Frame(self._content, bg=self.BG)
        self._power_panel = tk.Frame(self._content, bg=self.BG)
        self._build_power_panel(self._power_panel)
        self._build_calibration_panel(self._calibration_panel)
        if self._message_store is not None:
            self._build_messages_panel(self._messages_panel)
        if self._voice_store is not None:
            self._build_voice_panel(self._voice_panel)
        if self._visual_store is not None:
            self._build_visual_panel(self._visual_panel)

        learning = self._card(self._detector_panel, padx=18, pady=15)
        learning.pack(fill="x", pady=(0, 14))
        top = tk.Frame(learning, bg=self.CARD)
        top.pack(fill="x")
        tk.Label(
            top,
            text="挥鞭识别",
            bg=self.CARD,
            fg=self.TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        self.stage_badge = tk.Label(
            top,
            text="未开始",
            bg=self.CARD_ALT,
            fg=self.MUTED,
            padx=10,
            pady=4,
            font=("Microsoft YaHei UI", 8, "bold"),
        )

        self.learning_status = tk.StringVar(
            value=(
                "已经记住你的挥鞭动作，也可以重新录入。"
                if self._use_raw_v3
                and self._motion_engine is not None
                and self._motion_engine.trained
                else "先挥鞭 15 次；推荐再做 5 次放到桌面动作，帮助排除当前误触发。"
            )
        )
        self._learning_status_label = tk.Label(
            learning,
            textvariable=self.learning_status,
            bg=self.CARD,
            fg=self.MUTED,
            justify="left",
            anchor="w",
            wraplength=790,
            font=("Microsoft YaHei UI", 9),
        )

        style = ttk.Style(self.window)
        style.configure(
            "Whip.Horizontal.TProgressbar",
            troughcolor=self.CARD_ALT,
            background=self.ACCENT,
            bordercolor=self.CARD_ALT,
            lightcolor=self.ACCENT,
            darkcolor=self.ACCENT,
        )
        progress_row = self._learning_progress_row = tk.Frame(learning, bg=self.CARD)
        self.progress = ttk.Progressbar(
            progress_row,
            style="Whip.Horizontal.TProgressbar",
            maximum=POSITIVE_SAMPLE_COUNT,
            value=0,
        )
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress_text = tk.StringVar(value=f"0 / {POSITIVE_SAMPLE_COUNT}")
        tk.Label(
            progress_row,
            textvariable=self.progress_text,
            width=8,
            bg=self.CARD,
            fg=self.TEXT,
            font=("Cascadia Mono", 9, "bold"),
        ).pack(side="right", padx=(12, 0))

        self.average_text = tk.StringVar(value="尚无样本")
        self._learning_average_label = tk.Label(
            learning,
            textvariable=self.average_text,
            bg=self.CARD,
            fg=self.BLUE,
            anchor="w",
            font=("Microsoft YaHei UI", 8),
        )

        learning_buttons = self._learning_buttons = tk.Frame(learning, bg=self.CARD)
        self.positive_button = self._button(
            learning_buttons,
            "开始校准",
            self.start_positive_learning,
            self.ACCENT,
            "#17120A",
        )
        self.positive_button.pack(side="left")
        self.record_button = self._button(
            learning_buttons,
            "录入刚刚动作",
            self.request_record,
            self.GREEN,
            "#07130D",
        )
        self.record_button.configure(state="disabled")
        self.record_button.pack(side="left", padx=(8, 0))
        self.negative_button = self._button(
            learning_buttons,
            "采集 5 次放桌动作",
            self.start_negative_learning,
            self.CARD_ALT,
            self.TEXT,
        )
        self.negative_button.configure(state="disabled")
        self.negative_button.pack(side="left", padx=(8, 0))
        self.skip_button = self._button(
            learning_buttons,
            "跳过反例",
            self.skip_negatives,
            self.CARD,
            self.MUTED,
        )
        self.skip_button.configure(state="disabled")
        self.skip_button.pack(side="left", padx=(8, 0))
        self.undo_button = self._button(
            learning_buttons,
            "撤销上一条",
            self.undo_sample,
            self.CARD,
            self.MUTED,
        )
        self.undo_button.configure(state="disabled")
        self.undo_button.pack(side="right")

        tuning = self._sensitivity_row = tk.Frame(learning, bg=self.CARD)
        tuning.pack(fill="x", pady=(12, 0))
        percent = (self._motion_engine.tolerance_percent
                   if self._motion_engine is not None and self._motion_engine.trained
                   else self._sensitivity.percent)
        self.tolerance_value = tk.DoubleVar(value=percent)
        self.sensitivity_label = tk.StringVar(value=f"挥鞭灵敏度：{percent:.0f}%")
        tk.Label(tuning, textvariable=self.sensitivity_label, bg=self.CARD,
                 fg=self.TEXT, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0,4))
        self.tolerance_scale = TickSlider(tuning, minimum=60, maximum=180,
            variable=self.tolerance_value, formatter=lambda value:f'{value:.0f}%',
            command=lambda value:self.sensitivity_label.set(f"挥鞭灵敏度：{value:.0f}%"), bg=self.CARD)
        self.tolerance_scale.pack(fill="x")
        self.tolerance_scale.bind("<ButtonRelease-1>", self._commit_tolerance, add="+")
        self.tolerance_scale.bind("<KeyRelease>", self._commit_tolerance, add="+")
        self.detector_advanced = Disclosure(self._detector_panel, "开发者参数 · 挥鞭检测", bg=self.BG)
        threshold = self._card(self.detector_advanced.body, padx=18, pady=15)
        threshold.pack(fill="x")
        if self._use_raw_v3 and self._motion_engine is not None:
            self._button(threshold, "清除学习模型…", self.clear_v3_profile,
                         self.CARD, self.RED).pack(anchor="e", pady=(0, 8))
        title_row = tk.Frame(threshold, bg=self.CARD)
        title_row.pack(fill="x", pady=(0, 10))
        tk.Label(
            title_row,
            text="检测阈值",
            bg=self.CARD,
            fg=self.TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            title_row,
            text="数值越高通常越难触发；时间上限类相反",
            bg=self.CARD,
            fg=self.MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")

        grid = tk.Frame(threshold, bg=self.CARD)
        grid.pack(fill="both", expand=True)
        for column in range(2):
            grid.grid_columnconfigure(column * 3 + 1, weight=1)

        for index, field in enumerate(THRESHOLD_FIELDS):
            block = index // 7
            row = index % 7
            base_column = block * 3
            tk.Label(
                grid,
                text=field.label,
                bg=self.CARD,
                fg=self.MUTED,
                anchor="w",
                font=("Microsoft YaHei UI", 8),
            ).grid(row=row, column=base_column, sticky="w", pady=4)
            variable = tk.StringVar()
            self._variables[field.name] = variable
            entry = tk.Entry(
                grid,
                textvariable=variable,
                bg=self.CARD_ALT,
                fg=self.TEXT,
                insertbackground=self.TEXT,
                relief="flat",
                highlightthickness=1,
                highlightbackground=self.LINE,
                highlightcolor=self.ACCENT,
                width=12,
                justify="right",
                font=("Cascadia Mono", 9),
            )
            entry.grid(
                row=row,
                column=base_column + 1,
                sticky="ew",
                padx=(8, 6 if block == 1 else 6),
                pady=4,
                ipady=5,
            )
            tk.Label(
                grid,
                text=field.unit,
                width=5,
                bg=self.CARD,
                fg=self.MUTED,
                anchor="w",
                font=("Microsoft YaHei UI", 8),
            ).grid(
                row=row,
                column=base_column + 2,
                sticky="w",
                padx=(0, 22 if block == 0 else 0),
            )

        self._populate(profile)
        footer = tk.Frame(threshold, bg=self.CARD)
        footer.pack(fill="x", pady=(13, 0))
        self.save_button = self._button(
            footer,
            "保存并应用到设备",
            self.save_and_apply,
            self.GREEN,
            "#07130D",
        )
        self.save_button.pack(side="right")
        self.default_button = self._button(
            footer,
            "恢复默认值",
            self.restore_defaults,
            self.CARD_ALT,
            self.TEXT,
        )
        self.default_button.pack(side="right", padx=(0, 8))
        self.apply_status = tk.StringVar(value="等待修改")
        tk.Label(
            footer,
            textvariable=self.apply_status,
            bg=self.CARD,
            fg=self.MUTED,
            anchor="w",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left", fill="x", expand=True)

        self._select_section("messages" if self._message_store is not None else "detector")

    def _select_section(self, section: str) -> None:
        if hasattr(self,'section_router'):
            return self.section_router(section)
        if section != 'calibration' and self._voice_calibrating:
            self._cancel_tap_calibration()
        title, description = {
            "messages": ("发送消息", "写下你想说的话，挥鞭时按你的偏好发送。"),
            "detector": ("动作学习", "让手柄认识你的挥动习惯，不必追求完全相同的动作。"),
            "voice": ("语音输入", "轻敲两下开始说话，把想法留给下一鞭。"),
            "calibration": ("校准", ""),
            "visual": ("外观与效果", "调整抽打留下的痕迹，以及快捷键触发的红眼效果。"),
        }.get(section, ("设置", "按你的习惯调整。"))
        self.page_title.configure(text=title)
        self.page_description.configure(text=description)
        for panel in (
            self._messages_panel,
            self._voice_panel,
            self._visual_panel,
            self._detector_panel,
            self._calibration_panel,
            self._power_panel,
        ):
            panel.pack_forget()
        selected = {
            "messages": self._messages_panel,
            "voice": self._voice_panel,
            "visual": self._visual_panel,
            "detector": self._detector_panel,
            "calibration": self._calibration_panel,
        }.get(section, self._detector_panel)
        selected.pack(fill="both", expand=True)
        if hasattr(self,'voice_feature_card'):
            self.voice_feature_card.pack_forget()
            if section == 'voice':
                self.voice_feature_card.pack(fill='x',before=selected,pady=(0,16))
        for key, button in self._section_buttons.items():
            active = key == section
            button.configure(
                bg=self.ACCENT if active else self.CARD_ALT,
                fg="#FFFFFF" if active else self.MUTED,
                activebackground=self.ACCENT if active else self.CARD_ALT,
                activeforeground="#FFFFFF" if active else self.TEXT,
            )

    def _sync_learning_controls(self):
        active = self._stage in {"positive", "negative"}
        self._learning_status_label.pack_forget()
        self._learning_average_label.pack_forget()
        self.stage_badge.pack_forget()
        if self._stage != "idle":
            self.stage_badge.pack(side="right")
            self._learning_status_label.pack(fill="x", before=self._sensitivity_row, pady=(18,12))
            self._learning_average_label.pack(fill="x", before=self._sensitivity_row, pady=(0,12))
        self.tolerance_scale.configure(state="disabled" if active else "normal")
        self._learning_progress_row.pack_forget()
        if active:
            if not self._learning_buttons.winfo_manager():
                self._learning_buttons.pack(fill="x", pady=(10, 0), before=self._sensitivity_row)
            self._learning_progress_row.pack(fill="x", before=self._learning_buttons)
        elif self._stage == "idle":
            self._learning_buttons.pack_forget()
        for button in (self.record_button,self.negative_button,self.skip_button,self.undo_button):
            button.pack_forget()
            if str(button["state"]) != "disabled":
                button.pack(side="left",padx=(8,0))

    def show_group(self, section):
        """Compatibility entry point; the product UI is now one page."""
        self.show_all()

    def show_all(self):
        """Show only the common-path controls on one continuous page."""
        if self._voice_calibrating:
            self._cancel_tap_calibration()
        self.page_title.configure(text='设置')
        self.page_description.pack_forget()
        panels = (self._power_panel,self._messages_panel,self._voice_panel,self._visual_panel,self._detector_panel,
                  self._calibration_panel,self.voice_feature_card)
        for panel in panels:
            panel.pack_forget()
        for panel in (self._messages_panel, self.voice_feature_card, self._voice_panel,
                      self._detector_panel, self._calibration_panel, self._visual_panel):
            panel.pack(fill='x',pady=(0,16))
        self.detector_advanced.pack_forget()
        self.voice_advanced.pack_forget()
        if hasattr(self, 'speech_service'):
            self.speech_service.card.pack_forget()
        self._sync_learning_controls()

    def show_developer_page(self):
        """Internal API for Codex/developers; never linked from product UI."""
        self.page_title.configure(text='开发者设置')
        self.page_description.configure(text='高级参数会直接影响识别与音频链路。')
        self.page_description.pack(anchor='w', pady=(6, 8))
        for panel in (self._power_panel,self._messages_panel,self._voice_panel,self._visual_panel,
                      self._detector_panel,self._calibration_panel,self.voice_feature_card):
            panel.pack_forget()
        self._detector_panel.pack(fill='x', pady=(0,16))
        self.detector_advanced.pack(fill='x', pady=(0,16))
        self._voice_panel.pack(fill='x', pady=(0,16))
        if hasattr(self, 'speech_service'):
            self.speech_service.card.pack(fill='x', pady=(0,16))
        self.voice_advanced.pack(fill='x', pady=(0,16))

    def _build_power_panel(self, parent: tk.Frame) -> None:
        card = self._card(parent, padx=18, pady=15)
        card.pack(fill='x')
        self.power_enabled = tk.BooleanVar(master=self.window, value=self._initial_power_enabled)
        self.power_status = tk.StringVar(master=self.window, value=self._initial_power_status)
        style.Switch(card, text='省电模式', variable=self.power_enabled,
                     command=self._toggle_power).pack(anchor='w')

    def _toggle_power(self):
        enabled = self.power_enabled.get()
        if self._apply_power_settings is None or not self._apply_power_settings(enabled):
            self.power_enabled.set(not enabled)

    def _build_visual_panel(self, parent: tk.Frame) -> None:
        assert self._visual_store is not None
        card = self._card(parent, padx=18, pady=15)
        card.pack(fill="x", pady=(0, 14))
        tk.Label(
            card,
            text="伤口频率",
            bg=self.CARD,
            fg=self.TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        row = tk.Frame(card, bg=self.CARD)
        row.pack(fill="x", pady=(24,0))
        self.visual_strikes_per_wound = tk.StringVar(
            value=str(self._visual_store.settings.strikes_per_wound)
        )
        self.visual_frequency_label = tk.StringVar()
        initial_count = max(1, min(10, self._visual_store.settings.strikes_per_wound or 1))
        self.visual_frequency_value = tk.DoubleVar(value=round(100 / initial_count))
        def frequency_changed(value):
            percent = max(10, min(100, round(float(value))))
            count = max(1, min(10, round(100 / percent)))
            self.visual_strikes_per_wound.set(str(count))
            self.visual_frequency_label.set(f'{percent}%')
        frequency_changed(self.visual_frequency_value.get())
        tk.Label(row, textvariable=self.visual_frequency_label, bg=self.CARD, fg=self.TEXT,
                 font=('Microsoft YaHei UI',10,'bold')).pack(anchor='w')
        self.visual_frequency_slider = TickSlider(row, minimum=10, maximum=100,
            variable=self.visual_frequency_value, command=frequency_changed,
            ticks=(10,25,50,75,100), formatter=lambda v:f'{v:.0f}%', bg=self.CARD)
        self.visual_frequency_slider.pack(fill='x')

        footer = tk.Frame(card, bg=self.CARD)
        footer.pack(fill="x", pady=(16, 0))
        self.visual_status = tk.StringVar(value="")
        tk.Label(
            footer,
            textvariable=self.visual_status,
            bg=self.CARD,
            fg=self.MUTED,
            anchor="w",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left", fill="x", expand=True)
        self.visual_frequency_slider.bind("<ButtonRelease-1>", lambda _e: self._save_visual_settings(), add='+')
        self.visual_frequency_slider.bind("<KeyRelease>", lambda _e: self._save_visual_settings(), add='+')

    def _save_visual_settings(self) -> None:
        if self._apply_visual_settings is None:
            return
        try:
            settings = replace(self._visual_store.settings,
                strikes_per_wound=int(self.visual_strikes_per_wound.get().strip()),
                scare_enabled=False).validated()
        except ValueError as exc:
            messagebox.showerror("视觉设置无效", str(exc), parent=self.window)
            return
        if self._apply_visual_settings(settings):
            self.visual_status.set(f"已保存：每 {settings.strikes_per_wound} 次显示伤口")

    def _build_voice_panel(self, parent: tk.Frame) -> None:
        assert self._voice_store is not None
        settings = self._voice_store.settings
        enabled_card = self.voice_feature_card = self._card(self._content, padx=24, pady=24)
        enabled_card.pack(fill="x", pady=(0, 14))
        title = tk.Frame(enabled_card, bg=self.CARD)
        title.pack(fill="x")
        tk.Label(
            title,
            text="语音输入",
            bg=self.CARD,
            fg=self.TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        self.voice_enabled = tk.BooleanVar(value=settings.enabled)
        style.Switch(
            title,
            text="",
            variable=self.voice_enabled,
            command=self._save_voice_enabled,
            bg=self.CARD,
            activebackground=self.CARD,
            fg=self.TEXT,
            activeforeground=self.TEXT,
            selectcolor=self.CARD_ALT,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        ).pack(side="right")
        tk.Label(
            enabled_card,
            text="豆包 2.0 · 本地备用",
            bg=self.CARD,
            fg=self.MUTED,
            font=(style.FONT, 9),
        ).pack(anchor="w", pady=(8, 0))

        percent = self._gain_to_percent(settings.recording_gain)
        self.voice_sensitivity_value = tk.DoubleVar(value=percent)
        self.voice_sensitivity_label = tk.StringVar(value=f'识别灵敏度  {percent:.0f}%')
        tk.Label(enabled_card, textvariable=self.voice_sensitivity_label, bg=self.CARD,
                 fg=self.TEXT, font=(style.FONT,10,'bold')).pack(anchor='w', pady=(20,4))
        self.voice_sensitivity_slider = TickSlider(
            enabled_card, minimum=0, maximum=100, variable=self.voice_sensitivity_value,
            ticks=(0,25,50,75,100), formatter=lambda value:f'{value:.0f}%', bg=self.CARD,
            command=lambda value:self.voice_sensitivity_label.set(
                f'识别灵敏度  {float(value):.0f}%'))
        self.voice_sensitivity_slider.pack(fill='x')
        self.voice_sensitivity_slider.bind('<ButtonRelease-1>', self._commit_voice_sensitivity, add='+')
        self.voice_sensitivity_slider.bind('<KeyRelease>', self._commit_voice_sensitivity, add='+')

        from .speech_settings import SpeechServiceCard
        self.speech_service = SpeechServiceCard(parent, self._voice_store, self._apply_voice_settings)
        self.speech_service.card.pack_forget()

        self.voice_advanced = Disclosure(parent, "开发者参数 · 录音", bg=self.BG)
        tuning = self._card(self.voice_advanced.body, padx=18, pady=15)
        tuning.pack(fill="x")
        tk.Label(
            tuning,
            text="录音参数",
            bg=self.CARD,
            fg=self.TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        fields_spec = (
            ("silence_ms", "结束录音静音", "ms"),
            ("max_recording_ms", "最长录音", "ms"),
        )
        grid = tk.Frame(tuning, bg=self.CARD)
        grid.pack(fill="x")
        for column in (1, 4):
            grid.grid_columnconfigure(column, weight=1)
        for index, (name, label, unit) in enumerate(fields_spec):
            target_grid = grid
            row = index
            column = 0
            target_grid.grid_columnconfigure(1,weight=1)
            tk.Label(
                target_grid, text=label, bg=self.CARD, fg=self.MUTED,
                font=("Microsoft YaHei UI", 8),
            ).grid(row=row, column=column, sticky="w", pady=5)
            variable = tk.StringVar(value=str(getattr(settings, name)))
            self._voice_variables[name] = variable
            tk.Entry(
                target_grid,
                textvariable=variable,
                bg=self.CARD_ALT,
                fg=self.TEXT,
                insertbackground=self.TEXT,
                relief="flat",
                highlightthickness=1,
                highlightbackground=self.LINE,
                highlightcolor=self.ACCENT,
                justify="right",
                width=12,
                font=("Cascadia Mono", 9),
            ).grid(row=row, column=column + 1, sticky="ew", padx=(8, 6), pady=5, ipady=5)
            tk.Label(
                target_grid, text=unit, bg=self.CARD, fg=self.MUTED, width=5,
                anchor="w", font=("Microsoft YaHei UI", 8),
            ).grid(row=row, column=column + 2, sticky="w", padx=(0, 20 if column == 0 else 0))

        self._button(tuning, "保存录音参数", self._save_voice_settings, self.BLUE, "#FFFFFF").pack(anchor="e",pady=(12,0))
        actions = self._card(parent, padx=18, pady=15)
        # Reuse the service card's status line instead of another status-only card.
        self.voice_status = self.speech_service.status
        tk.Label(
            actions,
            textvariable=self.voice_status,
            bg=self.CARD,
            fg=self.BLUE,
            anchor="w",
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x", pady=(0, 11))
        self.voice_calibrate_button = self._button(
            actions,
            "调整最低冲击",
            lambda: self._select_section('calibration'),
            self.CARD_ALT,
            self.TEXT,
        )
        self.voice_record_button = self._button(
            actions,
            "硬件双敲无需学习",
            self._begin_voice_calibration,
            self.ACCENT,
            "#17120A",
        )
        self.voice_record_button.configure(state="disabled")
        # Kept alive for the programmatic developer page, never shown in the
        # normal settings flow.

    def _save_voice_enabled(self):
        if self._apply_voice_settings is None:
            return
        current = self._voice_store.settings
        if not self._apply_voice_settings(replace(current, enabled=bool(self.voice_enabled.get()))):
            self.voice_enabled.set(current.enabled)

    @staticmethod
    def _gain_to_percent(gain):
        return max(0.0, min(100.0, (float(gain) - 1.0) / 7.0 * 100.0))

    @staticmethod
    def _percent_to_gain(percent):
        return 1.0 + max(0.0, min(100.0, float(percent))) / 100.0 * 7.0

    def _commit_voice_sensitivity(self, _event=None):
        if self._voice_store is None or self._apply_voice_settings is None:
            return False
        current = self._voice_store.settings
        updated = replace(current, input_mode='transcription',
                          recording_gain=self._percent_to_gain(self.voice_sensitivity_value.get()))
        if not self._apply_voice_settings(updated):
            value = self._gain_to_percent(current.recording_gain)
            self.voice_sensitivity_slider.set(value)
            return False
        return True

    def _read_voice_settings(self) -> VoiceSettings:
        current = self._voice_store.settings if self._voice_store else VoiceSettings()
        values: dict[str, object] = {
            "enabled": bool(self.voice_enabled.get()),
            "max_pulse_ms": current.max_pulse_ms,
        }
        integer_fields = {
            "min_interval_ms", "max_interval_ms", "pre_still_ms", "settle_ms",
            "silence_ms", "max_recording_ms",
        }
        for name, variable in self._voice_variables.items():
            values[name] = int(float(variable.get())) if name in integer_fields else float(variable.get())
        return replace(current, **values).validated()

    def _save_voice_settings(self) -> None:
        if self._apply_voice_settings is None:
            return
        try:
            settings = self._read_voice_settings()
        except (TypeError, ValueError) as exc:
            messagebox.showerror("语音设置无效", str(exc))
            return
        if self._apply_voice_settings(settings):
            if not settings.enabled:
                self._voice_calibrating = False
                self.voice_calibrate_button.configure(text="调整最低冲击")
                self.voice_record_button.configure(state="disabled")
            self.voice_status.set(
                "已开启；等待双敲手柄" if settings.enabled else "语音模块已关闭"
            )

    def _begin_voice_calibration(self) -> None:
        self._select_section('calibration')

    def _build_calibration_panel(self, parent):
        card = self._card(parent, padx=24, pady=24)
        card.pack(fill='x', pady=(0, 16))
        tk.Label(card, text='双敲识别', bg=self.CARD, fg=self.TEXT,
                 font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w')
        settings = self._voice_store.settings if self._voice_store else VoiceSettings()
        minimum = self._tap_minimum_value(settings)
        self.tap_minimum = tk.DoubleVar(master=self.window, value=minimum)
        percent = self._tap_minimum_to_percent(minimum)
        self.tap_sensitivity = tk.DoubleVar(master=self.window, value=percent)
        self.tap_minimum_label = tk.StringVar(master=self.window, value=f'灵敏度  {percent:.0f}%')
        tk.Label(card, textvariable=self.tap_minimum_label, bg=self.CARD, fg=self.TEXT,
                 font=('Microsoft YaHei UI',10,'bold')).pack(anchor='w')
        self.tap_minimum_slider = TickSlider(
            card, minimum=0, maximum=100, variable=self.tap_sensitivity,
            ticks=(0,25,50,75,100), formatter=lambda value:f'{value:.0f}%',
            command=self._tap_minimum_changed, bg=self.CARD)
        self.tap_minimum_slider.pack(fill='x')
        self.tap_minimum_slider.bind('<ButtonRelease-1>', self._commit_tap_minimum, add='+')
        self.tap_minimum_slider.bind('<KeyRelease>', self._commit_tap_minimum, add='+')
        effective = self._effective_tap_threshold(minimum)
        self.tap_range_status = tk.StringVar(value=(
            f'芯片实际阈值：{effective:.1f} g · 双敲窗口固定 1 秒'))

    @staticmethod
    def _tap_minimum_value(settings):
        minimum = (settings.tap_light_g if settings.tap_force_calibrated
                   and settings.tap_light_g >= .25 else settings.impact_dynamic_accel_g)
        return max(MIN_HARDWARE_TAP_G, min(MAX_HARDWARE_TAP_G, minimum))

    @staticmethod
    def _effective_tap_threshold(minimum):
        import math
        return min(MAX_HARDWARE_TAP_G, math.ceil(float(minimum) * 2.0 - 1e-9) / 2.0)

    @staticmethod
    def _tap_minimum_to_percent(minimum):
        span = MAX_HARDWARE_TAP_G - MIN_HARDWARE_TAP_G
        return max(0.0, min(100.0, (MAX_HARDWARE_TAP_G - float(minimum)) / span * 100.0))

    @staticmethod
    def _tap_percent_to_minimum(percent):
        value = max(0.0, min(100.0, float(percent)))
        span = MAX_HARDWARE_TAP_G - MIN_HARDWARE_TAP_G
        return MAX_HARDWARE_TAP_G - value / 100.0 * span

    def _set_tap_minimum(self, minimum):
        self.tap_minimum.set(minimum)
        percent = self._tap_minimum_to_percent(minimum)
        self.tap_minimum_slider.set(percent)
        self._tap_minimum_changed(percent)

    def _tap_minimum_changed(self, value):
        minimum = self._tap_percent_to_minimum(value)
        self.tap_minimum.set(minimum)
        self.tap_minimum_label.set(f'灵敏度  {float(value):.0f}%')
        self.tap_range_status.set(
            f'芯片实际阈值：{self._effective_tap_threshold(minimum):.1f} g · 双敲窗口固定 1 秒')

    def _commit_tap_minimum(self, _event=None):
        if self._voice_store is None or self._apply_voice_settings is None:
            return False
        minimum = round(self.tap_minimum.get(), 2)
        current = self._voice_store.settings
        updated = replace(
            current,
            tap_force_calibrated=True,
            tap_light_g=minimum,
            tap_heavy_g=12.0,
            impact_dynamic_accel_g=minimum,
            min_interval_ms=80,
            max_interval_ms=1000,
        )
        if not self._apply_voice_settings(updated):
            self._set_tap_minimum(self._tap_minimum_value(current))
            return False
        if 'impact_dynamic_accel_g' in self._voice_variables:
            self._voice_variables['impact_dynamic_accel_g'].set(str(minimum))
        self.tap_range_status.set(
            f'已保存 · 芯片实际阈值 {self._effective_tap_threshold(minimum):.1f} g · 1 秒内双敲')
        return True

    def _cancel_tap_calibration(self):
        if self._cancel_voice_calibration:
            self._cancel_voice_calibration()
        self._voice_calibrating = False
        self._tap_stage = 'idle'
        self.tap_range_status.set('已取消旧版测量，当前最低冲击保持不变。')

    def handle_voice_calibration(self, done: int, total: int) -> None:
        if done <= 0:
            self.voice_status.set(f"双敲学习：0 / {total}。请完成一次双敲，再点击录入。")
        elif done < total:
            self.voice_status.set(
                f"已手动录入 {done} / {total}。请完成下一次双敲，再点击录入。"
            )

    def set_voice_calibration_error(self, text: str) -> None:
        self.voice_status.set(f"自动测量失败：{text}")
        if self._voice_calibrating:
            self.tap_range_status.set(f'旧版测量未完成：{text}')

    def refresh_voice_settings(self, settings: VoiceSettings) -> None:
        self.voice_enabled.set(settings.enabled)
        if hasattr(self, "speech_service"):
            self.speech_service.mode.set('transcription')
            self.speech_service.mode_changed()
        if hasattr(self, 'voice_sensitivity_slider'):
            self.voice_sensitivity_slider.set(self._gain_to_percent(settings.recording_gain))
        for name, variable in self._voice_variables.items():
            variable.set(str(getattr(settings, name)))
        self._voice_calibrating = False
        self.voice_calibrate_button.configure(text="调整最低冲击", state="normal")
        self.voice_record_button.configure(state="disabled")
        if hasattr(self, 'tap_minimum_slider'):
            self._set_tap_minimum(self._tap_minimum_value(settings))
        self.voice_status.set("硬件双敲设置已保存")

    def set_hardware_tap_threshold(self, effective_g: float) -> None:
        if hasattr(self, 'tap_range_status'):
            self.tap_range_status.set(
                f'芯片已应用：{effective_g:.1f} g · 双敲窗口固定 1 秒')

    def set_voice_runtime_status(self, text: str) -> None:
        if hasattr(self, "voice_status") and not self._voice_calibrating:
            self.voice_status.set(text)

    def _build_messages_panel(self, parent: tk.Frame) -> None:
        assert self._message_store is not None
        messages = self._card(parent, padx=24, pady=24)
        messages.pack(fill="x")
        order = tk.Frame(messages, bg=self.CARD)
        order.pack(fill="x", pady=(0, 16))
        tk.Label(
            order,
            text="挥鞭消息",
            bg=self.CARD,
            fg=self.TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        self.message_order = tk.StringVar(value=self._message_store.profile.order)
        self.message_order_button = style.ActionButton(
            order, self._message_order_icon(), self._toggle_message_order)
        self.message_order_button.configure(font=("Segoe UI Symbol", 13), takefocus=True)
        self.message_order_button.pack(side="right")


        title_row = tk.Frame(messages, bg=self.CARD)
        title_row.pack_forget()
        tk.Label(
            title_row,
            text="发送消息",
            bg=self.CARD,
            fg=self.TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            title_row,
            text="拖动  ≡  调整顺序；每次有效挥鞭使用一张卡片",
            bg=self.CARD,
            fg=self.MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")

        list_shell = self._message_list_shell = tk.Frame(messages, bg=self.CARD)
        list_shell.pack(fill="both", expand=True)
        messages.bind('<Configure>', self._resize_message_shell, add='+')
        self._message_canvas = tk.Canvas(
            list_shell,
            bg=self.CARD,
            highlightthickness=0,
            bd=0,
        )
        scrollbar = tk.Scrollbar(
            list_shell,
            orient="vertical",
            command=self._message_canvas.yview,
            bg=self.CARD_ALT,
            troughcolor=self.CARD,
            activebackground=self.ACCENT,
        )
        self._message_scrollbar = scrollbar
        self._message_canvas.configure(yscrollcommand=scrollbar.set)
        self._message_canvas.pack(side="left", fill="both", expand=True)
        self._message_list = tk.Frame(self._message_canvas, bg=self.CARD)
        self._message_canvas_window = self._message_canvas.create_window(
            (0, 0), window=self._message_list, anchor="nw"
        )
        self._message_list.bind(
            "<Configure>",
            self._update_message_scrollbar,
        )
        self._message_canvas.bind(
            "<Configure>",
            lambda event: (
                self._message_canvas.itemconfigure(
                    self._message_canvas_window, width=event.width
                ),
                self.window.after_idle(self._update_message_scrollbar),
            ),
        )
        self._render_message_cards()

        footer = tk.Frame(messages, bg=self.CARD)
        footer.pack(fill="x", pady=(12, 0))
        self.message_status = tk.StringVar(value="修改后点击保存，下一次挥鞭立即生效")
        self._button(
            footer, "添加消息", self._add_message, self.CARD_ALT, self.TEXT
        ).pack(side="right", padx=(8, 0))
        self._button(
            footer, "保存消息设置", self._save_messages, self.GREEN, "#07130D"
        ).pack(side="right")

    def _message_order_icon(self):
        return '↕' if self.message_order.get() == 'sequential' else '⤨'

    def _toggle_message_order(self):
        self.message_order.set('random' if self.message_order.get() == 'sequential' else 'sequential')
        self.message_order_button.configure(text=self._message_order_icon())
        self._save_messages()

    def _resize_message_shell(self, event):
        # Keep the editable column at 70% and center it inside the card.
        inset = max(0, round(float(event.width) * .15))
        self._message_list_shell.pack_configure(padx=(inset, inset))

    def _update_message_scrollbar(self, _event: tk.Event | None = None) -> None:
        # One page scrollbar: nested scrollbars clip the final action column
        # and oscillate around the exact-fit content height on Windows.
        bounds = self._message_canvas.bbox("all")
        self._message_canvas.configure(scrollregion=bounds)
        self._message_canvas.configure(height=max(80, self._message_list.winfo_reqheight()))
        self._message_scrollbar.pack_forget()

    def _render_message_cards(self) -> None:
        for child in self._message_list.winfo_children():
            child.destroy()
        self._message_widgets.clear()
        self._message_cards.clear()
        for index, value in enumerate(self._message_values):
            card = style.RoundedCard(
                self._message_list,
                bg=self.CARD_ALT,
                highlightbackground=self.LINE,
                highlightthickness=1,
                padx=10,
                pady=9,
            )
            card.pack(fill="x", pady=(0, 8))
            drag = tk.Label(
                card,
                text="≡",
                bg=self.CARD,
                fg=self.MUTED,
                width=3,
                cursor="fleur",
                font=("Segoe UI", 15, "bold"),
            )
            drag.pack(side="left", fill="y", padx=(0, 8))
            drag.bind("<ButtonPress-1>", lambda event, i=index: self._start_message_drag(i, event))
            drag.bind("<B1-Motion>", self._move_message_drag)
            drag.bind("<ButtonRelease-1>", self._finish_message_drag)
            text = tk.Text(
                card,
                height=3,
                wrap="word",
                undo=True,
                bg=self.CARD,
                fg=self.TEXT,
                insertbackground=self.TEXT,
                relief="flat",
                highlightthickness=1,
                highlightbackground=self.LINE,
                highlightcolor=self.ACCENT,
                padx=9,
                pady=7,
                font=("Microsoft YaHei UI", 9),
            )
            text.insert("1.0", value)
            text.pack(side="left", fill="both", expand=True)
            self._button(
                card,
                "删除",
                lambda i=index: self._delete_message(i),
                self.CARD_ALT,
                self.RED,
            ).pack(side="right", padx=(8, 0))
            self._message_widgets.append(text)
            self._message_cards.append(card)
        style.restyle_fields(self._message_list)

    def _collect_message_values(self) -> list[str]:
        if self._message_widgets:
            self._message_values = [
                widget.get("1.0", "end-1c").strip()
                for widget in self._message_widgets
            ]
        return list(self._message_values)

    def _add_message(self) -> None:
        self._collect_message_values()
        self._message_values.append("继续当前任务，先完成一个可验证的关键节点。")
        self._render_message_cards()
        self._message_canvas.yview_moveto(1.0)

    def _delete_message(self, index: int) -> None:
        values = self._collect_message_values()
        if len(values) <= 1:
            messagebox.showinfo("不能删除", "至少需要保留一条发送消息。")
            return
        values.pop(index)
        self._message_values = values
        self._render_message_cards()

    def _start_message_drag(self, index: int, _event: tk.Event) -> str:
        self._collect_message_values()
        self._message_drag_index = index
        self._message_cards[index].configure(highlightbackground=self.ACCENT, highlightthickness=1)
        return "break"

    def _message_drop_index(self, y_root: float) -> int:
        for index, card in enumerate(self._message_cards):
            center = card.winfo_rooty() + card.winfo_height() / 2
            if y_root < center:
                return index
        return max(0, len(self._message_cards) - 1)

    def _move_message_drag(self, event: tk.Event) -> str:
        if self._message_drag_index is None:
            return "break"
        target = self._message_drop_index(float(event.y_root))
        for index, card in enumerate(self._message_cards):
            card.configure(
                highlightbackground=self.ACCENT if index == target else self.LINE,
                highlightthickness=1 if index == target else 0,
            )
        return "break"

    def _finish_message_drag(self, event: tk.Event) -> str:
        if self._message_drag_index is None:
            return "break"
        source = self._message_drag_index
        target = self._message_drop_index(float(event.y_root))
        self._message_drag_index = None
        if source != target:
            self._message_values = list(
                reorder_messages(self._message_values, source, target)
            )
        self._render_message_cards()
        return "break"

    def _save_messages(self) -> None:
        assert self._message_store is not None
        try:
            profile = MessageProfile(
                self.message_order.get(), tuple(self._collect_message_values())
            ).validated()
            self._message_store.update(profile)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法保存消息", str(exc))
            self.message_status.set("保存失败")
            return
        self._message_values = list(profile.messages)
        self._render_message_cards()
        order = "按卡片顺序" if profile.order == "sequential" else "随机且避免紧邻重复"
        self.message_status.set(f"已保存 {len(profile.messages)} 条消息；{order}发送")

    def _card(self, parent: tk.Widget, **options: object) -> tk.Frame:
        options.update(padx=24,pady=24)
        return style.RoundedCard(
            parent,
            bg=self.CARD,
            highlightbackground=self.LINE,
            highlightthickness=1,
            **options,
        )

    @staticmethod
    def _button(
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        background: str,
        foreground: str,
    ) -> tk.Button:
        primary = background == DetectorSettingsWindow.ACCENT
        return style.ActionButton(parent, text, command, primary=primary,
                                  fg="#FFFFFF" if primary else foreground)

    def _populate(self, profile: DetectorProfile) -> None:
        for field in THRESHOLD_FIELDS:
            value = getattr(profile, field.name)
            text = str(int(value)) if field.digits == 0 else f"{value:.{field.digits}f}"
            self._variables[field.name].set(text)

    def _read_profile(self) -> DetectorProfile:
        integer_names = {
            field.name for field in fields(DetectorProfile)
            if field.type == "int" or str(field.type) == "int"
        }
        values: dict[str, int | float] = {}
        for item in THRESHOLD_FIELDS:
            raw = self._variables[item.name].get().strip()
            try:
                values[item.name] = int(raw) if item.name in integer_names else float(raw)
            except ValueError as exc:
                raise ValueError(f"{item.label} 不是有效数字") from exc
        return DetectorProfile(**values).validated()

    def start_positive_learning(self) -> None:
        command = "RAW,2" if self._use_raw_v3 else "LEARN,START"
        if not self._send_command(command):
            required = "0.4.0" if self._use_raw_v3 else "0.3.1"
            messagebox.showwarning("设备未就绪", f"请先连接运行 {required} 或更新版本固件的鞭子。")
            return
        self._positive_samples.clear()
        self._negative_samples.clear()
        self._positive_motion.clear()
        self._negative_motion.clear()
        if self._use_raw_v3 and self._motion_engine is not None:
            self._motion_engine.set_paused(True)
        self._stage = "positive"
        self._awaiting_record = False
        self.stage_badge.configure(text="挥鞭样本", fg=self.ACCENT)
        self.learning_status.set(
            "每次只做两步：①自然挥鞭一次；②立即点击“录入刚刚动作”。只有点击后才计数。"
        )
        self.positive_button.configure(state="disabled", text="正在采集挥鞭…")
        self.record_button.configure(state="normal")
        self.negative_button.configure(state="disabled")
        self.skip_button.configure(state="disabled")
        self.undo_button.configure(state="normal")
        self._update_progress()

    def start_negative_learning(self) -> None:
        if self._stage != "positive_done":
            return
        if not self._send_command("RAW,2" if self._use_raw_v3 else "LEARN,START"):
            messagebox.showwarning("设备未就绪", "蓝牙连接已断开，请重新连接后再试。")
            return
        self._negative_samples.clear()
        self._stage = "negative"
        self._awaiting_record = False
        self.stage_badge.configure(text="放桌反例", fg=self.BLUE)
        self.learning_status.set(
            "每次把鞭子正常放到桌面后，立即点击“录入刚刚动作”；然后再拿起做下一次。"
        )
        self.negative_button.configure(state="disabled", text="正在采集放桌动作…")
        self.record_button.configure(state="normal")
        self.skip_button.configure(state="disabled")
        self.undo_button.configure(state="normal")
        self._update_progress()

    def handle_sample(self, sample: LearningSample) -> None:
        if not self._awaiting_record:
            return
        self._finish_record_request()
        if self._stage == "positive":
            self._positive_samples.append(sample)
            if len(self._positive_samples) >= POSITIVE_SAMPLE_COUNT:
                self._send_command("LEARN,STOP")
                self._stage = "positive_done"
                self.stage_badge.configure(text="挥鞭已完成", fg=self.GREEN)
                self.learning_status.set(
                    "15 次挥鞭采集完成。推荐继续采集 5 次放桌动作；也可以跳过反例直接生成。"
                )
                self.positive_button.configure(
                    state="normal", text="重新采集 15 次挥鞭"
                )
                self.record_button.configure(state="disabled")
                self.negative_button.configure(state="normal")
                self.skip_button.configure(state="normal")
                self.undo_button.configure(state="disabled")
        elif self._stage == "negative":
            self._negative_samples.append(sample)
            if len(self._negative_samples) >= NEGATIVE_SAMPLE_COUNT:
                self._send_command("LEARN,STOP")
                self.record_button.configure(state="disabled")
                self._generate_profile()
        if self._stage in {"positive", "negative"}:
            self.record_button.configure(state="normal")
            self.learning_status.set(
                "已录入刚刚动作。请完成下一次动作，再点击“录入刚刚动作”。"
            )
        self._update_progress()

    def request_record(self) -> None:
        if self._stage not in {"positive", "negative"} or self._awaiting_record:
            return
        if self._use_raw_v3 and self._motion_engine is not None:
            try:
                template = self._motion_engine.capture_recent(self._stage)
            except ValueError as exc:
                self.learning_status.set(str(exc))
                return
            self._handle_motion_template(template)
            return
        if not self._send_command("LEARN,TAKE"):
            messagebox.showwarning("设备未就绪", "蓝牙连接已断开，请重新连接后再试。")
            return
        self._awaiting_record = True
        self.record_button.configure(state="disabled")
        self.learning_status.set("正在读取开发板暂存的最近一次完整动作…")
        self._record_timeout_after = self.window.after(2500, self._record_timeout)

    def _handle_motion_template(self, template: MotionTemplate) -> None:
        if self._stage == "positive":
            self._positive_motion.append(template)
            if len(self._positive_motion) >= POSITIVE_SAMPLE_COUNT:
                self._stage = "positive_done"
                self.stage_badge.configure(text="V3 挥鞭已完成", fg=self.GREEN)
                self.learning_status.set(
                    "15 次完整轨迹已录入。继续录入 5 次放桌反例，可显著降低误报。"
                )
                self.positive_button.configure(state="normal", text="重新采集 15 次挥鞭")
                self.record_button.configure(state="disabled")
                self.negative_button.configure(state="normal")
                self.skip_button.configure(state="normal")
                self.undo_button.configure(state="disabled")
        elif self._stage == "negative":
            self._negative_motion.append(template)
            if len(self._negative_motion) >= NEGATIVE_SAMPLE_COUNT:
                self.record_button.configure(state="disabled")
                self._generate_motion_profile()
        if self._stage in {"positive", "negative"}:
            self.learning_status.set("已录入刚刚的六轴轨迹。请做下一次动作后再点击录入。")
        self._update_progress()

    def handle_device_status(self, fields: tuple[str, ...]) -> None:
        if not fields:
            return
        status = fields[0].upper()
        if status == "EMPTY" and self._awaiting_record:
            self._finish_record_request()
            self.record_button.configure(state="normal")
            self.learning_status.set(
                "没有找到完整动作，本次未计数。请重新挥动一次，再点击录入。"
            )

    def _record_timeout(self) -> None:
        self._record_timeout_after = None
        if not self._awaiting_record:
            return
        self._awaiting_record = False
        if self._stage in {"positive", "negative"}:
            self.record_button.configure(state="normal")
        self.learning_status.set("设备没有及时回应，本次未计数；请检查蓝牙连接后重试。")

    def _finish_record_request(self) -> None:
        if self._record_timeout_after is not None:
            try:
                self.window.after_cancel(self._record_timeout_after)
            except tk.TclError:
                pass
            self._record_timeout_after = None
        self._awaiting_record = False

    def undo_sample(self) -> None:
        positives = self._positive_motion if self._use_raw_v3 else self._positive_samples
        negatives = self._negative_motion if self._use_raw_v3 else self._negative_samples
        if self._stage == "positive" and positives:
            positives.pop()
        elif self._stage == "negative" and negatives:
            negatives.pop()
        self._update_progress()

    def skip_negatives(self) -> None:
        if self._stage == "positive_done":
            if self._use_raw_v3:
                self._negative_motion.clear()
                self._generate_motion_profile()
            else:
                self._negative_samples.clear()
                self._generate_profile()

    def _generate_motion_profile(self) -> None:
        if self._motion_engine is None:
            return
        try:
            profile = self._motion_engine.train(
                self._positive_motion, self._negative_motion
            )
        except (ValueError, OSError) as exc:
            messagebox.showerror("无法生成 V3 模型", str(exc))
            return
        kept = sum(self._motion_engine.classify(item) for item in self._positive_motion)
        false_positives = sum(
            self._motion_engine.classify(item) for item in self._negative_motion
        )
        self._stage = "complete"
        self.stage_badge.configure(text="V3 模型已保存", fg=self.GREEN)
        self.learning_status.set(
            f"已选出 {len(profile.positive_templates)} 条代表轨迹；"
            f"回放通过 {kept}/{len(self._positive_motion)} 次挥鞭，"
            f"误接收 {false_positives}/{len(self._negative_motion)} 次反例。"
        )
        self.negative_button.configure(state="disabled", text="采集 5 次放桌动作")
        self.record_button.configure(state="disabled")
        self.skip_button.configure(state="disabled")
        self.undo_button.configure(state="disabled")
        self._send_command("RAW,2")
        self._motion_engine.set_paused(False)
        self._update_progress()

    def _generate_profile(self) -> None:
        try:
            profile = learn_profile(self._positive_samples, self._negative_samples)
        except ValueError as exc:
            messagebox.showerror("无法生成阈值", str(exc))
            return
        self._populate(profile)
        kept, false_positives = profile_score(
            profile, self._positive_samples, self._negative_samples
        )
        self._stage = "complete"
        self.stage_badge.configure(text="阈值已生成", fg=self.GREEN)
        self.learning_status.set(
            f"已生成建议值：回放通过 {kept}/{len(self._positive_samples)} 次挥鞭，"
            f"误接收 {false_positives}/{len(self._negative_samples)} 次反例。"
            "请点击“保存并应用到设备”。"
        )
        self.negative_button.configure(
            state="disabled", text="采集 5 次放桌动作"
        )
        self.record_button.configure(state="disabled")
        self.skip_button.configure(state="disabled")
        self.undo_button.configure(state="disabled")
        self.apply_status.set("建议值尚未保存")
        if not self.detector_advanced.expanded:
            self.detector_advanced.toggle_open()
        self._update_progress()

    def _update_progress(self) -> None:
        self._sync_learning_controls()
        if self._use_raw_v3:
            if self._stage == "negative":
                samples = self._negative_motion
                total = NEGATIVE_SAMPLE_COUNT
            else:
                samples = self._positive_motion
                total = POSITIVE_SAMPLE_COUNT
            self.progress.configure(maximum=total, value=len(samples))
            self.progress_text.set(f"{len(samples)} / {total}")
            if samples:
                self.average_text.set(
                    "完整轨迹平均："
                    f"{sum(item.peak_gyro_dps for item in samples) / len(samples):.0f} dps · "
                    f"动态加速度 {sum(item.peak_dynamic_accel_g for item in samples) / len(samples):.2f} g · "
                    f"{sum(item.duration_ms for item in samples) / len(samples):.0f} ms"
                )
            else:
                self.average_text.set("尚无六轴轨迹")
            return
        if self._stage == "negative":
            count = len(self._negative_samples)
            total = NEGATIVE_SAMPLE_COUNT
            samples = self._negative_samples
        else:
            count = len(self._positive_samples)
            total = POSITIVE_SAMPLE_COUNT
            samples = self._positive_samples
        self.progress.configure(maximum=total, value=count)
        self.progress_text.set(f"{count} / {total}")
        averages = sample_averages(samples)
        if averages:
            self.average_text.set(
                "当前平均："
                f"{averages['peak_gyro_dps']:.0f} dps · "
                f"{averages['peak_dynamic_accel_g']:.2f} g · "
                f"{averages['angular_travel_deg']:.0f}° · "
                f"主轴 {averages['dominant_axis_ratio']:.0%}"
            )
        else:
            self.average_text.set("尚无样本")

    def save_and_apply(self) -> None:
        try:
            profile = self._read_profile()
        except ValueError as exc:
            messagebox.showerror("阈值无效", str(exc))
            return
        if self._apply_profile(profile):
            self.apply_status.set("已保存，并已排队下发到设备")
        else:
            self.apply_status.set("已保存；设备重连后会自动下发")
        from .calibration import load_profile
        if load_profile() == profile:
            self._sensitivity = WhipSensitivity(profile)
            if not (self._motion_engine and self._motion_engine.trained):
                self.tolerance_scale.set(self._sensitivity.percent)

    def restore_defaults(self) -> None:
        self._populate(DetectorProfile())
        self.apply_status.set("已载入默认值，尚未保存")

    def _commit_tolerance(self, _event=None):
        if self._stage in {"positive", "negative"}:
            return
        if self._motion_engine is not None and self._motion_engine.trained:
            self.save_v3_tolerance()
            return
        percent = float(self.tolerance_value.get())
        profile = scaled_profile(self._sensitivity.base, percent)
        from .calibration import load_profile
        queued = self._apply_profile(profile)
        if queued or load_profile() == profile:
            self._populate(profile)
            try:
                self._sensitivity.save(percent)
            except OSError as exc:
                self.apply_status.set(f"阈值已保存；灵敏度位置保存失败：{exc}")
                return
            self.apply_status.set("已保存并等待手柄确认" if queued else "已保存；连接后自动同步")
        else:
            self.tolerance_scale.set(self._sensitivity.percent)

    def save_v3_tolerance(self) -> None:
        if self._motion_engine is None or not self._motion_engine.trained:
            messagebox.showinfo("尚无 V3 模型", "请先完成个性化录入。")
            return
        self._motion_engine.set_tolerance_percent(round(self.tolerance_value.get()))
        self.learning_status.set("动作匹配灵敏度已保存，试着挥动几次看看。")

    def clear_v3_profile(self) -> None:
        if self._motion_engine is None:
            return
        if not messagebox.askyesno("清除 V3 模型", "确定删除已录入的个性化轨迹吗？"):
            return
        self._motion_engine.clear_profile()
        self._motion_engine.set_paused(False)
        self._send_command("RAW,1")
        self.stage_badge.configure(text="V3 模型已清除", fg=self.RED)
        self.learning_status.set("已恢复开发板 V2 识别；原始六轴数据仍在缓存。")

    def bring_to_front(self) -> None:
        if not self._embedded:
            self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self) -> None:
        if self._voice_calibrating and self._cancel_voice_calibration is not None:
            self._cancel_voice_calibration()
            self._voice_calibrating = False
        self._finish_record_request()
        if self._use_raw_v3:
            if self._motion_engine is not None:
                self._motion_engine.set_paused(False)
            self._send_command("RAW,2" if self._motion_engine and self._motion_engine.trained else "RAW,1")
        elif self._stage in {"positive", "negative"}:
            self._send_command("LEARN,STOP")
        self.window.destroy()
