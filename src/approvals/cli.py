"""The `approvals` command: what agents use, plus `listen` for the service."""
import argparse
import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

from . import cards, config, core, reach, slackapi, store, transcripts


def _load_card(path):
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    return json.loads(text)


def _die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def _prepare_media(card, version):
    """Upload the image privately and publish any video; store the handles on the version."""
    if card.get("image"):
        version["image_file_id"] = slackapi.upload_private_image(card["image"], card["title"])
    if card.get("video"):
        version["video_url"] = slackapi.share_video(card["video"], card["title"], card.get("video_note") or "A video waiting for approval.")


def _video_in_thread(rid, n):
    """No share command configured: put the video in the thread, where Slack plays it, and link the card to it."""
    rec = store.load(rid)
    v = rec["versions"][n - 1]
    if not v["card"].get("video") or v.get("video_url"):
        return
    link = slackapi.upload_to_thread(v["card"]["video"], rec["thread_ts"], v["card"]["title"], "The video, to play here.")
    store.update(rid, lambda r: r["versions"][n - 1].__setitem__("video_url", link))
    core.refresh_and_clear(rid)


def _evidence_file(item):
    """A file on this Mac that an evidence line names, alone or after a label ("Recipe: /path/recipe.json"),
    with that label. Relative paths are from the agent's folder. A path posted as text can't be opened
    from Slack, so these get uploaded instead."""
    label, path = None, item.strip()
    m = re.match(r"^(.{1,200}?):\s+(\S+)$", path)
    if m:
        label, path = m.group(1).strip(), m.group(2)
    try:
        p = Path(path).expanduser()
        p = p if p.is_absolute() else Path.cwd() / p
        return (p, label) if p.is_file() else (None, None)
    except OSError:
        return None, None


def _post_evidence(rec, evidence):
    for item in cards.evidence_items(evidence):
        path, label = _evidence_file(item)
        if path:
            slackapi.upload_to_thread(path, rec["thread_ts"], label or path.name, label)
        else:
            slackapi.note(rec["thread_ts"], item)


def cmd_submit(a):
    card = _load_card(a.card)
    errs = cards.validate(card)
    if errs:
        _die("Card refused:\n- " + "\n- ".join(errs))
    who = reach.capture(a.agent)
    hours = card.get("deadline_hours") or cards.TYPES[card["type"]]["hours"]
    deadline = store.now() + (timedelta(minutes=a.deadline_minutes) if a.deadline_minutes else timedelta(hours=hours))
    version = {"n": 1, "card": card, "posted_at": store.iso()}
    _prepare_media(card, version)
    rec = store.create({
        "type": card["type"], "title": card["title"], "agent": card.get("agent") or who["agent"],
        "created": store.iso(), "deadline": store.iso(deadline), "status": "pending",
        "test": bool(a.test or card.get("test") or config.is_test_run()), "versions": [version], "thread_ts": None,
        "last_seen": None, "open_question": None, "decision": None, "done": None,
        "events": [], "outbox": [], "reach": None, "moves": [], "card_dirty": False,
    })
    reach.refresh(rec, who)
    ts = slackapi.post(cards.card_blocks(rec, 1, "open"), cards.card_text(rec, 1))
    rec["thread_ts"] = ts
    rec["last_seen"] = ts
    version["ts"] = ts
    store.event(rec, "submitted")
    store.save(rec)
    _video_in_thread(rec["id"], 1)
    _post_evidence(rec, card.get("evidence"))
    print(rec["id"])
    print(slackapi.permalink(ts))


def cmd_ask(a):
    def fn(rec):
        reach.refresh(rec, reach.capture())
        rec["open_question"] = {"text": a.question, "at": store.iso()}
        rec["status"] = "question"
        store.event(rec, "question", text=a.question)
        return rec
    rec = store.update(a.id, fn)
    ts = slackapi.post(cards.question_blocks(rec, a.question), f"{rec['agent']} asks: {a.question}", thread_ts=rec["thread_ts"], broadcast=True)
    store.update(a.id, lambda r: r["open_question"].__setitem__("ts", ts))
    print(f"Asked in the thread of {a.id}. The answer will arrive as an [Approvals] message.")


