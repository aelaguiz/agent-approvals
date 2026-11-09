"""Approvals pressed in a row reach an agent one at a time; the approver's replies in a thread never wait, and a
thread he just posted in is read straight away."""
from datetime import timedelta
from types import SimpleNamespace

from approvals import config, core, listener, store
from samples import SAMPLES
from test_cards_store import make_rec


def card(rid, sid, thread):
    rec = make_rec(SAMPLES["money"], id=rid, thread_ts=thread, reach={"runtime": "claude", "session_id": sid},
                   deadline=store.iso(store.now() + timedelta(hours=5)))
    store.save(rec)
    return rec


def press_yes(rid):
    store.update(rid, lambda r: core.decide(r, 1, "approved", by=config.settings()["approver"]))
    return store.load(rid)["outbox"][-1]["ref"]


def going():
    return core.to_deliver(store.all_records(), config.machine())[0]


def mark(rid, ref, **fields):
    def change(r):
        next(i for i in r["outbox"] if i["ref"] == ref).update(fields)
    store.update(rid, change)


def test_approvals_pressed_in_a_row_reach_the_agent_one_at_a_time():
    for rid, thread in (("AP-0001", "1.1"), ("AP-0002", "2.1"), ("AP-0003", "3.1")):
        card(rid, "sid-a", thread)
    first, second, third = press_yes("AP-0001"), press_yes("AP-0002"), press_yes("AP-0003")
    assert going() == [["AP-0001", first]]
    assert core.held(store.all_records()) == {("AP-0002", second), ("AP-0003", third)}

    mark("AP-0001", first, sent_at=store.iso())   # being typed in; offered again until it lands, and the queue drops repeats
    assert going() == [["AP-0001", first]]
    mark("AP-0001", first, delivered_at=store.iso())
    assert going() == []                         # landed, but the agent hasn't reacted yet
    store.update("AP-0001", lambda r: core.acknowledge(r, "Got it: sending it now.", "9.0"))
    assert going() == [["AP-0002", second]]      # the next one, and only that one


def test_an_agent_that_never_acknowledges_gets_the_next_one_after_two_minutes():
    card("AP-0001", "sid-a", "1.1")
    card("AP-0002", "sid-a", "2.1")
    first, second = press_yes("AP-0001"), press_yes("AP-0002")
    long_ago = store.iso(store.now() - timedelta(seconds=core.PACE_SECONDS + 1))
    mark("AP-0001", first, sent_at=long_ago, delivered_at=long_ago)
    assert going() == [["AP-0002", second]]


def test_different_agents_dont_wait_on_each_other():
    card("AP-0001", "sid-a", "1.1")
    card("AP-0002", "sid-b", "2.1")
    first, second = press_yes("AP-0001"), press_yes("AP-0002")
    assert sorted(going()) == [["AP-0001", first], ["AP-0002", second]]


def test_a_thread_reply_goes_straight_through_while_the_agent_is_reacting_to_a_yes():
    card("AP-0001", "sid-a", "1.1")
    card("AP-0002", "sid-a", "2.1")
    first = press_yes("AP-0001")
    mark("AP-0001", first, sent_at=store.iso(), delivered_at=store.iso())
    store.update("AP-0002", lambda r: core.take_replies(r, [("5.0", "wait, use the other link")]))
    reply = store.load("AP-0002")["outbox"][-1]["ref"]
    assert going() == [["AP-0002", reply]]


def test_rows_of_one_card_pressed_in_a_row_each_wait_for_their_own_acknowledgement():
    rec = make_rec(SAMPLES["money"], reach={"runtime": "claude", "session_id": "sid-a"},
                   deadline=store.iso(store.now() + timedelta(hours=5)))
    rec["versions"][0]["card"] = dict(rec["versions"][0]["card"], parts=[{"title": "one"}, {"title": "two"}])
    store.save(rec)
    for j in (1, 2):
        store.update("AP-0001", lambda r, j=j: core.answer_part(r, 1, j, "approved", by="U"))
    first, second = (i["ref"] for i in store.load("AP-0001")["outbox"])
    mark("AP-0001", first, sent_at=store.iso(), delivered_at=store.iso())
    assert core.held(store.all_records()) == {("AP-0001", second)}
    store.update("AP-0001", lambda r: core.acknowledge(r, "Got it: row one.", "9.0"))
    assert [bool(i["acked_at"]) for i in store.load("AP-0001")["outbox"]] == [True, False]
    assert going() == [["AP-0001", second]]


