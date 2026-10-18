"""Paths, settings and Slack tokens.

Everything workspace-specific lives in ~/.approvals/config.json (see config.example.json).
APPROVALS_HOME points the whole tool at another folder, which the tests use.
"""
import json
import os
import socket
from pathlib import Path

HOME = Path(os.environ.get("APPROVALS_HOME", Path.home() / ".approvals"))
REQUESTS = HOME / "requests"
LOGS = HOME / "logs"
LOCK = HOME / "store.lock"
COUNTER = HOME / "next_id"
CONFIG = HOME / "config.json"

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
}


class ConfigError(RuntimeError):
    pass


def settings():
    s = dict(DEFAULTS)
    if CONFIG.exists():
        s.update(json.loads(CONFIG.read_text()))
    if os.environ.get("APPROVALS_CHANNEL"):
        s["channel"] = os.environ["APPROVALS_CHANNEL"]
    return s


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


def ensure_dirs():
    for d in (HOME, REQUESTS, LOGS):
        d.mkdir(parents=True, exist_ok=True)
