"""The deputy hears every card and makes its own call on each: recorded and shown in a dry run, the approver's yes once
auto-approval is on, his to take back, and summed up each morning. The asking agents change nothing they do."""
import json
import queue
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from approvals import basecamp, cards, cli, config, core, digest, listener, reach, router, slackapi, store
from kinds import KINDS
from samples import SAMPLES
from test_cards_store import make_rec
from test_hub import hubbox  # noqa: F401 (a fixture)
from test_kinds import press_payload, rec_for, run, slack, txt, values  # noqa: F401 (slack is a fixture)

DEPUTY = {"agent": "Exec · approver", "runtime": "claude", "session_id": "sid-dep", "machine": "testbox"}
ASKER = {"agent": "Pricing", "runtime": "claude", "session_id": "sid-a", "machine": "testbox"}
APPROVER = "UAPPROVER"


def be_deputy(live=False, **kw):
    store.set_deputy({"reach": dict(DEPUTY, **kw), "live": live, "test": False, "since": store.iso(), "live_since": None})


def as_session(monkeypatch, sid, agent="Exec · approver"):
    me = {"agent": agent, "runtime": "claude", "session_id": sid, "cwd": "/x", "machine": "testbox"}
    monkeypatch.setattr(reach, "capture", lambda agent=None: dict(me))
    return me


def card(rid="AP-0001", kind=None, sample="budget", **kw):
    rec = rec_for(KINDS[kind] if kind else SAMPLES[sample], id=rid, thread_ts=f"{rid[-1]}.1", reach=dict(ASKER), **kw)
    rec["versions"][0]["posted_at"] = rec["created"]
    store.save(rec)


def to(rid, who=None):
    return [i for i in store.load(rid)["outbox"] if i.get("to") == who]


def press(rid, what, n=1):
    return store.update(rid, lambda r: core.press(r, n, what, by=APPROVER))


def call(rid, *args):
    cli.main(["call", rid, *args])


# ---------- the role ----------

def test_the_role_is_taken_moved_with_its_mode_switched_and_ended(monkeypatch, capsys):
    me = as_session(monkeypatch, "sid-1")
    cli.main(["deputy", "--agent", "Exec · approver"])
    d = store.deputy()
    assert config.DEPUTY.exists() and d["reach"]["session_id"] == "sid-1" and d["live"] is False and d["test"] is False
    cli.main(["deputy", "--live"])
    assert store.deputy()["live"] and store.deputy()["live_since"] and "Auto-approval is on now" in capsys.readouterr().out
    me["session_id"] = "sid-2"   # a restored session takes the role where the old one stood
    cli.main(["deputy", "--agent", "Exec · approver"])
    assert store.deputy()["reach"]["session_id"] == "sid-2" and store.deputy()["live"]
    capsys.readouterr()
    cli.main(["deputy"])
    assert "Exec · approver is the deputy, from session sid-2 on testbox, and auto-approval is on" in capsys.readouterr().out
    cli.main(["deputy", "--dry-run"])
    assert not store.deputy()["live"] and store.deputy()["live_since"] is None and "paused" in capsys.readouterr().out
    cli.main(["deputy", "--off"])
    assert store.deputy() is None
    me["session_id"] = None
    with pytest.raises(SystemExit):
        cli.main(["deputy", "--agent", "Exec · approver"])


def test_repoint_moves_the_deputy_with_its_session(monkeypatch, capsys):
    be_deputy(live=True)
    monkeypatch.setattr(reach, "running", lambda sid: dict(DEPUTY, session_id="sid-new", agent=None) if sid == "sid-new" else None)
    cli.main(["repoint", "sid-dep", "sid-new"])
    d = store.deputy()
    assert (d["reach"]["session_id"], d["reach"]["agent"], d["live"]) == ("sid-new", "Exec · approver", True)
    assert "the deputy's role" in capsys.readouterr().out


def test_the_deputy_is_kept_on_the_hub_and_hears_on_its_own_machine(hubbox):
    """The deputy runs on the laptop; a card asked from the hub machine reaches it there."""
    store.set_deputy({"reach": dict(DEPUTY, machine="laptop"), "live": False, "test": False})
    assert config.DEPUTY.exists() and store.deputy()["reach"]["session_id"] == "sid-dep"
    rec = make_rec(SAMPLES["money"], reach=dict(ASKER, machine="hubbox"))
    rec.pop("id")
    rid = store.create(rec)["id"]
    store.update(rid, lambda r: core.decide(r, 1, "declined", by=APPROVER))
    asker, dep = store.load(rid)["outbox"]
    client = store.hub_client()
    assert dep["to"] == "deputy" and client.due("laptop")[0] == [[rid, dep["ref"]]]
    assert client.due("hubbox")[0] == [[rid, asker["ref"]]]


