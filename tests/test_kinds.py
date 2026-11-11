"""The kinds of card: each renders within Slack's limits in every state, and each answer reaches the agent as one
unambiguous message, a set's answers row by row."""
import json
from datetime import timedelta

import pytest

from approvals import cards, cli, config, core, digest, listener, slackapi, store
from kinds import KINDS
from samples import SAMPLES
from test_cards_store import make_rec


def rec_for(card, **kw):
    files = {key: f"F{i}" for i, (key, _, _) in enumerate(cards.pictures(card))}
    rec = make_rec(card, deadline=store.iso(store.now() + timedelta(hours=5)), **kw)
    rec["versions"][0]["files"] = files
    return rec


def txt(x):
    return json.dumps(x, ensure_ascii=False)


def values(blocks):
    return [e.get("value") for b in blocks for e in (b.get("elements") or []) if b["type"] == "actions" and e.get("value")]


def all_values(blocks):
    """Button values anywhere: actions blocks and the cards in a carousel."""
    out = values(blocks)
    for b in blocks:
        for c in b.get("elements") or [] if b["type"] == "carousel" else []:
            out += [a.get("value") for a in c.get("actions") or [] if a.get("value")]
    return out


@pytest.fixture
def slack(monkeypatch):
    posts = []
    n = iter(range(100, 10000))
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, thread_ts=None, broadcast=False:
                        posts.append({"text": text, "blocks": blocks, "thread": thread_ts, "broadcast": broadcast}) or f"{next(n)}.0")
    monkeypatch.setattr(slackapi, "update", lambda ts, blocks, text: posts.append({"update": ts, "blocks": blocks, "text": text}))
    monkeypatch.setattr(slackapi, "note", lambda ts, text: posts.append({"note": text}) or f"{next(n)}.0")
    monkeypatch.setattr(slackapi, "upload_private_image", lambda path, title: f"F{next(n)}")
    monkeypatch.setattr(slackapi, "upload_pictures_to_thread", lambda items, ts, comment: posts.append({"pictures": [str(p) for p, _ in items], "thread": ts})
                        or [{"id": f"F{next(n)}", "url": "https://slack/f"} for _ in items])
    monkeypatch.setattr(slackapi, "permalink", lambda ts: "https://slack/x")
    monkeypatch.setattr(slackapi, "ephemeral", lambda user, text, thread_ts=None: posts.append({"ephemeral": text}))
    monkeypatch.setattr(slackapi, "views_open", lambda trigger, view: posts.append({"view": view}))
    monkeypatch.setattr(slackapi, "views_update", lambda vid, view, h=None: posts.append({"view_update": view}))
    return posts


# ---------- every kind, every state ----------

@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_kind_validates_and_renders_within_slacks_limits(kind):
    card = KINDS[kind]
    assert cards.validate(card) == []
    rec = rec_for(card)
    for mode in ("open", "final", "view"):
        blocks = cards.card_blocks(rec, 1, mode)
        assert blocks[0]["type"] == "header" and len(blocks) <= cards.BLOCKS_MAX
        txt(blocks)
    assert len(cards.see_view(rec, 1)["blocks"]) <= 100
    text = txt(cards.card_blocks(rec, 1, "open"))
    frame = f"Part of: {card['part_of']}" if card["part_of"] else "Stands alone"
    assert frame in text and "Your yes covers" in text and "*Why:*" in text


def test_several_pictures_are_a_carousel_and_one_is_full_width():
    blocks = cards.card_blocks(rec_for(KINDS["before_after"]), 1, "open")
    slides = next(b for b in blocks if b["type"] == "carousel")["elements"]
    assert [s["body"]["text"] for s in slides] == ["*Fix 1 of 3 · TODAY · real screen, iPhone, 2.1.65*", "*Fix 1 of 3 · AFTER · real screen, staging*"]
    assert slides[0]["hero_image"]["slack_file"]["id"] == "F0" and slides[1]["subtext"]["text"].startswith("New: this step")
    one = cards.card_blocks(rec_for(KINDS["message"]), 1, "open")
    assert not [b for b in one if b["type"] == "carousel"]
    assert [b["title"]["text"] for b in one if b["type"] == "image"] == ["The email, as designed · real render at iPhone width"]


