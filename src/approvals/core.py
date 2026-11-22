"""What happens to a request: decisions, replies, deadlines, card refreshes, messages to the agent."""
import contextvars
import json
import logging
import re
import secrets
from datetime import datetime, time, timedelta

from . import cards, config, slackapi, store


# Where the answer being handled was given: Slack, or the pod dashboard (`approvals press`). The record is the same
# either way; only the messages that say where it came from name the place.
VIA = contextvars.ContextVar("via", default="Slack")


def said_in():
    return "on the pod dashboard" if VIA.get() == "dashboard" else "in Slack"


def who(rec):
    return config.card_target(rec)["approver_name"]

def can_answer(rec, user, *, allow_workspace_team=True):
    if user == config.card_target(rec)["approver"]:
        return True
    if allow_workspace_team:
        s = config.settings()
        if s["team_answers_any"] and (user == s["approver"] or any(p["user"] == user for p in s["team"].values())):
            return True
    send = cards.also_on(rec, len(rec.get("versions", [])))
    return bool(send and send["user"] == user and (send.get("handed") or send.get("sent_at"))
                and not (send.get("handed_back_at") or send.get("skipped")))


def answerer(rec, answer):
    return cards.teammate(rec, answer) or who(rec)


FINAL = {"approved", "declined", "expired", "done", "withdrawn"}
CLOSED = {"approved", "declined", "done", "withdrawn"}   # a reply now is about a settled request
REPLIES = {"feedback", "answer", "comment"}


def one_line(s):
    return " / ".join(part.strip() for part in str(s).splitlines() if part.strip())


def enqueue(rec, kind, text, react_ts=None, to=None, member=None, by=None):
    """A message for the agent that asked, or with to="reviewer" for the session reviewing the card's type, or
    to="deputy" for the deputy."""
    ref = secrets.token_hex(3)
    if member:
        send = cards.also_on(rec, len(rec["versions"]))
        if send and send["user"] == by and (send.get("handed") or send.get("sent_at")):
            first = f"{who(rec)} handed this card to {member}" if send.get("handed") else f"The card also went to {member}"
        elif by == config.target()["approver"]:
            first = f"{member} is the workspace's default approver"
        else:
            first = f"{member} is on {who(rec)}'s team"
        text += (f" {first}, and {member}'s answer counts as {who(rec)}'s, "
                 "for exactly what the card describes.")
    rec.setdefault("outbox", []).append({
        "ref": ref, "kind": kind, "text": f"{text} (ref {ref})", "created": store.iso(),
        "react_ts": react_ts, "attempts": 0, "next_try": store.iso(), "delivered_at": None,
        "noted_failure": False, "seen_by_agent": False,
        "needs_ack": kind not in ("expired", "review", "deputy", "handed", "again"), "acked_at": None, "ack_warned": False,
        **({"asks_again": True} if kind in REPLIES and not to else {}),
        **({"to": to} if to else {}),
        **({"answerer": member} if member else {}),
    })
    return ref


def tell_deputy(rec, news):
    """Every card reaches the deputy, which makes its own call on each: when it's posted, at each new version, with
    everything the approver says or does on it other than a plain yes (which it reads from the record), and when its
    agent withdraws it. Nothing waits on the deputy. TEST cards reach only a deputy set up in a test run, and the
    deputy's own cards don't come back to it."""
    d = store.deputy()
    if (rec.get("target") is not None or not d or bool(rec.get("test")) != bool(d.get("test"))
            or (rec.get("reach") or {}).get("session_id") == d["reach"].get("session_id")):
        return
    enqueue(rec, "deputy", f"{head(rec)} {news}", to="deputy")


def make_your_call(rec):
    rid = rec["id"]
    send = cards.also_on(rec, len(rec["versions"]))
    if send and send.get("handed"):
        name = send["name"]
        also = f" {who(rec)} handed this card to {name}, so --his leaves it with {name}; --also is not taken."
    else:
        also = also_instruction(rec.get("target"))
    return (f'Make your call: `approvals show {rid}` reads it, `approvals pictures {rid}` saves its pictures where you '
            f'can look at them, then `approvals call {rid} --approve --why "<why>"` or `approvals call {rid} --his --why "<why>"`.'
            + also)


def also_instruction(target=None):
    team = config.settings()["team"]
    return (" Add `--also <name>` beside `--his` to also send the card to that person "
            f"({', '.join(sorted(team))}). A card sent this way waits for that person's working hours; "
            "add --urgent to send it at once when it can't.") if team and target is None else ""


def tell_reviewer(rec, news):
    """A card of a type someone reviews (the CFO, for money) reaches that session too: when it's posted, at each new
    version, and with each of the approver's answers, so its read can be in the thread before he answers. The asker's
    side doesn't change, and nothing waits on the reviewer. TEST cards reach only a reviewer set up in a test run, and
    the reviewer's own cards don't come back to it."""
    rv = store.reviewer()
    if (not rv or rec["type"] not in rv["types"] or bool(rec.get("test")) != bool(rv.get("test"))
            or (rec.get("reach") or {}).get("session_id") == rv["reach"].get("session_id")):
        return
    enqueue(rec, "review", f'{head(rec)} a {rec["type"]} card from {rec["agent"]}, which you review: {news} '
                           f'`approvals show {rec["id"]}` reads it.', to="reviewer")


def where_said(where):
    return {"button": f"button {said_in()}", "pop-up": said_in(), "deputy": "auto-approval by his deputy"}.get(where, where)


def quoted_words(words, member=None):
    whose = f"{member}'s" if member else "His"
    return f' {whose} words: "{one_line(words)}".' if words else ""


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


def msg_again(rec, item):
    """A reply the agent was handed and hasn't answered on the card, typed in again with its words and when it first
    went."""
    first = item["text"].rsplit(" (ref ", 1)[0]
    return (f'{head(rec)} Asked again: this reached you at {cards.when(store.parse(item["delivered_at"]))} and nothing '
            f'of yours has been posted on the card since. Answer it on the card; until you post there, it comes again '
            f'every 30 minutes. What was sent: {first}')


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


