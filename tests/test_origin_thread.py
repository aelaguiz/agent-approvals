import json
from datetime import timedelta

import pytest

from approvals import cards, cli, config, core, digest, listener, slackapi, store
from samples import SAMPLES
from test_cards_store import make_rec

SENDER = {"name": "Growth", "icon_url": "https://example.com/icon.png"}
LINK = "https://test.slack.com/archives/CSOURCE/p1234567890123456?thread_ts=1234567890.000001"


def source_rec(**kw):
    rec = make_rec(dict(SAMPLES["money"]), deadline=store.iso(store.now() + timedelta(hours=5)), **kw)
    rec["link"] = "https://test.slack.com/archives/CTEST/p11"
    rec["as"] = SENDER
    rec["thread"] = {"link": LINK, "channel": "CSOURCE", "root_ts": "1234567890.000001", "small_ts": "2.0",
                     "text": "A message", "approver": True}
    return rec


def test_nearest_sender_and_invalid_values(tmp_path):
    (tmp_path / ".approvals.json").write_text(json.dumps({"post_as": SENDER}))
    child = tmp_path / "child"
    child.mkdir()
    assert config.folder_sender(child) == SENDER
    (child / ".approvals.json").write_text('{}')
    assert config.folder_sender(child) is None
    for bad in ({"name": "", "icon_url": "https://x"}, {"name": "x" * 81, "icon_url": "https://x"},
                {"name": "a", "icon_url": "http://x"}, None):
        (child / ".approvals.json").write_text(json.dumps({"post_as": bad}))
        with pytest.raises(config.ConfigError):
            config.folder_sender(child)


def test_slack_posts_use_same_bot_with_sender_and_channel(monkeypatch):
    calls = []
    class Client:
        def chat_postMessage(self, **kw):
            calls.append(kw)
            return {"ts": "5.0"}
    monkeypatch.setattr(slackapi, "client", Client)
    monkeypatch.setattr(slackapi, "POST_AS", SENDER)
    slackapi.post([], "one")
    slackapi.note("1.0", "two")
    assert all(c["username"] == "Growth" and c["icon_url"] == SENDER["icon_url"] for c in calls)
    assert all(c["channel"] == "CTEST" for c in calls)


def test_link_validation_and_read_only_linked_message(monkeypatch):
    monkeypatch.setattr(slackapi, "_team_url", lambda: "https://test.slack.com/")
    for bad in ("https://other.slack.com/archives/C/p1234567890123456", "http://test.slack.com/archives/C/p1234567890123456",
                "https://test.slack.com/archives/C/p1234567890123456?thread_ts=abc"):
        with pytest.raises(ValueError):
            slackapi.thread_link(bad)
    # Slack's own link to a reply carries cid beside thread_ts
    assert slackapi.thread_link("https://test.slack.com/archives/C/p1234567890123456?thread_ts=1234567890.000001&cid=C")["root_ts"] == "1234567890.000001"
    thread = slackapi.thread_link(LINK)
    calls = []
    class Client:
        def conversations_replies(self, **kw):
            calls.append(kw)
            return {"messages": [{"ts": thread["ts"], "thread_ts": "1234567890.000001", "user": "UAPPROVER", "text": "Hi &amp; <https://example.com|read>"}]}
        def conversations_info(self, **kw):
            return {"channel": {"name": "growth"}}
    monkeypatch.setattr(slackapi, "client", Client)
    slackapi.read_thread(thread)
    assert calls[0]["ts"] == "1234567890.000001" and calls[0]["oldest"] == calls[0]["latest"] == thread["ts"]
    assert thread["text"] == "Hi & read (https://example.com)" and thread["approver"]
    class HistoryClient(Client):
        def conversations_history(self, **kw):
            calls.append(kw)
            return {"messages": [{"ts": bare["ts"], "thread_ts": "1234567890.000001", "user": "UAPPROVER", "text": "Hi"}]}
    bare = slackapi.thread_link(LINK.split("?")[0])
    monkeypatch.setattr(slackapi, "client", HistoryClient)
    slackapi.read_thread(bare)
    assert bare["root_ts"] == "1234567890.000001" and calls[-1]["oldest"] == bare["ts"]