def test_a_waiting_card_shows_an_hourglass_until_its_answer_goes(monkeypatch):
    added, removed = [], []
    monkeypatch.setattr(listener.slackapi, "react", lambda ts, name: added.append((ts, name)))
    monkeypatch.setattr(listener.slackapi, "unreact", lambda ts, name: removed.append((ts, name)))
    card("AP-0001", "sid-a", "1.1")
    card("AP-0002", "sid-a", "2.1")
    first, _ = press_yes("AP-0001"), press_yes("AP-0002")
    L = listener.Listener.__new__(listener.Listener)
    L.in_flight, L.deliveries, L.me, L.next_stray_look = set(), __import__("queue").Queue(), config.machine(), 0.0
    L.deliver_due()
    L.deliver_due()
    assert added == [("2.1", listener.WAITING)] and removed == []
    assert L.deliveries.get_nowait() == ("AP-0001", first) and L.deliveries.empty()
    mark("AP-0001", first, sent_at=store.iso(), delivered_at=store.iso())
    store.update("AP-0001", lambda r: core.acknowledge(r, "On it.", "9.0"))
    L.in_flight.clear()
    L.deliver_due()
    assert removed == [("2.1", listener.WAITING)] and store.load("AP-0002")["waiting_shown"] == []


def test_a_thread_the_approver_just_posted_in_is_read_before_the_overdue_ones(monkeypatch):
    for n in range(300):
        store.save(make_rec(SAMPLES["money"], id=f"AP-{n + 1:04d}", thread_ts=f"{n + 1}.1"))
    read = []
    monkeypatch.setattr(listener.Listener, "poll_thread", lambda self, rec: read.append(rec["thread_ts"]))
    L = listener.Listener()
    L.poll_once()                                  # everything is overdue at the start
    approver = config.settings()["approver"]
    for ts, who in (("250.1", approver), ("251.1", approver), ("252.1", approver), ("253.1", approver), ("254.1", "UBOT")):
        L.on_socket(SimpleNamespace(send_socket_mode_response=lambda r: None),
                    SimpleNamespace(type="events_api", envelope_id="e", payload={"event": {
                        "type": "message", "channel": config.settings()["channel"], "thread_ts": ts, "user": who}}))
    read.clear()
    L.poll_once()
    assert read == ["250.1", "251.1", "252.1"]     # three reads a round, flagged threads first
    read.clear()
    L.poll_once()
    assert read[0] == "253.1" and "254.1" not in read   # the flag that didn't fit keeps its place; the bot's post never flags


def test_a_reply_that_lands_sends_the_next_one_on_the_card_straight_away(monkeypatch):
    monkeypatch.setattr(listener.slackapi, "react", lambda ts, name: None)
    monkeypatch.setattr(listener.router, "deliver", lambda *a, **k: listener.router.Result("delivered"))
    card("AP-0001", "sid-a", "1.1")
    for ts, words in (("5.0", "cap it at $25"), ("6.0", "sorry, $28")):
        store.update("AP-0001", lambda r, ts=ts, words=words: core.take_replies(r, [(ts, words)]))
    first, second = (i["ref"] for i in store.load("AP-0001")["outbox"])
    L = listener.Listener.__new__(listener.Listener)
    L.in_flight, L.deliveries = set(), __import__("queue").Queue()
    L.deliver_one("AP-0001", first)
    assert L.deliveries.get_nowait() == ("AP-0001", second)


