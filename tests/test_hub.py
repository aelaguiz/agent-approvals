"""One machine holds the records; the others reach them over HTTP and deliver to their own sessions.

Each test runs a real hub on localhost and calls it as another machine would: this process says it is the
machine "laptop", the settings name "hubbox" as the hub, and the hub's handlers write the files of the test's
own folder.
"""
import io
import json
from contextlib import redirect_stdout

import pytest

from approvals import cli, config, core, hub, listener, reach, router, slackapi, store, transcripts
from samples import SAMPLES
from test_cards_store import make_rec


@pytest.fixture
def hubbox(monkeypatch):
    srv = hub.serve(port=0)
    port = srv.server_address[1]
    s = json.loads(config.CONFIG.read_text())
    s.update(hub="hubbox", hub_url=f"http://127.0.0.1:{port}")
    config.CONFIG.write_text(json.dumps(s))
    monkeypatch.setenv("APPROVALS_MACHINE", "laptop")
    yield srv
    srv.shutdown()
    srv.server_close()


def as_hub(monkeypatch):
    monkeypatch.setenv("APPROVALS_MACHINE", "hubbox")


def card_rec(machine=None, **kw):
    rec = make_rec(SAMPLES["money"], **kw)
    rec.pop("id")
    rec["reach"] = {"runtime": "claude", "session_id": f"sid-{machine}", "agent": "Tester"}
    if machine:
        rec["reach"]["machine"] = machine
    core.enqueue(rec, "decision", "approved", react_ts="1.1")
    return store.create(rec)


def test_records_made_on_another_machine_are_kept_on_the_hub(hubbox):
    rec = store.create(make_rec(SAMPLES["money"]) | {"id": None})
    assert rec["id"] == "AP-0001"
    assert (config.REQUESTS / "AP-0001.json").exists()      # the hub's file
    assert store.load("AP-0001")["title"] == rec["title"]
    assert [r["id"] for r in store.all_records()] == ["AP-0001"]
    store.delete("AP-0001")
    assert store.all_records() == []
    with pytest.raises(FileNotFoundError):
        store.load("AP-0001")


def test_two_machines_changing_one_record_both_land(hubbox):
    rid = store.create(make_rec(SAMPLES["money"]))["id"]
    calls = []

    def mine(rec):
        calls.append(1)
        if len(calls) == 1:   # another machine writes in between this one's read and its write
            store.update(rid, lambda r: r.__setitem__("title", "changed elsewhere"))
        rec["status"] = "approved"
        return "ok"
    assert store.update(rid, mine) == "ok"
    saved = store.load(rid)
    assert len(calls) == 2 and saved["title"] == "changed elsewhere" and saved["status"] == "approved"


