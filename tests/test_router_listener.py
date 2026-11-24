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


@pytest.mark.parametrize("shell", ["zsh", "-bash", "sh", "fish"])
def test_closed_session_reuses_its_shell_pane(reach, world, shell):
    world.pid_sid = {4242: "sid-1"}
    reach.update(herdr_workspace="old-workspace", herdr_tab="old-tab")
    fake = FakeHerdr(panes={"w1:p1": {"tab_id": "w1:t1", "workspace_id": "w1"}},
                     procs={"w1:p1": [{"pid": 1, "name": shell}]})
    res = run(world, fake, reach)
    assert res.status == "delivered" and fake.resumes()[0][2] == "w1:p1"
    assert fake.sent_to("w1:p1")
    assert not [c for c in fake.calls if c[:2] in (["tab", "create"], ["workspace", "create"])]
    assert {k: res.reach[k] for k in ("herdr_session", "herdr_pane", "herdr_workspace", "herdr_tab")} == {
        "herdr_session": "brain", "herdr_pane": "w1:p1", "herdr_workspace": "w1", "herdr_tab": "w1:t1"}


def test_closed_session_opens_a_tab_when_its_pane_is_busy(reach, world):
    world.pid_sid = {4242: "sid-1"}
    fake = FakeHerdr(panes={"w1:p1": {"tab_id": "w1:t1", "workspace_id": "w1"}},
                     procs={"w1:p1": [{"pid": 1, "name": "vim"}]})
    res = run(world, fake, reach)
    assert res.status == "delivered" and fake.resumes()[0][2] == "w1:p9"
    assert [c for c in fake.calls if c[:2] == ["tab", "create"]]
    assert not [c for c in fake.calls if c[:2] == ["workspace", "create"]]


def test_closed_session_names_a_new_workspace_for_its_agent(reach, world):
    world.pid_sid = {4242: "sid-1"}
    fake = FakeHerdr(workspaces=())
    res = run(world, fake, reach)
    assert res.status == "delivered" and fake.resumes()[0][2] == "w7:p1"
    assert [c for c in fake.calls if c[:2] == ["workspace", "create"]] == [
        ["workspace", "create", "--cwd", "/tmp/x", "--label", "approvals-Tester", "--no-focus"]]


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


# ----- an agent started again in a new session still gets its answers -----

MEMBER = "/Users/sam/work/pods/social/instagram"


def seen_as(session, machine, at, agent="Social pod · Instagram member", cwd=MEMBER, **kw):
    return {"runtime": "claude", "session_id": session, "agent": agent, "cwd": cwd, "machine": machine,
            "herdr_session": "pods", "herdr_pane": "w3:p1", "updated": at, **kw}


def test_the_same_agents_newest_other_session_is_where_it_runs_now():
    old = make_rec(SAMPLES["money"], agent="Social pod · Instagram member", reach=seen_as("s-old", "mini-1", "2026-11-13T03:43:16+00:00"))
    others = [
        make_rec(SAMPLES["money"], id="AP-0002", reach=seen_as("s-mid", "m3-max", "2026-11-14T18:39:00+00:00")),
        # On Linux the same folder sits under /home; and a command on someone else's card shows where it ran too
        make_rec(SAMPLES["money"], id="AP-0003", reach=seen_as("s-x", "mini-2", "2026-11-15T01:00:00+00:00", agent="Lessons pod · writer"),
                 events=[{"kind": "used_by_other_session", "at": "2026-11-15T04:06:00+00:00", "session_id": "s-new",
                          "seen": seen_as("s-new", "home", "2026-11-15T04:06:00+00:00", cwd="/home/sam/work/pods/social/instagram")}]),
        make_rec(SAMPLES["money"], id="AP-0004", reach=seen_as("s-before", "mini-1", "2026-11-12T00:00:00+00:00")),   # older than the card's own
        make_rec(SAMPLES["money"], id="AP-0005", reach=seen_as("s-lead", "m3-max", "2026-11-15T05:00:00+00:00", cwd="/Users/sam/work/pods/social/lead")),
        make_rec(SAMPLES["money"], id="AP-0006", reach=seen_as("s-codex", "m3-max", "2026-11-15T05:00:00+00:00", runtime="codex")),
    ]
    assert core.successor(old, [old] + others)["session_id"] == "s-new"
    assert core.successor(old, [old] + others[:1])["session_id"] == "s-mid"
    assert core.successor(old, [old] + others[3:]) is None


