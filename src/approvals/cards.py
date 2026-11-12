"""The twelve card types, the shapes a card can take, validation, and Block Kit rendering.

A card's `type` says what the action is (its emoji, button words and deadline). On top of that, one card is
answered one way: a yes or no, a pick among `options`, a yes per row of `parts`, or a yes to a list of `items`.
Pictures, sections, links and the rest can go on any card.
"""
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

TYPES = {
    "creative":       {"emoji": "🎨", "yes": "Run it",        "no": "Don't run",  "hours": 48, "needs": ["media"]},
    "reply":          {"emoji": "💬", "yes": "Post it",       "no": "Skip",       "hours": 8,  "needs": ["quotes2"]},
    "budget":         {"emoji": "💵", "yes": "Approve",       "no": "No",         "hours": 48, "needs": []},
    "purchase":       {"emoji": "🛒", "yes": "Buy it",        "no": "No",         "hours": 48, "needs": []},
    "money":          {"emoji": "🏦", "yes": "Approve",       "no": "No",         "hours": 48, "needs": []},
    "player_message": {"emoji": "📣", "yes": "Approve",       "no": "No",         "hours": 48, "needs": ["quotes1"]},
    "price":          {"emoji": "🏷️", "yes": "Set it live",   "no": "No",         "hours": 48, "needs": ["table"]},
    "experiment":     {"emoji": "🧪", "yes": "Approve",       "no": "No",         "hours": 48, "needs": []},
    "page":           {"emoji": "🏪", "yes": "Approve",       "no": "No",         "hours": 48, "needs": ["table"]},
    "content":        {"emoji": "🃏", "yes": "Put it live",   "no": "No",         "hours": 48, "needs": []},
    "email":          {"emoji": "✉️", "yes": "Send it",       "no": "Don't send", "hours": 48, "needs": ["quotes1"]},
    "account":        {"emoji": "👤", "yes": "Do it",         "no": "No",         "hours": 48, "needs": [], "yes_style": "danger"},
}

NEED_TEXT = {
    "media": "an image or a video (the thing being approved)",
    "quotes2": "two quotes: what we're answering, then our exact reply",
    "quotes1": "a quote with the exact words people will see",
    "table": "a table of today beside proposed",
}

# Slack's limits: a carousel holds 10 cards; a card's title 150 characters and its body 200; a button 75; a pop-up's
# text box 3,000; a message 50 blocks.
# A set is several cards posted together, each its own decision.
GALLERY_MAX, PARTS_MAX, OPTIONS_MAX, ITEMS_MAX, LINKS_MAX, SET_MAX = 10, 10, 10, 100, 5, 10
LABEL_MAX, NOTE_MAX, BUTTON_MAX, EDIT_MAX, BLOCKS_MAX, WHY_MAX = 150, 200, 75, 3000, 50, 400
SHAPES = ("parts", "options", "items")


# ---------- the parts of a card ----------

def part_id(part, j):
    """What a row is known by across versions: its own id, else its place (1, 2, 3)."""
    return str(part.get("id") or j)


def option_button(o):
    """An option with a title is named by a short key ("A") and its button says "Pick A"; one without a title is
    named by its action, which is its button."""
    return o.get("button") or (f"Pick {o['name']}" if o.get("title") else o["name"])


def item_name(it):
    return it if isinstance(it, str) else it[0]


def editable_quote(card):
    return next((i for i, q in enumerate(card.get("quotes") or []) if q.get("edit")), None)


def pictures(card):
    """(key, path, mark) for every picture the card uploads: the gallery's, each row's and each option's."""
    out = []
    for i, g in enumerate(card.get("gallery") or []):
        out.append((f"gallery:{i}", g["image"], g.get("mark")))
    for j, p in enumerate(card.get("parts") or [], 1):
        if p.get("image"):
            out.append((f"part:{j}", p["image"], None))
    for i, o in enumerate(card.get("options") or []):
        if o.get("image"):
            out.append((f"option:{i}", o["image"], None))
    return out


def in_carousel(card, key):
    """Pictures shown as carousel cards: a gallery of two or more, and options with pictures."""
    return (key.startswith("gallery:") and len(card.get("gallery") or []) > 1) or key.startswith("option:")


def gallery_videos(card):
    return [(f"gallery:{i}", g["video"]) for i, g in enumerate(card.get("gallery") or []) if g.get("video")]


# ---------- validation ----------

def _text(v, limit=None):
    return isinstance(v, str) and v.strip() and (limit is None or len(v) <= limit)


def _file(path):
    try:
        return bool(path) and Path(str(path)).expanduser().is_file()
    except OSError:
        return False


def validate(card, revision=False):
    errs = []
    t = card.get("type")
    if t not in TYPES:
        return [f"type must be one of: {', '.join(TYPES)}"]
    for key in ("title", "approves", "does_not_approve"):
        if not str(card.get(key, "")).strip():
            errs.append(f"{key} is required")
    if len(str(card.get("title", ""))) > LABEL_MAX:
        errs.append(f"title is the card's headline, which Slack cuts at {LABEL_MAX} characters: say the action, and put the rest in facts")
    if not _text(card.get("why"), WHY_MAX):
        errs.append(f"why is required, under {WHY_MAX} characters: one or two sentences, first on the card, saying what the "
                    "business gets and why now, in the approver's words. For a test, say what its result decides. If you "
                    "can't write it, or don't believe it, the card isn't ready to send")
    if "part_of" not in card or not (card["part_of"] is None or _text(card["part_of"], 120)):
        errs.append("part_of is required: the initiative this card belongs to, in the approver's words "
                    "(\"Payers keep using what they paid for\"), or null if it stands alone")
    facts = card.get("facts") or []
    if not facts or not all(isinstance(f, (list, tuple)) and len(f) == 2 and str(f[0]).strip() and str(f[1]).strip() for f in facts):
        errs.append("facts must be a list of [label, value] pairs, at least one")
    quotes = card.get("quotes") or []
    for need in TYPES[t]["needs"]:
        if need == "media" and not (card.get("image") or card.get("video") or card.get("gallery")):
            errs.append("needs " + NEED_TEXT[need])
        if need == "quotes2" and len(quotes) < 2:
            errs.append("needs " + NEED_TEXT[need])
        if need == "quotes1" and len(quotes) < 1:
            errs.append("needs " + NEED_TEXT[need])
        if need == "table" and not card.get("table"):
            errs.append("needs " + NEED_TEXT[need])
    table = card.get("table")
    if table:
        cols = table.get("columns") or []
        rows = table.get("rows") or []
        if not cols or not rows:
            errs.append("table needs columns and at least one row")
        for r in rows:
            if len(r) != len(cols):
                errs.append("every table row needs one cell per column")
                break
            if any(not str(c).strip() for c in r):
                errs.append("table cells can't be empty (Slack refuses the whole card); write why a value is missing")
                break
    ev = card.get("evidence")
    if ev is not None and not (isinstance(ev, str) or (isinstance(ev, list) and all(isinstance(x, str) for x in ev))):
        errs.append("evidence must be text, or a list of text items: notes, links, or file paths")
    if revision and not str(card.get("changes", "")).strip():
        errs.append("changes is required on a new version: say what changed from the last one")
    errs += _validate_shapes(card)
    if not errs and (n := len(card_blocks(_stub(card), 1, "open"))) > BLOCKS_MAX:
        errs.append(f"too much for one card: it needs {n} Slack blocks and a message holds {BLOCKS_MAX}; "
                    "move detail to evidence, or send it as a set of cards, one decision each")
    return errs


