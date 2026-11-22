"""A hub whose cards the pod dashboard's record keeps works from the record: it carries out each answer the record took
with the press code a Slack press runs, in a write that names the answer, in the order taken; its own deadlines and
hand-backs, the deputy's calls and recorded answers are answers the record takes; each version keeps its pop-ups; who
may answer and the deputy are the record's."""
import copy
import json
from datetime import timedelta

import pytest

from approvals import cli, config, core, digest, listener, slackapi, store
from test_deputy import DEPUTY, to
from test_kinds import submit_payload
from test_team import NOW, RID, USER, card, click, latest, slack, team


APPROVER = "UAPPROVER"
CHANNEL = "CTEST"


class FakeRecord:
    """The record's card routes as the hub uses them: cards at revisions, the answers it took, and put_card's
    marking of each answer an event names as carried out, once."""

    def __init__(self):
        self.cards, self.rows, self.refused_words, self.changed = {}, [], {}, []
        self.deputy = store._read_role(config.DEPUTY)

    def read(self, rid):
        if rid not in self.cards:
            raise FileNotFoundError(rid)
        rev, doc = self.cards[rid]
        return copy.deepcopy(doc), rev

    def put(self, rec, rev=None, media=None):
        store.Record.keep_views(rec)
        old_rev, old = self.cards.get(rec["id"], (0, {"events": []}))
        if rev is not None and rev != old_rev:
            return None
        for e in rec.get("events", [])[len(old["events"]):]:
            if "answer" in e:
                row = next(a for a in self.rows if a["id"] == e["answer"])
                if row.get("carried") and row["carried"] != old_rev + 1 or row.get("refused"):
                    raise store.Refused(f"put_card: {rec['id']} names answer {e['answer']}, which is not one the record took on it and is waiting.")
                row["carried"] = old_rev + 1
        self.cards[rec["id"]] = (old_rev + 1, copy.deepcopy(rec))
        return old_rev + 1

    def create(self, rec, media=None):
        rec = dict(rec, id=rec.get("id") or f"AP-{len(self.cards) + 1:04d}")
        self.cards[rec["id"]] = (1, copy.deepcopy(rec))
        return self.read(rec["id"])[0]

    def all(self):
        return [self.read(rid)[0] for rid in sorted(self.cards)]

    def delete(self, rid):
        self.cards.pop(rid, None)

    def without_views(self):
        return sorted(rid for rid, (_, doc) in self.cards.items() if any("views" not in v for v in doc["versions"]))

    def answers(self):
        return [dict(a) for a in self.rows if not a.get("carried") and not a.get("refused")]

    def take(self, answer):
        repeat = next((a for a in self.rows if a["key"] == answer["key"]), None)
        if repeat:
            return {"answer": repeat["id"], "taken": False}
        if answer["card"] not in self.cards:
            raise store.Refused(f"take_answer: {answer['card']} is not in the record.")
        row = {"id": len(self.rows) + 1, "slack_user": answer.get("user"), "items": None, "until_at": None,
               "detail": {}, **{k: v for k, v in answer.items() if k != "user"}}
        if row["via"] == "deputy":
            row["detail"] = dict(row["detail"], live=bool(self.deputy and self.deputy["live"]))
        self.rows.append(row)
        return {"answer": row["id"], "card": row["card"], "version": row["version"], "taken": True}

    def refuse(self, answer_id, words):
        row = next(a for a in self.rows if a["id"] == answer_id)
        assert not row.get("carried") and not row.get("refused")
        row["refused"] = words

    def who(self):
        s = config.file_settings()
        return {"channel": s["channel"], "approver": s["approver"], "approver_name": s["approver_name"],
                "approvers": s.get("approvers") or {}, "team_answers_any": s["team_answers_any"],
                "team": {k: {"user": p["user"], "name": p["name"]} for k, p in s["team"].items()},
                "team_order": list(s["team"]), "deputy": self.deputy}

    def set_deputy(self, d):
        self.deputy = d


@pytest.fixture
def record(team, slack, monkeypatch):
    fake = FakeRecord()
    monkeypatch.setattr(store, "record", lambda: fake)
    config.forget_lists()
    return fake


