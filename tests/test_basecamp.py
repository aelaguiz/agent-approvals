"""A card names the Basecamp items it serves: "Part of:" links to the first, and each Basecamp card keeps one step,
written as the asker's own login, saying where the approval stands."""
import json
from datetime import datetime, timedelta

import pytest

from approvals import basecamp, cards, cli, core, listener, router, slackapi, store
from samples import SAMPLES

CARD = "https://app.basecamp.com/1000001/buckets/111/card_tables/cards/222"
CARD2 = "https://3.basecamp.com/1000001/buckets/333/card_tables/cards/444"
TODO = "https://app.basecamp.com/1000001/buckets/555/todos/666"


@pytest.fixture
def bc(monkeypatch, tmp_path):
    """A fake basecamp: each call is recorded, and a step's title and checkbox are kept as Basecamp would."""
    calls, steps, fail = [], {}, []

    def run(prof, account, *args):
        calls.append((prof, account, *args))
        if fail:
            raise RuntimeError(fail[0])
        verb = args[0]
        if verb == "create":
            sid = str(900 + len(steps))
            steps[sid] = {"title": args[1], "card": args[3], "project": args[5], "done": False}
            return {"id": int(sid)}
        if verb == "update":
            steps[args[1]]["title"] = args[2]
        else:
            steps[args[1]]["done"] = verb == "complete"
        return {"id": int(args[1])}
    monkeypatch.setattr(basecamp, "run", run)
    folder = tmp_path / "pods" / "cfo"
    (folder / ".basecamp").mkdir(parents=True)
    (folder / ".basecamp" / "config.json").write_text(json.dumps({"default_profile": "cfo"}))
    (folder / "work").mkdir()
    monkeypatch.chdir(folder / "work")   # the agent's shell is in a folder below its own
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-asker")
    posts = []
    monkeypatch.setattr(slackapi, "post", lambda blocks, text, thread_ts=None, broadcast=False:
                        posts.append(blocks) or f"{100 + len(posts)}.0")
    monkeypatch.setattr(slackapi, "permalink", lambda ts: f"https://slack.example/p{ts}")
    monkeypatch.setattr(slackapi, "update", lambda *a, **k: None)
    monkeypatch.setattr(slackapi, "react", lambda *a, **k: None)
    monkeypatch.setattr(slackapi, "unreact", lambda *a, **k: None)
    monkeypatch.setattr(slackapi, "note", lambda *a, **k: None)
    return {"calls": calls, "steps": steps, "fail": fail, "posts": posts, "folder": folder}


def submit(tmp_path, *flags, **card):
    p = tmp_path / "card.json"
    p.write_text(json.dumps(dict(SAMPLES["money"], part_of="Pay the bills on time", **card)))
    cli.main(["submit", str(p), "--agent", "CFO", *flags])
    return store.all_records()[-1]["id"]


def service_pass():
    """What the service on the asker's machine does every few seconds: write the steps that are due."""
    L = listener.Listener()
    L.deliver_due()
    while not L.steps.empty():
        basecamp.sync(L.steps.get())
    return L


def step(bc, i=0):
    return list(bc["steps"].values())[i]


def at(rec):
    return cards.clock(datetime.fromisoformat(rec["decision"]["at"]))


def test_a_posted_card_links_its_basecamp_card_and_says_it_waits_on_the_approver(bc, tmp_path):
    rid = submit(tmp_path, "--basecamp", CARD, title="Move $20,000 from savings to operating")
    rec = store.load(rid)
    assert rec["basecamp"][0]["url"] == CARD and rec["link"] == "https://slack.example/p101.0"
    assert f"Part of: <{CARD}|Pay the bills on time>" in json.dumps(bc["posts"][0])
    assert bc["calls"][0][:2] == ("cfo", "1000001")   # the asker's own login, from its folder
    assert step(bc) == {"title": f"{rid} · waiting on Sam: Move $20,000 from savings to operating · https://slack.example/p101.0",
                        "card": "222", "project": "111", "done": False}


def test_two_cards_each_get_the_step_and_a_todo_only_gets_the_link(bc, tmp_path):
    rid = submit(tmp_path, "--basecamp", TODO, "--basecamp", CARD2, basecamp=[CARD, TODO])
    assert [e["url"] for e in store.load(rid)["basecamp"]] == [CARD, TODO, CARD2]
    assert f"Part of: <{CARD}|" in json.dumps(bc["posts"][0])
    assert sorted(s["card"] for s in bc["steps"].values()) == ["222", "444"]


def test_a_link_that_isnt_basecamps_is_refused_and_nothing_is_posted(bc, tmp_path, capsys):
    with pytest.raises(SystemExit):
        submit(tmp_path, "--basecamp", "https://basecamp.example.com/1/buckets/2/card_tables/cards/3")
    assert "isn't a Basecamp link" in capsys.readouterr().err and not bc["posts"] and not store.all_records()


def test_a_long_headline_is_cut_at_a_word():
    cut = basecamp.headline("Move twenty thousand dollars from the savings account to the operating account before payroll runs on Friday")
    assert len(cut) <= 81 and cut.endswith("…") and cut.startswith("Move twenty thousand dollars")


