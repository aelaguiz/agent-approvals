import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from approvals import cli, config, core, digest, listener, reach, slackapi, store
from samples import SAMPLES
from test_cards_store import make_rec
from test_sets import card_files


@pytest.fixture
def named():
    s = json.loads(config.CONFIG.read_text())
    s["approvers"] = {"alex": {"channel": "CALEX", "approver": "UALEX", "approver_name": "Alex"}}
    config.CONFIG.write_text(json.dumps(s))


def test_submit_set_targets_named_person_and_rejects_unknown(named, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-asker")
    posts = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, **kw: posts.append((blocks, kw)) or f"{len(posts)}.0")
    monkeypatch.setattr(slackapi, "permalink", lambda ts, **kw: f"https://slack.test/{kw.get('channel_id')}/{ts}")
    paths = card_files(tmp_path, 2)
    with pytest.raises(SystemExit):
        cli.main(["submit", *paths, "--to", "missing"])
    assert "configured names: alex" in capsys.readouterr().err
    assert not posts and not store.all_records()
    cli.main(["submit", *paths, "--to", "alex"])
    records = store.all_records()
    assert len(records) == 2 and all(r["target"] == "alex" for r in records)
    assert all(p[1]["channel_id"] == "CALEX" for p in posts)
    assert all("/CALEX/" in r["link"] for r in records)


def test_click_popup_and_reply_only_count_named_person(named, monkeypatch):
    rec = make_rec(SAMPLES["money"], target="alex")
    store.save(rec)
    notices = []
    monkeypatch.setattr(slackapi, "ephemeral", lambda user, text, thread_ts=None, **kw: notices.append((text, kw)))
    L = listener.Listener()
    def press(user):
        return L.handle_click({"user": {"id": user}, "actions": [{"value": "AP-0001|1|approve"}],
                               "channel": {"id": "CALEX"}})
    press("UAPPROVER")()
    assert notices[0][0] == "Only Alex can decide approvals."
    assert store.load(rec["id"])["status"] == "pending"
    view = {"callback_id": "approvals", "private_metadata": "AP-0001|1|change", "state": {"values": {"words": {"words": {"value": "fix"}}}}}
    response, _ = L.handle_submission({"user": {"id": "UAPPROVER"}, "view": view})
    assert response["errors"]["words"] == "Only Alex can decide approvals."
    assert not listener.is_approver({"user": "UAPPROVER"}, rec)
    assert listener.is_approver({"user": "UALEX"}, rec)
    response, follow_up = L.handle_submission({"user": {"id": "UALEX"}, "view": view})
    assert response is None and follow_up is not None
    assert store.load(rec["id"])["status"] == "revising"
    store.save(rec)
    assert press("UALEX") is not None
    result = store.load(rec["id"])
    assert result["status"] == "approved" and "Alex approved" in result["outbox"][0]["text"]


def test_legacy_and_missing_target(named, monkeypatch):
    old = make_rec(SAMPLES["money"])
    assert listener.is_approver({"user": "UAPPROVER"}, old)
    channels = []
    client = SimpleNamespace(chat_update=lambda **kw: channels.append(kw["channel"]))
    monkeypatch.setattr(slackapi, "client", lambda: client)
    core.refresh_cards(old)
    assert channels == ["CTEST"]
    missing = dict(old, target="missing")
    with pytest.raises(config.ConfigError, match="Unknown approver"):
        core.refresh_cards(missing)
    assert channels == ["CTEST"]


def test_deputy_does_not_hear_or_call_other_persons_card(named, monkeypatch, capsys):
    deputy = {"reach": {"agent": "Deputy", "session_id": "sid-dep", "machine": "testbox"}, "live": True, "test": False}
    store.set_deputy(deputy)
    monkeypatch.setattr(reach, "capture", lambda *a: {"agent": "Deputy", "session_id": "sid-dep", "machine": "testbox"})
    rec = make_rec(SAMPLES["money"], target="alex", reach={"session_id": "sid-asker"})
    store.save(rec)
    store.update(rec["id"], lambda r: core.tell_deputy(r, "new card"))
    assert not store.load(rec["id"])["outbox"]
    assert core.call(rec, "approve", "ok", "Deputy", True) == "this card is for a different approver"
    cli.main(["show", rec["id"]])
    assert rec["id"] in capsys.readouterr().out
    cli.main(["history"])
    assert rec["id"] in capsys.readouterr().out


