"""`approvals press`: a press from the pod dashboard runs the Slack press's own steps, and Sleep and Wake now."""
import copy
from datetime import timedelta

import pytest

from approvals import cli, core, store
from test_deputy import to
from test_kinds import submit_payload
from test_team import NOW, RID, USER, card, click, latest, slack, team


APPROVER = "UAPPROVER"
CHANNEL = "CTEST"


def press(capsys, *args, by=APPROVER, n=1):
    code = 0
    try:
        cli.main(["press", RID, str(n), *args, "--by", by])
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr().out.strip()


def asker_texts(rid=RID):
    return [m["text"].rsplit(" (ref ", 1)[0] for m in to(rid)]


def without_refs(rec):
    rec = copy.deepcopy(rec)
    for m in rec.get("outbox", []):
        m.pop("ref"), m.pop("text")
    for e in rec.get("events", []):
        e.pop("actor", None)
    return rec


@pytest.mark.parametrize("action,what", [("approve", "approve"), ("decline", "decline"), ("remove", "remove")])
def test_a_dashboard_press_saves_and_redraws_what_the_slack_press_does(team, slack, capsys, action, what):
    card()
    click(what, user=APPROVER, channel=CHANNEL)
    from_slack, slack_updates, slack_said = store.load(RID), list(slack.updates), asker_texts()
    store.delete(RID)
    slack.updates.clear()
    card()
    assert press(capsys, action) == (0, "ok")
    from_dashboard = store.load(RID)
    assert without_refs(from_dashboard) == without_refs(from_slack)
    assert slack.updates == slack_updates
    assert from_dashboard["events"][-1]["actor"] == APPROVER
    assert [t.replace("on the pod dashboard", "in Slack") for t in asker_texts()] == slack_said
    assert any("on the pod dashboard" in t for t in asker_texts())


def test_the_asker_hears_the_pod_dashboard_named_as_where_he_answered(team, slack, capsys):
    card()
    press(capsys, "approve")
    [yes] = asker_texts()
    assert "Sam approved it (button on the pod dashboard, Mon 12:00 pm)." in yes and "in Slack" not in yes


def test_a_press_on_a_replaced_version_is_refused_in_slacks_words(team, slack, capsys):
    rec = card()
    rec["versions"].append(dict(rec["versions"][0], n=2))
    store.save(rec)
    assert press(capsys, "approve", n=1) == (1, "That version was replaced; use the newest card's buttons.")
    assert store.load(RID) == rec and not slack.updates


def test_a_teammate_cannot_press_a_card_not_handed_to_them(team, slack, capsys):
    rec = card()
    assert press(capsys, "approve", by=USER) == (1, "Only Sam can decide approvals.")
    assert store.load(RID) == rec


def test_a_teammates_yes_on_a_handed_card_counts_as_theirs(team, slack, capsys):
    card()
    assert press(capsys, "hand", "--teammate", USER) == (0, "ok")
    assert latest()[1]["name"] == "Riley"
    from approvals import listener
    listener.Listener().sweep()   # delivers the hand-off to Riley
    assert press(capsys, "approve", by=USER) == (0, "ok")
    rec = store.load(RID)
    assert rec["status"] == "approved" and rec["decision"]["by"] == USER
    assert "Riley approved it (button on the pod dashboard" in asker_texts()[-1]


def test_hand_to_someone_not_on_the_team_is_refused(team, slack, capsys):
    rec = card()
    assert press(capsys, "hand", "--teammate", "UNOBODY") == (1, "That button doesn't match the card any more; use the newest card's buttons.")
    assert store.load(RID) == rec


def test_not_mine_posts_the_return_line_slack_posts(team, slack, capsys):
    card()
    press(capsys, "hand", "--teammate", USER)
    from approvals import listener
    listener.Listener().sweep()
    assert press(capsys, "notmine", by=USER) == (0, "ok")
    assert any("Riley handed this back, so it's back with you." in b[0]["text"]["text"]
               for _, b, kw in slack.posts if kw.get("broadcast"))


def test_change_writes_what_slacks_change_box_writes_and_posts_the_words_in_the_thread(team, slack, capsys):
    card()
    assert press(capsys, "change", "--words", "Make it $200.") == (0, "ok")
    rec = store.load(RID)
    assert rec["status"] == "revising" and rec["events"][-1]["kind"] == "feedback"
    assert slack.notes[-1] == ("1.1", "✏️ Sam asked for a change:\n> Make it $200.")
    assert "Sam replied on the pod dashboard at Mon 12:00 pm: \"Make it $200.\"" in asker_texts()[-1]


def test_empty_words_are_refused(team, slack, capsys):
    rec = card()
    assert press(capsys, "change", "--words", "  ") == (1, "The words can't be empty.")
    assert store.load(RID) == rec