def test_buttons_are_named_for_what_happens():
    rec = rec_for(KINDS["before_after"])
    assert values(cards.card_blocks(rec, 1, "open")) == ["AP-0001|1|approve", "AP-0001|1|change", "AP-0001|1|decline"]
    labels = [e["text"]["text"] for b in cards.card_blocks(rec, 1, "open") if b["type"] == "actions" for e in b["elements"]]
    assert labels == ["Release all 3", "Change this fix", "No"]


def test_old_cards_without_the_new_fields_still_render():
    rec = make_rec(dict(SAMPLES["money"]))
    del rec["versions"][0]["card"]["part_of"]
    blocks = cards.card_blocks(rec, 1, "open")
    assert "Part of" not in txt(blocks) and "From Tester" in txt(blocks)
    assert values(blocks) == ["AP-0001|1|approve", "AP-0001|1|decline"]


def test_the_iphone_line_carries_the_clock():
    rec = rec_for(KINDS["spend"])
    text = cards.card_text(rec, 1)
    assert "Answer by" in text and "With no answer, it stops at 72 hours" in text and "2 to answer" in text


# ---------- refusals ----------

def test_refusals_say_what_to_fix(tmp_path):
    card = dict(KINDS["set"])
    del card["part_of"]
    assert any("part_of is required" in e for e in cards.validate(card))
    assert any("no file at" in e for e in cards.validate(dict(KINDS["set"], parts=[{"title": "x", "image": "/nope.png"}])))
    assert any("one of" in e for e in cards.validate(dict(KINDS["set"], items=["a", "b"])))
    assert any("pick must be one of" in e for e in cards.validate(dict(KINDS["choice"], pick="C")))
    two = [dict(q, edit=True) for q in KINDS["as_you"]["quotes"]]
    assert any("only one quote" in e for e in cards.validate(dict(KINDS["as_you"], quotes=two)))
    assert any("label is required" in e for e in cards.validate(dict(KINDS["test"], gallery=[{"image": KINDS["test"]["gallery"][0]["image"]}])))
    many = dict(KINDS["set"], parts=[{"title": f"Row {i}", "note": "x", "image": KINDS["test"]["gallery"][0]["image"]} for i in range(10)],
                sections=[{"title": f"S{i}", "lines": ["a"]} for i in range(25)])
    assert any("too much for one card" in e for e in cards.validate(many))
    assert any("https" in e for e in cards.validate(dict(KINDS["set"], links=[["Deck", "/local/file"]])))
    assert any("headline" in e for e in cards.validate(dict(KINDS["set"], title="Keep training " * 15)))


# ---------- a set: each row its own answer ----------

def test_each_row_is_answered_on_its_own_and_its_message_names_it():
    rec = rec_for(KINDS["set"])
    assert core.press(rec, 1, "p1:approve", "U") == "ok"
    assert core.press(rec, 1, "p2:decline", "U") == "ok"
    first, second = rec["outbox"][0]["text"], rec["outbox"][1]["text"]
    assert 'part 1 of 4 "Right after paying, take payers to what they came for"' in first and "approved this part" in first
    assert "This answer is for part 1 only" in first and "parts 2, 3 and 4 are waiting" in first
    assert 'part 2 of 4 "Practice vs AI stops calling itself a beta"' in second and "declined this part" in second
    assert "part 1 is approved" in second and "Then drop part 2 only" in second
    assert rec["status"] == "pending" and rec["decision"] is None
    assert core.press(rec, 1, "p1:decline", "U") == "already"
    blocks = cards.card_blocks(rec, 1, core.mode_for(rec))
    assert "p1:approve" not in txt(values(blocks)) and "AP-0001|1|p3:approve" in values(blocks)
    assert "✅ *Approved*" in txt(blocks) and "❌ *Declined*" in txt(blocks)


def test_approve_all_covers_the_open_plain_rows_in_one_message():
    rec = rec_for(KINDS["set"])
    core.press(rec, 1, "p2:decline", "U")
    assert "AP-0001|1|all" in values(cards.card_blocks(rec, 1, "open"))
    assert core.press(rec, 1, "all", "U") == "ok"
    text = rec["outbox"][-1]["text"]
    assert "approved 3 parts at once" in text and 'part 1 "Right after paying' in text and 'part 4 "Tickets' in text
    assert "part 2 is declined" in text
    assert rec["status"] == "approved" and core.mode_for(rec) == "final"
    assert not [v for v in values(cards.card_blocks(rec, 1, "final")) if "approve" in v]