def test_cards_keep_origin_and_small_card_uses_main_buttons():
    rec = source_rec()
    rec["versions"][0]["card"]["quotes"] = [{"label": "Draft", "text": "hello " * 100, "edit": True}]
    full = cards.card_blocks(rec, 1)
    assert "From your thread with Growth" in json.dumps(full) and "You:" in json.dumps(full)
    small = cards.small_blocks(rec)
    main_buttons = cards._buttons(rec, 1)
    small_buttons = next(b["elements"] for b in small if b["type"] == "actions")
    assert small_buttons == main_buttons
    assert len(json.dumps(small)) < 5000
    rec["status"] = "revising"
    assert "is changing it" in json.dumps(cards.small_blocks(rec))
    rec["status"] = "pending"
    core.decide(rec, 1, "approved", "UAPPROVER")
    assert "Approved by" in json.dumps(cards.small_blocks(rec))
    rec["status"] = "done"
    rec["done"] = {"at": store.iso(), "text": "Posted"}
    assert "Done by Tester" in json.dumps(cards.small_blocks(rec))
    assert "A message" not in json.dumps(cards.card_blocks(rec, 1, "final"))


def test_refresh_updates_source_and_isolates_failure(monkeypatch):
    rec = source_rec()
    calls = []
    def update(ts, blocks, text, channel_id=None):
        calls.append((ts, channel_id))
        if channel_id:
            raise ValueError("no access")
    monkeypatch.setattr(slackapi, "update", update)
    core.refresh_cards(rec)
    assert calls == [("1.1", None), ("2.0", "CSOURCE")]


def test_poll_and_popup_copy_once_and_small_button_decides(monkeypatch):
    rec = source_rec()
    store.save(rec)
    posts = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, **kw: posts.append((text, kw)) or f"{len(posts)+10}.0")
    monkeypatch.setattr(slackapi, "note", lambda *a, **kw: "20.0")
    monkeypatch.setattr(slackapi, "update", lambda *a, **kw: None)
    monkeypatch.setattr(slackapi, "replies", lambda *a, **kw: [{"ts": "3.0", "user": "UAPPROVER", "text": "Please adjust"}])
    L = listener.Listener()
    L.poll_thread(rec)
    L.poll_thread(store.load(rec["id"]))
    assert len(posts) == 1 and posts[0][1]["sender"] == SENDER and posts[0][1]["channel_id"] == "CSOURCE"
    L.copy_to_source(store.load(rec["id"]), "Changed words", "view:1", store.now())
    L.copy_to_source(store.load(rec["id"]), "Changed words", "view:1", store.now())
    assert len(posts) == 2 and len(store.load(rec["id"])["thread"]["copied_ts"]) == 2
    rec = source_rec(id="AP-0002")
    store.save(rec)
    payload = {"user": {"id": "UAPPROVER"}, "actions": [{"value": "AP-0002|1|approve"}],
               "channel": {"id": "CSOURCE"}, "container": {"thread_ts": "1234567890.000001"}}
    L.handle_click(payload)
    assert store.load("AP-0002")["status"] == "approved"


def test_invalid_thread_in_set_posts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(slackapi, "_team_url", lambda: "https://test.slack.com/")
    posts = []
    monkeypatch.setattr(slackapi, "post", lambda *a, **kw: posts.append(a))
    paths = []
    for i, thread in enumerate((LINK, "https://other.slack.com/archives/C/p1234567890123456")):
        path = tmp_path / f"{i}.json"
        path.write_text(json.dumps(dict(SAMPLES["money"], thread=thread)))
        paths.append(str(path))
    with pytest.raises(SystemExit):
        cli.main(["submit", *paths])
    assert posts == []


def test_unreadable_thread_keeps_link_but_does_not_post_small(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(slackapi, "_team_url", lambda: "https://test.slack.com/")
    monkeypatch.setattr(slackapi, "read_thread", lambda t: (_ for _ in ()).throw(ValueError("private\nconversation")))
    posts = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, **kw: posts.append(kw) or "2.0")
    monkeypatch.setattr(slackapi, "permalink", lambda ts: "https://test.slack.com/card")
    monkeypatch.setenv("CODEX_THREAD_ID", "test-session")
    path = tmp_path / "card.json"
    path.write_text(json.dumps(dict(SAMPLES["money"], thread=LINK)))
    cli.main(["submit", str(path)])
    rec = store.all_records()[0]
    assert len(posts) == 1 and rec["thread"]["root_ts"] == "1234567890.000001"
    warning = capsys.readouterr().err
    assert "small_ts" not in rec["thread"] and "link the approval card there yourself" in warning
    assert "won't show the card" in warning and len(warning.splitlines()) == 1