def msg_decision(rec, verdict, where, choice=None, items=None, edited=None, member=None):
    t = cards.when(store.now())
    c = card(rec)
    name = member or who(rec)
    if verdict == "approved" and choice is not None:
        o = next(o for o in c["options"] if o["name"] == choice)
        return (f'{head(rec)} {name} picked {choice}' + (f' ("{o["title"]}")' if o.get("title") else "") + f' ({where}, {t}). '
                f'{ack_step(rec, "what you are about to do")} Then do exactly what the card describes for {choice}, nothing else, and {done_step(rec)}')
    if verdict == "approved" and items is not None:
        names = [cards.item_name(it) for it in c["items"]]
        keep = "; ".join(f"{i} {names[i - 1]}" for i in items)
        left = [f"{i} {x}" for i, x in enumerate(names, 1) if i not in items]
        what = f'approved all {len(names)} items' if not left else f'approved {len(items)} of the {len(names)} items'
        where = "Pick which in Slack" if where.startswith("picked") else where
        return (f'{head(rec)} {name} {what} ({where}, {t}). Do these: {keep}.' + (f' Leave out: {"; ".join(left)}.' if left else '')
                + f' {ack_step(rec, "what you are about to do")} Then do exactly what the card describes for those items only, and {done_step(rec)}')
    if verdict == "approved" and edited is not None:
        edited_words = f"{member}'s edited words" if member else "his own edited words"
        return (f'{head(rec)} {name} approved it with {edited_words} ({where}, {t}). Use exactly these words, not the card\'s; '
                f'here they are as a JSON string: {json.dumps(edited, ensure_ascii=False)}. {ack_step(rec, "what you are about to do")} '
                f'Then do what the card describes with those words, and {done_step(rec)}')
    if verdict == "approved":
        return (f'{head(rec)} {name} approved it ({where}, {t}). {ack_step(rec, "what you are about to do")} '
                f'Then do exactly what the card describes, and {done_step(rec)}')
    if verdict == "declined":
        return (f'{head(rec)} {name} declined it ({where}, {t}). {ack_step(rec, "that you are dropping it")} '
                f'Then drop this proposal and don\'t send it again. If what it was for still matters, a different proposal is a new card.')
    said = f' The card said that with no answer, {c["if_no_answer"]}: do that.' if c.get("if_no_answer") else ""
    return f'{head(rec)} No answer by the deadline, so it is not approved.{said} Don\'t resubmit or send a reminder; a reply in the thread would reopen it.'


def msg_removed(rec):
    t = cards.when(store.now())
    kept = (" Parts he had already answered keep those answers." if card(rec).get("parts") and len(cards.open_rows(rec)) < len(card(rec)["parts"]) else "")
    return (f'{head(rec)} {who(rec)} removed it from his list (Remove, on his list of what waits on him {said_in()}, {t}). '
            f'The card is closed and nothing still waiting on it is approved.{kept} {ack_step(rec, "that you are dropping it")} '
            f'Then drop it and don\'t post it again. Removing isn\'t declining: it says this card shouldn\'t be waiting on him, '
            f'not that the idea is wrong. If what it was for still matters, work out why it didn\'t belong in front of him '
            f'(yours to decide, already settled, or out of date) before asking him again.')


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
    where = f"button {said_in()}" if a.get("where") == "button" else a.get("where") or "Slack"
    top = f'{head(rec)} part {j} of {n_parts} "{p["title"]}":'
    scope = f'This answer is for part {j} only; {_others(rec, {j})}.'
    name = answerer(rec, a)
    if a["verdict"] == "approved":
        return (f'{top} {name} approved this part ({where}, {t}). {scope} {ack_step(rec, "what you are about to do")} '
                f'Then do exactly what part {j} describes, nothing more; post its progress with `approvals status {rid} "<what happened>" --part {j}`, '
                f'and run `approvals done {rid} "<what you did and how you checked it>"` once every approved part is real.')
    if a["verdict"] == "chose":
        return (f'{top} {name} chose "{a["choice"]}" for this part ({where}, {t}). {scope} {ack_step(rec, "what you are about to do")} '
                f'Then do exactly that, nothing more; post its progress with `approvals status {rid} "<what happened>" --part {j}`.')
    if a["verdict"] == "declined":
        return (f'{top} {name} declined this part ({where}, {t}). {scope} {ack_step(rec, "that you are dropping it")} '
                f'Then drop part {j} only, and don\'t send it again.')
    return (f'{top} {name} asked for a change to this part ({where}, {t}): "{one_line(a.get("words") or "")}". {scope} '
            f'{ack_step(rec, "what you will change")} Then post the next version with `approvals revise {rid} <card.json>`, changing part {j} only '
            f'and saying what changed in "changes"; parts you leave exactly as they are keep their answers.')


def msg_all(rec, js, member=None):
    c = card(rec)
    t = cards.when(store.now())
    listed = "; ".join(f'part {j} "{c["parts"][j - 1]["title"]}"' for j in js)
    rest = _others(rec, set(js))
    return (f'{head(rec)} {member or who(rec)} approved {len(js)} parts at once (Approve all in Slack, {t}): {listed}. Any other parts: {rest}. '
            f'{ack_step(rec, "what you are about to do")} Then do exactly what each of those parts describes, nothing more; post progress with '
            f'`approvals status {rec["id"]} "<what happened>" --part <n>`, and run `approvals done {rec["id"]} "<what you did>"` once every approved part is real.')


def msg_offer(rec, choice):
    c = card(rec)
    t = cards.when(store.now())
    if choice == "stop":
        return (f'{head(rec)} {who(rec)} answered your offer ("{c["offer"]}") with "Stop asking for these" ({t}). '
                f'{ack_step(rec, "what you will stop asking for")} Then record it as a standing exception in your own rules, with today\'s date and '
                f'exactly what it covers, and from now on do that kind without a card. Anything outside it still gets a card.')
    return (f'{head(rec)} {who(rec)} answered your offer ("{c["offer"]}") with "Keep asking" ({t}). '
            f'{ack_step(rec, "that you will keep asking")} Keep sending a card for each one.')


