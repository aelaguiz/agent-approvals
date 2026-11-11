"""The step an approval card keeps on each Basecamp card it serves.

Amir decides on the Slack card, but the work's picture is in Basecamp. So a card may name the Basecamp items it serves
(`approvals submit --basecamp <url>`, or "basecamp" in its JSON). The Slack card's "Part of:" links to the first. On
each Basecamp card (a to-do only gets the link) the asker's own Basecamp login keeps one step that says where the
approval stands: waiting on him, then his answer and its time, "not yet seen by" the asker until it acknowledges or
the answer lands, checked once the card is done, withdrawn or expired. A step notifies nobody, so he never gets his own
answer back, and nobody's edit of the card's text can erase it.

The step is written where the state changes: by `approvals` in the asker's session for what the asker does, and by the
service on the asker's machine for the approver's answers and their delivery, whether or not the answer could be typed
in (on November 11, 2026 two yeses never reached their agent and nothing in Basecamp showed it). Basecamp never holds up
a card or an answer: a failure is logged, noted on the record and tried again on later passes, up to TRIES times for
the same text.
"""
import json
import logging
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import cards, config, core, store

log = logging.getLogger("approvals")

URL = re.compile(r"^https://(?:app|3)\.basecamp\.com/(\d+)/\S+$")
CARD = re.compile(r"^https://(?:app|3)\.basecamp\.com/(\d+)/buckets/(\d+)/card_tables/cards/(\d+)(?:[/?#]\S*)?$")
TRIES = 5
HEADLINE_MAX = 80
CREDENTIALS = Path.home() / ".config" / "basecamp" / "credentials.json"
_lock = threading.Lock()   # one writer per process, so two passes never both create a card's step


def urls(given):
    """The URLs a card names: one, or a list."""
    return [given] if isinstance(given, str) else list(given or [])


def problems(given):
    return [f"basecamp: {u!r} isn't a Basecamp link; give the item's address, https://app.basecamp.com/<account>/..."
            for u in given if not (isinstance(u, str) and URL.match(u.strip()))]


def entries(given):
    out = []
    for u in (u.strip() for u in given):
        if u not in [e["url"] for e in out]:
            out.append({"url": u})
    return out


def is_card(url):
    return bool(CARD.match(url))


# ---------- what the step says ----------

def headline(title):
    if len(title) <= HEADLINE_MAX:
        return title
    cut = title[:HEADLINE_MAX].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-") + "…"


def unseen(rec):
    """An answer has gone to the asker's outbox that it hasn't acknowledged and that hasn't been seen landing."""
    return any(not i.get("to") and not i.get("acked_at") and not i.get("delivered_at") for i in rec.get("outbox", []))


def last_reply_at(rec):
    return next((e["at"] for e in reversed(rec.get("events", []))
                 if e["kind"] in ("feedback", "answer") or e["kind"] == "part" and e.get("verdict") == "change"), None)


def wanted(rec):
    """(text, checked) the step should show now."""
    who = config.settings()["approver_name"]
    status = rec["status"]
    d = rec.get("decision") or {}
    verdict = "withdrawn" if status == "withdrawn" else d.get("verdict") if status in ("approved", "declined", "expired", "done") else None
    if verdict == "withdrawn":
        what = "withdrawn"
    elif verdict:
        what = verdict + (f" {cards.clock(datetime.fromisoformat(d['at']))}" if d.get("at") else "")
    elif status == "revising" and last_reply_at(rec):
        what = f"asked a question {cards.clock(datetime.fromisoformat(last_reply_at(rec)))}"
    else:
        what = f"waiting on {who}: {headline(rec['title'])}"
    if verdict != "withdrawn" and not what.startswith("waiting") and unseen(rec):
        what += f" · not yet seen by {rec['agent']}"
    text = " · ".join(x for x in (rec["id"], what, rec.get("link")) if x)
    return text, status in ("done", "withdrawn", "expired")


def stale(rec, e, want):
    return is_card(e["url"]) and e.get("written") != list(want)


def due(records, machine):
    """Ids of the records whose steps the service on `machine`, the asker's, should write now: the approver's answers
    and their delivery, and retries. A step not yet created is left to the asker's own `approvals` until it has failed
    there once, so the service and the asker never both create one."""
    one_machine = not config.settings().get("hub")
    out = []
    for rec in records:
        if not rec.get("basecamp") or not (one_machine or core.machine_of(rec) == machine):
            continue
        want = list(wanted(rec))
        for e in rec["basecamp"]:
            if not stale(rec, e, want) or not (e.get("step") or e.get("tries")):
                continue
            if e.get("tried") == want and (e.get("tries", 0) >= TRIES or store.parse(e.get("next_try") or store.iso()) > store.now()):
                continue
            out.append(rec["id"])
            break
    return out