SEAT = {"pod_command": "/Users/sam/work/pod", "herdr_session": "pods", "pod": "social", "copy": "instagram"}


def test_a_pod_member_is_its_tab_and_anyone_else_is_known_by_a_name_only_from_its_folder():
    at, later = "2026-11-13T03:43:16+00:00", "2026-11-15T04:06:00+00:00"
    work = MEMBER + "/work/2026-11-11-a-job"
    # A member raises a card from a work folder; the session in its tab later, on any machine, is the member
    card = make_rec(SAMPLES["money"], reach=seen_as("s-old", "mini-1", at, cwd=work, seat=SEAT))
    in_its_tab = make_rec(SAMPLES["money"], id="AP-0002", reach=seen_as("s-new", "m3-max", later, seat=dict(SEAT, pod_command="/x/pod")))
    # The same launcher's name, in its folder: a chat opened there, or the role's second copy, in a tab of its own
    chat = make_rec(SAMPLES["money"], id="AP-0003", reach=seen_as("s-chat", "mini-1", later))
    copy_2 = make_rec(SAMPLES["money"], id="AP-0004", reach=seen_as("s-two", "mini-1", later, seat=dict(SEAT, copy="instagram-2")))
    assert core.successor(card, [card, chat, copy_2]) is None
    assert core.successor(card, [card, chat, copy_2, in_its_tab])["session_id"] == "s-new"
    # Another agent's name counts only from the same folder, and never joins a pod member's session
    typed = make_rec(SAMPLES["money"], reach=seen_as("s-old", "mini-1", at, cwd=work))
    elsewhere = make_rec(SAMPLES["money"], id="AP-0002", reach=seen_as("s-new", "m3-max", later))
    assert core.successor(typed, [typed, elsewhere, in_its_tab]) is None
    # A name nobody gave is the folder's own, which every session that runs a command there shares
    root = "/Users/sam/work"
    unnamed = make_rec(SAMPLES["money"], reach=seen_as("s-old", "mini-1", at, agent="work", cwd=root))
    someone_else = make_rec(SAMPLES["money"], id="AP-0002", reach=seen_as("s-other", "mini-2", later, agent="work", cwd=root))
    assert core.successor(unnamed, [unnamed, someone_else]) is None


def test_a_fork_is_never_where_the_agent_it_was_forked_from_runs_now():
    old = make_rec(SAMPLES["money"], reach=seen_as("s-old", "mini-1", "2026-11-13T03:43:16+00:00"))
    fork = make_rec(SAMPLES["money"], id="AP-0002", reach=seen_as("s-fork", "mini-1", "2026-11-14T00:00:00+00:00"))
    assert core.successor(old, [old, fork])["session_id"] == "s-fork"
    store.add_fork("s-fork")
    assert core.successor(old, [old, fork]) is None


