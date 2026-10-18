"""The twelve card types, validation, and Block Kit rendering."""
from datetime import datetime
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


def validate(card, revision=False):
    errs = []
    t = card.get("type")
    if t not in TYPES:
        return [f"type must be one of: {', '.join(TYPES)}"]
    for key in ("title", "approves", "does_not_approve"):
        if not str(card.get(key, "")).strip():
            errs.append(f"{key} is required")
    facts = card.get("facts") or []
    if not facts or not all(isinstance(f, (list, tuple)) and len(f) == 2 and str(f[0]).strip() and str(f[1]).strip() for f in facts):
        errs.append("facts must be a list of [label, value] pairs, at least one")
    quotes = card.get("quotes") or []
    for need in TYPES[t]["needs"]:
        if need == "media" and not (card.get("image") or card.get("video")):
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
    if revision and not str(card.get("changes", "")).strip():
        errs.append("changes is required on a new version: say what changed from the last one")
    return errs


# ---------- formatting helpers ----------

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def local(dt):
    return dt.astimezone(ZoneInfo(config.settings()["timezone"]))


def when(dt):
    """'Sun 3:22 pm' in Central."""
    d = local(dt)
    return d.strftime("%a ") + d.strftime("%I:%M %p").lstrip("0").lower()


def header(text):
    text = text if len(text) <= 150 else text[:147] + "..."
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def md(text):
    text = text if len(text) <= 2990 else text[:2985] + "..."
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def ctx(text):
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def quote_block(q):
    label = esc(q.get("label", ""))
    head = f"*{label}:*"
    if q.get("link"):
        head += f" <{q['link']}|{esc(q.get('link_text') or 'open')}>"
    if q.get("note"):
        head += " · " + esc(q["note"])
    body = "\n".join("> " + esc(line) if line.strip() else ">" for line in str(q.get("text", "")).splitlines())
    return md(head + "\n" + body)


def table_block(table):
    cols = table["columns"]
    rows = [cols] + table["rows"]
    aligns = table.get("align") or (["left"] + ["right"] * (len(cols) - 1) if len(cols) > 2 else ["left"] * len(cols))
    return {
        "type": "table",
        "column_settings": [{"align": a, "is_wrapped": True} for a in aligns],
        "rows": [[{"type": "raw_text", "text": str(c)} for c in r] for r in rows],
    }


def test_tag():
    return ctx("🧪 *TEST card.* Not a real request; it gets deleted after testing.")


def version_label(n):
    return f"v{n}"


# ---------- cards ----------

def card_blocks(rec, n, mode="open"):
    """Blocks for version n of a request.

    mode: "open" (buttons), "final" (decided or done, no buttons), "replaced" (a later version exists).
    """
    v = rec["versions"][n - 1]
    card = v["card"]
    t = TYPES[card["type"]]
    blocks = []
    if rec.get("test"):
        blocks.append(test_tag())

    title = card["title"]
    if mode == "final" and rec.get("decision"):
        mark = {"approved": "✅", "declined": "❌", "expired": "⌛"}[rec["decision"]["verdict"]]
        prefix = {"approved": "Approved: ", "declined": "Declined: ", "expired": "No answer: "}[rec["decision"]["verdict"]]
        head = f"{mark} {prefix}{title}"
    elif mode == "replaced":
        head = f"{t['emoji']} {version_label(n)} (replaced) · {title}"
    else:
        head = f"{t['emoji']} {title}" if n == 1 else f"{t['emoji']} {version_label(n)} · {title}"
    blocks.append(header(head))

    if n > 1 and card.get("changes"):
        blocks.append(md(f"*Changed from {version_label(n - 1)}:* {esc(card['changes'])}"))
    if v.get("image_file_id"):
        blocks.append({"type": "image", "slack_file": {"id": v["image_file_id"]}, "alt_text": esc(card.get("image_alt") or title)[:1900]})
    if v.get("video_url"):
        blocks.append(md(f"▶️ *<{v['video_url']}|Play the video>*" + (f" · {esc(card['video_note'])}" if card.get("video_note") else "")))
    for q in card.get("quotes") or []:
        blocks.append(quote_block(q))
    if card.get("table"):
        blocks.append(table_block(card["table"]))
    blocks.append(md("\n".join(f"*{esc(k)}:* {esc(val)}" for k, val in card["facts"])))
    blocks.append(md(f"*A yes approves:* {esc(card['approves'])}\n*It does not approve:* {esc(card['does_not_approve'])}"))

    deadline = when(datetime.fromisoformat(rec["deadline"]))
    base = f"From {esc(rec['agent'])} · {rec['id']} · {version_label(n)}"
    if mode == "open":
        yes = card.get("yes_label") or t["yes"]
        no = card.get("no_label") or t["no"]
        blocks.append({"type": "actions", "block_id": f"ap|{rec['id']}|{n}", "elements": [
            {"type": "button", "action_id": "approve", "style": t.get("yes_style", "primary"),
             "text": {"type": "plain_text", "text": "✅ " + yes, "emoji": True}, "value": f"{rec['id']}|{n}|approve"},
            {"type": "button", "action_id": "decline", **({"style": "danger"} if t.get("yes_style") != "danger" else {}),
             "text": {"type": "plain_text", "text": "❌ " + no, "emoji": True}, "value": f"{rec['id']}|{n}|decline"},
        ]})
        blocks.append(ctx(f"💬 *Feedback? Reply in the thread.*\n{base} · No answer by {deadline} means no"))
    elif mode == "final":
        d = rec.get("decision") or {}
        lines = []
        if d.get("verdict") == "approved":
            lines.append(f"*Approved by {config.settings()['approver_name']}, {when(datetime.fromisoformat(d['at']))}*" + (f" · {esc(d['where'])}" if d.get("where") and d["where"] != "button" else ""))
        elif d.get("verdict") == "declined":
            lines.append(f"*Declined by {config.settings()['approver_name']}, {when(datetime.fromisoformat(d['at']))}*" + (f" · {esc(d['where'])}" if d.get("where") and d["where"] != "button" else ""))
        elif d.get("verdict") == "expired":
            lines.append(f"*No answer by {deadline}, so not approved.* A reply in the thread reopens it.")
        if d.get("words"):
            lines.append(f"*Your words:* “{esc(d['words'])}”")
        if rec.get("done"):
            lines.append(f"*Done, {when(datetime.fromisoformat(rec['done']['at']))}:* {esc(rec['done']['text'])}")
        if lines:
            blocks.append(md("\n".join(lines)))
        blocks.append(ctx(f"💬 Reply in the thread with anything else\n{base}"))
    else:  # replaced
        latest = len(rec["versions"])
        blocks.append(ctx(f"Replaced by {version_label(latest)} in this thread; these buttons no longer act\n{base}"))
    return blocks


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


def question_blocks(rec, question):
    blocks = [test_tag()] if rec.get("test") else []
    blocks.append(md(f"❓ *{esc(rec['agent'])} asks:*\n{esc(question)}"))
    blocks.append(ctx(f"Reply in the thread · {rec['id']} · it waits on your answer, with no other reminder"))
    return blocks


def card_text(rec, n):
    card = rec["versions"][n - 1]["card"]
    return f"{TYPES[card['type']]['emoji']} {card['title']}" + (f" ({version_label(n)})" if n > 1 else "")
