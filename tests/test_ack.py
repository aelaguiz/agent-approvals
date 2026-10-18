"""Agents acknowledge answers themselves; a delivery nobody acknowledges is called out, once."""
from datetime import timedelta

from approvals import core, listener, store


def rec_with(*kinds):
    rec = {"id": "AP-0001", "title": "t", "agent": "Pricing", "status": "pending", "versions": [{"n": 1}],
           "events": [], "outbox": [], "open_question": None, "thread_ts": "1.1",
           "deadline": store.iso(store.now() + timedelta(hours=5))}
    for k in kinds:
        if k == "decision":
            core.decide(rec, 1, "approved", "U")
        elif k == "expired":
            core.expire(rec)
        else:
            core.take_replies(rec, [("2.0", "make it shorter")])
    return rec


def test_every_answer_tells_the_agent_to_acknowledge_first():
    for kind in ("feedback", "decision"):
        text = rec_with(kind)["outbox"][-1]["text"]
        assert "First acknowledge it in the thread" in text and "approvals ack AP-0001" in text
        assert text.index("approvals ack") < text.index("approvals done" if kind == "decision" else "approvals revise")
    assert rec_with("expired")["outbox"][-1]["needs_ack"] is False


def test_acknowledging_marks_every_open_answer():
    rec = rec_with("feedback", "feedback")
    kinds = core.acknowledge(rec, "Got it: cutting it to half and dropping the product mention.", "3.0")
    assert kinds == ["feedback", "feedback"] and all(i["acked_at"] for i in rec["outbox"])
    assert rec["events"][-1]["kind"] == "ack"


def test_unacknowledged_delivery_is_called_out_once(monkeypatch):
    notes = []
    monkeypatch.setattr(listener.slackapi, "note", lambda ts, text: notes.append(text))
    rec = rec_with("feedback")
    rec["outbox"][0]["delivered_at"] = store.iso(store.now() - timedelta(minutes=11))
    store.save(rec)
    L = listener.Listener.__new__(listener.Listener)
    L.warn_unacknowledged(store.load("AP-0001"))
    L.warn_unacknowledged(store.load("AP-0001"))
    assert len(notes) == 1 and "Pricing received your answer" in notes[0] and "hasn't acknowledged" in notes[0]


def test_acknowledged_or_recent_deliveries_are_not_called_out(monkeypatch):
    notes = []
    monkeypatch.setattr(listener.slackapi, "note", lambda ts, text: notes.append(text))
    L = listener.Listener.__new__(listener.Listener)
    recent = rec_with("feedback")
    recent["outbox"][0]["delivered_at"] = store.iso(store.now() - timedelta(minutes=3))
    L.warn_unacknowledged(recent)
    acked = rec_with("feedback")
    acked["outbox"][0]["delivered_at"] = store.iso(store.now() - timedelta(minutes=30))
    core.acknowledge(acked, "On it.", "3.0")
    L.warn_unacknowledged(acked)
    assert notes == []


def test_inbox_gives_each_answer_to_one_session(monkeypatch, capsys):
    from types import SimpleNamespace
    from approvals import cli, reach
    rec = rec_with("decision")
    rec["reach"] = {"agent": "Daily", "session_id": "asker", "earlier_ids": []}
    store.save(rec)
    # Today's run of the same agent: the answer is still on its way to the asker, so it's not handed over.
    monkeypatch.setattr(reach, "capture", lambda agent=None: {"agent": "Daily", "session_id": "today"})
    cli.cmd_inbox(SimpleNamespace(agent="Daily", all=False))
    assert "No approval answers waiting" in capsys.readouterr().out
    # Once the service gives up delivering it, today's run gets it, and the service stops trying.
    def give_up(r):
        r["outbox"][0]["noted_failure"] = True
    store.update("AP-0001", give_up)
    cli.cmd_inbox(SimpleNamespace(agent="Daily", all=False))
    assert "approved it" in capsys.readouterr().out
    item = store.load("AP-0001")["outbox"][0]
    assert item["delivered_via"] == "inbox" and item["claimed_by"] == "today" and item["delivered_at"]
