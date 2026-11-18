"""The default approver can hand a card to a teammate and get it back if unanswered."""
import json
from datetime import timedelta

import pytest

from approvals import cards, cli, config, core, digest, listener, slackapi, store
from test_deputy import to
from test_kinds import press_payload, run, txt, values
from test_team import DM, NOW, RID, USER, card, click, latest, leave, revise, slack, team


APPROVER = "UAPPROVER"
CHANNEL = "CTEST"
CREDIT = "Sam handed this card to Riley, and Riley's answer counts as Sam's, for exactly what the card describes."
BACK = "Sam has this card too. Whoever answers first has answered."


def hand(name="riley", n=1):
    click(f"hand:{name}", user=APPROVER, channel=CHANNEL, n=n)


def broadcasts(slack):
    return [(blocks[0]["text"]["text"], kw["thread_ts"]) for _, blocks, kw in slack.posts if kw.get("broadcast")]


def menu(blocks):
    return [e for b in blocks if b["type"] == "actions" for e in b["elements"] if e["type"] == "overflow"]


@pytest.mark.parametrize("start,due", [
    ("13T14:00", "13T17:00"),
    ("13T16:30", "14T10:30"),
    ("13T20:40", "14T12:00"),
    ("16T19:00", "19T12:00"),
    ("17T11:00", "19T12:00"),
    ("13T15:00", "13T18:00"),
])
def test_the_work_clock_counts_time_inside_weekdays(start, due):
    start, due = (store.parse(f"2026-01-{t}:00+00:00") for t in (start, due))
    assert core.work_after(start, 3, ["09:00", "18:00"]) == due


def test_the_work_clock_uses_the_workspace_zone_and_persons_window(team, slack, monkeypatch):
    team["timezone"] = "America/Chicago"
    team["team"]["riley"]["work"] = ["09:30", "13:00"]
    team["handoff_hours"] = 2.5
    config.CONFIG.write_text(json.dumps(team))
    monkeypatch.setattr(store, "now", lambda: store.parse("2026-01-13T18:30:00+00:00"))
    card()
    hand()
    assert latest()[1]["return_at"] == "2026-01-14T11:30:00-06:00"


def test_a_hand_off_moves_the_deadline_and_tells_the_asker_and_deputy_once(team, slack):
    before = card()
    assert store.update(RID, lambda r: core.press(r, 1, "hand:riley", APPROVER)) == "ok"
    rec, send = latest()
    at, due = store.iso(NOW), store.iso(NOW + timedelta(hours=3))
    assert send == {"version": 1, "name": "Riley", "user": USER, "why": "", "at": at, "send_after": None,
                    "handed": {"by": APPROVER, "at": at}, "return_at": due}
    assert rec["card_dirty"] and rec["deadline"] > before["deadline"]
    assert store.parse(rec["deadline"]) == store.parse(due) + timedelta(hours=48)
    assert rec["events"][-1] == {"kind": "handed", "at": at, "version": 1, "name": "Riley", "return_at": due}
    [notice] = to(RID)
    assert notice["kind"] == "handed" and notice["needs_ack"] is False
    assert ("Sam handed this card to Riley to answer (Mon 12:00 pm). Riley's answer counts as Sam's, "
            "for exactly what the card describes. If Riley hasn't answered by Mon 3:00 pm, it goes back to Sam. "
            "With no answer by Wed 3:00 pm, it closes as before. Nothing to do or acknowledge.") in notice["text"]
    [deputy] = to(RID, "deputy")
    assert "Sam handed it to Riley (Mon 12:00 pm). It waits on Riley until Mon 3:00 pm; there is nothing to call." in deputy["text"]
    assert not slack.opens


@pytest.mark.parametrize("status,prior_hours,due_hours", [("pending", 48, 48), ("question", 1, 15)])
def test_the_deadline_uses_the_cards_own_window_and_keeps_a_later_deadline(team, slack, status, prior_hours, due_hours):
    rec = card(status=status)
    v = rec["versions"][0]
    v["card"] = dict(v["card"], deadline_hours=12)
    rec["deadline"] = store.iso(NOW + timedelta(hours=prior_hours))
    store.save(rec)
    hand()
    assert store.parse(latest()[0]["deadline"]) == NOW + timedelta(hours=due_hours)


