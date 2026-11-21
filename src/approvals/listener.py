"""`approvals listen`: Slack buttons over Socket Mode, thread replies, deadlines, and delivery to agents.

On the workspace's hub it does all of that, delivers to the sessions on its own machine, and serves the records
to the other machines. On every other machine it only delivers to the sessions there.

A hub that keeps its cards in the pod dashboard's record doesn't connect to Slack. Slack sends every press and pop-up
answer to the record, which takes it (or refuses it in the presser's thread) before Slack is answered and opens each
pop-up from the ones kept with the card's version. The hub hears each change from the record's stream: it carries out
every answer taken and not carried out, in the order taken, with the same press code a Slack press runs here, and the
write that does names the answer, so the record marks it carried out once. On start it carries out whatever was taken
while it was stopped. Its own deadlines and hand-backs, the deputy's calls and answers an agent records are answers
the record takes too.
"""
import collections
import logging
import logging.handlers
import os
import queue
import ssl
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse

from . import basecamp, cards, config, core, digest, groups, router, slackapi, store, transcripts
from . import reach as reach_of

log = logging.getLogger("approvals")

_TLS = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def clear_tls_errors():
    """Empty this thread's OpenSSL error queue.

    When a TLS write fails with a system error such as a broken pipe, Python's ssl module raises it but leaves it
    queued on the thread, and it doesn't clear the queue before reading. On that thread, every later read that has
    to wait for data then fails with the same broken pipe, on any connection. Python clears the queue whenever it
    makes a TLS object, so making a throwaway one empties it.
    """
    _TLS.wrap_bio(ssl.MemoryBIO(), ssl.MemoryBIO(), server_hostname="slack.com")


class SlackConnection(SocketModeClient):
    """Slack's built-in Socket Mode client, with each connection read from a clean TLS error queue.

    The client opens connections on one thread and reads all of them on another, answering Slack's pings as it
    reads. On November 8, 2026 a ping went to a connection that had already died, and from then on every new
    connection failed on its first read: the hub reconnected every 5 seconds for 42 minutes and heard no button
    presses until the service restarted.
    """

    def _run_current_session(self):
        clear_tls_errors()
        super()._run_current_session()

WATCH_DAYS = 14          # threads stay watched this long after their last activity
POLL_BUDGET_PER_MIN = 40
DM_POLL_SECONDS = 15
FAILURE_NOTE_AFTER = 3
ACK_MINUTES = 10         # a delivered answer the agent hasn't acknowledged by then gets a warning in the thread
FETCH_SECONDS = 5        # how often a machine other than the hub asks it for answers to deliver
WAITING = "hourglass_flowing_sand"   # ⏳ on a card while its decision waits for the agent to catch up
STRAY_SECONDS = 60       # how often a machine looks for answers whose session turned up on it
QUIET = 60               # reconnect when Slack hasn't answered a ping for this long
HELLO = 30               # reconnect when a new connection hasn't heard Slack's hello, which comes within a second or two


def is_approver(msg, rec, *, allow_workspace_team=True):
    """Replies from someone who can answer count, along with a stand-in's "[test reply]" posts on TEST cards."""
    if core.can_answer(rec, msg.get("user"), allow_workspace_team=allow_workspace_team) and not msg.get("bot_id"):
        return True
    tester = config.test_approver()
    return (bool(tester) and bool(rec.get("test")) and msg.get("user") == tester
            and (msg.get("text") or "").startswith("[test reply]"))


def is_ask(msg, target=None):
    """The approver posting in the channel itself, not in a card's thread, asks for a fresh list. The channel is
    only cards, so any such post means it; in a test run, the stand-in's "[test reply]" posts count."""
    return (msg.get("thread_ts") in (None, msg.get("ts")) and not msg.get("subtype")
            and is_approver(msg, {"test": config.is_test_run(), "target": target}, allow_workspace_team=False))


def reply_text(msg):
    t = msg.get("text") or ""
    return t[len("[test reply]"):].strip() if t.startswith("[test reply]") else t


