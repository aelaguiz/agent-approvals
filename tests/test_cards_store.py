import json
import multiprocessing
import os

import pytest

from approvals import cards, core, store
from samples import SAMPLES


def make_rec(card, **kw):
    rec = {"id": "AP-0001", "type": card["type"], "title": card["title"], "agent": "Tester", "created": store.iso(),
           "deadline": store.iso(), "status": "pending", "test": False,
           "versions": [{"n": 1, "card": card, "ts": "1.1"}], "thread_ts": "1.1", "last_seen": "1.1",
           "open_question": None, "decision": None, "done": None, "events": [], "outbox": [], "reach": {}, "moves": []}
    rec.update(kw)
    return rec


@pytest.mark.parametrize("t", sorted(SAMPLES))
def test_every_type_validates_and_renders(t):
    card = dict(SAMPLES[t])
    assert cards.validate(card) == []
    rec = make_rec(card)
    for mode in ("open", "final", "replaced"):
        blocks = cards.card_blocks(rec, 1, mode)
        assert blocks[0]["type"] == "header"
        for b in blocks:
            if b["type"] == "table":
                assert all(c["text"].strip() for row in b["rows"] for c in row)
    actions = [b for b in cards.card_blocks(rec, 1, "open") if b["type"] == "actions"]
    assert [e["value"] for e in actions[0]["elements"]] == ["AP-0001|1|approve", "AP-0001|1|decline"]


def test_missing_fields_are_named():
    errs = cards.validate({"type": "price", "title": "x", "facts": [["a", "b"]], "approves": "x", "does_not_approve": "y"})
    assert any("table" in e for e in errs)
    errs = cards.validate({"type": "creative", "title": "", "facts": [], "approves": "", "does_not_approve": ""})
    assert any("title" in e for e in errs) and any("facts" in e for e in errs) and any("image or a video" in e for e in errs)
    errs = cards.validate(dict(SAMPLES["price"], table={"columns": ["a", "b"], "rows": [["x", ""]]}))
    assert any("empty" in e for e in errs)
    assert any("changes" in e for e in cards.validate(SAMPLES["money"], revision=True))


def test_special_characters_are_escaped():
    rec = make_rec(SAMPLES["reply"])
    text = json.dumps(cards.card_blocks(rec, 1, "open"))
    assert "&amp; pick your play first &lt;really&gt;" in text
    assert "<https://reddit.com/r/x|Best books?>" in text


def _submit_one(home):
    os.environ["APPROVALS_HOME"] = home
    import importlib
    from approvals import config, store as st
    importlib.reload(config)
    importlib.reload(st)
    return st.create({"title": "x"})["id"]


def test_parallel_submits_get_distinct_numbers(tmp_home):
    home = os.environ["APPROVALS_HOME"]
    with multiprocessing.get_context("spawn").Pool(8) as pool:
        ids = pool.map(_submit_one, [home] * 20)
    assert len(set(ids)) == 20
    assert len(store.all_records()) == 20


def test_first_press_wins_and_old_versions_refuse():
    rec = make_rec(SAMPLES["money"])
    rec["versions"].append({"n": 2, "card": SAMPLES["money"], "copy_ts": "1.5"})
    assert core.decide(rec, 1, "approved", "U") == "stale"
    assert core.decide(rec, 2, "approved", "U") == "ok"
    assert core.decide(rec, 2, "declined", "U") == "already"
    assert rec["decision"]["verdict"] == "approved"
    assert len(rec["outbox"]) == 1 and "approved it" in rec["outbox"][0]["text"]
    assert rec["outbox"][0]["react_ts"] == "1.5"


def test_reply_is_feedback_or_answer_or_comment():
    rec = make_rec(SAMPLES["price"])
    assert core.take_replies(rec, [("2.0", "Do Brazil only"), ("2.1", "and hurry")]) == "feedback"
    assert rec["status"] == "revising"
    assert '"Do Brazil only" / "and hurry"' in rec["outbox"][-1]["text"]
    rec["open_question"] = {"text": "MX$899 or MX$999?"}
    assert core.take_replies(rec, [("3.0", "899")]) == "answer"
    assert "answered your question" in rec["outbox"][-1]["text"] and rec["open_question"] is None
    core.decide(rec, 1, "approved", "U")
    assert core.take_replies(rec, [("4.0", "nice")]) == "comment"
    assert rec["status"] == "approved"


def test_late_reply_reopens_an_expired_request():
    rec = make_rec(SAMPLES["money"])
    core.expire(rec)
    assert rec["status"] == "expired"
    core.take_replies(rec, [("5.0", "actually yes, but $50k")])
    assert rec["status"] == "revising" and rec["decision"] is None


def test_messages_to_agents_are_one_line():
    rec = make_rec(SAMPLES["price"])
    core.take_replies(rec, [("2.0", "line one\nline two")])
    assert "\n" not in rec["outbox"][-1]["text"]
    assert rec["outbox"][-1]["text"].startswith("[Approvals] AP-0001 v1")
