"""What happens to a request: decisions, replies, deadlines, card refreshes, messages to the agent."""
import json
import secrets
from datetime import datetime, timedelta

from . import cards, config, slackapi, store


def who():
    return config.settings()["approver_name"]

FINAL = {"approved", "declined", "expired", "done", "withdrawn"}
CLOSED = {"approved", "declined", "done", "withdrawn"}   # a reply now is about a settled request


def one_line(s):
    return " / ".join(part.strip() for part in str(s).splitlines() if part.strip())


def enqueue(rec, kind, text, react_ts=None):
    ref = secrets.token_hex(3)
    rec.setdefault("outbox", []).append({
        "ref": ref, "kind": kind, "text": f"{text} (ref {ref})", "created": store.iso(),
        "react_ts": react_ts, "attempts": 0, "next_try": store.iso(), "delivered_at": None,
        "noted_failure": False, "seen_by_agent": False,
        "needs_ack": kind != "expired", "acked_at": None, "ack_warned": False,
    })
    return ref


def ack_step(rec, what):
    return f'First acknowledge it in the thread, in your own words, with what you understood and {what}: `approvals ack {rec["id"]} "<your acknowledgement>"`.'


ACK_LABELS = {"decision": "answer", "feedback": "reply", "answer": "answer to your question", "comment": "reply"}


def sent(item):
    """It has gone to the agent: typed into its session (stamped as typing starts, since agents often acknowledge
    before the service has seen the message in their transcript), or read from its inbox."""
    return bool(item.get("sent_at") or item.get("delivered_at") or item.get("seen_by_agent"))


def mark_acked(rec):
    """Any post of the agent's on the card (an answer, a new version, a status line, a question, a withdrawal, done)
    shows it read what it was sent, so everything sent so far counts as acknowledged."""
    kinds = []
    for item in rec.get("outbox", []):
        if item.get("needs_ack") and not item.get("acked_at") and sent(item):
            item["acked_at"] = store.iso()
            kinds.append(item["kind"])
    return kinds


def acknowledge(rec, text, ts):
    """The agent posted its acknowledgement; every answer it had been sent so far counts as acknowledged. One still
    waiting to go doesn't, so it gets an acknowledgement of its own."""
    kinds = mark_acked(rec)
    store.event(rec, "ack", text=text, ts=ts, acknowledges=kinds)
    return kinds


def head(rec):
    n = len(rec["versions"])
    return f'[Approvals] {rec["id"]} v{n} "{rec["title"]}":'


def card(rec):
    return rec["versions"][-1]["card"]


def done_step(rec):
    return (f'run `approvals done {rec["id"]} "<what you did and how you checked it>"` once it is real, and post each step '
            f'before that on the card with `approvals status {rec["id"]} "<what happened>"`.')


def msg_decision(rec, verdict, where, choice=None, items=None, edited=None):
    t = cards.when(store.now())
    c = card(rec)
    if verdict == "approved" and choice is not None:
        o = next(o for o in c["options"] if o["name"] == choice)
        return (f'{head(rec)} {who()} picked {choice}' + (f' ("{o["title"]}")' if o.get("title") else "") + f' ({where}, {t}). '
                f'{ack_step(rec, "what you are about to do")} Then do exactly what the card describes for {choice}, nothing else, and {done_step(rec)}')
    if verdict == "approved" and items is not None:
        names = [cards.item_name(it) for it in c["items"]]
        keep = "; ".join(f"{i} {names[i - 1]}" for i in items)
        left = [f"{i} {x}" for i, x in enumerate(names, 1) if i not in items]
        what = f'approved all {len(names)} items' if not left else f'approved {len(items)} of the {len(names)} items'
        where = "Pick which in Slack" if where.startswith("picked") else where
        return (f'{head(rec)} {who()} {what} ({where}, {t}). Do these: {keep}.' + (f' Leave out: {"; ".join(left)}.' if left else '')
                + f' {ack_step(rec, "what you are about to do")} Then do exactly what the card describes for those items only, and {done_step(rec)}')
    if verdict == "approved" and edited is not None:
        return (f'{head(rec)} {who()} approved it with his own edited words ({where}, {t}). Use exactly these words, not the card\'s; '
                f'here they are as a JSON string: {json.dumps(edited, ensure_ascii=False)}. {ack_step(rec, "what you are about to do")} '
                f'Then do what the card describes with those words, and {done_step(rec)}')
    if verdict == "approved":
        return (f'{head(rec)} {who()} approved it ({where}, {t}). {ack_step(rec, "what you are about to do")} '
                f'Then do exactly what the card describes, and {done_step(rec)}')
    if verdict == "declined":
        return (f'{head(rec)} {who()} declined it ({where}, {t}). {ack_step(rec, "that you are dropping it")} '
                f'Then drop this proposal and don\'t send it again. If what it was for still matters, a different proposal is a new card.')
    said = f' The card said that with no answer, {c["if_no_answer"]}: do that.' if c.get("if_no_answer") else ""
    return f'{head(rec)} No answer by the deadline, so it is not approved.{said} Don\'t resubmit or send a reminder; a reply in the thread would reopen it.'


