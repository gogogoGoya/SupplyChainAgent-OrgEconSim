from __future__ import annotations

import asyncio
from contextlib import aclosing
import ast
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List, Optional

from agent.data_config import DepartmentSpec, EnterpriseSpec
from agent.multi_tenant_utils import MultiTenantUtils
from agent.GlobalDepartmentLockManager import GlobalDepartmentLockManager
from agent.scripted_rule_runner import ScriptedRuleRunner
from agent.long_horizon_hybrid_replay import LongHorizonHybridReplayRunner
from claude_agent_sdk import query
from claude_agent_sdk._errors import ProcessError

from agent.SessionRegistry import SessionRegistry

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config.environment_config import EnvironmentConfig
from config.fd_ablation_experiment import VERSION as FD_ABLATION_VERSION
from config.integration_profiles import resolve_integration_profiles
from config.simulation_preset_config import get_active_scenario_id
from config.fd_experiment import (
    FD4_WORKLOAD_REFERENCE_VERSIONS, FD4_ROLE_CONTRACT_VERSIONS, FD4_COMPLETION_VERSIONS, FD4_REFERENCE_VERSIONS,
    FD4_DECISION_CHECK_VERSIONS, FD4_AGENT_PROMPT_VERSION,
    fd2_agent_policy, fd_completion_enabled, fd2_fact_audit_policy,
)
from core.fd_decision_evidence import validate_fd4_decision
from core.fd2_fact_audit import fd2_fact_warnings
from core.model_failure import (
    FatalModelError, attach_model_failure, check_model_message, classify_model_failure,
)
from core.fd_forward_evidence import describe_forward_evidence
from core.skill_protocol import (
    SkillExecutionAudit,
    adapt_legacy_actions,
    adapt_legacy_analysis,
    audit_context_files,
)
from coordination.semantic_rule_packs import describe_source_routes
from agent.fd_experiment_runtime import (
    read_policy, materialize_coordination_input, configure_input_access, procurement_guidance,
    FDInputLoopError, fd_input_retry_context, FD_INPUT_ACCESS_VERSION,
    accessed_inputs, input_access_prompt, fd4_decision_guidance, output_access_evidence,
    compact_fd_input, materialize_fd_department_inputs, project_fd4_decision_evidence,
    project_fd2_decision_evidence, fd2_decision_guidance,
)

@dataclass
class SkillRunSpec:
    role: str
    name: str
    skill_name: str
    skill_args: str
    output_file_name: str
    output_file_path: str
    analysis_path: str
    state_path: str
    blackboard_path: str
    trade_decision_card_path: Optional[str]
    communication_file_path: str
    action_template_dir: str
    compact_context: bool = False


@dataclass
class RunInspection:
    skill_called: bool
    skill_args_ok: bool
    read_analysis: bool
    read_state: bool
    read_blackboard: bool
    read_trade_decision_card: bool
    write_called: bool
    write_target_ok: bool
    read_output_called: bool
    has_json_block: bool
    json_block: Optional[str]
    failure_type: str

@dataclass
class AnalystRunSpec:
    role: str
    skill_name: str
    skill_args: str
    output_file_name: str
    output_file_path: str
    observation_path: str
    compact_observation_enabled: bool
    history_projection_path: str
    history_projection_day: int
    compact_observation_kind: str = ""
    chart_export_path: str = ""
    chart_export_enabled: bool = False


@dataclass
class AnalystRunInspection:
    skill_called: bool
    skill_args_ok: bool
    write_called: bool
    write_target_ok: bool
    read_output_called: bool
    has_json_block: bool
    json_block: Optional[str]
    failure_type: str