def test_submit_posts_small_to_readable_root_and_records_sender(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".approvals.json").write_text(json.dumps({"post_as": SENDER}))
    monkeypatch.setattr(slackapi, "_team_url", lambda: "https://test.slack.com/")
    def read(thread):
        thread.update(readable=True, text="", approver=False)
    monkeypatch.setattr(slackapi, "read_thread", read)
    posts = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, **kw: posts.append((blocks, kw, slackapi.POST_AS)) or f"{len(posts)}.0")
    monkeypatch.setattr(slackapi, "permalink", lambda ts: "https://test.slack.com/card")
    monkeypatch.setenv("CODEX_THREAD_ID", "test-session")
    path = tmp_path / "card.json"
    path.write_text(json.dumps(SAMPLES["money"]))
    cli.main(["submit", str(path), "--thread", LINK])
    rec = store.all_records()[0]
    assert rec["as"] == SENDER and rec["thread"]["small_ts"] == "2.0"
    assert posts[1][1]["channel_id"] == "CSOURCE" and posts[1][1]["thread_ts"] == "1234567890.000001"
    assert all(p[2] == SENDER for p in posts)
    assert len(cards.origin_blocks(rec)) == 1


def test_invalid_sender_refuses_command_before_post(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".approvals.json").write_text('{"post_as": {"name": "", "icon_url": "https://x"}}')
    path = tmp_path / "card.json"
    path.write_text(json.dumps(SAMPLES["money"]))
    posts = []
    monkeypatch.setattr(slackapi, "post", lambda *a, **k: posts.append(1))
    with pytest.raises(SystemExit):
        cli.main(["submit", str(path)])
    assert not posts and not store.all_records()


def test_bot_message_is_not_quoted(monkeypatch):
    monkeypatch.setattr(slackapi, "_team_url", lambda: "https://test.slack.com/")
    thread = slackapi.thread_link(LINK)
    class Client:
        def conversations_replies(self, **kw):
            return {"messages": [{"ts": thread["ts"], "bot_id": "B1", "user": "UAPPROVER", "text": "bot text"}]}
        def conversations_info(self, **kw):
            return {"channel": {"name": "growth"}}
    monkeypatch.setattr(slackapi, "client", Client)
    slackapi.read_thread(thread)
    assert thread["text"] == ""


@pytest.mark.parametrize("status", ["approved", "declined", "expired", "withdrawn", "removed"])
def test_small_terminal_states_name_who_or_deadline(status):
    rec = source_rec()
    if status in ("approved", "declined"):
        core.decide(rec, 1, status, "UAPPROVER")
    elif status == "expired":
        core.expire(rec)
    else:
        rec["status"] = "withdrawn"
        rec["withdrawn"] = {"text": "Stopped", "at": store.iso(), **({"by": "UAPPROVER"} if status == "removed" else {})}
    text = json.dumps(cards.small_blocks(rec), ensure_ascii=False)
    expected = {"approved": "Approved by Sam", "declined": "Declined by Sam", "expired": "No answer by",
                "withdrawn": "Withdrawn by Tester", "removed": "Removed from your list by Sam"}
    assert expected[status] in text
    assert not any(b["type"] == "actions" for b in cards.small_blocks(rec))


def test_small_rows_have_no_buttons_and_state_includes_decision():
    from kinds import KINDS
    rec = source_rec()
    rec["versions"][0]["card"] = KINDS["set"]
    assert not any(b["type"] == "actions" for b in cards.small_blocks(rec))
    rec["status"] = "approved"
    rec["decision"] = {"verdict": "approved", "at": store.iso(), "where": "rows"}
    rec["timeline"] = [{"kind": "done", "text": "Next step complete", "at": store.iso()}]
    text = json.dumps(cards.small_blocks(rec))
    assert "Approved by Sam" in text and "Next step complete" in text


def test_origin_quote_is_dropped_before_card_material():
    rec = source_rec()
    card = rec["versions"][0]["card"]
    for count in range(1, 60):
        card["facts"] = [[str(i), "value"] for i in range(count)]
        base = dict(rec)
        base.pop("thread")
        if len(cards.card_blocks(base, 1)) == 49:
            break
    blocks = cards.card_blocks(rec, 1)
    assert len(blocks) <= 50 and "Your yes covers" in json.dumps(blocks)
    assert "Open the thread" in json.dumps(blocks) and "A message" not in json.dumps(blocks)