# ---------- what the deputy hears ----------

def test_the_deputy_hears_each_card_posted_and_each_new_version(monkeypatch, slack, tmp_path):
    be_deputy()
    path = tmp_path / "card.json"
    path.write_text(json.dumps(SAMPLES["budget"]))
    as_session(monkeypatch, "sid-a", agent="Pricing")
    cli.main(["submit", str(path)])
    [item] = to("AP-0001", "deputy")
    assert item["text"].startswith(
        '[Approvals] AP-0001 v1 "Add $250 to the search ads learning budget": a budget card from Pricing, posted for '
        "Sam's answer. Make your call: `approvals show AP-0001` reads it, `approvals pictures AP-0001` saves its pictures "
        'where you can look at them, then `approvals call AP-0001 --approve --why "<why>"` or `approvals call AP-0001 --his --why "<why>"`.')
    assert item["kind"] == "deputy" and not item["needs_ack"] and to("AP-0001") == []
    path.write_text(json.dumps(dict(SAMPLES["budget"], changes="Cut to $200")))
    cli.main(["revise", "AP-0001", str(path)])
    text = to("AP-0001", "deputy")[-1]["text"]
    assert 'Pricing posted v2 ("Cut to $200")' in text and "doesn't carry to this version" in text and "approvals call AP-0001" in text
    # A TEST card stays away from the real deputy, and the deputy's own cards don't come back to it.
    path.write_text(json.dumps(dict(SAMPLES["budget"], test=True)))
    cli.main(["submit", str(path)])
    as_session(monkeypatch, "sid-dep")
    path.write_text(json.dumps(SAMPLES["budget"]))
    cli.main(["submit", str(path)])
    assert to("AP-0002", "deputy") == [] and to("AP-0003", "deputy") == []


def test_the_deputy_hears_what_the_approver_says_or_does_but_not_a_plain_yes(monkeypatch, slack):
    be_deputy()
    for rid in ("AP-0001", "AP-0002", "AP-0003", "AP-0004", "AP-0005"):
        card(rid)
    card("AP-0006", "batch")
    card("AP-0007", "batch")
    card("AP-0008", "choice")
    card("AP-0009", "set")
    press("AP-0001", "approve")                                     # a plain yes: read from the record later
    press("AP-0002", "decline")
    store.update("AP-0003", lambda r: core.take_replies(r, [("5.0", "make it $200,\nnot $250")]))
    store.update("AP-0004", lambda r: core.decide(r, 1, "approved", by=APPROVER, where="pop-up", edited="Hi there"))
    press("AP-0005", "remove")
    store.update("AP-0006", lambda r: core.decide(r, 1, "approved", by=APPROVER, where="pop-up", items=[1, 2]))
    store.update("AP-0007", lambda r: core.decide(r, 1, "approved", by=APPROVER, where="pop-up", items=list(range(1, 12))))
    press("AP-0008", "pick:0")
    press("AP-0009", "p1:approve")                                  # a row's plain yes
    store.update("AP-0009", lambda r: core.ask_change(r, 1, "shorter", by=APPROVER, j=2))
    store.update("AP-0001", lambda r: core.take_replies(r, [("6.0", "and tell me when it's live")]))
    said = {rid: [i["text"] for i in to(rid, "deputy")] for rid in
            ("AP-0001", "AP-0002", "AP-0003", "AP-0004", "AP-0005", "AP-0006", "AP-0007", "AP-0008", "AP-0009")}
    assert len(said["AP-0001"]) == 1 and "replied in the thread after his decision: \"and tell me when it's live\"" in said["AP-0001"][0]
    assert "Sam declined it (button in Slack" in said["AP-0002"][0]
    assert 'Sam replied in the thread: "make it $200, / not $250".' in said["AP-0003"][0]
    assert 'Sam approved it with his own edited words (in Slack' in said["AP-0004"][0] and '"Hi there"' in said["AP-0004"][0]
    assert "removed it from his list" in said["AP-0005"][0]
    assert "approved items 1, 2 of 11" in said["AP-0006"][0] and said["AP-0007"] == []
    assert "Sam picked A" in said["AP-0008"][0]
    assert len(said["AP-0009"]) == 1 and 'asked for a change to part 2' in said["AP-0009"][0] and '"shorter"' in said["AP-0009"][0]
    # The asker's own messages are what they always were.
    assert [i["kind"] for i in to("AP-0003")] == ["feedback"] and [i["kind"] for i in to("AP-0002")] == ["decision"]


