"""`approvals listen`: Slack buttons over Socket Mode, thread replies, deadlines, and delivery to agents.

On the workspace's hub it does all of that, delivers to the sessions on its own machine, and serves the records
to the other machines. On every other machine it only delivers to the sessions there.
"""
import logging
import logging.handlers
import queue
import ssl
import threading
import time
from datetime import datetime, timedelta

from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse

from . import cards, config, core, digest, router, slackapi, store, transcripts

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
FAILURE_NOTE_AFTER = 3
ACK_MINUTES = 10         # a delivered answer the agent hasn't acknowledged by then gets a warning in the thread
FETCH_SECONDS = 5        # how often a machine other than the hub asks it for answers to deliver
WAITING = "hourglass_flowing_sand"   # ⏳ on a card while its decision waits for the agent to catch up
STRAY_SECONDS = 60       # how often a machine looks for answers whose session turned up on it


def is_approver(msg, rec):
    """The approver's own replies count. In tests, a stand-in's "[test reply]" posts count on TEST cards only."""
    s = config.settings()
    if msg.get("user") == s["approver"] and not msg.get("bot_id"):
        return True
    tester = config.test_approver()
    return (bool(tester) and bool(rec.get("test")) and msg.get("user") == tester
            and (msg.get("text") or "").startswith("[test reply]"))


