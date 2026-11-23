"""The morning list: once a day, one message in the approvals channel naming every card that waits on the
approver, kept current all day as they answer. The approver gets a fresh one any time by posting in the channel,
and answers a card from it in one tap: Approve, Deny, or Remove it from the list.

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
import threading
from datetime import datetime, timedelta

from . import cards, config, core, slackapi, store

log = logging.getLogger("approvals")

HOUR = 8                        # posted at 8:00 am in the workspace's timezone
WAITING = {"pending", "question"}
GROUPS = ["🔴 *Expires today*", "🟡 *Expires tomorrow*", "⚪ *Later*"]


_lock = threading.Lock()        # the sweep and a button press's follow-up both edit the list


def _path(target=None):
    if target is None:
        return config.HOME / "digest.json"
    return config.HOME / f"digest-{target}.json"


def load_state(target=None):
    try:
        return json.loads(_path(target).read_text())
    except FileNotFoundError:
        return None


def _save_state(st, target=None):
    fd, tmp = tempfile.mkstemp(dir=config.HOME, prefix=".tmp-digest-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, _path(target))


def waiting(rec):
    """Waiting on the approver: a card or question he hasn't answered, or a yes with a condition only he can clear."""
    return not cards.handed(rec) and ((rec["status"] in WAITING and not cards.waiting_on(rec)) or bool(cards.on_you(rec)))


def _ours(rec):
    """Real cards on the real list; a test run's list covers only TEST cards."""
    return bool(rec.get("thread_ts")) and bool(rec.get("test")) == config.is_test_run()


def _last(rec, *kinds):
    at = [e["at"] for e in rec.get("events") or [] if e["kind"] in kinds]
    return datetime.fromisoformat(at[-1]) if at else None


def waiting_since(rec):
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
    if cards.handed(rec):
        send = cards.also_on(rec, len(rec["versions"]))
        return f"⏳ You handed it to {cards.esc(send['name'])}, {_at(store.parse(send['at']), day)}"
    s, d = rec["status"], rec.get("decision") or {}
    if cards.auto_approved(rec):
        return f"🤖 Auto-approved by {cards.esc(d['by'])}, {_at(datetime.fromisoformat(d['at']), day)}"
    member = cards.teammate(rec, d)
    who = cards.esc(member) if member else "You"
    if s in ("approved", "done"):
        return f"✅ {who} approved it, {_at(datetime.fromisoformat(d['at']), day)}" if d.get("at") else "✅ Approved"
    if s == "declined":
        return f"❌ {who} declined it, {_at(datetime.fromisoformat(d['at']), day)}"
    if s == "revising":
        reply = cards.last_reply(rec)
        member = cards.teammate(rec, reply)
        who = cards.esc(member) if member else "You"
        at = store.parse(reply["at"]) if member else _last(rec, "feedback", "answer")
        return f"💬 {who} replied {_at(at, day)}; the agent is working on it"
    if s == "expired":
        return f"⌛ No answer by {_at(datetime.fromisoformat(rec['deadline']), day)}, so not approved"
    if s == "withdrawn" and cards.removed(rec):
        return f"🗑️ You removed it, {_at(datetime.fromisoformat(rec['withdrawn']['at']), day)}"
    if s == "withdrawn":
        return f"↩️ The agent withdrew it, {_at(datetime.fromisoformat(rec['withdrawn']['at']), day)}"
    return s


def _buttons(rec):
    """One tap answers a card from the list, as the card's own buttons would: Approve says yes to all of it (every
    plain row of a set, every item of a batch; a choice gets its options instead), Deny says no, and Remove takes it
    off the list without a verdict on the idea. A choice shown in pictures has no pick here: it's decided by looking."""
    c = rec["versions"][-1]["card"]
    n = len(rec["versions"])
    base = f"{rec['id']}|{n}"
    menu = cards.hand_menu(rec, n)
    if c.get("parts"):
        # Approve all covers two or more plain rows; every other open row (one that needs its own yes, a pick, or the
        # one plain row left) gets its own button, as on the card, named for its row when the line has several. Without
        # them a release card, whose rows all need their own yes, showed only Deny and Remove here.
        rows, left = cards.approve_all_rows(rec), cards.open_rows(rec)
        yes = [cards.button(f"Approve all {len(rows)}" if len(rows) == len(left) else f"Approve {len(rows)} of {len(left)}",
                            f"{base}|all", "primary")] if len(rows) >= 2 else []
        for j, p in left:
            if len(rows) < 2 or (j, p) not in rows:
                yes += _row_buttons(base, j, p, named=len(left) > 1)
        yes = yes[:cards.ACTIONS_MAX - 2 - bool(menu)]   # the card has the rest
    elif c.get("options"):
        yes = [] if any(o.get("image") for o in c["options"]) else [
            cards.button(cards.option_button(o), f"{base}|pick:{i}",
                         "primary" if o["name"] == c.get("pick") else None) for i, o in enumerate(c["options"])]
    else:
        yes = [cards.button(f"Approve all {len(c['items'])}" if c.get("items") else "Approve", f"{base}|approve", "primary")]
    return {"type": "actions", "block_id": f"list|{base}",
            "elements": yes + [cards.button("Deny", f"{base}|decline", "danger"), cards.button("Remove", f"{base}|remove")]
            + ([menu] if menu else [])}