def msg_replies(rec, kind, texts, question=None, member=None):
    t = cards.when(store.now())
    said = " / ".join(f'"{one_line(x)}"' for x in texts)
    rid = rec["id"]
    name = member or who(rec)
    one_reply = (f'Your reply is your acknowledgement: post nothing before it, and if it needs more than a few minutes of '
                 f'work, say first in one line what is coming and when, with `approvals ack {rid} "<what and when>"`.')
    if kind == "answer":
        return (f'{head(rec)} {name} answered your question ("{one_line(question)}") {said_in()} at {t}: {said}. '
                f'If it asks for a change, carry on with `approvals revise {rid} <card.json>`, adding `--answer "<the verdict, then the fact it rests on>"` if it also asks something. '
                f'If it approves the card as it stands (a yes, or a detail you asked about settled with no change to the card), '
                f'record it with `approvals record {rid} --approved --words "<their words>" --where "Slack thread"`, do it, then run `approvals done {rid} "<readback>"`. {one_reply}')
    if kind == "comment":
        return (f'{head(rec)} {name} replied {said_in()} at {t}, after the decision: {said}. '
                f'If it asks something, answer it with `approvals answer {rid} "<the answer, verdict first>"`. '
                f'If it changes what you do, say so in one line with `approvals ack {rid} "<what changes>"`. No new card unless asked for one.')
    return (f'{head(rec)} {name} replied {said_in()} at {t}: {said}. '
            f'If it asks a question, answer it with `approvals answer {rid} "<the verdict, then the fact it rests on>"`: that hands the card back as it stands, with no new version. '
            f'If it asks for a change, post the next version with `approvals revise {rid} <card.json>` (say what changed in "changes"). '
            f'If it asks a question and the answer also changes the card, do both in one: `approvals revise {rid} <card.json> --answer "<the verdict, then the fact it rests on>"`. '
            f'The answer then goes under his reply with one line saying what changed on the card, so keep the answer out of "changes". '
            f'If you truly cannot go on without one fact, ask it with `approvals ask {rid} "<question>"`. {one_reply} '
            f'If the reply plainly approves the card as it stands, record it with `approvals record {rid} --approved --words "<their words>" --where "Slack thread"`, do it, then run `approvals done {rid} "<readback>"`. '
            f'If it plainly declines, record it with `approvals record {rid} --declined --words "<their words>" --where "Slack thread"` and drop it.')


# ---------- decisions and replies (called under the store lock via store.update) ----------

def _check(rec, n):
    if n != len(rec["versions"]):
        return "stale"
    if rec["status"] == "withdrawn":
        return "removed" if cards.removed(rec) else "withdrawn"
    return None


def card_ts(rec):
    return rec["versions"][-1].get("copy_ts") or rec["thread_ts"]


def decide(rec, n, verdict, by, where="button", words=None, choice=None, items=None, edited=None, again=False):
    """Returns 'ok', 'stale', 'withdrawn' or 'already'. A decision belongs to the version it was made on, so
    a later version can be decided afresh. A pick (choice), a batch picked in a pop-up (items) and a yes with the
    approver's own words (edited) are yeses that say exactly what to do. On a set, a decision answers every open row.
    again: the agent records the approver's answer to a reply on a card he had already decided (status revising),
    such as "sure, schedule it whenever" after a question about a missed hour; it stands as his new decision on
    this version, so the card can be marked done. An answer asking for a change is never recorded: it gets a new
    version."""
    bad = _check(rec, n)
    if bad:
        return bad
    member = cards.teammate(rec, {"by": by, "version": n})
    name = member or who(rec)
    actor = {"by": by} if member else {}
    if card(rec).get("parts"):
        rows = cards.open_rows(rec)
        if not rows:
            if not (again and verdict == "approved" and rec["status"] == "revising" and _rows_decided(rec, n)):
                return "already"
            store.event(rec, "decision", verdict=verdict, version=n, where=where, words=words, **actor)
            news = f'{name} let the card go ahead as its rows were decided ({where_said(where)}).{quoted_words(words, member)}'
            tell_reviewer(rec, news)
            if words or member:
                tell_deputy(rec, news)
            settle(rec)
            return "ok"
        for j, p in rows:
            # The deputy's yes on a row that asks for a pick takes the pick the card names for it.
            pick = p.get("pick") if where == "deputy" and verdict == "approved" and p.get("options") else None
            r = answer_part(rec, n, j, "chose" if pick else verdict, by, where=where, words=words, choice=pick,
                            tell=where == "button", review=False)
        news = f'{name} {verdict} every part still open ({where_said(where)}).{quoted_words(words, member)}'
        tell_reviewer(rec, news)
        if verdict != "approved" or words or member:
            tell_deputy(rec, news)
        return r
    d = rec.get("decision")
    if d and d.get("version") == n and d.get("verdict") in ("approved", "declined") and not (
            again and rec["status"] == "revising"):
        return "already"
    end_sleep(rec)
    rec["decision"] = {"verdict": verdict, "version": n, "by": by, "where": where, "words": words, "at": store.iso()}
    rec["decision"].update({k: v for k, v in (("choice", choice), ("items", items), ("edited", edited)) if v is not None})
    rec["status"] = verdict
    rec["open_question"] = None
    rec["card_dirty"] = True
    store.event(rec, "decision", verdict=verdict, version=n, where=where, words=words, choice=choice, items=items, edited=edited, **actor)
    if where in ("button", "pop-up"):
        how = (f"button {said_in()}" if where == "button" else
               f"picked in a pop-up {said_in()}" if items is not None else f"Edit the words {said_in()}")
        enqueue(rec, "decision", msg_decision(rec, verdict, how, choice, items, edited, member),
                react_ts=card_ts(rec), member=member, by=by)
    edited_words = f"{member}'s edited words" if member else "his own edited words"
    what = (f"picked {choice}" if choice is not None else
            f'approved items {", ".join(map(str, items))} of {len(card(rec)["items"])}' if items is not None else
            f"approved it with {edited_words}" if edited is not None else f"{verdict} it")
    news = f"{name} {what} ({where_said(where)}, {cards.when(store.now())}).{quoted_words(edited or words, member)}"
    tell_reviewer(rec, news)
    plain = (verdict == "approved" and not words and edited is None and choice is None
             and (items is None or len(items) == len(card(rec).get("items") or [])))
    if (not plain or member) and where != "deputy":   # the deputy isn't told of its own approval
        tell_deputy(rec, news)
    return "ok"


def _rows_decided(rec, n):
    """Every row of the set answered on version n with a decision (none sent back for a change)."""
    answers = rec.get("answers") or {}
    got = [answers.get(cards.part_id(p, j)) for j, p in enumerate(card(rec)["parts"], 1)]
    return all(a and a.get("version") == n and a["verdict"] in ("approved", "declined", "chose") for a in got)


