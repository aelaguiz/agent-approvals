"""The morning list: once a day, one message in the approvals channel naming every card that waits on the
approver, kept current all day as they answer. The approver gets a fresh one any time by posting in the channel.

Cards pile up in the channel and the approver loses track of which still wait on them; one card closed
unanswered is one the agent drops. The list is what's left, so a line that stops waiting on them is struck
through with what happened rather than removed. Cards that arrive after the list is posted are in the channel
as usual and in the next list. Only the newest list is kept current; the one it replaces shrinks to a link to it.
"""
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta

from . import cards, config, slackapi, store

log = logging.getLogger("approvals")

HOUR = 8                        # posted at 8:00 am in the workspace's timezone
WAITING = {"pending", "question"}
MAX_LINES = 18                  # two blocks a line; Slack takes 50 blocks a message
GROUPS = ["🔴 *Expires today*", "🟡 *Expires tomorrow*", "⚪ *Later*"]


def _path():
    return config.HOME / "digest.json"


def load_state():
    try:
        return json.loads(_path().read_text())
    except FileNotFoundError:
        return None


def _save_state(st):
    fd, tmp = tempfile.mkstemp(dir=config.HOME, prefix=".tmp-digest-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, _path())


def waiting(rec):
    """Waiting on the approver: a card or question he hasn't answered, or a yes with a condition only he can clear."""
    return rec["status"] in WAITING or bool(cards.on_you(rec))


def _ours(rec):
    """Real cards on the real list; a test run's list covers only TEST cards."""
    return bool(rec.get("thread_ts")) and bool(rec.get("test")) == config.is_test_run()


def _last(rec, *kinds):
    at = [e["at"] for e in rec.get("events") or [] if e["kind"] in kinds]
    return datetime.fromisoformat(at[-1]) if at else None


def _waiting_since(rec):
    """When it last came to the approver: the card, its newest version, or the agent's question."""
    return _last(rec, "revised", "question") or datetime.fromisoformat(rec["created"])


def _at(dt, day):
    """'4:47 pm' on the list's own day, 'Sat 4:47 pm' within the week, 'Nov 9 4:47 pm' beyond it."""
    d = cards.local(dt)
    t = d.strftime("%I:%M %p").lstrip("0").lower()
    if d.date() == day:
        return t
    if abs((d.date() - day).days) < 6:
        return d.strftime("%a ") + t
    return d.strftime("%b ") + str(d.day) + " " + t


def _group(rec, day):
    d = cards.local(datetime.fromisoformat(rec["deadline"])).date()
    return 0 if d <= day else 1 if d == day + timedelta(days=1) else 2


def _title(rec):
    t = cards.esc(" ".join(str(rec["title"]).split()))
    return t if len(t) <= 140 else t[:137].rstrip() + "…"


def _outcome(rec, day):
    """What happened to a card that no longer waits on the approver."""
    s, d = rec["status"], rec.get("decision") or {}
    if s in ("approved", "done"):
        return f"✅ You approved it, {_at(datetime.fromisoformat(d['at']), day)}" if d.get("at") else "✅ Approved"
    if s == "declined":
        return f"❌ You declined it, {_at(datetime.fromisoformat(d['at']), day)}"
    if s == "revising":
        return f"💬 You replied {_at(_last(rec, 'feedback', 'answer'), day)}; the agent is working on it"
    if s == "expired":
        return f"⌛ No answer by {_at(datetime.fromisoformat(rec['deadline']), day)}, so not approved"
    if s == "withdrawn":
        return f"↩️ The agent withdrew it, {_at(datetime.fromisoformat(rec['withdrawn']['at']), day)}"
    return s


def _line(rec, day, detail=None):
    """Two blocks: the card, linked, then one gray line. A card no longer waiting is struck through."""
    emoji = cards.TYPES[rec["versions"][-1]["card"]["type"]]["emoji"]
    link = f"<{slackapi.message_url(rec['thread_ts'])}|{rec['id']}>"
    if detail is None and not waiting(rec):
        return [cards.md(f"{emoji} ~{link}  {_title(rec)}~"), cards.ctx(_outcome(rec, day))]
    if detail is None and rec["status"] not in WAITING:
        detail = f"{cards.esc(rec['agent'])} · ⏳ waiting on you: {cards.esc(cards.on_you(rec)['text'])}"
    if detail is None:
        asked = "❓ asked you a question · " if rec["status"] == "question" else ""
        detail = (f"{cards.esc(rec['agent'])} · {asked}since {_at(_waiting_since(rec), day)}"
                  f" · expires {_at(datetime.fromisoformat(rec['deadline']), day)}")
    return [cards.md(f"{emoji} {link}  *{_title(rec)}*"), cards.ctx(detail)]


def render(st, records):
    """The list's blocks, its notification text, and a hash of its content that ignores the 'updated' stamp."""
    day = datetime.fromisoformat(st["date"]).date()
    by_id = {r["id"]: r for r in records}
    listed = [by_id[i] for i in st["listed"] if i in by_id]
    closed = [by_id[i] for i in st["closed"] if i in by_id]
    still = sum(1 for r in listed if waiting(r))
    n = len(listed)
    if n and still == n:
        headline = f"{n} cards are waiting on you" if n > 1 else "1 card is waiting on you"
    elif still:
        headline = f"{still} of {n} still waiting on you"
    elif n:
        headline = f"Nothing left waiting on you: all {n} are answered or closed"
    else:
        headline = "Nothing is waiting on you"
    # A list the approver just asked for needs no ping.
    mention = f"<@{config.settings()['approver']}> " if (listed or closed) and not st.get("asked") else ""
    icon = "📋" if st.get("asked") else "☀️"

    body, shown = [], 0
    for g, label in enumerate(GROUPS):
        rows = sorted((r for r in listed if _group(r, day) == g), key=lambda r: r["deadline"])
        if not rows:
            continue
        body.append(cards.md(label))
        for r in rows:
            if shown < MAX_LINES:
                body += _line(r, day)
            shown += 1
    if closed:
        body.append({"type": "divider"})
        body.append(cards.md("⏰ *Closed with no answer since the last list*"))
        body.append(cards.ctx("The agent dropped these. A reply in the card's thread reopens it."))
        for r in sorted(closed, key=lambda r: r["deadline"]):
            if shown < MAX_LINES:
                expired = _last(r, "expired") or datetime.fromisoformat(r["deadline"])
                body += _line(r, day, None if r["status"] != "expired" else f"{cards.esc(r['agent'])} · closed {_at(expired, day)}")
            shown += 1
    if shown > MAX_LINES:
        body.append(cards.ctx(f"…and {shown - MAX_LINES} more; `approvals list --open` has them all."))
    if listed or closed:
        body.append(cards.ctx("Tap a number to open its card. Each line crosses off when you answer it."))

    top = [cards.ctx("🧪 *TEST list.* It covers TEST cards only and gets deleted after testing.")] if config.is_test_run() else []
    top.append(cards.md(f"{mention}{icon} *{headline}*"))
    sub = day.strftime("%A, %b ") + str(day.day) + (" · soonest deadline first" if listed else "")
    content = hashlib.sha256(json.dumps(top + [cards.ctx(sub)] + body, sort_keys=True).encode()).hexdigest()
    if st.get("updated_at"):
        sub += " · updated " + _at(datetime.fromisoformat(st["updated_at"]), day)
    return top + [cards.ctx(sub)] + body, f"{mention}{icon} {headline}", content


def tick(now=None, asked=False):
    """Post the morning list once it's due, or a fresh list when the approver asked for one; otherwise keep the
    newest list current. Called every few seconds. State changes only after Slack accepts the post or edit, so a
    failure is retried next time."""
    now = now or store.now()
    local = cards.local(now)
    st = load_state()
    records = [r for r in store.all_records() if _ours(r)]
    eight = local.replace(hour=HOUR, minute=0, second=0, microsecond=0)
    morning = local >= eight and (not st or datetime.fromisoformat(st["posted_at"]) < eight)
    if morning or asked:
        since = datetime.fromisoformat(st["posted_at"]) if st else now - timedelta(days=1)
        new = {
            "date": local.date().isoformat(), "asked": not morning,
            "listed": [r["id"] for r in records if waiting(r)],
            "closed": [r["id"] for r in records if r["status"] == "expired" and (_last(r, "expired") or now) >= since],
            "posted_at": store.iso(now), "updated_at": None,
        }
        blocks, text, content = render(new, records)
        new["ts"] = slackapi.post(blocks, text)
        new["content"] = content
        _save_state(new)
        log.info("%s posted: %d waiting, %d closed with no answer", "morning list" if morning else "list asked for",
                 len(new["listed"]), len(new["closed"]))
        if st:
            _retire(st, new)
        return
    if not st:
        return
    _, _, content = render(st, records)
    if content == st.get("content"):
        return
    st["updated_at"] = store.iso(now)
    blocks, text, content = render(st, records)
    slackapi.update(st["ts"], blocks, text)
    st["content"] = content
    _save_state(st)
    log.info("list of %s updated", st["posted_at"])


def _retire(old, new):
    """Shrink the replaced list to a link to the new one, so the channel never shows two lists that disagree."""
    when = _at(datetime.fromisoformat(new["posted_at"]), datetime.fromisoformat(old["date"]).date())
    text = f"Replaced by <{slackapi.message_url(new['ts'])}|a newer list> posted {when}."
    try:
        slackapi.update(old["ts"], [cards.ctx(text)], text)
    except Exception as e:
        log.warning("couldn't shrink the replaced list %s: %s", old["ts"], e)
