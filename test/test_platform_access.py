"""Public browsing stays independent of host model credentials and control APIs."""
import json
from pathlib import Path
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from config.platform_access import get_platform_access, MODE_ENV, TOKEN_ENV, operator_request_headers
from runtime.operations.http_router import create_operations_router

TOKEN = "fixture-operator-access-" + "x" * 32


@pytest.fixture(autouse=True)
def isolated_access(monkeypatch):
    monkeypatch.delenv(MODE_ENV, raising=False)
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    monkeypatch.delenv("SUPPLY_CHAIN_ACCESS_REQUEST_URL", raising=False)


class Records:
    def __init__(self):
        self.created = []

    def list_jobs(self, **kwargs):
        return {"ok": True, "data": [{"job_id": "archived", "status": "completed"}]}

    def create_experiment(self, **kwargs):
        self.created.append(kwargs)
        return {"ok": True, "data": {"job_id": "new"}}

    def _capture(self, callback):
        return {"ok": True, "data": callback()}


def app_for(records):
    app = FastAPI()
    app.include_router(create_operations_router(api=records))
    return app


def test_default_browsing_and_direct_create_denial():
    records = Records()
    with TestClient(app_for(records)) as client:
        access = client.get("/operations/access").json()["data"]
        assert access["mode"] == "visitor" and not access["can_operate"]
        assert client.get("/operations/jobs").json()["data"] == []
        response = client.post("/operations/jobs", json={"scenario_id": "beer_game_scripted"},
                               headers={"Authorization": "Bearer " + TOKEN})
        assert response.status_code == 403
    assert records.created == []


@pytest.mark.parametrize("path,payload", [
    ("/operations/jobs/x/run", {}), ("/operations/jobs/x/start", {}),
    ("/operations/jobs/x/resume", {}), ("/operations/jobs/x/stop", {"reason": "test"}),
    ("/operations/jobs/x/delete", {}), ("/operations/artifacts/resume", {"source": "workspace_jobs"}),
    ("/operations/batches", {"items": []}), ("/operations/batches/x/resume", {}),
    ("/operations/batches/x/pause", {}),
])
def test_visitor_cannot_mutate_operations(path, payload):
    with TestClient(app_for(Records())) as client:
        assert client.post(path, json=payload).status_code == 403


@pytest.mark.parametrize("mode,token", [("operator", ""), ("operator", "short"), ("unexpected", TOKEN)])
def test_misconfigured_execution_stays_closed(monkeypatch, mode, token):
    monkeypatch.setenv(MODE_ENV, mode)
    monkeypatch.setenv(TOKEN_ENV, token)
    assert not get_platform_access().execution_enabled
    with TestClient(app_for(Records())) as client:
        assert client.post("/operations/jobs", json={"scenario_id": "x"}).status_code == 403


def test_operator_requires_valid_token_and_does_not_disclose_it(monkeypatch):
    monkeypatch.setenv(MODE_ENV, "operator")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    records = Records()
    with TestClient(app_for(records)) as client:
        for header in [{}, {"Authorization": "Bearer wrong"}]:
            assert client.post("/operations/jobs", json={"scenario_id": "x"}, headers=header).status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        assert client.get("/operations/access").json()["data"]["can_operate"] is False
        response = client.get("/operations/access", headers=headers)
        assert response.json()["data"]["can_operate"] is True
        assert TOKEN not in response.text and TOKEN not in repr(get_platform_access())
        assert client.post("/operations/jobs", json={"scenario_id": "x"}, headers=headers).status_code == 200
    assert len(records.created) == 1
    assert operator_request_headers() == headers


@pytest.mark.parametrize("path", ["/simulation_config", "/execute", "/next_turn", "/check_orders",
                                  "/statistics_orders", "/runtime_checkpoint/load", "/runtime_checkpoint/save"])
def test_raw_engine_endpoints_cannot_bypass_visitor_mode(path):
    from simulate.simulation_server import app
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post(path, json={}).status_code == 403


def test_visitor_never_opens_runtime_env_or_refreshes_worker(monkeypatch, tmp_path):
    from config import environment_config
    from runtime.operations import api
    monkeypatch.setenv("ANTHROPIC_MODEL", "inherited-private-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inherited-secret")
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("Visitor attempted to read a configuration file"))
    environment_config._load_runtime_env_file()
    assert environment_config._env_value("ANTHROPIC_MODEL") == ""
    assert environment_config.EnvironmentConfig.get_agent_env() == {}
    with pytest.raises(PermissionError):
        api._refresh_agent_env_for_child({}, tmp_path)
    result = api.OperationsAPI().run_job("any-job")
    assert not result["ok"] and result["error"]["type"] == "PermissionError"


