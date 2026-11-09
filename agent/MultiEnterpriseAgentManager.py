import anyio
import time
import copy
import asyncio
import inspect
import json
import os
import re
import shutil
import sys
from datetime import datetime
from agent.static_utils import Config as StaticConfig, StaticUtils
from agent.EnterpriseRuntime import EnterpriseRuntime
from pathlib import Path
from typing import Dict, List, Callable, Any, Optional
from agent.SessionRegistry import SessionRegistry
from agent.multi_tenant_utils import MultiTenantUtils
from agent.data_config import DepartmentSpec, EnterpriseSpec
from agent.GlobalDepartmentLockManager import GlobalDepartmentLockManager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config.simulation_preset_config import (
    DEFAULT_AGENT_MODEL_NAME_LIST,
    DEFAULT_AGENT_RUN_STEPS,
    DEFAULT_BEER_GAME_PRODUCT_ID,
    DEFAULT_BEER_GAME_DEMAND_SERIES,
    DEFAULT_COBWEB_CONFIG,
    DEFAULT_HERDING_CONFIG,
    DEFAULT_MARKET_DEMAND_MODE,
    DEFAULT_MAX_DEPARTMENT_CONCURRENCY,
    DEFAULT_MAX_ENTERPRISE_CONCURRENCY,
    DEFAULT_SERVICE_TOTAL_STEPS,
    DEFAULT_SHARED_RESOURCE_CONFIG,
    get_agent_enterprise_layout,
    get_active_scenario_id,
    get_scenario_config,
    get_active_scenario_metadata,
)
from config.integration_profiles import get_scenario_integration_profiles
from config.environment_config import EnvironmentConfig
from core.model_failure import abort_on_fatal_model_error, gather_model_tasks
from persistence.sql_mirror_runtime import (
    get_sql_mirror_repository,
    mirror_config_version_if_enabled,
    mirror_job_if_enabled,
    mirror_job_update_if_enabled,
    mirror_run_metadata_if_enabled,
    mirror_run_update_if_enabled,
)
from runtime.operations import OperationsService, OperationsStopHook

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

# ============================================================

# ============================================================

