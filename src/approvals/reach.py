"""Who is asking: read from the calling agent's own environment."""
import os
from pathlib import Path

from . import config, store, transcripts


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
    """Keep reach current as the asker moves; remember every earlier session id.

    Only the asker itself, or a moved copy of it, may update where answers go. Another session running a
    command on this request (a different agent, a later run of the same scheduled agent, a sub-agent) is
    noted on the record but doesn't take over its answers.
    """
    old = rec.get("reach") or {}
    if not new.get("session_id"):
        return
    if old.get("session_id") and new["session_id"] != old["session_id"]:
        if new.get("runtime") != old.get("runtime") or not transcripts.is_copy(
                old["runtime"], old["session_id"], new["session_id"], rec.get("id")):
            store.event(rec, "used_by_other_session", session_id=new["session_id"], agent=new.get("agent"))
            return
    ids = list(old.get("earlier_ids", []))
    if old.get("session_id") and old["session_id"] != new["session_id"] and old["session_id"] not in ids:
        ids.append(old["session_id"])
    merged = dict(new, earlier_ids=ids)
    if not new.get("agent") and old.get("agent"):
        merged["agent"] = old["agent"]
    rec["reach"] = merged