def test_the_deputy_hears_a_withdrawal_with_its_reason(monkeypatch, slack):
    be_deputy()
    card()
    as_session(monkeypatch, "sid-a", agent="Pricing")
    cli.main(["withdraw", "AP-0001", "Prices changed; a new card is coming"])
    assert 'Tester withdrew it: "Prices changed; a new card is coming".' in to("AP-0001", "deputy")[-1]["text"]


def test_with_no_deputy_nothing_changes(slack):
    card()
    press("AP-0001", "decline")
    assert [i.get("to") for i in store.load("AP-0001")["outbox"]] == [None]


# ---------- the call ----------

def test_a_dry_run_approve_is_recorded_and_shown_and_approves_nothing(monkeypatch, slack, capsys):
    be_deputy()
    card()
    as_session(monkeypatch, "sid-dep")
    call("AP-0001", "--approve", "--why", "A $250 top-up inside the test's cap; he has said yes to each of these.")
    rec = store.load("AP-0001")
    c = rec["calls"][0]
    assert (c["version"], c["call"], c["by"], c["live"]) == (1, "approve", "Exec · approver", False)
    assert rec["status"] == "pending" and rec["decision"] is None and rec["outbox"] == [] and rec["events"][-1]["kind"] == "call"
    assert "a dry run, so nothing was approved" in capsys.readouterr().out
    blocks = [p for p in slack if p.get("update") == "1.1"][-1]["blocks"]
    assert blocks[-2] == cards.ctx("🤖 Dry run · Exec · approver would have approved this for you: A $250 top-up inside "
                                   "the test's cap; he has said yes to each of these.")
    assert "AP-0001|1|approve" in values(blocks)   # still his to answer


def test_his_call_shows_one_line_and_changes_nothing_in_either_mode(monkeypatch, slack):
    for live in (False, True):
        store.set_deputy(None)
        be_deputy(live=live)
        rid = "AP-0001" if not live else "AP-0002"
        card(rid)
        as_session(monkeypatch, "sid-dep")
        call(rid, "--his", "--why", "A new kind of spend he hasn't seen before.")
        rec = store.load(rid)
        assert rec["status"] == "pending" and rec["outbox"] == []
        assert "🤖 Exec · approver left this for you: A new kind of spend he hasn't seen before." in txt(cards.card_blocks(rec, 1, "open"))


def test_with_auto_approval_on_the_approve_is_his_yes_and_done_works(monkeypatch, slack, capsys):
    be_deputy(live=True)
    store.set_reviewer({"types": ["budget"], "reach": {"agent": "CFO", "runtime": "claude", "session_id": "sid-cfo", "machine": "testbox"}})
    card()
    as_session(monkeypatch, "sid-dep")
    call("AP-0001", "--approve", "--why", "Inside the test's cap.")
    rec = store.load("AP-0001")
    d = rec["decision"]
    assert rec["status"] == "approved" and {k: d[k] for k in ("verdict", "version", "by", "where", "why", "words")} == {
        "verdict": "approved", "version": 1, "by": "Exec · approver", "where": "deputy", "why": "Inside the test's cap.", "words": None}
    [msg] = to("AP-0001")
    assert msg["kind"] == "decision" and msg["needs_ack"] and msg["react_ts"] == "1.1"
    assert msg["text"].startswith('[Approvals] AP-0001 v1 "Add $250 to the search ads learning budget": approved for Sam by '
                                  'his deputy, Exec · approver (auto-approval, ')
    assert ('Why: "Inside the test\'s cap.". It counts as his yes, for exactly what the card describes. First acknowledge'
            in msg["text"] and "approvals done AP-0001" in msg["text"])
    assert "Sam approved it (auto-approval by his deputy" in to("AP-0001", "reviewer")[0]["text"]
    assert to("AP-0001", "deputy") == []   # its own yes doesn't come back to it
    assert "approved for Sam; its agent has his yes" in capsys.readouterr().out
    assert basecamp.wanted(rec)[0].startswith("AP-0001 · auto-approved ")
    final = txt(cards.card_blocks(rec, 1, "final"))
    assert "🤖 Auto-approved: Add $250" in final and "🤖 Approved for you by Exec · approver" in final and "Inside the test's cap." in final
    assert "Approved by Sam" not in final and values(cards.card_blocks(rec, 1, "final")) == ["AP-0001|1|see", "AP-0001|1|takeback"]
    # Paced like any yes: held while the asker hasn't acknowledged another, and done works after it.
    as_session(monkeypatch, "sid-a", agent="Pricing")
    cli.main(["done", "AP-0001", "Raised the cap to $700; read it back"])
    assert store.load("AP-0001")["status"] == "done"


