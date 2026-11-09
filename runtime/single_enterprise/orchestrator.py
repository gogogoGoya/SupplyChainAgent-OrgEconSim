"""Single-enterprise orchestrator that reuses the shared EnterpriseRuntime."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = ROOT / 'agent'
for value in (str(ROOT), str(AGENT_ROOT)):
    if value not in sys.path:
        sys.path.insert(0, value)

from agent.data_config import DepartmentSpec, EnterpriseSpec  # noqa: E402
from agent.EnterpriseRuntime import EnterpriseRuntime  # noqa: E402
from agent.SessionRegistry import SessionRegistry  # noqa: E402
from agent.static_utils import StaticUtils  # noqa: E402
from agent.multi_tenant_utils import MultiTenantUtils  # noqa: E402
from agent.GlobalDepartmentLockManager import GlobalDepartmentLockManager  # noqa: E402
from agent.fd_experiment_runtime import record_handover  # noqa: E402

from config.environment_config import EnvironmentConfig  # noqa: E402
from core.model_failure import abort_on_fatal_model_error
from config.integration_profiles import resolve_integration_profiles  # noqa: E402
from config.simulation_preset_config import (  # noqa: E402
    DEFAULT_AGENT_MODEL_NAME_LIST,
    DEFAULT_AGENT_RUN_STEPS,
    DEFAULT_BEER_GAME_DEMAND_SERIES,
    DEFAULT_BEER_GAME_PRODUCT_ID,
    DEFAULT_COBWEB_CONFIG,
    DEFAULT_HERDING_CONFIG,
    DEFAULT_MARKET_DEMAND_MODE,
    DEFAULT_MAX_DEPARTMENT_CONCURRENCY,
    DEFAULT_SERVICE_TOTAL_STEPS,
    DEFAULT_SHARED_RESOURCE_CONFIG,
    get_active_scenario_id,
    get_active_scenario_metadata,
    get_agent_enterprise_layout,
    get_scenario_config,
)
from persistence.sql_mirror_runtime import (  # noqa: E402
    get_sql_mirror_repository,
    mirror_config_version_if_enabled,
    mirror_job_if_enabled,
    mirror_job_update_if_enabled,
    mirror_run_metadata_if_enabled,
    mirror_run_update_if_enabled,
)
from runtime.operations import OperationsService, OperationsStopHook  # noqa: E402

for _agent_env_key, _agent_env_value in EnvironmentConfig.get_agent_env().items():
    os.environ.setdefault(_agent_env_key, _agent_env_value)


def _current_scenario_beer_game_product_id() -> str:
    try:
        simulation_config = get_scenario_config().get("simulation") or {}
        return str(simulation_config.get("beer_game_product_id") or DEFAULT_BEER_GAME_PRODUCT_ID or "beer")
    except Exception:
        return str(DEFAULT_BEER_GAME_PRODUCT_ID or "beer")


def _materialize_market_config_snapshot(
    scenario_config: Dict[str, Any],
    *,
    beer_game_product_id: str,
) -> Dict[str, Any]:
    materialized = copy.deepcopy(scenario_config or {})
    simulation_config = materialized.setdefault("simulation", {})
    if beer_game_product_id:
        simulation_config["beer_game_product_id"] = beer_game_product_id
    return materialized


class SingleEnterpriseSimulationManager:
    """
    Run one enterprise through the shared runtime under a single-enterprise case.

    This class intentionally mirrors the world-day lifecycle of the
    multi-enterprise manager, but it refuses multi-enterprise profiles and never
    injects single-specific phases into the default multi-enterprise path.
    """

    def __init__(
        self,
        client_dir: str,
        enterprise_spec: EnterpriseSpec,
        workspace_dir: Optional[str] = None,
        model_name: Optional[str] = None,
        anthropic_base_url: Optional[str] = None,
        max_department_concurrency: int = DEFAULT_MAX_DEPARTMENT_CONCURRENCY,
        simulation_max_step: int = DEFAULT_SERVICE_TOTAL_STEPS,
        market_demand_mode: str = DEFAULT_MARKET_DEMAND_MODE,
        beer_game_demand_series: Optional[List[float]] = None,
        beer_game_product_id: Optional[str] = None,
        cobweb_config: Optional[Dict[str, Any]] = None,
        shared_resource_config: Optional[Dict[str, Any]] = None,
        herding_config: Optional[Dict[str, Any]] = None,
    ):
        self.client_dir = Path(client_dir)
        self.workspace_dir = Path(workspace_dir) if workspace_dir else (
            self.client_dir / "workspace_multi"
        )
        MultiTenantUtils.configure_workspace(self.workspace_dir)
        self.enterprise_spec = enterprise_spec
        self.simulation_max_step = simulation_max_step
        self.market_demand_mode = market_demand_mode
        self.beer_game_demand_series = beer_game_demand_series or list(
            DEFAULT_BEER_GAME_DEMAND_SERIES
        )
        self.beer_game_product_id = beer_game_product_id or _current_scenario_beer_game_product_id()
        self.cobweb_config = copy.deepcopy(cobweb_config or DEFAULT_COBWEB_CONFIG)
        self.shared_resource_config = copy.deepcopy(
            shared_resource_config or DEFAULT_SHARED_RESOURCE_CONFIG
        )
        self.herding_config = copy.deepcopy(herding_config or DEFAULT_HERDING_CONFIG)
        self.session_registry = SessionRegistry(
            self.workspace_dir / "session_registry.json"
        )
        GlobalDepartmentLockManager()

        resolved_model = (
            model_name
            or EnvironmentConfig.DEFAULT_AGENT_MODEL
            or (DEFAULT_AGENT_MODEL_NAME_LIST[0] if DEFAULT_AGENT_MODEL_NAME_LIST else None)
        )
        if not resolved_model:
            raise RuntimeError(
                "Single-enterprise Agent model is not configured. "
                "Set ANTHROPIC_MODEL in scripts/.env or the shell environment."
            )
        resolved_agent_base_url = (
            anthropic_base_url or EnvironmentConfig.LOCAL_AGENT_GATEWAY_BASE_URL
        )
        self.runtime = EnterpriseRuntime(
            client_dir=str(self.client_dir),
            enterprise_spec=enterprise_spec,
            session_registry=self.session_registry,
            model_name=resolved_model,
            anthropic_base_url=resolved_agent_base_url,
            max_department_concurrency=max_department_concurrency,
            workspace_dir=self.workspace_dir,
        )

    async def run_one_day(self, round_id: int) -> Dict[str, List[dict]]:
        print(f"\n========== SINGLE ENTERPRISE DAY {round_id} START ==========")
        MultiTenantUtils.handle_enterprise_daily()
        MultiTenantUtils.save_enterprise_observations()
        MultiTenantUtils.save_observer_state(round_id, "after_daily")
        MultiTenantUtils.write_single_enterprise_chart_export(round_id)

        workflow_messages = await self.runtime.run_workflow(round_id)
        await self.runtime.dispatch_firm_phase(round_id, "decision")

        StaticUtils.check_orders()
        MultiTenantUtils.save_exchange_info(round_id, "after_check_orders")
        MultiTenantUtils.save_observer_state(round_id, "after_check_orders")
        MultiTenantUtils.save_enterprise_observations()

        trade_messages = await self.runtime.run_trade(
            round_id,
            observation_already_refreshed=True,
        )
        await self.runtime.dispatch_firm_phase(round_id, "trade")

        StaticUtils.statistics_orders()
        MultiTenantUtils.save_exchange_info(round_id, "end_of_day")
        MultiTenantUtils.save_exchange_info(round_id)
        MultiTenantUtils.save_observer_state(round_id, "end_of_day")
        MultiTenantUtils.save_enterprise_observations()
        MultiTenantUtils.write_single_enterprise_chart_export(round_id)
        MultiTenantUtils.archive_enterprise_json_files(round_id)
        MultiTenantUtils.write_history_projection_summary(round_id)
        MultiTenantUtils.write_round_integrity_summary(round_id)
        MultiTenantUtils.write_run_metrics_projection(round_id)
        MultiTenantUtils.write_case_evaluation_projection(round_id)

        print(f"========== SINGLE ENTERPRISE DAY {round_id} END ==========\n")
        return {
            "workflow": workflow_messages or [],
            "trade": trade_messages or [],
        }

    def _build_prewarm_action_pass(self, department: str, reason: str) -> Dict[str, Any]:
        module_type_map = {
            "finance": "FinanceManager",
            "hr": "HRManager",
            "procurement": "ProcurementManager",
            "production": "ProductionManager",
            "sales": "SalesManager",
            "inventory": "InventoryManager",
        }
        return {
            "action": {
                "action_name": "action_pass",
                "action_param": reason,
            },
            "action_reason": reason,
            "module_type": module_type_map.get(department, ""),
            "executor_id": self.enterprise_spec.enterprise_id,
        }

    def _prewarm_workflow_for_department(
        self,
        plan: Dict[str, Any],
        *,
        round_id: int,
        department: str,
    ) -> Dict[str, Any]:
        days = plan.get("days") or {}
        day_payload = days.get(str(round_id)) or days.get(round_id) or {}
        department_payload = (
            day_payload.get(department)
            if isinstance(day_payload, dict)
            else None
        )
        if isinstance(department_payload, dict):
            workflow = copy.deepcopy(department_payload.get("workflow") or [])
            communications = copy.deepcopy(department_payload.get("communications") or {})
        else:
            workflow = []
            communications = {}

        default_reasons = plan.get("default_pass_reasons") or {}
        if not workflow:
            workflow = [
                self._build_prewarm_action_pass(
                    department,
                    str(
                        default_reasons.get(department)
                        or f"{department} follows the single-case prewarm no-op plan."
                    ),
                )
            ]

        for item in workflow:
            if not isinstance(item, dict):
                continue
            item["single_case_prewarm_seed"] = True
            item.setdefault("decision_source", "single_case_custom_prewarm")
            item.setdefault("executor_id", self.enterprise_spec.enterprise_id)
            if item.get("executor_id") == "Manufacturer":
                item["executor_id"] = self.enterprise_spec.enterprise_id
        return {"workflow": workflow, "communications": communications}

    def _scripted_market_order_workflow(
        self,
        plan: Dict[str, Any],
        *,
        round_id: int,
    ) -> List[Dict[str, Any]]:
        """Materialize seed market events before same-day accept actions.

        The refreshed case05 package stores market events separately from the
        sales decisions. They must be created first so the following
        ``accept_order`` IDs refer to real orders. This is single-enterprise
        prewarm behavior only; the shared multi-enterprise daily injector is
        untouched.
        """
        scripted = plan.get("scripted_market_orders") or {}
        orders_by_day = scripted.get("orders_by_day") or {}
        orders = orders_by_day.get(str(round_id)) or orders_by_day.get(round_id) or []
        if not isinstance(orders, list):
            return []

        created: List[Dict[str, Any]] = []
        for index, order in enumerate(orders, start=1):
            if not isinstance(order, dict):
                continue
            action_param = copy.deepcopy(order)
            action_param.setdefault(
                "external_demand_id",
                f"single_case:{plan.get('scene_id', 'unknown')}:day{round_id}:{index}",
            )
            created.append(
                {
                    "action": {
                        "action_name": "create_order",
                        "action_param": action_param,
                    },
                    "action_reason": (
                        "Create the deterministic market-originated order from "
                        f"the single-case prewarm seed before sales conversion on day{round_id}."
                    ),
                    "module_type": "SalesManager",
                    "executor_id": self.enterprise_spec.enterprise_id,
                    "single_case_prewarm_seed": True,
                    "decision_source": "single_case_custom_prewarm_market_seed",
                }
            )
        return created

    def _write_prewarm_department_files(
        self,
        *,
        round_id: int,
        department: str,
        workflow: List[Dict[str, Any]],
        communications: Dict[str, Any],
        file_prefix: Optional[str] = None,
    ) -> Dict[str, Path]:
        action_dir = (
            self.workspace_dir
            / "enterprises"
            / self.enterprise_spec.enterprise_id
            / "department"
            / department
            / f"day{round_id}"
        )
        action_dir.mkdir(parents=True, exist_ok=True)
        stem = file_prefix or department
        action_path = action_dir / f"{stem}_action.json"
        result_path = action_dir / f"{stem}_result.json"
        error_path = action_dir / f"{stem}_error.json"
        action_path.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if communications:
            (action_dir / f"{department}_communications.json").write_text(
                json.dumps(communications, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return {
            "action": action_path,
            "result": result_path,
            "error": error_path,
        }

    def _execute_prewarm_department(
        self,
        *,
        round_id: int,
        department: str,
        workflow: List[Dict[str, Any]],
        communications: Dict[str, Any],
        file_prefix: Optional[str] = None,
    ) -> Dict[str, Any]:
        paths = self._write_prewarm_department_files(
            round_id=round_id,
            department=department,
            workflow=workflow,
            communications=communications,
            file_prefix=file_prefix,
        )
        result = StaticUtils.execute_action(
            paths["action"],
            paths["result"],
            "run",
            paths["error"],
            department=department,
            round_id=str(round_id),
            enterprise_name=self.enterprise_spec.enterprise_id,
        )
        summary = {
            "round_id": int(round_id),
            "department": department,
            "status": result.get("status"),
            "message": result.get("message"),
            "action_names": [
                str(((item.get("action") or {}).get("action_name") or "unknown"))
                for item in workflow
                if isinstance(item, dict)
            ],
            "action_count": len([item for item in workflow if isinstance(item, dict)]),
            "non_pass_action_count": len([
                item for item in workflow
                if isinstance(item, dict)
                and ((item.get("action") or {}).get("action_name") != "action_pass")
            ]),
            "action_file": str(paths["action"]),
            "result_file": str(paths["result"]),
            "error_file": str(result.get("file_path") or paths["error"]),
            "execution_result": self._json_safe(result),
        }
        if result.get("status") != "success":
            summary["errors"] = self._load_prewarm_error_artifacts(paths["error"])
        return summary

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """Normalize filesystem values before writing run projections."""
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {
                str(key): SingleEnterpriseSimulationManager._json_safe(child)
                for key, child in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [SingleEnterpriseSimulationManager._json_safe(child) for child in value]
        return value

    @staticmethod
    def _load_prewarm_error_artifacts(error_path: Path) -> List[Any]:
        artifacts: List[Any] = []
        candidates = [error_path]
        candidates.extend(sorted(error_path.parent.glob(f"{error_path.stem}_*.json")))
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                artifacts.append({
                    "path": str(candidate),
                    "payload": json.loads(candidate.read_text(encoding="utf-8")),
                })
            except Exception as exc:
                artifacts.append({"path": str(candidate), "error": str(exc)})
        return artifacts

    def _write_prewarm_execution_summary(
        self,
        *,
        scenario_id: str,
        handoff_day: int,
        entries: List[Dict[str, Any]],
        status: str,
    ) -> None:
        output_path = (
            self.workspace_dir
            / "projections"
            / "single_case_prewarm"
            / "prewarm_execution_summary.json"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        failed_entries = [
            entry for entry in entries
            if entry.get("status") != "success"
        ]
        payload = {
            "schema_version": "single_case_prewarm_execution.v1",
            "scenario_id": scenario_id,
            "enterprise_id": self.enterprise_spec.enterprise_id,
            "status": status,
            "handoff_day": int(handoff_day),
            "entry_count": len(entries),
            "action_count": sum(int(entry.get("action_count") or 0) for entry in entries),
            "non_pass_action_count": sum(
                int(entry.get("non_pass_action_count") or 0) for entry in entries
            ),
            "failed_entry_count": len(failed_entries),
            "failed_entries": failed_entries,
            "entries": entries,
        }
        output_path.write_text(
            json.dumps(self._json_safe(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _load_execution_errors(result_path: Path) -> List[Any]:
        if not result_path.exists():
            return [{"message": f"missing execution result: {result_path}"}]
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return [{"message": f"invalid execution result {result_path}: {exc}"}]
        error_payload = ((payload.get("result") or {}).get("error") or [])
        return error_payload if error_payload else []

    def _raise_if_static_init_failed(self) -> None:
        static_dir = self.workspace_dir / "_static_commands"
        failed = []
        for index in (1, 2, 3):
            result_path = static_dir / f"init_result_{index}.json"
            errors = self._load_execution_errors(result_path)
            if errors:
                failed.append({"phase": index, "path": str(result_path), "errors": errors})
        if failed:
            raise RuntimeError(f"Single-case static initialization failed: {failed}")

    def _execute_single_case_deferred_init_actions(
        self,
        actions: List[Dict[str, Any]],
    ) -> None:
        if not actions:
            return
        action_root = self.workspace_dir / "_single_case_deferred_init"
        action_root.mkdir(parents=True, exist_ok=True)
        for index, action in enumerate(actions, start=1):
            action_path = action_root / f"deferred_init_action_{index:03d}.json"
            result_path = action_root / f"deferred_init_result_{index:03d}.json"
            error_path = action_root / f"deferred_init_error_{index:03d}.json"
            action_path.write_text(
                json.dumps([action], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result = StaticUtils.execute_action(
                action_path,
                result_path,
                "init",
                error_path,
                enterprise_name=self.enterprise_spec.enterprise_id,
            )
            errors = self._load_execution_errors(result_path)
            if result.get("status") != "success" or errors:
                raise RuntimeError(
                    "Single-case deferred initialization failed: "
                    f"index={index}, action={action}, result={result}, errors={errors}"
                )

    def _run_single_case_prewarm(
        self,
        plan: Dict[str, Any],
        *,
        max_step: int,
        checkpoint_callback: Optional[Any] = None,
        scenario_id: str,
    ) -> int:
        if not plan:
            return 0
        handoff_day = max(0, int(plan.get("handoff_day") or 0))
        handoff_day = min(handoff_day, max(0, int(max_step)))
        if handoff_day <= 0:
            return 0

        departments = list(plan.get("department_execution_order") or [])
        if not departments:
            departments = [department.dept_id for department in self.enterprise_spec.departments]

        print(
            "\n========== SINGLE ENTERPRISE CUSTOM PREWARM "
            f"DAY 0-{handoff_day - 1} START =========="
        )
        execution_entries: List[Dict[str, Any]] = []
        for round_id in range(handoff_day):
            MultiTenantUtils.handle_enterprise_daily()
            MultiTenantUtils.save_enterprise_observations()
            MultiTenantUtils.save_observer_state(round_id, "prewarm_after_daily")
            MultiTenantUtils.write_single_enterprise_chart_export(round_id)

            seeded_market_workflow = self._scripted_market_order_workflow(
                plan,
                round_id=round_id,
            )
            if seeded_market_workflow:
                seed_entry = self._execute_prewarm_department(
                    round_id=round_id,
                    department="sales",
                    workflow=seeded_market_workflow,
                    communications={},
                    file_prefix="sales_market_seed",
                )
                execution_entries.append(seed_entry)
                self._write_prewarm_execution_summary(
                    scenario_id=scenario_id,
                    handoff_day=handoff_day,
                    entries=execution_entries,
                    status="running",
                )
                if seed_entry.get("status") != "success":
                    self._write_prewarm_execution_summary(
                        scenario_id=scenario_id,
                        handoff_day=handoff_day,
                        entries=execution_entries,
                        status="failed",
                    )
                    raise RuntimeError(
                        "Single-case scripted market order failed: "
                        f"day={round_id}, result={seed_entry.get('execution_result')}, "
                        f"errors={seed_entry.get('errors')}"
                    )

            for department in departments:
                payload = self._prewarm_workflow_for_department(
                    plan,
                    round_id=round_id,
                    department=department,
                )
                entry = self._execute_prewarm_department(
                    round_id=round_id,
                    department=department,
                    workflow=payload["workflow"],
                    communications=payload["communications"],
                )
                execution_entries.append(entry)
                self._write_prewarm_execution_summary(
                    scenario_id=scenario_id,
                    handoff_day=handoff_day,
                    entries=execution_entries,
                    status="running",
                )
                if entry.get("status") != "success":
                    self._write_prewarm_execution_summary(
                        scenario_id=scenario_id,
                        handoff_day=handoff_day,
                        entries=execution_entries,
                        status="failed",
                    )
                    raise RuntimeError(
                        "Single-case prewarm action failed: "
                        f"day={round_id}, department={department}, "
                        f"result={entry.get('execution_result')}, errors={entry.get('errors')}"
                    )

            StaticUtils.check_orders()
            StaticUtils.statistics_orders()
            MultiTenantUtils.save_exchange_info(round_id, "prewarm_end_of_day")
            MultiTenantUtils.save_exchange_info(round_id)
            MultiTenantUtils.save_observer_state(round_id, "prewarm_end_of_day")
            MultiTenantUtils.save_enterprise_observations()
            MultiTenantUtils.write_single_enterprise_chart_export(round_id)
            MultiTenantUtils.archive_enterprise_json_files(round_id)
            MultiTenantUtils.write_history_projection_summary(round_id)
            MultiTenantUtils.write_round_integrity_summary(round_id)
            MultiTenantUtils.write_run_metrics_projection(round_id)
            MultiTenantUtils.write_case_evaluation_projection(round_id)
            if checkpoint_callback is not None:
                checkpoint_callback(
                    completed_steps=round_id + 1,
                    last_complete_round=round_id,
                    checkpoint_uri=f"file://{self.workspace_dir}",
                    artifact_root=str(self.workspace_dir),
                    details={
                        "orchestrator": "single_enterprise",
                        "scenario_id": scenario_id,
                        "round_id": round_id,
                        "phase": "custom_prewarm",
                    },
                )
            StaticUtils.run_day()

        self._write_prewarm_execution_summary(
            scenario_id=scenario_id,
            handoff_day=handoff_day,
            entries=execution_entries,
            status="completed",
        )
        print("========== SINGLE ENTERPRISE CUSTOM PREWARM END ==========\n")
        return handoff_day

    @abort_on_fatal_model_error
    async def run(
        self,
        max_step: Optional[int] = None,
        checkpoint_callback: Optional[Any] = None,
        operations_job_id: Optional[str] = None,
        operations_run_id: Optional[str] = None,
        operations_repository: Optional[Any] = None,
    ) -> Dict[str, Any]:
        max_step = max_step or self.simulation_max_step
        scenario_id = get_active_scenario_id()
        scenario_metadata = get_active_scenario_metadata()
        scenario_config = get_scenario_config(scenario_id)
        scenario_config = _materialize_market_config_snapshot(
            scenario_config,
            beer_game_product_id=self.beer_game_product_id,
        )
        experiment_design = scenario_config.get("experiment_design") or {}
        integration_profiles = resolve_integration_profiles(scenario_config)
        if integration_profiles["simulation"]["profile"] != "single_enterprise":
            raise ValueError(
                "SingleEnterpriseSimulationManager requires "
                "simulation.profile=single_enterprise"
            )

        case_policy = (
            scenario_config.get("single_enterprise_case")
            or (scenario_config.get("runtime_injection") or {}).get(
                "single_enterprise_case_policy"
            )
            or {"enabled": False}
        )
        run_id = operations_run_id or MultiTenantUtils.generate_next_run_id(prefix="single_run")
        job_record_id = operations_job_id or run_id
        run_started_at = os.getenv("SIMULATION_RUN_STARTED_AT") or datetime.now().isoformat(timespec="seconds")
        config_version_id = f"{job_record_id}:config"
        workspace_run_meta = {
            "run_id": run_id,
            "job_id": job_record_id,
            "status": "running",
            "started_at": run_started_at,
            "orchestrator": "single_enterprise",
            "scenario_id": scenario_id,
            "scenario_meta": scenario_metadata,
            "scenario_config": scenario_config,
            "experiment_design": experiment_design,
            "decision_regime": experiment_design.get("decision_regime"),
            "agent_objective_profile": experiment_design.get("agent_objective_profile"),
            "constraint_level": experiment_design.get("constraint_level"),
            "experiment_group": experiment_design.get("experiment_group"),
            "integration_profiles": integration_profiles,
            "config_version_id": config_version_id,
            "artifact_root": str(self.workspace_dir),
            "single_enterprise_case": case_policy,
            "planned_total_steps": max_step,
            "prewarm_execution_plan": case_policy.get("prewarm_execution_plan") or {},
            "prewarm_rounds": int(case_policy.get("prewarm_rounds") or 0),
            "handoff_day": int(case_policy.get("handoff_day") or 0),
            "market_demand_mode": self.market_demand_mode,
            "beer_game_product_id": self.beer_game_product_id,
            "cobweb_config": self.cobweb_config,
            "shared_resource_config": self.shared_resource_config,
            "herding_config": self.herding_config,
            "enterprise_ids": [self.enterprise_spec.enterprise_id],
            "safety_stop_policy": MultiTenantUtils.get_safety_stop_policy(),
        }
        MultiTenantUtils.clear_workspace_stop_report()
        MultiTenantUtils.write_workspace_run_metadata(workspace_run_meta)
        if not operations_job_id:
            mirror_config_version_if_enabled(
                integration_profiles,
                version_id=config_version_id,
                scenario_id=scenario_id,
                scenario_config=scenario_config,
                integration_profiles=integration_profiles,
            )
        mirror_run_metadata_if_enabled(integration_profiles, workspace_run_meta)
        if not operations_job_id:
            mirror_job_if_enabled(
                integration_profiles,
                {
                    "job_id": job_record_id,
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "orchestrator": "single_enterprise",
                    "status": "running",
                    "planned_total_steps": max_step,
                    "config_version_id": config_version_id,
                    "started_at": run_started_at,
                },
            )

        StaticUtils.configure_simulation(
            total_steps=max_step,
            market_demand_mode=self.market_demand_mode,
            beer_game_demand_series=self.beer_game_demand_series,
            beer_game_product_id=self.beer_game_product_id,
            cobweb_config=self.cobweb_config,
            shared_resource_config=self.shared_resource_config,
            herding_config=self.herding_config,
        )

        enterprise_root = self.workspace_dir / "enterprises" / self.enterprise_spec.enterprise_id
        enterprise_root.mkdir(parents=True, exist_ok=True)
        MultiTenantUtils.handle_enterprises_init()
        self._raise_if_static_init_failed()
        self._execute_single_case_deferred_init_actions(
            case_policy.get("deferred_init_actions") or []
        )
        prewarm_completed_steps = self._run_single_case_prewarm(
            case_policy.get("prewarm_execution_plan") or {},
            max_step=max_step,
            checkpoint_callback=checkpoint_callback,
            scenario_id=scenario_id,
        )
        if prewarm_completed_steps:
            record_handover(self.workspace_dir, self.enterprise_spec.enterprise_id, scenario_config)
            workspace_run_meta.update(
                {
                    "prewarm_status": "completed",
                    "prewarm_completed_steps": prewarm_completed_steps,
                    "agent_takeover_round": prewarm_completed_steps,
                }
            )
            MultiTenantUtils.write_workspace_run_metadata(workspace_run_meta)

        stop_reason = None
        mirror_repo = operations_repository or get_sql_mirror_repository(integration_profiles)
        operations_stop_hook = (
            OperationsStopHook(OperationsService(mirror_repo), job_record_id)
            if mirror_repo is not None
            else None
        )
        for step in range(prewarm_completed_steps, max_step):
            await self.run_one_day(step)
            if checkpoint_callback is not None:
                checkpoint_callback(
                    completed_steps=step + 1,
                    last_complete_round=step,
                    checkpoint_uri=f"file://{self.workspace_dir}",
                    artifact_root=str(self.workspace_dir),
                    details={
                        "orchestrator": "single_enterprise",
                        "scenario_id": scenario_id,
                        "round_id": step,
                    },
                )
            if operations_stop_hook is not None:
                stop_reason = operations_stop_hook.seal_if_requested(completed_round=step)
                if stop_reason:
                    effective_total_steps = step + 1
                    workspace_run_meta.update(
                        {
                            "status": "stopped",
                            "finished_at": datetime.now().isoformat(timespec="seconds"),
                            "planned_total_steps": effective_total_steps,
                            "completed_steps": effective_total_steps,
                            "stopped_after_round": step,
                            "stop_reason": stop_reason,
                        }
                    )
                    MultiTenantUtils.write_workspace_stop_report(
                        {
                            "run_id": run_id,
                            "scenario_id": scenario_id,
                            "status": "stopped",
                            "started_at": run_started_at,
                            "finished_at": workspace_run_meta["finished_at"],
                            "effective_total_steps": effective_total_steps,
                            "stopped_after_round": step,
                            "stop_reason": stop_reason,
                        }
                    )
                    MultiTenantUtils.write_workspace_run_metadata(workspace_run_meta)
                    mirror_run_update_if_enabled(
                        integration_profiles,
                        run_id,
                        workspace_run_meta,
                    )
                    print(f"Simulation stopped by operations request: {stop_reason['message']}")
                    break
            stop_reason = MultiTenantUtils.build_safety_stop_reason(
                round_id=step,
                enterprise_ids=[self.enterprise_spec.enterprise_id],
            )
            if stop_reason:
                effective_total_steps = step + 1
                workspace_run_meta.update(
                    {
                        "status": "stopped_by_guard",
                        "finished_at": datetime.now().isoformat(timespec="seconds"),
                        "planned_total_steps": effective_total_steps,
                        "completed_steps": effective_total_steps,
                        "stopped_after_round": step,
                        "stop_reason": stop_reason,
                    }
                )
                MultiTenantUtils.write_workspace_stop_report(
                    {
                        "run_id": run_id,
                        "scenario_id": scenario_id,
                        "status": "stopped_by_guard",
                        "started_at": run_started_at,
                        "finished_at": workspace_run_meta["finished_at"],
                        "effective_total_steps": effective_total_steps,
                        "stopped_after_round": step,
                        "stop_reason": stop_reason,
                    }
                )
                MultiTenantUtils.write_workspace_run_metadata(workspace_run_meta)
                mirror_run_update_if_enabled(
                    integration_profiles,
                    run_id,
                    workspace_run_meta,
                )
                mirror_job_update_if_enabled(
                    integration_profiles,
                    job_record_id,
                    {
                        "status": "stopped",
                        "finished_at": workspace_run_meta["finished_at"],
                        "completed_steps": effective_total_steps,
                        "last_complete_round": step,
                        "stop_reason": stop_reason,
                        "finish_reason": stop_reason.get("message"),
                    },
                )
                print(f"Simulation stopped by safety guard: {stop_reason['message']}")
                break
            StaticUtils.run_day()

        if not stop_reason:
            MultiTenantUtils.clear_workspace_stop_report()
            workspace_run_meta.update(
                {
                    "status": "completed",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "completed_steps": max_step,
                }
            )
        MultiTenantUtils.write_workspace_run_metadata(workspace_run_meta)
        mirror_run_update_if_enabled(
            integration_profiles,
            run_id,
            workspace_run_meta,
        )
        mirror_job_update_if_enabled(
            integration_profiles,
            job_record_id,
            {
                "status": (
                    "stopped"
                    if workspace_run_meta["status"] == "stopped_by_guard"
                    else workspace_run_meta["status"]
                ),
                "finished_at": workspace_run_meta["finished_at"],
                "completed_steps": workspace_run_meta.get("completed_steps", max_step),
                "last_complete_round": workspace_run_meta.get("completed_steps", max_step) - 1,
            },
        )

        snapshot_path = None
        if MultiTenantUtils.is_workspace_jobs_workspace(self.workspace_dir):
            print(
                "Single enterprise simulation snapshot archive skipped: "
                "workspace_jobs is authoritative."
            )
        else:
            snapshot_path = MultiTenantUtils.archive_current_run_snapshot(
                run_id=run_id,
                metadata={
                    "status": workspace_run_meta["status"],
                    "started_at": run_started_at,
                    "finished_at": workspace_run_meta["finished_at"],
                    "orchestrator": "single_enterprise",
                    "scenario_id": scenario_id,
                    "scenario_meta": scenario_metadata,
                    "scenario_config": scenario_config,
                    "experiment_design": experiment_design,
                    "integration_profiles": integration_profiles,
                    "single_enterprise_case": case_policy,
                    "total_steps": workspace_run_meta.get("completed_steps", max_step),
                    "market_demand_mode": self.market_demand_mode,
                    "enterprise_ids": [self.enterprise_spec.enterprise_id],
                    "stop_reason": stop_reason,
                },
            )
            print(f"Single enterprise simulation snapshot archived to: {snapshot_path}")
        return {
            "status": (
                "stopped"
                if workspace_run_meta["status"] == "stopped_by_guard"
                else workspace_run_meta["status"]
            ),
            "completed_steps": workspace_run_meta.get("completed_steps", max_step),
            "last_complete_round": workspace_run_meta.get("completed_steps", max_step) - 1,
            "snapshot_path": str(snapshot_path) if snapshot_path else None,
            "stop_reason": stop_reason,
        }


def build_single_enterprise_specs_from_layouts(
    layouts: List[Dict[str, Any]],
) -> List[EnterpriseSpec]:
    if len(layouts) != 1:
        raise ValueError(
            "single-enterprise orchestrator requires exactly one enterprise "
            f"layout, got {len(layouts)}"
        )
    enterprise_layout = layouts[0]
    departments = [
        DepartmentSpec(**department_layout)
        for department_layout in enterprise_layout["departments"]
    ]
    return [
        EnterpriseSpec(
            enterprise_id=enterprise_layout["enterprise_id"],
            enterprise_name=enterprise_layout["enterprise_name"],
            departments=departments,
        )
    ]


def build_single_enterprise_specs() -> List[EnterpriseSpec]:
    return build_single_enterprise_specs_from_layouts(get_agent_enterprise_layout())


def resolve_agent_run_steps(default_steps: int) -> int:
    raw_value = os.getenv("SIMULATION_AGENT_RUN_STEPS")
    if not raw_value:
        return default_steps
    try:
        resolved_steps = int(raw_value)
    except ValueError:
        return default_steps
    return resolved_steps if resolved_steps > 0 else default_steps


async def main() -> None:
    enterprise_specs = build_single_enterprise_specs()
    manager = SingleEnterpriseSimulationManager(
        client_dir=str(AGENT_ROOT),
        enterprise_spec=enterprise_specs[0],
        workspace_dir=str(AGENT_ROOT / "workspace_multi"),
    )
    await manager.run(resolve_agent_run_steps(DEFAULT_AGENT_RUN_STEPS))


if __name__ == "__main__":
    import anyio

    anyio.run(main)
