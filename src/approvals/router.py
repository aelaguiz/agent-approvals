"""Getting a message into the agent session that asked, resuming it through AI Manager when it's closed."""
import json
import shlex
import subprocess
import time
from dataclasses import dataclass, field

from . import config, transcripts

CONFIRM_SECONDS = 90
READY_SECONDS = 180


class HerdrError(RuntimeError):
    pass


class Herdr:
    """Every call names the Herdr session explicitly; launchd has no Herdr variables."""

    def __init__(self, session, run=subprocess.run):
        self.session = session
        self.run = run

    def call(self, *args, timeout=30):
        cmd = ["herdr", "--session", self.session, *args]
        r = self.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise HerdrError((r.stderr or r.stdout or "").strip()[:400])
        out = (r.stdout or "").strip()
        try:
            return json.loads(out) if out else {}
        except json.JSONDecodeError:
            return {"raw": out}

    def pane(self, pane_id):
        try:
            return self.call("pane", "get", pane_id)["result"]["pane"]
        except (HerdrError, KeyError):
            return None

    def procs(self, pane_id):
        try:
            return self.call("pane", "process-info", "--pane", pane_id)["result"]["process_info"]["foreground_processes"]
        except (HerdrError, KeyError):
            return []

    def workspaces(self):
        try:
            return [w["workspace_id"] for w in self.call("workspace", "list")["result"]["workspaces"]]
        except (HerdrError, KeyError):
            return []

    def new_tab(self, workspace, cwd, label):
        r = self.call("tab", "create", "--workspace", workspace, "--cwd", cwd, "--label", label, "--no-focus")["result"]
        return r["root_pane"]["pane_id"], r["tab"]["tab_id"], workspace

    def new_workspace(self, cwd, label):
        r = self.call("workspace", "create", "--cwd", cwd, "--label", label, "--no-focus")["result"]
        return r["root_pane"]["pane_id"], r["tab"]["tab_id"], r["workspace"]["workspace_id"]

    def run_cmd(self, pane_id, text):
        self.call("pane", "run", pane_id, text)

    def read(self, pane_id, lines=40):
        try:
            r = self.call("pane", "read", pane_id, "--source", "recent-unwrapped", "--lines", str(lines))
            if "raw" in r:          # pane read prints the screen text, not JSON
                return r["raw"]
            res = r.get("result", {})
            return res.get("text") or res.get("content") or json.dumps(res)
        except HerdrError:
            return ""

    def agent_status(self, pane_id):
        p = self.pane(pane_id)
        return (p or {}).get("agent_status", "unknown")

    def send(self, pane_id, text):
        """Type one line into the agent and press Enter."""
        try:
            self.call("agent", "prompt", pane_id, text, timeout=60)
        except HerdrError as e:
            if "agent_blocked" in str(e):
                raise
            self.run_cmd(pane_id, text)


@dataclass
class Result:
    status: str                      # delivered | retry | inbox
    reason: str = ""
    reach: dict = field(default_factory=dict)   # fields to update on the record's reach
    moves: list = field(default_factory=list)   # [old_id, new_id] pairs seen


def _claude_sid_in_pane(h, pane):
    for p in h.procs(pane):
        sid = transcripts.claude_session_for_pid(p.get("pid"))
        if sid:
            return sid
    return None


def _codex_in_pane(h, pane):
    return any("codex" in (p.get("cmdline") or "") for p in h.procs(pane))


def _codex_sid_in_pane(h, pane):
    for p in h.procs(pane):
        cmd = p.get("cmdline") or ""
        if "/codex" in cmd and "vendor" in cmd:
            sid = transcripts.codex_session_for_pid(p.get("pid"))
            if sid:
                return sid
    return None


def session_in_pane(h, pane, runtime):
    return _claude_sid_in_pane(h, pane) if runtime == "claude" else _codex_sid_in_pane(h, pane)