def test_with_auto_approval_on_a_set_and_a_batch_are_approved_whole(monkeypatch, slack):
    be_deputy(live=True)
    card("AP-0001", "set")
    card("AP-0002", "batch")
    as_session(monkeypatch, "sid-dep")
    call("AP-0001", "--approve", "--why", "Four copy fixes he asked for.")
    call("AP-0002", "--approve", "--why", "The creator emails he approves every time.")
    s, b = store.load("AP-0001"), store.load("AP-0002")
    assert s["status"] == "approved" and {a["verdict"] for a in s["answers"].values()} == {"approved"} and len(s["answers"]) == 4
    assert s["decision"]["where"] == "deputy" and len(to("AP-0001")) == 1
    assert b["status"] == "approved" and "items" not in b["decision"]
    final = txt(cards.card_blocks(s, 1, "final"))
    assert "🤖 Auto-approved: Payers keep using" in final and "AP-0001|1|takeback" in values(cards.card_blocks(s, 1, "final"))


@pytest.mark.parametrize("setup,args,why", [
    (lambda: press("AP-0001", "approve"), ["--his"], "it is already decided (approved)"),
    (lambda: press("AP-0001", "decline"), ["--his"], "it is already decided (declined)"),
    (lambda: press("AP-0001", "remove"), ["--his"], "he removed it from his list"),
    (lambda: store.update("AP-0001", lambda r: core.expire(r)), ["--his"], "it closed with no answer by its deadline"),
    (lambda: store.update("AP-0001", lambda r: core.take_replies(r, [("5.0", "why $250?")])), ["--his"],
     "it is back with its agent after Sam's reply"),
    (lambda: store.update("AP-0001", lambda r: (core.take_replies(r, [("5.0", "why $250?")]), r.update(status="pending"))),
     ["--approve"], "Sam has already answered on v1"),
    (lambda: call("AP-0001", "--his", "--why", "New spend."), ["--approve"], "v1 already has your call"),
])
def test_a_call_is_refused_with_nothing_recorded_when_the_card_isnt_waiting_on_his_first_answer(
        monkeypatch, slack, capsys, setup, args, why):
    be_deputy(live=True)
    card()
    as_session(monkeypatch, "sid-dep")
    setup()
    before = store.load("AP-0001")
    capsys.readouterr()
    with pytest.raises(SystemExit):
        call("AP-0001", *args, "--why", "Routine.")
    assert why in capsys.readouterr().err
    after = store.load("AP-0001")
    assert after.get("calls") == before.get("calls") and after["outbox"] == before["outbox"] and after["status"] == before["status"]


def test_an_approve_is_refused_on_a_pick_a_row_that_needs_its_own_yes_and_from_any_other_session(monkeypatch, slack, capsys):
    be_deputy(live=True)
    card("AP-0001", "choice")
    card("AP-0002", "spend")     # a row with options
    card("AP-0003", "release")   # rows that each need his own yes
    card("AP-0004")
    as_session(monkeypatch, "sid-dep")
    for rid, said in (("AP-0001", "a pick is the approver's"), ("AP-0002", "a pick is the approver's"),
                      ("AP-0003", "needs the approver's own yes")):
        with pytest.raises(SystemExit):
            call(rid, "--approve", "--why", "Routine.")
        assert said in capsys.readouterr().err and not store.load(rid).get("calls")
    call("AP-0001", "--his", "--why", "A design choice is his.")   # leaving it for him is always fine
    for why, said in (("  ", "Say why"), ("x" * 301, "The why is 301 characters; keep it under 300")):
        with pytest.raises(SystemExit):
            call("AP-0004", "--approve", "--why", why)
        assert said in capsys.readouterr().err
    as_session(monkeypatch, "sid-a")
    with pytest.raises(SystemExit):
        call("AP-0004", "--approve", "--why", "Routine.")
    assert "Only the deputy's own session can make a call, and that is Exec · approver's session" in capsys.readouterr().err
    assert not store.load("AP-0004").get("calls")


