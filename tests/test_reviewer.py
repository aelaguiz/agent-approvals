"""A reviewer (the CFO, for money cards) hears about every card of its types and puts its read in the thread; the
asker's side doesn't change, and with no reviewer nothing does."""
import queue
from datetime import timedelta
from types import SimpleNamespace

import pytest

from approvals import cli, config, core, listener, reach, router, slackapi, store
from samples import SAMPLES
from test_cards_store import make_rec

CFO = {"agent": "CFO", "runtime": "claude", "session_id": "sid-cfo", "machine": "testbox"}


def be_reviewer(**kw):
    store.set_reviewer({"types": ["budget", "purchase", "money"], "reach": dict(CFO, **kw), "test": False})


def card(rid, sample="money", test=False):
    rec = make_rec(SAMPLES[sample], id=rid, thread_ts=f"{rid[-1]}.1", test=test,
                   reach={"agent": "Tester", "runtime": "claude", "session_id": "sid-a", "machine": "testbox"},
                   deadline=store.iso(store.now() + timedelta(hours=5)))
    store.save(rec)


def yes(rid):
    store.update(rid, lambda r: core.decide(r, 1, "approved", by=config.settings()["approver"]))


def to(rid):
    return [i.get("to") for i in store.load(rid)["outbox"]]


def test_only_money_cards_reach_the_reviewer_and_with_none_nothing_changes():
    be_reviewer()
    card("AP-0001")
    card("AP-0002", "creative")
    card("AP-0003", test=True)    # a live test's card stays away from the real reviewer
    for rid in ("AP-0001", "AP-0002", "AP-0003"):
        yes(rid)
    assert to("AP-0001") == [None, "reviewer"] and to("AP-0002") == [None] and to("AP-0003") == [None]
    text = store.load("AP-0001")["outbox"][1]["text"]
    assert "Sam approved it (button in Slack" in text and "approvals show AP-0001" in text
    store.set_reviewer(None)
    card("AP-0004")
    yes("AP-0004")
    assert to("AP-0004") == [None]


def test_the_reviewers_messages_neither_wait_for_nor_unpace_the_askers():
    be_reviewer()
    card("AP-0001")
    card("AP-0002")
    yes("AP-0001")
    yes("AP-0002")
    first, review1 = (i["ref"] for i in store.load("AP-0001")["outbox"])
    second, review2 = (i["ref"] for i in store.load("AP-0002")["outbox"])
    due = core.to_deliver(store.all_records(), config.machine(), store.reviewer())[0]
    assert sorted(due) == sorted([["AP-0001", first], ["AP-0001", review1], ["AP-0002", review2]])
    assert core.held(store.all_records()) == {("AP-0002", second)}   # the asker still gets one yes at a time


def test_the_reviewer_is_typed_into_its_own_session_and_the_asker_keeps_its_own(monkeypatch):
    be_reviewer()
    card("AP-0001")
    yes("AP-0001")
    review = store.load("AP-0001")["outbox"][1]["ref"]
    typed, notes = [], []
    monkeypatch.setattr(slackapi, "note", lambda ts, text: notes.append(text))
    monkeypatch.setattr(router, "deliver", lambda r, text, ref, **kw: typed.append(r["session_id"])
                        or router.Result("delivered", reach={"session_id": "sid-cfo-2"}, moves=[["sid-cfo", "sid-cfo-2"]]))
    L = listener.Listener.__new__(listener.Listener)
    L.in_flight, L.deliveries = set(), queue.Queue()
    L.deliver_one("AP-0001", review)
    rec = store.load("AP-0001")
    assert typed == ["sid-cfo"] and rec["outbox"][1]["delivered_at"]
    assert rec["reach"]["session_id"] == "sid-a" and rec["moves"] == []
    # A reviewer that isn't running finds it in its inbox; the approver's thread hears nothing about it.
    store.update("AP-0001", lambda r: core.take_replies(r, [("5.0", "make it $200")]))
    monkeypatch.setattr(router, "deliver", lambda r, text, ref, **kw: router.Result("waiting", "its session isn't running"))
    L.deliver_one("AP-0001", store.load("AP-0001")["outbox"][3]["ref"])
    assert store.load("AP-0001")["outbox"][3]["inbox_only"] and notes == []


def test_inbox_hands_the_reviewer_its_messages_and_the_asker_its_own(monkeypatch, capsys):
    be_reviewer()
    card("AP-0001")
    yes("AP-0001")
    me = {}
    monkeypatch.setattr(reach, "capture", lambda agent=None: dict(me))
    me.update(agent="Tester", session_id="sid-a", runtime="claude", cwd="/x")
    cli.cmd_inbox(SimpleNamespace(agent=None, all=False))
    out = capsys.readouterr().out
    assert "First acknowledge" in out and "which you review" not in out
    me.update(agent="CFO", session_id="sid-cfo")
    cli.cmd_inbox(SimpleNamespace(agent=None, all=False))
    out = capsys.readouterr().out
    assert "which you review" in out and "First acknowledge" not in out


def test_review_moves_with_the_session_and_only_its_session_can_note(monkeypatch, tmp_path):
    posted = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, thread_ts=None, broadcast=False:
                        posted.append((thread_ts, blocks)) or "7.0")
    me = {"agent": "CFO", "runtime": "claude", "cwd": "/x", "machine": "testbox"}
    monkeypatch.setattr(reach, "capture", lambda agent=None: dict(me))
    for sid in ("sid-cfo", "sid-cfo-restored"):
        me["session_id"] = sid
        cli.cmd_review(SimpleNamespace(types="budget,purchase,money", agent=None, off=False))
    assert store.reviewer()["reach"]["session_id"] == "sid-cfo-restored"
    card("AP-0001")
    note = tmp_path / "read.md"
    note.write_text("**Worth it** while a trial stays under $25.\n- Runway: 12.9 months before and after.")
    me["session_id"] = "sid-cfo"   # the old session no longer holds the role
    with pytest.raises(SystemExit):
        cli.cmd_note(SimpleNamespace(id="AP-0001", file=str(note)))
    me["session_id"] = "sid-cfo-restored"
    cli.cmd_note(SimpleNamespace(id="AP-0001", file=str(note)))
    thread, blocks = posted[0]
    assert thread == "1.1" and "*Worth it*" in blocks[0]["text"]["text"] and "• Runway" in blocks[0]["text"]["text"]
    rec = store.load("AP-0001")
    assert rec["status"] == "pending" and rec["outbox"] == [] and rec["events"][-1]["kind"] == "review_note"
    cli.cmd_review(SimpleNamespace(types=None, agent=None, off=True))
    assert store.reviewer() is None
