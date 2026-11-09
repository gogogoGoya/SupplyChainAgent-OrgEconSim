"""Lightweight JSON-style API wrapper for operations calls."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Callable, Dict, List, Optional

from config.integration_profiles import get_scenario_integration_profiles
from config.platform_access import require_execution_enabled
from config.project_paths import PROJECT_ROOT, WORKSPACE_JOBS_ROOT, resolve_project_path
from persistence.sql_mirror import SQLiteMirrorRepository
from persistence.sql_mirror_runtime import get_sql_mirror_repository

from .facade import OperationsFacade
from .runner_adapter import OperationsSimulationRunner
from .worker import OperationsWorker
from .provenance import capture_provenance, assert_provenance
from .process_health import process_alive, process_identity


RepositoryFactory = Callable[[Dict[str, Any]], Optional[Any]]
ProfileLoader = Callable[[Optional[str]], Dict[str, Any]]
RunnerFactory = Callable[..., Any]
OPERATIONS_SQLITE_PATH_ENV = "SIMULATION_OPERATIONS_SQLITE_PATH"
OPERATIONS_CHILD_WORKER_ENV = "SIMULATION_OPERATIONS_CHILD_WORKER"
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
AGENT_ENV_KEYS = {
    "ANTHROPIC_BASE_URL",
    "AGENT_GATEWAY_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_CEO_MODEL",
    "ANTHROPIC_MODEL_LIST",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "NO_PROXY",
    "no_proxy",
}


def _local_timestamp() -> str:
    return datetime.now(LOCAL_TIMEZONE).isoformat(timespec="seconds")


def _env_flag_enabled(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _load_runtime_env_file_values(project_root: Path) -> Dict[str, str]:
    env_path = project_root / "scripts" / ".env"
    if not env_path.exists():
        return {}
    values: Dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            values[key] = value
    return values


def _refresh_agent_env_for_child(env: Dict[str, str], project_root: Path) -> None:
    """Read current file settings for each new worker, ahead of inherited values.

    The backend may have started before a gateway/key rotation. Keep its own
    environment untouched, but override the worker's Agent settings with the
    non-empty values explicitly configured in scripts/.env.
    """
    require_execution_enabled()
    file_values = {
        key: value
        for key, value in _load_runtime_env_file_values(project_root).items()
        if key in AGENT_ENV_KEYS
    }
    if not file_values:
        return

    env.update(file_values)
    file_agent_base = file_values.get("ANTHROPIC_BASE_URL") or file_values.get(
        "AGENT_GATEWAY_BASE_URL"
    )
    if file_agent_base:
        env["ANTHROPIC_BASE_URL"] = file_agent_base
        env["AGENT_GATEWAY_BASE_URL"] = file_agent_base

    # EnvironmentConfig prefers AUTH_TOKEN. A new API_KEY alone must also
    # replace any inherited AUTH_TOKEN, and vice versa.
    file_auth = file_values.get("ANTHROPIC_AUTH_TOKEN") or file_values.get("ANTHROPIC_API_KEY")
    if file_auth:
        env["ANTHROPIC_AUTH_TOKEN"] = file_auth
        env["ANTHROPIC_API_KEY"] = file_auth

    if file_values.get("ANTHROPIC_MODEL"):
        # These optional selectors otherwise keep using inherited old models.
        for key in ("ANTHROPIC_MODEL_LIST", "ANTHROPIC_CEO_MODEL"):
            if key not in file_values:
                env.pop(key, None)


def _redact_agent_env(env: Dict[str, str]) -> Dict[str, str]:
    snapshot: Dict[str, str] = {}
    for key in sorted(AGENT_ENV_KEYS):
        if key not in env:
            continue
        value = str(env.get(key) or "")
        if any(marker in key for marker in ("TOKEN", "KEY", "SECRET")):
            snapshot[key] = "<set>" if value else "<unset>"
        else:
            snapshot[key] = value
    return snapshot


def _write_agent_env_snapshot(path: Path, env: Dict[str, str]) -> None:
    payload = {
        "created_at": _local_timestamp(),
        "agent_env": _redact_agent_env(env),
    }
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


class OperationsAPI:
    """
    Small in-process API surface for CLI, HTTP adapters and tests.

    The API normalizes success/error responses but delegates all state
    transitions to OperationsFacade/OperationsService.
    """

    def __init__(
        self,
        repository: Any = None,
        *,
        repository_factory: RepositoryFactory = None,
        profile_loader: ProfileLoader = None,
        scenario_loader: Callable[[str], Dict[str, Any]] = None,
        runner_factory: RunnerFactory = None,
    ):
        self.repository = repository
        self.repository_factory = repository_factory or get_sql_mirror_repository
        self.profile_loader = profile_loader or get_scenario_integration_profiles
        self.scenario_loader = scenario_loader
        self.runner_factory = runner_factory or OperationsSimulationRunner

    def create_experiment(
        self,
        *,
        scenario_id: str,
        planned_total_steps: Optional[int] = None,
        tags: Optional[List[str]] = None,
        requested_by: Optional[str] = None,
        job_id: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._capture_control(
            lambda: self._facade(scenario_id).create_experiment_from_scenario(
                scenario_id=scenario_id,
                planned_total_steps=planned_total_steps,
                tags=tags,
                requested_by=requested_by,
                job_id=job_id,
                config_overrides=config_overrides,
            )
        )

    def start_experiment(
        self,
        job_id: str,
        *,
        scenario_id: Optional[str] = None,
        run_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        artifact_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._capture_control(
            lambda: self._facade(scenario_id).start_experiment(
                job_id,
                run_id=run_id,
                worker_id=worker_id,
                artifact_root=artifact_root,
            )
        )

    def request_stop(
        self,
        job_id: str,
        *,
        reason: str,
        scenario_id: Optional[str] = None,
        requested_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._capture(
            lambda: self._facade(scenario_id).request_stop(
                job_id,
                reason=reason,
                requested_by=requested_by,
            )
        )

    def resume_experiment(
        self,
        job_id: str,
        *,
        scenario_id: Optional[str] = None,
        target_total_steps: Optional[int] = None,
        requested_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._capture_control(
            lambda: self._facade(scenario_id).resume_experiment(
                job_id,
                target_total_steps=target_total_steps,
                requested_by=requested_by,
            )
        )

    def import_resumable_experiment(
        self,
        *,
        scenario_id: str,
        artifact_root: str,
        run_meta: Dict[str, Any],
        last_complete_round: int,
        target_total_steps: int = 200,
        requested_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._capture_control(
            lambda: self._facade(scenario_id).import_resumable_experiment(
                artifact_root=artifact_root,
                run_meta=run_meta,
                last_complete_round=last_complete_round,
                target_total_steps=target_total_steps,
                requested_by=requested_by,
            )
        )

    def record_checkpoint(
        self,
        job_id: str,
        *,
        scenario_id: Optional[str] = None,
        completed_steps: int,
        last_complete_round: Optional[int],
        checkpoint_uri: Optional[str] = None,
        artifact_root: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._capture(
            lambda: self._facade(scenario_id).record_checkpoint(
                job_id,
                completed_steps=completed_steps,
                last_complete_round=last_complete_round,
                checkpoint_uri=checkpoint_uri,
                artifact_root=artifact_root,
                details=details,
            )
        )

    def run_job(
        self,
        job_id: str,
        *,
        scenario_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        client_dir: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        batch_owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        def callback() -> Any:
            require_execution_enabled()
            repository = self._repository(scenario_id)
            job = repository.get_job(job_id)
            if not job:
                raise ValueError(f"Unknown job_id: {job_id}")
            if job.get("status") == "batch_pending":
                raise ValueError('Batch-pending jobs can only be started by the batch scheduler.')
            if batch_owner and job.get("batch_id") != batch_owner:
                raise ValueError("Batch does not own this job")
            is_scripted_job = self._is_scripted_job(repository, job)
            if not _env_flag_enabled(os.getenv(OPERATIONS_CHILD_WORKER_ENV)):
                if not is_scripted_job:
                    self._assert_agent_run_allowed(repository, job)
                return self._dispatch_subprocess(
                    repository,
                    job,
                    scenario_id=scenario_id,
                    worker_id=worker_id,
                    client_dir=client_dir,
                    workspace_dir=workspace_dir,
                    execution_mode=(
                        "scripted_subprocess" if is_scripted_job else "agent_subprocess"
                    ),
                )
            if not is_scripted_job:
                self._assert_agent_run_allowed(repository, job)
            runner = self.runner_factory(
                client_dir=client_dir,
                workspace_dir=workspace_dir,
            )
            return OperationsWorker(
                repository,
                runner,
                worker_id=worker_id or "operations-http-worker",
            ).run_job(job_id)

        if _env_flag_enabled(os.getenv(OPERATIONS_CHILD_WORKER_ENV)):
            return self._capture(callback)
        return self._capture_control(callback, batch_owner=batch_owner)

    def _is_scripted_job(self, repository: Any, job: Dict[str, Any]) -> bool:
        tags = {str(tag).strip().lower() for tag in (job.get("tags") or [])}
        if "preset:scripted" in tags:
            return True
        overrides = job.get("config_overrides") or {}
        scripted_policy = (
            ((overrides.get("runtime_injection") or {}).get("scripted_rule_policy"))
            or {}
        )
        if scripted_policy.get("enabled") is True:
            return True
        config_version = self._get_job_config_version(repository, job)
        scenario_config = config_version.get("scenario_config") or {}
        runtime_injection = scenario_config.get("runtime_injection") or {}
        scenario_scripted_policy = runtime_injection.get("scripted_rule_policy") or {}
        return scenario_scripted_policy.get("enabled") is True

    def _get_job_config_version(self, repository: Any, job: Dict[str, Any]) -> Dict[str, Any]:
        version_id = job.get("config_version_id")
        if not version_id or not hasattr(repository, "get_version"):
            return {}
        return repository.get_version(str(version_id)) or {}

    def _assert_agent_run_allowed(self, repository: Any, job: Dict[str, Any]) -> None:
        if self._is_scripted_job(repository, job):
            return
        job_id = str(job.get("job_id") or "")
        active_statuses = {"queued", "running", "stop_requested"}
        for active_job in repository.list_jobs(limit=None):
            active_job_id = str(active_job.get("job_id") or "")
            if active_job_id == job_id:
                continue
            if str(active_job.get("status") or "") not in active_statuses:
                continue
            if active_job.get("process_pid") and not process_alive(active_job["process_pid"], active_job.get("process_identity")):
                continue
            if active_job.get("status") == "queued" and not active_job.get("process_dispatched_at"):
                continue
            if self._is_scripted_job(repository, active_job):
                continue
            raise RuntimeError(
                "Another Agent Skill job is already active. "
                f"Stop or finish job {active_job_id} before starting a new Agent Skill run."
            )

    def _dispatch_subprocess(
        self,
        repository: Any,
        job: Dict[str, Any],
        *,
        scenario_id: Optional[str],
        worker_id: Optional[str],
        client_dir: Optional[str],
        workspace_dir: Optional[str],
        execution_mode: str,
    ) -> Dict[str, Any]:
        status = str(job.get("status") or "")
        if status in {"completed", "failed", "stopped"}:
            return {
                "job_id": job.get("job_id"),
                "status": status,
                "message": "terminal job was not relaunched",
            }
        existing_pid = job.get("process_pid")
        if existing_pid and status in {"queued", "running", "stop_requested"}:
            if process_alive(existing_pid, job.get("process_identity")):
                return {
                    "job_id": job.get("job_id"),
                    "status": status,
                    "process_pid": existing_pid,
                    "message": "job is already dispatched",
                }
            repository.update_job(
                str(job.get("job_id")),
                {
                    "process_pid": None,
                    "process_stale_observed_at": _local_timestamp(),
                },
            )

        project_root = PROJECT_ROOT
        logs_dir = WORKSPACE_JOBS_ROOT / "_process_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        job_id = str(job.get("job_id"))
        stdout_path = logs_dir / f"{job_id}.log"
        stderr_path = logs_dir / f"{job_id}.err.log"
        agent_env_path = logs_dir / f"{job_id}.agent_env.json"
        command = [
            sys.executable,
            "-m",
            "runtime.operations.cli",
            "run-job",
            job_id,
        ]
        resolved_scenario_id = scenario_id or job.get("scenario_id")
        if resolved_scenario_id:
            command.extend(["--scenario-id", str(resolved_scenario_id)])
        command.extend(["--worker-id", worker_id or "operations-scripted-process"])
        if client_dir:
            command.extend(["--client-dir", str(client_dir)])
        if workspace_dir:
            command.extend(["--workspace-dir", str(workspace_dir)])

        env = os.environ.copy()
        env[OPERATIONS_CHILD_WORKER_ENV] = "1"
        env.setdefault("PYTHONPATH", str(project_root))
        if str(project_root) not in env["PYTHONPATH"].split(os.pathsep):
            env["PYTHONPATH"] = f"{project_root}{os.pathsep}{env['PYTHONPATH']}"
        _refresh_agent_env_for_child(env, project_root)
        provenance = capture_provenance(project_root, env)
        assert_provenance(job.get("execution_provenance"), provenance)
        get_version = getattr(repository, "get_version", None)
        version = get_version(job.get("config_version_id")) if callable(get_version) else None
        if isinstance(version, dict):
            provenance["scenario_config_sha256"] = hashlib.sha256(json.dumps(
                {"scenario_config": version.get("scenario_config"), "integration_profiles": version.get("integration_profiles")},
                sort_keys=True, default=str).encode()).hexdigest()
        _write_agent_env_snapshot(agent_env_path, env)

        stdout_handle = stdout_path.open("a", encoding="utf-8")
        stderr_handle = stderr_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(project_root),
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                start_new_session=True,
            )
        finally:
            stdout_handle.close()
            stderr_handle.close()

        updates = {
            "process_pid": process.pid,
            "process_identity": process_identity(process.pid),
            "execution_provenance": provenance,
            "process_worker_id": worker_id or "operations-scripted-process",
            "process_log": str(stdout_path),
            "process_error_log": str(stderr_path),
            "process_agent_env_log": str(agent_env_path),
            "process_dispatched_at": _local_timestamp(),
            "execution_mode": execution_mode,
        }
        repository.update_job(job_id, updates)
        return {
            "job_id": job_id,
            "status": job.get("status") or "queued",
            "process_pid": process.pid,
            "execution_mode": execution_mode,
            "message": "job dispatched to an isolated subprocess",
        }

    @staticmethod
    def _process_is_alive(pid: Any) -> bool:
        try:
            os.kill(int(pid), 0)
            return True
        except (TypeError, ValueError, ProcessLookupError):
            return False
        except PermissionError:
            return True

    def list_jobs(
        self,
        *,
        scenario_id: Optional[str] = None,
        status: Optional[str] = None,
        active_only: bool = False,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._capture(
            lambda: self._facade(scenario_id).list_jobs(
                status=status,
                active_only=active_only,
                limit=limit,
            )
        )

    def get_job(
        self,
        job_id: str,
        *,
        scenario_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._capture(lambda: self._facade(scenario_id).get_job(job_id))

    def reconcile_jobs(self):
        def callback():
            require_execution_enabled()
            repository = self._repository()
            from .service import OperationsService
            changed = []
            for job in repository.list_jobs(limit=None):
                if job.get("status") in {"queued", "running", "stop_requested"} and job.get("process_pid") and not process_alive(job["process_pid"], job.get("process_identity")):
                    changed.append(OperationsService(repository).mark_failed(
                        job["job_id"], error="Worker exited without a terminal receipt; sealed rounds retained",
                        completed_steps=int(job.get("completed_steps") or 0), last_complete_round=job.get("last_complete_round")))
            return changed
        return self._capture(callback)

    def delete_job(
        self,
        job_id: str,
        *,
        scenario_id: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        return self._capture(
            lambda: self._facade(scenario_id).delete_job(job_id, force=force)
        )

    def _facade(self, scenario_id: Optional[str] = None) -> OperationsFacade:
        return OperationsFacade(
            self._repository(scenario_id),
            scenario_loader=self.scenario_loader,
            profile_loader=self.profile_loader,
        )

    def _repository(self, scenario_id: Optional[str] = None) -> Any:
        if self.repository is not None:
            return self.repository
        profiles = self.profile_loader(scenario_id)
        repository = self.repository_factory(profiles)
        if repository is None and os.getenv(OPERATIONS_SQLITE_PATH_ENV):
            repository = SQLiteMirrorRepository(
                resolve_project_path(
                    os.environ[OPERATIONS_SQLITE_PATH_ENV],
                    relative_to=PROJECT_ROOT,
                )
            )
        if repository is None:
            raise RuntimeError(
                "Operations repository is not configured. Enable a SQL mirror "
                "profile and set SIMULATION_SQL_MIRROR_PATH or "
                "SIMULATION_POSTGRES_DSN, set SIMULATION_OPERATIONS_SQLITE_PATH "
                "for local operations, or inject a repository."
            )
        return repository

    def _capture(self, callback: Callable[[], Any]) -> Dict[str, Any]:
        try:
            return {"ok": True, "data": callback()}
        except Exception as exc:
            return {
                "ok": False,
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
            }

    def _capture_control(self, callback: Callable[[], Any], *, batch_owner=None):
        from .batch import BatchService, control_lock

        def guarded():
            require_execution_enabled()
            with control_lock():
                BatchService(self).assert_available(owner=batch_owner)
                return callback()
        return self._capture(guarded)
