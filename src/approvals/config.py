"""Paths, settings and Slack tokens.

Everything workspace-specific lives in ~/.approvals/config.json (see config.example.json). Another Slack
workspace gets its own folder inside it, ~/.approvals/<name>/, with its own config.json, records and service.
APPROVALS_HOME points the whole tool at one folder, which the services and the tests use.

A workspace whose settings name a `hub` keeps its records on that one machine; every other machine reaches them
at `hub_url`. ~/.approvals/machine.json names this machine, for every workspace.
"""
import hashlib
import hmac
import json
import os
import socket
from urllib.parse import urlparse
from pathlib import Path

ROOT = Path.home() / ".approvals"


def use(home):
    """Point the tool at one Slack workspace's folder: its settings, records and log."""
    global HOME, REQUESTS, LOGS, LOCK, COUNTER, CONFIG, REVIEWER, DEPUTY, FORKS
    HOME = Path(home)
    REQUESTS = HOME / "requests"
    LOGS = HOME / "logs"
    LOCK = HOME / "store.lock"
    COUNTER = HOME / "next_id"
    CONFIG = HOME / "config.json"
    REVIEWER = HOME / "reviewer.json"
    DEPUTY = HOME / "deputy.json"
    FORKS = HOME / "forks.json"


use(os.environ.get("APPROVALS_HOME", ROOT))

REQUIRED = ["channel", "approver", "bot_token_file", "app_token_file"]

DEFAULTS = {
    "approver_name": "the approver",
    "bot_token_key": "SLACK_BOT_TOKEN",
    "app_token_key": "SLACK_APP_TOKEN",
    "timezone": "UTC",
    "default_herdr_session": "default",
    "video_share_command": None,   # e.g. ["bash", "/path/to/cf_share.sh"]; must print "URL: <url>"
    "test_approver": None,         # a Slack user id whose "[test reply]" posts count, on TEST cards only
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


def settings(home=None):
    s = dict(DEFAULTS)
    path = Path(home) / "config.json" if home else CONFIG
    if path.exists():
        s.update(json.loads(path.read_text()))
    if os.environ.get("APPROVALS_CHANNEL"):
        s["channel"] = os.environ["APPROVALS_CHANNEL"]
    return s


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
    s = settings()
    return [k for k in REQUIRED if not s.get(k)]


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
    s = require()
    return _read_env_file(s["bot_token_file"], s["bot_token_key"])


def app_token():
    if os.environ.get("SLACK_APP_TOKEN"):
        return os.environ["SLACK_APP_TOKEN"]
    s = require()
    return _read_env_file(s["app_token_file"], s["app_token_key"])


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
    return settings().get("hub") or machine()


def is_hub():
    return hub() == machine()


def hub_url():
    url = settings().get("hub_url")
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