@pytest.fixture
def hub(record):
    L = listener.Listener()
    L.later = lambda follow_up: follow_up()
    return L


def taken(fake, what, by=APPROVER, n=1, via="dashboard", rid=RID, **fields):
    return fake.take({"card": rid, "version": n, "what": what, "via": via, "key": f"{via}:{len(fake.rows)}",
                      "user": by if via in ("slack", "dashboard") else None, **fields})["answer"]


def carried(hub, fake, what, **kw):
    """An answer taken and carried out: None, or the hub's refusal."""
    aid = taken(fake, what, **kw)
    hub.carry_waiting()
    return hub.outcomes[aid]


def asker_texts(rid=RID):
    return [m["text"].rsplit(" (ref ", 1)[0] for m in to(rid)]


def without_refs(rec):
    rec = copy.deepcopy(rec)
    for m in rec.get("outbox", []):
        m.pop("ref"), m.pop("text")
    for e in rec.get("events", []):
        e.pop("actor", None), e.pop("answer", None)
    for v in rec.get("versions", []):
        v.pop("views", None)
    return rec


# ---------- answers carried out as Slack presses are ----------

@pytest.mark.parametrize("what", ["approve", "decline", "remove"])
def test_an_answer_from_the_record_saves_and_redraws_what_the_slack_press_does(team, slack, monkeypatch, what):
    card()
    click(what, user=APPROVER, channel=CHANNEL)
    from_slack, slack_updates, slack_said = store.load(RID), list(slack.updates), asker_texts()
    slack.updates.clear()
    fake = FakeRecord()
    monkeypatch.setattr(store, "record", lambda: fake)
    card()
    L = listener.Listener()
    L.later = lambda follow_up: follow_up()
    assert carried(L, fake, what, via="slack") is None
    from_record = store.load(RID)
    assert without_refs(from_record) == without_refs(from_slack)
    assert slack.updates == slack_updates and asker_texts() == slack_said
    # Every event the carrying write added names the answer, so the record marked it carried out, once.
    assert {e.get("answer") for e in from_record["events"] if e.get("actor") == APPROVER} == {1}
    assert fake.rows[0]["carried"] and fake.answers() == []


def test_the_asker_hears_the_pod_dashboard_named_where_he_answered_there(hub, record):
    card()
    carried(hub, record, "approve")
    [yes] = asker_texts()
    assert "Sam approved it (button on the pod dashboard, Mon 12:00 pm)." in yes and "in Slack" not in yes


def test_answers_taken_while_the_hub_was_stopped_are_carried_out_in_the_order_taken(hub, record, slack):
    card()
    card(rid="AP-0002")
    first = taken(record, "remove")
    second = taken(record, "approve", rid="AP-0002", via="slack")
    third = taken(record, "approve", via="slack")   # after the Remove, nothing is left to decide
    hub.carry_waiting()
    removed = "You removed this one from your list, so there's nothing to decide."
    assert [hub.outcomes[a] for a in (first, second, third)] == [None, None, removed]
    assert store.load(RID)["status"] == "withdrawn" and store.load("AP-0002")["status"] == "approved"
    assert record.rows[2]["refused"] == removed and record.answers() == []
    assert slack.notices[-1] == removed


def test_a_refusal_the_records_write_makes_is_marked_on_the_answer(hub, record):
    card()
    aid = taken(record, "approve")
    real_put = record.put

    def refusing(rec, rev=None, media=None):
        raise store.Refused("put_card: AP-0001 asks for a pick; an approval of it must name the option chosen.")
    record.put = refusing
    hub.carry_waiting()
    record.put = real_put
    assert record.rows[0]["refused"].startswith("put_card: AP-0001 asks for a pick") and hub.outcomes[aid]


def test_an_answer_on_a_card_taken_out_of_the_store_is_refused_and_the_next_is_carried_out(hub, record):
    card()
    card(rid="AP-0002")
    gone = taken(record, "approve")
    record.delete(RID)
    taken(record, "approve", rid="AP-0002")
    hub.carry_waiting()
    assert record.rows[0]["refused"] == hub.REFUSED["gone"] and hub.outcomes[gone] == hub.REFUSED["gone"]
    assert store.load("AP-0002")["status"] == "approved"


