import json

import pytest

from quantpilot.jobs.prepare_kis_paper_runtime import prepare_runtime


def env(account="12345678"):
    return {"KIS_PAPER_APP_KEY": "fixture-key", "KIS_PAPER_APP_SECRET": "fixture-secret",
            "KIS_PAPER_ACCOUNT_NUMBER": account}


def test_prepare_creates_bound_store_without_authority(tmp_path):
    result = prepare_runtime(tmp_path, env())
    assert result["status"] == "prepared_not_armed"
    assert (tmp_path / "state.sqlite3").is_file()
    policy = json.loads((tmp_path / "policy.draft.json").read_text())
    assert policy["authority_level"] == 2
    assert policy["kill_switch_engaged"] is True
    assert policy["fully_automated_operator_enabled"] is False
    registry = json.loads((tmp_path / "registry.draft.json").read_text())
    assert registry["entries"][0]["status"] == "draft"
    assert registry["entries"][0]["allowed_execution_levels"] == []
    assert registry["lifecycle_records"] == []
    for path in tmp_path.glob("*.json"):
        assert "fixture-secret" not in path.read_text()
        assert "12345678" not in path.read_text()


def test_prepare_preserves_user_edits(tmp_path):
    prepare_runtime(tmp_path, env())
    policy = tmp_path / "policy.draft.json"
    policy.write_text("user-edited")
    prepare_runtime(tmp_path, env())
    assert policy.read_text() == "user-edited"


def test_other_account_cannot_rebind_existing_store(tmp_path):
    prepare_runtime(tmp_path, env())
    with pytest.raises(Exception):
        prepare_runtime(tmp_path, env("87654321"))


def test_repository_destination_is_rejected():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    with pytest.raises(ValueError):
        prepare_runtime(root / "paper-runtime", env())