def test_an_answer_for_a_session_that_is_gone_goes_to_the_same_agents_new_session(monkeypatch):
    """The social pod moved machines on November 14, 2026: each member got a new session, eight of the Instagram
    member's open cards still named its old one, and Amir's answer on one was told the member wasn't running."""
    old, new = seen_as("s-old", "mini-1", "2026-11-13T03:43:16+00:00"), seen_as("s-new", "m3-max", "2026-11-15T04:06:00+00:00")
    name = old["agent"]
    answered = make_rec(SAMPLES["money"], agent=name, status="approved", reach=dict(old))
    core.enqueue(answered, "comment", "Amir replied: do it")
    put(answered)
    put(make_rec(SAMPLES["money"], id="AP-0002", agent=name, status="approved", reach=dict(old)))   # its other open card
    put(make_rec(SAMPLES["money"], id="AP-0003", agent=name, status="done", reach=dict(old)))       # a finished one stays
    put(make_rec(SAMPLES["money"], id="AP-0004", agent=name, reach=dict(new)))                      # raised since it moved
    monkeypatch.setattr(router, "deliver", lambda reach, text, ref, **kw: router.Result("waiting", "its session isn't running"))
    notes = []
    monkeypatch.setattr(listener.slackapi, "note", lambda ts, text: notes.append(text))
    ref = answered["outbox"][0]["ref"]
    listener.Listener().deliver_one("AP-0001", ref)
    rec = store.load("AP-0001")
    assert rec["reach"]["session_id"] == "s-new" and rec["reach"]["machine"] == "m3-max" and rec["reach"]["earlier_ids"] == ["s-old"]
    assert rec["reach"]["agent"] == name and rec["moves"] == [["s-old", "s-new"]]
    assert core.to_deliver([rec], "m3-max")[0] == [["AP-0001", ref]]   # typed in on that machine's next pass
    assert not rec["outbox"][0].get("waiting_for_agent") and notes == []
    assert store.load("AP-0002")["reach"]["session_id"] == "s-new"
    assert store.load("AP-0003")["reach"]["session_id"] == "s-old"
    # Not running there either: the agent really is stopped, and he is told so once
    listener.Listener().deliver_one("AP-0001", ref)
    assert store.load("AP-0001")["outbox"][0]["waiting_for_agent"] and len(notes) == 1 and "isn't running" in notes[0]


def test_a_command_from_another_session_says_where_it_ran(monkeypatch):
    from approvals import reach as where
    monkeypatch.setattr(transcripts, "is_copy", lambda *a: False)
    rec = make_rec(SAMPLES["money"], reach=seen_as("s-old", "mini-1", "2026-11-13T03:43:16+00:00"))
    new = seen_as("s-new", "m3-max", "2026-11-15T04:06:00+00:00")
    where.refresh(rec, new)
    assert rec["reach"]["session_id"] == "s-old"                      # the asker may still be running: not yet
    assert rec["events"][-1]["seen"] == new and core.successor(rec, [rec]) == new


