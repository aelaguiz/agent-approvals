import json
import subprocess
from types import SimpleNamespace

import pytest

from approvals import config, core, listener, router, store, transcripts
from samples import SAMPLES
from test_cards_store import make_rec


class FakeHerdr:
    """Stands in for the herdr CLI across several Herdr servers; records every call."""

    def __init__(self, panes=None, procs=None, workspaces=("w1",), text="⏵⏵ bypass permissions on", agent_ok=True, sessions=("brain",)):
        self.calls = []
        self.panes = panes or {}          # pane id -> pane info
        self.procs = procs or {}          # pane id -> foreground processes
        self.workspaces = list(workspaces)
        self.text = text
        self.agent_ok = agent_ok
        self.sessions = list(sessions)
        self.resumed_pid = 4242

    def __call__(self, cmd, capture_output=True, text=True, timeout=None):
        if cmd[:3] == ["herdr", "session", "list"]:
            rows = "name status directory socket\n" + "".join(f"{n} running /x /x.sock\n" for n in self.sessions)
            return SimpleNamespace(returncode=0, stdout=rows, stderr="")
        assert cmd[:2] == ["herdr", "--session"] and cmd[2], "every pane call must name the Herdr session"
        args = cmd[3:]
        self.calls.append(args)
        out, rc = {}, 0
        if args[:2] == ["pane", "get"]:
            if args[2] in self.panes:
                out = {"result": {"pane": self.panes[args[2]]}}
            else:
                rc = 1
        elif args[:2] == ["pane", "list"]:
            out = {"result": {"panes": [{"pane_id": p} for p in self.panes]}}
        elif args[:2] == ["pane", "process-info"]:
            out = {"result": {"process_info": {"foreground_processes": self.procs.get(args[3], [])}}}
        elif args[:2] == ["workspace", "list"]:
            out = {"result": {"workspaces": [{"workspace_id": w} for w in self.workspaces]}}
        elif args[:2] == ["tab", "create"]:
            self.panes["w1:p9"] = {"focused": False}
            out = {"result": {"tab": {"tab_id": "w1:t9"}, "root_pane": {"pane_id": "w1:p9"}}}
        elif args[:2] == ["workspace", "create"]:
            self.panes["w7:p1"] = {"focused": False}
            out = {"result": {"workspace": {"workspace_id": "w7"}, "tab": {"tab_id": "w7:t1"}, "root_pane": {"pane_id": "w7:p1"}}}
        elif args[:2] == ["pane", "run"]:
            if "aim claude resume" in args[3]:
                self.procs[args[2]] = [{"pid": self.resumed_pid, "cmdline": "/x/2.1.302"}]
        elif args[:2] == ["pane", "read"]:
            return SimpleNamespace(returncode=0, stdout=self.text, stderr="")
        elif args[:2] == ["agent", "prompt"]:
            if not self.agent_ok:
                rc = 1
        return SimpleNamespace(returncode=rc, stdout=json.dumps(out), stderr="agent_not_found" if rc else "")

    def resumes(self):
        return [c for c in self.calls if c[:2] == ["pane", "run"] and "resume" in c[-1]]

    def sent_to(self, pane):
        return [c for c in self.calls if c[:3] in (["agent", "prompt", pane], ["pane", "run", pane]) and "(ref" in c[-1]]


@pytest.fixture
def reach():
    return {"runtime": "claude", "session_id": "sid-1", "host": config.hostname(), "herdr_session": "brain",
            "herdr_workspace": "w1", "herdr_pane": "w1:p1", "cwd": "/tmp/x", "agent": "Tester"}


@pytest.fixture
def world(monkeypatch):
    """Which pid runs which session, and which sessions are copies of the asker."""
    w = SimpleNamespace(pid_sid={}, copies=set(), delivered=False, fake=None)
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: w.pid_sid.get(pid))
    monkeypatch.setattr(transcripts, "contains", lambda path, needle: w.delivered)
    monkeypatch.setattr(transcripts, "transcript", lambda runtime, sid: f"/t/{sid}.jsonl")
    monkeypatch.setattr(transcripts, "claude_copies", lambda sid, cwd, rid=None: [])
    monkeypatch.setattr(transcripts, "is_copy", lambda runtime, asker, cand, rid: cand == asker or cand in w.copies)

    def live(runtime):
        pids = {p["pid"] for procs in (w.fake.procs.values() if w.fake else []) for p in procs}
        return [(pid, w.pid_sid[pid], "/tmp/x") for pid in pids if pid in w.pid_sid]
    monkeypatch.setattr(transcripts, "live_sessions", live)
    return w


