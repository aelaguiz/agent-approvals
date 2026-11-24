"""Which session a pod member is now. A pod member is its place: the tab labelled for it in its pod's workspace, with a
session running in its folder. Whatever starts it again (its pod, the agent watcher's move to another account, Amir by
hand, a fresh session typed into the tab), the session in that tab is the member, so its answers go there. Its session
id and its name prove nothing: a chat opened in the member's folder somewhere else is not the member, and two copies of
one role share a folder and a name. The rule is the pods' own `./pod` (its copy_tabs), read here from `./pod list`, so
it is kept in one place; nothing here decides who a member is.

A seat is how a card, a role or a sent email names the member: {"pod_command", "herdr_session", "pod", "copy"}, the
copy being the tab's label (writer, writer-2)."""
import functools
import os
import re
import subprocess
from pathlib import Path

from . import transcripts

POD = re.compile(r"(\S+) pod: ")
# `./pod list`'s line for a copy running in its tab: "  writer  ...  running in w1:p2, ..., on pro3, session <id>, idle"
RUNNING = re.compile(r"  (\S+)\s.*\brunning in ([^\s,]+).*, session ([^\s,]+), ")


class Unreadable(RuntimeError):
    """`./pod` couldn't say who runs in the pod's tabs (its Herdr session down, say): nobody is known, not gone."""


def command(folder):
    """(the ./pod that runs it, pod, member folder) for a folder inside a pod member's folder (<root>/pods/<pod>/<member>),
    else None."""
    try:
        p = Path(folder).resolve()
    except (TypeError, OSError):
        return None
    for d in (p, *p.parents):
        if d.parent.parent.name == "pods" and os.access(d.parent.parent.parent / "pod", os.X_OK):
            return str(d.parent.parent.parent / "pod"), d.parent.name, str(d)
    return None


def listed(pod_command, herdr_session=None):
    """{(pod, copy): (session id or None, pane)} for every copy `./pod list` shows running on this machine, in roster
    order. Raises Unreadable when it can't read them."""
    env = dict(os.environ, POD_HERDR_SESSION=herdr_session) if herdr_session else None
    try:
        r = subprocess.run([pod_command, "list"], capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise Unreadable(str(e)) from e
    if r.returncode:
        raise Unreadable((r.stderr or r.stdout).strip()[-300:])
    out, pod = {}, None
    for line in r.stdout.splitlines():
        if m := POD.match(line):
            pod = m.group(1)
        elif pod and (m := RUNNING.match(line)):
            sid = m.group(3).lower()
            out[(pod, m.group(1))] = (None if sid == "unknown" else sid, m.group(2))
    return out


def same(a, b):
    return bool(a and b) and (a["pod"], a["copy"]) == (b["pod"], b["copy"])


def name(seat):
    """The name the pods give the member's session, and the pod dashboard knows it by: <pod>-<copy>."""
    return f"{seat['pod']}-{seat['copy']}" if seat else None


@functools.cache
def here():
    """The calling session's seat when it is a pod member running in its own tab, else None. Read once per command."""
    env = os.environ
    sid = env.get("CLAUDE_CODE_SESSION_ID") or env.get("CODEX_THREAD_ID") or env.get("ANTIGRAVITY_CONVERSATION_ID")
    pane = env.get("HERDR_PANE_ID")
    if not sid or not pane:
        return None
    # The shell may be in a work folder; the folder the session started in is the member's.
    found = command(os.getcwd()) or command(next((cwd for runtime in ("claude", "codex", "agy")
                                                  for _, s, cwd in transcripts.live_sessions(runtime)
                                                  if s == sid.lower()), None))
    if not found:
        return None
    cmd, pod, _ = found
    try:
        rows = listed(cmd, env.get("HERDR_SESSION"))
    except Unreadable:
        return None
    return next(({"pod_command": cmd, "herdr_session": env.get("HERDR_SESSION"), "pod": p, "copy": copy}
                 for (p, copy), (s, pn) in rows.items() if p == pod and pn == pane and s in (sid.lower(), None)), None)


def of(reach):
    """The seat answers to `reach` go to: the one it recorded, or for one recorded before seats were, the copy whose tab
    holds the pane it was recorded in, now. None for a session that isn't a pod member."""
    if reach.get("seat"):
        return reach["seat"]
    found, pane = command(reach.get("cwd")), reach.get("herdr_pane")
    if not found or not pane:
        return None
    try:
        rows = listed(found[0], reach.get("herdr_session"))
    except Unreadable:
        return None
    return next(({"pod_command": found[0], "herdr_session": reach.get("herdr_session"), "pod": p, "copy": copy}
                 for (p, copy), (_, pn) in rows.items() if p == found[1] and pn == pane), None)


def now(seat):
    """(session id or None when ./pod can't read it, pane) of the session running in the seat's tab now, or None when
    nothing runs there. Raises Unreadable."""
    return listed(seat["pod_command"], seat.get("herdr_session")).get((seat["pod"], seat["copy"]))


def in_folder(folder):
    """For a pod member's folder: [(seat, runtime, session id, pid)] for each of its copies running in its tab, first
    copy first (none when nothing runs in them). None for a folder that isn't a pod member's. Raises Unreadable."""
    found = command(folder)
    if not found or Path(found[2]) != Path(folder).resolve():
        return None
    cmd, pod, member = found
    live = {s: (runtime, pid, os.path.realpath(cwd or "")) for runtime in ("claude", "codex", "agy")
            for pid, s, cwd in transcripts.live_sessions(runtime)}
    return [({"pod_command": cmd, "herdr_session": None, "pod": p, "copy": copy}, live[s][0], s, live[s][1])
            for (p, copy), (s, _) in listed(cmd).items()
            if p == pod and s in live and live[s][2] == os.path.realpath(member)]