def test_a_teammates_answer_on_a_handed_card_counts_as_theirs(hub, record):
    card()
    assert carried(hub, record, "hand:riley") is None
    hub.sweep()   # sends the hand-off to Riley
    assert carried(hub, record, "approve", by=USER, via="slack") is None
    rec = store.load(RID)
    assert rec["status"] == "approved" and rec["decision"]["by"] == USER


def test_a_change_writes_what_slacks_change_box_writes_and_posts_the_words(hub, record, slack):
    card()
    assert carried(hub, record, "change", words="Make it $200.") is None
    rec = store.load(RID)
    assert rec["status"] == "revising" and rec["events"][-1]["kind"] == "feedback" and rec["events"][-1]["answer"] == 1
    assert slack.notes[-1] == ("1.1", "✏️ Sam asked for a change:\n> Make it $200.")


def test_a_dashboard_change_matches_slacks_change_box(team, slack, monkeypatch):
    card()
    values = {"words": {"words": {"value": "Make it $200."}}}
    answer, follow = listener.Listener().handle_submission(submit_payload(f"{RID}|1|change", values))
    follow()
    from_slack, said = store.load(RID), asker_texts()
    fake = FakeRecord()
    monkeypatch.setattr(store, "record", lambda: fake)
    card()
    L = listener.Listener()
    L.later = lambda follow_up: follow_up()
    carried(L, fake, "change", words="Make it $200.", via="slack")
    assert without_refs(store.load(RID)) == without_refs(from_slack)
    assert asker_texts() == said


@pytest.mark.parametrize("what,verdict", [("approve", "approved"), ("decline", "declined")])
def test_a_yes_or_no_with_a_note_is_the_press_then_his_reply_in_one_write(hub, record, what, verdict, slack):
    card()
    assert carried(hub, record, what, words="Watch the cap.") is None
    rec = store.load(RID)
    assert rec["status"] == verdict and [e["kind"] for e in rec["events"][-2:]] == ["decision", "comment"]
    assert slack.notes[-1] == ("1.1", "💬 Sam replied on the card:\n> Watch the cap.")


def test_pick_which_takes_the_items_ticked(hub, record):
    card(kind="batch")
    assert carried(hub, record, "items", items=[1, 3]) is None
    assert store.load(RID)["decision"]["items"] == [1, 3]


def test_sleep_and_wake_from_the_record(hub, record, slack):
    before = card()
    until = store.iso(NOW + timedelta(hours=1))
    assert carried(hub, record, "sleep", until_at=until) is None
    rec = store.load(RID)
    assert rec["sleep"]["until"] == until and rec["deadline"] == before["deadline"] and not slack.updates
    assert carried(hub, record, "wake") is None and "sleep" not in store.load(RID)


# ---------- answers the hub gives ----------

def test_a_deadline_passing_is_the_hubs_own_answer_through_the_record(hub, record, monkeypatch):
    card()
    monkeypatch.setattr(store, "now", lambda: NOW + timedelta(days=3))
    hub.sweep()
    [row] = record.rows
    assert (row["via"], row["what"], row["key"]) == ("timer", "expire", f"expire:{RID}:{store.iso(NOW + timedelta(days=2))}")
    rec = store.load(RID)
    assert rec["status"] == "expired" and rec["events"][-1] == dict(rec["events"][-1], kind="expired", answer=row["id"])
    hub.sweep()
    assert len(record.rows) == 1   # the same deadline is one answer


def test_a_hand_off_not_answered_in_time_comes_back_through_the_record(hub, record, monkeypatch):
    card()
    carried(hub, record, "hand:riley")
    hub.sweep()
    _, send = latest()
    monkeypatch.setattr(store, "now", lambda: store.parse(send["return_at"]) + timedelta(minutes=1))
    hub.sweep()
    row = record.rows[-1]
    assert (row["via"], row["what"]) == ("timer", "return") and row.get("carried")
    assert store.load(RID)["events"][-1]["kind"] == "returned"


