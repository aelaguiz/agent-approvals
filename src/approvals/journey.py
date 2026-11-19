"""Draw a journey for Slack and write the page that opens it at full size."""
import json
import tempfile
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

SIDES = ("today", "after")
PATH_NAMES = {"today": "Today", "after": "With the change"}
SCREEN, PAD, GAP = 360, 24, 156
BG, INK, DIM, LINE, NEW = "#10141e", "#edf1fa", "#b0bdd3", "#687790", "#50e2ef"


def entries(card):
    value = card.get("journey")
    return [value] if isinstance(value, dict) else value or []


def screen_files(journeys):
    """Each picture path and its first key, in entry, step and then path order."""
    out = {}
    for e, entry in enumerate(journeys):
        for i, step in enumerate(entry.get("steps") or entry["places"]):
            for side in SIDES:
                screen = step.get(side)
                if isinstance(screen, dict) and "image" in screen:
                    path = Path(screen["image"]).expanduser().resolve()
                    out.setdefault(path, f"journey:{e}:{i}:{side}")
    return out


def _image(path):
    with Image.open(Path(path).expanduser()) as img:
        rgba = ImageOps.exif_transpose(img).convert("RGBA")
    rgb = Image.new("RGB", rgba.size, BG)
    rgb.paste(rgba, mask=rgba.getchannel("A"))
    return rgb


def _resize(img, width):
    return img.resize((width, max(1, round(img.height * width / img.width))), Image.Resampling.LANCZOS)


def _wrap(text, font, width):
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}" if line else word
        if font.getlength(candidate) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ""
        for char in word:
            if line and font.getlength(line + char) > width:
                lines.append(line)
                line = ""
            line += char
    return "\n".join([*lines, line])


def _text_size(text, font):
    _, top, _, bottom = ImageDraw.Draw(Image.new("RGB", (1, 1))).multiline_textbbox((0, 0), text, font=font)
    return bottom - top


def _tile(cell, label_font, source_font):
    """A top-aligned screen and its caption, or the box where the path ends."""
    if cell is None:
        return Image.new("RGB", (SCREEN + 12, 1), BG), 0
    if "ends" in cell:
        label = _wrap(cell["ends"], label_font, SCREEN - 48)
        height = _text_size(label, label_font) + 44
        img = Image.new("RGB", (SCREEN + 12, height + 12), BG)
        draw = ImageDraw.Draw(img)
        for x in range(6, SCREEN + 6, 14):
            for y in (6, height + 6):
                draw.line((x, y, min(x + 8, SCREEN + 6), y), fill=LINE, width=2)
        for y in range(6, height + 6, 14):
            for x in (6, SCREEN + 6):
                draw.line((x, y, x, min(y + 8, height + 6)), fill=LINE, width=2)
        draw.multiline_text((30, 26), label, font=label_font, fill=DIM)
        return img, height

    shot = _resize(_image(cell["image"]), SCREEN)
    label = _wrap(cell["label"], label_font, SCREEN)
    source = _wrap(cell["source"], source_font, SCREEN)
    source_y = shot.height + 20 + _text_size(label, label_font)
    img = Image.new("RGB", (SCREEN + 12, source_y + _text_size(source, source_font) + 12), BG)
    draw = ImageDraw.Draw(img)
    if cell.get("new"):
        draw.rectangle((0, 0, SCREEN + 11, shot.height + 11), outline=NEW, width=4)
    img.paste(shot, (6, 6))
    draw.multiline_text((6, shot.height + 13), label, font=label_font, fill=INK)
    draw.multiline_text((6, source_y), source, font=source_font, fill=DIM)
    return img, shot.height


