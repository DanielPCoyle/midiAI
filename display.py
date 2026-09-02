#!/usr/bin/env python3
"""The Push 2's 960x160 screen. Not MIDI -- a bulk USB endpoint of its own.

  python3 display.py          solid colour bars, proves the pipe
  python3 display.py --text   render some text
"""
import sys
from datetime import datetime
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
# push_cc.DONE in screen colours -- idle, but the seat was not the one
# focused when it got there. Not a STATUS_RGB entry: status itself never
# becomes "done" (push_cc.DONE's comment explains why), so this is combined
# with STATUS_RGB by the two renderers below rather than looked up by status
# alone. Matches app/src/theme.js's SEAT_HEX.done -- change both.
DONE_RGB = (176, 74, 224)


def status_rgb(status, unseen=False):
    """The one place idle+unseen becomes a colour, for both glass renderers
    that draw a seat's status (seat_strip's band, render(cols)'s column)."""
    if status == "idle" and unseen:
        return DONE_RGB
    return STATUS_RGB.get(status, STATUS_RGB[None])


COL_W = WIDTH // 8
SEAT_H = 12                             # seat_strip's height, at the far edge
BOTTOM = 160 - SEAT_H                   # where content has to stop; HEIGHT below
STRIP_H = 12                            # view_strip's height; renderers below
                                         # leave this band clear when composed


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


def ctx_bar(d, x, y, w, frac, h=3):
    """How full the context is. Colour carries the warning, not a number: at a
    glance you want to know whether it is fine, not that it is 0.62."""
    frac = max(0.0, min(1.0, frac or 0.0))
    hue = ((60, 208, 90) if frac < 0.6 else
           (224, 208, 44) if frac < 0.85 else (224, 60, 60))
    d.rectangle([x, y, x + w, y + h], fill=(38, 38, 38))
    if frac > 0:
        d.rectangle([x, y, x + max(1, int(w * frac)), y + h], fill=hue)


# The screen half of push_cc.VIEW_CC: one hue per view, so the label above a
# button and the button itself are the same colour. Unselected labels are the
# same hue dimmed -- on the glass, unlike the buttons, a hue does have one.
VIEW_RGB = {"focus": (235, 235, 240), "sessions": (110, 150, 240),
            "tests": (60, 220, 90), "prs": (240, 205, 60),
            "usage": (240, 80, 80)}


# push_cc.ANSWER_CC in screen colours -- the chip beside an option and the pad
# that answers it are the same hue. Change both.
ANSWER_RGB = [(60, 220, 90), (110, 150, 240), (240, 205, 60),
              (240, 150, 60), (240, 80, 80), (235, 235, 240)]


def view_strip(img, names, current, page=0, pages=1):
    """names: list of view names, in button order, up to 8 (e.g.
              ["focus", "sessions", "usage", "plan", "macros"])
       current: index of the active view
       page, pages: which bank of pads the grid is showing, and how many
              there are -- drawn only once there is more than one, so the
              tag appearing is itself the news that a second page exists
       Draws in place on `img` and returns it.

    A thin band across the very top -- the Push's 8 view-picker buttons sit
    directly above this glass, one per COL_W column, so this is the legend
    that says what each one does. Permanent chrome every view carries, so it
    stays quiet: small type, muted colour, and it never fights the content
    beneath it (renderers leave the top STRIP_H px clear for exactly this).
    """
    d = ImageDraw.Draw(img)
    names = list(names or [])[:8]
    if not (isinstance(current, int) and 0 <= current < len(names)):
        current = -1
    d.rectangle([0, 0, WIDTH - 1, STRIP_H - 1], fill=(12, 12, 12))
    for i, name in enumerate(names):
        x = i * COL_W
        active = i == current
        if active:
            d.rectangle([x, 0, x + COL_W - 1, STRIP_H - 1], fill=(48, 70, 96))
        s, f = fit(d, name, COL_W - 8, 12)
        tw = d.textlength(s, font=f)
        rgb = VIEW_RGB.get(name, (200, 200, 200))
        colour = rgb if active else tuple(int(c * 0.42) for c in rgb)
        d.text((x + (COL_W - tw) / 2, (STRIP_H - f.size) / 2 - 1), s, font=f,
               fill=colour)
    if pages > 1 and len(names) < 8:
        # in the first column no view is using: beside the names, never over
        # one, and it costs the strip nothing while there is only one page
        s, f = fit(d, f"{page + 1}/{pages}", COL_W - 8, 12)
        d.text((len(names) * COL_W + 6, (STRIP_H - f.size) / 2 - 1), s, font=f,
               fill=(150, 150, 160))
    return img


def seat_strip(img, seats, current):
    """seats: up to 8 (name, status, unseen) triples or None, in button order
       current: index of the session you are driving
       Draws in place on `img` and returns it.

    view_strip's twin at the other edge. The Push's 8 session buttons sit
    directly below this glass, so this band is what says which agent each one
    is -- without it they are eight identical buttons you have to remember.
    Renderers stop at BOTTOM to leave it the room."""
    d = ImageDraw.Draw(img)
    d.rectangle([0, BOTTOM, WIDTH - 1, HEIGHT - 1], fill=(12, 12, 12))
    for i, seat in enumerate(list(seats or [])[:8]):
        x, active = i * COL_W, i == current
        if active:
            d.rectangle([x, BOTTOM, x + COL_W - 1, HEIGHT - 1], fill=(48, 70, 96))
        name, status, unseen = seat if seat else (str(i + 1), None, False)
        rgb = (DONE_RGB if status == "idle" and unseen else
               STATUS_RGB.get(status, (70, 70, 70))) if seat else (55, 55, 55)
        if active:                      # lift it off the highlight
            rgb = tuple(min(255, c + 70) for c in rgb)
        t, f = fit(d, name, COL_W - 8, 12)
        tw = d.textlength(t, font=f)
        d.text((x + (COL_W - tw) / 2, BOTTOM + (SEAT_H - f.size) / 2 - 1), t,
               font=f, fill=rgb)
    return img


