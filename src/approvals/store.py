"""Request records: one JSON file per request, written under a lock, on the workspace's hub machine.

On any other machine the same calls go to the hub over HTTP (hub.py). A change there is made against the copy it
read: when someone else changed the record in between, the hub refuses it and the change is made again on the
new copy, so a function passed to update() can run more than once and must only change the record it is given.
"""
import contextlib
import contextvars
import fcntl
import hashlib
import json
import marshal
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from . import config

STATUSES = {"pending", "question", "revising", "approved", "declined", "expired", "done"}
OPEN = {"pending", "question", "revising", "expired", "approved"}  # threads still watched for replies
UPDATE_TRIES = 20
FILE_TIMEOUT = 300   # a card's video can be 100 MB
_records_cache = {}
_records_cache_lock = threading.Lock()
_fork_cache = {}
_fork_cache_lock = threading.RLock()
# Who is acting in this thread: the Slack user whose press or pop-up is being handled, or the Herdr name of the agent
# running the command. Each event is stamped with it, so the record writes each one as that person or member.
ACTOR = contextvars.ContextVar("actor", default=None)
# The answer the record took that the write being made carries out. Each event the write adds names it, so the record
# marks that answer carried out in the same write, and only once.
ANSWER = contextvars.ContextVar("answer", default=None)


class StoreError(RuntimeError):
    """The hub that holds the records didn't answer, or refused."""


class Refused(StoreError):
    """The record refused a write or an answer by its rules, in its own words: the words the giver is shown."""


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


def parse(s):
    return datetime.fromisoformat(s)


def hub_client():
    """A client for the hub when this workspace's records are on another machine, else None."""
    if config.is_hub():
        return None
    from . import hub
    return hub.Client()


# ---------- the calls everything uses ----------

def create(rec_without_id, media=None):
    """media: how the card's pictures and videos were named when sent (send_media), kept with the card."""
    h = hub_client()
    return h.create(rec_without_id, media) if h else local_create(rec_without_id, media)


def load(rid):
    h = hub_client()
    return (h.read(rid) if h else local_read(rid))[0]


def save(rec):
    h = hub_client()
    if h:
        h.put(rec)
    else:
        local_put(rec)


def update(rid, fn, media=None):
    """Load, change with fn(rec), save. Returns fn's result. media goes with the write, as in create()."""
    h = hub_client()
    if not h and record():
        h = _RecordAsHub()
    if not h:
        with locked():
            rec = local_read(rid)[0]
            result = fn(rec)
            _write(rec)
            return result
    for _ in range(UPDATE_TRIES):
        rec, rev = h.read(rid)
        result = fn(rec)
        if h.put(rec, rev, media):
            return result
    raise StoreError(f"{rid} kept changing on the hub while this tried to change it; try again")


def all_records():
    h = hub_client()
    return h.records() if h else local_all()


def history_records(agent=None, type=None, since=None, limit=20, calls=False):
    """Cards newest first that match: part of the asker's name, a type, posted since a time, called by the deputy.
    Filtered where the records are kept, so a look at a few cards doesn't bring every record across the network."""
    h = hub_client()
    return h.history(agent, type, since, limit, calls) if h else local_history(agent, type, since, limit, calls)


def corrections(card_type):
    """The open corrections for a card type, each {"id", "title", "says"} as its writer wrote it, from the pod
    dashboard's record; none where no record keeps the cards."""
    h = hub_client()
    return h.corrections(card_type) if h else local_corrections(card_type)


def names(days, *, channel=None, test=False, exclude=None):
    """Recent Part of names, summarized where the records are kept."""
    h = hub_client()
    return (h.names if h else local_names)(days, channel=channel, test=test, exclude=exclude)


def waiting_records():
    """Records with a message the agent hasn't been given yet."""
    h = hub_client()
    return h.records(waiting=True) if h else [r for r in local_all() if waiting(r)]