def test_the_deputys_call_is_taken_by_the_record_and_carried_out_by_the_hub(hub, record, monkeypatch, capsys):
    card()
    monkeypatch.setattr(store, "keeps_answers", lambda: True)
    monkeypatch.setattr(store, "give", hub.give)
    cli.main(["call", RID, "--approve", "--why", "Inside the test's budget."])
    [row] = record.rows
    assert (row["via"], row["what"], row["session"], row["words"]) == ("deputy", "call:approve", "sid-dep", "Inside the test's budget.")
    rec = store.load(RID)
    assert rec["status"] == "approved" and rec["decision"]["where"] == "deputy"
    assert "approved for Sam" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["call", RID, "--his", "--why", "Again."])
    assert "Not recorded: it is already decided (approved)." in capsys.readouterr().err


def test_an_answer_an_agent_records_is_taken_by_the_record(hub, record, monkeypatch, capsys):
    card()
    monkeypatch.setattr(store, "keeps_answers", lambda: True)
    monkeypatch.setattr(store, "give", hub.give)
    cli.main(["record", RID, "--declined", "--words", "No, not this month.", "--where", "agent chat"])
    [row] = record.rows
    assert row["detail"] == {"where": "agent chat", "verdict": "declined"} and row["words"] == "No, not this month."
    assert store.load(RID)["status"] == "declined"
    assert capsys.readouterr().out.strip() == f"{RID} recorded as declined (agent chat)."


def test_auto_approval_is_switched_only_by_the_approver_where_the_record_keeps_it(hub, record, monkeypatch, capsys):
    monkeypatch.setattr(store, "keeps_answers", lambda: True)
    with pytest.raises(SystemExit):
        cli.main(["deputy", "--dry-run"])
    assert "Only Sam switches auto-approval, with the button on the morning summary" in capsys.readouterr().err
    assert record.deputy["live"] is True


# ---------- what the hub hears from the record ----------

def test_the_hub_carries_out_what_waits_when_it_starts_and_as_each_card_changes(hub, record, monkeypatch):
    card()
    taken(record, "approve")
    events = iter([(10, None), (11, {"thing": RID, "kind": "approval", "pod": None})])

    def stream(after=None):
        for item in events:
            yield item
        hub.stop.set()
    record.changes = stream
    hub.hear()
    assert hub.answers_waiting.is_set()
    hub.stop.clear()
    hub.answers_waiting.wait(0)
    hub.carry_waiting()
    assert store.load(RID)["status"] == "approved"


def test_a_line_said_in_a_cards_thread_reads_the_thread_now(hub, record):
    """The record keeps a card's Slack thread on the card, so a line there is a change to the card that says "said".
    On November 22, 2026, when the hub read a thread at once only on a change to a thread, five of his lines waited
    up to two hours for the rotation."""
    rec = card()
    events = iter([(10, None), (11, {"thing": RID, "kind": "approval", "pod": None, "what": "new version"}),
                   (12, {"thing": RID, "kind": "approval", "pod": None, "what": "said"})])

    def stream(after=None):
        for event_id, change in events:
            yield event_id, change
            if event_id == 11:
                assert not hub.poke   # a change to the card that is no line reads nothing
        hub.stop.set()
    record.changes = stream
    hub.hear()
    assert hub.poke == {(CHANNEL, rec["thread_ts"])}
    assert hub.answers_waiting.is_set()


def test_after_a_restart_the_most_recently_active_cards_threads_are_read_first(hub, record, slack, monkeypatch):
    """Every thread is due at once when the hub starts; the cards in conversation go first, not in card-number order."""
    for minutes, rid in enumerate(("AP-0001", "AP-0002", "AP-0003", "AP-0004", "AP-0005")):
        card(rid)
        store.update(rid, lambda r: r["events"].append({"kind": "ack", "at": store.iso(NOW + timedelta(minutes=minutes))}))
    store.update("AP-0002", lambda r: r["events"].append({"kind": "comment", "at": store.iso(NOW + timedelta(hours=1))}))
    monkeypatch.setattr(listener, "POLL_BUDGET_PER_MIN", 36)   # three threads a round
    read = []
    monkeypatch.setattr(hub, "poll_thread", lambda rec, send=None: read.append(rec["id"]))
    hub.poll_once()
    assert read == ["AP-0002", "AP-0005", "AP-0004"]


