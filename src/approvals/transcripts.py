"""Finding agent transcripts and live Claude sessions on this Mac."""
import glob
import json
import os
import re
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


UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def opening(path, limit=400):
    """The first prompt and the agent's first reply. A copy AI Manager made of a session carries both word
    for word, except for session ids, which it rewrites everywhere; another run started from the same
    prompt writes its own first reply."""
    if not path or not os.path.exists(path):
        return None
    user = agent = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = e.get("payload") or {}
            if e.get("type") in ("user", "assistant"):            # Claude Code
                role, content, meta = e["type"], (e.get("message") or {}).get("content"), e.get("isMeta")
            elif p.get("type") == "message":                       # Codex
                role, content, meta = p.get("role"), p.get("content"), False
            else:
                continue
            if meta:
                continue
            text = (_text_of(content) if role != "assistant" or not isinstance(content, list)
                    else " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") in ("text", "output_text"))).strip()
            if isinstance(content, list) and role == "user" and not text:
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "input_text").strip()
            if not text or text.startswith("<"):
                continue
            text = UUID_RE.sub("<id>", " ".join(text.split()))
            if role == "user" and user is None:
                user = text[:limit]
            elif role == "assistant" and user is not None and agent is None:
                agent = text[:limit]
            if user and agent:
                return user, agent
    return None


SUMMARY_RE = re.compile(r"full transcript at:\s*`?(\S+?\.jsonl)")


def continues(path):
    """The session a continued Claude session picks up from, or None for a fresh start. When AI Manager
    carries work into a new session (after a usage limit, for one), the new session opens with a summary
    that names the transcript it continues."""
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
            if not e.get("isCompactSummary"):
                return None
            m = SUMMARY_RE.search(_text_of((e.get("message") or {}).get("content")))
            prev = Path(m.group(1)).stem if m else None
            return prev if prev and prev != Path(path).stem else None
    return None



def lineage(session_id, depth=12):
    """The Claude sessions this one continues, nearest first."""
    out, seen, sid = [], {session_id}, session_id
    for _ in range(depth):
        prev = continues(claude_transcript(sid))
        if not prev or prev in seen:
            break
        out.append(prev)
        seen.add(prev)
        sid = prev
    return out


def claude_copies(session_id, cwd, request_id=None):
    """The asker under other ids, newest first: moved copies and sessions that continue it, in the same
    work folder."""
    src = claude_transcript(session_id)
    if not src:
        return []
    src_mtime = os.path.getmtime(src)
    slug = cwd.replace("/", "-").replace(".", "-") if cwd else "*"
    found = []
    for d in claude_config_dirs():
        for p in glob.glob(str(d / "projects" / slug / "*.jsonl")):
            if Path(p).stem == session_id or os.path.getmtime(p) <= src_mtime:
                continue
            if is_copy("claude", session_id, Path(p).stem, request_id):
                found.append(p)
    found.sort(key=os.path.getmtime, reverse=True)
    return [Path(p).stem for p in found]


def _moved_copy(runtime, asker, candidate, request_id):
    """AI Manager rewrote the asker's ids when it moved it: same opening word for word, and it holds the request."""
    first = opening(transcript(runtime, asker))
    cand = transcript(runtime, candidate)
    return bool(first) and first == opening(cand) and (not request_id or contains(cand, request_id))


def is_copy(runtime, asker, candidate, request_id):
    """Is `candidate` the asker under another id? Two kinds qualify. A moved copy carries the asker's
    history word for word, so it opens with the same prompt and first reply and holds the request. A
    continuation opens with a summary naming the asker's transcript (or a moved copy of it). A later run
    of the same scheduled agent may start from the same prompt and even read the request number, but it
    writes its own first reply and continues nothing."""
    if not candidate or not request_id:
        return False
    if candidate == asker:
        return True
    if _moved_copy(runtime, asker, candidate, request_id):
        return True
    if runtime != "claude":
        return False
    return any(prev == asker or _moved_copy(runtime, asker, prev, request_id) for prev in lineage(candidate))


USAGE_STOP_RE = re.compile(r"hit your .*limit", re.I)
USAGE_MENU = "Switch to usage credits"


def stopped_for_usage(runtime, session_id):
    """Claude ends a turn with "You've hit your weekly limit · resets ..." when its account runs out.
    Until the work continues somewhere else, nothing typed into that session gets an answer."""
    path = transcript(runtime, session_id) if runtime == "claude" else None
    if not path or not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - 262144))
        lines = f.read().decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "assistant":
            content = (e.get("message") or {}).get("content")
            text = content if isinstance(content, str) else " ".join(
                c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
            return bool(e.get("isApiErrorMessage") and USAGE_STOP_RE.search(text))
    return False


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
