"""Compact speech-service card, using the existing native settings components."""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk, messagebox

from .cloud_speech import PRESETS, LOCAL_LABEL, SpeechKeys, SpeechError
from . import settings_style as style


class SpeechServiceCard:
    def __init__(self, parent, store, apply, *, keys=None):
        self.store, self.apply = store, apply
        self.keys = keys or SpeechKeys()
        self.card = style.RoundedCard(parent, padx=24, pady=24)
        self.card.pack(fill='x', pady=(0,14))
        bg = self.card.cget('bg')
        self.labels = {'local': LOCAL_LABEL, **{k:v.label for k,v in PRESETS.items()}}
        self.choice = tk.StringVar(value=self.labels[store.settings.speech_provider])
        self.key = tk.StringVar()
        self.status = tk.StringVar()
        tk.Label(self.card,text='识别服务',bg=bg,font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(0,16))
        self.select = ttk.Combobox(self.card,textvariable=self.choice,state='readonly',
                                   values=list(self.labels.values()),font=('Microsoft YaHei UI',10))
        self.select.pack(fill='x')
        self.select.bind('<<ComboboxSelected>>',self.changed)
        self.key_row = tk.Frame(self.card,bg=bg)
        tk.Label(self.key_row,text='API Key',bg=bg,font=('Microsoft YaHei UI',9)).pack(anchor='w',pady=(0,6))
        self.entry = tk.Entry(self.key_row,textvariable=self.key,show='•',relief='flat',
                              bg='#F5F5F7',font=('Microsoft YaHei UI',10),highlightthickness=0)
        self.entry.pack(fill='x',ipady=8)
        self.notice = tk.Label(self.card,bg=bg,fg='#68686F',anchor='w',justify='left',
                               font=('Microsoft YaHei UI',9),wraplength=570)
        self.notice.pack(fill='x',pady=(12,0))
        row = tk.Frame(self.card,bg=bg)
        row.pack(fill='x',pady=(16,0))
        self.save_button = style.ActionButton(row,'保存',self.save,primary=True)
        self.save_button.pack(side='right')
        self.delete_button = style.ActionButton(row,'清除密钥',self.delete)
        tk.Label(self.card,textvariable=self.status,bg=bg,fg='#68686F',anchor='w',
                 font=('Microsoft YaHei UI',9),wraplength=570).pack(fill='x',pady=(10,0))
        self.changed()

    @property
    def selected(self):
        return next(k for k,v in self.labels.items() if v == self.choice.get())

    def changed(self, _event=None):
        self.key.set('')  # Never carry a typed credential to a different provider.
        if self.selected == 'local':
            self.key_row.pack_forget()
            self.delete_button.pack_forget()
            self.notice.configure(text='录音留在本机，无需 API Key。首次使用需下载本地模型。')
            self.status.set('')
        else:
            self.key_row.pack(fill='x',pady=(16,0),before=self.notice)
            self.delete_button.pack(side='left')
            self.notice.configure(text='录音将发送至所选服务，可能产生 API 费用。密钥仅保存在本机系统凭据中。')
            try:
                saved = bool(self.keys.get(self.selected))
                self.status.set('已保存密钥 · 留空可继续使用' if saved else '尚未填写 API Key')
            except SpeechError as exc:
                self.status.set(str(exc))

    def save(self):
        preset = self.selected
        try:
            if preset != 'local':
                if not self.key.get().strip() and not self.keys.get(preset):
                    raise SpeechError('请填写 API Key')
                if preset != self.store.settings.speech_provider and not messagebox.askyesno(
                        '启用云端语音识别', f'之后的录音将上传至 {self.labels[preset]} 进行转写，可能产生费用。\n是否启用？',
                        parent=self.card.winfo_toplevel()):
                    return
                if self.key.get().strip():
                    self.keys.set(preset,self.key.get())
            if self.apply and self.apply(replace(self.store.settings,speech_provider=preset)):
                self.key.set('')
                self.status.set('已保存 · 下次录音生效' if preset == 'local' else '已保存 · 下次录音生效，尚未验证 API 额度')
        except (SpeechError,OSError,ValueError) as exc:
            self.status.set(str(exc))

    def delete(self):
        preset = self.selected
        if preset == 'local':
            return
        if not messagebox.askyesno('清除密钥','清除此服务的本机密钥并切回本地识别？',parent=self.card.winfo_toplevel()):
            return
        try:
            # First switch away, so any failed vault deletion cannot leave an
            # active cloud configuration with no usable credential.
            if not self.apply or not self.apply(replace(self.store.settings,speech_provider='local')):
                return
            self.keys.delete(preset)
            self.choice.set(LOCAL_LABEL)
            self.changed()
            self.status.set('已清除密钥，已切回本地识别')
        except SpeechError as exc:
            self.status.set(str(exc))