def _strip(cells, taps, gap, label_font, source_font):
    """Place tiles in aligned columns and draw the arrows in the spaces between them."""
    tiles = [_tile(cell, label_font, source_font) for cell in cells]
    w = tiles[0][0].width
    arrows = []
    for i, tap in enumerate(taps):
        if tap is not None:
            label = _wrap(tap, source_font, gap - 20)
            height = _text_size(label, source_font) if label else 0
            y = max(height + 22, min(tiles[i][1], tiles[i + 1][1]) // 2)
            arrows.append((i, label, height, y))
    h = max([tile.height for tile, _ in tiles] + [y + 10 for _, _, _, y in arrows])
    img = Image.new("RGB", (len(tiles) * (w + gap) - gap, h), BG)
    draw = ImageDraw.Draw(img)
    for i, (tile, _) in enumerate(tiles):
        img.paste(tile, (i * (w + gap), 0))
    for i, label, height, y in arrows:
        x = i * (w + gap) + w
        if label:
            draw.multiline_text((x + gap // 2, y - height - 15), label, font=source_font,
                                fill=INK, anchor="ma", align="center")
        draw.line((x + 10, y, x + gap - 14, y), fill=DIM, width=3)
        end = x + gap - 10
        draw.polygon(((end - 12, y - 7), (end, y), (end - 12, y + 7)), fill=DIM)
    return img


def draw_map(entry) -> Path:
    """Write one PNG with the two paths, or a row of pairs for the places they differ."""
    heading = ImageFont.load_default(size=28)
    label = ImageFont.load_default(size=22)
    source = ImageFont.load_default(size=18)
    names = [entry.get(side, PATH_NAMES[side]) for side in SIDES]
    if "steps" in entry:
        strips = []
        for side in SIDES:
            cells, taps = [], []
            for step in entry["steps"]:
                cell = step.get(side)
                cells.append(dict(step["today"], source="Same as today", new=False) if cell == "same" else cell)
            for i, step in enumerate(entry["steps"][:-1]):
                tap = step["then"]
                continues = cells[i] and "image" in cells[i] and cells[i + 1]
                taps.append((tap[side] if isinstance(tap, dict) else tap) if continues else None)
            strips.append(_strip(cells, taps, GAP, label, source))
        img = Image.new("RGB", (strips[0].width + 2 * PAD, sum(s.height + 66 for s in strips) + PAD), BG)
        draw = ImageDraw.Draw(img)
        y = PAD
        for name, strip, color in zip(names, strips, (DIM, NEW)):
            draw.text((PAD + 6, y), name, font=heading, fill=color)
            img.paste(strip, (PAD, y + 42))
            y += strip.height + 66
    else:
        places = entry["places"]
        gap = 56
        pairs = [_strip([p["today"], p["after"]], [""], gap, label, source) for p in places]
        titles = [_wrap(p["name"], heading, pairs[0].width) for p in places]
        path_y = PAD + max(_text_size(t, heading) for t in titles) + 16
        img = Image.new("RGB", (sum(p.width + PAD for p in pairs) + PAD,
                                path_y + 34 + max(p.height for p in pairs) + PAD), BG)
        draw = ImageDraw.Draw(img)
        x = PAD
        for title, pair in zip(titles, pairs):
            draw.multiline_text((x + 6, PAD), title, font=heading, fill=INK)
            draw.text((x + 6, path_y), names[0], font=source, fill=DIM)
            draw.text((x + SCREEN + 18 + gap, path_y), names[1], font=source, fill=NEW)
            img.paste(pair, (x, path_y + 34))
            x += pair.width + PAD
    out = Path(tempfile.mkdtemp(prefix="approvals-")) / "map.png"
    img.save(out)
    return out


def write_page(folder, rec, n):
    """Write the version's page and one full-size JPEG and thumbnail for each picture."""
    card = rec["versions"][n - 1]["card"]
    journeys = entries(card)
    folder = Path(folder)
    (folder / "s").mkdir(parents=True, exist_ok=True)
    (folder / "t").mkdir(exist_ok=True)
    names = {path: key.replace(":", "-") + ".jpg" for path, key in screen_files(journeys).items()}
    for path, name in names.items():
        img = _image(path)
        full = _resize(img, 1400) if img.width > 1400 else img
        full.save(folder / "s" / name, quality=90)
        _resize(img, 600).save(folder / "t" / name, quality=90)

    # The page references each JPEG by its index, so repeats and "same" share the same file.
    indices = {path: i for i, path in enumerate(names)}
    page_entries = []
    for entry in journeys:
        shape = "steps" if "steps" in entry else "places"
        rows = []
        for step in entry[shape]:
            row = {key: step[key] for key in ("name", "then", *SIDES) if key in step}
            for side in SIDES:
                cell = row.get(side)
                if isinstance(cell, dict) and "image" in cell:
                    row[side] = {key: cell[key] for key in ("label", "source", "new") if key in cell}
                    row[side]["image"] = indices[Path(cell["image"]).expanduser().resolve()]
            rows.append(row)
        page_entries.append({"who": entry["who"], shape: rows, **{s: entry.get(s, PATH_NAMES[s]) for s in SIDES}})
    data = {"id": rec["id"], "version": n, "agent": rec["agent"], "title": card["title"], "why": card["why"],
            "link": rec.get("link"), "entries": page_entries, "images": list(names.values())}
    template = files("approvals").joinpath("journey.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    (folder / "index.html").write_text(template.replace("__JOURNEY_DATA__", payload), encoding="utf-8")
