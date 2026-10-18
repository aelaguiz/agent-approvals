import importlib
import json

import pytest

TEST_CONFIG = {
    "channel": "CTEST", "approver": "UAPPROVER", "approver_name": "Sam",
    "bot_token_file": "/nonexistent", "app_token_file": "/nonexistent", "timezone": "UTC",
    "default_herdr_session": "default",
}


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    """Every test gets its own record store and a made-up config."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps(TEST_CONFIG))
    monkeypatch.setenv("APPROVALS_HOME", str(home))
    monkeypatch.delenv("APPROVALS_TEST_APPROVER", raising=False)
    monkeypatch.delenv("APPROVALS_TEST", raising=False)
    from approvals import config
    importlib.reload(config)
    for mod in ("store", "cards", "core", "reach", "router", "listener"):
        importlib.reload(importlib.import_module(f"approvals.{mod}"))
    return tmp_path