def _column(d, i, rgb, focused):
    """Shared chrome: divider, focus ring, numbered swatch.

    Both callers (render_usage, render) compose this under a view_strip, so
    the focus ring's top edge and the numbered swatch are nudged down to
    clear that band -- 9px and 10px respectively, the minimum each needs
    (the swatch stays coupled to callers' own header text, which shift by
    10 too, keeping the swatch's original gap under it)."""
    x = i * COL_W
    if i:
        d.line([(x, STRIP_H), (x, BOTTOM - 8)], fill=(45, 45, 45))
    if focused:
        d.rectangle([x + 3, 12, x + COL_W - 4, BOTTOM - 4], outline=rgb, width=2)
    d.rectangle([x + 8, 22, x + 26, 40], fill=rgb)
    d.text((x + 12, 23), str(i + 1), font=font(15), fill=(0, 0, 0))
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
    # header cluster shifted +9 so it clears the view_strip band (STRIP_H=12)
    # composed on top of this image; relative spacing among these elements is
    # preserved exactly, only the whole block moved down.
    ctx_bar(d, 10, 12, WIDTH - 20, info.get("context", 0.0))
    d.rectangle([10, 19, 34, 43], fill=rgb)
    d.text((16, 21), str(info.get("slot", 0) + 1), font=font(18), fill=(0, 0, 0))
    s, f = fit(d, info.get("name", "?"), 300, 24)
    d.text((44, 20), s, font=f, fill=(240, 240, 240))
    d.text((352, 25), info.get("model", ""), font=font(16), fill=(150, 175, 215))
    d.text((352 + 130, 25), info.get("status", "") or "", font=font(16), fill=rgb)
    eff = info.get("effort", "")
    if eff:                          # secondary to the model, not a headline
        d.text((352 + 260, 26), eff, font=font(14), fill=(120, 120, 120))
    d.line([(10, 49), (WIDTH - 10, 49)], fill=(50, 50, 50))

    opts = info.get("opts") or []
    if opts:
        s, f = fit(d, info.get("say", "") or "waiting for an answer", WIDTH - 24, 18)
        d.text((12, 55), s, font=f, fill=(200, 200, 200))
        for i, (num, label) in enumerate(opts[:4]):
            y = 81 + i * 22
            d.rectangle([12, y, 34, y + 18],
                        fill=ANSWER_RGB[i % len(ANSWER_RGB)])
            d.text((18, y + 1), num, font=font(14), fill=(0, 0, 0))
            s, f = fit(d, label, WIDTH - 60, 16)
            d.text((42, y), s, font=f, fill=(225, 225, 225))
        d.text((WIDTH - 210, 29), "the left column answers", font=font(13), fill=(120, 120, 120))
        return img

    pending = info.get("pending")

    if info.get("scroll", 0) > 0:
        # full view: the raw pane, scrolled -- untouched by the tldr default
        # below, because once you have asked to scroll you want the transcript
        bottom = BOTTOM - (22 if pending else 0)
        bf = font(15)
        rows = (bottom - 55) // 17
        body = flow(d, info.get("lines") or [], WIDTH - 30, bf)

        # scroll counts lines back from the newest, so 0 is always the live tail
        scroll = max(0, min(info.get("scroll", 0), max(0, len(body) - rows)))
        end = len(body) - scroll
        for i, line in enumerate(body[max(0, end - rows):end]):
            grey = 150 if line.startswith("  ") else 228
            d.text((12, 55 + i * 17), line, font=bf, fill=(grey,) * 3)

        if len(body) > rows:                       # scrollbar, right edge
            span = bottom - 55
            h = max(12, int(span * rows / len(body)))
            y = 55 + int((span - h) * (1 - scroll / max(1, len(body) - rows)))
            d.rectangle([WIDTH - 6, 55, WIDTH - 4, bottom], fill=(38, 38, 38))
            d.rectangle([WIDTH - 6, y, WIDTH - 4, y + h],
                        fill=(150, 175, 215) if scroll else (80, 80, 80))
        if scroll:
            d.text((WIDTH - 150, 25), f"-{scroll} lines", font=font(13), fill=(150, 175, 215))

        if pending:
            d.rectangle([0, BOTTOM - 22, WIDTH, BOTTOM], fill=(18, 24, 34))
            s, f = fit(d, "> " + pending, WIDTH - 24, 15)
            d.text((12, BOTTOM - 19), s, font=f, fill=(150, 175, 215))
        return img

    if pending and not info.get("suggested"):
        # actively being typed -- the prompt IS the body, full size, no strip
        pf = font(22)
        rows = max(0, (BOTTOM - 55) // 26)
        for i, line in enumerate(wrap(d, "❯ " + pending, WIDTH - 24, pf, rows)):
            d.text((12, 55 + i * 26), line, font=pf, fill=(150, 175, 215))
        return img

    # common case: summary at rest, not the raw transcript
    text_lines = info.get("tldr") or ([info["say"]] if info.get("say") else [])
    bottom = BOTTOM - (22 if pending else 0)
    avail = bottom - 55

    # shrink-to-fit, the same pattern render_pad uses: try a comfortable size,
    # and if the wrapped result overflows the available rows, step down and
    # re-wrap. A TLDR that silently drops its tail (usually the "Next" line)
    # is worse than one rendered smaller, so the ladder always wins over
    # clipping.
    tf, rows, out = font(20), 0, []
    for size in (20, 17, 15, 13):
        tf = font(size)
        rows = max(0, avail // (size + 4))
        out = []
        for line in text_lines:
            out.extend(wrap(d, line, WIDTH - 24, tf, 10 ** 6))
        if len(out) <= rows:
            break
    else:
        # even the smallest size doesn't fit -- clip, but from the TOP: the
        # end of a TLDR (usually "Next: ...") matters more than its start.
        out = out[len(out) - rows:] if rows else []

    row_h = tf.size + 4
    for i, line in enumerate(out[:rows]):
        d.text((12, 55 + i * row_h), line, font=tf, fill=(228, 228, 228))

    if pending:                                 # suggested: proposal, not yours yet
        d.rectangle([0, BOTTOM - 22, WIDTH, BOTTOM], fill=(18, 24, 34))
        s, f = fit(d, "> " + pending, WIDTH - 24, 15)
        d.text((12, BOTTOM - 19), s, font=f, fill=(150, 175, 215))
    return img


# question -> (background, frame/name colour, what it asks)
CONFIRMS = {"close": ((26, 6, 6), (240, 60, 60), "Close this session?"),
            "focus": ((6, 18, 26), (70, 170, 240), "Focus this agent?")}


def render_confirm(name, slot, seconds, kind="close"):
    """Anything you cannot take back asks first, on the Push."""
    bg, hue, question = CONFIRMS.get(kind, CONFIRMS["close"])
    img = Image.new("RGB", (WIDTH, HEIGHT), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH - 1, HEIGHT - 1], outline=hue, width=3)
    s, f = fit(d, question, WIDTH - 60, 34)
    d.text((30, 18), s, font=f, fill=(255, 255, 255))
    s, f = fit(d, f"{slot + 1}. {name}", WIDTH - 60, 26)
    d.text((30, 58), s, font=f, fill=tuple(min(255, c + 90) for c in hue))

    x = 30
    for colour, text in (((60, 220, 90), "button 1  green  = yes"),
                         ((240, 60, 60), "button 2  red    = no")):
        d.rectangle([x, 104, x + 20, 124], fill=colour)
        d.text((x + 30, 105), text, font=font(17), fill=(225, 225, 225))
        x += 330
    d.text((WIDTH - 90, 108), f"{seconds}s", font=font(20), fill=(150, 150, 150))
    return img


def render_pad(macro, index):
    """What one pad does, asked for before committing to finding out."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    swatch = {127: (224, 60, 60), 3: (224, 138, 44), 8: (224, 208, 44),
              126: (60, 208, 90), 125: (74, 134, 208), 122: (230, 230, 230)}
    hue = swatch.get((macro or {}).get("colour", 125), (150, 175, 215))
    d.rectangle([0, 0, 6, HEIGHT], fill=hue if macro else (50, 50, 50))
    d.text((20, 12), f"pad {index}", font=font(13), fill=(110, 110, 110))
    if not macro:
        d.text((20, 60), "empty", font=font(34), fill=(70, 70, 70))
        return img
    s, f = fit(d, macro["label"], WIDTH - 340, 26)
    d.text((20, 32), s, font=f, fill=hue)
    tags = []
    if macro.get("tag"):
        tags.append(macro["tag"])
    tags.append("submits on tap" if macro.get("submit") else "waits for Play")
    d.text((WIDTH - 320, 36), "  \u00b7  ".join(tags), font=font(15),
           fill=(130, 130, 130))
    bf = font(22)
    lines = wrap(d, macro["text"], WIDTH - 44, bf, 3)
    if len(lines) >= 3:
        bf = font(17)
        lines = wrap(d, macro["text"], WIDTH - 44, bf, 4)
    for j, line in enumerate(lines):
        d.text((20, 70 + j * (bf.size + 5)), line, font=bf, fill=(232, 232, 232))
    return img


def render_macros(macros, arming=False, moving=None):
    """All 64 pads. Drawn the way the grid sits under your hands: note 36 is
    bottom-left on the Push, so it is bottom-left here too. Getting that
    backwards would make the screen actively misleading.

    moving: None       -- not in move mode
            -1         -- move mode on, no source pad picked yet
            0..63      -- move mode on, this pad index is the picked source
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    MOVE_ACCENT = (60, 208, 90)
    accent = ((240, 60, 60) if arming else
              MOVE_ACCENT if moving is not None else (150, 175, 215))
    # grid top shifted +10 (matching the header text below) to clear the
    # view_strip band; cell_h recomputed to keep the same bottom margin
    rows, cell_h = 8, (BOTTOM - 26) // 8
    # approximate: the Push owns the real palette, this only has to be
    # recognisable next to it
    swatches = {127: (224, 60, 60), 3: (224, 138, 44), 8: (224, 208, 44),
                126: (60, 208, 90), 125: (74, 134, 208), 122: (230, 230, 230)}
    if moving == -1:
        header = "MOVE - tap a pad to pick it up"
    elif moving is not None and moving >= 0:
        header = "MOVE - tap where it goes"
    else:
        header = "RECORD - tap a pad to save the prompt" if arming else "SHORTCUTS"
    d.text((WIDTH - 250, 12), header, font=font(11),
           fill=accent if (arming or moving is not None) else (80, 80, 80))

    # the carried pad -- out of range or empty just means no source to show
    source = moving if (moving is not None and moving >= 0 and
                         moving < len(macros) and macros[moving]) else None

    for r in range(rows):
        for c in range(8):
            i = (rows - 1 - r) * 8 + c          # screen top row = top pad row
            x, y = c * COL_W, 22 + r * cell_h
            m = macros[i] if i < len(macros) else None
            box = [x + 2, y, x + COL_W - 4, y + cell_h - 2]
            if i == source:
                # this is the thing in hand -- loudest mark on the screen
                d.rectangle(box, fill=tuple(v // 4 for v in MOVE_ACCENT),
                            outline=MOVE_ACCENT, width=3)
            else:
                pickable = moving == -1 and m
                d.rectangle(box, outline=(64, 84, 64) if pickable else (38, 38, 38))
            if not m:
                continue
            if i != source:
                hue = accent if arming else swatches.get(m["colour"], (150, 175, 215))
                d.rectangle([x + 2, y, x + 5, y + cell_h - 2], fill=hue)
            s, ff = fit(d, m["label"], COL_W - 20, 11)
            d.text((x + 9, y + 1), s, font=ff, fill=(215, 215, 215))
            if m.get("submit"):        # this one fires the moment you tap it
                d.text((x + COL_W - 13, y + 1), "\u23ce", font=font(11),
                       fill=(110, 170, 110))
    return img


CHAIN_PHASE = {
    "armed":   ((74, 134, 208), "PLAY to run  ·  STOP to discard"),
    "running": ((224, 208, 44), "running  ·  STOP to abort"),
    "waiting": ((240, 60, 60), "waiting on you  ·  answer on the pads"),
    "done":    ((60, 208, 90), "chain complete"),
}


def render_chain(chain):
    """A chain is a queue of prompts fired at one agent in sequence, so the
    thing worth seeing at a glance is not any single step but where you are
    in the line: what already ran, what's live, what's still queued. Cells
    are sized off COL_W the way render_macros sizes its grid -- one row of
    the same 120px units instead of eight."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    swatches = {127: (224, 60, 60), 3: (224, 138, 44), 8: (224, 208, 44),
                126: (60, 208, 90), 125: (74, 134, 208), 122: (230, 230, 230)}
    steps = chain.get("steps") or []
    n = len(steps)
    index = chain.get("index", 0)
    seq = chain.get("seq", 0)
    seqs = chain.get("seqs", 1)
    accent, hint = CHAIN_PHASE.get(chain.get("phase"), CHAIN_PHASE["armed"])

    # header: who, which slot (1-based for humans), how far through the queue
    # -- shifted +4 (whole cluster, same relative spacing) to clear the
    # view_strip band composed on top of this image
    d.rectangle([10, 12, 30, 32], fill=accent)
    d.text((14, 14), str(chain.get("slot", 0) + 1), font=font(14), fill=(0, 0, 0))
    s, f = fit(d, chain.get("agent", "?"), 260, 20)
    d.text((38, 13), s, font=f, fill=(235, 235, 235))
    ctr, cf = fit(d, f"step {min(index, max(n - 1, 0)) + 1}/{n}", 160, 16)
    ctr_x = WIDTH - 16 - d.textlength(ctr, font=cf)
    d.text((ctr_x, 15), ctr, font=cf, fill=(150, 150, 150))

    # a blank pad can split a row into more than one runnable sequence -- when
    # it does, say which one is picked and that Left/Right switch it, right
    # next to the step counter it sits alongside
    if seqs > 1:
        pick = max(0, min(seq, seqs - 1)) + 1
        sel, sf = fit(d, f"← {pick}/{seqs} →", 150, 16)
        d.text((ctr_x - 14 - d.textlength(sel, font=sf), 15), sel, font=sf, fill=accent)

    d.line([(10, 38), (WIDTH - 10, 38)], fill=(50, 50, 50))

    # the row of steps, left to right in run order
    top, bottom = 46, BOTTOM - 26
    for i, step in enumerate(steps[:8]):
        x = i * COL_W
        hue = swatches.get(step["colour"], (150, 175, 215))
        if i < index:                                     # already fired
            fill, txt, outline = tuple(c // 3 for c in hue), (100, 100, 100), None
        elif i == index:                                   # live right now
            fill = tuple(min(255, c + 40) for c in hue)
            txt, outline = (250, 250, 250), accent
        else:                                               # still queued
            fill, txt, outline = hue, (185, 185, 185), None
        d.rectangle([x + 3, top, x + COL_W - 4, bottom], outline=(38, 38, 38))
        d.rectangle([x + 3, top, x + COL_W - 4, top + 5], fill=fill)
        if outline:
            d.rectangle([x + 3, top, x + COL_W - 4, bottom], outline=outline, width=2)
        s, ff = fit(d, step["label"], COL_W - 16, 15)
        d.text((x + 9, top + 16), s, font=ff, fill=txt)

    # footer: what phase this is, and what the transport/pads do about it
    d.rectangle([0, BOTTOM - 22, WIDTH, BOTTOM], fill=(16, 16, 16))
    d.rectangle([10, BOTTOM - 18, 24, BOTTOM - 4], fill=accent)
    s, f = fit(d, hint, WIDTH - 44, 15)
    d.text((32, BOTTOM - 19), s, font=f, fill=accent)
    return img


_EFFORT_RAMP = ((74, 134, 208), (60, 176, 160), (60, 208, 90),
                (224, 208, 44), (224, 60, 60))          # cool -> hot


def _effort_hue(i, n):
    """Interpolated, not keyed by name, so the ramp stays monotonic even if
    the level count ever changes -- len(levels) stays the source of truth."""
    if n <= 1:
        return _EFFORT_RAMP[-1]
    frac = i / (n - 1) * (len(_EFFORT_RAMP) - 1)
    lo = int(frac)
    hi = min(lo + 1, len(_EFFORT_RAMP) - 1)
    t = frac - lo
    return tuple(int(_EFFORT_RAMP[lo][k] + (_EFFORT_RAMP[hi][k] - _EFFORT_RAMP[lo][k]) * t)
                 for k in range(3))


EFFORT_ABBR = {"low": "LOW", "medium": "MED", "high": "HIGH", "xhigh": "XH", "max": "MAX"}


def render_effort(level, levels, drops=0):
    """Live overlay while a finger is still on the touchstrip -- nothing has
    been sent yet, lifting off is what commits. So the only job here is
    making the pick unmistakable from across a desk: a horizontal ladder in
    strip order, low on the left and max on the right, so the screen agrees
    with the hand. Everything else stays visible but obviously secondary."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    n = max(1, len(levels))
    cw = WIDTH // n
    sel = levels.index(level) if level in levels else -1   # unknown -> nothing picked

    # label and ladder both shifted +10 (same delta, gap preserved) to clear
    # the view_strip band composed on top of this image
    d.text((8, 12), "EFFORT", font=font(11), fill=(80, 80, 80))
    if drops:
        # the price of the pick, next to the pick. Changing effort invalidates
        # the messages cache, so this much context gets re-read at full rate.
        w = d.textlength(f"drops {human(drops)} cached", font=font(11))
        d.text((WIDTH - w - 8, 12), f"drops {human(drops)} cached",
               font=font(11), fill=(190, 150, 60))
    top, bottom = 28, BOTTOM - 30
    for i, lvl in enumerate(levels):
        x = i * cw
        hue = _effort_hue(i, n)
        if i == sel:
            bright = tuple(min(255, c + 30) for c in hue)
            d.rectangle([x + 5, top, x + cw - 6, bottom], fill=bright)
            s, f = fit(d, lvl, cw - 20, 32)
            tw = d.textlength(s, font=f)
            d.text((x + (cw - tw) / 2, top + (bottom - top - f.size) / 2),
                   s, font=f, fill=(15, 15, 15))
        else:
            dim = tuple(c // 3 for c in hue)
            d.rectangle([x + 5, top, x + cw - 6, bottom], outline=dim)
            s, f = fit(d, lvl, cw - 24, 15)
            tw = d.textlength(s, font=f)
            d.text((x + (cw - tw) / 2, top + (bottom - top - f.size) / 2),
                   s, font=f, fill=dim)

    s, f = fit(d, "lift off to set", WIDTH - 20, 14)
    tw = d.textlength(s, font=f)
    d.text(((WIDTH - tw) / 2, BOTTOM - 20), s, font=f, fill=(140, 140, 140))
    return img


def render_usage(cols, mode=0, modes=1):
    """cols: 8 entries of (name, out, ctx, focused, cache%), None if empty."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    _mode_indicator(d, mode, modes)
    # +10, matching the per-column swatch shift in _column, so the title and
    # name labels clear the view_strip band composed on top of this image
    d.text((8, 12), "USAGE", font=font(12), fill=(90, 90, 90))
    for i, col in enumerate(cols):
        if not col:
            x = i * COL_W
            if i:
                d.line([(x, STRIP_H), (x, BOTTOM - 8)], fill=(45, 45, 45))
            continue
        name, out, ctx, focused, hit = col
        rgb = (150, 175, 215)
        x = _column(d, i, rgb, focused)
        s, f = fit(d, name, COL_W - 34, 15)
        d.text((x + 32, 24), s, font=f, fill=(200, 200, 200))
        d.text((x + 10, 48), "out", font=font(13), fill=(100, 100, 100))
        s, f = fit(d, human(out), COL_W - 20, 30)
        d.text((x + 10, 62), s, font=f, fill=(235, 235, 235))
        d.text((x + 10, 104), "context", font=font(13), fill=(100, 100, 100))
        s, f = fit(d, human(ctx), COL_W - 20, 22)
        d.text((x + 10, 120), s, font=f, fill=rgb)
        # Beside the context label, not under it: BOTTOM is 148 and the stack
        # above already reaches 142. A read costs about a tenth of the same
        # token uncached, so a column sitting low here is the expensive one.
        # Dashes until it has been asked anything -- no requests yet is not a
        # bad hit rate.
        cache = "--" if hit is None else f"{hit}%"
        cw2 = d.textlength(cache, font=font(15))
        d.text((x + COL_W - cw2 - 10, 102), cache, font=font(15),
               fill=(120, 120, 120) if hit is None
               else (110, 200, 130) if hit >= 80 else (215, 165, 70))
    return img


MODEL_COLS = (("out", "out"), ("inp", "in"), ("cread", "cread"), ("cwrite", "cwrite"))
NAME_W = 210


def _mode_indicator(d, mode, modes):
    """← n/total → in the header, same idiom render_chain uses for a row that
    holds more than one sequence -- this view is the same trick one level up:
    whole modes behind its view button (and Left/Right) instead of sequences
    in one step. Silent when there's only one mode, so a single-mode renderer
    is unaffected."""
    if modes <= 1:
        return
    mode = max(0, min(mode, modes - 1))
    s, f = fit(d, f"← {mode + 1}/{modes} →", 150, 16)
    # +1, the minimum to clear the view_strip band (STRIP_H=12) composed on
    # top of this image
    d.text((WIDTH - 16 - d.textlength(s, font=f), 12), s, font=f, fill=(150, 175, 215))


def render_models(totals, mode=0, modes=1):
    """totals: {model_name: {"inp": int, "out": int, "cread": int, "cwrite": int}}
       model_name is already short, e.g. "opus 5", "fable 5", "haiku 4.5".
       May be empty.

    mode/modes: this view and render_plan are Left/Right pages of one screen;
    see _mode_indicator.

    Mirrors what /usage shows per session, minus dollars -- see the README:
    an invented cost is worse than no cost. cache-read dwarfs everything else
    (billions vs. thousands) but it's the cheap kind of token, so out -- the
    work actually done -- gets the brightest, biggest text; cache columns
    stay small and grey on purpose.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    # +10 to clear the view_strip band; the column headers/divider/rows below
    # already start at y>=15, clear of the band, so they're untouched
    d.text((8, 12), "TOKENS BY MODEL", font=font(11), fill=(80, 80, 80))
    _mode_indicator(d, mode, modes)

    if not totals:
        s, f = fit(d, "no usage yet", WIDTH - 40, 22)
        tw = d.textlength(s, font=f)
        d.text(((WIDTH - tw) / 2, HEIGHT / 2 - f.size / 2), s, font=f, fill=(90, 90, 90))
        return img

    rows = sorted(totals.items(), key=lambda kv: -kv[1].get("out", 0))
    col_w = (WIDTH - NAME_W - 10) // 4
    col_x = [NAME_W + 10 + i * col_w for i in range(4)]

    hf = font(12)
    for i, (key, label) in enumerate(MODEL_COLS):
        tw = d.textlength(label, font=hf)
        d.text((col_x[i] + col_w - 10 - tw, 15), label, font=hf,
               fill=(150, 175, 215) if key == "out" else (90, 90, 90))
    d.line([(8, 30), (WIDTH - 8, 30)], fill=(50, 50, 50))

    # shrink as the row count grows -- 1-2 models get room to breathe, 6 have
    # to fit the same 160px, same ladder pattern render_focus uses for tldr
    avail = BOTTOM - 34
    row_h = max(13, avail // (len(rows) + 1))          # +1 for the total row
    size = 20 if row_h >= 26 else 17 if row_h >= 20 else 14 if row_h >= 17 else 11

    def draw_row(y, name, vals, name_rgb, out_rgb, sub_rgb, sz):
        s, f = fit(d, name, NAME_W - 16, sz)
        d.text((8, y + (row_h - f.size) / 2), s, font=f, fill=name_rgb)
        for i, (key, _) in enumerate(MODEL_COLS):
            vsz = sz if key == "out" else max(11, sz - 3)
            s, f = fit(d, human(vals.get(key, 0)), col_w - 10, vsz)
            tw = d.textlength(s, font=f)
            d.text((col_x[i] + col_w - 10 - tw, y + (row_h - f.size) / 2), s,
                   font=f, fill=out_rgb if key == "out" else sub_rgb)

    y = 30
    for name, vals in rows:
        draw_row(y, name, vals, (220, 220, 220), (235, 235, 235), (120, 120, 120), size)
        y += row_h

    if len(rows) > 1:
        d.line([(8, y), (WIDTH - 8, y)], fill=(50, 50, 50))
        total = {key: sum(v.get(key, 0) for _, v in rows) for key, _ in MODEL_COLS}
        draw_row(y + 1, "total", total, (150, 175, 215), (150, 175, 215),
                 (110, 110, 110), max(11, size - 3))
    return img


_PLAN_RAMP = ((60, 208, 90), (224, 208, 44), (224, 60, 60))     # comfortable -> alarming
_SEVERITY_FLOOR = {"warning": 1, "critical": 2}   # anything else, "normal" included, floats to 0


def _plan_hue(used, severity):
    """Colour escalates with fill; severity can only push it hotter, never
    cooler than the number alone would say. Unknown severities are normal."""
    level = 0 if used < 0.6 else 1 if used < 0.85 else 2
    return _PLAN_RAMP[max(level, _SEVERITY_FLOOR.get(severity, 0))]


def _plan_reset(resets):
    """ISO timestamp -> 'resets 16:09' today, 'resets Sep 2' otherwise.
    Anything that doesn't parse renders as nothing, not a crash."""
    if not resets:
        return ""
    try:
        dt = datetime.fromisoformat(resets)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone()          # wall-clock time where the human is
    now = datetime.now(dt.tzinfo)
    if dt.date() == now.date():
        return f"resets {dt.strftime('%H:%M')}"
    return f"resets {dt.strftime('%b %d').replace(' 0', ' ')}"


def render_plan(bars, err="", mode=0, modes=1):
    """bars: list of dicts {label, used, severity, active, resets} -- the
    same windows /usage shows (may be empty). err: a short failure string
    when bars is empty because the fetch failed, "" when there's just
    nothing to show yet.

    mode/modes: this view and render_models are Left/Right pages of one
    screen; see _mode_indicator.

    One chunky bar per window, not a thin ctx_bar-style rule: this screen's
    whole job is being readable from a desk away, and a number alone doesn't
    say "you're fine" the way a mostly-empty green bar does at a glance.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)

    if not bars:
        msg = f"usage unavailable — {err}" if err else "no usage data"
        s, f = fit(d, msg, WIDTH - 40, 22)
        tw = d.textlength(s, font=f)
        d.text(((WIDTH - tw) / 2, HEIGHT / 2 - f.size / 2), s, font=f,
               fill=(150, 150, 150) if err else (90, 90, 90))
        _mode_indicator(d, mode, modes)
        return img

    # title and the bar rows' top both shifted +10 (same delta, gap
    # preserved) to clear the view_strip band composed on top of this image
    d.text((8, 12), "PLAN USAGE", font=font(11), fill=(80, 80, 80))
    _mode_indicator(d, mode, modes)
    n = min(len(bars), 5)
    top, bottom = 26, BOTTOM - 2
    row_h = (bottom - top) / n
    size = 16 if row_h >= 42 else 13 if row_h >= 32 else 11
    lf = font(size)
    line_h = size + 4

    for i, bar in enumerate(bars[:5]):
        y = top + i * row_h
        label = bar.get("label") or "?"
        used = max(0.0, min(1.0, bar.get("used") or 0.0))
        active = bool(bar.get("active"))
        hue = _plan_hue(used, bar.get("severity"))
        reset = _plan_reset(bar.get("resets") or "")

        # label (active gets a marker and brighter text), then the reset
        # time trailing it, small and muted -- secondary to the bar itself
        s = ("▸ " if active else "") + label
        d.text((12, y), s, font=lf, fill=(235, 235, 235) if active else (200, 200, 200))
        if reset:
            rf = font(max(10, size - 3))
            d.text((12 + d.textlength(s, font=lf) + 8, y + (size - rf.size)),
                   reset, font=rf, fill=(110, 110, 110))

        # percentage, right-aligned, coloured with the bar so the number and
        # the fill never disagree
        pct = f"{round(used * 100)}%"
        d.text((WIDTH - 12 - d.textlength(pct, font=lf), y), pct, font=lf, fill=hue)

        # the bar: this is the point of the screen
        bar_top, bar_bottom = y + line_h, y + row_h - 4
        d.rectangle([12, bar_top, WIDTH - 12, bar_bottom], fill=(32, 32, 32))
        if used > 0:
            fw = max(2, int((WIDTH - 24) * used))
            d.rectangle([12, bar_top, 12 + fw, bar_bottom], fill=hue)
        if active:              # the window actually counting down right now
            d.rectangle([12, bar_top, WIDTH - 12, bar_bottom], outline=hue, width=2)

    return img


_TEST_STATE_RGB = {
    "fail": (224, 60, 60), "pass": (60, 208, 90),
    "run": (224, 208, 44), "": (100, 100, 100),
}


PR_ROWS = 5         # rows that fit above the seat band without crowding
CHECK_RGB = {"pass": (60, 220, 90), "fail": (240, 60, 60),
             "pending": (240, 200, 40), "none": (80, 80, 80)}


SUB_RGB = {"running": (240, 200, 40), "done": (60, 220, 90)}
SUB_ROWS, SUB_COLS = 6, 2      # what fits between the two bands, in reading order


def render_subs(info):
    """info: repo, subs (label, type, model, running, focused).

    The pads in this view are these, in this order: pad 1 is the bottom-left
    one. The grid says which are still going; this says what they were asked
    to do, which no colour can."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    subs = info.get("subs") or []
    t, f = fit(d, info.get("repo", "?"), 300, 20)
    d.text((10, 14), t, font=f, fill=(235, 235, 235))
    live = sum(1 for x in subs if x.get("running"))
    shown = subs[:SUB_ROWS * SUB_COLS]
    tag = (f"{len(subs)} subagents  ·  {live} running"
           + (f"  ·  {len(subs) - len(shown)} not shown"
              if len(subs) > len(shown) else "")) if subs else ""
    if tag:
        t, f = fit(d, tag, 420, 15)
        d.text((WIDTH - 14 - d.textlength(t, font=f), 18), t, font=f,
               fill=(120, 120, 120))
    d.line([(10, 38), (WIDTH - 10, 38)], fill=(50, 50, 50))
    if not subs:
        t, f = fit(d, "this session has spawned none", WIDTH - 40, 20)
        d.text((20, 70), t, font=f, fill=(90, 90, 90))
        return img

    cw, step = (WIDTH - 20) // SUB_COLS, (BOTTOM - 44) // SUB_ROWS
    for i, sub in enumerate(shown):
        x = 10 + (i // SUB_ROWS) * cw
        y = 42 + (i % SUB_ROWS) * step
        rgb = SUB_RGB["running" if sub.get("running") else "done"]
        d.rectangle([x, y + 2, x + 10, y + step - 5], fill=rgb)
        num, f = fit(d, str(i + 1), 30, 13)
        d.text((x + 16, y + 3), num, font=f, fill=(110, 110, 110))
        # the type earns its place: two subagents with one description happens,
        # and "Explore" next to "general-purpose" is what tells them apart
        kind = sub.get("type", "")
        kw = 0
        if kind:
            t, kf = fit(d, kind, 150, 12)
            kw = d.textlength(t, font=kf) + 12
            d.text((x + cw - 14 - kw + 12, y + 4), t, font=kf, fill=(95, 95, 95))
        t, f = fit(d, sub.get("label", "?"), cw - 60 - kw, 15)
        d.text((x + 40, y + 2), t, font=f,
               fill=(245, 245, 245) if sub.get("focused") else (195, 195, 195))
        if sub.get("focused"):          # the one the focus view is sitting on
            d.line([(x - 4, y + 2), (x - 4, y + step - 5)],
                   fill=(150, 175, 215), width=3)
    return img


def render_prs(info):
    """info: repo, rows (n, title, who, draft, checks, review, mine), err.

    A row per open pull request, worst checks first. The swatch is the whole
    CI answer: you are looking for red before you are reading titles."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    every, err = info.get("rows") or [], info.get("err") or ""
    rows = every[:PR_ROWS]
    t, f = fit(d, info.get("repo", "?"), 300, 20)
    d.text((10, 14), t, font=f, fill=(235, 235, 235))
    more = len(every) - len(rows)
    tag = (f"{len(every)} open" + (f"  ·  {more} not shown" if more else "")
           if every else ("" if err else "no open PRs"))
    if tag:
        t, f = fit(d, tag, 260, 15)
        d.text((WIDTH - 14 - d.textlength(t, font=f), 18), t, font=f,
               fill=(120, 120, 120))
    d.line([(10, 38), (WIDTH - 10, 38)], fill=(50, 50, 50))

    if err and not every:
        t, f = fit(d, err, WIDTH - 40, 20)
        d.text((20, 70), t, font=f, fill=(200, 140, 60))
        return img
    if not every:
        t, f = fit(d, "nothing waiting on a review", WIDTH - 40, 20)
        d.text((20, 70), t, font=f, fill=(90, 90, 90))
        return img

    y, step = 44, min(24, (BOTTOM - 46) // max(1, len(rows)))   # <= PR_ROWS
    for row in rows:
        rgb = CHECK_RGB.get(row.get("checks"), CHECK_RGB["none"])
        d.rectangle([10, y + 3, 22, y + step - 6], fill=rgb)
        num, f = fit(d, f"#{row.get('n', 0)}", 60, 17)
        d.text((30, y + 2), num, font=f,
               fill=(235, 235, 235) if row.get("mine") else (140, 140, 140))
        # the right-hand tags are fixed width so the titles all end together
        tags = [x for x in (("draft" if row.get("draft") else ""),
                            row.get("review", "")) if x]
        note = "  ".join(tags)
        nw = 0
        if note:
            t, nf = fit(d, note, 210, 15)
            nw = d.textlength(t, font=nf) + 16
            d.text((WIDTH - 12 - nw + 16, y + 4), t, font=nf,
                   fill=(60, 220, 90) if "approved" in note else (150, 150, 150))
        who = row.get("who", "")
        ww = 0
        if who:
            t, wf = fit(d, who, 150, 14)
            ww = d.textlength(t, font=wf) + 14
            d.text((WIDTH - 12 - nw - ww + 14, y + 5), t, font=wf,
                   fill=(100, 100, 100))
        t, f = fit(d, row.get("title", ""), WIDTH - 100 - nw - ww, 17)
        d.text((90, y + 2), t, font=f,
               fill=(235, 235, 235) if row.get("mine") else (185, 185, 185))
        if row.get("mine"):     # the branch you are standing on
            d.line([(4, y + 2), (4, y + step - 5)], fill=(150, 175, 215), width=3)
        y += step
    return img


def render_tests(info):
    """A zoomable tree of test files, navigated on the pad grid. info: repo,
    path (zoom breadcrumb), items (children of the current zoom, PAD ORDER),
    running, passed, failed, slow. See module callers for the full shape.

    Mapped exactly like render_macros -- items[i] sits on pad i, pad 0 is
    bottom-left on the Push, so the cell you look at is the pad you'd press.
    Failure colour has already propagated upward into a directory's `state`
    by the time it reaches here; this only draws what it's given, it does
    not compute the propagation itself.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)

    items = (info.get("items") or [])[:64]
    repo = info.get("repo") or "?"
    path = list(info.get("path") or [])
    running = info.get("running") or ""
    slow = bool(info.get("slow"))
    passed = int(info.get("passed") or 0)
    failed = int(info.get("failed") or 0)

    # breadcrumb, truncated from the LEFT -- unlike fit() (shrink, then chop
    # the end) the tail is what matters here: you already know the repo,
    # what you need is where in it you've zoomed to
    crumb = " / ".join([repo] + path)
    size = 12
    cf = font(size)
    while size > 8 and d.textlength(crumb, font=cf) > WIDTH - 20:
        size -= 1
        cf = font(size)
    text = crumb
    while text and d.textlength("..." + text, font=cf) > WIDTH - 20:
        text = text[1:]
    if text != crumb:
        text = "..." + text
    d.text((10, 12), text, font=cf, fill=(190, 195, 200))

    # second line: in-progress / slow markers on the left, the tally on the
    # right -- failures read louder than passes on purpose
    y2 = 25
    x = 10
    if running:
        s, rf = fit(d, f"running {running}", 280, 12)
        d.rectangle([x, y2 + 1, x + 5, y2 + rf.size], fill=(224, 208, 44))
        d.text((x + 9, y2), s, font=rf, fill=(224, 208, 44))
        x += 13 + d.textlength(s, font=rf) + 14
    if slow:
        sf = font(11)
        d.rectangle([x, y2 + 1, x + 5, y2 + sf.size], fill=(224, 138, 44))
        d.text((x + 9, y2), "slow runner", font=sf, fill=(224, 138, 44))

    fail_f = font(15 if failed else 12)
    pass_f = font(11)
    fail_s, pass_s = str(failed), f"{passed} passed"
    tx = WIDTH - 12 - d.textlength(fail_s, font=fail_f)
    d.text((tx, y2 - 1), fail_s, font=fail_f,
           fill=(230, 70, 70) if failed else (90, 90, 90))
    tx -= 6 + d.textlength(pass_s, font=pass_f)
    d.text((tx, y2 + 2), pass_s, font=pass_f, fill=(110, 150, 115))

    grid_top, grid_bottom = 42, BOTTOM - 2
    if not items:
        # nothing discovered: say so quietly, not an empty grid
        s, mf = fit(d, "no tests found", WIDTH - 40, 18)
        tw = d.textlength(s, font=mf)
        mid = grid_top + (grid_bottom - grid_top - mf.size) / 2
        d.text(((WIDTH - tw) / 2, mid), s, font=mf, fill=(90, 90, 90))
        return img

    rows = 8
    cell_h = (grid_bottom - grid_top) // rows
    for r in range(rows):
        for c in range(8):
            i = (rows - 1 - r) * 8 + c              # pad 0 = bottom-left
            x = c * COL_W
            y = grid_top + r * cell_h
            box = [x + 1, y, x + COL_W - 2, y + cell_h - 2]
            if i >= len(items):
                d.rectangle(box, outline=(30, 30, 30))
                continue
            item = items[i]
            state = item.get("state") or ""
            hue = _TEST_STATE_RGB.get(state, _TEST_STATE_RGB[""])
            fail = state == "fail"
            d.rectangle(box, outline=hue if fail else (40, 40, 40), width=2 if fail else 1)
            d.rectangle([x + 1, y, x + 4, y + cell_h - 2], fill=hue)
            name = (item.get("name") or "?") + ("/" if item.get("dir") else "")
            s, nf = fit(d, name, COL_W - 12, 12)
            txt = ((240, 240, 240) if fail else (200, 220, 200) if state == "pass"
                   else (235, 225, 180) if state == "run" else (135, 135, 135))
            d.text((x + 8, y + (cell_h - nf.size) // 2), s, font=nf, fill=txt)
    return img


def render(cols):
    """cols: 8 entries of (name, status, model, sub, focused, unseen), None.

    Eight cards, and only that. A half-written prompt belongs to the focus
    view, which is where you go to read one; letting it take this glass meant
    the one screen showing all eight sessions kept turning into a screen about
    one of them."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, col in enumerate(cols):
        x = i * COL_W
        if not col:
            if i:
                d.line([(x, STRIP_H), (x, BOTTOM - 8)], fill=(45, 45, 45))
            t, f = fit(d, str(i + 1), COL_W - 16, 18)
            d.text((x + 10, 20), t, font=f, fill=(45, 45, 45))
            continue
        name, status, model = col["name"], col["status"], col["model"]
        sub = col["sub"]
        rgb = status_rgb(status, col.get("unseen"))
        _column(d, i, rgb, col["focused"])
        # ctx_bar and sub shifted (+10/+10) to stay paired with the swatch
        # row in _column, which itself shifted to clear the view_strip band
        ctx_bar(d, x + 8, 16, COL_W - 18, col["context"])
        t, f = fit(d, name, COL_W - 20, 22)
        d.text((x + 10, 46), t, font=f, fill=(235, 235, 235))
        t, f = fit(d, sub, COL_W - 34, 12)
        d.text((x + 30, 27), t, font=f, fill=(95, 95, 95))
        t, f = fit(d, status or "?", COL_W - 20, 19)
        d.text((x + 10, 78), t, font=f, fill=rgb)
        t, f = fit(d, model, COL_W - 46, 16)
        d.text((x + 10, 102), t, font=f, fill=(150, 175, 215))
        eff = col.get("effort", "")
        if eff:                 # tight column: abbreviate, stay decipherable
            tag = EFFORT_ABBR.get(eff, eff[:2].upper())
            s, ef = fit(d, tag, 34, 12)
            d.text((x + COL_W - 12 - d.textlength(s, font=ef), 104), s, font=ef,
                   fill=(110, 110, 110))
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
