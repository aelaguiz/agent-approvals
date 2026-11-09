"""Getting a message into the agent session that asked, resuming it through AI Manager when it's closed."""
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field

from . import config, transcripts

CONFIRM_SECONDS = 90
READY_SECONDS = 180
TRUST_PROMPT = "Yes, I trust this folder"


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

    def close(self, pane_id):
        try:
            self.call("pane", "close", pane_id)
        except HerdrError:
            pass

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
    status: str                      # delivered | retry | inbox | waiting (the session isn't running)
    reason: str = ""
    reach: dict = field(default_factory=dict)   # fields to update on the record's reach
    moves: list = field(default_factory=list)   # [old_id, new_id] pairs seen
    elsewhere: bool = False          # inbox because the session isn't on this machine; another machine may have it


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


def _start(cmd, **kw):
    return subprocess.Popen(cmd, **kw)


def ensure_herdr(session, run=subprocess.run, popen=None, sleep=time.sleep):
    """Start a headless Herdr server for a session that isn't running, with a clean environment so its shells load
    the user's own profile rather than inheriting this service's."""
    if session in herdr_sessions(run):
        return
    keep = ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG", "SSH_AUTH_SOCK")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(TERM="xterm-256color", HERDR_SESSION=session, HERDR_STARTUP_CWD=os.path.expanduser("~"))
    (popen or _start)(["herdr", "server"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(40):
        sleep(0.25)
        if session in herdr_sessions(run):
            return
    raise HerdrError(f"the Herdr session {session} did not start")


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


def live_askers(reach, request_id, run=subprocess.run):
    """Every running session that is the asker under some id (itself, a moved copy, or a session that
    continues it), as (session id, Herdr session, pane); the recorded pane first. Herdr session and pane
    are None for a session running outside Herdr."""
    runtime, sid, cwd = reach.get("runtime"), reach.get("session_id"), reach.get("cwd")
    h = Herdr(reach.get("herdr_session") or config.settings()["default_herdr_session"], run=run)
    out, seen = [], set()
    pane = reach.get("herdr_pane")
    if pane and h.pane(pane):
        found = session_in_pane(h, pane, runtime)
        if found and (found == sid or transcripts.is_copy(runtime, sid, found, request_id)):
            out.append((found, h.session, pane))
            seen.add(found)
        elif found is None and runtime == "codex" and _codex_in_pane(h, pane):
            return [(sid, h.session, pane)]   # Codex is in the recorded pane but its thread can't be read
    for pid, found, found_cwd in transcripts.live_sessions(runtime):
        if found in seen:
            continue
        if found == sid or ((found_cwd is None or found_cwd == cwd) and transcripts.is_copy(runtime, sid, found, request_id)):
            loc = find_pane_for_pid(pid, h.session, run=run)
            out.append((found, *(loc or (None, None))))
            seen.add(found)
    return out


def usage_menu_showing(h, pane):
    return transcripts.USAGE_MENU in h.read(pane, 15)


STOPPED = "the agent's session is stopped at its usage limit, and no session continuing its work can take this yet; trying again later"


def deliver(reach, text, ref, request_id=None, run=subprocess.run, sleep=time.sleep, clock=time.monotonic,
            popen=None, resume=True):
    """Type `text` into the session that asked. With resume=False a session that isn't running is left alone:
    the answer waits for it. Approvals answers that way; agent-email resumes, so its replies reach a closed session."""
    runtime, sid = reach.get("runtime"), reach.get("session_id")
    if runtime not in ("claude", "codex") or not sid:
        return Result("inbox", "it wasn't asked from a Claude or Codex session")
    # A session whose transcript is here is here. Without one, a card that names its machine was asked from somewhere
    # else, wherever the record says; an older card names only the Mac, whose reported name follows its network.
    if not transcripts.transcript(runtime, sid) and (reach.get("machine") or reach.get("host") != config.hostname()):
        return Result("inbox", f"that session isn't on {config.machine()}", elsewhere=True)
    if already_delivered(reach, ref):
        return Result("delivered", "already in the transcript")

    moves = []
    updates = {}
    live = live_askers(reach, request_id, run=run)
    working = [x for x in live if not transcripts.stopped_for_usage(runtime, x[0])]
    found, hs, pane = working[0] if working else (None, None, None)
    if found and found != sid:
        moves.append([sid, found])
        sid = found
    if found and not pane:
        return Result("retry", "the session is open outside Herdr, so it can't be typed into; trying again later", moves=moves)

    if pane:
        h = Herdr(hs, run=run)
        if usage_menu_showing(h, pane):
            return Result("retry", STOPPED, moves=moves)
        h.send(pane, text)
        if pane != reach.get("herdr_pane") or hs != reach.get("herdr_session"):
            updates = {"herdr_session": hs, "herdr_pane": pane, "herdr_workspace": pane.split(":")[0], "herdr_tab": None}
    else:
        if not resume:
            if live:   # running, but stopped at its usage limit
                return Result("retry", STOPPED, moves=moves)
            return Result("waiting", "its session isn't running", moves=moves)
        # The session it ran in, if that Herdr server is still up; else this tool's own, started if need be.
        default = config.settings()["default_herdr_session"]
        hs = reach.get("herdr_session")
        if not hs or hs not in herdr_sessions(run):
            hs = default
            try:
                ensure_herdr(hs, run=run, popen=popen, sleep=sleep)
            except HerdrError as e:
                return Result("retry", f"couldn't start Herdr: {e}", moves=moves)
        h = Herdr(hs, run=run)
        running = {x[0] for x in live}
        choices = (transcripts.claude_copies(sid, reach.get("cwd"), request_id) if runtime == "claude" else []) + [sid]
        target = next((c for c in choices if c not in running and not transcripts.stopped_for_usage(runtime, c)), None)
        if not target:
            return Result("retry", STOPPED, moves=moves)
        if target != sid:
            moves.append([sid, target])
        workspace = reach.get("herdr_workspace")
        prefix = reach.get("tab_label", "approvals")   # agent-email shares this code and names its own tabs
        label = f"{prefix}-{reach.get('agent', 'agent')}"[:40]
        cwd = reach.get("cwd") or str(config.HOME)
        try:
            if workspace and workspace in h.workspaces():
                pane, tab, workspace = h.new_tab(workspace, cwd, label)
            else:
                pane, tab, workspace = h.new_workspace(cwd, prefix)
        except HerdrError as e:
            h = Herdr(default, run=run)
            try:
                ensure_herdr(default, run=run, popen=popen, sleep=sleep)
                pane, tab, workspace = h.new_workspace(cwd, prefix)
            except HerdrError:
                return Result("retry", f"couldn't open a Herdr pane: {e}", moves=moves)
        resume = "aim claude resume" if runtime == "claude" else "aim codex resume"
        h.run_cmd(pane, f"cd {shlex.quote(cwd)} && {resume} {target}")
        start = clock()
        real = None
        while clock() - start < READY_SECONDS:
            sleep(3)
            if runtime == "claude":
                screen = h.read(pane, 15)
                if transcripts.USAGE_MENU in screen:
                    h.close(pane)   # the account is out of usage; this pane can't take the message
                    return Result("retry", STOPPED, moves=moves)
                if TRUST_PROMPT in screen:
                    # The session worked in this folder already, but a run with no prompt never answered this.
                    h.call("pane", "send-keys", pane, "Down", "Enter")
                    continue
                real = _claude_sid_in_pane(h, pane)
                if real and "bypass permissions" in screen:
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
