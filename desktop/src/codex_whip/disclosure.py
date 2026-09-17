"""Accessible, initially collapsed containers; hiding never resets values."""
import tkinter as tk


class Disclosure(tk.Frame):
    def __init__(self, parent, title="高级设置", *, bg="#FFFFFF"):
        super().__init__(parent, bg=bg)
        self.expanded = False
        self.title = title
        self.toggle = tk.Button(
            self, text="▸  " + title, command=self.toggle_open, anchor="w",
            bg=bg, fg="#68686F", activebackground=bg, relief="flat",
            bd=0, padx=0, pady=9, cursor="hand2", takefocus=True,
            font=("Microsoft YaHei UI", 10),
        )
        self.toggle.pack(fill="x")
        self.body = tk.Frame(self, bg=bg)

    def toggle_open(self):
        self.expanded = not self.expanded
        self.toggle.configure(text=("▾  " if self.expanded else "▸  ") + self.title)
        if self.expanded:
            self.body.pack(fill="x", pady=(6, 0))
        else:
            self.body.pack_forget()
