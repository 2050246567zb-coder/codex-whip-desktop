"""Accessible, initially collapsed containers; hiding never resets values."""
import sys
import tkinter as tk


class _DisclosureLabel(tk.Label):
    def __init__(self, parent, command, **kwargs):
        self._command = command
        super().__init__(parent, **kwargs)

    def invoke(self):
        return self._command()


class Disclosure(tk.Frame):
    def __init__(self, parent, title="高级设置", *, bg="#FFFFFF"):
        super().__init__(parent, bg=bg)
        self.expanded = False
        self.title = title
        options = dict(text="▸  " + title, anchor="w", bg=bg, fg="#68686F",
                       relief="flat", bd=0, padx=0, pady=9,
                       cursor="hand2", takefocus=True,
                       font=("Helvetica Neue" if sys.platform == "darwin" else "Microsoft YaHei UI", 10))
        if sys.platform == "darwin":
            self.toggle = _DisclosureLabel(self, self.toggle_open,
                                           highlightthickness=0, **options)
            self.toggle.bind("<Button-1>", lambda _event: self.toggle.invoke())
            self.toggle.bind("<space>", lambda _event: self.toggle.invoke())
            self.toggle.bind("<Return>", lambda _event: self.toggle.invoke())
        else:
            self.toggle = tk.Button(self, command=self.toggle_open,
                                    activebackground=bg, **options)
        self.toggle.pack(fill="x")
        self.body = tk.Frame(self, bg=bg)

    def toggle_open(self):
        self.expanded = not self.expanded
        self.toggle.configure(text=("▾  " if self.expanded else "▸  ") + self.title)
        if self.expanded:
            self.body.pack(fill="x", pady=(6, 0))
        else:
            self.body.pack_forget()
