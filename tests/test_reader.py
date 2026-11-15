"""The card reader sees the card and the approver's rules, with no Slack or real agent."""
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from approvals import cli, config, reader


def test_card_drawing_keeps_the_words_and_table_whole():
    quote = "A long quote\n" + "word " * 300
    cell = "value " * 300
    card = {
        "type": "price", "title": "Choose a price", "why": "People can buy it.",
        "facts": [["Markets", ["US", "UK"]]],
        "quotes": [{"label": "Customer", "text": quote, "note": "Exact"}],
        "table": {"columns": ["Today", "Proposed"], "rows": [["old", cell]]},
        "options": [{"name": "Lower the price", "title": "to $59.99 in the US"}], "pick": "Lower the price",
        "parts": [{"title": "First", "note": "Test", "facts": [["Where", "US"]],
                   "quotes": [{"label": "Reply", "text": quote}]}],
        "sections": [{"title": "Details", "lines": ["Plain", ["Who", "Sam"], {"text": "Ready", "tag": "today"}]}],
        "approves": "Change the price", "does_not_approve": "Other prices",
    }
    text = reader.card_text(card)
    for part in ("Markets:   - US\n  - UK", f"SHOWN WORD FOR WORD (Customer):\n{quote}",
                 f"TABLE: Today | Proposed\n  old | {cell}", "CHOICE BUTTON: Lower the price (the writer's pick)\n  (line above the buttons: to $59.99 in the US)",
                 "ROW 1: First (Test)", f"SHOWN WORD FOR WORD (Reply):\n{quote}",
                 "SECTION Details:\n  - Plain\n  - Who: Sam\n  - Ready · today", "A YES COVERS: Change the price"):
        assert part in text


def test_brief_selects_heading_and_includes_optional_knowledge(tmp_path):
    path = tmp_path / "rules.md"
    path.write_text("# Other\nIgnore\n## Writing to Sam\nRead once.\n### More\nDetails.\n## Next\nNot included.\n")
    settings = {"approver_name": "Sam", "reader_rules": f"{path}#Writing to Sam", "reader_knows": "poker and software."}
    brief = reader.brief({"title": "A card"}, settings)
    assert "approver, Sam, will" in brief
    assert "<how_he_reads>\n## Writing to Sam\nRead once.\n### More\nDetails.\n</how_he_reads>" in brief
    assert "Ignore" not in brief and "Not included" not in brief
    assert "What he already knows, so never count it: poker and software. He does not know" in brief
    assert brief.endswith(reader.ASK_TEXT)
    settings["reader_rules"] = str(path)
    assert "# Other\nIgnore" in reader.brief({}, settings)


def test_brief_without_optional_settings():
    brief = reader.brief({}, {"approver_name": "Sam"})
    assert "<how_he_reads>" not in brief and "What he already knows" not in brief
    assert "He does not know anything that happened inside the writer's work" in brief
    assert "does not remember approval cards by their numbers" in brief


@pytest.mark.parametrize("rules", ["missing.md", "rules.md#Missing"])
def test_missing_rules_fail_before_any_reader(rules, tmp_path, monkeypatch, capsys):
    (tmp_path / "rules.md").write_text("# Writing\nHere\n")
    (tmp_path / "card.json").write_text("{}")
    monkeypatch.setattr(reader, "run", lambda *args: pytest.fail("reader started"))
    monkeypatch.setattr(config, "settings", lambda: {"approver_name": "Sam", "reader_rules": rules})
    with pytest.raises(SystemExit) as exc:
        cli.main(["read", "card.json"])
    assert exc.value.code == 2
    assert rules.split("#")[0] in capsys.readouterr().err


@pytest.mark.parametrize("which", ["claude", "codex", "codex through AI Manager"])
def test_reader_command_and_isolation(which, monkeypatch):
    calls = []
    result = SimpleNamespace(returncode=0, stdout="read\n")
    managed = which == "codex through AI Manager"
    monkeypatch.setattr(reader.shutil, "which", lambda name: "/somewhere/aim" if managed or which == "claude" else None)
    which, start = ("codex", ["aim", "codex", "run", "--"]) if managed else (which, ["codex"])
    monkeypatch.setenv("CLAUDE_CODE_REMOTE_MEMORY_DIR", "saved-memory")
    def fake_run(cmd, **kw):
        assert Path(kw["cwd"]).is_dir() and list(Path(kw["cwd"]).iterdir()) == []
        calls.append((cmd, kw))
        return result
    monkeypatch.setattr(reader.subprocess, "run", fake_run)
    assert reader.run("the brief", which) is result
    assert len(calls) == 1
    cmd, kw = calls[0]
    assert kw["input"] == "the brief" and kw["timeout"] == 300 and kw["capture_output"] and kw["text"]
    assert not Path(kw["cwd"]).exists()
    if which == "claude":
        assert cmd == ["claude", "-p", "--model", "opus", "--effort", "medium", "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--permission-mode", "manual", "--permission-prompts", "none"]
        assert "CLAUDE_CODE_REMOTE_MEMORY_DIR" not in kw["env"]
    else:
        assert cmd == start + ["exec", "--skip-git-repo-check", "-m", "gpt-6.1-terra", "-c", 'model_reasoning_effort="xhigh"', "-"]


