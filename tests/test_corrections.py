"""A card type's open corrections, kept in the pod dashboard's record, come with the card's form: `read` and `submit`
print those a card doesn't name, and `submit` sends the ones it names (corrections_seen) with the card, for the record
to check and to keep as shown to the agent that put it up."""
import json
from types import SimpleNamespace

import pytest

from approvals import cli, hub, pods, reader, store
from test_kinds import slack  # noqa: F401

OPEN = [{"id": "C-6", "title": "Say what the money buys", "says": "The last two budget cards said how much.\nNot what for."},
        {"id": "C-9", "title": "One decision per card", "says": "Split a card that asks two things."}]
CARD = {"type": "experiment", "title": "Show a search hint", "part_of": None,
        "stands": {"whole": "Search hint", "his_latest": "No earlier words or open discussion; checked today with Product.", "state": "new-to-him"},
        "why": "Readers can find the search box.", "facts": [["Place", "Library"]],
        "approves": "The search hint", "does_not_approve": "A new search engine"}


@pytest.fixture
def held(monkeypatch):
    asked = []
    monkeypatch.setattr(store, "corrections", lambda card_type: asked.append(card_type) or (OPEN if card_type == "experiment" else []))
    return asked


def printed(text):
    return [line for line in text.splitlines() if line]


def test_read_prints_the_open_corrections_a_card_doesnt_name_whole(held, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(reader, "run", lambda *args: SimpleNamespace(returncode=0, stdout="HE WOULD STOP AT:\nnowhere\n"))
    path = tmp_path / "card.json"
    path.write_text(json.dumps(CARD | {"corrections_seen": ["C-6"]}))
    cli.main(["read", str(path)])
    assert printed(capsys.readouterr().out) == [
        "HE WOULD STOP AT:", "nowhere",
        'Open corrections for experiment cards, left by earlier ones, printed below. A new experiment card names each '
        'in its JSON: "corrections_seen": ["C-6", "C-9"]',
        "C-6 Say what the money buys", "  The last two budget cards said how much.", "  Not what for.",
        "C-9 One decision per card", "  Split a card that asks two things."]

    path.write_text(json.dumps(CARD | {"corrections_seen": ["C-9", "C-6"]}))
    cli.main(["read", str(path)])
    assert printed(capsys.readouterr().out) == ["HE WOULD STOP AT:", "nowhere"]
    path.write_text(json.dumps(CARD | {"type": "budget"}))
    cli.main(["read", str(path)])
    assert printed(capsys.readouterr().out) == ["HE WOULD STOP AT:", "nowhere"]
    assert held == ["experiment", "experiment", "budget"]


def test_read_says_so_before_any_reader_when_the_corrections_cant_be_read(monkeypatch, tmp_path, capsys):
    def down(card_type):
        raise store.StoreError("the approvals hub on hubbox isn't answering")
    monkeypatch.setattr(store, "corrections", down)
    monkeypatch.setattr(reader, "run", lambda *args: pytest.fail("reader started"))
    (tmp_path / "card.json").write_text(json.dumps(CARD))
    with pytest.raises(SystemExit):
        cli.main(["read", str(tmp_path / "card.json")])
    assert "Cannot read the open corrections: the approvals hub on hubbox isn't answering" in capsys.readouterr().err


def test_submit_sends_the_corrections_named_with_the_card_and_who_put_it_up(held, monkeypatch, tmp_path, capsys, slack):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "fixture")
    monkeypatch.setattr(pods, "here", lambda: {"pod": "product", "copy": "bots"})
    path = tmp_path / "card.json"
    path.write_text(json.dumps(CARD | {"corrections_seen": ["C-6", "C-9"]}))
    cli.main(["submit", str(path)])
    out = capsys.readouterr()
    rec = store.load(out.out.splitlines()[0])
    assert rec["corrections_seen"] == ["C-6", "C-9"]
    assert "corrections_seen" not in rec["versions"][0]["card"] and rec["versions"][0]["actor"] == "product-bots"
    assert "corrections" not in out.err

    # A card that names none still goes up (the record refuses it once its rule is on), and the agent is shown them.
    path.write_text(json.dumps(CARD | {"title": "Show a second hint"}))
    cli.main(["submit", str(path)])
    out = capsys.readouterr()
    rid = out.out.splitlines()[0]
    assert "corrections_seen" not in store.load(rid)
    assert printed(out.err)[:2] == [
        f"{rid} named none or not all of its type's open corrections in corrections_seen.",
        'Open corrections for experiment cards, left by earlier ones, printed below. A new experiment card names each '
        'in its JSON: "corrections_seen": ["C-6", "C-9"]']


def test_a_machine_reads_the_corrections_through_the_hub(monkeypatch):
    srv = hub.serve(port=0)
    try:
        monkeypatch.setattr(store, "local_corrections", lambda card_type: OPEN if card_type == "player_message" else [])
        client = hub.Client.__new__(hub.Client)
        client.url, client.token = f"http://127.0.0.1:{srv.server_address[1]}", srv.token
        assert client.corrections("player_message") == OPEN
        assert client.corrections("budget") == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_without_a_record_a_card_type_has_no_corrections(monkeypatch):
    monkeypatch.setattr(store, "record", lambda: None)
    assert store.local_corrections("experiment") == []


def test_guide_puts_the_open_corrections_with_his_line_before_how_to_write_the_card(monkeypatch, capsys):
    line = {"card": "AP-0042", "at": "2026-11-20T21:22:43+00:00", "words": "NEVER email a competitor", "by": "Sam"}
    monkeypatch.setattr(store, "corrections", lambda card_type: [OPEN[0] | {"line": line}, OPEN[1] | {"line": None}])
    cli.main(["guide", "experiment"])
    out = printed(capsys.readouterr().out)
    assert out[:7] == [
        'Open corrections for experiment cards, left by earlier ones, printed below. A new experiment card names each '
        'in its JSON: "corrections_seen": ["C-6", "C-9"]',
        "C-6 Say what the money buys", "  The last two budget cards said how much.", "  Not what for.",
        "  Sam, Nov 20, 9:22 pm on AP-0042: NEVER email a competitor",
        "C-9 One decision per card", "  Split a card that asks two things."]
    assert out[7] == "## experiment" and "## page" not in out and "## budget" not in out


def test_guide_still_says_how_to_write_the_card_when_the_corrections_cant_be_read(monkeypatch, capsys):
    def down(card_type):
        raise store.StoreError("the approvals hub on hubbox isn't answering")
    monkeypatch.setattr(store, "corrections", down)
    cli.main(["guide", "budget"])
    out = printed(capsys.readouterr().out)
    assert out[0] == "Cannot read the open corrections for budget cards: the approvals hub on hubbox isn't answering"
    assert "## budget" in out and "## purchase" not in out

    monkeypatch.setattr(store, "corrections", lambda card_type: [])
    cli.main(["guide", "account"])
    out = printed(capsys.readouterr().out)
    assert out[:2] == ["No open corrections for account cards.", "## account"]


def test_guide_refuses_a_type_there_is_no_card_for(capsys):
    with pytest.raises(SystemExit):
        cli.main(["guide", "tweet"])
    assert "No card type 'tweet'" in capsys.readouterr().err