def _row_buttons(base, j, p, named):
    """One row's yes on the list, the same press as its own buttons on the card: Approve, or its options."""
    title = " ".join(str(p["title"]).split())
    if p.get("options"):
        return [cards.button(f"{title}: {cards.option_button(o)}" if named else cards.option_button(o), f"{base}|p{j}:pick:{i}",
                             "primary" if o["name"] == p.get("pick") else None) for i, o in enumerate(p["options"])]
    return [cards.button(f"Approve {title}" if named else "Approve", f"{base}|p{j}:approve", "primary")]


def _line(rec, day, detail=None, team=False):
    """The card, linked, then one gray line; a card waiting on the approver's answer gets its buttons under them. A
    card no longer waiting is struck through."""
    emoji = cards.kind_mark(rec["versions"][-1]["card"])
    link = f"<{slackapi.message_url(rec['thread_ts'], **config.channel_args(rec))}|{rec['id']}>"
    if team and cards.handed(rec):
        send = cards.also_on(rec, len(rec["versions"]))
        name = cards.esc(send["name"])
        detail = f"{cards.esc(rec['agent'])} · with {name} since {_at(store.parse(send['at']), day)}"
        if response := cards.responded(rec, send):
            detail += f" · {name} responded {_at(store.parse(response['at']), day)}"
        else:
            detail += f" · back to you {_at(store.parse(send['return_at']), day)}"
        return [cards.md(f"{emoji} {link}  *{_title(rec)}*"), cards.ctx(detail), _buttons(rec)]
    if team or (detail is None and not waiting(rec)):
        outcome = "↩️ Back with you" if team and waiting(rec) else _outcome(rec, day)
        return [cards.md(f"{emoji} ~{link}  {_title(rec)}~"), cards.ctx(outcome)]
    if detail is None and rec["status"] not in WAITING:
        detail = f"{cards.esc(rec['agent'])} · ⏳ waiting on you: {cards.esc(cards.on_you(rec)['text'])}"
    buttons = []
    if detail is None:
        asked = "❓ asked you a question · " if rec["status"] == "question" else ""
        detail = (f"{cards.esc(rec['agent'])} · {asked}since {_at(waiting_since(rec), day)}"
                  f" · expires {_at(datetime.fromisoformat(rec['deadline']), day)}")
        buttons = [_buttons(rec)]
    return [cards.md(f"{emoji} {link}  *{_title(rec)}*"), cards.ctx(detail)] + buttons