def test_codex_reader_uses_the_machines_own_login_when_ai_manager_refuses(monkeypatch):
    calls = []
    pooled = SimpleNamespace(returncode=1, stdout="pooled output", stderr="pooled error")
    direct = SimpleNamespace(returncode=0, stdout="read\n", stderr="")
    monkeypatch.setattr(reader.shutil, "which", lambda name: "/somewhere/aim")
    times = iter([100, 112])
    monkeypatch.setattr(reader.time, "monotonic", lambda: next(times))
    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return pooled if cmd[0] == "aim" else direct
    monkeypatch.setattr(reader.subprocess, "run", fake_run)
    assert reader.run("the brief", "codex") is direct
    assert len(calls) == 2
    assert [cmd[0] for cmd, _ in calls] == ["aim", "codex"] and calls[0][0][4:] == calls[1][0][1:]
    assert calls[0][1]["timeout"] == 300 and calls[1][1]["timeout"] == 288
    assert direct.stdout == "read\n" and direct.stderr == ""


def test_codex_reader_reports_both_failures(monkeypatch):
    pooled = subprocess.CompletedProcess(["aim"], 1, stdout="pooled output", stderr="pooled error")
    direct = subprocess.CompletedProcess(["codex"], 2, stdout="direct output", stderr="direct error")
    results = iter([pooled, direct])
    monkeypatch.setattr(reader.shutil, "which", lambda name: "aim")
    monkeypatch.setattr(reader.subprocess, "run", lambda *args, **kwargs: next(results))
    result = reader.run("the brief", "codex")
    assert result is direct and result.returncode == 2
    assert result.stderr.index("pooled output") < result.stderr.index("direct output")
    assert "pooled error" in result.stderr and "direct error" in result.stderr


def test_codex_reader_does_not_fallback_when_budget_is_gone(monkeypatch):
    pooled = subprocess.CompletedProcess(["aim"], 1, stdout="", stderr="failed")
    times = iter([100, 400])
    monkeypatch.setattr(reader.shutil, "which", lambda name: "aim")
    monkeypatch.setattr(reader.time, "monotonic", lambda: next(times))
    calls = []
    def fake_run(cmd, **kw):
        calls.append(cmd)
        return pooled
    monkeypatch.setattr(reader.subprocess, "run", fake_run)
    assert reader.run("the brief", "codex") is pooled
    assert len(calls) == 1


@pytest.mark.parametrize("attempt", [0, 1])
def test_codex_reader_propagates_timeout(monkeypatch, attempt):
    error = subprocess.TimeoutExpired("codex", 300)
    calls = []
    monkeypatch.setattr(reader.shutil, "which", lambda name: "aim")
    def fake_run(cmd, **kw):
        calls.append(cmd)
        if len(calls) == attempt + 1:
            raise error
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="failed")
    monkeypatch.setattr(reader.subprocess, "run", fake_run)
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        reader.run("the brief", "codex")
    assert exc.value is error and len(calls) == attempt + 1


def test_all_cards_are_checked_before_starting(tmp_path, monkeypatch, capsys):
    (tmp_path / "good.json").write_text("{}")
    (tmp_path / "bad.json").write_text("[]")
    monkeypatch.setattr(reader, "run", lambda *args: pytest.fail("reader started"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["read", "good.json", "bad.json"])
    assert exc.value.code == 2
    assert "bad.json" in capsys.readouterr().err


def test_readers_run_together_and_results_are_in_file_order(tmp_path, monkeypatch, capsys):
    for name in ("a", "b"):
        (tmp_path / f"{name}.json").write_text(json.dumps({"title": name}))
    barrier = threading.Barrier(2, timeout=5)
    calls = []
    def fake_run(brief, which):
        calls.append(which)
        barrier.wait()
        return SimpleNamespace(returncode=0, stdout="first\n" if "TITLE: a" in brief else "second\n")
    monkeypatch.setattr(reader, "run", fake_run)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    cli.main(["read", "a.json", "b.json"])
    assert calls == ["claude", "claude"]
    assert capsys.readouterr().out == "a.json:\nfirst\nb.json:\nsecond\n"
    cli.main(["read", "a.json", "b.json", "--with", "codex"])
    assert calls[-2:] == ["codex", "codex"]


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("codex", 300, output=b"partial output"),
                                   subprocess.CalledProcessError(1, "codex", output="partial output", stderr="failed")])
def test_reader_failure_names_card_and_output(tmp_path, monkeypatch, capsys, error):
    (tmp_path / "card.json").write_text("{}")
    def fake_run(*args):
        raise error
    monkeypatch.setattr(reader, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        cli.main(["read", "card.json"])
    assert exc.value.code == 1
    error_text = capsys.readouterr().err
    assert "card.json" in error_text and "partial output" in error_text