def test_rows_that_need_their_own_yes_are_never_approved_all_at_once():
    rec = rec_for(KINDS["release"])
    assert "AP-0001|1|all" not in values(cards.card_blocks(rec, 1, "open"))
    assert core.press(rec, 1, "all", "U") == "already"
    core.press(rec, 1, "p1:approve", "U")
    core.press(rec, 1, "p2:approve", "U")
    assert rec["status"] == "pending" and "AP-0001|1|p3:approve" in values(cards.card_blocks(rec, 1, "open"))


def test_a_choice_on_a_row_names_the_choice():
    rec = rec_for(KINDS["spend"])
    blocks = cards.card_blocks(rec, 1, "open")
    assert "AP-0001|1|p2:pick:0" in values(blocks) and "Our pick:* Stop at 72 h" in txt(blocks)
    core.press(rec, 1, "p2:pick:0", "U")
    assert 'chose "Stop at 72 h, resume if the read says so" for this part' in rec["outbox"][-1]["text"]
    assert rec["answers"]["2"]["choice"] == "Stop at 72 h, resume if the read says so"


def test_a_change_on_a_row_goes_back_to_the_agent_and_the_others_keep_their_answers(tmp_path, slack):
    rec = rec_for(KINDS["set"])
    core.press(rec, 1, "p1:approve", "U")
    core.press(rec, 1, "p2:approve", "U")
    core.press(rec, 1, "p4:approve", "U")
    assert core.ask_change(rec, 1, "i think you can do better on wording", "U", j=3) == "ok"
    msg = rec["outbox"][-1]["text"]
    assert 'part 3 of 4 "The paywall stops promising \\"no ads\\""' in msg or 'part 3 of 4 "The paywall' in msg
    assert "asked for a change" in msg and "changing part 3 only" in msg and rec["status"] == "revising"
    store.save(rec)
    v2 = json.loads(json.dumps(KINDS["set"]))
    v2["parts"][2]["title"] = "The paywall stops promising \"no ads\" · version 2"
    v2["changes"] = "Row 3 has new words, after your note"
    p = tmp_path / "v2.json"
    p.write_text(json.dumps(v2))
    cli.main(["revise", "AP-0001", str(p)])
    rec = store.load("AP-0001")
    assert set(rec["answers"]) == {"1", "2", "4"} and rec["status"] == "pending"
    blocks = cards.card_blocks(rec, 2, "open")
    assert values(blocks)[-3:] == ["AP-0001|2|p3:approve", "AP-0001|2|p3:change", "AP-0001|2|p3:decline"]
    see = [e for b in blocks if b.get("block_id") == "ap|AP-0001|2|p3" for e in b["elements"]][-1]
    assert see["text"]["text"] == "See it" and see["url"].startswith("https://")   # opens the picture itself, full resolution
    assert "🆕 *3 ·" in txt(blocks) and "Version 2" in txt(blocks)
    assert any(x.get("broadcast") and "Row 3 has new words" in txt(x["blocks"]) for x in slack)
    assert core.press(rec, 1, "p3:approve", "U") == "stale"


def test_at_the_deadline_only_open_rows_close():
    rec = rec_for(KINDS["spend"])
    core.press(rec, 1, "p1:approve", "U")
    core.expire(rec)
    msg = rec["outbox"][-1]["text"]
    assert 'No answer by the deadline on part 2 "The run past 72 hours' in msg and "with no answer, it stops at 72 hours: do that" in msg
    assert rec["answers"]["1"]["verdict"] == "approved" and rec["answers"]["2"]["verdict"] == "expired"
    assert rec["status"] == "approved"


# ---------- a pick, a batch, his own words, the offer ----------

def test_a_pick_is_one_message_naming_the_option():
    rec = rec_for(KINDS["choice"])
    blocks = cards.card_blocks(rec, 1, "open")
    slides = next(b for b in blocks if b["type"] == "carousel")["elements"]
    assert slides[0]["subtitle"]["text"] == "🔹 *Our pick*" and slides[0]["actions"][0]["text"]["text"] == "Pick A"
    assert values(blocks) == ["AP-0001|1|decline"]
    core.press(rec, 1, "pick:0", "U")
    assert 'picked A ("a panel from the hand count")' in rec["outbox"][-1]["text"]
    assert cards.card_blocks(rec, 1, "final")[0]["text"]["text"] == "✅ You picked A"


