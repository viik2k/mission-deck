"""Crisp vector-style icons drawn at runtime with Pillow.

CustomTkinter renders button/label *text*, so the previous build leaned on
Unicode glyphs (▤ ⧉ ◍ …) which look inconsistent and ugly across fonts. Instead
we draw simple line icons with :mod:`PIL.ImageDraw` (Pillow ships with
CustomTkinter — no new dependency), supersampled 4× and downscaled with LANCZOS
for clean anti-aliased strokes, then wrap them in a ``CTkImage`` so they scale
with the UI and can be tinted any colour.

Use :func:`icon` to get a cached ``CTkImage`` for a named icon at a size/colour.
Drawing geometry is expressed in a 0–24 coordinate grid (like a 24px SVG view
box) and mapped to pixels, so every icon shares the same visual weight.
"""

from __future__ import annotations

import customtkinter as ctk
from PIL import Image, ImageDraw

# Cache keyed by (name, size, colour) → CTkImage. Icons are immutable, so a
# given variant is drawn once and reused everywhere.
_CACHE: dict[tuple[str, int, str], ctk.CTkImage] = {}

_SS = 4  # supersample factor


def icon(name: str, size: int = 20, color: str = "#cfd6e0") -> ctk.CTkImage:
    key = (name, size, color)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    image = _render(name, size, color)
    cimg = ctk.CTkImage(light_image=image, dark_image=image, size=(size, size))
    _CACHE[key] = cimg
    return cimg


def _render(name: str, size: int, color: str) -> Image.Image:
    box = size * _SS
    im = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = max(1, round(1.9 / 24.0 * box))

    def P(v: float) -> float:
        return v / 24.0 * box

    def line(pts, width: int | None = None) -> None:
        ww = width or w
        d.line([(P(x), P(y)) for x, y in pts], fill=color, width=ww)
        r = ww / 2.0
        for x, y in pts:  # round caps / joins
            cx, cy = P(x), P(y)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

    def oval(x0, y0, x1, y1, fill: bool = False) -> None:
        bbox = [P(x0), P(y0), P(x1), P(y1)]
        if fill:
            d.ellipse(bbox, fill=color)
        else:
            d.ellipse(bbox, outline=color, width=w)

    def rect(x0, y0, x1, y1, r: float = 3, fill: bool = False) -> None:
        bbox = [P(x0), P(y0), P(x1), P(y1)]
        if fill:
            d.rounded_rectangle(bbox, radius=P(r), fill=color)
        else:
            d.rounded_rectangle(bbox, radius=P(r), outline=color, width=w)

    def arc(x0, y0, x1, y1, start, end) -> None:
        d.arc([P(x0), P(y0), P(x1), P(y1)], start=start, end=end, fill=color, width=w)

    def poly(pts) -> None:
        d.polygon([(P(x), P(y)) for x, y in pts], fill=color)

    drawer = _ICONS.get(name, _ICONS["box"])
    drawer(line, oval, rect, arc, poly)
    return im.resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# Icon geometry (24×24 grid). Each takes the drawing helpers.
# --------------------------------------------------------------------------- #
def _overview(line, oval, rect, arc, poly):
    line([(4, 12), (12, 4), (20, 12)])
    line([(6.5, 11), (6.5, 20), (17.5, 20), (17.5, 11)])


def _rooms(line, oval, rect, arc, poly):
    rect(3, 5, 21, 19, r=2)
    line([(9, 5), (9, 19)])


def _dashboards(line, oval, rect, arc, poly):
    rect(4, 4, 11, 11, r=1.5)
    rect(13, 4, 20, 11, r=1.5)
    rect(4, 13, 11, 20, r=1.5)
    rect(13, 13, 20, 20, r=1.5)


def _plugins(line, oval, rect, arc, poly):
    rect(4, 4, 13, 13, r=2)
    rect(11, 11, 20, 20, r=2)


def _cloud(line, oval, rect, arc, poly):
    oval(3, 12, 11, 20, fill=True)
    oval(7, 7.5, 17.5, 19, fill=True)
    oval(12, 12, 21, 20, fill=True)
    rect(6, 15, 18, 19, r=0, fill=True)


def _settings(line, oval, rect, arc, poly):
    for y, kx in ((7, 15), (12, 9.5), (17, 16)):
        line([(5, y), (19, y)])
        oval(kx - 2.2, y - 2.2, kx + 2.2, y + 2.2, fill=True)


def _user(line, oval, rect, arc, poly):
    oval(8.5, 4, 15.5, 11)
    line([(5, 20), (6.5, 15), (17.5, 15), (19, 20)])


def _search(line, oval, rect, arc, poly):
    oval(5, 5, 16, 16)
    line([(15, 15), (20.5, 20.5)])


def _check(line, oval, rect, arc, poly):
    line([(3, 12), (8, 12), (10.5, 6), (13.5, 18), (16, 12), (21, 12)])


def _external(line, oval, rect, arc, poly):
    rect(4, 8, 16, 20, r=2)
    line([(11, 13), (20, 4)])
    line([(14, 4), (20, 4)])
    line([(20, 4), (20, 10)])


def _plus(line, oval, rect, arc, poly):
    line([(12, 5), (12, 19)])
    line([(5, 12), (19, 12)])


def _edit(line, oval, rect, arc, poly):
    line([(6, 18.5), (16.5, 8)])
    line([(13.5, 5), (19, 10.5)])
    line([(6, 18.5), (5, 21), (7.5, 20)])


def _refresh(line, oval, rect, arc, poly):
    arc(5, 5, 19, 19, 300, 200)
    poly([(8.4, 6.2), (5.4, 7.4), (9.4, 9.4)])