def test_request_link_is_explicit_and_safe(monkeypatch):
    monkeypatch.setenv("SUPPLY_CHAIN_ACCESS_REQUEST_URL", "https://[invalid")
    assert get_platform_access().capabilities()["access_request_url"] == ""
    monkeypatch.setenv("SUPPLY_CHAIN_ACCESS_REQUEST_URL", "javascript:alert(1)")
    assert get_platform_access().capabilities()["access_request_url"] == ""
    monkeypatch.setenv("SUPPLY_CHAIN_ACCESS_REQUEST_URL", "https://example.org/request")
    assert get_platform_access().capabilities()["access_request_url"] == "https://example.org/request"


def test_public_download_filters_private_files_and_symlinks(tmp_path, monkeypatch):
    from core.public_artifacts import is_public_artifact
    from runtime.operations import http_router
    root = tmp_path / "run"
    root.mkdir()
    (root / "run_meta.json").write_text('{}')
    for name in [".env", "auth.private.json", "worker.agent_env.json", "state.pkl", "storage.sqlite", "worker.log"]:
        (root / name).write_text("not public")
        assert not is_public_artifact(root / name, root)
    outside = tmp_path / "outside.json"
    outside.write_text("not public")
    (root / "escape.json").symlink_to(outside)
    assert not is_public_artifact(root / "escape.json", root)
    (root / "public_manifest.json").write_text(json.dumps({"files":{"run_meta.json":{}}}))
    monkeypatch.setattr(http_router, "_validate_artifact_root", lambda path: path)
    response = http_router._build_artifact_zip(root)
    try:
        with zipfile.ZipFile(response.path) as archive:
            assert sorted(archive.namelist()) == ["run/public_manifest.json", "run/run_meta.json"]
    finally:
        Path(response.path).unlink()


def test_public_batch_listing_does_not_rewrite_archives(tmp_path, monkeypatch):
    from runtime.operations import batch
    batch_id = "batch_" + "a" * 12
    record = dict(batch_id=batch_id, status="running", supervisor_pid=None, created_at="2026-01-01")
    monkeypatch.setattr(batch, "batch_root", lambda: tmp_path)
    path = tmp_path / (batch_id + ".json")
    path.write_text(json.dumps(record))
    before = path.read_bytes()
    with TestClient(app_for(Records())) as client:
        assert client.get("/operations/batches").json()["data"] == []
    assert path.read_bytes() == before


def test_private_directory_cannot_be_selected_as_zip_root(tmp_path, monkeypatch):
    from runtime.operations import http_router
    private = tmp_path / "_operations"
    private.mkdir()
    monkeypatch.setattr(http_router, "_artifact_bases", lambda: {"workspace_jobs": tmp_path})
    with pytest.raises(ValueError, match="not public"):
        http_router._validate_artifact_root(private)


def test_operator_file_cannot_replace_mode_or_access_token(tmp_path, monkeypatch):
    from config import environment_config
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / ".env").write_text(
        "SUPPLY_CHAIN_PLATFORM_MODE=visitor\nSUPPLY_CHAIN_OPERATOR_TOKEN=untrusted\nANTHROPIC_MODEL=own-model\n")
    monkeypatch.setattr(environment_config, "__file__", str(config_dir / "environment_config.py"))
    monkeypatch.setenv(MODE_ENV, "operator")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    environment_config._load_runtime_env_file()
    assert get_platform_access().mode == "operator"
    assert get_platform_access().authenticated("Bearer " + TOKEN)
    assert environment_config._env_value("ANTHROPIC_MODEL") == "own-model"


def test_engine_authentication_and_worker_headers(monkeypatch):
    from simulate.simulation_server import app
    from agent.static_utils import StaticUtils
    monkeypatch.setenv(MODE_ENV, "operator")
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    monkeypatch.setenv("SIMULATION_SESSION_ID", "own-session")
    headers = StaticUtils.simulation_session_headers()
    assert headers["Authorization"] == "Bearer " + TOKEN
    assert headers["X-Simulation-Session-Id"] == "own-session"
    with TestClient(app) as client:
        assert client.post("/simulation_config", json={}).status_code == 401


def test_live_engine_reads_require_an_operator_even_when_results_are_public(monkeypatch):
    from simulate.simulation_server import app
    with TestClient(app) as client:
        assert client.get('/health').status_code == 200
        for path in ['/state/json','/exchange','/simulation_context','/session/status']:
            assert client.get(path).status_code == 403
        monkeypatch.setenv(MODE_ENV,'operator')
        monkeypatch.setenv(TOKEN_ENV,TOKEN)
        assert client.get('/session/status').status_code == 401
        response=client.get('/session/status',headers={'Authorization':'Bearer '+TOKEN})
        assert response.status_code == 404