def _validate_shapes(card):
    errs = []
    used = [k for k in SHAPES if card.get(k)]
    if len(used) > 1:
        errs.append(f"a card is answered one way: use one of {', '.join(used)}, not several")
    for key in ("image", "video"):
        if card.get(key) and not _file(card[key]):
            errs.append(f"{key}: no file at {card[key]}")
    for key in ("if_no_answer", "from", "change_label", "pick_label", "offer"):
        if key in card and not _text(card[key], 300):
            errs.append(f"{key} must be text, under 300 characters")
    for key in ("yes_label", "no_label", "change_label"):
        if card.get(key) and len(card[key]) > BUTTON_MAX:
            errs.append(f"{key} is a button: keep it under {BUTTON_MAX} characters")

    g = card.get("gallery")
    if g is not None:
        if not isinstance(g, list) or not 1 <= len(g) <= GALLERY_MAX:
            errs.append(f"gallery must be a list of 1 to {GALLERY_MAX} pictures")
        else:
            for i, x in enumerate(g, 1):
                if not isinstance(x, dict) or not _file(x.get("image")):
                    errs.append(f"gallery picture {i}: image must be the path of a picture file that exists")
                    continue
                if not _text(x.get("label"), LABEL_MAX):
                    errs.append(f"gallery picture {i}: label is required, under {LABEL_MAX} characters "
                                "(what it is: today or after, real or mock, which phone and build)")
                if x.get("note") is not None and not _text(x["note"], NOTE_MAX):
                    errs.append(f"gallery picture {i}: note must be under {NOTE_MAX} characters")
                m = x.get("mark")
                if m is not None and not (isinstance(m, list) and len(m) == 4 and all(isinstance(v, (int, float)) and v >= 0 for v in m)):
                    errs.append(f"gallery picture {i}: mark is [x, y, width, height] in the picture's pixels")
                if x.get("video") and not _file(x["video"]):
                    errs.append(f"gallery picture {i}: no video file at {x['video']}")

    parts = card.get("parts")
    if parts is not None:
        if not isinstance(parts, list) or not 1 <= len(parts) <= PARTS_MAX:
            errs.append(f"parts must be a list of 1 to {PARTS_MAX} rows")
        else:
            ids = [part_id(p, j) for j, p in enumerate(parts, 1) if isinstance(p, dict)]
            if len(set(ids)) != len(ids):
                errs.append("each row needs its own id")
            for j, p in enumerate(parts, 1):
                if not isinstance(p, dict) or not _text(p.get("title"), LABEL_MAX):
                    errs.append(f"row {j}: title is required, under {LABEL_MAX} characters")
                    continue
                if p.get("image") and not _file(p["image"]):
                    errs.append(f"row {j}: no file at {p['image']}")
                for key in ("yes_label", "no_label"):
                    if p.get(key) and len(p[key]) > BUTTON_MAX:
                        errs.append(f"row {j}: {key} is a button: keep it under {BUTTON_MAX} characters")
                if p.get("options") is not None:
                    errs += _validate_options(p["options"], p.get("pick"), f"row {j}", pictures=False)
                for q in p.get("quotes") or []:
                    if not (isinstance(q, dict) and _text(q.get("label"), LABEL_MAX) and _text(q.get("text"), EDIT_MAX)):
                        errs.append(f"row {j}: each quote needs a label and its words, under {EDIT_MAX} characters")
                    elif q.get("button") or q.get("edit"):
                        errs.append(f"row {j}: a row's quote is drawn in the row, right above its buttons; it takes no button")
            if any(q.get("button") for q in card.get("quotes") or [] if isinstance(q, dict)):
                errs.append("on a card with rows, words a row is answered on go in that row's own quotes, drawn above its "
                            "buttons, never behind a Read button. Words that need a reading of their own make a card of "
                            "their own: send the cards together as a set (`approvals submit a.json b.json`)")

    if card.get("options") is not None:
        errs += _validate_options(card["options"], card.get("pick"), "options", pictures=True)

    items = card.get("items")
    if items is not None:
        if not isinstance(items, list) or not 2 <= len(items) <= ITEMS_MAX:
            errs.append(f"items must be a list of 2 to {ITEMS_MAX}")
        elif not all(_text(it, 150) or (isinstance(it, list) and len(it) == 2 and _text(it[0], 150) and _text(it[1], 150)) for it in items):
            errs.append("each item is its name, or [name, what differs], each under 150 characters")

    links = card.get("links")
    if links is not None and not (isinstance(links, list) and len(links) <= LINKS_MAX and all(
            isinstance(x, list) and len(x) == 2 and _text(x[0], BUTTON_MAX) and str(x[1]).startswith("https://") for x in links)):
        errs.append(f"links must be up to {LINKS_MAX} [label, https://…] pairs")

    for i, s in enumerate(card.get("sections") or [], 1):
        if not (isinstance(s, dict) and _text(s.get("title"), 150) and isinstance(s.get("lines"), list) and s["lines"]):
            errs.append(f"section {i}: needs a title and a list of lines")
            continue
        for line in s["lines"]:
            ok = (_text(line) or (isinstance(line, list) and len(line) == 2 and _text(line[0]) and _text(line[1]))
                  or (isinstance(line, dict) and _text(line.get("text")) and (line.get("tag") is None or _text(line["tag"], 40))))
            if not ok:
                errs.append(f"section {i}: each line is text, [label, value], or {{\"text\": …, \"tag\": …}}")
                break

    edits = [q for q in card.get("quotes") or [] if q.get("edit")]
    if len(edits) > 1:
        errs.append("only one quote can be editable: the words that go out")
    if edits and len(str(edits[0].get("text", ""))) > EDIT_MAX:
        errs.append(f"an editable quote must be under {EDIT_MAX} characters, Slack's limit for a text box")
    for q in card.get("quotes") or []:
        if q.get("button") is not None and not _text(q["button"], BUTTON_MAX):
            errs.append(f"a quote's button is its label: under {BUTTON_MAX} characters")
    return errs


