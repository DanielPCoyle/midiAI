#!/usr/bin/env python3
"""The Push 2's 960x160 screen. Not MIDI -- a bulk USB endpoint of its own.

  python3 display.py          solid colour bars, proves the pipe
  python3 display.py --text   render some text
"""
import sys
import numpy as np
import usb.core
from PIL import Image, ImageDraw, ImageFont

VENDOR, PRODUCT, ENDPOINT = 0x2982, 0x1967, 0x01
WIDTH, HEIGHT = 960, 160
LINE_BYTES = 2048                       # 1920 visible + 128 the device discards
FRAME_HEADER = bytes([0xFF, 0xCC, 0xAA, 0x88] + [0] * 12)
XOR_MASK = bytes([0xE7, 0xF3, 0xE7, 0xFF])   # spec calls it signal shaping


MASK16 = np.frombuffer(XOR_MASK * (LINE_BYTES // 4), dtype="<u2")   # one padded line


def pack(img):
    """RGB image -> the byte layout the display expects, XOR mask applied.

    BGR565, blue in the high bits -- confirmed against the hardware with
    labelled colour bars, which is the one thing the docs are ambiguous on.
    """
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT))
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    px = ((b >> 3) << 11) | ((g >> 2) << 5) | (r >> 3)
    line = np.zeros((HEIGHT, LINE_BYTES // 2), dtype="<u2")   # 1024 words, 960 used
    line[:, :WIDTH] = px
    return (line ^ MASK16).tobytes()


class Display:
    """Frames persist about 2s before the device blanks itself, so keep sending."""

    def __init__(self):
        self.dev = usb.core.find(idVendor=VENDOR, idProduct=PRODUCT)
        if self.dev is None:
            raise RuntimeError("Push 2 not on USB")
        self.dev.set_configuration()

    def show(self, img):
        self.dev.write(ENDPOINT, FRAME_HEADER, 1000)
        self.dev.write(ENDPOINT, pack(img), 1000)

    def blank(self):
        self.show(Image.new("RGB", (WIDTH, HEIGHT), "black"))


def font(size):
    for p in ("/System/Library/Fonts/SFNSMono.ttf",
              "/System/Library/Fonts/Supplemental/Menlo.ttc",
              "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


STATUS_RGB = {
    "idle": (60, 220, 90), "working": (240, 200, 40),
    "blocked": (240, 60, 60), None: (110, 110, 110),
}
COL_W = WIDTH // 8


def fit(draw, text, width, size):
    """Shrink, then truncate. 120px is not much room for a repo name."""
    while size > 9 and draw.textlength(text, font=font(size)) > width:
        size -= 1
    f = font(size)
    while text and draw.textlength(text, font=f) > width:
        text = text[:-1]
    return text, f


def human(n):
    """45231 -> 45.2k. Screen columns are 120px; four digits is all that fits."""
    for lim, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if n >= lim:
            return f"{n / lim:.1f}{suf}"
    return str(int(n))


def _column(d, i, rgb, focused):
    """Shared chrome: divider, focus ring, numbered swatch."""
    x = i * COL_W
    if i:
        d.line([(x, 8), (x, HEIGHT - 8)], fill=(45, 45, 45))
    if focused:
        d.rectangle([x + 3, 3, x + COL_W - 4, HEIGHT - 4], outline=rgb, width=2)
    d.rectangle([x + 8, 12, x + 26, 30], fill=rgb)
    d.text((x + 12, 13), str(i + 1), font=font(15), fill=(0, 0, 0))
    return x


def render_usage(cols):
    """cols: 8 entries of (name, out_tokens, ctx_tokens, focused), None if empty."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((8, 2), "USAGE", font=font(12), fill=(90, 90, 90))
    for i, col in enumerate(cols):
        if not col:
            x = i * COL_W
            if i:
                d.line([(x, 8), (x, HEIGHT - 8)], fill=(45, 45, 45))
            continue
        name, out, ctx, focused = col
        rgb = (150, 175, 215)
        x = _column(d, i, rgb, focused)
        s, f = fit(d, name, COL_W - 34, 15)
        d.text((x + 32, 14), s, font=f, fill=(200, 200, 200))
        d.text((x + 10, 48), "out", font=font(13), fill=(100, 100, 100))
        s, f = fit(d, human(out), COL_W - 20, 30)
        d.text((x + 10, 62), s, font=f, fill=(235, 235, 235))
        d.text((x + 10, 104), "context", font=font(13), fill=(100, 100, 100))
        s, f = fit(d, human(ctx), COL_W - 20, 22)
        d.text((x + 10, 120), s, font=f, fill=rgb)
    return img


def render(cols):
    """cols: 8 entries of (name, status, model, sub, focused), None if empty."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, col in enumerate(cols):
        x = i * COL_W
        if not col:
            if i:
                d.line([(x, 8), (x, HEIGHT - 8)], fill=(45, 45, 45))
            t, f = fit(d, str(i + 1), COL_W - 16, 18)
            d.text((x + 10, 10), t, font=f, fill=(45, 45, 45))
            continue
        name, status, model, sub, focused = col
        rgb = STATUS_RGB.get(status, STATUS_RGB[None])
        _column(d, i, rgb, focused)
        t, f = fit(d, name, COL_W - 20, 22)
        d.text((x + 10, 46), t, font=f, fill=(235, 235, 235))
        t, f = fit(d, status or "?", COL_W - 20, 19)
        d.text((x + 10, 80), t, font=f, fill=rgb)
        t, f = fit(d, model, COL_W - 20, 16)
        d.text((x + 10, 106), t, font=f, fill=(150, 175, 215))
        t, f = fit(d, sub, COL_W - 20, 13)
        d.text((x + 10, 130), t, font=f, fill=(105, 105, 105))
    return img


def demo(text=False):
    img = Image.new("RGB", (WIDTH, HEIGHT), "black")
    d = ImageDraw.Draw(img)
    if text:
        d.text((20, 40), "push 2 display", font=font(48), fill=(255, 255, 255))
        d.text((20, 100), "960 x 160, bulk endpoint 0x01", font=font(24), fill=(0, 200, 255))
    else:
        # Colour bars: wrong hue here means BGR;16 needs swapping to RGB;16.
        for i, c in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255),
                               (255, 255, 0), (255, 0, 255), (0, 255, 255),
                               (255, 255, 255), (60, 60, 60)]):
            d.rectangle([i * 120, 0, (i + 1) * 120 - 1, HEIGHT], fill=c)
        d.text((14, 66), "R", font=font(40), fill=(0, 0, 0))
    Display().show(img)
    print("frame sent. red bar should be leftmost, marked R.")


if __name__ == "__main__":
    demo(text="--text" in sys.argv)
