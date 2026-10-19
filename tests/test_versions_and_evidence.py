"""Settled requests: replies after a decline, new versions after an approval, withdrawing; and evidence."""
import json
from datetime import timedelta

import pytest

from approvals import cards, cli, core, listener, slackapi, store
from samples import SAMPLES
from test_cards_store import make_rec


@pytest.fixture
def slack(monkeypatch):
    """Records every post instead of sending it."""
    posts = []
    n = iter(range(100, 10000))
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, thread_ts=None, broadcast=False:
                        posts.append({"text": text, "thread": thread_ts, "broadcast": broadcast}) or f"{next(n)}.0")
    monkeypatch.setattr(slackapi, "update", lambda ts, blocks, text: None)
    monkeypatch.setattr(slackapi, "note", lambda ts, text: posts.append({"note": text}) or f"{next(n)}.0")
    monkeypatch.setattr(slackapi, "upload_to_thread", lambda path, ts, title, comment=None: posts.append({"file": str(path), "title": title}))
    monkeypatch.setattr(slackapi, "permalink", lambda ts: "https://slack/x")
    return posts


def saved(**kw):
    rec = make_rec(dict(SAMPLES["money"]), deadline=store.iso(store.now() + timedelta(hours=5)), **kw)
    store.save(rec)
    return rec


def card_file(tmp_path, **kw):
    p = tmp_path / "card.json"
    p.write_text(json.dumps(dict(SAMPLES["money"], **kw)))
    return str(p)


def test_a_reply_on_a_declined_card_still_reaches_the_agent():
    rec = saved()
    core.decide(rec, 1, "declined", "UAPPROVER")
    store.save(rec)
    L = listener.Listener.__new__(listener.Listener)
    assert [r["id"] for r in L.watched()] == ["AP-0001"]
    kind = core.take_replies(rec, [("9.0", "you still need to make something for that slot")])
    assert kind == "comment" and "after the decision" in rec["outbox"][-1]["text"]


def test_a_decline_tells_the_agent_to_drop_the_proposal_not_the_goal():
    rec = saved()
    core.decide(rec, 1, "declined", "UAPPROVER")
    text = rec["outbox"][-1]["text"]
    assert "drop this proposal" in text and "a different proposal is a new card" in text


def test_a_new_version_after_an_approval_can_be_approved(tmp_path, slack):
    rec = saved()
    core.decide(rec, 1, "approved", "UAPPROVER")
    store.save(rec)
    cli.main(["revise", "AP-0001", card_file(tmp_path, changes="the version you pick")])
    rec = store.load("AP-0001")
    assert rec["status"] == "pending" and rec["decision"] is None and rec["versions"][0]["decision"]["verdict"] == "approved"
    assert core.mode_for(rec) == "open"
    assert core.decide(rec, 2, "approved", "UAPPROVER") == "ok"
    assert any(p.get("broadcast") and "posted v2" in p["text"] for p in slack)   # the channel shows it


def test_a_declined_card_gets_a_new_card_not_a_new_version(tmp_path, slack):
    rec = saved()
    core.decide(rec, 1, "declined", "UAPPROVER")
    store.save(rec)
    with pytest.raises(SystemExit):
        cli.main(["revise", "AP-0001", card_file(tmp_path, changes="x")])


def test_withdrawing_takes_the_buttons_away_and_presses_say_why(slack):
    saved()
    cli.main(["withdraw", "AP-0001", "I dropped this version; a new script is coming as a new card."])
    rec = store.load("AP-0001")
    blocks = cards.card_blocks(rec, 1, core.mode_for(rec))
    assert rec["status"] == "withdrawn" and not [b for b in blocks if b["type"] == "actions"]
    assert blocks[0]["text"]["text"].startswith("↩️ Withdrawn:")
    assert "a new script is coming" in json.dumps(blocks)
    assert core.decide(rec, 1, "approved", "UAPPROVER") == "withdrawn"
    assert core.take_replies(rec, [("9.0", "ok")]) == "comment"


def test_a_question_shows_in_the_channel(slack):
    saved()
    cli.main(["ask", "AP-0001", "Mexico at MX$899 or MX$999?"])
    assert slack[-1]["broadcast"] and "MX$899" in slack[-1]["text"]


def test_evidence_as_one_block_of_text_is_one_post(slack):
    rec = saved()
    cli._post_evidence(rec, "Last month's bank statement: $1,000 in, $400 out.")
    assert slack == [{"note": "Last month's bank statement: $1,000 in, $400 out."}]


def test_files_named_in_evidence_are_uploaded_so_they_open_in_slack(tmp_path, monkeypatch, slack):
    rec = saved()
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "answer.md").write_text("the answer")
    (tmp_path / "recipe.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    cli._post_evidence(rec, [f"Recipe and both comparisons: {tmp_path / 'recipe.json'}", "notes/answer.md",
                             "Checks: the plan check passed", "Workbook: https://docs.google.com/spreadsheets/d/x"])
    assert slack[0] == {"file": str(tmp_path / "recipe.json"), "title": "Recipe and both comparisons"}
    assert slack[1] == {"file": str(tmp_path / "notes" / "answer.md"), "title": "answer.md"}
    assert slack[2:] == [{"note": "Checks: the plan check passed"}, {"note": "Workbook: https://docs.google.com/spreadsheets/d/x"}]


def test_evidence_in_another_shape_is_refused():
    assert any("evidence" in e for e in cards.validate(dict(SAMPLES["money"], evidence={"a": 1})))
    assert cards.validate(dict(SAMPLES["money"], evidence="one block of text")) == []
