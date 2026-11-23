import json
from types import SimpleNamespace

from approvals import config, reach, router, transcripts
from test_router_listener import FakeHerdr

SID_A = "11111111-1111-4111-8111-111111111111"
SID_B = "22222222-2222-4222-8222-222222222222"
SID_SUB = "33333333-3333-4333-8333-333333333333"
SID_CRASH = "44444444-4444-4444-8444-444444444444"


def test_capture_detects_antigravity_and_keeps_claude_codex_order(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setenv("ANTIGRAVITY_CONVERSATION_ID", SID_A)
    monkeypatch.setenv("APPROVALS_FROM", "Team Lead")
    got = reach.capture()
    assert (got["runtime"], got["session_id"], got["agent"], got["named_by_launcher"]) == ("agy", SID_A, "Team Lead", True)

    monkeypatch.setenv("CODEX_THREAD_ID", SID_B)
    assert reach.capture()["runtime"] == "codex"
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID_SUB)
    assert reach.capture()["runtime"] == "claude"


def test_agy_transcript_prefers_full_and_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(transcripts, "HOME", tmp_path)
    logs = tmp_path / ".gemini/antigravity-cli/brain" / SID_A / ".system_generated/logs"
    logs.mkdir(parents=True)
    compact = logs / "transcript.jsonl"
    compact.write_text('{"type":"USER_INPUT"}\n')
    assert transcripts.transcript("agy", SID_A) == str(compact)

    full = logs / "transcript_full.jsonl"
    full.write_text('{"type":"USER_INPUT"}\n')
    assert transcripts.transcript("agy", SID_A) == str(full)
    assert transcripts.transcript("agy", "../escape") is None
    assert transcripts.transcript("unknown", SID_A) is None


