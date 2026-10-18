"""What happens to a request: decisions, replies, deadlines, card refreshes, messages to the agent."""
import secrets
from datetime import datetime

from . import cards, config, slackapi, store


def who():
    return config.settings()["approver_name"]

FINAL = {"approved", "declined", "expired", "done"}


def one_line(s):
    return " / ".join(part.strip() for part in str(s).splitlines() if part.strip())


def enqueue(rec, kind, text, react_ts=None):
    ref = secrets.token_hex(3)
    rec.setdefault("outbox", []).append({
        "ref": ref, "kind": kind, "text": f"{text} (ref {ref})", "created": store.iso(),
        "react_ts": react_ts, "attempts": 0, "next_try": store.iso(), "delivered_at": None,
        "noted_failure": False, "seen_by_agent": False,
    })
    return ref


def head(rec):
    n = len(rec["versions"])
    return f'[Approvals] {rec["id"]} v{n} "{rec["title"]}":'


def msg_decision(rec, verdict, where):
    t = cards.when(store.now())
    if verdict == "approved":
        return f'{head(rec)} {who()} approved it ({where}, {t}). Do it now, then run `approvals done {rec["id"]} "<what you did and how you checked it>"`.'
    if verdict == "declined":
        return f'{head(rec)} {who()} declined it ({where}, {t}). Drop it. Don\'t resubmit unless asked to.'
    return f'{head(rec)} No answer by the deadline, so it is not approved. Don\'t resubmit or send a reminder; a reply in the thread would reopen it.'


def msg_replies(rec, kind, texts, question=None):
    t = cards.when(store.now())
    said = " / ".join(f'"{one_line(x)}"' for x in texts)
    rid = rec["id"]
    if kind == "answer":
        return f'{head(rec)} {who()} answered your question ("{one_line(question)}") in Slack at {t}: {said}. Carry on, usually with `approvals revise {rid} <card.json>`.'
    if kind == "comment":
        return f'{head(rec)} {who()} replied in Slack at {t}, after the decision: {said}. Take it into account; no new card unless asked for one.'
    return (f'{head(rec)} {who()} replied in Slack at {t}: {said}. This is feedback on the card. '
            f'Post a revised card with `approvals revise {rid} <card.json>` (say what changed in "changes"), '
            f'or ask one question with `approvals ask {rid} "<question>"`. '
            f'If the reply plainly approves the card as it stands, record it with `approvals record {rid} --approved --words "<their words>" --where "Slack thread"`, do it, then run `approvals done {rid} "<readback>"`. '
            f'If it plainly declines, record it with `approvals record {rid} --declined --words "<their words>" --where "Slack thread"` and drop it.')


# ---------- decisions and replies (called under the store lock via store.update) ----------

def decide(rec, n, verdict, by, where="button", words=None):
    """Returns 'ok', 'stale' or 'already'."""
    latest = len(rec["versions"])
    if n != latest:
        return "stale"
    d = rec.get("decision")
    if d and d.get("verdict") in ("approved", "declined"):
        return "already"
    rec["decision"] = {"verdict": verdict, "version": n, "by": by, "where": where, "words": words, "at": store.iso()}
    rec["status"] = verdict
    rec["open_question"] = None
    rec["card_dirty"] = True
    store.event(rec, "decision", verdict=verdict, version=n, where=where, words=words)
    if where in ("button",):
        enqueue(rec, "decision", msg_decision(rec, verdict, "button in Slack"), react_ts=rec["versions"][-1].get("copy_ts") or rec["thread_ts"])
    return "ok"


def take_replies(rec, replies):
    """replies: [(ts, text)] from the approver, oldest first."""
    texts = [t for _, t in replies]
    last_ts = replies[-1][0]
    if rec["status"] in ("approved", "declined", "done"):
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
    rec["decision"] = {"verdict": "expired", "version": len(rec["versions"]), "by": None, "where": "deadline", "words": None, "at": store.iso()}
    rec["status"] = "expired"
    rec["card_dirty"] = True
    store.event(rec, "expired")
    enqueue(rec, "expired", msg_decision(rec, "expired", "deadline"))


# ---------- Slack side ----------

def mode_for(rec):
    return "final" if rec["status"] in FINAL and rec.get("decision") else "open"


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