@pytest.mark.parametrize("user,channel,n,name,target,status,notice", [
    (USER, CHANNEL, 1, "riley", None, "pending", "Only Sam can decide approvals."),
    (APPROVER, CHANNEL, 1, "riley", None, "replaced", "That version was replaced"),
    ("ULEAD", "CLEAD", 1, "riley", "lead", "pending", "That button doesn't match"),
    (APPROVER, CHANNEL, 1, "unknown", None, "pending", "That button doesn't match"),
    (APPROVER, CHANNEL, 1, "riley", None, "revising", "It's back with its agent."),
    (APPROVER, CHANNEL, 1, "riley", None, "expired", "This one is already decided."),
    (APPROVER, CHANNEL, 1, "riley", None, "withdrawn", "The agent withdrew this one"),
])
def test_unavailable_handoffs_are_refused(team, slack, user, channel, n, name, target, status, notice):
    rec = card(target=target, status=status)
    if status == "replaced":
        rec["status"] = "pending"
        rec["versions"].append(dict(rec["versions"][0], n=2))
        store.save(rec)
    click(f"hand:{name}", user=user, channel=channel, n=n)
    assert notice in slack.notices[-1] and store.load(RID) == rec and not slack.posts


def test_only_one_active_handoff_to_the_same_person(team, slack):
    card()
    hand()
    before = store.load(RID)
    hand()
    assert slack.notices[-1] == "Riley already has this one." and store.load(RID) == before
    assert len(to(RID)) == 1 and len(to(RID, "deputy")) == 1


@pytest.mark.parametrize("deputys_send", [False, True])
def test_handing_it_over_closes_and_redraws_the_previous_send(team, slack, deputys_send):
    card()
    if deputys_send:
        leave()
        closed_for, expect = "Riley", "Sam handed this card to you: it comes as a new message."
    else:
        hand()
        closed_for, expect = "Morgan", "Sam handed this card to Morgan."
    L = listener.Listener()
    L.sweep()
    old = latest()[1]
    hand(closed_for.lower())
    rec, send = latest()
    assert len(rec["also"]) == 2 and send["name"] == closed_for and not send.get("sent_at")
    assert cards.also_on(rec, 1) is send
    closed = rec["also"][0]
    assert closed["closed_at"] == store.iso(NOW) and closed["closed_for"] == closed_for
    assert closed["sent_at"] == old["sent_at"]
    blocks = cards.also_blocks(rec, closed)
    assert len(blocks) == 3 and expect in txt(blocks[1]) and rec["link"] in txt(blocks[2])
    assert not values(blocks) and any(ts == old["ts"] and b == blocks for ts, b, _ in slack.updates)
    L.sweep()
    assert latest()[1]["sent_at"] and slack.opens == [USER, send["user"]]


def test_a_handoff_during_delivery_keeps_each_dm_with_its_own_send(team, slack, monkeypatch):
    card()
    hand()
    post = slackapi.post

    def reroute(*a, **kw):
        monkeypatch.setattr(slackapi, "post", post)
        hand("morgan")
        return post(*a, **kw)

    monkeypatch.setattr(slackapi, "post", reroute)
    L = listener.Listener()
    L.sweep()
    rec, send = latest()
    assert rec["also"][0]["ts"] == slack.posts[0][0] and not send.get("sent_at")
    assert any(ts == slack.posts[0][0] and "Sam handed this card to Morgan." in txt(b) for ts, b, _ in slack.updates)
    L.sweep()
    assert latest()[1]["ts"] == slack.posts[1][0]


def test_a_handoff_that_extends_the_deadline_while_sweeping_prevents_expiry(team, slack, monkeypatch):
    rec = card()
    rec["deadline"] = store.iso(NOW)
    store.save(rec)
    update = store.update

    def hand_before_expiring(rid, fn):
        monkeypatch.setattr(store, "update", update)
        hand()
        return update(rid, fn)

    monkeypatch.setattr(store, "update", hand_before_expiring)
    listener.Listener().sweep()
    rec, send = latest()
    assert cards.handed(rec) and send["sent_at"] and rec["status"] == "pending"
    assert [item["kind"] for item in to(RID)] == ["handed"]


