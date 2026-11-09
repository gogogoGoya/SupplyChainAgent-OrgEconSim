"""Gateway/key rotation for new Operations workers, without model requests."""

import json
from types import SimpleNamespace

import pytest

from runtime.operations import api


def write_env(root, text):
    directory = root / "scripts"
    directory.mkdir(exist_ok=True)
    (directory / ".env").write_text(text, encoding="utf-8")


@pytest.mark.parametrize("endpoint_key", ["ANTHROPIC_BASE_URL", "AGENT_GATEWAY_BASE_URL"])
@pytest.mark.parametrize("auth_key", ["ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"])
def test_rotation_overrides_valid_old_endpoint_and_both_auth_aliases(tmp_path, endpoint_key, auth_key):
    write_env(tmp_path, f"{endpoint_key}=https://new.example.invalid\n{auth_key}=new-secret\n")
    inherited = {
        "ANTHROPIC_BASE_URL": "https://old.example.invalid",
        "AGENT_GATEWAY_BASE_URL": "https://old-alias.example.invalid",
        "ANTHROPIC_AUTH_TOKEN": "old-token",
        "ANTHROPIC_API_KEY": "old-key",
        "SIMULATION_API_BASE_URL": "http://127.0.0.1:8000",
    }
    child = inherited.copy()
    api._refresh_agent_env_for_child(child, tmp_path)
    assert child["ANTHROPIC_BASE_URL"] == child["AGENT_GATEWAY_BASE_URL"] == "https://new.example.invalid"
    assert child["ANTHROPIC_AUTH_TOKEN"] == child["ANTHROPIC_API_KEY"] == "new-secret"
    assert child["SIMULATION_API_BASE_URL"] == inherited["SIMULATION_API_BASE_URL"]
    assert inherited["ANTHROPIC_AUTH_TOKEN"] == "old-token"


def test_each_new_worker_reads_latest_file_without_changing_existing_worker(tmp_path):
    parent = {"ANTHROPIC_BASE_URL": "https://parent.example.invalid"}
    write_env(tmp_path, "ANTHROPIC_BASE_URL=https://first.example.invalid\n")
    first = parent.copy()
    api._refresh_agent_env_for_child(first, tmp_path)
    write_env(tmp_path, "ANTHROPIC_BASE_URL=https://second.example.invalid\n")
    second = parent.copy()
    api._refresh_agent_env_for_child(second, tmp_path)
    assert first["ANTHROPIC_BASE_URL"] == "https://first.example.invalid"
    assert second["ANTHROPIC_BASE_URL"] == "https://second.example.invalid"
    assert parent["ANTHROPIC_BASE_URL"] == "https://parent.example.invalid"


def test_new_default_model_does_not_inherit_old_optional_model_selectors(tmp_path):
    write_env(tmp_path, 'ANTHROPIC_MODEL=new-model\nANTHROPIC_MODEL_LIST=\n')
    child = {"ANTHROPIC_MODEL": "old-model", "ANTHROPIC_MODEL_LIST": '["old-model"]',
             "ANTHROPIC_CEO_MODEL": "old-ceo"}
    api._refresh_agent_env_for_child(child, tmp_path)
    assert child["ANTHROPIC_MODEL"] == "new-model"
    assert "ANTHROPIC_MODEL_LIST" not in child
    assert "ANTHROPIC_CEO_MODEL" not in child


def test_explicit_optional_models_and_existing_auth_priority_are_preserved(tmp_path):
    write_env(tmp_path, 'ANTHROPIC_MODEL=new-model\nANTHROPIC_MODEL_LIST=["a","b"]\n'
              'ANTHROPIC_CEO_MODEL=new-ceo\nANTHROPIC_AUTH_TOKEN=token\nANTHROPIC_API_KEY=key\n')
    child = {}
    api._refresh_agent_env_for_child(child, tmp_path)
    assert child["ANTHROPIC_MODEL_LIST"] == '["a","b"]'
    assert child["ANTHROPIC_CEO_MODEL"] == "new-ceo"
    assert child["ANTHROPIC_AUTH_TOKEN"] == child["ANTHROPIC_API_KEY"] == "token"


@pytest.mark.parametrize("text", [None, "ANTHROPIC_BASE_URL=\nANTHROPIC_API_KEY=\n", "SIMULATION_API_BASE_URL=other\n"])
def test_missing_or_empty_agent_config_keeps_deployment_environment(tmp_path, text):
    if text is not None:
        write_env(tmp_path, text)
    child = {"ANTHROPIC_BASE_URL": "https://deployed.example.invalid", "ANTHROPIC_AUTH_TOKEN": "deployed"}
    before = child.copy()
    api._refresh_agent_env_for_child(child, tmp_path)
    assert child == before


def test_dispatch_passes_rotated_settings_to_worker_and_redacts_log(tmp_path, monkeypatch):
    write_env(tmp_path, "ANTHROPIC_BASE_URL=https://new.example.invalid\nANTHROPIC_API_KEY=new-secret\n")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://old.example.invalid")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "old-secret")
    monkeypatch.setattr(api, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(api, "WORKSPACE_JOBS_ROOT", tmp_path / "jobs")
    captured = {}

    def popen(command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(api.subprocess, "Popen", popen)

    class Repository:
        def update_job(self, job_id, changes):
            self.changes = changes

    repo = Repository()
    api.OperationsAPI(repository=repo)._dispatch_subprocess(
        repo, {"job_id": "rotation-test", "status": "queued", "scenario_id": "firm_ablation_fd4_sa"},
        scenario_id=None, worker_id=None, client_dir=None, workspace_dir=None, execution_mode="agent_subprocess",
    )
    assert captured["env"]["ANTHROPIC_BASE_URL"] == "https://new.example.invalid"
    assert captured["env"]["ANTHROPIC_AUTH_TOKEN"] == "new-secret"
    snapshot = json.loads((tmp_path / "jobs/_process_logs/rotation-test.agent_env.json").read_text())
    assert snapshot["agent_env"]["ANTHROPIC_BASE_URL"] == "https://new.example.invalid"
    assert snapshot["agent_env"]["ANTHROPIC_AUTH_TOKEN"] == "<set>"
    assert "new-secret" not in json.dumps(snapshot)