def render(st, records):
    """The list's blocks, its notification text, and a hash of its content that ignores the 'updated' stamp."""
    day = datetime.fromisoformat(st["date"]).date()
    by_id = {r["id"]: r for r in records}
    listed = [by_id[i] for i in st["listed"] if i in by_id]
    team = [by_id[i] for i in st.get("team", []) if i in by_id]
    listed += [r for r in team if waiting(r)]
    closed = [by_id[i] for i in st["closed"] if i in by_id]
    still = sum(1 for r in listed if waiting(r))
    with_team = sum(1 for r in records if cards.handed(r))
    n = len(listed)
    if n and still == n:
        headline = f"{n} cards are waiting on you" if n > 1 else "1 card is waiting on you"
    elif still:
        headline = f"{still} of {n} still waiting on you"
    elif with_team:
        headline = f"Nothing is waiting on you · {with_team} with your team"
    elif n:
        headline = f"Nothing left waiting on you: all {n} are answered or closed"
    else:
        headline = "Nothing is waiting on you"
    # A list the approver just asked for needs no ping.
    mention = f"<@{config.target(st.get("target"))['approver']}> " if (listed or team or closed) and not st.get("asked") else ""
    icon = "📋" if st.get("asked") else "☀️"

    # Each line in order, a heading riding with the first line under it so it never shows alone.
    chunks = []
    for g, label in enumerate(GROUPS):
        rows = sorted((r for r in listed if _group(r, day) == g), key=lambda r: r["deadline"])
        chunks += [([cards.md(label)] if i == 0 else []) + _line(r, day) for i, r in enumerate(rows)]
    for i, r in enumerate(sorted(team, key=lambda r: r["deadline"])):
        chunks.append(([cards.md("⏳ *With your team*")] if i == 0 else []) + _line(r, day, team=True))
    for i, r in enumerate(sorted(closed, key=lambda r: r["deadline"])):
        head = [{"type": "divider"}, cards.md("⏰ *Closed with no answer since the last list*"),
                cards.ctx("The agent dropped these. A reply in the card's thread reopens it.")] if i == 0 else []
        expired = _last(r, "expired") or datetime.fromisoformat(r["deadline"])
        chunks.append(head + _line(r, day, None if r["status"] != "expired" else f"{cards.esc(r['agent'])} · closed {_at(expired, day)}"))

    top = [cards.ctx("🧪 *TEST list.* It covers TEST cards only and gets deleted after testing.")] if config.is_test_run() else []
    top.append(cards.md(f"{mention}{icon} *{headline}*"))
    room = cards.BLOCKS_MAX - len(top) - 3   # Slack's limit, less the date line, "…and N more" and the last line
    body = []
    for k, chunk in enumerate(chunks):
        if len(body) + len(chunk) > room:
            body.append(cards.ctx(f"…and {len(chunks) - k} more. Answer some, then post in the channel for a fresh list."))
            break
        body += chunk
    if listed or team or closed:
        body.append(cards.ctx("Approve, Deny or Remove answers a card right here; tap its number to read it first. "
                              "Each line crosses off when you answer it."))
    sub = day.strftime("%A, %b ") + str(day.day) + (" · soonest deadline first" if listed else "")
    content = hashlib.sha256(json.dumps(top + [cards.ctx(sub)] + body, sort_keys=True).encode()).hexdigest()
    if st.get("updated_at"):
        sub += " · updated " + _at(datetime.fromisoformat(st["updated_at"]), day)
    return top + [cards.ctx(sub)] + body, f"{mention}{icon} {headline}", content


def tick(now=None, asked=False, target=None):
    """Post the morning list once it's due, or a fresh list when the approver asked for one; otherwise keep the
    newest list current. Called every few seconds, and right after a button press so its line crosses off at once.
    State changes only after Slack accepts the post or edit, so a failure is retried next time."""
    with _lock:
        _tick(now, asked, target)


def _tick(now, asked, target=None):
    now = now or store.now()
    local = cards.local(now)
    selected = config.target(target)
    kw = {"channel_id": selected["channel"]} if target is not None else {}
    st = load_state(target) if target is not None else load_state()
    records = [r for r in store.all_records() if _ours(r) and r.get("target") == target]
    eight = local.replace(hour=HOUR, minute=0, second=0, microsecond=0)
    morning = local >= eight and (not st or datetime.fromisoformat(st["posted_at"]) < eight)
    if morning or asked:
        since = datetime.fromisoformat(st["posted_at"]) if st else now - timedelta(days=1)
        new = {
            "date": local.date().isoformat(), "asked": not morning, "target": target,
            "listed": [r["id"] for r in records if waiting(r)],
            "team": [r["id"] for r in records if cards.handed(r)],
            "closed": [r["id"] for r in records if r["status"] == "expired" and (_last(r, "expired") or now) >= since],
            "posted_at": store.iso(now), "updated_at": None,
        }
        blocks, text, content = render(new, records)
        sender = config.settings().get("list_as")
        sender = config.sender(sender, "list_as") if sender is not None else None
        new["ts"] = slackapi.post(blocks, text, sender=sender, **kw) if sender else slackapi.post(blocks, text, **kw)
        new["content"] = content
        _save_state(new, target) if target is not None else _save_state(new)
        log.info("%s posted: %d waiting, %d closed with no answer", "morning list" if morning else "list asked for",
                 len(new["listed"]), len(new["closed"]))
        if st:
            _retire(st, new, **kw)
        if morning and target is None:
            try:
                rendered = summary(store.deputy(), eight - timedelta(days=1), records)
                if rendered:
                    slackapi.post(*rendered, sender=sender) if sender else slackapi.post(*rendered)
                    log.info("the deputy's summary posted")
            except Exception as e:
                log.warning("the deputy's summary wasn't posted: %s", e)
        return
    if not st:
        return
    _, _, content = render(st, records)
    if content == st.get("content"):
        return
    st["updated_at"] = store.iso(now)
    blocks, text, content = render(st, records)
    slackapi.update(st["ts"], blocks, text, **kw)
    st["content"] = content
    _save_state(st, target) if target is not None else _save_state(st)
    log.info("list of %s updated", st["posted_at"])


