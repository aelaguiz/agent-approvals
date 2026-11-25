"""Related approval cards and their shared journey page."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from approvals import cards, cli, config, core, groups, hub, journey, listener, reader, slackapi, store
from test_cards_store import make_rec
from test_journey import page_data
from test_kinds import slack

NOW = datetime(2026, 1, 15, 16, tzinfo=timezone.utc)
STEPS = [{"who": "A returning reader", "today": "Today", "after": "With the change", "steps": [
    {"today": {"label": "Library", "source": "Test fixture", "image": 0},
     "after": {"label": "Library with search", "source": "Test fixture", "image": 1, "new": True}},
]}]


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(store, "now", lambda: NOW)


def card(n, name="Library search", *, days=0, settled=None, journey=True, **kw):
    rid = f"AP-{n:04d}"
    rec = make_rec(
        {"title": f"Library change {n}", "part_of": name, "type": "experiment"},
        id=rid, created=store.iso(NOW - timedelta(days=days)), deadline=store.iso(NOW + timedelta(days=1)),
        link=f"https://example.com/cards/{rid}", thread_ts=f"{n}.0", **kw,
    )
    if journey:
        rec["versions"][0].update(
            journey_url=f"https://example.com/journeys/{rid}/index.html",
            journey_data={"entries": STEPS, "images": ["today.jpg", "after.jpg"]},
        )
    if settled is not None:
        rec.update(status="approved", decision={"verdict": "approved", "version": 1, "at": store.iso(settled)})
    return rec


def ids(page):
    return [c["id"] for c in page["cards"]]


def test_group_shows_only_eligible_cards_and_keeps_channels_and_tests_apart():
    config.CONFIG.write_text(json.dumps(dict(config.settings(), approvers={
        "alternate": dict(config.target(), channel="alternate-channel"),
        "same-channel": config.target(),
    })))
    pending = card(1, "  library\n  SEARCH ", days=12)
    answered = card(2, "Other work", settled=NOW - timedelta(days=1), frame="Library search")
    condition = card(3, days=8, settled=NOW - timedelta(days=8))
    core.add_status(condition, "Confirm the search label", kind="on_you")
    without_journey = card(4, journey=False)
    legacy = card(5)
    del legacy["versions"][0]["journey_data"]
    waiting_on_someone_else = card(6)
    core.add_status(waiting_on_someone_else, "Review by the editor", kind="waiting")
    unposted = card(7)
    unposted["thread_ts"] = None
    recs = [
        pending, answered, condition, without_journey, legacy, waiting_on_someone_else, unposted,
        card(8, status="withdrawn"), card(9, frame=None), card(10, status="revising"),
        card(11, "Library searching"), card(12, test=True), card(13, target="alternate"),
    ]
    pages = groups.build_pages(recs, NOW)
    key = (config.target()["channel"], False, "library search")
    assert list(pages) == [key]
    page = pages[key]
    assert ids(page) == [condition["id"], pending["id"], answered["id"]]
    assert [c["waiting"] for c in page["cards"]] == [True, True, False]
    assert page["without_journey"] == [{"link": without_journey["link"], "title": without_journey["title"]}]

    pages = groups.build_pages(recs + [card(14, test=True), card(15, target="alternate"), card(16, target="same-channel")], NOW)
    assert ids(pages[key])[0] == "AP-0016"
    assert ids(pages[key[:1] + (True, key[2])]) == ["AP-0014", "AP-0012"]
    assert ids(pages["alternate-channel", False, key[2]]) == ["AP-0015", "AP-0013"]


def test_seven_days_and_twenty_answered_cards_newest_first():
    cutoff = NOW - timedelta(days=7)
    on_boundary = card(23, settled=cutoff)
    too_old = card(24, settled=cutoff - timedelta(seconds=1))
    page, = groups.build_pages([too_old, on_boundary, card(25)], NOW).values()
    assert ids(page) == ["AP-0025", "AP-0023"] and page["omitted"] == 0

    settled = [card(n, settled=NOW - timedelta(hours=n)) for n in range(1, 23)]
    page, = groups.build_pages([too_old, on_boundary, *reversed(settled), card(25)], NOW).values()
    assert ids(page) == ["AP-0025", *(r["id"] for r in settled[:20])]
    assert page["omitted"] == 3


def test_group_page_reuses_decisions_statuses_and_each_versions_images(tmp_path):
    pending = card(1)
    answered = card(2, settled=NOW - timedelta(hours=1))
    answered["versions"].append(dict(answered["versions"][0], n=2, journey_url="https://example.com/latest/"))
    core.add_status(answered, "First line")
    core.add_status(answered, "Confirm <label> & spacing", kind="next")
    page, = groups.build_pages([pending, answered], NOW).values()
    folder = tmp_path / "page"
    folder.mkdir()
    journey.write_html(folder, page)
    assert list(folder.iterdir()) == [folder / "index.html"]
    assert page_data(folder) == page
    first, second = page["cards"]
    assert first["image_base"] == "https://example.com/journeys/AP-0001/"
    assert second["image_base"] == "https://example.com/latest/"
    assert first["entries"] == second["entries"] == STEPS
    assert first["images"] == second["images"] == ["today.jpg", "after.jpg"]
    assert second["version"] == 2 and second["link"] == answered["link"]
    assert second["state"] == cards.decision_line(answered, answered["versions"][-1]["card"])
    assert second["status"] == {"text": "⏳ Confirm <label> & spacing", "at": cards.when(NOW)}


@pytest.mark.parametrize(("name", "short"), [
    ("  Library search : screens (two paths)", "Library search"),
    ("  Library search (two paths): screens", "Library search"),
    ("A" * 40, "A" * 40),
    ("B" * 41, "B" * 39 + "…"),
    ("(two paths) Library search", "(two paths) Library search"),
])
def test_short_name(name, short):
    assert cards.short_name(name) == short


def test_open_and_folded_cards_link_to_the_page():
    rec = card(1, settled=NOW)
    req = rec["versions"][0]["card"]
    req.update(why="Help readers find a title.", approves="Change search.", does_not_approve="Change checkout.",
               facts=[["Place", "Library"]], links=[["More detail", "https://example.com/details"]])
    rec["group"] = {"url": "https://example.com/group/", "count": 5, "name": "Library search: screens"}
    open_links = next(b["elements"] for b in cards.card_blocks(rec, 1) if b.get("block_id") == "links")
    folded_links = next(b["elements"] for b in cards.card_blocks(rec, 1, mode="final") if b.get("block_id", "").endswith("|see"))
    for links, before in ((open_links, "More detail"), (folded_links, "See the card")):
        assert [b["text"]["text"] for b in links] == [before, "All 5 in Library search"]
        assert links[-1]["url"] == rec["group"]["url"] and "value" not in links[-1]


@pytest.fixture
def published(monkeypatch):
    calls = []

    def share(folder, title, description, slug):
        page = page_data(folder)
        assert title == page["name"] and str(len(page["cards"])) in description
        calls.append((page, slug))
        return f"https://example.com/group/{len(calls)}/"

    monkeypatch.setattr(slackapi, "share_page", share)
    return calls


def test_hub_sweep_changes_buttons_when_pages_change_and_regroups_after_a_move(monkeypatch, slack, published):
    one, two, three, four = recs = [card(1), card(2), card(3, "Reading history"), card(4, "Reading history")]
    for rec in recs:
        rec["versions"][0]["card"].update(approves="The screen change", does_not_approve="Any other change", facts=[])
        store.save(rec)

    service = listener.Listener()
    service.sweep()
    assert len(published) == 2
    assert len(slack) == 4
    first = store.load(one["id"])["group"]
    other = store.load(four["id"])["group"]
    assert first["count"] == other["count"] == 2
    assert first == store.load(two["id"])["group"] and other == store.load(three["id"])["group"]
    assert not store.load(one["id"])["card_dirty"]
    state_path = config.HOME / "groups.json"
    state_mtime = state_path.stat().st_mtime_ns

    with monkeypatch.context() as patch:
        patch.setattr(store, "update", lambda *args: pytest.fail("unchanged sweep wrote a record"))
        service.sweep()
    assert (len(published), len(slack), state_path.stat().st_mtime_ns) == (2, 4, state_mtime)

    store.update(one["id"], lambda r: core.add_status(r, "The search box is ready"))
    service.sweep()
    assert len(published) == 3 and published[2][1] == published[0][1]
    assert store.load(one["id"])["group"]["url"] != first["url"]
    service.sweep()
    assert len(published) == 3

    cli.main(["status", one["id"], "--part-of", "Reading history"])
    service.sweep()
    assert len(published) == 4 and published[3][1] == published[1][1]
    assert "group" not in store.load(two["id"])
    for rec in (one, three, four):
        assert store.load(rec["id"])["group"] == {
            "name": "Reading history", "url": "https://example.com/group/4/", "count": 3,
        }
    latest = {p["update"]: p["blocks"] for p in slack}
    assert any(b.get("block_id") == "links" for b in latest[one["thread_ts"]])
    assert all(b.get("block_id") != "links" for b in latest[two["thread_ts"]])


def test_failed_publish_waits_for_next_sweep_and_keeps_the_slug(monkeypatch, caplog, published):
    for rec in (card(1), card(2), card(3, "Reading history"), card(4, "Reading history")):
        store.save(rec)
    share = slackapi.share_page
    failed = []

    def fail_first(folder, title, description, slug):
        if title == "Library search" and not failed:
            failed.append(slug)
            raise RuntimeError("publisher unavailable")
        return share(folder, title, description, slug)

    monkeypatch.setattr(slackapi, "share_page", fail_first)
    assert groups.refresh(store.all_records()) == ["AP-0003", "AP-0004"]
    assert "publisher unavailable" in caplog.text
    assert groups.refresh(store.all_records()) == ["AP-0001", "AP-0002"]
    assert published[-1][1] == failed[0]


def test_status_change_republishes_at_the_same_address_without_changing_buttons(monkeypatch, published):
    for rec in (card(1), card(2)):
        store.save(rec)
    share = slackapi.share_page

    def fixed_address(folder, title, description, slug):
        share(folder, title, description, slug)
        return f"https://example.com/group/{slug}/"

    monkeypatch.setattr(slackapi, "share_page", fixed_address)
    assert groups.refresh(store.all_records()) == ["AP-0001", "AP-0002"]
    store.update("AP-0001", lambda r: core.add_status(r, "Search is ready"))
    assert groups.refresh(store.all_records()) == []
    assert len(published) == 2 and published[0][1] == published[1][1]


def test_age_and_withdrawal_clear_buttons_and_keep_the_group_address(monkeypatch, published):
    one, two, three = card(1), card(2, settled=NOW - timedelta(days=7)), card(3)
    for rec in (one, two, three):
        store.save(rec)
    assert groups.refresh(store.all_records()) == ["AP-0001", "AP-0002", "AP-0003"]
    slug = published[0][1]
    monkeypatch.setattr(store, "now", lambda: NOW + timedelta(seconds=1))
    assert groups.refresh(store.all_records()) == ["AP-0001", "AP-0002", "AP-0003"]
    assert "group" not in store.load(two["id"])
    assert store.load(one["id"])["group"]["count"] == 2 and published[-1][1] == slug

    store.update(three["id"], lambda r: r.update(status="withdrawn"))
    assert groups.refresh(store.all_records()) == ["AP-0001", "AP-0003"]
    assert all("group" not in r for r in store.all_records())

    store.save(card(4))
    assert groups.refresh(store.all_records()) == ["AP-0001", "AP-0004"]
    assert published[-1][1] == slug


def test_only_the_hub_runs_the_group_pass(monkeypatch):
    service = listener.Listener()
    monkeypatch.setattr(config, "is_hub", lambda: False)
    monkeypatch.setattr(store, "all_records", lambda: [])
    monkeypatch.setattr(groups, "refresh", lambda records: pytest.fail("a delivery machine ran the group pass"))
    service.sweep()


@pytest.mark.parametrize("flag", [None, "--slug"])
def test_share_page_passes_the_configured_slug_flag(monkeypatch, tmp_path, flag):
    monkeypatch.setattr(config, "settings", lambda: {"page_share_command": ["publish"], "page_slug_flag": flag})
    args = []
    monkeypatch.setattr(slackapi.subprocess, "run", lambda cmd, **kw:
                        args.extend(cmd) or SimpleNamespace(stdout="URL: https://example.com/page", returncode=0))
    slackapi.share_page(tmp_path, "Library search", "Two cards", slug="random-slug")
    assert args == ["publish", "--title", "Library search", "--description", "Two cards",
                    *([flag, "random-slug"] if flag else []), str(tmp_path)]


def test_names_are_summarized_on_the_hub_and_use_days(monkeypatch, capsys):
    for rec in (card(1, "Library search", agent="Design", days=2), card(2, "  LIBRARY   search", agent="QA"),
                card(3, "Reading history", days=4), card(4, "Draft"), card(5, "Test name", test=True)):
        if rec["id"] == "AP-0004":
            rec["thread_ts"] = None
        store.save(rec)

    handler = object.__new__(hub.Handler)
    handler.server = SimpleNamespace(token=config.hub_token())
    handler.headers = {"Authorization": f"Bearer {handler.server.token}"}
    handler._send = lambda code, obj: (code, obj)
    client = object.__new__(hub.Client)

    def call(method, path):
        assert path.startswith("/names?")
        handler.path = path
        return handler._handle(method)

    client.call = call
    monkeypatch.setattr(store, "hub_client", lambda: client)
    cli.main(["names"])
    assert capsys.readouterr().out.splitlines() == ["LIBRARY search · 2 cards · QA, Design · Library change 2"]
    cli.main(["names", "--days", "5"])
    assert capsys.readouterr().out.splitlines() == [
        "LIBRARY search · 2 cards · QA, Design · Library change 2", "Reading history · 1 card · Tester · Library change 3",
    ]


def test_read_names_only_when_no_other_card_matches(monkeypatch, tmp_path, capsys):
    for n in range(1, 32):
        store.save(card(n, f"Library project {n}", journey=False))
    monkeypatch.setattr(reader, "run", lambda *args: SimpleNamespace(returncode=0, stdout="Read complete\n"))
    path = tmp_path / "card.json"
    path.write_text(json.dumps({"title": "New layout", "part_of": None}))
    cli.main(["read", str(path)])
    result = capsys.readouterr()
    lines = result.err.splitlines()
    assert result.out == "Read complete\n"
    assert "Part of line, word for word" in lines[0] and len(lines) == 31
    assert lines[1] == "Library project 31 · 1 card · Tester · Library change 31"

    path.write_text(json.dumps({"title": "New layout", "part_of": "library   PROJECT 1"}))
    cli.main(["read", str(path)])
    assert capsys.readouterr().err == ""

    # A hub that can't list the names leaves the read as it was.
    def no_names(*args, **kw):
        raise store.StoreError("the hub runs a version without names")
    monkeypatch.setattr(store, "names", no_names)
    path.write_text(json.dumps({"title": "New layout", "part_of": None}))
    cli.main(["read", str(path)])
    result = capsys.readouterr()
    assert result.out == "Read complete\n" and result.err == ""


def test_submit_names_excludes_the_new_card_and_prints_the_move_command(monkeypatch, tmp_path, capsys, slack):
    store.create(card(1, journey=False))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "fixture")
    req = {"type": "experiment", "title": "Show a search hint", "part_of": "Search help", "about": "X-12",
           "stands": {"whole": "Search hint", "his_latest": "No earlier words or open discussion; checked today with Product.", "state": "new-to-him"},
           "why": "Readers can find the search box.", "facts": [["Place", "Library"]],
           "approves": "The search hint", "does_not_approve": "A new search engine"}
    path = tmp_path / "card.json"
    path.write_text(json.dumps(req))
    cli.main(["submit", str(path)])
    result = capsys.readouterr()
    assert result.out.splitlines()[0] == "AP-0002"
    assert "Library search · 1 card · Tester · Library change 1" in result.err
    assert 'approvals status AP-0002 --part-of "<name>"' in result.err
    assert "Search help · " not in result.err

    path.write_text(json.dumps(dict(req, part_of="library   search")))
    cli.main(["submit", str(path)])
    assert capsys.readouterr().err == ""