@pytest.mark.parametrize("quiet", [False, True])
def test_delivery_and_return_time_respect_quiet_hours(team, slack, monkeypatch, quiet):
    if quiet:
        team["team"]["riley"]["quiet"] = ["10:00", "17:00"]
        config.CONFIG.write_text(json.dumps(team))
    now = NOW
    monkeypatch.setattr(store, "now", lambda: now)
    card()
    hand()
    rec, send = latest()
    L = listener.Listener()
    L.sweep()
    if quiet:
        blocks = cards.card_blocks(rec, 1, core.mode_for(rec))
        assert "It reaches Riley at 5:00 pm and comes back to you Tue 11:00 am" in txt(blocks)
        assert not slack.posts and send["send_after"] == store.iso(NOW + timedelta(hours=5))
        now += timedelta(hours=5)
        L.sweep()
        assert store.parse(send["return_at"]) == NOW + timedelta(hours=23)
    else:
        assert send["send_after"] is None and store.parse(send["return_at"]) == NOW + timedelta(hours=3)
    assert len(slack.posts) == 1 and latest()[1]["sent_at"] == store.iso(now)


@pytest.mark.parametrize("deliver", [False, True])
@pytest.mark.parametrize("user", [APPROVER, USER])
def test_either_person_can_answer_before_or_after_delivery(team, slack, deliver, user):
    card()
    hand()
    if deliver:
        listener.Listener().sweep()
    rec, send = latest()
    assert core.can_answer(rec, user) and bool(send.get("sent_at")) == deliver
    click("approve", user=user, channel=DM if deliver and user == USER else CHANNEL)
    rec, _ = latest()
    assert rec["decision"]["by"] == user and not cards.handed(rec)
    notice, answer = to(RID)
    assert (notice["kind"], answer["kind"]) == ("handed", "decision")
    assert (CREDIT in answer["text"]) == (user == USER)
    assert f"{'Riley' if user == USER else 'Sam'} approved it" in answer["text"]


def test_a_channel_reply_can_answer_before_the_dm_arrives(team, slack):
    rec = card()
    hand()
    slack.threads[rec["thread_ts"]] = [
        {"ts": str(NOW.timestamp() - 1), "user": USER, "text": "Old words"},
        {"ts": str(NOW.timestamp() + 1), "user": USER, "text": "Lower the cap"},
    ]
    L = listener.Listener()
    L.poll_once()
    _, answer = to(RID)
    assert "Lower the cap" in answer["text"] and "Old words" not in answer["text"] and CREDIT in answer["text"]
    L.sweep()
    assert not slack.opens and latest()[1]["skipped"]


def test_a_row_answer_keeps_the_rest_of_the_set_with_the_teammate(team, slack, monkeypatch):
    now = NOW
    monkeypatch.setattr(store, "now", lambda: now)
    card(kind="set")
    hand()
    L = listener.Listener()
    L.sweep()
    now += timedelta(hours=1)
    click("p1:approve", channel=CHANNEL)
    rec, send = latest()
    assert rec["status"] == "pending" and cards.handed(rec) and not digest.waiting(rec)
    assert core.can_answer(rec, USER)
    assert CREDIT in to(RID)[-1]["text"]
    blocks = cards.card_blocks(rec, 1, core.mode_for(rec))
    assert "You handed this to Riley, Mon 12:00 pm. Riley responded Mon 1:00 pm, so it stays with Riley." in txt(blocks)

    now = store.parse(send["return_at"]) + timedelta(seconds=1)
    L.sweep()
    assert latest() == (rec, send) and not broadcasts(slack)


