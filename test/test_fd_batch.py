"""Serial batch control with real frozen jobs and isolated fake business runs."""
import json

import pytest

from persistence.sql_mirror import SQLiteMirrorRepository
from runtime.operations.api import OperationsAPI
from runtime.operations import batch
from runtime.operations.batch import BatchService
from runtime.operations.runner_adapter import OperationsSimulationRunner
from runtime.operations.service import OperationsService
from runtime.operations.worker import OperationsWorker


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "WORKSPACE_JOBS_ROOT", tmp_path / "jobs")
    monkeypatch.delenv("SIMULATION_OPERATIONS_CHILD_WORKER", raising=False)
    # This fixture simulates a live supervisor without starting a process.
    real_alive = batch.alive
    def fixture_alive(pid):
        if pid == 999999999:
            return any(json.loads(p.read_text()).get("status") in {"running", "pause_requested"} for p in (tmp_path / "jobs/_batches").glob("batch_*.json"))
        return real_alive(pid)
    monkeypatch.setattr(batch, "alive", fixture_alive)
    repo = SQLiteMirrorRepository(tmp_path / "ops.sqlite")
    api = OperationsAPI(repository=repo)
    service = BatchService(api)
    def launch(record):
        record.update(status="running", supervisor_pid=999999999)
        return service.save(record)
    monkeypatch.setattr(service, "_launch", launch)
    return service, api, repo


def items(family="fd1", arms=("ma", "na", "nc", "sa")):
    return [dict(scenario_id=f"firm_ablation_{family}_{arm}", planned_total_steps=20,
                 tags=["preset:agent"], config_overrides={}) for arm in arms]


@pytest.mark.parametrize("family", ["fd1", "fd2", "fd3", "fd4", "fd5"])
def test_freezes_jobs_without_making_future_items_active(setup, family):
    service, api, repo = setup
    arms = ("ma", "na", "nc", "sa")
    record = service.create(items(family, arms), repeats=2)
    jobs = list(repo.list_jobs())
    assert len(jobs) == len(arms) * 2
    assert all(j["status"] == "batch_pending" for j in jobs)
    assert len({j["config_version_id"] for j in jobs}) == len(jobs)
    assert [i["repeat_id"] for i in record["items"]] == [1] * len(arms) + [2] * len(arms)
    for job in jobs:
        config = repo.get_version(job["config_version_id"])["scenario_config"]
        assert config["simulation"]["agent_run_steps"] == 20
        assert config["experiment_design"]["batch_id"] == record["batch_id"]
        assert config["experiment_design"]["repeat_id"] in {1, 2}


@pytest.mark.parametrize("bad", [[], items() + items("fd2", ("sa",)), items("fd1", ("ma", "ma")),
                                 [dict(scenario_id="../anything")]])
def test_bad_batch_leaves_no_partial_jobs(setup, bad):
    service, _, repo = setup
    with pytest.raises(ValueError):
        service.create(bad)
    assert list(repo.list_jobs()) == []


def test_invalid_last_configuration_is_validated_before_any_write(setup):
    service, _, repo = setup
    payload = items()
    payload[-1]["planned_total_steps"] = 999
    with pytest.raises(ValueError):
        service.create(payload)
    assert list(repo.list_jobs()) == []


def fake_dispatch(monkeypatch, api, repo, tmp_path, failure_at=None):
    calls = []
    states = []
    def run(job_id, **kwargs):
        calls.append(job_id)
        assert kwargs["batch_owner"]
        # A new runner instance and environment for EVERY ordinary job.
        state = {"counter": 0}
        states.append(state)
        def runner(job, checkpoint):
            assert state["counter"] == 0
            state["counter"] += 1
            workspace = OperationsSimulationRunner(workspace_dir=tmp_path / "workspace_multi")._workspace_for_job(job)
            workspace.mkdir(parents=True)
            (workspace / "trace.json").write_text(json.dumps({"job": job_id}))
            repo.update_job(job_id, {"artifact_root": str(workspace)})
            if failure_at == len(calls):
                raise RuntimeError("model_quota_exhausted")
            checkpoint(completed_steps=20, last_complete_round=19, artifact_root=str(workspace))
            return {"completed_steps": 20, "last_complete_round": 19}
        result = OperationsWorker(repo, runner).run_job(job_id)
        return {"ok": True, "data": result}
    monkeypatch.setattr(api, "run_job", run)
    return calls, states