def send_media(n, files):
    """Send version n's pictures and videos, (key, path) on this machine, to where the cards are kept, each file once
    under its sha256 and extension. Returns how a card's write names them, [{"version", "key", "address"}]: none when
    the cards are kept where files can't go (no record, or a hub from before files), and without a file it refused."""
    h = hub_client()
    if h:
        if not h.health().get("files"):
            return []
        send = h.put_file
    elif record():
        send = record().put_file
    else:
        return []
    kept, media = {}, []
    for key, path in files:
        path = Path(path).expanduser()
        data = path.read_bytes()
        name = hashlib.sha256(data).hexdigest() + path.suffix.lower()
        if name not in kept:
            kept[name] = send(name, data)
            if not kept[name]:
                print(f"(the record didn't keep {path}; the card shows without it there)", file=sys.stderr)
        if kept[name]:
            media.append({"version": n, "key": key, "address": name})
    return media


def delete(rid):
    h = hub_client()
    if h:
        h.delete(rid)
    else:
        local_delete(rid)


def reviewer():
    """The one session that reviews the workspace's cards of some types (the CFO, for money), as
    {"types", "reach", "test"}, or None."""
    h = hub_client()
    return h.reviewer() if h else local_reviewer()


def set_reviewer(rv):
    """rv None: no session reviews."""
    h = hub_client()
    if h:
        h.set_reviewer(rv)
    else:
        local_set_reviewer(rv)


def deputy():
    """The one session that hears every card and makes its own call on each, as
    {"reach", "live", "test", "since", "live_since"}, or None. live: its approve is the approver's yes. Kept in the
    record when the record holds the cards."""
    h = hub_client()
    return h.deputy() if h else local_deputy()


def set_deputy(d):
    """d None: nobody is the deputy."""
    h = hub_client()
    if h:
        h.set_deputy(d)
    else:
        local_set_deputy(d)


def keeps_answers():
    """Whether this workspace's answers are taken by the record: its hub keeps the cards there."""
    h = hub_client()
    return bool(h.health().get("record")) if h else bool(record())


def give(answer):
    """An answer given here rather than pressed (the deputy's call, one an agent records), when the record takes
    answers: {"card", "version", "what", "via", "key", and "words", "session", "detail" as it needs}. The record takes
    it, then the hub's service carries it out, as it does every answer. Returns once it is carried out; raises Refused
    with the words when the record or the hub's press code refuses it."""
    from . import hub
    return hub.Client().give(answer)


def forks():
    """The workspace's registered session ids and their fork families."""
    with _fork_cache_lock:
        key = str(config.HOME)
        cached = _fork_cache.get(key)
        now = time.monotonic()
        if cached is None or now - cached[0] >= 15:
            h = hub_client()
            cached = (now, h.forks() if h else local_forks())
            _fork_cache[key] = cached
        return dict(cached[1])


def add_fork(fork, same_as=None):
    h = hub_client()
    family = h.add_fork(fork, same_as) if h else local_add_fork(fork, same_as)
    with _fork_cache_lock:
        cached = _fork_cache.get(str(config.HOME))
        if cached:
            cached[1][fork] = family


def same_family(first, second):
    families = forks()
    return families.get(first) == families.get(second)


def inbox_count(session_id):
    h = hub_client()
    return h.inbox_count(session_id) if h else local_inbox_count(session_id)


def waiting(rec):
    return any(not i.get("delivered_at") and not i.get("seen_by_agent") for i in rec.get("outbox", []))


def event(rec, kind, **data):
    actor = {"actor": ACTOR.get()} if ACTOR.get() and "actor" not in data else {}
    answer = {"answer": ANSWER.get()} if ANSWER.get() is not None else {}
    rec.setdefault("events", []).append(dict(kind=kind, at=iso(), **data, **actor, **answer))


# ---------- the files, on the hub ----------