# ---------- writing it ----------

def profile(cwd):
    """The Basecamp login of the agent working in cwd: its folder's (or the nearest folder above's)
    .basecamp/config.json names it. None when it has none."""
    if not cwd:
        return None
    for d in (Path(cwd), *Path(cwd).parents):
        f = d / ".basecamp" / "config.json"
        try:
            return json.loads(f.read_text()).get("default_profile") or None
        except (OSError, ValueError):
            continue
    return None


def run(prof, account, *args):
    """One basecamp command as the agent's own login. A machine that keeps its logins in the credentials file (every
    one but Amir's laptop, so sessions started over ssh, where the keychain is locked, can read them) is told to use
    it, or a service in the desktop session would look in the keychain and find nothing."""
    env = dict(os.environ)
    if CREDENTIALS.exists():
        env["BASECAMP_NO_KEYRING"] = "1"
    p = subprocess.run(["basecamp", "--profile", prof, "--account", account, "cards", "step", *args, "--agent"],
                       capture_output=True, text=True, timeout=60, env=env, cwd=Path.home())
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = None
    if p.returncode or not isinstance(out, dict) or out.get("error"):
        err = out.get("error") if isinstance(out, dict) else None
        raise RuntimeError((err or (p.stderr.strip().splitlines() or [f"exit {p.returncode}"])[-1])[:300])
    return out


def write(prof, e, want, done_before):
    """Create, retitle, check or uncheck the step until it says `want`. Returns the step's id."""
    account, project, card = CARD.match(e["url"]).groups()
    text, checked = want
    step = e.get("step")
    if not step:
        step = str(run(prof, account, "create", text, "--card", card, "--in", project)["id"])
        e["step"] = step   # kept even if a later call fails, so a retry updates it rather than adding another
    elif (e.get("written") or [None])[0] != text:
        run(prof, account, "update", step, text, "--in", project)
    if checked != done_before:
        run(prof, account, "complete" if checked else "uncomplete", step, "--in", project)
    return step


def sync(rid):
    """Bring every Basecamp step of the request up to date. Never raises: what failed is logged and noted on the
    record, and tried again on a later pass. Returns what it couldn't do, for the command to say."""
    with _lock:
        try:
            rec = store.load(rid)
        except Exception as e:
            log.warning("%s: Basecamp step not checked: %s", rid, e)
            return [str(e)]
        want = list(wanted(rec))
        todo = [i for i, e in enumerate(rec.get("basecamp") or []) if stale(rec, e, want)]
        if not todo:
            return []
        cwd = (rec.get("reach") or {}).get("cwd")
        prof = profile(cwd)
        results = {}
        for i in todo:
            e = dict(rec["basecamp"][i])
            if not prof:
                results[i] = (None, f"no Basecamp login in {cwd}", TRIES)
                continue
            try:
                write(prof, e, want, bool((e.get("written") or [None, False])[1]))
                results[i] = (e["step"], None, 0)
            except Exception as err:
                results[i] = (e.get("step"), str(err) or type(err).__name__, None)

        def save(r):
            for i, (step, err, tries) in results.items():
                e = r["basecamp"][i]
                if step:
                    e["step"] = step
                if err is None:
                    e.update(written=want, tried=want, tries=0, error=None)
                    continue
                n = tries if tries is not None else (e.get("tries", 0) + 1 if e.get("tried") == want else 1)
                e.update(tried=want, tries=n, error=err, next_try=store.iso(store.now() + timedelta(minutes=n)))
                store.event(r, "basecamp_failed", url=e["url"], error=err)
        try:
            store.update(rid, save)
        except Exception as err:
            log.warning("%s: Basecamp step written but not saved on the record: %s", rid, err)
        failed = [f"{rec['basecamp'][i]['url']}: {err}" for i, (_, err, _) in results.items() if err]
        for f in failed:
            log.warning("%s: Basecamp step not written (%s)", rid, f)
        for i, (_, err, _) in results.items():
            if not err:
                log.info("%s: Basecamp step on %s: %s", rid, rec["basecamp"][i]["url"], want[0])
        return failed