def answer_part(rec, n, j, verdict, by, where="button", words=None, choice=None, tell=True, review=True, again=False):
    """One row of a set answered on its own: approved, declined, chosen ('chose'), or sent back ('change').
    Returns 'ok', 'stale', 'withdrawn', 'already' or 'gone' (no such row). review=False: the caller tells the
    reviewer once for all the rows it answers. again: as in decide(), the approver's answer to a reply on a set
    whose rows were all decided (status revising) is recorded as this row's new answer, so the card can be done."""
    bad = _check(rec, n)
    if bad:
        return bad
    parts = card(rec).get("parts") or []
    if not 1 <= j <= len(parts):
        return "gone"
    pid = cards.part_id(parts[j - 1], j)
    answers = rec.setdefault("answers", {})
    if pid in answers and not (again and verdict != "change" and rec["status"] == "revising" and _rows_decided(rec, n)):
        return "already"
    a = {"verdict": verdict, "version": n, "by": by, "where": where, "at": store.iso()}
    a.update({k: v for k, v in (("words", words), ("choice", choice)) if v is not None})
    answers[pid] = a
    end_sleep(rec)
    member = cards.teammate(rec, a)
    store.event(rec, "part", part=j, verdict=verdict, version=n, where=where, words=words, choice=choice,
                **({"by": by} if member else {}))
    if tell:
        kind = "feedback" if verdict == "change" else "decision"
        enqueue(rec, kind, msg_part(rec, j, a), react_ts=card_ts(rec), member=member, by=by)
    if review:
        what = {"approved": "approved", "declined": "declined", "chose": f'chose "{choice}" for',
                "change": "asked for a change to"}[verdict]
        news = f'{member or who(rec)} {what} part {j} "{parts[j - 1]["title"]}" ({where_said(where)}).{quoted_words(words, member)}'
        tell_reviewer(rec, news)
        if verdict != "approved" or words or member:
            tell_deputy(rec, news)
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
        answer_part(rec, n, j, "approved", by, where="Approve all", tell=False, review=False)
    member = cards.teammate(rec, {"by": by, "version": n})
    enqueue(rec, "decision", msg_all(rec, [j for j, _ in rows], member), react_ts=card_ts(rec), member=member, by=by)
    news = f'{member or who(rec)} approved parts {", ".join(str(j) for j, _ in rows)} (Approve all in Slack).'
    tell_reviewer(rec, news)
    if member:
        tell_deputy(rec, news)
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
    if what == "notmine":
        return hand_back(rec, n, by)
    if what == "mine":
        return take_mine(rec, n, by)
    if what.startswith("hand:"):
        return hand(rec, n, by, what[5:])
    c = rec["versions"][n - 1]["card"] if 1 <= n <= len(rec["versions"]) else {}
    if what in ("approve", "decline"):
        return decide(rec, n, "approved" if what == "approve" else "declined", by=by)
    if what.startswith("pick:"):
        i = int(what.split(":")[1])
        return decide(rec, n, "approved", by=by, choice=c["options"][i]["name"])
    if what == "all":
        return approve_all(rec, n, by)
    if what == "remove":
        return remove(rec, n, by)
    if what == "takeback":
        return take_back(rec, n, by)
    if what.startswith("offer:"):
        return answer_offer(rec, n, what.split(":")[1], by)
    if what.startswith("p"):
        j_s, act, *rest = what[1:].split(":")
        j = int(j_s)
        if act == "pick":
            return answer_part(rec, n, j, "chose", by, choice=c["parts"][j - 1]["options"][int(rest[0])]["name"])
        return answer_part(rec, n, j, "approved" if act == "approve" else "declined", by)
    return "gone"


def row(rec, part):
    """A row of the newest version by its number (1, 2, 3) or its id; 0 when there's no such row."""
    parts = rec["versions"][-1]["card"].get("parts") or []
    if str(part).isdigit():
        return int(part) if 1 <= int(part) <= len(parts) else 0
    return next((j for j, p in enumerate(parts, 1) if cards.part_id(p, j) == part), 0)


def record_answer(rec, verdict, words, where, part=None, choice=None):
    """An answer the approver gave somewhere else, as an agent records it (`approvals record`), on the newest version:
    'ok', or why it wasn't recorded. In a Slack thread, a teammate's newest reply on that version is the answer."""
    n = len(rec["versions"])
    by = who(rec)
    if where == "Slack thread":
        reply = next((e for e in reversed(rec.get("events", [])) if e["kind"] in ("feedback", "answer", "comment")), {})
        if reply.get("version") == n and cards.teammate(rec, reply):
            by = reply["by"]
    if part:
        j = row(rec, part)
        if not j:
            return f"there is no row {part}"
        result = answer_part(rec, n, j, "chose" if choice else verdict, by, where=where, words=words, choice=choice,
                             tell=False, again=True)
        if result == "ok":
            mark_acked(rec)
        return result
    if choice and choice not in [o["name"] for o in rec["versions"][-1]["card"].get("options") or []]:
        return f"{choice} isn't one of this card's options"
    result = decide(rec, n, verdict, by=by, where=where, words=words, choice=choice, again=True)
    if result == "ok":
        mark_acked(rec)
    return result


def remove(rec, n, by):
    """Remove, on the approver's list: the card comes off it, closed with nothing still waiting on it approved, and
    without a verdict on the idea. Kept as a withdrawn card marked as his, so everything that knows a withdrawn card
    (no buttons, no replies counted as a decision, the agent's own commands refused) treats it as one."""
    bad = _check(rec, n)
    if bad:
        return bad
    if rec["status"] in CLOSED:
        return "already"
    end_sleep(rec)
    rec["status"] = "withdrawn"
    rec["withdrawn"] = {"text": f"{who(rec)} removed it from his list", "at": store.iso(), "by": by}
    rec["open_question"] = None
    rec["card_dirty"] = True
    store.event(rec, "removed", by=by)
    enqueue(rec, "decision", msg_removed(rec), react_ts=card_ts(rec))
    news = f"{who(rec)} removed it from his list, without approving or declining it ({cards.when(store.now())})."
    tell_reviewer(rec, news)
    tell_deputy(rec, news)
    return "ok"


def answer_offer(rec, n, choice, by):
    if rec.get("offer_answer"):
        return "already"
    rec["offer_answer"] = {"choice": choice, "by": by, "at": store.iso()}
    rec["card_dirty"] = True
    store.event(rec, "offer", choice=choice)
    enqueue(rec, "comment", msg_offer(rec, choice), react_ts=card_ts(rec))
    return "ok"


# ---------- the deputy ----------

def _answered_on(rec, n, since=None):
    """An answer on version n, if there is one.
    since: count only what came from then on, for a card sent to a teammate after an earlier reply on it."""
    posted = since or rec["versions"][n - 1].get("posted_at") or rec["created"]
    reply = next((e for e in rec.get("events") or [] if e["kind"] in ("feedback", "answer", "comment")
                  and e.get("version", n) == n and e["at"] >= posted), None)
    decision = rec.get("decision") or {}
    if reply or decision.get("version") == n:
        return reply or decision
    return next((a for a in (rec.get("answers") or {}).values()
                 if a.get("version") == n and (not since or a["at"] >= since)), None)