def test_a_reply_on_a_card_whose_yes_is_waiting_sends_both_now():
    card("AP-0001", "sid-a", "1.1")
    card("AP-0002", "sid-a", "2.1")
    first, second = press_yes("AP-0001"), press_yes("AP-0002")
    mark("AP-0001", first, sent_at=store.iso(), delivered_at=store.iso())
    assert going() == []                             # the second yes waits for the agent
    store.update("AP-0002", lambda r: core.take_replies(r, [("5.0", "wait, don't send this")]))
    assert going() == [["AP-0002", second]]          # and then the reply, the moment it lands


def replies_on_one_card(*words):
    card("AP-0001", "sid-a", "1.1")
    for n, w in enumerate(words):
        store.update("AP-0001", lambda r, n=n, w=w: core.take_replies(r, [(f"{5 + n}.0", w)]))
    return [i["ref"] for i in store.load("AP-0001")["outbox"]]


def worker(monkeypatch, result):
    typed = []
    monkeypatch.setattr(listener.slackapi, "react", lambda ts, name: typed.append(("📨", ts)))
    monkeypatch.setattr(listener.slackapi, "note", lambda ts, text: None)
    monkeypatch.setattr(listener.router, "deliver", lambda reach, text, ref, **kw: typed.append(ref) or listener.router.Result(
        result, reach={"session_id": "sid-a"}))
    L = listener.Listener.__new__(listener.Listener)
    L.in_flight, L.deliveries, L.awaiting = set(), __import__("queue").Queue(), {}
    return L, typed


def test_a_cards_next_reply_is_typed_right_behind_the_last_before_either_shows_up(monkeypatch):
    """So the agent takes a yes and the "wait, don't send" after it in at the same step."""
    first, second = replies_on_one_card("ship it", "wait, don't send")
    L, typed = worker(monkeypatch, "sent")
    L.deliver_one("AP-0001", first)
    L.deliver_one(*L.deliveries.get_nowait())
    assert typed == [first, second] and set(L.awaiting) == {("AP-0001", first), ("AP-0001", second)}
    assert going() == []                    # neither is typed again while it waits to show up
    assert store.load("AP-0001")["outbox"][0]["delivered_at"] is None


def test_a_typed_message_is_delivered_once_it_shows_up_in_the_transcript(monkeypatch):
    first, = replies_on_one_card("ship it")
    L, typed = worker(monkeypatch, "sent")
    L.deliver_one("AP-0001", first)
    seen = set()
    monkeypatch.setattr(listener.transcripts, "transcript", lambda runtime, sid: f"/t/{sid}")
    monkeypatch.setattr(listener.transcripts, "contains", lambda path, ref: ref in seen)
    L.confirm_typed()
    assert store.load("AP-0001")["outbox"][0]["delivered_at"] is None and L.awaiting
    seen.add(first)
    L.confirm_typed()
    item = store.load("AP-0001")["outbox"][0]
    assert item["delivered_at"] and not L.awaiting and ("📨", "5.0") in typed


def test_a_typed_message_that_never_shows_up_is_tried_again_later_and_the_next_waits(monkeypatch):
    first, second = replies_on_one_card("ship it", "wait, don't send")
    L, typed = worker(monkeypatch, "sent")
    L.deliver_one("AP-0001", first)
    L.deliveries.get_nowait()
    monkeypatch.setattr(listener.transcripts, "contains", lambda path, ref: False)
    L.awaiting[("AP-0001", first)] = ("claude", "sid-a", listener.time.monotonic() - core.TYPED_SECONDS)
    L.confirm_typed()
    item = store.load("AP-0001")["outbox"][0]
    assert item["attempts"] == 1 and not item.get("typed_at") and not L.awaiting
    assert core.next_item(store.load("AP-0001"))["ref"] == first and going() == []   # retried in 5 minutes; the next waits behind it


def test_when_a_message_cant_be_typed_the_next_one_on_the_card_waits(monkeypatch):
    first, second = replies_on_one_card("ship it", "wait, don't send")
    L, typed = worker(monkeypatch, "retry")
    L.deliver_one("AP-0001", first)
    assert typed == [first] and L.deliveries.empty() and going() == []
