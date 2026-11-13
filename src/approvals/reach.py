"""Who is asking: read from the calling agent's own environment."""
import os
from pathlib import Path

from . import config, router, store, transcripts


def capture(agent_name=None):
    """APPROVALS_FROM is set by whatever starts the agent (a team's launcher) and wins over the name the agent gives,
    so every card and reply says which team and member sent it, however the agent signs itself."""
    env = os.environ
    if env.get("CLAUDE_CODE_SESSION_ID"):
        runtime, sid = "claude", env["CLAUDE_CODE_SESSION_ID"]
    elif env.get("CODEX_THREAD_ID"):
        runtime, sid = "codex", env["CODEX_THREAD_ID"]
    else:
        runtime, sid = None, None
    cwd = os.getcwd()
    return {
        "agent": env.get("APPROVALS_FROM") or agent_name or env.get("APPROVALS_AGENT") or Path(cwd).name,
        "runtime": runtime,
        "session_id": sid,
        "cwd": cwd,
        "host": config.hostname(),
        "machine": config.machine(),
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


def running(session_id):
    """Where a session running on this machine is, as reach, or None when it isn't running here: its runtime, folder,
    and Herdr pane when it runs in one."""
    for runtime in ("claude", "codex"):
        for pid, sid, cwd in transcripts.live_sessions(runtime):
            if sid == session_id:
                loc = router.find_pane_for_pid(pid)
                hs, pane = loc or (None, None)
                return {"runtime": runtime, "session_id": sid, "cwd": cwd, "host": config.hostname(),
                        "machine": config.machine(), "herdr_session": hs, "herdr_pane": pane,
                        "herdr_workspace": pane.split(":")[0] if pane else None, "herdr_tab": None,
                        "updated": store.iso()}
    return None


def take_over(rec, old, new):
    """`new` (running(), above) takes the record's answers from session `old`, which no longer runs: a pod restored
    from a save starts each member in a new session that carries none of the old one's history, so nothing else can
    tell they are the same agent. Returns False when the record's answers don't go to `old`."""
    r = rec.get("reach") or {}
    if r.get("session_id") != old or not store.same_family(old, new["session_id"]):
        return False
    ids = [i for i in r.get("earlier_ids", []) if i != new["session_id"]]
    if old not in ids:
        ids.append(old)
    rec["reach"] = dict(new, agent=r.get("agent"), cwd=new.get("cwd") or r.get("cwd"), earlier_ids=ids)
    rec.setdefault("moves", []).append([old, new["session_id"]])
    store.event(rec, "taken_over", old=old, new=new["session_id"])
    return True