def _retire(old, new, **kw):
    """Shrink the replaced list to a link to the new one, so the channel never shows two lists that disagree."""
    when = _at(datetime.fromisoformat(new["posted_at"]), datetime.fromisoformat(old["date"]).date())
    text = f"Replaced by <{slackapi.message_url(new['ts'], **kw)}|a newer list> posted {when}."
    try:
        slackapi.update(old["ts"], [cards.ctx(text)], text, **kw)
    except Exception as e:
        log.warning("couldn't shrink the replaced list %s: %s", old["ts"], e)


# ---------- the deputy's summary ----------

SECTION_MAX = 2900   # a section block holds 3,000 characters


def _switch(live):
    """The summary's one button: the approver's own switch for auto-approval."""
    b = (cards.button("Pause auto-approval", "deputy|0|pause") if live else
         cards.button("Turn auto-approval on", "deputy|0|live", "primary"))
    return {"type": "actions", "block_id": "deputy", "elements": [b]}


def switched(blocks, live, at):
    """The summary after its button: the other button, and who switched auto-approval when."""
    keep = [b for b in blocks if b.get("block_id") not in ("deputy", "deputy-mode")]
    line = cards.ctx(f"Auto-approval {'turned on' if live else 'paused'} by {config.settings()['approver_name']}, {cards.when(at)}")
    return keep + [dict(line, block_id="deputy-mode"), _switch(live)]


def _what_he_did(rec, n):
    """What the approver did on version n of a card the deputy would have approved, and which count it goes in:
    approved without a word (A), replied on or declined (B), still open or withdrawn (C)."""
    if cards.removed(rec):
        return "🗑️ you removed it", "B"
    if rec["status"] == "withdrawn":
        return "↩️ the agent withdrew it", "C"
    if core.plain_yes(rec, n):
        return "✅ you approved it", "A"
    posted = rec["versions"][n - 1].get("posted_at") or rec["created"]
    his = [e for e in rec.get("events") or [] if e["at"] >= posted and (
        e["kind"] in ("feedback", "answer", "comment")
        or e["kind"] in ("decision", "part") and e.get("version") == n and e.get("verdict") != "expired")]
    if config.settings()["team_answers_any"]:
        for e in his:
            if e.get("version") == n and (member := cards.teammate(rec, e)):
                mark, what = {"approved": ("✅", "approved it"), "chose": ("✅", "made a pick"),
                              "declined": ("❌", "declined it")}.get(e.get("verdict"), ("💬", "replied"))
                return f"{mark} {cards.esc(member)} {what}", "B"
    if any(e.get("verdict") == "declined" for e in his):
        return "❌ you declined it", "B"
    if his:
        return "💬 you replied", "B"
    if rec["status"] == "expired":
        return "⌛ no answer by its deadline", "C"
    return "⏳ still open", "C"


def _team_outcome(rec, send):
    if send.get("handed_back_at"):
        return "↩️ handed it back"
    if send.get("returned_at") and not send.get("returned_by"):
        return "↩️ came back to you unanswered"
    n = send["version"]
    since = send["at"] if send.get("handed") else send["sent_at"]
    answers = []
    for e in rec.get("events", []):
        if e["kind"] == "revised" and e["version"] > n:
            break
        if e["at"] < since or e.get("version", n) != n:
            continue
        if e["kind"] in ("feedback", "answer") or (e["kind"] in ("decision", "part") and e["verdict"] != "expired"):
            answers.append(e)
    if answers:
        if answers[0].get("by") != send["user"]:
            if config.settings()["team_answers_any"] and (member := cards.teammate(rec, answers[0])):
                return f"{cards.esc(member)} answered first"
            return "you answered first"
        verdict = next((e["verdict"] for e in answers if e.get("by") == send["user"] and "verdict" in e), None)
        return {"approved": "✅ approved it", "chose": "✅ approved it", "declined": "❌ declined it"}.get(verdict, "💬 replied")
    if n != len(rec["versions"]):
        return "A newer version replaced this one."
    if cards.removed(rec):
        return "you answered first"
    if rec["status"] == "withdrawn":
        return "withdrawn by its agent"
    if rec["status"] == "expired":
        return "⌛ no answer by its deadline"
    if cards.handed(rec) and cards.also_on(rec, n) is send and not cards.responded(rec, send):
        return f"⏳ with {cards.esc(send['name'])}, back to you {cards.when(store.parse(send['return_at']))}"
    return "⏳ still open"