def test_a_batch_picked_in_the_pop_up_names_what_to_do_and_what_to_leave_out():
    rec = rec_for(KINDS["batch"])
    assert values(cards.card_blocks(rec, 1, "open")) == ["AP-0001|1|approve", "AP-0001|1|items", "AP-0001|1|read:0", "AP-0001|1|decline"]
    view = cards.items_view(rec, 1)
    assert view["submit"]["text"] == "Send 11" and len([b for b in view["blocks"] if b["type"] == "input"]) == 2   # 10 a group
    assert cards.items_view(rec, 1, [1, 2, 3])["submit"]["text"] == "Send 3"
    ticked = [i for i in range(1, 12) if i != 6]
    core.decide(rec, 1, "approved", "U", where="pop-up", items=ticked)
    msg = rec["outbox"][-1]["text"]
    assert "approved 10 of the 11 items" in msg and "1 Creator One" in msg and "Leave out: 6 Creator Six." in msg
    final = txt(cards.card_blocks(rec, 1, "final"))
    assert "Approved 10 of 11" in final and "held back: Creator Six" in final


def test_edited_words_are_the_yes_and_reach_the_agent_exactly():
    rec = rec_for(KINDS["as_you"])
    assert "AP-0001|1|edit" in values(cards.card_blocks(rec, 1, "open"))
    view = cards.edit_view(rec, 1)
    assert view["blocks"][0]["element"]["initial_value"].startswith("Good news") and view["submit"]["text"] == "Send as you"
    words = "Good news.\nOpen this link: [their own free-year link]"
    core.decide(rec, 1, "approved", "U", where="pop-up", edited=words)
    msg = rec["outbox"][-1]["text"]
    assert json.dumps(words) in msg and "Use exactly these words" in msg and "\n" not in msg
    final = txt(cards.card_blocks(rec, 1, "final"))
    assert "Your words" in final and "No reply yet" in final
    core.add_status(rec, "Thanks, trying it now", kind="their_answer")
    assert "Thanks, trying it now" in txt(cards.card_blocks(rec, 1, "final"))


def test_the_offer_to_stop_asking_comes_after_the_yes_and_its_answer_reaches_the_agent():
    rec = rec_for(KINDS["batch"])
    assert "offer:stop" not in txt(cards.card_blocks(rec, 1, "open"))
    core.decide(rec, 1, "approved", "U")
    assert "AP-0001|1|offer:stop" in values(cards.card_blocks(rec, 1, "final"))
    assert core.press(rec, 1, "offer:stop", "U") == "ok"
    assert "Stop asking for these" in rec["outbox"][-1]["text"] and "standing exception" in rec["outbox"][-1]["text"]
    assert core.press(rec, 1, "offer:keep", "U") == "already"


# ---------- after the yes ----------

def test_status_lines_keep_the_card_current_until_its_out():
    rec = rec_for(KINDS["choice"])
    core.press(rec, 1, "pick:0", "U")
    core.add_status(rec, "you play it yourself before it merges", kind="on_you")
    core.add_status(rec, "Building with the change · then your playtest", kind="next")
    text = txt(cards.card_blocks(rec, 1, "final"))
    assert "Waiting on you:* you play it yourself" in text and "⏳ Building with the change" in text
    assert digest.waiting(rec)
    core.add_status(rec, "You played it at 9:10 am; merging")
    text = txt(cards.card_blocks(rec, 1, "final"))
    assert "Waiting on you" not in text and "⏳ Building" not in text and "✅ You played it" in text
    assert not digest.waiting(rec)
    core.add_status(rec, None, headline="Released: the hand look-back")
    assert cards.card_blocks(rec, 1, "final")[0]["text"]["text"] == "✅ Released: the hand look-back"


def test_a_rows_status_shows_under_that_row(slack):
    rec = rec_for(KINDS["release"])
    store.save(rec)
    core.press(rec, 1, "p1:approve", "U")
    store.save(rec)
    cli.main(["status", "AP-0001", "live for everyone 6:07 am", "--part", "1"])
    cli.main(["status", "AP-0001", "waiting for review at Apple since 7:04 am", "--part", "2", "--next"])
    rec = store.load("AP-0001")
    text = txt(cards.card_blocks(rec, 1, "open"))
    assert "live for everyone 6:07 am" in text and "✅ live" not in text and "⏳ waiting for review at Apple" in text
    with pytest.raises(SystemExit):
        cli.main(["status", "AP-0001", "x", "--part", "9"])


