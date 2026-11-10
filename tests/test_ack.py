"""Agents acknowledge answers themselves; a delivery nobody acknowledges is called out, once."""
from datetime import timedelta

from approvals import core, listener, store


def rec_with(*kinds):
    rec = {"id": "AP-0001", "title": "t", "agent": "Pricing", "status": "pending", "versions": [{"n": 1, "card": {"type": "money", "title": "t"}}],
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


def test_a_decision_is_acknowledged_first_and_a_reply_is_answered_once():
    text = rec_with("decision")["outbox"][-1]["text"]
    assert "First acknowledge it in the thread" in text and text.index("approvals ack") < text.index("approvals done")
    text = rec_with("feedback")["outbox"][-1]["text"]
    assert "approvals answer AP-0001" in text and "approvals revise AP-0001" in text
    assert "Your reply is your acknowledgement" in text and "First acknowledge" not in text
    assert rec_with("expired")["outbox"][-1]["needs_ack"] is False


def test_any_post_of_the_agents_counts_as_its_acknowledgement():
    rec = rec_with("feedback")
    rec["outbox"][-1]["delivered_at"] = store.iso()
    assert core.mark_acked(rec) == ["feedback"] and rec["outbox"][-1]["acked_at"]
    assert core.mark_acked(rec) == []


def test_acknowledging_marks_every_answer_sent_so_far():
    rec = rec_with("feedback", "feedback", "feedback")
    rec["outbox"][0]["delivered_at"] = store.iso()
    rec["outbox"][1]["sent_at"] = store.iso()   # typed, not yet seen in the transcript: agents often ack first
    kinds = core.acknowledge(rec, "Got it: cutting it to half and dropping the product mention.", "3.0")
    assert kinds == ["feedback", "feedback"] and [bool(i["acked_at"]) for i in rec["outbox"]] == [True, True, False]
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
