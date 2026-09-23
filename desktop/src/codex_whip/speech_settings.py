"""Compact speech-service card, using the existing native settings components."""
from dataclasses import replace
import threading
import tkinter as tk
from tkinter import messagebox

from .audio_driver_installer import AudioDriverInstaller, AudioDriverInstallError
from .cloud_speech import PRESETS, SpeechKeys, SpeechError, bundled_doubao_key
from . import settings_style as style
from .tick_slider import TickSlider
from .voice_replay import RecordingPlayer, recording_path, recording_duration
from .virtual_microphone import VirtualMicrophoneBridge, VirtualMicrophoneError


class SpeechServiceCard:
    def __init__(self, parent, store, apply, *, keys=None, driver_installer=None, virtual_bridge=None):
        self.store, self.apply = store, apply
        self.keys = keys or SpeechKeys()
        self.card = style.RoundedCard(parent, padx=24, pady=24)
        self.card.pack(fill='x', pady=(0,14))
        bg = self.card.cget('bg')
        self.labels = {k:v.label for k,v in PRESETS.items()}
        self.mode = tk.StringVar(value='transcription')
        self.key = tk.StringVar()
        self.status = tk.StringVar()
        tk.Label(self.card,text='语音识别服务',bg=bg,font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(0,12))
        tk.Label(self.card,text='豆包录音文件识别 2.0',bg=bg,fg=style.TEXT,
                 font=(style.FONT,10,'bold')).pack(anchor='w')
        self.key_row = tk.Frame(self.card,bg=bg)
        self.key_label = tk.Label(self.key_row,text='API Key',bg=bg,font=(style.FONT,9))
        self.key_label.pack(anchor='w',pady=(0,6))
        self.entry = tk.Entry(self.key_row,textvariable=self.key,show='•',relief='flat',
                              bg='#F5F5F7',font=('Microsoft YaHei UI',10),highlightthickness=0)
        self.entry.pack(fill='x',ipady=8)
        self.notice = tk.Label(self.card,bg=bg,fg='#68686F',anchor='w',justify='left',
                               font=('Microsoft YaHei UI',9),wraplength=570)
        self.notice.pack(fill='x',pady=(12,0))
        self.driver_installer = driver_installer or AudioDriverInstaller()
        self.virtual_bridge = virtual_bridge or VirtualMicrophoneBridge()
        self.driver_panel = tk.Frame(self.card, bg=bg)
        self.driver_status = tk.StringVar(value='尚未检测')
        tk.Label(self.driver_panel, textvariable=self.driver_status, bg=bg, fg=style.MUTED,
                 font=(style.FONT,9)).pack(anchor='w', pady=(0,10))
        driver_actions = tk.Frame(self.driver_panel, bg=bg)
        driver_actions.pack(fill='x')
        self.install_driver_button = style.ActionButton(
            driver_actions, '安装音频驱动', self.install_audio_driver, primary=True)
        self.install_driver_button.pack(side='left')
        self.detect_driver_button = style.ActionButton(driver_actions, '重新检测', self.detect_audio_driver)
        self.detect_driver_button.pack(side='left', padx=(8,0))
        self.gain = tk.DoubleVar(value=store.settings.recording_gain)
        self.gain_label = tk.StringVar(value=f'录音增益：{self.gain.get():.1f} 倍')
        self.gain_title = tk.Label(self.card,textvariable=self.gain_label,bg=bg,fg=style.TEXT,
                                   font=(style.FONT,10))
        self.gain_title.pack(anchor='w',pady=(18,4))
        self.gain_slider = TickSlider(self.card,minimum=1,maximum=8,variable=self.gain,
            ticks=[1,2,4,6,8],formatter=lambda v:f'{v:g}×',bg=bg,
            command=lambda value:self.gain_label.set(f'录音增益：{value:.1f} 倍'))
        self.gain_slider.pack(fill='x')
        row = tk.Frame(self.card,bg=bg)
        row.pack(fill='x',pady=(16,0))
        self.save_button = style.ActionButton(row,'保存',self.save,primary=True)
        self.save_button.pack(side='right')
        self.delete_button = style.ActionButton(row,'清除密钥',self.delete)
        tk.Label(self.card,textvariable=self.status,bg=bg,fg='#68686F',anchor='w',
                 font=('Microsoft YaHei UI',9),wraplength=570).pack(fill='x',pady=(10,0))
        self.mode_changed()
        self._player = RecordingPlayer()
        self._replay_active = False
        self._recording = False
        self.recording_active = None
        self._replay_after = None
        self._driver_after_ids = []
        replay = tk.Frame(self.card, bg=bg)
        replay.pack(fill='x', pady=(16,0))
        self.replay_button = style.ActionButton(replay, '播放上次录音', self.toggle_replay)
        self.replay_button.pack(side='left')
        self.replay_detail = tk.StringVar()
        tk.Label(replay, textvariable=self.replay_detail, bg=bg, fg=style.MUTED,
                 font=(style.FONT,9)).pack(side='left', padx=(12,0))
        self.card.bind('<Destroy>', self._destroy_replay, add='+')
        self._refresh_replay()

    def _refresh_replay(self):
        self._replay_after = None
        try:
            if self.recording_active is not None:
                self.set_recording(self.recording_active())
            if self._replay_active and not self._player.playing:
                self._player.stop()
                self._replay_active = False
            duration = recording_duration(recording_path(self.store.path))
            self.replay_button.configure(text='停止播放' if self._replay_active else '播放上次录音',
                state='normal' if duration is not None and not self._recording else 'disabled')
            self.replay_detail.set('录音中' if self._recording else
                ('尚无录音' if duration is None else f'{duration:.1f} 秒 · 仅本机保留最近一次'))
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            self._replay_active = False
        self._replay_after = self.card.after(250, self._refresh_replay)

    def stop_replay(self):
        try:
            self._player.stop()
        except OSError as exc:
            self.status.set(str(exc))
        self._replay_active = False

    def toggle_replay(self):
        if self.recording_active is not None:
            self.set_recording(self.recording_active())
        if self._recording:
            return
        try:
            if self._replay_active:
                self.stop_replay()
            else:
                self._player.play(recording_path(self.store.path))
                self._replay_active = True
            self.replay_button.configure(text='停止播放' if self._replay_active else '播放上次录音')
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def set_recording(self, recording):
        if recording and not self._recording:
            self.stop_replay()
        self._recording = bool(recording)

    def _destroy_replay(self, event):
        if event.widget is self.card:
            if self._replay_after is not None:
                self.card.after_cancel(self._replay_after)
                self._replay_after = None
            for after_id in self._driver_after_ids:
                try:
                    self.card.after_cancel(after_id)
                except tk.TclError:
                    pass
            self._driver_after_ids.clear()
            self.stop_replay()

    @property
    def selected(self):
        return 'doubao-v2'

    def changed(self, _event=None):
        if self.mode.get() != 'transcription':
            return
        self.key.set('')  # Never carry a typed credential to a different provider.
        self.key_label.configure(text='API Key')
        self.key_row.pack(fill='x',pady=(16,0),before=self.notice)
        self.delete_button.pack_forget()
        self.notice.configure(text=('豆包录音文件识别 2.0 优先；服务不可用时自动切换本地识别。'
                                    if self.store.settings.precise_recognition else '当前只使用本地识别。'))
        try:
            saved = bool(self.keys.get(self.selected))
            self.status.set('安装包内置密钥已就绪' if bundled_doubao_key()
                            else '已保存密钥' if saved else '安装包尚未写入 API Key')
        except SpeechError as exc:
            self.status.set(str(exc))

    def mode_changed(self):
        self.mode.set('transcription')
        self.driver_panel.pack_forget()
        self.changed()

    def detect_audio_driver(self):
        if self.mode.get() != 'virtual_microphone':
            return
        try:
            device = self.virtual_bridge.detect()
            self.driver_status.set(f'已安装 · {device.name}')
            self.install_driver_button.configure(text='重新安装', state='normal')
        except (VirtualMicrophoneError, OSError, ValueError):
            self.driver_status.set(f'未检测到 {self.driver_installer.product_name}')
            self.install_driver_button.configure(text='安装音频驱动', state='normal')

    def install_audio_driver(self):
        source = self.driver_installer.source_url
        if not messagebox.askyesno(
                '安装虚拟音频驱动',
                f'将从官方来源安装 {self.driver_installer.product_name}。\n\n'
                '系统会显示管理员或安装确认，安装后可能需要重启音频应用。\n'
                f'官方来源：{source}\n\n继续吗？',
                parent=self.card.winfo_toplevel()):
            return
        self.install_driver_button.configure(state='disabled')
        self.detect_driver_button.configure(state='disabled')
        self.driver_status.set('正在下载并验证官方安装包…')

        def work():
            try:
                result = self.driver_installer.install()
                error = None
            except (AudioDriverInstallError, OSError) as exc:
                result, error = None, str(exc)
            try:
                self.card.after(0, lambda: self._finish_driver_install(result, error))
            except tk.TclError:
                pass

        threading.Thread(target=work, name='codex-whip-driver-install', daemon=True).start()

    def _finish_driver_install(self, result, error):
        self.install_driver_button.configure(state='normal')
        self.detect_driver_button.configure(state='normal')
        if error:
            self.driver_status.set(error)
            return
        self.driver_status.set(result.message)
        for delay in (2500, 8000):
            self._driver_after_ids.append(self.card.after(delay, self.detect_audio_driver))

    def save(self):
        preset = self.selected
        try:
            if self.mode.get() == 'transcription':
                credential = self.key.get().strip()
                if preset != self.store.settings.speech_provider and not messagebox.askyesno(
                        '启用云端语音识别', f'之后的录音将上传至 {self.labels[preset]} 进行转写，可能产生费用。\n是否启用？',
                        parent=self.card.winfo_toplevel()):
                    return
                if credential:
                    self.keys.set(preset,credential)
            if self.apply and self.apply(replace(
                    self.store.settings, input_mode='transcription',
                    speech_provider=preset, recording_gain=self.gain.get())):
                self.key.set('')
                self.status.set('已保存 · 下次录音生效')
                self.changed()
        except (SpeechError,OSError,ValueError) as exc:
            self.status.set(str(exc))

    def delete(self):
        preset = self.selected
        if not messagebox.askyesno('清除密钥','清除此服务的本机密钥？安装包内置密钥不会被删除。',parent=self.card.winfo_toplevel()):
            return
        try:
            self.keys.delete(preset)
            self.changed()
            self.status.set('已清除本机密钥')
        except SpeechError as exc:
            self.status.set(str(exc))