def _validate_options(options, pick, where, pictures):
    errs = []
    if not isinstance(options, list) or not 2 <= len(options) <= OPTIONS_MAX:
        return [f"{where}: options must be a list of 2 to {OPTIONS_MAX}"]
    names = []
    for i, o in enumerate(options, 1):
        if not isinstance(o, dict) or not _text(o.get("name"), BUTTON_MAX):
            errs.append(f"{where}: option {i} needs a name, under {BUTTON_MAX} characters")
            continue
        names.append(o["name"])
        if o.get("title") is not None and not _text(o["title"], LABEL_MAX):
            errs.append(f"{where}: option {i}'s title must be under {LABEL_MAX} characters")
        if o.get("image"):
            if not pictures:
                errs.append(f"{where}: a row's options are buttons; put the picture on the row")
            elif not _file(o["image"]):
                errs.append(f"{where}: option {i}: no file at {o['image']}")
        if len(option_button(o)) > BUTTON_MAX:
            errs.append(f"{where}: option {i}'s button must be under {BUTTON_MAX} characters")
    if len(set(names)) != len(names):
        errs.append(f"{where}: each option needs its own name")
    if pick is not None and pick not in names:
        errs.append(f"{where}: pick must be one of the option names: {', '.join(names)}")
    return errs


def _stub(card):
    return {"id": "AP-0000", "agent": "agent", "deadline": datetime.now(ZoneInfo("UTC")).isoformat(), "status": "pending",
            "versions": [{"n": 1, "card": card, "files": {k: "F0" for k, _, _ in pictures(card)}}], "answers": {}}


def evidence_items(evidence):
    """One post per item; a single block of text is one item."""
    if not evidence:
        return []
    return [evidence] if isinstance(evidence, str) else [x for x in evidence if x.strip()]


# ---------- formatting helpers ----------

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def local(dt):
    return dt.astimezone(ZoneInfo(config.settings()["timezone"]))


def when(dt):
    """'Sun 3:22 pm' in Central."""
    d = local(dt)
    return d.strftime("%a ") + d.strftime("%I:%M %p").lstrip("0").lower()


def clock(dt):
    """'3:22 pm'."""
    return local(dt).strftime("%I:%M %p").lstrip("0").lower()


def header(text):
    text = text if len(text) <= 150 else text[:147] + "..."
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def md(text):
    text = text if len(text) <= 2990 else text[:2985] + "..."
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def ctx(text):
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def plain(text, limit=BUTTON_MAX):
    text = str(text)
    return {"type": "plain_text", "text": text if len(text) <= limit else text[:limit - 1] + "…", "emoji": True}


def button(text, value=None, style=None, url=None, action_id=None):
    b = {"type": "button", "text": plain(text), "action_id": action_id or (value or text)[:250]}
    if value:
        b["value"] = value
    if url:
        b["url"] = url
    if style:
        b["style"] = style
    return b


def quoted(text):
    return "\n".join("> " + esc(line) if line.strip() else ">" for line in str(text).splitlines())


def quote_block(q):
    label = esc(q.get("label", ""))
    head = f"*{label}:*"
    if q.get("link"):
        head += f" <{q['link']}|{esc(q.get('link_text') or 'open')}>"
    if q.get("note"):
        head += " · " + esc(q["note"])
    return md(head + "\n" + quoted(q.get("text", "")))


def table_block(table):
    cols = table["columns"]
    rows = [cols] + table["rows"]
    aligns = table.get("align") or (["left"] + ["right"] * (len(cols) - 1) if len(cols) > 2 else ["left"] * len(cols))
    return {
        "type": "table",
        "column_settings": [{"align": a, "is_wrapped": True} for a in aligns],
        "rows": [[{"type": "raw_text", "text": str(c)} for c in r] for r in rows],
    }


def full_size(text, rec, n, key):
    """A button to one picture at its own resolution. Pictures posted in the thread open there, in Slack's own viewer,
    where they show at full resolution and zoom; a pop-up shrinks every picture to its width (about 500 pixels), so
    a slide or a phone screen in one couldn't be read. An older card's pictures have no link and still open a pop-up."""
    ref = (rec["versions"][n - 1].get("files") or {}).get(key)
    if isinstance(ref, dict) and ref.get("url"):
        return button(text, url=ref["url"], action_id=f"full:{key}")
    return button(text, f"{rec['id']}|{n}|pic:{key}")


def picture(ref, alt, hero=False):
    """An uploaded picture: an older card's file id, or {"id", "url"}. A carousel card's picture only shows when
    named by its URL."""
    f = {"url": ref["url"]} if hero and isinstance(ref, dict) else {"id": ref["id"] if isinstance(ref, dict) else ref}
    return {"type": "image", "slack_file": f, "alt_text": str(alt)[:1900] or "picture"}


def test_tag():
    return ctx("🧪 *TEST card.* Not a real request; it gets deleted after testing.")


def version_label(n):
    return f"v{n}"


# ---------- what happened after the card went up ----------

def answer_line(a, deadline=None):
    """One row's answer, as the row shows it."""
    at = clock(datetime.fromisoformat(a["at"]))
    where = f" · {esc(a['where'])}" if a.get("where") not in (None, "button", "Approve all", "pop-up") else ""
    if a["verdict"] == "approved":
        return f"✅ *Approved* {at}{where}"
    if a["verdict"] == "declined":
        return f"❌ *Declined* {at}{where}" + (f": “{esc(a['words'])}”" if a.get("words") else "")
    if a["verdict"] == "chose":
        return f"✅ *{esc(a['choice'])}* · chosen {at}{where}"
    if a["verdict"] == "change":
        return f"✏️ *Change asked* {at}: “{esc(a.get('words') or '')}” · the next version comes back here"
    return f"⌛ No answer by {when(deadline) if deadline else at}, so not approved"


def _scope(rec, part):
    return [e for e in rec.get("timeline") or [] if e.get("part") == part]