def test_answering_a_teammates_reply_reopens_the_card_with_the_teammate(team, slack, monkeypatch, capsys):
    now = NOW
    monkeypatch.setattr(store, "now", lambda: now)
    rec = card()
    hand()
    L = listener.Listener()
    L.sweep()
    digest.tick()

    now += timedelta(hours=1)
    slack.threads[rec["thread_ts"]] = [{"ts": str(now.timestamp()), "user": USER, "text": "Does the cap include tax?"}]
    L.poll_once()
    assert store.load(RID)["status"] == "revising"
    now += timedelta(hours=1)
    cli.main(["answer", RID, "Yes, the cap includes tax."])
    assert "The card stays with the teammate." in capsys.readouterr().out
    rec, send = latest()
    assert rec["status"] == "pending" and len(rec["versions"]) == 1
    assert cards.handed(rec) and not digest.waiting(rec)
    channel = next(b for ts, b, _ in reversed(slack.updates) if ts == rec["thread_ts"])
    assert "You handed this to Riley, Mon 12:00 pm. Riley responded Mon 1:00 pm, so it stays with Riley." in txt(channel)
    dm = next(b for ts, b, _ in reversed(slack.updates) if ts == send["ts"])
    assert dm[-1] == cards.ctx(f"{BACK} · <{rec['link']}|Open it in #approvals>")

    digest.tick()
    st = digest.load_state()
    assert st["listed"] == [] and st["team"] == [RID]
    body = next(b for ts, b, _ in reversed(slack.updates) if ts == st["ts"])
    assert "Nothing is waiting on you · 1 with your team" in txt(body)
    assert "Tester · with Riley since 12:00 pm · Riley responded 1:00 pm" in txt(body)
    summary = txt(digest.summary(store.deputy(), NOW - timedelta(days=1), [rec]))
    assert "Riley · handed by you · 💬 replied" in summary

    now = store.parse(send["return_at"]) + timedelta(seconds=1)
    monkeypatch.setattr(L, "return_hand", lambda rec: pytest.fail("the sweep tried to return a card with a response"))
    L.sweep()
    assert latest() == (rec, send) and not broadcasts(slack)


def test_the_channel_card_has_the_test_tag_frame_and_two_buttons(team, slack):
    rec = card(test=True)
    hand()
    rec, _ = latest()
    mode = core.mode_for(rec)
    blocks = cards.card_blocks(rec, 1, mode)
    assert mode == "handed" and not digest.waiting(rec)
    assert blocks[0] == cards.test_tag() and blocks[1] == cards.header(f"⏳ With Riley: {rec['title']}")
    assert blocks[2] == cards.frame_line(rec, core.card(rec))
    assert "You handed this to Riley, Mon 12:00 pm. It comes back to you Mon 3:00 pm if Riley hasn't answered." in txt(blocks)
    assert values(blocks) == [f"{RID}|1|mine", f"{RID}|1|see"]
    assert [b["text"]["text"] for b in blocks[-2]["elements"]] == ["Back to me", "See the card"]
    assert "No answer by Wed 3:00 pm means no" in txt(blocks[-1])


def test_the_list_crosses_off_hand_offs_and_counts_only_cards_with_the_approver(team, slack):
    card()
    digest.tick()
    assert digest.load_state()["listed"] == [RID]
    hand()
    digest.tick()
    old = txt(slack.updates[-1][1])
    assert f"|{RID}>  " in old and "~<" in old and "⏳ You handed it to Riley, 12:00 pm" in old
    assert "Nothing is waiting on you · 1 with your team" in old
    card("AP-0002")
    digest.tick(asked=True)
    st = digest.load_state()
    assert st["listed"] == ["AP-0002"] and st["team"] == [RID]
    blocks = slack.posts[-1][1]
    body = txt(blocks)
    assert "1 card is waiting on you" in body
    assert body.index("|AP-0002>") < body.index("⏳ *With your team*") < body.index(f"|{RID}>")
    assert "Tester · with Riley since 12:00 pm · back to you 3:00 pm" in body
    assert f"{RID}|1|approve" in values(blocks) and f"{RID}|1|remove" in values(blocks)
    assert [o["value"] for o in menu(blocks)[-1]["options"]] == [f"{RID}|1|hand:riley", f"{RID}|1|hand:morgan"]
    click("approve", user=APPROVER, channel=CHANNEL)
    digest.tick()
    crossed = txt(slack.updates[-1][1])
    assert "✅ You approved it, 12:00 pm" in crossed and f"{RID}|1|approve" not in crossed


def test_back_to_me_puts_the_card_on_the_list_but_the_teammate_can_still_answer(team, slack):
    card()
    hand()
    L = listener.Listener()
    L.sweep()
    digest.tick()
    click("mine", channel=CHANNEL)
    assert slack.notices[-1] == "Only Sam can decide approvals." and cards.handed(store.load(RID))
    click("mine", user=APPROVER, channel=CHANNEL)
    rec, send = latest()
    assert send["returned_at"] == store.iso(NOW) and send["returned_by"] == "approver"
    assert not cards.handed(rec) and digest.waiting(rec) and core.can_answer(rec, USER)
    digest.tick()
    blocks = txt(slack.updates[-1][1])
    assert "1 card is waiting on you" in blocks and "↩️ Back with you" in blocks
    assert "↩️" not in txt(cards.card_blocks(rec, 1, core.mode_for(rec)))
    assert BACK in txt(cards.also_blocks(rec, send)[-1])
    assert "Sam took v1 back from Riley" in "\n".join(cli._happened(rec))
    click("approve")
    assert store.load(RID)["decision"]["by"] == USER