def test_refusal_for_small_button_goes_to_source_thread(monkeypatch):
    rec = source_rec()
    store.save(rec)
    calls = []
    monkeypatch.setattr(slackapi, "ephemeral", lambda user, text, thread_ts=None, channel_id=None: calls.append((channel_id, thread_ts)))
    payload = {"user": {"id": "OTHER"}, "actions": [{"value": "AP-0001|1|approve"}],
               "channel": {"id": "CSOURCE"}, "container": {"thread_ts": "1234567890.000001"}}
    listener.Listener().handle_click(payload)()
    assert calls == [("CSOURCE", "1234567890.000001")]


def test_purge_deletes_only_selected_source_posts(monkeypatch):
    from types import SimpleNamespace
    rec = source_rec(test=True)
    rec["thread"]["copied_ts"] = ["3.0", "4.0"]
    store.save(rec)
    other = source_rec(id="AP-0002", test=True)
    store.save(other)
    deleted = []
    monkeypatch.setattr(slackapi, "delete", lambda ts, channel_id=None: deleted.append((ts, channel_id)))
    monkeypatch.setattr(slackapi, "replies", lambda ts: [{"ts": ts}])
    monkeypatch.setattr(slackapi, "bot_user", lambda: "BOT")
    cli.cmd_purge_tests(SimpleNamespace(only=["AP-0001"]))
    assert deleted == [("2.0", "CSOURCE"), ("3.0", "CSOURCE"), ("4.0", "CSOURCE"), ("1.1", None)]
    assert store.load("AP-0002")


def test_popup_words_are_copied_once_and_untruncated(monkeypatch):
    rec = source_rec()
    store.save(rec)
    posts = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, **kw: posts.append((blocks, text, kw)) or "8.0")
    monkeypatch.setattr(slackapi, "note", lambda *a, **kw: "9.0")
    L = listener.Listener()
    monkeypatch.setattr(L, "try_refresh", lambda *a: None)
    words = "a&" * 1300
    payload = {"user": {"id": "UAPPROVER"}, "view": {"id": "VIEW1", "callback_id": "approvals",
        "private_metadata": "AP-0001|1|change", "state": {"values": {"words": {"words": {"value": words}}}}}}
    result, follow = L.handle_submission(payload)
    assert result is None
    follow()
    follow()
    assert len(posts) == 1
    assert posts[0][1] == words and len(posts[0][0]) > 2
    assert sum(len(b["text"]["text"].replace("> ", "").replace("&amp;", "&")) for b in posts[0][0][1:]) == len(words)
    assert store.load(rec["id"])["thread"]["copied_keys"] == ["view:VIEW1"]


def test_list_and_summary_use_workspace_sender(monkeypatch):
    from datetime import datetime, timezone
    now = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)
    cfg = json.loads(config.CONFIG.read_text())
    cfg["list_as"] = SENDER
    config.CONFIG.write_text(json.dumps(cfg))
    posts = []
    state = {"value": None}
    monkeypatch.setattr(digest, "load_state", lambda: state["value"])
    monkeypatch.setattr(digest, "_save_state", lambda x: state.update(value=x))
    monkeypatch.setattr(digest, "_retire", lambda *a: None)
    monkeypatch.setattr(digest, "render", lambda *a: ([], "list", "content"))
    monkeypatch.setattr(digest, "summary", lambda *a: ([], "summary"))
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, **kw: posts.append((text, kw)) or "10.0")
    digest._tick(now, False)
    digest._tick(now + timedelta(minutes=5), True)
    assert [p[0] for p in posts] == ["list", "summary", "list"]
    assert all(p[1]["sender"] == SENDER for p in posts)


def test_cli_thread_posts_take_current_callers_sender(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".approvals.json").write_text(json.dumps({"post_as": SENDER}))
    monkeypatch.setenv("CODEX_THREAD_ID", "test-session")
    calls = []
    class Client:
        def chat_postMessage(self, **kw):
            calls.append(kw)
            return {"ts": f"{len(calls)+10}.0"}
        def chat_update(self, **kw):
            pass
    monkeypatch.setattr(slackapi, "client", Client)
    monkeypatch.setattr(slackapi, "permalink", lambda ts: "https://test.slack.com/card")
    monkeypatch.setattr(slackapi, "_team_url", lambda: "https://test.slack.com/")
    path = tmp_path / "card.json"
    path.write_text(json.dumps(dict(SAMPLES["money"], evidence="Checked in the local ledger")))
    cli.main(["submit", str(path)])
    cli.main(["ask", "AP-0001", "Which one?"])
    cli.main(["answer", "AP-0001", "This one."])
    cli.main(["ack", "AP-0001", "Checking."])
    cli.main(["status", "AP-0001", "Need your input", "--on-you"])
    path.write_text(json.dumps(dict(SAMPLES["money"], changes="Adjusted")))
    cli.main(["revise", "AP-0001", str(path)])
    assert len(calls) >= 7
    assert all(c.get("username") == SENDER["name"] and c.get("icon_url") == SENDER["icon_url"] for c in calls)


