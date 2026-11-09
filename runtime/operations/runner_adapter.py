"""Adapters that let OperationsWorker execute real simulation managers."""

from __future__ import annotations

import inspect
from copy import deepcopy
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import anyio

from config.integration_profiles import (
    get_scenario_integration_profiles,
    resolve_integration_profiles,
)
from config.project_paths import AGENT_ROOT, PROJECT_ROOT, resolve_project_path
from config.simulation_preset_config import (
    SIMULATION_SCENARIO_ENV_VAR,
    get_scenario_config,
    temporary_scenario_config_override,
)
from runtime.simulation_session_context import (
    reset_simulation_session_id,
    set_simulation_session_id,
)

from .worker import CheckpointCallback


@dataclass
class SimulationRunContext:
    job_id: str
    run_id: str
    scenario_id: str
    simulation_profile: str
    planned_total_steps: int
    client_dir: Path
    workspace_dir: Path


ManagerFactory = Callable[[SimulationRunContext], Any]


@contextmanager
def _temporary_scenario(scenario_id: str):
    previous = os.environ.get(SIMULATION_SCENARIO_ENV_VAR)
    os.environ[SIMULATION_SCENARIO_ENV_VAR] = scenario_id
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(SIMULATION_SCENARIO_ENV_VAR, None)
        else:
            os.environ[SIMULATION_SCENARIO_ENV_VAR] = previous


@contextmanager
def _temporary_simulation_session(session_id: str):
    previous = os.environ.get("SIMULATION_SESSION_ID")
    os.environ["SIMULATION_SESSION_ID"] = session_id
    token = set_simulation_session_id(session_id)
    try:
        yield
    finally:
        reset_simulation_session_id(token)
        if previous is None:
            os.environ.pop("SIMULATION_SESSION_ID", None)
        else:
            os.environ["SIMULATION_SESSION_ID"] = previous


@contextmanager
def _temporary_run_started_at(started_at: str):
    previous = os.environ.get("SIMULATION_RUN_STARTED_AT")
    if started_at:
        os.environ["SIMULATION_RUN_STARTED_AT"] = started_at
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("SIMULATION_RUN_STARTED_AT", None)
        else:
            os.environ["SIMULATION_RUN_STARTED_AT"] = previous


def _ensure_agent_import_path(agent_root: Path, project_root: Path) -> None:
    for value in (str(project_root), str(agent_root)):
        if value not in sys.path:
            sys.path.insert(0, value)


def default_simulation_manager_factory(context: SimulationRunContext) -> Any:
    """Build the existing multi/single manager for an operations job."""
    project_root = PROJECT_ROOT
    agent_root = context.client_dir
    _ensure_agent_import_path(agent_root, project_root)
    scenario_config = get_scenario_config(context.scenario_id)
    simulation_config = scenario_config.get("simulation", {})

    if context.simulation_profile == "single_enterprise":
        from runtime.single_enterprise.orchestrator import (  # noqa: WPS433
            SingleEnterpriseSimulationManager,
            build_single_enterprise_specs,
        )

        specs = build_single_enterprise_specs()
        return SingleEnterpriseSimulationManager(
            client_dir=str(agent_root),
            enterprise_spec=specs[0],
            workspace_dir=str(context.workspace_dir),
            max_department_concurrency=int(
                simulation_config.get("max_department_concurrency") or 1
            ),
            simulation_max_step=int(
                simulation_config.get("service_total_steps")
                or context.planned_total_steps
                or 1
            ),
            market_demand_mode=simulation_config.get("market_demand_mode"),
            beer_game_demand_series=simulation_config.get("beer_game_demand_series"),
            beer_game_product_id=simulation_config.get("beer_game_product_id"),
            cobweb_config=simulation_config.get("cobweb_config"),
            shared_resource_config=simulation_config.get("shared_resource_config"),
            herding_config=simulation_config.get("herding_config"),
        )

    from agent.MultiEnterpriseAgentManager import (  # noqa: WPS433
        MultiEnterpriseClaudeManager,
        build_demo_specs,
    )

    return MultiEnterpriseClaudeManager(
        client_dir=str(agent_root),
        enterprise_specs=build_demo_specs(),
        workspace_dir=str(context.workspace_dir),
        max_enterprise_concurrency=int(
            simulation_config.get("max_enterprise_concurrency") or 1
        ),
        max_department_concurrency=int(
            simulation_config.get("max_department_concurrency") or 1
        ),
        simulation_max_step=int(
            simulation_config.get("service_total_steps")
            or context.planned_total_steps
            or 1
        ),
        market_demand_mode=simulation_config.get("market_demand_mode"),
        beer_game_demand_series=simulation_config.get("beer_game_demand_series"),
        beer_game_product_id=simulation_config.get("beer_game_product_id"),
        cobweb_config=simulation_config.get("cobweb_config"),
        shared_resource_config=simulation_config.get("shared_resource_config"),
        herding_config=simulation_config.get("herding_config"),
    )


