"""One decision per card: a row carries its own words above its buttons, and several cards can go out as a set."""
import json

import pytest

from approvals import cards, cli, slackapi, store
from samples import SAMPLES
from test_cards_store import make_rec


def replies_card(**kw):
    return dict(SAMPLES["money"], parts=[
        {"title": "A yearly charge after a cancelled trial", "quotes": [
            {"label": "Their words", "text": "I cancelled my free trial yesterday, but I was charged anyway."},
            {"label": "Your reply", "text": "Sorry this sat so long. Apple takes the first payment up to a day early."}]},
        {"title": "A month bought by mistake", "quotes": [{"label": "Your reply", "text": "We've refunded the month."}]},
    ], **kw)


def test_a_rows_words_sit_between_its_title_and_its_buttons():
    rec = make_rec(replies_card())
    assert not cards.validate(rec["versions"][0]["card"])
    blocks = [json.dumps(b) for b in cards.card_blocks(rec, 1, "open")]
    title = next(i for i, b in enumerate(blocks) if "A yearly charge after a cancelled trial" in b)
    words = next(i for i, b in enumerate(blocks) if "Your reply:" in b and "sat so long" in b)
    buttons = next(i for i, b in enumerate(blocks) if '"actions"' in b and "|p1:approve" in b)
    assert title < words < buttons
    assert not any("read:" in b for b in blocks)


def test_words_a_row_turns_on_never_hide_behind_a_button():
    row_button = replies_card()
    row_button["parts"][0]["quotes"][1]["button"] = "Read reply 1"
    assert any("takes no button" in e for e in cards.validate(row_button))
    card_button = dict(replies_card(), quotes=[{"label": "Reply 1", "text": "Hi", "button": "Read reply 1"}])
    errs = cards.validate(card_button)
    assert any("never behind a Read button" in e and "as a set" in e for e in errs)


def test_a_card_too_big_for_slack_says_to_send_a_set():
    card = dict(SAMPLES["money"], parts=[{"title": f"Row {j}", "quotes": [{"label": "Words", "text": "x"}] * 3} for j in range(10)])
    assert any("send it as a set" in e for e in cards.validate(card))


@pytest.fixture
def posting(monkeypatch):
    posts = []
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-asker")
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, thread_ts=None, broadcast=False:
                        posts.append({"text": text, "blocks": blocks}) or f"{100 + len(posts)}.0")
    monkeypatch.setattr(slackapi, "permalink", lambda ts: "https://slack/x")
    return posts


def card_files(tmp_path, n, broken=None):
    paths = []
    for i in range(1, n + 1):
        p = tmp_path / f"c{i}.json"
        card = dict(SAMPLES["money"], title=f"Reply to person {i}", part_of="Answer people waiting on us")
        if i == broken:
            card.pop("why")
        p.write_text(json.dumps(card))
        paths.append(str(p))
    return paths


def test_several_cards_go_out_together_each_its_own_decision_showing_its_place(tmp_path, posting):
    cli.main(["submit", *card_files(tmp_path, 3)])
    recs = sorted(store.all_records(), key=lambda r: r["id"])
    assert [r["set"] for r in recs] == [{"n": 1, "of": 3}, {"n": 2, "of": 3}, {"n": 3, "of": 3}]
    assert [p["text"].split(": ", 1)[1] for p in posting] == [
        "Reply to person 1 · 1 of 3", "Reply to person 2 · 2 of 3", "Reply to person 3 · 3 of 3"]
    frame = json.dumps(posting[1]["blocks"][1])
    assert "Part of: Answer people waiting on us \\u00b7 2 of 3 \\u00b7" in frame
    assert all(r["thread_ts"] for r in recs) and len({r["thread_ts"] for r in recs}) == 3


def test_a_set_with_one_bad_card_posts_none(tmp_path, posting, capsys):
    with pytest.raises(SystemExit):
        cli.main(["submit", *card_files(tmp_path, 3, broken=2)])
    assert "Card 2 of 3" in capsys.readouterr().err and posting == [] and store.all_records() == []


def test_one_card_alone_shows_no_place_in_a_set(tmp_path, posting):
    cli.main(["submit", *card_files(tmp_path, 1)])
    rec = store.all_records()[0]
    assert "set" not in rec and " of " not in posting[0]["text"]
