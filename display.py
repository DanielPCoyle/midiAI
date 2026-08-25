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


_FONTS = {}
# Menlo first, and not for looks: SF NS Mono has no glyph for a checkbox, and
# none for the markers Claude draws its whole transcript with -- no assistant
# dot, no activity star, no tool-result elbow, no prompt caret. They render as
# nothing, so the structure silently disappears. Menlo has all of them and is
# monospaced, which suits terminal output anyway.
FONT_PATHS = ("/System/Library/Fonts/Menlo.ttc",
              "/System/Library/Fonts/Supplemental/Menlo.ttc",
              "/System/Library/Fonts/Monaco.ttf",
              "/System/Library/Fonts/SFNSMono.ttf")


def font(size):
    if size not in _FONTS:
        for p in FONT_PATHS:
            try:
                _FONTS[size] = ImageFont.truetype(p, size)
                break
            except OSError:
                continue
        else:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


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


def wrap(d, text, width, f, rows):
    """Greedy wrap to at most `rows` lines, ellipsis on the last if it overflows."""
    out, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if d.textlength(trial, font=f) <= width:
            line = trial
            continue
        if line:
            out.append(line)
        line = word
        if len(out) == rows:
            break
    if line and len(out) < rows:
        out.append(line)
    if len(out) == rows and out:
        while out[-1] and d.textlength(out[-1] + "...", font=f) > width:
            out[-1] = out[-1][:-1]
        remaining = len(text.split()) - sum(len(o.split()) for o in out)
        if remaining > 0:
            out[-1] += "..."
    return out


def flow(d, lines, width, f):
    """Terminal lines are ~200 chars; 960px fits far fewer. Re-wrap, keeping
    blank lines as spacing but never letting them stack up."""
    out = []
    for raw in lines:
        text = raw.rstrip()
        if not text.strip():
            if out and out[-1]:
                out.append("")
            continue
        # keep the real indent, capped: a todo list under a tool result is
        # three levels deep and flattening it loses the nesting entirely
        indent = raw[:len(raw) - len(raw.lstrip())].replace("\t", "  ")[:10]
        piece = ""
        for word in text.split():
            trial = f"{piece} {word}".strip()
            if piece and d.textlength(indent + trial, font=f) > width:
                out.append(indent + piece)
                indent = indent + "  " if len(indent) < 10 else indent  # hang
                piece = word
            else:
                piece = trial
        if piece:
            out.append(indent + piece)
    return out


def render_focus(info):
    """One agent, full width. info: slot, name, model, status, act, say,
    pending, opts. With opts present this becomes the answer screen."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    rgb = STATUS_RGB.get(info.get("status"), STATUS_RGB[None])
    d.rectangle([10, 10, 34, 34], fill=rgb)
    d.text((16, 12), str(info.get("slot", 0) + 1), font=font(18), fill=(0, 0, 0))
    s, f = fit(d, info.get("name", "?"), 300, 24)
    d.text((44, 11), s, font=f, fill=(240, 240, 240))
    d.text((352, 16), info.get("model", ""), font=font(16), fill=(150, 175, 215))
    d.text((352 + 130, 16), info.get("status", "") or "", font=font(16), fill=rgb)
    d.line([(10, 40), (WIDTH - 10, 40)], fill=(50, 50, 50))

    opts = info.get("opts") or []
    if opts:
        s, f = fit(d, info.get("say", "") or "waiting for an answer", WIDTH - 24, 18)
        d.text((12, 46), s, font=f, fill=(200, 200, 200))
        for i, (num, label) in enumerate(opts[:4]):
            y = 72 + i * 22
            d.rectangle([12, y, 34, y + 18], fill=(150, 175, 215))
            d.text((18, y + 1), num, font=font(14), fill=(0, 0, 0))
            s, f = fit(d, label, WIDTH - 60, 16)
            d.text((42, y), s, font=f, fill=(225, 225, 225))
        d.text((WIDTH - 210, 20), "bottom row answers", font=font(13), fill=(120, 120, 120))
        return img

    pending = info.get("pending")
    bottom = HEIGHT - (22 if pending else 0)
    bf = font(15)
    rows = (bottom - 46) // 17
    body = flow(d, info.get("lines") or [], WIDTH - 30, bf)

    # scroll counts lines back from the newest, so 0 is always the live tail
    scroll = max(0, min(info.get("scroll", 0), max(0, len(body) - rows)))
    end = len(body) - scroll
    for i, line in enumerate(body[max(0, end - rows):end]):
        grey = 150 if line.startswith("  ") else 228
        d.text((12, 46 + i * 17), line, font=bf, fill=(grey,) * 3)

    if len(body) > rows:                       # scrollbar, right edge
        span = bottom - 46
        h = max(12, int(span * rows / len(body)))
        y = 46 + int((span - h) * (1 - scroll / max(1, len(body) - rows)))
        d.rectangle([WIDTH - 6, 46, WIDTH - 4, bottom], fill=(38, 38, 38))
        d.rectangle([WIDTH - 6, y, WIDTH - 4, y + h],
                    fill=(150, 175, 215) if scroll else (80, 80, 80))
    if scroll:
        d.text((WIDTH - 150, 16), f"-{scroll} lines", font=font(13), fill=(150, 175, 215))

    if pending:
        d.rectangle([0, HEIGHT - 22, WIDTH, HEIGHT], fill=(18, 24, 34))
        s, f = fit(d, "> " + pending, WIDTH - 24, 15)
        d.text((12, HEIGHT - 19), s, font=f, fill=(150, 175, 215))
    return img


def render_confirm(name, slot, seconds):
    """The only irreversible thing on the surface asks first, on the Push."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (26, 6, 6))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH - 1, HEIGHT - 1], outline=(240, 60, 60), width=3)
    s, f = fit(d, "Close this session?", WIDTH - 60, 34)
    d.text((30, 18), s, font=f, fill=(255, 255, 255))
    s, f = fit(d, f"{slot + 1}. {name}", WIDTH - 60, 26)
    d.text((30, 58), s, font=f, fill=(240, 160, 160))

    x = 30
    for colour, text in (((60, 220, 90), "button 1  green  = yes"),
                         ((240, 60, 60), "button 2  red    = no")):
        d.rectangle([x, 104, x + 20, 124], fill=colour)
        d.text((x + 30, 105), text, font=font(17), fill=(225, 225, 225))
        x += 330
    d.text((WIDTH - 90, 108), f"{seconds}s", font=font(20), fill=(150, 150, 150))
    return img


