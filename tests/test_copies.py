"""Copy detection against real transcript files: a moved copy holds the request; a scheduled twin doesn't."""
import json
import os
import time

from approvals import reach, store, transcripts

PROMPT = "From the daily brief: check yesterday's numbers and post the summary."


def write(path, first_prompt, extra=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "message": {"role": "user", "content": first_prompt}},
             {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": extra}]}}]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def setup_homes(tmp_path, monkeypatch):
    home = tmp_path / ".claude"
    proj = home / "projects" / "-tmp-x"
    monkeypatch.setattr(transcripts, "claude_config_dirs", lambda: [home])
    write(proj / "asker.jsonl", PROMPT, "Submitted AP-0001. Waiting on it.")
    time.sleep(0.01)
    write(proj / "twin.jsonl", PROMPT, "Submitted AP-0002 for today's run.")
    os.utime(proj / "twin.jsonl", (time.time() + 5, time.time() + 5))
    return proj


def test_R2_scheduled_twin_is_not_a_copy(tmp_path, monkeypatch):
    setup_homes(tmp_path, monkeypatch)
    assert transcripts.claude_copies("asker", "/tmp/x", "AP-0001") == []
    assert not transcripts.is_copy("claude", "asker", "twin", "AP-0001")


def test_R4_moved_copy_is_found(tmp_path, monkeypatch):
    proj = setup_homes(tmp_path, monkeypatch)
    write(proj / "moved.jsonl", PROMPT, "Submitted AP-0001. Waiting on it. (copied by AI Manager)")
    os.utime(proj / "moved.jsonl", (time.time() + 10, time.time() + 10))
    assert transcripts.claude_copies("asker", "/tmp/x", "AP-0001") == ["moved"]
    assert transcripts.is_copy("claude", "asker", "moved", "AP-0001")


def test_R7_another_session_cannot_take_over_a_request(tmp_path, monkeypatch):
    proj = setup_homes(tmp_path, monkeypatch)
    rec = {"id": "AP-0001", "events": [], "reach": {"runtime": "claude", "session_id": "asker", "agent": "Daily", "herdr_pane": "w1:p1"}}
    reach.refresh(rec, {"runtime": "claude", "session_id": "twin", "agent": "Daily", "herdr_pane": "w2:p1"})
    assert rec["reach"]["session_id"] == "asker" and rec["reach"]["herdr_pane"] == "w1:p1"
    assert rec["events"][-1]["kind"] == "used_by_other_session"
    write(proj / "moved.jsonl", PROMPT, "Submitted AP-0001. (copied by AI Manager)")
    reach.refresh(rec, {"runtime": "claude", "session_id": "moved", "agent": "Daily", "herdr_pane": "w3:p1"})
    assert rec["reach"]["session_id"] == "moved" and rec["reach"]["earlier_ids"] == ["asker"]
    reach.refresh(rec, {"runtime": "claude", "session_id": "moved", "agent": "Daily", "herdr_pane": "w4:p1"})
    assert rec["reach"]["herdr_pane"] == "w4:p1"   # the asker itself moving panes is always fine


def test_R8_two_answers_are_delivered_in_order():
    from approvals import core
    rec = {"id": "AP-0001", "title": "t", "status": "pending", "versions": [{"n": 1}], "events": [], "outbox": [], "open_question": None}
    core.take_replies(rec, [("1.0", "first")])
    core.take_replies(rec, [("2.0", "second")])
    texts = [o["text"] for o in rec["outbox"]]
    assert '"first"' in texts[0] and '"second"' in texts[1]


def test_R8_a_later_message_waits_for_an_earlier_retry(monkeypatch):
    from datetime import timedelta
    from approvals import core, listener
    rec = {"id": "AP-0001", "title": "t", "status": "pending", "versions": [{"n": 1}], "events": [], "outbox": [],
           "open_question": None, "thread_ts": "1.1", "deadline": store.iso(store.now() + timedelta(hours=1))}
    core.take_replies(rec, [("1.0", "first")])
    core.take_replies(rec, [("2.0", "second")])
    rec["outbox"][0]["next_try"] = store.iso(store.now() + timedelta(minutes=5))   # first is waiting to retry
    store.save(rec)
    L = listener.Listener.__new__(listener.Listener)
    L.in_flight, L.deliveries = set(), __import__("queue").Queue()
    monkeypatch.setattr(listener.Listener, "try_refresh", lambda self, rid: None)
    L.sweep()
    assert L.deliveries.empty()
    rec["outbox"][0]["next_try"] = store.iso(store.now())
    store.save(rec)
    L.sweep()
    assert L.deliveries.get_nowait() == ("AP-0001", rec["outbox"][0]["ref"]) and L.deliveries.empty()
