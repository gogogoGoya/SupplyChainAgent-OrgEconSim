import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.model_failure import (
    FatalModelError, abort_on_fatal_model_error, attach_model_failure,
    check_model_message, classify_model_failure, gather_model_tasks,
)


@pytest.fixture(autouse=True)
def private_diagnostics_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMULATION_PRIVATE_DIAGNOSTICS_DIR", str(tmp_path / "private_diagnostics"))
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fixture-inherited-auth")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-inherited-api-key")


@pytest.mark.parametrize("text,code", [
    ('API Error: 401 {"code":"invalid_api_key"}', "model_authentication_failed"),
    ("Incorrect API key provided", "model_authentication_failed"),
    ("HTTP 401 Unauthorized", "model_authentication_failed"),
    ("status_code=403", "model_permission_denied"),
    ('{"error":{"type":"authentication_error"}}', "model_authentication_failed"),
    ('API Error: 404 model_not_found', "model_configuration_invalid"),
    ('API Error: 429 {"error":{"message":"Provider error: {\\"error\\":{\\"message\\":\\"Your token-plan 1-month quota has been exhausted.\\"}}"}}', "model_quota_exhausted"),
    ('HTTP 429 {"error":{"code":"insufficient_quota"}}', "model_quota_exhausted"),
    ('HTTP 429 billing_hard_limit_reached', "model_quota_exhausted"),
])
def test_permanent_service_errors_are_fatal(text, code):
    assert classify_model_failure(text).code == code


@pytest.mark.parametrize("text", ["API Error: 429 rate limited", "HTTP 503 unavailable", "request timed out", "invalid JSON output",
                                 "HTTP 429 per-minute quota exceeded, retry later", "HTTP 429 token rate limit exceeded",
                                 "HTTP 429 daily request limit reached"])
def test_transient_and_output_errors_are_not_immediately_fatal(text):
    assert classify_model_failure(text) is None


def test_success_subtype_cannot_hide_error_and_private_key_log_is_separate(tmp_path):
    workspace = tmp_path / "run"
    workspace.mkdir()
    (workspace / "run_meta.json").write_text(json.dumps({"job_id": "job-test", "run_id": "run-test"}))
    options = SimpleNamespace(env={"ANTHROPIC_API_KEY": "fixture-raw-sdk-key", "ANTHROPIC_BASE_URL": "https://gateway.invalid"}, model="fixture-model")
    context = dict(workspace=workspace, options=options, enterprise_id="E", role="Enterprise", round_id=10)
    message = SimpleNamespace(subtype="success", is_error=True, result="API Error: 401 invalid_api_key")
    with pytest.raises(FatalModelError) as raised:
        check_model_message(message, **context)
    path = Path(raised.value.context["credential_diagnostic_file"])
    assert path.is_relative_to(tmp_path / "private_diagnostics")
    assert path.stat().st_mode & 0o777 == 0o600
    diagnostic = json.loads(path.read_text())
    assert diagnostic["sdk_credentials"]["ANTHROPIC_API_KEY"] == "fixture-raw-sdk-key"
    assert diagnostic["credential_source"]["ANTHROPIC_API_KEY"] == "ClaudeAgentOptions.env"
    assert diagnostic["upstream_provider_key"] is None
    assert "fixture-raw-sdk-key" not in str(raised.value)
    assert "fixture-raw-sdk-key" not in json.dumps(raised.value.to_dict())
    assert not list(workspace.glob("*.private.json"))


def test_normal_model_discussion_of_authentication_is_not_a_service_failure():
    check_model_message(SimpleNamespace(model="real-model", content=[SimpleNamespace(text="The customer mentioned invalid_api_key in a document.")]))


def test_quota_failure_stops_without_creating_credential_diagnostics(tmp_path):
    text = "API Error: 429 Your token-plan 1-month quota has been exhausted."
    context = dict(workspace=tmp_path / "run", options=SimpleNamespace(env={}),
                   enterprise_id="Manufacturer", role="Enterprise", round_id=31)
    with pytest.raises(FatalModelError, match="model_quota_exhausted") as raised:
        check_model_message(SimpleNamespace(model="<synthetic>", content=[SimpleNamespace(text=text)]), **context)
    assert raised.value.context["round_id"] == 31
    assert "credential_diagnostic_file" not in raised.value.context
    assert not list(tmp_path.rglob("*.private.json"))
    # A business discussion containing the same words is not a provider response.
    check_model_message(SimpleNamespace(model="real-model", content=[SimpleNamespace(text="The report says: " + text)]))


def test_fatal_failure_cancels_inflight_sibling_model_tasks():
    cancelled = []

    async def sibling():
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)

    async def fail():
        await asyncio.sleep(0)
        raise FatalModelError("model_authentication_failed", "fixture")

    with pytest.raises(FatalModelError):
        asyncio.run(gather_model_tasks(sibling(), fail()))
    assert cancelled == [True]


def test_unwritable_private_diagnostic_does_not_hide_fatal_failure(tmp_path):
    (tmp_path / "private_diagnostics").write_text("not a directory")
    error = attach_model_failure(FatalModelError("model_authentication_failed", "fixture"),
                                workspace=tmp_path / "run", options=SimpleNamespace(env={}),
                                enterprise_id="E", role="Enterprise", round_id=0)
    assert error.context["credential_diagnostic_error"] == "FileExistsError"


def test_operations_job_and_archive_fail_at_last_complete_checkpoint(tmp_path):
    from persistence.sql_mirror import SQLiteMirrorRepository
    from runtime.operations.facade import OperationsFacade
    from runtime.operations.runner_adapter import OperationsSimulationRunner
    from runtime.operations.worker import OperationsWorker

    repo = SQLiteMirrorRepository(tmp_path / "operations.sqlite")
    job = OperationsFacade(repo).create_experiment_from_scenario(scenario_id="firm_ablation_fd4_sa", planned_total_steps=20)
    workspace = tmp_path / "run"

    class Manager:
        def __init__(self, context):
            self.workspace_dir = context.workspace_dir

        @abort_on_fatal_model_error
        async def run(self, max_step, checkpoint_callback, operations_job_id, operations_run_id, operations_repository):
            (self.workspace_dir / "run_meta.json").write_text(json.dumps({"job_id": operations_job_id, "run_id": operations_run_id}))
            checkpoint_callback(completed_steps=10, last_complete_round=9)
            raise FatalModelError("model_authentication_failed", "fixture 401")

    repo.update_job(job["job_id"], {"artifact_root": str(workspace)})
    runner = OperationsSimulationRunner(workspace_dir=tmp_path, manager_factory=Manager)
    result = OperationsWorker(repo, runner).run_job(job["job_id"])
    assert result["status"] == "failed"
    assert result["completed_steps"] == 10
    assert result["last_complete_round"] == 9
    assert repo.get_job(job["job_id"])["status"] == "failed"
    meta = json.loads((workspace / "run_meta.json").read_text())
    assert meta["status"] == "failed"
    assert meta["completed_steps"] == 10
    assert meta["model_failure"]["code"] == "model_authentication_failed"
    assert json.loads((workspace / "model_failure.json").read_text())["status"] == "failed"