def render_macros(macros, arming=False):
    """All 64 pads. Drawn the way the grid sits under your hands: note 36 is
    bottom-left on the Push, so it is bottom-left here too. Getting that
    backwards would make the screen actively misleading."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    accent = (240, 60, 60) if arming else (150, 175, 215)
    rows, cell_h = 8, (HEIGHT - 14) // 8
    # approximate: the Push owns the real palette, this only has to be
    # recognisable next to it
    swatches = {127: (224, 60, 60), 3: (224, 138, 44), 8: (224, 208, 44),
                126: (60, 208, 90), 125: (74, 134, 208), 122: (230, 230, 230)}
    d.text((WIDTH - 250, 2), "RECORD - tap a pad to save the prompt" if arming
           else "SHORTCUTS", font=font(11), fill=accent if arming else (80, 80, 80))
    for r in range(rows):
        for c in range(8):
            i = (rows - 1 - r) * 8 + c          # screen top row = top pad row
            x, y = c * COL_W, 12 + r * cell_h
            m = macros[i] if i < len(macros) else None
            d.rectangle([x + 2, y, x + COL_W - 4, y + cell_h - 2],
                        outline=(38, 38, 38))
            if not m:
                continue
            hue = accent if arming else swatches.get(m["colour"], (150, 175, 215))
            d.rectangle([x + 2, y, x + 5, y + cell_h - 2], fill=hue)
            s, ff = fit(d, m["label"], COL_W - 20, 11)
            d.text((x + 9, y + 1), s, font=ff, fill=(215, 215, 215))
            if m.get("submit"):        # this one fires the moment you tap it
                d.text((x + COL_W - 13, y + 1), "\u23ce", font=font(11),
                       fill=(110, 170, 110))
    return img


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
    """cols: 8 entries of (name, status, model, sub, focused, typed), None."""
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
        name, status, model, sub, focused, typed = col
        rgb = STATUS_RGB.get(status, STATUS_RGB[None])
        _column(d, i, rgb, focused)
        t, f = fit(d, name, COL_W - 20, 22)
        d.text((x + 10, 46), t, font=f, fill=(235, 235, 235))
        t, f = fit(d, sub, COL_W - 34, 12)
        d.text((x + 30, 17), t, font=f, fill=(95, 95, 95))
        t, f = fit(d, status or "?", COL_W - 20, 19)
        d.text((x + 10, 78), t, font=f, fill=rgb)
        t, f = fit(d, model, COL_W - 20, 16)
        d.text((x + 10, 102), t, font=f, fill=(150, 175, 215))
        if typed:               # someone is mid-sentence in this one
            d.rectangle([x + 6, HEIGHT - 30, x + COL_W - 8, HEIGHT - 6],
                        fill=(18, 24, 34))
            bf = font(12)
            for j, line in enumerate(wrap(d, typed, COL_W - 22, bf, 2)):
                d.text((x + 11, HEIGHT - 28 + j * 12), line, font=bf,
                       fill=(150, 175, 215))
    return img


def check():
    """The markers Claude draws its transcript with must actually have glyphs.
    SF NS Mono silently renders every one of them as nothing."""
    import numpy as np
    f = font(15)
    missing = []
    for g in "☐☑☒✓✔✗●○⏺✻⎿❯⏵※─→←│•":
        probe = Image.new("RGB", (40, 30), "black")
        ImageDraw.Draw(probe).text((4, 4), g, font=f, fill=(255, 255, 255))
        if np.asarray(probe).sum() <= 2000:
            missing.append(g)
    assert not missing, f"{f.getname()} cannot draw: {''.join(missing)}"
    assert flow(ImageDraw.Draw(Image.new("RGB", (10, 10))),
                ["    ☒ deep"], 500, f) == ["    ☒ deep"], "indent must survive"
    print(f"ok ({f.getname()[0]})")


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
    check() if "--check" in sys.argv else demo(text="--text" in sys.argv)
