"""A pod member is its tab: answers, the deputy's and reviewer's roles and the actor follow the session in it."""
import json
from types import SimpleNamespace

import pytest

from approvals import cli, core, pods, reach, router, store, transcripts
from samples import SAMPLES
from test_cards_store import make_rec
from test_router_listener import FakeHerdr, run, world  # noqa: F401  (fixture)

# What `./pod list` prints, as it stands (psbrain's list_members): the second copy runs without its pod name.
LIST = """\
content pod: runs on Mini 3; ./pod list content shows it there
b2 pod: "b2 pod agents" open (w1), 2 of 3 running
  writer    writer  opus xhigh  first "Take your next piece, then say "running in w9:p9, on x, session no, ok"."  running in w1:p1, on pro3, session SID-1, idle
  writer-2  writer  opus xhigh  running in w1:p2, without its pod name, on pro4, session sid-2, working
  editor    editor  sonnet high   not running
  thread-ab  "a fork"  open in w1:p4, on pro5, session sid-4, idle
"""


@pytest.fixture
def pod(tmp_path):
    """A pods root whose ./pod prints LIST, with the writer's folder in it."""
    root = tmp_path / "brain"
    (root / "pods" / "b2" / "writer" / "work").mkdir(parents=True)
    (root / "list.txt").write_text(LIST)
    cmd = root / "pod"
    cmd.write_text(f"#!/bin/sh\n[ \"$1\" = list ] && cat {root}/list.txt && echo \"$POD_HERDR_SESSION\" > {root}/session\n")
    cmd.chmod(0o755)
    return root


def test_a_member_is_found_by_its_tab_and_nothing_else_is(pod, monkeypatch):
    folder = pod / "pods" / "b2" / "writer"
    assert pods.command(folder / "work") == (str(pod / "pod"), "b2", str(folder))
    assert pods.command(pod / "pods" / "b2") is None
    assert pods.listed(str(pod / "pod")) == {("b2", "writer"): ("sid-1", "w1:p1"), ("b2", "writer-2"): ("sid-2", "w1:p2")}
    monkeypatch.chdir(folder / "work")
    monkeypatch.setenv("HERDR_SESSION", "mine")
    for pane, sid, seat in (("w1:p2", "sid-2", "writer-2"),   # the second copy, in its own tab, without its name
                            ("w5:p1", "sid-2", None),          # a chat in the folder, in another workspace
                            ("w1:p1", "sid-9", None)):         # a helper started from the member's shell
        monkeypatch.setenv("HERDR_PANE_ID", pane)
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)
        pods.here.cache_clear()
        got = pods.here()
        assert (got or {}).get("copy") == seat and (pods.name(got) == f"b2-{seat}" if seat else got is None)
    assert (pod / "session").read_text().strip() == "mine"     # ./pod reads the member's own Herdr session


SEAT = {"pod_command": "/b/pod", "herdr_session": "brain", "pod": "b2", "copy": "writer"}


@pytest.fixture
def asker():
    return {"runtime": "claude", "session_id": "sid-1", "host": "h", "herdr_session": "brain", "herdr_workspace": "w1",
            "herdr_pane": "w1:p1", "cwd": "/tmp/x", "agent": "B2 pod · writer"}


def in_tab(monkeypatch, tab):
    monkeypatch.setattr(pods, "listed", lambda cmd, hs=None: {("b2", "writer"): tab} if tab else {})


def test_an_answer_goes_to_the_session_in_the_members_tab_and_never_starts_one(asker, world, monkeypatch):
    world.pid_sid = {1: "sid-1", 2: "sid-2"}
    asked = dict(asker, seat=SEAT)
    # Restarted by hand in its tab: a fresh session with none of the old one's history
    in_tab(monkeypatch, ("sid-2", "w1:p2"))
    fake = FakeHerdr(panes={"w1:p2": {}}, procs={"w1:p2": [{"pid": 2}]})
    res = run(world, fake, asked)
    assert res.status == "delivered" and fake.sent_to("w1:p2") and res.moves == [["sid-1", "sid-2"]]
    assert res.reach["session_id"] == "sid-2" and res.reach["herdr_pane"] == "w1:p2" and not fake.resumes()
    # Its tab closed: a reply waits, even for agent-email, which resumes any other closed session
    in_tab(monkeypatch, None)
    fake = FakeHerdr(panes={"w1:p1": {}}, procs={"w1:p1": [{"pid": 1}]})
    res = run(world, fake, asked)
    assert res.status == "waiting" and not fake.calls
    # The old session still running elsewhere is not the member
    assert run(world, FakeHerdr(panes={}), asked, resume=True).status == "waiting"
    # A fork in the tab never takes its parent's answers
    in_tab(monkeypatch, ("sid-2", "w1:p2"))
    store.add_fork("sid-2")
    assert run(world, FakeHerdr(panes={"w1:p2": {}}, procs={"w1:p2": [{"pid": 2}]}), asked).status == "waiting"