def pending_lines(rec, part=None):
    """The newest 'next' and 'waiting on you' lines that nothing has happened after yet."""
    lines = _scope(rec, part)
    done_at = rec["done"]["at"] if part is None and rec.get("done") else ""
    last_done = max([i for i, e in enumerate(lines) if e["kind"] == "done"] or [-1])
    out = {}
    for i, e in enumerate(lines):
        if i > last_done and e["kind"] in ("next", "on_you", "waiting") and e["at"] > done_at:
            out[e["kind"]] = e
    return out


def removed(rec):
    """The approver took the card off his list with Remove: withdrawn, by him rather than its agent."""
    return rec.get("status") == "withdrawn" and bool((rec.get("withdrawn") or {}).get("by"))


def waiting_on(rec):
    """Someone other than the approver the card waits on, until a later line of what happened."""
    if rec.get("status") in ("withdrawn", "done", "declined"):
        return None
    return pending_lines(rec).get("waiting")


def on_you(rec):
    """A condition only the approver can clear, still standing: the card waits on him."""
    if rec.get("status") == "withdrawn":
        return None
    for part in [None] + [e.get("part") for e in rec.get("timeline") or [] if e.get("part")]:
        e = pending_lines(rec, part).get("on_you")
        if e:
            return e
    return None


def status_lines(rec, part=None):
    """The progress lines shown for the card, or for one row: what happened, then what comes next. A row's lines
    sit under its answer, so they carry no check of their own."""
    lines = [("" if part else "✅ ") + esc(e["text"]) for e in _scope(rec, part) if e["kind"] == "done"]
    nxt = pending_lines(rec, part).get("next")
    if nxt:
        lines.append(f"⏳ {esc(nxt['text'])}")
    return lines


def their_answer(rec):
    return next((e for e in reversed(rec.get("timeline") or []) if e["kind"] == "their_answer"), None)


# ---------- the card ----------

def card_blocks(rec, n, mode="open"):
    """Blocks for version n of a request.

    mode: "open" (buttons), "final" (answered or closed: the timeline), "replaced" (a later version exists: one line), or
    "view" (the whole card in a pop-up, without buttons).
    """
    v = rec["versions"][n - 1]
    card = v["card"]
    if mode == "final" and not card.get("parts"):
        return _final_blocks(rec, n)
    if mode == "replaced":
        return _replaced_blocks(rec, n)
    blocks = _card_blocks(rec, n, mode)
    # Each fact gets a block of its own, for room between them; a card that would pass Slack's limit that way keeps
    # its facts in one block instead.
    return blocks if len(blocks) <= BLOCKS_MAX else _card_blocks(rec, n, mode, compact=True)


def _card_blocks(rec, n, mode, compact=False):
    card = rec["versions"][n - 1]["card"]
    blocks = [test_tag()] if rec.get("test") else []
    title = card["title"]
    if mode == "final":   # a set of rows: the rows are the card, so it stays whole
        blocks.append(header(_final_head(rec, card)))
    else:
        blocks.append(header(title))
    frame = frame_line(rec, card)
    if frame:
        blocks.append(frame)
    if card.get("why"):
        blocks.append(md(f"*Why:* {esc(card['why'])}"))
    if n > 1 and card.get("changes"):
        blocks += _version_note(rec, n, card, mode)
    open_ = mode == "open"
    waits = waiting_on(rec)
    if waits and mode != "view":
        blocks.append(md(f"⏳ *Waiting on:* {esc(waits['text'])}"))
    blocks += _body(rec, n, mode, compact)
    if card.get("parts"):
        blocks += _rows(rec, n, mode)
    if mode == "final":
        blocks += _timeline_blocks(rec, card)
    elif mode == "open" and status_lines(rec):
        blocks.append(ctx("\n".join(status_lines(rec))))
    if mode != "final":
        if blocks[-1].get("type") != "divider":   # rows end on their own divider
            blocks.append({"type": "divider"})
        blocks.append(md(f"*Your yes covers:* {esc(card['approves'])}\n*It doesn't cover:* {esc(card['does_not_approve'])}"))
        if card.get("links"):
            blocks.append({"type": "actions", "block_id": "links", "elements": [
                button(label, url=url, action_id=f"link{i}") for i, (label, url) in enumerate(card["links"])]})
    if open_:
        row = _buttons(rec, n)
        if row:
            blocks.append({"type": "actions", "block_id": f"ap|{rec['id']}|{n}", "elements": row})
    blocks.append(_footer(rec, n, mode))
    return blocks


def has_frame(rec, card):
    return "frame" in rec or "part_of" in card


def set_pos(rec):
    """ "1 of 3" for a card posted as one of a set, else None."""
    s = rec.get("set")
    return f"{s['n']} of {s['of']}" if s else None


def frame_line(rec, card):
    """What the card belongs to, where it sits in its set, and who sent it. Part of is set in place on the record
    (`approvals status --part-of`), else the card's own. A card from before the frame line, never given one, has none."""
    pos = set_pos(rec)
    if not has_frame(rec, card) and not pos and not rec.get("basecamp"):
        return None
    part_of = (rec["frame"] if "frame" in rec else card["part_of"]) if has_frame(rec, card) else None
    bc = (rec.get("basecamp") or [{}])[0].get("url")
    if bc:   # the work's picture is in Basecamp: one tap from his decision to it
        head = [f"Part of: <{bc}|{esc(part_of or 'the work in Basecamp')}>"]
    else:
        head = [f"Part of: {esc(part_of)}"] if part_of else [] if pos else ["Stands alone"]
    return ctx(" · ".join(head + ([pos] if pos else []) + [esc(rec["agent"])]))


def _replaced_blocks(rec, n):
    """A version a later one replaced shrinks to one line, so the thread holds one whole card, the newest, and what's
    new is plain to see. The version as it was opens from its button."""
    blocks = [test_tag()] if rec.get("test") else []
    line = md(f"{version_label(n)}, replaced by {version_label(len(rec['versions']))} below.")
    line["accessory"] = button(f"See {version_label(n)}", f"{rec['id']}|{n}|see")
    return blocks + [line]


def _version_note(rec, n, card, mode):
    """What changed, the approver's words that led to it, and a button to the version before."""
    prev_at = rec["versions"][n - 2].get("posted_at") or ""
    said = [e for e in rec.get("events") or [] if e["kind"] in ("feedback", "answer") and e["at"] >= prev_at and e.get("text")]
    note = md(f"🔄 *Version {n}* · {esc(card['changes'])}")
    if mode != "view":   # a pop-up can't open another
        note["accessory"] = button(f"See {version_label(n - 1)}", f"{rec['id']}|{n - 1}|see")
    out = [note]
    if said:
        out.append(md(quoted(said[-1]["text"])))
    return out


