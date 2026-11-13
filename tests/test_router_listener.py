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
        elif args[:2] == ["pane", "send-keys"]:
            self.text = getattr(self, "after_keys", self.text)
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
    w = SimpleNamespace(pid_sid={}, copies=set(), newer=[], stopped=set(), delivered=False, fake=None)
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: w.pid_sid.get(pid))
    monkeypatch.setattr(transcripts, "contains", lambda path, needle: w.delivered)
    monkeypatch.setattr(transcripts, "transcript", lambda runtime, sid: f"/t/{sid}.jsonl")
    monkeypatch.setattr(transcripts, "claude_copies", lambda sid, cwd, rid=None: list(w.newer))
    monkeypatch.setattr(transcripts, "stopped_for_usage", lambda runtime, sid: sid in w.stopped)
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


def test_focused_pane_gets_the_message_right_away(reach, world):
    world.pid_sid = {1: "sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {"focused": True}}, procs={"w1:p1": [{"pid": 1}]})
    assert run(world, fake, reach).status == "delivered"
    assert fake.sent_to("w1:p1")


def test_closed_session_is_resumed_then_told(reach, world):
    world.pid_sid = {4242: "sid-1"}
    fake = FakeHerdr(panes={})
    res = run(world, fake, reach)
    assert res.status == "delivered" and len(fake.resumes()) == 1
    assert fake.resumes()[0][-1] == "cd /tmp/x && aim claude resume sid-1"
    assert fake.sent_to("w1:p9") and res.reach["herdr_pane"] == "w1:p9"


def test_resume_does_not_send_when_it_opens_a_different_fork_family(reach, world):
    store.add_fork("fork")
    world.pid_sid = {4242: "fork"}
    fake = FakeHerdr(panes={})
    res = run(world, fake, reach)
    assert res.status == "waiting" and not res.moves
    assert ["pane", "close", "w1:p9"] in fake.calls and not fake.sent_to("w1:p9")


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


def test_stopped_asker_hands_the_answer_to_the_session_continuing_its_work(reach, world):
    world.pid_sid, world.copies, world.stopped = {1: "sid-1", 2: "sid-2"}, {"sid-2"}, {"sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {}, "w1:p2": {}}, procs={"w1:p1": [{"pid": 1}], "w1:p2": [{"pid": 2}]})
    res = run(world, fake, reach)
    assert res.status == "delivered" and fake.sent_to("w1:p2") and not fake.sent_to("w1:p1")
    assert ["sid-1", "sid-2"] in res.moves and res.reach["session_id"] == "sid-2"


def test_stopped_asker_with_nothing_continuing_it_gets_nothing_typed(reach, world):
    world.pid_sid, world.stopped = {1: "sid-1"}, {"sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {}}, procs={"w1:p1": [{"pid": 1}]})
    res = run(world, fake, reach)
    assert res.status == "retry" and "usage limit" in res.reason
    assert not fake.sent_to("w1:p1") and not fake.resumes()


def test_usage_limit_menu_on_screen_stops_a_send(reach, world):
    world.pid_sid = {1: "sid-1"}
    menu = "What do you want to do?\n1. Stop and wait for limit to reset\n3. Switch to usage credits"
    fake = FakeHerdr(panes={"w1:p1": {}}, procs={"w1:p1": [{"pid": 1}]}, text=menu)
    res = run(world, fake, reach)
    assert res.status == "retry" and not fake.sent_to("w1:p1")


def test_closed_asker_resumes_the_newest_session_that_can_still_work(reach, world):
    world.newer, world.stopped, world.pid_sid = ["sid-3", "sid-2"], {"sid-3"}, {4242: "sid-2"}
    fake = FakeHerdr(panes={})
    res = run(world, fake, reach)
    assert res.status == "delivered" and fake.resumes()[0][-1].endswith("aim claude resume sid-2")
    assert ["sid-1", "sid-2"] in res.moves


def test_a_resume_that_lands_on_the_usage_limit_menu_closes_its_pane(reach, world):
    world.pid_sid = {4242: "sid-1"}
    fake = FakeHerdr(panes={}, text="What do you want to do?\n3. Switch to usage credits")
    res = run(world, fake, reach)
    assert res.status == "retry" and "usage limit" in res.reason
    assert ["pane", "close", "w1:p9"] in fake.calls and not fake.sent_to("w1:p9")


def test_a_resume_that_lands_on_the_trust_prompt_accepts_it_then_tells(reach, world):
    world.pid_sid = {4242: "sid-1"}
    fake = FakeHerdr(panes={}, text="Yes, I trust this folder")
    fake.after_keys = "⏵⏵ bypass permissions on"
    run(world, fake, reach)
    assert ["pane", "send-keys", "w1:p9", "Down", "Enter"] in fake.calls and fake.sent_to("w1:p9")


def test_a_mac_that_changed_its_name_still_gets_its_own_sessions_answers(reach, world, monkeypatch):
    """A Mac's reported name follows its network, so the name on an older card can differ from today's."""
    monkeypatch.setattr(config, "hostname", lambda: "Studio-Laptop")
    reach["host"] = "Mac"
    world.pid_sid = {1: "sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1, "cmdline": "/x/2.1.302"}]})
    res = run(world, fake, reach)
    assert res.status != "inbox" and fake.sent_to("w1:p1")


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


def test_other_machines_go_to_the_inbox(reach, world, monkeypatch):
    reach["host"] = "another-machine"
    monkeypatch.setattr(transcripts, "transcript", lambda runtime, sid: None)
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


def test_the_approvers_own_post_in_the_channel_asks_for_a_list(monkeypatch):
    me = config.settings()["approver"]
    assert listener.is_ask({"user": me, "ts": "5.0", "text": "what's waiting?"})
    assert listener.is_ask({"user": me, "ts": "5.0", "thread_ts": "5.0", "text": "a post that has replies"})
    assert not listener.is_ask({"user": me, "ts": "6.0", "thread_ts": "1.0", "subtype": "thread_broadcast", "text": "x"})
    assert not listener.is_ask({"user": me, "ts": "5.0", "subtype": "channel_join", "text": "joined"})
    assert not listener.is_ask({"user": "U_SOMEONE", "ts": "5.0", "text": "what's waiting?"})
    stand_in = {"user": "UBOT", "bot_id": "B1", "ts": "5.0", "text": "[test reply] what's waiting?"}
    monkeypatch.setenv("APPROVALS_TEST_APPROVER", "UBOT")
    assert not listener.is_ask(stand_in)
    monkeypatch.setenv("APPROVALS_TEST", "1")
    assert listener.is_ask(stand_in)

    seen = {}
    monkeypatch.setattr(listener.digest, "load_state", lambda: {"ts": "100.1"})
    monkeypatch.setattr(listener.slackapi, "history", lambda oldest: seen.setdefault("since", oldest) and [stand_in])
    monkeypatch.setattr(listener.digest, "tick", lambda asked: seen.update(asked=asked))
    listener.Listener().lists()
    assert seen == {"since": "100.1", "asked": True}


def test_polling_sends_each_reply_as_its_own_message(monkeypatch):
    """The approver correcting himself in a row reaches the agent every time, never joined or held back."""
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
    assert len(saved["outbox"]) == 2 and saved["last_seen"] == "3.5"
    assert '"Do Brazil and Mexico."' in saved["outbox"][0]["text"] and '"Leave Japan."' in saved["outbox"][1]["text"]
    assert core.to_deliver([saved], config.machine())[0] == [["AP-0001", saved["outbox"][0]["ref"]]]


def test_a_herdr_session_that_isnt_running_is_started_for_the_resume(reach, world):
    """Most machines don't run the session this tool resumes into; it is started headless when needed."""
    world.pid_sid = {4242: "sid-1"}
    fake = FakeHerdr(panes={}, sessions=())
    started = []

    def popen(cmd, env=None, **kw):
        started.append((cmd, env["HERDR_SESSION"]))
        fake.sessions.append(env["HERDR_SESSION"])
    res = run(world, fake, reach, popen=popen)
    assert started == [(["herdr", "server"], "default")]
    assert res.status == "delivered" and res.reach["herdr_session"] == "default"


def test_an_answer_for_a_session_on_another_machine_is_offered_elsewhere(reach, world, monkeypatch):
    reach["machine"] = "laptop"
    monkeypatch.setattr(transcripts, "transcript", lambda runtime, sid: None)
    res = run(world, FakeHerdr(), reach)
    assert res.status == "inbox" and res.elsewhere


# ----- an agent that isn't running is never started for an answer -----

def test_a_closed_session_is_left_alone_and_its_answer_waits(reach, world):
    world.pid_sid = {4242: "sid-1"}
    fake = FakeHerdr(panes={}, sessions=())
    started = []
    res = run(world, fake, reach, resume=False, popen=lambda cmd, **kw: started.append(cmd))
    assert res.status == "waiting"
    assert not fake.resumes() and not started
    assert not [c for c in fake.calls if c[:2] in (["tab", "create"], ["workspace", "create"])]


def test_a_running_session_still_has_its_answer_typed_in(reach, world):
    world.pid_sid = {1: "sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1}]})
    assert run(world, fake, reach, resume=False).status == "delivered"
    assert fake.sent_to("w1:p1") and not fake.resumes()


def test_a_running_session_stopped_at_its_usage_limit_waits_for_its_work_to_continue(reach, world):
    world.pid_sid = {1: "sid-1"}
    world.stopped = {"sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {"focused": False}}, procs={"w1:p1": [{"pid": 1}]})
    res = run(world, fake, reach, resume=False)
    assert res.status == "retry" and res.reason == router.STOPPED and not fake.resumes()


def test_answers_for_a_stopped_agent_wait_for_it_with_one_note():
    rec = make_rec(SAMPLES["money"])
    core.enqueue(rec, "feedback", "m1")
    core.enqueue(rec, "feedback", "m2")
    first, second = (listener.Listener._after(rec, i["ref"], router.Result("waiting", "its session isn't running"))
                     for i in list(rec["outbox"]))
    assert "Tester isn't running" in first and second is None
    assert all(i["inbox_only"] and i["waiting_for_agent"] and i["attempts"] == 0 for i in rec["outbox"])
    assert core.next_item(rec) is None          # not typed in later, even once the agent is back
    core.enqueue(rec, "decision", "m3")         # an answer while it's running again is typed in as usual
    assert core.next_item(rec)["ref"] == rec["outbox"][-1]["ref"]


def test_the_service_never_asks_for_a_session_to_be_resumed(monkeypatch):
    rec = make_rec(SAMPLES["money"])
    core.enqueue(rec, "decision", "m")
    put(rec)
    asked = {}

    def deliver(reach, text, ref, request_id=None, **kw):
        asked.update(kw)
        return router.Result("waiting", "its session isn't running")
    monkeypatch.setattr(router, "deliver", deliver)
    notes = []
    monkeypatch.setattr(listener.slackapi, "note", lambda ts, text: notes.append(text))
    listener.Listener().deliver_one("AP-0001", rec["outbox"][0]["ref"])
    assert asked == {"resume": False, "confirm": False}
    assert store.load("AP-0001")["outbox"][0]["waiting_for_agent"] and len(notes) == 1


def test_an_agent_back_from_being_stopped_finds_its_answer_in_its_inbox(monkeypatch, capsys):
    from approvals import cli
    rec = make_rec(SAMPLES["money"], reach={"runtime": "claude", "session_id": "sid-1", "agent": "Tester"})
    core.enqueue(rec, "feedback", "Amir replied: make it $15,000", react_ts="2.2")
    listener.Listener._after(rec, rec["outbox"][0]["ref"], router.Result("waiting", "its session isn't running"))
    put(rec)
    reacted = []
    monkeypatch.setattr(cli.slackapi, "react", lambda ts, name: reacted.append((ts, name)))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-1")
    cli.main(["inbox", "--agent", "Tester"])
    assert "make it $15,000" in capsys.readouterr().out
    item = store.load("AP-0001")["outbox"][0]
    assert item["delivered_via"] == "inbox" and item["seen_by_agent"]
    assert reacted == [("2.2", "incoming_envelope")]
    cli.main(["inbox", "--agent", "Tester"])
    assert "No approval answers waiting" in capsys.readouterr().out


def test_a_later_run_of_the_same_job_takes_an_answer_waiting_for_a_run_that_stopped(monkeypatch, capsys):
    """A finished scheduled run never comes back, so its answer goes to the job's next run, once."""
    from approvals import cli
    rec = make_rec(SAMPLES["money"], reach={"runtime": "claude", "session_id": "run-1", "agent": "Tester", "cwd": "/tmp/x"})
    core.enqueue(rec, "decision", "Amir approved it")
    put(rec)
    monkeypatch.setattr(cli.slackapi, "react", lambda ts, name: None)
    monkeypatch.setattr(transcripts, "is_copy", lambda *a: False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "run-2")
    cli.main(["inbox", "--agent", "Tester"])
    assert "No approval answers waiting" in capsys.readouterr().out      # run 1 may still be running: not yet
    listener.Listener._after(rec, rec["outbox"][0]["ref"], router.Result("waiting", "its session isn't running"))
    put(rec)
    cli.main(["inbox", "--agent", "Tester"])
    assert "Amir approved it" in capsys.readouterr().out
    assert store.load("AP-0001")["outbox"][0]["claimed_by"] == "run-2"
