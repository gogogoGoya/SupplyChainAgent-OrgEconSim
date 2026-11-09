"""Durable serial FD batches; each item uses the ordinary isolated job launcher."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from config.project_paths import PROJECT_ROOT, WORKSPACE_JOBS_ROOT
from config.platform_access import require_execution_enabled
from .provenance import capture_provenance
from .process_health import process_identity


def batch_root():
    return WORKSPACE_JOBS_ROOT / "_batches"


@contextmanager
def control_lock():
    root = batch_root()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".control.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def alive(pid):
    if not pid:
        return False
    try:
        # Detached children can briefly remain zombies until their parent reaps them.
        stat = Path(f"/proc/{int(pid)}/stat")
        if stat.exists() and stat.read_text().rsplit(")", 1)[1].strip().split()[0] == "Z":
            try:
                os.waitpid(int(pid), os.WNOHANG)
            except ChildProcessError:
                pass
            return False
        os.kill(int(pid), 0)
        return True
    except (ValueError, ProcessLookupError, FileNotFoundError):
        return False
    except PermissionError:
        return True


def scheduler_alive(record):
    identity = record.get("supervisor_identity")
    current = process_identity(record.get("supervisor_pid")) if identity else None
    if current and current["start_ticks"] != identity.get("start_ticks"):
        return False
    return alive(record.get("supervisor_pid"))


def worker_alive(job):
    identity = job.get("process_identity")
    current = process_identity(job.get("process_pid")) if identity else None
    if current and current["start_ticks"] != identity.get("start_ticks"):
        return False
    return alive(job.get("process_pid"))


class BatchService:
    def __init__(self, api):
        self.api = api

    def _path(self, batch_id):
        if not re.fullmatch(r"batch_[a-f0-9]{12}", batch_id or ""):
            raise ValueError("Invalid batch_id")
        return batch_root() / f"{batch_id}.json"

    def get(self, batch_id):
        path = self._path(batch_id)
        if not path.exists():
            raise ValueError("Unknown batch_id")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, record):
        from .api import _local_timestamp
        record["updated_at"] = _local_timestamp()
        path = self._path(record["batch_id"])
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return record

    def list(self, *, reconcile=True):
        records = []
        for path in batch_root().glob("batch_*.json"):
            record = self.get(path.stem)
            # Do not silently replay an interrupted process; explicit resume reattaches.
            if reconcile and record["status"] in {"running", "pause_requested"} and not scheduler_alive(record):
                with control_lock():
                    record = self.get(path.stem)
                    if record["status"] in {"running", "pause_requested"} and not scheduler_alive(record):
                        record.update(status="paused", message='The batch scheduler exited. Resume remaining items or reattach to the current live worker.')
                        self.save(record)
            view = deepcopy(record)
            view["scheduler_health"] = "alive" if scheduler_alive(record) else "exited"
            if view["status"] in {"running", "pause_requested"} and view["scheduler_health"] == "exited":
                view.update(status="paused", stored_status=record["status"], message="Scheduler exited; resume to reattach or continue pending items.")
            records.append(view)
        return sorted(records, key=lambda r: r["created_at"], reverse=True)

    def assert_available(self, owner=None):
        # Called under the shared dispatch lock, including from manual launch paths.
        for path in batch_root().glob("batch_*.json"):
            record = self.get(path.stem)
            if record["status"] in {"running", "pause_requested"} and record["batch_id"] != owner:
                if not scheduler_alive(record):
                    for item in record.get("items", []):
                        if item.get("status") == "running":
                            job = self.api._repository(item["scenario_id"]).get_job(item["job_id"])
                            if job and worker_alive(job):
                                raise RuntimeError("A batch worker is still active; resume its exited scheduler before manual dispatch")
                    record.update(status="paused", message="Exited scheduler reconciled during authorized dispatch")
                    self.save(record)
                    continue
                raise RuntimeError(f"Batch {record['batch_id']} is running serially; finish or pause it before a manual launch.")

    def create(self, items, repeats=1, requested_by="operations-page"):
        require_execution_enabled()
        from .api import _local_timestamp
        from .facade import OperationsFacade
        if not items or len(items) > 8 or type(repeats) is not int or not 1 <= repeats <= 20:
            raise ValueError("Select 1–8 FD variants and 1–20 repeats")
        families = {re.fullmatch(r"firm_ablation_(fd[1-5])_(ma|na|nc|sa)", i.get("scenario_id", ""))[1]
                    for i in items if re.fullmatch(r"firm_ablation_(fd[1-5])_(ma|na|nc|sa)", i.get("scenario_id", ""))}
        if len(families) != 1 or any(not re.fullmatch(r"firm_ablation_fd[1-5]_(ma|na|nc|sa)", i.get("scenario_id", "")) for i in items):
            raise ValueError("A batch must contain ablation variants of a single FD")
        if len({i["scenario_id"] for i in items}) != len(items):
            raise ValueError("Duplicate variants: use repeats instead")
        batch_id = "batch_" + uuid.uuid4().hex[:12]
        # Validate and freeze every configuration before writing any real job.
        class Collector:
            def __init__(self):
                self.versions = []
                self.jobs = []
            def save_version(self, version):
                self.versions.append(version)
            def create_job(self, job):
                self.jobs.append(job)
        from .api import _refresh_agent_env_for_child
        model_env = os.environ.copy()
        _refresh_agent_env_for_child(model_env, PROJECT_ROOT)
        provenance = capture_provenance(PROJECT_ROOT, model_env)
        collector = Collector()
        facade = OperationsFacade(collector, scenario_loader=self.api.scenario_loader,
                                  profile_loader=self.api.profile_loader)
        entries = []
        for repeat in range(1, repeats + 1):
            for item in items:
                payload = deepcopy(item)
                payload.pop("job_id", None)
                if payload.get("planned_total_steps") is None:
                    payload["planned_total_steps"] = 20
                payload["requested_by"] = requested_by
                payload["tags"] = list(dict.fromkeys([*payload.get("tags", []), f"batch:{batch_id}", f"batch-repeat:{repeat}"]))
                # Batch provenance does not change the experiment's economic rules.
                design = payload.setdefault("config_overrides", {}).setdefault("experiment_design", {})
                design.update(batch_id=batch_id, repeat_id=repeat)
                job = facade.create_experiment_from_scenario(**payload)
                job["status"] = "batch_pending"
                job["batch_id"] = batch_id
                job["execution_provenance"] = provenance
                entries.append(dict(job_id=job["job_id"], scenario_id=job["scenario_id"],
                                    config_version_id=job["config_version_id"], planned_total_steps=20,
                                    repeat_id=repeat, status="pending", run_id=None, artifact_root=None))
        with control_lock():
            self.assert_available()
            repository = self.api._repository(items[0]["scenario_id"])
            if any(j["status"] in {"running", "queued", "stop_requested"}
                   for j in repository.list_jobs(limit=None)):
                raise RuntimeError('A job is already running or queued. Finish it before submitting a batch.')
            for version, job in zip(collector.versions, collector.jobs):
                repository.save_version(version)
                repository.create_job(job)
            record = dict(batch_id=batch_id, family=next(iter(families)), status="paused", items=entries,
                          created_at=_local_timestamp(), supervisor_pid=None,
                          message='Configurations frozen; ready to launch independent jobs.', requested_by=requested_by)
            self.save(record)
            return self._launch(record)

    def _launch(self, record):
        log = batch_root() / f"{record['batch_id']}.log"
        # Parent holds control_lock until PID and running status are durable.
        with log.open("a", encoding="utf-8") as output:
            env = os.environ.copy()
            env.pop("SIMULATION_OPERATIONS_CHILD_WORKER", None)
            process = subprocess.Popen([sys.executable, "-m", "runtime.operations.batch", record["batch_id"]],
                                       cwd=str(PROJECT_ROOT), env=env, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        record.update(status="running", supervisor_pid=process.pid, supervisor_identity=process_identity(process.pid), log_path=str(log), message='Batch started.')
        return self.save(record)

    def resume(self, batch_id):
        require_execution_enabled()
        with control_lock():
            record = self.get(batch_id)
            self.assert_available(owner=batch_id)
            if record["status"] in {"completed", "finished_with_failures"}:
                raise ValueError("Batch already completed")
            if scheduler_alive(record):
                raise RuntimeError('The scheduler is still running. Wait until it pauses after the current item.')
            own_ids = {i["job_id"] for i in record["items"]}
            repo = self.api._repository(record["items"][0]["scenario_id"])
            if any(j["status"] in {"queued", "running", "stop_requested"} and j["job_id"] not in own_ids
                   for j in repo.list_jobs(limit=None)):
                raise RuntimeError('Another manual job is active. Finish it before resuming the batch.')
            return self._launch(record)

    def pause(self, batch_id):
        with control_lock():
            record = self.get(batch_id)
            if record["status"] in {"running", "pause_requested"}:
                record.update(status="pause_requested", message='Pause after the current item; do not launch the next item.')
            return self.save(record)

    def run(self, batch_id, poll_seconds=3):
        # OS lease also prevents accidental duplicate supervisors after API restarts.
        with (batch_root() / f"{batch_id}.worker.lock").open("a") as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            try:
                while self.tick(batch_id):
                    time.sleep(poll_seconds)
            except Exception as exc:
                with control_lock():
                    record = self.get(batch_id)
                    record.update(status="paused", message=f"Scheduler failed; batch paused: {exc}")
                    self.save(record)
                raise

    def tick(self, batch_id):
        """Advance at most one item; useful for deterministic scheduler tests."""
        with control_lock():
            record = self.get(batch_id)
            if record["status"] not in {"running", "pause_requested"}:
                return False
            current = next((i for i in record["items"] if i["status"] == "running"), None)
            if current:
                repo = self.api._repository(current["scenario_id"])
                job = repo.get_job(current["job_id"])
                if not job:
                    raise RuntimeError("Current batch job was deleted; inspect logs before continuing")
                current.update(run_id=job.get("run_id"), artifact_root=job.get("artifact_root"),
                               completed_steps=int(job.get("completed_steps") or 0),
                               last_complete_round=job.get("last_complete_round"))
                if worker_alive(job):
                    self.save(record)
                    return True
                status = job["status"]
                if status not in {"completed", "failed", "stopped"}:
                    from .service import OperationsService
                    OperationsService(repo).mark_failed(job["job_id"], error="Worker exited without a terminal result",
                                                       completed_steps=current["completed_steps"],
                                                       last_complete_round=current["last_complete_round"])
                    status = "failed"
                if status == "completed" and int(job.get("completed_steps") or 0) < int(job.get("planned_total_steps") or 20):
                    from .service import OperationsService
                    status = "failed"
                    current["error"] = "Job reported completion before the planned final round"
                    OperationsService(repo).mark_failed(job["job_id"], error=current["error"],
                                                       completed_steps=current["completed_steps"],
                                                       last_complete_round=current["last_complete_round"])
                current.update(status=status, process_log=job.get("process_log"),
                               error=current.get("error") or job.get("error") or job.get("finish_reason"))
                if status != "completed":
                    record.update(status="paused", message=f"{current['scenario_id']} {status}; batch paused. Resume skips this item; rerun it as a new job.")
                    self.save(record)
                    return False
            if record["status"] == "pause_requested":
                record.update(status="paused", message='Paused; completed items will not be replayed.')
                self.save(record)
                return False
            next_item = next((i for i in record["items"] if i["status"] == "pending"), None)
            if not next_item:
                failures = any(i["status"] != "completed" for i in record["items"])
                record.update(status="finished_with_failures" if failures else "completed",
                              message='Batch finished with failed/stopped items.' if failures else 'All independent experiments completed.')
                self.save(record)
                return False
            next_item["status"] = "running"
            repo = self.api._repository(next_item["scenario_id"])
            repo.update_job(next_item["job_id"], {"status": "queued"})
            # Persist item identity before dispatch; a restarted supervisor can reattach.
            self.save(record)
        result = self.api.run_job(next_item["job_id"], scenario_id=next_item["scenario_id"],
                                  worker_id=f"batch:{batch_id}", batch_owner=batch_id)
        if not result.get("ok"):
            from .service import OperationsService
            error = (result.get("error") or {}).get("message", "Job dispatch failed")
            with control_lock():
                record = self.get(batch_id)
                item = next(i for i in record["items"] if i["job_id"] == next_item["job_id"])
                item.update(status="failed", error=error)
                OperationsService(repo).mark_failed(item["job_id"], error=error)
                record.update(status="paused", message=f"Job launch failed; batch paused: {error}")
                self.save(record)
            return False
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("batch_id")
    args = parser.parse_args()
    from .api import OperationsAPI
    BatchService(OperationsAPI()).run(args.batch_id)


if __name__ == "__main__":
    main()
