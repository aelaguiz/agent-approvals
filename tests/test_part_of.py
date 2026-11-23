"""A card's "Part of:" names the pod dashboard's thing it decides."""
import json

import pytest


from approvals import cards, cli, slackapi, store
from samples import SAMPLES


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
    return posted


def submit(tmp_path, *flags, **card):
    p = tmp_path / "card.json"
    p.write_text(json.dumps(dict(SAMPLES["money"], part_of="Pay the bills on time", **card)))
    cli.main(["submit", str(p), "--agent", "CFO", *flags])
    return store.all_records()[-1]["id"]


def test_part_of_names_the_thing_the_card_decides(posts, tmp_path):
    submit(tmp_path, about="T-12")
    assert "Part of: Pay the bills on time · T-12" in json.dumps(posts[0], ensure_ascii=False)


def test_old_card_uses_dashboard_thing_when_redrawn(posts, tmp_path):
    rid = submit(tmp_path, about="T-12")
    store.update(rid, lambda r: r.update(basecamp=[{"url": "https://example.com/old-card"}]))
    rec = store.load(rid)
    assert "Part of: Pay the bills on time · T-12" in json.dumps(cards.card_blocks(rec, 1, "final"), ensure_ascii=False)
    assert "https://example.com/old-card" not in json.dumps(cards.card_blocks(rec, 1, "final"))


def test_a_card_naming_no_thing_still_goes_up_and_its_asker_is_asked_for_one(posts, tmp_path, capsys):
    rid = submit(tmp_path)
    assert posts and store.load(rid)["status"] == "pending"
    err = capsys.readouterr().err
    assert f"{rid} names no thing it decides" in err and f"pd tie <id> {rid}" in err
    submit(tmp_path, about="T-12")
    assert "names no thing it decides" not in capsys.readouterr().err