class SkillRunner:

    def __init__(
        self,
        enterprise_spec: EnterpriseSpec,
        session_registry: SessionRegistry,
        build_options: Callable,
        client_dir: str,
        workspace_dir: Optional[Path] = None,
    ):
        self.enterprise_spec = enterprise_spec
        self.global_department_lock_manager = GlobalDepartmentLockManager()
        self.session_registry = session_registry
        self.build_options = build_options
        self.client_dir = Path(client_dir)
        self.workspace_dir = Path(workspace_dir) if workspace_dir else (
            self.client_dir / "workspace_multi"
        )
        self.scripted_rule_runner = ScriptedRuleRunner(
            self.client_dir,
            self.enterprise_spec,
            workspace_dir=self.workspace_dir,
        )
        self.hybrid_replay_runner = LongHorizonHybridReplayRunner(
            self.workspace_dir,
            self.enterprise_spec,
            self.scripted_rule_runner,
        )

    def _workspace_root(self) -> Path:
        workspace_dir = getattr(self, "workspace_dir", None)
        if workspace_dir is not None:
            return Path(workspace_dir)
        return Path(self.client_dir) / "workspace_multi"

    def _runtime_contract(self) -> Dict[str, Any]:
        run_meta = MultiTenantUtils._read_json_if_exists(
            self._workspace_root() / "run_meta.json",
            {},
        )
        scenario_config = run_meta.get("scenario_config") or {}
        profiles = run_meta.get("integration_profiles")
        if not isinstance(profiles, dict):
            profiles = resolve_integration_profiles(scenario_config)
        return {
            "run_id": str(run_meta.get("run_id") or "workspace_unarchived"),
            "scenario_id": str(
                run_meta.get("scenario_id")
                or (scenario_config.get("meta") or {}).get("scenario_id")
                or get_active_scenario_id()
            ),
            "scenario_config": scenario_config,
            "profiles": profiles,
            "run_meta": run_meta,
        }

    def _skill_profile(self) -> Dict[str, Any]:
        contract = self._runtime_contract()
        return (
            ((contract["profiles"].get("capabilities") or {}).get("skill"))
            or {}
        )

    def _analysis_profile(self) -> Dict[str, Any]:
        contract = self._runtime_contract()
        return (
            ((contract["profiles"].get("capabilities") or {}).get("analysis"))
            or {}
        )

    def _long_run_agent_failure_continuation_enabled(self) -> bool:
        contract = self._runtime_contract()
        runtime_injection = (
            (contract.get("scenario_config") or {}).get("runtime_injection") or {}
        )
        policy = runtime_injection.get("long_run_experiment_policy") or {}
        return bool(
            policy.get("enabled")
            and policy.get("continue_on_agent_failure")
            and str((contract.get("run_meta") or {}).get("orchestrator") or "multi_enterprise")
            == "multi_enterprise"
        )

    def _long_run_experiment_policy(self) -> Dict[str, Any]:
        contract = self._runtime_contract()
        runtime_injection = (
            (contract.get("scenario_config") or {}).get("runtime_injection") or {}
        )
        policy = runtime_injection.get("long_run_experiment_policy") or {}
        if not isinstance(policy, dict) or not policy.get("enabled"):
            return {}
        if str(contract.get("scenario_id") or "") != "long_horizon_evolution":
            return {}
        return policy

    def _long_run_agent_timeout_seconds(
        self,
        *,
        role: str,
        round_id: int,
        retry_index: int,
        default_seconds: int,
        analyst: bool = False,
    ) -> int:
        policy = self._long_run_experiment_policy()
        timeout_policy = policy.get("agent_timeout_policy") or {}
        if not timeout_policy.get("enabled"):
            return max(1, int(default_seconds))
        if analyst:
            event_turns = {int(item) for item in policy.get("event_turns") or []}
            follow_up_offsets = {
                int(item) for item in policy.get("event_follow_up_offsets") or []
            }
            event_review_rounds = {
                event_turn + offset
                for event_turn in event_turns
                for offset in follow_up_offsets
            }
            if retry_index > 0:
                configured = timeout_policy.get("analyst_retry_seconds")
            elif int(round_id) in event_review_rounds:
                configured = timeout_policy.get("analyst_event_attempt_seconds")
            else:
                configured = timeout_policy.get("analyst_first_attempt_seconds")
        elif retry_index > 0:
            configured = timeout_policy.get("department_retry_seconds")
        else:
            configured = timeout_policy.get("department_first_attempt_seconds")
        try:
            resolved = int(configured)
        except (TypeError, ValueError):
            resolved = int(default_seconds)
        try:
            hard_max = int(timeout_policy.get("hard_max_seconds") or resolved)
        except (TypeError, ValueError):
            hard_max = resolved
        return max(1, min(resolved, max(1, hard_max)))

    def _long_run_agent_context_compaction_policy(self) -> Dict[str, Any]:
        """Return the E1-only bounded Agent input policy from the run snapshot."""
        contract = self._runtime_contract()
        scenario_config = contract.get("scenario_config") or {}
        runtime_injection = scenario_config.get("runtime_injection") or {}
        long_run_policy = runtime_injection.get("long_run_experiment_policy") or {}
        compaction = long_run_policy.get("agent_context_compaction") or {}
        run_meta = contract.get("run_meta") or {}
        is_e1 = bool(
            str(contract.get("scenario_id") or "") == "long_horizon_evolution"
            or str(
                ((scenario_config.get("experiment_design") or {}).get("experiment_group"))
                or ""
            ).upper() == "E1"
        )
        if not (
            is_e1
            and long_run_policy.get("enabled") is True
            and compaction.get("enabled") is True
            and str(run_meta.get("orchestrator") or "multi_enterprise")
            == "multi_enterprise"
        ):
            return {}
        return compaction

    @staticmethod
    def _bounded_records(records: Any, limit: int, *, keep_tail: bool = False) -> List[Any]:
        if not isinstance(records, list):
            return []
        bounded_limit = max(0, int(limit))
        if bounded_limit == 0:
            return []
        return records[-bounded_limit:] if keep_tail else records[:bounded_limit]

    @staticmethod
    def _order_is_currently_actionable(order: Any, round_id: int) -> bool:
        if not isinstance(order, dict):
            return False
        for field in ("offer_expiry_day", "delivery_deadline"):
            value = order.get(field)
            try:
                if value not in (None, "") and int(round_id) > int(value):
                    return False
            except (TypeError, ValueError):
                continue
        return True

    @classmethod
    def _compact_long_run_procurement_state(
        cls,
        self_state: Dict[str, Any],
        *,
        recent_history_items: int,
        max_open_records: int,
    ) -> Dict[str, Any]:
        compacted = {
            key: value
            for key, value in self_state.items()
            if key not in {"orders", "replenishment", "supplier_selection_events"}
        }
        events = self_state.get("supplier_selection_events") or []
        compacted["supplier_selection_events"] = cls._bounded_records(
            events,
            recent_history_items,
            keep_tail=True,
        )
        explicit_material_ids = {
            str(item)
            for item in (self_state.get("purchasable_materials_idList") or [])
            if item not in (None, "")
        }
        if explicit_material_ids:
            matrix = self_state.get("materials_suppliers_matrix") or {}
            compacted["materials_suppliers_matrix"] = {
                str(material_id): suppliers
                for material_id, suppliers in matrix.items()
                if str(material_id) in explicit_material_ids
            }
            filtered_candidates = []
            for candidate in self_state.get("supplier_candidates") or []:
                if not isinstance(candidate, dict):
                    continue
                materials = candidate.get("materials") or {}
                candidate_material_ids = (
                    {str(item) for item in materials.keys()}
                    if isinstance(materials, dict)
                    else {str(item) for item in materials or []}
                )
                if candidate_material_ids & explicit_material_ids:
                    filtered_candidates.append(candidate)
            compacted["supplier_candidates"] = filtered_candidates

        open_buckets = {
            "available",
            "created",
            "confirmed",
            "open",
            "ordered",
            "pending",
            "in_progress",
            "in_transit",
        }
        compact_orders: Dict[str, Any] = {}
        order_history_summary: Dict[str, int] = {}
        open_records_summary: Dict[str, Dict[str, int]] = {}
        for bucket, records in (self_state.get("orders") or {}).items():
            if not isinstance(records, list):
                compact_orders[bucket] = records
                continue
            if str(bucket).lower() in open_buckets:
                compact_orders[bucket] = cls._bounded_records(
                    records,
                    max_open_records,
                )
                open_records_summary[bucket] = {
                    "total": len(records),
                    "included": len(compact_orders[bucket]),
                }
            else:
                order_history_summary[bucket] = len(records)
                if records:
                    compact_orders[f"recent_{bucket}"] = cls._bounded_records(
                        records,
                        recent_history_items,
                        keep_tail=True,
                    )
        compact_orders["history_summary"] = order_history_summary
        compact_orders["open_records_summary"] = open_records_summary
        compacted["orders"] = compact_orders

        replenishment = self_state.get("replenishment") or {}
        compact_replenishment: Dict[str, Any] = {}
        replenishment_history_summary: Dict[str, int] = {}
        for key, value in replenishment.items():
            if isinstance(value, list):
                replenishment_history_summary[key] = len(value)
                compact_replenishment[f"recent_{key}"] = cls._bounded_records(
                    value,
                    recent_history_items,
                    keep_tail=True,
                )
            else:
                compact_replenishment[key] = value
        compact_replenishment["history_summary"] = replenishment_history_summary
        compacted["replenishment"] = compact_replenishment
        return compacted

    @classmethod
    def _compact_long_run_sales_state(
        cls,
        self_state: Dict[str, Any],
        *,
        round_id: int,
        recent_history_items: int,
        max_open_records: int,
    ) -> Dict[str, Any]:
        compacted = {
            key: value
            for key, value in self_state.items()
            if key not in {"sales_orders", "demand_backlog", "proposals_list"}
        }
        open_buckets = {
            "available",
            "accepted",
            "backlog",
            "in_progress",
            "open",
            "pending",
        }
        compact_orders: Dict[str, Any] = {}
        order_history_summary: Dict[str, int] = {}
        open_records_summary: Dict[str, Dict[str, int]] = {}
        filtered_non_actionable: Dict[str, int] = {}
        for bucket, records in (self_state.get("sales_orders") or {}).items():
            if not isinstance(records, list):
                compact_orders[bucket] = records
                continue
            bucket_name = str(bucket).lower()
            if bucket_name in open_buckets:
                current_records = records
                if bucket_name == "available":
                    current_records = [
                        order
                        for order in records
                        if cls._order_is_currently_actionable(order, round_id)
                    ]
                    filtered_non_actionable[bucket] = len(records) - len(current_records)
                compact_orders[bucket] = cls._bounded_records(
                    current_records,
                    max_open_records,
                )
                open_records_summary[bucket] = {
                    "total": len(current_records),
                    "included": len(compact_orders[bucket]),
                }
            else:
                order_history_summary[bucket] = len(records)
                if records:
                    compact_orders[f"recent_{bucket}"] = cls._bounded_records(
                        records,
                        recent_history_items,
                        keep_tail=True,
                    )
        compact_orders["history_summary"] = order_history_summary
        compact_orders["open_records_summary"] = open_records_summary
        compact_orders["filtered_non_actionable_summary"] = filtered_non_actionable
        compacted["sales_orders"] = compact_orders

        demand_backlog = self_state.get("demand_backlog") or {}
        compact_demand: Dict[str, Any] = {}
        demand_history_summary: Dict[str, int] = {}
        for key, value in demand_backlog.items():
            if isinstance(value, list):
                demand_history_summary[key] = len(value)
                compact_demand[f"recent_{key}"] = cls._bounded_records(
                    value,
                    recent_history_items,
                    keep_tail=True,
                )
            else:
                compact_demand[key] = value
        compact_demand["history_summary"] = demand_history_summary
        compacted["demand_backlog"] = compact_demand

        proposals = self_state.get("proposals_list") or []
        compacted["proposals_list"] = cls._bounded_records(
            proposals,
            max_open_records,
        )
        compacted["proposals_summary"] = {
            "total": len(proposals) if isinstance(proposals, list) else 0,
            "included": len(compacted["proposals_list"]),
        }
        return compacted

    @staticmethod
    def _compact_long_run_simulation_context(context: Any) -> Dict[str, Any]:
        if not isinstance(context, dict):
            return {}
        fields = (
            "total_steps",
            "final_round",
            "market_demand_mode",
            "trade_mode",
            "is_scheduled_external_demand_mode",
            "is_beer_game_mode",
            "is_cobweb_mode",
            "is_shared_resource_mode",
            "is_herding_mode",
            "external_environment",
            "context_scope",
        )
        return {field: context.get(field) for field in fields if field in context}

    def _build_long_run_external_change_review(
        self,
        simulation_context: Dict[str, Any],
        round_id: int,
    ) -> Dict[str, Any]:
        """Build a compact, non-prescriptive E1 event comparison for the Analyst."""
        current = (
            (simulation_context or {}).get("external_environment") or {}
        )
        if not isinstance(current, dict) or not current.get("enabled"):
            return {"enabled": False, "current_turn": int(round_id)}

        previous: Dict[str, Any] = {}
        if int(round_id) > 0:
            previous_root = (
                self.workspace_dir
                / "public"
                / "exchange"
                / f"day{int(round_id) - 1}"
            )
            previous = MultiTenantUtils._read_json_if_exists(
                previous_root / "end_of_day" / "external_environment.json",
                {},
            ) or MultiTenantUtils._read_json_if_exists(
                previous_root / "external_environment.json",
                {},
            )

        active_factors = current.get("active_factors") or {}
        previous_factors = (
            previous.get("active_factors") or {}
            if isinstance(previous, dict)
            else {}
        )
        factor_comparison: Dict[str, Any] = {}
        for factor, value in active_factors.items():
            try:
                observed = float(value)
            except (TypeError, ValueError):
                continue
            try:
                prior = float(previous_factors.get(factor, 1.0))
            except (TypeError, ValueError):
                prior = 1.0
            factor_comparison[str(factor)] = {
                "baseline": 1.0,
                "previous_turn": prior,
                "current_turn": observed,
                "change_from_previous": observed - prior,
                "deviation_from_baseline": observed - 1.0,
                "changed_this_turn": not math.isclose(observed, prior),
                "active_deviation": not math.isclose(observed, 1.0),
            }

        latest_event = current.get("latest_event") or {}
        event_turn = latest_event.get("turn")
        try:
            turns_since_event = max(0, int(round_id) - int(event_turn))
        except (TypeError, ValueError):
            turns_since_event = None
        triggered = bool(current.get("event_triggered_this_turn"))
        active_deviations = [
            factor
            for factor, comparison in factor_comparison.items()
            if comparison.get("active_deviation")
        ]
        return {
            "schema_version": "long_horizon_external_change_review.v1",
            "enabled": True,
            "current_turn": int(round_id),
            "review_status": (
                "event_triggered_now"
                if triggered
                else "post_event_follow_up"
                if latest_event and turns_since_event is not None and turns_since_event <= 5
                else "active_change_monitoring"
                if active_deviations
                else "baseline_monitoring"
            ),
            "event_triggered_this_turn": triggered,
            "triggered_events": current.get("triggered_events") or [],
            "latest_event": latest_event,
            "recent_events": current.get("recent_events") or [],
            "turns_since_latest_event": turns_since_event,
            "factor_comparison": factor_comparison,
            "active_deviating_factors": active_deviations,
            "actual_values": current.get("actual_values") or {},
            "analysis_contract": {
                "use_local_operating_evidence": True,
                "distinguish_event_effect_from_backlog_or_demand_pressure": True,
                "compare_margin_cash_service_inventory_and_capacity": True,
                "do_not_treat_factor_change_as_an_action_command": True,
                "define_review_criteria_for_the_next_2_to_5_turns": True,
            },
        }

    @classmethod
    def _build_long_run_department_context_projection(
        cls,
        payload: Dict[str, Any],
        department: str,
        round_id: int,
        policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        recent_history_items = max(1, int(policy.get("recent_history_items") or 4))
        max_open_records = max(8, int(policy.get("max_open_records") or 48))
        self_state = payload.get("self_state") or {}
        if department == "procurement":
            self_state = cls._compact_long_run_procurement_state(
                self_state,
                recent_history_items=recent_history_items,
                max_open_records=max_open_records,
            )
        elif department == "sales":
            self_state = cls._compact_long_run_sales_state(
                self_state,
                round_id=round_id,
                recent_history_items=recent_history_items,
                max_open_records=max_open_records,
            )

        projection = {
            "agent_context_projection": {
                "schema_version": "long_run_agent_context.v1",
                "enabled": True,
                "authoritative_for_agent_decision": True,
                "full_state_retained_for_execution_and_visualization": True,
                "recent_history_items": recent_history_items,
                "max_open_records": max_open_records,
            },
            "department": payload.get("department", department),
            "round_id": payload.get("round_id", round_id),
            "agent_decision_brief": payload.get("agent_decision_brief") or {},
            "policy_context": payload.get("policy_context") or {},
            "self_state": self_state,
            "simulation_context": cls._compact_long_run_simulation_context(
                payload.get("simulation_context")
            ),
            "target": payload.get("target"),
            "target_reason": payload.get("target_reason"),
            "evaluation": payload.get("evaluation"),
        }
        return projection

    @classmethod
    def _build_long_run_blackboard_projection(
        cls,
        payload: Dict[str, Any],
        department: str,
    ) -> Dict[str, Any]:
        return {
            "agent_context_projection": {
                "schema_version": "long_run_blackboard_context.v1",
                "enabled": True,
                "policy_context_source": "department_state.policy_context",
                "department": department,
            },
            "round_id": payload.get("round_id"),
            "simulation_context": cls._compact_long_run_simulation_context(
                payload.get("simulation_context")
            ),
            "departments": payload.get("departments") or {},
        }

    def _materialize_long_run_agent_context(
        self,
        *,
        state_path: Path,
        blackboard_path: Path,
        department: str,
        round_id: int,
    ) -> tuple[Path, Path, bool]:
        policy = self._long_run_agent_context_compaction_policy()
        if not policy:
            return state_path, blackboard_path, False
        try:
            state_payload = json.loads(state_path.read_text(encoding="utf-8"))
            blackboard_payload = json.loads(blackboard_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return state_path, blackboard_path, False

        projection_dir = (
            self.workspace_dir
            / "projections"
            / "agent_context"
            / f"day{round_id}"
            / self.enterprise_spec.enterprise_name
            / department
        )
        projection_dir.mkdir(parents=True, exist_ok=True)
        projected_state_path = projection_dir / f"{department}.json"
        projected_blackboard_path = projection_dir / "blackboard.json"
        projected_state_path.write_text(
            json.dumps(
                self._build_long_run_department_context_projection(
                    state_payload,
                    department,
                    round_id,
                    policy,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        projected_blackboard_path.write_text(
            json.dumps(
                self._build_long_run_blackboard_projection(
                    blackboard_payload,
                    department,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return projected_state_path, projected_blackboard_path, True

    def _coordination_profile(self) -> Dict[str, Any]:
        contract = self._runtime_contract()
        return (
            ((contract["profiles"].get("capabilities") or {}).get("coordination"))
            or {}
        )

    def _communication_enabled(self) -> bool:
        if read_policy(self.workspace_dir).get("condition") == "no_shared_coordination":
            return False
        profile = self._coordination_profile()
        return bool(
            profile.get("blackboard_enabled")
            or str(profile.get("communication_validation") or "off") != "off"
        )

    def _history_projection_day(self, round_id: int) -> int:
        return max(0, int(round_id) - 1)

    def _history_projection_path(self, round_id: int) -> Path:
        return (
            self.workspace_dir
            / "projections"
            / "history"
            / f"day{self._history_projection_day(round_id)}"
            / self.enterprise_spec.enterprise_name
            / "history_projection.json"
        )

    def _history_projection_expected(self) -> bool:
        return self._analysis_profile().get("profile") == "historical_diagnosis"

    def _cobweb_profit_objective_contract(self) -> Dict[str, Any]:
        contract = self._runtime_contract()
        scenario_config = contract.get("scenario_config") or {}
        simulation = scenario_config.get("simulation") or {}
        cobweb_config = simulation.get("cobweb_config") or {}
        runtime_injection = scenario_config.get("runtime_injection") or {}
        profit_policy = runtime_injection.get("profit_objective_policy") or {}
        if not (
            (
                simulation.get("market_demand_mode") == "cobweb"
                or cobweb_config.get("enabled") is True
            )
            and cobweb_config.get("production_response_mode")
            == "agent_endogenous"
            and cobweb_config.get("decision_profile") == "profit_balanced"
            and profit_policy.get("enabled") is True
        ):
            return {}
        return {
            "cobweb_config": cobweb_config,
            "profit_policy": profit_policy,
            "long_run_policy": runtime_injection.get(
                "long_run_experiment_policy"
            )
            or {},
        }

    def cobweb_profit_objective_enabled(self) -> bool:
        return bool(self._cobweb_profit_objective_contract())

    @staticmethod
    def _select_mapping_fields(
        payload: Any,
        fields: List[str],
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        return {
            field: payload.get(field)
            for field in fields
            if field in payload
        }

    @staticmethod
    def _num(value: Any, default: float = 0.0) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _sum_order_quantity(orders: Any, *, round_id: int) -> float:
        total = 0.0
        if not isinstance(orders, list):
            return total
        for order in orders:
            if not isinstance(order, dict):
                continue
            quantity = SkillRunner._num(order.get("quantity"), 0.0)
            if quantity <= 0:
                continue
            deadline = order.get("delivery_deadline")
            try:
                if deadline not in (None, "") and int(round_id) > int(deadline):
                    continue
            except (TypeError, ValueError):
                pass
            total += quantity
        return total

    @staticmethod
    def _inventory_quantity(observation: Dict[str, Any], item_id: str) -> float:
        inventory = observation.get("inventory") or {}
        for item in inventory.get("inventory_items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("item_id") or "") == str(item_id):
                return SkillRunner._num(item.get("quantity"), 0.0)
        return 0.0

    @staticmethod
    def _line_status_count(production_lines: Dict[str, Any], statuses: set[str]) -> float:
        by_status = production_lines.get("by_status") or {}
        if isinstance(by_status, dict):
            total = sum(
                SkillRunner._num(by_status.get(status), 0.0)
                for status in statuses
            )
            if total > 0:
                return total
        count = 0.0
        for line in production_lines.get("details") or []:
            if not isinstance(line, dict):
                continue
            if str(line.get("status") or "").lower() in statuses:
                count += 1.0
        return count

    @staticmethod
    def _build_single_enterprise_capacity_expansion_signal(
        observation: Dict[str, Any],
        chart_export: Dict[str, Any],
        *,
        round_id: int,
    ) -> Dict[str, Any]:
        production = observation.get("production") or {}
        sales = observation.get("sales") or {}
        production_lines = production.get("production_lines") or {}
        if not isinstance(production_lines, dict):
            production_lines = {}

        charts = chart_export.get("charts") or {}
        capacity_chart = charts.get("capacity_utilization_analysis") or {}
        series = capacity_chart.get("series") or {}
        utilization_history = [
            SkillRunner._num(value, 0.0)
            for value in (series.get("capacity_utilization_percent") or [])[-3:]
        ]
        total_capacity_history = [
            SkillRunner._num(value, 0.0)
            for value in (series.get("total_capacity") or [])[-3:]
        ]
        available_capacity_history = [
            SkillRunner._num(value, 0.0)
            for value in (series.get("available_capacity") or [])[-3:]
        ]

        total_capacity = SkillRunner._num(
            production_lines.get("total_capacity"),
            SkillRunner._num(production.get("total_capacity"), 0.0),
        )
        available_capacity = SkillRunner._num(
            production_lines.get("available_capacity"),
            SkillRunner._num(production.get("available_capacity"), total_capacity),
        )
        occupied_capacity = SkillRunner._num(
            production_lines.get("occupied_capacity"),
            max(0.0, total_capacity - available_capacity),
        )
        current_utilization_percent = SkillRunner._num(
            (production.get("production_metrics") or {}).get("capacity_utilization"),
            0.0,
        )
        if current_utilization_percent <= 1.5:
            current_utilization_percent *= 100
        if total_capacity > 0 and current_utilization_percent <= 0:
            current_utilization_percent = min(
                100.0,
                max(0.0, occupied_capacity / max(total_capacity, 1.0) * 100),
            )
        if not utilization_history and current_utilization_percent > 0:
            utilization_history = [current_utilization_percent]
        if not total_capacity_history:
            total_capacity_history = [total_capacity]
        if not available_capacity_history:
            available_capacity_history = [available_capacity]

        recent_high_utilization_rounds = sum(
            1 for value in utilization_history if value >= 85.0
        )
        recent_zero_available_rounds = sum(
            1 for value in available_capacity_history if value <= 0.0
        )
        avg_recent_utilization = (
            sum(utilization_history) / len(utilization_history)
            if utilization_history
            else current_utilization_percent
        )
        low_total_capacity = total_capacity <= 500.0

        sales_orders = sales.get("sales_orders") or {}
        committed_order_quantity = 0.0
        valid_available_order_quantity = 0.0
        if isinstance(sales_orders, dict):
            for bucket in ("accepted", "in_progress", "backlog"):
                committed_order_quantity += SkillRunner._sum_order_quantity(
                    sales_orders.get(bucket) or [],
                    round_id=round_id,
                )
            valid_available_order_quantity = SkillRunner._sum_order_quantity(
                sales_orders.get("available") or [],
                round_id=round_id,
            )
        demand_backlog = sales.get("demand_backlog") or {}
        committed_order_quantity += SkillRunner._num(
            demand_backlog.get("total_backlog_quantity"),
            0.0,
        )
        sales_metrics = sales.get("sales_metrics") or {}
        committed_order_quantity += SkillRunner._num(
            sales_metrics.get("confirmed_order_backlog_quantity"),
            0.0,
        )
        lost_sales_quantity = SkillRunner._num(
            sales_metrics.get("lost_sales_quantity"),
            0.0,
        )
        total_downstream_demand = SkillRunner._num(
            sales_metrics.get("total_downstream_demand"),
            0.0,
        )
        product_inventory = SkillRunner._inventory_quantity(
            observation,
            "PRODUCT_1",
        )
        low_stock_with_demand_signal = (
            product_inventory < 60.0
            and (
                lost_sales_quantity > 0
                or total_downstream_demand > 0
                or committed_order_quantity > 0
                or valid_available_order_quantity > 0
            )
        )
        demand_or_stock_pressure = (
            committed_order_quantity > 0
            or valid_available_order_quantity > 0
            or low_stock_with_demand_signal
        )

        ready_lines = SkillRunner._line_status_count(
            production_lines,
            {"idle", "ready", "available"},
        )
        building_lines = SkillRunner._line_status_count(
            production_lines,
            {"under_construction", "building", "pending", "planned"},
        )
        total_lines = SkillRunner._num(
            production_lines.get("total"),
            float(len(production_lines.get("details") or [])),
        )
        cash_guard = production.get("cash_guard") or {}
        cash_hard_blocked = str(cash_guard.get("guard_level") or "").lower() in {
            "hard_blocked",
            "blocked",
            "critical",
        }
        history_pressure = (
            recent_high_utilization_rounds >= 2
            or recent_zero_available_rounds >= 2
            or avg_recent_utilization >= 80.0
        )
        no_current_capacity = available_capacity <= 0 or ready_lines <= 0
        should_prioritize = bool(
            low_total_capacity
            and demand_or_stock_pressure
            and building_lines <= 0
            and not cash_hard_blocked
            and (history_pressure or no_current_capacity or total_lines <= 0)
        )
        reason_parts = []
        if should_prioritize:
            reason_parts.append("low total capacity with sustained recent capacity pressure")
        if demand_or_stock_pressure:
            reason_parts.append("orders or fulfillable demand exist, or finished-goods inventory is low")
        if no_current_capacity:
            reason_parts.append("no available capacity or production line")
        if building_lines > 0:
            reason_parts.append("a production line is already under construction; avoid duplicate expansion")
        if cash_hard_blocked:
            reason_parts.append("cash is a hard constraint, so line expansion cannot be prioritized")
        return {
            "enabled": True,
            "should_prioritize_build_line_review": should_prioritize,
            "recommended_action_when_feasible": (
                "build_production_line" if should_prioritize else None
            ),
            "reason": "; ".join(reason_parts) or "no clear line-expansion priority signal",
            "thresholds": {
                "high_utilization_percent": 85,
                "avg_recent_utilization_percent": 80,
                "low_total_capacity_threshold": 500,
                "low_finished_goods_threshold": 60,
            },
            "evidence": {
                "round_id": round_id,
                "recent_utilization_percent": utilization_history,
                "recent_total_capacity": total_capacity_history,
                "recent_available_capacity": available_capacity_history,
                "recent_high_utilization_rounds": recent_high_utilization_rounds,
                "recent_zero_available_rounds": recent_zero_available_rounds,
                "avg_recent_utilization_percent": round(avg_recent_utilization, 2),
                "current_total_capacity": total_capacity,
                "current_available_capacity": available_capacity,
                "ready_line_count": ready_lines,
                "building_line_count": building_lines,
                "total_line_count": total_lines,
                "committed_order_quantity": committed_order_quantity,
                "valid_available_order_quantity": valid_available_order_quantity,
                "product_inventory": product_inventory,
                "lost_sales_quantity": lost_sales_quantity,
                "total_downstream_demand": total_downstream_demand,
                "cash_guard_level": cash_guard.get("guard_level"),
            },
        }

    @staticmethod
    def _compact_cobweb_order(
        order: Any,
        round_id: int,
    ) -> Dict[str, Any]:
        if not isinstance(order, dict):
            return {}
        compact = SkillRunner._select_mapping_fields(
            order,
            [
                "order_id",
                "product_id",
                "quantity",
                "unit_price",
                "total_amount",
                "source_type",
                "created_time",
                "delivery_deadline",
                "offer_expiry_day",
                "breach_penalty_enabled",
                "breach_penalty_per_unit",
                "status",
            ],
        )
        created_time = order.get("created_time")
        deadline = order.get("delivery_deadline")
        try:
            compact["age_rounds"] = max(0, int(round_id) - int(created_time))
        except (TypeError, ValueError):
            compact["age_rounds"] = None
        try:
            compact["deadline_status"] = (
                "expired" if int(round_id) > int(deadline) else "open"
            )
        except (TypeError, ValueError):
            compact["deadline_status"] = "unknown"
        return compact

    def _raw_analyst_observation_path(self, round_id: int) -> Path:
        return (
            self.workspace_dir
            / "enterprises"
            / self.enterprise_spec.enterprise_name
            / "observations"
            / f"observation_day{round_id}.txt"
        )

    def _compact_cobweb_analyst_observation_path(self, round_id: int) -> Path:
        return (
            self.workspace_dir
            / "projections"
            / "agent_context"
            / f"day{round_id}"
            / self.enterprise_spec.enterprise_name
            / "cobweb_c3_analyst_observation.json"
        )

    def _compact_long_run_analyst_observation_path(self, round_id: int) -> Path:
        return (
            self.workspace_dir
            / "projections"
            / "agent_context"
            / f"day{round_id}"
            / self.enterprise_spec.enterprise_name
            / "long_horizon_analyst_observation.json"
        )

    def _build_long_run_analyst_observation(
        self,
        observation: Dict[str, Any],
        history_projection: Dict[str, Any],
        round_id: int,
    ) -> Dict[str, Any]:
        policy = self._long_run_agent_context_compaction_policy()
        if not policy:
            return observation
        recent_limit = max(1, int(policy.get("recent_history_items") or 4))
        open_limit = max(8, int(policy.get("max_open_records") or 48))

        procurement_raw = observation.get("procurement") or {}
        procurement = self._compact_long_run_procurement_state(
            procurement_raw,
            recent_history_items=recent_limit,
            max_open_records=open_limit,
        )
        proposal_history = procurement_raw.get("proposal_history") or []
        procurement["proposals_list"] = self._bounded_records(
            procurement_raw.get("proposals_list") or [],
            open_limit,
        )
        procurement["proposal_history_summary"] = {
            "total": len(proposal_history) if isinstance(proposal_history, list) else 0,
        }
        procurement["recent_proposal_history"] = self._bounded_records(
            proposal_history,
            recent_limit,
            keep_tail=True,
        )
        procurement.pop("proposal_history", None)

        sales_raw = observation.get("sales") or {}
        sales = self._compact_long_run_sales_state(
            sales_raw,
            round_id=round_id,
            recent_history_items=recent_limit,
            max_open_records=open_limit,
        )
        sales_proposal_history = sales_raw.get("proposal_history") or []
        sales["proposal_history_summary"] = {
            "total": (
                len(sales_proposal_history)
                if isinstance(sales_proposal_history, list)
                else 0
            ),
        }
        sales["recent_proposal_history"] = self._bounded_records(
            sales_proposal_history,
            recent_limit,
            keep_tail=True,
        )
        sales.pop("proposal_history", None)

        production_raw = observation.get("production") or {}
        production = {
            key: value
            for key, value in production_raw.items()
            if key != "production_plans"
        }
        compact_plans: Dict[str, Any] = {}
        plan_history_summary: Dict[str, int] = {}
        open_plan_buckets = {"created", "pending", "in_progress", "active"}
        for bucket, records in (production_raw.get("production_plans") or {}).items():
            if not isinstance(records, list):
                compact_plans[bucket] = records
            elif str(bucket).lower() in open_plan_buckets:
                compact_plans[bucket] = self._bounded_records(records, open_limit)
            else:
                plan_history_summary[bucket] = len(records)
                if records:
                    compact_plans[f"recent_{bucket}"] = self._bounded_records(
                        records,
                        recent_limit,
                        keep_tail=True,
                    )
        compact_plans["history_summary"] = plan_history_summary
        production["production_plans"] = compact_plans

        hr_raw = observation.get("hr") or {}
        hr = dict(hr_raw)
        employees = hr_raw.get("employees") or []
        hr["employees"] = self._bounded_records(employees, open_limit)
        hr["employees_summary"] = {
            "total": len(employees) if isinstance(employees, list) else 0,
            "included": len(hr["employees"]),
        }

        history_days = history_projection.get("days") or []
        recent_history_days = []
        for day in history_days[-recent_limit:]:
            if isinstance(day, dict):
                recent_history_days.append({
                    "round_id": day.get("round_id", day.get("day")),
                    "department_metrics": day.get("department_metrics") or {},
                })

        enterprise_policy = observation.get("enterprise_policy_context") or {}
        department_policies = observation.get("policy_context_by_department") or {}
        representative_policy = (
            department_policies.get("finance")
            or department_policies.get("production")
            or next(iter(department_policies.values()), {})
        )
        return {
            "schema_version": "long_horizon_analyst_observation.v1",
            "enterprise_id": observation.get("enterprise_id"),
            "round_id": int(round_id),
            "input_contract": {
                "scope": "E1_long_horizon_agent_analysis",
                "raw_observation_archived": True,
                "raw_observation_must_not_be_read_by_agent": True,
                "full_state_retained_for_execution_and_visualization": True,
                "recent_history_items": recent_limit,
                "max_open_records_per_bucket": open_limit,
            },
            "finance": observation.get("finance") or {},
            "production": production,
            "procurement": procurement,
            "inventory": observation.get("inventory") or {},
            "sales": sales,
            "hr": hr,
            "simulation_context": self._compact_long_run_simulation_context(
                observation.get("simulation_context")
            ),
            "external_change_review": self._build_long_run_external_change_review(
                observation.get("simulation_context") or {},
                round_id,
            ),
            "policy_context": {
                "schema_version": enterprise_policy.get("schema_version"),
                "scenario_id": enterprise_policy.get("scenario_id"),
                "enabled_modes": enterprise_policy.get("enabled_modes") or [],
                "analyst_contract": enterprise_policy.get("analyst_contract"),
                "active_modes": representative_policy.get("active_modes") or {},
                "decision_weights": representative_policy.get("decision_weights") or {},
                "priority_rules": representative_policy.get("priority_rules") or [],
            },
            "recent_history": {
                "source_day_range": history_projection.get("source_day_range"),
                "no_future_data": history_projection.get("no_future_data"),
                "trends": history_projection.get("trends") or {},
                "latest_days": recent_history_days,
            },
        }

    def _prepare_long_run_analyst_observation(self, round_id: int) -> Path:
        raw_path = self._raw_analyst_observation_path(round_id)
        if not raw_path.exists():
            raise FileNotFoundError(f"E1 analyst observation missing: {raw_path}")
        observation = MultiTenantUtils._read_json_if_exists(raw_path, {})
        if not isinstance(observation, dict) or not observation:
            raise ValueError(f"E1 analyst observation is invalid: {raw_path}")
        history_projection = MultiTenantUtils._read_json_if_exists(
            self._history_projection_path(round_id),
            {},
        )
        compact_path = self._compact_long_run_analyst_observation_path(round_id)
        MultiTenantUtils._write_json(
            compact_path,
            self._build_long_run_analyst_observation(
                observation,
                history_projection,
                round_id,
            ),
        )
        return compact_path

    def _build_cobweb_profit_analyst_observation(
        self,
        observation: Dict[str, Any],
        history_projection: Dict[str, Any],
        round_id: int,
    ) -> Dict[str, Any]:
        objective_contract = self._cobweb_profit_objective_contract()
        if not objective_contract:
            return observation

        finance = observation.get("finance") or {}
        production = observation.get("production") or {}
        sales = observation.get("sales") or {}
        inventory = observation.get("inventory") or {}
        hr = observation.get("hr") or {}
        production_lines = production.get("production_lines") or {}
        sales_orders = sales.get("sales_orders") or {}
        demand_backlog = sales.get("demand_backlog") or {}
        production_plans = production.get("production_plans") or {}
        if not isinstance(production_lines, dict):
            production_lines = {}
        if not isinstance(production_plans, dict):
            production_plans = {}
        if not isinstance(sales_orders, dict):
            sales_orders = {}
        if not isinstance(demand_backlog, dict):
            demand_backlog = {}

        available_orders = [
            self._compact_cobweb_order(order, round_id)
            for order in (sales_orders.get("available") or [])
            if isinstance(order, dict)
        ]
        available_orders = [
            order for order in available_orders if order.get("order_id")
        ]

        safe_recovery_candidates = []
        for candidate in (
            (production.get("recovery_guard") or {}).get("candidates") or []
        ):
            compact = self._select_mapping_fields(
                candidate,
                [
                    "product_id",
                    "on_hand",
                    "demand_backlog_quantity",
                    "confirmed_order_backlog_quantity",
                    "stale_backlog_quantity",
                    "material_feasible_quantity",
                    "capacity_feasible_quantity",
                    "blocking_reasons",
                    "material_shortages",
                ],
            )
            if compact:
                safe_recovery_candidates.append(compact)

        safe_margin_candidates = []
        for candidate in (
            (production.get("margin_guard") or {}).get("candidates") or []
        ):
            compact = self._select_mapping_fields(
                candidate,
                [
                    "product_id",
                    "estimated_sale_unit_price",
                    "estimated_unit_cost",
                    "estimated_unit_margin",
                    "projected_revenue",
                    "projected_total_cost",
                    "projected_gross_profit",
                    "margin_ratio",
                    "cash_feasible",
                    "material_feasible",
                    "capacity_feasible",
                    "hard_blocked",
                    "blocking_reasons",
                ],
            )
            if compact:
                safe_margin_candidates.append(compact)

        history_days = history_projection.get("days") or []
        recent_history_days = []
        for day in history_days[-3:]:
            if not isinstance(day, dict):
                continue
            recent_history_days.append({
                "round_id": day.get("round_id", day.get("day")),
                "department_metrics": day.get("department_metrics") or {},
            })

        profit_policy = objective_contract["profit_policy"]
        return {
            "schema_version": "cobweb_c3_analyst_observation.v1",
            "enterprise_name": self.enterprise_spec.enterprise_name,
            "enterprise_id": observation.get("enterprise_id")
            or getattr(
                self.enterprise_spec,
                "enterprise_id",
                self.enterprise_spec.enterprise_name,
            ),
            "round_id": round_id,
            "input_contract": {
                "scope": "cobweb_c3_profit_objective_only",
                "raw_observation_archived": True,
                "raw_observation_must_not_be_read_by_agent": True,
                "prescriptive_quantity_fields_removed": True,
                "completed_order_histories_summarized": True,
                "analysis_must_use_current_round": True,
            },
            "objective_policy": {
                "planning_horizon_rounds": profit_policy.get(
                    "planning_horizon_rounds",
                    8,
                ),
                "objective_weights": profit_policy.get("objective_weights") or {},
                "decision_contract": profit_policy.get("decision_contract") or {},
            },
            "finance_state": self._select_mapping_fields(
                finance,
                [
                    "cash",
                    "total_revenue",
                    "total_cost",
                    "financial_indicators",
                    "cash_summary",
                ],
            ),
            "market_state": self._select_mapping_fields(
                production.get("cobweb_decision_signal")
                or observation.get("cobweb_decision_signal")
                or {},
                [
                    "round_id",
                    "product_id",
                    "current_market_price",
                    "lagged_price",
                    "planned_supply_quantity",
                    "market_supply_quantity",
                    "actual_supply_quantity",
                    "external_order_quantity",
                    "equilibrium_price",
                    "equilibrium_quantity",
                    "price_deviation_from_equilibrium",
                    "quantity_deviation_from_equilibrium",
                    "suggested_supply_direction",
                    "market_supply_source",
                    "stability_label",
                ],
            ),
            "production_state": {
                "products": production.get("products_idList") or [],
                "lines": {
                    **self._select_mapping_fields(
                        production_lines,
                        [
                            "total",
                            "by_status",
                            "total_capacity",
                            "available_capacity",
                            "occupied_capacity",
                        ],
                    ),
                    "details": [
                        self._select_mapping_fields(
                            item,
                            [
                                "line_id",
                                "line_type",
                                "capacity",
                                "remaining_capacity",
                                "total_produced",
                            ],
                        )
                        for item in (production_lines.get("details") or [])[:8]
                        if isinstance(item, dict)
                    ],
                },
                "metrics": production.get("production_metrics") or {},
                "plan_status_counts": {
                    key: len(value) if isinstance(value, list) else 0
                    for key, value in production_plans.items()
                },
                "cash_guard": self._select_mapping_fields(
                    production.get("cash_guard") or {},
                    [
                        "guard_level",
                        "available_conversion_budget",
                        "current_cash",
                        "warning_threshold",
                        "hard_blocked",
                    ],
                ),
                "recovery_feasibility": safe_recovery_candidates,
                "unit_economics": safe_margin_candidates,
            },
            "sales_state": {
                "order_status_counts": {
                    key: len(value) if isinstance(value, list) else 0
                    for key, value in sales_orders.items()
                },
                "available_orders": available_orders,
                "metrics": sales.get("sales_metrics") or {},
                "backlog_by_product": demand_backlog.get("by_product") or {},
            },
            "inventory_state": {
                "items": [
                    self._select_mapping_fields(
                        item,
                        [
                            "item_id",
                            "item_type",
                            "quantity",
                            "unit_price",
                            "total_value",
                            "safety_stock",
                            "reorder_point",
                            "is_low_stock",
                            "is_below_reorder_point",
                        ],
                    )
                    for item in (inventory.get("inventory_items") or [])
                    if isinstance(item, dict)
                ],
                "metrics": inventory.get("inventory_metrics") or {},
                "warehouse_capacity": inventory.get("warehouse_capacity"),
                "used_capacity": inventory.get("used_capacity"),
                "warehouse_utilization": inventory.get(
                    "warehouse_utilization"
                ),
            },
            "staffing_state": {
                "department_staffing": hr.get("department_staffing") or {},
                "total_payroll": hr.get("total_payroll"),
                "recruitment_status": hr.get("recruitment_status"),
            },
            "recent_history": {
                "source_day_range": history_projection.get("source_day_range"),
                "no_future_data": history_projection.get("no_future_data"),
                "trends": history_projection.get("trends") or {},
                "latest_days": recent_history_days,
            },
        }

    def _prepare_cobweb_profit_analyst_observation(
        self,
        round_id: int,
    ) -> Path:
        raw_path = self._raw_analyst_observation_path(round_id)
        if not raw_path.exists():
            raise FileNotFoundError(
                f"Cobweb C3 analyst observation missing: {raw_path}"
            )
        observation = MultiTenantUtils._read_json_if_exists(raw_path, {})
        if not isinstance(observation, dict) or not observation:
            raise ValueError(
                f"Cobweb C3 analyst observation is invalid: {raw_path}"
            )
        history_path = self._history_projection_path(round_id)
        history_projection = MultiTenantUtils._read_json_if_exists(
            history_path,
            {},
        )
        compact_payload = self._build_cobweb_profit_analyst_observation(
            observation,
            history_projection,
            round_id,
        )
        compact_path = self._compact_cobweb_analyst_observation_path(round_id)
        MultiTenantUtils._write_json(compact_path, compact_payload)
        return compact_path

    def _build_cobweb_profit_objective_analyst_prompt(self) -> str:
        objective_contract = self._cobweb_profit_objective_contract()
        if not objective_contract:
            return ""
        profit_policy = objective_contract["profit_policy"]
        objective_weights = profit_policy.get("objective_weights") or {}
        return f"""

Cobweb open-profit-objective analysis constraints:
- This is not a mechanism-reproduction task. Treat price direction as market-risk evidence, not as a command to expand at high prices or stop at low prices.
- Use price, supply, profit, fulfillment, inventory, and production-adjustment trends from the latest closed history to identify issues for the next two to three turns; the overall planning horizon is {profit_policy.get("planning_horizon_rounds", 8)} turns.
- Objective weights are {json.dumps(objective_weights, ensure_ascii=False)}. Phase objectives must jointly address profit or marginal return, cash safety, service level, and production-adjustment or price-boundary risk.
- Do not prescribe one exact production quantity. Use interpretable ranges, thresholds, or comparison rules so production can decide using the current cobweb_operating_economics.
- Do not treat theoretical equilibrium supply, recovery_guard quantity, or current backlog as the production answer, and do not use reproduction of a divergent path as the success criterion.
- The production objective must cover at least three of four dimensions: profit/cost, cash, fulfillment/backlog, and stability/price risk, with explicit trade-offs.
""".rstrip()

    def _single_enterprise_chart_export_policy(self) -> Dict[str, Any]:
        run_meta = self._read_json_file(self.workspace_dir / "run_meta.json", {})
        scenario_config = run_meta.get("scenario_config") or {}
        runtime_injection = scenario_config.get("runtime_injection") or {}
        return runtime_injection.get("single_enterprise_chart_export_policy") or {}

    def _chart_export_path(self) -> Path:
        return (
            self.workspace_dir
            / "enterprises"
            / self.enterprise_spec.enterprise_name
            / "charts_data_export.json"
        )

    def _single_enterprise_analyst_input_dir(self, round_id: int) -> Path:
        return (
            self.workspace_dir
            / "enterprises"
            / self.enterprise_spec.enterprise_name
            / "analyst_inputs"
            / f"day{round_id}"
        )

    def _prepare_single_enterprise_analyst_observation(self, round_id: int) -> Path:
        source_path = self._raw_analyst_observation_path(round_id)
        target_dir = self._single_enterprise_analyst_input_dir(round_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "observation_compact.json"
        source = MultiTenantUtils._read_json_if_exists(source_path, {})
        if not isinstance(source, dict):
            MultiTenantUtils._write_json(target_path, {})
            return target_path

        compact: Dict[str, Any] = {
            "schema_version": "single_enterprise_analyst_observation_compact.v1",
            "source_observation": str(source_path),
            "enterprise_id": source.get("enterprise_id"),
            "finance": source.get("finance") or {},
            "production": source.get("production") or {},
            "procurement": source.get("procurement") or {},
            "inventory": source.get("inventory") or {},
            "sales": source.get("sales") or {},
            "hr": source.get("hr") or {},
            "simulation_context": source.get("simulation_context") or {},
            "policy_context_summary": {},
        }
        chart_export = MultiTenantUtils._read_json_if_exists(self._chart_export_path(), {})
        if not isinstance(chart_export, dict):
            chart_export = {}
        compact["capacity_expansion_signal"] = (
            self._build_single_enterprise_capacity_expansion_signal(
                source,
                chart_export,
                round_id=round_id,
            )
        )
        if isinstance(compact.get("production"), dict):
            compact["production"]["capacity_expansion_signal"] = compact[
                "capacity_expansion_signal"
            ]
        enterprise_policy = source.get("enterprise_policy_context") or {}
        if isinstance(enterprise_policy, dict):
            compact["policy_context_summary"] = {
                "active_modes": enterprise_policy.get("active_modes") or {},
                "single_enterprise_diagnostic_policy": (
                    enterprise_policy.get("single_enterprise_diagnostic_policy") or {}
                ),
                "priority_rules": enterprise_policy.get("priority_rules") or [],
            }
        if read_policy(self.workspace_dir, round_id):
            policy = read_policy(self.workspace_dir, round_id)
            autonomous = (policy.get("agent_prompt_policy_version") in {FD4_AGENT_PROMPT_VERSION, FD_ABLATION_VERSION}
                          or fd2_agent_policy(policy))
            compact = compact_fd_input(compact, preserve_active_records=autonomous)
            if autonomous:
                compact = {"fd_forward_evidence": describe_forward_evidence(compact, round_id), **compact}
            compact = project_fd4_decision_evidence(
                compact, self.workspace_dir, round_id,
            )
            compact = project_fd2_decision_evidence(compact, self.workspace_dir, round_id)
            compact["fd_input_projection"] = {
                "schema_version": "fd_bounded_input.v1", "recent_history_items": 4,
                "all_open_records_retained": True,
            }
        MultiTenantUtils._write_json(target_path, compact)
        return target_path

    def _prepare_single_enterprise_analyst_chart_export(self, round_id: int) -> Path:
        source_path = self._chart_export_path()
        target_dir = self._single_enterprise_analyst_input_dir(round_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / "charts_data_export_compact.json"
        source = MultiTenantUtils._read_json_if_exists(source_path, {})
        if not isinstance(source, dict):
            MultiTenantUtils._write_json(target_path, {})
            return target_path

        charts = source.get("charts") or {}
        compact_charts = {}
        if isinstance(charts, dict):
            excluded = {
                "single_case_prewarm_execution",
                "department_action_statistics",
            }
            compact_charts = {
                key: value
                for key, value in charts.items()
                if key not in excluded
            }
        compact = {
            "schema_version": "single_enterprise_analyst_chart_compact.v1",
            "source_chart_export": str(source_path),
            "meta": source.get("meta") or {},
            "charts": compact_charts,
            "excluded_charts": [
                "single_case_prewarm_execution",
                "department_action_statistics",
            ],
        }
        MultiTenantUtils._write_json(target_path, compact)
        return target_path

    def _chart_export_injected_to_analyst(self) -> bool:
        policy = self._single_enterprise_chart_export_policy()
        return bool(policy.get("enabled") and policy.get("inject_to_analyst", True))

    def _single_enterprise_case_policy_for_prompt(self) -> Dict[str, Any]:
        run_meta = self._read_json_file(self.workspace_dir / "run_meta.json", {})
        mode_markers = " ".join(
            str(run_meta.get(key) or "")
            for key in (
                "orchestrator",
                "decision_regime",
                "agent_objective_profile",
                "experiment_group",
            )
        ).lower()
        if "scripted" in mode_markers:
            return {}
        scenario_config = run_meta.get("scenario_config") or {}
        runtime_injection = scenario_config.get("runtime_injection") or {}
        policy = (
            run_meta.get("single_enterprise_case")
            or scenario_config.get("single_enterprise_case")
            or runtime_injection.get("single_enterprise_case_policy")
            or {}
        )
        if not isinstance(policy, dict) or not policy.get("enabled"):
            return {}
        if (
            "single_enterprise" not in mode_markers
            and policy.get("family") != "single_enterprise_diagnostic"
        ):
            return {}
        return policy

    def _single_enterprise_agent_run_active(self) -> bool:
        return bool(self._single_enterprise_case_policy_for_prompt())

    @staticmethod
    def _agent_transport_error_summary(messages: List[dict]) -> str:
        markers = (
            "API Error:",
            "ProcessError",
            "UnexpectedQueryError",
            "Command failed with exit code",
            "Connection refused",
            "ECONNREFUSED",
        )
        snippets: List[str] = []
        for message in messages or []:
            content = str((message or {}).get("content") or "")
            if not content or not any(marker in content for marker in markers):
                continue
            compact = " ".join(content.split())
            if compact and compact not in snippets:
                snippets.append(compact[:500])
            if len(snippets) >= 3:
                break
        return " | ".join(snippets)

    def _agent_runtime_env_summary(self) -> Dict[str, str]:
        keys = (
            "ANTHROPIC_BASE_URL",
            "AGENT_GATEWAY_BASE_URL",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_MODEL_LIST",
            "NO_PROXY",
            "no_proxy",
        )
        summary: Dict[str, str] = {}
        for key in keys:
            value = os.environ.get(key)
            if value:
                summary[key] = value
        return summary

    def _claude_stderr_tail(self, role: str, max_lines: int = 8) -> List[str]:
        log_dir = self.client_dir.parent / "logs"
        if not log_dir.exists():
            return []
        paths = sorted(
            log_dir.glob("claude_stderr_*.log"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
        snippets: List[str] = []
        role_marker = f"role={role}"
        for path in paths[:2]:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for line in reversed(lines):
                if role_marker not in line:
                    continue
                compact = " ".join(line.split())
                if compact and compact not in snippets:
                    snippets.append(compact[-700:])
                if len(snippets) >= max_lines:
                    return list(reversed(snippets))
        return list(reversed(snippets))

    def _single_enterprise_agent_transport_failure(
        self,
        *,
        role: str,
        round_id: int,
        messages: List[dict],
    ) -> Optional[Dict[str, Any]]:
        if not self._single_enterprise_agent_run_active():
            return None
        summary = self._agent_transport_error_summary(messages)
        if not summary:
            return None
        stderr_tail = self._claude_stderr_tail(role)
        stderr_hint = ""
        if stderr_tail:
            stderr_hint = " Stderr tail: " + " | ".join(stderr_tail[-3:])
        return {
            "status": "error",
            "message": (
                "Single-enterprise Agent Skill invocation failed before producing "
                f"a valid decision: role={role}, round={round_id}. "
                "Check ANTHROPIC_BASE_URL, ANTHROPIC_MODEL, ANTHROPIC_MODEL_LIST "
                f"and auth token. Raw error: {summary}{stderr_hint}"
            ),
            "error_type": "agent_transport_error",
            "role": role,
            "round_id": round_id,
            "runtime_agent_env": self._agent_runtime_env_summary(),
            "claude_stderr_tail": stderr_tail,
        }

    def _build_single_enterprise_analyst_prompt(self) -> str:
        policy = self._single_enterprise_case_policy_for_prompt()
        if not policy:
            return ""

        extra = procurement_guidance() if read_policy(self.workspace_dir).get("scenario") == "FD2" else ""
        if read_policy(self.workspace_dir):
            extra += """
Output contract: department_targets must contain sales, procurement, production and hr objects, each with target, evaluation and reason. Include HR based on real staffing even when no hiring is needed. Set round_id to the current integer round and enterprise_name to this enterprise.
"""
            extra += (
                """
After reading, proceed immediately. Read observation, charts and history once each. The bounded observation retains all open records and only counts plus four recent historical records. Do not read raw department archives or raw observations: they are outside the analyst permission scope. Reuse complete successful reads; only request an unread range with explicit offset/limit if crucial content was truncated. Read requires file_path. Do not loop on missing, denied or duplicate reads. Complete the four department targets, Write to the exact absolute output path, then Read it back. State insufficient evidence in reason and give conditional rather than invented targets.
"""
            )
        return extra + f'\n\nSingle-enterprise diagnostic mode:\nUse runtime business evidence only and the same diagnostic framework for all cases. These rules do not apply to multi-enterprise runs. Do not cite internal experiment metadata, preset answers, department priorities or expected conclusions in enterprise_summarys, department_targets or reason. Identify real workload, net demand, resource constraints and feasible actions from observation, charts, history and authorized department facts.\n'.rstrip()

    def _build_single_enterprise_department_decision_prompt(self, role: str) -> str:
        policy = self._single_enterprise_case_policy_for_prompt()
        if not policy:
            return ""

        role_key = str(role or "").strip().lower()
        if role_key not in {"procurement", "production", "sales", "hr"}:
            return ""

        lines = [
            "",
            'Single-enterprise S0 phased department decision guidance:',
            '- This guidance applies only to the current single-enterprise Agent diagnostic run; do not transfer it to multi-enterprise decisions.',
            '- Do not cite experimental metadata in action_reason unless it is supported by department state, blackboard, or analysis evidence, and do not claim knowledge of the current experiment type.',
            "- Decide whether to act only from this department's state, blackboard and analysis evidence, action templates, and feasibility constraints.",
        ]

        if role_key == "procurement":
            lines.extend([
                '- If the inventory position, confirmed inbound replenishment, and supplier coverage for MATERIAL_1 or another critical input cannot support accepted orders, backlog, or near-term production, create the necessary purchases. Multiple non-conflicting procurement actions may be returned in one JSON document.',
                '- If self_state.supplier_candidates or materials_suppliers_matrix contains unregistered candidates, compare unit price, minimum order quantity, processing_time, reliability_score, and logistics transit_time. supplier_name must be an actual candidate name. The first valid purchase establishes the relationship; do not invent a separate registration action.',
                '- Add supplier processing time and logistics transit time to estimate arrival. First retain combinations that can meet the earliest unmet production or fulfillment deadline, then compare landed cost and reliability among feasible combinations.',
                '- Select logistics by actual urgency: road has base cost 100, unit cost 2, and 3-day transit; rail has base cost 200, unit cost 1.5, and 2-day transit; air has base cost 500, unit cost 5, and 1-day transit. Prefer the fastest feasible combination when raw material is zero, inventory position is negative, or the first recovery purchase affects near-term production or fulfillment; use rail for a moderate gap and road only for routine replenishment.',
                '- Do not treat the historical total of received purchase orders as current coverage. Only open orders in replenishment.pending_by_material, operational_summary.incoming, or orders with pending/ordered/in_transit/confirmed/created status prevent duplicate purchasing.',
                '- Do not reorder when open inbound supply covers the current gap. Reorder when production has reduced inventory position to zero or below and open inbound supply is insufficient, even if earlier purchases succeeded.',
                '- When agent_decision_brief.procurement_recovery_signal exists, prioritize its recovery_stage, recommended_urgency, incoming_or_open_order_quantity, supplier_option_ranking, and suggested_quantity_band when deciding whether and how much to purchase and which supplier and logistics mode to use.',
                '- Expected arrival must matter within the visible order, backlog, or near-term production window. If slow transport arrives too late, choose a faster feasible option or use action_pass when the purchase would have no operational effect.',
                '- Purchase quantity must be justified by inventory position, bill-of-material requirements, orders or backlog, or production-recovery needs.',
            ])
        elif role_key == "production":
            lines.extend([
                '- If agent_decision_brief.capacity_expansion_signal.should_prioritize_build_line_review=true, first check the build_production_line template and return one executable expansion action. Use action_pass only when a line is already under construction, cash or staffing imposes a hard constraint, or the signal conflicts with live state evidence.',
                '- Base expansion review on capacity_expansion_signal.evidence.recent_utilization_percent and recent_available_capacity over recent rounds, together with current total_capacity and available_capacity. Do not attempt only create_production_plan when utilization remains high, available capacity is zero, or total capacity is very low.',
                '- When real orders or backlog exist and materials, production staffing, and idle capacity are feasible, prioritize a bounded PRODUCT_1 production plan.',
                '- When no line is idle or available, finished-goods inventory is below its low-stock threshold, cannot cover real orders or backlog, or is clearly below the safety buffer, and cash and staffing impose no hard constraint, prioritize build_production_line review.',
                '- Do not expand again when a line is already under construction, capacity is available, or the binding constraint is material, staffing, or cash. Expansion parameters must use only fields allowed by the template.',
                '- If materials, staffing, or capacity has a hard shortfall, use action_pass and identify the blocking values in reason.',
            ])
        elif role_key == "sales":
            lines.extend([
                '- Accept only real, nonzero, unexpired orders that finished-goods inventory or explicit near-term production can cover. Use reject_order or action_pass for infeasible orders.',
                '- When multiple real available_orders exist, compare unit_price, quantity, offer_expiry_day, delivery_deadline, breach cost, and resource use. Select a portfolio by expected contribution and fulfillment feasibility instead of accepting every order mechanically.',
                '- When agent_decision_brief.order_quality_reviews exists, use estimated_unit_margin, estimated_gross_margin, breach_penalty_exposure, days_to_deadline, days_to_expiry, quantity_to_uncommitted_inventory_ratio, and risk_flags to screen the order portfolio.',
                '- When order_quality_reviews conflicts with the generic rule to prioritize available orders, follow the portfolio-quality evidence in order_quality_reviews.',
                '- Prefer reject_order or defer acceptance for an available order with low margin, high breach exposure, a short deadline, and heavy use of uncommitted inventory or near-term capacity. A high unit price does not make an order attractive when it would cause a breach.',
                '- When returning multiple accept_order actions, order them from highest to lowest portfolio quality: high unit margin, low risk, and low resource use first; lower-quality or resource-intensive orders later. Do not let a large low-quality order consume inventory and capacity budgets first.',
                '- One JSON document may contain multiple accept_order or reject_order actions. There is no one-order-per-round rule, but the combined commitment must remain feasible.',
                '- If market_growth_signal.recommendation_mode=evidence_only, there is no precomputed market-development answer. Independently compare active and developing markets, recent order inflow, uncommitted inventory, confirmed backlog, development cost, and duration. Refer to should_develop_market only in other modes. Treat market coverage as weak evidence.',
                '- If fulfillable available_orders remain in the same round, accept all genuinely feasible orders after develop_market where appropriate. Do not ignore market development merely because existing orders persist.',
                '- When accepting multiple orders, decrement committed inventory and deadline-feasible capacity after each order. Do not allocate the same inventory or capacity window more than once. High-revenue orders that cause a breach are not beneficial.',
            ])
        elif role_key == "hr":
            lines.extend([
                '- If production, sales, procurement, or inventory has zero available staff, high utilization, or a pending failure caused by insufficient staffing, and pending recruitment does not cover the gap, return handle_recruitment. Multiple recruitment actions may be returned when several departments have uncovered hard gaps.',
                '- The recruitment department must be the department with the actual staffing constraint: use PROCUREMENT when procurement execution is blocked, PRODUCTION when production is blocked, and likewise for SALES or INVENTORY.',
                "- Recruitment normally reaches onboarding during the next business day's fixed settlement. Do not recruit again when pending recruitment already covers the gap.",
                '- Use action_pass when headcount, available staff, pending recruitment, and failure records show no hard staffing constraint.',
            ])

        if read_policy(self.workspace_dir).get("scenario") == "FD2":
            lines.append(procurement_guidance())
        fd_policy = read_policy(self.workspace_dir)
        if fd_policy:
            lines.extend([
                '- Read the supplied analysis.json interface this round. It supplies department targets; actual action feasibility comes from current department state. Do not read other departments, complete enterprise observations or run metadata.',
                '- Use the exact shared-input path supplied in the launch prompt. If coordination_available=false, use only authorized department facts, the Analyst interface and own history/feedback; do not seek another shared file.',
            ])
        return "\n".join(lines).rstrip()

    def _build_communication_prompt(
        self,
        spec: SkillRunSpec,
        round_id: int,
    ) -> str:
        if not self._communication_enabled():
            return ""
        validation_mode = str(
            self._coordination_profile().get("communication_validation")
            or "off"
        )
        requirement = (
            "must be written even when messages is empty"
            if validation_mode == "strict"
            else "write it when information must be sent to another department"
        )
        semantic_rule_pack = str(
            self._coordination_profile().get("semantic_rule_pack") or "none"
        )
        semantic_prompt = ""
        if semantic_rule_pack != "none":
            routes = describe_source_routes(
                semantic_rule_pack,
                spec.role.lower(),
            )
            semantic_prompt = f"""
- Active semantic rule pack: {semantic_rule_pack}.
- Select topic from the values allowed for the destination: {routes}
- Send only facts, constraints, recommendations, and coordination requests. Do not place a destination department's action_name directly in a message.
""".rstrip()
        return f"""

Internal enterprise communication sidecar:
- Path: {spec.communication_file_path}
- {requirement}.
- Format: {{"schema_version":"enterprise_communication.v1","messages":[{{"to_department":"sales","message":"...","topic":"general","priority":"medium","evidence_refs":[],"round_id":{round_id}}}]}}
- to_department must be an enabled department in this enterprise. Do not use an enterprise ID or disguise an internal recommendation as an external order or transaction event.
{semantic_prompt}
""".rstrip()

    def _read_json_file(self, path: Path, default=None) -> Any:
        return MultiTenantUtils._read_json_if_exists(path, default)

    def _build_context_audit(
        self,
        spec: SkillRunSpec,
        department: str,
        phase: str,
        round_id: int,
    ) -> Dict[str, Any]:
        audit = audit_context_files(
            department=department,
            state_path=spec.state_path,
            blackboard_path=spec.blackboard_path,
            trade_decision_card_path=spec.trade_decision_card_path,
            history_projection_path=(
                str(self._history_projection_path(round_id))
                if self._history_projection_expected()
                else None
            ),
            read_json=self._read_json_file,
        )
        payload = audit.to_dict()
        prefix = "trade_" if phase == "trade" else ""
        target = (
            Path(spec.output_file_path).parent
            / f"{prefix}skill_context_audit.json"
        )
        MultiTenantUtils._write_json(target, payload)
        payload["artifact"] = str(target)
        return payload

    def _write_skill_execution_audit(
        self,
        *,
        role: str,
        department: str,
        round_id: int,
        output_file_path: str,
        all_messages: List[dict],
        attempts: int,
        success: bool,
        fallback_used: bool,
        elapsed_seconds: float,
        context_audit: Dict[str, Any],
        execution_result: Optional[Dict[str, Any]],
        phase: str,
        analyst: bool = False,
    ) -> str:
        contract = self._runtime_contract()
        profile = self._skill_profile()
        validation_mode = str(profile.get("output_validation") or "off")
        context_validation_mode = str(
            profile.get("context_validation") or "off"
        )
        audit_dir = Path(output_file_path).parent
        if analyst:
            audit_dir = (
                audit_dir
                / "department"
                / "analyst"
                / f"day{round_id}"
            )
            audit_dir.mkdir(parents=True, exist_ok=True)
        if analyst:
            raw_messages_name = "analyst_raw_messages.json"
        elif phase == "trade":
            raw_messages_name = "trade_skill_raw_messages.json"
        else:
            raw_messages_name = "skill_raw_messages.json"
        raw_messages_path = audit_dir / raw_messages_name
        MultiTenantUtils._write_json(raw_messages_path, all_messages)

        output_payload = self._read_json_file(Path(output_file_path), {})
        if analyst:
            envelope = adapt_legacy_analysis(
                run_id=contract["run_id"],
                enterprise_id=self.enterprise_spec.enterprise_id,
                day=round_id,
                analysis=output_payload,
                output_file=output_file_path,
            )
        else:
            communications = []
            if phase == "decision":
                communication_payload = self._read_json_file(
                    Path(output_file_path).with_name(
                        f"{department}_communication.json"
                    ),
                    {},
                )
                if isinstance(communication_payload, dict):
                    communications = communication_payload.get("messages") or []
                elif isinstance(communication_payload, list):
                    communications = communication_payload
            envelope = adapt_legacy_actions(
                run_id=contract["run_id"],
                enterprise_id=self.enterprise_spec.enterprise_id,
                department=department,
                day=round_id,
                actions=output_payload,
                output_file=output_file_path,
                fallback_used=fallback_used,
                communications=communications,
            )

        validation_errors = envelope.validation_errors()
        if not Path(output_file_path).exists():
            validation_errors.append("output file is missing")
        if analyst:
            analysis_validation_error = getattr(
                self,
                "_last_analysis_validation_error",
                "",
            )
            if analysis_validation_error:
                validation_errors.append(
                    "analysis validation failed before cleanup: "
                    + analysis_validation_error
                )
        validation_warnings = []
        if validation_mode == "warn":
            validation_warnings.extend(validation_errors)
        if context_audit.get("disallowed_policy_names"):
            validation_warnings.append(
                "Context contained disallowed scenario policies: "
                + ", ".join(context_audit["disallowed_policy_names"])
            )
        if context_audit.get("missing_files"):
            validation_warnings.append(
                "Context files were missing: "
                + ", ".join(context_audit["missing_files"])
            )

        normalized_envelope = None
        if not validation_errors:
            normalized_envelope = envelope.to_dict()

        audit = SkillExecutionAudit(
            schema_version="skill_audit.v1",
            run_id=contract["run_id"],
            scenario_id=contract["scenario_id"],
            enterprise_id=self.enterprise_spec.enterprise_id,
            department=department,
            phase=phase,
            day=round_id,
            context_validation_mode=context_validation_mode,
            validation_mode=validation_mode,
            attempts=attempts,
            success=success,
            fallback_used=fallback_used,
            elapsed_seconds=round(float(elapsed_seconds), 6),
            output_file=output_file_path,
            raw_messages_artifacts=[str(raw_messages_path)],
            validation_errors=validation_errors,
            validation_warnings=validation_warnings,
            context_audit=context_audit,
            normalized_envelope=normalized_envelope,
            execution_result=execution_result,
        )
        if analyst:
            target_name = "analyst_execution_audit.json"
        elif phase == "trade":
            target_name = "trade_skill_execution_audit.json"
        else:
            target_name = "skill_execution_audit.json"
        target = audit_dir / target_name
        audit_payload = audit.to_dict()
        if read_policy(self.workspace_dir, round_id):
            audit_payload["input_access_version"] = FD_INPUT_ACCESS_VERSION
            audit_payload["output_protocol_evidence"] = output_access_evidence(
                self.workspace_dir, self.enterprise_spec.enterprise_name, role,
                round_id, output_file_path,
            )
            audit_payload["controller_output_recovery"] = getattr(
                self, "_output_recovery_sources", {},
            ).get(str(Path(output_file_path).resolve()))
        if (fd4_decision_guidance(self.workspace_dir, round_id, role)
                or fd2_decision_guidance(self.workspace_dir, round_id, role)):
            audit_payload["agent_prompt_policy_version"] = read_policy(
                self.workspace_dir, round_id,
            )["agent_prompt_policy_version"]
        if read_policy(self.workspace_dir, round_id).get("agent_prompt_policy_version") == FD4_AGENT_PROMPT_VERSION:
            warnings = (getattr(self, "_analysis_decision_warnings", []) if analyst else
                        getattr(self, "_department_warning_records", {}).get(str(Path(output_file_path).resolve()), []))
            audit_payload["decision_consistency_mode"] = "audit_only"
            audit_payload["decision_consistency_warnings"] = warnings
            audit_payload["validation_warnings"].extend(warnings)
        policy = read_policy(self.workspace_dir, round_id)
        if fd2_agent_policy(policy):
            audit_payload["decision_consistency_mode"] = "off"
            audit_payload["analyst_role"] = "advisory"
        if fd2_fact_audit_policy(policy):
            warnings = (getattr(self, "_analysis_decision_warnings", []) if analyst else
                        getattr(self, "_department_warning_records", {}).get(str(Path(output_file_path).resolve()), []))
            audit_payload["fact_audit_mode"] = "nonblocking"
            audit_payload["fact_audit_warnings"] = warnings
            audit_payload["validation_warnings"].extend(warnings)
        if policy.get("execution_policy_version"):
            audit_payload["execution_policy_version"] = policy["execution_policy_version"]
        if analyst and fd_completion_enabled(policy, role, round_id):
            audit_payload["analysis_attempt_failures"] = getattr(self, "_analyst_attempt_failures", [])
        if fd_completion_enabled(policy, role, round_id):
            audit_payload["completion_guard"] = {
                "max_stop_blocks_per_attempt": 2, "successful_write_and_latest_readback_required": True,
                "transcript_and_legacy_path_recovery_enabled": False,
            }
        MultiTenantUtils._write_json(target, audit_payload)
        return str(target)

    def _strict_context_failure(self, context_audit: Dict[str, Any]) -> Optional[str]:
        if str(self._skill_profile().get("context_validation") or "off") != "strict":
            return None
        problems = []
        if context_audit.get("disallowed_policy_names"):
            problems.append(
                "disallowed policies="
                + ",".join(context_audit["disallowed_policy_names"])
            )
        if context_audit.get("missing_files"):
            problems.append(
                "missing files=" + ",".join(context_audit["missing_files"])
            )
        return "; ".join(problems) if problems else None

    # =========================
    # helper: spec / prompt
    # =========================

    def _build_analyst_run_spec(
        self,
        round_id: int,
    ) -> AnalystRunSpec:
        enterprise_name = self.enterprise_spec.enterprise_name
        skill_name = self.enterprise_spec.analyst_skill_name

        skill_args = f"--round_id {round_id} --enterprise_name {enterprise_name}"

        output_file_name = "analysis.json"
        output_file_path = str(
            self.workspace_dir
            / "enterprises"
            / enterprise_name
            / "analysis.json"
        )
        history_projection_day = self._history_projection_day(round_id)
        history_projection_path = str(self._history_projection_path(round_id))
        cobweb_compaction_enabled = self.cobweb_profit_objective_enabled()
        long_run_compaction_enabled = bool(
            self._long_run_agent_context_compaction_policy()
        )
        compact_observation_kind = (
            "cobweb_c3"
            if cobweb_compaction_enabled
            else "long_horizon_e1"
            if long_run_compaction_enabled
            else ""
        )
        compact_observation_enabled = bool(compact_observation_kind)
        single_case_active = bool(self._single_enterprise_case_policy_for_prompt())
        observation_path = (
            self._prepare_cobweb_profit_analyst_observation(round_id)
            if cobweb_compaction_enabled
            else self._prepare_long_run_analyst_observation(round_id)
            if long_run_compaction_enabled
            else self._prepare_single_enterprise_analyst_observation(round_id)
            if single_case_active
            else self._raw_analyst_observation_path(round_id)
        )
        if compact_observation_enabled or single_case_active:
            skill_args += f" --observation_path {observation_path}"
        chart_export_enabled = self._chart_export_injected_to_analyst()
        chart_export_path = (
            self._prepare_single_enterprise_analyst_chart_export(round_id)
            if chart_export_enabled and single_case_active
            else self._chart_export_path()
        )

        return AnalystRunSpec(
            role="Analyst",
            skill_name=skill_name,
            skill_args=skill_args,
            output_file_name=output_file_name,
            output_file_path=output_file_path,
            observation_path=str(observation_path),
            compact_observation_enabled=compact_observation_enabled,
            history_projection_path=history_projection_path,
            history_projection_day=history_projection_day,
            compact_observation_kind=compact_observation_kind,
            chart_export_path=str(chart_export_path),
            chart_export_enabled=chart_export_enabled,
        )

    def _build_analyst_launch_prompt(
        self,
        spec: AnalystRunSpec,
        round_id: int,
    ) -> str:
        single_case_prompt = self._build_single_enterprise_analyst_prompt()
        single_case_active = bool(single_case_prompt)
        cobweb_profit_prompt = (
            self._build_cobweb_profit_objective_analyst_prompt()
        )
        long_horizon_response_prompt = (
            '\n\nE1 external-change analysis contract:\n- Read external_change_review first instead of merely restating event descriptions from external_environment.\n- Use factor_comparison to distinguish a new change this round, an earlier change that remains active, and no change. Combine this with recent enterprise history to determine whether the effect comes from cost changes, order or backlog pressure, or both.\n- Do not translate a cost-factor change directly into a fixed purchase quantity, fixed production quantity, or unconditional price change. Explain the trade-off using unit economics, cash, inventory coverage, service, and capacity.\n- analysis.json must also contain the top-level external_environment_response object with this structure:\n  {\n    "observed_event_ids": ["event_id values actually observed; empty when none exist"],\n    "active_factor_assessment": {\n      "name of a factor that actually deviates from baseline": {\n        "observed_value": 1.0,\n        "business_implication": "verifiable effect on this enterprise"\n      }\n    },\n    "impact_assessment": "explain the effect and possible confounders using enterprise evidence",\n    "response_strategy": "conditional response direction for the next 2 to 3 rounds, not one mandatory action",\n    "review_criteria": "quantitative profit, cash, fulfillment, inventory, or capacity criteria for review over the next 2 to 5 rounds"\n  }\n- target, evaluation, and reason for affected departments in department_targets must agree with this response contract. When no action is needed, explain how holding the current course avoids cost, cash, or fulfillment risk.\n'.rstrip()
            if spec.compact_observation_kind == "long_horizon_e1"
            else ""
        )
        observation_prompt = (
            f'\n\nCobweb C3 input:\n- Current bounded observation: {spec.observation_path}.\n- This contains current operating state and recent history and overrides generic observation examples. Read this compact input only; do not open raw observation_day{round_id}.txt, complete order history or complete cobweb history.\n'.rstrip()
            if spec.compact_observation_kind == "cobweb_c3"
            else f'\n\nBounded long-horizon input:\n- Current projected observation: {spec.observation_path}.\n- This retains actionable orders, proposals, inventory, capacity, cash, staffing and environment, with unbounded history compressed into counts, trends and recent records.\n- Read only this bounded file; do not open raw observation_day{round_id}.txt or complete order/proposal histories.\n- Full observations remain available for execution, visualization and audit; a compact input does not imply missing real business state.\n'.rstrip()
            if spec.compact_observation_kind == "long_horizon_e1"
            else ""
        )
        history_prompt = (
            '\nHistorical diagnostic input:\n- Closed recent history is embedded in the recent_history field of the compact observation.\n- Do not read a separate history_projection file.\n'.rstrip()
            if spec.compact_observation_enabled
            else f'\nSingle-enterprise historical diagnosis (priority):\n- Latest sealed projection, day {spec.history_projection_day}: {spec.history_projection_path}\n- If present, read its trends first to assess demand, fulfillment, inventory/materials, capacity, staffing, cash and procurement.\n- It describes this enterprise only, not an inter-firm supply chain or peer actions.\n- If absent, no historical window is available; do not read future projections or invent trends.\n'.rstrip()
            if single_case_active
            else f'\nHistorical input:\n- Latest sealed projection, day {spec.history_projection_day}: {spec.history_projection_path}\n- If present, use its trends to assess phase-level problems.\n- If absent, do not read future projections or invent history.\n'.rstrip()
        )
        chart_export_prompt = ""
        if spec.chart_export_enabled:
            chart_export_prompt = (
                f'\n\nSingle-enterprise chart evidence (priority):\n- Current charts_data_export.json: {spec.chart_export_path}\n- Read once if present. Use fulfillment, material coverage, warehouse/cash pressure, demand-capacity gaps, order funnels and backlog aging as primary diagnostic evidence.\n- These charts describe internal enterprise bottlenecks, not peer aggregates or mechanism replication.\n- Locate anomalies in charts, then verify order, inventory, capacity, staffing, cash, supplier and feasibility facts in authorized observation/state.\n- If absent, use observation/history without inventing chart values.\n'.rstrip()
                if single_case_active
                else f'\n\nLegacy single-enterprise chart input:\n- Current charts_data_export.json: {spec.chart_export_path}\n- If present, read once and use fulfillment, materials, warehouse/cash pressure, demand-capacity gaps and order-funnel fields as supporting evidence.\n- This compatibility input is configuration-controlled; if absent, use observation/history without inventing charts.\n'.rstrip()
            )

        return f'\nCurrent simulation round: {round_id}.\nYou are the analyst for enterprise {self.enterprise_spec.enterprise_name}. Produce low-frequency strategic analysis.\nIdentify structural problems, bottlenecks and opportunities; use the current observation to define stable, executable department goals for the next two or three rounds. Do not issue round-by-round micromanagement or unsupported exact numerical targets. Use only available actions. When external_environment.enabled=true, inspect current_turn, triggered_events, active_factors and actual_values; identify activated changes and use recent history to assess cost, cash, service, inventory, procurement and production. Do not infer undisclosed future shocks.\n{single_case_prompt}\n{fd4_decision_guidance(self.workspace_dir, round_id, 'Analyst')}{fd2_decision_guidance(self.workspace_dir, round_id, 'Analyst')}\n{cobweb_profit_prompt}\n{long_horizon_response_prompt}\n{observation_prompt}\n{history_prompt}\n{chart_export_prompt}\n\nActual paths for this run:\n- Current observation: {spec.observation_path}\n- Final analysis.json: {spec.output_file_path}\n- These paths override example workspace_multi paths in the Skill.\nInvoke this exact Skill call immediately, retaining args and its format:\nSkill(skill="{spec.skill_name}", args="{spec.skill_args}")\n\nAfter invoking the Skill, continue until you successfully write {spec.output_file_name}.\nPrinting explanations or JSON does not replace writing the file.\n'.strip()

    def _build_analyst_same_session_resume_prompt(
        self,
        spec: AnalystRunSpec,
        round_id: int,
        reason: str,
    ) -> str:
        single_case_prompt = self._build_single_enterprise_analyst_prompt()
        single_case_active = bool(single_case_prompt)
        cobweb_profit_prompt = (
            self._build_cobweb_profit_objective_analyst_prompt()
        )
        long_horizon_response_prompt = (
            '\n\nThe E1 external-change analysis contract remains active. Complete external_environment_response with observed_event_ids,\nactive_factor_assessment, impact_assessment, response_strategy, and review_criteria. Revise it using actual factors from\nexternal_change_review and enterprise evidence; do not present one fixed action as the answer.\n'.rstrip()
            if spec.compact_observation_kind == "long_horizon_e1"
            else ""
        )
        observation_prompt = (
            f'\n\nCobweb C3 input remains {spec.observation_path}. Read this compact input only;\ndo not open raw observation_day{round_id}.txt, complete order history or complete cobweb history.\n'.rstrip()
            if spec.compact_observation_kind == "cobweb_c3"
            else f'\n\nBounded long-horizon input remains {spec.observation_path}. Read this projection only;\ndo not open raw observation_day{round_id}.txt or complete order/proposal histories.\n'.rstrip()
            if spec.compact_observation_kind == "long_horizon_e1"
            else ""
        )
        history_prompt = (
            "\nHistorical diagnostic input:\n- Closed recent history is embedded in the compact observation's recent_history field. Do not read a separate history_projection file.\n".rstrip()
            if spec.compact_observation_enabled
            else f"\nSingle-enterprise historical evidence remains a priority:\n- Latest sealed projection, day {spec.history_projection_day}: {spec.history_projection_path}\n- If present, use this enterprise's demand, fulfillment, inventory/materials, capacity, staffing, cash and procurement trends; never read future projections.\n- Do not interpret it as inter-firm or peer behavior.\n".rstrip()
            if single_case_active
            else f'\nHistorical input:\n- Latest sealed projection, day {spec.history_projection_day}: {spec.history_projection_path}\n- If present, use the trends; otherwise do not read future projections.\n'.rstrip()
        )
        chart_export_prompt = ""
        if spec.chart_export_enabled:
            chart_export_prompt = (
                f'\n\nSingle-enterprise chart evidence remains a priority:\n- Current charts_data_export.json: {spec.chart_export_path}\n- Read once if present to assess fulfillment, materials, warehouse/cash pressure, demand-capacity gaps, order funnels and backlog aging.\n- These describe this enterprise only, not inter-firm mechanisms or peer aggregates.\n'.rstrip()
                if single_case_active
                else f'\n\nLegacy chart input:\n- Current charts_data_export.json: {spec.chart_export_path}\n- If present, read once for fulfillment, materials, warehouse/cash pressure, demand-capacity gaps and order funnels.\n'.rstrip()
            )

        return f'\nCurrent simulation round: {round_id}.\nYou remain the analyst for enterprise {self.enterprise_spec.enterprise_name}.\nThe previous attempt in this session did not finish writing the target file.\nFailure: {reason}\n\nRetain the analyst boundary: provide low-frequency context and goals for two or three rounds, not department micromanagement. Use executable actions. Where observations do not justify precise large numbers, use grounded thresholds, ranges or directional quantities.\n{single_case_prompt}\n{fd4_decision_guidance(self.workspace_dir, round_id, 'Analyst')}{fd2_decision_guidance(self.workspace_dir, round_id, 'Analyst')}\n{cobweb_profit_prompt}\n{long_horizon_response_prompt}\n{observation_prompt}\n\nDo not restart explanations or lengthy analysis or print Markdown fences. Reuse completed reads and reasoning in this session and finish the missing steps.\nRetain this exact Skill call contract:\nSkill(skill="{spec.skill_name}", args="{spec.skill_args}")\n\n{history_prompt}\n{chart_export_prompt}\n\nActual paths for this run:\n- Current observation: {spec.observation_path}\n- Final analysis.json: {spec.output_file_path}\n- Supplied paths override workspace_multi examples in the Skill.\nPerform only operations needed to finish {spec.output_file_name}:\n1. If final JSON is ready, Write it directly to {spec.output_file_name}\n2. Immediately Read back {spec.output_file_name}\n3. Complete any missing required reads, then write immediately.\n4. Do not substitute printed explanations or JSON for file output.\n'.strip()

    def _inspect_analyst_messages(
        self,
        messages: List[dict],
        spec: AnalystRunSpec,
        session_messages: Optional[List[dict]] = None,
    ) -> AnalystRunInspection:
        text = "\n".join(m["content"] for m in messages)

        skill_text = "\n".join(m["content"] for m in (session_messages or [])) + "\n" + text
        skill_called = f"name='Skill'" in skill_text and f"'skill': '{spec.skill_name}'" in skill_text
        skill_args_ok = spec.skill_args in skill_text

        write_called = "name='Write'" in text or 'name="Write"' in text
        write_target_ok = spec.output_file_path in text or spec.output_file_name in text

        read_output_called = (
            ("name='Read'" in text or 'name="Read"' in text)
            and spec.output_file_path in text
        )

        json_block = self._extract_last_json_block(text)
        has_json_block = json_block is not None

        if not skill_called:
            failure_type = "skill_not_called"
        elif not skill_args_ok:
            failure_type = "skill_args_missing_or_wrong"
        elif has_json_block and not write_called:
            failure_type = "json_printed_but_not_written"
        elif write_called and not write_target_ok:
            failure_type = "write_wrong_target"
        elif write_called and not read_output_called:
            failure_type = "written_but_not_readback"
        elif not write_called:
            failure_type = "stopped_before_write"
        else:
            failure_type = "unknown"

        return AnalystRunInspection(
            skill_called=skill_called,
            skill_args_ok=skill_args_ok,
            write_called=write_called,
            write_target_ok=write_target_ok,
            read_output_called=read_output_called,
            has_json_block=has_json_block,
            json_block=json_block,
            failure_type=failure_type,
        )

    def _next_analyst_prompt_by_failure(
        self,
        spec: AnalystRunSpec,
        round_id: int,
        inspection: AnalystRunInspection,
    ) -> str:
        validation_error = getattr(self, "_last_analysis_validation_error", "")
        mapping = {
            "skill_not_called": 'The Skill tool was not actually called in the previous turn. Call it now in the exact required format and complete the file in this session.',
            "skill_args_missing_or_wrong": f'The previous Skill call omitted or changed args. Use exactly args="{spec.skill_args}"。',
            "json_printed_but_not_written": f'The previous attempt produced final JSON without Write. Write that JSON directly to {spec.output_file_name} and Read it back.',
            "write_wrong_target": f'The previous Write used the wrong path. Write the final JSON to {spec.output_file_name} and Read it back.',
            "written_but_not_readback": f'The previous attempt wrote {spec.output_file_name} but did not read it back. Read the output now and validate it.',
            "stopped_before_write": f'The previous attempt stopped before writing. Reuse the decision and finish Write and readback for {spec.output_file_name}.',
            "unknown": f'The previous attempt did not finish {spec.output_file_name}. Reuse existing work to finish Write and readback.',
        }
        if validation_error:
            mapping["unknown"] = (
                f'The previously written {spec.output_file_name} failed structural/semantic validation: {validation_error}. Correct this issue, Write the full candidate and immediately Read it back.'
            )
            if read_policy(self.workspace_dir, round_id):
                return self._build_analyst_same_session_resume_prompt(
                    spec, round_id, mapping["unknown"],
                )
        return self._build_analyst_same_session_resume_prompt(
            spec,
            round_id,
            mapping.get(inspection.failure_type, mapping["unknown"]),
        )

    def _is_valid_json_file(self, file_path: str) -> bool:
        path = Path(file_path)
        active_round = getattr(self, "_active_analyst_round_id", None)
        fd_analysis = (
            path.name == "analysis.json"
            and active_round is not None
            and bool(read_policy(self.workspace_dir, active_round))
        )
        try:
            if not path.exists():
                if fd_analysis:
                    self._set_analysis_validation_error("analysis.json was not created")
                return False
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                if fd_analysis:
                    self._set_analysis_validation_error("analysis.json is empty")
                return False
            data = json.loads(content)
            if path.name == "analysis.json":
                return self._is_valid_analysis_payload(data)
            return True
        except Exception as exc:
            if fd_analysis:
                self._set_analysis_validation_error(
                    f"analysis.json could not be read or validated: {type(exc).__name__}: {exc}"
                )
            return False

    def _is_placeholder_text(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        text = value.strip()
        if not text:
            return True
        lowered = text.lower()
        return (
            lowered in {"string", "todo", "tbd", "none", "null"}
            or text in {
                "<...>", "...", "example", "to be completed",
            }
            or (text.startswith("<") and text.endswith(">"))
        )

    def _set_analysis_validation_error(self, message: str) -> bool:
        self._last_analysis_validation_error = message
        return False

    def _flatten_analysis_target_text(self, target: Dict[str, Any]) -> str:
        if not isinstance(target, dict):
            return ""
        return " ".join(
            str(target.get(field) or "")
            for field in ("target", "evaluation", "reason")
        )

    def _contains_unnegated_term(self, text: str, terms: List[str]) -> bool:
        lowered = text.lower()
        negations = (
            "do not", "avoid", "no ", "without", "pass", "not ",
            "discourage", "discouraged", "discourages",
            "discouraged_action", "discouraged_actions",
            "against", "constraint against",
        )
        for term in terms:
            term_lower = term.lower()
            start = 0
            while True:
                idx = lowered.find(term_lower, start)
                if idx < 0:
                    break
                window = lowered[max(0, idx - 40):idx]
                after_window = lowered[idx + len(term_lower):idx + len(term_lower) + 56]
                if not any(
                    neg.lower() in window or neg.lower() in after_window
                    for neg in negations
                ):
                    return True
                start = idx + len(term_lower)
        return False

    def _validate_single_enterprise_analysis_payload(self, data: Dict[str, Any]) -> bool:
        policy = self._single_enterprise_case_policy_for_prompt()
        if not policy:
            return True

        # Hidden single-case answer keys are kept out of analysis validation.
        # This guard only prevents metadata leakage and checks output shape;
        # action preferences must come from live orders/inventory/cash/staff/
        # capacity/supplier evidence, not from case labels.
        primary_issue = ""
        discouraged_actions = set()
        targets = data.get("department_targets") or {}
        all_target_text = "\n".join(
            self._flatten_analysis_target_text(target)
            for target in targets.values()
            if isinstance(target, dict)
        )
        visible_analysis_text = "\n".join([
            str(data.get("enterprise_summarys") or ""),
            all_target_text,
        ])
        if re.search(
            r"(single_case[_\w-]*|source_primary_contradiction|primary_issue|case_id|case0[1-9]|case\s*['\"]?\s*single_case|"
            r"\b(market_insufficient|market_shortage|raw_material_shortage|material_shortage|capacity_bottleneck|staff_shortage|cash_pressure)\b)",
            visible_analysis_text,
            re.IGNORECASE,
        ):
            return self._set_analysis_validation_error(
                "analysis.json must not cite experiment metadata or department priority that is absent from runtime state. Restate the diagnosis using only orders, inventory, materials, capacity, staffing, cash, and supplier state."
            )

        if "hr" not in targets:
            return self._set_analysis_validation_error(
                "A single-enterprise diagnosis must include department_targets.hr and evaluate staffing actions from actual headcount, available staff, pending recruitment, and insufficient-staff failure records."
            )

        for action in sorted(discouraged_actions):
            if action and self._contains_unnegated_term(all_target_text, [action]):
                return self._set_analysis_validation_error(
                    "A single-enterprise diagnostic objective contains an action inconsistent with current state constraints. Set objectives from actual inventory, orders, cash, capacity, and staffing state."
                )

        if primary_issue in {"market_shortage", "market_insufficient"}:
            sales_text = self._flatten_analysis_target_text(targets.get("sales") or {})
            sales_text_lower = sales_text.lower()
            market_terms = [
                "develop_market", "market development", "market coverage",
                "real nonzero demand",
            ]
            invalid_order_terms = [
                "invalid_quantity", "quantity=0", "quantity <= 0", "zero quantity",
                "invalid", "placeholder",
            ]
            accept_terms = [
                "accept_order", "accept order", "orders_received", "accepted",
                "available order accepted by the system",
            ]
            if self._contains_unnegated_term(sales_text, accept_terms) and not any(
                term.lower() in sales_text_lower
                for term in market_terms
            ) and not any(
                term.lower() in sales_text_lower
                for term in invalid_order_terms
            ):
                return self._set_analysis_validation_error(
                    "A Sales objective must not treat acceptance of an invalid or placeholder order as growth. When real nonzero demand is insufficient, base the objective on market coverage, demand formation, or waiting for a real order."
                )

        if primary_issue == "cash_pressure":
            forbidden_positive_terms = [
                "create_production_plan", "build_production_line", "create_purchase_order",
                "handle_recruitment", "develop_market", "expand_warehouse",
                "production plan", "start production", "manufacturing", "putaway",
                "procurement replenishment", "purchase order", "recruitment",
                "warehouse expansion", "market development",
            ]
            if self._contains_unnegated_term(all_target_text, forbidden_positive_terms):
                return self._set_analysis_validation_error(
                    "Current cash state does not support treating production startup, purchasing, recruitment, warehouse expansion, or market development as a positive objective. Determine a low-risk action from cash, inventory, and fulfillable orders first."
                )

        return True

    def _validate_cobweb_profit_analysis_payload(
        self,
        data: Dict[str, Any],
    ) -> bool:
        if not self.cobweb_profit_objective_enabled():
            return True

        expected_round = getattr(self, "_active_analyst_round_id", None)
        if expected_round is not None:
            try:
                payload_round = int(data.get("round_id"))
            except (TypeError, ValueError):
                return self._set_analysis_validation_error(
                    "Cobweb C3 analysis.round_id must be the integer for the current round."
                )
            if payload_round != int(expected_round):
                return self._set_analysis_validation_error(
                    "Cobweb C3 analysis.json must be generated for the current round; "
                    f"expected round_id={expected_round}, received {payload_round}."
                )

        serialized = json.dumps(data, ensure_ascii=False).lower()
        forbidden_evidence = [
            "recommended_plan_quantity",
            "recommended_daily_capacity",
            "affordable_recovery_candidates",
        ]
        leaked_fields = [
            field for field in forbidden_evidence if field in serialized
        ]
        if leaked_fields:
            return self._set_analysis_validation_error(
                "Cobweb C3 analysis cites prohibited prescriptive quantity fields: "
                + ", ".join(leaked_fields)
                + ". Use profit, cash, fulfillment, and stability evidence to form a range or threshold."
            )

        production_target = (
            (data.get("department_targets") or {}).get("production") or {}
        )
        production_text = self._flatten_analysis_target_text(
            production_target
        )
        exact_quantity_patterns = [
            r"(?:production|plan)\s+quantity\s*(?:is|=|at)\s*"
            r"\d+(?:\.\d+)?\s*(?:units?)?",
            r"exactly\s+\d+(?:\.\d+)?\s*(?:units?)?",
        ]
        if any(
            re.search(pattern, production_text, flags=re.IGNORECASE)
            for pattern in exact_quantity_patterns
        ):
            return self._set_analysis_validation_error(
                "A Cobweb C3 production-phase objective must not prescribe one exact quantity. "
                "Use a range, threshold, or conditional comparison rule."
            )

        objective_groups = [
            ("profit", "gross margin", "margin", "cost", "revenue"),
            ("cash", "funding", "liquidity"),
            ("fulfillment", "service", "backlog", "order", "fill"),
            ("stability", "adjustment", "volatility", "boundary", "price risk"),
        ]
        lowered = production_text.lower()
        covered_groups = sum(
            any(term.lower() in lowered for term in group)
            for group in objective_groups
        )
        if covered_groups < 3:
            return self._set_analysis_validation_error(
                "A Cobweb C3 production objective must cover at least three evidence groups: "
                "profit or cost, cash, fulfillment or backlog, and stability or price risk."
            )
        return True

    def _validate_long_horizon_analysis_payload(
        self,
        data: Dict[str, Any],
    ) -> bool:
        if not self._long_run_agent_context_compaction_policy():
            return True

        expected_round = getattr(self, "_active_analyst_round_id", None)
        if expected_round is not None:
            try:
                payload_round = int(data.get("round_id"))
            except (TypeError, ValueError):
                return self._set_analysis_validation_error(
                    "E1 analysis.round_id must be the integer for the current round."
                )
            if payload_round != int(expected_round):
                return self._set_analysis_validation_error(
                    "E1 analysis.json must be generated for the current round; "
                    f"expected round_id={expected_round}, received {payload_round}."
                )

        response = data.get("external_environment_response")
        if not isinstance(response, dict):
            return self._set_analysis_validation_error(
                "E1 analysis.json is missing the external_environment_response object."
            )
        for field in (
            "impact_assessment",
            "response_strategy",
            "review_criteria",
        ):
            if (
                not isinstance(response.get(field), str)
                or self._is_placeholder_text(response.get(field))
            ):
                return self._set_analysis_validation_error(
                    f"external_environment_response.{field} is missing or contains placeholder text."
                )
        observed_event_ids = response.get("observed_event_ids")
        if not isinstance(observed_event_ids, list):
            return self._set_analysis_validation_error(
                "external_environment_response.observed_event_ids must be an array."
            )
        factor_assessment = response.get("active_factor_assessment")
        if not isinstance(factor_assessment, dict):
            return self._set_analysis_validation_error(
                "external_environment_response.active_factor_assessment must be an object."
            )

        review: Dict[str, Any] = {}
        if expected_round is not None:
            compact_observation = MultiTenantUtils._read_json_if_exists(
                self._compact_long_run_analyst_observation_path(
                    int(expected_round)
                ),
                {},
            )
            review = compact_observation.get("external_change_review") or {}
        latest_event = review.get("latest_event") or {}
        latest_event_id = str(latest_event.get("event_id") or "")
        if latest_event_id and latest_event_id not in {
            str(item) for item in observed_event_ids
        }:
            return self._set_analysis_validation_error(
                "external_environment_response.observed_event_ids must include "
                f"the released and still relevant event {latest_event_id}."
            )

        for factor in review.get("active_deviating_factors") or []:
            assessment = factor_assessment.get(str(factor))
            if not isinstance(assessment, dict):
                return self._set_analysis_validation_error(
                    "external_environment_response.active_factor_assessment "
                    f"is missing factor {factor}, which currently deviates from baseline."
                )
            if (
                not isinstance(assessment.get("business_implication"), str)
                or self._is_placeholder_text(
                    assessment.get("business_implication")
                )
            ):
                return self._set_analysis_validation_error(
                    f"Factor {factor} lacks a business_implication grounded in this enterprise's state."
                )
            observed_value = assessment.get("observed_value")
            if observed_value is None:
                return self._set_analysis_validation_error(
                    f"Factor {factor} lacks observed_value."
                )
            try:
                float(observed_value)
            except (TypeError, ValueError):
                return self._set_analysis_validation_error(
                    f"observed_value for factor {factor} must be numeric."
                )
        return True

    def _is_valid_analysis_payload(self, data: Any) -> bool:
        self._last_analysis_validation_error = ""
        if not isinstance(data, dict):
            return self._set_analysis_validation_error('The top level of analysis.json must be an object.')
        active_round = getattr(self, "_active_analyst_round_id", None)
        if active_round is not None and read_policy(self.workspace_dir, active_round):
            raw_round = data.get("round_id")
            # The shared Skill schema permits integer rounds encoded as strings.
            valid_round_type = type(raw_round) is int or (
                isinstance(raw_round, str)
                and re.fullmatch(r"[0-9]+", raw_round.strip()) is not None
            )
            if not valid_round_type or int(raw_round) != active_round:
                return self._set_analysis_validation_error(
                    f'round_id must be the current round {active_round} (integer or integer string); received {raw_round!r}。'
                )
            if data.get("enterprise_name") != self.enterprise_spec.enterprise_name:
                return self._set_analysis_validation_error(
                    f'enterprise_name must be {self.enterprise_spec.enterprise_name}; received {data.get('enterprise_name')!r}。'
                )
        for key in ("enterprise_name", "round_id", "enterprise_summarys", "department_targets"):
            if key not in data:
                return self._set_analysis_validation_error(f'analysis.json is missing top-level fields: {key}')
        if self._is_placeholder_text(data.get("enterprise_name")):
            return self._set_analysis_validation_error('enterprise_name is empty or contains placeholder text.')
        if self._is_placeholder_text(str(data.get("round_id"))):
            return self._set_analysis_validation_error('round_id is empty or contains placeholder text.')
        if self._is_placeholder_text(data.get("enterprise_summarys")):
            return self._set_analysis_validation_error('enterprise_summarys is empty or contains placeholder text.')
        targets = data.get("department_targets")
        if not isinstance(targets, dict):
            return self._set_analysis_validation_error('department_targets must be an object.')
        for dept in ("sales", "procurement"):
            if dept not in targets:
                return self._set_analysis_validation_error(f'department_targets is missing {dept}。')
        if self.enterprise_spec.enterprise_name == "Manufacturer" and "production" not in targets:
            return self._set_analysis_validation_error('Manufacturer department_targets is missing production.')
        for dept, target in targets.items():
            if not isinstance(target, dict):
                return self._set_analysis_validation_error(f'department_targets.{dept} must be an object.')
            for field in ("target", "evaluation", "reason"):
                if field not in target or self._is_placeholder_text(target.get(field)):
                    return self._set_analysis_validation_error(
                        f'department_targets.{dept}.{field} is missing or contains placeholder text.'
                    )
        if not self._validate_cobweb_profit_analysis_payload(data):
            return False
        if not self._validate_long_horizon_analysis_payload(data):
            return False
        policy = read_policy(self.workspace_dir, active_round) if active_round is not None else {}
        if (policy.get("agent_prompt_policy_version") in FD4_DECISION_CHECK_VERSIONS
                and active_round <= int(policy.get("formal_end", 19))):
            source = Path(getattr(self, "_active_analyst_observation_path", None) or (
                self._single_enterprise_analyst_input_dir(active_round) / "observation_compact.json"
            ))
            sources = {"observation": self._read_json_file(source, {})}
            spec = getattr(self, "_active_analyst_spec", None)
            if policy["agent_prompt_policy_version"] in FD4_REFERENCE_VERSIONS and spec:
                optional = []
                if spec.chart_export_enabled:
                    optional.append(("charts", spec.chart_export_path))
                if not spec.compact_observation_enabled:
                    optional.append(("history", spec.history_projection_path))
                for name, path in optional:
                    if path and Path(path).is_file():
                        sources[name] = self._read_json_file(Path(path), {})
            errors = validate_fd4_decision(data, "analyst", sources, active_round,
                allow_empty_records=policy["agent_prompt_policy_version"] in FD4_COMPLETION_VERSIONS)
            if policy["agent_prompt_policy_version"] == FD4_AGENT_PROMPT_VERSION:
                self._analysis_decision_warnings = errors
            elif errors:
                return self._set_analysis_validation_error("Decision consistency: " + "; ".join(errors))
        if fd2_fact_audit_policy(policy):
            self._analysis_decision_warnings = self._fd2_analysis_fact_warnings(data, active_round)
        return self._validate_single_enterprise_analysis_payload(data)

    def _fd2_analysis_fact_warnings(self, data, round_id):
        source = getattr(self, "_active_analyst_observation_path", None)
        if not source:
            return []
        try:
            return fd2_fact_warnings(data, "analyst", {"observation": self._read_json_file(Path(source), {})},
                round_id, int(read_policy(self.workspace_dir, round_id).get("formal_end", 19)))
        except (TypeError, ValueError, AttributeError, OverflowError) as exc:
            return [f"Nonblocking fact audit could not inspect this analysis: {type(exc).__name__}: {exc}"]

    def _analyst_fact_warnings(self, spec, round_id):
        warnings = self._fd2_analysis_fact_warnings(self._read_json_file(Path(spec.output_file_path), {}), round_id)
        self._analysis_decision_warnings = warnings
        return warnings

    def _department_decision_errors(self, spec: SkillRunSpec, round_id: int) -> List[str]:
        policy = read_policy(self.workspace_dir, round_id)
        if policy.get("agent_prompt_policy_version") == FD4_AGENT_PROMPT_VERSION or fd2_fact_audit_policy(policy):
            try:
                warnings = self._department_decision_warnings(spec, round_id)
            except (TypeError, ValueError, AttributeError, OverflowError) as exc:
                warnings = [f"Semantic audit could not inspect this proposal: {type(exc).__name__}: {exc}"]
            records = getattr(self, "_department_warning_records", None)
            if records is None:
                self._department_warning_records = records = {}
            records[str(Path(spec.output_file_path).resolve())] = warnings
            return []
        return self._department_decision_warnings(spec, round_id)

    def _department_decision_warnings(self, spec: SkillRunSpec, round_id: int) -> List[str]:
        policy = read_policy(self.workspace_dir, round_id)
        if fd2_fact_audit_policy(policy):
            return fd2_fact_warnings(self._read_json_file(Path(spec.output_file_path), []), spec.role,
                {"state": self._read_json_file(Path(spec.state_path), {}),
                 "coordination": self._read_json_file(Path(spec.blackboard_path), {})},
                round_id, int(policy.get("formal_end", 19)))
        if (policy.get("agent_prompt_policy_version") not in FD4_DECISION_CHECK_VERSIONS
                or round_id > int(policy.get("formal_end", 19))):
            return []
        return validate_fd4_decision(
            self._read_json_file(Path(spec.output_file_path), []), spec.role,
            {"state": self._read_json_file(Path(spec.state_path), {}),
             "coordination": self._read_json_file(Path(spec.blackboard_path), {}),
             "analysis": self._read_json_file(Path(spec.analysis_path), {})},
            round_id, allow_empty_records=policy["agent_prompt_policy_version"] in FD4_COMPLETION_VERSIONS,
            allow_source_aliases=policy["agent_prompt_policy_version"] in FD4_ROLE_CONTRACT_VERSIONS,
            allow_workload_leaves=policy["agent_prompt_policy_version"] in FD4_WORKLOAD_REFERENCE_VERSIONS,
        )

    def _department_completion_error(self, spec, round_id, attempt_state):
        path = Path(spec.output_file_path)
        if not path.is_file() or not MultiTenantUtils._is_valid_action_file(path, spec.role.lower()):
            return "Required output is missing or does not match the action schema: " + str(path)
        errors = self._department_decision_errors(spec, round_id)
        if errors:
            return "Decision consistency: " + "; ".join(errors)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if attempt_state.get("output_write_sha256") != digest:
            return "The current valid output must be written using Write."
        if attempt_state.get("output_readback_sha256") != digest:
            return "Read back the latest Write before completing."
        admitted = accessed_inputs(self.workspace_dir, self.enterprise_spec.enterprise_name, spec.role, round_id)
        required = [spec.state_path, spec.blackboard_path, spec.analysis_path]
        if spec.trade_decision_card_path:
            required.append(spec.trade_decision_card_path)
        missing = [value for value in required if str(Path(value).resolve()) not in admitted]
        return "Required authorized reads missing: " + ", ".join(missing) if missing else None

    def _analyst_completion_error(self, spec, round_id, attempt_state):
        path = Path(spec.output_file_path)
        if not path.is_file():
            return "analysis.json was not created at the required output path"
        if not self._is_valid_json_file(spec.output_file_path):
            return getattr(self, "_last_analysis_validation_error", "") or "analysis.json is invalid"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if attempt_state.get("output_write_sha256") != digest:
            return "Write has not successfully produced the current complete analysis.json"
        if attempt_state.get("output_readback_sha256") != digest:
            return "Read has not successfully checked the latest Write; read back analysis.json"
        admitted = accessed_inputs(self.workspace_dir, self.enterprise_spec.enterprise_name, "Analyst", round_id)
        required = [spec.observation_path]
        if spec.chart_export_enabled and Path(spec.chart_export_path).is_file():
            required.append(spec.chart_export_path)
        if not spec.compact_observation_enabled and Path(spec.history_projection_path).is_file():
            required.append(spec.history_projection_path)
        missing = [value for value in required if str(Path(value).resolve()) not in admitted]
        if missing:
            return "Required authorized Analyst reads missing: " + ", ".join(missing)
        return None

    def _remove_stale_output_file(self, file_path: str) -> None:
        path = Path(file_path)
        getattr(self, "_output_recovery_sources", {}).pop(str(path.resolve()), None)
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def _department_output_failure_reason(self, file_path: str, department: str) -> str:
        path = Path(file_path)
        if not path.exists():
            return "Agent output was not created: " + file_path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return f"Agent output cannot be parsed: {type(exc).__name__}: {exc}; path={file_path}"
        payload = MultiTenantUtils._normalize_action_payload_shape(payload)
        if not isinstance(payload, list) or not payload:
            return "Agent output must be a nonempty flat action array [ACTION_ITEM, ...]: " + file_path
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                return (
                    f"Agent output item[{index}] must be an action object, not {type(item).__name__}; "
                    "use [ACTION_ITEM, ...], not [[ACTION_ITEM, ...]]."
                )
            if not MultiTenantUtils._is_valid_action_payload([item], department):
                return (
                    f"Agent output item[{index}] violates the {department} action schema; "
                    "check action.action_name, required action.action_param fields, "
                    "action_reason, module_type and executor_id."
                )
        return "Agent output failed validation: " + file_path

    def _read_python_string_literal(self, text: str, start_idx: int) -> Optional[str]:
        if start_idx >= len(text) or text[start_idx] not in ("'", '"'):
            return None

        quote = text[start_idx]
        escaped = False
        for idx in range(start_idx + 1, len(text)):
            char = text[idx]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                try:
                    value = ast.literal_eval(text[start_idx:idx + 1])
                    return value if isinstance(value, str) else None
                except Exception:
                    return None
        return None

    def _extract_json_blocks(self, text: str) -> List[str]:
        """Extract fenced JSON blocks while remaining compatible with transcript-style output."""
        if not isinstance(text, str):
            return []
        variants = [text]
        try:
            decoded = text.encode("utf-8").decode("unicode_escape")
            if decoded != text:
                variants.append(decoded)
        except Exception:
            pass

        payloads: List[str] = []
        seen = set()
        for variant in variants:
            for pattern in (r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```"):
                for match in re.finditer(pattern, variant, flags=re.DOTALL | re.IGNORECASE):
                    candidate = match.group(1).strip()
                    if not (candidate.startswith("[") or candidate.startswith("{")):
                        continue
                    if candidate in seen:
                        continue
                    seen.add(candidate)
                    payloads.append(candidate)
        return payloads

    def _extract_balanced_json_candidates(self, text: str) -> List[str]:
        """JSON arrays/objects are extracted from the plain text."""
        if not isinstance(text, str):
            return []
        candidates: List[str] = []
        stack: List[str] = []
        start_idx: Optional[int] = None
        in_string = False
        escaped = False
        pairs = {"[": "]", "{": "}"}

        for idx, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char in pairs:
                if not stack:
                    start_idx = idx
                stack.append(pairs[char])
                continue
            if stack and char == stack[-1]:
                stack.pop()
                if not stack and start_idx is not None:
                    candidate = text[start_idx:idx + 1].strip()
                    if len(candidate) >= 2:
                        candidates.append(candidate)
                    start_idx = None
        return candidates

    def _extract_json_payloads_from_messages(self, messages: List[dict]) -> List[str]:
        text = "\n".join(m.get("content", "") for m in messages)
        payloads: List[str] = []

        payloads.extend(self._extract_json_blocks(text))

        for marker in ("TextBlock(text=", "result=", "'content': ", '"content": '):
            search_from = 0
            while True:
                marker_idx = text.find(marker, search_from)
                if marker_idx < 0:
                    break

                literal_start = marker_idx + len(marker)
                payload = self._read_python_string_literal(text, literal_start)
                if payload:
                    payloads.append(payload)
                    payloads.extend(self._extract_json_blocks(payload))
                    payloads.extend(self._extract_balanced_json_candidates(payload))

                search_from = literal_start + 1

        payloads.extend(self._extract_balanced_json_candidates(text))
        return payloads

    def _is_valid_recovered_output_payload(self, data: Any, output_file_path: str) -> bool:
        output_name = Path(output_file_path).name
        if output_name == "analysis.json":
            return self._is_valid_analysis_payload(data)
        if output_name == "finance_advice.json":
            return isinstance(data, dict)
        if output_name.startswith("pre_"):
            output_name = output_name[4:]
        if output_name.endswith("_action.json"):
            dept = output_name.removesuffix("_action.json")
            data = self._normalize_recovered_output_payload(data, output_file_path)
            return MultiTenantUtils._is_valid_action_payload(data, dept)
        return isinstance(data, (list, dict))

    def _normalize_recovered_output_payload(self, data: Any, output_file_path: str) -> Any:
        output_name = Path(output_file_path).name
        if output_name.startswith("pre_"):
            output_name = output_name[4:]
        if output_name.endswith("_action.json"):
            day_match = re.fullmatch(r"day(\d+)", Path(output_file_path).parent.name)
            workspace = getattr(self, "workspace_dir", None)
            allow_wrapper = bool(
                workspace is not None and day_match
                and read_policy(workspace, int(day_match.group(1)))
            )
            return MultiTenantUtils._normalize_action_payload_shape(
                data, allow_legacy_wrapper=allow_wrapper,
            )
        return data

    def _extract_payload_from_printed_write_call(self, data: Any, output_file_path: str) -> Any:
        """
        The compatible model uses the Write tool to print JSON text without a real call tool.

        Accepts only the target path to match the current output file Write payload to avoid the wrong recovery of other document contents.
                """
        if not isinstance(data, dict) or data.get("name") != "Write":
            return data

        arguments = data.get("arguments") or data.get("input") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                return data
        if not isinstance(arguments, dict):
            return data

        target_path = str(arguments.get("file_path") or "")
        expected_path = str(output_file_path)
        if target_path and target_path != expected_path and Path(target_path).name != Path(expected_path).name:
            return data

        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return data
        try:
            return json.loads(content)
        except Exception:
            return data

    def _recover_output_file_from_messages(
        self,
        messages: List[dict],
        output_file_path: str,
    ) -> bool:
        for payload in reversed(self._extract_json_payloads_from_messages(messages)):
            try:
                data = json.loads(payload)
            except Exception:
                continue
            data = self._extract_payload_from_printed_write_call(data, output_file_path)
            if not isinstance(data, (list, dict)):
                continue
            if not self._is_valid_recovered_output_payload(data, output_file_path):
                continue
            data = self._normalize_recovered_output_payload(data, output_file_path)

            try:
                path = Path(output_file_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if not hasattr(self, "_output_recovery_sources"):
                    self._output_recovery_sources = {}
                self._output_recovery_sources[str(path.resolve())] = "validated_model_transcript"
                return True
            except Exception:
                return False

        return False

    def _legacy_analyst_output_path(self, enterprise_name: str) -> Path:
        return (
            self.client_dir
            / "workspace_multi"
            / "enterprises"
            / enterprise_name
            / "analysis.json"
        )

    def _recover_analyst_output_from_legacy_path(
        self,
        spec: AnalystRunSpec,
        attempt_messages: List[dict],
        attempt_started_at: float,
    ) -> Optional[str]:
        """
        Recover analyst output when a skill follows legacy workspace_multi paths
        while the active run uses an isolated workspace_jobs/run_* directory.
        """
        expected_path = Path(spec.output_file_path)
        if expected_path.exists():
            return None

        legacy_path = self._legacy_analyst_output_path(
            self.enterprise_spec.enterprise_name
        )
        if legacy_path == expected_path or not legacy_path.exists():
            return None

        text = "\n".join(str(m.get("content") or "") for m in attempt_messages)
        if str(legacy_path) not in text:
            return None

        try:
            if legacy_path.stat().st_mtime < attempt_started_at - 1.0:
                return None
            data = self._read_json_file(legacy_path, None)
            if not self._is_valid_recovered_output_payload(data, spec.output_file_path):
                return None

            expected_path.parent.mkdir(parents=True, exist_ok=True)
            expected_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not hasattr(self, "_output_recovery_sources"):
                self._output_recovery_sources = {}
            self._output_recovery_sources[str(expected_path.resolve())] = "validated_legacy_output_path"
            return str(legacy_path)
        except Exception:
            return None

    def _write_action_pass_file(self, spec: SkillRunSpec, dept_id: str, reason: str) -> None:
        module_type_map = {
            "sales": "SalesManager",
            "procurement": "ProcurementManager",
            "production": "ProductionManager",
            "hr": "HRManager",
            "inventory": "InventoryManager",
        }
        payload = [
            {
                "action": {
                    "action_name": "action_pass",
                    "action_param": reason,
                },
                "action_reason": reason,
                "module_type": module_type_map.get(dept_id, ""),
                "executor_id": self.enterprise_spec.enterprise_name,
            }
        ]
        path = Path(spec.output_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _sanitize_long_run_fallback_text(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        cleaned = re.sub(r"(?i)scripted(?:[_ -]?rules?)?", "state-based decision", value)
        return re.sub(r"\s+", " ", cleaned).strip()

    @classmethod
    def _sanitize_long_run_fallback_payload(cls, payload: Any) -> Any:
        if isinstance(payload, dict):
            return {
                key: cls._sanitize_long_run_fallback_payload(value)
                for key, value in payload.items()
            }
        if isinstance(payload, list):
            return [cls._sanitize_long_run_fallback_payload(value) for value in payload]
        return cls._sanitize_long_run_fallback_text(payload)

    def _write_long_run_state_driven_fallback_file(
        self,
        spec: SkillRunSpec,
        dept_id: str,
        round_id: int,
        *,
        need_trade: bool,
    ) -> bool:
        policy = self._long_run_experiment_policy()
        fallback_policy = policy.get("state_driven_fallback_policy") or {}
        if not (
            self._long_run_agent_failure_continuation_enabled()
            and fallback_policy.get("enabled")
            and fallback_policy.get("trigger_after_exhausted_agent_attempts", True)
        ):
            return False
        try:
            payload = self.scripted_rule_runner._build_department_action(
                dept_id,
                round_id,
                need_trade,
            )
        except Exception:
            return False
        if not isinstance(payload, list) or not payload:
            return False

        payload = self._sanitize_long_run_fallback_payload(payload)
        prefix = self._sanitize_long_run_fallback_text(
            fallback_policy.get("visible_reason_prefix")
            or "Agent reasoning timed out; invoked the state-driven fallback"
        )
        evidence_reason = (
            f"{prefix}: formed an executable decision from current orders, inventory, cash, capacity, staffing, and fulfillment state."
        )
        for item in payload:
            if not isinstance(item, dict):
                continue
            original_reason = self._sanitize_long_run_fallback_text(
                item.get("action_reason") or ""
            )
            item["action_reason"] = (
                f"{evidence_reason} {original_reason}".strip()
                if original_reason
                else evidence_reason
            )
            action = item.get("action") or {}
            action_param = action.get("action_param")
            if action.get("action_name") == "action_pass" and isinstance(action_param, str):
                action["action_param"] = item["action_reason"]

        path = Path(spec.output_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        MultiTenantUtils._normalize_action_file_if_possible(path, dept_id)
        return MultiTenantUtils._is_valid_action_file(path, dept_id)

    def _write_hr_staffing_fallback_file(
        self,
        spec: SkillRunSpec,
        reason: str,
    ) -> bool:
        state = self._read_json_file(Path(spec.state_path), {}) or {}
        self_state = state.get("self_state") or {}
        department_staffing = self_state.get("department_staffing") or {}
        def safe_int(value: Any, default: int = 0) -> int:
            try:
                if value is None or value == "":
                    return default
                return int(float(value))
            except (TypeError, ValueError):
                return default

        department_aliases = {
            "PROCUREMENT": {"PROCUREMENT", "procurement"},
            "PRODUCTION": {"PRODUCTION", "production"},
            "SALES": {"SALES", "sales"},
            "INVENTORY": {"INVENTORY", "inventory", "warehouse"},
            "HR": {"HR", "hr", "human_resources", "human resources"},
            "FINANCE": {"FINANCE", "finance", "financial management"},
        }
        department_minimums = {
            "PROCUREMENT": 1,
            "PRODUCTION": 2,
            "SALES": 1,
            "INVENTORY": 1,
            "HR": 1,
            "FINANCE": 1,
        }
        department_priority = ["PROCUREMENT", "PRODUCTION", "SALES", "INVENTORY", "HR", "FINANCE"]

        def alias_key(value: Any) -> str:
            return str(value or "").strip().lower()

        def aliases_for(department_code: str) -> set:
            return {alias_key(item) for item in department_aliases.get(department_code, {department_code})}

        def read_staffing(department_code: str) -> Dict[str, Any]:
            aliases = aliases_for(department_code)
            staffing = {
                "count": 0,
                "allocated": 0,
                "available": 0,
                "pending": 0,
                "seen": False,
            }

            def apply_entry(entry: Any) -> None:
                if not isinstance(entry, dict):
                    return
                staffing["seen"] = True
                count = safe_int(entry.get("count"), staffing["count"])
                allocated = safe_int(entry.get("allocated"), staffing["allocated"])
                available = safe_int(entry.get("available"), max(0, count - allocated))
                staffing["count"] = max(staffing["count"], count)
                staffing["allocated"] = max(staffing["allocated"], allocated)
                staffing["available"] = max(staffing["available"], available)
                staffing["pending"] = max(
                    staffing["pending"],
                    safe_int(entry.get("pending_recruits"), staffing["pending"]),
                )

            if isinstance(department_staffing, dict):
                for key, entry in department_staffing.items():
                    if alias_key(key) in aliases:
                        apply_entry(entry)
            for employee in self_state.get("employees") or []:
                if isinstance(employee, dict) and alias_key(employee.get("department")) in aliases:
                    apply_entry(employee)
            pending_map = (
                (self_state.get("recruitment_status") or {}).get("pending_by_department")
                or {}
            )
            if isinstance(pending_map, dict):
                for key, value in pending_map.items():
                    if alias_key(key) in aliases:
                        staffing["pending"] = max(staffing["pending"], safe_int(value))
            return staffing

        target_text = " ".join(
            str(item or "")
            for item in (
                (state.get("agent_decision_brief") or {}).get("target"),
                state.get("target"),
                state.get("evaluation"),
                json.dumps(self_state.get("recent_failures") or {}, ensure_ascii=False),
                json.dumps(self_state.get("department_staffing") or {}, ensure_ascii=False),
            )
        ).lower()
        low_staff_signal = any(
            token in target_text
            for token in (
                "staff shortage",
                "insufficient staff",
                "available_workers",
                "\"available\": 0",
                "\"available\":0",
                "\"count\": 0",
                "\"count\":0",
                "insufficient_staff",
            )
        )
        candidates = []
        for department_code in department_priority:
            staffing = read_staffing(department_code)
            if staffing["pending"] > 0:
                continue
            minimum = department_minimums[department_code]
            if staffing["count"] >= minimum and staffing["available"] >= minimum:
                continue
            aliases = aliases_for(department_code)
            has_department_evidence = any(alias in target_text for alias in aliases)
            if low_staff_signal or has_department_evidence:
                shortage = max(
                    1,
                    minimum - staffing["count"],
                    minimum - staffing["available"],
                )
                candidates.append((department_code, shortage, staffing))
        if not candidates:
            return False

        department_code, shortage, staffing = candidates[0]

        recruit_people = 2
        match = re.search(
            r"(?:at least|recruit|add staff)\s*(\d+)",
            target_text,
        )
        if match:
            recruit_people = int(match.group(1))
        recruit_people = max(1, min(10, max(recruit_people, shortage)))

        payload = [
            {
                "action": {
                    "action_name": "handle_recruitment",
                    "action_param": {
                        "department": department_code,
                        "num_people": recruit_people,
                    },
                },
                "action_reason": (
                    "The HR Skill did not produce a valid file after repeated attempts, while the current HR state shows a real department staffing gap. "
                    f"A fallback recruitment action was written to restore staffing in {department_code}. "
                    f"Current headcount: {staffing.get('count', 0)}; available staff: {staffing.get('available', 0)}. "
                    f"Original failure reason: {reason}"
                ),
                "module_type": "HRManager",
                "executor_id": self.enterprise_spec.enterprise_name,
            }
        ]
        path = Path(spec.output_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True

    def _write_advisory_fallback_file(self, spec: SkillRunSpec, reason: str) -> None:
        payload = {
            "schema_version": "finance_advice.v1",
            "status": "fallback",
            "risk_summary": reason,
            "cash_position": "unknown",
            "budget_constraints": [],
            "recommended_controls": ["action_pass"],
            "evidence_refs": [],
        }
        path = Path(spec.output_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_empty_communication_sidecar(
        self,
        spec: SkillRunSpec,
        reason: str,
    ) -> None:
        path = Path(spec.communication_file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": "enterprise_communication.v1",
                    "messages": [],
                    "reason": reason,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )



    def _build_skill_run_spec(
        self,
        dept: DepartmentSpec,
        round_id: int,
    ) -> SkillRunSpec:
        role = dept.role
        name = dept.name
        skill_name = dept.skill_name
        enterprise_name = self.enterprise_spec.enterprise_name

        skill_args = f"--round_id {round_id} --enterprise_name {enterprise_name}"
        if dept.dept_id == "finance":
            output_file_name = "finance_advice.json"
        else:
            output_file_name = role.lower() + "_action.json"
        state_file_name = role.lower() + ".json"
        dept_dir_name = role.lower()

        enterprise_workspace = self.workspace_dir / "enterprises" / enterprise_name
        analysis_path = str(enterprise_workspace / "analysis.json")
        raw_state_path = (
            enterprise_workspace
            / "department"
            / dept_dir_name
            / f"day{round_id}"
            / state_file_name
        )
        raw_blackboard_path = (
            enterprise_workspace
            / "department"
            / "blackboard"
            / f"day{round_id}"
            / "blackboard.json"
        )
        state_path, blackboard_path, compact_context = (
            self._materialize_long_run_agent_context(
                state_path=raw_state_path,
                blackboard_path=raw_blackboard_path,
                department=dept_dir_name,
                round_id=round_id,
            )
        )
        blackboard_path = materialize_coordination_input(
            self.workspace_dir, enterprise_name, dept_dir_name, round_id, blackboard_path,
        )
        state_path, blackboard_path = materialize_fd_department_inputs(
            self.workspace_dir, enterprise_name, dept_dir_name, round_id, state_path, blackboard_path,
        )
        trade_decision_card_path = None
        if dept_dir_name in {"procurement", "sales"}:
            trade_decision_card_path = str(
                enterprise_workspace
                / "department"
                / dept_dir_name
                / f"day{round_id}"
                / "trade_decision_card.json"
            )
        output_file_path = str(
            enterprise_workspace
            / "department"
            / dept_dir_name
            / f"day{round_id}"
            / output_file_name
        )
        communication_file_path = str(
            enterprise_workspace
            / "department"
            / dept_dir_name
            / f"day{round_id}"
            / f"{dept_dir_name}_communication.json"
        )
        action_template_dir = str(
            self.client_dir / ".claude" / "skills" / skill_name / "actions"
        )

        return SkillRunSpec(
            role=role,
            name=name,
            skill_name=skill_name,
            skill_args=skill_args,
            output_file_name=output_file_name,
            output_file_path=output_file_path,
            analysis_path=analysis_path,
            state_path=str(state_path),
            blackboard_path=str(blackboard_path),
            trade_decision_card_path=trade_decision_card_path,
            communication_file_path=communication_file_path,
            action_template_dir=action_template_dir,
            compact_context=compact_context,
        )

    def _is_advisory_department(self, dept: DepartmentSpec) -> bool:
        return str(dept.dept_id).lower() == "finance"

    def _build_long_horizon_department_response_prompt(self, role: str) -> str:
        if not self._long_run_agent_context_compaction_policy():
            return ""
        evidence_by_role = {
            "Procurement": "compare landed unit cost, inventory coverage, inbound quantity, lot size, and committed cash",
            "Production": "compare incremental conversion cost, order margin, capacity use, material coverage, and inventory turnover",
            "Sales": "compare fulfillable-order margin, pricing room, service risk, and finished-goods inventory",
            "Inventory": "compare safety-stock coverage, holding cost, stockout risk, and turnover",
            "Finance": "compare round and phase profit, cash change, payable pressure, and service risk",
            "HR": "assess staffing changes only when the change creates a verifiable workload or execution bottleneck",
        }
        role_evidence = evidence_by_role.get(
            role,
            "compare cost, cash, inventory, fulfillment, and execution capacity",
        )
        operating_guard_by_role = {
            "Procurement": (
                "prioritize coverage recommendations from calculate_replenishment_quantity/create_replenishment_order; "
                "do not issue another purchase request when inventory and inbound supply already provide coverage, and do not let new single-round commitments consume the future cash buffer"
            ),
            "Production": (
                "schedule production only for confirmed demand and a reasonable safety stock; verify unit contribution when costs rise, "
                "and do not conceal declining profit by increasing output"
            ),
            "Sales": (
                "verify landed unit cost and positive contribution before accepting or responding to an order; "
                "protect margin when external costs rise without committing an infeasible quantity"
            ),
            "Inventory": "target turnover and service coverage; avoid warehouse expansion or inventory growth unsupported by demand",
            "Finance": "check cumulative net profit, phase cash change, outstanding purchase commitments, and unit contribution together",
            "HR": "add staff only when a verifiable workload persistently blocks operations; avoid unsupported fixed-cost expansion",
        }
        operating_guard = operating_guard_by_role.get(
            role,
            "actions must preserve positive unit contribution and control new cash commitments",
        )
        return f"""
E1 external-change response constraints:
- Read policy_context.relevant_policies.external_environment.state and treat analysis.json.external_environment_response as a phase-level hypothesis; live department state remains the basis for action.
- This department should {role_evidence}, distinguishing the effect of external change from ordinary order or backlog pressure.
- During an event or review window, action_reason must cite at least one actual changed factor and one piece of department operating evidence; do not merely repeat the event label.
- If evidence does not support action this round, action_pass is valid, but explain how holding the current course controls profit, cash, inventory, or fulfillment risk.
- Do not mechanically increase purchasing, replenishment, or production when costs rise, and do not stop unconditionally. Explain action direction and quantity using executable current-round data.
- Long-horizon profitable-operation boundary: {operating_guard}.
""".rstrip()

    def _build_launch_prompt(
        self,
        spec: SkillRunSpec,
        round_id: int,
        need_trade: bool = False
    ) -> str:
        trade_prompt = ""
        if need_trade:
            trade_prompt = 'This is the second, trade phase: prioritize proposals in the trade card and choose a valid accept_proposal_order or reject_proposal_order action.'
        extra_input_prompt = ""
        if spec.trade_decision_card_path:
            extra_input_prompt = f'\n3. trade_decision_card.json: {spec.trade_decision_card_path}\nRead this first and decide from action_candidates / review_queue / new_or_updated_proposals / pressure_changes.'
        action_prompt = ""
        if read_policy(self.workspace_dir, round_id):
            action_prompt = (
                f"\nRead the supplied analysis interface once: {spec.analysis_path}. "
                "Use only the supplied role-scoped state, coordination input and your own feedback.\n"
            )
            action_prompt += (
                """Reuse successful reads; request explicit unread ranges only when crucial content was truncated. Do not repeatedly request absent or denied files. action_pass.action_param is a real state-based reason string; retain action_reason, the department module_type and enterprise executor_id. No extra pass-template read is needed. Decide pass from feasibility, not a read failure alone.
Inputs retain all open records and counts plus four recent historical records. Do not read raw department archives or cumulative history. Shared facts come from authorized shared inputs; department policy comes from state.policy_context. Read each input once, decide, Write the complete JSON, then Read it back.
"""
            )
        long_horizon_response_prompt = (
            self._build_long_horizon_department_response_prompt(spec.role)
        )
        compact_context_prompt = ""
        if spec.compact_context:
            compact_context_prompt = '\nE1 long-horizon context constraints:\n- The department state and blackboard in this prompt are authoritative compact projections for the current round. The executor and presentation layer retain the complete archive.\n- Decide directly from agent_decision_brief, policy_context, current open records, recent-history summary, and cross-department summary in the projection.\n- Do not search or paginate through the complete same-round department archive, cumulative order history, or cumulative replenishment history; they contain no additional executable objects.\n- Candidate records provide a bounded decision window through max_open_records. open_records_summary and filtered_non_actionable_summary describe coverage; use history_summary and recent_* only for trend checks, never to invent an order or proposal id.\n'.rstrip()
        if spec.role in {"Production", "Sales", "Procurement", "HR"}:
            action_prompt += f'\n\n{spec.role} department constraints:\n- Do not ask the user for confirmation or finish with Markdown/code fences.\n- Once state and blackboard contain sufficient facts, immediately write {spec.output_file_name}; do not repeatedly read analysis or history.\n- Read the initial agent_decision_brief at offset=0 first; do not page through large tails merely to find simulation_context, herding_history or blackboard content.\n- Once final JSON is ready, invoke Write; printing JSON is not completion.\n'.rstrip()
        if spec.role == "Production":
            action_prompt += '\n- In herding_market, empty raw_materials is valid configuration; do not perform lengthy recipe-completion reasoning.\n- In herding_market, choose only create_production_plan or action_pass. Do not read production action templates unless an existing plan_id must be operated on.\n'.rstrip()
            runtime_contract = self._runtime_contract()
            simulation_config = (
                (runtime_contract.get("scenario_config") or {}).get("simulation")
                or {}
            )
            cobweb_config = simulation_config.get("cobweb_config") or {}
            if (
                simulation_config.get("market_demand_mode") == "cobweb"
                or cobweb_config.get("enabled") is True
            ):
                runtime_injection = (
                    (runtime_contract.get("scenario_config") or {}).get(
                        "runtime_injection"
                    )
                    or {}
                )
                profit_objective_enabled = bool(
                    (runtime_injection.get("profit_objective_policy") or {}).get(
                        "enabled"
                    )
                )
                action_prompt += '\n- In cobweb mode, agent_decision_brief.cobweb_signal at the top of production.json is the authoritative price-signal summary for this round. Check only the following policy_context and self_state feasibility fields.\n- simulation_context.cobweb_history is already a compact recent window. Do not paginate in search of full history; complete history is for experiment archiving, not a prerequisite for current action.\n- Once price direction, actual product_id, available capacity, materials, and cash support an action, write production_action.json immediately.\n'.rstrip()
                if profit_objective_enabled:
                    action_prompt += '\n- This is profit-objective cobweb mode: price direction is market evidence, not an instruction to expand or contract production. Determine quantity according to objective_mode using profit, cash, fulfillment, inventory, and adjustment risk.\n'.rstrip()
        if spec.role == "HR":
            if (read_policy(self.workspace_dir, round_id).get("agent_prompt_policy_version") == FD4_AGENT_PROMPT_VERSION
                    or fd2_agent_policy(read_policy(self.workspace_dir, round_id))):
                action_prompt += """
- Analyst targets are advisory. Correct them using current staffing, completed hiring and actual work; do not hire HR or other departments solely to satisfy a target.
- Recruitment does not require existing HR staff. Production tasks allocate their own workers. decision_check is optional audit evidence; its absence or warnings do not prevent legal actions.
""".rstrip()
            elif read_policy(self.workspace_dir, round_id).get("agent_prompt_policy_version") in FD4_ROLE_CONTRACT_VERSIONS:
                action_prompt += """
- Hiring requires both department-specific work and a net staffing gap; zero headcount, abundant cash or Analyst advice is not workload evidence. Derive headcount rather than defaulting to two.
- With allocated=0 and available>0, legal work actions allocate staff; do not invent allocation or transfer actions. Explain coverage before choosing pass.
- handle_recruitment requires top-level decision_check citing current state/coordination; the supplied blackboard.json is the coordination input.
""".rstrip()
            else:
                action_prompt += """
- During diagnosis, prioritize handle_recruitment when actual workload, zero available staff, high utilization or staffing failures establish a hard gap not covered by pending hires. Do not pass only because a generic threshold is unmet.
- Use the actually blocked department: PROCUREMENT or PRODUCTION as appropriate. Use a state-supported headcount or the conservative legacy default of two.
- If no legal hiring action exists, output standard action_pass without rereading its template; do not use the old top-level pass_reason format.
""".rstrip()
        if spec.role == "Sales":
            if self._cobweb_profit_objective_contract():
                action_prompt += '\n- This is Cobweb C3 profit-objective mode. unit_price, total_amount, quantity, and deadline in agent_decision_brief.available_orders are order-economics evidence; do not accept an order solely because it is available.\n- If finished-goods inventory can fulfill an order, a positive-price order can contribute incremental revenue. If accepting it requires new production, compare unit price, estimated incremental production cost, cash, delivery risk, and service pressure.\n- Do not accept a residual order with deadline_status=expired. If unexpired orders exist, process the one with the strongest economics and timing; otherwise use reject_order to clear the expired residual.\n- Choose accept_order, reject_order, or action_pass, and explain profit or marginal contribution, inventory coverage, and service effect in action_reason. Price direction is not an instruction to accept an order.\n- agent_decision_brief, compact self_state.sales_orders, and sales_metrics are sufficient for this round. Do not paginate through completed-order or demand_backlog history.\n'.rstrip()
            else:
                action_prompt += '\n- If market_growth_signal.recommendation_mode=evidence_only, decide whether to develop_market from market count, order inflow, uncommitted inventory, backlog, development cost, and duration. Do not assume the system has recommended an action. In other modes, prioritize the minimum effective market-development action only when should_develop_market=true.\n- Otherwise, if agent_decision_brief.available_orders is nonempty, select one or more orders by price, quantity, offer validity, delivery deadline, expected contribution, and cumulative fulfillment coverage. Process beneficial fulfillable orders and reject orders that clearly lose money, are dominated by another order, or create overcommitment. Do not assume only one order can be accepted per round.\n'.rstrip()
        if spec.role in {"Sales", "Procurement"} and spec.trade_decision_card_path:
            action_prompt += '\n- Select a proposal only from trade_decision_card.action_candidates or an actionable item in review_queue. filtered_proposals is audit-only and must not be accepted or rejected.\n- If action_candidates is empty, prioritize action_pass. Do not process an expired, superseded, fulfilled, rejected, or already-responded proposal.\n'.rstrip()
        single_case_department_prompt = self._build_single_enterprise_department_decision_prompt(spec.role)
        if spec.role == "Finance":
            history_projection_path = self._history_projection_path(round_id)
            history_projection_day = self._history_projection_day(round_id)
            action_prompt += f'\nFinance advisory constraints:\n- Produce financial-diagnosis sidecar JSON, not executable accounting actions.\n- Final output must be {spec.output_file_path}, not finance_action.json.\n- A sealed historical projection, day {history_projection_day}, may be read at {history_projection_path}\n- If absent, do not read future projections or invent trends.\n- recommended_controls contains prose advice only, not action JSON or commands that other departments must execute.\n'.rstrip()
        communication_prompt = (
            self._build_communication_prompt(spec, round_id)
            if not need_trade
            else ""
        )
        return f'\nCurrent simulation round: {round_id}.\nYou manage {self.enterprise_spec.enterprise_name} / {spec.role}.\nInvoke this exact Skill call immediately; do not omit or change args:\nSkill(skill="{spec.skill_name}", args="{spec.skill_args}")\n{trade_prompt}\n\nActual paths:\n1. Department state: {spec.state_path}\n2. blackboard.json: {spec.blackboard_path}\n{extra_input_prompt}\n{(4 if not spec.trade_decision_card_path else 5)}. analysis.json（{('Required interface this round' if read_policy(self.workspace_dir, round_id) else 'Low-frequency strategic reference, used only when long-term targets or cross-department context are needed')}）: {spec.analysis_path}\n{(5 if not spec.trade_decision_card_path else 6)}. Final output: {spec.output_file_path}\n{(6 if not spec.trade_decision_card_path else 7)}. Action templates: {spec.action_template_dir}\n\nUse these absolute paths rather than symbolic or old workspace_multi examples. Read templates as <template directory>/<action_name>.json.\nAuthority order: policy_context and current state / blackboard / trade_decision_card override analysis.json and general prose.\nIf external_environment_evolution is active, inspect external_environment current_turn, triggered_events, active_factors and actual_values. Adapt to activated changes only; do not infer undisclosed future shocks.\n{long_horizon_response_prompt}\nSingle-enterprise diagnostic rules:\n- single_enterprise_diagnostic_policy.enabled identifies this diagnostic mode only. Do not infer internal experiment IDs, preset answers, department priority or the intended conflict.\n- Decide actions/pass from real department state, templates and feasibility rather than ordinary expansion goals that contradict current constraints.\n- Do not cite department identity or priority as the action/pass reason.\n- Sales must assess inventory, delivery deadlines, capacity, materials, cash and existing commitments instead of accepting every available order.\n- Procurement and Production must assess actual demand and net gaps rather than treating recommended targets as execution commands.\n{single_case_department_prompt}\n{compact_context_prompt}\n{action_prompt}\n{fd4_decision_guidance(self.workspace_dir, round_id, spec.role)}{fd2_decision_guidance(self.workspace_dir, round_id, spec.role)}\n{communication_prompt}\n\nAfter invoking the Skill, continue until you successfully write {spec.output_file_name}.\nPrinting explanations or JSON does not replace writing the file.\n'.strip()

    def _build_same_session_resume_prompt(
        self,
        spec: SkillRunSpec,
        round_id: int,
        reason: str,
        need_trade: bool = False,
    ) -> str:
        extra_input_prompt = ""
        if spec.trade_decision_card_path:
            extra_input_prompt = f'\n3. trade_decision_card.json: {spec.trade_decision_card_path}\nRead if unread; otherwise reuse review_queue / pressure_changes to complete the trade decision.'
        communication_prompt = (
            self._build_communication_prompt(spec, round_id)
            if not need_trade
            else ""
        )
        single_case_department_prompt = self._build_single_enterprise_department_decision_prompt(spec.role)
        long_horizon_response_prompt = (
            self._build_long_horizon_department_response_prompt(spec.role)
        )
        return f'\nCurrent simulation round: {round_id}.\nYou still manage {self.enterprise_spec.enterprise_name} / {spec.role}.\nThe previous attempt in this session did not finish the target file.\nFailure: {reason}\n\nDo not restart lengthy analysis or print Markdown fences. Reuse completed reads and decisions in this session and finish the missing steps.\nRetain this exact Skill call contract:\nSkill(skill="{spec.skill_name}", args="{spec.skill_args}")\n\nActual paths:\n1. Department state: {spec.state_path}\n2. blackboard.json: {spec.blackboard_path}\n{extra_input_prompt}\n{(4 if not spec.trade_decision_card_path else 5)}. analysis.json（{('Required interface this round' if read_policy(self.workspace_dir, round_id) else 'Low-frequency strategic reference, used only when long-term targets or cross-department context are needed')}）: {spec.analysis_path}\n{(5 if not spec.trade_decision_card_path else 6)}. Final output: {spec.output_file_path}\n{(6 if not spec.trade_decision_card_path else 7)}. Action templates: {spec.action_template_dir}\n\nUse these actual absolute paths instead of symbolic or old workspace_multi examples. Read templates as <template directory>/<action_name>.json.\nAuthority order: policy_context and current state / blackboard / trade_decision_card override analysis.json and general prose.\n{long_horizon_response_prompt}\nSingle-enterprise diagnostic rules remain active: the policy flag identifies the mode, not a preset answer. Decide actions/pass using current state, templates and feasibility; do not cite department priority or internal experiment labels.\n{single_case_department_prompt}\n{fd4_decision_guidance(self.workspace_dir, round_id, spec.role)}{fd2_decision_guidance(self.workspace_dir, round_id, spec.role)}\n{communication_prompt}\n\nPerform only operations required to finish {spec.output_file_name}:\n1. If final JSON is ready, Write it directly to {spec.output_file_name}\n2. Immediately Read back {spec.output_file_name}\n3. If the department brief or crucial order/production facts were read, stop paging through large files and write.\n4. If required reads remain, read only the crucial initial range with offset=0, then write.\n5. Do not substitute explanations or printed JSON for file output.\n'.strip()

    def _build_business_repair_prompt(
        self,
        spec: SkillRunSpec,
        round_id: int,
        error_file: str,
        need_trade: bool = False,
    ) -> str:
        extra_input_prompt = ""
        if spec.trade_decision_card_path:
            extra_input_prompt = f'\n3. trade_decision_card.json: {spec.trade_decision_card_path}\nDuring repair, use review_queue / recommended_action and do not reprocess historical proposals.'
        communication_prompt = (
            self._build_communication_prompt(spec, round_id)
            if not need_trade
            else ""
        )
        single_case_department_prompt = self._build_single_enterprise_department_decision_prompt(spec.role)
        long_horizon_response_prompt = (
            self._build_long_horizon_department_response_prompt(spec.role)
        )
        return f'\nCurrent simulation round: {round_id}.\nYou still manage {self.enterprise_spec.enterprise_name} / {spec.role}.\nThe previous attempt produced {spec.output_file_name} but execution failed.\nError file: /{error_file}\n\nIn this session, repair the specific error and regenerate {spec.output_file_name}.\nRetain this exact Skill call contract:\nSkill(skill="{spec.skill_name}", args="{spec.skill_args}")\n\nActual paths:\n1. Department state: {spec.state_path}\n2. blackboard.json: {spec.blackboard_path}\n{extra_input_prompt}\n{(4 if not spec.trade_decision_card_path else 5)}. analysis.json（{('Required interface this round' if read_policy(self.workspace_dir, round_id) else 'Low-frequency strategic reference, used only when long-term targets or cross-department context are needed')}）: {spec.analysis_path}\n{(5 if not spec.trade_decision_card_path else 6)}. Final output: {spec.output_file_path}\n{(6 if not spec.trade_decision_card_path else 7)}. Action templates: {spec.action_template_dir}\n\nUse these actual absolute paths instead of symbolic or old workspace_multi examples. Read templates as <template directory>/<action_name>.json.\nAuthority order: policy_context and current state / blackboard / trade_decision_card override analysis.json and general prose.\n{long_horizon_response_prompt}\nSingle-enterprise diagnostic rules remain active. Do not override current constraints with ordinary expansion goals or justify action/pass by department priority or internal labels.\n{single_case_department_prompt}\n{fd4_decision_guidance(self.workspace_dir, round_id, spec.role)}{fd2_decision_guidance(self.workspace_dir, round_id, spec.role)}\n{communication_prompt}\n\nCompletion requires:\n1. Write the corrected {spec.output_file_name}\n2. Immediately Read it back and validate.\n3. Printing explanations or JSON is not file delivery.\n'.strip()

    # =========================
    # helper: options / session
    # =========================

    def _is_recoverable_session_error(self, error: Exception) -> bool:
        text = str(error).lower()
        return "session" in text or "resume" in text

    def _bind_session_to_options(self, options: Any, session_id: Optional[str]) -> Any:
        """
        Bind an existing session to resume it.

        Claude CLI --session-id is not resum; if entered simultaneously in the current SDK
        - session-id without -- fork-session triggers CLI parameter error. So it's a common use here.
        Resume fields, and cleans up historical compatible fields to avoid creating an error parameter combination.
                """
        if not session_id:
            return options

        # dict type options
        if isinstance(options, dict):
            options["resume"] = session_id
            options.pop("session_id", None)
            options.pop("resume_session_id", None)
            options.pop("conversation_id", None)
            return options

        # Object type options
        if hasattr(options, "resume"):
            try:
                setattr(options, "resume", session_id)
            except Exception:
                pass

        for attr in ("resume_session_id", "session_id", "conversation_id"):
            if hasattr(options, attr):
                try:
                    setattr(options, attr, None)
                except Exception:
                    pass

        # Some SDK options may support model_copy/update
        if hasattr(options, "model_copy"):
            try:
                return options.model_copy(
                    update={
                        "resume": session_id,
                        "session_id": None,
                        "resume_session_id": None,
                        "conversation_id": None,
                    }
                )
            except Exception:
                pass

        return options

    # =========================
    # helper: transcript inspect
    # =========================

    def _extract_last_json_block(self, text: str) -> Optional[str]:
        """
        Priority is given to extracting the last ``json ... ` `coding block.
                """
        blocks = self._extract_json_blocks(text)
        return blocks[-1] if blocks else None

    def _inspect_messages(
        self,
        messages: List[dict],
        spec: SkillRunSpec,
        session_messages: Optional[List[dict]] = None,
    ) -> RunInspection:
        text = "\n".join(m["content"] for m in messages)

        session_text = "\n".join(m["content"] for m in (session_messages or []) + messages)
        skill_called = f"name='Skill'" in session_text and f"'skill': '{spec.skill_name}'" in session_text
        skill_args_ok = spec.skill_args in session_text

        read_analysis = spec.analysis_path in text or Path(spec.analysis_path).name in text
        read_state = spec.state_path in text or Path(spec.state_path).name in text
        read_blackboard = spec.blackboard_path in text or Path(spec.blackboard_path).name in text
        read_trade_decision_card = (
            not spec.trade_decision_card_path
            or spec.trade_decision_card_path in text
            or Path(spec.trade_decision_card_path).name in text
        )
        fd_policy = read_policy(self.workspace_dir, getattr(self, "_active_department_round_id", None))
        if fd_policy and spec.role != "Analyst":
            # Tool admission evidence avoids accepting a path mentioned only in prose.
            match = re.search(r"day(\d+)", spec.output_file_path)
            if match:
                admitted = accessed_inputs(self.workspace_dir, self.enterprise_spec.enterprise_name,
                                           spec.role, int(match.group(1)))
                read_analysis = str(Path(spec.analysis_path).resolve()) in admitted
                read_state = str(Path(spec.state_path).resolve()) in admitted
                read_blackboard = str(Path(spec.blackboard_path).resolve()) in admitted
                read_trade_decision_card = not spec.trade_decision_card_path or str(Path(spec.trade_decision_card_path).resolve()) in admitted

        write_called = "name='Write'" in text or 'name="Write"' in text
        write_target_ok = spec.output_file_path in text or spec.output_file_name in text


        read_output_called = (
            ("name='Read'" in text or 'name="Read"' in text)
            and spec.output_file_path in text
        )

        json_block = self._extract_last_json_block(text)
        has_json_block = json_block is not None


        if not skill_called:
            failure_type = "skill_not_called"
        elif not skill_args_ok:
            failure_type = "skill_args_missing_or_wrong"
        elif not read_state or not read_blackboard or not read_trade_decision_card or (fd_policy and not read_analysis):
            failure_type = "required_reads_incomplete"
        elif has_json_block and not write_called:
            failure_type = "json_printed_but_not_written"
        elif write_called and not write_target_ok:
            failure_type = "write_wrong_target"
        elif write_called and not read_output_called:
            failure_type = "written_but_not_readback"
        elif not write_called:
            failure_type = "stopped_before_write"
        else:
            failure_type = "unknown"

        return RunInspection(
            skill_called=skill_called,
            skill_args_ok=skill_args_ok,
            read_analysis=read_analysis,
            read_state=read_state,
            read_blackboard=read_blackboard,
            read_trade_decision_card=read_trade_decision_card,
            write_called=write_called,
            write_target_ok=write_target_ok,
            read_output_called=read_output_called,
            has_json_block=has_json_block,
            json_block=json_block,
            failure_type=failure_type,
        )

    # =========================
    # helper: attempt logging
    # =========================

    def _abort_fatal_model_failure(self, error, options, role, round_id, attempt_idx, messages):
        fatal = error if isinstance(error, FatalModelError) else classify_model_failure(str(error))
        if fatal is None:
            return
        if not isinstance(error, FatalModelError):
            fatal = attach_model_failure(fatal, workspace=self.workspace_dir, options=options,
                                         enterprise_id=self.enterprise_spec.enterprise_id,
                                         role=role, round_id=round_id)
        self.session_registry.clear(self.enterprise_spec.enterprise_id, role)
        self._dump_attempt_messages(self.enterprise_spec.enterprise_name, role, round_id, attempt_idx,
                                    [*messages, {"role": role, "content": str(fatal)}])
        raise fatal

    def _dump_attempt_messages(
        self,
        enterprise_name: str,
        role: str,
        round_id: int,
        attempt_idx: int,
        messages: List[dict],
    ) -> None:
        messages_dir = (
            self.workspace_dir
            / "debug_messages"
            / enterprise_name
        )
        messages_dir.mkdir(parents=True, exist_ok=True)
        messages_file = messages_dir / f"{role}_action_messages_{attempt_idx}-{round_id}.json"
        messages_file.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # =========================
    # helper: prompt routing by failure type
    # =========================

    def _next_prompt_by_failure(
        self,
        spec: SkillRunSpec,
        round_id: int,
        inspection: RunInspection,
        *,
        error_file: Optional[str] = None,
        need_trade: bool = False,
    ) -> str:
        if error_file:
            return self._build_business_repair_prompt(
                spec,
                round_id,
                error_file,
                need_trade=need_trade,
            )

        mapping = {
            "skill_not_called": "The Skill tool was not actually called in the previous turn. Call it now in the exact required format and complete the file in this session.",
            "skill_args_missing_or_wrong": f"The prior Skill call omitted or changed args. Use args=\"{spec.skill_args}\" exactly.",
            "required_reads_incomplete": "Required core files were not fully read. Complete only the necessary reads and write the file immediately without an explanation.",
            "json_printed_but_not_written": f"The final JSON was printed but Write was not called. Do not repeat the analysis; write the established JSON to {spec.output_file_name} and read it back.",
            "write_wrong_target": f"Write was called with the wrong target. Write the final JSON to {spec.output_file_name} and read it back.",
            "written_but_not_readback": f"{spec.output_file_name} was written but not read back for validation. Read the target file now and complete validation.",
            "stopped_before_write": f"The previous turn stopped before writing. Do not repeat the analysis; complete Write and readback for {spec.output_file_name}.",
            "unknown": f"The previous turn did not complete {spec.output_file_name}. Do not restart; reuse the existing result and complete Write and readback.",
        }
        return self._build_same_session_resume_prompt(
            spec,
            round_id,
            mapping.get(inspection.failure_type, mapping["unknown"]),
            need_trade=need_trade,
        )

    # =========================
    # main: refactored controller
    # =========================

    async def run_department(
        self,
        dept: DepartmentSpec,
        round_id: int,
        need_trade: bool = False
    ) -> List[dict]:
        role = dept.role
        dept_type = dept.type

        global_lock_manager = GlobalDepartmentLockManager()

        hybrid_messages = self.hybrid_replay_runner.run_department(
            dept,
            round_id,
            need_trade=need_trade,
        )
        if hybrid_messages is not None:
            return hybrid_messages

        if self.scripted_rule_runner.department_enabled(dept.dept_id):
            messages = self.scripted_rule_runner.write_department_action(
                dept.dept_id,
                round_id,
                need_trade=need_trade,
            )
            if dept.dept_id != "finance":
                MultiTenantUtils.execute_enterprise_dept_action(
                    round_id,
                    0,
                    dept.dept_id,
                    self.enterprise_spec.enterprise_name,
                )
            return messages

        if dept_type == "Auto":
            MultiTenantUtils.generate_dept_auto_action(
                dept.dept_id,
                self.enterprise_spec.enterprise_name,
                round_id,
            )
            return []

        if dept_type == "Scripted":
            messages = self.scripted_rule_runner.write_department_action(
                dept.dept_id,
                round_id,
                need_trade=need_trade,
            )
            MultiTenantUtils.execute_enterprise_dept_action(
                round_id,
                0,
                dept.dept_id,
                self.enterprise_spec.enterprise_name,
            )
            return messages

        spec = self._build_skill_run_spec(dept, round_id)
        advisory_only = self._is_advisory_department(dept)
        self._remove_stale_output_file(spec.output_file_path)
        if self._communication_enabled() and not need_trade:
            self._remove_stale_output_file(spec.communication_file_path)
        phase = "trade" if need_trade else "decision"
        context_audit = self._build_context_audit(spec, dept.dept_id, phase, round_id)
        strict_context_failure = self._strict_context_failure(context_audit)

        success = False
        retry_time = 0
        all_messages: List[dict] = []
        prompt = self._build_launch_prompt(spec, round_id, need_trade)
        skill_profile = self._skill_profile()
        context_validation_mode = str(
            skill_profile.get("context_validation") or "off"
        )
        if context_validation_mode in {"warn", "strict"} and not read_policy(self.workspace_dir, round_id):
            prompt += (
                f'\n\nStructured context-boundary audit file: {context_audit.get('artifact')}. Do not cite scenario fields marked disallowed.'
            )
        run_started_at = time.time()
        execution_result: Optional[Dict[str, Any]] = None
        fd_failure_reason = "no valid Agent output"

        if strict_context_failure:
            if read_policy(self.workspace_dir, round_id):
                raise RuntimeError(f"FD context validation failed: {strict_context_failure}")
            fallback_reason = (
                "strict context validation blocked Agent execution: "
                f"{strict_context_failure}"
            )
            if advisory_only:
                self._write_advisory_fallback_file(spec, fallback_reason)
                execution_result = {
                    "status": "success",
                    "advisory_only": True,
                    "reason": fallback_reason,
                }
            else:
                self._write_action_pass_file(spec, dept.dept_id, fallback_reason)
            if self._communication_enabled() and not need_trade:
                self._write_empty_communication_sidecar(
                    spec,
                    fallback_reason,
                )
            if not advisory_only:
                execution_result = MultiTenantUtils.execute_enterprise_dept_action(
                    round_id=round_id,
                    retry_time=1,
                    dept=dept.dept_id,
                    enterprise_name=self.enterprise_spec.enterprise_name,
                )
            self._write_skill_execution_audit(
                role=role,
                department=dept.dept_id,
                round_id=round_id,
                output_file_path=spec.output_file_path,
                all_messages=all_messages,
                attempts=0,
                success=execution_result.get("status") == "success",
                fallback_used=True,
                elapsed_seconds=time.time() - run_started_at,
                context_audit=context_audit,
                execution_result=execution_result,
                phase=phase,
            )
            return all_messages


        resume_session_id: Optional[str] = None

        max_retry = max(1, int(skill_profile.get("max_retries", 3) or 0))
        default_timeout = max(1, int(dept.timeout or 120))
        role_completion_enabled = fd_completion_enabled(read_policy(self.workspace_dir, round_id), role, round_id)
        session_messages: List[dict] = []

        while not success and retry_time < max_retry:
            # await global_lock_manager.acquire_department_lock(role)
            time_start = time.time()

            attempt_messages: List[dict] = []
            session_id_found: str = resume_session_id or ""
            recover_from_session_error = False
            timeout = self._long_run_agent_timeout_seconds(
                role=role,
                round_id=round_id,
                retry_index=retry_time,
                default_seconds=default_timeout,
            )

            print(f"START-{retry_time} {self.enterprise_spec.enterprise_id} {dept.name}")


            options = self.build_options(role)
            input_attempt_state = {}
            if not resume_session_id:
                session_messages = []
            options = configure_input_access(options, spec, self.workspace_dir, self.client_dir,
                                             self.enterprise_spec.enterprise_name, round_id,
                                             attempt_state=input_attempt_state,
                                             output_validator=(
                                                 lambda state=input_attempt_state: self._department_completion_error(spec, round_id, state)
                                             ) if role_completion_enabled else None,
                                             output_warning_provider=(lambda: self._department_decision_warnings(spec, round_id))
                                             if read_policy(self.workspace_dir, round_id).get("agent_prompt_policy_version") == FD4_AGENT_PROMPT_VERSION else None)
            options = self._bind_session_to_options(options, resume_session_id)

            try:
                async def stream() -> None:
                    nonlocal session_id_found

                    async with aclosing(query(
                        prompt=input_access_prompt(prompt, self.workspace_dir, round_id),
                        options=options,
                    )) as response_stream:
                        async for message in response_stream:
                            record = {
                                "enterprise_id": self.enterprise_spec.enterprise_id,
                                "enterprise_name": self.enterprise_spec.enterprise_name,
                                "role": role,
                                "content": str(message),
                            }
                            attempt_messages.append(record)
                            all_messages.append(record)

                            check_model_message(message, workspace=self.workspace_dir, options=options,
                                                enterprise_id=self.enterprise_spec.enterprise_id,
                                                role=role, round_id=round_id)

                            if input_attempt_state.get("loop_reason"):
                                raise FDInputLoopError(input_attempt_state["loop_reason"])

                            msg_session_id = getattr(message, "data", {}).get("session_id")
                            if msg_session_id:
                                session_id_found = msg_session_id

                await asyncio.wait_for(stream(), timeout=timeout)

            except asyncio.TimeoutError:
                attempt_messages.append({
                    "enterprise_id": self.enterprise_spec.enterprise_id,
                    "enterprise_name": self.enterprise_spec.enterprise_name,
                    "role": role,
                    "content": (
                        f"[Timeout] role={role}, retry={retry_time}, "
                        f"timeout_seconds={timeout}"
                    ),
                })
                all_messages.extend(attempt_messages[-1:])
                self.session_registry.clear(self.enterprise_spec.enterprise_id, role)
                resume_session_id = ""
                session_id_found = ""
                timeout_output_path = Path(spec.output_file_path)
                if timeout_output_path.exists() and not advisory_only:
                    MultiTenantUtils._normalize_action_file_if_possible(
                        timeout_output_path,
                        dept.dept_id,
                        allow_legacy_wrapper=bool(read_policy(self.workspace_dir, round_id)),
                    )
                timeout_output_valid = (
                    MultiTenantUtils._is_valid_json_file(timeout_output_path)
                    if advisory_only and timeout_output_path.exists()
                    else MultiTenantUtils._is_valid_action_file(
                        timeout_output_path,
                        dept.dept_id,
                    )
                    if timeout_output_path.exists()
                    else False
                )
                # The CLI can finish Write just before its result stream reaches the
                # timeout. Preserve and execute that valid action instead of discarding
                # it and paying for another full Agent attempt.
                recover_from_session_error = not timeout_output_valid
            except (ProcessError, FDInputLoopError) as e:
                self._abort_fatal_model_failure(e, options, role, round_id, retry_time + 1, attempt_messages)
                error_label = "InputAccessLoop" if isinstance(e, FDInputLoopError) else "ProcessError"
                error_record = {
                    "enterprise_id": self.enterprise_spec.enterprise_id,
                    "enterprise_name": self.enterprise_spec.enterprise_name,
                    "role": role,
                    "content": f"[{error_label}] role={role}, retry={retry_time}, error={e}",
                }
                attempt_messages.append(error_record)
                all_messages.append(error_record)
                self.session_registry.clear(self.enterprise_spec.enterprise_id, role)
                resume_session_id = ""
                session_id_found = ""
                recover_from_session_error = True
            except Exception as e:
                self._abort_fatal_model_failure(e, options, role, round_id, retry_time + 1, attempt_messages)
                error_record = {
                    "enterprise_id": self.enterprise_spec.enterprise_id,
                    "enterprise_name": self.enterprise_spec.enterprise_name,
                    "role": role,
                    "content": (
                        f"[UnexpectedQueryError] role={role}, retry={retry_time}, "
                        f"error_type={type(e).__name__}, error={e}"
                    ),
                }
                attempt_messages.append(error_record)
                all_messages.append(error_record)
                self.session_registry.clear(self.enterprise_spec.enterprise_id, role)
                resume_session_id = ""
                session_id_found = ""
                recover_from_session_error = True

            finally:
                pass
                # global_lock_manager.release_department_lock(role)

            if recover_from_session_error:
                fd_failure_reason = attempt_messages[-1]["content"]
                self._dump_attempt_messages(
                    self.enterprise_spec.enterprise_name,
                    role,
                    round_id,
                    retry_time + 1,
                    attempt_messages,
                )
                prompt = self._build_launch_prompt(spec, round_id, need_trade)
                prompt += fd_input_retry_context(spec, input_attempt_state)
                retry_time += 1
                time_end = time.time()
                print(
                    f'END-{retry_time} {self.enterprise_spec.enterprise_id} {dept.name} elapsed: {time_end - time_start}'
                )
                continue


            if session_id_found:
                if resume_session_id and resume_session_id != session_id_found:
                    session_messages = []
                resume_session_id = session_id_found


            if advisory_only:
                has_file = Path(spec.output_file_path).exists()
            else:
                has_file = MultiTenantUtils.file_check_department(
                    self.enterprise_spec.enterprise_name,
                    dept.dept_id,
                    round_id,
                )
            if not has_file and not role_completion_enabled:
                self._recover_output_file_from_messages(attempt_messages, spec.output_file_path)
                has_file = (
                    Path(spec.output_file_path).exists()
                    if advisory_only
                    else MultiTenantUtils.file_check_department(
                        self.enterprise_spec.enterprise_name,
                        dept.dept_id,
                        round_id,
                    )
                )
            if has_file and not advisory_only:
                MultiTenantUtils._normalize_action_file_if_possible(
                    Path(spec.output_file_path),
                    dept.dept_id,
                    allow_legacy_wrapper=bool(read_policy(self.workspace_dir, round_id)),
                )
            valid_file = (
                MultiTenantUtils._is_valid_json_file(Path(spec.output_file_path))
                if advisory_only
                else MultiTenantUtils._is_valid_action_file(Path(spec.output_file_path), dept.dept_id)
                if has_file
                else False
            )

            if valid_file and read_policy(self.workspace_dir, round_id):
                admitted = accessed_inputs(self.workspace_dir, self.enterprise_spec.enterprise_name,
                                           role, round_id)
                required = [spec.state_path, spec.blackboard_path, spec.analysis_path]
                if spec.trade_decision_card_path:
                    required.append(spec.trade_decision_card_path)
                missing = [value for value in required if str(Path(value).resolve()) not in admitted]
                if missing:
                    valid_file = False
                    fd_failure_reason = "Required authorized reads missing: " + ", ".join(missing)
                    if not admitted:
                        fd_failure_reason += (
                            "; no input access evidence was recorded. Check SDK hook initialization "
                            "and fd_audit access logs; output is not eligible for execution."
                        )
                    failure_record = {
                        "enterprise_id": self.enterprise_spec.enterprise_id,
                        "enterprise_name": self.enterprise_spec.enterprise_name,
                        "role": role, "content": fd_failure_reason,
                    }
                    attempt_messages.append(failure_record)
                    all_messages.append(failure_record)
                else:
                    fd_failure_reason = "no valid Agent output"
            elif not valid_file:
                fd_failure_reason = (
                    self._department_output_failure_reason(spec.output_file_path, dept.dept_id)
                    if not advisory_only
                    else "Agent advisory output is missing or invalid: " + spec.output_file_path
                )

            if valid_file and not advisory_only:
                decision_errors = self._department_decision_errors(spec, round_id)
                if decision_errors:
                    valid_file = False
                    fd_failure_reason = "Decision consistency: " + "; ".join(decision_errors)
                    failure_record = {
                        "enterprise_id": self.enterprise_spec.enterprise_id,
                        "enterprise_name": self.enterprise_spec.enterprise_name,
                        "role": role, "content": "[DecisionConsistencyFailed] " + fd_failure_reason,
                    }
                    attempt_messages.append(failure_record)
                    all_messages.append(failure_record)

            if valid_file and role_completion_enabled:
                completion_error = self._department_completion_error(spec, round_id, input_attempt_state)
                if completion_error:
                    valid_file = False
                    fd_failure_reason = completion_error

            if has_file and valid_file:
                if advisory_only:
                    success = True
                    execution_result = {
                        "status": "success",
                        "advisory_only": True,
                        "output_file": spec.output_file_path,
                    }
                else:
                    result = MultiTenantUtils.execute_enterprise_dept_action(
                        round_id=round_id,
                        retry_time=retry_time + 1,
                        dept=dept.dept_id,
                        enterprise_name=self.enterprise_spec.enterprise_name,
                    )
                    execution_result = result

                    if result.get("status") == "success":
                        success = True
                    else:
                        fd_failure_reason = "Business execution failed: " + str(result)
                        error_file = result.get("file_path", "")
                        prompt = self._next_prompt_by_failure(
                            spec,
                            round_id,
                            RunInspection(
                                skill_called=True,
                                skill_args_ok=True,
                                read_analysis=True,
                                read_state=True,
                                read_blackboard=True,
                                read_trade_decision_card=True,
                                write_called=True,
                                write_target_ok=True,
                                read_output_called=True,
                                has_json_block=False,
                                json_block=None,
                                failure_type="unknown",
                            ),
                            error_file=error_file,
                            need_trade=need_trade,
                        )
                        self._remove_stale_output_file(spec.output_file_path)
            else:

                inspection = self._inspect_messages(
                    attempt_messages, spec,
                    session_messages=session_messages if role_completion_enabled else None,
                )
                prompt = self._next_prompt_by_failure(
                    spec,
                    round_id,
                    inspection,
                    need_trade=need_trade,
                )
                if read_policy(self.workspace_dir, round_id):
                    prompt += "\n\nController validation failure: " + fd_failure_reason
                if has_file:
                    self._remove_stale_output_file(spec.output_file_path)
                    if read_policy(self.workspace_dir, round_id):
                        prompt += (
                            """
The controller deleted the invalid candidate. Correct and Write it again; do not just read back or claim the previous write completed. Use a nonempty flat action array whose entries contain action, action_reason, module_type and executor_id."""
                        )


                self._dump_attempt_messages(
                    self.enterprise_spec.enterprise_name,
                    role,
                    round_id,
                    retry_time + 1,
                    attempt_messages,
                )

            session_messages.extend(attempt_messages)
            retry_time += 1
            time_end = time.time()
            print(
                f'END-{retry_time} {self.enterprise_spec.enterprise_id} {dept.name} elapsed: {time_end - time_start}'
            )

        if not success:
            if read_policy(self.workspace_dir, round_id):
                self._write_skill_execution_audit(
                    role=role, department=dept.dept_id, round_id=round_id,
                    output_file_path=spec.output_file_path, all_messages=all_messages,
                    attempts=retry_time, success=False, fallback_used=False,
                    elapsed_seconds=time.time() - run_started_at, context_audit=context_audit,
                    execution_result={"status": "error", "reason": fd_failure_reason,
                                      "heuristic_fallback_disabled": True},
                    phase=phase,
                )
                raise RuntimeError(
                    f"FD Agent decision failed: {role} at round {round_id}; no heuristic substitution. "
                    f"Reason: {fd_failure_reason}"
                )
            transport_failure = self._single_enterprise_agent_transport_failure(
                role=role,
                round_id=round_id,
                messages=all_messages,
            )
            if transport_failure:
                if bool(skill_profile.get("audit_enabled", True)):
                    self._write_skill_execution_audit(
                        role=role,
                        department=dept.dept_id,
                        round_id=round_id,
                        output_file_path=spec.output_file_path,
                        all_messages=all_messages,
                        attempts=retry_time,
                        success=False,
                        fallback_used=False,
                        elapsed_seconds=time.time() - run_started_at,
                        context_audit=context_audit,
                        execution_result=transport_failure,
                        phase=phase,
                    )
                raise RuntimeError(transport_failure["message"])

            fallback_reason = (
                'Timeout fallback: continue this round using current orders, inventory, cash, capacity, staffing and fulfillment state.'
            )
            if advisory_only:
                self._write_advisory_fallback_file(spec, fallback_reason)
            else:
                fallback_written = self._write_long_run_state_driven_fallback_file(
                    spec,
                    dept.dept_id,
                    round_id,
                    need_trade=need_trade,
                )
                if not fallback_written and dept.dept_id == "hr":
                    fallback_written = self._write_hr_staffing_fallback_file(
                        spec,
                        fallback_reason,
                    )
                if not fallback_written:
                    self._write_action_pass_file(spec, dept.dept_id, fallback_reason)
            if self._communication_enabled() and not need_trade:
                self._write_empty_communication_sidecar(
                    spec,
                    fallback_reason,
                )
            if advisory_only:
                execution_result = {
                    "status": "success",
                    "advisory_only": True,
                    "fallback": True,
                    "output_file": spec.output_file_path,
                }
            else:
                result = MultiTenantUtils.execute_enterprise_dept_action(
                    round_id=round_id,
                    retry_time=max_retry + 1,
                    dept=dept.dept_id,
                    enterprise_name=self.enterprise_spec.enterprise_name,
                )
                execution_result = result
            if execution_result.get("status") != "success":
                all_messages.append({
                    "enterprise_id": self.enterprise_spec.enterprise_id,
                    "enterprise_name": self.enterprise_spec.enterprise_name,
                    "role": role,
                    "content": f"[StateDrivenFallbackFailed] {execution_result}",
                })
                if not advisory_only:
                    self._write_action_pass_file(
                        spec,
                        dept.dept_id,
                        (
                            'Timeout fallback: the round action failed execution validation. Preserve accounting and state consistency by making no new commitments this round.'
                        ),
                    )
                    execution_result = MultiTenantUtils.execute_enterprise_dept_action(
                        round_id=round_id,
                        retry_time=max_retry + 2,
                        dept=dept.dept_id,
                        enterprise_name=self.enterprise_spec.enterprise_name,
                    )

        if bool(skill_profile.get("audit_enabled", True)):
            self._write_skill_execution_audit(
                role=role,
                department=dept.dept_id,
                round_id=round_id,
                output_file_path=spec.output_file_path,
                all_messages=all_messages,
                attempts=retry_time,
                success=(
                    success
                    or bool(
                        execution_result
                        and execution_result.get("status") == "success"
                    )
                ),
                fallback_used=not success,
                elapsed_seconds=time.time() - run_started_at,
                context_audit=context_audit,
                execution_result=execution_result,
                phase=phase,
            )

        return all_messages

    async def run_analyst(self, round_id: int) -> List[dict]:
        hybrid_messages = self.hybrid_replay_runner.write_analysis(round_id)
        if hybrid_messages is not None:
            return hybrid_messages
        spec = self._build_analyst_run_spec(round_id)
        self._active_analyst_round_id = round_id
        self._active_analyst_observation_path = spec.observation_path
        self._active_analyst_spec = spec
        self._analysis_decision_warnings = []
        self._remove_stale_output_file(spec.output_file_path)

        success = False
        retry_time = 0
        all_messages: List[dict] = []
        prompt = self._build_analyst_launch_prompt(spec, round_id)
        skill_profile = self._skill_profile()
        completion_guard_enabled = fd_completion_enabled(read_policy(self.workspace_dir, round_id), "Analyst", round_id)
        session_messages: List[dict] = []
        attempt_failures: List[dict] = []
        self._analyst_attempt_failures = attempt_failures


        resume_session_id: Optional[str] = None

        default_timeout = 240 if self._single_enterprise_agent_run_active() else 120
        max_retry = max(1, int(skill_profile.get("max_retries", 3) or 0))

        print(f"START- {self.enterprise_spec.enterprise_id} Analyst")
        time_start = time.time()

        while not success and retry_time < max_retry:
            if not resume_session_id:
                session_messages = []
            attempt_messages: List[dict] = []
            session_id_found: str = resume_session_id or ""
            recover_from_session_error = False
            attempt_started_at = time.time()
            timeout = self._long_run_agent_timeout_seconds(
                role="Analyst",
                round_id=round_id,
                retry_index=retry_time,
                default_seconds=default_timeout,
                analyst=True,
            )

            options = self.build_options("Analyst")
            input_attempt_state = {}
            options = configure_input_access(options, spec, self.workspace_dir, self.client_dir,
                                             self.enterprise_spec.enterprise_name, round_id,
                                             attempt_state=input_attempt_state,
                                             output_validator=(
                                                 lambda state=input_attempt_state: self._analyst_completion_error(spec, round_id, state)
                                             ) if completion_guard_enabled else None,
                                             output_warning_provider=(lambda: self._analyst_fact_warnings(spec, round_id))
                                             if fd2_fact_audit_policy(read_policy(self.workspace_dir, round_id)) else None)
            options = self._bind_session_to_options(options, resume_session_id)

            try:
                async def stream() -> None:
                    nonlocal session_id_found

                    async with aclosing(query(
                        prompt=input_access_prompt(prompt, self.workspace_dir, round_id),
                        options=options,
                    )) as response_stream:
                        async for message in response_stream:
                            record = {
                                "enterprise_id": self.enterprise_spec.enterprise_id,
                                "enterprise_name": self.enterprise_spec.enterprise_name,
                                "role": "Analyst",
                                "content": str(message),
                            }
                            attempt_messages.append(record)
                            all_messages.append(record)

                            check_model_message(message, workspace=self.workspace_dir, options=options,
                                                enterprise_id=self.enterprise_spec.enterprise_id,
                                                role="Analyst", round_id=round_id)

                            if input_attempt_state.get("loop_reason"):
                                raise FDInputLoopError(input_attempt_state["loop_reason"])

                            msg_session_id = getattr(message, "data", {}).get("session_id")
                            if msg_session_id:
                                session_id_found = msg_session_id

                await asyncio.wait_for(stream(), timeout=timeout)

            except asyncio.TimeoutError:
                timeout_record = {
                    "enterprise_id": self.enterprise_spec.enterprise_id,
                    "enterprise_name": self.enterprise_spec.enterprise_name,
                    "role": "Analyst",
                    "content": (
                        f"[Timeout] role=Analyst, retry={retry_time}, "
                        f"timeout_seconds={timeout}"
                    ),
                }
                attempt_messages.append(timeout_record)
                all_messages.append(timeout_record)
                self.session_registry.clear(self.enterprise_spec.enterprise_id, "Analyst")
                resume_session_id = ""
                session_id_found = ""
                # FD output may be ready before the CLI closes its result stream.
                # Authorized input evidence is still checked below before acceptance.
                recover_from_session_error = not (
                    read_policy(self.workspace_dir, round_id)
                    and self._is_valid_json_file(spec.output_file_path)
                )
            except (ProcessError, FDInputLoopError) as e:
                self._abort_fatal_model_failure(e, options, "Analyst", round_id, retry_time + 1, attempt_messages)
                error_label = "InputAccessLoop" if isinstance(e, FDInputLoopError) else "ProcessError"
                error_record = {
                    "enterprise_id": self.enterprise_spec.enterprise_id,
                    "enterprise_name": self.enterprise_spec.enterprise_name,
                    "role": "Analyst",
                    "content": f"[{error_label}] role=Analyst, retry={retry_time}, error={e}",
                }
                attempt_messages.append(error_record)
                all_messages.append(error_record)
                self.session_registry.clear(self.enterprise_spec.enterprise_id, "Analyst")
                resume_session_id = ""
                session_id_found = ""
                recover_from_session_error = True
            except Exception as e:
                self._abort_fatal_model_failure(e, options, "Analyst", round_id, retry_time + 1, attempt_messages)
                error_record = {
                    "enterprise_id": self.enterprise_spec.enterprise_id,
                    "enterprise_name": self.enterprise_spec.enterprise_name,
                    "role": "Analyst",
                    "content": (
                        f"[UnexpectedQueryError] role=Analyst, retry={retry_time}, "
                        f"error_type={type(e).__name__}, error={e}"
                    ),
                }
                attempt_messages.append(error_record)
                all_messages.append(error_record)
                self.session_registry.clear(self.enterprise_spec.enterprise_id, "Analyst")
                resume_session_id = ""
                session_id_found = ""
                recover_from_session_error = True

            if recover_from_session_error:
                if read_policy(self.workspace_dir, round_id):
                    self._last_analysis_validation_error = attempt_messages[-1]["content"]
                self._dump_attempt_messages(
                    self.enterprise_spec.enterprise_name,
                    "Analyst",
                    round_id,
                    retry_time + 1,
                    attempt_messages,
                )
                prompt = self._build_analyst_launch_prompt(spec, round_id)
                prompt += fd_input_retry_context(spec, input_attempt_state)
                retry_time += 1
                continue


            if session_id_found:
                if session_id_found != resume_session_id:
                    session_messages = []
                resume_session_id = session_id_found


            output_path = Path(spec.output_file_path)
            has_file = output_path.exists()
            if not has_file:
                if not completion_guard_enabled:
                    self._recover_output_file_from_messages(attempt_messages, spec.output_file_path)
                has_file = output_path.exists()
            if not has_file and not completion_guard_enabled:
                legacy_source = self._recover_analyst_output_from_legacy_path(
                    spec,
                    attempt_messages,
                    attempt_started_at,
                )
                if legacy_source:
                    recovery_record = {
                        "enterprise_id": self.enterprise_spec.enterprise_id,
                        "enterprise_name": self.enterprise_spec.enterprise_name,
                        "role": "Analyst",
                        "content": (
                            "[RecoveredAnalysisFromLegacyPath] "
                            f"source={legacy_source}, target={spec.output_file_path}"
                        ),
                    }
                    attempt_messages.append(recovery_record)
                    all_messages.append(recovery_record)
                has_file = output_path.exists()
            valid_file = self._is_valid_json_file(spec.output_file_path) if has_file else False
            if valid_file and completion_guard_enabled:
                completion_error = self._analyst_completion_error(spec, round_id, input_attempt_state)
                if completion_error:
                    valid_file = self._set_analysis_validation_error(completion_error)

            if valid_file and read_policy(self.workspace_dir, round_id):
                admitted = accessed_inputs(
                    self.workspace_dir, self.enterprise_spec.enterprise_name, "Analyst", round_id,
                )
                required = [spec.observation_path]
                if spec.chart_export_enabled and Path(spec.chart_export_path).is_file():
                    required.append(spec.chart_export_path)
                if not spec.compact_observation_enabled and Path(spec.history_projection_path).is_file():
                    required.append(spec.history_projection_path)
                missing = [value for value in required if str(Path(value).resolve()) not in admitted]
                if missing:
                    valid_file = self._set_analysis_validation_error(
                        "Required authorized Analyst reads missing: " + ", ".join(missing)
                        + ("; no input access evidence was recorded. Check SDK hook initialization "
                           "and fd_audit access logs." if not admitted else "")
                    )

            if has_file and valid_file:
                success = True
            else:
                validation_error = getattr(
                    self,
                    "_last_analysis_validation_error",
                    "",
                )
                if has_file and validation_error:
                    validation_record = {
                        "enterprise_id": self.enterprise_spec.enterprise_id,
                        "enterprise_name": self.enterprise_spec.enterprise_name,
                        "role": "Analyst",
                        "content": (
                            "[AnalysisValidationFailed] "
                            f"{validation_error}"
                        ),
                    }
                    attempt_messages.append(validation_record)
                    all_messages.append(validation_record)
                inspection = self._inspect_analyst_messages(
                    attempt_messages, spec,
                    session_messages=session_messages if completion_guard_enabled else None,
                )
                if not has_file and read_policy(self.workspace_dir, round_id):
                    self._last_analysis_validation_error = ""
                    prompt = self._next_analyst_prompt_by_failure(spec, round_id, inspection)
                    self._last_analysis_validation_error = (
                        "analysis.json was not created at the required output path; "
                        f"tool inspection: {inspection.failure_type}"
                    )
                    if completion_guard_enabled and attempt_failures:
                        self._last_analysis_validation_error += (
                            "; previous attempt: " + attempt_failures[-1]["reason"]
                        )
                    failure_record = {
                        "enterprise_id": self.enterprise_spec.enterprise_id,
                        "enterprise_name": self.enterprise_spec.enterprise_name,
                        "role": "Analyst",
                        "content": f"[AnalysisValidationFailed] {self._last_analysis_validation_error}",
                    }
                    attempt_messages.append(failure_record)
                    all_messages.append(failure_record)
                else:
                    prompt = self._next_analyst_prompt_by_failure(spec, round_id, inspection)
                if completion_guard_enabled:
                    attempt_failures.append({"attempt": retry_time + 1,
                        "failure_type": inspection.failure_type,
                        "reason": self._last_analysis_validation_error,
                        "completion_failures": input_attempt_state.get("completion_failures") or []})
                    prompt += (
                        """
Candidate failure record (repair evidence, not business facts): """
                        + json.dumps(attempt_failures[-2:], ensure_ascii=False)
                        + f'\nWrite the full corrected candidate to {spec.output_file_path} and Read it back; explanations or partial reference arrays are not delivery.'
                    )
                if has_file:
                    self._remove_stale_output_file(spec.output_file_path)


                self._dump_attempt_messages(
                    self.enterprise_spec.enterprise_name,
                    "Analyst",
                    round_id,
                    retry_time + 1,
                    attempt_messages,
                )

            session_messages.extend(attempt_messages)
            retry_time += 1

        time_end = time.time()
        print(f"END- {self.enterprise_spec.enterprise_id} Analyst--- {time_end - time_start}")
        transport_failure = None
        fallback_used = False
        if not success:
            transport_failure = self._single_enterprise_agent_transport_failure(
                role="Analyst",
                round_id=round_id,
                messages=all_messages,
            )
            if (
                transport_failure is None
                and self._long_run_agent_failure_continuation_enabled()
                and not read_policy(self.workspace_dir, round_id)
            ):
                fallback_used = MultiTenantUtils._write_fallback_enterprise_analysis(
                    self.enterprise_spec.enterprise_name,
                    round_id,
                )
                if fallback_used:
                    all_messages.append({
                        "enterprise_id": self.enterprise_spec.enterprise_id,
                        "enterprise_name": self.enterprise_spec.enterprise_name,
                        "role": "Analyst",
                        "content": (
                            "[LongRunFallbackAnalysis] Agent analysis unavailable; "
                            "continued from current observation state."
                        ),
                    })

        if bool(skill_profile.get("audit_enabled", True)):
            self._write_skill_execution_audit(
                role="Analyst",
                department="analyst",
                round_id=round_id,
                output_file_path=spec.output_file_path,
                all_messages=all_messages,
                attempts=retry_time,
                success=success,
                fallback_used=fallback_used,
                elapsed_seconds=time_end - time_start,
                context_audit={
                    "department": "analyst",
                    "ok": True,
                    "source_files": {
                        "analysis": spec.output_file_path,
                        "observation": spec.observation_path,
                        "history_projection": spec.history_projection_path,
                    },
                    "active_modes": {
                        "cobweb_profit_objective": (
                            spec.compact_observation_kind == "cobweb_c3"
                        ),
                        "long_horizon_context_compaction": (
                            spec.compact_observation_kind == "long_horizon_e1"
                        ),
                    },
                    "visible_policy_names": [],
                    "disallowed_policy_names": [],
                    "missing_files": [],
                },
                execution_result=transport_failure,
                phase="analyst",
                analyst=True,
            )

        self._active_analyst_round_id = None
        self._active_analyst_observation_path = None
        self._active_analyst_spec = None
        if transport_failure:
            raise RuntimeError(transport_failure["message"])
        if not success and read_policy(self.workspace_dir, round_id):
            reason = getattr(self, "_last_analysis_validation_error", "") or "no valid analysis output"
            raise RuntimeError(
                f"Analyst analysis validation failed for {self.enterprise_spec.enterprise_name} "
                f"at round {round_id} after {retry_time} attempts: {reason}. "
                f"Output: {spec.output_file_path}"
            )
        return all_messages