def test_a_machine_without_the_bot_token_is_refused(hubbox, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-someone-else")
    with pytest.raises(store.StoreError, match="refused"):
        store.all_records()


def test_a_hub_that_is_down_says_so(hubbox):
    s = json.loads(config.CONFIG.read_text())
    s["hub_url"] = "http://127.0.0.1:9"
    config.CONFIG.write_text(json.dumps(s))
    with pytest.raises(store.StoreError, match="isn't answering"):
        store.load("AP-0001")


def test_each_machine_is_given_only_its_own_sessions_answers(hubbox):
    a = card_rec("laptop")["id"]
    b = card_rec("hubbox")["id"]
    c = card_rec(None)["id"]      # a card from before machines had names: the hub's
    client = store.hub_client()
    assert [x[0] for x in client.due("laptop")[0]] == [a]
    assert sorted(x[0] for x in client.due("hubbox")[0]) == sorted([b, c])
    assert client.due("elsewhere") == ([], [])


def test_on_one_machine_every_card_is_its_own_whatever_name_the_mac_gave(monkeypatch):
    """Without a hub there is one machine, and a Mac's own name follows its network."""
    monkeypatch.setenv("APPROVALS_MACHINE", "Sams-Laptop")
    rid = card_rec("Mac")["id"]
    assert core.to_deliver(store.all_records(), config.machine())[0] == [[rid, store.load(rid)["outbox"][0]["ref"]]]


def test_a_card_names_the_machine_it_was_asked_from(monkeypatch):
    monkeypatch.setenv("APPROVALS_MACHINE", "laptop")
    assert reach.capture()["machine"] == "laptop"


def test_off_the_hub_the_service_delivers_to_its_own_sessions(hubbox, monkeypatch):
    rid = card_rec("laptop")["id"]
    sent, reacted = [], []
    monkeypatch.setattr(router, "deliver", lambda r, text, ref, request_id=None: sent.append((r["session_id"], ref))
                        or router.Result("delivered", "seen in the transcript"))
    monkeypatch.setattr(slackapi, "react", lambda ts, name: reacted.append(name))
    L = listener.Listener()
    L.fetch()
    L.deliver_one(*L.deliveries.get_nowait())
    assert sent == [("sid-laptop", store.load(rid)["outbox"][0]["ref"])]
    assert store.load(rid)["outbox"][0]["delivered_at"] and reacted == ["incoming_envelope"]
    L.in_flight.clear()
    L.fetch()
    assert L.deliveries.empty()      # delivered once


def test_an_answer_whose_session_moved_is_delivered_where_the_session_is(hubbox, monkeypatch):
    """The card says hubbox, but the session is now on laptop: hubbox can't deliver it, laptop can."""
    rid = card_rec("hubbox")["id"]
    notes, sent = [], []
    monkeypatch.setattr(slackapi, "note", lambda ts, text: notes.append(text))
    monkeypatch.setattr(slackapi, "react", lambda ts, name: None)

    as_hub(monkeypatch)
    monkeypatch.setattr(transcripts, "transcript", lambda runtime, sid: None)
    H = listener.Listener()
    H.sweep()
    H.deliver_one(*H.deliveries.get_nowait())
    item = store.load(rid)["outbox"][0]
    assert item["stray"] and item["inbox_only"] and not item["delivered_at"]
    assert "Whichever machine has the session will deliver it" in notes[0]

    monkeypatch.setenv("APPROVALS_MACHINE", "laptop")
    monkeypatch.setattr(transcripts, "transcript", lambda runtime, sid: f"/t/{sid}.jsonl" if sid == "sid-hubbox" else None)
    monkeypatch.setattr(router, "deliver", lambda r, text, ref, request_id=None: sent.append(ref) or router.Result("delivered"))
    L = listener.Listener()
    L.fetch()
    L.deliver_one(*L.deliveries.get_nowait())
    rec = store.load(rid)
    assert sent == [item["ref"]] and rec["outbox"][0]["delivered_at"] and rec["reach"]["machine"] == "laptop"
    assert [e["kind"] for e in rec["events"]][-2:] == ["claimed", "delivered"]


def test_inbox_prints_each_answer_once_when_the_record_changed_meanwhile(hubbox, monkeypatch):
    rid = card_rec("laptop")["id"]
    put = hub.Client.put
    raced = []

    def racing_put(self, rec, rev=None):
        if not raced:     # the hub's service touches the record between this read and write
            raced.append(1)
            store.update(rid, lambda r: r.__setitem__("last_seen", "9.9"))
        return put(self, rec, rev)
    monkeypatch.setattr(hub.Client, "put", racing_put)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-laptop")
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["inbox"])
    assert out.getvalue().count("(ref ") == 1
    assert store.load(rid)["outbox"][0]["delivered_via"] == "inbox" and store.load(rid)["last_seen"] == "9.9"


def test_a_machine_that_cannot_post_to_the_workspace_saves_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    card = tmp_path / "card.json"
    card.write_text(json.dumps(SAMPLES["money"]))
    with pytest.raises(SystemExit):
        cli.main(["submit", str(card)])
    assert "can't post" in capsys.readouterr().err and store.all_records() == []


def test_role_says_what_the_service_does_here(hubbox, monkeypatch, capsys):
    cli.main(["role"])
    as_hub(monkeypatch)
    cli.main(["role"])
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    cli.main(["role"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == ["delivery", "hub"] and lines[2].startswith("none: token file not found")
