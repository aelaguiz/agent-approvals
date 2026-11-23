"""Paths, settings and Slack tokens.

Everything workspace-specific lives in ~/.approvals/config.json (see config.example.json). Another Slack
workspace gets its own folder inside it, ~/.approvals/<name>/, with its own config.json, records and service.
APPROVALS_HOME points the whole tool at one folder, which the services and the tests use.

A workspace whose settings name a `hub` keeps its records on that one machine; every other machine reaches them
at `hub_url`. ~/.approvals/machine.json names this machine, for every workspace.

Who decides, the team a card can be handed to and whether the team may answer any card are the hub's: from the pod
dashboard's record when the hub keeps its cards there (record_url), else from the hub's own settings. Every other
machine asks the hub for them. Each teammate's quiet and working hours stay in the settings of the machine that sends
the card to them, under its "team" entry with the same Slack user.
"""
import hashlib
import hmac
import json
import os
import socket
import threading
import time
from urllib.parse import urlparse
from pathlib import Path

ROOT = Path.home() / ".approvals"


def use(home):
    """Point the tool at one Slack workspace's folder: its settings, records and log."""
    global HOME, REQUESTS, LOGS, LOCK, COUNTER, CONFIG, REVIEWER, DEPUTY, FORKS, COMMENT_POSTS
    HOME = Path(home)
    REQUESTS = HOME / "requests"
    LOGS = HOME / "logs"
    LOCK = HOME / "store.lock"
    COUNTER = HOME / "next_id"
    CONFIG = HOME / "config.json"
    REVIEWER = HOME / "reviewer.json"
    DEPUTY = HOME / "deputy.json"
    FORKS = HOME / "forks.json"
    COMMENT_POSTS = HOME / "comment_posts.json"   # a dashboard comment posted in Slack and not yet written (store)


use(os.environ.get("APPROVALS_HOME", ROOT))

REQUIRED = ["bot_token_file", "app_token_file"]
# Who may answer, as the hub holds them: the record's lists, or the hub's own settings.
LISTS = ("channel", "approver", "approver_name", "approvers", "team", "team_answers_any")
LISTS_SECONDS = 15
SERVING = False   # this process serves the hub (hub.serve), so it holds the lists, never asks for them
_lists = {}
_lists_lock = threading.Lock()

DEFAULTS = {
    "approver_name": "the approver",
    "team": {},
    "team_answers_any": False,
    "handoff_hours": 3,
    "reader_rules": None,          # Markdown file, optionally #heading, for the card reader
    "reader_knows": None,          # what the card reader should assume the approver knows
    "bot_token_key": "SLACK_BOT_TOKEN",
    "app_token_key": "SLACK_APP_TOKEN",
    "timezone": "UTC",
    "default_herdr_session": "default",
    "video_share_command": None,   # e.g. ["bash", "/path/to/cf_share.sh"]; must print "URL: <url>"
    "page_share_command": None,    # a command that publishes a folder and prints "URL: <url>"
    "page_slug_flag": None,        # e.g. "--slug"; the publisher keeps the group page at the same URL
    "test_approver": None,         # a Slack user id whose "[test reply]" posts count, on TEST cards only
    "alarm": None,                 # channel and command for delivery failures
    "sweep_seconds": 30,
    "id_prefix": "AP",             # request numbers, AP-0001; each workspace needs its own
    "folders": [],                 # agents working in these folders use this workspace
    "hub": None,                   # the machine that holds the records and the Slack connection; None: this one
    "hub_url": None,               # where the other machines reach it, e.g. "http://hub-machine:8790"
}


class ConfigError(RuntimeError):
    pass


def sender(value, label="post_as"):
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"].strip() or len(value["name"]) > 80:
        raise ConfigError(f"{label} needs a nonempty name of at most 80 characters")
    url = value.get("icon_url")
    if not isinstance(url, str) or urlparse(url).scheme != "https" or not urlparse(url).netloc:
        raise ConfigError(f"{label} needs an https icon_url")
    return {"name": value["name"], "icon_url": url}


def folder_sender(cwd=None):
    here = Path(cwd or os.getcwd()).resolve()
    for folder in (here, *here.parents):
        path = folder / ".approvals.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text())
                if not isinstance(data, dict):
                    raise ValueError("expected an object")
                return sender(data["post_as"]) if "post_as" in data else None
            except (ValueError, OSError) as e:
                raise ConfigError(f"{path}: {e}") from e
    return None


def file_settings(home=None):
    """This machine's own settings for the workspace, as its config.json has them."""
    s = dict(DEFAULTS)
    path = Path(home) / "config.json" if home else CONFIG
    if path.exists():
        s.update(json.loads(path.read_text()))
    return s


def settings(home=None):
    """The workspace's settings, with who may answer as its hub holds them (another workspace's folder, named by
    home, is only this machine's file)."""
    s = file_settings(home)
    if home is None:
        held = lists()
        if held is not None:
            hours = {p.get("user"): p for p in (s.get("team") or {}).values() if isinstance(p, dict)}
            s.update({k: held[k] for k in LISTS if k != "team"})
            s["team"] = {key: dict(hours.get(held["team"][key]["user"], {}), **held["team"][key])
                         for key in held.get("team_order") or held["team"]}
    if os.environ.get("APPROVALS_CHANNEL"):
        s["channel"] = os.environ["APPROVALS_CHANNEL"]
    return s