def _body(rec, n, mode, compact=False):
    v = rec["versions"][n - 1]
    card = v["card"]
    blocks = []
    if card.get("from"):
        blocks.append(md(f"👤 *From {esc(card['from'])}*"))
    for q in card.get("quotes") or []:
        if not q.get("button"):
            blocks.append(quote_block(q))
    if v.get("image_file_id"):
        label = "The video's first frame: play it below" if card.get("video") else (card.get("image_alt") or card["title"])
        blocks.append(dict(picture(v["image_file_id"], card.get("image_alt") or card["title"]), title=plain(label, 1900)))
    if v.get("video_url"):
        blocks.append(md(f"▶️ *<{v['video_url']}|Play the video>*" + (f" · {esc(card['video_note'])}" if card.get("video_note") else "")))
    blocks += _gallery(rec, n, stacked=mode == "view")
    if card.get("options") and any(o.get("image") for o in card["options"]):
        blocks += _option_cards(rec, n, mode)
    for s in card.get("sections") or []:
        lines = [f"*{esc(s['title'])}*"]
        for line in s["lines"]:
            if isinstance(line, str):
                lines.append(esc(line))
            elif isinstance(line, dict):
                lines.append(esc(line["text"]) + (f"  ·  _{esc(line['tag'])}_" if line.get("tag") else ""))
            else:
                lines.append(f"*{esc(line[0])}:* {esc(line[1])}")
        blocks.append(md("\n".join(lines)))
    blocks.append({"type": "divider"})
    if compact:
        blocks.append(md("\n".join(f"*{esc(k)}:* {esc(val)}" for k, val in card["facts"])))
    else:
        blocks += [md(f"*{esc(k)}*\n{bullets(esc(val))}") for k, val in card["facts"]]
    if card.get("table"):
        blocks.append(table_block(card["table"]))
    if card.get("items"):
        blocks += _items(card, rec.get("decision") if mode == "final" else None)
    if mode == "view" and card.get("options") and not any(o.get("image") for o in card["options"]):
        blocks.append(md("\n".join(f"• {esc(option_button(o))}" + (" · *our pick*" if o["name"] == card.get("pick") else "")
                               for o in card["options"])))
    return blocks


def _gallery(rec, n, stacked=False):
    """One picture full width with its label; several as a carousel, each fitted to its card with a button that
    opens it full size. A pop-up can't hold a carousel, so there they stack."""
    v = rec["versions"][n - 1]
    card = v["card"]
    files, videos = v.get("files") or {}, v.get("videos") or {}
    shown = [(i, g) for i, g in enumerate(card.get("gallery") or []) if files.get(f"gallery:{i}")]
    if len(shown) == 1 or (stacked and shown):
        out = []
        for i, g in shown:
            out.append(dict(picture(files[f"gallery:{i}"], g["label"]), title=plain(g["label"], 1900)))
            extra = ([esc(g["note"])] if g.get("note") else []) + ([f"▶️ *<{videos[f'gallery:{i}']}|Play it>*"] if videos.get(f"gallery:{i}") else [])
            if extra:
                out.append(ctx(" · ".join(extra)))
        return out
    if not shown:
        return []
    slides = []
    for i, g in shown:
        key = f"gallery:{i}"
        # A card's title is cut to one line; its body and subtext wrap, so the label (which says real or mock, which
        # phone and build) goes in the body.
        c = {"type": "card", "block_id": f"g{i}", "hero_image": picture(files.get(key + ":card") or files[key], g["label"], hero=True),
             "body": {"type": "mrkdwn", "text": f"*{esc(g['label'])}*"[:NOTE_MAX]}}
        if g.get("note"):
            c["subtext"] = {"type": "mrkdwn", "text": esc(g["note"])[:NOTE_MAX]}
        c["actions"] = [full_size("Full size", rec, n, key)]
        if videos.get(key):
            c["actions"].append(button("▶ Play it", url=videos[key], action_id=f"play{i}"))
        slides.append(c)
    return [{"type": "carousel", "block_id": "gallery", "elements": slides}]


def _option_cards(rec, n, mode):
    """Options with pictures, side by side, each with its own button and the sender's pick marked."""
    v = rec["versions"][n - 1]
    card, files = v["card"], v.get("files") or {}
    slides = []
    for i, o in enumerate(card["options"]):
        c = {"type": "card", "block_id": f"o{i}", "body": {"type": "mrkdwn", "text": (f"*{esc(o['name'])}*" + (f" · {esc(o['title'])}" if o.get("title") else ""))[:NOTE_MAX]}}
        if files.get(f"option:{i}"):
            c["hero_image"] = picture(files.get(f"option:{i}:card") or files[f"option:{i}"], o.get("title") or o["name"], hero=True)
        if o["name"] == card.get("pick"):
            c["subtitle"] = {"type": "mrkdwn", "text": "🔹 *Our pick*"}
        if o.get("note"):
            c["subtext"] = {"type": "mrkdwn", "text": esc(o["note"])[:NOTE_MAX]}
        c["actions"] = [button(option_button(o), f"{rec['id']}|{n}|pick:{i}", "primary" if o["name"] == card.get("pick") else None)] if mode == "open" else []
        if files.get(f"option:{i}"):
            c["actions"].append(full_size("Full size", rec, n, f"option:{i}"))
        if not c["actions"]:
            del c["actions"]
        slides.append(c)
    if mode == "view":   # a pop-up can't hold a carousel
        out = []
        for i, o in enumerate(card["options"]):
            if files.get(f"option:{i}"):
                out.append(dict(picture(files[f"option:{i}"], o["name"]), title=plain(f"{o['name']} · {o.get('title') or ''}", 1900)))
        return out
    return [{"type": "carousel", "block_id": "options", "elements": slides}]


def _items(card, decision=None):
    chosen = set(decision["items"]) if decision and decision.get("items") is not None else None
    lines = []
    for i, it in enumerate(card["items"], 1):
        name, detail = (it, None) if isinstance(it, str) else it
        line = f"{i}. *{esc(name)}*" + (f" · {esc(detail)}" if detail else "")
        lines.append(line if chosen is None or i in chosen else f"~{line}~ · held back")
    return [md("\n".join(lines[k:k + 25])) for k in range(0, len(lines), 25)]


