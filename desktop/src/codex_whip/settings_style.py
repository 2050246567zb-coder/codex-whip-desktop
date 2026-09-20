"""Native settings design tokens and controls. No backend/state ownership."""
import sys
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageDraw, ImageTk

BG = "#F6F7F9"
SIDEBAR = "#ECEEF2"
CARD = "#FFFFFF"
FIELD = "#F5F6F8"
TEXT = "#24262B"
MUTED = "#717681"
LINE = "#E3E6EC"
BLUE = "#246BEB"
SELECTED = "#DCE7FB"
RED = "#B64343"
FONT = "Helvetica Neue" if sys.platform == "darwin" else "Microsoft YaHei UI"


class ActionButton(tk.Button):
    """Keep native focus, invoke, disabled and keyboard behavior; soften chrome."""
    def __init__(self, parent, text="", command=None, *, primary=False, **kwargs):
        self._nav = kwargs.pop("navigation", False)
        background = kwargs.pop("bg", BLUE if primary else CARD)
        foreground = kwargs.pop("fg", "#FFFFFF" if primary else TEXT)
        self._fill = background
        self._images = []
        self._disabled_surface = None
        self._paint_key = None
        self._ready = False
        super().__init__(parent, text=text, command=command, bg=background,
                         fg=foreground, activeforeground=foreground,
                         disabledforeground="#9BA0AA", relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=parent.cget("bg"),
                         highlightcolor=BLUE, cursor="hand2", takefocus=True,
                         font=(FONT, 10), compound="center", padx=0, pady=0, **kwargs)
        self._ready = True
        self._paint()
        self.bind("<Enter>", lambda _e: self._paint(hover=True), add="+")
        self.bind("<Leave>", lambda _e: self._paint(), add="+")
        self.bind("<ButtonPress-1>", lambda _e: self._paint(pressed=True), add="+")
        self.bind("<ButtonRelease-1>", lambda _e: self._paint(), add="+")

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None
        if "bg" in kwargs:
            self._fill = kwargs["bg"]
        if "fg" in kwargs and "activeforeground" not in kwargs:
            kwargs["activeforeground"] = kwargs["fg"]
        result = super().configure(cnf, **kwargs)
        if self._ready and kwargs:
            self._paint()
        return result

    config = configure

    def _paint(self, *, hover=False, pressed=False):
        font = tkfont.Font(root=self, font=self.cget("font"))
        width = 148 if self._nav else font.measure(self.cget("text")) + 28
        height = 38 if self._nav else max(32, font.metrics("linespace") + 14)
        fill = self._fill
        disabled = str(self.cget("state")) == "disabled"
        if disabled:
            # Use the same geometry in both states; a noninteractive label
            # covers Windows' disabled-image stipple without enabling the button.
            fill = "#AFC5EB" if self._fill == BLUE else FIELD
        elif pressed:
            fill = "#1857C4" if fill == BLUE else "#DFE4EB"
        elif hover:
            fill = "#1D60DA" if fill == BLUE else "#E8EDF5"
        if self._disabled_surface is not None:
            self._disabled_surface.place_forget()
        key = (width, height, fill, self.master.cget("bg"), self.cget('text'), disabled)
        if key == self._paint_key:
            if disabled and self._disabled_surface is not None:
                self._disabled_surface.place(x=0,y=0,relwidth=1,relheight=1)
            return
        self._paint_key = key
        image = Image.new("RGBA", (width * 3, height * 3))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, width * 3 - 2, height * 3 - 2), radius=21,
                               fill=fill, outline=LINE if fill == CARD else fill, width=3)
        photo = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=self)
        self._images = [photo]
        # The transparent corners blend into the actual parent, not a gray tile.
        super().configure(image=photo, bg=self.master.cget("bg"),
                          padx=0, pady=0,
                          activebackground=self.master.cget("bg"))
        if disabled:
            if self._disabled_surface is None:
                self._disabled_surface = tk.Label(self, bd=0, highlightthickness=0,
                                                  compound='center', padx=0,pady=0,takefocus=False)
            self._disabled_surface.configure(image=photo,text=self.cget('text'),font=self.cget('font'),
                bg=self.master.cget('bg'),fg='#FFFFFF' if self._fill == BLUE else MUTED)
            self._disabled_surface.place(x=0,y=0,relwidth=1,relheight=1)


