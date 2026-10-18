import json
import subprocess
from types import SimpleNamespace

import pytest

from approvals import config, core, listener, router, store, transcripts
from samples import SAMPLES
from test_cards_store import make_rec


class FakeHerdr:
    """Stands in for the herdr CLI; records every call."""

    def __init__(self, panes=None, procs=None, workspaces=("w1",), text="⏵⏵ bypass permissions on", agent_ok=True):
        self.calls = []
        self.panes = panes or {}
        self.procs = procs or {}
        self.workspaces = list(workspaces)
        self.text = text
        self.agent_ok = agent_ok

    def __call__(self, cmd, capture_output=True, text=True, timeout=None):
        assert cmd[:3] == ["herdr", "--session", cmd[2]] and cmd[2], "every call must name the Herdr session"
        args = cmd[3:]
        self.calls.append(args)
        out = {}
        rc = 0
        if args[:2] == ["pane", "get"]:
            if args[2] in self.panes:
                out = {"result": {"pane": self.panes[args[2]]}}
            else:
                rc = 1
        elif args[:2] == ["pane", "process-info"]:
            out = {"result": {"process_info": {"foreground_processes": self.procs.get(args[3], [])}}}
        elif args[:2] == ["workspace", "list"]:
            out = {"result": {"workspaces": [{"workspace_id": w} for w in self.workspaces]}}
        elif args[:2] == ["tab", "create"]:
            self.panes["w1:p9"] = {"focused": False, "agent_status": "unknown"}
            out = {"result": {"tab": {"tab_id": "w1:t9"}, "root_pane": {"pane_id": "w1:p9"}}}
        elif args[:2] == ["workspace", "create"]:
            self.panes["w7:p1"] = {"focused": False}
            out = {"result": {"workspace": {"workspace_id": "w7"}, "tab": {"tab_id": "w7:t1"}, "root_pane": {"pane_id": "w7:p1"}}}
        elif args[:2] == ["pane", "run"]:
            if "aim claude resume" in args[3]:
                self.procs[args[2]] = [{"pid": 4242, "cmdline": "/x/2.1.302"}]
        elif args[:2] == ["pane", "read"]:
            return SimpleNamespace(returncode=0, stdout=self.text, stderr="")
        elif args[:2] == ["agent", "prompt"]:
            if not self.agent_ok:
                rc = 1
        return SimpleNamespace(returncode=rc, stdout=json.dumps(out), stderr="agent_not_found" if rc else "")


@pytest.fixture
def reach():
    return {"runtime": "claude", "session_id": "sid-1", "host": config.hostname(), "herdr_session": "brain",
            "herdr_workspace": "w1", "herdr_pane": "w1:p1", "cwd": "/tmp/x", "agent": "Tester"}


@pytest.fixture
def quick(monkeypatch):
    seen = {"delivered": False}
    monkeypatch.setattr(transcripts, "contains", lambda path, needle: seen["delivered"])
    monkeypatch.setattr(transcripts, "transcript", lambda runtime, sid: f"/t/{sid}.jsonl")
    monkeypatch.setattr(transcripts, "claude_copies", lambda sid, cwd: [])
    return seen


def kw(fake, seen):
    def sleep(_):
        seen["delivered"] = any(c[:2] in (["agent", "prompt"], ["pane", "run"]) and "(ref" in c[-1] for c in fake.calls)
    return dict(run=fake, sleep=sleep, clock=iter(range(0, 100000, 1)).__next__)


def test_live_pane_gets_the_message(reach, quick, monkeypatch):
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: "sid-1")
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1, "cmdline": "/x/2.1.302"}]})
    res = router.deliver(reach, "[Approvals] hi (ref abc)", "abc", **kw(fake, quick))
    assert res.status == "delivered"
    assert ["agent", "prompt", "w1:p1", "[Approvals] hi (ref abc)"] in fake.calls
    assert not any(c[:2] == ["pane", "run"] and "resume" in c[-1] for c in fake.calls)


def test_unrecognized_agent_falls_back_to_typing(reach, quick, monkeypatch):
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: "sid-1")
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1}]}, agent_ok=False)
    assert router.deliver(reach, "msg (ref abc)", "abc", **kw(fake, quick)).status == "delivered"
    assert ["pane", "run", "w1:p1", "msg (ref abc)"] in fake.calls


def test_focused_pane_waits(reach, quick, monkeypatch):
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: "sid-1")
    fake = FakeHerdr(panes={"w1:p1": {"focused": True}}, procs={"w1:p1": [{"pid": 1}]})
    assert router.deliver(reach, "m (ref abc)", "abc", **kw(fake, quick)).status == "wait"
    assert router.deliver(reach, "m (ref abc)", "abc", waited_seconds=700, **kw(fake, quick)).status == "delivered"


def test_closed_session_is_resumed_then_told(reach, quick, monkeypatch):
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: "sid-1" if pid == 4242 else None)
    fake = FakeHerdr(panes={})
    res = router.deliver(reach, "m (ref abc)", "abc", **kw(fake, quick))
    assert res.status == "delivered"
    runs = [c for c in fake.calls if c[:2] == ["pane", "run"]]
    assert runs[0] == ["pane", "run", "w1:p9", "cd /tmp/x && aim claude resume sid-1"]
    assert runs[-1] == ["pane", "run", "w1:p9", "m (ref abc)"] or ["agent", "prompt", "w1:p9", "m (ref abc)"] in fake.calls
    assert res.reach["herdr_pane"] == "w1:p9"