def test_record_takes_a_row_or_a_choice_from_the_thread(slack):
    rec = rec_for(KINDS["set"])
    store.save(rec)
    cli.main(["record", "AP-0001", "--approved", "--part", "2", "--words", "yes to 2", "--where", "Slack thread"])
    assert store.load("AP-0001")["answers"]["2"]["verdict"] == "approved"
    choice = rec_for(KINDS["choice"], id="AP-0002")
    store.save(choice)
    cli.main(["record", "AP-0002", "--choice", "B", "--words", "B, but slower", "--where", "Slack thread"])
    assert store.load("AP-0002")["decision"]["choice"] == "B"


def test_an_answer_that_lets_an_approved_card_go_ahead_is_recorded_so_it_can_be_done(slack):
    """#2: approved, then a question about the missed hour, then "sure, schedule it whenever"."""
    rec = rec_for(SAMPLES["creative"])
    assert core.decide(rec, 1, "approved", "U") == "ok"
    rec.update(status="question", open_question={"text": "Which hour now?"})   # as `approvals ask` leaves it
    core.take_replies(rec, [("2.0", "sure dude whatever just schedule it as you want")])
    assert rec["status"] == "revising"
    store.save(rec)
    with pytest.raises(SystemExit):
        cli.main(["done", "AP-0001", "posted at 9:02 pm"])
    assert core.decide(store.load("AP-0001"), 1, "approved", "U") == "already"   # a second press still counts once
    cli.main(["record", "AP-0001", "--approved", "--words", "sure dude whatever", "--where", "Slack thread"])
    assert store.load("AP-0001")["decision"]["words"] == "sure dude whatever"
    cli.main(["done", "AP-0001", "posted at 9:02 pm"])
    assert store.load("AP-0001")["status"] == "done"


def test_repoint_hands_open_cards_from_a_saved_session_to_the_restored_one(slack, monkeypatch, capsys):
    from approvals import reach
    old, new, other = "11111111-0000-0000-0000-000000000000", "22222222-0000-0000-0000-000000000000", "33333333-0000-0000-0000-000000000000"
    here = {"runtime": "claude", "session_id": new, "cwd": "/pods/ai/lead", "herdr_session": "pods", "herdr_pane": "w9:p1",
            "herdr_workspace": "w9", "machine": "testbox"}
    monkeypatch.setattr(reach, "running", lambda sid: dict(here) if sid == new else None)
    asked = {"runtime": "claude", "session_id": old, "agent": "AI lead", "cwd": "/pods/ai/lead", "herdr_pane": "w4:p1"}
    store.save(rec_for(SAMPLES["creative"], reach=dict(asked)))
    store.save(rec_for(SAMPLES["creative"], id="AP-0002", reach=dict(asked), status="done"))
    store.save(rec_for(SAMPLES["creative"], id="AP-0003", reach=dict(asked, session_id=other)))
    store.set_reviewer({"types": ["money"], "reach": dict(asked)})   # a restored reviewer keeps its role too
    cli.main(["repoint", old, new])
    r = store.load("AP-0001")["reach"]
    assert (r["session_id"], r["herdr_pane"], r["agent"], r["earlier_ids"]) == (new, "w9:p1", "AI lead", [old])
    assert (store.reviewer()["reach"]["session_id"], store.reviewer()["reach"]["agent"]) == (new, "AI lead")
    assert store.load("AP-0002")["reach"]["session_id"] == old       # a finished card is left as it was
    assert store.load("AP-0003")["reach"]["session_id"] == other     # someone else's card too
    assert "AP-0001" in capsys.readouterr().out
    cli.main(["repoint", old, new])
    assert "No open request" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["repoint", old, other])   # not running here


# ---------- presses and pop-ups through the listener ----------

def press_payload(value, user=None, trigger="t1"):
    return {"type": "block_actions", "user": {"id": user or config.settings()["approver"]}, "trigger_id": trigger,
            "actions": [{"value": value, "action_id": value}], "container": {}}


def submit_payload(meta, values, user=None):
    return {"type": "view_submission", "user": {"id": user or config.settings()["approver"]},
            "view": {"callback_id": "approvals", "private_metadata": meta, "state": {"values": values}}}


def run(follow):
    if follow:
        follow()


