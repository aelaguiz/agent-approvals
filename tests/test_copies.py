"""Copy detection against real transcript files: a moved copy holds the request; a scheduled twin doesn't."""
import json
import os
import time

from approvals import reach, store, transcripts

PROMPT = "From the daily brief: check yesterday's numbers and post the summary."


def write(path, first_prompt, first_reply, *later):
    """A transcript: the prompt, the agent's first reply, then later assistant turns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "message": {"role": "user", "content": first_prompt}},
             {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": first_reply}]}}]
    lines += [{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}} for t in later]
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n")


def setup_homes(tmp_path, monkeypatch):
    home = tmp_path / ".claude"
    proj = home / "projects" / "-tmp-x"
    monkeypatch.setattr(transcripts, "claude_config_dirs", lambda: [home])
    write(proj / "asker.jsonl", PROMPT, "Raising a cap needs approval; I'll post a card.", "Submitted AP-0001. Waiting on it.")
    time.sleep(0.01)
    # Today's run: same prompt, its own first reply, and it has even read the request number.
    write(proj / "twin.jsonl", PROMPT, "Spend changes need Amir's yes. Checking the inbox first.", "approvals show AP-0001: approved")
    os.utime(proj / "twin.jsonl", (time.time() + 5, time.time() + 5))
    return proj


def test_R2_scheduled_twin_is_not_a_copy(tmp_path, monkeypatch):
    setup_homes(tmp_path, monkeypatch)
    assert transcripts.claude_copies("asker", "/tmp/x", "AP-0001") == []
    assert not transcripts.is_copy("claude", "asker", "twin", "AP-0001")


def test_R4_moved_copy_is_found(tmp_path, monkeypatch):
    proj = setup_homes(tmp_path, monkeypatch)
    write(proj / "moved.jsonl", PROMPT, "Raising a cap needs approval; I'll post a card.", "Submitted AP-0001. Waiting on it.", "Back after the move.")
    os.utime(proj / "moved.jsonl", (time.time() + 10, time.time() + 10))
    assert transcripts.claude_copies("asker", "/tmp/x", "AP-0001") == ["moved"]
    assert transcripts.is_copy("claude", "asker", "moved", "AP-0001")


def test_R7_another_session_cannot_take_over_a_request(tmp_path, monkeypatch):
    proj = setup_homes(tmp_path, monkeypatch)
    rec = {"id": "AP-0001", "events": [], "reach": {"runtime": "claude", "session_id": "asker", "agent": "Daily", "herdr_pane": "w1:p1"}}
    reach.refresh(rec, {"runtime": "claude", "session_id": "twin", "agent": "Daily", "herdr_pane": "w2:p1"})
    assert rec["reach"]["session_id"] == "asker" and rec["reach"]["herdr_pane"] == "w1:p1"
    assert rec["events"][-1]["kind"] == "used_by_other_session"
    write(proj / "moved.jsonl", PROMPT, "Raising a cap needs approval; I'll post a card.", "Submitted AP-0001. Waiting on it.")
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


def summary_of(path):
    """How a continued session opens: Claude's summary of the earlier work, naming its transcript."""
    return {"type": "user", "isCompactSummary": True, "message": {"role": "user", "content":
            "This session is being continued from a previous conversation that ran out of context. "
            f"... read the full transcript at: {path}"}}


def write_continuation(path, earlier, *later):
    path.write_text("\n".join(json.dumps(l) for l in [
        summary_of(earlier),
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Picking up where I left off."}]}},
        *[{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": t}]}} for t in later]]) + "\n")
    os.utime(path, (time.time() + 20, time.time() + 20))


def test_a_session_continuing_the_asker_counts_as_the_asker(tmp_path, monkeypatch):
    proj = setup_homes(tmp_path, monkeypatch)
    write_continuation(proj / "cont.jsonl", "/elsewhere/projects/-tmp-x/asker.jsonl", "Revised AP-0001.")
    write_continuation(proj / "cont2.jsonl", str(proj / "cont.jsonl"))       # and a continuation of that one
    assert transcripts.lineage("cont2") == ["cont", "asker"]
    assert transcripts.is_copy("claude", "asker", "cont", "AP-0001") and transcripts.is_copy("claude", "asker", "cont2", "AP-0001")
    assert not transcripts.is_copy("claude", "asker", "twin", "AP-0001")       # a fresh run still isn't
    assert set(transcripts.claude_copies("asker", "/tmp/x", "AP-0001")) == {"cont", "cont2"}


def test_a_moved_copy_still_matches_when_its_session_ids_were_rewritten(tmp_path, monkeypatch):
    proj = setup_homes(tmp_path, monkeypatch)
    write(proj / "a1.jsonl", "Resume the climb; session 11111111-2222-4333-8444-555555555555 has the notes.", "Reading the notes.", "Submitted AP-0002.")
    write(proj / "a2.jsonl", "Resume the climb; session 99999999-8888-4777-8666-555555555555 has the notes.", "Reading the notes.", "Submitted AP-0002.")
    assert transcripts.is_copy("claude", "a1", "a2", "AP-0002")


def test_continuing_session_takes_over_the_request(tmp_path, monkeypatch):
    proj = setup_homes(tmp_path, monkeypatch)
    write_continuation(proj / "cont.jsonl", str(proj / "asker.jsonl"), "Working on AP-0001.")
    rec = {"id": "AP-0001", "events": [], "reach": {"runtime": "claude", "session_id": "asker", "agent": "Daily", "herdr_pane": "w1:p1"}}
    reach.refresh(rec, {"runtime": "claude", "session_id": "cont", "agent": "Daily", "herdr_pane": "w5:p1"})
    assert rec["reach"]["session_id"] == "cont" and rec["reach"]["earlier_ids"] == ["asker"]


def test_a_usage_limit_stop_is_read_from_the_last_reply(tmp_path, monkeypatch):
    proj = setup_homes(tmp_path, monkeypatch)
    stop = {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit", "message": {"role": "assistant", "model": "<synthetic>",
            "content": [{"type": "text", "text": "You've hit your weekly limit · resets Oct 24 at 12pm (America/Chicago)"}]}}
    with open(proj / "asker.jsonl", "a") as f:
        f.write(json.dumps(stop) + "\n")
    assert transcripts.stopped_for_usage("claude", "asker")
    with open(proj / "asker.jsonl", "a") as f:
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Back at it."}]}}) + "\n")
    assert not transcripts.stopped_for_usage("claude", "asker")
    assert not transcripts.stopped_for_usage("claude", "twin")


def test_the_continuing_session_sees_the_askers_waiting_answers_in_its_inbox(tmp_path, monkeypatch, capsys):
    from approvals import cli, core
    proj = setup_homes(tmp_path, monkeypatch)
    write_continuation(proj / "cont.jsonl", str(proj / "asker.jsonl"))
    monkeypatch.chdir(tmp_path)
    rec = {"id": "AP-0001", "title": "t", "agent": "Daily", "status": "pending", "versions": [{"n": 1}], "events": [], "outbox": [],
           "open_question": None, "reach": {"runtime": "claude", "session_id": "asker", "agent": "Daily", "cwd": os.getcwd()}}
    core.take_replies(rec, [("1.0", "make it shorter")])
    store.save(rec)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cont")
    cli.main(["inbox", "--agent", "Someone else"])
    assert '"make it shorter"' in capsys.readouterr().out