def _rows(rec, n, mode):
    """A set: each row with its own answer."""
    v = rec["versions"][n - 1]
    card, files = v["card"], v.get("files") or {}
    prev = rec["versions"][n - 2]["card"].get("parts") or [] if n > 1 else []
    prev_by_id = {part_id(p, j): p for j, p in enumerate(prev, 1)}
    answers = rec.get("answers") or {} if mode in ("open", "final", "view") else {}
    deadline = datetime.fromisoformat(rec["deadline"])
    out = [{"type": "divider"}]
    for j, p in enumerate(card["parts"], 1):
        pid = part_id(p, j)
        a = answers.get(pid)
        new = n > 1 and prev_by_id.get(pid) != p
        title = re.sub(rf"^{j}\s*[.·:)]\s*", "", p["title"])   # the card numbers its rows; drop a number the agent wrote too
        text = f"{'🆕 ' if new else ''}*{j} · {esc(title)}*" + (f"\n{esc(p['note'])}" if p.get("note") else "")
        sec = md(text)
        if files.get(f"part:{j}"):
            sec["accessory"] = picture(files[f"part:{j}"], p["title"])
        out.append(sec)
        out += [quote_block(q) for q in p.get("quotes") or []]   # the row's own words, right above its buttons
        under = ([answer_line(a, deadline)] if a else []) + status_lines(rec, pid)
        if under:
            out.append(ctx("\n".join(under)))
        if not a and mode == "open":
            if p.get("options") and p.get("pick"):
                out.append(ctx(f"🔹 *Our pick:* {esc(p['pick'])}"))
            out.append({"type": "actions", "block_id": f"ap|{rec['id']}|{n}|p{j}", "elements": _row_buttons(rec, n, j, p)})
        out.append({"type": "divider"})
    return out


def _row_buttons(rec, n, j, p):
    card = rec["versions"][n - 1]["card"]
    base = f"{rec['id']}|{n}|p{j}"
    if p.get("options"):
        row = [button(option_button(o), f"{base}:pick:{i}", "primary" if o["name"] == p.get("pick") else None)
               for i, o in enumerate(p["options"])]
        if p.get("no_label"):
            row.append(button(p["no_label"], f"{base}:decline", "danger"))
        return row
    row = [button(p.get("yes_label") or card.get("yes_label") or "Approve", f"{base}:approve", "primary")]
    if card.get("change_label"):
        row.append(button(card["change_label"], f"{base}:change"))
    row.append(button(p.get("no_label") or card.get("no_label") or "No", f"{base}:decline", "danger"))
    if (rec["versions"][n - 1].get("files") or {}).get(f"part:{j}"):
        row.append(full_size("See it", rec, n, f"part:{j}"))
    return row


def open_rows(rec, card=None):
    """(j, part) of each row still waiting on an answer."""
    card = card or rec["versions"][-1]["card"]
    answers = rec.get("answers") or {}
    return [(j, p) for j, p in enumerate(card.get("parts") or [], 1) if part_id(p, j) not in answers]


def approve_all_rows(rec):
    """The rows Approve all covers: plain yes-or-no rows still open, never a row that needs its own yes."""
    return [(j, p) for j, p in open_rows(rec) if not p.get("options") and not p.get("own_yes")]


def _buttons(rec, n):
    """The card's own buttons: a yes and a no, a pick, or a batch; for a set, Approve all."""
    card = rec["versions"][n - 1]["card"]
    t = TYPES[card["type"]]
    base = f"{rec['id']}|{n}"
    reads = [button(q["button"], f"{base}|read:{i}") for i, q in enumerate(card.get("quotes") or []) if q.get("button")]
    if card.get("parts"):
        rows = approve_all_rows(rec)
        return ([button(f"Approve all {len(rows)}", f"{base}|all", "primary")] if len(rows) >= 2 else []) + reads
    row = []
    if card.get("options"):
        if not any(o.get("image") for o in card["options"]):
            row = [button(option_button(o), f"{base}|pick:{i}", "primary" if o["name"] == card.get("pick") else None)
                   for i, o in enumerate(card["options"])]
    else:
        yes_style = t.get("yes_style", "primary")
        row.append(button(card.get("yes_label") or t["yes"], f"{base}|approve", yes_style))
        if card.get("items"):
            row.append(button("Pick which", f"{base}|items"))
    if card.get("change_label"):
        row.append(button(card["change_label"], f"{base}|change"))
    if editable_quote(card) is not None:
        row.append(button("Edit the words", f"{base}|edit"))
    row += reads
    no = card.get("no_label") or (None if card.get("options") else t["no"])
    if no:
        row.append(button(no, f"{base}|decline", "danger" if t.get("yes_style") != "danger" else None))
    return row


def _footer(rec, n, mode):
    card = rec["versions"][n - 1]["card"]
    base = f"{rec['id']} · {version_label(n)}" + ("" if has_frame(rec, card) or rec.get("set") else f" · From {esc(rec['agent'])}")
    if mode == "open":
        by = when(datetime.fromisoformat(rec["deadline"]))
        late = f"With no answer by {by}, {esc(card['if_no_answer'])}" if card.get("if_no_answer") else f"No answer by {by} means no"
        return ctx(f"{base} · {late} · 💬 Reply in the thread")
    return ctx(f"💬 Reply in the thread with anything else · {base}")


# ---------- the card after it's answered or closed ----------

def _verdict(rec):
    return "withdrawn" if rec.get("status") == "withdrawn" else (rec.get("decision") or {}).get("verdict")


def _final_head(rec, card):
    verdict = _verdict(rec)
    d = rec.get("decision") or {}
    if verdict in ("approved", None) and rec.get("headline"):
        return f"✅ {rec['headline']}"
    if verdict == "approved" and d.get("choice"):
        return f"✅ You picked {d['choice']}"
    if verdict == "approved" and d.get("items") is not None and len(d["items"]) < len(card.get("items") or []):
        return f"✅ Approved {len(d['items'])} of {len(card['items'])}: {card['title']}"
    if removed(rec):
        return f"🗑️ Removed: {card['title']}"
    mark = {"approved": "✅", "declined": "❌", "expired": "⌛", "withdrawn": "↩️"}.get(verdict, "✅")
    prefix = {"approved": "Approved: ", "declined": "Declined: ", "expired": "No answer: ", "withdrawn": "Withdrawn: "}.get(verdict, "")
    return f"{mark} {prefix}{card['title']}"