def _not_callable(rec, n, since=None):
    """Why the deputy can't call version n: it's no longer waiting on the approver's first answer. None when it is."""
    s = rec["status"]
    if s == "withdrawn":
        return "he removed it from his list" if cards.removed(rec) else "its agent withdrew it"
    if s in ("approved", "declined", "done"):
        return f"it is already decided ({s})"
    if s == "expired":
        return "it closed with no answer by its deadline"
    team_answers = config.settings()["team_answers_any"]
    if s == "revising":
        name = answerer(rec, cards.last_reply(rec)) if team_answers else who(rec)
        return f"it is back with its agent after {name}'s reply"
    if answer := _answered_on(rec, n, since):
        name = answerer(rec, answer) if team_answers else who(rec)
        return f"{name} has already answered on v{n}"
    return None


def send_after(now, quiet):
    if not quiet:
        return None
    start, end = (time.fromisoformat(t) for t in quiet)
    local = cards.local(now)
    clock = local.time()
    overnight = start > end
    in_quiet = (clock >= start or clock < end) if overnight else start <= clock < end
    if local.weekday() < 5 and not in_quiet:
        return None
    day = local.date() + timedelta(days=int(overnight and clock >= start))
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return store.iso(datetime.combine(day, end, local.tzinfo))


def work_after(start, hours, work):
    opens, closes = (time.fromisoformat(t) for t in work)
    if opens >= closes:
        raise ValueError("work must start before it ends on the same day")
    local = cards.local(start)
    left = timedelta(hours=hours)
    while left > timedelta(0):
        end = datetime.combine(local.date(), closes, local.tzinfo)
        if local.weekday() < 5 and local < end:
            local = max(local, datetime.combine(local.date(), opens, local.tzinfo))
            if left <= end - local:
                return local + left
            left -= end - local
        local = datetime.combine(local.date() + timedelta(days=1), opens, local.tzinfo)
    return local


def add_hand(rec, n, by, person):
    now = store.now()
    after = send_after(now, person.get("quiet"))
    due = work_after(store.parse(after) if after else now, config.settings()["handoff_hours"],
                     person.get("work", ["09:00", "18:00"]))
    at, return_at = store.iso(now), store.iso(due)
    send = {"version": n, "name": person["name"], "user": person["user"], "why": "", "at": at,
            "send_after": after, "handed": {"by": by, "at": at}, "return_at": return_at}
    rec.setdefault("also", []).append(send)
    end_sleep(rec)
    c = card(rec)
    hours = c.get("deadline_hours") or cards.TYPES[c["type"]]["hours"]
    deadline = due.astimezone(now.tzinfo) + timedelta(hours=hours)
    rec["deadline"] = store.iso(max(store.parse(rec["deadline"]), deadline))
    store.event(rec, "handed", version=n, name=send["name"], return_at=return_at)
    rec["card_dirty"] = True
    return send


def hand(rec, n, by, name):
    target = config.card_target(rec)
    if by != target["approver"]:
        return f"Only {target['approver_name']} can decide approvals."
    bad = _check(rec, n)
    if bad:
        return bad
    person = config.settings()["team"].get(name)
    if rec.get("target") is not None or person is None:
        return "gone"
    if rec["status"] == "revising":
        return "It's back with its agent."
    if rec["status"] not in ("pending", "question"):
        return "already"
    prior = cards.also_on(rec, n)
    name = person["name"]
    if cards.handed(rec) and prior["user"] == person["user"]:
        return f"{name} already has this one."
    send = add_hand(rec, n, by, person)
    if prior:
        prior.update(closed_at=send["at"], closed_for=name)
    approver = target["approver_name"]
    when = cards.when(store.parse(send["at"]))
    due = cards.when(store.parse(send["return_at"]))
    deadline = cards.when(store.parse(rec["deadline"]))
    enqueue(rec, "handed", f"{head(rec)} {approver} handed this card to {name} to answer ({when}). "
            f"{name}'s answer counts as {approver}'s, for exactly what the card describes. "
            f"If {name} hasn't answered by {due}, it goes back to {approver}. "
            f"With no answer by {deadline}, it closes as before. Nothing to do or acknowledge.")
    tell_deputy(rec, f"{approver} handed it to {name} ({when}). It waits on {name} until {due}; there is nothing to call.")
    return "ok"


def take_mine(rec, n, by):
    target = config.card_target(rec)
    if by != target["approver"]:
        return f"Only {target['approver_name']} can decide approvals."
    bad = _check(rec, n)
    if bad:
        return bad
    if not cards.handed(rec):
        return "gone"
    send = cards.also_on(rec, n)
    send.update(returned_at=store.iso(), returned_by="approver")
    if not send.get("sent_at"):   # taken back before it reached the teammate, so it never goes out
        send["skipped"] = True
    store.event(rec, "mine", version=n, name=send["name"])
    rec["card_dirty"] = True
    return "ok"


SLEEP_TIME = "Choose a future time before the answer deadline."


def sleep(rec, n, by, until):
    """Sleep, from the pod dashboard: the card leaves his waiting list until `until` (an ISO time), and comes back by
    itself once that time has passed. Nothing else changes: not its deadline, its status, its Slack card or its
    buttons, and nobody is told. Any answer ends the sleep."""
    target = config.card_target(rec)
    if by != target["approver"]:
        return f"Only {target['approver_name']} can decide approvals."
    bad = _check(rec, n)
    if bad:
        return bad
    if rec["status"] not in ("pending", "question") or cards.handed(rec):
        return "already"
    try:
        wake_at = store.parse(until)
    except (TypeError, ValueError):
        return SLEEP_TIME
    if wake_at.tzinfo is None or not store.now() < wake_at < store.parse(rec["deadline"]):
        return SLEEP_TIME
    at = store.iso()
    rec["sleep"] = {"until": store.iso(wake_at), "by": by, "at": at}
    store.event(rec, "slept", version=n, until=rec["sleep"]["until"])
    return "ok"


def wake(rec, n, by):
    """Wake now: a sleeping card is back on his waiting list at once."""
    target = config.card_target(rec)
    if by != target["approver"]:
        return f"Only {target['approver_name']} can decide approvals."
    bad = _check(rec, n)
    if bad:
        return bad
    if rec["status"] not in ("pending", "question") or not rec.get("sleep"):
        return "already"
    rec.pop("sleep")
    store.event(rec, "woke", version=n)
    return "ok"