def cmd_revise(a):
    card = _load_card(a.card)
    errs = cards.validate(card, revision=True)
    if errs:
        _die("Card refused:\n- " + "\n- ".join(errs))
    rec = store.load(a.id)
    if rec["status"] in ("declined", "done", "withdrawn"):
        _die(f"{a.id} is {rec['status']}; a different proposal is a new card: approvals submit <card.json>.")
    n = len(rec["versions"]) + 1
    version = {"n": n, "card": card, "posted_at": store.iso()}
    _prepare_media(card, version)
    prev = rec["versions"][-1]

    def add(r):
        reach.refresh(r, reach.capture())
        r["versions"].append(version)
        r["status"] = "pending"
        r["open_question"] = None
        if r.get("decision"):
            # The answer was to the earlier version; this one gets its own.
            r["versions"][prev["n"] - 1]["decision"] = r["decision"]
            r["decision"] = None
            r["card_dirty"] = True
        hours = card.get("deadline_hours") or cards.TYPES[card["type"]]["hours"]
        r["deadline"] = store.iso(store.now() + timedelta(hours=hours))
        r["title"] = card["title"]
        store.event(r, "revised", version=n)
        return r
    rec = store.update(a.id, add)
    # Keep the replaced version readable in the thread, then post the new one there too.
    if not prev.get("copy_ts"):
        ts_old = slackapi.post(cards.card_blocks(rec, prev["n"], "replaced"), cards.card_text(rec, prev["n"]), thread_ts=rec["thread_ts"])
        store.update(a.id, lambda r: r["versions"][prev["n"] - 1].__setitem__("copy_ts", ts_old))
    rec = store.load(a.id)
    ts_new = slackapi.post(cards.card_blocks(rec, n, "open"), cards.card_text(rec, n), thread_ts=rec["thread_ts"])
    store.update(a.id, lambda r: r["versions"][n - 1].__setitem__("copy_ts", ts_new))
    core.refresh_and_clear(a.id)
    _video_in_thread(a.id, n)
    slackapi.post(cards.new_version_blocks(rec, n), f"{rec['agent']} posted v{n}: {card['title']}", thread_ts=rec["thread_ts"], broadcast=True)
    print(f"{a.id} v{n} posted in the thread, with a line in the channel; the first post now shows v{n}.")


def cmd_ack(a):
    rec = store.load(a.id)
    me = reach.capture(a.agent)
    name = rec["agent"]
    ts = slackapi.post([cards.md(f"🤖 *{cards.esc(name)}:* {cards.esc(a.text)}"),
                        cards.ctx(f"Acknowledged by the agent · {rec['id']} · {cards.when(store.now())}")],
                       f"{name}: {a.text}", thread_ts=rec["thread_ts"])

    def fn(r):
        reach.refresh(r, me)
        return core.acknowledge(r, a.text, ts)
    kinds = store.update(a.id, fn)
    print(f"Acknowledged in the thread of {a.id}" + (f" ({', '.join(kinds)})." if kinds else "."))


def cmd_done(a):
    def fn(rec):
        reach.refresh(rec, reach.capture())
        if rec["status"] != "approved":
            return rec["status"]
        rec["done"] = {"text": a.readback, "at": store.iso()}
        rec["status"] = "done"
        store.event(rec, "done", text=a.readback)
        return "ok"
    r = store.update(a.id, fn)
    if r != "ok":
        _die(f"{a.id} is {r}, not approved; nothing marked done.")
    core.refresh_and_clear(a.id)
    print(f"{a.id} marked Done on its card.")


def cmd_withdraw(a):
    def fn(rec):
        reach.refresh(rec, reach.capture())
        if rec["status"] in ("done", "declined", "withdrawn"):
            return rec["status"]
        rec["status"] = "withdrawn"
        rec["withdrawn"] = {"text": a.why, "at": store.iso()}
        rec["open_question"] = None
        rec["card_dirty"] = True
        store.event(rec, "withdrawn", text=a.why)
        return "ok"
    r = store.update(a.id, fn)
    if r != "ok":
        _die(f"{a.id} is {r}; nothing to withdraw.")
    core.refresh_and_clear(a.id)
    print(f"{a.id} withdrawn; its card says why and has no buttons.")


def cmd_record(a):
    verdict = "approved" if a.approved else "declined"
    r = store.update(a.id, lambda rec: core.decide(rec, len(rec["versions"]), verdict, by=config.settings()["approver_name"], where=a.where, words=a.words))
    if r != "ok":
        _die(f"Not recorded: {r}.")
    core.refresh_and_clear(a.id)
    print(f"{a.id} recorded as {verdict} ({a.where}).")