def test_a_row_press_through_the_listener_saves_before_the_ack(slack):
    store.save(rec_for(KINDS["set"]))
    L = listener.Listener()
    follow = L.handle_click(press_payload("AP-0001|1|p3:approve"))
    saved = store.load("AP-0001")
    assert saved["answers"]["3"]["verdict"] == "approved" and len(saved["outbox"]) == 1
    run(follow)
    run(L.handle_click(press_payload("AP-0001|1|p3:decline")))
    assert slack[-1] == {"ephemeral": "This one is already decided."}


def test_the_change_box_opens_and_its_words_reach_the_agent_and_the_thread(slack):
    store.save(rec_for(KINDS["set"]))
    L = listener.Listener()
    run(L.handle_click(press_payload("AP-0001|1|p2:change")))
    view = slack[-1]["view"]
    assert view["private_metadata"] == "AP-0001|1|p2:change" and "Row 2" in txt(view)
    answer, follow = L.handle_submission(submit_payload("AP-0001|1|p2:change", {"words": {"words": {"value": "say it plainer"}}}))
    assert answer is None
    run(follow)
    rec = store.load("AP-0001")
    assert rec["answers"]["2"] == dict(rec["answers"]["2"], verdict="change", words="say it plainer")
    assert 'asked for a change to this part' in rec["outbox"][-1]["text"]
    assert any("asked for a change to part 2" in x.get("note", "") for x in slack)


def test_a_change_on_a_whole_card_is_feedback_like_a_thread_reply(slack):
    store.save(rec_for(KINDS["before_after"]))
    L = listener.Listener()
    answer, follow = L.handle_submission(submit_payload("AP-0001|1|change", {"words": {"words": {"value": "fix 2's outline is on the wrong line"}}}))
    run(follow)
    rec = store.load("AP-0001")
    assert rec["status"] == "revising" and "approvals revise AP-0001" in rec["outbox"][-1]["text"]
    assert rec["outbox"][-1]["react_ts"] == rec["thread_ts"]


def test_a_pop_up_on_a_replaced_or_answered_card_is_refused_in_the_box(slack):
    rec = rec_for(KINDS["as_you"])
    rec["versions"].append({"n": 2, "card": dict(KINDS["as_you"], changes="shorter"), "copy_ts": "1.5"})
    store.save(rec)
    L = listener.Listener()
    answer, follow = L.handle_submission(submit_payload("AP-0001|1|edit", {"words": {"words": {"value": "hi"}}}))
    assert answer["response_action"] == "errors" and "replaced" in txt(answer) and follow is None
    assert store.load("AP-0001")["decision"] is None
    answer, _ = L.handle_submission(submit_payload("AP-0001|2|edit", {"words": {"words": {"value": "hi"}}}, user="U_SOMEONE"))
    assert answer["response_action"] == "errors"


def test_pick_which_counts_and_sends_the_ticked_ones(slack):
    store.save(rec_for(KINDS["batch"]))
    L = listener.Listener()
    run(L.handle_click(press_payload("AP-0001|1|items")))
    view = slack[-1]["view"]
    state = {"items0": {"items": {"selected_options": [{"value": "1"}, {"value": "2"}]}}, "items1": {"items": {"selected_options": []}}}
    run(L.handle_click({"type": "block_actions", "user": {"id": config.settings()["approver"]}, "actions": [{"action_id": "items"}],
                        "view": dict(view, id="V1", hash="h", state={"values": state})}))
    assert slack[-1]["view_update"]["submit"]["text"] == "Send 2"
    answer, _ = L.handle_submission(submit_payload("AP-0001|1|items", {"items0": {"items": {"selected_options": []}}}))
    assert "Tick at least one" in txt(answer)
    answer, follow = L.handle_submission(submit_payload("AP-0001|1|items", state))
    run(follow)
    assert store.load("AP-0001")["decision"]["items"] == [1, 2]


def test_anyone_can_open_the_card_as_asked_but_only_the_approver_decides(slack):
    rec = rec_for(KINDS["test"])
    core.decide(rec, 1, "approved", "U")
    store.save(rec)
    L = listener.Listener()
    run(L.handle_click(press_payload("AP-0001|1|see", user="U_SOMEONE")))
    assert slack[-1]["view"]["title"]["text"] == "The card as asked"
    run(L.handle_click(press_payload("AP-0001|1|change", user="U_SOMEONE")))
    assert "can decide" in slack[-1]["ephemeral"]