def _others(rec, skip):
    """Where the card's other rows stand, so a row's answer can't be read as the whole card's."""
    c = card(rec)
    answers = rec.get("answers") or {}
    groups = {}
    for j, p in enumerate(c["parts"], 1):
        if j in skip:
            continue
        a = answers.get(cards.part_id(p, j))
        word = {None: "waiting", "approved": "approved", "declined": "declined", "chose": "answered",
                "change": "waiting for your next version", "expired": "closed with no answer"}[a and a["verdict"]]
        groups.setdefault(word, []).append(str(j))
    if not groups:
        return "no other parts are open"
    return ", ".join(f'part{"s" if len(js) > 1 else ""} {" and ".join([", ".join(js[:-1]), js[-1]] if len(js) > 1 else js)} '
                     f'{"are" if len(js) > 1 else "is"} {word}' for word, js in groups.items())


def msg_part(rec, j, a):
    c = card(rec)
    p = c["parts"][j - 1]
    n_parts = len(c["parts"])
    t = cards.when(store.now())
    rid = rec["id"]
    where = "button in Slack" if a.get("where") == "button" else a.get("where") or "Slack"
    top = f'{head(rec)} part {j} of {n_parts} "{p["title"]}":'
    scope = f'This answer is for part {j} only; {_others(rec, {j})}.'
    if a["verdict"] == "approved":
        return (f'{top} {who()} approved this part ({where}, {t}). {scope} {ack_step(rec, "what you are about to do")} '
                f'Then do exactly what part {j} describes, nothing more; post its progress with `approvals status {rid} "<what happened>" --part {j}`, '
                f'and run `approvals done {rid} "<what you did and how you checked it>"` once every approved part is real.')
    if a["verdict"] == "chose":
        return (f'{top} {who()} chose "{a["choice"]}" for this part ({where}, {t}). {scope} {ack_step(rec, "what you are about to do")} '
                f'Then do exactly that, nothing more; post its progress with `approvals status {rid} "<what happened>" --part {j}`.')
    if a["verdict"] == "declined":
        return (f'{top} {who()} declined this part ({where}, {t}). {scope} {ack_step(rec, "that you are dropping it")} '
                f'Then drop part {j} only, and don\'t send it again.')
    return (f'{top} {who()} asked for a change to this part ({where}, {t}): "{one_line(a.get("words") or "")}". {scope} '
            f'{ack_step(rec, "what you will change")} Then post the next version with `approvals revise {rid} <card.json>`, changing part {j} only '
            f'and saying what changed in "changes"; parts you leave exactly as they are keep their answers.')


def msg_all(rec, js):
    c = card(rec)
    t = cards.when(store.now())
    listed = "; ".join(f'part {j} "{c["parts"][j - 1]["title"]}"' for j in js)
    rest = _others(rec, set(js))
    return (f'{head(rec)} {who()} approved {len(js)} parts at once (Approve all in Slack, {t}): {listed}. Any other parts: {rest}. '
            f'{ack_step(rec, "what you are about to do")} Then do exactly what each of those parts describes, nothing more; post progress with '
            f'`approvals status {rec["id"]} "<what happened>" --part <n>`, and run `approvals done {rec["id"]} "<what you did>"` once every approved part is real.')