def test_all_arms_and_repeats_have_separate_runs_workspaces_and_clean_state(setup, monkeypatch, tmp_path):
    service, api, repo = setup
    record = service.create(items(), repeats=2)
    calls, states = fake_dispatch(monkeypatch, api, repo, tmp_path)
    while service.tick(record["batch_id"]):
        pass
    result = service.get(record["batch_id"])
    assert result["status"] == "completed"
    assert len(calls) == 8 and len({id(s) for s in states}) == 8
    assert [i["job_id"] for i in result["items"]] == calls
    assert len({i["run_id"] for i in result["items"]}) == 8
    assert len({i["artifact_root"] for i in result["items"]}) == 8
    for item in result["items"]:
        assert json.loads((batch.Path(item["artifact_root"]) / "trace.json").read_text())["job"] == item["job_id"]
    assert len(list(repo.list_jobs(status="running"))) == 0
    # Completed jobs can never be replayed by another tick.
    assert not service.tick(record["batch_id"])
    assert len(calls) == 8


def test_failure_pauses_and_resume_skips_failed_and_completed_jobs(setup, monkeypatch, tmp_path):
    service, api, repo = setup
    record = service.create(items())
    calls, _ = fake_dispatch(monkeypatch, api, repo, tmp_path, failure_at=2)
    while service.tick(record["batch_id"]):
        pass
    paused = service.get(record["batch_id"])
    assert paused["status"] == "paused"
    assert [i["status"] for i in paused["items"]] == ["completed", "failed", "pending", "pending"]
    assert repo.get_job(calls[-1])["finish_reason"] == "model_quota_exhausted"
    assert len(calls) == 2
    service.resume(record["batch_id"])
    while service.tick(record["batch_id"]):
        pass
    assert len(calls) == 4 and len(set(calls)) == 4
    assert service.get(record["batch_id"])["status"] == "finished_with_failures"


def test_pause_never_starts_the_next_item(setup, monkeypatch, tmp_path):
    service, api, repo = setup
    record = service.create(items())
    calls, _ = fake_dispatch(monkeypatch, api, repo, tmp_path)
    service.tick(record["batch_id"])
    service.pause(record["batch_id"])
    assert not service.tick(record["batch_id"])
    assert service.get(record["batch_id"])["status"] == "paused"
    assert len(calls) == 1
    service.resume(record["batch_id"])
    while service.tick(record["batch_id"]):
        pass
    assert len(calls) == 4


def test_manual_launch_and_second_batch_cannot_intrude_between_items(setup):
    service, api, repo = setup
    record = service.create(items())
    response = api.create_experiment(scenario_id="firm_ablation_fd2_ma", planned_total_steps=20)
    assert not response["ok"] and 'Batch' in response["error"]["message"]
    response = api.run_job(record["items"][0]["job_id"])
    assert not response["ok"]
    with pytest.raises(RuntimeError):
        service.create(items("fd2"))
    assert len(list(repo.list_jobs())) == 4


def test_dead_worker_does_not_count_as_success(setup, monkeypatch):
    service, api, repo = setup
    record = service.create(items())
    monkeypatch.setattr(api, "run_job", lambda *a, **k: {"ok": True})
    service.tick(record["batch_id"])
    assert not service.tick(record["batch_id"])
    assert service.get(record["batch_id"])["items"][0]["status"] == "failed"
    assert repo.get_job(record["items"][0]["job_id"])["status"] == "failed"