class Listener:
    def __init__(self):
        self.s = config.require()
        self.poke = set()                 # threads to poll right away (from message events)
        self.poke_lock = threading.Lock()
        self.deliveries = queue.Queue()
        self.in_flight = set()
        self.awaiting = {}                # (request id, ref) -> (runtime, session id, monotonic time typed)
        self.next_poll = {}               # thread_ts -> monotonic time it is next due
        self.stop = threading.Event()
        self.me = config.machine()
        self.next_stray_look = 0.0
        self.steps = queue.Queue()        # requests whose Basecamp steps this machine should write
        self.steps_queued = set()
        self.carrying = threading.Lock()  # one pass over the record's waiting answers at a time, so each in order
        self.answers_waiting = threading.Event()
        self.outcomes = collections.OrderedDict()   # answer id -> None (carried out) or the refusal's words
        self.deputy_live = None           # auto-approval as last heard from the record

    @staticmethod
    def later(follow_up):
        """What follows an answer carried out (the card and the list redrawn, words in the thread), off the carrier."""
        threading.Thread(target=follow_up, daemon=True).start()

    # ----- buttons -----

    def on_socket(self, client, req):
        kind = (req.payload or {}).get("type")
        if req.type == "interactive" and kind in ("block_actions", "view_submission"):
            follow_up, answer = None, None
            acting = store.ACTOR.set((req.payload.get("user") or {}).get("id"))
            try:
                if kind == "block_actions":
                    follow_up = self.handle_click(req.payload)
                else:
                    answer, follow_up = self.handle_submission(req.payload)
            except Exception:
                log.exception("%s failed", kind)
            finally:
                store.ACTOR.reset(acting)
            client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id, payload=answer))
            if follow_up:
                threading.Thread(target=follow_up, daemon=True).start()
            return
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type == "events_api":
            ev = (req.payload or {}).get("event") or {}
            who = ev.get("user")
            if ev.get("type") != "message" or not ev.get("thread_ts") or not who:
                return
            targets = config.targets().values()
            if ev.get("channel") not in {t["channel"] for t in targets}:
                return
            people = {t["approver"] for t in targets} | {config.test_approver()}
            people.update(p["user"] for p in config.settings()["team"].values())
            if who in people:
                with self.poke_lock:
                    self.poke.add((ev["channel"], ev["thread_ts"]))

    REFUSED = {
        "stale": "That version was replaced; use the newest card's buttons.",
        "already": "This one is already decided.",
        "withdrawn": "The agent withdrew this one, so there's nothing to decide.",
        "removed": "You removed this one from your list, so there's nothing to decide.",
        "gone": "That button doesn't match the card any more; use the newest card's buttons.",
        "taken": "You already took this one back.",
    }

    def answer_refusal(self, rec, n, what, user):
        target = config.card_target(rec)
        if user == target["approver"]:
            return None
        only = f"Only {target['approver_name']} can decide approvals."
        if what in ("remove", "takeback", "mine", "sleep", "wake") or what.startswith("hand:"):
            return only
        send = cards.also_on(rec, n)
        if n != len(rec["versions"]) and send and send["user"] == user and (send.get("sent_at") or send.get("handed")):
            return self.REFUSED["stale"]
        if not core.can_answer(rec, user, allow_workspace_team=what != "notmine"):
            return only
        bad = core._check(rec, n)
        if bad:
            return self.REFUSED[bad]
        if rec["status"] not in ("pending", "question"):
            return self.REFUSED["already"]
        return None

    def handle_click(self, payload):
        """Save the decision and the agent's message before acking; card and notes follow. A button that opens a
        pop-up opens it right after the ack, inside the 3 seconds Slack allows."""
        user = (payload.get("user") or {}).get("id")
        action = (payload.get("actions") or [{}])[0]
        if (payload.get("view") or {}).get("callback_id") == "approvals" and action.get("action_id") == "items":
            return lambda: self.recount_items(payload)
        try:
            value = action["selected_option"]["value"] if action.get("type") == "overflow" else action.get("value", "")
            rid, n, what = value.split("|", 2)
            n = int(n)
        except ValueError:
            return None   # a link button: Slack opens it
        thread_ts = ((payload.get("container") or {}).get("thread_ts")
                     or (payload.get("message") or {}).get("thread_ts"))
        channel_id = (payload.get("channel") or {}).get("id") or (payload.get("container") or {}).get("channel_id")
        trigger = payload.get("trigger_id")
        rec = None if rid == "deputy" else store.load(rid)
        target = config.target() if rec is None else config.card_target(rec)
        source_channel = ((rec or {}).get("thread") or {}).get("channel")
        send = cards.also_on(rec, n) if rec else None
        if channel_id and channel_id not in (target["channel"], source_channel, (send or {}).get("channel")):
            return None
        if what == "see" or what.startswith(("read:", "pic:")):
            return lambda: self.open_view(rid, n, what, trigger, user, thread_ts, channel_id)
        if rid == "deputy":
            if user != target["approver"]:
                return lambda: slackapi.ephemeral(user, f"Only {target['approver_name']} can decide approvals.", thread_ts, channel_id=channel_id)
            return self.switch_deputy(what == "live", payload, user)
        refused = self.answer_refusal(rec, n, what, user)
        if refused:
            return lambda: slackapi.ephemeral(user, refused, thread_ts, channel_id=channel_id)
        if what in ("change", "edit", "items") or what.endswith(":change"):
            return lambda: self.open_view(rid, n, what, trigger, user, thread_ts, channel_id)
        r, follow_up = self.press(rec, n, what, user)
        if r != "ok":
            return lambda: slackapi.ephemeral(user, self.REFUSED.get(r, r), thread_ts, channel_id=channel_id)
        return follow_up

    def press(self, rec, n, what, user, words=None, until=None):
        """A press of one of the card's buttons, saved, as (result, what follows it): the card and the list redrawn,
        and for Not mine the line in the thread. The pod dashboard's presses come here too (`approvals press`), and
        its yes or no with a note adds his words as a reply in the thread, in the same write: the two things he does
        in Slack for the same result. Sleep and Wake now are the dashboard's own; they redraw nothing."""
        rid = rec["id"]

        def apply(r):
            if what == "sleep":
                return self.answer_refusal(r, n, what, user) or core.sleep(r, n, user, until), None
            if what == "wake":
                return self.answer_refusal(r, n, what, user) or core.wake(r, n, user), None
            dm = core.conversation_send(r) if words and user == config.card_target(r)["approver"] else None
            result = self.answer_refusal(r, n, what, user) or core.press(r, n, what, by=user)
            if result == "ok" and words:
                core.take_replies(r, [(None, words)], react_ts=core.card_ts(r), by=user)
            send = cards.also_on(r, n) if result == "ok" and what == "notmine" else dm
            return result, send
        r, send = store.update(rid, apply)
        log.info("click %s v%s %s -> %s", rid, n, what, r)
        if r != "ok":
            return r, None
        if what in ("sleep", "wake"):
            return r, None
        if what == "notmine" and send:
            if send.get("handed"):
                text = f"{cards.esc(send['name'])} handed this back, so it's back with you."
                return r, lambda: (self.broadcast_return(rec, text), self.try_refresh(rid), self.refresh_list())
            return r, lambda: self.try_refresh(rid)
        if words:
            name = core.answerer(rec, {"by": user, "version": n})
            return r, lambda: (self.after_submission(rid, "note", words, "dashboard:" + store.iso(), name, send),
                               self.refresh_list())
        return r, lambda: (self.try_refresh(rid), self.refresh_list())

    def switch_deputy(self, live, payload, user):
        """Turn auto-approval on or pause it, from the morning summary's button: saved before the ack, then the
        summary is redrawn with the other button and who switched it when."""
        d = store.deputy()
        if not d:
            return lambda: slackapi.ephemeral(user, "No session is the deputy now, so there's nothing to switch.")
        store.set_deputy(dict(d, live=live, live_since=store.iso() if live else None))
        log.info("auto-approval %s from the summary", "on" if live else "paused")
        msg = payload.get("message") or {}
        ts = (payload.get("container") or {}).get("message_ts") or msg.get("ts")

        def redraw():
            try:
                slackapi.update(ts, digest.switched(msg.get("blocks") or [], live, store.now()), msg.get("text") or "")
            except Exception as e:
                log.warning("redrawing the deputy's summary failed: %s", e)
        return redraw

    def refresh_list(self):
        """A press, on the card or on the list, crosses its line off the list now rather than at the next sweep, so
        the approver going down the list sees each answer land."""
        try:
            for name in config.targets():
                digest.tick(**({"target": name} if name is not None else {}))
        except Exception as e:
            log.warning("the list's refresh after a press failed, the sweep will retry: %s", e)

    def open_view(self, rid, n, what, trigger, user, thread_ts, channel_id=None):
        """Open the pop-up a button asks for, unless the card moved on since it was drawn."""
        rec = store.load(rid)
        if what != "see" and not what.startswith(("read:", "pic:")):
            refused = self.answer_refusal(rec, n, what, user)
            if refused:
                return slackapi.ephemeral(user, refused, thread_ts, channel_id=channel_id)
            bad = core._check(rec, n)
            j = int(what[1:].split(":")[0]) if what.startswith("p") else None
            if not bad and j and cards.part_id(rec["versions"][-1]["card"]["parts"][j - 1], j) in (rec.get("answers") or {}):
                bad = "already"
            if not bad and not j and (rec.get("decision") or {}).get("version") == n:
                bad = "already"
            if bad:
                return slackapi.ephemeral(user, self.REFUSED[bad], thread_ts, channel_id=channel_id)
        if what == "see":
            view = cards.see_view(rec, n)
        elif what.startswith("read:"):
            view = cards.read_view(rec, n, int(what.split(":")[1]))
        elif what.startswith("pic:"):
            view = cards.picture_view(rec, n, what[4:])
        elif what == "edit":
            view = cards.edit_view(rec, n)
        elif what == "items":
            view = cards.items_view(rec, n)
        else:
            view = cards.change_view(rec, n, int(what[1:].split(":")[0]) if what.startswith("p") else None)
        slackapi.views_open(trigger, view)

    def recount_items(self, payload):
        """A tick in the Pick which pop-up: its button says how many it sends."""
        view = payload["view"]
        rid, n, _ = view["private_metadata"].split("|", 2)
        rec = store.load(rid)
        if self.answer_refusal(rec, int(n), "items", payload["user"]["id"]):
            return
        ticked = self._ticked(view)
        slackapi.views_update(view["id"], cards.items_view(rec, int(n), ticked), view.get("hash"))

    @staticmethod
    def _ticked(view):
        return sorted(int(o["value"]) for block in (view.get("state") or {}).get("values", {}).values()
                      for o in (block.get("items") or {}).get("selected_options") or [])

    def handle_submission(self, payload):
        """A pop-up's button: the approver's change, his own words, or the items he ticked. Returns the answer for
        Slack (errors keep the pop-up open) and what to do after the ack."""
        user = (payload.get("user") or {}).get("id")
        view = payload.get("view") or {}
        if view.get("callback_id") != "approvals":
            return None, None
        rid, n, what = view["private_metadata"].split("|", 2)
        n = int(n)
        values = (view.get("state") or {}).get("values") or {}
        if what == "see" or what.startswith(("read:", "pic:")):
            return None, None
        first = next(iter(values), "words")
        rec = store.load(rid)
        refused = self.answer_refusal(rec, n, what, user)
        if refused:
            return {"response_action": "errors", "errors": {first: refused}}, None
        words = ((values.get("words") or {}).get("words") or {}).get("value") or ""
        ticked = None
        if what == "items":
            ticked = self._ticked(view)
            if not ticked:
                return {"response_action": "errors", "errors": {first: "Tick at least one, or press No on the card."}}, None
        elif what == "edit":
            if not words.strip():
                return {"response_action": "errors", "errors": {"words": "The words can't be empty."}}, None
        r, follow_up = self.submit(rec, n, what, words, user, "view:" + view.get("id", ""), ticked)
        if r != "ok":
            return {"response_action": "errors", "errors": {first: self.REFUSED.get(r, r)}}, None
        return None, follow_up

    def submit(self, rec, n, what, words, user, key, ticked=None):
        """A pop-up's answer saved, as (result, what follows it): the items he ticked, his own words, or his change,
        then the card redrawn and his words in the thread. The pod dashboard's Change and Edit the words come here
        too (`approvals press`)."""
        rid = rec["id"]
        if what == "items":
            apply = lambda rec: core.decide(rec, n, "approved", by=user, where="pop-up", items=ticked)
        elif what == "edit":
            apply = lambda rec: core.decide(rec, n, "approved", by=user, where="pop-up", edited=words)
        else:
            j = int(what[1:].split(":")[0]) if what.startswith("p") else None
            apply = lambda rec: core.ask_change(rec, n, words, by=user, j=j)
        def save(rec):
            # Edited words can close the card. Check who was in the conversation when they were submitted.
            send = (core.conversation_send(rec) if user == config.card_target(rec)["approver"]
                    and (what == "edit" or what.endswith("change")) else None)
            return self.answer_refusal(rec, n, what, user) or apply(rec), send

        r, send = store.update(rid, save)
        log.info("pop-up %s v%s %s -> %s", rid, n, what, r)
        if r != "ok":
            return r, None
        name = core.answerer(rec, {"by": user, "version": n})
        return r, lambda: self.after_submission(rid, what, words, key, name, send)

    def after_submission(self, rid, what, words, key, who, send):
        """Words from a pop-up, or a note on a yes or no from the pod dashboard, go in the thread too, so the thread
        keeps the whole record."""
        self.try_refresh(rid)
        rec = store.load(rid)
        if words and (what in ("edit", "note") or what.endswith("change")):
            self.copy_to_source(rec, words, key, store.now(), who)
        try:
            if what == "note":
                slackapi.note(rec["thread_ts"], f"💬 {cards.esc(who)} replied on the card:\n{cards.quoted(words)}", **config.channel_args(rec))
            elif what == "edit":
                slackapi.note(rec["thread_ts"], f"✍️ {who} approved it with these words:\n{cards.quoted(words)}", **config.channel_args(rec))
            elif what.endswith("change"):
                row = f" to part {what[1:].split(':')[0]}" if what.startswith("p") else ""
                slackapi.note(rec["thread_ts"], f"✏️ {who} asked for a change{row}:\n{cards.quoted(words)}", **config.channel_args(rec))
        except Exception as e:
            log.warning("thread note for %s failed: %s", rid, e)
        if send and words:
            core.post_to_teammate(rid, send, f"💬 {cards.esc(who)} replied on the card:\n{cards.quoted(words)}")

    def copy_to_source(self, rec, words, key, at, name=None):
        source = rec.get("thread") or {}
        if not source.get("small_ts") or key in source.get("copied_keys", []):
            return
        target = config.card_target(rec)
        line = cards.ctx(f"💬 {cards.esc(name or target['approver_name'])}, on the card in <#{target['channel']}> · {cards.when(at)}")
        try:
            # One quote when it fits a section's 3,000 characters once escaped, as nearly every reply does; else in pieces.
            chunks = ([cards.md(cards.quoted(words))] if len(cards.quoted(words)) <= 2900
                      else [cards.md(cards.quoted(words[i:i + 500])) for i in range(0, len(words), 500)])
            ts = slackapi.post([line, *chunks[:49]], words, thread_ts=source["root_ts"], channel_id=source["channel"],
                               sender=rec.get("as"))
            def saved(r):
                r["thread"].setdefault("copied_keys", []).append(key)
                r["thread"].setdefault("copied_ts", []).append(ts)
            store.update(rec["id"], saved)
            # His reply puts the card back with its agent; the card itself isn't redrawn for that, the small card is,
            # so the thread doesn't show buttons above the words that just overtook them.
            now = store.load(rec["id"])
            slackapi.update(source["small_ts"], cards.small_blocks(now), cards.card_text(now, len(now["versions"])),
                            channel_id=source["channel"])
        except Exception as e:
            log.warning("copying reply to source for %s failed: %s", rec["id"], e)

    def try_refresh(self, rid):
        try:
            core.refresh_and_clear(rid)
        except Exception as e:
            log.warning("card refresh for %s failed, will retry: %s", rid, e)

    # ----- thread replies -----

    def watched(self):
        cutoff = store.now() - timedelta(days=WATCH_DAYS)
        out = []
        for rec in store.all_records():
            if not rec.get("thread_ts"):
                continue
            last = max(datetime.fromisoformat(e["at"]) for e in rec.get("events") or [{"at": rec["created"]}])
            if last >= cutoff:
                out.append(rec)
        return out

    def poll_once(self):
        recs = {}
        for rec in self.watched():
            recs[config.card_target(rec)["channel"], rec["thread_ts"]] = (rec, None)
            send = cards.also_on(rec, len(rec["versions"]))
            if send and send.get("sent_at") and not send.get("handed_back_at") and rec["status"] not in core.FINAL:
                recs[send["channel"], send["ts"]] = (rec, send)
        if not recs:
            return
        now = time.monotonic()
        with self.poke_lock:
            self.poke &= set(recs)
            poked = set(self.poke)
        interval = max(15.0, len(recs) * 60.0 / POLL_BUDGET_PER_MIN)
        due = [ts for ts in recs if ts in poked or self.next_poll.get(ts, 0) <= now]
        # Message events and due direct messages go ahead of the overdue rotation.
        due.sort(key=lambda ts: (ts not in poked, recs[ts][1] is None, self.next_poll.get(ts, 0)))
        per_cycle = max(1, POLL_BUDGET_PER_MIN * 5 // 60)   # this loop runs every 5 seconds
        for ts in due[:per_cycle]:
            rec, send = recs[ts]
            self.next_poll[ts] = now + (DM_POLL_SECONDS if send else interval)
            with self.poke_lock:
                self.poke.discard(ts)   # a flag that didn't fit this round keeps its place for the next
            try:
                if send:
                    self.poll_thread(rec, send)
                else:
                    self.poll_thread(rec)
            except Exception:
                log.exception("polling %s failed", rec["id"])

    def poll_thread(self, rec, send=None):
        thread = send["ts"] if send else rec["thread_ts"]
        channel = {"channel_id": send["channel"]} if send else config.channel_args(rec)
        last = (send or rec).get("last_seen") or thread
        msgs = [m for m in slackapi.replies(thread, oldest=last, **channel) if float(m["ts"]) > float(last)]
        if not msgs:
            return
        newest = max(m["ts"] for m in msgs)

        def apply(r):
            if send:
                seen = cards.also_on(r, len(r["versions"]))
                if not seen or seen.get("ts") != thread or seen.get("handed_back_at") or r["status"] in core.FINAL:
                    return []
            else:
                seen = r
            cursor = float(seen.get("last_seen") or last)
            if float(newest) <= cursor:
                return []
            seen["last_seen"] = newest
            accepted = []
            for m in msgs:   # each its own message, so a correction reaches the agent too
                ts, words, user = m["ts"], reply_text(m), m.get("user")
                if float(ts) <= cursor or not words.strip() or not is_approver(m, r) or (send and user != seen["user"]):
                    continue
                n = len(r["versions"])
                member = cards.teammate(r, {"by": user, "version": n})
                if member and not send and not config.settings()["team_answers_any"]:
                    current = cards.also_on(r, n)
                    since = store.parse(current["handed"]["at"]).timestamp() if current.get("handed") else float(current["ts"])
                    if float(ts) < since:
                        continue
                dm = core.conversation_send(r) if not send and user == config.card_target(r)["approver"] else None
                kind = core.take_replies(r, [(ts, words)], react_ts=core.card_ts(r) if send else None, by=user)
                name = member or core.who(r)
                accepted.append((ts, words, name, kind, dm))
            return accepted

        accepted = store.update(rec["id"], apply)
        if accepted:
            for ts, words, name, kind, dm in accepted:
                log.info("%s: reply from %s -> %s", rec["id"], name, kind)
                if send:
                    try:
                        slackapi.note(rec["thread_ts"], f"💬 {cards.esc(name)} replied in a direct message:\n{cards.quoted(words)}",
                                      **config.channel_args(rec))
                    except Exception as e:
                        log.warning("direct message reply note for %s failed: %s", rec["id"], e)
                else:
                    self.copy_to_source(store.load(rec["id"]), words, "reply:" + ts,
                                        datetime.fromtimestamp(float(ts), tz=timezone.utc), name)
                    core.post_to_teammate(rec["id"], dm, f"💬 {cards.esc(name)} replied on the card:\n{cards.quoted(words)}")
            if store.load(rec["id"]).get("card_dirty"):
                self.try_refresh(rec["id"])

    # ----- the list of what waits on the approver -----

    def lists(self):
        for name, target in config.targets().items():
            kw = {"target": name} if name is not None else {}
            st = digest.load_state(**kw)
            asked = bool(st) and any(is_ask(m, name) for m in slackapi.history(st["ts"], **({"channel_id": target["channel"]} if name is not None else {})))
            digest.tick(asked=asked, **kw)

    # ----- deadlines, card refreshes, deliveries -----

    def sweep(self):
        def expire_due(rec):
            if rec["status"] in ("pending", "question") and store.parse(rec["deadline"]) <= store.now():
                core.expire(rec)
                return True

        records = store.all_records()
        for i, rec in enumerate(records):
            if not rec.get("thread_ts"):
                continue
            if rec["status"] in ("pending", "question") and store.parse(rec["deadline"]) <= store.now():
                if store.record():
                    self.give_own(rec, "expire", f"expire:{rec['id']}:{rec['deadline']}")
                elif store.update(rec["id"], expire_due):
                    log.info("%s expired", rec["id"])
                rec = store.load(rec["id"])
            send = cards.also_on(rec, len(rec["versions"]))
            if (send and not any(send.get(k) for k in ("sent_at", "handed_back_at", "skipped"))
                    and (not send.get("send_after") or store.parse(send["send_after"]) <= store.now())):
                self.send_also(rec, send)
                rec = store.load(rec["id"])
                send = cards.also_on(rec, len(rec["versions"]))
            if (cards.handed(rec) and send.get("sent_at") and store.parse(send["return_at"]) <= store.now()
                    and not core._not_callable(rec, send["version"], send["at"])):
                if store.record():
                    self.give_own(rec, "return", f"return:{rec['id']}:{send['at']}")
                else:
                    self.return_hand(rec)
                rec = store.load(rec["id"])
            if rec.get("card_dirty"):
                self.try_refresh(rec["id"])
            self.warn_unacknowledged(rec)
            records[i] = rec

        if config.is_hub():
            for rid in groups.refresh(records):
                self.try_refresh(rid)

    def send_also(self, rec, send):
        rid, n = rec["id"], send["version"]
        index = rec["also"].index(send)
        try:
            if not core._not_callable(rec, n, send["at"]):
                channel = slackapi.open_dm(send["user"])
                rec = store.load(rid)
                if (n == len(rec["versions"]) and rec["also"][index] is cards.also_on(rec, n)
                        and not core._not_callable(rec, n, send["at"])):
                    sent = {"channel": channel, "sent_at": store.iso()}
                    sent["ts"] = slackapi.post(cards.also_blocks(rec, dict(send, **sent)), cards.card_text(rec, n),
                                              channel_id=channel)

                    def save(r):
                        r["also"][index].update(sent)
                        store.event(r, "also_sent", version=n, name=send["name"], send_after=send.get("send_after"))
                        r["card_dirty"] = True
                    store.update(rid, save)
                    return
            def skip(r):
                r["also"][index]["skipped"] = True
                r["card_dirty"] = True
            store.update(rid, skip)
        except Exception as e:
            log.warning("direct message for %s failed, will retry: %s", rid, e)

    def return_hand(self, rec):
        def apply(r):
            if not cards.handed(r):
                return
            n = len(r["versions"])
            send = cards.also_on(r, n)
            now = store.now()
            if (not send.get("sent_at") or store.parse(send["return_at"]) > now
                    or core._not_callable(r, n, send["at"])):
                return
            send["returned_at"] = store.iso(now)
            name = send["name"]
            due = cards.when(store.parse(send["return_at"]))
            store.event(r, "returned", version=n, name=name)
            r["card_dirty"] = True
            core.tell_deputy(r, f"{name} hadn't answered by {due}, so it is back with {core.who(r)}.")
            return name
        name = store.update(rec["id"], apply)
        if name:
            hours = config.settings()["handoff_hours"]
            self.broadcast_return(rec, f"{cards.esc(name)} hasn't answered this in {hours} working hours, so it's back with you.")
        return name

    def broadcast_return(self, rec, words):
        approver = config.card_target(rec)["approver"]
        text = f"↩️ <@{approver}> {words}"
        try:
            slackapi.post([cards.md(text)], text, thread_ts=rec["thread_ts"], broadcast=True, **config.channel_args(rec))
        except Exception:
            log.exception("hand-off return for %s failed", rec["id"])

    def deliver_due(self):
        """On the hub, every few seconds: what's due for this machine's sessions, and ⏳ kept true on the cards."""
        recs = [r for r in store.all_records() if r.get("thread_ts")]
        due, strays = core.to_deliver(recs, self.me, store.reviewer(), store.deputy())
        self.queue_steps(basecamp.due(recs, self.me))
        self.queue(due)
        self.take_strays(strays)
        self.show_waiting(recs, core.held(recs))

    def show_waiting(self, recs, hold):
        """⏳ on a card while one of its decisions waits for the agent to catch up; gone once none does."""
        for rec in recs:
            on = sorted({i["react_ts"] for i in rec.get("outbox", []) if i.get("react_ts") and (rec["id"], i["ref"]) in hold})
            was = rec.get("waiting_shown") or []
            if on == was:
                continue
            try:
                for ts in set(on) - set(was):
                    slackapi.react(ts, WAITING, **config.channel_args(rec))
                for ts in set(was) - set(on):
                    slackapi.unreact(ts, WAITING, **config.channel_args(rec))
            except Exception as e:
                log.warning("the waiting mark on %s failed, will retry: %s", rec["id"], e)
                continue
            store.update(rec["id"], lambda r, on=on: r.update(waiting_shown=on))

    # ----- deliveries to this machine's sessions -----

    def queue(self, due):
        for rid, ref in due:
            if (rid, ref) not in self.in_flight:
                self.in_flight.add((rid, ref))
                self.deliveries.put((rid, ref))

    def queue_steps(self, rids):
        """The approver's answers, and their delivery, show on the Basecamp cards the card serves. Written on their own
        thread, queued before the answer is, so they show even when typing it in fails, and a slow or failing Basecamp
        never holds up an answer."""
        for rid in rids:
            if rid not in self.steps_queued:
                self.steps_queued.add(rid)
                self.steps.put(rid)

    def step_writer(self):
        while not self.stop.is_set():
            try:
                rid = self.steps.get(timeout=1)
            except queue.Empty:
                continue
            try:
                basecamp.sync(rid)
            except Exception:
                log.exception("Basecamp step for %s crashed", rid)
            finally:
                self.steps_queued.discard(rid)

    def take_strays(self, strays, force=False):
        """An answer the card's machine couldn't deliver goes to the machine that has the session's transcript."""
        if not strays or not (force or time.monotonic() >= self.next_stray_look):
            return
        self.next_stray_look = time.monotonic() + STRAY_SECONDS
        for rid, ref, reach in strays:
            ids = [reach.get("session_id"), *reach.get("earlier_ids", [])]
            if (rid, ref) in self.in_flight or not any(transcripts.transcript(reach.get("runtime"), i) for i in ids if i):
                continue
            if store.update(rid, lambda r, ref=ref: core.claim(r, ref, self.me)):
                log.info("%s %s: its session is on this machine; delivering it here", rid, ref)
                self.queue([[rid, ref]])

    def fetch(self):
        """Off the hub: what's due for this machine's sessions."""
        due, strays, steps = store.hub_client().due(self.me)
        self.queue_steps(steps)
        self.queue(due)
        self.take_strays(strays)

    def warn_unacknowledged(self, rec):
        """Delivery only means the message was typed into the session. Say so when the agent never confirms it."""
        late = [i for i in rec.get("outbox", []) if i.get("needs_ack") and i.get("delivered_at") and not i.get("acked_at")
                and not i.get("ack_warned")
                and store.now() - datetime.fromisoformat(i["delivered_at"]) >= timedelta(minutes=ACK_MINUTES)]
        if not late:
            return
        refs = {i["ref"] for i in late}

        def mark(r):
            for i in r["outbox"]:
                if i["ref"] in refs:
                    i["ack_warned"] = True
        store.update(rec["id"], mark)
        first = min(datetime.fromisoformat(i["delivered_at"]) for i in late)
        names = {i.get("answerer") for i in late}
        whose = "your " if names == {None} else f"{late[0]['answerer']}'s " if len(names) == 1 else "the "
        what = whose + ("answer" if len(late) == 1 else f"last {len(late)} answers")
        try:
            slackapi.note(rec["thread_ts"], f"⚠️ {rec['agent']} received {what} at {cards.when(first)} (📨) but hasn't acknowledged "
                                            f"in {ACK_MINUTES} minutes, so nothing confirms it was understood or acted on.", **config.channel_args(rec))
        except Exception as e:
            log.warning("ack warning for %s failed: %s", rec["id"], e)
        log.info("%s: no acknowledgement for %s", rec["id"], ", ".join(sorted(refs)))

    def delivery_worker(self):
        while not self.stop.is_set():
            try:
                rid, ref = self.deliveries.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.deliver_one(rid, ref)
            except Exception as e:
                log.exception("delivery %s %s crashed", rid, ref)
                store.update(rid, lambda r: self._after(r, ref, router.Result("retry", f"{type(e).__name__}: {e}")))
            finally:
                self.in_flight.discard((rid, ref))

    def deliver_one(self, rid, ref):
        rec = store.load(rid)
        item = next(i for i in rec["outbox"] if i["ref"] == ref)
        if item.get("delivered_at"):
            return
        to = item.get("to")
        role = {"reviewer": store.reviewer, "deputy": store.deputy}[to]() if to else None
        reach = ((role or {}).get("reach") if to else rec.get("reach")) or {}
        start = time.monotonic()
        store.update(rid, lambda r: self._sending(r, ref))
        saved = time.monotonic()
        res = router.deliver(reach, item["text"], ref, request_id=rid, resume=False, confirm=False)
        log.info("%s %s%s: %s (%s); saving took %.1fs, typing %.1fs", rid, ref, f" ({to})" if to else "", res.status,
                 res.reason, saved - start, time.monotonic() - saved)
        if res.status == "waiting" and not to and self.follow(rec, ref):
            return
        sid = res.reach.get("session_id")
        if to:
            res.reach, res.moves = {}, []   # where the reviewer or the deputy is moves nothing of the asker's
        self.record(rid, ref, res)
        if res.status == "sent":
            self.awaiting[(rid, ref)] = (reach.get("runtime"), sid, time.monotonic())
        if res.status in ("sent", "delivered"):
            # The approver's next reply on this card is typed right behind it, not on the next pass, so the agent
            # takes both in at once. A decision is left to the pass, which spaces them out.
            nxt = core.next_item(store.load(rid), to)
            if nxt and nxt["kind"] != "decision" and core.due(nxt):
                self.queue([[rid, nxt["ref"]]])

    def follow(self, rec, ref):
        """The session that asked isn't running. If the same agent has been seen in a newer session (it was started
        again, or its pod moved to another machine), every open card of the old session goes to that one, and this
        answer is typed in there on the next pass of the machine it is on. True when this card moved; False leaves
        the answer waiting for an agent that really is stopped."""
        records = store.all_records()
        new = core.successor(rec, records)
        if not new:
            return False
        old = rec["reach"]["session_id"]

        def move(r):
            if not reach_of.take_over(r, old, new):
                return False
            for i in r.get("outbox", []):
                if i["ref"] == ref:
                    i.pop("sent_at", None)   # nothing was typed
            return True
        moved = [r["id"] for r in records if (r["id"] == rec["id"] or r["status"] in store.OPEN)
                 and (r.get("reach") or {}).get("session_id") == old and store.update(r["id"], move)]
        log.info("%s: %s isn't running as session %s; it runs as %s on %s now, so its cards go there: %s", rec["id"],
                 rec["agent"], old, new["session_id"], new["machine"], ", ".join(moved) or "none")
        return rec["id"] in moved

    def confirm_typed(self):
        """A message typed into a session is delivered once it shows up in the session's transcript, which is when
        the agent takes it in. One that doesn't within TYPED_SECONDS is tried again later, as a failed one is."""
        for key, (runtime, sid, at) in list(self.awaiting.items()):
            if transcripts.typed(transcripts.transcript(runtime, sid), key[1]):
                res = router.Result("delivered", "seen in the transcript")
            elif time.monotonic() - at >= core.TYPED_SECONDS:
                res = router.Result("retry", "sent, but not seen in the transcript yet")
            else:
                continue
            self.awaiting.pop(key, None)
            log.info("%s %s: %s (%s)", *key, res.status, res.reason)
            self.record(*key, res)

    def record(self, rid, ref, res):
        """Save what happened to one message, then 📨 it if it landed, or post the note a failure calls for."""
        note = store.update(rid, lambda r: self._after(r, ref, res))
        rec = store.load(rid)
        item = next(i for i in rec["outbox"] if i["ref"] == ref)
        if res.status == "delivered" and item.get("react_ts"):
            try:
                slackapi.react(item["react_ts"], "incoming_envelope", **config.channel_args(rec))
            except Exception as e:
                log.warning("reaction failed: %s", e)
        if note and not item.get("to"):   # the approver's thread isn't told about the reviewer's or deputy's deliveries
            try:
                slackapi.note(rec["thread_ts"], note, **config.channel_args(rec))
            except Exception as e:
                log.warning("failure note failed: %s", e)

    @staticmethod
    def _sending(r, ref):
        next(i for i in r["outbox"] if i["ref"] == ref)["sent_at"] = store.iso()

    @staticmethod
    def _after(r, ref, res):
        item = next(i for i in r["outbox"] if i["ref"] == ref)
        for old, new in res.moves:
            if [old, new] not in r.setdefault("moves", []):
                r["moves"].append([old, new])
                store.event(r, "session_moved", old=old, new=new)
        if res.reach:
            reach = r.get("reach") or {}
            if res.reach.get("session_id") and res.reach["session_id"] != reach.get("session_id"):
                reach.setdefault("earlier_ids", [])
                if reach.get("session_id") and reach["session_id"] not in reach["earlier_ids"]:
                    reach["earlier_ids"].append(reach["session_id"])
            reach.update(res.reach)
            r["reach"] = reach
        if res.status == "sent":
            item["typed_at"] = store.iso()
            return None
        if res.status == "delivered":
            item["delivered_at"] = store.iso()
            store.event(r, "delivered", ref=ref)
            return None
        item.pop("typed_at", None)
        if res.status in ("waiting", "inbox"):
            item.pop("sent_at", None)   # nothing was typed
        if res.status == "waiting":
            # The approver decides when an agent runs, so a stopped one isn't started for an answer, and isn't
            # typed into when it's back either: it finds the answer itself, with `approvals inbox`.
            already = any(i.get("waiting_for_agent") and not i.get("delivered_at") and i.get("to") == item.get("to")
                          for i in r["outbox"])
            item.update(inbox_only=True, waiting_for_agent=True, next_try=store.iso(store.now() + timedelta(days=3650)))
            store.event(r, "waiting_for_agent", ref=ref)
            if already:
                return None
            return (f"💤 {r['agent']} isn't running, so your answer is waiting for it. Nothing will start it; "
                    f"it will see the answer when it's back.")
        if res.status == "inbox":
            item["delivered_at"] = None
            item["next_try"] = store.iso(store.now() + timedelta(days=3650))
            item["inbox_only"] = True
            store.event(r, "inbox_only", ref=ref, reason=res.reason)
            if res.elsewhere:
                item["stray"] = True
                return (f"⚠️ Couldn't deliver this to {r['agent']} directly ({res.reason}). Whichever machine has the "
                        f"session will deliver it; until then it's waiting in its `approvals inbox`.")
            return f"⚠️ Couldn't deliver this to {r['agent']} directly ({res.reason}). It's waiting in its `approvals inbox`."
        item["attempts"] += 1
        mins = 5 if item["attempts"] < FAILURE_NOTE_AFTER else 60
        item["next_try"] = store.iso(store.now() + timedelta(minutes=mins))
        item["last_error"] = res.reason
        if item["attempts"] >= FAILURE_NOTE_AFTER and not item.get("noted_failure"):
            item["noted_failure"] = True
            return f"⚠️ Couldn't reach {r['agent']} yet: {res.reason}. Trying again hourly; it's also in its `approvals inbox`."
        return None

    # ----- the answers the record took, when the record keeps the cards -----

    def carry(self, a):
        """One answer the record took, carried out by the press code its button runs here, in a write that names it.
        None once carried out, or the words of this hub's own refusal, which are marked on the answer and, for a press
        in Slack, shown to the presser where the card is."""
        rid, n, what, user, words = a["card"], a["version"], a["what"], a.get("slack_user"), a.get("words")
        rec = store.load(rid)
        via = a["via"]
        acting = store.ACTOR.set(user if via in ("slack", "dashboard") else None)
        where = core.VIA.set("dashboard" if via == "dashboard" else "Slack")
        answer = store.ANSWER.set(a["id"])
        try:
            if via in ("slack", "dashboard"):
                if what in ("change", "edit", "items") or what.endswith(":change"):
                    r, follow_up = self.submit(rec, n, what, words or "", user, f"answer:{a['id']}", a.get("items"))
                else:
                    r, follow_up = self.press(rec, n, what, user, words=words, until=a.get("until_at"))
            elif via == "deputy":
                d = store.deputy()
                detail = a["detail"]
                r = store.update(rid, lambda r: core.call(r, what.split(":")[1], words, d["reach"]["agent"],
                                                          bool(detail["live"]), detail.get("also"),
                                                          urgent=bool(detail.get("urgent")))) if d else \
                    "Only the deputy's own session can make a call; nobody is the deputy now."
                follow_up = lambda: (self.try_refresh(rid), self.refresh_list())
            elif what == "expire":
                def expire_due(r):
                    if r["status"] not in ("pending", "question") or store.parse(r["deadline"]) > store.now():
                        return "already"
                    core.expire(r)
                    return "ok"
                r = store.update(rid, expire_due)
                follow_up = lambda: (self.try_refresh(rid), self.refresh_list())
            elif what == "return":
                r = "ok" if self.return_hand(rec) else "gone"
                follow_up = lambda: (self.try_refresh(rid), self.refresh_list())
            else:
                detail = a["detail"]
                verdict = "approved" if detail.get("choice") else detail["verdict"]
                r = store.update(rid, lambda r: core.record_answer(r, verdict, words, detail["where"],
                                                                   detail.get("part"), detail.get("choice")))
                follow_up = lambda: (self.try_refresh(rid), self.refresh_list())
        finally:
            store.ANSWER.reset(answer)
            core.VIA.reset(where)
            store.ACTOR.reset(acting)
        log.info("answer %s on %s v%s by %s %s -> %s", a["id"], rid, n, via, what, r)
        if r == "ok":
            if follow_up:
                self.later(follow_up)
            return None
        refused = self.REFUSED.get(r, r)
        store.record().refuse(a["id"], refused)
        if via == "slack":
            # Where the door says he pressed it, else the card's own thread.
            detail = a.get("detail") or {}
            channel = detail.get("channel") or config.card_target(rec)["channel"]
            try:
                slackapi.ephemeral(user, refused, detail.get("thread_ts") or rec.get("thread_ts"), channel_id=channel)
            except Exception as e:
                log.warning("telling %s that answer %s was refused failed: %s", user, a["id"], e)
        return refused

    def carry_waiting(self):
        """Every answer the record took and nothing has carried out, oldest first. A pass that can't reach the record
        stops where it is, so the next carries on from the same answer."""
        with self.carrying:
            for a in store.record().answers():
                try:
                    outcome = self.carry(a)
                except store.Refused as refused:   # the record refused the write that would carry it out
                    outcome = str(refused)
                    log.warning("the record refused carrying out answer %s on %s: %s", a["id"], a["card"], refused)
                    store.record().refuse(a["id"], outcome)
                except FileNotFoundError:   # the card was taken out of the store since
                    outcome = self.REFUSED["gone"]
                    store.record().refuse(a["id"], outcome)
                self.outcomes[a["id"]] = outcome
                while len(self.outcomes) > 1000:
                    self.outcomes.popitem(last=False)

    def give(self, answer):
        """An answer given on this hub or sent to it (the deputy's call, one an agent records, its own timers): taken by
        the record, then carried out. 'ok', or Refused with the words."""
        taken = store.record().take(answer)
        self.carry_waiting()
        refused = self.outcomes.get(taken["answer"])
        if refused:
            raise store.Refused(refused)
        return "ok"

    def give_own(self, rec, what, key):
        """The hub's own answer on its timer: a deadline passed, or a hand-off not answered in time."""
        try:
            self.give({"card": rec["id"], "version": len(rec["versions"]), "what": what, "via": "timer", "key": key})
        except store.Refused as refused:
            log.info("%s: the record took no %s: %s", rec["id"], what, refused)

    def carrier(self):
        """Carries out the waiting answers each time the record says a card changed, and at least every sweep, so an
        answer whose carrying out couldn't reach the record is carried out once it can."""
        while not self.stop.is_set():
            self.answers_waiting.wait(self.s["sweep_seconds"])
            self.answers_waiting.clear()
            try:
                self.carry_waiting()
            except store.StoreError as e:
                log.warning("carrying out the record's answers: %s", e)
            except Exception:
                log.exception("carrying out the record's answers failed")

    def hear(self):
        """The record's stream of changes: a card changed (an answer may be waiting), a card's thread in Slack changed
        (it is read now), or a change to no card (who may answer or the deputy may have changed). A stream that is cut
        comes back from the last change it had."""
        after = None
        while not self.stop.is_set():
            try:
                for event_id, change in store.record().changes(after):
                    after = event_id
                    if change is None:   # the stream's start: what was taken while nobody listened waits too
                        self.lists_changed()
                        self.answers_waiting.set()
                    elif change["thing"] is None:
                        self.lists_changed()
                    elif change["kind"] == "approval":
                        self.answers_waiting.set()
                    elif change["kind"] == "thread":
                        self.thread_changed(change["thing"])
                    if self.stop.is_set():
                        return
            except store.StoreError as e:
                log.warning("%s; hearing it again", e)
            except Exception:
                log.exception("hearing the record's changes failed")
            self.stop.wait(1)

    def thread_changed(self, thing):
        """A Slack thread the record copies changed: a card's own is read now rather than in its turn. The record names
        a thread "slack:<team>:<channel>:<time>", its Slack address."""
        address = (store.record().thing(thing).get("thing") or {}).get("read_from") or ""
        parts = address.split(":")
        if len(parts) == 4 and parts[0] == "slack":
            with self.poke_lock:
                self.poke.add((parts[2], parts[3]))

    def lists_changed(self):
        """Who may answer is read again; when auto-approval was switched, the morning summary shows it."""
        config.forget_lists()
        d = store.deputy()
        live = bool(d and d.get("live"))
        was, self.deputy_live = self.deputy_live, live
        if was is not None and was != live:
            log.info("auto-approval %s in the record", "on" if live else "paused")
            self.redraw_summary(live, store.parse(d["live_since"]) if live and d.get("live_since") else store.now())

    def redraw_summary(self, live, at):
        """The deputy's summary under the newest morning list, with the other button and who switched it when."""
        st = digest.load_state()
        if not st:
            return
        try:
            summary = next((m for m in slackapi.history(st["ts"])
                            if any(b.get("block_id") == "deputy" for b in m.get("blocks") or [])), None)
            if summary:
                slackapi.update(summary["ts"], digest.switched(summary["blocks"], live, at), summary.get("text") or "")
        except Exception as e:
            log.warning("redrawing the deputy's summary failed: %s", e)

    def keep_views(self):
        """Every card whose versions aren't yet kept with their pop-ups gets them, once: the write draws them."""
        store.all_records()
        lacking = store.record().without_views()
        for rid in lacking:
            try:
                store.update(rid, lambda r: None)
            except store.StoreError as e:
                log.warning("keeping %s's pop-ups failed: %s", rid, e)
        if lacking:
            log.info("kept the pop-ups of %d cards' versions in the record", len(lacking))

    # ----- run -----

    def alarm(self):
        """The hub, every 30 seconds: an answer this tool has failed to deliver is said in the alarm channel and to the
        agent that owns the tool. From November 12 to 15, 2026 one machine's deliveries all failed; the card threads were
        told "delivery crashed; see the log", which reached the approver and nobody who could fix it."""
        alarm = self.s.get("alarm")
        if not alarm:
            return
        records = store.all_records()
        now = store.now()
        for rec, item, reason in core.overdue(records, now):
            if rec.get("test") and not config.is_test_run():
                continue
            rid, ref = rec["id"], item["ref"]

            def mark(r):
                i = next(i for i in r["outbox"] if i["ref"] == ref)
                if i.get("alarmed_at") or i.get("delivered_at"):
                    return None
                i["alarmed_at"] = store.iso(now)
                return r, i

            marked = store.update(rid, mark)   # saved before anything is sent, so a crash never repeats an alarm
            if not marked:
                continue
            r, i = marked
            age = int((now - store.parse(i["created"])).total_seconds())
            machine = core.machine_of(r)
            name = i.get("answerer") or core.who(r)
            try:
                line = (f"🚨 {name}'s answer on {rid} ({core.one_line(r['title'])}) hasn't reached "
                        f"{core.one_line(r['agent'])} after {age} seconds: {core.one_line(reason)}. "
                        f"{slackapi.message_url(r['thread_ts'], **config.channel_args(r))}")
                slackapi.post([], line, channel_id=alarm["channel"])
            except Exception:
                log.exception("alarm post for %s %s failed", rid, ref)
            paragraph = (f"This is an alarm from the approvals service on the hub, not a person. "
                         f"{name}'s answer on {rid} ({core.one_line(r['title'])}) has not reached "
                         f"{core.one_line(r['agent'])} on {machine}: {core.one_line(reason)}. "
                         f"The answer is safe on the hub and will be typed in as soon as delivery works, "
                         f"or handed over when the agent runs `approvals inbox`. "
                         f"Look at {machine}'s ~/.approvals/logs/listener.log.")
            try:
                cmd = [os.path.expanduser(alarm["wake"][0]), *alarm["wake"][1:]]
                subprocess.run(cmd, input=paragraph, text=True, timeout=60, check=True)
            except Exception:
                log.exception("alarm wake for %s %s failed", rid, ref)

    def loop(self, every, fn):
        while not self.stop.is_set():
            try:
                fn()
            except store.StoreError as e:
                log.warning("%s: %s", fn.__name__, e)
            except Exception:
                log.exception("%s failed", fn.__name__)
            self.stop.wait(every)

    def quiet(self, client, now=None):
        """Seconds since the last pong, or since this listener first saw the current session."""
        now = time.time() if now is None else now
        cur = client.current_session
        seen, since = getattr(self, "_conn", (None, now))
        if cur is not seen:
            since = now
        self._conn = (cur, since)
        return now - max(since, getattr(cur, "last_ping_pong_time", None) or 0)

    def unheard(self, client, now=None):
        """Seconds the current connection has gone without Slack's hello, or 0 once it has said it.

        Slack says hello on every connection it will send events on. One it never greets is open, answers every
        ping, and carries nothing: on November 14, 2026 another app of ours sat on one for eight hours and heard
        no message. A message can be read back later; a button press on a card cannot."""
        now = time.time() if now is None else now
        cur, since = getattr(self, "_conn", (None, now))   # kept by quiet(), which every pass calls first
        return 0 if getattr(self, "_hello", None) is cur else now - since

    def hub_threads(self):
        return [
            threading.Thread(target=self.loop, args=(5, self.poll_once), daemon=True),
            threading.Thread(target=self.loop, args=(self.s["sweep_seconds"], self.sweep), daemon=True),
            threading.Thread(target=self.loop, args=(5, self.deliver_due), daemon=True),
            threading.Thread(target=self.loop, args=(30, self.alarm), daemon=True),
            threading.Thread(target=self.loop, args=(5, self.lists), daemon=True),   # answers an ask within seconds
            threading.Thread(target=self.delivery_worker, daemon=True),
            threading.Thread(target=self.loop, args=(3, self.confirm_typed), daemon=True),
            threading.Thread(target=self.step_writer, daemon=True),
        ]

    def run_from_record(self):
        """The hub of a workspace whose cards the record keeps: no Slack connection; every answer from the record."""
        if config.file_settings().get("hub"):
            from . import hub
            hub.serve(carrier=self.give)
        log.info("hearing answers from the record; not connected to Slack")
        threads = [threading.Thread(target=self.hear, daemon=True), threading.Thread(target=self.carrier, daemon=True),
                   threading.Thread(target=self.keep_views, daemon=True), *self.hub_threads()]
        for t in threads:
            t.start()
        try:
            while not self.stop.is_set():
                time.sleep(1)
        finally:
            self.stop.set()

    def run(self):
        if not config.is_hub():
            return self.run_delivery()
        if store.record():
            return self.run_from_record()
        if config.settings().get("hub"):
            from . import hub
            hub.serve()
        sm = SlackConnection(app_token=config.app_token(), web_client=slackapi.client())
        sm.socket_mode_request_listeners.append(self.on_socket)
        sm.on_message_listeners.append(
            lambda raw: '"type":"hello"' in raw[:40] and setattr(self, "_hello", sm.current_session))
        sm.connect()
        log.info("connected to Slack; watching %s", ", ".join(sorted({t["channel"] for t in config.targets().values()})))
        threads = self.hub_threads()
        for t in threads:
            t.start()
        try:
            while not self.stop.is_set():
                time.sleep(1)
                quiet = self.quiet(sm)
                if quiet <= QUIET and self.unheard(sm) <= HELLO:
                    continue
                # Slack's client reconnects a closed connection itself. It never doubts one that has had no pong
                # yet, or one Slack never greeted, so those are replaced here, the client's own way: a new
                # address, under its lock.
                if quiet > QUIET:
                    log.warning("no answer from Slack for %s seconds; reconnecting", int(quiet))
                else:
                    log.warning("no hello from Slack on this connection in %s seconds, so it carries nothing; reconnecting", HELLO)
                try:
                    sm.connect_to_new_endpoint(force=True)
                except Exception:
                    log.exception("reconnecting to Slack failed")
        finally:
            self.stop.set()
            sm.close()

    def run_delivery(self):
        log.info("delivering answers to the sessions on %s; the records and Slack connection are on %s", self.me, config.hub())
        threads = [threading.Thread(target=self.loop, args=(FETCH_SECONDS, self.fetch), daemon=True),
                   threading.Thread(target=self.delivery_worker, daemon=True),
                   threading.Thread(target=self.loop, args=(3, self.confirm_typed), daemon=True),
                   threading.Thread(target=self.step_writer, daemon=True)]
        for t in threads:
            t.start()
        try:
            while not self.stop.is_set():
                time.sleep(1)
        finally:
            self.stop.set()


def main():
    config.ensure_dirs()
    handler = logging.handlers.RotatingFileHandler(config.LOGS / "listener.log", maxBytes=5_000_000, backupCount=3)
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()],
                        format="%(asctime)s %(levelname)s %(message)s")
    Listener().run()