def _command(line, oval, rect, arc, poly):
    rect(4, 5, 20, 19, r=2)
    line([(8, 10), (11, 13), (8, 16)])
    line([(13, 16), (16.5, 16)])


def _bell(line, oval, rect, arc, poly):
    line([(12, 4), (12, 6)])
    arc(7, 6, 17, 16, 180, 360)
    line([(7, 11), (6, 17), (18, 17), (17, 11)])
    line([(10.5, 19.5), (13.5, 19.5)])


def _warn(line, oval, rect, arc, poly):
    line([(12, 4), (21, 20), (3, 20), (12, 4)])
    line([(12, 9.5), (12, 15)])
    oval(11.1, 17, 12.9, 18.8, fill=True)


def _server(line, oval, rect, arc, poly):
    rect(4, 5, 20, 11, r=1.5)
    rect(4, 13, 20, 19, r=1.5)
    oval(6.6, 7.2, 8.4, 9.0, fill=True)
    oval(6.6, 15.2, 8.4, 17.0, fill=True)


def _folder(line, oval, rect, arc, poly):
    line([(4, 18), (4, 7), (9, 7), (11, 9.5), (20, 9.5), (20, 18), (4, 18)])


def _clock(line, oval, rect, arc, poly):
    oval(5, 5, 19, 19)
    line([(12, 8), (12, 12.5), (15.5, 14.5)])


def _eye(line, oval, rect, arc, poly):
    oval(5.5, 8, 18.5, 16)
    oval(10, 9.6, 14, 14.4)


def _camera(line, oval, rect, arc, poly):
    rect(8, 4.5, 12, 7, r=1)
    rect(3, 7, 21, 18.5, r=2.5)
    oval(9, 9, 15, 16.5)


def _cpu(line, oval, rect, arc, poly):
    rect(7, 7, 17, 17, r=1.5)
    rect(10.5, 10.5, 13.5, 13.5, r=0.8)
    for x in (9.5, 14.5):
        line([(x, 4), (x, 7)])
        line([(x, 17), (x, 20)])
    for y in (9.5, 14.5):
        line([(4, y), (7, y)])
        line([(17, y), (20, y)])


def _audio(line, oval, rect, arc, poly):
    for x, (y0, y1) in zip((6, 10, 14, 18), ((15, 9), (17, 6), (16, 8), (14, 10))):
        line([(x, y0), (x, y1)], width=None)


def _display(line, oval, rect, arc, poly):
    rect(3, 5, 21, 16, r=2)
    line([(12, 16), (12, 20)])
    line([(8, 20), (16, 20)])


def _doc(line, oval, rect, arc, poly):
    line([(6, 4), (14, 4), (18, 8), (18, 20), (6, 20), (6, 4)])
    line([(14, 4), (14, 8), (18, 8)])
    line([(9, 12.5), (15, 12.5)])
    line([(9, 15.5), (15, 15.5)])


def _record(line, oval, rect, arc, poly):
    oval(4, 4, 20, 20)
    oval(9, 9, 15, 15, fill=True)


def _matrix(line, oval, rect, arc, poly):
    for x in (7, 12, 17):
        for y in (7, 12, 17):
            oval(x - 1.5, y - 1.5, x + 1.5, y + 1.5, fill=True)


def _tx(line, oval, rect, arc, poly):
    oval(4, 4, 20, 20)
    line([(12, 16), (12, 8)])
    line([(8.5, 11.5), (12, 8), (15.5, 11.5)])


def _rx(line, oval, rect, arc, poly):
    oval(4, 4, 20, 20)
    line([(12, 8), (12, 16)])
    line([(8.5, 12.5), (12, 16), (15.5, 12.5)])


def _video(line, oval, rect, arc, poly):
    rect(3, 7, 15, 17, r=2)
    poly([(15, 10.5), (20, 7), (20, 17), (15, 13.5)])


def _bolt(line, oval, rect, arc, poly):
    poly([(13, 3), (6, 13.5), (11, 13.5), (9, 21), (18, 9.5), (12.5, 9.5)])


def _box(line, oval, rect, arc, poly):
    rect(5, 5, 19, 19, r=3)
    oval(11, 11, 13, 13, fill=True)


def _logo(line, oval, rect, arc, poly):
    rect(6, 13, 9, 18.5, r=0.8, fill=True)
    rect(10.5, 9, 13.5, 18.5, r=0.8, fill=True)
    rect(15, 5, 18, 18.5, r=0.8, fill=True)


_ICONS = {
    "overview": _overview, "rooms": _rooms, "dashboards": _dashboards,
    "plugins": _plugins, "cloud": _cloud, "settings": _settings, "user": _user,
    "search": _search, "check": _check, "external": _external, "plus": _plus,
    "edit": _edit, "refresh": _refresh, "command": _command, "bell": _bell,
    "warn": _warn, "server": _server, "folder": _folder, "clock": _clock,
    "eye": _eye, "camera": _camera, "cpu": _cpu, "audio": _audio,
    "display": _display, "doc": _doc, "record": _record, "matrix": _matrix,
    "tx": _tx, "rx": _rx, "video": _video, "bolt": _bolt, "box": _box,
    "logo": _logo,
    # aliases
    "link": _external, "pulse": _check, "history": _clock,
}


# Device category → icon name.
CATEGORY_ICONS: dict[str, str] = {
    "PTZ Camera": "camera",
    "Control Processor": "cpu",
    "Audio DSP": "audio",
    "Display": "display",
    "Document Camera": "doc",
    "Recorder": "record",
    "Video Matrix": "matrix",
    "Video Encoder (TX)": "tx",
    "Video Decoder (RX)": "rx",
    "Video Conferencing": "video",
    "Generic Device": "box",
}


def category_icon_name(category: str) -> str:
    return CATEGORY_ICONS.get(category, "box")