def _summary_sections(chunks, room):
    body, lines, shown = [], [], 0
    for text in chunks:
        if lines and len("\n".join(lines + [text])) > SECTION_MAX:
            body.append(cards.md("\n".join(lines)))
            lines = []
            if len(body) == room:
                break
        lines.append(text)
        shown += 1
    if lines:
        body.append(cards.md("\n".join(lines)))
    return body, len(chunks) - shown


def summary(d, since, records):
    """The deputy's calls since yesterday morning, posted right after the morning list as a snapshot, not kept
    current: (blocks, text), or None when there's no deputy or it made no call. With auto-approval on, every card it
    approved; in a dry run, every card it would have approved and what the approver did instead. Only facts from the
    records, grouped by asking agent."""
    if not d:
        return None
    latest = {}
    team = {}
    name, live = cards.esc(d["reach"]["agent"]), bool(d.get("live"))
    for r in records:
        calls = [c for c in r.get("calls") or [] if datetime.fromisoformat(c["at"]) >= since]
        if calls:
            latest[r["id"]] = (r, calls[-1])
        if live:
            sends = [s for s in r.get("also", []) if (at := s["at"] if s.get("handed") else s.get("sent_at"))
                     and store.parse(at) >= since and not s.get("skipped")]
            if sends:
                team[r["id"]] = (r, sends[-1])
    if not latest and not team:
        return None
    yeses = [(r, c) for r, c in latest.values() if c["call"] == "approve" and bool(c["live"]) == live]
    groups, counts = {}, {"A": 0, "B": 0, "C": 0}
    for r, c in sorted(yeses, key=lambda x: x[0]["id"]):
        link = f"<{slackapi.message_url(r['thread_ts'])}|{r['id']}>"
        title = " ".join(str(r["title"]).split())
        line = f"{link} {cards.esc(title if len(title) <= 90 else title[:89].rstrip() + '…')} — {cards.esc(core.one_line(c['why']))}"
        pick = (r.get("decision") or {}).get("choice") if live and cards.auto_approved(r) else None
        if pick:
            line += f" · took the card's pick, {cards.esc(pick)}"
        if live and r.get("taken_back"):
            line = f"~{line}~ ↩️ taken back"
        elif not live:
            label, group = _what_he_did(r, c["version"])
            counts[group] += 1
            line += f" · {label}"
        groups.setdefault(r["agent"], []).append(f"• {line}")
    n = len(yeses)
    if live:
        left = [(r, c) for r, c in latest.values() if c["call"] == "his"]
        took = sum(1 for r, _ in yeses if r.get("taken_back"))
        plain = sum(1 for r, c in left if core.plain_yes(r, c["version"]))
        headline = f"🤖 *{n} card{'s were' if n != 1 else ' was'} approved for you since yesterday morning*"
        sub = f"By {name} · you took back {took} · it left {len(left)} for you, and you approved {plain} of those without a word"
    else:
        headline = f"🤖 *Dry run: {name} would have approved {n} of the {len(latest)} cards since yesterday morning*"
        answered = (f"{counts['B']} had other answers" if config.settings()["team_answers_any"]
                    else f"replied on or declined {counts['B']}")
        sub = (f"Of those {n}, you approved {counts['A']} without a word, {answered}, "
               f"and {counts['C']} are still open or were withdrawn")
    top = [cards.ctx("🧪 *TEST summary.* It covers TEST cards only.")] if config.is_test_run() else []
    top += [cards.md(headline), cards.ctx(sub)]
    # Each agent's name rides with its first line, so it never shows alone.
    chunks = [(f"*{cards.esc(agent)}*\n" if i == 0 else "") + line for agent in sorted(groups) for i, line in enumerate(groups[agent])]
    room = cards.BLOCKS_MAX - len(top) - 3   # less "…and N more", the switch's line and its button
    body, more = _summary_sections(chunks, room - bool(team))
    if team:
        count = len(team)
        team_lines = []
        for rid, (r, send) in sorted(team.items()):
            link = f"<{slackapi.message_url(r['thread_ts'])}|{rid}>"
            title = cards.esc(" ".join(r["versions"][send["version"] - 1]["card"]["title"].split())[:140])
            handed = " · handed by you" if send.get("handed") else ""
            team_lines.append(f"• {link} {title} · {cards.esc(send['name'])}{handed} · {_team_outcome(r, send)}")
        team_lines[0] = f"👥 *{count} card{'s' if count != 1 else ''} also went to your team since yesterday morning*\n" + team_lines[0]
        part, left = _summary_sections(team_lines, room - len(body))
        body += part
        more += left
    if more:
        body.append(cards.ctx(f"…and {more} more"))
    return top + body + [_switch(live)], headline.replace("*", "")
