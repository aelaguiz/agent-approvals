"""A card's "Part of:" names the pod dashboard's thing it decides. Cards no longer keep a step on a Basecamp card: one
that still names a Basecamp card is refused, saying what to name instead, and a card made before keeps its link."""
import json
import subprocess

import pytest

from approvals import cards, cli, core, listener, slackapi, store
from samples import SAMPLES

CARD = "https://app.basecamp.com/1000001/buckets/111/card_tables/cards/222"


@pytest.fixture
def posts(monkeypatch):
    # submit refuses a shell that is no agent's session, so these tests are the session they post from: they pass
    # in a plain shell as in an agent's, and what submit refuses stays as it is.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-part-of")
    posted = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, thread_ts=None, broadcast=False:
                        posted.append(blocks) or f"{100 + len(posted)}.0")
    monkeypatch.setattr(slackapi, "permalink", lambda ts: f"https://slack.example/p{ts}")
    for name in ("update", "react", "unreact", "note"):
        monkeypatch.setattr(slackapi, name, lambda *a, **k: None)
    real_run = subprocess.run

    def no_basecamp(cmd, *a, **k):
        assert "basecamp" not in cmd[:1], f"something ran basecamp: {cmd}"
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", no_basecamp)
    return posted


def submit(tmp_path, *flags, **card):
    p = tmp_path / "card.json"
    p.write_text(json.dumps(dict(SAMPLES["money"], part_of="Pay the bills on time", **card)))
    cli.main(["submit", str(p), "--agent", "CFO", *flags])
    return store.all_records()[-1]["id"]


def test_part_of_names_the_thing_the_card_decides(posts, tmp_path):
    submit(tmp_path, about="T-12")
    assert "Part of: Pay the bills on time · T-12" in json.dumps(posts[0], ensure_ascii=False)


@pytest.mark.parametrize("flags,card", [(("--basecamp", CARD), {}), ((), {"basecamp": [CARD]})])
def test_a_card_naming_a_basecamp_card_is_refused_and_told_what_to_name(posts, tmp_path, capsys, flags, card):
    with pytest.raises(SystemExit):
        submit(tmp_path, *flags, about="T-12", **card)
    err = capsys.readouterr().err
    assert "--basecamp" in err and '"about"' in err and "pd open" in err
    assert not posts and not store.all_records()


def test_a_card_made_before_keeps_its_basecamp_link_and_nothing_writes_there(posts, tmp_path):
    rid = submit(tmp_path, about="T-12")
    store.update(rid, lambda r: r.update(basecamp=[{"url": CARD, "step": "900"}]))
    store.update(rid, lambda r: core.decide(r, 1, "approved", "UAPPROVER"))
    listener.Listener().deliver_due()
    rec = store.load(rid)
    assert f"Part of: <{CARD}|Pay the bills on time>" in json.dumps(cards.card_blocks(rec, 1, "final"))
    assert rec["basecamp"] == [{"url": CARD, "step": "900"}]


def test_a_card_naming_no_thing_still_goes_up_and_its_asker_is_asked_for_one(posts, tmp_path, capsys):
    rid = submit(tmp_path)
    assert posts and store.load(rid)["status"] == "pending"
    err = capsys.readouterr().err
    assert f"{rid} names no thing it decides" in err and f"pd tie <id> {rid}" in err
    submit(tmp_path, about="T-12")
    assert "names no thing it decides" not in capsys.readouterr().err