def test_the_sweep_returns_an_unanswered_card_once_at_its_return_time(team, slack, monkeypatch):
    now = NOW
    monkeypatch.setattr(store, "now", lambda: now)
    card()
    leave()
    hand("morgan")
    L = listener.Listener()
    L.sweep()
    now += timedelta(hours=3, seconds=-1)
    L.sweep()
    assert cards.handed(store.load(RID)) and not broadcasts(slack)
    now += timedelta(seconds=1)
    L.sweep()
    L.sweep()
    rec, send = latest()
    assert send["returned_at"] == store.iso(now) and "returned_by" not in send
    assert digest.waiting(rec) and core.can_answer(rec, send["user"])
    assert [e["name"] for e in rec["events"] if e["kind"] == "returned"] == ["Morgan"]
    assert broadcasts(slack) == [("↩️ <@UAPPROVER> Morgan hasn't answered this in 3 working hours, so it's back with you.", rec["thread_ts"])]
    assert "Morgan hadn't answered by Mon 3:00 pm, so it is back with Sam." in to(RID, "deputy")[-1]["text"]
    channel = txt(cards.card_blocks(rec, 1, core.mode_for(rec)))
    assert "↩️ Morgan hadn't answered by Mon 3:00 pm, so it's back with you." in channel and "left this for you" in channel
    assert BACK in txt(cards.also_blocks(rec, send)[-1])


@pytest.mark.parametrize("answer", ["approve", "reply"])
def test_the_sweep_leaves_answered_and_revising_cards_alone(team, slack, monkeypatch, answer):
    card()
    hand()
    L = listener.Listener()
    L.sweep()
    if answer == "approve":
        click("approve", user=APPROVER, channel=CHANNEL)
    else:
        store.update(RID, lambda r: core.take_replies(r, [("2.0", "Lower the cap")], by=USER))
    monkeypatch.setattr(store, "now", lambda: NOW + timedelta(hours=4))
    L.sweep()
    rec, send = latest()
    assert rec["status"] == {"approve": "approved", "reply": "revising"}[answer]
    assert not send.get("returned_at") and not broadcasts(slack)


def test_a_failed_broadcast_leaves_the_card_returned_without_repeating_it(team, slack, monkeypatch, caplog):
    card()
    hand()
    L = listener.Listener()
    L.sweep()
    monkeypatch.setattr(store, "now", lambda: NOW + timedelta(hours=4))

    def broken(*a, **kw):
        raise ValueError("Slack failed")

    monkeypatch.setattr(slackapi, "post", broken)
    L.sweep()
    L.sweep()
    rec, send = latest()
    assert send["returned_at"] and digest.waiting(rec)
    assert caplog.text.count("hand-off return for AP-0001 failed") == 1


def test_not_mine_returns_the_card_and_ends_the_teammates_access(team, slack):
    card()
    hand()
    listener.Listener().sweep()
    click("notmine")
    rec, send = latest()
    assert send["handed_back_at"] == store.iso(NOW) and digest.waiting(rec) and not core.can_answer(rec, USER)
    assert "handed it back" in to(RID, "deputy")[-1]["text"]
    assert broadcasts(slack) == [("↩️ <@UAPPROVER> Riley handed this back, so it's back with you.", rec["thread_ts"])]
    assert "↩️ Riley handed it back, 12:00 pm." in txt(cards.card_blocks(rec, 1, core.mode_for(rec)))
    assert "You handed it back to Sam" in txt(cards.also_blocks(rec, send)) and not values(cards.also_blocks(rec, send))


