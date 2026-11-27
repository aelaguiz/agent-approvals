import json
import multiprocessing
import os

import pytest

from approvals import cards, config, core, store
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
    for mode in ("open", "final"):
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


@pytest.mark.parametrize("stands", [None, "yes", {}, {"whole": "nothing larger"},
    dict(SAMPLES["money"]["stands"], whole=""), dict(SAMPLES["money"]["stands"], whole="x" * 161),
    dict(SAMPLES["money"]["stands"], his_latest=" "), dict(SAMPLES["money"]["stands"], his_latest="x" * 601),
    dict(SAMPLES["money"]["stands"], his_latest=3), dict(SAMPLES["money"]["stands"], state=[]),
    dict(SAMPLES["money"]["stands"], state="approved"), dict(SAMPLES["money"]["stands"], extra="x")])
def test_malformed_stands_gets_only_the_question_on_a_first_version(stands):
    card = dict(SAMPLES["money"], stands=stands)
    assert cards.validate(card) == [cards.STANDS_QUESTION]
    assert cards.validate(dict(card, changes="Updated the number"), revision=True) == []


def test_missing_stands_gets_the_question_but_a_revision_can_omit_it():
    card = dict(SAMPLES["money"])
    del card["stands"]
    assert cards.validate(card) == [cards.STANDS_QUESTION]
    assert cards.validate(dict(card, changes="Updated the number"), revision=True) == []


def test_still_shaping_refuses_the_first_version_not_a_revision():
    card = dict(SAMPLES["money"], stands={"whole": "The new home screen", "his_latest": '"Hold on", today in chat with Design.',
                                        "state": "still-shaping"})
    assert cards.validate(card) == ["No card: he is still shaping The new home screen. Take this piece to the agent he is shaping it with, "
                                    "take back any card you promised, and tell whoever asked you to card it."]
    assert cards.validate(dict(card, changes="Updated the number"), revision=True) == []


@pytest.mark.parametrize("state", ["not-on-his-mind", "new-to-him", "yes-to-the-whole", "still-shaping"])
def test_only_a_yes_to_the_whole_is_shown_near_part_of(state):
    stands = {"whole": "x" * 160, "his_latest": '"Yes to this" on Monday in chat with Design.', "state": state}
    card = dict(SAMPLES["money"], part_of="A plan", stands=stands)
    if state != "still-shaping":
        assert cards.validate(card) == []
    rec = make_rec(card)
    for mode in ("open", "final", "view"):
        blocks = cards.card_blocks(rec, 1, mode)
        if state == "yes-to-the-whole":
            assert blocks[2] == cards.ctx(f"Your yes: {stands['his_latest']}")
        else:
            assert "Your yes:" not in json.dumps(blocks)


def test_revision_without_stands_shows_the_first_versions_yes_and_trims_it():
    first = dict(SAMPLES["money"], stands={"whole": "A plan", "his_latest": "&" * 600, "state": "yes-to-the-whole"})
    rec = make_rec(first)
    revised = dict(first, stands=dict(first["stands"], his_latest="A later yes"), changes="Updated the number")
    omitted = dict(revised)
    del omitted["stands"]
    rec["versions"] += [{"n": 2, "card": revised}, {"n": 3, "card": omitted}]
    blocks = cards.card_blocks(rec, 3, "open")
    expected = blocks[2]["elements"][0]["text"]
    assert expected.startswith("Your yes: &amp;") and len(expected) <= 1900 and expected.endswith("…")
    assert cards.card_blocks(rec, 2, "view")[2] == cards.ctx("Your yes: A later yes")


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


def test_local_all_reuses_unchanged_records_and_returns_copies(monkeypatch):
    config.ensure_dirs()
    first, second = (config.REQUESTS / f"AP-{n:04d}.json" for n in (1, 2))
    first.write_text(json.dumps({"id": "AP-0001", "values": ["first"]}))
    second.write_text(json.dumps({"id": "AP-0002", "values": ["second"]}))
    reads = []
    read_text = type(first).read_text

    def counted_read(path, *args, **kwargs):
        if path in (first, second):
            reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(first), "read_text", counted_read)
    records = store.local_all()
    records[1]["values"].append("mutated")
    first.write_text(json.dumps({"id": "AP-0001", "values": ["changed"]}))
    assert store.local_all() == [
        {"id": "AP-0001", "values": ["changed"]},
        {"id": "AP-0002", "values": ["second"]},
    ]
    assert reads == [first, second, first]


def test_readers_share_one_copy_until_a_card_is_written(tmp_home):
    config.ensure_dirs()
    store._write({"id": "AP-0001", "values": ["first"]})
    a = store.local_all(shared=True)
    assert store.local_all(shared=True) is a
    assert store.local_all() is not a
    store._write({"id": "AP-0002", "values": ["second"]})
    b = store.local_all(shared=True)
    assert b is not a and [r["id"] for r in b] == ["AP-0001", "AP-0002"]


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