def lists():
    """Who may answer as the hub holds them, read again every LISTS_SECONDS (forget_lists reads them at once): from
    the record on a hub that keeps its cards there, from the hub on any other machine, and None on a hub that keeps
    them in its own settings."""
    from . import hub, store
    record = store.record()
    if record is None and (is_hub() or SERVING):
        return None
    key = str(HOME)
    with _lists_lock:
        at, held = _lists.get(key, (None, None))
        if at is None or time.monotonic() - at >= LISTS_SECONDS:
            held = record.who() if record else hub.Client().who()
            _lists[key] = (time.monotonic(), held)
        return held


def forget_lists():
    with _lists_lock:
        _lists.pop(str(HOME), None)


def target(name=None):
    """Resolve a card's target. Old records without a target use the workspace default."""
    s = settings()
    if name is None:
        return {k: s[k] for k in ("channel", "approver", "approver_name")}
    targets = s.get("approvers") or {}
    if name not in targets:
        names = ", ".join(sorted(targets)) or "none"
        raise ConfigError(f"Unknown approver {name!r}; configured names: {names}")
    return targets[name]


def card_target(rec):
    return target(rec.get("target"))


def channel_args(rec):
    channel = card_target(rec)["channel"]
    return {"channel_id": channel} if rec.get("target") is not None else {}


def targets():
    return {None: target(), **{name: target(name) for name in (settings().get("approvers") or {})}}


def homes():
    """The main workspace's folder, then each workspace folder inside it."""
    return [ROOT] + sorted(p.parent for p in ROOT.glob("*/config.json"))


def choose(request_id=None, cwd=None):
    """The folder of the workspace a command is for: the one whose numbers the request carries, else the one
    whose folders hold the agent's working folder, else the main one."""
    found = [(h, settings(h)) for h in homes()]
    if request_id:
        prefix = request_id.split("-")[0]
        for h, s in found:
            if s["id_prefix"] == prefix:
                return h
        raise ConfigError(f"no Slack workspace on this machine numbers its requests {prefix}-")
    here = Path(os.path.realpath(cwd or os.getcwd()))
    for h, s in found:
        for f in s["folders"]:
            f = Path(os.path.realpath(os.path.expanduser(f)))
            if here == f or f in here.parents:
                return h
    return ROOT


def missing():
    """What this machine's settings lack: the token files, and who decides on a hub that keeps him in its settings."""
    s = file_settings()
    from . import store
    own = ["channel", "approver"] if is_hub() and not store.record() else []
    return [k for k in REQUIRED + own if not s.get(k)]


def _tokens():
    gaps = [k for k in REQUIRED if not file_settings().get(k)]
    if gaps:
        raise ConfigError(f"{CONFIG} needs: {', '.join(gaps)} (see config.example.json)")
    return file_settings()


def require():
    gaps = missing()
    if gaps:
        raise ConfigError(f"{CONFIG} needs: {', '.join(gaps)} (see config.example.json)")
    return settings()


def test_approver():
    return os.environ.get("APPROVALS_TEST_APPROVER") or settings().get("test_approver")


def is_test_run():
    return os.environ.get("APPROVALS_TEST") == "1"


def _read_env_file(path, key):
    path = Path(os.path.expanduser(path))
    if not path.exists():
        raise ConfigError(f"token file not found: {path}")
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ConfigError(f"{key} not found in {path}")


def bot_token():
    if os.environ.get("SLACK_BOT_TOKEN"):
        return os.environ["SLACK_BOT_TOKEN"]
    s = _tokens()
    return _read_env_file(s["bot_token_file"], s["bot_token_key"])


def app_token():
    if os.environ.get("SLACK_APP_TOKEN"):
        return os.environ["SLACK_APP_TOKEN"]
    s = _tokens()
    return _read_env_file(s["app_token_file"], s["app_token_key"])


def record_token():
    """The token the pod dashboard's record takes from the hub: record_token_file/record_token_key, read from the
    secrets checkout like the Slack tokens, or a raw record_token in this machine's own settings. Read from the file
    alone: the full settings ask the record who may answer, which needs this token."""
    s = file_settings()
    if s.get("record_token_file"):
        return _read_env_file(s["record_token_file"], s.get("record_token_key", "APPROVALS_RECORD_TOKEN"))
    if s.get("record_token"):
        return s["record_token"]
    raise ConfigError("record_url is set but neither record_token_file nor record_token is")


def hostname():
    return socket.gethostname().split(".")[0]


def machine():
    """This machine's name as the fleet knows it (`make machine_file MACHINE=<name>`). A Mac's own name follows its
    network, so it is only the fallback."""
    if os.environ.get("APPROVALS_MACHINE"):
        return os.environ["APPROVALS_MACHINE"]
    try:
        return json.loads((ROOT / "machine.json").read_text())["name"]
    except (OSError, ValueError, KeyError):
        return hostname()


def hub():
    """The machine holding this workspace's records: the one named in its settings, else this one."""
    return file_settings().get("hub") or machine()


def is_hub():
    return hub() == machine()


def hub_url():
    url = file_settings().get("hub_url")
    if not url:
        raise ConfigError(f"{CONFIG} names {hub()} as the hub but gives no hub_url")
    return url.rstrip("/")


def hub_token():
    """Every machine that can post as the bot may use the hub, so the bot token is the shared secret, without
    sending it: the hub and its callers both derive this from it."""
    return hmac.new(bot_token().encode(), b"agent-approvals hub", hashlib.sha256).hexdigest()


def ensure_dirs():
    for d in (HOME, REQUESTS, LOGS):
        d.mkdir(parents=True, exist_ok=True)