def end_sleep(rec):
    """Any answer on a card ends its sleep."""
    rec.pop("sleep", None)


def call(rec, what, why, by, live, also=None, urgent=False):
    """The deputy's call on the newest version: "approve" (for the approver) or "his" (left for him). Recorded
    either way; an approve approves the card for him only when auto-approval is on (live). Returns 'ok', or why
    nothing was recorded."""
    n = len(rec["versions"])
    if urgent and (also is None or what != "his"):
        return "--urgent requires --his and --also <name>"
    if also is not None:
        send = cards.also_on(rec, n)
        if send:
            return f"{who(rec)} handed this card to {send['name']}; call it without --also."
        team = config.settings()["team"]
        names = ", ".join(sorted(team)) or "none"
        if what != "his" or also not in team or rec.get("target") is not None:
            return f"--also takes a team name with --his on a card for {config.settings()['approver_name']}. Configured names: {names}"
    if rec.get("target") is not None:
        return "this card is for a different approver"
    bad = _not_callable(rec, n)
    if bad:
        return bad
    prior = cards.call_on(rec, n)
    # One call a version, except that a dry-run call can be made again once auto-approval is on: a dry run approved
    # nothing, so the card would otherwise wait on the approver for a call that was only ever shown. The new call
    # stands as the call on that version; the dry-run one stays in the events. A call that also sends the card
    # already took effect in either mode, so it stands.
    if prior and (prior["live"] or not live or prior.get("also") is not None):
        return f"v{n} already has your call"
    c = card(rec)
    open_rows = cards.open_rows(rec)
    choice = None
    if what == "approve":
        # An approve covers the whole card: every open row, a row marked own_yes included (only the approver's own
        # Approve all skips those). Where the card asks for a pick, at its own level or on a row, the approve takes
        # the pick its agent named; a card that names none leaves nothing for an approval to take.
        if (c.get("options") and not c.get("pick")) or any(p.get("options") and not p.get("pick") for _, p in open_rows):
            return "it asks for a pick and names none, so there is nothing for an approval to take"
        choice = c.get("pick") if c.get("options") else None
    now = store.now()
    extra = {"also": also} if also is not None else {}
    if urgent:
        extra["urgent"] = True
    rec.setdefault("calls", []).append({"version": n, "call": what, "why": why, "by": by, "at": store.iso(now), "live": live, **extra})
    store.event(rec, "call", version=n, call=what, why=why, by=by, live=live, **extra)
    if also is not None:
        person = team[also]
        after = send_after(now, person.get("quiet"))
        send = {"version": n, "name": person["name"], "user": person["user"], "why": why, "at": store.iso(now)}
        if urgent:
            send["urgent"] = True
            if after:
                send["outside_hours"] = True
        else:
            send["send_after"] = after
        rec.setdefault("also", []).append(send)
    rec["card_dirty"] = True
    if what == "approve" and live:
        r = decide(rec, n, "approved", by=by, where="deputy", choice=choice)
        if r != "ok":   # nothing was approved, so the asker must not be told it was
            rec["calls"].pop()
            rec["events"].pop()
            return f"it could not be approved ({r})"
        rec["decision"] = {"verdict": "approved", "version": n, "by": by, "where": "deputy", "why": why, "words": None,
                           "at": store.iso()}
        if choice is not None:
            rec["decision"]["choice"] = choice
        enqueue(rec, "decision", msg_auto(rec, by, why), react_ts=card_ts(rec))
    return "ok"


def hand_back(rec, n, by):
    bad = _check(rec, n)
    if bad:
        return bad
    send = cards.also_on(rec, n)
    if not send or send["user"] != by or not send.get("sent_at") or send.get("handed_back_at"):
        return "gone"
    if rec["status"] not in ("pending", "question"):
        return "already"
    now = store.now()
    send["handed_back_at"] = store.iso(now)
    store.event(rec, "also_handed_back", version=n, name=send["name"])
    rec["card_dirty"] = True
    name = send["name"]
    tell_deputy(rec, f"{name} handed it back as not {name}'s to answer (button {said_in()}, {cards.when(now)}). "
                     f"It stays with {who(rec)}; there is nothing to call.")
    return "ok"


def msg_auto(rec, by, why):
    """The deputy's approval, typed to the asker. Where it took a pick the card named, at the card's level or on a
    row, it says which, since that is what the yes covers."""
    c = card(rec)
    n = len(rec["versions"])
    choice = (rec.get("decision") or {}).get("choice")
    took, do = "", "do exactly what the card describes"
    if choice is not None:
        o = next(o for o in c["options"] if o["name"] == choice)
        took = f' It took the pick the card names, {choice}' + (f' ("{o["title"]}")' if o.get("title") else "") + "."
        do = f"do exactly what the card describes for {choice}, nothing else"
    answers = rec.get("answers") or {}
    rows = [(j, a["choice"]) for j, p in enumerate(c.get("parts") or [], 1)
            for a in [answers.get(cards.part_id(p, j)) or {}]
            if a.get("where") == "deputy" and a.get("version") == n and a.get("verdict") == "chose"]
    if rows:
        took = " " + " ".join(f'On part {j} it took the pick the card names, "{ch}".' for j, ch in rows)
        do = "do exactly what the card describes, with " + ("that pick" if len(rows) == 1 else "those picks")
    return (f'{head(rec)} approved for {who(rec)} by his deputy, {by} (auto-approval, {cards.when(store.now())}). '
            f'Why: "{one_line(why)}". It counts as his yes, for exactly what the card describes.{took} '
            f'{ack_step(rec, "what you are about to do")} Then {do}, and {done_step(rec)}')


def take_back(rec, n, by):
    """Take it back, on an auto-approved card, at any time (after done too): the card becomes the approver's own
    decline, and its agent stops and undoes what it can."""
    if n != len(rec["versions"]):
        return "stale"
    if rec.get("taken_back"):
        return "taken"
    if not cards.auto_approved(rec):
        return "gone"
    at = store.iso()
    rec["taken_back"] = {"by": by, "at": at}
    rec["decision"] = {"verdict": "declined", "version": n, "by": by, "where": "took back the auto-approval", "words": None, "at": at}
    for a in (rec.get("answers") or {}).values():   # a set's rows the deputy approved show as not approved
        if a.get("where") == "deputy" and a.get("version") == n:
            a.update(verdict="declined", where="took back the auto-approval", at=at)
    rec["status"] = "declined"
    rec["open_question"] = None
    rec["card_dirty"] = True
    store.event(rec, "taken_back", by=by)
    # Not paced like a decision: it takes back a yes, so it is typed right behind that yes if it hasn't gone yet.
    enqueue(rec, "taken_back", msg_taken_back(rec), react_ts=card_ts(rec))
    tell_deputy(rec, f"{who(rec)} took back your approval (button {said_in()}, {cards.when(store.now())}), so it is not approved.")
    return "ok"


