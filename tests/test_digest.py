"""The morning list: posted once a day, lists what waits on the approver, and crosses lines off as they answer."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from approvals import core, digest, store

MON_8AM = datetime(2026, 10, 26, 8, 0, 30, tzinfo=timezone.utc)   # the test config's timezone is UTC


@pytest.fixture
def slack(monkeypatch):
    posted, updated = [], []

    def post(blocks, text):
        posted.append((blocks, text))
        return f"100.{len(posted)}"
    monkeypatch.setattr(digest.slackapi, "post", post)
    monkeypatch.setattr(digest.slackapi, "update", lambda ts, blocks, text: updated.append((ts, blocks, text)))
    monkeypatch.setattr(digest.slackapi, "message_url", lambda ts: f"https://x.slack.com/p{ts}")
    return posted, updated


def card(n, status="pending", deadline=MON_8AM + timedelta(hours=8), test=False, type="creative", events=()):
    rec = {"id": f"AP-{n:04d}", "title": f"Card {n}", "agent": "Ads specialist", "status": status, "test": test,
           "versions": [{"n": 1, "card": {"type": type}}], "thread_ts": f"{n}.0", "decision": None,
           "created": store.iso(MON_8AM - timedelta(days=1)), "deadline": store.iso(deadline),
           "events": [dict(e) for e in events], "outbox": []}
    store.save(rec)
    return rec


def text_of(blocks):
    out = []
    for b in blocks:
        if b["type"] == "section":
            out.append(b["text"]["text"])
        elif b["type"] == "context":
            out.append(b["elements"][0]["text"])
    return "\n".join(out)


def test_posts_once_a_day_from_8am(slack):
    posted, updated = slack
    card(1)
    digest.tick(MON_8AM - timedelta(minutes=1))
    assert posted == []
    digest.tick(MON_8AM)
    digest.tick(MON_8AM + timedelta(hours=3))
    assert len(posted) == 1 and updated == []
    digest.tick(MON_8AM + timedelta(days=1))
    assert len(posted) == 2


def test_lists_only_cards_waiting_on_the_approver_soonest_first(slack):
    posted, _ = slack
    card(1, deadline=MON_8AM + timedelta(days=3))
    card(2, status="question", deadline=MON_8AM + timedelta(days=1), type="price")
    card(3, deadline=MON_8AM + timedelta(hours=6))
    card(4, status="revising")
    card(5, status="approved")
    card(6, test=True)
    digest.tick(MON_8AM)
    blocks, text = posted[0]
    body = text_of(blocks)
    assert text.startswith("<@UAPPROVER>") and "3 cards are waiting on you" in text
    for absent in ("AP-0004", "AP-0005", "AP-0006"):
        assert absent not in body
    order = [body.index(s) for s in ("Expires today", "AP-0003", "Expires tomorrow", "AP-0002", "Later", "AP-0001")]
    assert order == sorted(order)
    assert "🏷️ <https://x.slack.com/p2.0|AP-0002>  *Card 2*" in body
    assert "Ads specialist · ❓ asked you a question · since Sun 8:00 am · expires Tue 8:00 am" in body
    assert "expires 2:00 pm" in body   # today's deadline shows no weekday


def test_answered_lines_cross_off_and_a_new_version_brings_one_back(slack, monkeypatch):
    posted, updated = slack
    card(1)
    card(2)
    digest.tick(MON_8AM)
    monkeypatch.setattr(store, "now", lambda: MON_8AM + timedelta(hours=1))
    store.update("AP-0001", lambda r: core.decide(r, 1, "approved", by="UAPPROVER"))
    digest.tick(MON_8AM + timedelta(hours=1))
    digest.tick(MON_8AM + timedelta(hours=2))   # nothing changed since: no edit
    assert len(updated) == 1
    ts, blocks, text = updated[0]
    body = text_of(blocks)
    assert ts == "100.1" and "1 of 2 still waiting on you" in text
    assert "~<https://x.slack.com/p1.0|AP-0001>  Card 1~" in body and "✅ You approved it, 9:00 am" in body
    assert "updated 9:00 am" in body

    def revise(r):
        r["status"], r["decision"] = "pending", None
        store.event(r, "revised", version=2)
    store.update("AP-0001", revise)
    digest.tick(MON_8AM + timedelta(hours=3))
    _, blocks, text = updated[-1]
    assert "2 cards are waiting on you" in text and "~" not in text_of(blocks)


def test_each_way_a_card_stops_waiting_says_what_happened(slack):
    _, updated = slack
    for n in range(1, 5):
        card(n)
    digest.tick(MON_8AM)
    store.update("AP-0001", lambda r: core.decide(r, 1, "declined", by="UAPPROVER"))
    store.update("AP-0002", lambda r: core.take_replies(r, [("5.0", "shorter please")]))
    store.update("AP-0003", core.expire)

    def withdraw(r):
        r["status"], r["withdrawn"] = "withdrawn", {"at": store.iso(), "text": "moot"}
    store.update("AP-0004", withdraw)
    digest.tick(MON_8AM + timedelta(hours=1))
    _, blocks, text = updated[-1]
    body = text_of(blocks)
    assert "Nothing left waiting on you" in text
    assert "❌ You declined it" in body and "💬 You replied" in body and "the agent is working on it" in body
    assert "⌛ No answer by 4:00 pm, so not approved" in body and "↩️ The agent withdrew it" in body


def test_cards_closed_with_no_answer_since_the_last_list(slack):
    posted, _ = slack
    yesterday = MON_8AM - timedelta(days=1)
    (digest.config.HOME / "digest.json").write_text(json.dumps(
        {"date": "2026-10-25", "ts": "99.1", "listed": [], "closed": [], "posted_at": store.iso(yesterday),
         "updated_at": None, "content": "x"}))
    card(1, status="expired", deadline=yesterday + timedelta(hours=9),
         events=[{"kind": "expired", "at": store.iso(yesterday + timedelta(hours=9))}])
    card(2, status="expired", deadline=yesterday - timedelta(hours=2),
         events=[{"kind": "expired", "at": store.iso(yesterday - timedelta(hours=2))}])
    digest.tick(MON_8AM)
    blocks, text = posted[0]
    body = text_of(blocks)
    assert text == "<@UAPPROVER> ☀️ Nothing is waiting on you"
    assert "Closed with no answer since the last list" in body and "A reply in the card's thread reopens it" in body
    assert "AP-0001" in body and "closed Sun 5:00 pm" in body and "AP-0002" not in body


def test_nothing_waiting_is_one_line_and_mentions_no_one(slack):
    posted, _ = slack
    card(1, status="done")
    digest.tick(MON_8AM)
    blocks, text = posted[0]
    assert text == "☀️ Nothing is waiting on you" and len(blocks) == 2


def test_a_failed_post_is_tried_again_next_sweep(slack, monkeypatch):
    posted, _ = slack
    card(1)
    ok = digest.slackapi.post

    def down(blocks, text):
        raise RuntimeError("slack is down")
    monkeypatch.setattr(digest.slackapi, "post", down)
    with pytest.raises(RuntimeError):
        digest.tick(MON_8AM)
    assert digest.load_state() is None
    monkeypatch.setattr(digest.slackapi, "post", ok)
    digest.tick(MON_8AM + timedelta(seconds=30))
    assert len(posted) == 1 and digest.load_state()["listed"] == ["AP-0001"]


def test_a_list_asked_for_replaces_the_last_one(slack, monkeypatch):
    posted, updated = slack
    card(1)
    digest.tick(MON_8AM)
    card(2)   # arrives after the morning list
    digest.tick(MON_8AM + timedelta(hours=7), asked=True)
    blocks, text = posted[1]
    assert text == "📋 2 cards are waiting on you"   # no ping: the approver just asked
    assert "AP-0002" in text_of(blocks)
    ts, blocks, _ = updated[0]
    assert ts == "100.1" and text_of(blocks) == "Replaced by <https://x.slack.com/p100.2|a newer list> posted 3:00 pm."
    monkeypatch.setattr(store, "now", lambda: MON_8AM + timedelta(hours=8))
    store.update("AP-0001", lambda r: core.decide(r, 1, "approved", by="UAPPROVER"))
    digest.tick(MON_8AM + timedelta(hours=8))
    assert updated[-1][0] == "100.2" and "1 of 2 still waiting on you" in updated[-1][2]   # only the newest is kept current
    digest.tick(MON_8AM + timedelta(days=1))
    assert posted[-1][1].startswith("<@UAPPROVER> ☀️") and updated[-1][0] == "100.2"


def test_asking_before_8am_still_gets_the_morning_list(slack):
    posted, _ = slack
    card(1)
    digest.tick(MON_8AM - timedelta(hours=2), asked=True)
    digest.tick(MON_8AM)
    digest.tick(MON_8AM + timedelta(hours=1))
    assert [t for _, t in posted] == ["📋 1 card is waiting on you", "<@UAPPROVER> ☀️ 1 card is waiting on you"]


def buttons_of(blocks):
    return [[(e["text"]["text"], e["value"]) for e in b["elements"]] for b in blocks if b["type"] == "actions"]


def shaped(n, **card_fields):
    rec = card(n)
    rec["versions"][0]["card"].update(card_fields)
    store.save(rec)


def test_each_waiting_line_answers_its_card_in_one_tap(slack):
    posted, _ = slack
    card(1)
    rec = card(2, type="experiment")
    rec["versions"].append({"n": 2, "card": {"type": "experiment"}})
    store.save(rec)
    shaped(3, parts=[{"title": "a"}, {"title": "b"}])
    shaped(4, parts=[{"title": "a"}, {"title": "b", "own_yes": True}])
    shaped(5, items=["x", "y", "z"])
    shaped(6, options=[{"name": "A", "title": "a panel"}, {"name": "B", "title": "a rail"}], pick="B")
    shaped(7, options=[{"name": "A", "title": "a panel", "image": "a.png"}, {"name": "B", "title": "a rail", "image": "b.png"}])
    rec = card(8, status="approved")   # approved, with a condition only the approver can clear: listed, nothing to tap
    rec["timeline"] = [{"text": "play it before it merges", "kind": "on_you", "part": None, "at": rec["created"]}]
    store.save(rec)
    digest.tick(MON_8AM)
    no = lambda i, v=1: [("Deny", f"AP-000{i}|{v}|decline"), ("Remove", f"AP-000{i}|{v}|remove")]
    assert buttons_of(posted[0][0]) == [
        [("Approve", "AP-0001|1|approve")] + no(1),
        [("Approve", "AP-0002|2|approve")] + no(2, 2),   # the newest version's
        [("Approve all 2", "AP-0003|1|all")] + no(3),
        [("Approve 1 of 2", "AP-0004|1|all")] + no(4),
        [("Approve all 3", "AP-0005|1|approve")] + no(5),
        [("A: a panel", "AP-0006|1|pick:0"), ("B: a rail", "AP-0006|1|pick:1")] + no(6),
        no(7),   # a choice in pictures is decided by looking at them
    ]
    assert "AP-0008" in text_of(posted[0][0]) and "waiting on you: play it before it merges" in text_of(posted[0][0])


def test_remove_crosses_it_off_and_tells_the_agent_it_is_closed_without_a_no(slack, monkeypatch):
    posted, updated = slack
    card(1)
    card(2)
    digest.tick(MON_8AM)
    monkeypatch.setattr(store, "now", lambda: MON_8AM + timedelta(hours=1))
    assert store.update("AP-0001", lambda r: core.press(r, 1, "remove", by="UAPPROVER")) == "ok"
    rec = store.load("AP-0001")
    assert rec["status"] == "withdrawn" and rec["withdrawn"]["by"] == "UAPPROVER"
    msg = rec["outbox"][-1]["text"]
    assert "Sam removed it from his list" in msg and "nothing still waiting on it is approved" in msg
    assert "Removing isn't declining" in msg and "approvals ack AP-0001" in msg
    assert store.update("AP-0001", lambda r: core.press(r, 1, "approve", by="UAPPROVER")) == "removed"
    digest.tick(MON_8AM + timedelta(hours=1))
    _, blocks, text = updated[-1]
    assert "1 of 2 still waiting on you" in text and "🗑️ You removed it, 9:00 am" in text_of(blocks)
    assert [row[0][1] for row in buttons_of(blocks)] == ["AP-0002|1|approve"]   # answered lines have no buttons


def test_a_long_list_stays_within_slacks_limit_and_says_how_many_more(slack):
    posted, _ = slack
    for n in range(1, 21):
        card(n)
    digest.tick(MON_8AM)
    blocks, _ = posted[0]
    assert len(blocks) == 50 and len(buttons_of(blocks)) == 15
    assert "…and 5 more. Answer some, then post in the channel for a fresh list." in text_of(blocks)


def test_a_list_that_fills_its_room_exactly_still_fits_with_its_headline(slack):
    # Two headings and 15 lines filled the room to the block, so the headline added after made 51 (November 12).
    posted, _ = slack
    for n in range(1, 17):
        card(n, deadline=MON_8AM + (timedelta(hours=8) if n <= 10 else timedelta(days=1) if n <= 15 else timedelta(days=3)))
    digest.tick(MON_8AM)
    blocks, _ = posted[0]
    assert len(blocks) <= 50 and "more. Answer some" in text_of(blocks)
