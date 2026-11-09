"""Marks drawn on a card's pictures before they're uploaded: an outline round what changed, and a play mark on a
video's first frame. The approver reads cards on a phone, where a small change in a full screenshot is easy to miss
("I'm having to look really hard to even see what you're talking about")."""
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

YELLOW = (255, 214, 10)


def _save(img, src):
    out = Path(tempfile.mkdtemp(prefix="approvals-")) / Path(src).name
    if out.suffix.lower() in (".jpg", ".jpeg"):
        img.convert("RGB").save(out, quality=90)
    else:
        img.save(out)
    return out


def outlined(path, box):
    """A copy of the picture with a yellow rounded outline round box = [x, y, width, height], in its own pixels."""
    img = Image.open(path)
    x, y, w, h = (float(v) for v in box)
    width = max(4, round(min(img.size) / 120))
    pad = width
    ImageDraw.Draw(img).rounded_rectangle([x - pad, y - pad, x + w + pad, y + h + pad],
                                          radius=max(8, width * 3), outline=YELLOW, width=width)
    return _save(img, path)


def with_play_mark(path):
    """A copy of a video's first frame with a white play button in the middle, so it reads as a video to tap."""
    img = Image.open(path).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    r = min(img.size) // 9
    cx, cy = img.size[0] // 2, img.size[1] // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 0, 0, 120), outline=(255, 255, 255, 235), width=max(3, r // 12))
    t = r * 0.5
    d.polygon([(cx - t * 0.6, cy - t), (cx - t * 0.6, cy + t), (cx + t, cy)], fill=(255, 255, 255, 235))
    return _save(Image.alpha_composite(img, layer), path)


def card_frame(path, mark=None, size=(1200, 900)):
    """A copy sized to a carousel card's landscape frame, which Slack fills by cropping. With a mark, the frame zooms
    onto what changed, outlined; without one, the whole picture is letterboxed so nothing is cut off."""
    img = Image.open(path).convert("RGB")
    if mark:
        img = Image.open(outlined(path, mark)).convert("RGB")
        x, y, w, h = (float(v) for v in mark)
        cx, cy = x + w / 2, y + h / 2
        fw = max(w * 1.5, h * 1.5 * 4 / 3, img.width * 0.5)
        fh = fw * 3 / 4
        if fh > img.height:
            fh = img.height
            fw = min(img.width, fh * 4 / 3)
        left = min(max(0, cx - fw / 2), img.width - fw)
        top = min(max(0, cy - fh / 2), img.height - fh)
        img = img.crop((round(left), round(top), round(left + fw), round(top + fh)))
    frame = Image.new("RGB", size, (236, 237, 240))
    pad = 24
    scale = min((size[0] - 2 * pad) / img.width, (size[1] - 2 * pad) / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    frame.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2))
    out = Path(tempfile.mkdtemp(prefix="approvals-")) / (Path(path).stem + "-card.jpg")
    frame.save(out, quality=90)
    return out