def msg_offer(rec, choice):
    c = card(rec)
    t = cards.when(store.now())
    if choice == "stop":
        return (f'{head(rec)} {who()} answered your offer ("{c["offer"]}") with "Stop asking for these" ({t}). '
                f'{ack_step(rec, "what you will stop asking for")} Then record it as a standing exception in your own rules, with today\'s date and '
                f'exactly what it covers, and from now on do that kind without a card. Anything outside it still gets a card.')
    return (f'{head(rec)} {who()} answered your offer ("{c["offer"]}") with "Keep asking" ({t}). '
            f'{ack_step(rec, "that you will keep asking")} Keep sending a card for each one.')


def msg_replies(rec, kind, texts, question=None):
    t = cards.when(store.now())
    said = " / ".join(f'"{one_line(x)}"' for x in texts)
    rid = rec["id"]
    one_reply = (f'Your reply is your acknowledgement: post nothing before it, and if it needs more than a few minutes of '
                 f'work, say first in one line what is coming and when, with `approvals ack {rid} "<what and when>"`.')
    if kind == "answer":
        return (f'{head(rec)} {who()} answered your question ("{one_line(question)}") in Slack at {t}: {said}. '
                f'Carry on, usually with `approvals revise {rid} <card.json>`. {one_reply}')
    if kind == "comment":
        return (f'{head(rec)} {who()} replied in Slack at {t}, after the decision: {said}. '
                f'If it asks something, answer it with `approvals answer {rid} "<the answer, verdict first>"`. '
                f'If it changes what you do, say so in one line with `approvals ack {rid} "<what changes>"`. No new card unless asked for one.')
    return (f'{head(rec)} {who()} replied in Slack at {t}: {said}. '
            f'If it asks a question, answer it with `approvals answer {rid} "<the answer, verdict first>"`: that hands the card back as it stands, with no new version. '
            f'If it asks for a change, post the next version with `approvals revise {rid} <card.json>` (say what changed in "changes"). '
            f'If you truly cannot go on without one fact, ask it with `approvals ask {rid} "<question>"`. {one_reply} '
            f'If the reply plainly approves the card as it stands, record it with `approvals record {rid} --approved --words "<their words>" --where "Slack thread"`, do it, then run `approvals done {rid} "<readback>"`. '
            f'If it plainly declines, record it with `approvals record {rid} --declined --words "<their words>" --where "Slack thread"` and drop it.')


# ---------- decisions and replies (called under the store lock via store.update) ----------

def _check(rec, n):
    if n != len(rec["versions"]):
        return "stale"
    if rec["status"] == "withdrawn":
        return "withdrawn"
    return None


def card_ts(rec):
    return rec["versions"][-1].get("copy_ts") or rec["thread_ts"]


def decide(rec, n, verdict, by, where="button", words=None, choice=None, items=None, edited=None):
    """Returns 'ok', 'stale', 'withdrawn' or 'already'. A decision belongs to the version it was made on, so
    a later version can be decided afresh. A pick (choice), a batch picked in a pop-up (items) and a yes with the
    approver's own words (edited) are yeses that say exactly what to do. On a set, a decision answers every open row."""
    bad = _check(rec, n)
    if bad:
        return bad
    if card(rec).get("parts"):
        rows = cards.open_rows(rec)
        if not rows:
            return "already"
        for j, _ in rows:
            r = answer_part(rec, n, j, verdict, by, where=where, words=words, tell=where == "button")
        return r
    d = rec.get("decision")
    if d and d.get("version") == n and d.get("verdict") in ("approved", "declined"):
        return "already"
    rec["decision"] = {"verdict": verdict, "version": n, "by": by, "where": where, "words": words, "at": store.iso()}
    rec["decision"].update({k: v for k, v in (("choice", choice), ("items", items), ("edited", edited)) if v is not None})
    rec["status"] = verdict
    rec["open_question"] = None
    rec["card_dirty"] = True
    store.event(rec, "decision", verdict=verdict, version=n, where=where, words=words, choice=choice, items=items, edited=edited)
    if where in ("button", "pop-up"):
        how = "button in Slack" if where == "button" else ("picked in a pop-up in Slack" if items is not None else "Edit the words in Slack")
        enqueue(rec, "decision", msg_decision(rec, verdict, how, choice, items, edited), react_ts=card_ts(rec))
    return "ok"