def herdr_sessions(run=subprocess.run):
    """Names of the running Herdr servers."""
    try:
        out = run(["herdr", "session", "list"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[0] for line in out.splitlines()[1:] if len(line.split()) > 1 and line.split()[1] == "running"]


def find_pane_for_pid(pid, first_session=None, run=subprocess.run):
    """(Herdr session, pane id) whose foreground processes include pid, or None."""
    names = herdr_sessions(run)
    if first_session in names:
        names.remove(first_session)
        names.insert(0, first_session)
    for name in names:
        h = Herdr(name, run=run)
        try:
            panes = h.call("pane", "list")["result"]["panes"]
        except (HerdrError, KeyError):
            continue
        for pane in panes:
            if any(p.get("pid") == pid for p in h.procs(pane["pane_id"])):
                return name, pane["pane_id"]
    return None


def already_delivered(reach, ref):
    runtime = reach.get("runtime")
    ids = [reach.get("session_id")] + list(reach.get("earlier_ids", []))
    return any(transcripts.contains(transcripts.transcript(runtime, i), ref) for i in ids if i)


def find_live_asker(reach, request_id, run=subprocess.run):
    """Where the asker (or a moved copy of it) is running right now: (session id, Herdr session, pane).

    Returns (None, None, None) if it isn't running, or (sid, None, None) if it's running outside Herdr.
    """
    runtime, sid, cwd = reach.get("runtime"), reach.get("session_id"), reach.get("cwd")
    h = Herdr(reach.get("herdr_session") or config.settings()["default_herdr_session"], run=run)
    pane = reach.get("herdr_pane")
    if pane and h.pane(pane):
        found = session_in_pane(h, pane, runtime)
        if found and (found == sid or transcripts.is_copy(runtime, sid, found, request_id)):
            return found, h.session, pane
        if found is None and runtime == "codex" and _codex_in_pane(h, pane):
            return sid, h.session, pane   # Codex is in the recorded pane but its thread can't be read
    for pid, found, found_cwd in transcripts.live_sessions(runtime):
        if found == sid or ((found_cwd is None or found_cwd == cwd) and transcripts.is_copy(runtime, sid, found, request_id)):
            loc = find_pane_for_pid(pid, h.session, run=run)
            return (found, loc[0], loc[1]) if loc else (found, None, None)
    return None, None, None


def deliver(reach, text, ref, request_id=None, run=subprocess.run, sleep=time.sleep, clock=time.monotonic):
    runtime, sid = reach.get("runtime"), reach.get("session_id")
    if reach.get("host") != config.hostname() or runtime not in ("claude", "codex") or not sid:
        return Result("inbox", "that session isn't a Claude or Codex session on this Mac")
    if already_delivered(reach, ref):
        return Result("delivered", "already in the transcript")

    moves = []
    updates = {}
    found, hs, pane = find_live_asker(reach, request_id, run=run)
    if found and found != sid:
        moves.append([sid, found])
        sid = found
    if found and not pane:
        return Result("retry", "the session is open outside Herdr, so it can't be typed into; trying again later", moves=moves)

    if pane:
        h = Herdr(hs, run=run)
        h.send(pane, text)
        if pane != reach.get("herdr_pane") or hs != reach.get("herdr_session"):
            updates = {"herdr_session": hs, "herdr_pane": pane, "herdr_workspace": pane.split(":")[0], "herdr_tab": None}
    else:
        h = Herdr(reach.get("herdr_session") or config.settings()["default_herdr_session"], run=run)
        target = sid
        if runtime == "claude":
            copies = transcripts.claude_copies(sid, reach.get("cwd"), request_id)
            if copies:
                moves.append([sid, copies[0]])
                target = copies[0]
        workspace = reach.get("herdr_workspace")
        label = f"approvals-{reach.get('agent', 'agent')}"[:40]
        cwd = reach.get("cwd") or str(config.HOME)
        try:
            if workspace and workspace in h.workspaces():
                pane, tab, workspace = h.new_tab(workspace, cwd, label)
            else:
                pane, tab, workspace = h.new_workspace(cwd, "approvals")
        except HerdrError as e:
            h = Herdr(config.settings()["default_herdr_session"], run=run)
            try:
                pane, tab, workspace = h.new_workspace(cwd, "approvals")
            except HerdrError:
                return Result("retry", f"couldn't open a Herdr pane: {e}", moves=moves)
        resume = "aim claude resume" if runtime == "claude" else "aim codex resume"
        h.run_cmd(pane, f"cd {shlex.quote(cwd)} && {resume} {target}")
        start = clock()
        real = None
        while clock() - start < READY_SECONDS:
            sleep(3)
            if runtime == "claude":
                real = _claude_sid_in_pane(h, pane)
                if real and "bypass permissions" in h.read(pane, 15):
                    break
            else:
                # Herdr often can't classify Codex; its idle input screen is the reliable sign.
                screen = h.read(pane, 15)
                if _codex_in_pane(h, pane) and ("for shortcuts" in screen or "context left" in screen):
                    real = target
                    break
        else:
            return Result("retry", "the resumed session didn't come up: " + h.read(pane, 8)[-300:], moves=moves,
                          reach={"herdr_session": h.session, "herdr_pane": pane, "herdr_tab": tab, "herdr_workspace": workspace})
        if real and real != target:
            moves.append([target, real])
        sid = real or target
        sleep(2)
        h.send(pane, text)
        updates = {"herdr_session": h.session, "herdr_pane": pane, "herdr_tab": tab, "herdr_workspace": workspace}

    updates["session_id"] = sid
    start = clock()
    while clock() - start < CONFIRM_SECONDS:
        if transcripts.contains(transcripts.transcript(runtime, sid), ref):
            return Result("delivered", "seen in the transcript", reach=updates, moves=moves)
        sleep(3)
    return Result("retry", "sent, but not seen in the transcript yet", reach=updates, moves=moves)