def test_reviewer_posts_under_own_folder_sender_without_changing_asker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_THREAD_ID", "test-session")
    (tmp_path / ".approvals.json").write_text(json.dumps({"post_as": SENDER}))
    posted = []
    class Client:
        def chat_postMessage(self, **kw):
            posted.append(kw)
            return {"ts": f"{len(posted)}.0"}
    monkeypatch.setattr(slackapi, "client", Client)
    monkeypatch.setattr(slackapi, "permalink", lambda ts: "https://test.slack.com/card")
    path = tmp_path / "card.json"
    path.write_text(json.dumps(SAMPLES["money"]))
    cli.main(["submit", str(path)])
    store.set_reviewer({"types": ["money"], "reach": {"agent": "Reviewer", "session_id": store.load("AP-0001")["reach"]["session_id"]}, "test": False})
    folder = tmp_path / "reviewer"
    folder.mkdir()
    reviewer = {"name": "Finance", "icon_url": "https://example.com/finance.png"}
    (folder / ".approvals.json").write_text(json.dumps({"post_as": reviewer}))
    note = folder / "note.md"
    note.write_text("The numbers are checked.")
    monkeypatch.chdir(folder)
    cli.main(["note", "AP-0001", "--file", str(note)])
    assert posted[0]["username"] == "Growth" and posted[-1]["username"] == "Finance"
    assert posted[-1]["icon_url"] == reviewer["icon_url"] and store.load("AP-0001")["as"] == SENDER


def test_small_card_uses_latest_version_and_exact_new_buttons():
    rec = source_rec()
    card = dict(rec["versions"][0]["card"], title="The revised proposal", yes_label="Do the revised work", change_label="Change it")
    rec["versions"].append({"n": 2, "card": card})
    blocks = cards.small_blocks(rec)
    rendered = json.dumps(blocks)
    assert "The revised proposal" in rendered and "version 2" in rendered
    buttons = next(b["elements"] for b in blocks if b["type"] == "actions")
    assert buttons == cards._buttons(rec, 2)
    assert all(b["value"].startswith("AP-0001|2|") for b in buttons)


def test_small_card_sections_and_contexts_stay_within_slack_limits():
    rec = source_rec()
    rec["versions"][0]["card"]["approves"] = "scope " * 1000
    rec["versions"][0]["card"]["quotes"] = [{"label": "Draft", "text": "quote " * 1000}]
    samples = [cards.small_blocks(rec)]
    rec["status"] = "revising"
    samples.append(cards.small_blocks(rec))
    core.decide(rec, 1, "approved", "UAPPROVER")
    rec["timeline"] = [{"kind": "their_answer", "text": "status " * 1000, "at": store.iso()}]
    samples.append(cards.small_blocks(rec))
    rec["status"] = "withdrawn"
    rec["withdrawn"] = {"at": store.iso(), "text": "reason " * 1000}
    samples.append(cards.small_blocks(rec))
    for blocks in samples:
        for block in blocks:
            if block["type"] == "context":
                assert all(len(e["text"]) <= 2000 for e in block["elements"])
            if block["type"] == "section":
                assert len(block["text"]["text"]) <= 3000
    assert "…" in json.dumps(samples, ensure_ascii=False)


def test_small_decision_preserves_pick_and_partial_batch():
    rec = source_rec()
    rec["status"] = "approved"
    rec["decision"] = {"at": store.iso(), "verdict": "approved", "choice": "B"}
    assert "Picked by Sam" in json.dumps(cards.small_blocks(rec))
    rec["versions"][0]["card"]["items"] = ["One", "Two", "Three"]
    rec["decision"] = {"at": store.iso(), "verdict": "approved", "items": [1, 3]}
    text = json.dumps(cards.small_blocks(rec))
    assert "2 of 3" in text and "held back: Two" in text
