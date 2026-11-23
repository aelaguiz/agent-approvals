"""An email or reply card says whom it goes to as a field of its own (`recipients`), exactly as the place writes it,
so the pod dashboard's record can move the person whose route it is when the card is done, without reading the card's
words. A player's place holds the thread's or review's id, never their address. A card from before has none."""
import json

import pytest

from approvals import cards, cli, slackapi, store
from samples import SAMPLES

CREATOR = [{"place": "instagram", "handle": "@onecardsteve"}]


@pytest.fixture
def posts(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-recipients")
    posted = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, thread_ts=None, broadcast=False:
                        posted.append(blocks) or f"{100 + len(posted)}.0")
    monkeypatch.setattr(slackapi, "permalink", lambda ts: f"https://slack.example/p{ts}")
    for name in ("update", "react", "unreact", "note", "delete"):
        monkeypatch.setattr(slackapi, name, lambda *a, **k: None)
    return posted


def card(kind="reply", **kw):
    c = dict(SAMPLES[kind], part_of="Poker creators use Poker Skill", about="P-1")
    c.pop("recipients", None)
    return dict(c, **kw)


def submit(tmp_path, c):
    p = tmp_path / "card.json"
    p.write_text(json.dumps(c))
    cli.main(["submit", str(p), "--agent", "Creators"])
    return store.all_records()[-1]["id"]


@pytest.mark.parametrize("kind", ["email", "reply"])
def test_an_email_or_reply_card_naming_no_recipient_is_refused_saying_how_to_name_one(posts, tmp_path, capsys, kind):
    with pytest.raises(SystemExit):
        submit(tmp_path, card(kind))
    err = capsys.readouterr().err
    assert "recipients is required" in err and '"email thread"' in err and '"contact form"' in err
    assert not posts and not store.all_records()


def test_another_type_needs_none():
    assert cards.validate(card("money")) == []


@pytest.mark.parametrize("recipients,words", [
    ([], "a list of 1 to"),
    ([{"place": "email", "handle": "not an address"}], "the address alone"),
    ([{"place": "email", "handle": "Mark Ciccone <mark@stevensciccone.com>"}], "with no name or <>"),
    ([{"place": "instagram", "handle": "@one card"}], "with no link or spaces"),
    ([{"place": "youtube", "handle": "youtube.com/@ElRivieraPoker"}], "alone, with no link"),
    ([{"place": "email thread", "handle": "colten@example.com"}], "a player's address and phone stay off the card"),
    ([{"place": "player account", "handle": "colten@example.com"}], "a player's address and phone stay off the card"),
    ([{"place": "google play review", "handle": "gp:abc@x"}], "a player's address and phone stay off the card"),
    ([{"place": "mailing", "handle": "payers: a@b.com"}], "with no address"),
    ([{"place": "other", "handle": "a text to colten@example.com"}], "with no email address in it"),
    ([{"place": "other", "handle": "  "}], "where and whom, in a few words"),
    ([{"place": "carrier pigeon", "handle": "coo"}], "where none fits, it is \"other\""),
    ([{"place": "email", "handle": "a@b.com", "name": "A"}], "and nothing else"),
    ([{"place": "email", "handle": "a" * 200 + "@b.com"}], "up to 200"),
])
def test_each_recipient_is_a_listed_place_and_a_handle_of_its_shape(recipients, words):
    errs = cards.validate(card("email", recipients=recipients))
    assert any(words in e for e in errs), errs


@pytest.mark.parametrize("recipients", [
    [{"place": "email", "handle": "brantzenpoker@gmail.com"}],
    [{"place": "contact form", "handle": "torneos.poker/contacto"}],
    [{"place": "email thread", "handle": "1a2032b7d9e29555"}],
    [{"place": "google play review", "handle": "97b1cc82-4380-486a-96e6-b2b3e21a292d"}],
    [{"place": "mailing", "handle": "Saturday's 12 payers who turned renewal off"}],
    [{"place": "youtube", "handle": "@parttimepokerpro"}, {"place": "x", "handle": "riverratpoker"}],
    [{"place": "reddit", "handle": "r/poker"}, {"place": "slack", "handle": "#release-discussion"}],
    [{"place": "support ticket", "handle": "fly.io/dashboard/funcountry/support"}],
    [{"place": "forum", "handle": "forumserver.twoplustwo.com/69/online-no-limit-holdem-cash/40nl-quads-1865594/"}],
    [{"place": "player account", "handle": "8a41f0c2"}, {"place": "player account", "handle": "1187342"}],
    [{"place": "testers' feedback", "handle": "Colten's note of Nov 22"}],
    [{"place": "other", "handle": "Discord, #poker-help, to its moderators @mods"}],
])
def test_the_shapes_the_places_write(recipients):
    assert cards.validate(card("email", recipients=recipients)) == []


def test_the_card_he_reads_says_whom_it_goes_to_and_the_asker_is_told_what_its_done_moves(posts, tmp_path, capsys):
    rid = submit(tmp_path, card("reply", recipients=CREATOR))
    assert "*To:* @onecardsteve (instagram)" in json.dumps(posts[0], ensure_ascii=False)
    assert store.load(rid)["versions"][0]["card"]["recipients"] == CREATOR
    assert f"{rid} goes to @onecardsteve (instagram)" in capsys.readouterr().err


def test_a_new_version_keeps_whom_it_goes_to_unless_it_names_others(posts, tmp_path):
    rid = submit(tmp_path, card("reply", recipients=CREATOR))
    p = tmp_path / "v2.json"
    p.write_text(json.dumps(card("reply", changes="Shorter, per your note")))
    cli.main(["revise", rid, str(p)])
    assert store.load(rid)["versions"][1]["card"]["recipients"] == CREATOR
    other = [{"place": "instagram", "handle": "@allanlanchester"}]
    p.write_text(json.dumps(card("reply", changes="To Allan instead", recipients=other)))
    cli.main(["revise", rid, str(p)])
    assert store.load(rid)["versions"][2]["card"]["recipients"] == other


def test_a_card_from_before_the_field_is_drawn_as_it_was():
    old = card("email")   # no recipients: put up before the field, never guessed from its To fact or done line
    rec = {"id": "AP-0009", "agent": "agent", "deadline": store.iso(), "status": "done",
           "versions": [{"n": 1, "card": old}], "answers": {}, "events": []}
    assert "*To:*" not in json.dumps(cards.card_blocks(rec, 1, "open"))


@pytest.mark.parametrize("recipients", [None, [], [{"place": "instagram dm", "handle": "@onecardsteve"}],
                                        [{"to": "@onecardsteve"}]])
def test_a_refusal_shows_what_to_write_instead(recipients):
    c = card("email", recipients=recipients) if recipients is not None else card("email")
    errs = cards.validate(c)
    assert errs and any('"place": "email", "handle": "brantzenpoker@gmail.com"' in e or '"other"' in e for e in errs), errs
