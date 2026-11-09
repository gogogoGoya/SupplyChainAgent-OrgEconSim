"""Lightweight operations worker and checkpoint boundary."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .service import OperationsService


CheckpointCallback = Callable[..., Dict[str, Any]]
JobRunner = Callable[[Dict[str, Any], CheckpointCallback], Dict[str, Any]]
_SIMULATION_EXECUTION_LOCK = threading.Lock()


@dataclass
class WorkerResult:
    job_id: str
    status: str
    completed_steps: int = 0
    last_complete_round: Optional[int] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "completed_steps": self.completed_steps,
            "last_complete_round": self.last_complete_round,
            "message": self.message,
        }


class OperationsWorker:
    """
    Consume repository-backed operations jobs with an injected runner.

    The worker owns orchestration status transitions only. The runner owns
    domain execution and may call the provided checkpoint callback after full
    round boundaries.
    """

    def __init__(
        self,
        repository: Any,
        runner: JobRunner,
        *,
        worker_id: str = "operations-worker",
    ):
        self.service = OperationsService(repository)
        self.runner = runner
        self.worker_id = worker_id
        bind_repository = getattr(runner, "bind_operations_repository", None)
        if callable(bind_repository):
            bind_repository(repository)

    def run_next(self) -> Optional[Dict[str, Any]]:
        queued = self.service.recover_jobs(status="queued", limit=1)
        if not queued:
            return None
        return self.run_job(queued[0]["job_id"])

    def recover_active_jobs(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.service.recover_jobs(active_only=True, limit=limit)

    def run_job(self, job_id: str) -> Dict[str, Any]:
        with _SIMULATION_EXECUTION_LOCK:
            return self._run_job_exclusive(job_id)

    def _run_job_exclusive(self, job_id: str) -> Dict[str, Any]:
        job = self.service.get_job(job_id)
        if job.get("status") == "stop_requested":
            stopped = self.service.mark_stopped(
                job_id,
                completed_steps=int(job.get("completed_steps") or 0),
                last_complete_round=job.get("last_complete_round"),
                reason="stop requested before worker pickup",
            )
            return WorkerResult(
                job_id=job_id,
                status=stopped["status"],
                completed_steps=int(stopped.get("completed_steps") or 0),
                last_complete_round=stopped.get("last_complete_round"),
                message="sealed queued stop request",
            ).to_dict()

        running = self.service.start_experiment(
            job_id,
            run_id=job.get("run_id"),
            worker_id=self.worker_id,
            artifact_root=job.get("artifact_root"),
        )

        def checkpoint(
            *,
            completed_steps: int,
            last_complete_round: Optional[int],
            checkpoint_uri: Optional[str] = None,
            artifact_root: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            return self.service.record_checkpoint(
                job_id,
                completed_steps=completed_steps,
                last_complete_round=last_complete_round,
                checkpoint_uri=checkpoint_uri,
                artifact_root=artifact_root,
                details=details,
            )

        try:
            result = self.runner(running, checkpoint)
            if not isinstance(result, dict) or not result:
                raise RuntimeError("Runner returned no execution result")
        except Exception as exc:
            latest = self.service.get_job(job_id)
            failed = self.service.mark_failed(
                job_id,
                error=str(exc),
                completed_steps=int(latest.get("completed_steps") or 0),
                last_complete_round=latest.get("last_complete_round"),
            )
            return WorkerResult(
                job_id=job_id,
                status=failed["status"],
                completed_steps=int(failed.get("completed_steps") or 0),
                last_complete_round=failed.get("last_complete_round"),
                message=str(exc),
            ).to_dict()

        latest = self.service.get_job(job_id)
        completed_steps = int(
            result.get("completed_steps")
            if result.get("completed_steps") is not None
            else latest.get("completed_steps")
            or 0
        )
        last_complete_round = (
            result.get("last_complete_round")
            if result.get("last_complete_round") is not None
            else latest.get("last_complete_round")
        )
        result_status = result.get("status", "completed")
        if result_status == "failed" or result_status not in {"completed", "stopped", "stopped_by_guard"}:
            finished = self.service.mark_failed(
                job_id, completed_steps=completed_steps,
                last_complete_round=last_complete_round,
                error=result.get("finish_reason") or result.get("error") or f"Runner ended with status {result_status}",
            )
        elif latest.get("status") == "stop_requested" or result_status in {"stopped", "stopped_by_guard"}:
            finished = self.service.mark_stopped(
                job_id,
                completed_steps=completed_steps,
                last_complete_round=last_complete_round,
                reason=result.get("finish_reason") or "worker observed stop request",
            )
        else:
            finished = self.service.mark_completed(
                job_id,
                completed_steps=completed_steps,
                last_complete_round=last_complete_round,
            )
        return WorkerResult(
            job_id=job_id,
            status=finished["status"],
            completed_steps=int(finished.get("completed_steps") or 0),
            last_complete_round=finished.get("last_complete_round"),
            message=finished.get("finish_reason") or "",
        ).to_dict()
