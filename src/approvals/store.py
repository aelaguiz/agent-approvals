"""Request records: one JSON file per request, written under a lock."""
import contextlib
import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone

from . import config

STATUSES = {"pending", "question", "revising", "approved", "declined", "expired", "done"}
OPEN = {"pending", "question", "revising", "expired", "approved"}  # threads still watched for replies


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).isoformat(timespec="seconds")


def parse(s):
    return datetime.fromisoformat(s)


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


def _write(rec):
    fd, tmp = tempfile.mkstemp(dir=config.REQUESTS, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
    os.replace(tmp, _path(rec["id"]))


def new_id():
    """Caller must hold the lock."""
    n = int(config.COUNTER.read_text()) if config.COUNTER.exists() else 1
    config.COUNTER.write_text(str(n + 1))
    return f"AP-{n:04d}"


def create(rec_without_id):
    with locked():
        rid = new_id()
        rec = dict(rec_without_id, id=rid)
        _write(rec)
        return rec


def load(rid):
    return json.loads(_path(rid).read_text())


def save(rec):
    with locked():
        _write(rec)


def update(rid, fn):
    """Load, change with fn(rec), save, all under the lock. Returns fn's result."""
    with locked():
        rec = load(rid)
        result = fn(rec)
        _write(rec)
        return result


def all_records():
    config.ensure_dirs()
    out = []
    for p in sorted(config.REQUESTS.glob("AP-*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def event(rec, kind, **data):
    rec.setdefault("events", []).append(dict(kind=kind, at=iso(), **data))
