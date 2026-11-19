"""Journeys on the card, in its thread and on the page it opens."""
import json
import re
import sys
from pathlib import Path

import pytest
from PIL import Image

from approvals import cards, cli, config, core, journey, reader, slackapi, store
from samples import SAMPLES
from test_kinds import rec_for, slack


@pytest.fixture
def screens(tmp_path):
    out = []
    for i, color in enumerate(("blue", "green", "red", "orange")):
        path = tmp_path / f"screen-{i}.png"
        Image.new("RGB", (1500, 1000), color).save(path)
        out.append({"image": str(path), "label": f"Screen {i + 1}", "source": "Mock · test fixture"})
    return out


@pytest.fixture
def entry(screens):
    return {"who": "A reader opening the library", "after": "With the search box", "steps": [
        {"today": screens[0], "after": "same", "then": {"today": "Opens the menu", "after": "Taps the library"}},
        {"today": {"ends": "The menu has no search."}, "after": dict(screens[1], new=True), "then": "Searches for a book"},
        {"after": screens[2]},
    ]}


def page_data(folder):
    html = (Path(folder) / "index.html").read_text()
    return json.loads(re.search(r'<script type="application/json" id="journey-data">(.*?)</script>', html)[1])


def big_button(blocks):
    return [e for b in blocks if b.get("block_id") == "journey" for e in b["elements"]]


@pytest.mark.parametrize("shape", ["steps", "places"])
def test_journey_validates_and_draws_in_the_open_card_and_its_pop_up(shape, entry, screens):
    if shape == "places":
        entry = {"who": entry["who"], "places": [{"name": "Library", "today": screens[0], "after": screens[1]}]}
    card = dict(SAMPLES["creative"], journey=entry)
    card.pop("image", None)
    assert cards.validate(card) == []
    assert card["journey"] == [entry]
    # A gallery can sit below the map on the same card.
    card["gallery"] = [screens[3]]
    rec = rec_for(card)
    rec["versions"][0]["files"].update({"journey:0:map": "FMap", "journey:0:0:today": "FScreen"})
    rec["versions"][0]["journey_url"] = "https://example.com/page"
    for blocks in (cards.card_blocks(rec, 1), cards.see_view(rec, 1)["blocks"]):
        images = [b for b in blocks if b["type"] == "image"]
        assert [b["title"]["text"] for b in images] == [entry["who"], screens[3]["label"]]
        assert images[0]["slack_file"] == {"id": "FMap"}
        assert [b["text"]["text"] for b in big_button(blocks)] == ["See it big"]
        assert len(blocks) <= cards.BLOCKS_MAX
    core.decide(rec, 1, "approved", "UAPPROVER")
    final = cards.card_blocks(rec, 1, "final")
    assert len(final) <= cards.BLOCKS_MAX
    assert any(e.get("value") == "AP-0001|1|see" for b in final if b["type"] == "actions" for e in b["elements"])
    rec["versions"].append({"n": 2, "card": card})
    replaced = cards.card_blocks(rec, 1, "replaced")
    assert len(replaced) == 1 and replaced[0]["accessory"]["value"] == "AP-0001|1|see"
    assert big_button(cards.see_view(rec, 1)["blocks"])[0]["url"] == "https://example.com/page"


@pytest.mark.parametrize("edit, refusal", [
    (lambda j: [], "1 to 4 entries"),
    (lambda j: [j] * 5, "1 to 4 entries"),
    (lambda j: [None], "entry with who"),
    (lambda j: {k: v for k, v in j.items() if k != "who"}, "who takes this path"),
    (lambda j: dict(j, who="x" * 201), "up to 200 characters"),
    (lambda j: dict(j, today="x" * 41), "today a path name"),
    (lambda j: dict(j, after=None), "after a path name"),
    (lambda j: dict(j, places=[]), "either steps"),
    (lambda j: {"who": j["who"]}, "either steps"),
    (lambda j: dict(j, steps=j["steps"] * 3), "1 to 8 steps"),
    (lambda j: dict(j, steps={}), "1 to 8 steps"),
    (lambda j: {"who": j["who"], "places": [{}] * 7}, "1 to 6 places"),
    (lambda j: {"who": j["who"], "places": []}, "1 to 6 places"),
    (lambda j: dict(j, steps=[None, *j["steps"]]), "Write an object"),
    (lambda j: dict(j, steps=[*j["steps"], {}]), "Write today, after, or both"),
    (lambda j: dict(j, steps=[{"after": j["steps"][2]["after"]}]), "when the path doesn't exist today"),
    (lambda j: dict(j, steps=[{"today": j["steps"][0]["today"], "after": "same"}]), "Show the change"),
    (lambda j: dict(j, steps=[dict(j["steps"][0], today={"ends": "Stops here"}), *j["steps"][1:]]), 'before writing "same"'),
    (lambda j: dict(j, steps=[{"today": j["steps"][0]["today"], "after": "same"}, *j["steps"][1:]]), "Write then"),
    (lambda j: dict(j, steps=[dict(j["steps"][0], then=None), *j["steps"][1:]]), "Write then"),
    (lambda j: dict(j, steps=[dict(j["steps"][0], then="x" * 81), *j["steps"][1:]]), "up to 80 characters"),
    (lambda j: dict(j, steps=[dict(j["steps"][0], then={"after": "Tap"}), *j["steps"][1:]]), "today and after"),
    (lambda j: dict(j, steps=[dict(j["steps"][0], then={"today": "Tap", "after": "x" * 81}), *j["steps"][1:]]), "up to 80 characters"),
    (lambda j: dict(j, steps=[*j["steps"][:2], {"after": {"ends": "x" * 201}}]), "where the path ends"),
])
def test_journey_refusals_say_what_to_write(entry, edit, refusal):
    card = dict(SAMPLES["content"], journey=edit(entry))
    assert any(refusal in err for err in cards.validate(card))