def test_opening_parses_antigravity_user_request_and_first_visible_reply(tmp_path):
    p = tmp_path / "transcript_full.jsonl"
    rows = [
        {"type": "USER_INPUT", "source": "USER_EXPLICIT",
         "content": f"<USER_REQUEST>\nCheck card {SID_A}\n</USER_REQUEST>\n<USER_SETTINGS_CHANGE>x</USER_SETTINGS_CHANGE>"},
        {"type": "PLANNER_RESPONSE", "source": "MODEL", "content": ""},
        {"type": "GENERIC", "source": "MODEL", "content": "tool output"},
        {"type": "PLANNER_RESPONSE", "source": "MODEL", "content": f"Working on {SID_B} now."},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert transcripts.opening(str(p)) == ("Check card <id>", "Working on <id> now.")


def test_live_codex_sessions_skips_non_interactive_helpers_and_subagents(tmp_path, monkeypatch):
    sub = tmp_path / f"rollout-2026-11-19T00-17-10-{SID_SUB}.jsonl"
    sub.write_text(json.dumps({"type": "session_meta", "payload": {"source": {"subagent": {"depth": 1}}}}) + "\n")
    main = tmp_path / f"rollout-2026-11-18T22-28-45-{SID_A}.jsonl"
    main.write_text(json.dumps({"type": "session_meta", "payload": {"source": "cli"}}) + "\n")

    ps_out = "\n".join([
        "  206 /opt/aimgr-codex-runtime/artifacts/abc123/codex-code-mode-host --listen grpc+stdio://",
        "98015 /opt/homebrew/bin/node /Users/u/.local/share/aimgr/bin/aimgr.js codex run",
        "98100 /opt/aimgr-codex-runtime/artifacts/abc123/codex -m gpt-6.2-sol exec --skip-git-repo-check",
        "98101 /opt/aimgr-codex-runtime/artifacts/abc123/codex e --skip-git-repo-check",
        "98102 /opt/aimgr-codex-runtime/artifacts/abc123/codex app-server",
        "98138 /opt/aimgr-codex-runtime/artifacts/abc123/codex --no-daemon -p yolo",
    ])
    lsof_out = "\n".join([
        "p98138",
        "fcwd",
        "n/tmp/work/codex-seat",
        "f10",
        f"n{sub}",
        "f11",
        f"n{main}",
    ])

    def fake_run(cmd, **kw):
        if cmd[0] == "ps":
            return SimpleNamespace(returncode=0, stdout=ps_out, stderr="")
        assert cmd == ["lsof", "-p", "98138", "-Fn"]
        return SimpleNamespace(returncode=0, stdout=lsof_out, stderr="")

    monkeypatch.setattr(transcripts.subprocess, "run", fake_run)
    assert transcripts.live_sessions("codex") == [(98138, SID_A, "/tmp/work/codex-seat")]


def test_live_agy_sessions_skips_print_helpers_subagents_and_crashes(monkeypatch):
    ps_out = "\n".join([
        "89510 /usr/local/bin/agy -p --output-format json",
        "89511 /usr/local/bin/agy --print hello",
        "89512 /usr/local/bin/agy acp",
        "89518 agy",
        "89519 node /tmp/helper.js agy",
        "89520 /usr/local/bin/agy --model gemini-4-neon",
    ])
    lsof_by_pid = {
        "89518": "\n".join([
            "p89518",
            "fcwd",
            "n/tmp/work/agy-one",
            "f5",
            f"n/Users/u/.gemini/antigravity-cli/crashes/crash_89518_{SID_CRASH}.log",
            "f6",
            f"n/Users/u/.gemini/antigravity-cli/brain/{SID_SUB}/.system_generated",
            "f7",
            f"n/Users/u/.gemini/antigravity-cli/conversations/{SID_A}.db-shm",
            "f8",
            f"n/Users/u/.gemini/antigravity-cli/presence/{SID_A}.lock",
        ]),
        "89520": "\n".join([
            "p89520",
            "fcwd",
            "n/tmp/work/agy-two",
            "f6",
            f"n/Users/u/.gemini/antigravity-cli/conversations/{SID_B}.db",
        ]),
    }

    def fake_run(cmd, **kw):
        if cmd[0] == "ps":
            return SimpleNamespace(returncode=0, stdout=ps_out, stderr="")
        return SimpleNamespace(returncode=0, stdout=lsof_by_pid[cmd[2]], stderr="")

    monkeypatch.setattr(transcripts.subprocess, "run", fake_run)
    assert transcripts.live_sessions("agy") == [
        (89518, SID_A, "/tmp/work/agy-one"),
        (89520, SID_B, "/tmp/work/agy-two"),
    ]


def test_reach_running_finds_antigravity_session(monkeypatch):
    monkeypatch.setattr(transcripts, "live_sessions", lambda rt: [(89518, SID_A, "/tmp/work")] if rt == "agy" else [])
    monkeypatch.setattr(router, "find_pane_for_pid", lambda pid: ("pods", "w5:p1") if pid == 89518 else None)
    found = reach.running(SID_A)
    assert (found["runtime"], found["session_id"], found["cwd"], found["herdr_session"], found["herdr_pane"]) == (
        "agy", SID_A, "/tmp/work", "pods", "w5:p1"
    )


def test_live_agy_fallback_requires_interactive_agy_pane(monkeypatch):
    monkeypatch.setattr(transcripts, "agy_session_for_pid", lambda pid: None)
    monkeypatch.setattr(transcripts, "live_sessions", lambda rt: [])
    reach_rec = {"runtime": "agy", "session_id": SID_A, "host": config.hostname(),
                 "herdr_session": "brain", "herdr_workspace": "w1", "herdr_pane": "w1:p1", "cwd": "/tmp/x", "agent": "Lead"}

    claude_with_helper = FakeHerdr(
        panes={"w1:p1": {"agent": "claude", "agent_status": "working"}},
        procs={"w1:p1": [{"pid": 10, "name": "agy", "cmdline": "agy -p check"}]},
    )
    assert router.live_askers(reach_rec, "AP-1", run=claude_with_helper) == []

    blocked_agy = FakeHerdr(
        panes={"w1:p1": {"agent": "agy", "agent_status": "blocked"}},
        procs={"w1:p1": [{"pid": 10, "name": "agy", "cmdline": "agy"}]},
    )
    assert router.live_askers(reach_rec, "AP-1", run=blocked_agy) == []

    idle_agy = FakeHerdr(
        panes={"w1:p1": {"agent": "agy", "agent_status": "idle"}},
        procs={"w1:p1": [{"pid": 10, "name": "agy", "cmdline": "agy"}]},
    )
    assert router.live_askers(reach_rec, "AP-1", run=idle_agy) == [(SID_A, "brain", "w1:p1")]


def test_live_agy_and_aimgr_codex_panes_receive_answers(monkeypatch):
    monkeypatch.setattr(transcripts, "transcript", lambda rt, sid: f"/t/{rt}/{sid}.jsonl")
    monkeypatch.setattr(transcripts, "stopped_for_usage", lambda rt, sid: False)
    monkeypatch.setattr(transcripts, "agy_session_for_pid", lambda pid: SID_A if pid == 10 else None)
    monkeypatch.setattr(transcripts, "codex_session_for_pid", lambda pid: SID_B if pid == 20 else None)
    monkeypatch.setattr(transcripts, "live_sessions", lambda rt: [])

    delivered = {"ok": False}
    monkeypatch.setattr(transcripts, "contains", lambda path, needle: delivered["ok"])

    def sleep(_):
        delivered["ok"] = True

    agy_fake = FakeHerdr(panes={"w1:p1": {"focused": False, "agent": "agy", "agent_status": "idle"}},
                         procs={"w1:p1": [{"pid": 10, "name": "agy", "cmdline": "agy"}]})
    agy_reach = {"runtime": "agy", "session_id": SID_A, "host": config.hostname(),
                 "herdr_session": "brain", "herdr_workspace": "w1", "herdr_pane": "w1:p1", "cwd": "/tmp/x", "agent": "Lead"}
    res = router.deliver(agy_reach, "msg (ref r1)", "r1", run=agy_fake, sleep=sleep, clock=iter(range(1000)).__next__)
    assert res.status == "delivered" and agy_fake.sent_to("w1:p1") and not agy_fake.resumes()

    delivered["ok"] = False
    codex_fake = FakeHerdr(panes={"w1:p2": {"focused": False}},
                           procs={"w1:p2": [{"pid": 20, "name": "codex",
                                             "cmdline": "/opt/aimgr-codex-runtime/artifacts/abc/codex -p yolo"}]})
    codex_reach = {"runtime": "codex", "session_id": SID_B, "host": config.hostname(),
                   "herdr_session": "brain", "herdr_workspace": "w1", "herdr_pane": "w1:p2", "cwd": "/tmp/x", "agent": "Eng"}
    res = router.deliver(codex_reach, "msg (ref r2)", "r2", run=codex_fake, sleep=sleep, clock=iter(range(1000)).__next__)
    assert res.status == "delivered" and codex_fake.sent_to("w1:p2") and not codex_fake.resumes()


def test_closed_codex_resumes_fresh_with_identity_model_effort_and_records_move(tmp_path, monkeypatch):
    rollout = tmp_path / f"rollout-2026-11-18-{SID_A}.jsonl"
    rollout.write_text(
        json.dumps({"type": "turn_context", "payload": {"model": "gpt-6.2-sol", "effort": "high"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"model": "gpt-6.2-sol", "effort": "xhigh"}}) + "\n"
    )
    new_rollout = tmp_path / f"rollout-2026-11-18-{SID_B}.jsonl"
    new_rollout.write_text("")
    monkeypatch.setattr(transcripts, "transcript", lambda rt, sid: str(rollout if sid == SID_A else new_rollout))
    monkeypatch.setattr(transcripts, "stopped_for_usage", lambda rt, sid: False)
    monkeypatch.setattr(transcripts, "live_sessions", lambda rt: [])
    monkeypatch.setattr(transcripts, "codex_session_for_pid", lambda pid: SID_B if pid == 777 else None)

    fake = FakeHerdr(panes={}, text="Trust this folder?\n1. Trust and continue")
    fake.after_keys = "? for shortcuts · 95% context left"
    orig_call = fake.__call__

    def wrapped(cmd, **kw):
        out = orig_call(cmd, **kw)
        if cmd[3:5] == ["pane", "run"] and "aim codex resume-fresh" in cmd[6]:
            fake.procs[cmd[5]] = [{"pid": 777, "name": "codex", "cmdline": "/opt/aimgr-codex-runtime/artifacts/abc/codex -p yolo"}]
        elif cmd[3:5] == ["agent", "prompt"]:
            new_rollout.write_text(json.dumps({"text": cmd[6]}) + "\n")
        return out

    reach_rec = {
        "runtime": "codex", "session_id": SID_A, "host": config.hostname(),
        "herdr_session": "brain", "herdr_workspace": "w1", "herdr_pane": "w1:p1", "cwd": "/tmp/x", "agent": "Eng",
        "agent_identity_bin": True,
        "env": {"BASECAMP_PROFILE": "demo-eng", "APPROVALS_FROM": "Demo Eng", "GIT_AUTHOR_NAME": "Demo Eng (agent)"},
    }
    res = router.deliver(reach_rec, "Read it: agent-email read EM-9", "EM-9",
                         needle="agent-email read EM-9", run=wrapped, sleep=lambda _: None,
                         clock=iter(range(1000)).__next__)
    assert res.status == "delivered"
    ran = fake.resumes()[0][-1]
    assert 'PATH="$HOME/.local/share/agent-identity/bin:$PATH"' in ran
    assert "BASECAMP_PROFILE=demo-eng" not in ran
    assert "APPROVALS_FROM='Demo Eng'" in ran
    assert "GIT_AUTHOR_NAME='Demo Eng (agent)'" in ran
    assert f"aim codex resume-fresh {SID_A} -- -m gpt-6.2-sol -c 'model_reasoning_effort=\"xhigh\"'" in ran
    assert ["pane", "send-keys", "w1:p9", "Enter"] in fake.calls
    assert ["pane", "send-keys", "w1:p9", "Down", "Enter"] not in fake.calls
    assert res.moves == [[SID_A, SID_B]]
    assert res.reach["session_id"] == SID_B


def test_closed_codex_timeout_still_returns_newly_minted_thread_id(monkeypatch):
    monkeypatch.setattr(transcripts, "transcript", lambda rt, sid: f"/t/{sid}.jsonl")
    monkeypatch.setattr(transcripts, "stopped_for_usage", lambda rt, sid: False)
    monkeypatch.setattr(transcripts, "live_sessions", lambda rt: [])
    monkeypatch.setattr(transcripts, "codex_session_for_pid", lambda pid: SID_B if pid == 777 else None)
    monkeypatch.setattr(transcripts, "contains", lambda path, needle: False)

    fake = FakeHerdr(panes={}, text="Loading thread...")
    orig_call = fake.__call__

    def wrapped(cmd, **kw):
        out = orig_call(cmd, **kw)
        if cmd[3:5] == ["pane", "run"] and "aim codex resume-fresh" in cmd[6]:
            fake.procs[cmd[5]] = [{"pid": 777, "name": "codex", "cmdline": "/opt/aimgr-codex-runtime/artifacts/abc/codex -p yolo"}]
        return out

    reach_rec = {"runtime": "codex", "session_id": SID_A, "host": config.hostname(),
                 "herdr_session": "brain", "herdr_workspace": "w1", "herdr_pane": "w1:p1", "cwd": "/tmp/x", "agent": "Eng"}
    res = router.deliver(reach_rec, "reply (ref r9)", "r9", run=wrapped, sleep=lambda _: None,
                         clock=iter([0, 50, 200]).__next__)
    assert res.status == "retry"
    assert res.moves == [[SID_A, SID_B]]
    assert res.reach["session_id"] == SID_B


def test_closed_agy_resumes_with_identity_model_effort_and_enter_trust(monkeypatch):
    monkeypatch.setattr(transcripts, "transcript", lambda rt, sid: f"/t/{sid}.jsonl")
    monkeypatch.setattr(transcripts, "stopped_for_usage", lambda rt, sid: False)
    monkeypatch.setattr(transcripts, "live_sessions", lambda rt: [])
    monkeypatch.setattr(transcripts, "agy_session_for_pid", lambda pid: SID_A if pid == 888 else None)

    delivered = {"ok": False}
    monkeypatch.setattr(transcripts, "contains", lambda path, needle: delivered["ok"])

    fake = FakeHerdr(panes={}, text="Do you trust the contents of this project?\n1. Yes, I trust this folder\nConfirm")
    fake.after_keys = "Gemini 4 Neon · xhigh"
    orig_call = fake.__call__

    def wrapped(cmd, **kw):
        out = orig_call(cmd, **kw)
        if cmd[3:5] == ["pane", "run"] and "agy --conversation" in cmd[6]:
            fake.procs[cmd[5]] = [{"pid": 888, "name": "agy", "cmdline": "agy"}]
            fake.panes[cmd[5]] = {"focused": False, "agent": "agy", "agent_status": "blocked"}
        elif cmd[3:5] == ["pane", "send-keys"]:
            fake.panes[cmd[5]] = {"focused": False, "agent": "agy", "agent_status": "idle"}
        elif cmd[3:5] == ["agent", "prompt"]:
            delivered["ok"] = True
        return out

    reach_rec = {
        "runtime": "agy", "session_id": SID_A, "host": config.hostname(),
        "herdr_session": "brain", "herdr_workspace": "w1", "herdr_pane": "w1:p1", "cwd": "/tmp/x", "agent": "Lead",
        "model": "gemini-4-neon", "effort": "xhigh", "agent_identity_bin": True,
        "env": {"BASECAMP_PROFILE": "demo-lead", "APPROVALS_FROM": "Demo Lead"},
    }
    res = router.deliver(reach_rec, "reply (ref r4)", "r4", run=wrapped, sleep=lambda _: None, clock=iter(range(1000)).__next__)
    assert res.status == "delivered"
    ran = [c[-1] for c in fake.calls if c[:2] == ["pane", "run"] and "agy --conversation" in c[-1]][0]
    assert "BASECAMP_PROFILE=demo-lead" not in ran
    assert "APPROVALS_FROM='Demo Lead'" in ran
    assert f"agy --conversation {SID_A} --model gemini-4-neon --effort xhigh --dangerously-skip-permissions" in ran
    assert ["pane", "send-keys", "w1:p9", "Enter"] in fake.calls
    assert ["pane", "send-keys", "w1:p9", "Down", "Enter"] not in fake.calls
    assert res.reach["session_id"] == SID_A


def test_herdr_read_falls_back_to_visible_for_alternate_screen_tuis():
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd[3:])
        if "--source" in cmd and cmd[cmd.index("--source") + 1] == "visible":
            return SimpleNamespace(returncode=0, stdout="Do you trust the contents of this project?", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    h = router.Herdr("brain", run=fake_run)
    assert "Do you trust" in h.read("w1:p1", 15)
    assert calls == [
        ["pane", "read", "w1:p1", "--source", "recent-unwrapped", "--lines", "15"],
        ["pane", "read", "w1:p1", "--source", "visible"],
    ]