def test_a_card_from_before_seats_finds_its_member_by_the_pane_it_was_asked_from(asker, world, pod):
    world.pid_sid = {2: "sid-2"}
    asked = dict(asker, cwd=str(pod / "pods" / "b2" / "writer"), herdr_pane="w1:p2")
    fake = FakeHerdr(panes={"w1:p2": {}}, procs={"w1:p2": [{"pid": 2}]})
    res = run(world, fake, asked)
    assert res.status == "delivered" and res.reach["seat"]["copy"] == "writer-2" and res.reach["session_id"] == "sid-2"


def member(monkeypatch, sid, seat, agent="B2 pod · writer"):
    me = {"agent": agent, "runtime": "claude", "session_id": sid, "cwd": "/b/pods/b2/writer", "machine": "testbox",
          "herdr_session": "brain", "herdr_pane": "w1:p1", "seat": seat}
    monkeypatch.setattr(reach, "capture", lambda agent=None: dict(me))
    return me


def test_the_member_in_the_tab_takes_its_waiting_answers_and_its_roles_and_a_chat_in_its_folder_does_not(monkeypatch, capsys):
    rec = make_rec(SAMPLES["money"], agent="B2 pod · writer",
                   reach={"runtime": "claude", "session_id": "sid-1", "agent": "B2 pod · writer", "seat": SEAT})
    core.enqueue(rec, "feedback", "Amir replied: make it $15,000")
    rec["outbox"][0].update(inbox_only=True, waiting_for_agent=True)
    store.save(rec)
    monkeypatch.setattr(cli.slackapi, "react", lambda *a, **k: None)
    store.set_deputy({"reach": {"agent": "B2 pod · writer", "runtime": "claude", "session_id": "sid-1", "seat": SEAT},
                      "live": True, "test": False, "since": store.iso(), "live_since": None})
    for sid, seat in (("sid-chat", None), ("sid-copy2", dict(SEAT, copy="writer-2"))):
        member(monkeypatch, sid, seat)
        cli.cmd_inbox(SimpleNamespace(agent=None, all=False, peek=False))
        assert "No approval answers waiting" in capsys.readouterr().out
        with pytest.raises(SystemExit):
            cli.cmd_call(SimpleNamespace(id="AP-0001", approve=True, his=False, why="fine", also=None, urgent=False))
    member(monkeypatch, "sid-2", dict(SEAT, pod_command="/elsewhere/pod"))
    cli.cmd_inbox(SimpleNamespace(agent=None, all=False, peek=False))
    assert "make it $15,000" in capsys.readouterr().out
    assert store.deputy()["reach"]["session_id"] == "sid-2" and store.deputy()["live"]


def test_a_command_from_the_session_in_the_tab_takes_the_card_and_a_chat_in_its_folder_is_only_noted():
    rec = make_rec(SAMPLES["money"], reach={"runtime": "claude", "session_id": "sid-1", "agent": "B2 pod · writer",
                                            "seat": SEAT, "herdr_pane": "w1:p1"})
    reach.refresh(rec, {"runtime": "claude", "session_id": "sid-chat", "agent": "B2 pod · writer", "seat": None})
    assert rec["reach"]["session_id"] == "sid-1" and rec["events"][-1]["kind"] == "used_by_other_session"
    reach.refresh(rec, {"runtime": "claude", "session_id": "sid-2", "agent": "B2 pod · writer", "seat": dict(SEAT)})
    assert rec["reach"]["session_id"] == "sid-2" and rec["reach"]["earlier_ids"] == ["sid-1"]
    assert rec["events"][-1]["kind"] == "taken_over"