def answer_part(rec, n, j, verdict, by, where="button", words=None, choice=None, tell=True):
    """One row of a set answered on its own: approved, declined, chosen ('chose'), or sent back ('change').
    Returns 'ok', 'stale', 'withdrawn', 'already' or 'gone' (no such row)."""
    bad = _check(rec, n)
    if bad:
        return bad
    parts = card(rec).get("parts") or []
    if not 1 <= j <= len(parts):
        return "gone"
    pid = cards.part_id(parts[j - 1], j)
    answers = rec.setdefault("answers", {})
    if pid in answers:
        return "already"
    a = {"verdict": verdict, "version": n, "by": by, "where": where, "at": store.iso()}
    a.update({k: v for k, v in (("words", words), ("choice", choice)) if v is not None})
    answers[pid] = a
    store.event(rec, "part", part=j, verdict=verdict, version=n, where=where, words=words, choice=choice)
    if tell:
        kind = "feedback" if verdict == "change" else "decision"
        enqueue(rec, kind, msg_part(rec, j, a), react_ts=card_ts(rec))
    settle(rec)
    return "ok"


def approve_all(rec, n, by):
    """Approve every plain row still open, except rows that need their own yes. One message names them all."""
    bad = _check(rec, n)
    if bad:
        return bad
    rows = cards.approve_all_rows(rec)
    if not rows:
        return "already"
    for j, _ in rows:
        answer_part(rec, n, j, "approved", by, where="Approve all", tell=False)
    enqueue(rec, "decision", msg_all(rec, [j for j, _ in rows]), react_ts=card_ts(rec))
    return "ok"


def settle(rec):
    """A set's state follows its rows: waiting while any row is open, back with the agent while a row waits for
    its next version, otherwise approved if anything was, else declined (or closed, if nothing was answered)."""
    c = card(rec)
    if not c.get("parts") or rec["status"] in ("withdrawn", "done"):
        rec["card_dirty"] = True
        return
    answers = rec.get("answers") or {}
    states = [(answers.get(cards.part_id(p, j)) or {}).get("verdict") for j, p in enumerate(c["parts"], 1)]
    rec["card_dirty"] = True
    if None in states:
        rec["decision"] = None
        if rec["status"] != "question":
            rec["status"] = "pending"
        return
    if "change" in states:
        rec["decision"] = None
        rec["status"] = "revising"
        return
    verdict = ("approved" if {"approved", "chose"} & set(states)
               else "expired" if set(states) == {"expired"} else "declined")
    last = max(answers.values(), key=lambda a: a["at"])
    rec["decision"] = {"verdict": verdict, "version": len(rec["versions"]), "by": last.get("by"), "where": "rows", "words": None, "at": last["at"]}
    rec["status"] = verdict
    rec["open_question"] = None


def press(rec, n, what, by):
    """A button press on a card: what it says, applied to the record. Pop-ups are opened elsewhere."""
    c = rec["versions"][n - 1]["card"] if 1 <= n <= len(rec["versions"]) else {}
    if what in ("approve", "decline"):
        return decide(rec, n, "approved" if what == "approve" else "declined", by=by)
    if what.startswith("pick:"):
        i = int(what.split(":")[1])
        return decide(rec, n, "approved", by=by, choice=c["options"][i]["name"])
    if what == "all":
        return approve_all(rec, n, by)
    if what.startswith("offer:"):
        return answer_offer(rec, n, what.split(":")[1], by)
    if what.startswith("p"):
        j_s, act, *rest = what[1:].split(":")
        j = int(j_s)
        if act == "pick":
            return answer_part(rec, n, j, "chose", by, choice=c["parts"][j - 1]["options"][int(rest[0])]["name"])
        return answer_part(rec, n, j, "approved" if act == "approve" else "declined", by)
    return "gone"


def answer_offer(rec, n, choice, by):
    if rec.get("offer_answer"):
        return "already"
    rec["offer_answer"] = {"choice": choice, "by": by, "at": store.iso()}
    rec["card_dirty"] = True
    store.event(rec, "offer", choice=choice)
    enqueue(rec, "comment", msg_offer(rec, choice), react_ts=card_ts(rec))
    return "ok"


def ask_change(rec, n, words, by, j=None):
    """The Change button's box: on a row, that row goes back to the agent; on the card, it's feedback, exactly
    as a reply in the thread is."""
    bad = _check(rec, n)
    if bad:
        return bad
    if j:
        return answer_part(rec, n, j, "change", by, where="Change button in Slack", words=words)
    if rec["status"] in CLOSED:
        return "already"
    take_replies(rec, [(None, words)], react_ts=card_ts(rec))
    return "ok"