def test_the_direct_message_says_who_handed_it_over_and_when_it_goes_back(team, slack):
    card()
    hand()
    listener.Listener().sweep()
    rec, send = latest()
    blocks = slack.posts[-1][1]
    assert "Sam handed you this card from Tester. It's yours to answer." in txt(blocks[0])
    assert "Why you" not in txt(blocks) and f"{RID}|1|notmine" in values(blocks)
    assert f"If it's still waiting at Mon 3:00 pm, it goes back to Sam. · <{rec['link']}|Open it in #approvals>" in txt(blocks[-1])
    assert not menu(blocks) and blocks == cards.also_blocks(rec, send)


def test_the_overflow_menu_is_last_on_the_channel_and_list_and_its_value_is_read(team, slack):
    rec = card()
    blocks = cards.card_blocks(rec, 1)
    [overflow] = menu(blocks)
    assert overflow == next(b["elements"][-1] for b in blocks if b.get("block_id") == f"ap|{RID}|1")
    assert [(o["text"]["text"], o["value"]) for o in overflow["options"]] == [
        ("Hand to Riley", f"{RID}|1|hand:riley"), ("Hand to Morgan", f"{RID}|1|hand:morgan")]
    assert digest._buttons(rec)["elements"][-1] == overflow
    assert not menu(cards.small_blocks(rec)) and not menu(cards.see_view(rec, 1)["blocks"])
    named = dict(rec, target="lead")
    assert not menu(cards.card_blocks(named, 1)) and not menu([digest._buttons(named)])
    payload = press_payload("ignored", user=APPROVER)
    payload["actions"][0].update(type="overflow", selected_option=overflow["options"][1])
    run(listener.Listener().handle_click(payload))
    assert latest()[1]["name"] == "Morgan"
    config.CONFIG.write_text(json.dumps(dict(team, team={})))
    assert not menu(cards.card_blocks(rec, 1)) and not menu([digest._buttons(rec)])


def test_a_full_channel_actions_row_has_no_menu(team, monkeypatch):
    rec = card()
    monkeypatch.setattr(cards, "_buttons", lambda r, n: [cards.button(str(i)) for i in range(25)])
    assert not menu(cards.card_blocks(rec, 1))


def test_a_revision_carries_the_hand_off_with_fresh_times_and_no_new_asker_message(team, slack, tmp_path, monkeypatch, capsys):
    team["team"]["riley"]["quiet"] = ["14:00", "16:00"]
    config.CONFIG.write_text(json.dumps(team))
    card()
    hand()
    listener.Listener().sweep()
    now = NOW + timedelta(hours=2)
    monkeypatch.setattr(store, "now", lambda: now)
    revise(tmp_path)
    rec, send = latest()
    assert send["version"] == 2 and send["name"] == "Riley" and send["user"] == USER
    assert send["handed"] == {"by": APPROVER, "at": store.iso(now)}
    assert store.parse(send["send_after"]) == NOW + timedelta(hours=4)
    assert store.parse(send["return_at"]) == NOW + timedelta(hours=22)
    assert store.parse(rec["deadline"]) == store.parse(send["return_at"]) + timedelta(hours=48)
    assert len(to(RID)) == 1 and "Sam handed v2 to Riley" in "\n".join(cli._happened(rec))
    instructions = "Sam handed this card to Riley, so --his leaves it with Riley; --also is not taken."
    assert instructions in core.make_your_call(rec) and instructions in to(RID, "deputy")[-1]["text"]
    assert any("⏳ With Riley:" in txt(b) for _, b, _ in slack.posts)
    with pytest.raises(SystemExit):
        leave(name="morgan")
    assert "Sam handed this card to Riley; call it without --also." in capsys.readouterr().err


def test_the_summary_and_history_name_the_hand_off_and_automatic_return(team, slack, monkeypatch):
    card()
    hand()
    rec, _ = latest()
    since = NOW - timedelta(days=1)
    assert "Riley · handed by you · ⏳ with Riley, back to you Mon 3:00 pm" in txt(digest.summary(store.deputy(), since, [rec]))
    assert "Sam handed v1 to Riley, back to him Jan 12 3:00 pm if unanswered" in "\n".join(cli._happened(rec))
    L = listener.Listener()
    L.sweep()
    monkeypatch.setattr(store, "now", lambda: NOW + timedelta(hours=3))
    L.sweep()
    rec = store.load(RID)
    assert "Riley · handed by you · ↩️ came back to you unanswered" in txt(digest.summary(store.deputy(), since, [rec]))
    assert "back with Sam: Riley hadn't answered" in "\n".join(cli._happened(rec))