# ---------- taking it back ----------

def auto_approved(monkeypatch, rid="AP-0001", kind=None):
    be_deputy(live=True)
    card(rid, kind)
    as_session(monkeypatch, "sid-dep")
    call(rid, "--approve", "--why", "Inside the test's cap.")


def test_take_it_back_makes_it_his_decline_and_tells_the_asker_and_the_deputy(monkeypatch, slack):
    auto_approved(monkeypatch)
    L = listener.Listener()
    run(L.handle_click(press_payload("AP-0001|1|takeback", user="U_SOMEONE")))
    assert slack[-1] == {"ephemeral": "Only Sam can decide approvals."} and store.load("AP-0001")["status"] == "approved"
    run(L.handle_click(press_payload("AP-0001|1|takeback")))
    rec = store.load("AP-0001")
    assert rec["status"] == "declined" and rec["taken_back"]["by"] == APPROVER and rec["events"][-1]["kind"] == "taken_back"
    assert {k: rec["decision"][k] for k in ("verdict", "by", "where")} == {
        "verdict": "declined", "by": APPROVER, "where": "took back the auto-approval"}
    msg = to("AP-0001")[-1]
    assert msg["needs_ack"] and "Sam took back the approval his deputy gave (button in Slack, " in msg["text"]
    assert "so it is not approved. First acknowledge it in the thread" in msg["text"] and "that you are stopping, and what is already done" in msg["text"]
    assert "If you haven't acted, don't." in msg["text"] and 'approvals status AP-0001 "<what>"' in msg["text"]
    assert "Sam took back your approval" in to("AP-0001", "deputy")[-1]["text"]
    final = txt(cards.card_blocks(rec, 1, "final"))
    assert "↩️ You took it back: Add $250" in final and "↩️ Auto-approval taken back by Sam, " in final
    assert "Exec · approver had approved it: Inside the test's cap." in final
    assert values(cards.card_blocks(rec, 1, "final")) == ["AP-0001|1|see"]
    run(L.handle_click(press_payload("AP-0001|1|takeback")))
    assert slack[-1] == {"ephemeral": "You already took this one back."}
    # His reply on it afterwards still reaches both the asker and the deputy.
    store.update("AP-0001", lambda r: core.take_replies(r, [("9.0", "we'll do it next month")]))
    assert to("AP-0001")[-1]["kind"] == "comment" and "we'll do it next month" in to("AP-0001", "deputy")[-1]["text"]


def test_take_it_back_after_done_and_its_message_isnt_held_behind_the_yes(monkeypatch, slack):
    auto_approved(monkeypatch)
    as_session(monkeypatch, "sid-a", agent="Pricing")
    cli.main(["done", "AP-0001", "Raised the cap"])
    assert press("AP-0001", "takeback") == "ok"
    rec = store.load("AP-0001")
    assert rec["status"] == "declined" and rec["done"] and "✅ *Done" in txt(cards.card_blocks(rec, 1, "final"))
    # Taken back before its yes went: the yes isn't held for pacing, and the take-back is typed right behind it.
    auto_approved(monkeypatch, "AP-0002")
    store.update("AP-0002", lambda r: r["outbox"][0].update(sent_at=None))
    card("AP-0003")
    press("AP-0003", "approve")
    store.update("AP-0003", lambda r: r["outbox"][0].update(sent_at=store.iso()))   # the asker is busy with another yes
    assert ("AP-0002", to("AP-0002")[0]["ref"]) in core.held(store.all_records())
    press("AP-0002", "takeback")
    assert ("AP-0002", to("AP-0002")[0]["ref"]) not in core.held(store.all_records())
    assert press("AP-0003", "takeback") == "gone"   # only an auto-approved card has it


def test_a_set_taken_back_shows_its_rows_not_approved(monkeypatch, slack):
    auto_approved(monkeypatch, kind="set")
    press("AP-0001", "takeback")
    rec = store.load("AP-0001")
    assert {a["verdict"] for a in rec["answers"].values()} == {"declined"} and rec["status"] == "declined"
    assert "✅ *Approved*" not in txt(cards.card_blocks(rec, 1, "final"))


# ---------- what Amir sees elsewhere ----------