UNSET = object()


def add_status(rec, text, kind="done", part=None, headline=None, frame=UNSET):
    """Keep a posted card true without a new version: a line saying what happened ('done'), what comes next
    ('next'), a condition only the approver can clear ('on_you'), or the other side's reply to a message sent as
    him ('their_answer'); the answered card's title; or what the card is part of (frame: the initiative, or None
    when it stands alone). The card is redrawn in place, which notifies no one and leaves its answers alone."""
    if text:
        rec.setdefault("timeline", []).append({"text": text, "kind": kind, "part": part, "at": store.iso()})
    if headline:
        rec["headline"] = headline
    if frame is not UNSET:
        rec["frame"] = frame
    rec["card_dirty"] = True
    store.event(rec, "status", text=text, status_kind=kind, part=part, headline=headline,
                **({"frame": frame} if frame is not UNSET else {}))


def take_replies(rec, replies, react_ts=None):
    """replies: [(ts, text)] from the approver, oldest first."""
    texts = [t for _, t in replies]
    last_ts = react_ts or replies[-1][0]
    if rec["status"] in CLOSED:
        kind = "comment"
    elif rec.get("open_question"):
        kind = "answer"
    else:
        kind = "feedback"
    q = (rec.get("open_question") or {}).get("text")
    for ts, t in replies:
        store.event(rec, kind, text=t, ts=ts)
    if kind != "comment":
        if rec["status"] == "expired":
            rec["decision"] = None
            rec["card_dirty"] = True
        rec["status"] = "revising"
        rec["open_question"] = None
    enqueue(rec, kind, msg_replies(rec, kind, texts, q), react_ts=last_ts)
    return kind


def expire(rec):
    c = card(rec)
    if c.get("parts"):
        # Rows already answered keep their answers; only the open ones close.
        rows = cards.open_rows(rec)
        n = len(rec["versions"])
        for j, _ in rows:
            answer_part(rec, n, j, "expired", None, where="deadline", tell=False)
        store.event(rec, "expired", parts=[j for j, _ in rows])
        listed = "; ".join(f'part {j} "{p["title"]}"' for j, p in rows)
        said = f' The card said that with no answer, {c["if_no_answer"]}: do that.' if c.get("if_no_answer") else ""
        enqueue(rec, "expired", f'{head(rec)} No answer by the deadline on {listed}, so {"they are" if len(rows) > 1 else "it is"} not approved.{said} '
                                f'Answers already given on other parts stand. Don\'t resubmit or send a reminder; a reply in the thread would reopen it.')
        return
    rec["decision"] = {"verdict": "expired", "version": len(rec["versions"]), "by": None, "where": "deadline", "words": None, "at": store.iso()}
    rec["status"] = "expired"
    rec["card_dirty"] = True
    store.event(rec, "expired")
    enqueue(rec, "expired", msg_decision(rec, "expired", "deadline"))


# ---------- Slack side ----------

def mode_for(rec):
    return "final" if rec["status"] in FINAL and (rec.get("decision") or rec["status"] == "withdrawn") else "open"


def refresh_cards(rec):
    """Make every card message match the record. Raises if Slack fails; callers retry."""
    latest = len(rec["versions"])
    mode = mode_for(rec)
    slackapi.update(rec["thread_ts"], cards.card_blocks(rec, latest, mode), cards.card_text(rec, latest))
    for v in rec["versions"]:
        if not v.get("copy_ts"):
            continue
        m = mode if v["n"] == latest else "replaced"
        slackapi.update(v["copy_ts"], cards.card_blocks(rec, v["n"], m), cards.card_text(rec, v["n"]))


def refresh_and_clear(rid):
    rec = store.load(rid)
    refresh_cards(rec)

    def clear(r):
        r["card_dirty"] = False
    store.update(rid, clear)


def due(item):
    return not item.get("delivered_at") and datetime.fromisoformat(item["next_try"]) <= store.now()


# ---------- which machine delivers what ----------

STRAY_DAYS = 14


def machine_of(rec):
    """The machine the asker last ran an approvals command on; the hub's for cards from before machines had names."""
    return (rec.get("reach") or {}).get("machine") or config.hub()