def _final_blocks(rec, n):
    """An answered card collapses to what happened and what's next; the card as asked opens from a button."""
    card = rec["versions"][n - 1]["card"]
    blocks = [test_tag()] if rec.get("test") else []
    blocks.append(header(_final_head(rec, card)))
    frame = frame_line(rec, card)
    if frame:
        blocks.append(frame)
    blocks += _timeline_blocks(rec, card)
    if card.get("offer") and _verdict(rec) == "approved":
        blocks += _offer_blocks(rec, n, card)
    blocks.append({"type": "actions", "block_id": f"ap|{rec['id']}|{n}|see", "elements": [
        button("See the card", f"{rec['id']}|{n}|see")]})
    blocks.append(_footer(rec, n, "final"))
    return blocks


def _timeline_blocks(rec, card):
    d = rec.get("decision") or {}
    who = config.settings()["approver_name"]
    blocks = []
    # His words on it: what he sent with the yes, else his latest reply in the thread since (a change, a condition).
    after = [e["text"] for e in rec.get("events") or [] if e["kind"] == "comment" and e.get("text") and e["at"] >= (d.get("at") or "")]
    words = d.get("edited") or d.get("words") or (after[-1] if after else None)
    if words:
        blocks.append(md(("*Your words:*\n" if d.get("edited") else "") + quoted(words)))
    wait = on_you(rec)
    if wait:
        blocks.append(md(f"⏳ *Waiting on you:* {esc(wait['text'])}"))
    lines = []
    where = f" · {esc(d['where'])}" if d.get("where") not in (None, "button", "pop-up", "rows") else ""
    at = when(datetime.fromisoformat(d["at"])) if d.get("at") else ""
    verdict = _verdict(rec)
    if verdict == "approved" and not card.get("parts"):
        verb = "Picked" if d.get("choice") else "Approved"
        line = f"✅ {verb} by {who}, {at}{where}"
        if d.get("items") is not None and card.get("items"):
            held = [item_name(it) for i, it in enumerate(card["items"], 1) if i not in d["items"]]
            line += f" · {len(d['items'])} of {len(card['items'])}" + (f" · held back: {esc(', '.join(held))}" if held else "")
        if d.get("edited"):
            line += ", with your edited words"
        lines.append(line)
    elif verdict == "declined" and not card.get("parts"):
        lines.append(f"❌ Declined by {who}, {at}{where}")
    elif verdict == "expired":
        lines.append(f"⌛ No answer by {when(datetime.fromisoformat(rec['deadline']))}, so not approved. A reply in the thread reopens it.")
    elif removed(rec):
        lines.append(f"🗑️ Removed from your list by {who}, {when(datetime.fromisoformat(rec['withdrawn']['at']))}. Nothing still waiting on it was approved.")
    elif verdict == "withdrawn" and rec.get("withdrawn"):
        w = rec["withdrawn"]
        lines.append(f"↩️ Withdrawn by {esc(rec['agent'])}, {when(datetime.fromisoformat(w['at']))}: {esc(w['text'])}")
    # What happened, in the order it happened (done included), then what comes next.
    happened = [(e["at"], 1, f"✅ {esc(e['text'])}") for e in _scope(rec, None) if e["kind"] == "done"]
    if rec.get("done"):
        happened.append((rec["done"]["at"], 0, f"✅ *Done, {when(datetime.fromisoformat(rec['done']['at']))}:* {esc(rec['done']['text'])}"))
    lines += [text for *_, text in sorted(happened, key=lambda x: x[:2])]
    nxt = pending_lines(rec).get("next")
    if nxt:
        lines.append(f"⏳ {esc(nxt['text'])}")
    if lines:
        blocks.append(md("\n".join(lines)))
    if card.get("from") and not card.get("items") and verdict == "approved":   # one message sent as him: their reply
        ans = their_answer(rec)
        blocks.append(md("*Their answer*\n" + (quoted(ans["text"]) if ans else "_No reply yet. It shows here when they answer._")))
    return blocks


def _offer_blocks(rec, n, card):
    ans = rec.get("offer_answer")
    if ans:
        said = "Stop asking for these" if ans["choice"] == "stop" else "Keep asking"
        return [ctx(f"💡 {esc(card['offer'])}\n*You said:* {said}, {when(datetime.fromisoformat(ans['at']))}")]
    return [md(f"💡 {esc(card['offer'])}"),
            {"type": "actions", "block_id": f"ap|{rec['id']}|{n}|offer", "elements": [
                button("Stop asking for these", f"{rec['id']}|{n}|offer:stop"),
                button("Keep asking", f"{rec['id']}|{n}|offer:keep")]}]


# ---------- pop-ups ----------

def _modal(title, blocks, meta, submit=None):
    view = {"type": "modal", "callback_id": "approvals", "private_metadata": meta,
            "title": plain(title, 24), "close": plain("Cancel", 24), "blocks": blocks}
    if submit:
        view["submit"] = plain(submit, 24)
    return view


def change_view(rec, n, j=None):
    """A box for what should change, on the card or one row of it."""
    card = rec["versions"][n - 1]["card"]
    what = f"Row {j}: {card['parts'][j - 1]['title']}" if j else card["title"]
    meta = f"{rec['id']}|{n}|" + (f"p{j}:change" if j else "change")
    return _modal("What should change?", [
        ctx(esc(what)),
        {"type": "input", "block_id": "words", "label": plain("What should change", 2000),
         "element": {"type": "plain_text_input", "action_id": "words", "multiline": True}},
        ctx("It goes to the agent, and into the card's thread."),
    ], meta, "Send")


def edit_view(rec, n):
    """The words that go out, in a box to change before they go."""
    card = rec["versions"][n - 1]["card"]
    q = card["quotes"][editable_quote(card)]
    return _modal("Edit before it goes", [
        {"type": "input", "block_id": "words", "label": plain(q.get("label") or "The words", 2000),
         "element": {"type": "plain_text_input", "action_id": "words", "multiline": True, "initial_value": str(q.get("text", ""))[:EDIT_MAX]},
         **({"hint": plain(q["hint"], 2000)} if q.get("hint") else {})},
    ], f"{rec['id']}|{n}|edit", card.get("yes_label") or TYPES[card["type"]]["yes"])