def run(w, fake, reach, ref="abc", **extra):
    w.fake = fake

    def sleep(_):
        w.delivered = any("(ref" in c[-1] for c in fake.calls if c[:2] in (["agent", "prompt"], ["pane", "run"]))
    w.delivered = False
    return router.deliver(reach, f"m (ref {ref})", ref, request_id="AP-0001", run=fake, sleep=sleep,
                          clock=iter(range(0, 100000)).__next__, **extra)


def test_live_pane_gets_the_message(reach, world):
    world.pid_sid = {1: "sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1}]})
    assert run(world, fake, reach).status == "delivered"
    assert fake.sent_to("w1:p1") and not fake.resumes()


def test_unrecognized_agent_falls_back_to_typing(reach, world):
    world.pid_sid = {1: "sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1}]}, agent_ok=False)
    assert run(world, fake, reach).status == "delivered"
    assert ["pane", "run", "w1:p1", "m (ref abc)"] in fake.calls


def test_focused_pane_waits(reach, world):
    world.pid_sid = {1: "sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {"focused": True}}, procs={"w1:p1": [{"pid": 1}]})
    assert run(world, fake, reach).status == "wait"
    assert run(world, fake, reach, waited_seconds=700).status == "delivered"


def test_closed_session_is_resumed_then_told(reach, world):
    world.pid_sid = {4242: "sid-1"}
    fake = FakeHerdr(panes={})
    res = run(world, fake, reach)
    assert res.status == "delivered" and len(fake.resumes()) == 1
    assert fake.resumes()[0][-1] == "cd /tmp/x && aim claude resume sid-1"
    assert fake.sent_to("w1:p9") and res.reach["herdr_pane"] == "w1:p9"


def test_R3_a_different_session_in_the_pane_is_not_prompted(reach, world):
    world.pid_sid = {1: "someone-else", 4242: "sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1}]})
    res = run(world, fake, reach)
    assert res.status == "delivered"
    assert not fake.sent_to("w1:p1") and len(fake.resumes()) == 1


def test_R4_moved_copy_in_the_pane_is_used(reach, world):
    world.pid_sid, world.copies = {1: "sid-3"}, {"sid-3"}
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1}]})
    res = run(world, fake, reach)
    assert res.status == "delivered" and ["sid-1", "sid-3"] in res.moves and fake.sent_to("w1:p1")


def test_R5_asker_running_in_another_pane_is_used_not_resumed(reach, world):
    world.pid_sid = {77: "sid-1"}
    fake = FakeHerdr(panes={"w9:p2": {"focused": False}}, procs={"w9:p2": [{"pid": 77}]})
    res = run(world, fake, reach)
    assert res.status == "delivered" and fake.sent_to("w9:p2") and not fake.resumes()
    assert res.reach["herdr_pane"] == "w9:p2"


def test_R6_two_answers_one_closed_session_resumes_it_once(reach, world):
    world.pid_sid = {4242: "sid-1"}
    fake = FakeHerdr(panes={})
    first = run(world, fake, reach, ref="aaa")
    second = run(world, fake, dict(reach), ref="bbb")   # the other request still points at the closed pane
    assert first.status == second.status == "delivered"
    assert len(fake.resumes()) == 1
    assert fake.sent_to("w1:p9") and len(fake.sent_to("w1:p9")) == 2


def test_open_outside_herdr_retries(reach, world, monkeypatch):
    world.pid_sid = {55: "sid-1"}
    monkeypatch.setattr(transcripts, "live_sessions", lambda runtime: [(55, "sid-1", "/tmp/x")])
    fake = FakeHerdr(panes={})
    res = run(world, fake, reach)
    assert res.status == "retry" and "outside Herdr" in res.reason and not fake.resumes()


def test_already_delivered_is_not_sent_twice(reach, world):
    fake = FakeHerdr()
    world.fake = fake
    world.delivered = True
    res = router.deliver(reach, "m (ref abc)", "abc", request_id="AP-0001", run=fake)
    assert res.status == "delivered" and fake.calls == []


def test_other_machines_go_to_the_inbox(reach, world):
    reach["host"] = "another-machine"
    assert run(world, FakeHerdr(), reach).status == "inbox"


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