def msg_taken_back(rec):
    return (f'{head(rec)} {who(rec)} took back the approval his deputy gave (button {said_in()}, {cards.when(store.now())}), '
            f'so it is not approved. {ack_step(rec, "that you are stopping, and what is already done")} '
            f'If you haven\'t acted, don\'t. If you have, undo what can be undone, and say on the card where it stands with '
            f'`approvals status {rec["id"]} "<what>"`. He may say more in the thread.')


def plain_yes(rec, n):
    """His first decision on version n was Approve by the button (no edited words, no partial batch, no pick: a batch
    ticked whole in Pick which counts; on a set, every row he answered approved by its button or Approve all), and no
    reply of his came between the version's posting and that decision. A fact about the record, used only for the morning summary's counts."""
    c = rec["versions"][n - 1]["card"]
    posted = rec["versions"][n - 1].get("posted_at") or rec["created"]
    events = [e for e in rec.get("events") or [] if e["at"] >= posted]
    if c.get("parts"):
        rows = [i for i, e in enumerate(events) if e["kind"] == "part" and e.get("version") == n]
        if not rows or any(events[i]["verdict"] != "approved" or events[i].get("where") not in ("button", "Approve all")
                           or events[i].get("words") or cards.teammate(rec, events[i]) for i in rows):
            return False
        decided = rows[-1]
    else:
        decided = next((i for i, e in enumerate(events) if e["kind"] == "decision" and e.get("version") == n), None)
        d = events[decided] if decided is not None else {}
        if (d.get("verdict") != "approved" or d.get("where") not in ("button", "pop-up") or d.get("words") or d.get("choice") is not None
                or d.get("edited") is not None or cards.teammate(rec, d)
                or (d.get("items") is not None and len(d["items"]) < len(c.get("items") or []))):
            return False
    return not any(e["kind"] in ("feedback", "answer", "comment") for e in events[:decided])


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
    take_replies(rec, [(None, words)], react_ts=card_ts(rec), by=by)
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


def take_replies(rec, replies, react_ts=None, by=None):
    """replies: [(ts, text)] from one person who can answer, oldest first."""
    texts = [t for _, t in replies]
    last_ts = react_ts or replies[-1][0]
    if rec["status"] in CLOSED:
        kind = "comment"
    elif rec.get("open_question"):
        kind = "answer"
    else:
        kind = "feedback"
    q = (rec.get("open_question") or {}).get("text")
    n = len(rec["versions"])
    member = cards.teammate(rec, {"by": by, "version": n})
    name = member or who(rec)
    end_sleep(rec)
    via = "dashboard" if VIA.get() == "dashboard" else "slack"
    for ts, t in replies:
        store.event(rec, kind, text=t, ts=ts, via=via, **({"by": by, "version": n} if member else {}))
    if kind != "comment":
        if rec["status"] == "expired":
            rec["decision"] = None
            rec["card_dirty"] = True
        rec["status"] = "revising"
        rec["open_question"] = None
    if cards.also_on(rec, n):
        rec["card_dirty"] = True
    enqueue(rec, kind, msg_replies(rec, kind, texts, q, member), react_ts=last_ts, member=member, by=by)
    said = " / ".join(f'"{one_line(x)}"' for x in texts)
    tell_reviewer(rec, f"{name} replied in the thread: {said}.")
    tell_deputy(rec, f"{name} replied in the thread" + (" after the decision" if member and kind == "comment"
                else " after his decision" if kind == "comment" else "") + f": {said}.")
    return kind


def expire(rec):
    c = card(rec)
    if c.get("parts"):
        # Rows already answered keep their answers; only the open ones close.
        rows = cards.open_rows(rec)
        n = len(rec["versions"])
        for j, _ in rows:
            answer_part(rec, n, j, "expired", None, where="deadline", tell=False, review=False)
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

def conversation_send(rec):
    """The current direct message that should also show the card's conversation."""
    send = cards.also_on(rec, len(rec["versions"]))
    if (send and send.get("sent_at") and not send.get("handed_back_at") and rec["status"] not in FINAL
            and (send.get("handed") or cards.responded(rec, send))):
        return send


def post_to_teammate(rid, send, text, blocks=None):
    if send:
        try:
            slackapi.post(blocks, text, thread_ts=send["ts"], channel_id=send["channel"])
        except Exception:
            logging.getLogger(__name__).exception("direct message reply for %s failed", rid)


def mode_for(rec):
    if cards.handed(rec):
        return "handed"
    return "final" if rec["status"] in FINAL and (rec.get("decision") or rec["status"] == "withdrawn") else "open"


def refresh_cards(rec):
    """Make every card message match the record. Raises if Slack fails; callers retry."""
    latest = len(rec["versions"])
    mode = mode_for(rec)
    if rec.get("thread_ts"):   # a card made without a Slack post has no message to redraw (AP-1942, November 21, 2026)
        slackapi.update(rec["thread_ts"], cards.card_blocks(rec, latest, mode), cards.card_text(rec, latest), **config.channel_args(rec))
    source = rec.get("thread") or {}
    if source.get("small_ts"):
        try:
            slackapi.update(source["small_ts"], cards.small_blocks(rec), cards.card_text(rec, latest), channel_id=source["channel"])
        except Exception:
            logging.getLogger(__name__).exception("source card refresh for %s failed", rec["id"])
    for v in rec["versions"]:
        if not v.get("copy_ts"):
            continue
        m = mode if v["n"] == latest else "replaced"
        slackapi.update(v["copy_ts"], cards.card_blocks(rec, v["n"], m), cards.card_text(rec, v["n"]), **config.channel_args(rec))
    for send in rec.get("also", []):
        if send.get("sent_at"):
            try:
                slackapi.update(send["ts"], cards.also_blocks(rec, send), cards.card_text(rec, send["version"]),
                                channel_id=send["channel"])
            except Exception:
                logging.getLogger(__name__).exception("direct message refresh for %s failed", rec["id"])


