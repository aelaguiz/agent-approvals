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