class RoundedCard(tk.Frame):
    """Native frame with antialiased corners; children remain real Tk widgets."""
    def __init__(self, parent, **kwargs):
        kwargs.pop("bg", None)
        kwargs.pop("highlightthickness", None)
        kwargs.pop("highlightbackground", None)
        super().__init__(parent, bg=CARD, **kwargs)
        surface = Image.new("RGB", (72, 72), parent.cget("bg"))
        ImageDraw.Draw(surface).rounded_rectangle((0, 0, 71, 71), radius=35, fill=CARD)
        surface = surface.resize((24, 24), Image.Resampling.LANCZOS)
        self._corners = []
        for box, x, y, anchor in (((0, 0, 12, 12), 0, 0, "nw"),
                                  ((12, 0, 24, 12), 1, 0, "ne"),
                                  ((0, 12, 12, 24), 0, 1, "sw"),
                                  ((12, 12, 24, 24), 1, 1, "se")):
            photo = ImageTk.PhotoImage(surface.crop(box), master=self)
            corner = tk.Label(self, image=photo, bd=0, highlightthickness=0)
            corner.place(relx=x, rely=y, anchor=anchor, bordermode="outside")
            self._corners.append((corner, photo))


class Switch(tk.Checkbutton):
    """Native checkbutton semantics with a consistent on/off track."""
    def __init__(self, parent, **kwargs):
        for key in ("bg", "activebackground", "fg", "activeforeground", "selectcolor",
                    "font", "cursor", "takefocus"):
            kwargs.pop(key, None)
        self._photos = []
        for enabled in (False, True):
            image = Image.new("RGBA", (132, 78))
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((3, 3, 129, 75), radius=36,
                                   fill=BLUE if enabled else "#C8CDD5")
            left = 60 if enabled else 9
            draw.ellipse((left, 9, left + 60, 69), fill=CARD)
            self._photos.append(ImageTk.PhotoImage(image.resize((44, 26), Image.Resampling.LANCZOS), master=parent))
        super().__init__(parent, image=self._photos[0], selectimage=self._photos[1],
                         indicatoron=False, compound="left", font=(FONT, 10),
                         bg=parent.cget("bg"), activebackground=parent.cget("bg"),
                         selectcolor=parent.cget("bg"), fg=TEXT, activeforeground=TEXT,
                         relief="flat", offrelief="flat", overrelief="flat", bd=0,
                         highlightthickness=1, highlightbackground=parent.cget("bg"),
                         highlightcolor=BLUE, padx=8, pady=3, takefocus=True,
                         cursor="hand2", **kwargs)


def restyle_fields(parent):
    """Consistent quiet field surfaces, readable fonts and semantic colors."""
    for child in parent.winfo_children():
        if isinstance(child, (tk.Entry, tk.Spinbox, tk.Text)) and 'bg' in child.keys():
            child.configure(bg=FIELD, fg=TEXT, insertbackground=TEXT,
                            relief="flat", highlightthickness=1,
                            highlightbackground=LINE, highlightcolor=BLUE,
                            font=(FONT, 10))
        elif isinstance(child, tk.Scrollbar):
            child.configure(width=9, bd=0, relief="flat", bg=LINE,
                            activebackground=MUTED, troughcolor=BG, highlightthickness=0)
        elif isinstance(child, tk.Label) and child.cget("text"):
            info = tkfont.Font(root=child, font=child.cget("font")).actual()
            child.configure(font=(FONT, max(9, info["size"]), info["weight"]))
        restyle_fields(child)