def test_a_posted_card_gets_its_frame_and_status_lines_in_place(slack):
    """Cards already out (answered or not) get their Part of line without a new version: redrawn, no new post,
    answers and buttons untouched."""
    old = make_rec(dict(SAMPLES["money"]))
    del old["versions"][0]["card"]["part_of"]
    store.save(old)
    cli.main(["status", "AP-0001", "--part-of", "Payers keep using what they paid for"])
    cli.main(["status", "AP-0001", "Waiting for the bank's cut-off at 3 pm", "--next"])
    rec = store.load("AP-0001")
    assert len(rec["versions"]) == 1 and rec["decision"] is None and rec["outbox"] == []
    blocks = cards.card_blocks(rec, 1, "open")
    text = txt(blocks)
    assert "Part of: Payers keep using what they paid for · Tester" in text and "From Tester" not in text
    assert "⏳ Waiting for the bank's cut-off" in text and values(blocks) == ["AP-0001|1|approve", "AP-0001|1|decline"]
    assert not [x for x in slack if "text" in x and "update" not in x]   # redrawn in place, nothing posted
    assert [x["update"] for x in slack if "update" in x] == ["1.1", "1.1"]
    cli.main(["status", "AP-0001", "--stands-alone"])
    assert "Stands alone · Tester" in txt(cards.card_blocks(store.load("AP-0001"), 1, "open"))


def test_the_launchers_name_wins_over_the_agents_own(tmp_path, monkeypatch, slack):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-from")
    monkeypatch.setenv("APPROVALS_FROM", "AI pod · lead")
    p = tmp_path / "card.json"
    p.write_text(json.dumps(KINDS["before_after"]))
    cli.main(["submit", str(p), "--agent", "lead"])
    rec = store.load("AP-0001")
    assert rec["agent"] == "AI pod · lead" and "AI pod · lead" in txt(cards.card_blocks(rec, 1, "open"))


def test_a_new_cards_pictures_go_in_its_thread_and_then_into_the_card(tmp_path, monkeypatch, slack):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-kinds")
    p = tmp_path / "card.json"
    p.write_text(json.dumps(KINDS["before_after"]))
    cli.main(["submit", str(p), "--agent", "Lessons"])
    root = next(x for x in slack if "text" in x and x.get("thread") is None)
    shared = next(x for x in slack if "pictures" in x)
    assert shared["thread"] == "100.0" and len(shared["pictures"]) == 4   # two frames, and each picture whole for its Full size button
    rec = store.load("AP-0001")
    assert set(rec["versions"][0]["files"]) == {"gallery:0", "gallery:0:card", "gallery:1", "gallery:1:card"}
    redrawn = [x for x in slack if x.get("update") == "100.0"][-1]["blocks"]
    slides = next(b for b in redrawn if b["type"] == "carousel")["elements"]
    assert slides[1]["hero_image"]["slack_file"] == {"url": "https://slack/f"} and not any(b["type"] == "carousel" for b in root["blocks"])


def test_a_next_step_posted_before_done_drops_away():
    rec = rec_for(KINDS["test"])
    core.decide(rec, 1, "approved", "U")
    core.add_status(rec, "First read at about day 3", kind="next")
    rec["done"] = {"text": "Built and running", "at": rec["timeline"][-1]["at"]}   # the same second, as agents do
    text = txt(cards.card_blocks(rec, 1, "final"))
    assert "First read at about day 3" not in text and "Done" in text
    core.add_status(rec, "Early read, day 3: no harm to trials")
    text = txt(cards.card_blocks(rec, 1, "final"))
    assert "✅ Early read, day 3" in text and text.index("Done") < text.index("Early read")   # in the order it happened


def test_a_row_numbered_by_the_agent_isnt_numbered_twice():
    card = dict(KINDS["set"], parts=[{"title": "1. Under the Plus badge, one line"}, {"title": "2 · The streak moves"}])
    text = txt(cards.card_blocks(rec_for(card), 1, "open"))
    assert "*1 · Under the Plus badge" in text and "*2 · The streak moves" in text


def test_his_reply_after_a_pick_is_quoted_on_the_answered_card():
    rec = rec_for(KINDS["choice"])
    core.press(rec, 1, "pick:0", "U")
    core.take_replies(rec, [("9.0", "Yes, A, but the cards slide out from under the name label")])
    text = txt(cards.card_blocks(rec, 1, "final"))
    assert "> Yes, A, but the cards slide out" in text and rec["status"] == "approved"