@pytest.mark.parametrize("answer,words", [
    (lambda r: core.decide(r, 1, "approved", "UAPPROVER"), "approved"),
    (lambda r: core.decide(r, 1, "declined", "UAPPROVER"), "declined"),
    (lambda r: core.take_replies(r, [("200.0", "Why now and not next week?")]), "asked a question"),
])
def test_his_answer_shows_until_the_asker_has_it(bc, tmp_path, answer, words, monkeypatch):
    rid = submit(tmp_path, "--basecamp", CARD)
    store.update(rid, answer)
    service_pass()
    rec = store.load(rid)
    when = at(rec) if rec.get("decision") else cards.clock(datetime.fromisoformat(rec["events"][-1]["at"]))
    assert step(bc)["title"] == f"{rid} · {words} {when} · not yet seen by CFO · https://slack.example/p101.0"
    assert not step(bc)["done"]
    # The service sees the answer land in the session: the asker has it.
    monkeypatch.setattr(router, "deliver", lambda *a, **k: router.Result("delivered", "seen in the transcript"))
    L = listener.Listener()
    L.deliver_one(rid, store.load(rid)["outbox"][-1]["ref"])
    service_pass()
    assert step(bc)["title"] == f"{rid} · {words} {when} · https://slack.example/p101.0"


def test_the_askers_acknowledgement_clears_not_yet_seen_and_done_checks_the_step(bc, tmp_path):
    rid = submit(tmp_path, "--basecamp", CARD)
    store.update(rid, lambda r: core.decide(r, 1, "approved", "UAPPROVER"))
    store.update(rid, lambda r: r["outbox"][-1].__setitem__("sent_at", store.iso()))   # typed in
    cli.main(["ack", rid, "Got it: moving the money now."])
    assert step(bc)["title"] == f"{rid} · approved {at(store.load(rid))} · https://slack.example/p101.0"
    cli.main(["done", rid, "Moved; the operating balance shows it."])
    assert step(bc)["done"] and "approved" in step(bc)["title"]


def test_withdrawing_or_expiring_checks_the_step(bc, tmp_path):
    a = submit(tmp_path, "--basecamp", CARD)
    cli.main(["withdraw", a, "The bill was paid another way."])
    assert step(bc) ["title"] == f"{a} · withdrawn · https://slack.example/p101.0" and step(bc)["done"]
    b = submit(tmp_path, "--basecamp", CARD2)
    store.update(b, lambda r: r.__setitem__("deadline", store.iso(store.now() - timedelta(minutes=1))))
    listener.Listener().sweep()
    service_pass()
    s = step(bc, 1)
    assert s["title"].startswith(f"{b} · expired ") and "not yet seen by CFO" in s["title"] and s["done"]


def test_a_folder_with_no_basecamp_login_is_skipped(bc, tmp_path, monkeypatch):
    plain = tmp_path / "nologin"
    plain.mkdir()
    monkeypatch.chdir(plain)
    rid = submit(tmp_path, "--basecamp", CARD)
    assert not bc["calls"] and "no Basecamp login" in store.load(rid)["basecamp"][0]["error"]
    store.update(rid, lambda r: core.decide(r, 1, "approved", "UAPPROVER"))
    service_pass()
    assert not bc["calls"]


def test_basecamp_failing_never_stops_the_card_or_the_answer_and_is_tried_again(bc, tmp_path, monkeypatch):
    bc["fail"].append("Not authenticated for profile:cfo")
    rid = submit(tmp_path, "--basecamp", CARD)   # posted all the same
    assert store.load(rid)["thread_ts"] and store.load(rid)["basecamp"][0]["tries"] == 1
    store.update(rid, lambda r: core.decide(r, 1, "approved", "UAPPROVER"))
    sent = []
    monkeypatch.setattr(router, "deliver", lambda reach, text, ref, **k: sent.append(ref) or router.Result("delivered", "seen"))
    L = listener.Listener()
    L.deliver_due()
    L.deliver_one(*L.deliveries.get_nowait())
    basecamp.sync(L.steps.get_nowait())
    assert sent and store.load(rid)["outbox"][-1]["delivered_at"]      # the answer reached the agent
    e = store.load(rid)["basecamp"][0]
    assert e["error"] == "Not authenticated for profile:cfo" and e["tries"] == 1 and not e.get("step")
    assert any(ev["kind"] == "basecamp_failed" for ev in store.load(rid)["events"])
    assert basecamp.due([store.load(rid)], "testbox") == []          # not again until its next try
    store.update(rid, lambda r: r["basecamp"][0].__setitem__("next_try", store.iso()))
    bc["fail"].clear()
    service_pass()
    assert step(bc)["title"] == f"{rid} · approved {at(store.load(rid))} · https://slack.example/p101.0"
    assert store.load(rid)["basecamp"][0]["error"] is None


def test_retries_stop_after_a_few(bc, tmp_path):
    bc["fail"].append("Basecamp is down")
    rid = submit(tmp_path, "--basecamp", CARD)
    for _ in range(basecamp.TRIES + 2):
        store.update(rid, lambda r: r["basecamp"][0].__setitem__("next_try", store.iso()))
        service_pass()
    assert store.load(rid)["basecamp"][0]["tries"] == basecamp.TRIES and len(bc["calls"]) == basecamp.TRIES


def test_the_command_reads_the_machines_credentials_file_when_it_keeps_one(monkeypatch, tmp_path):
    seen = []

    class Done:
        returncode, stdout, stderr = 0, '{"id": 7}', ""
    monkeypatch.setattr(basecamp.subprocess, "run", lambda cmd, **kw: seen.append((cmd, kw["env"].get("BASECAMP_NO_KEYRING"))) or Done)
    monkeypatch.setattr(basecamp, "CREDENTIALS", tmp_path / "credentials.json")
    basecamp.run("cfo", "1000001", "complete", "7", "--in", "111")
    (tmp_path / "credentials.json").write_text("{}")
    basecamp.run("cfo", "1000001", "complete", "7", "--in", "111")
    assert seen[0][0][:5] == ["basecamp", "--profile", "cfo", "--account", "1000001"] and seen[0][0][-1] == "--agent"
    assert [s[1] for s in seen] == [None, "1"]
