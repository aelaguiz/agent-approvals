"""Who is asking: read from the calling agent's own environment."""
import os
from pathlib import Path

from . import config, store


def capture(agent_name=None):
    env = os.environ
    if env.get("CLAUDE_CODE_SESSION_ID"):
        runtime, sid = "claude", env["CLAUDE_CODE_SESSION_ID"]
    elif env.get("CODEX_THREAD_ID"):
        runtime, sid = "codex", env["CODEX_THREAD_ID"]
    else:
        runtime, sid = None, None
    cwd = os.getcwd()
    return {
        "agent": agent_name or env.get("APPROVALS_AGENT") or Path(cwd).name,
        "runtime": runtime,
        "session_id": sid,
        "cwd": cwd,
        "host": config.hostname(),
        "herdr_session": env.get("HERDR_SESSION"),
        "herdr_workspace": env.get("HERDR_WORKSPACE_ID"),
        "herdr_tab": env.get("HERDR_TAB_ID"),
        "herdr_pane": env.get("HERDR_PANE_ID"),
        "updated": store.iso(),
    }


def refresh(rec, new):
    """Keep reach current; remember every earlier session id."""
    old = rec.get("reach") or {}
    if not new.get("session_id"):
        return
    ids = list(old.get("earlier_ids", []))
    if old.get("session_id") and old["session_id"] != new["session_id"] and old["session_id"] not in ids:
        ids.append(old["session_id"])
    merged = dict(new, earlier_ids=ids)
    if not new.get("agent") and old.get("agent"):
        merged["agent"] = old["agent"]
    rec["reach"] = merged