class OperationsSimulationRunner:
    """
    JobRunner implementation backed by the real simulation managers.

    The adapter keeps OperationsWorker responsible for job state transitions
    and lets existing managers keep their domain lifecycle. Checkpoints are
    emitted only by the managers after complete round archival.
    """

    def __init__(
        self,
        *,
        client_dir: Optional[Path] = None,
        workspace_dir: Optional[Path] = None,
        manager_factory: Optional[ManagerFactory] = None,
    ):
        self.client_dir = resolve_project_path(
            client_dir,
            default=AGENT_ROOT,
            relative_to=PROJECT_ROOT,
        )
        self.workspace_dir = Path(workspace_dir) if workspace_dir else (
            self.client_dir / "workspace_multi"
        )
        self.manager_factory = manager_factory or default_simulation_manager_factory
        self.operations_repository = None

    def bind_operations_repository(self, repository: Any) -> None:
        self.operations_repository = repository

    @staticmethod
    def _safe_path_part(value: Any, fallback: str = "unknown") -> str:
        text = str(value or "").strip() or fallback
        return re.sub(r"[^A-Za-z0-9_.=-]+", "_", text).strip("_") or fallback

    @classmethod
    def _workspace_name_for_job(cls, job: Dict[str, Any]) -> str:
        run_id = cls._safe_path_part(job.get("run_id") or job.get("job_id"), "run")
        started_at = cls._safe_path_part(
            str(job.get("started_at") or job.get("created_at") or "").replace("+08:00", ""),
            "",
        )
        if started_at:
            return f"{run_id}__started_{started_at}"
        return run_id

    def _workspace_for_job(self, job: Dict[str, Any]) -> Path:
        artifact_root = job.get("artifact_root")
        if artifact_root:
            return resolve_project_path(
                artifact_root,
                relative_to=AGENT_ROOT,
            )
        workspace_name = self._workspace_name_for_job(job)
        if self.workspace_dir.name == "workspace_multi":
            return self.workspace_dir.parent / "workspace_jobs" / workspace_name
        return self.workspace_dir / workspace_name

    def __call__(
        self,
        job: Dict[str, Any],
        checkpoint: CheckpointCallback,
    ) -> Dict[str, Any]:
        return anyio.run(self._run_async, dict(job), checkpoint)

    async def _run_async(
        self,
        job: Dict[str, Any],
        checkpoint: CheckpointCallback,
    ) -> Dict[str, Any]:
        scenario_id = str(job.get("scenario_id") or "")
        if not scenario_id:
            raise ValueError("operations job requires scenario_id")
        scenario_config = self._load_job_scenario_config(job)
        resume_context = job.get("resume_context") or {}
        if resume_context and scenario_id == "long_horizon_evolution":
            scenario_config = self._apply_e1_resume_runtime_compatibility(
                scenario_config or get_scenario_config(scenario_id),
                resume_round=(
                    int(resume_context.get("last_complete_round")) + 1
                ),
            )
        profiles = (
            resolve_integration_profiles(scenario_config)
            if scenario_config
            else get_scenario_integration_profiles(scenario_id)
        )
        simulation_profile = profiles.get("simulation", {}).get(
            "profile",
            "multi_enterprise",
        )
        planned_total_steps = int(job.get("planned_total_steps") or 1)
        if planned_total_steps <= 0:
            planned_total_steps = 1
        context = SimulationRunContext(
            job_id=str(job.get("job_id")),
            run_id=str(job.get("run_id") or job.get("job_id")),
            scenario_id=scenario_id,
            simulation_profile=simulation_profile,
            planned_total_steps=planned_total_steps,
            client_dir=self.client_dir,
            workspace_dir=self._workspace_for_job(job),
        )
        context.workspace_dir.mkdir(parents=True, exist_ok=True)
        if self.operations_repository is not None:
            artifact_root = str(context.workspace_dir)
            update_job = getattr(self.operations_repository, "update_job", None)
            update_run = getattr(self.operations_repository, "update_run", None)
            if callable(update_job):
                update_job(context.job_id, {"artifact_root": artifact_root})
            if callable(update_run):
                update_run(context.run_id, {"artifact_root": artifact_root})
        with _temporary_scenario(scenario_id), _temporary_simulation_session(context.job_id), _temporary_run_started_at(str(job.get("started_at") or "")), temporary_scenario_config_override(
            scenario_id,
            scenario_config or get_scenario_config(scenario_id),
        ):
            manager = self.manager_factory(context)
            run_kwargs = {
                "max_step": planned_total_steps,
                "checkpoint_callback": checkpoint,
                "operations_job_id": context.job_id,
                "operations_run_id": context.run_id,
                "operations_repository": self.operations_repository,
            }
            if resume_context:
                run_kwargs["resume_context"] = resume_context
            try:
                result = manager.run(**run_kwargs)
                if inspect.isawaitable(result):
                    result = await result
            finally:
                import json
                meta_path = context.workspace_dir / "run_meta.json"
                if meta_path.is_file():
                    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                    metadata["execution_provenance"] = job.get("execution_provenance")
                    metadata["execution_protocol"] = "ordered_business_commit.v1"
                    temporary = meta_path.with_suffix(".provenance.tmp")
                    temporary.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
                    temporary.replace(meta_path)
                if self.manager_factory is default_simulation_manager_factory and os.getenv("SIMULATION_KEEP_FINISHED_SESSIONS") != "1":
                    from config.environment_config import EnvironmentConfig
                    import requests
                    base = EnvironmentConfig.SIMULATION_API_BASE_URL
                    if base:
                        headers = {"X-Simulation-Session-Id": context.job_id}
                        if os.getenv("SUPPLY_CHAIN_OPERATOR_TOKEN"):
                            headers["Authorization"] = "Bearer " + os.environ["SUPPLY_CHAIN_OPERATOR_TOKEN"]
                        try:
                            requests.post(base.rstrip("/")+"/session/close", headers=headers, timeout=5)
                        except requests.RequestException:
                            pass  # A quarantined, draining session is released by the operator later.
        if not isinstance(result, dict) or not result:
            raise RuntimeError("Simulation manager returned no execution result")
        return {
            "status": result.get("status") or "completed",
            "completed_steps": result.get("completed_steps"),
            "last_complete_round": result.get("last_complete_round"),
            "finish_reason": (
                result.get("finish_reason")
                or (result.get("stop_reason") or {}).get("message")
            ),
            "snapshot_path": result.get("snapshot_path"),
        }

    def _load_job_scenario_config(self, job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.operations_repository is None:
            return None
        version_id = job.get("config_version_id")
        if not version_id or not hasattr(self.operations_repository, "get_version"):
            return None
        config_version = self.operations_repository.get_version(str(version_id)) or {}
        scenario_config = config_version.get("scenario_config")
        return dict(scenario_config) if isinstance(scenario_config, dict) else None

    @staticmethod
    def _apply_e1_resume_runtime_compatibility(
        scenario_config: Dict[str, Any],
        *,
        resume_round: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Apply execution-only E1 safeguards added after an interrupted run."""
        patched = deepcopy(scenario_config or {})
        current = get_scenario_config("long_horizon_evolution")
        current_policy = (
            (current.get("runtime_injection") or {}).get(
                "long_run_experiment_policy"
            )
            or {}
        )
        policy = patched.setdefault("runtime_injection", {}).setdefault(
            "long_run_experiment_policy",
            {},
        )
        for policy_name in (
            "agent_context_compaction",
            "agent_timeout_policy",
            "state_driven_fallback_policy",
            "agent_timeout_circuit_breaker",
            "execution_guard",
            "artifact_retention_policy",
        ):
            policy[policy_name] = deepcopy(current_policy.get(policy_name) or {})
        policy["continue_on_agent_failure"] = True
        policy["max_agent_retries_per_turn"] = int(
            current_policy.get("max_agent_retries_per_turn") or 2
        )
        policy["checkpoint_every_turn"] = True
        if resume_round is not None:
            analyst_policy = patched.setdefault("runtime_injection", {}).setdefault(
                "analyst_policy",
                {},
            )
            force_on_rounds = {
                int(value)
                for value in (analyst_policy.get("force_on_rounds") or [])
                if str(value).lstrip("-").isdigit()
            }
            force_on_rounds.add(int(resume_round))
            analyst_policy["force_on_rounds"] = sorted(force_on_rounds)
        patched.setdefault("resume_compatibility", {}).update({
            "schema_version": "e1_resume_compatibility.v1",
            "agent_context_compaction_applied": True,
            "agent_timeout_policy_applied": True,
            "agent_timeout_circuit_breaker_applied": True,
            "runtime_checkpoint_enabled": True,
            "analyst_forced_on_resume_round": resume_round,
            "economic_configuration_unchanged": True,
        })
        return patched