def test_a_different_session_in_the_pane_is_not_prompted(reach, quick, monkeypatch):
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: "other" if pid == 1 else "sid-1")
    monkeypatch.setattr(router, "_same_conversation", lambda a, b: False)
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1}]})
    res = router.deliver(reach, "m (ref abc)", "abc", **kw(fake, quick))
    assert res.status == "delivered"
    assert not any(c[:3] == ["agent", "prompt", "w1:p1"] for c in fake.calls)
    assert any(c[:2] == ["pane", "run"] and "aim claude resume sid-1" in c[-1] for c in fake.calls)


def test_newest_copy_is_resumed_not_the_frozen_id(reach, quick, monkeypatch):
    monkeypatch.setattr(transcripts, "claude_copies", lambda sid, cwd: ["sid-2"])
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: "sid-2" if pid == 4242 else None)
    fake = FakeHerdr(panes={})
    res = router.deliver(reach, "m (ref abc)", "abc", **kw(fake, quick))
    assert any("aim claude resume sid-2" in c[-1] for c in fake.calls if c[:2] == ["pane", "run"])
    assert ["sid-1", "sid-2"] in res.moves and res.reach["session_id"] == "sid-2"


def test_moved_session_in_the_same_pane_is_used(reach, quick, monkeypatch):
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: "sid-3")
    monkeypatch.setattr(router, "_same_conversation", lambda a, b: True)
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1}]})
    res = router.deliver(reach, "m (ref abc)", "abc", **kw(fake, quick))
    assert res.status == "delivered" and ["sid-1", "sid-3"] in res.moves
    assert ["agent", "prompt", "w1:p1", "m (ref abc)"] in fake.calls


def test_already_delivered_is_not_sent_twice(reach, quick):
    quick["delivered"] = True
    fake = FakeHerdr()
    assert router.deliver(reach, "m (ref abc)", "abc", **kw(fake, quick)).status == "delivered"
    assert fake.calls == []


def test_other_machines_go_to_the_inbox(reach, quick):
    reach["host"] = "amir-server"
    assert router.deliver(reach, "m", "abc", **kw(FakeHerdr(), quick)).status == "inbox"


def test_failures_post_one_note_after_three_tries():
    rec = make_rec(SAMPLES["money"])
    core.enqueue(rec, "feedback", "m")
    ref = rec["outbox"][0]["ref"]
    notes = [listener.Listener._after(rec, ref, router.Result("retry", "no Claude account with room")) for _ in range(5)]
    assert notes[:2] == [None, None] and notes[2] and "no Claude account with room" in notes[2] and notes[3:] == [None, None]


# ----- listener: who counts, and saving before the ack -----

def put(rec):
    store.save(rec)


def test_only_amir_clicks_count(monkeypatch):
    rec = make_rec(SAMPLES["money"])
    put(rec)
    L = listener.Listener()
    payload = {"type": "block_actions", "user": {"id": "U_SOMEONE"}, "actions": [{"value": "AP-0001|1|approve"}], "container": {}}
    L.handle_click(payload)
    assert store.load("AP-0001")["decision"] is None
    payload["user"]["id"] = config.settings()["approver"]
    L.handle_click(payload)
    saved = store.load("AP-0001")
    assert saved["decision"]["verdict"] == "approved" and len(saved["outbox"]) == 1


def test_reply_filter(monkeypatch):
    s = config.settings()
    assert listener.is_approver({"user": s["approver"], "text": "hi"}, {})
    assert not listener.is_approver({"user": s["approver"], "bot_id": "B1", "text": "hi"}, {})
    assert not listener.is_approver({"user": "UBOT", "text": "[test reply] hi"}, {"test": True})
    monkeypatch.setenv("APPROVALS_TEST_APPROVER", "UBOT")
    assert listener.is_approver({"user": "UBOT", "text": "[test reply] hi"}, {"test": True})
    assert not listener.is_approver({"user": "UBOT", "text": "[test reply] hi"}, {"test": False})
    assert not listener.is_approver({"user": "UBOT", "text": "evidence note"}, {"test": True})
    assert listener.reply_text({"text": "[test reply] Do Brazil"}) == "Do Brazil"


def test_polling_turns_replies_into_one_message(monkeypatch):
    rec = make_rec(SAMPLES["price"])
    put(rec)
    s = config.settings()
    msgs = [{"ts": "1.1", "user": "UBOT", "text": "card"},
            {"ts": "2.0", "user": "UBOT", "bot_id": "B", "text": "evidence"},
            {"ts": "3.0", "user": s["approver"], "text": "Do Brazil and Mexico."},
            {"ts": "3.5", "user": s["approver"], "text": "Leave Japan."}]
    monkeypatch.setattr(listener.slackapi, "replies", lambda ts, oldest=None: msgs)
    monkeypatch.setattr(listener.Listener, "try_refresh", lambda self, rid: None)
    L = listener.Listener()
    L.poll_thread(store.load("AP-0001"))
    L.poll_thread(store.load("AP-0001"))   # second pass finds nothing new
    saved = store.load("AP-0001")
    assert len(saved["outbox"]) == 1 and saved["last_seen"] == "3.5"
    assert '"Do Brazil and Mexico." / "Leave Japan."' in saved["outbox"][0]["text"]
