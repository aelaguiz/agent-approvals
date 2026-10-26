"""More than one Slack workspace: each numbers its own requests, and the command picks the right one."""
import json

import pytest

from approvals import cli, config, store
from conftest import TEST_CONFIG


@pytest.fixture
def two(tmp_path, monkeypatch):
    """The main workspace, and a second one, cj, whose agents work in work/cj."""
    root = tmp_path / "root"
    cj = root / "cj"
    cj.mkdir(parents=True)
    (root / "config.json").write_text(json.dumps(TEST_CONFIG))
    (cj / "config.json").write_text(json.dumps(dict(TEST_CONFIG, id_prefix="CJ", folders=[str(tmp_path / "work" / "cj")])))
    for d in ("work/cj/specialists/seller", "work/cj-old", "work/brain"):
        (tmp_path / d).mkdir(parents=True)
    monkeypatch.setattr(config, "ROOT", root)
    monkeypatch.delenv("APPROVALS_HOME")
    return tmp_path, root, cj


def test_each_workspace_numbers_and_lists_its_own_requests(two):
    _, root, cj = two
    config.use(cj)
    assert [store.create({"title": "x"})["id"] for _ in range(2)] == ["CJ-0001", "CJ-0002"]
    config.use(root)
    assert store.create({"title": "y"})["id"] == "AP-0001"
    assert [r["id"] for r in store.all_records()] == ["AP-0001"]
    config.use(cj)
    assert [r["id"] for r in store.all_records()] == ["CJ-0001", "CJ-0002"]


def test_the_request_number_then_the_folder_picks_the_workspace(two):
    tmp, root, cj = two
    assert config.choose("CJ-0001", cwd=tmp / "work/brain") == cj
    assert config.choose("AP-0001", cwd=tmp / "work/cj") == root
    assert config.choose(cwd=tmp / "work/cj") == cj
    assert config.choose(cwd=tmp / "work/cj/specialists/seller") == cj
    assert config.choose(cwd=tmp / "work/cj-old") == root
    assert config.choose(cwd=tmp / "work/brain") == root
    with pytest.raises(config.ConfigError, match="XX-"):
        config.choose("XX-0001")


def test_with_one_workspace_every_folder_uses_it(two):
    tmp, root, cj = two
    (cj / "config.json").unlink()
    assert config.choose(cwd=tmp / "work/cj") == root


def test_the_command_finds_the_workspace_by_itself(two, monkeypatch, capsys):
    tmp, root, cj = two
    config.use(cj)
    store.create({"title": "Email a seller", "status": "pending", "versions": [{"n": 1}], "agent": "Seller"})
    config.use(root)
    monkeypatch.chdir(tmp / "work/brain")
    cli.main(["show", "CJ-0001"])
    assert '"Email a seller"' in capsys.readouterr().out
    config.use(root)
    monkeypatch.chdir(tmp / "work/cj/specialists/seller")
    cli.main(["list"])
    assert "CJ-0001" in capsys.readouterr().out
