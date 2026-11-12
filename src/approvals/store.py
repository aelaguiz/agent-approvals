"""Request records: one JSON file per request, written under a lock, on the workspace's hub machine.

On any other machine the same calls go to the hub over HTTP (hub.py). A change there is made against the copy it
read: when someone else changed the record in between, the hub refuses it and the change is made again on the
new copy, so a function passed to update() can run more than once and must only change the record it is given.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

from . import config

STATUSES = {"pending", "question", "revising", "approved", "declined", "expired", "done"}
OPEN = {"pending", "question", "revising", "expired", "approved"}  # threads still watched for replies
UPDATE_TRIES = 20


class StoreError(RuntimeError):
    """The hub that holds the records didn't answer, or refused."""


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

def create(rec_without_id):
    h = hub_client()
    return h.create(rec_without_id) if h else local_create(rec_without_id)


def load(rid):
    h = hub_client()
    return (h.read(rid) if h else local_read(rid))[0]


def save(rec):
    h = hub_client()
    if h:
        h.put(rec)
    else:
        local_put(rec)


def update(rid, fn):
    """Load, change with fn(rec), save. Returns fn's result."""
    h = hub_client()
    if not h:
        with locked():
            rec = local_read(rid)[0]
            result = fn(rec)
            _write(rec)
            return result
    for _ in range(UPDATE_TRIES):
        rec, rev = h.read(rid)
        result = fn(rec)
        if h.put(rec, rev):
            return result
    raise StoreError(f"{rid} kept changing on the hub while this tried to change it; try again")


def all_records():
    h = hub_client()
    return h.records() if h else local_all()


def waiting_records():
    """Records with a message the agent hasn't been given yet."""
    h = hub_client()
    return h.records(waiting=True) if h else [r for r in local_all() if waiting(r)]


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
    {"reach", "live", "test", "since", "live_since"}, or None. live: its approve is the approver's yes."""
    h = hub_client()
    return h.deputy() if h else local_deputy()


def set_deputy(d):
    """d None: nobody is the deputy."""
    h = hub_client()
    if h:
        h.set_deputy(d)
    else:
        local_set_deputy(d)


def waiting(rec):
    return any(not i.get("delivered_at") and not i.get("seen_by_agent") for i in rec.get("outbox", []))


def event(rec, kind, **data):
    rec.setdefault("events", []).append(dict(kind=kind, at=iso(), **data))


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


def local_create(rec_without_id):
    with locked():
        rid = new_id()
        rec = dict(rec_without_id, id=rid)
        _write(rec)
        return rec


def local_read(rid):
    """(record, revision). Raises FileNotFoundError for a request that doesn't exist."""
    data = _path(rid).read_bytes()
    return json.loads(data), rev_of(data)


def local_put(rec, rev=None):
    """Write a record. Given the revision it was read at, only if nobody changed it since: returns the new
    revision, or None when it changed."""
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
    config.ensure_dirs()
    out = []
    for p in sorted(config.REQUESTS.glob(f"{config.settings()['id_prefix']}-*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def local_delete(rid):
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
    return _read_role(config.DEPUTY)


def local_set_deputy(d):
    _write_role(config.DEPUTY, d)