def test_terminal_status_waits_until_old_process_exits(setup, monkeypatch):
    service, api, repo = setup
    record = service.create(items())
    launched = []
    monkeypatch.setattr(api, "run_job", lambda job_id, **kw: launched.append(job_id) or {"ok": True})
    service.tick(record["batch_id"])
    job_id = launched[0]
    repo.update_job(job_id, {"status": "completed", "completed_steps": 20, "process_pid": 22222})
    monkeypatch.setattr(batch, "alive", lambda pid: pid == 22222)
    assert service.tick(record["batch_id"])
    assert launched == [job_id]
    monkeypatch.setattr(batch, "alive", lambda pid: False)
    assert service.tick(record["batch_id"])
    assert len(launched) == 2


def test_dead_supervisor_is_visible_and_resume_reattaches_existing_worker(setup, monkeypatch):
    service, api, repo = setup
    record = service.create(items())
    monkeypatch.setattr(api, "run_job", lambda *a, **k: {"ok": True})
    service.tick(record["batch_id"])
    current_id = record["items"][0]["job_id"]
    repo.update_job(current_id, {"status": "running", "process_pid": 22222})
    monkeypatch.setattr(batch, "alive", lambda pid: pid == 22222)
    assert service.list()[0]["status"] == "paused"
    service.resume(record["batch_id"])
    monkeypatch.setattr(api, "run_job", lambda *a, **k: pytest.fail("Must not duplicate current worker"))
    assert service.tick(record["batch_id"])
    assert service.get(record["batch_id"])["items"][0]["job_id"] == current_id


def test_frozen_job_has_identical_economics_to_manual_creation(setup):
    service, api, repo = setup
    manual = api.create_experiment(scenario_id="firm_ablation_fd2_ma", planned_total_steps=20)["data"]
    manual_config = repo.get_version(manual["config_version_id"])["scenario_config"]
    repo.update_job(manual["job_id"], {"status": "completed"})
    record = service.create(items("fd2", ("ma",)))
    job = repo.get_job(record["items"][0]["job_id"])
    automatic = repo.get_version(job["config_version_id"])["scenario_config"]
    automatic["experiment_design"].pop("batch_id")
    automatic["experiment_design"].pop("repeat_id")
    assert automatic == manual_config