def test_the_morning_list_crosses_an_auto_approved_card_off_as_auto_approved(monkeypatch, slack):
    posted, updated = [], []
    monkeypatch.setattr(digest.slackapi, "post", lambda blocks, text: posted.append(blocks) or "100.1")
    monkeypatch.setattr(digest.slackapi, "update", lambda ts, blocks, text: updated.append(blocks))
    monkeypatch.setattr(digest.slackapi, "message_url", lambda ts: f"https://x.slack.com/p{ts}")
    card()
    card("AP-0002")
    digest.tick()
    auto_approved(monkeypatch)
    digest.tick()
    assert "🤖 Auto-approved by Exec · approver, " in txt(updated[-1])


def test_plain_yes_is_a_button_approve_with_nothing_said_before_it(slack):
    card("AP-0001")
    press("AP-0001", "approve")
    card("AP-0002")
    store.update("AP-0002", lambda r: core.take_replies(r, [("5.0", "hmm")]))
    store.update("AP-0002", lambda r: r.update(status="pending"))
    press("AP-0002", "approve")
    card("AP-0003", "as_you")
    store.update("AP-0003", lambda r: core.decide(r, 1, "approved", by=APPROVER, where="pop-up", edited="Hi"))
    card("AP-0004", "batch")
    store.update("AP-0004", lambda r: core.decide(r, 1, "approved", by=APPROVER, where="pop-up", items=[1]))
    card("AP-0005", "batch")
    store.update("AP-0005", lambda r: core.decide(r, 1, "approved", by=APPROVER, where="pop-up", items=list(range(1, 12))))
    card("AP-0006", "choice")
    press("AP-0006", "pick:0")
    card("AP-0007", "set")
    press("AP-0007", "all")
    card("AP-0008", "set")
    press("AP-0008", "p1:approve")
    press("AP-0008", "p2:decline")
    card("AP-0009")
    store.update("AP-0009", lambda r: core.decide(r, 1, "approved", by=APPROVER, where="Slack thread", words="yes"))
    card("AP-0010")
    press("AP-0010", "approve")
    store.update("AP-0010", lambda r: core.take_replies(r, [("5.0", "thanks")]))   # after the yes: still plain
    got = {rid: core.plain_yes(store.load(rid), 1) for rid in (f"AP-{n:04d}" for n in range(1, 11))}
    assert got == {"AP-0001": True, "AP-0002": False, "AP-0003": False, "AP-0004": False, "AP-0005": True,
                   "AP-0006": False, "AP-0007": True, "AP-0008": False, "AP-0009": False, "AP-0010": True}


# ---------- the morning summary ----------

MON_8AM = datetime(2026, 10, 26, 8, 0, 30, tzinfo=timezone.utc)


def summary_text(blocks):
    return "\n".join(b["text"]["text"] if b["type"] == "section" else b["elements"][0]["text"]
                     for b in blocks if b["type"] in ("section", "context"))


@pytest.fixture
def morning(monkeypatch, slack):
    """The clock at Sunday 9 am, so the calls fall between Sunday's and Monday's morning lists."""
    monkeypatch.setattr(store, "now", lambda: MON_8AM - timedelta(hours=23))
    monkeypatch.setattr(slackapi, "message_url", lambda ts: f"https://x.slack.com/p{ts}")
    return slack


def test_the_summary_in_a_dry_run_says_what_he_did_with_each_card_it_would_have_approved(monkeypatch, morning):
    be_deputy()
    as_session(monkeypatch, "sid-dep")
    for n in range(1, 8):
        card(f"AP-{n:04d}", agent="Pricing" if n % 2 else "Ads")
    for n in range(1, 7):
        call(f"AP-{n:04d}", "--approve", "--why", f"Routine {n}.")
    call("AP-0007", "--his", "--why", "New.")
    press("AP-0001", "approve")
    press("AP-0002", "decline")
    store.update("AP-0003", lambda r: core.take_replies(r, [("5.0", "smaller")]))
    as_session(monkeypatch, "sid-a")
    cli.main(["withdraw", "AP-0004", "out of date"])
    press("AP-0007", "approve")
    blocks, text = digest.summary(store.deputy(), MON_8AM - timedelta(days=1), store.all_records())
    body = summary_text(blocks)
    assert text == "🤖 Dry run: Exec · approver would have approved 6 of the 7 cards since yesterday morning"
    assert ("Of those 6, you approved 1 without a word, replied on or declined 2, and 3 are still open or were withdrawn" in body)
    assert body.index("*Ads*") < body.index("*Pricing*")
    assert "• <https://x.slack.com/p1.1|AP-0001> Add $250 to the search ads learning budget — Routine 1. · ✅ you approved it" in body
    for label in ("❌ you declined it", "💬 you replied", "↩️ the agent withdrew it", "⏳ still open"):
        assert label in body
    assert "AP-0007" not in body
    assert blocks[-1]["elements"][0]["value"] == "deputy|0|live" and blocks[-1]["elements"][0]["text"]["text"] == "Turn auto-approval on"
    assert "<@" not in json.dumps(blocks)   # no mention