def test_the_summary_shows_the_switch_when_the_record_says_auto_approval_changed(hub, record, monkeypatch, slack):
    digest._save_state({"ts": "5.0", "posted_at": store.iso(NOW), "date": "2026-01-12"})
    blocks = [{"type": "section", "block_id": "s"}, digest._switch(True)]
    monkeypatch.setattr(slackapi, "history", lambda oldest, **kw: [{"ts": "5.1", "blocks": blocks, "text": "summary"}])
    hub.lists_changed()
    assert not slack.updates   # the first look only learns where it stands
    record.deputy = dict(record.deputy, live=False, live_since=None)
    hub.lists_changed()
    [(ts, drawn, _)] = slack.updates
    assert ts == "5.1" and drawn[-1]["elements"][0]["value"] == "deputy|0|live"
    assert drawn[-2]["block_id"] == "deputy-mode" and "paused by Sam" in drawn[-2]["elements"][0]["text"]


# ---------- who may answer, and the pop-ups ----------

def test_who_may_answer_comes_from_the_record_and_each_teammates_hours_from_the_settings(record, monkeypatch):
    s = config.file_settings()
    s["team"]["riley"]["quiet"] = ["18:00", "05:00"]
    config.CONFIG.write_text(json.dumps(s))
    record.who = lambda: {"channel": "CREC", "approver": "UREC", "approver_name": "Amir", "approvers": {},
                          "team_answers_any": False, "team": {"joey": {"user": "UTEAM2", "name": "Joey"},
                                                              "justin": {"user": USER, "name": "Justin"}},
                          "team_order": ["justin", "joey"], "deputy": None}
    config.forget_lists()
    held = config.settings()
    assert (held["channel"], held["approver"], held["approver_name"]) == ("CREC", "UREC", "Amir")
    assert list(held["team"]) == ["justin", "joey"]
    assert held["team"]["justin"] == {"user": USER, "name": "Justin", "quiet": ["18:00", "05:00"]}
    assert held["team"]["joey"] == {"user": "UTEAM2", "name": "Joey"}


def test_each_version_is_kept_with_every_pop_up_its_buttons_open(hub, record):
    card(kind="batch")
    v = record.read(RID)[0]["versions"][0]
    items = len(v["card"]["items"])
    assert {"see", "change", "items", *(f"items:{k}" for k in range(items + 1))} <= set(v["views"])
    assert v["views"]["items:2"]["submit"]["text"].endswith("2")
    assert v["views"]["see"]["private_metadata"] == f"{RID}|1|see"
    record.cards[RID][1]["versions"][0].pop("views")
    hub.keep_views()
    assert "views" in record.read(RID)[0]["versions"][0]


def test_team_lists_the_hand_to_menu_in_its_order(team, capsys):
    cli.main(["team"])
    assert json.loads(capsys.readouterr().out) == [{"user": USER, "name": "Riley"}, {"user": "UTEAM2", "name": "Morgan"}]


def test_a_hub_reaches_its_record_with_the_token_its_own_settings_name():
    """The token is read from this machine's own settings: the full settings ask the record who may answer, which
    needs the token, so reading it through them never ended (the first start of a hub on this branch, November 21)."""
    held = json.loads(config.CONFIG.read_text())
    config.CONFIG.write_text(json.dumps(held | {"hub": "testbox", "hub_url": "http://127.0.0.1:1",
                                                "record_url": "http://127.0.0.1:2", "record_token": "made-up"}))
    store._record.clear()
    assert store.record().token == "made-up"


# ---------- a comment typed on the pod dashboard ----------

