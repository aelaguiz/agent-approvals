"""Fork sessions only share answers with other sessions of their own family."""
import pytest

from approvals import cli, config, core, reach, router, store, transcripts
from samples import SAMPLES
from test_cards_store import make_rec
from test_copies import write, write_continuation
from test_router_listener import FakeHerdr


@pytest.fixture
def forked(tmp_path, monkeypatch):
    home = tmp_path / ".claude"
    project = home / "projects" / "-tmp-x"
    monkeypatch.setattr(transcripts, "claude_config_dirs", lambda: [home])
    write(project / "lead.jsonl", "Handle the conversation.", "Reading it now.", "AP-0001 and AP-0002.")
    for sid in ("lead-moved", "fork", "fork-moved", "other-fork"):
        (project / f"{sid}.jsonl").write_text((project / "lead.jsonl").read_text())
    store.add_fork("fork")
    return project


def where(sid):
    return {"runtime": "claude", "session_id": sid, "cwd": "/tmp/x", "agent": "Worker",
            "host": config.hostname(), "machine": config.machine(), "herdr_session": "brain", "herdr_pane": "w1:gone"}


def deliver_to(monkeypatch, asker, candidate, recorded=False):
    fake = FakeHerdr(panes={"w1:p2": {}}, procs={"w1:p2": [{"pid": 1}]})
    monkeypatch.setattr(transcripts, "claude_session_for_pid", lambda pid: candidate)
    monkeypatch.setattr(transcripts, "live_sessions", lambda runtime: [(1, candidate, "/tmp/x")])
    asker = where(asker)
    if recorded:
        asker["herdr_pane"] = "w1:p2"
    result = router.deliver(asker, "Answer (ref test-answer)", "test-answer", request_id="AP-0001",
                            run=fake, resume=False, confirm=False)
    return result, fake


@pytest.mark.parametrize("asker,candidate", [("lead", "fork"), ("fork", "lead"),
                                               ("lead-moved", "fork"), ("fork", "lead-moved")])
@pytest.mark.parametrize("recorded", [False, True])
def test_fork_and_unregistered_sessions_cannot_receive_each_others_answers(forked, monkeypatch, asker, candidate, recorded):
    result, fake = deliver_to(monkeypatch, asker, candidate, recorded)
    assert result.status == "waiting" and result.moves == []
    assert not fake.sent_to("w1:p2") and not fake.resumes()


def test_fork_owns_its_answers_and_registered_later_id_can_receive_them(forked, monkeypatch):
    result, fake = deliver_to(monkeypatch, "fork", "fork")
    assert result.status == "sent" and fake.sent_to("w1:p2")
    result, fake = deliver_to(monkeypatch, "fork", "fork-moved")
    assert result.status == "waiting" and not fake.sent_to("w1:p2")
    store.add_fork("fork-moved", "fork")
    result, fake = deliver_to(monkeypatch, "fork", "fork-moved")
    assert result.status == "sent" and result.moves == [["fork", "fork-moved"]]
    assert fake.sent_to("w1:p2")
    write_continuation(forked / "fork-continued.jsonl", str(forked / "fork.jsonl"))
    assert not transcripts.is_copy("claude", "fork", "fork-continued", "AP-0001")
    store.add_fork("fork-continued", "fork-moved")
    assert transcripts.is_copy("claude", "fork", "fork-continued", "AP-0001")


def test_unregistered_copies_still_work_and_separate_forks_do_not_match(forked, monkeypatch):
    assert transcripts.is_copy("claude", "lead", "lead-moved", "AP-0001")
    store.add_fork("other-fork")
    assert not transcripts.is_copy("claude", "fork", "other-fork", "AP-0001")
    assert "other-fork" not in transcripts.claude_copies("fork", "/tmp/x", "AP-0001")
    result, fake = deliver_to(monkeypatch, "fork", "other-fork")
    assert result.status == "waiting" and not fake.sent_to("w1:p2")


@pytest.mark.parametrize("asker,other", [("lead-moved", "fork"), ("fork", "lead-moved")])
@pytest.mark.parametrize("earlier", [False, True])
def test_inbox_and_refresh_never_take_another_familys_answers(forked, monkeypatch, capsys, asker, other, earlier):
    rec = make_rec(SAMPLES["money"], reach=where(asker))
    if earlier:
        rec["reach"]["earlier_ids"] = [other]
    core.enqueue(rec, "feedback", "Please revise the wording.")
    rec["outbox"][0]["inbox_only"] = True
    store.save(rec)
    reach.refresh(rec, where(other))
    assert rec["reach"]["session_id"] == asker
    assert not reach.take_over(rec, asker, where(other))
    monkeypatch.setattr(reach, "capture", lambda agent=None: where(other))
    cli.main(["inbox", "--agent", "Worker"])
    assert "No approval answers waiting" in capsys.readouterr().out
    assert not store.load(rec["id"])["outbox"][0].get("delivered_at")
    cli.main(["inbox", "--all"])
    assert "Please revise the wording." in capsys.readouterr().out


def test_earlier_id_of_another_family_does_not_suppress_delivery(forked, monkeypatch):
    monkeypatch.setattr(transcripts, "transcript", lambda runtime, sid: sid)
    monkeypatch.setattr(transcripts, "contains", lambda path, ref: path == "fork")
    assert not router.already_delivered({"runtime": "claude", "session_id": "lead", "earlier_ids": ["fork"]}, "ref")


def test_repoint_does_not_move_cards_or_roles_between_families(forked, monkeypatch):
    rec = make_rec(SAMPLES["money"], reach=where("fork"))
    store.save(rec)
    role = {"reach": where("fork"), "types": ["money"], "live": False}
    store.set_reviewer(role)
    store.set_deputy(role)
    monkeypatch.setattr(reach, "running", lambda sid: where(sid) if sid == "lead-moved" else None)
    cli.main(["repoint", "fork", "lead-moved"])
    assert store.load(rec["id"])["reach"]["session_id"] == "fork"
    assert store.reviewer()["reach"]["session_id"] == store.deputy()["reach"]["session_id"] == "fork"


def test_registration_is_idempotent_and_workspace_specific(forked, capsys):
    before = config.FORKS.read_bytes()
    cli.main(["fork", "fork", "--same-as", "missing"])
    assert config.FORKS.read_bytes() == before
    cli.main(["fork", "fork-moved", "--same-as", "fork"])
    capsys.readouterr()
    cli.main(["fork"])
    assert capsys.readouterr().out == "fork fork\nfork-moved fork\n"
    config.use(config.HOME / "another-workspace")
    assert store.forks() == {}
    with pytest.raises(SystemExit):
        cli.main(["fork", "unknown", "--same-as", "missing"])


def test_registry_cache_expires_and_is_keyed_by_workspace(forked, monkeypatch, tmp_path):
    clock = [100.0]
    monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])
    store._fork_cache.clear()
    reads, read = [], store.local_forks
    def counted():
        reads.append(config.HOME)
        return read()
    monkeypatch.setattr(store, "local_forks", counted)
    first = config.HOME
    assert store.forks() == store.forks() == {"fork": "fork"}
    assert len(reads) == 1
    clock[0] += 14.99
    assert store.forks() == {"fork": "fork"} and len(reads) == 1
    config.use(tmp_path / "other")
    assert store.forks() == {} and len(reads) == 2
    config.use(first)
    clock[0] += .01
    assert store.forks() == {"fork": "fork"} and len(reads) == 3


@pytest.mark.parametrize("args", [["fork", "--same-as", "fork"], ["inbox", "--peek"], ["inbox", "--session", "fork"]])
def test_incomplete_commands_are_refused(args):
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2
