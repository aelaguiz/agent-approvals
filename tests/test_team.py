"""A card left for the default approver can also be answered by one teammate."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from approvals import cards, cli, config, core, digest, listener, slackapi, store
from kinds import KINDS
from samples import SAMPLES
from test_deputy import ASKER, as_session, be_deputy, call, to
from test_kinds import press_payload, rec_for, run, submit_payload, txt, values


RID = "AP-0001"
USER, DM = "UTEAM1", "DTEAM1"
WHY = "Riley runs the search ads."
CREDIT = "The card also went to Riley, and Riley's answer counts as Sam's, for exactly what the card describes."
NOW = datetime(2026, 1, 12, 12, tzinfo=timezone.utc)


@pytest.fixture
def team(monkeypatch):
    cfg = config.settings()
    cfg["team"] = {"riley": {"user": USER, "name": "Riley"}, "morgan": {"user": "UTEAM2", "name": "Morgan"}}
    cfg["approvers"] = {"lead": {"approver": "ULEAD", "approver_name": "Team lead", "channel": "CLEAD"}}
    config.CONFIG.write_text(json.dumps(cfg))
    monkeypatch.setattr(store, "now", lambda: NOW)
    as_session(monkeypatch, "sid-dep")
    be_deputy(live=True)
    return cfg


@pytest.fixture
def slack(monkeypatch):
    wire = SimpleNamespace(opens=[], posts=[], updates=[], notes=[], notices=[], threads={}, reads=[], views=[], deletes=[])
    def post(blocks, text, **kw):
        ts = f"{store.now().timestamp() + len(wire.posts) / 10:.6f}"
        wire.posts.append((ts, blocks, kw))
        return ts

    monkeypatch.setattr(slackapi, "open_dm", lambda user: wire.opens.append(user) or DM)
    monkeypatch.setattr(slackapi, "post", post)
    monkeypatch.setattr(slackapi, "update", lambda ts, blocks, text, **kw: wire.updates.append((ts, blocks, kw)))
    monkeypatch.setattr(slackapi, "note", lambda ts, text, **kw: wire.notes.append((ts, text)))
    monkeypatch.setattr(slackapi, "ephemeral", lambda user, text, ts=None, **kw: wire.notices.append(text))
    monkeypatch.setattr(slackapi, "message_url", lambda ts, **kw: f"https://slack.test/{ts}")
    monkeypatch.setattr(slackapi, "replies", lambda ts, **kw: wire.reads.append((ts, kw)) or wire.threads.get(ts, []))
    monkeypatch.setattr(slackapi, "views_open", lambda trigger, view: wire.views.append(view))
    monkeypatch.setattr(slackapi, "views_update", lambda vid, view, h=None: wire.views.append(view))
    monkeypatch.setattr(slackapi, "delete", lambda ts, **kw: wire.deletes.append((ts, kw)))
    monkeypatch.setattr(listener.Listener, "refresh_list", lambda self: None)
    return wire


def card(rid=RID, kind=None, **kw):
    rec = rec_for(KINDS[kind] if kind else SAMPLES["budget"], id=rid, thread_ts=f"{rid[-1]}.1",
                  reach=dict(ASKER), link=f"https://slack.test/{rid}", **kw)
    rec["versions"][0]["posted_at"] = rec["created"]
    rec["deadline"] = store.iso(store.now() + timedelta(days=2))
    store.save(rec)
    return rec


def leave(rid=RID, name="riley"):
    call(rid, "--his", "--also", name, "--why", WHY)


def click(what, user=USER, channel=DM, rid=RID, n=1):
    payload = press_payload(f"{rid}|{n}|{what}", user=user)
    payload["channel"] = {"id": channel}
    run(listener.Listener().handle_click(payload))


def latest(rid=RID):
    rec = store.load(rid)
    return rec, rec["also"][-1]


def reply(ts, words, user=USER):
    return {"ts": f"{float(ts) + 1:.6f}", "user": user, "text": words}


def revise(tmp_path, rid=RID):
    path = tmp_path / "card.json"
    path.write_text(json.dumps(dict(SAMPLES["budget"], changes="Lower the cap.")))
    cli.main(["revise", rid, str(path)])


def test_call_records_the_person_and_the_why_and_leaves_sending_to_the_sweep(team, slack, capsys):
    card()
    leave()
    rec, send = latest()
    assert rec["calls"][0]["also"] == rec["events"][0]["also"] == "riley"
    assert send == {"version": 1, "name": "Riley", "user": USER, "why": WHY, "at": store.iso(NOW), "send_after": None}
    assert "left for Sam and goes to Riley as a direct message" in capsys.readouterr().out
    assert not slack.opens and not slack.posts
    assert WHY in txt(cards.call_line(rec, 1)) and "Also goes to Riley" in txt(cards.call_line(rec, 1))


@pytest.mark.parametrize("flag,name,target", [("--approve", "riley", None), ("--his", "unknown", None),
                                            ("--his", "riley", "lead")])
def test_unavailable_also_is_refused_with_the_configured_names(team, slack, capsys, flag, name, target):
    before = card(target=target)
    with pytest.raises(SystemExit):
        call(RID, flag, "--also", name, "--why", WHY)
    assert "Configured names: morgan, riley" in capsys.readouterr().err
    assert store.load(RID) == before and not slack.posts and not slack.updates


def test_the_deputy_instructions_list_names_only_for_the_default_approvers_team(team, capsys):
    rec = card()
    assert "`--also <name>` beside `--his`" in core.make_your_call(rec)
    assert "morgan, riley" in core.make_your_call(rec)
    assert "--also" not in core.make_your_call(dict(rec, target="lead"))
    cli.main(["deputy", "--agent", "Test deputy"])
    assert "morgan, riley" in capsys.readouterr().out
    config.CONFIG.write_text(json.dumps(dict(team, team={})))
    assert "--also" not in core.make_your_call(rec)


@pytest.mark.parametrize("quiet,at,due", [
    (["21:00", "05:00"], "2026-01-12T20:59:00+00:00", None),
    (["21:00", "05:00"], "2026-01-12T21:00:00+00:00", "2026-01-13T05:00:00+00:00"),
    (["21:00", "05:00"], "2026-01-13T04:59:00+00:00", "2026-01-13T05:00:00+00:00"),
    (["21:00", "05:00"], "2026-01-13T05:00:00+00:00", None),
    (["09:00", "14:00"], "2026-01-12T12:00:00+00:00", "2026-01-12T14:00:00+00:00"),
])
def test_call_records_the_end_of_the_quiet_window(team, slack, monkeypatch, quiet, at, due):
    team["team"]["riley"]["quiet"] = quiet
    config.CONFIG.write_text(json.dumps(team))
    monkeypatch.setattr(store, "now", lambda: store.parse(at))
    card()
    leave()
    assert latest()[1]["send_after"] == due


def test_the_sweep_waits_for_local_quiet_hours_and_sends_once(team, slack, monkeypatch, capsys):
    team["timezone"] = "America/Chicago"
    team["team"]["riley"]["quiet"] = ["21:00", "05:00"]
    config.CONFIG.write_text(json.dumps(team))
    now = store.parse("2026-01-13T04:00:00+00:00")  # 10 pm in the workspace
    monkeypatch.setattr(store, "now", lambda: now)
    card()
    leave()
    assert "goes to Riley at 5:00 am, after Riley's quiet hours" in capsys.readouterr().out
    assert "Also goes to Riley at 5:00 am" in txt(cards.call_line(store.load(RID), 1))
    L = listener.Listener()
    L.sweep()
    assert not slack.opens
    now += timedelta(hours=7)
    L.sweep()
    L.sweep()
    rec, send = latest()
    assert slack.opens == [USER] and len(slack.posts) == 1
    assert (send["channel"], send["ts"], send["sent_at"]) == (DM, slack.posts[0][0], store.iso(now))
    assert "Also sent to Riley, 5:00 am" in txt(cards.call_line(rec, 1))
    lines = "\n".join(cli._happened(rec))
    assert "also to Riley" in lines and "sent v1 to Riley in a direct message, held until Jan 13 5:00 am" in lines


@pytest.mark.parametrize("outcome", ["approved", "withdrawn", "row answered", "during open"])
def test_the_sweep_skips_a_send_for_a_card_that_no_longer_waits(team, slack, monkeypatch, outcome):
    card(kind="set" if outcome == "row answered" else None)
    leave()
    if outcome == "withdrawn":
        cli.main(["withdraw", RID, "No longer needed"])
    elif outcome == "approved":
        click("approve", user="UAPPROVER", channel="CTEST")
    elif outcome == "row answered":
        click("p1:approve", user="UAPPROVER", channel="CTEST")
    else:
        def open_dm(user):
            click("approve", user="UAPPROVER", channel="CTEST")
            return DM
        monkeypatch.setattr(slackapi, "open_dm", open_dm)
    listener.Listener().sweep()
    assert latest()[1]["skipped"] and not slack.posts
    if outcome == "row answered":
        assert "Also goes to Riley" not in txt(slack.updates[-1][1])


def test_a_failed_send_is_tried_in_the_next_sweep(team, slack, monkeypatch, caplog):
    card()
    leave()
    post = slackapi.post
    def broken(*a, **kw):
        raise ValueError("Slack failed")
    monkeypatch.setattr(slackapi, "post", broken)
    L = listener.Listener()
    L.sweep()
    assert not latest()[1].get("sent_at") and "direct message for AP-0001 failed" in caplog.text
    monkeypatch.setattr(slackapi, "post", post)
    L.sweep()
    assert len(slack.posts) == 1 and latest()[1]["sent_at"]


def test_a_call_that_sent_to_someone_stands_when_auto_approval_is_turned_on(team, slack, capsys):
    be_deputy()
    card()
    leave()
    listener.Listener().sweep()
    be_deputy(live=True)
    with pytest.raises(SystemExit):
        leave(name="morgan")
    assert "Sam handed this card to Riley; call it without --also." in capsys.readouterr().err
    assert slack.opens == [USER] and len(latest()[0]["also"]) == 1


def test_the_open_dm_reuses_the_card_without_the_source_quote_or_deputy_line(team, slack):
    rec = card(kind="message")
    rec["thread"] = {"link": "https://slack.test/origin", "quote": "Approver's original words"}
    store.save(rec)
    leave()
    listener.Listener().sweep()
    rec, send = latest()
    blocks = slack.posts[0][1]
    assert "Tester asks Sam, and you can answer it too." in txt(blocks[0])
    assert txt(blocks[-1]).count("Sam has this card too. Whoever answers first has answered.") == 1
    assert f"<{rec['link']}|Open it in #approvals>" in txt(blocks[-1])
    assert "*Why you:* " + WHY in txt(blocks)
    assert "origin" not in txt(blocks) and "left this for you" not in txt(blocks)
    assert "Your yes covers" in txt(blocks) and "image" in [b["type"] for b in blocks]
    assert set(values(blocks)) == set(values(cards.card_blocks(rec, 1))) | {f"{RID}|1|notmine"}
    assert blocks == cards.also_blocks(rec, send)


def test_a_long_dm_keeps_its_buttons_and_facts_under_slacks_block_limit(team, slack):
    rec = card()
    c = rec["versions"][0]["card"]
    rec["versions"][0]["card"] = dict(c, facts=[[f"Fact {i}", "Value"] for i in range(41)])
    store.save(rec)
    leave()
    listener.Listener().sweep()
    blocks = slack.posts[0][1]
    assert len(blocks) <= cards.BLOCKS_MAX
    assert "Fact 40" in txt(blocks) and f"{RID}|1|approve" in values(blocks) and f"{RID}|1|notmine" in values(blocks)


def test_a_card_at_slacks_limit_keeps_its_content_in_both_places(team, slack):
    rec = card()
    c = rec["versions"][0]["card"]
    c["facts"] = [["Total", "$250"]]
    room = cards.BLOCKS_MAX - len(cards.card_blocks(rec, 1))
    c["sections"] = [{"title": f"Section {i}", "lines": ["Detail"]} for i in range(room)]
    assert cards.validate(c) == []
    store.save(rec)
    leave()
    listener.Listener().sweep()
    rec, send = latest()
    for blocks in (cards.card_blocks(rec, 1), cards.also_blocks(rec, send)):
        assert len(blocks) <= cards.BLOCKS_MAX
        assert all(f"*Section {i}*" in txt(blocks) for i in range(room))
        assert f"{RID}|1|approve" in values(blocks)
    assert f"{RID}|1|notmine" in txt(cards.also_blocks(rec, send))


@pytest.mark.parametrize("state,line", [
    ("approved", "Approved by Sam"), ("declined", "Declined by Sam"), ("done", "Done by Tester"),
    ("withdrawn", "Withdrawn by Tester"), ("removed", "Removed from your list by Sam"),
    ("revising", "Riley replied"), ("expired", "No answer by"), ("handed back", "You handed it back to Sam"),
    ("replaced", "A newer version replaced this one."),
])
def test_the_dm_closes_with_what_happened_and_a_link(team, slack, tmp_path, state, line):
    card()
    leave()
    listener.Listener().sweep()
    if state in ("approved", "declined", "removed"):
        click({"approved": "approve", "declined": "decline", "removed": "remove"}[state], user="UAPPROVER", channel="CTEST")
    elif state == "done":
        click("approve", user="UAPPROVER", channel="CTEST")
        cli.main(["done", RID, "Done and checked"])
    elif state == "withdrawn":
        cli.main(["withdraw", RID, "No longer needed"])
    elif state == "replaced":
        revise(tmp_path)
    elif state == "handed back":
        click("notmine")
    elif state == "expired":
        store.update(RID, core.expire)
    else:
        store.update(RID, lambda r: core.take_replies(r, [("2.0", "Change the total")], by=USER))
    rec, send = latest()
    blocks = cards.also_blocks(rec, send)
    assert len(blocks) == 3 and not values(blocks)
    assert rec["versions"][0]["card"]["title"] in txt(blocks[0])
    assert line in txt(blocks[1]) and rec["link"] in txt(blocks[2])
    if state != "replaced":
        assert " am" in txt(blocks[1]) or " pm" in txt(blocks[1])


def test_refresh_updates_both_messages_and_a_dm_failure_does_not_block_the_card(team, slack, monkeypatch, caplog):
    card()
    leave()
    listener.Listener().sweep()
    rec, send = latest()
    assert any(ts == send["ts"] and kw.get("channel_id") == DM for ts, _, kw in slack.updates)
    slack.updates.clear()
    update = slackapi.update
    def fail_dm(ts, blocks, text, channel_id=None):
        if channel_id == DM:
            raise ValueError("no access")
        update(ts, blocks, text)
    monkeypatch.setattr(slackapi, "update", fail_dm)
    click("approve", channel="CTEST")
    assert "Riley approved" in txt(slack.updates[0][1]) and "direct message refresh" in caplog.text
    assert not store.load(RID)["card_dirty"]


def test_purge_tests_deletes_the_test_cards_sent_dm(team, slack):
    card(test=True)
    leave()
    listener.Listener().sweep()
    sent = latest()[1]
    cli.main(["purge-tests", "--only", RID])
    assert (sent["ts"], {"channel_id": DM}) in slack.deletes
    assert not store.all_records()


@pytest.mark.parametrize("channel", ["CTEST", DM])
def test_team_yes_is_named_to_every_reader(team, slack, channel):
    rec = card()
    store.set_reviewer({"types": [rec["type"]], "reach": {"session_id": "sid-reviewer"}, "test": False})
    leave()
    listener.Listener().sweep()
    click("approve", channel=channel)
    rec = store.load(RID)
    assert rec["decision"]["by"] == rec["events"][-1]["by"] == USER and rec["status"] == "approved"
    [answer] = to(RID)
    assert "Riley approved it" in answer["text"] and answer["text"].count(CREDIT) == 1
    assert "Riley approved it" in to(RID, "deputy")[-1]["text"]
    assert "Riley approved it" in to(RID, "reviewer")[-1]["text"]
    assert "Riley approved" in txt(cards.card_blocks(rec, 1, "final")[0])
    assert "Approved by Riley" in txt(cards.card_blocks(rec, 1, "final"))
    assert "Approved by Riley" in txt(cards.small_blocks(rec))
    assert "Riley approved it" in txt(digest._line(rec, NOW.date()))
    assert "Riley approved v1" in "\n".join(cli._happened(rec))
    assert not core.plain_yes(rec, 1)
    assert "you approved 0 of those without a word" in txt(digest.summary(store.deputy(), NOW, [rec]))


def test_only_the_sent_person_can_answer_and_only_the_approver_can_remove_or_switch(team, slack):
    card()
    leave()
    click("approve", channel="CTEST")
    assert slack.notices[-1] == "Only Sam can decide approvals."
    listener.Listener().sweep()
    for what, user, channel in (("approve", "UTEAM2", DM), ("remove", USER, "CTEST"), ("takeback", USER, "CTEST")):
        click(what, user=user, channel=channel)
        assert slack.notices[-1] == "Only Sam can decide approvals."
    run(listener.Listener().handle_click(press_payload("deputy|0|pause", user=USER)))
    assert slack.notices[-1] == "Only Sam can decide approvals." and store.deputy()["live"]
    assert store.load(RID)["status"] == "pending"
    assert not listener.is_ask({"user": USER, "ts": "2.0", "text": "What's waiting?"})


def test_the_approvers_first_answer_stands(team, slack):
    card()
    leave()
    listener.Listener().sweep()
    click("approve", user="UAPPROVER", channel="CTEST")
    click("decline")
    rec = store.load(RID)
    assert slack.notices[-1] == "This one is already decided."
    assert rec["decision"]["by"] == "UAPPROVER" and core.plain_yes(rec, 1) and len(to(RID)) == 1


def test_a_new_version_ends_standing_until_the_deputy_names_the_person_again(team, slack, tmp_path):
    card()
    leave()
    L = listener.Listener()
    L.sweep()
    dm1 = latest()[1]["ts"]
    revise(tmp_path)
    click("approve")
    assert "version was replaced" in slack.notices[-1]
    click("approve", channel="CTEST", n=2)
    assert slack.notices[-1] == "Only Sam can decide approvals."
    leave()
    L.sweep()
    rec, send = latest()
    assert len(rec["also"]) == 2 and send["version"] == 2 and send["ts"] != dm1
    assert any(ts == dm1 and "A newer version replaced this one." in txt(blocks) for ts, blocks, _ in slack.updates)
    slack.threads[rec["thread_ts"]] = [{"ts": f"{float(dm1) + 0.05:.6f}", "user": USER, "text": "Yes to version one."}]
    L.poll_once()
    assert not to(RID)
    click("approve", n=2)
    assert store.load(RID)["decision"]["version"] == 2


@pytest.mark.parametrize("via_dm", [False, True])
def test_channel_and_dm_replies_reach_the_asker_once_with_the_name(team, slack, via_dm):
    card(thread={"link": "https://slack.test/origin", "small_ts": "8.0", "channel": "CSOURCE", "root_ts": "7.0"})
    leave()
    L = listener.Listener()
    L.sweep()
    rec, send = latest()
    thread = send["ts"] if via_dm else rec["thread_ts"]
    m = reply(send["ts"], "Do it.\nKeep the same cap.")
    slack.threads[thread] = [m]
    L.poll_once()
    L.next_poll.clear()
    L.poll_once()
    [answer] = to(RID)
    assert 'Riley replied in Slack' in answer["text"] and answer["text"].count(CREDIT) == 1
    assert '"Do it. / Keep the same cap."' in answer["text"]
    rec, send = latest()
    assert (send if via_dm else rec)["last_seen"] == m["ts"]
    assert rec["events"][-1]["by"] == USER
    assert "Riley replied" in "\n".join(cli._happened(rec))
    if via_dm:
        assert slack.notes == [(rec["thread_ts"], "💬 Riley replied in a direct message:\n" + cards.quoted(m["text"]))]
        assert (thread, {"oldest": m["ts"], "channel_id": DM}) in slack.reads
        assert answer["react_ts"] == rec["thread_ts"]
        assert not rec["thread"].get("copied_ts")
    else:
        assert not slack.notes
        assert "Riley" in txt(slack.posts[-1][1]) and rec["thread"]["copied_ts"]
    cli.main(["record", RID, "--approved", "--words", m["text"], "--where", "Slack thread"])
    assert store.load(RID)["decision"]["by"] == USER


def test_not_mine_leaves_the_card_waiting_and_ends_the_teammates_access(team, slack):
    before = card()
    leave()
    L = listener.Listener()
    L.sweep()
    click("notmine", user="UTEAM2")
    assert slack.notices[-1] == "Only Sam can decide approvals." and not latest()[1].get("handed_back_at")
    click("notmine")
    rec, send = latest()
    assert send["handed_back_at"] and rec["events"][-1]["kind"] == "also_handed_back"
    assert rec["status"] == "pending" and rec["deadline"] == before["deadline"]
    assert not to(RID) and "Riley handed it back as not Riley's to answer" in to(RID, "deputy")[-1]["text"]
    assert "It stays with Sam; there is nothing to call." in to(RID, "deputy")[-1]["text"]
    assert "Riley handed it back, 12:00 pm" in txt(cards.call_line(rec, 1))
    assert "Riley handed v1 back to Sam" in "\n".join(cli._happened(rec))
    click("approve", channel="CTEST")
    assert slack.notices[-1] == "Only Sam can decide approvals."
    slack.threads[rec["thread_ts"]] = slack.threads[send["ts"]] = [reply(send["ts"], "Yes")]
    L.poll_once()
    assert not to(RID) and not slack.notes and store.load(RID)["status"] == "pending"
    click("approve", user="UAPPROVER", channel="CTEST")
    assert store.load(RID)["status"] == "approved"


@pytest.mark.parametrize("kind,what,phrase", [
    ("choice", "pick:0", "Riley picked"),
    ("set", "p1:approve", "Riley approved this part"),
    ("set", "all", "Riley approved"),
])
def test_picks_and_row_buttons_carry_the_team_answer(team, slack, kind, what, phrase):
    card(kind=kind)
    leave()
    listener.Listener().sweep()
    click(what)
    [answer] = to(RID)
    assert phrase in answer["text"] and answer["text"].count(CREDIT) == 1
    rec = store.load(RID)
    assert rec["events"][-1]["by"] == USER
    assert not core.plain_yes(rec, 1)


@pytest.mark.parametrize("kind,what,phrase", [
    ("as_you", "edit", "Riley approved it with Riley's edited words"),
    ("message", "change", "Riley replied"),
    ("set", "p1:change", "Riley asked for a change to this part"),
    ("batch", "items", "Riley approved 1 of"),
])
def test_team_popups_and_recount_preserve_who_answered(team, slack, kind, what, phrase):
    card(kind=kind)
    leave()
    L = listener.Listener()
    L.sweep()
    click(what)
    assert slack.views[-1]["private_metadata"] == f"{RID}|1|{what}"
    fields = ({"items0": {"items": {"selected_options": [{"value": "1"}]}}} if what == "items"
              else {"words": {"words": {"value": "New words"}}})
    payload = submit_payload(f"{RID}|1|{what}", fields, user=USER)
    if what == "items":
        run(L.handle_click(dict(payload, actions=[{"action_id": "items"}])))
        assert "1" in slack.views[-1]["submit"]["text"]
    response, follow = L.handle_submission(payload)
    assert response is None
    run(follow)
    [answer] = to(RID)
    assert phrase in answer["text"] and answer["text"].count(CREDIT) == 1
    assert store.load(RID)["events"][-1]["by"] == USER
    if what != "items":
        assert "Riley " in slack.notes[-1][1] and "> New words" in slack.notes[-1][1]
    if what == "edit":
        assert "Riley's words" in txt(cards.card_blocks(store.load(RID), 1, "final"))


def test_the_morning_summary_reports_the_outcome_of_each_sent_card(team, slack):
    outcomes = ("✅ approved it", "❌ declined it", "💬 replied", "↩️ handed it back", "you answered first",
                "⏳ still open", "withdrawn by its agent")
    for i, _ in enumerate(outcomes, 1):
        rid = f"AP-{i:04d}"
        card(rid)
        leave(rid)
    listener.Listener().sweep()
    click("approve", rid=RID)
    click("decline", rid="AP-0002")
    store.update("AP-0003", lambda r: core.take_replies(r, [("7.0", "Do it")], by=USER))
    click("notmine", rid="AP-0004")
    click("approve", user="UAPPROVER", channel="CTEST", rid="AP-0005")
    cli.main(["withdraw", "AP-0007", "No longer needed"])
    card("AP-0008")
    leave("AP-0008")
    d = store.deputy()
    records = store.all_records()
    blocks, _ = digest.summary(d, NOW, records)
    text = "\n".join(b["text"]["text"] for b in blocks if b["type"] == "section")
    assert "👥 *7 cards also went to your team since yesterday morning*" in text
    for i, outcome in enumerate(outcomes, 1):
        line = next(line for line in text.splitlines() if f"|AP-{i:04d}>" in line)
        assert f"Riley · {outcome}" in line
    assert "AP-0008" not in text
    assert "your team" not in txt(digest.summary(d, NOW, [store.load("AP-0008")]))
    assert "your team" not in txt(digest.summary(dict(d, live=False), NOW, records))


def test_a_long_morning_summary_leaves_room_for_the_team(team, slack):
    rec = card()
    leave()
    listener.Listener().sweep()
    r, _ = latest()
    yes = {"call": "approve", "live": True, "at": store.iso(NOW), "why": "A" * 290, "version": 1}
    many = [dict(rec, id=f"AP-{i + 2:04d}", calls=[yes]) for i in range(1000)]
    blocks, _ = digest.summary(store.deputy(), NOW, [r, *many])
    assert len(digest.switched(blocks, False, NOW)) <= cards.BLOCKS_MAX
    assert all(len(b["text"]["text"]) <= 3000 for b in blocks if b["type"] == "section")
    assert "👥 *1 card also went to your team" in txt(blocks) and "…and " in txt(blocks)


def test_a_legacy_card_uses_only_the_approver_and_keeps_its_old_record(team, slack):
    card()
    click("approve", channel="CTEST")
    click("approve", user="UAPPROVER", channel="CTEST")
    rec = store.load(RID)
    assert "also" not in rec and "by" not in rec["events"][-1]
    assert core.plain_yes(rec, 1) and "Sam approved it" in to(RID)[0]["text"]
    assert CREDIT not in to(RID)[0]["text"] and not slack.opens