def test_the_summary_with_auto_approval_on_lists_what_it_approved_and_what_he_took_back(monkeypatch, morning):
    be_deputy(live=True)
    as_session(monkeypatch, "sid-dep")
    for n in range(1, 6):
        card(f"AP-{n:04d}", title="A" * 120 if n == 1 else f"Card {n}")
    call("AP-0001", "--approve", "--why", "Routine.")
    call("AP-0002", "--approve", "--why", "Routine.")
    call("AP-0003", "--his", "--why", "New.")
    call("AP-0004", "--his", "--why", "New.")
    press("AP-0002", "takeback")
    press("AP-0003", "approve")
    press("AP-0004", "decline")
    blocks, text = digest.summary(store.deputy(), MON_8AM - timedelta(days=1), store.all_records())
    body = summary_text(blocks)
    assert text == "🤖 2 cards were approved for you since yesterday morning"
    assert "By Exec · approver · you took back 1 · it left 2 for you, and you approved 1 of those without a word" in body
    assert f"<https://x.slack.com/p1.1|AP-0001> {'A' * 89}… — Routine." in body
    assert "• ~<https://x.slack.com/p2.1|AP-0002> Card 2 — Routine.~ ↩️ taken back" in body
    assert blocks[-1]["elements"][0]["value"] == "deputy|0|pause"


def test_the_summary_stays_within_slacks_limits(monkeypatch, morning):
    be_deputy(live=True)
    recs = []
    for n in range(1, 401):
        r = make_rec(SAMPLES["budget"], id=f"AP-{n:04d}", thread_ts=f"{n}.1", agent=f"Agent {n % 7}", status="approved")
        r["calls"] = [{"version": 1, "call": "approve", "why": "w" * 290, "by": "Exec · approver", "at": store.iso(), "live": True}]
        recs.append(r)
    blocks, _ = digest.summary(store.deputy(), MON_8AM - timedelta(days=1), recs)
    sections = [b for b in blocks if b["type"] == "section"]
    assert len(blocks) == 49 and all(len(b["text"]["text"]) <= 3000 for b in sections)
    assert len(digest.switched(blocks, False, store.now())) == 50   # room for the switch's line
    more = blocks[-2]["elements"][0]["text"]
    shown = summary_text(blocks).count("• ")
    assert more == f"…and {400 - shown} more" and shown > 0


def test_the_summary_follows_the_morning_list_only_when_the_deputy_made_a_call(monkeypatch, morning):
    posted = []
    monkeypatch.setattr(digest.slackapi, "post", lambda blocks, text: posted.append(text) or f"100.{len(posted)}")
    be_deputy()
    card()
    digest.tick(MON_8AM - timedelta(hours=23))   # Sunday's list; no call yet, so no summary
    assert len(posted) == 1
    as_session(monkeypatch, "sid-dep")
    call("AP-0001", "--his", "--why", "New.")
    digest.tick(MON_8AM)
    assert len(posted) == 3 and posted[2].startswith("🤖 Dry run: Exec · approver would have approved 0 of the 1 cards")
    digest.tick(MON_8AM + timedelta(minutes=5), asked=True)   # a list asked for brings no summary
    assert len(posted) == 4


def test_the_summarys_button_switches_auto_approval_and_redraws_it(monkeypatch, morning):
    be_deputy()
    as_session(monkeypatch, "sid-dep")
    card()
    call("AP-0001", "--approve", "--why", "Routine.")
    blocks, text = digest.summary(store.deputy(), MON_8AM - timedelta(days=1), store.all_records())
    L = listener.Listener()

    def click(value, user=APPROVER):
        p = press_payload(value, user=user)
        p.update(container={"message_ts": "100.2"}, message={"ts": "100.2", "blocks": blocks, "text": text})
        run(L.handle_click(p))
    click("deputy|0|live", user="U_SOMEONE")
    assert not store.deputy()["live"]
    click("deputy|0|live")
    assert store.deputy()["live"] and store.deputy()["live_since"]
    redrawn = [p for p in morning if p.get("update") == "100.2"][-1]["blocks"]
    assert redrawn[-2]["block_id"] == "deputy-mode" and "Auto-approval turned on by Sam, " in redrawn[-2]["elements"][0]["text"]
    assert redrawn[-1]["elements"][0]["value"] == "deputy|0|pause" and len(redrawn) == len(blocks) + 1
    blocks = redrawn
    click("deputy|0|pause")
    redrawn = [p for p in morning if p.get("update") == "100.2"][-1]["blocks"]
    assert not store.deputy()["live"] and "paused by Sam" in redrawn[-2]["elements"][0]["text"] and len(redrawn) == len(blocks)


