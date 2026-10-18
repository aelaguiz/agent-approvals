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


def claude_copies(session_id, cwd):
    """Other Claude transcripts for the same work folder that start with the same prompt, newest first."""
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
            if first_user_text(p) == first:
                found.append(p)
    found.sort(key=os.path.getmtime, reverse=True)
    return [Path(p).stem for p in found]
