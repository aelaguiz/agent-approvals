"""`approvals listen`: Slack buttons over Socket Mode, thread replies, deadlines, and delivery to agents."""
import logging
import logging.handlers
import queue
import threading
import time
from datetime import datetime, timedelta

from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse

from . import cards, config, core, router, slackapi, store

log = logging.getLogger("approvals")

WATCH_DAYS = 14          # threads stay watched this long after their last activity
POLL_BUDGET_PER_MIN = 40
FAILURE_NOTE_AFTER = 3
ACK_MINUTES = 10         # a delivered answer the agent hasn't acknowledged by then gets a warning in the thread


def is_approver(msg, rec):
    """The approver's own replies count. In tests, a stand-in's "[test reply]" posts count on TEST cards only."""
    s = config.settings()
    if msg.get("user") == s["approver"] and not msg.get("bot_id"):
        return True
    tester = config.test_approver()
    return (bool(tester) and bool(rec.get("test")) and msg.get("user") == tester
            and (msg.get("text") or "").startswith("[test reply]"))


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

    # ----- buttons -----

    def on_socket(self, client, req):
        if req.type == "interactive" and (req.payload or {}).get("type") == "block_actions":
            follow_up = None
            try:
                follow_up = self.handle_click(req.payload)
            except Exception:
                log.exception("button press failed")
            client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            if follow_up:
                threading.Thread(target=follow_up, daemon=True).start()
            return
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        if req.type == "events_api":
            ev = (req.payload or {}).get("event") or {}
            if ev.get("type") == "message" and ev.get("channel") == self.s["channel"] and ev.get("thread_ts"):
                with self.poke_lock:
                    self.poke.add(ev["thread_ts"])

    def handle_click(self, payload):
        """Save the decision and the agent's message before acking; card and notes follow."""
        user = (payload.get("user") or {}).get("id")
        action = (payload.get("actions") or [{}])[0]
        try:
            rid, n, what = action.get("value", "").split("|")
            n = int(n)
        except ValueError:
            return None
        thread_ts = ((payload.get("container") or {}).get("thread_ts")
                     or (payload.get("message") or {}).get("thread_ts"))
        if user != self.s["approver"]:
            return lambda: slackapi.ephemeral(user, f"Only {self.s['approver_name']} can decide approvals.", thread_ts)
        verdict = "approved" if what == "approve" else "declined"
        r = store.update(rid, lambda rec: core.decide(rec, n, verdict, by=user))
        log.info("click %s v%s %s -> %s", rid, n, verdict, r)
        if r == "stale":
            return lambda: slackapi.ephemeral(user, "That version was replaced; use the newest card's buttons.", thread_ts)
        if r == "already":
            return lambda: slackapi.ephemeral(user, "This one is already decided.", thread_ts)
        return lambda: self.try_refresh(rid)

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
            if not rec.get("thread_ts") or rec["status"] == "declined":
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
            poked, self.poke = self.poke & set(recs), set()
        interval = max(15.0, len(recs) * 60.0 / POLL_BUDGET_PER_MIN)
        due = [ts for ts in recs if ts in poked or self.next_poll.get(ts, 0) <= now]
        due.sort(key=lambda ts: self.next_poll.get(ts, 0))
        per_cycle = max(1, POLL_BUDGET_PER_MIN * 5 // 60)   # this loop runs every 5 seconds
        for ts in due[:per_cycle]:
            self.next_poll[ts] = now + interval
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
            if mine:
                return core.take_replies(r, [x for x in mine if float(x[0]) > float(last)])
            return None
        kind = store.update(rec["id"], apply)
        if kind:
            log.info("%s: %d repl%s from the approver -> %s", rec["id"], len(mine), "y" if len(mine) == 1 else "ies", kind)
            if store.load(rec["id"]).get("card_dirty"):
                self.try_refresh(rec["id"])

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
            for item in rec.get("outbox", []):
                if item.get("delivered_at") or item.get("inbox_only"):
                    continue
                key = (rec["id"], item["ref"])
                if core.due(item) and key not in self.in_flight:
                    self.in_flight.add(key)
                    self.deliveries.put(key)
                break   # a request's later messages wait until this one lands, so they arrive in order

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
        waited = (store.now() - datetime.fromisoformat(item["created"])).total_seconds()
        res = router.deliver(rec.get("reach") or {}, item["text"], ref, request_id=rid, waited_seconds=waited)
        log.info("%s %s: %s (%s)", rid, ref, res.status, res.reason)
        note = store.update(rid, lambda r: self._after(r, ref, res))
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
        if res.status == "wait":
            item["next_try"] = store.iso(store.now() + timedelta(seconds=60))
            return None
        if res.status == "inbox":
            item["delivered_at"] = None
            item["next_try"] = store.iso(store.now() + timedelta(days=3650))
            item["inbox_only"] = True
            store.event(r, "inbox_only", ref=ref, reason=res.reason)
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
            except Exception:
                log.exception("%s failed", fn.__name__)
            self.stop.wait(every)

    def run(self):
        sm = SocketModeClient(app_token=config.app_token(), web_client=slackapi.client())
        sm.socket_mode_request_listeners.append(self.on_socket)
        sm.connect()
        log.info("connected to Slack; watching %s", self.s["channel"])
        threads = [
            threading.Thread(target=self.loop, args=(5, self.poll_once), daemon=True),
            threading.Thread(target=self.loop, args=(self.s["sweep_seconds"], self.sweep), daemon=True),
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


def main():
    config.ensure_dirs()
    handler = logging.handlers.RotatingFileHandler(config.LOGS / "listener.log", maxBytes=5_000_000, backupCount=3)
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()],
                        format="%(asctime)s %(levelname)s %(message)s")
    Listener().run()