class MultiEnterpriseClaudeManager:
    '\n    multi-enterprise Master Manager\n\n    Core functions:\n    - Holds multiple Enterprise Runtime\n    World day\n    - and dispatch multiple enterprisex3/>\n    - Manage parameter / workspace \n        '

    def __init__(
        self,
        client_dir: str,
        enterprise_specs: List[EnterpriseSpec],
        workspace_dir: Optional[str] = None,
        model_name_list: List[str] = None,
        anthropic_base_url: Optional[str] = None,
        max_enterprise_concurrency: int = DEFAULT_MAX_ENTERPRISE_CONCURRENCY,
        max_department_concurrency: int = DEFAULT_MAX_DEPARTMENT_CONCURRENCY,
        simulation_max_step: int = DEFAULT_SERVICE_TOTAL_STEPS,
        market_demand_mode: str = DEFAULT_MARKET_DEMAND_MODE,
        beer_game_demand_series: Optional[List[float]] = None,
        beer_game_product_id: Optional[str] = None,
        cobweb_config: Optional[Dict[str, Any]] = None,
        shared_resource_config: Optional[Dict[str, Any]] = None,
        herding_config: Optional[Dict[str, Any]] = None
    ):
        self.client_dir = Path(client_dir)
        self.workspace_dir = Path(workspace_dir) if workspace_dir else (self.client_dir / "workspace_multi")
        MultiTenantUtils.configure_workspace(self.workspace_dir)
        self.simulation_max_step = simulation_max_step
        self.market_demand_mode = market_demand_mode
        self.beer_game_demand_series = beer_game_demand_series or list(DEFAULT_BEER_GAME_DEMAND_SERIES)
        self.beer_game_product_id = beer_game_product_id or _current_scenario_beer_game_product_id()
        self.cobweb_config = copy.deepcopy(cobweb_config or DEFAULT_COBWEB_CONFIG)
        self.shared_resource_config = copy.deepcopy(shared_resource_config or DEFAULT_SHARED_RESOURCE_CONFIG)
        self.herding_config = copy.deepcopy(herding_config or DEFAULT_HERDING_CONFIG)
        self.long_run_artifact_retention_policy: Dict[str, Any] = {}
        resolved_agent_base_url = (
            anthropic_base_url or EnvironmentConfig.LOCAL_AGENT_GATEWAY_BASE_URL
        )
        model_name_list = self._resolve_model_name_list(
            model_name_list or list(DEFAULT_AGENT_MODEL_NAME_LIST),
            len(enterprise_specs),
        )

        self.enterprise_specs: Dict[str, EnterpriseSpec] = {
            spec.enterprise_id: spec for spec in enterprise_specs
        }

        self.session_registry = SessionRegistry(self.workspace_dir / "session_registry.json")

        # Co-control enterprise
        self.enterprise_semaphore = asyncio.Semaphore(max_enterprise_concurrency)

        # Initialize global department lock manager (ensure that individual cases are created)
        GlobalDepartmentLockManager()

        self.runtimes: Dict[str, EnterpriseRuntime] = {
            spec.enterprise_id: EnterpriseRuntime(
                client_dir=str(self.client_dir),
                enterprise_spec=spec,
                session_registry=self.session_registry,
                model_name=model_name,
                anthropic_base_url=resolved_agent_base_url,
                max_department_concurrency=max_department_concurrency,
                workspace_dir=self.workspace_dir,
            )
            for spec, model_name in zip(
                enterprise_specs,
                model_name_list 
            )
        }
        missing_runtime_ids = [
            spec.enterprise_id
            for spec in enterprise_specs
            if spec.enterprise_id not in self.runtimes
        ]
        if missing_runtime_ids:
            raise RuntimeError(f"Missing EnterpriseRuntime for enterprises: {missing_runtime_ids}")

    @staticmethod
    def _resolve_model_name_list(model_name_list: List[str], enterprise_count: int) -> List[str]:
        """Ensure every enterprise gets a model; never let zip silently drop enterprises."""
        if enterprise_count <= 0:
            return []
        resolved = list(model_name_list or [])
        if not resolved:
            resolved = list(DEFAULT_AGENT_MODEL_NAME_LIST)
        if not resolved:
            resolved = [EnvironmentConfig.DEFAULT_AGENT_MODEL]
        if len(resolved) < enterprise_count:
            resolved.extend([resolved[-1]] * (enterprise_count - len(resolved)))
        return resolved[:enterprise_count]

    # ----------------------------

    # ----------------------------

    async def run_one_day(self, round_id: int) -> Dict[str, List[dict]]:
        print(f"\n========== WORLD DAY {round_id} START ==========")
        retention = self.long_run_artifact_retention_policy or {}
        retain_intermediate_observer = retention.get(
            "retain_intermediate_observer_state",
            True,
        )
        retain_intermediate_exchange = retention.get(
            "retain_intermediate_exchange_state",
            True,
        )
        MultiTenantUtils.handle_enterprise_daily()
        MultiTenantUtils.save_enterprise_observations()
        if retain_intermediate_observer:
            MultiTenantUtils.save_observer_state(round_id, "after_daily")

        # MultiTenantUtils.handle_test()
        async def run_enterprise_with_semaphore(enterprise_id, runtime):
            async with self.enterprise_semaphore:  # Use semaphore to control co-production
                return await runtime.run_workflow(round_id)

        async def run_trade_with_semaphore(enterprise_id, runtime):
            async with self.enterprise_semaphore:  # Use semaphore to control co-production
                return await runtime.run_trade(
                    round_id,
                    observation_already_refreshed=True,
                )

        tasks = {
            enterprise_id: asyncio.create_task(run_enterprise_with_semaphore(enterprise_id, rt))
            for enterprise_id, rt in self.runtimes.items()
        }
        await gather_model_tasks(*tasks.values())
        # Legacy architecture controls defer dispatch. SA v2 already dispatches
        # inside each enterprise workflow, matching the original C3 stage order.
        for runtime in self.runtimes.values():
            await runtime.dispatch_firm_phase(round_id, "decision")


        StaticUtils.check_orders()
        if retain_intermediate_exchange:
            MultiTenantUtils.save_exchange_info(round_id, "after_check_orders")
        if retain_intermediate_observer:
            MultiTenantUtils.save_observer_state(round_id, "after_check_orders")
        MultiTenantUtils.save_enterprise_observations()
        trade_tasks = {
            enterprise_id: asyncio.create_task(run_trade_with_semaphore(enterprise_id, rt))
            for enterprise_id, rt in self.runtimes.items()
        }
        await gather_model_tasks(*trade_tasks.values())
        for runtime in self.runtimes.values():
            await runtime.dispatch_firm_phase(round_id, "trade")

        StaticUtils.statistics_orders()
        MultiTenantUtils.save_exchange_info(round_id, "end_of_day")
        if retention.get("deduplicate_end_of_day_exchange_alias", False):
            MultiTenantUtils.materialize_exchange_root_alias(
                round_id,
                source_stage="end_of_day",
            )
        else:
            MultiTenantUtils.save_exchange_info(round_id)
        MultiTenantUtils.save_observer_state(round_id, "end_of_day")
        MultiTenantUtils.archive_enterprise_json_files(round_id)
        MultiTenantUtils.write_history_projection_summary(round_id)
        MultiTenantUtils.write_round_integrity_summary(round_id)
        MultiTenantUtils.write_run_metrics_projection(round_id)
        MultiTenantUtils.write_case_evaluation_projection(round_id)
        MultiTenantUtils.write_multi_enterprise_projections(round_id)
        
        print(f"========== WORLD DAY {round_id} END ==========\n")

    @staticmethod
    def _strip_volatile_checkpoint_fields(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: MultiEnterpriseClaudeManager._strip_volatile_checkpoint_fields(child)
                for key, child in value.items()
                if str(key).lower() not in {
                    "timestamp",
                    "created_at",
                    "updated_at",
                    "completed_at",
                }
            }
        if isinstance(value, list):
            return [
                MultiEnterpriseClaudeManager._strip_volatile_checkpoint_fields(item)
                for item in value
            ]
        if isinstance(value, float):
            return round(value, 8)
        return value

    @staticmethod
    def _observation_resume_signature(observation: Dict[str, Any]) -> Dict[str, Any]:
        observation = observation or {}
        return MultiEnterpriseClaudeManager._strip_volatile_checkpoint_fields({
            "finance": observation.get("finance") or {},
            "inventory": observation.get("inventory") or {},
            "production": observation.get("production") or {},
            "sales": observation.get("sales") or {},
            "procurement": observation.get("procurement") or {},
            "hr": observation.get("hr") or {},
        })

    @staticmethod
    def _canonicalize_resume_identifiers(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: MultiEnterpriseClaudeManager._canonicalize_resume_identifiers(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [
                MultiEnterpriseClaudeManager._canonicalize_resume_identifiers(child)
                for child in value
            ]
        if isinstance(value, str):
            return re.sub(
                r"(buy|sell|proposal|order)_\d+",
                lambda match: f"{match.group(1)}_ID",
                value,
                flags=re.IGNORECASE,
            )
        return value

    @staticmethod
    def _resume_values_equivalent(expected: Any, current: Any, path: str = "") -> bool:
        if isinstance(expected, dict) and isinstance(current, dict):
            return expected.keys() == current.keys() and all(
                MultiEnterpriseClaudeManager._resume_values_equivalent(
                    expected[key],
                    current[key],
                    f"{path}/{key}",
                )
                for key in expected
            )
        if isinstance(expected, list) and isinstance(current, list):
            return len(expected) == len(current) and all(
                MultiEnterpriseClaudeManager._resume_values_equivalent(
                    left,
                    right,
                    f"{path}/{index}",
                )
                for index, (left, right) in enumerate(zip(expected, current))
            )
        if (
            isinstance(expected, (int, float))
            and not isinstance(expected, bool)
            and isinstance(current, (int, float))
            and not isinstance(current, bool)
        ):
            difference = abs(float(expected) - float(current))
            field = path.rsplit("/", 1)[-1]
            small_monetary_drift_fields = {
                "available_after_warning_buffer",
                "available_conversion_budget",
                "available_procurement_budget",
                "cash",
                "current_cash",
                "net_profit",
                "remaining_cash_after_commitments",
                "total_cost",
                "total_maintenance_cost",
            }
            if field in small_monetary_drift_fields:
                return difference <= 5.0
            if field == "net_profit_rate":
                return difference <= 0.001
            return difference <= 1e-6
        return expected == current

    def _fetch_simulation_payload(self, endpoint: str, **request_json: Any) -> Dict[str, Any]:
        import requests

        response = requests.get(
            f"{StaticConfig.BASE_URL}{endpoint}",
            json=request_json or None,
            headers=StaticUtils.simulation_session_headers(),
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"Simulation endpoint {endpoint} failed: {payload}")
        return payload.get("data") or {}

    def _validate_replayed_state(self, source_root: Path, last_complete_round: int) -> None:
        mismatches = []
        for enterprise_id in self.enterprise_specs:
            expected_path = (
                source_root
                / "public"
                / "observer_state"
                / f"day{last_complete_round}"
                / "end_of_day"
                / f"{enterprise_id}.json"
            )
            if not expected_path.exists():
                raise FileNotFoundError(f"Resume observation missing: {expected_path}")
            expected_payload = json.loads(expected_path.read_text(encoding="utf-8"))
            expected = self._observation_resume_signature(
                expected_payload.get("observation") or {}
            )
            current_payload = self._fetch_simulation_payload(
                "/state",
                enterprise_name=enterprise_id,
            )
            current = self._observation_resume_signature(
                current_payload.get("observation") or {}
            )
            expected = self._canonicalize_resume_identifiers(expected)
            current = self._canonicalize_resume_identifiers(current)
            if not self._resume_values_equivalent(expected, current):
                mismatches.append(f"enterprise:{enterprise_id}")

        expected_exchange_path = (
            source_root
            / "public"
            / "exchange"
            / f"day{last_complete_round}"
            / "end_of_day"
            / "exchange.json"
        )
        if not expected_exchange_path.exists():
            raise FileNotFoundError(f"Resume exchange snapshot missing: {expected_exchange_path}")
        expected_exchange = json.loads(
            expected_exchange_path.read_text(encoding="utf-8")
        ).get("data") or {}
        current_exchange = self._fetch_simulation_payload("/exchange")
        expected_exchange = self._canonicalize_resume_identifiers(
            self._strip_volatile_checkpoint_fields(expected_exchange)
        )
        current_exchange = self._canonicalize_resume_identifiers(
            self._strip_volatile_checkpoint_fields(current_exchange)
        )
        if not self._resume_values_equivalent(expected_exchange, current_exchange):
            mismatches.append("exchange")
        if mismatches:
            raise RuntimeError(
                "Archived action replay did not reproduce the sealed checkpoint: "
                + ", ".join(mismatches)
            )

    @staticmethod
    def _archived_action_timestamp(action_path: Path) -> float:
        result_path = action_path.with_name(
            action_path.name.replace("_action.json", "_result.json")
        )
        payload = MultiTenantUtils._read_json_if_exists(result_path, {})
        timestamps: List[float] = []

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                timestamp = value.get("timestamp")
                if isinstance(timestamp, (int, float)):
                    timestamps.append(float(timestamp))
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(payload)
        return min(timestamps) if timestamps else float("inf")

    def _replay_archived_action_phase(
        self,
        action_specs: List[tuple[Path, str, str]],
        round_id: int,
        identifier_map: Optional[Dict[str, str]] = None,
    ) -> None:
        ordered = sorted(
            action_specs,
            key=lambda item: (
                self._archived_action_timestamp(item[0]),
                str(item[0]),
            ),
        )
        groups: List[List[tuple[Path, str, str]]] = []
        for action_spec in ordered:
            timestamp = self._archived_action_timestamp(action_spec[0])
            if (
                groups
                and timestamp != float("inf")
                and self._archived_action_timestamp(groups[-1][0][0]) != float("inf")
                and timestamp - self._archived_action_timestamp(groups[-1][0][0]) <= 0.05
            ):
                groups[-1].append(action_spec)
            else:
                groups.append([action_spec])

        def replay(action_spec: tuple[Path, str, str]) -> Dict[str, Any]:
            action_path, department, enterprise_id = action_spec
            return StaticUtils.replay_archived_action(
                action_path,
                department=department,
                enterprise_name=enterprise_id,
                round_id=round_id,
                identifier_map=identifier_map,
            )

        for group in groups:
            if len(group) == 1:
                replay(group[0])
                continue
            StaticUtils.replay_archived_action_group(
                group,
                round_id=round_id,
                identifier_map=identifier_map,
            )

    @staticmethod
    def _proposal_semantic_key(proposal: Dict[str, Any]) -> tuple[Any, ...]:
        def number(value: Any) -> Optional[float]:
            try:
                return round(float(value), 8)
            except (TypeError, ValueError):
                return None

        return (
            proposal.get("buyer_company_id") or proposal.get("buyer_id"),
            proposal.get("seller_company_id") or proposal.get("seller_id"),
            proposal.get("product_id") or proposal.get("material_id"),
            number(proposal.get("quantity") or proposal.get("proposed_quantity")),
            number(
                proposal.get("unit_price")
                or proposal.get("proposed_unit_price")
                or proposal.get("price")
            ),
            proposal.get("status"),
        )

    @staticmethod
    def _collect_proposals(value: Any) -> List[Dict[str, Any]]:
        proposals: List[Dict[str, Any]] = []
        if isinstance(value, dict):
            if value.get("proposal_id"):
                proposals.append(value)
            for child in value.values():
                proposals.extend(MultiEnterpriseClaudeManager._collect_proposals(child))
        elif isinstance(value, list):
            for child in value:
                proposals.extend(MultiEnterpriseClaudeManager._collect_proposals(child))
        return proposals

    def _build_replay_proposal_id_map(
        self,
        source_root: Path,
        round_id: int,
        checkpoint: str = "after_check_orders",
    ) -> Dict[str, str]:
        expected_path = (
            source_root
            / "public"
            / "exchange"
            / f"day{round_id}"
            / checkpoint
            / "exchange.json"
        )
        expected_payload = MultiTenantUtils._read_json_if_exists(expected_path, {})
        expected_exchange = expected_payload.get("data") or expected_payload
        current_exchange = self._fetch_simulation_payload("/exchange")
        current_by_key = {
            self._proposal_semantic_key(item): str(item.get("proposal_id"))
            for item in self._collect_proposals(current_exchange)
            if item.get("proposal_id")
        }
        identifier_map: Dict[str, str] = {}
        for item in self._collect_proposals(expected_exchange):
            archived_id = str(item.get("proposal_id") or "")
            current_id = current_by_key.get(self._proposal_semantic_key(item))
            if archived_id and current_id and archived_id != current_id:
                identifier_map[archived_id] = current_id
        return identifier_map

    def _replay_archived_rounds(self, source_root: Path, last_complete_round: int) -> None:
        """Rebuild an old run that predates persisted runtime checkpoints."""
        trade_departments = {"sales", "procurement"}
        for round_id in range(last_complete_round + 1):
            StaticUtils.replay_archived_action(
                source_root / "_static_commands" / "daily_action.json",
                execute_type="daily",
            )
            decision_id_map = (
                self._build_replay_proposal_id_map(
                    source_root,
                    round_id - 1,
                    checkpoint="end_of_day",
                )
                if round_id > 0
                else {}
            )
            decision_actions = []
            for enterprise_id, enterprise_spec in self.enterprise_specs.items():
                for dept in enterprise_spec.departments:
                    if not dept.enabled or dept.dept_id == "finance":
                        continue
                    day_dir = (
                        source_root
                        / "enterprises"
                        / enterprise_id
                        / "department"
                        / dept.dept_id
                        / f"day{round_id}"
                    )
                    action_path = (
                        day_dir / f"pre_{dept.dept_id}_action.json"
                        if dept.dept_id in trade_departments
                        else day_dir / f"{dept.dept_id}_action.json"
                    )
                    decision_actions.append(
                        (action_path, dept.dept_id, enterprise_id)
                    )
            self._replay_archived_action_phase(
                decision_actions,
                round_id,
                identifier_map=decision_id_map,
            )
            StaticUtils.check_orders()
            proposal_id_map = self._build_replay_proposal_id_map(
                source_root,
                round_id,
            )
            trade_actions = []
            for enterprise_id, enterprise_spec in self.enterprise_specs.items():
                for dept in enterprise_spec.departments:
                    if not dept.enabled or dept.dept_id not in trade_departments:
                        continue
                    action_path = (
                        source_root
                        / "enterprises"
                        / enterprise_id
                        / "department"
                        / dept.dept_id
                        / f"day{round_id}"
                        / f"{dept.dept_id}_action.json"
                    )
                    trade_actions.append(
                        (action_path, dept.dept_id, enterprise_id)
                    )
            self._replay_archived_action_phase(
                trade_actions,
                round_id,
                identifier_map=proposal_id_map,
            )
            StaticUtils.statistics_orders()
            if round_id < last_complete_round:
                StaticUtils.run_day()
        self._validate_replayed_state(source_root, last_complete_round)
        StaticUtils.run_day()

    def _restore_or_replay_resume_state(
        self,
        *,
        resume_context: Dict[str, Any],
        run_id: str,
        scenario_id: str,
    ) -> Dict[str, Any]:
        source_root = Path(
            resume_context.get("artifact_root") or self.workspace_dir
        ).resolve()
        last_complete_round = int(resume_context.get("last_complete_round"))
        quarantined_artifacts = self._quarantine_incomplete_resume_artifacts(
            source_root,
            last_complete_round,
        )
        checkpoint_path = source_root / "_runtime_checkpoint.pkl"
        if checkpoint_path.exists():
            restored = StaticUtils.load_runtime_checkpoint(
                checkpoint_path,
                run_id=run_id,
                scenario_id=scenario_id,
                last_complete_round=last_complete_round,
            )
            return {
                "mode": "runtime_checkpoint",
                "checkpoint_path": str(checkpoint_path),
                "quarantined_incomplete_artifacts": quarantined_artifacts,
                **restored,
            }
        for init_index in range(1, 4):
            StaticUtils.replay_archived_action(
                source_root
                / "_static_commands"
                / f"init_action_{init_index}.json",
                execute_type="init",
            )
        self._replay_archived_rounds(source_root, last_complete_round)
        replay_checkpoint = StaticUtils.save_runtime_checkpoint(
            checkpoint_path,
            run_id=run_id,
            scenario_id=scenario_id,
            completed_steps=last_complete_round + 1,
            last_complete_round=last_complete_round,
        )
        return {
            "mode": "archived_action_replay",
            "checkpoint_path": str(checkpoint_path),
            "replay_checkpoint": replay_checkpoint,
            "quarantined_incomplete_artifacts": quarantined_artifacts,
            "last_complete_round": last_complete_round,
            "current_day": last_complete_round + 1,
        }

    @staticmethod
    def _quarantine_incomplete_resume_artifacts(
        source_root: Path,
        last_complete_round: int,
    ) -> int:
        """Move partial post-checkpoint artifacts aside before replaying a turn."""
        candidates: List[Path] = []
        for path in source_root.rglob("day*"):
            try:
                relative = path.relative_to(source_root)
            except ValueError:
                continue
            if relative.parts and relative.parts[0].startswith("_resume_incomplete_attempts"):
                continue
            match = re.fullmatch(r"day(\d+)(?:\.json)?", path.name)
            if match and int(match.group(1)) > int(last_complete_round):
                candidates.append(path)
        selected: List[Path] = []
        for path in sorted(candidates, key=lambda item: len(item.parts)):
            if any(existing == path or existing in path.parents for existing in selected):
                continue
            selected.append(path)
        if not selected:
            return 0
        quarantine_root = (
            source_root
            / "_resume_incomplete_attempts"
            / datetime.now().strftime("%Y%m%dT%H%M%S")
        )
        for path in selected:
            destination = quarantine_root / path.relative_to(source_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
        return len(selected)

    @staticmethod
    def _audit_exhausted_by_timeouts(audit_path: Path) -> bool:
        audit = MultiTenantUtils._read_json_if_exists(audit_path, {})
        if not isinstance(audit, dict) or not audit.get("fallback_used"):
            return False
        try:
            attempts = int(audit.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        if attempts <= 0:
            return False

        raw_name = (
            "trade_skill_raw_messages.json"
            if audit_path.name == "trade_skill_execution_audit.json"
            else "analyst_raw_messages.json"
            if audit_path.name == "analyst_execution_audit.json"
            else "skill_raw_messages.json"
        )
        raw_messages = MultiTenantUtils._read_json_if_exists(
            audit_path.with_name(raw_name),
            [],
        )
        timeout_count = sum(
            1
            for item in (raw_messages if isinstance(raw_messages, list) else [])
            if isinstance(item, dict)
            and "[Timeout]" in str(item.get("content") or "")
        )
        return timeout_count >= attempts

    def _build_agent_timeout_round_health(
        self,
        round_id: int,
        policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        expected = []
        include_trade = bool(policy.get("include_trade_phase", True))
        include_analyst = bool(policy.get("include_analyst_phase", True))
        for enterprise_spec in self.enterprise_specs.values():
            for department in enterprise_spec.departments:
                if not department.enabled:
                    continue
                day_dir = (
                    self.workspace_dir
                    / "enterprises"
                    / enterprise_spec.enterprise_name
                    / "department"
                    / department.dept_id
                    / f"day{round_id}"
                )
                expected.append({
                    "enterprise_id": enterprise_spec.enterprise_id,
                    "department": department.dept_id,
                    "phase": "decision",
                    "path": day_dir / "skill_execution_audit.json",
                })
                if include_trade and department.need_trade:
                    expected.append({
                        "enterprise_id": enterprise_spec.enterprise_id,
                        "department": department.dept_id,
                        "phase": "trade",
                        "path": day_dir / "trade_skill_execution_audit.json",
                    })
            runtime = getattr(self, "runtimes", {}).get(
                enterprise_spec.enterprise_id
            )
            should_run_analyst = getattr(runtime, "_should_run_analyst", None)
            if (
                include_analyst
                and callable(should_run_analyst)
                and should_run_analyst(round_id)
            ):
                expected.append({
                    "enterprise_id": enterprise_spec.enterprise_id,
                    "department": "analyst",
                    "phase": "analysis",
                    "path": (
                        self.workspace_dir
                        / "enterprises"
                        / enterprise_spec.enterprise_name
                        / "department"
                        / "analyst"
                        / f"day{round_id}"
                        / "analyst_execution_audit.json"
                    ),
                })

        missing = []
        timed_out = []
        completed = []
        for item in expected:
            audit_path = item["path"]
            descriptor = {
                key: value
                for key, value in item.items()
                if key != "path"
            }
            descriptor["audit_path"] = str(audit_path)
            if not audit_path.exists():
                missing.append(descriptor)
            elif self._audit_exhausted_by_timeouts(audit_path):
                timed_out.append(descriptor)
            else:
                completed.append(descriptor)

        minimum_calls = max(
            1,
            int(policy.get("minimum_expected_agent_calls") or 1),
        )
        expected_count = len(expected)
        all_timed_out = bool(
            expected_count >= minimum_calls
            and not missing
            and len(timed_out) == expected_count
        )
        return {
            "schema_version": "agent_timeout_round_health.v1",
            "round_id": int(round_id),
            "expected_agent_calls": expected_count,
            "audited_agent_calls": expected_count - len(missing),
            "timeout_exhausted_calls": len(timed_out),
            "non_timeout_calls": len(completed),
            "missing_audits": missing,
            "timeout_calls": timed_out,
            "all_agent_calls_timed_out": all_timed_out,
        }
        
    @abort_on_fatal_model_error
    async def run(
        self,
        max_step: Optional[int] = None,
        checkpoint_callback: Optional[Callable[..., Any]] = None,
        operations_job_id: Optional[str] = None,
        operations_run_id: Optional[str] = None,
        operations_repository: Optional[Any] = None,
        resume_context: Optional[Dict[str, Any]] = None,
        pause_after_round: Optional[int] = None,
    ):
        max_step = max_step or self.simulation_max_step
        run_id = operations_run_id or MultiTenantUtils.generate_next_run_id()
        job_record_id = operations_job_id or run_id
        scenario_id = get_active_scenario_id()
        scenario_metadata = get_active_scenario_metadata()
        scenario_config = get_scenario_config(scenario_id)
        scenario_config = _materialize_market_config_snapshot(
            scenario_config,
            beer_game_product_id=self.beer_game_product_id,
        )
        experiment_design = scenario_config.get("experiment_design") or {}
        integration_profiles = get_scenario_integration_profiles(scenario_id)
        previous_run_meta = MultiTenantUtils._read_json_if_exists(
            self.workspace_dir / "run_meta.json",
            {},
        )
        run_started_at = (
            previous_run_meta.get("started_at")
            if resume_context
            else None
        ) or os.getenv("SIMULATION_RUN_STARTED_AT") or datetime.now().isoformat(timespec="seconds")
        config_version_id = f"{job_record_id}:config"

        workspace_run_meta = {
            "run_id": run_id,
            "job_id": job_record_id,
            "status": "running",
            "started_at": run_started_at,
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
            "planned_total_steps": max_step,
            "market_demand_mode": self.market_demand_mode,
            "beer_game_product_id": self.beer_game_product_id,
            "cobweb_config": self.cobweb_config,
            "shared_resource_config": self.shared_resource_config,
            "herding_config": self.herding_config,
            "enterprise_ids": list(self.enterprise_specs.keys()),
            "safety_stop_policy": MultiTenantUtils.get_safety_stop_policy(),
        }
        hybrid_replay_policy = (
            (scenario_config.get("runtime_injection") or {}).get(
                "long_horizon_hybrid_replay_policy"
            )
            or {}
        )
        if hybrid_replay_policy.get("enabled"):
            workspace_run_meta["hybrid_replay"] = {
                "schema_version": "long_horizon_hybrid_replay.v1",
                "derived_run": True,
                "pure_agent_sample": False,
                "source_run_id": hybrid_replay_policy.get("source_run_id"),
                "source_artifact_root": hybrid_replay_policy.get(
                    "source_artifact_root"
                ),
                "start_round": hybrid_replay_policy.get("start_round", 0),
                "end_round": hybrid_replay_policy.get("end_round"),
                "selection_policy": hybrid_replay_policy.get("selection_policy"),
            }
        if resume_context:
            workspace_run_meta["resume"] = {
                "requested_at": resume_context.get("requested_at"),
                "source_run_id": resume_context.get("source_run_id") or run_id,
                "last_complete_round": int(resume_context.get("last_complete_round")),
                "completed_steps_before_resume": int(
                    resume_context.get("completed_steps")
                    or int(resume_context.get("last_complete_round")) + 1
                ),
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
                    "orchestrator": "multi_enterprise",
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
        long_run_policy = (
            (scenario_config.get("runtime_injection") or {}).get(
                "long_run_experiment_policy"
            )
            or {}
        )
        self.long_run_artifact_retention_policy = (
            copy.deepcopy(long_run_policy.get("artifact_retention_policy") or {})
            if long_run_policy.get("enabled")
            else {}
        )
        persist_runtime_checkpoint = bool(
            long_run_policy.get("enabled")
            and long_run_policy.get("checkpoint_every_turn")
        )
        timeout_breaker_policy = (
            long_run_policy.get("agent_timeout_circuit_breaker") or {}
        )
        timeout_breaker_enabled = bool(
            long_run_policy.get("enabled")
            and timeout_breaker_policy.get("enabled")
        )
        consecutive_all_timeout_rounds = 0
        timeout_health_history: List[Dict[str, Any]] = []
        for enterprise_id, enterprise_spec in self.enterprise_specs.items():
            enterprise_root = self.workspace_dir / "enterprises" / enterprise_id
            if not enterprise_root.exists():
                enterprise_root.mkdir(parents=True, exist_ok=True)
            
        if resume_context:
            resume_result = self._restore_or_replay_resume_state(
                resume_context=resume_context,
                run_id=run_id,
                scenario_id=scenario_id,
            )
            workspace_run_meta["resume"].update(resume_result)
            start_step = int(resume_context.get("last_complete_round")) + 1
            workspace_run_meta["completed_steps"] = start_step
            MultiTenantUtils.write_workspace_run_metadata(workspace_run_meta)
        else:
            MultiTenantUtils.handle_enterprises_init()
            start_step = 0
        stop_reason = None
        for step in range(start_step, max_step):
            await self.run_one_day(step)
            StaticUtils.run_day()
            timeout_health = None
            if timeout_breaker_enabled:
                timeout_health = self._build_agent_timeout_round_health(
                    step,
                    timeout_breaker_policy,
                )
                if timeout_health["all_agent_calls_timed_out"]:
                    consecutive_all_timeout_rounds += 1
                else:
                    consecutive_all_timeout_rounds = 0
                timeout_health["consecutive_all_timeout_rounds"] = (
                    consecutive_all_timeout_rounds
                )
                timeout_health_history.append(timeout_health)
                timeout_health_history = timeout_health_history[-20:]
                MultiTenantUtils._write_json(
                    self.workspace_dir
                    / "runtime_health"
                    / f"day{step}_agent_timeout.json",
                    timeout_health,
                )
                workspace_run_meta["agent_timeout_circuit_breaker"] = {
                    "policy": copy.deepcopy(timeout_breaker_policy),
                    "latest": timeout_health,
                    "recent_history": timeout_health_history,
                }
            runtime_checkpoint = None
            if persist_runtime_checkpoint:
                runtime_checkpoint = StaticUtils.save_runtime_checkpoint(
                    self.workspace_dir / "_runtime_checkpoint.pkl",
                    run_id=run_id,
                    scenario_id=scenario_id,
                    completed_steps=step + 1,
                    last_complete_round=step,
                )
            if checkpoint_callback is not None:
                checkpoint_callback(
                    completed_steps=step + 1,
                    last_complete_round=step,
                    checkpoint_uri=(
                        f"file://{runtime_checkpoint['checkpoint_path']}"
                        if runtime_checkpoint
                        else f"file://{self.workspace_dir}"
                    ),
                    artifact_root=str(self.workspace_dir),
                    details={
                        "orchestrator": "multi_enterprise",
                        "scenario_id": scenario_id,
                        "round_id": step,
                        **(
                            {"agent_timeout_health": timeout_health}
                            if timeout_health
                            else {}
                        ),
                        **(
                            {"runtime_checkpoint": runtime_checkpoint}
                            if runtime_checkpoint
                            else {}
                        ),
                    },
                )
            timeout_threshold = max(
                2,
                int(
                    timeout_breaker_policy.get(
                        "consecutive_all_timeout_rounds",
                        2,
                    )
                    or 2
                ),
            )
            if (
                timeout_health
                and timeout_breaker_policy.get("pause_after_complete_round", True)
                and timeout_health["all_agent_calls_timed_out"]
                and consecutive_all_timeout_rounds >= timeout_threshold
            ):
                effective_total_steps = step + 1
                stop_reason = {
                    "type": "agent_timeout_circuit_breaker",
                    "message": (
                        'Every Agent call exhausted timeout retries over consecutive complete rounds. Paused at a complete checkpoint; resume after restoring model service or improving bounded context.'
                    ),
                    "policy": "seal_last_complete_round",
                    "completed_steps": effective_total_steps,
                    "last_complete_round": step,
                    "consecutive_all_timeout_rounds": consecutive_all_timeout_rounds,
                    "round_health": timeout_health,
                }
                workspace_run_meta.update({
                    "status": "stopped",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "completed_steps": effective_total_steps,
                    "last_complete_round": step,
                    "stopped_after_round": step,
                    "finish_reason": stop_reason["message"],
                    "stop_reason": stop_reason,
                    "latest_checkpoint": {
                        "completed_steps": effective_total_steps,
                        "last_complete_round": step,
                        "checkpoint_uri": (
                            f"file://{runtime_checkpoint['checkpoint_path']}"
                            if runtime_checkpoint
                            else f"file://{self.workspace_dir}"
                        ),
                        "artifact_root": str(self.workspace_dir),
                    },
                })
                MultiTenantUtils.write_workspace_stop_report({
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "status": "stopped",
                    "started_at": run_started_at,
                    "finished_at": workspace_run_meta["finished_at"],
                    "effective_total_steps": effective_total_steps,
                    "stopped_after_round": step,
                    "stop_reason": stop_reason,
                })
                MultiTenantUtils.write_workspace_run_metadata(workspace_run_meta)
                print(
                    "Simulation paused by Agent timeout circuit breaker after round: "
                    f"{step}"
                )
                break
            if pause_after_round is not None and step >= int(pause_after_round):
                effective_total_steps = step + 1
                stop_reason = {
                    "type": "planned_hybrid_replay_pause",
                    "message": (
                        "Hybrid replay segment completed and sealed for optional "
                        "Agent continuation."
                    ),
                    "policy": "seal_last_complete_round",
                    "completed_steps": effective_total_steps,
                    "last_complete_round": step,
                }
                workspace_run_meta.update({
                    "status": "stopped",
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "completed_steps": effective_total_steps,
                    "last_complete_round": step,
                    "stopped_after_round": step,
                    "finish_reason": "planned hybrid replay segment completed",
                    "stop_reason": stop_reason,
                    "latest_checkpoint": {
                        "completed_steps": effective_total_steps,
                        "last_complete_round": step,
                        "checkpoint_uri": (
                            f"file://{runtime_checkpoint['checkpoint_path']}"
                            if runtime_checkpoint
                            else f"file://{self.workspace_dir}"
                        ),
                        "artifact_root": str(self.workspace_dir),
                    },
                })
                MultiTenantUtils.write_workspace_stop_report({
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "status": "stopped",
                    "started_at": run_started_at,
                    "finished_at": workspace_run_meta["finished_at"],
                    "effective_total_steps": effective_total_steps,
                    "stopped_after_round": step,
                    "stop_reason": stop_reason,
                })
                MultiTenantUtils.write_workspace_run_metadata(workspace_run_meta)
                print(
                    "Simulation paused after sealed hybrid replay round: "
                    f"{step}"
                )
                break
            mirror_repo = operations_repository or get_sql_mirror_repository(integration_profiles)
            if mirror_repo is not None:
                stop_reason = OperationsStopHook(
                    OperationsService(mirror_repo),
                    job_record_id,
                ).seal_if_requested(completed_round=step)
                if stop_reason:
                    effective_total_steps = step + 1
                    workspace_run_meta.update(
                        {
                            "status": "stopped",
                            "finished_at": datetime.now().isoformat(timespec="seconds"),
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
                enterprise_ids=list(self.enterprise_specs.keys()),
            )
            if stop_reason:
                effective_total_steps = step + 1
                workspace_run_meta.update(
                    {
                        "status": "stopped_by_guard",
                        "finished_at": datetime.now().isoformat(timespec="seconds"),
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
        final_completed_steps = workspace_run_meta.get("completed_steps", max_step)
        if final_completed_steps:
            MultiTenantUtils.write_run_metrics_projection(final_completed_steps - 1)
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
                "Simulation snapshot archive skipped: workspace_jobs is authoritative."
            )
        else:
            snapshot_path = MultiTenantUtils.archive_current_run_snapshot(
                run_id=run_id,
                metadata={
                    "status": workspace_run_meta["status"],
                    "started_at": run_started_at,
                    "finished_at": workspace_run_meta["finished_at"],
                    "scenario_id": scenario_id,
                    "scenario_meta": scenario_metadata,
                    "scenario_config": scenario_config,
                    "experiment_design": experiment_design,
                    "integration_profiles": integration_profiles,
                    "total_steps": workspace_run_meta.get("completed_steps", max_step),
                    "market_demand_mode": self.market_demand_mode,
                    "cobweb_config": self.cobweb_config,
                    "herding_config": self.herding_config,
                    "enterprise_ids": list(self.enterprise_specs.keys()),
                    "stop_reason": stop_reason,
                }
            )
            print(f"Simulation snapshot archived to: {snapshot_path}")
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

def build_demo_specs() -> List[EnterpriseSpec]:
    enterprise_specs = []
    for enterprise_layout in get_agent_enterprise_layout():
        departments = [
            DepartmentSpec(**department_layout)
            for department_layout in enterprise_layout["departments"]
        ]
        enterprise_specs.append(
            EnterpriseSpec(
                enterprise_id=enterprise_layout["enterprise_id"],
                enterprise_name=enterprise_layout["enterprise_name"],
                departments=departments,
            )
        )
    return enterprise_specs

# ============================================================

# ============================================================

def resolve_agent_run_steps(default_steps: int) -> int:
    """Allow smoke tests to override Agent run length without changing scenario presets."""
    raw_value = os.getenv("SIMULATION_AGENT_RUN_STEPS")
    if not raw_value:
        return default_steps
    try:
        resolved_steps = int(raw_value)
    except ValueError:
        return default_steps
    return resolved_steps if resolved_steps > 0 else default_steps


async def main():
    enterprise_specs = build_demo_specs()

    manager = MultiEnterpriseClaudeManager(
        client_dir=str(Path(__file__).parent.absolute()),
        enterprise_specs=enterprise_specs,
        workspace_dir=str(Path(__file__).parent / "workspace_multi"),
        max_enterprise_concurrency=DEFAULT_MAX_ENTERPRISE_CONCURRENCY,
        max_department_concurrency=DEFAULT_MAX_DEPARTMENT_CONCURRENCY,
    )
    await manager.run(resolve_agent_run_steps(DEFAULT_AGENT_RUN_STEPS))


if __name__ == "__main__":
    anyio.run(main)