@pytest.mark.parametrize("changes, refusal", [
    ({"name": ""}, "place name"),
    ({"name": "x" * 61}, "place name"),
    ({"today": {"ends": "Stops here"}}, "today: Write a screen"),
    ({"after": None}, "after: Write a screen"),
    ({"then": "Taps"}, "Leave then out"),
])
def test_a_place_needs_a_name_and_two_screens_and_takes_no_then(screens, changes, refusal):
    pair = dict({"name": "Library", "today": screens[0], "after": screens[1]}, **changes)
    card = dict(SAMPLES["content"], journey={"who": "A reader", "places": [pair]})
    assert any(refusal in err for err in cards.validate(card))


@pytest.mark.parametrize("changes, refusal", [
    ({"image": ""}, "picture file"),
    ({"image": "missing.png"}, "picture file"),
    ({"label": ""}, "Write a label"),
    ({"label": "x" * 151}, "Write a label"),
    ({"source": None}, "Write a source"),
    ({"source": "x" * 101}, "Write a source"),
    ({"new": "true"}, "Write true for new"),
])
def test_a_screen_refuses_invalid_fields(entry, changes, refusal):
    entry["steps"][1]["after"].update(changes)
    assert any(refusal in err for err in cards.validate(dict(SAMPLES["content"], journey=entry)))


def test_an_existing_file_has_to_be_a_picture(entry, tmp_path):
    path = tmp_path / "text.png"
    path.write_text("This is plain text.")
    entry["steps"][1]["after"]["image"] = str(path)
    assert any("picture file" in err for err in cards.validate(dict(SAMPLES["content"], journey=entry)))


def test_maps_and_the_page_button_count_toward_the_block_limit(entry):
    card = dict(SAMPLES["content"], sections=[])
    available = 49 - len(cards.card_blocks(cards._stub(card), 1))
    card["sections"] = [{"title": "Notes", "lines": ["One line"]} for _ in range(available)]
    assert cards.validate(card) == []
    assert any("51 Slack blocks" in err for err in cards.validate(dict(card, journey=entry)))


def test_map_and_page_keep_the_screens_in_order(entry, screens, tmp_path, monkeypatch):
    monkeypatch.setattr(journey.tempfile, "tempdir", str(tmp_path))
    card = dict(SAMPLES["content"], journey=[entry, {"who": "A returning reader", "places": [
        {"name": "Library", "today": screens[2], "after": dict(screens[3], label='Shows "</script>"')}
    ]}])
    assert cards.validate(card) == []
    for item in card["journey"]:
        path = journey.draw_map(item)
        with Image.open(path) as map_image:
            assert map_image.format == "PNG" and map_image.width > 720
            map_image.verify()

    rec = rec_for(card, link="https://example.com/slack")
    folder = tmp_path / "page"
    journey.write_page(folder, rec, 1)
    html = (folder / "index.html").read_text()
    payload = page_data(folder)
    assert (payload["id"], payload["version"], payload["agent"], payload["link"]) == ("AP-0001", 1, "Tester", rec["link"])
    names = ["journey-0-0-today.jpg", "journey-0-1-after.jpg", "journey-0-2-after.jpg", "journey-1-0-after.jpg"]
    assert payload["images"] == names
    assert all(html.count(name) == 1 for name in names)
    assert str(tmp_path) not in html
    assert payload["entries"][0]["steps"][0]["after"] == "same"
    pair = payload["entries"][1]["places"][0]
    assert pair["today"]["image"] == 2 and pair["after"]["label"] == 'Shows "</script>"'
    for size, width in (("s", 1400), ("t", 600)):
        assert sorted(p.name for p in (folder / size).iterdir()) == names
        for name in names:
            with Image.open(folder / size / name) as img:
                assert img.format == "JPEG" and img.width == width