TYPED_SECONDS = 90   # a message typed into a session should show up in its transcript by then, or it's typed again


def just_typed(item):
    """Typed in a moment ago and waiting to show up in the transcript. Hidden a little longer than the check that
    confirms it waits, so that check and the next pass never both act on it."""
    at = item.get("typed_at")
    return bool(at) and store.now() - datetime.fromisoformat(at) < timedelta(seconds=TYPED_SECONDS + 30)


def next_item(rec):
    """A request's first message not yet delivered or left in the inbox. Its later ones wait for it, so they
    arrive in order. One just typed in doesn't hold them up: Claude takes a typed message in at its next step, so
    the next is typed right behind it and the agent reads both at once, rather than acting on a yes for a few
    seconds before the "wait, don't send" behind it."""
    return next((i for i in rec.get("outbox", []) if not i.get("delivered_at") and not i.get("inbox_only")
                 and not just_typed(i)), None)


PACE_SECONDS = 120   # the longest a decision waits for its agent to acknowledge the answer before it


def held(records):
    """Decisions waiting for their agent to catch up, as {(request id, ref)}. Approvals pressed in a row would
    otherwise land in the agent within seconds of each other, before it has reacted to the first. So an agent (the
    session that asked) gets one decision at a time, oldest first, once it has acknowledged what it was sent last,
    or PACE_SECONDS after that if it never does. Replies in a thread never wait: the approver correcting himself
    should reach the agent at once, even mid-task."""
    cutoff = store.now() - timedelta(seconds=PACE_SECONDS)
    busy, waiting = set(), {}
    for rec in records:
        sid = (rec.get("reach") or {}).get("session_id")
        if not sid or not rec.get("thread_ts"):
            continue
        for i in rec.get("outbox", []):
            at = i.get("sent_at") or i.get("delivered_at")
            if i.get("needs_ack") and not i.get("acked_at") and at and datetime.fromisoformat(at) >= cutoff:
                busy.add(sid)
        item = next_item(rec)
        if item and item["kind"] == "decision" and due(item) and not item.get("sent_at"):
            behind = rec["outbox"][rec["outbox"].index(item) + 1:]
            if any(i["kind"] != "decision" and not i.get("delivered_at") for i in behind):
                continue   # he's replying on this card ("wait, don't send"): the reply can't wait, so the yes before it goes now
            waiting.setdefault(sid, []).append((item["created"], rec["id"], item["ref"]))
    out = set()
    for sid, items in waiting.items():
        items.sort()
        out |= {(rid, ref) for _, rid, ref in (items if sid in busy else items[1:])}
    return out


def to_deliver(records, machine):
    """What one machine delivers now: [request id, ref] of each request's next message that is due, for sessions
    on that machine, except decisions held for their agent to catch up; and [request id, ref, reach] of strays,
    messages the machine on the card couldn't deliver because the session wasn't there, for whichever machine has it
    to take. Pass every record: holding a decision depends on the agent's other cards."""
    due_now, strays = [], []
    cutoff = store.now() - timedelta(days=STRAY_DAYS)
    one_machine = not config.settings().get("hub")   # every card is this machine's, whatever name it gave then
    hold = held(records)
    for rec in records:
        if not rec.get("thread_ts"):
            continue
        item = next_item(rec)
        if item and due(item) and (rec["id"], item["ref"]) not in hold and (one_machine or machine_of(rec) == machine):
            due_now.append([rec["id"], item["ref"]])
        for i in rec.get("outbox", []):
            if (i.get("stray") and not i.get("delivered_at") and not i.get("seen_by_agent")
                    and datetime.fromisoformat(i["created"]) >= cutoff):
                strays.append([rec["id"], i["ref"], rec.get("reach") or {}])
    return due_now, strays


def claim(rec, ref, machine):
    """This machine has the stray's session, so the card's answers go to it from now on. False if another
    machine took it first."""
    item = next((i for i in rec.get("outbox", []) if i["ref"] == ref), None)
    if not item or not item.get("stray") or item.get("delivered_at") or item.get("seen_by_agent"):
        return False
    item.update(stray=False, inbox_only=False, next_try=store.iso())
    rec["reach"] = dict(rec.get("reach") or {}, machine=machine)
    store.event(rec, "claimed", ref=ref, machine=machine)
    return True