def test_real_subprocesses_are_serial_and_have_distinct_process_ids(setup, monkeypatch, tmp_path):
    """Exercise the normal API subprocess dispatcher, replacing only model/business work."""
    import subprocess
    import sys
    from runtime.operations import api as api_module
    service, api, repo = setup
    monkeypatch.setattr(api_module, "WORKSPACE_JOBS_ROOT", tmp_path / "jobs")
    worker = tmp_path / "fixture_worker.py"
    worker.write_text('''
import json, os, sys
from pathlib import Path
from persistence.sql_mirror import SQLiteMirrorRepository
from runtime.operations.worker import OperationsWorker
from runtime.operations.runner_adapter import OperationsSimulationRunner
repo = SQLiteMirrorRepository(sys.argv[2])
def runner(job, checkpoint):
    root = OperationsSimulationRunner(workspace_dir=Path(sys.argv[3]) / "workspace_multi")._workspace_for_job(job)
    root.mkdir(parents=True)
    (root / "trace.json").write_text(json.dumps({"pid": os.getpid(), "fresh_counter": 1, "job_id": job["job_id"]}))
    repo.update_job(job["job_id"], {"artifact_root": str(root)})
    checkpoint(completed_steps=20, last_complete_round=19, artifact_root=str(root))
    return {"completed_steps": 20, "last_complete_round": 19}
OperationsWorker(repo, runner).run_job(sys.argv[1])
''')
    real_popen = subprocess.Popen
    processes = []
    def launch(command, **kwargs):
        assert command[1:4] == ["-m", "runtime.operations.cli", "run-job"]
        assert all(process.poll() is not None for process in processes)
        process = real_popen([sys.executable, str(worker), command[4], str(tmp_path / "ops.sqlite"), str(tmp_path)], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(api_module.subprocess, "Popen", launch)
    record = service.create(items())
    try:
        service.run(record["batch_id"], poll_seconds=0.02)
    finally:
        for process in processes:
            process.wait(timeout=10)
    result = service.get(record["batch_id"])
    assert result["status"] == "completed", result
    assert len(processes) == len({p.pid for p in processes}) == 4
    for item, process in zip(result["items"], processes):
        trace = json.loads((batch.Path(item["artifact_root"]) / "trace.json").read_text())
        assert trace["pid"] == process.pid and trace["job_id"] == item["job_id"]
        assert repo.get_job(item["job_id"])["execution_mode"] == "agent_subprocess"


def test_router_submission_progress_pause_and_resume(setup, monkeypatch):
    # Invoke the registered handlers directly: no server, model or thread portal needed.
    from pydantic import ValidationError
    from runtime.operations.http_router import create_operations_router, CreateBatchRequest, CreateExperimentRequest
    service, api, repo = setup
    monkeypatch.setattr(BatchService, "_launch", lambda self, record: service._launch(record))
    from runtime.operations import http_router
    monkeypatch.setattr(http_router, "private_results", type("OperatorScope", (), {"get": staticmethod(lambda: True)})())
    router = create_operations_router(api=api)
    def endpoint(path, method):
        return next(route.endpoint for route in router.routes if route.path == path and method in route.methods)
    response = endpoint("/operations/batches", "POST")(CreateBatchRequest(items=items(), repeats=1))
    assert response.status_code == 200, response.body
    record = json.loads(response.body)["data"]
    assert len(record["items"]) == 4
    monkeypatch.setattr(batch, "alive", lambda pid: pid == 999999999)
    listed = json.loads(endpoint("/operations/batches", "GET")().body)["data"]
    assert listed[0]["status"] == "running"
    blocked = endpoint("/operations/jobs", "POST")(CreateExperimentRequest(**items("fd2", ("ma",))[0]))
    assert blocked.status_code != 200
    paused = json.loads(endpoint("/operations/batches/{batch_id}/pause", "POST")(record["batch_id"]).body)["data"]
    assert paused["status"] == "pause_requested"
    assert not service.tick(record["batch_id"])
    monkeypatch.setattr(batch, "alive", lambda pid: False)
    resumed = endpoint("/operations/batches/{batch_id}/resume", "POST")(record["batch_id"])
    assert resumed.status_code == 200
    assert len(list(repo.list_jobs())) == 4
    with pytest.raises(ValidationError):
        CreateBatchRequest(items=items(), repeats=0)


def test_dispatch_failure_pauses_with_failed_item_visible(setup, monkeypatch):
    service, api, repo = setup
    record = service.create(items())
    monkeypatch.setattr(api, "run_job", lambda *a, **k: {"ok": False, "error": {"message": "spawn unavailable"}})
    assert not service.tick(record["batch_id"])
    failed = service.get(record["batch_id"])
    assert failed["status"] == "paused"
    assert failed["items"][0]["status"] == "failed"
    assert failed["items"][1]["status"] == "pending"
    assert repo.get_job(record["items"][0]["job_id"])["status"] == "failed"


def test_premature_completion_is_a_failure(setup, monkeypatch):
    service, api, repo = setup
    record = service.create(items())
    monkeypatch.setattr(api, "run_job", lambda *a, **k: {"ok": True})
    service.tick(record["batch_id"])
    job_id = record["items"][0]["job_id"]
    repo.update_job(job_id, {"status": "completed", "completed_steps": 12, "last_complete_round": 11})
    assert not service.tick(record["batch_id"])
    assert repo.get_job(job_id)["status"] == "failed"
    assert repo.get_job(job_id)["completed_steps"] == 12


def test_missing_steps_default_to_twenty(setup):
    service, _, repo = setup
    payload = items("fd1", ("ma",))
    payload[0].pop("planned_total_steps")
    record = service.create(payload)
    assert repo.get_job(record["items"][0]["job_id"])["planned_total_steps"] == 20