@pytest.mark.parametrize("publish", [True, False])
def test_submit_and_revise_share_the_journey_in_order(publish, entry, screens, tmp_path, monkeypatch, slack):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-journey")
    receipts = tmp_path / "published"
    receipts.mkdir()
    if publish:
        program = tmp_path / "publish.py"
        program.write_text("""
import json
import re
import sys
from pathlib import Path
folder = Path(sys.argv[-1])
page = (folder / 'index.html').read_text()
data = json.loads(re.search(r'<script type="application/json" id="journey-data">(.*?)</script>', page)[1])
assert all((folder / size / name).is_file() for name in data['images'] for size in ('s', 't'))
n = data['version']
(Path(sys.argv[1]) / str(n)).write_text(json.dumps([sys.argv[2:-1], data['link']]))
print(f'URL: https://example.com/journey/{n}')
""")
        config.CONFIG.write_text(json.dumps(dict(config.settings(), page_share_command=[sys.executable, str(program), str(receipts)])))

    card = dict(SAMPLES["content"], journey=[entry, {"who": "A returning reader", "places": [
        {"name": "Library", "today": screens[2], "after": screens[1]}
    ]}], gallery=[screens[3]])
    path = tmp_path / "card.json"
    path.write_text(json.dumps(card))
    upload = slackapi.upload_pictures_to_thread

    def upload_after_publishing(items, ts, comment):
        version = len(store.load("AP-0001")["versions"])
        assert (receipts / str(version)).exists() == publish
        return upload(items, ts, comment)

    monkeypatch.setattr(slackapi, "upload_pictures_to_thread", upload_after_publishing)
    cli.main(["submit", str(path)])
    path.write_text(json.dumps(dict(card, changes="Clearer screen labels.")))
    cli.main(["revise", "AP-0001", str(path)])
    rec = store.load("AP-0001")
    keys = ["journey:0:map", "journey:1:map", "journey:0:0:today", "journey:0:1:after", "journey:0:2:after", "gallery:0"]
    for n, (version, shared) in enumerate(zip(rec["versions"], [p for p in slack if "pictures" in p], strict=True), 1):
        assert [Path(p).name for p in shared["pictures"]] == ["map.png", "map.png", *(Path(s["image"]).name for s in screens)]
        assert list(version["files"]) == keys
        buttons = big_button(cards.see_view(rec, n)["blocks"])
        if publish:
            assert json.loads((receipts / str(n)).read_text()) == [["--title", card["title"], "--description", card["why"]], rec["link"]]
            assert buttons[0]["url"] == version["journey_url"] == f"https://example.com/journey/{n}"
        else:
            assert "journey_url" not in version and buttons == []
    redrawn = [p["blocks"] for p in slack if p.get("update") == rec["thread_ts"]]
    assert [b["title"]["text"] for b in redrawn[0] if b["type"] == "image"][:2] == [entry["who"], "A returning reader"]
    assert bool(big_button(redrawn[0])) == publish
    downloads = []
    monkeypatch.setattr(slackapi, "download", lambda file_id, folder, name: downloads.append(name))
    cli.main(["pictures", "AP-0001", "--dir", str(tmp_path / "downloads")])
    assert downloads == [k.replace(":", "-") for k in keys]


def test_share_page_raises_if_the_command_does_not_publish(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "settings", lambda: {})
    assert slackapi.share_page(tmp_path, "title", "why") is None
    monkeypatch.setattr(config, "settings", lambda: {"page_share_command": [sys.executable, "-c", "print('No URL')"]})
    with pytest.raises(RuntimeError, match="No URL"):
        slackapi.share_page(tmp_path, "title", "why")


def test_the_reader_gets_each_paths_words_in_order(entry):
    text = reader.card_text(dict(SAMPLES["content"], journey=entry))
    expected = [
        f"JOURNEY ({entry['who']})", "Step 1", "Today: Screen 1 · Mock", "With the search box: same",
        "Then (Today): Opens the menu", "Then (With the search box): Taps the library", "Step 2",
        "Today: The menu has no search.", "With the search box: Screen 2 · Mock", "Then: Searches for a book",
        "Step 3", "With the search box: Screen 3 · Mock",
    ]
    assert [text.index(s) for s in expected] == sorted(text.index(s) for s in expected)
    assert ".png" not in text


def test_a_card_without_a_journey_keeps_its_blocks(monkeypatch):
    rec = rec_for(SAMPLES["content"])
    for mode in ("open", "final", "view"):
        with monkeypatch.context() as patch:
            patch.setattr(cards, "_journey", lambda r, n: [])
            expected = cards.card_blocks(rec, 1, mode)
        assert cards.card_blocks(rec, 1, mode) == expected
