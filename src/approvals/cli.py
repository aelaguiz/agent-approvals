"""The `approvals` command: what agents use, plus `listen` for the service."""
import argparse
import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

from . import cards, config, core, pictures, reach, slackapi, store, transcripts


def _load_card(path):
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    return json.loads(text)


def _die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def _prepare_media(card, version):
    """Upload the pictures privately and publish any videos; store the handles on the version. A video's first
    frame gets a play mark, and a picture with a `mark` gets an outline round what changed."""
    if card.get("image"):
        path = pictures.with_play_mark(card["image"]) if card.get("video") else card["image"]
        version["image_file_id"] = slackapi.upload_private_image(path, card["title"])
    if card.get("video"):
        version["video_url"] = slackapi.share_video(card["video"], card["title"], card.get("video_note") or "A video waiting for approval.")
    videos = {}
    for key, path in cards.gallery_videos(card):
        url = slackapi.share_video(path, card["title"], "A clip waiting for approval.")
        if url:
            videos[key] = url
    if videos:
        version["videos"] = videos


def _share_pictures(rid, n):
    """The card's pictures go in its thread, one post, full size; Slack then shows them in the card too, which is
    redrawn with them. A carousel picture is fitted to the card's landscape frame (zoomed onto what changed when
    it's marked) and also posted whole, at its own resolution, for its Full size button."""
    rec = store.load(rid)
    card = rec["versions"][n - 1]["card"]
    todo = []
    for key, path, mark in cards.pictures(card):
        if cards.in_carousel(card, key):
            todo.append((key + ":card", pictures.card_frame(path, mark)))
            todo.append((key, pictures.outlined(path, mark) if mark else path))
        else:
            todo.append((key, pictures.outlined(path, mark) if mark else path))
    if not todo:
        return
    what = "The pictures on this card" if n == 1 else f"The pictures on {cards.version_label(n)}"
    refs = slackapi.upload_pictures_to_thread([(path, card["title"]) for _, path in todo], rec["thread_ts"], f"🖼 {what}, full size.")
    files = {key: ref for (key, _), ref in zip(todo, refs)}
    store.update(rid, lambda r: r["versions"][n - 1].__setitem__("files", files))