def test_morning_and_requested_lists_are_separate(named, monkeypatch):
    now = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
    old = make_rec(SAMPLES["money"], id="AP-0001", deadline=store.iso(now))
    new = make_rec(SAMPLES["money"], id="AP-0002", target="alex", deadline=store.iso(now), thread_ts="2.0")
    store.save(old)
    store.save(new)
    posts = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, **kw: posts.append((blocks, text, kw)) or f"{len(posts)}.0")
    monkeypatch.setattr(slackapi, "update", lambda *a, **kw: None)
    monkeypatch.setattr(slackapi, "message_url", lambda ts, **kw: f"https://slack.test/{kw.get('channel_id', 'CTEST')}/{ts}")
    digest.tick(now)
    digest.tick(now, target="alex")
    assert len(posts) == 2
    assert "<@UAPPROVER>" in posts[0][1] and "AP-0001" in json.dumps(posts[0][0]) and "AP-0002" not in json.dumps(posts[0][0])
    assert "<@UALEX>" in posts[1][1] and "AP-0002" in json.dumps(posts[1][0]) and "AP-0001" not in json.dumps(posts[1][0])
    assert posts[1][2]["channel_id"] == "CALEX"
    digest.tick(now, asked=True, target="alex")
    assert len(posts) == 3 and "<@UALEX>" not in posts[2][1]
    assert digest.load_state()["ts"] == "1.0" and digest.load_state("alex")["ts"] == "3.0"
    assert (config.HOME / "digest-alex.json").exists()


def test_replies_are_read_from_named_channel_and_only_from_named_person(named, monkeypatch):
    rec = make_rec(SAMPLES["money"], target="alex")
    store.save(rec)
    reads = []
    monkeypatch.setattr(slackapi, "replies", lambda ts, oldest=None, **kw: reads.append(kw) or [
        {"ts": "2.0", "user": "UAPPROVER", "text": "wrong person"},
        {"ts": "3.0", "user": "UALEX", "text": "please revise"}])
    monkeypatch.setattr(listener.Listener, "try_refresh", lambda *a: None)
    listener.Listener().poll_thread(rec)
    out = store.load(rec["id"])["outbox"]
    assert reads == [{"channel_id": "CALEX"}]
    assert len(out) == 1 and "Alex replied" in out[0]["text"] and "wrong person" not in out[0]["text"]


def test_requested_list_uses_approver_and_channel(named, monkeypatch):
    calls = []
    monkeypatch.setattr(digest, "load_state", lambda target=None: {"ts": "1.0"})
    monkeypatch.setattr(slackapi, "history", lambda ts, **kw: [{"user": "UALEX", "ts": "2.0"}])
    monkeypatch.setattr(digest, "tick", lambda **kw: calls.append(kw))
    listener.Listener().lists()
    assert calls == [{"asked": False}, {"asked": True, "target": "alex"}]


def test_named_person_is_recognized_in_source_thread(named, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-asker")
    posts = []
    monkeypatch.setattr(slackapi, "thread_link", lambda link: {"channel": "CSOURCE", "root_ts": "7.0", "link": link})
    monkeypatch.setattr(slackapi, "read_thread", lambda thread: thread.update(readable=True, user="UALEX", text="My request"))
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, **kw: posts.append((blocks, kw)) or f"{len(posts)}.0")
    monkeypatch.setattr(slackapi, "permalink", lambda ts, **kw: "https://slack.test/card")
    cli.main(["submit", *card_files(tmp_path, 1), "--to", "alex", "--thread", "https://slack.test/source"])
    rec = store.all_records()[0]
    assert rec["thread"]["approver"] and "user" not in rec["thread"]
    assert posts[0][1]["channel_id"] == "CALEX" and posts[1][1]["channel_id"] == "CSOURCE"


def test_socket_watches_named_channel_and_keeps_threads_in_each_channel_distinct(named, monkeypatch):
    default = make_rec(SAMPLES["money"], id="AP-0001", thread_ts="1.1")
    other = make_rec(SAMPLES["money"], id="AP-0002", target="alex", thread_ts="1.1")
    store.save(default)
    store.save(other)
    L = listener.Listener()
    client = SimpleNamespace(send_socket_mode_response=lambda response: None)
    def event(channel):
        return SimpleNamespace(type="events_api", envelope_id="event", payload={"event": {
            "type": "message", "channel": channel, "user": "UALEX", "thread_ts": "1.1"}})
    L.on_socket(client, event("CUNSERVED"))
    assert not L.poke
    L.on_socket(client, event("CALEX"))
    assert L.poke == {("CALEX", "1.1")}
    polled = []
    monkeypatch.setattr(L, "poll_thread", lambda rec: polled.append(rec["id"]))
    L.poll_once()
    assert set(polled) == {"AP-0001", "AP-0002"} and polled[0] == "AP-0002"