def test_a_ref_found_by_chance_inside_a_longer_id_is_not_a_delivery(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text('{"uuid": "0f4d77abc1239e5b"}\n')
    assert transcripts.contains(str(t), "abc123") and not transcripts.typed(str(t), "abc123")
    t.write_text('{"content": "[Approvals] AP-0001 was approved (ref abc123)"}\n')
    assert transcripts.typed(str(t), "abc123")


def test_overdue_answers_and_exclusions(monkeypatch):
    from datetime import timedelta

    now = store.now()
    monkeypatch.setattr(store, "now", lambda: now)
    rec = make_rec(SAMPLES["money"])
    def add(ref, **fields):
        core.enqueue(rec, "feedback", "answer")
        item = rec["outbox"][-1]
        item.update(ref=ref, created=store.iso(now - timedelta(seconds=60)), **fields)
        return item

    add("error", last_error="Herdr failed")
    add("inbox", inbox_only=True)
    add("inbox_after_sent", inbox_only=True, last_error="sent, but not seen in the transcript yet")
    add("untouched")
    add("stopped", inbox_only=True, waiting_for_agent=True)
    add("transcript", last_error="sent, but not seen in the transcript yet", sent_at=store.iso(now))
    add("typed", typed_at=store.iso(now))
    add("sent", sent_at=store.iso(now))
    add("young", last_error="failed")["created"] = store.iso(now - timedelta(seconds=59))
    add("alarmed", last_error="failed", alarmed_at=store.iso(now))
    add("reviewer", to="reviewer", last_error="failed")
    add("deputy", to="deputy", last_error="failed")
    add("delivered", delivered_at=store.iso(now), last_error="failed")
    add("seen", seen_by_agent=True, last_error="failed")
    add("acknowledged", inbox_only=True, acked_at=store.iso(now))
    found = {item["ref"]: reason for _, item, reason in core.overdue([rec], now)}
    assert found == {"error": "Herdr failed", "inbox": "the answer can only be picked up from the agent's inbox",
                     "inbox_after_sent": "sent, but not seen in the transcript yet",
                     "untouched": "nothing has typed the answer into the agent's session"}

    first = make_rec(SAMPLES["money"], id="AP-0002", reach={"session_id": "same"})
    second = make_rec(SAMPLES["money"], id="AP-0003", reach={"session_id": "same"})
    for r in (first, second):
        core.enqueue(r, "decision", "yes")
        r["outbox"][0]["created"] = store.iso(now - timedelta(seconds=60))
    first["outbox"][0]["sent_at"] = store.iso(now)
    assert (second["id"], second["outbox"][0]["ref"]) in core.held([first, second])
    assert core.overdue([first, second], now) == []


def test_alarm_posts_and_wakes_once_and_skips_test_cards(monkeypatch):
    from datetime import timedelta

    now = store.now()
    monkeypatch.setattr(store, "now", lambda: now)
    calls, wakes = [], []
    monkeypatch.setattr(listener.slackapi, "message_url", lambda ts: "https://slack.test/thread/" + ts)
    def post(blocks, text, channel_id):
        assert any(i.get("alarmed_at") for i in store.load(text.split(" on ")[1].split()[0])["outbox"])
        calls.append((text, channel_id))
    monkeypatch.setattr(listener.slackapi, "post", post)
    monkeypatch.setattr(listener.subprocess, "run", lambda cmd, **kw: wakes.append((cmd, kw)))
    rec = make_rec(SAMPLES["money"], reach={"machine": "mini-5"})
    core.enqueue(rec, "feedback", "answer")
    rec["outbox"][0].update(created=store.iso(now - timedelta(seconds=75)), last_error="Herdr failed")
    core.enqueue(rec, "feedback", "another")
    rec["outbox"][1].update(created=store.iso(now - timedelta(seconds=75)), last_error="Another failure")
    store.save(rec)
    test_rec = make_rec(SAMPLES["money"], id="AP-0002", test=True)
    core.enqueue(test_rec, "feedback", "test")
    test_rec["outbox"][0].update(created=store.iso(now - timedelta(seconds=75)), last_error="test failure")
    store.save(test_rec)
    L = listener.Listener()
    L.alarm()
    assert not calls and not wakes and not store.load(rec["id"])["outbox"][0].get("alarmed_at")
    L.s["alarm"] = {"channel": "COPS", "wake": ["~/wake", "--owner"]}
    L.alarm()
    L.alarm()
    assert len(calls) == len(wakes) == 2
    line, channel = calls[0]
    assert channel == "COPS" and line.startswith("🚨 Sam's answer on AP-0001")
    assert rec["title"] in line and "Tester" in line and "75 seconds" in line and "Herdr failed" in line
    assert "https://slack.test/thread/1.1" in line
    cmd, kw = wakes[0]
    assert cmd == [listener.os.path.expanduser("~/wake"), "--owner"]
    assert kw["timeout"] == 60 and kw["check"] and kw["text"]
    assert all(word in kw["input"] for word in ("not a person", "AP-0001", "Tester", "mini-5", "Herdr failed",
                                                "safe on the hub", "approvals inbox", "~/.approvals/logs/listener.log"))
    assert all(i.get("alarmed_at") for i in store.load(rec["id"])["outbox"])
    assert not store.load(test_rec["id"])["outbox"][0].get("alarmed_at")
    monkeypatch.setenv("APPROVALS_TEST", "1")
    L.alarm()
    assert len(calls) == len(wakes) == 3


def test_alarm_failures_do_not_repeat_or_stop_other_alarms(monkeypatch, caplog):
    from datetime import timedelta

    rec = make_rec(SAMPLES["money"])
    for _ in range(2):
        core.enqueue(rec, "feedback", "answer")
        rec["outbox"][-1].update(created=store.iso(store.now() - timedelta(seconds=75)), last_error="failed")
    store.save(rec)
    posts, wakes = [], []
    monkeypatch.setattr(listener.slackapi, "message_url", lambda ts: "link")
    def post(*args, **kw):
        posts.append(1)
        raise RuntimeError("post failed")
    def wake(*args, **kw):
        wakes.append(1)
        raise subprocess.TimeoutExpired("wake", 60)
    monkeypatch.setattr(listener.slackapi, "post", post)
    monkeypatch.setattr(listener.subprocess, "run", wake)
    L = listener.Listener()
    L.s["alarm"] = {"channel": "COPS", "wake": ["wake"]}
    L.alarm()
    L.alarm()
    assert len(posts) == len(wakes) == 2
    assert "alarm post" in caplog.text and "alarm wake" in caplog.text


def test_delivery_worker_records_exception(monkeypatch):
    rec = make_rec(SAMPLES["money"])
    core.enqueue(rec, "feedback", "answer")
    store.save(rec)
    ref = rec["outbox"][0]["ref"]
    L = listener.Listener()
    def fail(rid, ref):
        L.stop.set()
        raise RuntimeError("Herdr is broken")
    monkeypatch.setattr(L, "deliver_one", fail)
    L.queue([(rec["id"], ref)])
    L.delivery_worker()
    assert store.load(rec["id"])["outbox"][0]["last_error"] == "RuntimeError: Herdr is broken"


def test_a_missing_herdr_is_never_read_as_a_closed_pane():
    def missing(*args, **kw):
        raise FileNotFoundError("herdr")
    h = router.Herdr("default", run=missing)
    for ask in (lambda: h.call("pane", "get", "p1"), lambda: h.pane("p1")):
        with pytest.raises(RuntimeError, match="herdr isn't on this service's PATH on testbox") as e:
            ask()
        assert not isinstance(e.value, router.HerdrError)


def test_alarm_posts_for_a_card_with_no_slack_thread(monkeypatch):
    """AP-1942 (November 21, 2026) was made without a Slack post; its alarm crashed building the thread link."""
    from datetime import timedelta

    now = store.now()
    monkeypatch.setattr(store, "now", lambda: now)
    calls = []
    monkeypatch.setattr(listener.slackapi, "post", lambda blocks, text, channel_id: calls.append(text))
    monkeypatch.setattr(listener.subprocess, "run", lambda cmd, **kw: None)
    rec = make_rec(SAMPLES["money"], reach={"machine": "studio"})
    rec["thread_ts"] = None
    core.enqueue(rec, "feedback", "answer")
    rec["outbox"][0].update(created=store.iso(now - timedelta(seconds=75)), last_error="no session")
    store.save(rec)
    L = listener.Listener()
    L.s["alarm"] = {"channel": "COPS", "wake": ["~/wake"]}
    L.alarm()
    assert len(calls) == 1 and "AP-0001" in calls[0] and "no session" in calls[0]


def test_refresh_skips_the_slack_card_when_a_card_has_no_thread(monkeypatch):
    """AP-1942 had no Slack post; its refresh failed on every pass with Slack's "missing required field: ts"."""
    updates = []
    monkeypatch.setattr(core.slackapi, "update", lambda ts, *a, **k: updates.append(ts))
    rec = make_rec(SAMPLES["money"])
    rec["thread_ts"] = None
    rec["card_dirty"] = True
    store.save(rec)
    core.refresh_and_clear(rec["id"])
    assert None not in updates and not store.load(rec["id"])["card_dirty"]