def cmd_inbox(a):
    """Answers the service can't type into their asker's session. Each answer goes to exactly one session:
    the asker gets anything not yet delivered; another run of the same agent gets only answers the
    service has given up delivering, and taking one here stops the service from delivering it too."""
    me = reach.capture(a.agent)
    shown = 0

    def take(rec, own):
        nonlocal shown
        for item in rec.get("outbox", []):
            if item.get("delivered_at") or item.get("seen_by_agent"):
                continue
            if not own and not (item.get("inbox_only") or item.get("noted_failure")):
                continue
            print(item["text"])
            item["seen_by_agent"] = True
            item["delivered_at"] = store.iso()
            item["delivered_via"] = "inbox"
            item["claimed_by"] = me.get("session_id")
            shown += 1
    for rec in store.all_records():
        r = rec.get("reach") or {}
        own = bool(me.get("session_id")) and me["session_id"] in [r.get("session_id"), *r.get("earlier_ids", [])]
        waiting = any(not i.get("delivered_at") and not i.get("seen_by_agent") for i in rec.get("outbox", []))
        if not own and waiting and me.get("session_id") and me.get("runtime") == r.get("runtime") and me.get("cwd") == r.get("cwd"):
            own = transcripts.is_copy(r.get("runtime"), r.get("session_id"), me["session_id"], rec["id"])   # it continues the asker
        if own or a.all or r.get("agent") == me["agent"]:
            store.update(rec["id"], lambda x, own=own: take(x, own or a.all))
    if not shown:
        print("No approval answers waiting for you.")


def cmd_show(a):
    print(json.dumps(store.load(a.id), indent=1, ensure_ascii=False))


def cmd_list(a):
    for rec in store.all_records():
        if a.open and rec["status"] in ("done", "declined", "withdrawn"):
            continue
        print(f'{rec["id"]}  {rec["status"]:9}  v{len(rec["versions"])}  {rec["agent"]}: {rec["title"]}')


def cmd_purge_tests(a):
    n = 0
    for rec in store.all_records():
        if not rec.get("test") or (a.only and rec["id"] not in a.only):
            continue
        if rec.get("thread_ts"):
            try:
                for m in slackapi.replies(rec["thread_ts"]):
                    if m.get("user") == slackapi.bot_user() or m["ts"] == rec["thread_ts"]:
                        try:
                            slackapi.delete(m["ts"])
                        except Exception:
                            pass
            except Exception:
                pass
        (config.REQUESTS / f'{rec["id"]}.json').unlink(missing_ok=True)
        n += 1
    print(f"Removed {n} test requests and their Slack posts.")


def cmd_doctor(a):
    from . import doctor
    sys.exit(doctor.run())


def cmd_listen(a):
    from . import listener
    listener.main()


def main(argv=None):
    p = argparse.ArgumentParser(prog="approvals", description="Ask for approval as a Slack card; the answer comes back into this session.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="post a new request; prints its number")
    s.add_argument("card", help="card JSON file, or - for stdin")
    s.add_argument("--agent", help="your agent name, as the approver knows it (e.g. Monetization)")
    s.add_argument("--test", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--deadline-minutes", type=int, help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_submit)

    s = sub.add_parser("ask", help="ask the approver one question in the request's thread")
    s.add_argument("id")
    s.add_argument("question")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("revise", help="post the next version in the thread")
    s.add_argument("id")
    s.add_argument("card")
    s.set_defaults(fn=cmd_revise)

    s = sub.add_parser("ack", help="acknowledge an answer in the thread: what you understood and what you'll do")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--agent")
    s.set_defaults(fn=cmd_ack)

    s = sub.add_parser("done", help="mark an approved request done, with what you did and checked")
    s.add_argument("id")
    s.add_argument("readback")
    s.set_defaults(fn=cmd_done)

    s = sub.add_parser("withdraw", help="take down a card you no longer stand behind, saying why")
    s.add_argument("id")
    s.add_argument("why")
    s.set_defaults(fn=cmd_withdraw)

    s = sub.add_parser("record", help="record an answer the approver gave somewhere else")
    s.add_argument("id")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--approved", action="store_true")
    g.add_argument("--declined", action="store_true")
    s.add_argument("--words", required=True, help="his exact words")
    s.add_argument("--where", required=True, help="where he said it, e.g. 'ChatGPT relay' or 'agent chat'")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("inbox", help="answers waiting for you")
    s.add_argument("--agent")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("list")
    s.add_argument("--open", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("purge-tests", help=argparse.SUPPRESS)
    s.add_argument("--only", nargs="*", help="just these TEST requests")
    s.set_defaults(fn=cmd_purge_tests)

    s = sub.add_parser("doctor", help="check the configuration, Slack access and tools the service needs")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("listen", help="run the service (launchd runs this)")
    s.set_defaults(fn=cmd_listen)

    a = p.parse_args(argv)
    if not os.environ.get("APPROVALS_HOME"):   # the services and tests name their folder
        rid = getattr(a, "id", None) or (getattr(a, "only", None) or [None])[0]
        try:
            config.use(config.choose(rid))
        except config.ConfigError as e:
            _die(str(e))
    a.fn(a)


if __name__ == "__main__":
    main()
