"""Publish the journeys of related approval cards together on the hub."""
import hashlib
import json
import logging
import os
import secrets
import subprocess
import tempfile
from collections import defaultdict
from datetime import timedelta
from html import unescape
from urllib.parse import urljoin

from . import cards, config, core, digest, journey, slackapi, store

log = logging.getLogger("approvals")
SETTLED_DAYS = 7
SETTLED_LIMIT = 20


def _waiting_since(rec):
    if wait := cards.on_you(rec):
        return store.parse(wait["at"])
    return digest.waiting_since(rec)


def _page_card(rec, waiting):
    version = rec["versions"][-1]
    card = version["card"]
    decision = rec.get("decision") or {}
    if waiting:
        state = f"Waiting on you since {cards.when(_waiting_since(rec))}"
    elif line := cards.decision_line(rec, card):
        state = unescape(line)
    else:
        # A set answers each row on the card itself. Use the set's heading here.
        state = f"{cards._final_head(rec, card)} · {cards.when(store.parse(decision['at']))}"
    status = None
    if rec.get("timeline"):
        newest = rec["timeline"][-1]
        status = {"text": cards.status_line(newest, slack=False), "at": cards.when(store.parse(newest["at"]))}
    return {
        "id": rec["id"], "link": rec["link"], "title": card["title"], "agent": rec["agent"], "version": version["n"],
        "waiting": waiting, "state": state, "status": status,
        "image_base": urljoin(version["journey_url"], "."), **version["journey_data"],
    }


def build_pages(records, now):
    """Return each page's visible content, keyed by its channel, test flag and matching name."""
    cutoff = now - timedelta(days=SETTLED_DAYS)
    targets = config.targets()
    buckets = defaultdict(list)
    for rec in records:
        if not rec.get("thread_ts") or rec["status"] == "withdrawn":
            continue
        name = cards.part_of(rec, rec["versions"][-1]["card"])
        if key := cards.name_key(name):
            channel = targets[rec.get("target")]["channel"]
            buckets[channel, bool(rec.get("test")), key].append((rec, " ".join(name.split())))

    pages = {}
    for key, records_with_names in buckets.items():
        waiting, settled, without_journey = [], [], []
        for rec, name in records_with_names:
            version = rec["versions"][-1]
            open_ = digest.waiting(rec)
            if not version.get("journey_url"):
                if open_:
                    without_journey.append(rec)
                continue
            if not version.get("journey_data"):
                continue   # there are no picture references on older published versions
            if open_:
                waiting.append((_waiting_since(rec), rec["id"], rec, name))
            elif rec["status"] in core.FINAL and (at := (rec.get("decision") or {}).get("at")):
                if (decided := store.parse(at)) >= cutoff:
                    settled.append((decided, rec["id"], rec, name))

        shown = sorted(waiting, reverse=True) + sorted(settled, reverse=True)[:SETTLED_LIMIT]
        if len(shown) < 2:
            continue
        name = max(shown, key=lambda item: (item[2]["created"], item[1]))[3]
        other_waiting = sorted(without_journey, key=lambda r: (r["created"], r["id"]), reverse=True)
        pages[key] = {
            "name": name,
            "cards": [_page_card(rec, i < len(waiting)) for i, (_, _, rec, _) in enumerate(shown)],
            "without_journey": [{"title": r["versions"][-1]["card"]["title"], "link": r["link"]} for r in other_waiting],
            "omitted": max(0, len(settled) - SETTLED_LIMIT),
        }
    return pages


def _save_state(path, state):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-groups-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def refresh(records):
    """Publish only changed pages and return the IDs whose buttons need to be redrawn."""
    pages = build_pages(records, store.now())
    path = config.HOME / "groups.json"
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        state = {}
    current = {r["id"]: r["group"] for r in records if r.get("group")}
    wanted = {}
    for key, page in pages.items():
        name, shown = page["name"], page["cards"]
        state_key = json.dumps(key, ensure_ascii=False)
        previous = state.get(state_key)
        fingerprint = hashlib.sha256(json.dumps(page, sort_keys=True).encode()).hexdigest()
        try:
            if previous is None:
                previous = state[state_key] = {"slug": secrets.token_hex(16)}
                _save_state(path, state)
            if previous.get("fingerprint") != fingerprint:
                with tempfile.TemporaryDirectory(prefix="approvals-group-") as folder:
                    journey.write_html(folder, page)
                    url = slackapi.share_page(folder, name, f"{len(shown)} cards · Part of {name}", slug=previous["slug"])
                if not url:
                    raise RuntimeError("page_share_command is unset")
                state[state_key] = {**previous, "url": url, "fingerprint": fingerprint}
                _save_state(path, state)
        except (OSError, RuntimeError, subprocess.SubprocessError) as e:
            state[state_key] = previous
            log.warning("group page for %s failed, will retry: %s", name, e)
            # Keep existing links to the last published page for cards still in that group.
            for card in shown:
                rid = card["id"]
                if rid in current and current[rid]["url"] == previous.get("url"):
                    wanted[rid] = current[rid]
            continue
        group = {"url": state[state_key]["url"], "count": len(shown), "name": name}
        wanted.update((card["id"], group) for card in shown)

    changed = []
    for rid in sorted(current.keys() | wanted.keys()):
        group = wanted.get(rid)
        if group == current.get(rid):
            continue

        def set_group(rec):
            if group:
                rec["group"] = group
            else:
                rec.pop("group", None)
            rec["card_dirty"] = True

        try:
            store.update(rid, set_group)
        except FileNotFoundError:
            continue   # a test card was purged during the publish
        changed.append(rid)
    return changed
