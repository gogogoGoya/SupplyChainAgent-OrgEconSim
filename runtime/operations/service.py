"""Repository-backed operations service for simulation experiments."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import os
import signal
import time
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo


OPERATIONS_SCHEMA_VERSION = "operations_job.v1"
CONFIG_VERSION_SCHEMA_VERSION = "operations_config_version.v1"
RUN_METADATA_SCHEMA_VERSION = "operations_run_metadata.v1"

TERMINAL_STATUSES = {"completed", "failed", "stopped"}
VALID_STATUSES = {
    "batch_pending",
    "queued",
    "running",
    "stop_requested",
    "completed",
    "failed",
    "stopped",
}


LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _local_timestamp() -> str:
    return datetime.now(LOCAL_TIMEZONE).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class OperationsService:
    """
    Minimal operations layer over repository protocols.

    This service records experiment intent and run state. It does not start a
    worker by itself; API/worker layers can call it before and after invoking
    the existing simulation managers.
    """

    def __init__(self, repository: Any):
        self.repository = repository

    def create_experiment(
        self,
        *,
        scenario_id: str,
        scenario_config: Dict[str, Any],
        integration_profiles: Dict[str, Any],
        planned_total_steps: Optional[int] = None,
        tags: Optional[List[str]] = None,
        requested_by: Optional[str] = None,
        job_id: Optional[str] = None,
        config_version_id: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not scenario_id:
            raise ValueError("scenario_id is required")
        created_at = _local_timestamp()
        job_id = job_id or _new_id("job")
        config_version_id = config_version_id or f"{job_id}:config"
        config_version = {
            "schema_version": CONFIG_VERSION_SCHEMA_VERSION,
            "version_id": config_version_id,
            "scenario_id": scenario_id,
            "scenario_config": deepcopy(scenario_config or {}),
            "integration_profiles": deepcopy(integration_profiles or {}),
            "config_overrides": deepcopy(config_overrides or {}),
            "created_at": created_at,
            "created_by": requested_by,
            "immutable": True,
        }
        job = {
            "schema_version": OPERATIONS_SCHEMA_VERSION,
            "job_id": job_id,
            "status": "queued",
            "scenario_id": scenario_id,
            "config_version_id": config_version_id,
            "planned_total_steps": planned_total_steps,
            "tags": list(tags or []),
            "config_overrides": deepcopy(config_overrides or {}),
            "requested_by": requested_by,
            "created_at": created_at,
            "updated_at": created_at,
            "run_id": None,
            "stop_request": None,
        }
        self.repository.save_version(config_version)
        self.repository.create_job(job)
        return job

    def start_experiment(
        self,
        job_id: str,
        *,
        run_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        artifact_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        job = self._require_job(job_id)
        if job.get("status") not in {"queued", "stopped", "failed"}:
            raise ValueError(f"Cannot start job from status {job.get('status')}")
        now = _local_timestamp()
        run_id = run_id or _new_id("run")
        changes = {
            "status": "running",
            "run_id": run_id,
            "worker_id": worker_id,
            "artifact_root": artifact_root,
            "started_at": now,
            "updated_at": now,
        }
        self.repository.update_job(job_id, changes)
        run_metadata = {
            "schema_version": RUN_METADATA_SCHEMA_VERSION,
            "run_id": run_id,
            "job_id": job_id,
            "status": "running",
            "scenario_id": job.get("scenario_id"),
            "config_version_id": job.get("config_version_id"),
            "planned_total_steps": job.get("planned_total_steps"),
            "artifact_root": artifact_root,
            "started_at": now,
        }
        self.repository.create_run(run_metadata)
        return self._require_job(job_id)

    def request_stop(
        self,
        job_id: str,
        *,
        reason: str,
        requested_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        job = self._require_job(job_id)
        if job.get("status") in TERMINAL_STATUSES:
            raise ValueError(f"Cannot request stop for terminal job {job_id}")
        now = _local_timestamp()
        stop_request = {
            "requested_at": now,
            "requested_by": requested_by,
            "reason": reason,
            "policy": "seal_last_complete_round",
        }
        self.repository.update_job(
            job_id,
            {
                "status": "stop_requested",
                "stop_request": stop_request,
                "updated_at": now,
            },
        )
        run_id = job.get("run_id")
        if run_id:
            self.repository.update_run(
                run_id,
                {
                    "status": "stop_requested",
                    "stop_request": stop_request,
                    "updated_at": now,
                },
            )
        return self._require_job(job_id)

    def resume_experiment(
        self,
        job_id: str,
        *,
        target_total_steps: Optional[int] = None,
        requested_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queue a sealed run for continuation from its last full checkpoint."""
        job = self._require_job(job_id)
        if job.get("status") not in {"stopped", "failed"}:
            raise ValueError(f"Cannot resume job from status {job.get('status')}")
        if str(job.get("scenario_id") or "") != "long_horizon_evolution":
            raise ValueError("Runtime resume is currently limited to long_horizon_evolution")
        latest_checkpoint = job.get("latest_checkpoint") or {}
        last_complete_round = latest_checkpoint.get(
            "last_complete_round",
            job.get("last_complete_round"),
        )
        completed_steps = int(
            latest_checkpoint.get("completed_steps")
            or job.get("completed_steps")
            or 0
        )
        if last_complete_round is None or completed_steps <= 0:
            raise ValueError("Cannot resume without a complete-round checkpoint")
        planned_total_steps = int(
            target_total_steps
            or job.get("planned_total_steps")
            or 200
        )
        if planned_total_steps <= completed_steps:
            raise ValueError(
                "Resume target_total_steps must exceed completed_steps "
                f"({completed_steps})"
            )
        artifact_root = (
            latest_checkpoint.get("artifact_root")
            or job.get("artifact_root")
        )
        if not artifact_root:
            checkpoint_uri = str(latest_checkpoint.get("checkpoint_uri") or "")
            if checkpoint_uri.startswith("file://"):
                raw_path = checkpoint_uri.removeprefix("file://")
                artifact_root = (
                    raw_path.rsplit("/", 1)[0]
                    if raw_path.endswith((".pkl", ".pickle"))
                    else raw_path
                )
        if not artifact_root:
            raise ValueError("Cannot resume without an artifact_root")
        now = _local_timestamp()
        resume_context = {
            "schema_version": "operations_resume.v1",
            "requested_at": now,
            "requested_by": requested_by,
            "source_run_id": job.get("run_id"),
            "artifact_root": artifact_root,
            "completed_steps": completed_steps,
            "last_complete_round": int(last_complete_round),
            "target_total_steps": planned_total_steps,
            "checkpoint_uri": latest_checkpoint.get("checkpoint_uri"),
        }
        history = list(job.get("resume_history") or [])
        history.append(deepcopy(resume_context))
        self.repository.update_job(job_id, {
            "status": "queued",
            "planned_total_steps": planned_total_steps,
            "artifact_root": artifact_root,
            "resume_context": resume_context,
            "resume_history": history,
            "stop_request": None,
            "stop_reason": None,
            "finish_reason": None,
            "error": None,
            "finished_at": None,
            "updated_at": now,
        })
        run_id = job.get("run_id")
        if run_id:
            self.repository.update_run(run_id, {
                "status": "queued",
                "planned_total_steps": planned_total_steps,
                "resume_context": resume_context,
                "stop_request": None,
                "stop_reason": None,
                "finish_reason": None,
                "error": None,
                "finished_at": None,
                "updated_at": now,
            })
        return self._require_job(job_id)

    def import_resumable_experiment(
        self,
        *,
        artifact_root: str,
        run_meta: Dict[str, Any],
        last_complete_round: int,
        target_total_steps: int = 200,
        requested_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a sealed E1 filesystem archive and queue it for resume."""
        scenario_id = str(run_meta.get("scenario_id") or "")
        if scenario_id != "long_horizon_evolution":
            raise ValueError("Only long_horizon_evolution archives can be resumed")
        run_id = str(run_meta.get("run_id") or "")
        if not run_id:
            raise ValueError("Archived run_meta.json does not contain run_id")
        completed_steps = int(last_complete_round) + 1
        if completed_steps <= 0 or completed_steps >= int(target_total_steps):
            raise ValueError("Archived run does not contain a resumable sealed round")
        scenario_config = deepcopy(run_meta.get("scenario_config") or {})
        if not scenario_config:
            raise ValueError("Archived run_meta.json does not contain scenario_config")
        integration_profiles = deepcopy(
            run_meta.get("integration_profiles")
            or scenario_config.get("integration_profiles")
            or {}
        )
        job_id = str(run_meta.get("job_id") or "") or _new_id("job")
        existing = self.repository.get_job(job_id)
        if existing and existing.get("status") in {"queued", "running", "stop_requested"}:
            raise ValueError(f"Cannot import over active job {job_id}")
        if not existing:
            self.create_experiment(
                scenario_id=scenario_id,
                scenario_config=scenario_config,
                integration_profiles=integration_profiles,
                planned_total_steps=int(target_total_steps),
                tags=["imported_archive", "resume"],
                requested_by=requested_by,
                job_id=job_id,
            )
        checkpoint_path = os.path.join(artifact_root, "_runtime_checkpoint.pkl")
        checkpoint_uri = (
            f"file://{checkpoint_path}"
            if os.path.exists(checkpoint_path)
            else f"file://{artifact_root}"
        )
        now = _local_timestamp()
        checkpoint = {
            "checkpointed_at": now,
            "completed_steps": completed_steps,
            "last_complete_round": int(last_complete_round),
            "checkpoint_uri": checkpoint_uri,
            "artifact_root": artifact_root,
            "details": {
                "orchestrator": "multi_enterprise",
                "scenario_id": scenario_id,
                "imported_from_archive": True,
            },
        }
        self.repository.update_job(job_id, {
            "status": "stopped",
            "run_id": run_id,
            "artifact_root": artifact_root,
            "planned_total_steps": int(target_total_steps),
            "completed_steps": completed_steps,
            "last_complete_round": int(last_complete_round),
            "latest_checkpoint": checkpoint,
            "checkpoint_history": [checkpoint],
            "started_at": run_meta.get("started_at"),
            "finished_at": run_meta.get("finished_at"),
            "finish_reason": "imported sealed archive",
            "updated_at": now,
        })
        archived_run = {
            "schema_version": RUN_METADATA_SCHEMA_VERSION,
            "run_id": run_id,
            "job_id": job_id,
            "status": "stopped",
            "scenario_id": scenario_id,
            "config_version_id": self._require_job(job_id).get("config_version_id"),
            "planned_total_steps": int(target_total_steps),
            "completed_steps": completed_steps,
            "last_complete_round": int(last_complete_round),
            "latest_checkpoint": checkpoint,
            "artifact_root": artifact_root,
            "started_at": run_meta.get("started_at"),
            "finished_at": run_meta.get("finished_at"),
            "imported_at": now,
        }
        if self.repository.get_run(run_id):
            self.repository.update_run(run_id, archived_run)
        else:
            self.repository.create_run(archived_run)
        return self.resume_experiment(
            job_id,
            target_total_steps=int(target_total_steps),
            requested_by=requested_by,
        )

    def mark_stopped(
        self,
        job_id: str,
        *,
        completed_steps: int,
        last_complete_round: Optional[int],
        reason: str,
    ) -> Dict[str, Any]:
        return self._finish_job(
            job_id,
            status="stopped",
            completed_steps=completed_steps,
            last_complete_round=last_complete_round,
            finish_reason=reason,
        )

    def mark_completed(
        self,
        job_id: str,
        *,
        completed_steps: int,
        last_complete_round: Optional[int],
    ) -> Dict[str, Any]:
        job = self._require_job(job_id)
        planned = job.get("planned_total_steps")
        checkpoint = job.get("latest_checkpoint") or {}
        invalid = (
            int(completed_steps) <= 0
            or (planned is not None and int(completed_steps) != int(planned))
            or last_complete_round != int(completed_steps) - 1
            or (checkpoint and int(checkpoint.get("completed_steps", -1)) != int(completed_steps))
        )
        if invalid:
            return self.mark_failed(
                job_id, completed_steps=completed_steps, last_complete_round=last_complete_round,
                error="Incomplete execution: completed rounds, final sealed round and planned rounds do not agree",
            )
        return self._finish_job(
            job_id,
            status="completed",
            completed_steps=completed_steps,
            last_complete_round=last_complete_round,
            finish_reason="completed",
        )

    def mark_failed(
        self,
        job_id: str,
        *,
        error: str,
        completed_steps: int = 0,
        last_complete_round: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._finish_job(
            job_id,
            status="failed",
            completed_steps=completed_steps,
            last_complete_round=last_complete_round,
            finish_reason=error,
        )

    def record_checkpoint(
        self,
        job_id: str,
        *,
        completed_steps: int,
        last_complete_round: Optional[int],
        checkpoint_uri: Optional[str] = None,
        artifact_root: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        job = self._require_job(job_id)
        if job.get("status") not in {"running", "stop_requested"}:
            raise ValueError(f"Cannot checkpoint job from status {job.get('status')}")
        now = _local_timestamp()
        checkpoint = {
            "checkpointed_at": now,
            "completed_steps": int(completed_steps),
            "last_complete_round": last_complete_round,
            "checkpoint_uri": checkpoint_uri,
            "artifact_root": artifact_root or job.get("artifact_root"),
            "details": deepcopy(details or {}),
        }
        history = list(job.get("checkpoint_history") or [])
        history.append(checkpoint)
        changes = {
            "completed_steps": int(completed_steps),
            "last_complete_round": last_complete_round,
            "latest_checkpoint": checkpoint,
            "checkpoint_history": history,
            "updated_at": now,
        }
        self.repository.update_job(job_id, changes)
        run_id = job.get("run_id")
        if run_id:
            self.repository.update_run(
                run_id,
                {
                    "completed_steps": int(completed_steps),
                    "last_complete_round": last_complete_round,
                    "latest_checkpoint": checkpoint,
                    "updated_at": now,
                },
            )
        return self._require_job(job_id)

    def recover_jobs(
        self,
        *,
        status: Optional[str] = None,
        active_only: bool = False,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        jobs = list(self.repository.list_jobs(status=status, limit=limit))
        if active_only:
            jobs = [
                job
                for job in jobs
                if job.get("status") in {"queued", "running", "stop_requested"}
            ]
        return jobs

    def get_job(self, job_id: str) -> Dict[str, Any]:
        return self._require_job(job_id)

    def delete_job(self, job_id: str, *, force: bool = False) -> Dict[str, Any]:
        job = self._require_job(job_id)
        if job.get("status") not in TERMINAL_STATUSES and not force:
            raise ValueError(f"Cannot delete non-terminal job {job_id}")
        if not hasattr(self.repository, "delete_job"):
            raise RuntimeError("Operations repository does not support delete_job")
        termination = self._terminate_job_process(job) if force else {}
        self.repository.delete_job(job_id)
        deleted = deepcopy(job)
        deleted["deleted"] = True
        deleted["forced"] = bool(force)
        if termination:
            deleted["termination"] = termination
        return deleted

    def _terminate_job_process(self, job: Dict[str, Any]) -> Dict[str, Any]:
        pid = job.get("process_pid")
        if not pid:
            return {"attempted": False, "reason": "no process_pid recorded"}
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            return {"attempted": False, "reason": f"invalid process_pid: {pid}"}

        from .process_health import process_identity
        expected = job.get("process_identity")
        actual = process_identity(pid_int)
        if expected and actual and expected.get("start_ticks") != actual.get("start_ticks"):
            return {"attempted": False, "terminated": False, "reason": "PID was reused; unrelated process left untouched"}
        result = {"attempted": True, "process_pid": pid_int, "terminated": False}
        try:
            os.kill(pid_int, 0)
        except ProcessLookupError:
            result.update({"terminated": True, "reason": "process already exited"})
            return result
        except PermissionError:
            result.update({"reason": "permission denied while probing process"})
            return result

        try:
            os.killpg(pid_int, signal.SIGTERM)
            result["signal"] = "SIGTERM"
        except ProcessLookupError:
            result.update({"terminated": True, "reason": "process group already exited"})
            return result
        except PermissionError:
            result.update({"reason": "permission denied while terminating process group"})
            return result
        except Exception:
            try:
                os.kill(pid_int, signal.SIGTERM)
                result["signal"] = "SIGTERM"
            except ProcessLookupError:
                result.update({"terminated": True, "reason": "process already exited"})
                return result
            except PermissionError:
                result.update({"reason": "permission denied while terminating process"})
                return result

        time.sleep(0.2)
        try:
            os.kill(pid_int, 0)
        except ProcessLookupError:
            result["terminated"] = True
            return result
        except PermissionError:
            result["reason"] = "termination requested; permission denied while verifying"
            return result

        try:
            os.killpg(pid_int, signal.SIGKILL)
            result["signal"] = "SIGKILL"
            result["terminated"] = True
        except ProcessLookupError:
            result["terminated"] = True
        except Exception as exc:
            result["reason"] = f"SIGKILL failed: {exc}"
        return result

    def _finish_job(
        self,
        job_id: str,
        *,
        status: str,
        completed_steps: int,
        last_complete_round: Optional[int],
        finish_reason: str,
    ) -> Dict[str, Any]:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"Invalid terminal status: {status}")
        job = self._require_job(job_id)
        now = _local_timestamp()
        changes = {
            "status": status,
            "completed_steps": int(completed_steps),
            "last_complete_round": last_complete_round,
            "finish_reason": finish_reason,
            "finished_at": now,
            "updated_at": now,
        }
        self.repository.update_job(job_id, changes)
        run_id = job.get("run_id")
        if run_id:
            self.repository.update_run(
                run_id,
                {
                    "status": status,
                    "completed_steps": int(completed_steps),
                    "last_complete_round": last_complete_round,
                    "finish_reason": finish_reason,
                    "finished_at": now,
                    "updated_at": now,
                },
            )
        return self._require_job(job_id)

    def _require_job(self, job_id: str) -> Dict[str, Any]:
        job = self.repository.get_job(job_id)
        if not job:
            raise ValueError(f"Unknown job_id: {job_id}")
        status = job.get("status")
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid job status for {job_id}: {status}")
        return job