@contextlib.contextmanager
def locked():
    config.ensure_dirs()
    with open(config.LOCK, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _path(rid):
    return config.REQUESTS / f"{rid}.json"


def rev_of(data):
    return hashlib.sha256(data).hexdigest()[:16]


def _write(rec):
    """Caller must hold the lock. Returns the new revision."""
    data = json.dumps(rec, indent=1, ensure_ascii=False).encode()
    fd, tmp = tempfile.mkstemp(dir=config.REQUESTS, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, _path(rec["id"]))
    return rev_of(data)


def new_id():
    """Caller must hold the lock."""
    n = int(config.COUNTER.read_text()) if config.COUNTER.exists() else 1
    config.COUNTER.write_text(str(n + 1))
    return f"{config.settings()['id_prefix']}-{n:04d}"


def local_create(rec_without_id, media=None):
    """media is kept only by the record; a hub of files has nowhere to keep it."""
    if record():
        return record().create(rec_without_id, media)
    with locked():
        rid = new_id()
        rec = dict(rec_without_id, id=rid)
        _write(rec)
        return rec


def local_read(rid):
    """(record, revision). Raises FileNotFoundError for a request that doesn't exist."""
    if record():
        return record().read(rid)
    data = _path(rid).read_bytes()
    return json.loads(data), rev_of(data)


def local_put(rec, rev=None, media=None):
    """Write a record. Given the revision it was read at, only if nobody changed it since: returns the new
    revision, or None when it changed."""
    if record():
        return record().put(rec, rev, media)
    with locked():
        if rev is not None:
            try:
                current = rev_of(_path(rec["id"]).read_bytes())
            except FileNotFoundError:
                current = None
            if current != rev:
                return None
        return _write(rec)


def local_all():
    if record():
        return record().all()
    config.ensure_dirs()
    with _records_cache_lock:
        paths = sorted(config.REQUESTS.glob(f"{config.settings()['id_prefix']}-*.json"))
        for p in _records_cache.keys() - set(paths):
            del _records_cache[p]
        out = []
        for p in paths:
            try:
                stat = p.stat()
                cached = _records_cache.get(p)
                if cached is None or cached[:2] != (stat.st_mtime_ns, stat.st_size):
                    # Kept as marshal bytes: each call gets its own copy at a third of the cost of rereading
                    # and parsing every file (1,310 records on the hub: 27 ms against 81, November 16).
                    cached = (stat.st_mtime_ns, stat.st_size, marshal.dumps(json.loads(p.read_text())))
                    _records_cache[p] = cached
                out.append(marshal.loads(cached[2]))
            except (json.JSONDecodeError, OSError):
                _records_cache.pop(p, None)
        return out


def local_history(agent=None, type=None, since=None, limit=20, calls=False):
    out = []
    for rec in sorted(local_all(), key=lambda r: (r["created"], r["id"]), reverse=True):
        if ((agent and agent.lower() not in rec["agent"].lower()) or (type and rec["type"] != type)
                or (since and parse(rec["created"]) < parse(since)) or (calls and not rec.get("calls"))):
            continue
        if len(out) == limit:
            break
        out.append(rec)
    return out


def local_names(days, *, channel=None, test=False, exclude=None):
    from . import cards

    cutoff = now() - timedelta(days=days)
    targets = config.targets() if channel else {}
    names = {}
    for rec in sorted(local_all(), key=lambda r: (r["created"], r["id"]), reverse=True):
        if (not rec.get("thread_ts") or rec["id"] == exclude or bool(rec.get("test")) != test
                or parse(rec["created"]) < cutoff or channel and targets[rec.get("target")]["channel"] != channel):
            continue
        card = rec["versions"][-1]["card"]
        name = cards.part_of(rec, card)
        if not (key := cards.name_key(name)):
            continue
        summary = names.setdefault(key, {"name": " ".join(name.split()), "count": 0, "agents": [], "title": card["title"]})
        summary["count"] += 1
        if rec["agent"] not in summary["agents"]:
            summary["agents"].append(rec["agent"])
    return list(names.values())


def local_corrections(card_type):
    return record().corrections(card_type) if record() else []


def local_delete(rid):
    if record():
        return record().delete(rid)
    with locked():
        _path(rid).unlink(missing_ok=True)


def _read_role(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def _write_role(path, role):
    """Replaced whole and outside the lock: it's read while a record is changed under the lock."""
    config.ensure_dirs()
    if role is None:
        path.unlink(missing_ok=True)
        return
    fd, tmp = tempfile.mkstemp(dir=config.HOME, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(role, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def local_reviewer():
    return _read_role(config.REVIEWER)


def local_set_reviewer(rv):
    _write_role(config.REVIEWER, rv)


def local_deputy():
    return record().who()["deputy"] if record() else _read_role(config.DEPUTY)


def local_set_deputy(d):
    if record():
        record().set_deputy(d)
    else:
        _write_role(config.DEPUTY, d)


def local_forks():
    return _read_role(config.FORKS) or {}


def local_add_fork(fork, same_as=None):
    with locked():
        families = local_forks()
        if fork not in families:
            if same_as and same_as not in families:
                raise StoreError(f"No fork is registered as {same_as}")
            families[fork] = families[same_as] if same_as else fork
            _write_role(config.FORKS, families)
        return families[fork]


def local_inbox_count(session_id):
    """Count undelivered answers for a recorded session id without handing any out."""
    families = local_forks()
    roles = {to: bool(role) and session_id == role["reach"].get("session_id")
             for to, role in (("reviewer", local_reviewer()), ("deputy", local_deputy()))}
    count = 0
    for rec in local_all():
        r = rec.get("reach") or {}
        own = (session_id in [r.get("session_id"), *r.get("earlier_ids", [])]
               and families.get(r.get("session_id")) == families.get(session_id))
        count += sum(1 for i in rec.get("outbox", [])
                     if not i.get("delivered_at") and not i.get("seen_by_agent")
                     and (roles.get(i["to"], False) if i.get("to") else own))
    return count


# ---------- the record, when it holds the cards ----------

_record = {}


def record():
    """The pod dashboard's record, when this workspace's config names it (record_url) and this machine is the hub:
    every card is read and written there, and the hub's own files hold no cards."""
    url = config.file_settings().get("record_url")
    if not url or not config.is_hub():
        return None
    key = (str(config.HOME), url)
    if key not in _record:
        _record[key] = Record(url.rstrip("/"), config.record_token())
    return _record[key]


class Record:
    """The record's card routes. A card is the same document the files held; the record keeps it at a revision, and
    refuses a write on an older one (409) or one that breaks its rules (422, with the record's reason).

    Each version is kept with the pop-ups its buttons open (cards.views), drawn by the first write that keeps it, so
    the record opens them whether or not this hub is running. The hub itself never reads them, so its look at every
    card leaves them out."""

    def __init__(self, url, token):
        self.url, self.token = url, token
        self.lock = threading.Lock()
        self.cache = {}   # id -> (rev, marshal bytes of the card without its pop-ups, whether a version lacks them)

    def call(self, method, path, body=None):
        """(HTTP status, JSON body). A missing card (404) and a changed one (409) are answers; a write the record's
        rules refuse (422) raises Refused with its words; anything else that isn't 200 is an error."""
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"X-Hub-Token": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            if e.code in (404, 409):
                return e.code, json.loads(text or "{}")
            if e.code == 422:
                raise Refused(json.loads(text)["error"])
            raise StoreError(f"the record refused {method} {path}: {e.code} {text[:300]}")
        except (urllib.error.URLError, OSError) as e:
            raise StoreError(f"the record ({self.url}) isn't answering: {e}")

    @staticmethod
    def keep_views(rec):
        from . import cards
        for n, v in enumerate(rec.get("versions") or [], 1):
            if "views" not in v:
                v["views"] = cards.views(rec, n)

    def create(self, rec, media=None):
        """A new card, given its number by the record; its pop-ups are drawn by the write that follows, which knows
        the number they carry."""
        return self.call("POST", "/api/cards", {"record": rec, "media": media or []})[1]["record"]

    def read(self, rid):
        code, body = self.call("GET", f"/api/cards/{rid}")
        if code == 404:
            raise FileNotFoundError(f"no request {rid}")
        return body["record"], body["rev"]

    def put(self, rec, rev=None, media=None):
        """The new revision, or None when the card changed since rev. Without rev, written over what is there."""
        self.keep_views(rec)
        while True:
            at = rev if rev is not None else self.read(rec["id"])[1]
            code, body = self.call("PUT", f"/api/cards/{rec['id']}", {"record": rec, "rev": at, "media": media or []})
            if code == 200:
                return body["rev"]
            if rev is not None:
                return None

    def all(self):
        """Every card, read again only where its revision moved since the last look."""
        revs = {c["id"]: c["rev"] for c in self.call("GET", "/api/cards")[1]["cards"]}
        with self.lock:
            for rid in self.cache.keys() - revs.keys():
                del self.cache[rid]
            moved = [rid for rid, rev in revs.items() if self.cache.get(rid, (None,))[0] != rev]
            for i in range(0, len(moved), 200):
                for c in self.call("POST", "/api/cards/read", {"ids": moved[i:i + 200]})[1]["cards"]:
                    versions = c["record"].get("versions") or []
                    lacking = any("views" not in v for v in versions)
                    for v in versions:
                        v.pop("views", None)
                    self.cache[c["record"]["id"]] = (c["rev"], marshal.dumps(c["record"]), lacking)
            return [marshal.loads(self.cache[rid][1]) for rid in sorted(revs) if rid in self.cache]

    def without_views(self):
        """The cards a version of which isn't kept with its pop-ups yet, as of the last look at every card."""
        with self.lock:
            return sorted(rid for rid, (_, _, lacking) in self.cache.items() if lacking)

    # ----- the answers the record took -----

    def answers(self):
        """Every answer taken and not yet carried out or refused, oldest first."""
        return self.call("GET", "/api/cards/answers")[1]["answers"]

    def take(self, answer):
        """An answer this hub gives: its own deadline or hand-back, the deputy's call, or one an agent records. Returns
        {"answer", "card", "version", "taken"}; raises Refused with the record's words."""
        code, body = self.call("POST", "/api/cards/answers", answer)
        if code == 404:
            raise Refused(body["error"])
        return body

    def refuse(self, answer_id, words):
        """Mark an answer the record took that this hub's press code refused, with the words the giver was shown."""
        self.call("POST", f"/api/cards/answers/{answer_id}/refused", {"words": words})

    def who(self):
        """Who decides, the team and the deputy, in the shape of the hub's settings (with "team_order")."""
        return self.call("GET", "/api/cards/who")[1]

    def set_deputy(self, d):
        self.call("PUT", "/api/cards/deputy", {"deputy": d})

    def corrections(self, card_type):
        code, body = self.call("GET", f"/api/cards/corrections/{quote(card_type, safe='')}")
        if code != 200:
            raise StoreError(f"the record ({self.url}) runs a version without corrections; update it first")
        return body["corrections"]

    def changes(self, after=None):
        """The record's stream of changes: (event id, {"thing", "kind", "pod"}) as each is made, starting with
        (the id it starts after, None), and from `after` when given. Ends when the stream is cut."""
        path = "/api/changes" + (f"?after={after}" if after is not None else "")
        req = urllib.request.Request(self.url + path, headers={"X-Hub-Token": self.token})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                kind, event_id, data = None, None, None
                for raw in r:
                    line = raw.decode().rstrip("\r\n")
                    if line.startswith("event:"):
                        kind = line[6:].strip()
                    elif line.startswith("id:"):
                        event_id = int(line[3:].strip())
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                    elif not line:
                        if kind == "start":
                            yield event_id, None
                        elif event_id is not None and data is not None:
                            yield event_id, data
                        kind, event_id, data = None, None, None
        except (urllib.error.URLError, OSError) as e:
            raise StoreError(f"the record's stream of changes ({self.url}) was cut: {e}")

    def delete(self, rid):
        self.call("DELETE", f"/api/cards/{rid}")

    def picture(self, name):
        """One picture in the record's picture store, by its name there (a comment's, from the pod dashboard)."""
        try:
            with urllib.request.urlopen(f"{self.url}/pictures/{name}", timeout=FILE_TIMEOUT) as r:
                return r.read()
        except (urllib.error.URLError, OSError) as e:
            raise StoreError(f"the record ({self.url}) didn't give picture {name}: {e}")

    def put_file(self, name, data):
        """Keep one picture or video, named by its sha256 and extension; True when the record has it."""
        req = urllib.request.Request(f"{self.url}/api/cards/files/{name}", data=data, method="PUT",
                                     headers={"X-Hub-Token": self.token, "Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=FILE_TIMEOUT) as r:
                return r.status == 200
        except urllib.error.HTTPError as e:
            e.close()
            return False
        except (urllib.error.URLError, OSError):
            return False


class _RecordAsHub:
    """update() on the hub when the record holds the cards: the same read, change and write-if-unchanged as on any
    other machine."""

    def read(self, rid):
        return record().read(rid)

    def put(self, rec, rev, media=None):
        return record().put(rec, rev, media) is not None
