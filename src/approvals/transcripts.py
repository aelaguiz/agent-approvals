"""Finding agent transcripts and live Claude sessions on this Mac."""
import glob
import json
import os
from pathlib import Path

HOME = Path.home()


def claude_config_dirs():
    dirs = [HOME / ".claude"] + [Path(p) for p in glob.glob(str(HOME / ".aimgr/claude-homes/*/.claude"))]
    return [d for d in dirs if d.is_dir()]


def claude_transcript(session_id):
    """Newest file for a Claude session id across every account home."""
    hits = []
    for d in claude_config_dirs():
        hits += glob.glob(str(d / "projects" / "*" / f"{session_id}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def codex_transcript(thread_id):
    hits = glob.glob(str(HOME / ".codex/sessions/*/*/*" / f"rollout-*{thread_id}.jsonl"))
    return max(hits, key=os.path.getmtime) if hits else None


def transcript(runtime, session_id):
    if not session_id:
        return None
    return claude_transcript(session_id) if runtime == "claude" else codex_transcript(session_id)


def contains(path, needle):
    if not path or not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        return needle.encode() in f.read()


def claude_session_for_pid(pid):
    """Claude writes <config dir>/sessions/<pid>.json naming the session the process is running."""
    for d in claude_config_dirs():
        p = d / "sessions" / f"{pid}.json"
        if p.exists():
            try:
                rec = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if rec.get("pid") == pid and rec.get("sessionId"):
                return rec["sessionId"].lower()
    return None


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def first_user_text(path, limit=400):
    """The first real prompt in a Claude transcript; AI Manager keeps it when it copies a session."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") != "user" or e.get("isMeta"):
                continue
            text = _text_of((e.get("message") or {}).get("content")).strip()
            if text and not text.startswith("<"):
                return " ".join(text.split())[:limit]
    return None


def claude_copies(session_id, cwd, request_id=None):
    """Moved copies of a Claude session, newest first: same work folder, same first prompt, and (when a
    request number is given) that request in their history."""
    src = claude_transcript(session_id)
    first = first_user_text(src)
    if not first:
        return []
    src_mtime = os.path.getmtime(src)
    slug = cwd.replace("/", "-").replace(".", "-") if cwd else "*"
    found = []
    for d in claude_config_dirs():
        for p in glob.glob(str(d / "projects" / slug / "*.jsonl")):
            if Path(p).stem == session_id or os.path.getmtime(p) <= src_mtime:
                continue
            if first_user_text(p) == first and (not request_id or contains(p, request_id)):
                found.append(p)
    found.sort(key=os.path.getmtime, reverse=True)
    return [Path(p).stem for p in found]


def is_copy(runtime, asker, candidate, request_id):
    """Is `candidate` the asker under another id? Only a moved copy qualifies: it carries the asker's
    history, so it holds the request number, and (for Claude) it starts with the same first prompt.
    A later run of the same scheduled agent, or another agent in the same folder, may start the same
    way but never made this request."""
    if not candidate or not request_id:
        return False
    if candidate == asker:
        return True
    cand = transcript(runtime, candidate)
    if not contains(cand, request_id):
        return False
    if runtime == "claude":
        first = first_user_text(claude_transcript(asker))
        return bool(first) and first == first_user_text(cand)
    return True


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def live_claude_sessions():
    """(pid, session id, cwd) for every running Claude process, from its per-process record."""
    out = []
    for d in claude_config_dirs():
        for p in glob.glob(str(d / "sessions" / "*.json")):
            try:
                rec = json.loads(Path(p).read_text())
            except (json.JSONDecodeError, OSError):
                continue
            pid, sid = rec.get("pid"), (rec.get("sessionId") or "").lower()
            if sid and _alive(pid):
                out.append((pid, sid, rec.get("cwd")))
    return out


def codex_session_for_pid(pid):
    """The Codex thread a process is writing, from the session file it holds open."""
    import re
    import subprocess
    try:
        out = subprocess.run(["lsof", "-p", str(pid), "-Fn"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"rollout-[^/\n]*?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl", out)
    return m.group(1) if m else None


def live_codex_sessions():
    import subprocess
    out = []
    ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    for line in ps.splitlines():
        pid_s, _, cmd = line.strip().partition(" ")
        if "/codex" in cmd and "vendor" in cmd:   # the native Codex binary, not its node wrapper
            sid = codex_session_for_pid(int(pid_s))
            if sid:
                out.append((int(pid_s), sid, None))
    return out


def live_sessions(runtime):
    return live_claude_sessions() if runtime == "claude" else live_codex_sessions()