def is_ask(msg):
    """The approver posting in the channel itself, not in a card's thread, asks for a fresh list. The channel is
    only cards, so any such post means it; in a test run, the stand-in's "[test reply]" posts count."""
    return (msg.get("thread_ts") in (None, msg.get("ts")) and not msg.get("subtype")
            and is_approver(msg, {"test": config.is_test_run()}))


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
        self.next_poll = {}               # thread_ts -> monotonic time it is next due
        self.stop = threading.Event()
        self.me = config.machine()
        self.next_stray_look = 0.0

    # ----- buttons -----

    def on_socket(self, client, req):
        kind = (req.payload or {}).get("type")
        if req.type == "interactive" and kind in ("block_actions", "view_submission"):
            follow_up, answer = None, None
            try:
                if kind == "block_actions":
                    follow_up = self.handle_click(req.payload)
                else:
                    answer, follow_up = self.handle_submission(req.payload)
            except Exception:
                log.exception("%s failed", kind)
            client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id, payload=answer))
            if follow_up:
                threading.Thread(target=follow_up, daemon=True).start()
            return
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type == "events_api":
            ev = (req.payload or {}).get("event") or {}
            # Only the approver's own posts: the agents' acknowledgements and our notes would take his turn.
            who = ev.get("user")
            if (ev.get("type") == "message" and ev.get("channel") == self.s["channel"] and ev.get("thread_ts")
                    and who and who in (self.s["approver"], config.test_approver())):
                with self.poke_lock:
                    self.poke.add(ev["thread_ts"])

    REFUSED = {
        "stale": "That version was replaced; use the newest card's buttons.",
        "already": "This one is already decided.",
        "withdrawn": "The agent withdrew this one, so there's nothing to decide.",
        "gone": "That button doesn't match the card any more; use the newest card's buttons.",
    }

    def handle_click(self, payload):
        """Save the decision and the agent's message before acking; card and notes follow. A button that opens a
        pop-up opens it right after the ack, inside the 3 seconds Slack allows."""
        user = (payload.get("user") or {}).get("id")
        action = (payload.get("actions") or [{}])[0]
        if (payload.get("view") or {}).get("callback_id") == "approvals" and action.get("action_id") == "items":
            return lambda: self.recount_items(payload)
        try:
            rid, n, what = action.get("value", "").split("|", 2)
            n = int(n)
        except ValueError:
            return None   # a link button: Slack opens it
        thread_ts = ((payload.get("container") or {}).get("thread_ts")
                     or (payload.get("message") or {}).get("thread_ts"))
        trigger = payload.get("trigger_id")
        if what == "see" or what.startswith(("read:", "pic:")):
            return lambda: self.open_view(rid, n, what, trigger, user, thread_ts)
        if user != self.s["approver"]:
            return lambda: slackapi.ephemeral(user, f"Only {self.s['approver_name']} can decide approvals.", thread_ts)
        if what in ("change", "edit", "items") or what.endswith(":change"):
            return lambda: self.open_view(rid, n, what, trigger, user, thread_ts)
        r = store.update(rid, lambda rec: core.press(rec, n, what, by=user))
        log.info("click %s v%s %s -> %s", rid, n, what, r)
        if r in self.REFUSED:
            return lambda: slackapi.ephemeral(user, self.REFUSED[r], thread_ts)
        return lambda: self.try_refresh(rid)

    def open_view(self, rid, n, what, trigger, user, thread_ts):
        """Open the pop-up a button asks for, unless the card moved on since it was drawn."""
        rec = store.load(rid)
        if what != "see" and not what.startswith(("read:", "pic:")):
            bad = core._check(rec, n)
            j = int(what[1:].split(":")[0]) if what.startswith("p") else None
            if not bad and j and cards.part_id(rec["versions"][-1]["card"]["parts"][j - 1], j) in (rec.get("answers") or {}):
                bad = "already"
            if not bad and not j and (rec.get("decision") or {}).get("version") == n:
                bad = "already"
            if bad:
                return slackapi.ephemeral(user, self.REFUSED[bad], thread_ts)
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
        ticked = self._ticked(view)
        slackapi.views_update(view["id"], cards.items_view(store.load(rid), int(n), ticked), view.get("hash"))

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
        if user != self.s["approver"]:
            return {"response_action": "errors", "errors": {first: f"Only {self.s['approver_name']} can decide approvals."}}, None
        words = ((values.get("words") or {}).get("words") or {}).get("value") or ""
        if what == "items":
            ticked = self._ticked(view)
            if not ticked:
                return {"response_action": "errors", "errors": {first: "Tick at least one, or press No on the card."}}, None
            r = store.update(rid, lambda rec: core.decide(rec, n, "approved", by=user, where="pop-up", items=ticked))
        elif what == "edit":
            if not words.strip():
                return {"response_action": "errors", "errors": {"words": "The words can't be empty."}}, None
            r = store.update(rid, lambda rec: core.decide(rec, n, "approved", by=user, where="pop-up", edited=words))
        else:
            j = int(what[1:].split(":")[0]) if what.startswith("p") else None
            r = store.update(rid, lambda rec: core.ask_change(rec, n, words, by=user, j=j))
        log.info("pop-up %s v%s %s -> %s", rid, n, what, r)
        if r in self.REFUSED:
            return {"response_action": "errors", "errors": {first: self.REFUSED[r]}}, None
        return None, lambda: self.after_submission(rid, what, words)

    def after_submission(self, rid, what, words):
        """His words from a pop-up go in the thread too, so the thread keeps the whole record."""
        self.try_refresh(rid)
        rec = store.load(rid)
        who = self.s["approver_name"]
        try:
            if what == "edit":
                slackapi.note(rec["thread_ts"], f"✍️ {who} approved it with these words:\n{cards.quoted(words)}")
            elif what.endswith("change"):
                row = f" to part {what[1:].split(':')[0]}" if what.startswith("p") else ""
                slackapi.note(rec["thread_ts"], f"✏️ {who} asked for a change{row}:\n{cards.quoted(words)}")
        except Exception as e:
            log.warning("thread note for %s failed: %s", rid, e)

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
        recs = {r["thread_ts"]: r for r in self.watched()}
        if not recs:
            return
        now = time.monotonic()
        with self.poke_lock:
            self.poke &= set(recs)
            poked = set(self.poke)
        interval = max(15.0, len(recs) * 60.0 / POLL_BUDGET_PER_MIN)
        due = [ts for ts in recs if ts in poked or self.next_poll.get(ts, 0) <= now]
        # A thread Slack says the approver just posted in goes first. With hundreds of cards watched, the scheduled
        # reads are always behind, so a flagged thread sorted by its schedule would never get a turn.
        due.sort(key=lambda ts: (ts not in poked, self.next_poll.get(ts, 0)))
        per_cycle = max(1, POLL_BUDGET_PER_MIN * 5 // 60)   # this loop runs every 5 seconds
        for ts in due[:per_cycle]:
            self.next_poll[ts] = now + interval
            with self.poke_lock:
                self.poke.discard(ts)   # a flag that didn't fit this round keeps its place for the next
            try:
                self.poll_thread(recs[ts])
            except Exception:
                log.exception("polling %s failed", recs[ts]["id"])

    def poll_thread(self, rec):
        last = rec.get("last_seen") or rec["thread_ts"]
        msgs = [m for m in slackapi.replies(rec["thread_ts"], oldest=last) if float(m["ts"]) > float(last)]
        if not msgs:
            return
        mine = [(m["ts"], reply_text(m)) for m in msgs if is_approver(m, rec) and reply_text(m).strip()]
        newest = max(m["ts"] for m in msgs)

        def apply(r):
            if float(newest) <= float(r.get("last_seen") or 0):
                return None
            r["last_seen"] = newest
            kind = None
            for reply in mine:   # each its own message: the approver correcting himself should reach the agent every time
                if float(reply[0]) > float(last):
                    kind = core.take_replies(r, [reply])
            return kind
        kind = store.update(rec["id"], apply)
        if kind:
            log.info("%s: %d repl%s from the approver -> %s", rec["id"], len(mine), "y" if len(mine) == 1 else "ies", kind)
            if store.load(rec["id"]).get("card_dirty"):
                self.try_refresh(rec["id"])

    # ----- the list of what waits on the approver -----

    def lists(self):
        st = digest.load_state()
        asked = bool(st) and any(is_ask(m) for m in slackapi.history(st["ts"]))
        digest.tick(asked=asked)

    # ----- deadlines, card refreshes, deliveries -----

    def sweep(self):
        for rec in store.all_records():
            if not rec.get("thread_ts"):
                continue
            if rec["status"] in ("pending", "question") and datetime.fromisoformat(rec["deadline"]) <= store.now():
                store.update(rec["id"], lambda r: core.expire(r) if r["status"] in ("pending", "question") else None)
                log.info("%s expired", rec["id"])
                rec = store.load(rec["id"])
            if rec.get("card_dirty"):
                self.try_refresh(rec["id"])
            self.warn_unacknowledged(rec)

    def deliver_due(self):
        """On the hub, every few seconds: what's due for this machine's sessions, and ⏳ kept true on the cards."""
        recs = [r for r in store.all_records() if r.get("thread_ts")]
        due, strays = core.to_deliver(recs, self.me)
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
                    slackapi.react(ts, WAITING)
                for ts in set(was) - set(on):
                    slackapi.unreact(ts, WAITING)
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
        due, strays = store.hub_client().due(self.me)
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
        what = "your answer" if len(late) == 1 else f"your last {len(late)} answers"
        try:
            slackapi.note(rec["thread_ts"], f"⚠️ {rec['agent']} received {what} at {cards.when(first)} (📨) but hasn't acknowledged "
                                            f"in {ACK_MINUTES} minutes, so nothing confirms it was understood or acted on.")
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
            except Exception:
                log.exception("delivery %s %s crashed", rid, ref)
                store.update(rid, lambda r: self._after(r, ref, router.Result("retry", "delivery crashed; see the log")))
            finally:
                self.in_flight.discard((rid, ref))

    def deliver_one(self, rid, ref):
        rec = store.load(rid)
        item = next(i for i in rec["outbox"] if i["ref"] == ref)
        if item.get("delivered_at"):
            return
        store.update(rid, lambda r: self._sending(r, ref))
        res = router.deliver(rec.get("reach") or {}, item["text"], ref, request_id=rid, resume=False)
        log.info("%s %s: %s (%s)", rid, ref, res.status, res.reason)
        note = store.update(rid, lambda r: self._after(r, ref, res))
        if res.status == "delivered":
            # The approver's next reply on this card goes in now rather than on the next pass, so corrections typed
            # in a row land one right after another. A decision is left to the pass, which spaces them out.
            nxt = core.next_item(store.load(rid))
            if nxt and nxt["kind"] != "decision" and core.due(nxt):
                self.queue([[rid, nxt["ref"]]])
        if res.status == "delivered" and item.get("react_ts"):
            try:
                slackapi.react(item["react_ts"], "incoming_envelope")
            except Exception as e:
                log.warning("reaction failed: %s", e)
        if note:
            try:
                slackapi.note(rec["thread_ts"], note)
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
        if res.status == "delivered":
            item["delivered_at"] = store.iso()
            store.event(r, "delivered", ref=ref)
            return None
        if res.status in ("waiting", "inbox"):
            item.pop("sent_at", None)   # nothing was typed
        if res.status == "waiting":
            # The approver decides when an agent runs, so a stopped one isn't started for an answer, and isn't
            # typed into when it's back either: it finds the answer itself, with `approvals inbox`.
            already = any(i.get("waiting_for_agent") and not i.get("delivered_at") for i in r["outbox"])
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

    # ----- run -----

    def loop(self, every, fn):
        while not self.stop.is_set():
            try:
                fn()
            except store.StoreError as e:
                log.warning("%s: %s", fn.__name__, e)
            except Exception:
                log.exception("%s failed", fn.__name__)
            self.stop.wait(every)

    def run(self):
        if not config.is_hub():
            return self.run_delivery()
        if config.settings().get("hub"):
            from . import hub
            hub.serve()
        sm = SlackConnection(app_token=config.app_token(), web_client=slackapi.client())
        sm.socket_mode_request_listeners.append(self.on_socket)
        sm.connect()
        log.info("connected to Slack; watching %s", self.s["channel"])
        threads = [
            threading.Thread(target=self.loop, args=(5, self.poll_once), daemon=True),
            threading.Thread(target=self.loop, args=(self.s["sweep_seconds"], self.sweep), daemon=True),
            threading.Thread(target=self.loop, args=(5, self.deliver_due), daemon=True),
            threading.Thread(target=self.loop, args=(5, self.lists), daemon=True),   # answers an ask within seconds
            threading.Thread(target=self.delivery_worker, daemon=True),
        ]
        for t in threads:
            t.start()
        try:
            while not self.stop.is_set():
                time.sleep(1)
        finally:
            self.stop.set()
            sm.close()

    def run_delivery(self):
        log.info("delivering answers to the sessions on %s; the records and Slack connection are on %s", self.me, config.hub())
        threads = [threading.Thread(target=self.loop, args=(FETCH_SECONDS, self.fetch), daemon=True),
                   threading.Thread(target=self.delivery_worker, daemon=True)]
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