def items_view(rec, n, ticked=None):
    """Every item, ticked; the button sends the ticked ones and says how many."""
    card = rec["versions"][n - 1]["card"]
    items = card["items"]
    ticked = set(range(1, len(items) + 1)) if ticked is None else set(ticked)
    blocks = []
    for k in range(0, len(items), 10):
        opts = []
        for i in range(k + 1, min(k + 10, len(items)) + 1):
            it = items[i - 1]
            o = {"text": {"type": "mrkdwn", "text": f"*{esc(item_name(it))}*" + ("" if isinstance(it, str) else f" · {esc(it[1])}")}, "value": str(i)}
            opts.append(o)
        el = {"type": "checkboxes", "action_id": "items", "options": opts}
        chosen = [o for o in opts if int(o["value"]) in ticked]
        if chosen:
            el["initial_options"] = chosen
        blocks.append({"type": "input", "block_id": f"items{k // 10}", "optional": len(items) > 10, "dispatch_action": True,
                       "label": plain("Send these" if k == 0 else "And these", 2000), "element": el})
    label = (card.get("pick_label") or "Approve {n}").replace("{n}", str(len(ticked)))
    return _modal("Pick which", blocks, f"{rec['id']}|{n}|items", label)


def read_view(rec, n, i):
    q = rec["versions"][n - 1]["card"]["quotes"][i]
    text = str(q.get("text", ""))
    return _modal(q.get("button") or q.get("label") or "The words",
                  [md(f"*{esc(q.get('label', ''))}*")] + [md(esc(text[k:k + 2900])) for k in range(0, len(text), 2900)],
                  f"{rec['id']}|{n}|read:{i}")


def picture_view(rec, n, key):
    """One picture full size: a carousel slide, an option or a row's picture."""
    v = rec["versions"][n - 1]
    card = v["card"]
    kind, i = key.split(":")[0], int(key.split(":")[1])
    if kind == "gallery":
        g = card["gallery"][i]
        label, note = g["label"], g.get("note")
    elif kind == "option":
        o = card["options"][i]
        label, note = f"{o['name']} · {o.get('title') or ''}".strip(" ·"), o.get("note")
    else:
        p = card["parts"][i - 1]
        label, note = f"{i} · {p['title']}", p.get("note")
    blocks = [dict(picture(v["files"][key], label), title=plain(label, 1900))] + ([ctx(esc(note))] if note else [])
    return _modal("Full size", blocks, f"{rec['id']}|{n}|pic:{key}")


def see_view(rec, n):
    blocks = [b for b in card_blocks(rec, n, "view") if b["type"] != "header"]
    return _modal("The card as asked", [md(f"*{esc(rec['versions'][n - 1]['card']['title'])}*")] + blocks[:99], f"{rec['id']}|{n}|see")


# ---------- channel posts ----------

def root_revised_blocks(rec):
    """The first post, once a later version exists in the thread."""
    first = rec["versions"][0]["card"]
    latest = len(rec["versions"])
    blocks = [test_tag()] if rec.get("test") else []
    blocks.append(header(f"💬 Revised: {version_label(latest)} is in the thread"))
    last_fb = next((e for e in reversed(rec.get("events", [])) if e["kind"] in ("feedback", "answer")), None)
    lines = [f"~{esc(first['title'])}~"]
    if last_fb:
        lines.append(f"*Your last reply:* “{esc(last_fb['text'])}”")
    latest_card = rec["versions"][-1]["card"]
    lines.append(f"*{version_label(latest)}:* {esc(latest_card['title'])} · waiting for you in the thread")
    blocks.append(md("\n".join(lines)))
    blocks.append(ctx(f"From {esc(rec['agent'])} · {rec['id']} · v1's buttons no longer act"))
    return blocks


def new_version_blocks(rec, n):
    """A short line for the channel: a new version sits in an older thread, where nobody would see it."""
    card = rec["versions"][n - 1]["card"]
    blocks = [test_tag()] if rec.get("test") else []
    blocks.append(md(f"🔄 *{esc(rec['agent'])}* posted {version_label(n)}: {esc(card['title'])}"
                     + (f"\n{esc(card['changes'])}" if card.get("changes") else "")))
    blocks.append(ctx(f"Waiting for you in this thread, with its buttons · {rec['id']}"))
    return blocks


def bullets(text):
    """Lines the agent started with "- " or "* " read as Slack's bullets."""
    return re.sub(r"(?m)^[ \t]*[-*][ \t]+", "• ", text)


def answer_blocks(rec, text, at):
    """An answer in the thread: its first sentence, the verdict, in bold, then the facts behind it, and who answered
    in the small line under it."""
    body = bullets(esc(text.strip()))
    first = re.match(r"([^\n]+?[.!?])(\s|$)", body)
    if first and not body.startswith("*"):
        body = f"*{first.group(1)}*" + body[first.end(1):]
    blocks = [test_tag()] if rec.get("test") else []
    return blocks + [md(body), ctx(f"{esc(rec['agent'])} · answered · {rec['id']} · {when(at)}")]


def note_blocks(rec, name, text, at):
    """The reviewer's read of a card (the CFO's, of a money card), in its thread under the reviewer's name. It asks
    nothing and changes nothing on the card. Markdown's **bold** is Slack's *bold*."""
    blocks = [test_tag()] if rec.get("test") else []
    return blocks + [md(f"💬 *{esc(name)}:*\n{bullets(esc(text.strip().replace('**', '*')))}"),
                     ctx(f"{esc(name)} · a read for {esc(config.settings()['approver_name'])} before the answer; it changes nothing on the card · {rec['id']} · {when(at)}")]


def question_blocks(rec, question):
    blocks = [test_tag()] if rec.get("test") else []
    blocks.append(md(f"❓ *{esc(rec['agent'])} asks:*\n{esc(question)}"))
    blocks.append(ctx(f"Reply in the thread · {rec['id']} · it waits on your answer, with no other reminder"))
    return blocks


def card_text(rec, n):
    """The message's own text: the one line an iPhone notification shows."""
    card = rec["versions"][n - 1]["card"]
    text = f"{TYPES[card['type']]['emoji']} {rec['agent']}: {card['title']}" + (f" ({version_label(n)})" if n > 1 else "")
    if set_pos(rec):
        text += f" · {set_pos(rec)}"
    if card.get("parts") and (left := len(open_rows(rec, card))) and rec.get("status") not in ("withdrawn", "done"):
        text += f" · {left} to answer"
    if card.get("if_no_answer") and rec.get("status") in ("pending", "question"):
        text += f" · Answer by {when(datetime.fromisoformat(rec['deadline']))}. With no answer, {card['if_no_answer']}"
    return text