def posting(slack, monkeypatch, refuse=None):
    """Slack's note answering with its post's id, as the real one does; or refusing, in Slack's own error."""
    def note(ts, text, **kw):
        if refuse:
            raise slackapi_error(refuse)
        slack.notes.append((ts, text))
        return f"9.{len(slack.notes)}"
    monkeypatch.setattr(slackapi, "note", note)


def reply_item(rec, kind="feedback"):
    return next(i for i in rec["outbox"] if i["kind"] == kind and not i.get("to"))


def deliver_reply(minutes_ago=0):
    at = store.iso(store.now() - timedelta(minutes=minutes_ago))
    store.update(RID, lambda r: reply_item(r).update(delivered_at=at))


def slackapi_error(code):
    from slack_sdk.errors import SlackApiError
    return SlackApiError("The request to the Slack API failed.", {"ok": False, "error": code})


def test_a_comment_is_posted_in_the_thread_and_reaches_the_asker_as_his_reply(hub, record, slack, monkeypatch):
    card()
    posting(slack, monkeypatch)
    assert carried(hub, record, "comment", words="why does this need a backend change?") is None
    rec = store.load(RID)
    assert slack.notes[-1] == ("1.1", "💬 Sam replied on the card:\n> why does this need a backend change?")
    reply = rec["events"][-1]
    assert (reply["kind"], reply["ts"], reply["via"], reply["answer"]) == ("feedback", "9.1", "dashboard", 1)
    assert rec["status"] == "revising" and reply_item(rec)["react_ts"] == "9.1"
    [said] = asker_texts()
    assert "replied on the pod dashboard" in said and "why does this need a backend change?" in said
    assert record.rows[0]["carried"]


def test_a_comment_on_a_decided_card_changes_nothing_about_it(hub, record, slack, monkeypatch):
    card()
    carried(hub, record, "approve")
    posting(slack, monkeypatch)
    assert carried(hub, record, "comment", words="wait, I take that back") is None
    rec = store.load(RID)
    assert rec["status"] == "approved" and rec["events"][-1]["kind"] == "comment"


def test_slack_refusing_the_post_refuses_the_comment_in_slacks_words(hub, record, slack, monkeypatch):
    card()
    posting(slack, monkeypatch, refuse="not_in_channel")
    assert carried(hub, record, "comment", words="hello") == "not_in_channel"
    assert record.rows[0]["refused"] == "not_in_channel" and store.load(RID)["status"] == "pending"


def test_a_reply_not_acknowledged_is_asked_again_at_ten_minutes_then_every_thirty(hub, record, slack, monkeypatch):
    card()
    posting(slack, monkeypatch)
    carried(hub, record, "comment", words="give me a table by game mode")
    deliver_reply(minutes_ago=11)
    hub.ask_again(store.load(RID))
    hub.ask_again(store.load(RID))
    rec = store.load(RID)
    asked = rec["outbox"][-1]
    assert asked["kind"] == "again" and not asked["needs_ack"] and not asked.get("to")
    assert "Asked again" in asked["text"] and "give me a table by game mode" in asked["text"]
    assert len(reply_item(rec)["asked_again"]) == 1
    half_hour_ago = [store.iso(store.now() - timedelta(minutes=31))]
    store.update(RID, lambda r: reply_item(r).update(asked_again=half_hour_ago))
    hub.ask_again(store.load(RID))
    assert len(reply_item(store.load(RID))["asked_again"]) == 2
    store.update(RID, core.mark_acked)
    before = len(store.load(RID)["outbox"])
    store.update(RID, lambda r: reply_item(r).update(asked_again=half_hour_ago))
    hub.ask_again(store.load(RID))
    assert len(store.load(RID)["outbox"]) == before


def test_recording_his_answer_acknowledges_what_was_sent(hub, record, slack, monkeypatch):
    card()
    posting(slack, monkeypatch)
    carried(hub, record, "comment", words="No dude. Don't introduce this complexity right now")
    deliver_reply()
    assert carried(hub, record, "record", via="recorded",
                   detail={"verdict": "declined", "where": "Slack thread"},
                   words="No dude. Don't introduce this complexity right now") is None
    assert reply_item(store.load(RID))["acked_at"]