# ---------- reading the record ----------

def test_history_prints_what_happened_on_each_card_in_order(monkeypatch, slack, capsys):
    be_deputy()
    card("AP-0001")
    card("AP-0002", sample="creative", agent="Ads")
    as_session(monkeypatch, "sid-dep")
    call("AP-0001", "--approve", "--why", "Inside the cap.")
    store.update("AP-0001", lambda r: core.take_replies(r, [("5.0", "make it $200")]))
    store.update("AP-0001", lambda r: r.update(status="pending"))
    press("AP-0001", "approve")
    capsys.readouterr()
    cli.main(["history", "--calls"])
    out = capsys.readouterr().out
    assert out.startswith("AP-0001  ") and "budget  Tester: Add $250" in out and "AP-0002" not in out
    lines = out.splitlines()
    assert ('Exec · approver on v1: approve (dry run). Why: "Inside the cap."' in lines[1]
            and 'Sam replied: "make it $200"' in lines[2] and "Sam approved v1 (button in Slack), 0m after posting" in lines[3])
    cli.main(["history", "--agent", "ads", "--type", "creative"])
    out = capsys.readouterr().out
    assert out.startswith("AP-0002  ") and "AP-0001" not in out


def test_pictures_saves_the_newest_versions_pictures_and_names_its_video(monkeypatch, tmp_path, capsys):
    got = []
    monkeypatch.setattr(slackapi, "download", lambda fid, folder, name: got.append((fid, name)) or f"{folder}/{name}.png")
    rec = rec_for(KINDS["before_after"])
    rec["versions"][0]["files"] = {"gallery:0": {"id": "F1", "url": "u"}, "gallery:0:card": {"id": "F2", "url": "u"}, "gallery:1": "F3"}
    rec["versions"][0]["video_url"] = "https://example.com/v"
    store.save(rec)
    cli.main(["pictures", "AP-0001", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert got == [("F1", "gallery-0"), ("F3", "gallery-1")] and f"{tmp_path}/gallery-0.png" in out and "video: https://example.com/v" in out


# ---------- delivery ----------

def test_each_role_is_delivered_its_own_messages_and_takes_only_its_own_from_the_inbox(monkeypatch, slack, capsys):
    be_deputy()
    store.set_reviewer({"types": ["budget"], "reach": {"agent": "CFO", "runtime": "claude", "session_id": "sid-cfo", "machine": "testbox"}})
    card()
    press("AP-0001", "decline")
    asker, review, dep = (i["ref"] for i in store.load("AP-0001")["outbox"])
    due = core.to_deliver(store.all_records(), config.machine(), store.reviewer(), store.deputy())[0]
    assert sorted(due) == sorted([["AP-0001", asker], ["AP-0001", review], ["AP-0001", dep]])
    typed = []
    monkeypatch.setattr(router, "deliver", lambda r, text, ref, **kw: typed.append(r["session_id"])
                        or router.Result("delivered", reach={"session_id": "sid-dep-2"}, moves=[["sid-dep", "sid-dep-2"]]))
    L = listener.Listener.__new__(listener.Listener)
    L.in_flight, L.deliveries = set(), queue.Queue()
    L.deliver_one("AP-0001", dep)
    rec = store.load("AP-0001")
    assert typed == ["sid-dep"] and rec["reach"]["session_id"] == "sid-a" and rec["moves"] == []
    store.update("AP-0001", lambda r: r["outbox"][2].update(delivered_at=None))
    for sid, has, hasnt in (("sid-dep", "Sam declined it", "which you review"), ("sid-cfo", "which you review", "First acknowledge"),
                            ("sid-a", "First acknowledge", "which you review")):
        as_session(monkeypatch, sid, agent={"sid-a": "Tester"}.get(sid, "x"))
        cli.cmd_inbox(SimpleNamespace(agent=None, all=False))
        out = capsys.readouterr().out
        assert has in out and hasnt not in out and out.count("[Approvals]") == 1