def refresh_and_clear(rid):
    rec = store.load(rid)
    refresh_cards(rec)

    def clear(r):
        # A newer answer may have redrawn the DM while this refresh was still writing to Slack.
        r["card_dirty"] = r.get("group") != rec.get("group") or bool(r.get("also") and r != rec)
    store.update(rid, clear)


def due(item):
    return not item.get("delivered_at") and datetime.fromisoformat(item["next_try"]) <= store.now()


# ---------- which machine delivers what ----------

STRAY_DAYS = 14


def machine_of(rec):
    """The machine the asker last ran an approvals command on; the hub's for cards from before machines had names."""
    return (rec.get("reach") or {}).get("machine") or config.hub()


def _folder(cwd):
    """A folder as its path under the user's home: one checkout is /Users/<name>/... on a Mac and /home/<name>/... on Linux."""
    return re.sub(r"^/(?:Users|home)/[^/]+/", "~/", cwd or "")


def successor(rec, records):
    """Where the agent that asked runs now, as reach, once its session is gone: the newest other session seen
    running an approvals command under the same name and runtime since this card last heard from its asker. None
    when there is none.

    An agent started again (a pod member restarted, a pod restored or moved to another machine) is a new session
    with nothing of the old one's history, so nothing in its transcript ties the two; its name does. Until November
    15, 2026 only a launcher's own `approvals repoint` joined them, and 68 open cards from 34 agents named a session
    that ran nowhere: an answer on one got "isn't running" in Slack while the agent worked on, in its new session.

    How far a name is trusted follows who gave it. A name its launcher set (APPROVALS_FROM) is the member's own,
    whatever folder the command ran in: members raise cards from their work folders. A name the agent typed counts
    only from the same folder. A name nobody gave is the folder's own, shared by every session that ever ran there
    (ten agents had run a command from the repository's root as "psbrain"), so it joins nothing. A fork is never
    the agent it was forked from, as everywhere."""
    old = rec.get("reach") or {}
    name, folder = old.get("agent"), _folder(old.get("cwd"))
    if not name or not old.get("session_id"):
        return None
    typed = bool(folder) and name != folder.rstrip("/").rsplit("/", 1)[-1]
    families = store.forks()
    known = {old["session_id"], *old.get("earlier_ids", [])}
    best = None
    for r in records:
        seen = [r.get("reach") or {}] + [e["seen"] for e in r.get("events") or []
                                         if e["kind"] == "used_by_other_session" and e.get("seen")]
        for c in seen:
            if (c.get("agent") == name and c.get("runtime") == old.get("runtime")
                    and (c.get("named_by_launcher") or (typed and _folder(c.get("cwd")) == folder))
                    and c.get("session_id") and c["session_id"] not in known and c.get("machine")
                    and families.get(c["session_id"]) == families.get(old["session_id"])
                    and (c.get("updated") or "") > max(old.get("updated") or "", (best or {}).get("updated") or "")):
                best = c
    return best


TYPED_SECONDS = 90   # a message typed into a session should show up in its transcript by then, or it's typed again


def just_typed(item):
    """Typed in a moment ago and waiting to show up in the transcript. Hidden a little longer than the check that
    confirms it waits, so that check and the next pass never both act on it."""
    at = item.get("typed_at")
    return bool(at) and store.now() - datetime.fromisoformat(at) < timedelta(seconds=TYPED_SECONDS + 30)


def next_item(rec, to=None):
    """A request's first message for its asker (to="reviewer": for its reviewer; to="deputy": for the deputy) not
    yet delivered or left in the inbox. Its later ones wait for it, so they arrive in order. One just typed in doesn't
    hold them up: Claude takes a typed message in at its next step, so the next is typed right behind it and the agent
    reads both at once, rather than acting on a yes for a few seconds before the "wait, don't send" behind it. The
    asker's, the reviewer's and the deputy's messages never wait on each other."""
    return next((i for i in rec.get("outbox", []) if i.get("to") == to and not i.get("delivered_at")
                 and not i.get("inbox_only") and not just_typed(i)), None)


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
            if any(i["kind"] != "decision" and not i.get("to") and not i.get("delivered_at") for i in behind):
                continue   # he's replying on this card ("wait, don't send"): the reply can't wait, so the yes before it goes now
            waiting.setdefault(sid, []).append((item["created"], rec["id"], item["ref"]))
    out = set()
    for sid, items in waiting.items():
        items.sort()
        out |= {(rid, ref) for _, rid, ref in (items if sid in busy else items[1:])}
    return out


ALARM_SECONDS = 60


def overdue(records, now):
    """Asker messages the tool has failed to deliver, as (record, item, reason). A decision the agent acknowledged
    is one it has, however it got it: three answers acted on weeks before raised an alarm the first time this ran."""
    hold = held(records)
    out = []
    for rec in records:
        for item in rec.get("outbox", []):
            if (item.get("to") or item.get("delivered_at") or item.get("seen_by_agent") or item.get("acked_at")
                    or item.get("waiting_for_agent") or item.get("alarmed_at")
                    or (rec["id"], item["ref"]) in hold
                    or now - store.parse(item["created"]) < timedelta(seconds=ALARM_SECONDS)):
                continue
            error = item.get("last_error")
            if error and error != "sent, but not seen in the transcript yet":
                reason = error
            elif item.get("inbox_only"):
                reason = error or "the answer can only be picked up from the agent's inbox"
            elif not item.get("sent_at") and not item.get("typed_at") and not error:
                reason = "nothing has typed the answer into the agent's session"
            else:
                continue
            out.append((rec, item, reason))
    return out


def to_deliver(records, machine, reviewer=None, deputy=None):
    """What one machine delivers now: [request id, ref] of each request's next message that is due, for sessions
    on that machine, except decisions held for their agent to catch up, and its next message for the reviewer (and
    for the deputy) when that session is on that machine; and [request id, ref, reach] of strays, messages the machine
    on the card couldn't deliver because the session wasn't there, for whichever machine has it to take. Pass every
    record: holding a decision depends on the agent's other cards."""
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
        for to, role in (("reviewer", reviewer), ("deputy", deputy if rec.get("target") is None else None)):
            item = next_item(rec, to)
            if role and item and due(item) and (one_machine or role["reach"].get("machine") == machine):
                due_now.append([rec["id"], item["ref"]])
        for i in rec.get("outbox", []):
            if (i.get("stray") and not i.get("to") and not i.get("delivered_at") and not i.get("seen_by_agent")
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