@pytest.mark.parametrize("action,verdict", [("approve-note", "approved"), ("decline-note", "declined")])
def test_a_yes_or_no_with_a_note_is_the_press_then_his_reply_in_one_write(team, slack, capsys, action, verdict):
    card()
    assert press(capsys, action, "--words", "Watch the cap.") == (0, "ok")
    rec = store.load(RID)
    assert rec["status"] == verdict
    assert [e["kind"] for e in rec["events"][-2:]] == ["decision", "comment"]
    first, second = asker_texts()
    assert f"Sam {verdict} it (button on the pod dashboard" in first
    assert "Sam replied on the pod dashboard at Mon 12:00 pm, after the decision: \"Watch the cap.\"" in second
    assert slack.notes[-1] == ("1.1", "💬 Sam replied on the card:\n> Watch the cap.")


def test_edit_the_words_writes_what_slacks_box_writes(team, slack, capsys):
    card(kind="as_you")
    assert press(capsys, "edit", "--words", "Hello there.") == (0, "ok")
    rec = store.load(RID)
    assert rec["decision"]["edited"] == "Hello there." and rec["decision"]["where"] == "pop-up"
    assert "approved it with his own edited words (Edit the words on the pod dashboard" in asker_texts()[-1]


def test_edit_the_words_is_refused_where_slack_has_no_such_button(team, slack, capsys):
    rec = card()
    assert press(capsys, "edit", "--words", "Hello.")[0] == 1
    assert store.load(RID) == rec


def test_a_dashboard_change_matches_slacks_change_box(team, slack, capsys):
    from approvals import listener
    card()
    values = {"words": {"words": {"value": "Make it $200."}}}
    L = listener.Listener()
    answer, follow = L.handle_submission(submit_payload(f"{RID}|1|change", values))
    follow()
    from_slack, said = store.load(RID), asker_texts()
    store.delete(RID)
    card()
    press(capsys, "change", "--words", "Make it $200.")
    assert without_refs(store.load(RID)) == without_refs(from_slack)
    assert [t.replace("on the pod dashboard", "in Slack") for t in asker_texts()] == said


# ---------- Sleep and Wake now ----------

def sleep_until(hours):
    return store.iso(NOW + timedelta(hours=hours))


def test_sleep_holds_the_card_until_its_time_and_changes_nothing_else(team, slack, capsys):
    before = card()
    assert press(capsys, "sleep", "--until", sleep_until(1)) == (0, "ok")
    rec = store.load(RID)
    assert rec["sleep"] == {"until": sleep_until(1), "by": APPROVER, "at": store.iso(NOW)}
    assert rec["events"][-1] == {"kind": "slept", "at": store.iso(NOW), "version": 1, "until": sleep_until(1),
                                 "actor": APPROVER}
    assert (rec["deadline"], rec["status"], rec.get("card_dirty")) == (before["deadline"], "pending", before.get("card_dirty"))
    assert not to(RID) and not slack.updates and not slack.posts


@pytest.mark.parametrize("hours", [-1, 0, 48, 60])
def test_sleep_refuses_a_time_past_or_not_before_the_deadline(team, slack, capsys, hours):
    rec = card()
    assert press(capsys, "sleep", "--until", sleep_until(hours)) == (1, "Choose a future time before the answer deadline.")
    assert store.load(RID) == rec


def test_sleep_refuses_a_replaced_version_a_decided_card_and_anyone_but_the_approver(team, slack, capsys):
    rec = card()
    rec["versions"].append(dict(rec["versions"][0], n=2))
    store.save(rec)
    assert press(capsys, "sleep", "--until", sleep_until(1), n=1)[1] == "That version was replaced; use the newest card's buttons."
    assert press(capsys, "sleep", "--until", sleep_until(1), n=2, by=USER)[1] == "Only Sam can decide approvals."
    press(capsys, "approve", n=2)
    assert press(capsys, "sleep", "--until", sleep_until(1), n=2)[1] == "This one is already decided."


def test_wake_now_ends_the_sleep_and_tells_nobody(team, slack, capsys):
    card()
    press(capsys, "sleep", "--until", sleep_until(1))
    assert press(capsys, "wake") == (0, "ok")
    rec = store.load(RID)
    assert "sleep" not in rec and rec["events"][-1]["kind"] == "woke" and rec["events"][-1]["version"] == 1
    assert not to(RID) and not slack.updates
    assert press(capsys, "wake") == (1, "This one is already decided.")


def test_any_answer_ends_the_sleep_whether_from_slack_or_the_dashboard(team, slack, capsys):
    card()
    press(capsys, "sleep", "--until", sleep_until(1))
    click("approve", user=APPROVER, channel=CHANNEL)
    assert "sleep" not in store.load(RID)
    store.delete(RID)
    card()
    press(capsys, "sleep", "--until", sleep_until(1))
    press(capsys, "hand", "--teammate", USER)
    assert "sleep" not in store.load(RID)


def test_press_runs_only_on_the_hub(team, slack, capsys, monkeypatch):
    card()
    monkeypatch.setenv("APPROVALS_MACHINE", "elsewhere")
    cfg = team
    cfg["hub"] = "testbox"
    from approvals import config
    import json
    config.CONFIG.write_text(json.dumps(cfg))
    with pytest.raises(SystemExit):
        cli.main(["press", RID, "1", "approve", "--by", APPROVER])
    assert "approvals press runs on the workspace's hub." in capsys.readouterr().err