def _video_in_thread(rid, n):
    """No share command configured: put the videos in the thread, where Slack plays them, and link the card to them."""
    rec = store.load(rid)
    v = rec["versions"][n - 1]
    card = v["card"]
    todo = [("video", card["video"])] if card.get("video") and not v.get("video_url") else []
    todo += [(k, path) for k, path in cards.gallery_videos(card) if not (v.get("videos") or {}).get(k)]
    if not todo:
        return
    links = {k: slackapi.upload_to_thread(path, rec["thread_ts"], card["title"], "The video, to play here.") for k, path in todo}

    def put(r):
        ver = r["versions"][n - 1]
        if "video" in links:
            ver["video_url"] = links.pop("video")
        ver.setdefault("videos", {}).update(links)
    store.update(rid, put)
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
    who = reach.capture(card.get("agent") or a.agent)
    if not who["session_id"]:
        # A card with no asker can never have its answer typed back: every reply would wait in an inbox. The way
        # this happened was posting over ssh from another machine, whose shell carries no session.
        _die("This shell isn't a Claude or Codex session, so the answer to this card could never reach you. "
             "Run `approvals submit` in your own session, on the machine you run on; every machine has the command.")
    hours = card.get("deadline_hours") or cards.TYPES[card["type"]]["hours"]
    deadline = store.now() + (timedelta(minutes=a.deadline_minutes) if a.deadline_minutes else timedelta(hours=hours))
    version = {"n": 1, "card": card, "posted_at": store.iso()}
    _prepare_media(card, version)
    rec = store.create({
        "type": card["type"], "title": card["title"], "agent": who["agent"],
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
    if cards.pictures(card):
        _share_pictures(rec["id"], 1)
        core.refresh_and_clear(rec["id"])
    _video_in_thread(rec["id"], 1)
    _post_evidence(rec, card.get("evidence"))
    print(rec["id"])
    print(slackapi.permalink(ts))


def cmd_ask(a):
    def fn(rec):
        reach.refresh(rec, reach.capture())
        rec["open_question"] = {"text": a.question, "at": store.iso()}
        rec["status"] = "question"
        core.mark_acked(rec)
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
        core.mark_acked(r)
        r["versions"].append(version)
        r["status"] = "pending"
        r["open_question"] = None
        if r.get("decision"):
            # The answer was to the earlier version; this one gets its own.
            r["versions"][prev["n"] - 1]["decision"] = r["decision"]
            r["decision"] = None
            r["card_dirty"] = True
        if r.get("answers"):
            # A row left exactly as it was keeps its answer; a changed row, or one that closed unanswered, is asked again.
            r["versions"][prev["n"] - 1]["answers"] = r["answers"]
            before = {cards.part_id(p, j): p for j, p in enumerate(prev["card"].get("parts") or [], 1)}
            r["answers"] = {pid: a for j, p in enumerate(card.get("parts") or [], 1)
                            if (pid := cards.part_id(p, j)) in r["answers"] and before.get(pid) == p
                            and (a := r["answers"][pid])["verdict"] in ("approved", "declined", "chose")}
        core.settle(r)
        hours = card.get("deadline_hours") or cards.TYPES[card["type"]]["hours"]
        r["deadline"] = store.iso(store.now() + timedelta(hours=hours))
        r["title"] = card["title"]
        store.event(r, "revised", version=n)
        return r
    rec = store.update(a.id, add)
    # The new version goes at the bottom of the thread, where he reads; an earlier copy there shrinks to a line.
    _share_pictures(a.id, n)
    rec = store.load(a.id)
    ts_new = slackapi.post(cards.card_blocks(rec, n, "open"), cards.card_text(rec, n), thread_ts=rec["thread_ts"])
    store.update(a.id, lambda r: r["versions"][n - 1].__setitem__("copy_ts", ts_new))
    core.refresh_and_clear(a.id)
    _video_in_thread(a.id, n)
    ts_line = slackapi.post(cards.new_version_blocks(rec, n), f"{rec['agent']} posted v{n}: {card['title']}", thread_ts=rec["thread_ts"], broadcast=True)
    store.update(a.id, lambda r: r["versions"][n - 1].__setitem__("line_ts", ts_line))
    if prev.get("line_ts"):   # only the newest version is announced, in the channel and the thread
        try:
            slackapi.delete(prev["line_ts"])
        except Exception as e:
            print(f"(couldn't take down v{prev['n']}'s line in the channel: {e})", file=sys.stderr)
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
            return rec["status"] + (f" with {len(cards.open_rows(rec))} part(s) still open" if cards.open_rows(rec) else "")
        rec["done"] = {"text": a.readback, "at": store.iso()}
        rec["status"] = "done"
        core.mark_acked(rec)
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
        core.mark_acked(rec)
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
    if not (a.approved or a.declined or a.choice):
        _die("Say what they answered: --approved, --declined, or --choice <option>.")
    verdict = "approved" if a.approved or a.choice else "declined"
    by = config.settings()["approver_name"]

    def fn(rec):
        n = len(rec["versions"])
        if a.part:
            j = _row(rec, a.part)
            if not j:
                return f"there is no row {a.part}"
            return core.answer_part(rec, n, j, "chose" if a.choice else verdict, by, where=a.where, words=a.words,
                                    choice=a.choice, tell=False)
        if a.choice and a.choice not in [o["name"] for o in rec["versions"][-1]["card"].get("options") or []]:
            return f"{a.choice} isn't one of this card's options"
        return core.decide(rec, n, verdict, by=by, where=a.where, words=a.words, choice=a.choice, again=True)
    r = store.update(a.id, fn)
    if r != "ok":
        _die(f"Not recorded: {r}.")
    core.refresh_and_clear(a.id)
    what = f"part {a.part} " if a.part else ""
    print(f"{a.id} {what}recorded as {'picked ' + a.choice if a.choice else verdict} ({a.where}).")


def cmd_status(a):
    if not a.text and not a.headline and a.part_of is None and not a.stands_alone:
        _die("Say what happened, or give a --headline, --part-of or --stands-alone.")
    frame = a.part_of if a.part_of is not None else (None if a.stands_alone else core.UNSET)
    kind = ("next" if a.next else "on_you" if a.on_you else "waiting" if a.waiting_on else
            "their_answer" if a.their_answer else "done")

    def fn(rec):
        reach.refresh(rec, reach.capture())
        if rec["status"] == "withdrawn":
            return "withdrawn"
        part = None
        if a.part:
            j = _row(rec, a.part)
            if not j:
                return f"has no part {a.part}"
            part = cards.part_id(rec["versions"][-1]["card"]["parts"][j - 1], j)
        core.mark_acked(rec)
        core.add_status(rec, a.text, kind, part, a.headline, frame)
        return "ok"
    r = store.update(a.id, fn)
    if r != "ok":
        _die(f"{a.id} {r}; nothing added.")
    core.refresh_and_clear(a.id)
    rec = store.load(a.id)
    if kind == "on_you":
        # It waits on the approver: the thread says so, as a question would.
        slackapi.note(rec["thread_ts"], f"⏳ {rec['agent']} is waiting on you: {a.text}")
    print(f"{a.id}: the card shows it" + (" and the morning list counts it as waiting on the approver." if kind == "on_you" else
                                          " and keeps it off the approver's morning list until a later line." if kind == "waiting" else "."))


def _row(rec, part):
    """A row of the newest version by its number (1, 2, 3) or its id; 0 when there's no such row."""
    parts = rec["versions"][-1]["card"].get("parts") or []
    if str(part).isdigit():
        return int(part) if 1 <= int(part) <= len(parts) else 0
    return next((j for j, p in enumerate(parts, 1) if cards.part_id(p, j) == part), 0)


def cmd_answer(a):
    """An answer to the approver's question, in the thread: the verdict first. It counts as the acknowledgement and
    hands a card that was back with the agent to him again as it stands, with no new version."""
    rec = store.load(a.id)
    me = reach.capture(a.agent)
    ts = slackapi.post(cards.answer_blocks(rec, a.text, store.now()), f"{rec['agent']}: {a.text}", thread_ts=rec["thread_ts"])

    def fn(r):
        reach.refresh(r, me)
        kinds = core.acknowledge(r, a.text, ts)
        back = r["status"] == "revising"
        if back:
            r["status"] = "pending"
            r["card_dirty"] = True
        store.event(r, "answered", text=a.text, ts=ts, back=back)
        return back
    back = store.update(a.id, fn)
    if back:
        core.refresh_and_clear(a.id)
    print(f"Answered in the thread of {a.id}" + ("; the card is back with the approver as it stands." if back else "."))


def cmd_repoint(a):
    """Every open request whose answers go to session OLD goes to session NEW instead, in every Slack workspace this
    machine serves. For a launcher that starts an agent again in a new session with nothing of the old one's history,
    as a pod restore does; a moved copy or a continuation is found by itself."""
    new = reach.running(a.new)
    if not new:
        _die(f"Session {a.new} isn't running on this machine, so nothing was changed.")
    if reach.running(a.old):
        _die(f"Session {a.old} is still running, so its answers stay with it; nothing was changed.")
    homes = [config.HOME] if os.environ.get("APPROVALS_HOME") else config.homes()
    moved = []
    for home in homes:
        config.use(home)
        try:
            config.require()
            config.bot_token()
        except config.ConfigError:
            continue   # a workspace this machine can't serve has no cards from it
        for rec in store.all_records():
            if rec["status"] in store.OPEN and (rec.get("reach") or {}).get("session_id") == a.old:
                if store.update(rec["id"], lambda r: reach.take_over(r, a.old, new)):
                    moved.append(rec["id"])
    print(f"Answers to {', '.join(moved)} now go to session {a.new}." if moved else
          f"No open request was waiting on session {a.old}.")


def cmd_inbox(a):
    """Answers not typed into their asker's session: ones that came while it wasn't running, and ones the
    service can't type in. Each answer goes to exactly one session: the asker gets anything not yet delivered;
    another run of the same agent gets only answers the service isn't delivering, and taking one here stops the
    service from delivering it too."""
    me = reach.capture(a.agent)
    shown = 0

    def take(rec, own):
        """The answers taken; printed once the record is saved, since a change can be made more than once."""
        taken = []
        for item in rec.get("outbox", []):
            if item.get("delivered_at") or item.get("seen_by_agent"):
                continue
            if not own and not (item.get("inbox_only") or item.get("noted_failure")):
                continue
            taken.append((item["text"], item.get("react_ts")))
            item["seen_by_agent"] = True
            item["delivered_at"] = store.iso()
            item["delivered_via"] = "inbox"
            item["claimed_by"] = me.get("session_id")
        return taken
    for rec in store.waiting_records():
        r = rec.get("reach") or {}
        own = bool(me.get("session_id")) and me["session_id"] in [r.get("session_id"), *r.get("earlier_ids", [])]
        waiting = any(not i.get("delivered_at") and not i.get("seen_by_agent") for i in rec.get("outbox", []))
        if not own and waiting and me.get("session_id") and me.get("runtime") == r.get("runtime") and me.get("cwd") == r.get("cwd"):
            own = transcripts.is_copy(r.get("runtime"), r.get("session_id"), me["session_id"], rec["id"])   # it continues the asker
        if own or a.all or r.get("agent") == me["agent"]:
            for text, react_ts in store.update(rec["id"], lambda x, own=own: take(x, own or a.all)):
                print(text)
                shown += 1
                if react_ts:   # 📨, as when the service types an answer in: the agent has it
                    try:
                        slackapi.react(react_ts, "incoming_envelope")
                    except Exception as e:
                        print(f"(couldn't mark {rec['id']} as received in Slack: {e})", file=sys.stderr)
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
        store.delete(rec["id"])
        n += 1
    print(f"Removed {n} test requests and their Slack posts.")


def cmd_doctor(a):
    from . import doctor
    sys.exit(doctor.run())


def cmd_listen(a):
    from . import listener
    listener.main()


def cmd_role(a):
    """What this machine's service does for the workspace: hub, delivery, or none (with why), for the installer."""
    try:
        config.require()
        config.bot_token()
    except config.ConfigError as e:
        print(f"none: {e}")
        return
    print("hub" if config.is_hub() else "delivery")


# Commands that post to Slack check first that this machine can, so nothing is saved for a card it can't post.
POSTS = {"submit", "ask", "revise", "answer", "ack", "done", "withdraw", "record", "status"}


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

    s = sub.add_parser("answer", help="answer the approver's question in the thread, verdict first; hands the card back to him without a new version")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--agent")
    s.set_defaults(fn=cmd_answer)

    s = sub.add_parser("ack", help="one line, only when your reply will take a while: what is coming and when")
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
    g = s.add_mutually_exclusive_group()
    g.add_argument("--approved", action="store_true")
    g.add_argument("--declined", action="store_true")
    s.add_argument("--words", required=True, help="his exact words")
    s.add_argument("--where", required=True, help="where he said it, e.g. 'ChatGPT relay' or 'agent chat'")
    s.add_argument("--part", help="the row of a set he answered, by number (1, 2, 3) or id; without it, every open row")
    s.add_argument("--choice", help="the option he picked, by its name")
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("status", help="keep a posted card true in place, with no new version: where it stands after the yes, or what it's part of")
    s.add_argument("id")
    s.add_argument("text", nargs="?", help="what happened, e.g. 'Built, and live for half of new users'")
    s.add_argument("--part", help="the row of a set it is about, by number (1, 2, 3) or id")
    k = s.add_mutually_exclusive_group()
    k.add_argument("--next", action="store_true", help="what comes next, shown with ⏳ until a later line")
    k.add_argument("--on-you", action="store_true", help="a condition only the approver can clear; the card waits on him")
    k.add_argument("--their-answer", action="store_true", help="the reply to a message sent as him")
    k.add_argument("--waiting-on", action="store_true", help="it waits on someone other than the approver; the text says who, for what, by when. Off his morning list until a later line")
    s.add_argument("--headline", help="the answered card's title, e.g. 'Released: three lesson fixes'")
    f = s.add_mutually_exclusive_group()
    f.add_argument("--part-of", help="the initiative the card belongs to, in the approver's words; sets its frame line in place")
    f.add_argument("--stands-alone", action="store_true", help="say on the card that it belongs to no initiative")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("inbox", help="answers waiting for you")
    s.add_argument("--agent")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("repoint", help="send the answers of every open request asked from session OLD to session NEW, which has taken over its work (a pod restore runs this)")
    s.add_argument("old")
    s.add_argument("new")
    s.set_defaults(fn=cmd_repoint)

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

    s = sub.add_parser("role", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_role)

    a = p.parse_args(argv)
    if not os.environ.get("APPROVALS_HOME"):   # the services and tests name their folder
        rid = getattr(a, "id", None) or (getattr(a, "only", None) or [None])[0]
        try:
            config.use(config.choose(rid))
        except config.ConfigError as e:
            _die(str(e))
    if a.cmd in POSTS:
        try:
            config.bot_token()
        except config.ConfigError as e:
            _die(f"This machine can't post the cards of the Slack workspace set up in {config.HOME}: {e}")
    try:
        a.fn(a)
    except store.StoreError as e:
        _die(str(e))


if __name__ == "__main__":
    main()
