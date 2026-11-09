import requests
import os
import json
import logging
import math
import uuid
import re
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Set
from copy import deepcopy
import shutil
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config.environment_config import EnvironmentConfig
from config.simulation_preset_config import (
    get_active_enterprise_configs,
    get_active_enterprise_specs,
    get_runtime_injection_config,
)
from config.module_config import ProcurementConfig
from config.fd_experiment import policy_from_meta, policy_from_config, fd2_physical_execution_policy
from runtime.simulation_session_context import get_simulation_session_id
from core.model_failure import FatalModelError
from agent.policy_context import build_department_policy_context, build_enterprise_policy_context

# =========================

# =========================
class Config:
    BASE_URL = EnvironmentConfig.SIMULATION_API_BASE_URL
    BASE_DIR = Path(__file__).resolve().parent

    WORKSPACE = BASE_DIR / "workspace"

    ACTION_PLAN = WORKSPACE / "action_plan.json"
    HR_ACTION = WORKSPACE / "hr_action.json"
    INVENTORY_ACTION = WORKSPACE / "inventory_action.json"
    PROCUREMENT_ACTION = WORKSPACE / "procurement_action.json"
    SALES_ACTION = WORKSPACE / "sales_action.json"
    PRODUCTION_ACTION = WORKSPACE / "production_action.json"
    TEST_ACTION = WORKSPACE / "test_action.json"

    HR_ERROR = WORKSPACE / "hr_error.json"
    INVENTORY_ERROR = WORKSPACE / "inventory_error.json"
    PROCUREMENT_ERROR = WORKSPACE / "procurement_error.json"
    SALES_ERROR = WORKSPACE / "sales_error.json"
    PRODUCTION_ERROR = WORKSPACE / "production_error.json"

    RESULT = WORKSPACE / "result.json"
    HR_RESULT = WORKSPACE / "hr_result.json"
    INVENTORY_RESULT = WORKSPACE / "inventory_result.json"
    PROCUREMENT_RESULT = WORKSPACE / "procurement_result.json"
    SALES_RESULT = WORKSPACE / "sales_result.json"
    PRODUCTION_RESULT = WORKSPACE / "production_result.json"
    TEST_RESULT = WORKSPACE / "test_result.json"

    RE_ACTION_PLAN = WORKSPACE / "re_action_plan.json"
    RE_RESULT = WORKSPACE / "re_result.json"

    INIT_ACTION = WORKSPACE / "init_action.json"
    INIT_RESULT = WORKSPACE / "init_result.json"

    DAILY_ACTION = WORKSPACE / "daily_action.json"
    DAILY_RESULT = WORKSPACE / "daily_result.json"

    OBSERVATION_DIR = WORKSPACE / "observations"
    LOG_DIR = BASE_DIR / "logs"

    DEPARTMENTS = [
        "finance",
        "hr",
        "production",
        "sales",
        "procurement",
        "inventory"
    ]


# =========================

# =========================
class TraceIdFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, "trace_id"):
            record.trace_id = "-"
        return True

def setup_logger():

    logger = logging.getLogger("agent-utils")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | trace=%(trace_id)s | %(message)s"
    )

    class TraceIdFilter(logging.Filter):
        def filter(self, record):
            if not hasattr(record, "trace_id"):
                record.trace_id = "-"
            return True

    # File handler
    # File logging is opt-in and lazy so importing this module is read-only.
    fh = None
    if os.getenv("SUPPLY_CHAIN_RUNTIME_LOG_DIR"):
        log_dir = Path(os.environ["SUPPLY_CHAIN_RUNTIME_LOG_DIR"])
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / f"run_{datetime.now():%Y%m%d}.log", encoding="utf-8", delay=True)
        fh.setFormatter(formatter)
        fh.addFilter(TraceIdFilter())

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    ch.addFilter(TraceIdFilter())

    logger.handlers.clear()
    if fh is not None:
        logger.addHandler(fh)
    logger.addHandler(ch)

    return logger

logger = setup_logger()



# =========================

# =========================
class StaticUtils:
    ACTIONABLE_PROPOSAL_STATUSES = {"pending", "open", "available", "proposed"}
    NON_ACTIONABLE_PROPOSAL_STATUSES = {
        "accepted",
        "confirmed",
        "fulfilled",
        "delivered",
        "completed",
        "rejected",
        "cancelled",
        "canceled",
        "expired",
        "superseded",
        "closed",
    }

    @staticmethod
    def configure_workspace(workspace_dir: Path) -> None:
        """Point legacy single-enterprise paths at the active job workspace."""
        workspace = Path(workspace_dir)
        Config.WORKSPACE = workspace
        Config.ACTION_PLAN = workspace / "action_plan.json"
        Config.HR_ACTION = workspace / "hr_action.json"
        Config.INVENTORY_ACTION = workspace / "inventory_action.json"
        Config.PROCUREMENT_ACTION = workspace / "procurement_action.json"
        Config.SALES_ACTION = workspace / "sales_action.json"
        Config.PRODUCTION_ACTION = workspace / "production_action.json"
        Config.TEST_ACTION = workspace / "test_action.json"
        Config.HR_ERROR = workspace / "hr_error.json"
        Config.INVENTORY_ERROR = workspace / "inventory_error.json"
        Config.PROCUREMENT_ERROR = workspace / "procurement_error.json"
        Config.SALES_ERROR = workspace / "sales_error.json"
        Config.PRODUCTION_ERROR = workspace / "production_error.json"
        Config.RESULT = workspace / "result.json"
        Config.HR_RESULT = workspace / "hr_result.json"
        Config.INVENTORY_RESULT = workspace / "inventory_result.json"
        Config.PROCUREMENT_RESULT = workspace / "procurement_result.json"
        Config.SALES_RESULT = workspace / "sales_result.json"
        Config.PRODUCTION_RESULT = workspace / "production_result.json"
        Config.TEST_RESULT = workspace / "test_result.json"
        Config.RE_ACTION_PLAN = workspace / "re_action_plan.json"
        Config.RE_RESULT = workspace / "re_result.json"
        Config.INIT_ACTION = workspace / "init_action.json"
        Config.INIT_RESULT = workspace / "init_result.json"
        Config.DAILY_ACTION = workspace / "daily_action.json"
        Config.DAILY_RESULT = workspace / "daily_result.json"
        Config.OBSERVATION_DIR = workspace / "observations"
        Config.LOG_DIR = workspace / "_process_logs"

    @staticmethod
    def _post_execution(url, *, json, headers=None, timeout=60):
        """Give a proposal a stable receipt identity; unknown transport outcomes abort the run."""
        payload = {**json, "submission_id": json.get("submission_id") or uuid.uuid4().hex}
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except requests.RequestException as error:
            raise FatalModelError("environment_execution_indeterminate",
                "No execution receipt was obtained; stop instead of replaying an uncertain action. "
                f"Submission: {payload['submission_id']}") from error
        if response.status_code >= 400:
            raise FatalModelError("environment_execution_unavailable",
                f"Execution endpoint returned HTTP {response.status_code}; submission {payload['submission_id']} requires inspection.")
        return response

    @staticmethod
    def simulation_session_headers() -> Dict[str, str]:
        from config.platform_access import operator_request_headers
        session_id = get_simulation_session_id()
        headers = operator_request_headers()
        if session_id:
            headers["X-Simulation-Session-Id"] = session_id
        return headers

    @staticmethod
    def is_agent_endogenous_cobweb_context(agent_simulation_context: Dict[str, Any]) -> bool:
        """It's not like you're in the Agent internal web supply response mode."""
        context = agent_simulation_context or {}
        cobweb_config = context.get("cobweb_config") or {}
        return bool(
            context.get("is_cobweb_mode")
            or context.get("market_demand_mode") == "cobweb"
        ) and cobweb_config.get("production_response_mode") == "agent_endogenous"

    @staticmethod
    def is_shared_resource_context(agent_simulation_context: Dict[str, Any]) -> bool:
        """To judge whether the current market model for shared resources is in place."""
        context = agent_simulation_context or {}
        shared_resource_config = context.get("shared_resource_config") or {}
        return bool(
            context.get("market_demand_mode") == "shared_resource_market"
            or context.get("is_shared_resource_mode")
            or shared_resource_config.get("enabled")
        )

    @staticmethod
    def is_herding_context(agent_simulation_context: Dict[str, Any]) -> bool:
        """To judge whether or not it is currently in the sheep effect / information cascade market model."""
        context = agent_simulation_context or {}
        herding_config = context.get("herding_config") or {}
        return bool(
            context.get("market_demand_mode") == "herding_market"
            or context.get("is_herding_mode")
            or herding_config.get("enabled")
        )

    @staticmethod
    def get_shared_resource_product_id(agent_simulation_context: Dict[str, Any]) -> Optional[str]:
        """Reads the product ID of the shared resource from the simulation context."""
        context = agent_simulation_context or {}
        for key in ("shared_resource_config", "shared_resource_state"):
            source = context.get(key) or {}
            product_id = source.get("product_id")
            if product_id:
                return product_id
        return None

    @staticmethod
    def get_herding_product_id(agent_simulation_context: Dict[str, Any]) -> Optional[str]:
        """Read the product ID of the sheep effect experiment in the context of the simulation."""
        context = agent_simulation_context or {}
        for key in ("herding_config", "herding_state"):
            source = context.get(key) or {}
            product_id = source.get("product_id")
            if product_id:
                return product_id
        return None

    @staticmethod
    def redact_production_agent_state_for_shared_resource(
        production_state: Dict[str, Any],
        agent_simulation_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Decline traditional noise production in shared_resource mode

        In a shared-resource scenario, a target product with an empty recipe represents
        acquisition from the shared resource pool, not a missing bill of materials.
        The environment retains complete audit data; this helper adjusts only the Agent's decision view.
        To avoid NO_MATERIAL_FEASIBILITY/ MISSING_RECIPE_OR_ZERO_QUANTITY being misinterpreted as hard to block.
                """
        if not StaticUtils.is_shared_resource_context(agent_simulation_context):
            return production_state
        if not isinstance(production_state, dict):
            return production_state

        product_id = StaticUtils.get_shared_resource_product_id(agent_simulation_context)
        if not product_id:
            return production_state

        redacted = deepcopy(production_state)
        noisy_reason_codes = {
            "NO_MATERIAL_FEASIBILITY",
            "MISSING_RECIPE_OR_ZERO_QUANTITY",
            "NEGATIVE_MARGIN_GUARD",
        }
        removed_fields = [
            "recovery_guard.candidates[*].blocking_reasons.NO_MATERIAL_FEASIBILITY",
            "recovery_guard.candidates[*].material_feasible_quantity",
            "recovery_guard.candidates[*].material_shortages",
            "recovery_guard.candidates[*].recommended_plan_quantity",
            "recovery_guard.candidates[*].recommended_daily_capacity",
            "margin_guard.candidates[*].guard_level=hard_blocked",
            "margin_guard.candidates[*].reason_codes.MISSING_RECIPE_OR_ZERO_QUANTITY",
            "margin_guard.candidates[*].reason_codes.NEGATIVE_MARGIN_GUARD",
            "margin_guard.summary.hard_blocked_candidate_count_for_shared_resource_product",
        ]

        recovery_guard = redacted.get("recovery_guard")
        if isinstance(recovery_guard, dict):
            sanitized_guard = deepcopy(recovery_guard)
            sanitized_candidates = []
            target_candidate_count = 0
            for candidate in sanitized_guard.get("candidates") or []:
                if not isinstance(candidate, dict):
                    sanitized_candidates.append(candidate)
                    continue
                if candidate.get("product_id") != product_id:
                    sanitized_candidates.append(candidate)
                    continue

                target_candidate_count += 1
                sanitized = dict(candidate)
                sanitized["guard_role"] = "service_context_only"
                sanitized["shared_resource_note"] = (
                    "This product is settled by the shared_resource mechanism. An empty raw_materials map is valid; "
                    "resource availability must be read from shared_resource_metrics rather than conventional material feasibility."
                )
                sanitized["reason_codes"] = [
                    code for code in (sanitized.get("reason_codes") or [])
                    if code not in noisy_reason_codes
                ]
                sanitized["blocking_reasons"] = [
                    reason for reason in (sanitized.get("blocking_reasons") or [])
                    if reason not in noisy_reason_codes
                ]
                for field in (
                    "material_feasible_quantity",
                    "recommended_plan_quantity",
                    "recommended_daily_capacity",
                ):
                    sanitized.pop(field, None)
                sanitized["material_shortages"] = []
                sanitized_candidates.append(sanitized)

            sanitized_guard["candidates"] = sanitized_candidates
            if target_candidate_count:
                sanitized_guard["agent_input_filter"] = {
                    "applied": True,
                    "mode": "shared_resource_market",
                    "policy": "treat_legacy_recovery_guard_as_service_context_only",
                    "product_id": product_id,
                    "removed_fields": removed_fields[:5],
                }
            redacted["recovery_guard"] = sanitized_guard

        margin_guard = redacted.get("margin_guard")
        if isinstance(margin_guard, dict):
            sanitized_margin = deepcopy(margin_guard)
            sanitized_candidates = []
            shared_resource_hard_blocked_count = 0
            visible_hard_blocked_count = 0
            for candidate in sanitized_margin.get("candidates") or []:
                if not isinstance(candidate, dict):
                    sanitized_candidates.append(candidate)
                    continue

                sanitized = dict(candidate)
                if candidate.get("product_id") == product_id:
                    if sanitized.get("guard_level") == "hard_blocked":
                        shared_resource_hard_blocked_count += 1
                    sanitized["guard_level"] = "risk_context_only"
                    sanitized["guard_role"] = "shared_resource_risk_context"
                    sanitized["shared_resource_note"] = (
                        "Sales price, effective output, and resource quality for shared-resource products come from "
                        "shared_resource_metrics; the conventional margin guard is not a hard block for create_production_plan."
                    )
                    sanitized["reason_codes"] = [
                        code for code in (sanitized.get("reason_codes") or [])
                        if code not in noisy_reason_codes
                    ]
                    for field in (
                        "should_recover_now",
                        "recommended_plan_quantity",
                        "recommended_daily_capacity",
                    ):
                        sanitized.pop(field, None)
                elif sanitized.get("guard_level") == "hard_blocked":
                    visible_hard_blocked_count += 1
                sanitized_candidates.append(sanitized)

            sanitized_margin["candidates"] = sanitized_candidates
            sanitized_summary = dict(sanitized_margin.get("summary") or {})
            if shared_resource_hard_blocked_count:
                sanitized_summary["hard_blocked_candidate_count"] = visible_hard_blocked_count
                sanitized_summary["shared_resource_hard_blocked_reclassified_count"] = shared_resource_hard_blocked_count
                sanitized_summary["shared_resource_margin_guard_role"] = "risk_context_only"
            sanitized_margin["summary"] = sanitized_summary
            if shared_resource_hard_blocked_count:
                sanitized_margin["agent_input_filter"] = {
                    "applied": True,
                    "mode": "shared_resource_market",
                    "policy": "reclassify_target_product_margin_guard_as_risk_context",
                    "product_id": product_id,
                    "removed_fields": removed_fields[5:],
                }
            redacted["margin_guard"] = sanitized_margin

        redacted["shared_resource_plan_context"] = {
            "enabled": True,
            "product_id": product_id,
            "plan_quantity_meaning": "resource_or_product_acquisition_quantity",
            "empty_recipe_raw_materials_allowed": True,
            "use_recovery_guard_as_service_context_only": True,
            "use_margin_guard_as_risk_context_only": True,
            "resource_availability_source": "shared_resource_metrics",
        }
        redacted["agent_input_filter"] = {
            "applied": True,
            "mode": "shared_resource_market",
            "reason": "Shared-resource products are settled through the common pool; conventional material feasibility and margin hard blocks are retained only as audit context.",
            "product_id": product_id,
        }
        return redacted

    @staticmethod
    def redact_production_agent_state_for_herding(
        production_state: Dict[str, Any],
        agent_simulation_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Decline traditional noise production in herding_market mode

        In a herding scenario, the production plan represents output selected from market-activity and contract signals.
        * The present document is being issued without formal editing. Empty raw_materials is a functional configuration and should not be made available by raw material feasibility
        Or 0 sales price margin guard is misread as hard blockage.
                """
        if not StaticUtils.is_herding_context(agent_simulation_context):
            return production_state
        if not isinstance(production_state, dict):
            return production_state

        product_id = StaticUtils.get_herding_product_id(agent_simulation_context)
        if not product_id:
            return production_state

        context = agent_simulation_context or {}
        herding_config = context.get("herding_config") or {}
        unit_price = herding_config.get("base_unit_price")
        unit_cost = herding_config.get("unit_cost_reference")
        estimated_margin = None
        try:
            if unit_price is not None and unit_cost is not None:
                estimated_margin = float(unit_price) - float(unit_cost)
        except (TypeError, ValueError):
            estimated_margin = None

        redacted = deepcopy(production_state)
        noisy_reason_codes = {
            "NO_MATERIAL_FEASIBILITY",
            "MISSING_RECIPE_OR_ZERO_QUANTITY",
            "NEGATIVE_MARGIN_GUARD",
        }
        removed_fields = [
            "recovery_guard.candidates[*].blocking_reasons.NO_MATERIAL_FEASIBILITY",
            "recovery_guard.candidates[*].material_feasible_quantity",
            "recovery_guard.candidates[*].material_shortages",
            "recovery_guard.candidates[*].recommended_plan_quantity",
            "recovery_guard.candidates[*].recommended_daily_capacity",
            "margin_guard.candidates[*].guard_level=hard_blocked",
            "margin_guard.candidates[*].reason_codes.MISSING_RECIPE_OR_ZERO_QUANTITY",
            "margin_guard.candidates[*].reason_codes.NEGATIVE_MARGIN_GUARD",
            "margin_guard.summary.hard_blocked_candidate_count_for_herding_product",
        ]

        recovery_guard = redacted.get("recovery_guard")
        if isinstance(recovery_guard, dict):
            sanitized_guard = deepcopy(recovery_guard)
            sanitized_candidates = []
            target_candidate_count = 0
            for candidate in sanitized_guard.get("candidates") or []:
                if not isinstance(candidate, dict):
                    sanitized_candidates.append(candidate)
                    continue
                if candidate.get("product_id") != product_id:
                    sanitized_candidates.append(candidate)
                    continue

                target_candidate_count += 1
                sanitized = dict(candidate)
                sanitized["guard_role"] = "herding_feasibility_context_only"
                sanitized["herding_note"] = (
                    "The herding_market mechanism supplies market heat, peer summaries, and unit price/cost references. "
                    "An empty raw_materials map is valid, and conventional material feasibility is not a hard block for create_production_plan."
                )
                sanitized["reason_codes"] = [
                    code for code in (sanitized.get("reason_codes") or [])
                    if code not in noisy_reason_codes
                ]
                sanitized["blocking_reasons"] = [
                    reason for reason in (sanitized.get("blocking_reasons") or [])
                    if reason not in noisy_reason_codes
                ]
                for field in (
                    "material_feasible_quantity",
                    "recommended_plan_quantity",
                    "recommended_daily_capacity",
                ):
                    sanitized.pop(field, None)
                sanitized["material_shortages"] = []
                sanitized_candidates.append(sanitized)

            sanitized_guard["candidates"] = sanitized_candidates
            if target_candidate_count:
                sanitized_guard["agent_input_filter"] = {
                    "applied": True,
                    "mode": "herding_market",
                    "policy": "treat_legacy_recovery_guard_as_feasibility_context_only",
                    "product_id": product_id,
                    "removed_fields": removed_fields[:5],
                }
            redacted["recovery_guard"] = sanitized_guard

        margin_guard = redacted.get("margin_guard")
        if isinstance(margin_guard, dict):
            sanitized_margin = deepcopy(margin_guard)
            sanitized_candidates = []
            herding_hard_blocked_count = 0
            visible_hard_blocked_count = 0
            for candidate in sanitized_margin.get("candidates") or []:
                if not isinstance(candidate, dict):
                    sanitized_candidates.append(candidate)
                    continue

                sanitized = dict(candidate)
                if candidate.get("product_id") == product_id:
                    if sanitized.get("guard_level") == "hard_blocked":
                        herding_hard_blocked_count += 1
                    sanitized["guard_level"] = "risk_context_only"
                    sanitized["guard_role"] = "herding_market_risk_context"
                    sanitized["herding_note"] = (
                        "Price and cost references for the herding product come from herding_signal.unit_economics; "
                        "the conventional margin guard signals risk but does not hard-block a production plan."
                    )
                    sanitized["herding_reference_unit_price"] = unit_price
                    sanitized["herding_reference_unit_cost"] = unit_cost
                    sanitized["herding_reference_unit_margin"] = estimated_margin
                    sanitized["reason_codes"] = [
                        code for code in (sanitized.get("reason_codes") or [])
                        if code not in noisy_reason_codes
                    ]
                    for field in (
                        "should_recover_now",
                        "recommended_plan_quantity",
                        "recommended_daily_capacity",
                    ):
                        sanitized.pop(field, None)
                elif sanitized.get("guard_level") == "hard_blocked":
                    visible_hard_blocked_count += 1
                sanitized_candidates.append(sanitized)

            sanitized_margin["candidates"] = sanitized_candidates
            sanitized_summary = dict(sanitized_margin.get("summary") or {})
            if herding_hard_blocked_count:
                sanitized_summary["hard_blocked_candidate_count"] = visible_hard_blocked_count
                sanitized_summary["herding_hard_blocked_reclassified_count"] = herding_hard_blocked_count
                sanitized_summary["herding_margin_guard_role"] = "risk_context_only"
            sanitized_margin["summary"] = sanitized_summary
            if herding_hard_blocked_count:
                sanitized_margin["agent_input_filter"] = {
                    "applied": True,
                    "mode": "herding_market",
                    "policy": "reclassify_target_product_margin_guard_as_risk_context",
                    "product_id": product_id,
                    "removed_fields": removed_fields[5:],
                }
            redacted["margin_guard"] = sanitized_margin

        redacted["herding_plan_context"] = {
            "enabled": True,
            "product_id": product_id,
            "plan_quantity_meaning": "agent_decided_production_or_market_supply_quantity",
            "direct_production_empty_recipe_allowed": True,
            "use_recovery_guard_as_feasibility_context_only": True,
            "use_margin_guard_as_risk_context_only": True,
            "market_signal_source": "policy_context.relevant_policies.herding_signal",
            "unit_price_reference": unit_price,
            "unit_cost_reference": unit_cost,
            "estimated_unit_margin": estimated_margin,
        }
        redacted["product_recipes"] = StaticUtils._compact_herding_product_recipes(
            redacted.get("product_recipes"),
            product_id,
        )
        redacted["production_plans"] = StaticUtils._compact_herding_production_plans(
            redacted.get("production_plans"),
            product_id,
        )
        redacted["production_lines"] = StaticUtils._compact_herding_production_lines(
            redacted.get("production_lines"),
        )
        redacted["agent_input_filter"] = {
            "applied": True,
            "mode": "herding_market",
            "reason": "The herding product is driven by market heat and aggregated peer signals; legacy production guards remain only as feasibility and risk context.",
            "product_id": product_id,
        }
        return redacted

    @staticmethod
    def _compact_herding_product_recipes(recipes: Any, product_id: str) -> Any:
        """Compressed formulation input in sheep model to avoid the misreading of empty formulations as additional project information."""
        if not isinstance(recipes, list):
            return recipes
        compacted = []
        for recipe in recipes:
            if not isinstance(recipe, dict):
                continue
            if product_id and recipe.get("product_id") != product_id:
                continue
            raw_materials = recipe.get("raw_materials")
            compacted.append({
                "product_id": recipe.get("product_id"),
                "product_name": recipe.get("product_name"),
                "production_time": recipe.get("production_time"),
                "raw_materials_empty": not bool(raw_materials),
                "raw_materials": raw_materials or {},
                "herding_recipe_note": (
                    "herding_market allows this product to have no raw materials; "
                    "do not interpret an empty recipe as a request to add a recipe or procure materials."
                ),
            })
        return compacted

    @staticmethod
    def _compact_herding_production_plans(plans: Any, product_id: str) -> Any:
        """Only a summary of the plan required for production decision-making will be maintained to reduce later-stage inflate."""
        if not isinstance(plans, dict):
            return plans
        compacted: Dict[str, Any] = {}
        for status, items in plans.items():
            if not isinstance(items, list):
                compacted[status] = items
                continue
            selected = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if product_id and item.get("product_id") != product_id:
                    continue
                selected.append({
                    "plan_id": item.get("plan_id") or item.get("id"),
                    "product_id": item.get("product_id"),
                    "status": item.get("status") or status,
                    "quantity": item.get("quantity"),
                    "remaining_quantity": item.get("remaining_quantity"),
                    "daily_capacity": item.get("daily_capacity"),
                    "start_day": item.get("start_day"),
                    "end_day": item.get("end_day"),
                })
            compacted[status] = selected[:12]
        compacted["_compact_note"] = (
            "herding_market production_plans are compressed into target-product plan summaries. "
            "If no real plan requires pausing or resuming, choose between create_production_plan and action_pass."
        )
        return compacted

    @staticmethod
    def _compact_herding_production_lines(lines: Any) -> Any:
        """Compressed line details, only capacity and available status."""
        if not isinstance(lines, dict):
            return lines
        compacted = {
            "total_capacity": lines.get("total_capacity"),
            "available_capacity": lines.get("available_capacity"),
            "utilized_capacity": lines.get("utilized_capacity"),
            "total": lines.get("total"),
            "idle_count": lines.get("idle_count"),
            "active_count": lines.get("active_count"),
        }
        details = lines.get("details")
        if isinstance(details, list):
            compacted["details"] = [
                {
                    "line_id": item.get("line_id") or item.get("id"),
                    "line_type": item.get("line_type"),
                    "status": item.get("status"),
                    "capacity": item.get("capacity"),
                    "available_capacity": item.get("available_capacity"),
                }
                for item in details[:8]
                if isinstance(item, dict)
            ]
        return compacted

    @staticmethod
    def redact_production_agent_state_for_cobweb(
        production_state: Dict[str, Any],
        agent_simulation_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Crops the restorative target in the parameter spider web mode.

        The bottom modular status remains complete parameter guard data; only Agent is processed here
        . C2 lowers the target for the non-spread web, and C3 retains evidence of the results of the operation but still removes the recommended amount.
                """
        if not StaticUtils.is_agent_endogenous_cobweb_context(agent_simulation_context):
            return production_state
        if not isinstance(production_state, dict):
            return production_state

        redacted = deepcopy(production_state)
        cobweb_config = (agent_simulation_context or {}).get("cobweb_config") or {}
        profit_balanced = cobweb_config.get("decision_profile") == "profit_balanced"
        removed_fields = [
            "recovery_guard.candidates[*].should_recover_now",
            "recovery_guard.candidates[*].recommended_plan_quantity",
            "recovery_guard.candidates[*].recommended_daily_capacity",
            "cash_guard.affordable_recovery_candidates",
            "margin_guard.candidates[*].should_recover_now",
            "margin_guard.candidates[*].recommended_plan_quantity",
            "margin_guard.candidates[*].recommended_daily_capacity",
        ]
        mechanism_primary_removed_fields = [
            "recovery_guard.summary.should_recover_any",
            "recovery_guard.summary.active_candidate_count",
            "recovery_guard.candidates[*].reason_codes",
            "recovery_guard.candidates[*].demand_backlog_quantity",
            "recovery_guard.candidates[*].confirmed_order_backlog_quantity",
            "recovery_guard.candidates[*].proposal_backlog_quantity",
            "recovery_guard.candidates[*].stale_backlog_quantity",
            "recovery_guard.candidates[*].policy_floor",
            "recovery_guard.candidates[*].low_stock_threshold",
            "margin_guard.candidates[*].confirmed_order_backlog_quantity",
            "margin_guard.candidates[*].stale_backlog_quantity",
            "margin_guard.candidates[*].projected_revenue",
            "margin_guard.candidates[*].projected_total_cost",
            "margin_guard.candidates[*].projected_gross_profit",
        ]
        if not profit_balanced:
            removed_fields.extend(mechanism_primary_removed_fields)

        recovery_guard = redacted.get("recovery_guard")
        if isinstance(recovery_guard, dict):
            sanitized_guard = deepcopy(recovery_guard)
            sanitized_summary = dict(sanitized_guard.get("summary") or {})
            if not profit_balanced:
                sanitized_summary.pop("should_recover_any", None)
                sanitized_summary.pop("active_candidate_count", None)
            sanitized_guard["summary"] = sanitized_summary
            sanitized_candidates = []
            for candidate in sanitized_guard.get("candidates") or []:
                if not isinstance(candidate, dict):
                    sanitized_candidates.append(candidate)
                    continue
                sanitized = dict(candidate)
                fields_to_remove = [
                    "should_recover_now",
                    "recommended_plan_quantity",
                    "recommended_daily_capacity",
                ]
                if not profit_balanced:
                    fields_to_remove.extend([
                        "reason_codes",
                        "demand_backlog_quantity",
                        "confirmed_order_backlog_quantity",
                        "proposal_backlog_quantity",
                        "stale_backlog_quantity",
                        "policy_floor",
                        "low_stock_threshold",
                    ])
                for field in fields_to_remove:
                    sanitized.pop(field, None)
                sanitized_candidates.append(sanitized)
            sanitized_guard["candidates"] = sanitized_candidates
            sanitized_guard["agent_input_filter"] = {
                "applied": True,
                "mode": "profit_balanced_cobweb" if profit_balanced else "agent_endogenous_cobweb",
                "policy": (
                    "remove_prescriptive_recovery_quantities_keep_operating_evidence"
                    if profit_balanced
                    else "remove_recovery_quantity_targets_keep_feasibility_fields"
                ),
                "removed_fields": removed_fields,
            }
            redacted["recovery_guard"] = sanitized_guard

        cash_guard = redacted.get("cash_guard")
        if isinstance(cash_guard, dict):
            sanitized_cash = deepcopy(cash_guard)
            affordable_candidates = sanitized_cash.pop("affordable_recovery_candidates", None)
            if isinstance(affordable_candidates, list):
                sanitized_cash["affordable_recovery_candidate_count"] = len(affordable_candidates)
            sanitized_cash["agent_input_filter"] = {
                "applied": True,
                "mode": "profit_balanced_cobweb" if profit_balanced else "agent_endogenous_cobweb",
                "policy": "remove_recovery_quantity_targets_keep_budget_fields",
                "removed_fields": ["cash_guard.affordable_recovery_candidates"],
            }
            redacted["cash_guard"] = sanitized_cash

        margin_guard = redacted.get("margin_guard")
        if isinstance(margin_guard, dict):
            sanitized_margin = deepcopy(margin_guard)
            sanitized_candidates = []
            for candidate in sanitized_margin.get("candidates") or []:
                if not isinstance(candidate, dict):
                    sanitized_candidates.append(candidate)
                    continue
                sanitized = dict(candidate)
                fields_to_remove = [
                    "should_recover_now",
                    "recommended_plan_quantity",
                    "recommended_daily_capacity",
                ]
                if not profit_balanced:
                    fields_to_remove.extend([
                        "confirmed_order_backlog_quantity",
                        "stale_backlog_quantity",
                        "projected_revenue",
                        "projected_total_cost",
                        "projected_gross_profit",
                    ])
                for field in fields_to_remove:
                    sanitized.pop(field, None)
                sanitized_candidates.append(sanitized)
            sanitized_margin["candidates"] = sanitized_candidates
            sanitized_margin["agent_input_filter"] = {
                "applied": True,
                "mode": "profit_balanced_cobweb" if profit_balanced else "agent_endogenous_cobweb",
                "policy": (
                    "remove_prescriptive_quantities_keep_projected_unit_economics"
                    if profit_balanced
                    else "remove_recovery_quantity_targets_keep_margin_feasibility"
                ),
                "removed_fields": removed_fields,
            }
            redacted["margin_guard"] = sanitized_margin

        redacted["agent_input_filter"] = {
            "applied": True,
            "mode": "profit_balanced_cobweb" if profit_balanced else "agent_endogenous_cobweb",
            "reason": (
                "Production quantity must be derived jointly from price evidence, profit, cash, service, and adjustment risk; prescriptive recovery quantities remain only in low-level state for audit."
                if profit_balanced
                else "Production quantity must be derived endogenously from the cobweb price signal, operating constraints, and existing plans; recovery recommendations remain only in low-level state for audit."
            ),
        }
        return redacted

    @staticmethod
    def compact_sales_agent_state_for_cobweb_profit(
        sales_state: Dict[str, Any],
        policy_context: Dict[str, Any],
        round_id: int,
    ) -> Dict[str, Any]:
        """The cumulative history of the web-based C3 sales input and the retention of all enforceable orders."""
        active_modes = (policy_context or {}).get("active_modes") or {}
        if not (
            active_modes.get("cobweb")
            and active_modes.get("profit_objective")
        ):
            return sales_state
        if not isinstance(sales_state, dict):
            return sales_state

        compacted = deepcopy(sales_state)
        sales_orders = compacted.get("sales_orders") or {}
        if isinstance(sales_orders, dict):
            history_summary = {
                bucket: len(orders)
                for bucket, orders in sales_orders.items()
                if isinstance(orders, list)
            }

            def compact_order(order: Dict[str, Any]) -> Dict[str, Any]:
                fields = (
                    "order_id",
                    "product_id",
                    "quantity",
                    "unit_price",
                    "total_amount",
                    "source_id",
                    "source_type",
                    "created_time",
                    "delivery_deadline",
                    "status",
                    "accepted_time",
                )
                result = {
                    field: order.get(field)
                    for field in fields
                    if field in order
                }
                created_time = order.get("created_time")
                deadline = order.get("delivery_deadline")
                try:
                    result["age_rounds"] = max(
                        0,
                        int(round_id) - int(created_time),
                    )
                except (TypeError, ValueError):
                    result["age_rounds"] = None
                try:
                    result["deadline_status"] = (
                        "expired"
                        if int(round_id) > int(deadline)
                        else "open"
                    )
                except (TypeError, ValueError):
                    result["deadline_status"] = "unknown"
                return result

            available = [
                compact_order(order)
                for order in (sales_orders.get("available") or [])
                if isinstance(order, dict) and order.get("order_id")
            ]
            available.sort(
                key=lambda order: (
                    order.get("deadline_status") == "expired",
                    order.get("delivery_deadline")
                    if order.get("delivery_deadline") is not None
                    else 10**9,
                    order.get("created_time")
                    if order.get("created_time") is not None
                    else 10**9,
                    str(order.get("order_id") or ""),
                )
            )
            compact_orders: Dict[str, Any] = {"available": available}
            for bucket in ("accepted", "in_progress", "pending"):
                unresolved = [
                    compact_order(order)
                    for order in (sales_orders.get(bucket) or [])
                    if isinstance(order, dict) and order.get("order_id")
                ]
                if unresolved:
                    compact_orders[bucket] = unresolved
            compact_orders["history_summary"] = history_summary
            compacted["sales_orders"] = compact_orders

        demand_backlog = compacted.get("demand_backlog")
        if isinstance(demand_backlog, dict):
            compacted["demand_backlog"] = {
                "by_product": demand_backlog.get("by_product") or {},
                "history_counts": {
                    key: len(value)
                    for key, value in demand_backlog.items()
                    if key.endswith("_history") and isinstance(value, list)
                },
            }

        proposals = compacted.get("proposals_list")
        if isinstance(proposals, list):
            compacted["proposals_list"] = [
                {
                    field: proposal.get(field)
                    for field in (
                        "proposal_id",
                        "product_id",
                        "quantity",
                        "unit_price",
                        "total_amount",
                        "status",
                        "sender_id",
                        "receiver_id",
                        "proposed_delivery_round",
                    )
                    if field in proposal
                }
                for proposal in proposals[:8]
                if isinstance(proposal, dict)
            ]

        compacted["agent_input_filter"] = {
            "applied": True,
            "mode": "c3_cobweb_sales_long_run",
            "policy": (
                "keep_all_actionable_orders_and_current_metrics; "
                "replace completed/breached/rejected histories with counts"
            ),
        }
        return compacted


    @staticmethod
    def _load_department_state_snapshot(enterprise_name: str, department: str, round_id: Optional[int]) -> Dict[str, Any]:
        """Reads the current state snapshot of enterprisedepartment specified for action."""
        if round_id is None or department not in {"procurement", "sales", "production", "hr", "inventory"}:
            return {}
        state_path = (
            Config.WORKSPACE
            / "enterprises"
            / enterprise_name
            / "department"
            / department
            / f"day{round_id}"
            / f"{department}.json"
        )
        if not state_path.exists():
            return {}
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _get_pending_proposal_ids(state_snapshot: Dict[str, Any]) -> Set[str]:
        """Extract from the department status snapshot the current set of proposal_id still to be responded to."""
        return set(StaticUtils._get_state_proposal_index(state_snapshot).keys())

    @staticmethod
    def _proposal_actionability_status(
        proposal: Dict[str, Any],
        department: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return whether a proposal can still receive accept/reject actions."""
        if not isinstance(proposal, dict):
            return {
                "actionable": False,
                "status": "invalid",
                "reason": "proposal_record_not_dict",
            }
        status = str(proposal.get("status") or "pending").lower()
        if status in StaticUtils.NON_ACTIONABLE_PROPOSAL_STATUSES:
            return {
                "actionable": False,
                "status": status,
                "reason": f"proposal_status_{status}_not_actionable",
            }
        if status not in StaticUtils.ACTIONABLE_PROPOSAL_STATUSES:
            return {
                "actionable": False,
                "status": status,
                "reason": f"proposal_status_{status}_not_in_actionable_whitelist",
            }
        if department == "procurement" and "buyer_response" in proposal:
            response = proposal.get("buyer_response")
            if response not in (None, "", "pending"):
                return {
                    "actionable": False,
                    "status": status,
                    "reason": f"buyer_response_{response}_already_set",
                }
        if department == "sales" and "seller_response" in proposal:
            response = proposal.get("seller_response")
            if response not in (None, "", "pending"):
                return {
                    "actionable": False,
                    "status": status,
                    "reason": f"seller_response_{response}_already_set",
                }
        return {
            "actionable": True,
            "status": status,
            "reason": "actionable",
        }

    @staticmethod
    def _proposal_is_pending_for_response(proposal: Dict[str, Any], department: Optional[str] = None) -> bool:
        """This is a test of whether proposal can still be responded to by department."""
        return bool(
            StaticUtils._proposal_actionability_status(
                proposal,
                department,
            ).get("actionable")
        )

    @staticmethod
    def _iter_state_proposals(state_snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        self_state = state_snapshot.get("self_state", {}) if isinstance(state_snapshot, dict) else {}
        proposals = []
        if isinstance(self_state.get("proposals_list"), list):
            proposals = self_state.get("proposals_list") or []
        elif isinstance(state_snapshot.get("proposals_list"), list):
            proposals = state_snapshot.get("proposals_list") or []
        return [proposal for proposal in proposals if isinstance(proposal, dict)]

    @staticmethod
    def _get_state_proposal_index(state_snapshot: Dict[str, Any], department: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """returns the proposal that is still responsive in department state, indexed to proposal_id."""
        proposal_index: Dict[str, Dict[str, Any]] = {}
        for proposal in StaticUtils._iter_state_proposals(state_snapshot):
            proposal_id = proposal.get("proposal_id")
            if proposal_id and StaticUtils._proposal_is_pending_for_response(proposal, department):
                proposal_index[str(proposal_id)] = proposal
        return proposal_index

    @staticmethod
    def _get_exchange_proposal_index(round_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
        """Read the current round exchange de facto status and avoid department snapshot lags leading to duplicate/expired responses."""
        if round_id is None:
            return {}
        try:
            day = int(round_id)
        except (TypeError, ValueError):
            return {}
        exchange_path = Config.WORKSPACE / "public" / "exchange" / f"day{day}" / "exchange.json"
        if not exchange_path.exists():
            return {}
        try:
            payload = json.loads(exchange_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        exchanges = ((payload.get("data") or {}).get("exchanges") or {}) if isinstance(payload, dict) else {}
        proposal_index: Dict[str, Dict[str, Any]] = {}
        if not isinstance(exchanges, dict):
            return proposal_index
        for exchange in exchanges.values():
            if not isinstance(exchange, dict):
                continue
            proposals_block = exchange.get("proposals") or {}
            proposals = proposals_block.get("list") if isinstance(proposals_block, dict) else proposals_block
            if not isinstance(proposals, list):
                continue
            for proposal in proposals:
                if not isinstance(proposal, dict):
                    continue
                proposal_id = proposal.get("proposal_id")
                if proposal_id:
                    proposal_index[str(proposal_id)] = proposal
        return proposal_index

    @staticmethod
    def _get_exchange_buy_request_index(round_id: Optional[int]) -> Dict[str, Dict[str, Any]]:
        """Reads the current wheel buy reQuest de facto to block the response to a proposal already superseded/expired."""
        if round_id is None:
            return {}
        try:
            day = int(round_id)
        except (TypeError, ValueError):
            return {}
        exchange_path = Config.WORKSPACE / "public" / "exchange" / f"day{day}" / "exchange.json"
        if not exchange_path.exists():
            return {}
        try:
            payload = json.loads(exchange_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

        exchanges = ((payload.get("data") or {}).get("exchanges") or {}) if isinstance(payload, dict) else {}
        request_index: Dict[str, Dict[str, Any]] = {}
        if not isinstance(exchanges, dict):
            return request_index
        for exchange in exchanges.values():
            if not isinstance(exchange, dict):
                continue
            requests_block = exchange.get("buy_requests") or {}
            requests = requests_block.get("list") if isinstance(requests_block, dict) else requests_block
            if not isinstance(requests, list):
                continue
            for request in requests:
                if not isinstance(request, dict):
                    continue
                request_id = request.get("request_id")
                if request_id:
                    request_index[str(request_id)] = request
        return request_index

    @staticmethod
    def _procurement_acceptance_cash_guard(
        state_snapshot: Dict[str, Any],
        proposal: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Estimating the cash result of procurement acceptance as a pre-implementation hard-guard."""
        self_state = state_snapshot.get("self_state", {}) if isinstance(state_snapshot, dict) else {}
        cash_guard = self_state.get("cash_guard") or state_snapshot.get("cash_guard") or {}
        cash_summary = cash_guard.get("cash_summary") or {}
        available_cash = StaticUtils._safe_float(
            cash_guard.get("remaining_cash_after_commitments"),
            StaticUtils._safe_float(cash_summary.get("current_cash"), 0.0),
        ) or 0.0
        warning_threshold = StaticUtils._safe_float(cash_summary.get("warning_threshold"), 0.0) or 0.0
        quantity = StaticUtils._safe_float((proposal or {}).get("quantity"), 0.0) or 0.0
        unit_price = StaticUtils._safe_float(
            (proposal or {}).get("proposed_price"),
            StaticUtils._safe_float((proposal or {}).get("price_reference"), 0.0),
        ) or 0.0
        estimated_cost = max(0.0, quantity) * max(0.0, unit_price)
        projected_remaining_cash = available_cash - estimated_cost
        if projected_remaining_cash < 0:
            commitment_level = "hard_blocked"
        elif warning_threshold > 0 and projected_remaining_cash < warning_threshold:
            commitment_level = "warning"
        else:
            commitment_level = "normal"
        return {
            "available_cash": available_cash,
            "estimated_cost": estimated_cost,
            "projected_remaining_cash": projected_remaining_cash,
            "warning_threshold": warning_threshold,
            "commitment_level": commitment_level,
            "is_hard_blocked": projected_remaining_cash < 0,
        }

    @staticmethod
    def _get_pending_proposal_material_ids(state_snapshot: Dict[str, Any]) -> Dict[str, str]:
        """Return material or product IDs for proposals that are still awaiting a response."""
        pending_materials: Dict[str, str] = {}
        for proposal in StaticUtils._get_state_proposal_index(state_snapshot).values():
            proposal_id = proposal.get("proposal_id")
            material_id = proposal.get("product_id") or proposal.get("material_id")
            if proposal_id and material_id:
                pending_materials[proposal_id] = str(material_id)
        return pending_materials

    @staticmethod
    def _get_procurement_allowed_material_ids(state_snapshot: Dict[str, Any]) -> Set[str]:
        """Return materials valid for the current procurement state, excluding stale cross-scenario backlog candidates."""
        if not isinstance(state_snapshot, dict):
            return set()
        self_state = state_snapshot.get("self_state") if isinstance(state_snapshot.get("self_state"), dict) else state_snapshot
        if not isinstance(self_state, dict):
            return set()

        allowed: Set[str] = set()

        def add_one(value: Any) -> None:
            if value not in (None, ""):
                allowed.add(str(value))

        def add_many(values: Any) -> None:
            if isinstance(values, dict):
                for key in values.keys():
                    add_one(key)
            elif isinstance(values, list):
                for value in values:
                    add_one(value)

        explicit_materials = self_state.get("purchasable_materials_idList")
        add_many(explicit_materials)
        # An explicit enterprise material catalog is authoritative. Supplier
        # matrices are observations, and can contain stale candidates from a
        # different run; they must never expand the executable material set.
        if isinstance(explicit_materials, (list, dict)) and allowed:
            return allowed
        add_many(self_state.get("materials_suppliers_matrix"))

        replenishment = self_state.get("replenishment") or {}
        add_many(replenishment.get("pending_by_material"))

        operational_summary = self_state.get("operational_summary") or {}
        add_many(operational_summary.get("latest_replenishment_by_material"))
        add_many(operational_summary.get("recipe_recovery_signal_by_material"))

        recipe_signal = self_state.get("recipe_recovery_signal_by_material") or {}
        add_many(recipe_signal)

        for supplier in self_state.get("suppliers") or []:
            if not isinstance(supplier, dict):
                continue
            add_many(supplier.get("materials"))
            add_many(supplier.get("materials_offered"))
        for supplier in self_state.get("supplier_candidates") or []:
            if not isinstance(supplier, dict):
                continue
            add_many(supplier.get("materials"))
            add_many(supplier.get("materials_offered"))

        if not allowed:
            inventory_positions = operational_summary.get("inventory_position_by_material") or {}
            for material_id, item in inventory_positions.items():
                if not isinstance(item, dict):
                    continue
                if item.get("item_type") in {"raw_material", "material", "product"}:
                    add_one(material_id)

        return allowed

    @staticmethod
    def _get_procurement_supplier_lookup(state_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """Builds a map of supplier_id/supplier_name to supplier_name required for the implementation module."""
        if not isinstance(state_snapshot, dict):
            return {}
        self_state = state_snapshot.get("self_state") if isinstance(state_snapshot.get("self_state"), dict) else state_snapshot
        if not isinstance(self_state, dict):
            return {}

        id_to_name: Dict[str, str] = {}
        names: Set[str] = set()
        lower_name_to_name: Dict[str, str] = {}
        material_to_names: Dict[str, Set[str]] = {}

        def add_supplier(supplier: Any, material_id: Any = None) -> None:
            if not isinstance(supplier, dict):
                return
            supplier_name = supplier.get("supplier_name") or supplier.get("name")
            supplier_id = supplier.get("supplier_id") or supplier.get("id")
            if supplier_name not in (None, ""):
                supplier_name = str(supplier_name)
                names.add(supplier_name)
                lower_name_to_name[supplier_name.lower()] = supplier_name
            if supplier_id not in (None, "") and supplier_name not in (None, ""):
                id_to_name[str(supplier_id)] = str(supplier_name)
            if material_id not in (None, "") and supplier_name not in (None, ""):
                material_to_names.setdefault(str(material_id), set()).add(str(supplier_name))

        for supplier in self_state.get("suppliers") or []:
            add_supplier(supplier)
            for material_id in (supplier.get("materials") or supplier.get("materials_offered") or []):
                add_supplier(supplier, material_id)
        for supplier in self_state.get("supplier_candidates") or []:
            add_supplier(supplier)
            for material_id in (supplier.get("materials") or supplier.get("materials_offered") or []):
                add_supplier(supplier, material_id)

        matrix = self_state.get("materials_suppliers_matrix") or {}
        if isinstance(matrix, dict):
            for material_id, suppliers in matrix.items():
                if isinstance(suppliers, list):
                    for supplier in suppliers:
                        add_supplier(supplier, material_id)
                elif isinstance(suppliers, dict):
                    add_supplier(suppliers, material_id)

        return {
            "id_to_name": id_to_name,
            "names": names,
            "lower_name_to_name": lower_name_to_name,
            "material_to_names": material_to_names,
        }

    @staticmethod
    def _augment_procurement_state_with_single_case_suppliers(
        state_snapshot: Dict[str, Any],
        single_case_context: Dict[str, Any],
        enterprise_name: str,
    ) -> Dict[str, Any]:
        """Use run metadata suppliers when custom prewarm has no department state."""
        lookup = StaticUtils._get_procurement_supplier_lookup(state_snapshot)
        if lookup.get("names") or not single_case_context:
            return state_snapshot

        run_meta = single_case_context.get("run_meta") or {}
        scenario_config = run_meta.get("scenario_config") or {}
        enterprise_specs = scenario_config.get("enterprise_specs") or []
        if not isinstance(enterprise_specs, list):
            return state_snapshot

        matched_spec = None
        for spec in enterprise_specs:
            if not isinstance(spec, dict):
                continue
            spec_id = spec.get("enterprise_id") or spec.get("id")
            if str(spec_id) == str(enterprise_name):
                matched_spec = spec
                break
        runtime_injection = scenario_config.get("runtime_injection") or {}
        supplier_selection_policy = (
            runtime_injection.get("single_enterprise_supplier_selection_policy") or {}
        )
        candidate_suppliers = []
        if (
            isinstance(supplier_selection_policy, dict)
            and supplier_selection_policy.get("enabled")
        ):
            target_enterprises = (
                supplier_selection_policy.get("target_enterprise_ids")
                or supplier_selection_policy.get("enterprise_ids")
                or []
            )
            if not target_enterprises or str(enterprise_name) in {
                str(item) for item in target_enterprises
            }:
                candidate_suppliers = [
                    deepcopy(item)
                    for item in supplier_selection_policy.get("candidate_suppliers") or []
                    if isinstance(item, dict)
                ]

        suppliers = (matched_spec or {}).get("initial_suppliers") or []
        if not suppliers and not candidate_suppliers:
            return state_snapshot

        merged = deepcopy(state_snapshot) if isinstance(state_snapshot, dict) else {}
        self_state = (
            merged.get("self_state")
            if isinstance(merged.get("self_state"), dict)
            else merged
        )
        if self_state is merged:
            merged = {"self_state": self_state}

        if suppliers:
            self_state.setdefault("suppliers", deepcopy(suppliers))
        if candidate_suppliers:
            self_state.setdefault("supplier_candidates", deepcopy(candidate_suppliers))
        if matched_spec:
            self_state.setdefault(
                "purchasable_materials_idList",
                deepcopy(matched_spec.get("purchasable_materials_idList") or []),
            )
        if not self_state.get("materials_suppliers_matrix"):
            matrix: Dict[str, List[Dict[str, Any]]] = {}
            for supplier in list(suppliers) + list(candidate_suppliers):
                if not isinstance(supplier, dict):
                    continue
                materials = supplier.get("materials") or supplier.get("materials_offered") or {}
                material_ids = materials.keys() if isinstance(materials, dict) else materials
                for material_id in material_ids or []:
                    matrix.setdefault(str(material_id), []).append(deepcopy(supplier))
            if matrix:
                self_state["materials_suppliers_matrix"] = matrix
        return merged

    @staticmethod
    def _resolve_procurement_supplier_name(
        state_snapshot: Dict[str, Any],
        material_id: Any,
        supplier_name: Any = None,
        supplier_id: Any = None,
    ) -> Optional[str]:
        """The supplier_id frequently written by Agent is integrated into the supplier_name required for the implementation of the interface."""
        lookup = StaticUtils._get_procurement_supplier_lookup(state_snapshot)
        names: Set[str] = lookup.get("names") or set()
        lower_name_to_name: Dict[str, str] = lookup.get("lower_name_to_name") or {}
        id_to_name: Dict[str, str] = lookup.get("id_to_name") or {}
        material_to_names: Dict[str, Set[str]] = lookup.get("material_to_names") or {}

        for candidate in (supplier_name, supplier_id):
            if candidate in (None, ""):
                continue
            candidate_text = str(candidate)
            if candidate_text in names:
                return candidate_text
            if candidate_text in id_to_name:
                return id_to_name[candidate_text]
            lowered = candidate_text.lower()
            if lowered in lower_name_to_name:
                return lower_name_to_name[lowered]

        material_names = material_to_names.get(str(material_id)) if material_id not in (None, "") else None
        if material_names and len(material_names) == 1:
            return next(iter(material_names))
        if len(names) == 1:
            return next(iter(names))
        return None

    @staticmethod
    def _get_procurement_supplier_material_info(
        state_snapshot: Dict[str, Any],
        material_id: Any,
        supplier_name: Any,
    ) -> Dict[str, Any]:
        if not isinstance(state_snapshot, dict) or material_id in (None, "") or supplier_name in (None, ""):
            return {}
        self_state = state_snapshot.get("self_state") if isinstance(state_snapshot.get("self_state"), dict) else state_snapshot
        if not isinstance(self_state, dict):
            return {}

        material_key = str(material_id)
        supplier_key = str(supplier_name)

        def matches_supplier(supplier: Any) -> bool:
            if not isinstance(supplier, dict):
                return False
            names = {
                str(value)
                for value in (
                    supplier.get("supplier_name"),
                    supplier.get("name"),
                    supplier.get("supplier_id"),
                    supplier.get("id"),
                )
                if value not in (None, "")
            }
            return supplier_key in names

        def material_payload(supplier: Dict[str, Any]) -> Dict[str, Any]:
            materials = supplier.get("materials")
            if isinstance(materials, dict):
                payload = materials.get(material_key)
                if isinstance(payload, dict):
                    merged = dict(supplier)
                    merged.update(payload)
                    return merged
            if isinstance(materials, list) and material_key in {str(item) for item in materials}:
                return dict(supplier)
            offered = supplier.get("materials_offered")
            if isinstance(offered, dict):
                payload = offered.get(material_key)
                if isinstance(payload, dict):
                    merged = dict(supplier)
                    merged.update(payload)
                    return merged
            if isinstance(offered, list) and material_key in {str(item) for item in offered}:
                return dict(supplier)
            return {}

        for supplier in self_state.get("suppliers") or []:
            if matches_supplier(supplier):
                payload = material_payload(supplier)
                if payload:
                    return payload
        for supplier in self_state.get("supplier_candidates") or []:
            if matches_supplier(supplier):
                payload = material_payload(supplier)
                if payload:
                    return payload

        matrix = self_state.get("materials_suppliers_matrix") or {}
        suppliers = matrix.get(material_key) if isinstance(matrix, dict) else None
        if isinstance(suppliers, dict):
            suppliers = [suppliers]
        if isinstance(suppliers, list):
            for supplier in suppliers:
                if matches_supplier(supplier):
                    return dict(supplier)
        return {}

    @staticmethod
    def _single_case_top_tier_order_budget_from_state(
        state_snapshot: Dict[str, Any],
    ) -> Optional[float]:
        if not isinstance(state_snapshot, dict):
            return None
        self_state = state_snapshot.get("self_state") if isinstance(state_snapshot.get("self_state"), dict) else state_snapshot
        if not isinstance(self_state, dict):
            return None
        guard = self_state.get("top_tier_supply_guard") or state_snapshot.get("top_tier_supply_guard") or {}
        if not isinstance(guard, dict) or not guard.get("enabled"):
            return None
        limits = []
        for key in ("max_single_order_amount", "available_credit"):
            value = StaticUtils._safe_float(guard.get(key), 0.0) or 0.0
            if value > 0:
                limits.append(value)
        return min(limits) if limits else None

    @staticmethod
    def _select_single_case_procurement_logistics_mode(
        *,
        state_snapshot: Dict[str, Any],
        action_dir: Optional[Path],
        enterprise_name: str,
        round_id: Optional[int],
        material_id: Any,
        quantity: Any,
    ) -> str:
        logistics_configs = ProcurementConfig().LOGISTICS_CONFIGS
        material_key = str(material_id or "")
        requested = StaticUtils._safe_float(quantity, 0.0) or 0.0
        self_state = (
            state_snapshot.get("self_state")
            if isinstance(state_snapshot.get("self_state"), dict)
            else state_snapshot
        )
        operational_summary = (self_state or {}).get("operational_summary") or {}
        positions = operational_summary.get("inventory_position_by_material") or {}
        position_entry = positions.get(material_key) if isinstance(positions, dict) else {}
        if isinstance(position_entry, dict):
            current_position = StaticUtils._safe_float(
                position_entry.get("inventory_position"),
                StaticUtils._safe_float(position_entry.get("on_hand"), 0.0),
            ) or 0.0
        else:
            current_position = StaticUtils._safe_float(position_entry, 0.0) or 0.0

        required_material = requested
        production_snapshot = StaticUtils._load_sibling_department_state_snapshot(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
            department="production",
            round_id=round_id,
        )
        production_self = (production_snapshot or {}).get("self_state") or {}
        recipes = production_self.get("product_recipes") or {}
        raw_materials = {}
        if isinstance(recipes, list) and recipes:
            first_recipe = next((item for item in recipes if isinstance(item, dict)), {})
            raw_materials = first_recipe.get("raw_materials") or {}
        elif isinstance(recipes, dict) and recipes:
            first_recipe = next(
                (item for item in recipes.values() if isinstance(item, dict)),
                {},
            )
            raw_materials = first_recipe.get("raw_materials") or {}
        per_unit = StaticUtils._safe_float(raw_materials.get(material_key), 0.0) or 0.0
        if per_unit > 0:
            recovery_guard = production_self.get("recovery_guard") or {}
            candidates = recovery_guard.get("candidates") if isinstance(recovery_guard, dict) else []
            target_quantity = 0.0
            for candidate in candidates or []:
                if not isinstance(candidate, dict):
                    continue
                target_quantity = max(
                    target_quantity,
                    StaticUtils._safe_float(candidate.get("recommended_plan_quantity"), 0.0) or 0.0,
                    StaticUtils._safe_float(candidate.get("confirmed_order_backlog_quantity"), 0.0) or 0.0,
                    StaticUtils._safe_float(candidate.get("demand_backlog_quantity"), 0.0) or 0.0,
                )
            if target_quantity > 0:
                required_material = max(required_material, per_unit * target_quantity)

        shortage_ratio = 1.0
        if required_material > 0:
            shortage_ratio = max(0.0, (required_material - current_position) / required_material)
        if current_position <= 0 or shortage_ratio >= 0.5:
            return "air" if "air" in logistics_configs else "road"
        if shortage_ratio > 0:
            return "rail" if "rail" in logistics_configs else "road"
        return "road"

    @staticmethod
    def _cap_single_case_top_tier_purchase_param(
        *,
        state_snapshot: Dict[str, Any],
        action_dir: Optional[Path],
        enterprise_name: str,
        material_id: Any,
        quantity: Any,
        supplier_name: Any,
        logistics_mode: Any,
    ) -> Optional[Dict[str, Any]]:
        mode = str(logistics_mode or "road")
        logistics_configs = ProcurementConfig().LOGISTICS_CONFIGS
        if mode not in logistics_configs:
            mode = "road"
        base_param = {
            "material_id": material_id,
            "quantity": quantity,
            "supplier_name": supplier_name,
            "logistics_mode": mode,
        }
        if not StaticUtils._single_case_context_from_action_dir(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
        ):
            return base_param

        budget = StaticUtils._single_case_top_tier_order_budget_from_state(state_snapshot)
        if budget is None:
            return base_param

        quantity_value = StaticUtils._safe_float(quantity, 0.0) or 0.0
        if quantity_value <= 0:
            return None

        logistics_config = logistics_configs.get(mode, logistics_configs["road"])
        material_info = StaticUtils._get_procurement_supplier_material_info(
            state_snapshot,
            material_id,
            supplier_name,
        )
        unit_price = StaticUtils._safe_float(material_info.get("unit_price"), None)
        if unit_price is None or unit_price <= 0:
            return base_param

        self_state = state_snapshot.get("self_state") if isinstance(state_snapshot.get("self_state"), dict) else state_snapshot
        guard = (self_state or {}).get("top_tier_supply_guard") or {}
        multiplier = StaticUtils._safe_float(guard.get("external_logistics_cost_multiplier"), 1.0) or 1.0
        fixed_cost = (StaticUtils._safe_float(logistics_config.get("base_fee"), 0.0) or 0.0) * multiplier
        variable_cost = unit_price + (StaticUtils._safe_float(logistics_config.get("unit_fee"), 0.0) or 0.0) * multiplier
        if variable_cost <= 0:
            return None

        estimated_total = fixed_cost + variable_cost * quantity_value
        if estimated_total > budget:
            quantity_value = round(max(0.0, (budget - fixed_cost) / variable_cost), 2)
        min_order = StaticUtils._safe_float(material_info.get("min_order_quantity"), 0.0) or 0.0
        if quantity_value <= 0 or (min_order > 0 and quantity_value < min_order):
            return None

        return {
            "material_id": material_id,
            "quantity": quantity_value,
            "supplier_name": supplier_name,
            "logistics_mode": mode,
        }

    @staticmethod
    def _get_available_sales_order_ids(state_snapshot: Dict[str, Any]) -> Set[str]:
        """Extract currently available orders from the sales-state snapshot."""
        self_state = state_snapshot.get("self_state", {}) if isinstance(state_snapshot, dict) else {}
        sales_orders = self_state.get("sales_orders") or state_snapshot.get("sales_orders") or {}
        available_ids: Set[str] = set()

        def visit_order(order: Any, fallback_status: Optional[str] = None) -> None:
            if not isinstance(order, dict):
                return
            status = order.get("status") or order.get("order_status") or fallback_status
            order_id = order.get("order_id")
            if order_id and status == "available":
                available_ids.add(order_id)

        if isinstance(sales_orders, dict):
            for key, value in sales_orders.items():
                if isinstance(value, list):
                    for order in value:
                        visit_order(order, str(key) if key == "available" else None)
                elif isinstance(value, dict):
                    if value.get("order_id") is None:
                        value = dict(value)
                        value["order_id"] = key
                    visit_order(value, str(key) if key == "available" else None)
        elif isinstance(sales_orders, list):
            for order in sales_orders:
                visit_order(order)
        return available_ids

    @staticmethod
    def _get_available_sales_order_index(state_snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Extracts an index of the details of the aviable order from the sales status snapshot."""
        self_state = state_snapshot.get("self_state", {}) if isinstance(state_snapshot, dict) else {}
        sales_orders = self_state.get("sales_orders") or state_snapshot.get("sales_orders") or {}
        order_index: Dict[str, Dict[str, Any]] = {}

        def visit_order(
            order: Any,
            fallback_order_id: Optional[str] = None,
            fallback_status: Optional[str] = None,
        ) -> None:
            if not isinstance(order, dict):
                return
            normalized = dict(order)
            if normalized.get("order_id") is None and fallback_order_id:
                normalized["order_id"] = fallback_order_id
            if normalized.get("status") is None and fallback_status:
                normalized["status"] = fallback_status
            order_id = normalized.get("order_id")
            if order_id and normalized.get("status") == "available":
                order_index[str(order_id)] = normalized

        if isinstance(sales_orders, dict):
            for key, value in sales_orders.items():
                if isinstance(value, list):
                    for order in value:
                        visit_order(order, fallback_status=str(key) if key == "available" else None)
                elif isinstance(value, dict):
                    visit_order(
                        value,
                        str(key),
                        str(key) if key == "available" else None,
                    )
        elif isinstance(sales_orders, list):
            for order in sales_orders:
                visit_order(order)
        return order_index

    @staticmethod
    def _sales_order_snapshot_present(state_snapshot: Dict[str, Any]) -> bool:
        if not isinstance(state_snapshot, dict):
            return False
        self_state = state_snapshot.get("self_state") if isinstance(state_snapshot.get("self_state"), dict) else {}
        return "sales_orders" in self_state or "sales_orders" in state_snapshot

    @staticmethod
    def _sort_sales_order_ids(order_ids: Any) -> List[str]:
        def sort_key(order_id: Any) -> tuple:
            text = str(order_id)
            match = re.search(r"(\d+)$", text)
            return (text[: match.start()] if match else text, int(match.group(1)) if match else 0, text)

        return sorted([str(order_id) for order_id in order_ids if order_id], key=sort_key)

    @staticmethod
    def _single_case_sales_order_quality_review_index(
        state_snapshot: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        if not isinstance(state_snapshot, dict):
            return {}
        brief = state_snapshot.get("agent_decision_brief") or {}
        if not isinstance(brief, dict):
            return {}
        reviews = brief.get("order_quality_reviews") or []
        if not isinstance(reviews, list):
            return {}
        indexed: Dict[str, Dict[str, Any]] = {}
        for review in reviews:
            if not isinstance(review, dict):
                continue
            order_id = review.get("order_id")
            if order_id:
                indexed[str(order_id)] = review
        return indexed

    @staticmethod
    def _single_case_sales_order_quality_sort_key(
        order_id: str,
        *,
        order_detail: Optional[Dict[str, Any]] = None,
        quality_review: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        order_detail = order_detail or {}
        quality_review = quality_review or {}
        risk_flags = quality_review.get("risk_flags") or []
        if not isinstance(risk_flags, list):
            risk_flags = []
        flag_set = {str(flag) for flag in risk_flags}
        unit_margin = StaticUtils._safe_float(
            quality_review.get("estimated_unit_margin"),
            None,
        )
        if unit_margin is None:
            unit_price = StaticUtils._safe_float(order_detail.get("unit_price"), 0.0) or 0.0
            unit_margin = unit_price
        gross_margin = StaticUtils._safe_float(
            quality_review.get("estimated_gross_margin"),
            0.0,
        ) or 0.0
        quantity = StaticUtils._safe_float(
            quality_review.get("quantity"),
            StaticUtils._safe_float(order_detail.get("quantity"), 0.0),
        ) or 0.0
        ratio = StaticUtils._safe_float(
            quality_review.get("quantity_to_uncommitted_inventory_ratio"),
            0.0,
        ) or 0.0
        days_to_deadline = StaticUtils._safe_float(
            quality_review.get("days_to_deadline"),
            99.0,
        )
        if days_to_deadline is None:
            days_to_deadline = 99.0
        return (
            1 if "invalid_non_positive_order" in flag_set else 0,
            1 if "penalty_exposure_exceeds_gross_margin" in flag_set else 0,
            1 if "low_margin_vs_reference_cost" in flag_set else 0,
            1 if "near_cost_price" in flag_set else 0,
            1 if "tight_delivery_deadline" in flag_set else 0,
            1 if "large_commitment_vs_uncommitted_inventory" in flag_set else 0,
            len(flag_set),
            -float(unit_margin or 0.0),
            -float(gross_margin or 0.0),
            float(ratio or 0.0),
            float(quantity or 0.0),
            float(days_to_deadline or 99.0),
            StaticUtils._sort_sales_order_ids([order_id])[0] if order_id else "",
        )

    @staticmethod
    def _reorder_single_case_sales_accept_actions_by_quality(
        payload: List[Dict[str, Any]],
        *,
        available_sales_order_index: Dict[str, Dict[str, Any]],
        quality_review_index: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not isinstance(payload, list) or not quality_review_index:
            return payload
        accept_items: List[Dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            action = item.get("action")
            if not isinstance(action, dict):
                continue
            if action.get("action_name") != "accept_order":
                continue
            action_param = action.get("action_param") or {}
            if not isinstance(action_param, dict):
                continue
            order_id = action_param.get("order_id")
            if order_id and str(order_id) in quality_review_index:
                accept_items.append(item)
        if len(accept_items) <= 1:
            return payload

        def item_key(item: Dict[str, Any]) -> tuple:
            action = item.get("action") or {}
            action_param = action.get("action_param") or {}
            order_id = str(action_param.get("order_id") or "")
            return StaticUtils._single_case_sales_order_quality_sort_key(
                order_id,
                order_detail=available_sales_order_index.get(order_id) or {},
                quality_review=quality_review_index.get(order_id) or {},
            )

        sorted_accepts = iter(sorted(accept_items, key=item_key))
        accept_item_ids = {id(item) for item in accept_items}
        reordered: List[Dict[str, Any]] = []
        for item in payload:
            if id(item) in accept_item_ids:
                reordered.append(next(sorted_accepts))
            else:
                reordered.append(item)
        return reordered

    @staticmethod
    def _is_zero_value_sales_order(order: Dict[str, Any]) -> bool:
        """Determines whether the order is a Zero or Zero Zero Zero Zero Zone / Invalid Zone."""
        if not isinstance(order, dict):
            return False
        quantity = StaticUtils._safe_float(order.get("quantity"), None)
        if quantity is not None and quantity <= 0:
            return True
        total_amount = StaticUtils._safe_float(order.get("total_amount"), None)
        if total_amount is not None and total_amount <= 0:
            return True
        return False

    @staticmethod
    def _iter_procurement_order_snapshots(state_snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return procurement order snapshots from both list and bucket formats."""
        if not isinstance(state_snapshot, dict):
            return []
        self_state = (
            state_snapshot.get("self_state")
            if isinstance(state_snapshot.get("self_state"), dict)
            else state_snapshot
        )
        if not isinstance(self_state, dict):
            return []
        orders_block = self_state.get("orders") or state_snapshot.get("orders") or {}
        orders: List[Dict[str, Any]] = []

        def append_order(order: Any, fallback_id: Optional[str] = None) -> None:
            if not isinstance(order, dict):
                return
            normalized = dict(order)
            if fallback_id and not normalized.get("order_id"):
                normalized["order_id"] = fallback_id
            if normalized.get("material_id") or normalized.get("order_id"):
                orders.append(normalized)

        if isinstance(orders_block, list):
            for order in orders_block:
                append_order(order)
        elif isinstance(orders_block, dict):
            for key, value in orders_block.items():
                if isinstance(value, list):
                    for order in value:
                        append_order(order)
                elif isinstance(value, dict):
                    append_order(value, str(key))
        return orders

    @staticmethod
    def _single_case_material_coverage_summary(
        state_snapshot: Dict[str, Any],
        material_id: Any,
    ) -> Dict[str, float]:
        material_key = str(material_id or "")
        if not material_key or not isinstance(state_snapshot, dict):
            return {"on_hand": 0.0, "incoming": 0.0, "inventory_position": 0.0, "ordered_total": 0.0}
        self_state = (
            state_snapshot.get("self_state")
            if isinstance(state_snapshot.get("self_state"), dict)
            else state_snapshot
        )
        self_state = self_state if isinstance(self_state, dict) else {}
        operational_summary = (
            self_state.get("operational_summary")
            or state_snapshot.get("operational_summary")
            or {}
        )
        inventory_position_by_material = (
            operational_summary.get("inventory_position_by_material")
            if isinstance(operational_summary, dict)
            else {}
        ) or {}
        material_snapshot = inventory_position_by_material.get(material_key) or {}
        on_hand = StaticUtils._safe_float(material_snapshot.get("on_hand"), 0.0) or 0.0
        incoming = StaticUtils._safe_float(material_snapshot.get("incoming"), 0.0) or 0.0
        inventory_position = StaticUtils._safe_float(
            material_snapshot.get("inventory_position"),
            None,
        )

        replenishment = self_state.get("replenishment") or state_snapshot.get("replenishment") or {}
        pending_by_material = (
            replenishment.get("pending_by_material")
            if isinstance(replenishment, dict)
            else {}
        ) or {}
        incoming = max(
            incoming,
            StaticUtils._safe_float(pending_by_material.get(material_key), 0.0) or 0.0,
        )

        open_order_total = 0.0
        for order in StaticUtils._iter_procurement_order_snapshots(state_snapshot):
            if str(order.get("material_id") or "") != material_key:
                continue
            status = str(order.get("status") or "").lower()
            if status in {"rejected", "cancelled", "canceled", "failed", "error"}:
                continue
            if status in {"pending", "ordered", "in_transit", "confirmed", "created"}:
                open_order_total += StaticUtils._safe_float(order.get("quantity"), 0.0) or 0.0

        if inventory_position is None:
            inventory_position = on_hand + incoming
        return {
            "on_hand": on_hand,
            "incoming": incoming,
            "inventory_position": inventory_position,
            "ordered_total": open_order_total,
        }

    @staticmethod
    def _single_case_material_purchase_already_covered(
        state_snapshot: Dict[str, Any],
        material_id: Any,
        quantity: Any,
    ) -> Dict[str, Any]:
        requested = StaticUtils._safe_float(quantity, 0.0) or 0.0
        summary = StaticUtils._single_case_material_coverage_summary(
            state_snapshot,
            material_id,
        )
        if requested <= 0:
            return {"covered": True, **summary, "requested_quantity": requested}
        has_prior_procurement = (
            summary["incoming"] > 0
            or summary["ordered_total"] > 0
        )
        covered = bool(
            has_prior_procurement
            and (
                summary["incoming"] >= requested
                or summary["inventory_position"] >= requested
                or summary["ordered_total"] >= requested
            )
        )
        return {"covered": covered, **summary, "requested_quantity": requested}

    @staticmethod
    def _sanitize_single_enterprise_visible_text(text: Any) -> Any:
        """Remove hidden single-enterprise experiment labels from agent-visible prose."""
        if not isinstance(text, str) or not text:
            return text
        cleaned = text
        replacements = {
            "market_insufficient": "insufficient demand",
            "market_shortage": "insufficient demand",
            "raw_material_shortage": "insufficient material coverage",
            "material_shortage": "insufficient material coverage",
            "capacity_bottleneck": "insufficient capacity",
            "staff_shortage": "staff shortage",
            "cash_pressure": "cash pressure",
            "source_primary_contradiction": "current operating constraint",
            "primary_issue": "current operating constraint",
            "discouraged_actions": "state constraints",
            "discouraged_action": "state constraints",
        }
        for old, new in replacements.items():
            cleaned = re.sub(re.escape(old), new, cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(
            r"single_case[_\w-]*",
            "enterprise diagnosis",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\bcase0[1-9]\b",
            "current-turn diagnosis",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\bcase\s*['\"]?\s*enterprise diagnosis['\"]?",
            "enterprise diagnosis",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned

    @staticmethod
    def _build_action_pass_record(reason: str, department: str, enterprise_name: str) -> Dict[str, Any]:
        """Constructs action_pass records in a uniform format for re-use in the background output."""
        reason = StaticUtils._sanitize_single_enterprise_visible_text(str(reason or ""))
        module_type_map = {
            "sales": "SalesManager",
            "procurement": "ProcurementManager",
            "production": "ProductionManager",
            "hr": "HRManager",
            "inventory": "InventoryManager",
        }
        return {
            "action": {
                "action_name": "action_pass",
                "action_param": reason,
            },
            "action_reason": reason,
            "module_type": module_type_map.get(department, ""),
            "executor_id": enterprise_name,
        }

    @staticmethod
    def _build_department_action_record(
        *,
        action_name: str,
        action_param: Dict[str, Any],
        action_reason: str,
        department: str,
        enterprise_name: str,
    ) -> Dict[str, Any]:
        action_reason = StaticUtils._sanitize_single_enterprise_visible_text(action_reason)
        module_type_map = {
            "sales": "SalesManager",
            "procurement": "ProcurementManager",
            "production": "ProductionManager",
            "hr": "HRManager",
            "inventory": "InventoryManager",
        }
        return {
            "action": {
                "action_name": action_name,
                "action_param": action_param,
            },
            "action_reason": action_reason,
            "module_type": module_type_map.get(department, ""),
            "executor_id": enterprise_name,
        }

    @staticmethod
    def _load_sibling_department_state_snapshot(
        *,
        action_dir: Optional[Path],
        enterprise_name: str,
        department: str,
        round_id: Optional[int],
    ) -> Dict[str, Any]:
        if action_dir is None or round_id is None:
            return {}
        try:
            current_round = int(round_id)
            root = Path(action_dir)
        except (TypeError, ValueError):
            return {}

        enterprise_dir = None
        for parent in [root, *root.parents]:
            if parent.name == str(enterprise_name) and (parent / "department").exists():
                enterprise_dir = parent
                break
        if enterprise_dir is None:
            return {}

        snapshot_path = (
            enterprise_dir
            / "department"
            / department
            / f"day{current_round}"
            / f"{department}.json"
        )
        try:
            if snapshot_path.exists():
                payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
                return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
        return {}

    @staticmethod
    def _inventory_item_quantity(state_snapshot: Dict[str, Any], item_id: str) -> float:
        if not item_id or not isinstance(state_snapshot, dict):
            return 0.0
        self_state = (
            state_snapshot.get("self_state")
            if isinstance(state_snapshot.get("self_state"), dict)
            else state_snapshot
        )
        if not isinstance(self_state, dict):
            return 0.0

        operational_summary = self_state.get("operational_summary") or {}
        by_material = (
            operational_summary.get("inventory_position_by_material")
            if isinstance(operational_summary, dict)
            else {}
        ) or {}
        item_snapshot = by_material.get(item_id) or {}
        quantity = StaticUtils._safe_float(item_snapshot.get("on_hand"), None)
        if quantity is not None:
            return max(0.0, quantity)

        inventory_items = self_state.get("inventory_items") or state_snapshot.get("inventory_items") or []
        if isinstance(inventory_items, dict):
            inventory_items = list(inventory_items.values())
        for item in inventory_items:
            if not isinstance(item, dict):
                continue
            if str(item.get("item_id") or item.get("product_id") or "") == item_id:
                return max(0.0, StaticUtils._safe_float(item.get("quantity"), 0.0) or 0.0)
        return 0.0

    @staticmethod
    def _single_case_material_shortage_partial_production_param(
        state_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(state_snapshot, dict):
            return {}
        self_state = (
            state_snapshot.get("self_state")
            if isinstance(state_snapshot.get("self_state"), dict)
            else state_snapshot
        )
        if not isinstance(self_state, dict):
            return {}

        product_id = "PRODUCT_1"
        recovery_guard = self_state.get("recovery_guard") or {}
        candidates = recovery_guard.get("candidates") if isinstance(recovery_guard, dict) else []
        material_feasible_quantity = 0.0
        for candidate in candidates or []:
            if not isinstance(candidate, dict) or candidate.get("product_id") != product_id:
                continue
            blocking_reasons = set(candidate.get("blocking_reasons") or [])
            candidate_feasible = StaticUtils._safe_float(
                candidate.get("material_feasible_quantity"),
                0.0,
            ) or 0.0
            if "NO_MATERIAL_FEASIBILITY" in blocking_reasons and candidate_feasible <= 0:
                continue
            material_feasible_quantity = max(material_feasible_quantity, candidate_feasible)

        if material_feasible_quantity <= 0:
            material_quantity = StaticUtils._inventory_item_quantity(state_snapshot, "MATERIAL_1")
            if material_quantity > 0:
                material_feasible_quantity = material_quantity / 2.0

        production_lines = self_state.get("production_lines") or {}
        available_capacity_raw = (
            production_lines.get("available_capacity")
            if isinstance(production_lines, dict)
            else None
        )
        available_capacity = StaticUtils._safe_float(available_capacity_raw, 0.0) or 0.0
        if available_capacity_raw in (None, "") and isinstance(production_lines, dict):
            available_capacity = StaticUtils._safe_float(
                production_lines.get("total_capacity"),
                0.0,
            ) or 0.0

        min_recovery_batch = 300.0
        if material_feasible_quantity < min_recovery_batch or available_capacity <= 0:
            return {}

        quantity = min(material_feasible_quantity, available_capacity, 500.0)
        daily_capacity = min(quantity, available_capacity)
        if quantity <= 0 or daily_capacity <= 0:
            return {}
        return {
            "product_id": product_id,
            "quantity": int(round(quantity)),
            "daily_capacity": int(round(daily_capacity)),
        }

    @staticmethod
    def _single_case_material_shortage_safe_accept_order_param(
        *,
        state_snapshot: Dict[str, Any],
        inventory_snapshot: Dict[str, Any],
        available_order_index: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not isinstance(available_order_index, dict) or not available_order_index:
            return {}
        finished_goods = StaticUtils._inventory_item_quantity(inventory_snapshot, "PRODUCT_1")
        if finished_goods <= 0:
            return {}
        candidates = []
        for order_id, order in available_order_index.items():
            if not isinstance(order, dict) or StaticUtils._is_zero_value_sales_order(order):
                continue
            if str(order.get("product_id") or "PRODUCT_1") != "PRODUCT_1":
                continue
            quantity = StaticUtils._safe_float(order.get("quantity"), 0.0) or 0.0
            if quantity <= 0 or quantity > finished_goods:
                continue
            candidates.append((quantity, str(order_id)))
        if not candidates:
            return {}
        candidates.sort(key=lambda item: (item[0], item[1]))
        return {"order_id": candidates[0][1]}

    @staticmethod
    def _single_case_capacity_bottleneck_already_expanded(
        state_snapshot: Dict[str, Any],
    ) -> bool:
        if not isinstance(state_snapshot, dict):
            return False
        self_state = (
            state_snapshot.get("self_state")
            if isinstance(state_snapshot.get("self_state"), dict)
            else state_snapshot
        )
        if not isinstance(self_state, dict):
            return False
        production_lines = self_state.get("production_lines") or {}
        if not isinstance(production_lines, dict):
            return False
        by_status = production_lines.get("by_status") or {}
        total_lines = StaticUtils._safe_float(production_lines.get("total"), 0.0) or 0.0
        available_capacity = StaticUtils._safe_float(
            production_lines.get("available_capacity"),
            0.0,
        ) or 0.0
        idle_lines = StaticUtils._safe_float(by_status.get("idle"), 0.0) or 0.0
        under_construction = StaticUtils._safe_float(
            by_status.get("under_construction"),
            0.0,
        ) or 0.0
        return bool(
            total_lines > 1
            or available_capacity > 0
            or idle_lines > 0
            or under_construction > 0
        )

    @staticmethod
    def _single_enterprise_state_capacity_build_needed(
        state_snapshot: Dict[str, Any],
        inventory_snapshot: Dict[str, Any],
    ) -> bool:
        self_state = (
            state_snapshot.get("self_state")
            if isinstance(state_snapshot.get("self_state"), dict)
            else state_snapshot
        )
        if not isinstance(self_state, dict):
            return False
        production_lines = self_state.get("production_lines") or {}
        if not isinstance(production_lines, dict):
            return False
        by_status = production_lines.get("by_status") or {}
        available_capacity = StaticUtils._safe_float(
            production_lines.get("available_capacity"),
            None,
        )
        if available_capacity is None and isinstance(by_status, dict) and by_status:
            ready_count = sum(
                StaticUtils._safe_float(by_status.get(status), 0.0) or 0.0
                for status in ("idle", "ready", "available")
            )
            available_capacity = 1.0 if ready_count > 0 else 0.0
        if (available_capacity or 0.0) > 0:
            return False
        if isinstance(by_status, dict):
            blocked_by_existing_build = sum(
                StaticUtils._safe_float(by_status.get(status), 0.0) or 0.0
                for status in ("under_construction", "building", "pending", "planned")
            )
            if blocked_by_existing_build > 0:
                return False
        product_id = "PRODUCT_1"
        recipes = self_state.get("product_recipes") or {}
        if isinstance(recipes, dict) and recipes:
            product_id = str(next(iter(recipes.keys())) or product_id)
        elif isinstance(recipes, list):
            for recipe in recipes:
                if isinstance(recipe, dict) and recipe.get("product_id"):
                    product_id = str(recipe.get("product_id"))
                    break
        finished_goods = StaticUtils._inventory_item_quantity(
            inventory_snapshot or {},
            product_id,
        )
        if finished_goods <= 0 and product_id != "PRODUCT_1":
            finished_goods = StaticUtils._inventory_item_quantity(
                inventory_snapshot or {},
                "PRODUCT_1",
            )
        return finished_goods < 40.0

    @staticmethod
    def _single_case_capacity_bottleneck_recovery_plan_param(
        state_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(state_snapshot, dict):
            return {}
        self_state = (
            state_snapshot.get("self_state")
            if isinstance(state_snapshot.get("self_state"), dict)
            else state_snapshot
        )
        if not isinstance(self_state, dict):
            return {}
        production_lines = self_state.get("production_lines") or {}
        if not isinstance(production_lines, dict):
            return {}
        available_capacity = StaticUtils._safe_float(
            production_lines.get("available_capacity"),
            0.0,
        ) or 0.0
        if available_capacity <= 0:
            return {}

        product_id = "PRODUCT_1"
        material_feasible_quantity = 0.0
        recovery_guard = self_state.get("recovery_guard") or {}
        for candidate in (recovery_guard.get("candidates") if isinstance(recovery_guard, dict) else []) or []:
            if not isinstance(candidate, dict) or candidate.get("product_id") != product_id:
                continue
            feasible = StaticUtils._safe_float(
                candidate.get("material_feasible_quantity"),
                0.0,
            ) or 0.0
            material_feasible_quantity = max(material_feasible_quantity, feasible)

        if material_feasible_quantity <= 0:
            material_quantity = StaticUtils._inventory_item_quantity(state_snapshot, "MATERIAL_1")
            material_feasible_quantity = material_quantity / 2.0 if material_quantity > 0 else available_capacity
        if material_feasible_quantity <= 0:
            return {}

        quantity = min(available_capacity, material_feasible_quantity, 500.0)
        if quantity <= 0:
            return {}
        return {
            "product_id": product_id,
            "quantity": int(round(quantity)),
            "daily_capacity": int(round(min(quantity, available_capacity))),
        }

    @staticmethod
    def _single_case_capacity_bottleneck_accept_order_guard(
        *,
        order_detail: Dict[str, Any],
        inventory_snapshot: Dict[str, Any],
        round_id: Optional[int],
        reserved_finished_goods: float = 0.0,
        reject_overdue: bool = True,
    ) -> Dict[str, Any]:
        if not isinstance(order_detail, dict):
            return {"allowed": False, "reason": "order details are missing"}
        try:
            current_round = int(round_id)
        except (TypeError, ValueError):
            current_round = None
        deadline = StaticUtils._safe_float(order_detail.get("delivery_deadline"), None)
        if reject_overdue and deadline is not None and current_round is not None and deadline < current_round:
            return {
                "allowed": False,
                "reason": f"order delivery deadline has passed: delivery_deadline={deadline:g}, round={current_round}",
            }
        quantity = StaticUtils._safe_float(order_detail.get("quantity"), 0.0) or 0.0
        product_id = str(order_detail.get("product_id") or "PRODUCT_1")
        finished_goods = StaticUtils._inventory_item_quantity(inventory_snapshot, product_id)
        available_finished_goods = max(
            0.0,
            finished_goods - max(0.0, StaticUtils._safe_float(reserved_finished_goods, 0.0) or 0.0),
        )
        if inventory_snapshot and quantity > available_finished_goods:
            return {
                "allowed": False,
                "reason": (
                    f"finished-goods inventory cannot support immediate fulfillment: order_quantity={quantity:g}, "
                    f"{product_id}_on_hand={finished_goods:g}, "
                    f"reserved_this_round={reserved_finished_goods:g}"
                ),
            }
        return {"allowed": True, "reason": "order_serviceable"}

    @staticmethod
    def _single_case_production_free_workers(hr_snapshot: Dict[str, Any]) -> float:
        if not isinstance(hr_snapshot, dict):
            return 0.0
        self_state = (
            hr_snapshot.get("self_state")
            if isinstance(hr_snapshot.get("self_state"), dict)
            else hr_snapshot
        )
        if not isinstance(self_state, dict):
            return 0.0
        staffing = StaticUtils._normalize_hr_staffing_map(self_state)
        production_staffing = staffing.get("PRODUCTION") or {}
        if production_staffing:
            return max(0.0, StaticUtils._safe_float(production_staffing.get("available"), 0.0) or 0.0)
        employees = self_state.get("employees") or self_state.get("staffing") or []
        if isinstance(employees, dict):
            employees = list(employees.values())
        for item in employees or []:
            if not isinstance(item, dict):
                continue
            department_name = str(
                item.get("department") or item.get("dept") or item.get("department_name") or ""
            ).upper()
            if "PRODUCTION" not in department_name:
                continue
            count = StaticUtils._safe_float(item.get("count"), 0.0) or 0.0
            allocated = StaticUtils._safe_float(item.get("allocated"), 0.0) or 0.0
            return max(0.0, count - allocated)
        return 0.0

    @staticmethod
    def _canonical_staff_department_code(value: Any) -> str:
        raw = str(value or "").strip()
        lowered = raw.lower()
        alias_map = {
            "PRODUCTION": {"production", "production department", "productionmanager"},
            "PROCUREMENT": {"procurement", "procurement department", "procurementmanager"},
            "SALES": {"sales", "sales department", "salesmanager"},
            "INVENTORY": {"inventory", "warehouse", "inventory department", "inventorymanager"},
            "HR": {"hr", "human_resources", "human resources", "hrmanager"},
            "FINANCE": {"finance", "financial management", "financemanager"},
        }
        for code, aliases in alias_map.items():
            if lowered == code.lower() or lowered in aliases or raw in aliases:
                return code
        return raw.upper() if raw else ""

    @staticmethod
    def _empty_staffing_record(code: str) -> Dict[str, Any]:
        labels = {
            "PRODUCTION": "Production",
            "PROCUREMENT": "Procurement",
            "SALES": "Sales",
            "INVENTORY": "Inventory",
            "HR": "Human Resources",
            "FINANCE": "Finance",
        }
        return {
            "department": code,
            "label": labels.get(code, code),
            "count": 0,
            "allocated": 0,
            "available": 0,
            "physical_available": 0,
            "utilization": 0.0,
            "pending_recruits": 0,
            "seen": False,
        }

    @staticmethod
    def _normalize_hr_staffing_map(
        hr_state_or_self_state: Dict[str, Any],
        *,
        include_all_departments: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        if not isinstance(hr_state_or_self_state, dict):
            hr_state_or_self_state = {}
        self_state = (
            hr_state_or_self_state.get("self_state")
            if isinstance(hr_state_or_self_state.get("self_state"), dict)
            else hr_state_or_self_state
        )
        codes = ["PRODUCTION", "PROCUREMENT", "SALES", "INVENTORY", "HR", "FINANCE"]
        staffing: Dict[str, Dict[str, Any]] = {
            code: StaticUtils._empty_staffing_record(code) for code in codes
        } if include_all_departments else {}

        def ensure(code: str) -> Dict[str, Any]:
            if code not in staffing:
                staffing[code] = StaticUtils._empty_staffing_record(code)
            return staffing[code]

        def apply_entry(code: str, entry: Dict[str, Any]) -> None:
            if not code or not isinstance(entry, dict):
                return
            row = ensure(code)
            count = StaticUtils._safe_float(entry.get("count"), row.get("count", 0)) or 0
            allocated = StaticUtils._safe_float(entry.get("allocated"), row.get("allocated", 0)) or 0
            available = StaticUtils._safe_float(entry.get("available"), None)
            if available is None:
                available = max(0, count - allocated)
            physical_available = StaticUtils._safe_float(entry.get("physical_available"), max(0, count - allocated)) or 0
            row.update({
                "count": int(count),
                "allocated": allocated,
                "available": max(0, available),
                "physical_available": max(0, physical_available),
                "utilization": StaticUtils._safe_float(entry.get("utilization"), row.get("utilization", 0.0)) or 0.0,
                "pending_recruits": int(
                    StaticUtils._safe_float(entry.get("pending_recruits"), row.get("pending_recruits", 0)) or 0
                ),
                "seen": True,
            })

        detailed = self_state.get("department_staffing") or {}
        if isinstance(detailed, dict):
            for key, entry in detailed.items():
                code = StaticUtils._canonical_staff_department_code(key)
                apply_entry(code, entry if isinstance(entry, dict) else {"count": entry})

        employees = self_state.get("employees") or self_state.get("staffing") or []
        if isinstance(employees, dict):
            employees = list(employees.values())
        for item in employees or []:
            if not isinstance(item, dict):
                continue
            code = StaticUtils._canonical_staff_department_code(
                item.get("department") or item.get("dept") or item.get("department_name")
            )
            apply_entry(code, item)

        pending = (self_state.get("recruitment_status") or {}).get("pending_by_department") or {}
        if isinstance(pending, dict):
            for key, value in pending.items():
                code = StaticUtils._canonical_staff_department_code(key)
                if not code:
                    continue
                row = ensure(code)
                row["pending_recruits"] = max(
                    int(row.get("pending_recruits", 0) or 0),
                    int(StaticUtils._safe_float(value, 0) or 0),
                )

        return staffing

    @staticmethod
    def _single_case_hr_staffing_candidates(
        *,
        action_dir: Optional[Path],
        enterprise_name: str,
        round_id: Optional[int],
    ) -> List[Dict[str, Any]]:
        hr_snapshot = StaticUtils._load_sibling_department_state_snapshot(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
            department="hr",
            round_id=round_id,
        )
        if not hr_snapshot:
            hr_snapshot = StaticUtils._load_department_state_snapshot(
                enterprise_name=enterprise_name,
                department="hr",
                round_id=round_id,
            )
        if not hr_snapshot:
            return []

        staffing = StaticUtils._normalize_hr_staffing_map(hr_snapshot)
        candidates: List[Dict[str, Any]] = []

        def add_candidate(code: str, minimum: int, priority: int, reason: str) -> None:
            row = staffing.get(code) or StaticUtils._empty_staffing_record(code)
            if int(row.get("pending_recruits", 0) or 0) > 0:
                return
            count = StaticUtils._safe_float(row.get("count"), 0) or 0
            available = StaticUtils._safe_float(row.get("available"), 0) or 0
            if count >= minimum and available >= minimum:
                return
            shortage = max(1, minimum - int(count), minimum - int(available))
            candidates.append({
                "department": code,
                "num_people": max(
                    shortage,
                    StaticUtils._single_case_hr_recruit_people_from_action_dir(
                        action_dir=action_dir,
                        enterprise_name=enterprise_name,
                        default=2,
                    ),
                ),
                "priority": priority,
                "staffing": row,
                "reason": reason,
            })

        production_snapshot = StaticUtils._load_sibling_department_state_snapshot(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
            department="production",
            round_id=round_id,
        )
        sales_snapshot = StaticUtils._load_sibling_department_state_snapshot(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
            department="sales",
            round_id=round_id,
        )
        production_self = (production_snapshot or {}).get("self_state") or {}
        sales_self = (sales_snapshot or {}).get("self_state") or {}
        recovery_guard = production_self.get("recovery_guard") or {}
        service_summary = sales_self.get("service_level_summary") or {}
        backlog = StaticUtils._safe_float(
            service_summary.get("confirmed_order_backlog_quantity"),
            StaticUtils._safe_float(service_summary.get("stale_backlog_quantity"), 0),
        ) or 0
        production_recovery_needed = bool((recovery_guard.get("summary") or {}).get("should_recover_any")) or backlog > 0
        if production_recovery_needed:
            add_candidate("PRODUCTION", 2, 10, "production has backlog or recovery signals and insufficient staff")

        # single-enterprise Diagnosis, if parameter 0 people will make all procurement/ replenishment actions necessarily fail.
        add_candidate("PROCUREMENT", 1, 20, "procurement has no available staff, blocking purchasing and replenishment")
        add_candidate("SALES", 1, 50, "sales has no available staff, blocking market and order actions")
        add_candidate("INVENTORY", 1, 60, "inventory has no available staff, blocking warehouse expansion")
        add_candidate("HR", 1, 70, "human resources has no available staff, blocking sustained recruitment and workforce management")
        add_candidate("FINANCE", 1, 80, "finance has no available staff, blocking sustained cash and financial management")

        return sorted(candidates, key=lambda item: item.get("priority", 999))

    @staticmethod
    def _single_case_staffing_block_reason(
        *,
        action_name: str,
        action_param: Dict[str, Any],
        department: str,
        action_dir: Optional[Path],
        enterprise_name: str,
        round_id: Optional[int],
    ) -> str:
        if not isinstance(action_param, dict):
            action_param = {}

        production_build_workers = {
            "small": 1,
            "medium": 2,
            "large": 3,
        }
        warehouse_expand_workers = {
            500: 1,
            1000: 1,
            2000: 2,
            5000: 3,
        }
        line_type = str(action_param.get("line_type") or "small").lower()
        warehouse_size = int(StaticUtils._safe_float(action_param.get("size"), 0) or 0)
        production_quantity = StaticUtils._safe_float(action_param.get("quantity"), 0) or 0
        production_plan_workers = int(max(1, math.ceil(production_quantity / 1000))) if production_quantity > 0 else 1
        sales_market_workers = int(StaticUtils._safe_float(action_param.get("assigned_workers"), 1) or 1)
        sales_adjust_workers = int(StaticUtils._safe_float(action_param.get("adjust_nums"), 1) or 1)
        action_requirements = {
            ("procurement", "accept_proposal_order"): ("PROCUREMENT", 1),
            ("procurement", "reject_proposal_order"): ("PROCUREMENT", 1),
            ("procurement", "register_supplier"): ("PROCUREMENT", 1),
            ("procurement", "create_purchase_order"): ("PROCUREMENT", 1),
            ("procurement", "create_replenishment_order"): ("PROCUREMENT", 1),
            ("procurement", "create_purchase_demand"): ("PROCUREMENT", 1),
            ("procurement", "cancel_order"): ("PROCUREMENT", 1),
            ("production", "create_production_plan"): ("PRODUCTION", production_plan_workers),
            ("production", "build_production_line"): ("PRODUCTION", production_build_workers.get(line_type, 1)),
            ("production", "cancel_production_plan"): ("PRODUCTION", 1),
            ("production", "interrupt_production_plan"): ("PRODUCTION", 1),
            ("production", "resume_production_plan"): ("PRODUCTION", 1),
            ("sales", "adjust_sales_demand"): ("SALES", 1),
            ("sales", "develop_market"): ("SALES", sales_market_workers),
            ("sales", "adjust_market_workers"): (
                "SALES",
                sales_adjust_workers if str(action_param.get("adjust_type") or "").lower() == "increase" else 0,
            ),
            ("sales", "send_quotation"): ("SALES", 1),
            ("sales", "generate_quotation"): ("SALES", 1),
            ("sales", "respond_to_quotation"): ("SALES", 1),
            ("sales", "evaluate_counteroffer"): ("SALES", 1),
            ("inventory", "expand_warehouse"): ("INVENTORY", warehouse_expand_workers.get(warehouse_size, 1)),
        }
        requirement = action_requirements.get((department, action_name))
        if not requirement or requirement[1] <= 0:
            return ""

        hr_snapshot = StaticUtils._load_sibling_department_state_snapshot(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
            department="hr",
            round_id=round_id,
        )
        if not hr_snapshot:
            return ""
        staffing = StaticUtils._normalize_hr_staffing_map(hr_snapshot)
        staff_department, required_workers = requirement
        row = staffing.get(staff_department) or StaticUtils._empty_staffing_record(staff_department)
        available = StaticUtils._safe_float(row.get("available"), 0) or 0
        if available >= required_workers:
            return ""
        return (
            f"{staff_department} has insufficient available staff: {available:g} available, "
            f"while action {action_name} requires at least {required_workers:g}. Wait for HR recruitment before retrying."
        )

    @staticmethod
    def _single_case_staff_shortage_recovery_plan_param(
        *,
        state_snapshot: Dict[str, Any],
        hr_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(state_snapshot, dict):
            return {}
        free_workers = StaticUtils._single_case_production_free_workers(hr_snapshot)
        if free_workers <= 0:
            return {}

        self_state = (
            state_snapshot.get("self_state")
            if isinstance(state_snapshot.get("self_state"), dict)
            else state_snapshot
        )
        if not isinstance(self_state, dict):
            return {}

        constraints = (
            state_snapshot.get("policy_context", {}).get("action_constraints", {})
            if isinstance(state_snapshot.get("policy_context"), dict)
            else {}
        )
        if constraints.get("allow_create_production_plan") is False:
            return {}

        production_lines = self_state.get("production_lines") or {}
        if not isinstance(production_lines, dict):
            return {}
        available_capacity = StaticUtils._safe_float(
            production_lines.get("available_capacity"),
            0.0,
        ) or 0.0
        if available_capacity <= 0:
            return {}

        product_id = "PRODUCT_1"
        recovery_guard = self_state.get("recovery_guard") or {}
        candidates = recovery_guard.get("candidates") if isinstance(recovery_guard, dict) else []
        selected_candidate = None
        for candidate in candidates or []:
            if not isinstance(candidate, dict) or candidate.get("product_id") != product_id:
                continue
            selected_candidate = candidate
            break
        if not isinstance(selected_candidate, dict):
            return {}

        blocking_reasons = set(selected_candidate.get("blocking_reasons") or [])
        material_feasible_quantity = StaticUtils._safe_float(
            selected_candidate.get("material_feasible_quantity"),
            0.0,
        ) or 0.0
        if (
            "NO_MATERIAL_FEASIBILITY" in blocking_reasons
            or "MATERIAL_SHORTAGE" in blocking_reasons
            or material_feasible_quantity <= 0
        ):
            return {}

        backlog_quantity = max(
            StaticUtils._safe_float(selected_candidate.get("confirmed_order_backlog_quantity"), 0.0) or 0.0,
            StaticUtils._safe_float(selected_candidate.get("demand_backlog_quantity"), 0.0) or 0.0,
            StaticUtils._safe_float(selected_candidate.get("stale_backlog_quantity"), 0.0) or 0.0,
        )
        should_recover = bool(selected_candidate.get("should_recover_now")) or backlog_quantity > 0
        if not should_recover:
            return {}

        recommended_quantity = StaticUtils._safe_float(
            selected_candidate.get("recommended_plan_quantity"),
            0.0,
        ) or 0.0
        recommended_daily = StaticUtils._safe_float(
            selected_candidate.get("recommended_daily_capacity"),
            recommended_quantity,
        ) or 0.0

        quantity_candidates = [
            available_capacity,
            material_feasible_quantity,
        ]
        if recommended_quantity > 0:
            quantity_candidates.append(recommended_quantity)
        if backlog_quantity > 0:
            quantity_candidates.append(backlog_quantity)
        quantity = min(quantity_candidates)
        if quantity <= 0:
            return {}

        daily_capacity = min(
            quantity,
            available_capacity,
            recommended_daily if recommended_daily > 0 else quantity,
        )
        if daily_capacity <= 0:
            return {}
        return {
            "product_id": product_id,
            "quantity": int(round(quantity)),
            "daily_capacity": int(round(daily_capacity)),
        }

    @staticmethod
    def _get_already_responded_proposal_ids(action_dir: Path, department: str) -> Set[str]:
        """Scan historical error files and identify proposal_id that have been successfully responded to on this side."""
        responded: Set[str] = set()
        if not action_dir.exists():
            return responded
        pattern = re.compile(r"proposal[_\w\d]+")
        for error_path in sorted(action_dir.glob(f"{department}_error_*.json")):
            try:
                content = error_path.read_text(encoding="utf-8")
            except Exception:
                continue
            if "already has a procurement response" not in content and "already has a sales response" not in content:
                continue
            responded.update(pattern.findall(content))
        return responded

    @staticmethod
    def _is_single_case_prewarm_seed_context(
        *,
        action_dir: Optional[Path],
        round_id: Optional[int],
        enterprise_name: str,
    ) -> bool:
        try:
            current_round = int(round_id)
        except (TypeError, ValueError):
            return False

        context = StaticUtils._single_case_context_from_action_dir(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
        )
        if not context:
            return False

        case_policy = context.get("case_policy") or {}
        handoff_day = (
            case_policy.get("handoff_day")
            or context.get("run_meta", {}).get("handoff_day")
            or case_policy.get("prewarm_rounds")
            or context.get("run_meta", {}).get("prewarm_rounds")
            or 0
        )
        try:
            handoff_day = int(handoff_day)
        except (TypeError, ValueError):
            return False
        return handoff_day > 0 and current_round < handoff_day

    @staticmethod
    def _single_case_context_from_action_dir(
        *,
        action_dir: Optional[Path],
        enterprise_name: str,
    ) -> Dict[str, Any]:
        if action_dir is None:
            return {}
        try:
            search_root = Path(action_dir)
            parents = [search_root, *search_root.parents]
        except Exception:
            return {}

        run_meta_path = None
        for parent in parents:
            candidate = parent / "run_meta.json"
            if candidate.exists():
                run_meta_path = candidate
                break
        if run_meta_path is None:
            return {}

        try:
            run_meta = json.loads(run_meta_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if str(run_meta.get("orchestrator") or "") != "single_enterprise":
            return {}

        enterprise_ids = [str(item) for item in run_meta.get("enterprise_ids") or []]
        if enterprise_ids and str(enterprise_name) not in enterprise_ids:
            return {}

        case_policy = run_meta.get("single_enterprise_case") or {}
        if not isinstance(case_policy, dict) or not case_policy.get("enabled"):
            return {}
        return {"run_meta": run_meta, "case_policy": case_policy}

    @staticmethod
    def _is_single_case_capacity_bottleneck_context(
        *,
        action_dir: Optional[Path],
        round_id: Optional[int],
        enterprise_name: str,
    ) -> bool:
        if round_id is None:
            return False
        try:
            current_round = int(round_id)
        except (TypeError, ValueError):
            return False
        context = StaticUtils._single_case_context_from_action_dir(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
        )
        if not context:
            return False
        case_policy = context.get("case_policy") or {}
        handoff_day = (
            case_policy.get("handoff_day")
            or context.get("run_meta", {}).get("handoff_day")
            or case_policy.get("prewarm_rounds")
            or context.get("run_meta", {}).get("prewarm_rounds")
            or 0
        )
        try:
            handoff_day = int(handoff_day)
        except (TypeError, ValueError):
            return False
        if current_round < handoff_day:
            return False

        production_snapshot = StaticUtils._load_sibling_department_state_snapshot(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
            department="production",
            round_id=round_id,
        )
        if not production_snapshot:
            production_snapshot = StaticUtils._load_department_state_snapshot(
                enterprise_name=enterprise_name,
                department="production",
                round_id=round_id,
            )
        if not production_snapshot:
            return False

        inventory_snapshot = StaticUtils._load_sibling_department_state_snapshot(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
            department="inventory",
            round_id=round_id,
        )
        needs_first_or_recovery_line = StaticUtils._single_enterprise_state_capacity_build_needed(
            production_snapshot,
            inventory_snapshot,
        )
        if needs_first_or_recovery_line:
            return True

        production_self_state = (
            production_snapshot.get("self_state")
            if isinstance(production_snapshot.get("self_state"), dict)
            else production_snapshot
        )
        if not isinstance(production_self_state, dict):
            return False
        production_lines = production_self_state.get("production_lines") or {}
        if not isinstance(production_lines, dict):
            return False
        by_status = production_lines.get("by_status") or {}
        under_construction = sum(
            StaticUtils._safe_float(by_status.get(status), 0.0) or 0.0
            for status in ("under_construction", "building", "pending", "planned")
        )
        available_capacity = StaticUtils._safe_float(
            production_lines.get("available_capacity"),
            0.0,
        ) or 0.0
        return bool(under_construction > 0 or available_capacity > 0)

    @staticmethod
    def _single_case_diagnostic_window_active(
        case_policy: Dict[str, Any],
        round_id: Optional[int],
    ) -> bool:
        if not isinstance(case_policy, dict) or not case_policy.get("enabled"):
            return False
        explicit_active = case_policy.get("diagnostic_window_active")
        if isinstance(explicit_active, bool):
            return explicit_active
        try:
            current_round = int(round_id)
        except (TypeError, ValueError):
            return False
        diagnostic_rounds = {
            int(item)
            for item in (case_policy.get("diagnostic_evaluation_rounds") or [])
            if str(item).lstrip("-").isdigit()
        }
        if current_round in diagnostic_rounds:
            return True
        handoff_day = (
            case_policy.get("handoff_day")
            or case_policy.get("prewarm_rounds")
            or 0
        )
        try:
            return current_round >= int(handoff_day)
        except (TypeError, ValueError):
            return True

    @staticmethod
    def _single_case_hr_recruit_people_from_action_dir(
        *,
        action_dir: Optional[Path],
        enterprise_name: str,
        default: int = 2,
    ) -> int:
        context = StaticUtils._single_case_context_from_action_dir(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
        )
        run_meta = context.get("run_meta") or {}
        scenario_config = run_meta.get("scenario_config") or {}
        runtime_injection = scenario_config.get("runtime_injection") or {}
        scripted_policy = runtime_injection.get("scripted_rule_policy") or {}
        rules = scripted_policy.get("single_enterprise_rules") or {}
        value = rules.get("hr_recruit_people", default)
        try:
            recruit_people = int(value)
        except (TypeError, ValueError):
            recruit_people = default
        return max(1, min(10, recruit_people))

    @staticmethod
    def _build_single_case_hr_recruitment_record(
        *,
        department_to_recruit: str,
        num_people: int,
        enterprise_name: str,
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "action": {
                "action_name": "handle_recruitment",
                "action_param": {
                    "department": department_to_recruit,
                    "num_people": max(1, min(10, int(num_people))),
                },
            },
            "action_reason": reason,
            "module_type": "HRManager",
            "executor_id": enterprise_name,
        }

    @staticmethod
    def _normalize_department_workflow(
        payload: Any,
        *,
        department: Optional[str],
        enterprise_name: str,
        round_id: Optional[int],
        action_dir: Optional[Path] = None,
    ) -> Any:
        'procurement/sales Action Parameters are tightened before execution and filters duplicate or invalid responses.'
        if department not in {"procurement", "sales", "production", "hr", "inventory"} or not isinstance(payload, list):
            return payload

        state_snapshot = StaticUtils._load_sibling_department_state_snapshot(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
            department=department,
            round_id=round_id,
        )
        if not state_snapshot:
            state_snapshot = StaticUtils._load_department_state_snapshot(
                enterprise_name=enterprise_name,
                department=department,
                round_id=round_id,
            )
        single_case_context = StaticUtils._single_case_context_from_action_dir(
            action_dir=action_dir,
            enterprise_name=enterprise_name,
        )
        if department == "procurement":
            state_snapshot = StaticUtils._augment_procurement_state_with_single_case_suppliers(
                state_snapshot,
                single_case_context,
                enterprise_name,
            )
        pending_proposal_ids: Set[str] = set()
        state_proposal_index: Dict[str, Dict[str, Any]] = {}
        exchange_proposal_index: Dict[str, Dict[str, Any]] = {}
        exchange_buy_request_index: Dict[str, Dict[str, Any]] = {}
        already_responded_ids: Set[str] = set()
        pending_proposal_material_ids: Dict[str, str] = {}
        procurement_allowed_material_ids: Set[str] = set()
        state_has_proposal_snapshot = bool(StaticUtils._iter_state_proposals(state_snapshot))
        if department in {"procurement", "sales"}:
            state_proposal_index = StaticUtils._get_state_proposal_index(state_snapshot, department)
            pending_proposal_ids = set(state_proposal_index.keys())
            exchange_proposal_index = StaticUtils._get_exchange_proposal_index(round_id)
            exchange_buy_request_index = StaticUtils._get_exchange_buy_request_index(round_id)
            already_responded_ids = StaticUtils._get_already_responded_proposal_ids(
                action_dir or Path("."),
                department,
            )
        if department == "procurement":
            pending_proposal_material_ids = StaticUtils._get_pending_proposal_material_ids(state_snapshot)
            procurement_allowed_material_ids = StaticUtils._get_procurement_allowed_material_ids(state_snapshot)
        available_sales_order_ids: Set[str] = (
            StaticUtils._get_available_sales_order_ids(state_snapshot)
            if department == "sales"
            else set()
        )
        available_sales_order_index: Dict[str, Dict[str, Any]] = (
            StaticUtils._get_available_sales_order_index(state_snapshot)
            if department == "sales"
            else {}
        )
        normalized: List[Dict[str, Any]] = []
        seen_proposal_actions: Set[tuple] = set()
        seen_accept_buy_request_ids: Set[str] = set()
        seen_sales_order_ids: Set[str] = set()
        reserved_sales_finished_goods: Dict[str, float] = {}
        payload_has_production_plan = any(
            isinstance(item, dict)
            and isinstance(item.get("action"), dict)
            and item["action"].get("action_name") == "create_production_plan"
            for item in payload
        )
        production_margin_candidates = []
        production_recovery_candidates = []
        production_margin_summary = {}
        cobweb_decision_signal = {}
        cobweb_scripted_formula_controls_quantity = False
        cobweb_endogenous_price_signal_available = False
        action_constraints = {}
        policy_context = state_snapshot.get("policy_context") or {}
        active_modes: Dict[str, Any] = {}
        shared_resource_product_id = None
        shared_resource_plan_enabled = False
        herding_product_id = None
        herding_plan_enabled = False
        single_case_policy = {}
        single_case_prewarm_seed_context = StaticUtils._is_single_case_prewarm_seed_context(
            action_dir=action_dir,
            round_id=round_id,
            enterprise_name=enterprise_name,
        )
        run_meta_single_case_policy = (single_case_context.get("case_policy") or {})
        single_case_capacity_bottleneck_context = False
        if isinstance(policy_context, dict):
            single_case_policy = (
                policy_context.get("single_enterprise_diagnostic_policy")
                or policy_context.get("single_enterprise_case_policy")
                or {}
            )
            action_constraints = policy_context.get("action_constraints") or {}
            active_modes = policy_context.get("active_modes") or {}
            relevant_policies = policy_context.get("relevant_policies") or {}
            shared_resource_policy = relevant_policies.get("shared_resource") or {}
            shared_resource_product_id = ((shared_resource_policy.get("resource") or {}).get("product_id"))
            shared_resource_plan_enabled = bool(
                active_modes.get("shared_resource")
                and action_constraints.get("shared_resource_acquisition_plan_enabled")
                and shared_resource_product_id
            )
            herding_policy = relevant_policies.get("herding_signal") or {}
            herding_product_id = herding_policy.get("product_id")
            herding_plan_enabled = bool(
                active_modes.get("herding")
                and action_constraints.get("herding_signal_enabled")
                and herding_product_id
            )
        if department == "production":
            cobweb_decision_signal = (
                ((state_snapshot or {}).get("self_state") or {}).get("cobweb_decision_signal")
                or StaticUtils.build_cobweb_decision_signal(
                    (state_snapshot or {}).get("simulation_context") or {},
                    round_id=round_id,
                )
            )
            cobweb_scripted_formula_controls_quantity = bool(
                isinstance(cobweb_decision_signal, dict)
                and cobweb_decision_signal.get("enabled")
                and cobweb_decision_signal.get("production_response_mode") == "scripted_formula"
                and action_constraints.get("cobweb_plan_quantity_overrides_recovery_guard") is True
            )
            cobweb_endogenous_price_signal_available = bool(
                isinstance(cobweb_decision_signal, dict)
                and cobweb_decision_signal.get("enabled")
                and cobweb_decision_signal.get("production_response_mode") == "agent_endogenous"
                and cobweb_decision_signal.get("suggested_supply_direction")
                in {"expand_supply", "contract_supply", "hold_near_equilibrium"}
            )
            production_margin_candidates = (
                ((state_snapshot or {}).get("self_state") or {})
                .get("margin_guard", {})
                .get("candidates", [])
            )
            production_margin_summary = (
                ((state_snapshot or {}).get("self_state") or {})
                .get("margin_guard", {})
                .get("summary", {})
            )
            production_recovery_candidates = (
                ((state_snapshot or {}).get("self_state") or {})
                .get("recovery_guard", {})
                .get("candidates", [])
            )
        single_case_active = StaticUtils._single_case_diagnostic_window_active(
            single_case_policy,
            round_id,
        ) or StaticUtils._single_case_diagnostic_window_active(
            run_meta_single_case_policy,
            round_id,
        )
        fd_policy = policy_from_meta(single_case_context.get("run_meta") or {}, round_id)
        physical_fd2 = fd2_physical_execution_policy(fd_policy)
        single_case_capacity_bottleneck_context = StaticUtils._is_single_case_capacity_bottleneck_context(
            action_dir=action_dir,
            round_id=round_id,
            enterprise_name=enterprise_name,
        )
        # Do not expose hidden single-case answer keys to department prompts.
        # Execution-time normalization may still use run metadata for technical
        # compatibility, such as the single-enterprise diagnostic capacity flag.
        single_case_discouraged_actions: Set[str] = set()
        expected_primary_departments: Set[str] = set()
        primary_issue = str(
            run_meta_single_case_policy.get("primary_issue")
            or single_case_policy.get("primary_issue")
            or run_meta_single_case_policy.get("source_primary_contradiction")
            or single_case_policy.get("source_primary_contradiction")
            or ""
        )
        case_id = str(
            run_meta_single_case_policy.get("case_id")
            or single_case_policy.get("case_id")
            or ""
        )
        single_case_capacity_bottleneck_context = (
            single_case_capacity_bottleneck_context
            or "capacity" in case_id
            or "capacity" in primary_issue
            or 'Production capacity' in primary_issue
        )
        single_case_material_shortage_context = (
            "material" in case_id
            or "material" in primary_issue
            or 'material' in primary_issue
        )
        single_case_staff_shortage_context = (
            "staff" in case_id
            or "staff" in primary_issue
            or 'staff' in primary_issue
        )
        if physical_fd2:
            # Scenario diagnostics must not replace a selected business action.
            single_case_capacity_bottleneck_context = False

        if (
            department == "sales"
            and single_case_active
            and not single_case_prewarm_seed_context
            and not physical_fd2
        ):
            payload = StaticUtils._reorder_single_case_sales_accept_actions_by_quality(
                payload,
                available_sales_order_index=available_sales_order_index,
                quality_review_index=StaticUtils._single_case_sales_order_quality_review_index(
                    state_snapshot
                ),
            )

        for item in payload:
            if not isinstance(item, dict):
                normalized.append(item)
                continue

            action = item.get("action")
            if not isinstance(action, dict):
                normalized.append(item)
                continue

            item = dict(item)
            action = dict(action)
            item["action"] = action
            if isinstance(item.get("action_reason"), str):
                item["action_reason"] = StaticUtils._sanitize_single_enterprise_visible_text(
                    item.get("action_reason")
                )
            if isinstance(action.get("action_param"), str):
                action["action_param"] = StaticUtils._sanitize_single_enterprise_visible_text(
                    action.get("action_param")
                )

            action_name = action.get("action_name")
            action_param = action.get("action_param", {})
            is_single_case_prewarm_seed_action = bool(
                single_case_prewarm_seed_context
                and item.get("single_case_prewarm_seed") is True
            )
            if not is_single_case_prewarm_seed_action:
                item.pop("single_case_prewarm_seed", None)
                item.pop("_single_case_prewarm_seed", None)
            if (
                department == "hr"
                and single_case_active
                and not fd_policy
                and not is_single_case_prewarm_seed_action
                and action_name == "action_pass"
            ):
                hr_candidates = StaticUtils._single_case_hr_staffing_candidates(
                    action_dir=action_dir,
                    enterprise_name=enterprise_name,
                    round_id=round_id,
                )
                if hr_candidates:
                    candidate = hr_candidates[0]
                    staffing = candidate.get("staffing") or {}
                    normalized.append(
                        StaticUtils._build_single_case_hr_recruitment_record(
                            department_to_recruit=candidate["department"],
                            num_people=candidate["num_people"],
                            enterprise_name=enterprise_name,
                            reason=(
                                f'HR state establishes an uncovered hard staffing gap; replace pass with recruitment for {candidate['department']} {candidate['num_people']} people. Current headcount {staffing.get('count', 0)}, available {staffing.get('available', 0)}, reason: {candidate.get('reason')}'
                            ),
                        )
                    )
                    continue
            if department in {"procurement", "sales", "production", "hr", "inventory"} and action_name == "action_pass":
                reason = action_param if isinstance(action_param, str) else item.get("action_reason")
                normalized.append(
                    StaticUtils._build_action_pass_record(
                        str(reason or f'{department} judges that no action is necessary this round.'),
                        department,
                        enterprise_name,
                    )
                )
                continue
            if not isinstance(action_param, dict):
                normalized.append(item)
                continue
            if (
                single_case_active
                and not is_single_case_prewarm_seed_action
                and action_name in single_case_discouraged_actions
            ):
                normalized.append(
                    StaticUtils._build_action_pass_record(
                        (
                            'The action conflicts with current diagnostic constraints; wait for a real executable candidate in this department.'
                        ),
                        department,
                        enterprise_name,
                    )
                )
                continue

            if (
                department == "procurement"
                and action_name == "create_replenishment_order"
                and action_constraints.get("allow_create_replenishment_order") is False
            ):
                continue
            if (
                department == "procurement"
                and action_name == "create_purchase_order"
                and action_constraints.get("allow_external_purchase_order") is False
            ):
                continue
            if (
                department == "sales"
                and action_name == "adjust_sales_demand"
                and action_constraints.get("allow_adjust_sales_demand") is False
            ):
                continue
            if (
                department == "production"
                and action_name == "create_production_plan"
                and action_constraints.get("allow_create_production_plan") is False
            ):
                continue
            if (
                department == "production"
                and action_name == "build_production_line"
                and action_constraints.get("allow_build_production_line") is False
            ):
                continue

            cleaned_param = dict(action_param)
            cleaned_param.pop("single_case_prewarm_seed", None)
            cleaned_param.pop("_single_case_prewarm_seed", None)

            if physical_fd2 and (department, action_name) in {
                ("sales", "accept_order"), ("sales", "reject_order"),
                ("production", "create_production_plan"), ("procurement", "create_purchase_order"),
            }:
                # Keep aliases/shape compatibility, but let the business module
                # return errors instead of silently selecting pass/reject/a batch.
                keys = {
                    "accept_order": {"order_id"}, "reject_order": {"order_id", "reason"},
                    "create_production_plan": {"product_id", "quantity", "daily_capacity"},
                    "create_purchase_order": {"material_id", "quantity", "supplier_name", "logistics_mode"},
                }[action_name]
                if action_name == "create_purchase_order":
                    if "logistics_mode" not in cleaned_param and "logistics_type" in cleaned_param:
                        cleaned_param["logistics_mode"] = cleaned_param["logistics_type"]
                    if not cleaned_param.get("supplier_name") and cleaned_param.get("supplier_id"):
                        lookup = StaticUtils._get_procurement_supplier_lookup(state_snapshot)
                        identity = str(cleaned_param["supplier_id"])
                        cleaned_param["supplier_name"] = (lookup.get("id_to_name") or {}).get(identity, identity)
                for key in ("quantity", "daily_capacity"):
                    if key in cleaned_param:
                        number = StaticUtils._safe_float(cleaned_param[key], None)
                        if number is not None:
                            cleaned_param[key] = number
                action["action_param"] = {key: value for key, value in cleaned_param.items() if key in keys}
                normalized.append(item)
                continue

            if (
                single_case_active
                and not is_single_case_prewarm_seed_action
                and department in {"procurement", "production", "sales", "inventory"}
                and not physical_fd2
            ):
                staffing_block_reason = StaticUtils._single_case_staffing_block_reason(
                    action_name=action_name,
                    action_param=cleaned_param,
                    department=department,
                    action_dir=action_dir,
                    enterprise_name=enterprise_name,
                    round_id=round_id,
                )
                if staffing_block_reason:
                    normalized.append(
                        StaticUtils._build_action_pass_record(
                            staffing_block_reason,
                            department,
                            enterprise_name,
                        )
                    )
                    continue

            if (
                department == "hr"
                and single_case_active
                and not fd_policy
                and not is_single_case_prewarm_seed_action
                and action_name == "handle_recruitment"
            ):
                hr_candidates = StaticUtils._single_case_hr_staffing_candidates(
                    action_dir=action_dir,
                    enterprise_name=enterprise_name,
                    round_id=round_id,
                )
                requested_department = StaticUtils._canonical_staff_department_code(
                    cleaned_param.get("department")
                )
                matching_candidate = next(
                    (
                        candidate
                        for candidate in hr_candidates
                        if candidate.get("department") == requested_department
                    ),
                    None,
                )
                selected_candidate = matching_candidate or (hr_candidates[0] if hr_candidates else None)
                if not selected_candidate:
                    normalized.append(
                        StaticUtils._build_action_pass_record(
                            (
                                'Recruitment filtered: current staffing and pending hires do not establish an uncovered hard gap; avoid duplicate or incorrect hiring.'
                            ),
                            department,
                            enterprise_name,
                        )
                    )
                    continue
                if selected_candidate.get("department") != requested_department:
                    staffing = selected_candidate.get("staffing") or {}
                    cleaned_param["department"] = selected_candidate["department"]
                    cleaned_param["num_people"] = selected_candidate["num_people"]
                    action["action_param"] = cleaned_param
                    item["action_reason"] = (
                        f'Correct recruitment from current staffing gaps; prioritize {selected_candidate['department']} {selected_candidate['num_people']} people. Current headcount {staffing.get('count', 0)}, available {staffing.get('available', 0)}, reason: {selected_candidate.get('reason')}'
                    )
                else:
                    cleaned_param["department"] = selected_candidate["department"]
                    cleaned_param["num_people"] = max(
                        int(StaticUtils._safe_float(cleaned_param.get("num_people"), 1) or 1),
                        int(selected_candidate.get("num_people", 1) or 1),
                    )
                    action["action_param"] = cleaned_param

            if department == "production" and action_name == "build_production_line":
                line_type = str(cleaned_param.get("line_type") or "").strip()
                if (
                    single_case_active
                    and not is_single_case_prewarm_seed_action
                ):
                    # In the formal single-enterprise takeover stage, do not
                    # silently suppress Agent build-line decisions via
                    # diagnostic/margin/recovery guards. Normalize only the
                    # executable shape and let ProductionManager perform the
                    # real business validation.
                    if line_type not in {"small", "medium", "large"} and not physical_fd2:
                        line_type = "small"
                    cleaned_param = {
                        "line_type": line_type,
                        "single_enterprise_capacity_recovery": True,
                    }
                else:
                    inventory_snapshot_for_capacity = StaticUtils._load_sibling_department_state_snapshot(
                        action_dir=action_dir,
                        enterprise_name=enterprise_name,
                        department="inventory",
                        round_id=round_id,
                    )
                    state_capacity_build_needed = bool(
                        single_case_active
                        and not is_single_case_prewarm_seed_action
                        and StaticUtils._single_enterprise_state_capacity_build_needed(
                            state_snapshot,
                            inventory_snapshot_for_capacity,
                        )
                    )
                    if line_type not in {"small", "medium", "large"}:
                        if single_case_capacity_bottleneck_context or state_capacity_build_needed:
                            line_type = "small"
                        else:
                            continue
                    cleaned_param = {"line_type": line_type}
                    if isinstance(production_margin_summary, dict) and production_margin_summary:
                        has_expansion_candidate = bool(
                            production_margin_summary.get("has_capacity_expansion_candidate")
                            or production_margin_summary.get("capacity_expansion_candidate_count")
                        )
                        if (
                            not has_expansion_candidate
                            and not is_single_case_prewarm_seed_action
                            and not single_case_capacity_bottleneck_context
                            and not state_capacity_build_needed
                        ):
                            continue
                    if (
                        single_case_capacity_bottleneck_context
                        and not is_single_case_prewarm_seed_action
                        and StaticUtils._single_case_capacity_bottleneck_already_expanded(
                            state_snapshot,
                        )
                    ):
                        if payload_has_production_plan:
                            normalized.append(
                                StaticUtils._build_action_pass_record(
                                    (
                                        'Existing new/available capacity makes another line redundant; retain the existing production action.'
                                    ),
                                    department,
                                    enterprise_name,
                                )
                            )
                            continue
                        recovery_plan = StaticUtils._single_case_capacity_bottleneck_recovery_plan_param(
                            state_snapshot,
                        )
                        if recovery_plan:
                            normalized.append(
                                StaticUtils._build_department_action_record(
                                    action_name="create_production_plan",
                                    action_param=recovery_plan,
                                    action_reason=(
                                        'After the initial expansion, replace another build_production_line with a bounded PRODUCT_1 plan on existing idle capacity.'
                                    ),
                                    department="production",
                                    enterprise_name=enterprise_name,
                                )
                            )
                        else:
                            normalized.append(
                                StaticUtils._build_action_pass_record(
                                    (
                                        'Existing/new construction is sufficient and no idle capacity supports a new plan; do not build another line.'
                                    ),
                                    department,
                                    enterprise_name,
                                )
                            )
                        continue
                    if is_single_case_prewarm_seed_action:
                        cleaned_param["single_case_prewarm_seed"] = True
                    if single_case_capacity_bottleneck_context:
                        cleaned_param["single_enterprise_capacity_recovery"] = True

            if department == "procurement" and action_name == "create_replenishment_order":
                if "requested_quantity" in cleaned_param and "quantity" not in cleaned_param:
                    cleaned_param["quantity"] = cleaned_param["requested_quantity"]
                cleaned_param.pop("requested_quantity", None)
                material_id = cleaned_param.get("material_id")
                if material_id in (None, ""):
                    continue
                if procurement_allowed_material_ids and str(material_id) not in procurement_allowed_material_ids:
                    continue
                allowed_keys = {
                    "material_id",
                    "target_inventory_days",
                    "quantity",
                    "safety_stock",
                    "expected_daily_demand",
                    "max_price",
                    "expected_due_round",
                }
                cleaned_param = {
                    key: value
                    for key, value in cleaned_param.items()
                    if key in allowed_keys and value not in (None, "")
                }
                cleaned_param["material_id"] = material_id

            if department == "procurement" and action_name == "create_purchase_order":
                if "logistics_type" in cleaned_param and "logistics_mode" not in cleaned_param:
                    cleaned_param["logistics_mode"] = cleaned_param["logistics_type"]
                cleaned_param.pop("logistics_type", None)

                material_id = cleaned_param.get("material_id")
                quantity = cleaned_param.get("quantity")
                supplier_name = cleaned_param.get("supplier_name")
                supplier_id = cleaned_param.get("supplier_id")
                logistics_mode = cleaned_param.get("logistics_mode")
                if (
                    str(logistics_mode or "").lower() not in {"road", "rail", "air"}
                    and not isinstance(material_id, list)
                    and not isinstance(quantity, list)
                ):
                    logistics_mode = StaticUtils._select_single_case_procurement_logistics_mode(
                        state_snapshot=state_snapshot,
                        action_dir=action_dir,
                        enterprise_name=enterprise_name,
                        round_id=round_id,
                        material_id=material_id,
                        quantity=quantity,
                    )
                    cleaned_param["logistics_mode"] = logistics_mode

                if isinstance(material_id, list) or isinstance(quantity, list):
                    material_list = material_id if isinstance(material_id, list) else [material_id]
                    quantity_list = quantity if isinstance(quantity, list) else [quantity]
                    if len(material_list) == len(quantity_list) and len(material_list) > 0:
                        for material_item, quantity_item in zip(material_list, quantity_list):
                            if material_item in (None, ""):
                                continue
                            if (
                                procurement_allowed_material_ids
                                and str(material_item) not in procurement_allowed_material_ids
                            ):
                                continue
                            resolved_supplier_name = StaticUtils._resolve_procurement_supplier_name(
                                state_snapshot,
                                material_item,
                                supplier_name=supplier_name,
                                supplier_id=supplier_id,
                            )
                            if not resolved_supplier_name:
                                continue
                            split_logistics_mode = logistics_mode
                            if str(split_logistics_mode or "").lower() not in {"road", "rail", "air"}:
                                split_logistics_mode = StaticUtils._select_single_case_procurement_logistics_mode(
                                    state_snapshot=state_snapshot,
                                    action_dir=action_dir,
                                    enterprise_name=enterprise_name,
                                    round_id=round_id,
                                    material_id=material_item,
                                    quantity=quantity_item,
                                )
                            capped_param = StaticUtils._cap_single_case_top_tier_purchase_param(
                                state_snapshot=state_snapshot,
                                action_dir=action_dir,
                                enterprise_name=enterprise_name,
                                material_id=material_item,
                                quantity=quantity_item,
                                supplier_name=resolved_supplier_name,
                                logistics_mode=split_logistics_mode or "road",
                            )
                            if not capped_param:
                                continue
                            split_item = dict(item)
                            split_action = dict(action)
                            split_action["action_param"] = capped_param
                            split_item["action"] = split_action
                            normalized.append(split_item)
                        continue
                    else:
                        continue

                material_id = cleaned_param.get("material_id")
                quantity = cleaned_param.get("quantity")
                logistics_mode = cleaned_param.get("logistics_mode")
                if str(logistics_mode or "").lower() not in {"road", "rail", "air"}:
                    logistics_mode = StaticUtils._select_single_case_procurement_logistics_mode(
                        state_snapshot=state_snapshot,
                        action_dir=action_dir,
                        enterprise_name=enterprise_name,
                        round_id=round_id,
                        material_id=material_id,
                        quantity=quantity,
                    )
                    cleaned_param["logistics_mode"] = logistics_mode
                if material_id in (None, "") or quantity is None:
                    continue
                if procurement_allowed_material_ids and str(material_id) not in procurement_allowed_material_ids:
                    continue
                if (
                    single_case_active
                    and single_case_material_shortage_context
                    and not is_single_case_prewarm_seed_action
                    and not physical_fd2
                ):
                    coverage = StaticUtils._single_case_material_purchase_already_covered(
                        state_snapshot,
                        material_id,
                        quantity,
                    )
                    if coverage.get("covered"):
                        normalized.append(
                            StaticUtils._build_action_pass_record(
                                (
                                    f'Enterprise-diagnosis state already has sufficient replenishment coverage; {material_id} requested={coverage.get('requested_quantity')}, incoming={coverage.get('incoming')}, inventory_position={coverage.get('inventory_position')}, ordered_total={coverage.get('ordered_total')}. Do not duplicate create_purchase_order this round; wait for production and delivery recovery.'
                                ),
                                department,
                                enterprise_name,
                            )
                        )
                        continue
                supplier_name = StaticUtils._resolve_procurement_supplier_name(
                    state_snapshot,
                    material_id,
                    supplier_name=supplier_name,
                    supplier_id=supplier_id,
                )
                if not supplier_name:
                    continue
                cleaned_param = StaticUtils._cap_single_case_top_tier_purchase_param(
                    state_snapshot=state_snapshot,
                    action_dir=action_dir,
                    enterprise_name=enterprise_name,
                    material_id=material_id,
                    quantity=quantity,
                    supplier_name=supplier_name,
                    logistics_mode=logistics_mode or "road",
                )
                if not cleaned_param:
                    continue

            if department == "sales" and action_name == "adjust_sales_demand":
                product_id = cleaned_param.get("product_id")
                quantity = cleaned_param.get("quantity")
                if product_id in (None, "") or quantity is None:
                    continue
                cleaned_param = {
                    "product_id": product_id,
                    "quantity": quantity,
                }

            if department == "sales" and action_name == "develop_market":
                market_type = cleaned_param.get("market_type")
                assigned_workers = (
                    cleaned_param.get("assigned_workers")
                    if cleaned_param.get("assigned_workers") is not None
                    else cleaned_param.get("workers")
                )
                if assigned_workers is None:
                    assigned_workers = (
                        cleaned_param.get("num_workers")
                        or cleaned_param.get("worker_count")
                        or cleaned_param.get("staff_count")
                        or cleaned_param.get("sales_workers")
                        or cleaned_param.get("assigned_staff")
                    )
                market_name = cleaned_param.get("market_name")
                if market_type not in {"regional", "international"} or assigned_workers is None:
                    continue
                cleaned_param = {
                    "market_type": market_type,
                    "assigned_workers": int(StaticUtils._safe_float(assigned_workers, 0) or 0),
                }
                if cleaned_param["assigned_workers"] <= 0:
                    continue
                if isinstance(market_name, str) and market_name.strip():
                    cleaned_param["market_name"] = market_name.strip()

            if department == "sales" and action_name in {"accept_order", "reject_order"}:
                order_id = cleaned_param.get("order_id")
                if not order_id:
                    continue
                if action_name == "accept_order":
                    order_id = str(order_id)
                    if (
                        is_single_case_prewarm_seed_action
                        and available_sales_order_ids
                        and order_id not in available_sales_order_ids
                    ):
                        replacement_order_id = next(
                            (
                                candidate
                                for candidate in StaticUtils._sort_sales_order_ids(
                                    available_sales_order_ids
                                )
                                if candidate not in seen_sales_order_ids
                            ),
                            None,
                        )
                        if replacement_order_id:
                            item["action_reason"] = (
                                f"{item.get('action_reason', '')} "
                                f"[single_case_prewarm_order_remap: requested={order_id}, "
                                f"using_available={replacement_order_id}]"
                            )
                            order_id = replacement_order_id
                        else:
                            normalized.append(
                                StaticUtils._build_action_pass_record(
                                    (
                                        f'Warm-up requested acceptance of order {order_id}, but that order is absent from the sales snapshot. Convert to pass to avoid a fixed-ID failure interrupting warm-up.'
                                    ),
                                    department,
                                    enterprise_name,
                                )
                            )
                            continue
                    elif (
                        is_single_case_prewarm_seed_action
                        and not available_sales_order_ids
                        and StaticUtils._sales_order_snapshot_present(state_snapshot)
                    ):
                        normalized.append(
                            StaticUtils._build_action_pass_record(
                                (
                                    f'Warm-up requested acceptance of order {order_id}, but that order is absent from the sales snapshot. Convert to pass to avoid a fixed-ID failure interrupting warm-up.'
                                ),
                                department,
                                enterprise_name,
                            )
                        )
                        continue
                    elif available_sales_order_ids and order_id not in available_sales_order_ids:
                        continue
                    elif (
                        not available_sales_order_ids
                        and StaticUtils._sales_order_snapshot_present(state_snapshot)
                    ):
                        continue
                if (
                    action_name == "accept_order"
                    and single_case_active
                    and not is_single_case_prewarm_seed_action
                ):
                    order_detail = available_sales_order_index.get(str(order_id)) or {}
                    if StaticUtils._is_zero_value_sales_order(order_detail):
                        normalized.append(
                            StaticUtils._build_action_pass_record(
                                (
                                    f'Filter zero-quantity/value order {order_id}; do not accept it. Wait for real nonzero demand or an executable state-repair action.'
                                ),
                                department,
                                enterprise_name,
                            )
                        )
                        continue
                    if single_case_capacity_bottleneck_context or single_case_staff_shortage_context:
                        inventory_snapshot = StaticUtils._load_sibling_department_state_snapshot(
                            action_dir=action_dir,
                            enterprise_name=enterprise_name,
                            department="inventory",
                            round_id=round_id,
                        )
                        product_id = str(order_detail.get("product_id") or "PRODUCT_1")
                        service_guard = StaticUtils._single_case_capacity_bottleneck_accept_order_guard(
                            order_detail=order_detail,
                            inventory_snapshot=inventory_snapshot,
                            round_id=round_id,
                            reserved_finished_goods=reserved_sales_finished_goods.get(product_id, 0.0),
                            reject_overdue=single_case_capacity_bottleneck_context,
                        )
                        if not service_guard.get("allowed"):
                            case_label = (
                                'capacity-constraint recovery stage'
                                if single_case_capacity_bottleneck_context
                                else 'staffing-constraint recovery stage'
                            )
                            cleaned_param = {
                                "order_id": order_id,
                                "reason": (
                                    f'Single-enterprise {case_label} rejects an order that cannot be served safely; {service_guard.get('reason')}'
                                ),
                            }
                            action_name = "reject_order"
                            action["action_name"] = action_name
                if action_name == "reject_order":
                    reason = cleaned_param.get("reason")
                    cleaned_param = {"order_id": order_id}
                    if isinstance(reason, str) and reason.strip():
                        cleaned_param["reason"] = reason.strip()
                else:
                    cleaned_param = {"order_id": order_id}
                    seen_sales_order_ids.add(str(order_id))
                    if (
                        single_case_active
                        and not is_single_case_prewarm_seed_action
                        and (single_case_capacity_bottleneck_context or single_case_staff_shortage_context)
                    ):
                        product_id = str(order_detail.get("product_id") or "PRODUCT_1")
                        quantity = StaticUtils._safe_float(order_detail.get("quantity"), 0.0) or 0.0
                        reserved_sales_finished_goods[product_id] = (
                            reserved_sales_finished_goods.get(product_id, 0.0) + max(0.0, quantity)
                        )

            if department == "inventory" and action_name == "expand_warehouse":
                size = (
                    cleaned_param.get("size")
                    if cleaned_param.get("size") is not None
                    else cleaned_param.get("capacity")
                )
                if size is None:
                    size = (
                        cleaned_param.get("warehouse_size")
                        or cleaned_param.get("additional_capacity")
                        or cleaned_param.get("expand_size")
                    )
                size_value = int(StaticUtils._safe_float(size, 0) or 0)
                if size_value not in {1000, 2000, 5000}:
                    continue
                cleaned_param = {"size": size_value}

            if department == "production" and action_name == "create_production_plan":
                product_id = cleaned_param.get("product_id")
                quantity = cleaned_param.get("quantity")
                daily_capacity = cleaned_param.get("daily_capacity")
                if product_id in (None, "") or quantity is None or daily_capacity is None:
                    continue
                quantity_value = StaticUtils._safe_float(quantity, 0.0) or 0.0
                daily_capacity_value = StaticUtils._safe_float(daily_capacity, 0.0) or 0.0
                if quantity_value <= 0 or daily_capacity_value <= 0:
                    continue
                if (
                    active_modes.get("cobweb")
                    and active_modes.get("profit_objective")
                    and daily_capacity_value > quantity_value
                ):
                    daily_capacity_value = quantity_value
                    item["action_reason"] = (
                        f"{item.get('action_reason', '')} "
                        "[c3_cobweb_normalization: "
                        "daily_capacity_capped_to_quantity]"
                    ).strip()
                cleaned_param["quantity"] = quantity_value
                cleaned_param["daily_capacity"] = daily_capacity_value

                if (
                    isinstance(cobweb_decision_signal, dict)
                    and cobweb_decision_signal.get("enabled")
                    and action_constraints.get("cobweb_hard_cap_enabled") is True
                ):
                    cobweb_product_id = cobweb_decision_signal.get("product_id")
                    max_plan_quantity = StaticUtils._safe_float(
                        cobweb_decision_signal.get("recommended_plan_quantity")
                    )
                    if cobweb_product_id and product_id != cobweb_product_id:
                        continue
                    if max_plan_quantity is not None:
                        if max_plan_quantity <= 0:
                            continue
                        quantity_value = StaticUtils._safe_float(quantity, max_plan_quantity)
                        daily_capacity_value = StaticUtils._safe_float(daily_capacity, max_plan_quantity)
                        capped_quantity = min(quantity_value, max_plan_quantity)
                        capped_daily_capacity = min(daily_capacity_value, capped_quantity)
                        cleaned_param["quantity"] = max(1, int(round(capped_quantity)))
                        cleaned_param["daily_capacity"] = max(1, int(round(capped_daily_capacity)))
                        item["action_reason"] = (
                            f"{item.get('action_reason', '')} "
                            f"[cobweb_guard: capped_to_planned_supply={cleaned_param['quantity']}]"
                        ).strip()

                recovery_candidate = None
                for candidate in production_recovery_candidates:
                    if not isinstance(candidate, dict):
                        continue
                    if candidate.get("product_id") == product_id:
                        recovery_candidate = candidate
                        break

                margin_candidate = None
                for candidate in production_margin_candidates:
                    if not isinstance(candidate, dict):
                        continue
                    if candidate.get("product_id") == product_id:
                        margin_candidate = candidate
                        break

                is_shared_resource_plan = (
                    shared_resource_plan_enabled
                    and product_id == shared_resource_product_id
                )
                is_herding_plan = (
                    herding_plan_enabled
                    and product_id == herding_product_id
                )
                single_case_partial_material_recovery_allowed = False
                if is_shared_resource_plan:
                    sustainability_guard = StaticUtils._get_c3_shared_resource_sustainability_guard(
                        policy_context,
                        state_snapshot=state_snapshot,
                    )
                    if sustainability_guard.get("enabled"):
                        acquisition_cap = StaticUtils._safe_float(
                            sustainability_guard.get("dynamic_acquisition_cap")
                        )
                        if acquisition_cap is not None:
                            if acquisition_cap <= 0:
                                continue
                            current_quantity = StaticUtils._safe_float(
                                cleaned_param.get("quantity"),
                                quantity_value,
                            ) or 0.0
                            current_daily_capacity = StaticUtils._safe_float(
                                cleaned_param.get("daily_capacity"),
                                daily_capacity_value,
                            ) or 0.0
                            capped_quantity = min(current_quantity, acquisition_cap)
                            capped_daily_capacity = min(current_daily_capacity, capped_quantity)
                            if capped_quantity <= 0 or capped_daily_capacity <= 0:
                                continue
                            if (
                                capped_quantity < current_quantity
                                or capped_daily_capacity < current_daily_capacity
                            ):
                                item["action_reason"] = (
                                    f"{item.get('action_reason', '')} "
                                    "[c3_shared_resource_guard: "
                                    f"capped_to_sustainable_acquisition={capped_quantity:g}, "
                                    f"warning_level={sustainability_guard.get('warning_level')}]"
                                ).strip()
                            cleaned_param["quantity"] = capped_quantity
                            cleaned_param["daily_capacity"] = capped_daily_capacity
                if (
                    recovery_candidate
                    and not physical_fd2
                    and not is_shared_resource_plan
                    and not is_herding_plan
                    and not cobweb_scripted_formula_controls_quantity
                    and action_constraints.get("must_respect_recovery_guard", True)
                ):
                    blocking_reasons = set(recovery_candidate.get("blocking_reasons") or [])
                    raw_material_snapshots = recovery_candidate.get("raw_material_snapshots") or {}
                    has_raw_material_requirements = bool(raw_material_snapshots)
                    material_feasible_quantity = StaticUtils._safe_float(
                        recovery_candidate.get("material_feasible_quantity"),
                        0.0,
                    ) or 0.0
                    recommended_plan_quantity = StaticUtils._safe_float(
                        recovery_candidate.get("recommended_plan_quantity"),
                        0.0,
                    ) or 0.0
                    recommended_daily_capacity = StaticUtils._safe_float(
                        recovery_candidate.get("recommended_daily_capacity"),
                        recommended_plan_quantity,
                    ) or 0.0
                    has_material_block = bool(
                        "NO_MATERIAL_FEASIBILITY" in blocking_reasons
                        or (
                            "MATERIAL_SHORTAGE" in blocking_reasons
                            and material_feasible_quantity <= 0
                        )
                    )
                    if has_raw_material_requirements and (has_material_block or material_feasible_quantity <= 0):
                        continue
                    if single_case_material_shortage_context and material_feasible_quantity > 0:
                        single_case_partial_material_recovery_allowed = True

                    plan_cap = material_feasible_quantity if has_raw_material_requirements else quantity_value
                    if recommended_plan_quantity > 0:
                        plan_cap = min(plan_cap, recommended_plan_quantity)
                    capped_quantity = min(
                        StaticUtils._safe_float(cleaned_param.get("quantity"), quantity_value) or 0.0,
                        plan_cap,
                    )
                    if capped_quantity <= 0:
                        continue
                    daily_cap = capped_quantity
                    if recommended_daily_capacity > 0:
                        daily_cap = min(daily_cap, recommended_daily_capacity)
                    capped_daily_capacity = min(
                        StaticUtils._safe_float(cleaned_param.get("daily_capacity"), daily_capacity_value) or 0.0,
                        daily_cap,
                    )
                    if capped_daily_capacity <= 0:
                        continue
                    if (
                        capped_quantity < quantity_value
                        or capped_daily_capacity < daily_capacity_value
                    ):
                        item["action_reason"] = (
                            f"{item.get('action_reason', '')} "
                            f"[production_recovery_guard: capped_to_feasible_quantity={capped_quantity:g}]"
                        ).strip()
                    cleaned_param["quantity"] = capped_quantity
                    cleaned_param["daily_capacity"] = capped_daily_capacity

                if (
                    margin_candidate
                    and not physical_fd2
                    and not is_shared_resource_plan
                    and not is_herding_plan
                    and not cobweb_scripted_formula_controls_quantity
                ):
                    if (
                        margin_candidate.get("guard_level") == "hard_blocked"
                        and not margin_candidate.get("allow_service_recovery")
                        and not margin_candidate.get("allow_continuity_recovery")
                        and float(margin_candidate.get("estimated_sale_unit_price") or 0) <= 0
                        and not single_case_partial_material_recovery_allowed
                        and not (
                            cobweb_endogenous_price_signal_available
                            and action_constraints.get(
                                "margin_guard_is_hard_block_only_when_no_recovery_or_price_signal"
                            )
                            is True
                        )
                    ):
                        continue

                cleaned_param = {
                    "product_id": product_id,
                    "quantity": cleaned_param.get("quantity"),
                    "daily_capacity": cleaned_param.get("daily_capacity"),
                }
                if cobweb_scripted_formula_controls_quantity:
                    cleaned_param["_cobweb_scripted_formula_override"] = True

            if action_name in {"accept_proposal_order", "reject_proposal_order"}:
                proposal_id = cleaned_param.get("proposal_id")
                if not proposal_id:
                    continue
                dedupe_key = (action_name, proposal_id)
                if dedupe_key in seen_proposal_actions:
                    continue
                seen_proposal_actions.add(dedupe_key)

                if proposal_id in already_responded_ids:
                    continue
                if state_has_proposal_snapshot and proposal_id not in state_proposal_index:
                    continue
                exchange_proposal = exchange_proposal_index.get(str(proposal_id))
                if exchange_proposal and not StaticUtils._proposal_is_pending_for_response(exchange_proposal, department):
                    continue
                proposal_record = exchange_proposal or state_proposal_index.get(str(proposal_id)) or {}
                buy_request_id = proposal_record.get("buy_request_id")
                if buy_request_id:
                    buy_request = exchange_buy_request_index.get(str(buy_request_id)) or {}
                    lifecycle_status = str(buy_request.get("lifecycle_status") or "").lower()
                    if lifecycle_status in {"superseded", "expired", "cancelled", "canceled"}:
                        continue
                if department == "procurement" and procurement_allowed_material_ids:
                    proposal_material_id = pending_proposal_material_ids.get(proposal_id)
                    if proposal_material_id and str(proposal_material_id) not in procurement_allowed_material_ids:
                        continue
                if department == "procurement" and action_name == "accept_proposal_order":
                    cash_guard = StaticUtils._procurement_acceptance_cash_guard(
                        state_snapshot,
                        proposal_record,
                    )
                    if cash_guard.get("is_hard_blocked"):
                        action_name = "reject_proposal_order"
                        action["action_name"] = action_name
                        item["action_reason"] = (
                            f"{item.get('action_reason', '')} "
                            "[cash_guard: converted accept to reject because projected cash would be negative]"
                        ).strip()
                        converted_dedupe_key = (action_name, proposal_id)
                        if converted_dedupe_key in seen_proposal_actions:
                            continue
                        seen_proposal_actions.add(converted_dedupe_key)
                if action_name == "accept_proposal_order":
                    if buy_request_id:
                        buy_request_key = str(buy_request_id)
                        if buy_request_key in seen_accept_buy_request_ids:
                            continue
                        seen_accept_buy_request_ids.add(buy_request_key)

                cleaned_param = {"proposal_id": proposal_id}

            action["action_param"] = cleaned_param
            item["action"] = action
            normalized.append(item)

        non_pass_actions = [
            item for item in normalized
            if isinstance(item, dict)
            and isinstance(item.get("action"), dict)
            and item["action"].get("action_name") != "action_pass"
        ]
        explicit_pass_actions = [
            item for item in normalized
            if isinstance(item, dict)
            and isinstance(item.get("action"), dict)
            and item["action"].get("action_name") == "action_pass"
        ]
        if (
            department == "production"
            and single_case_active
            and not fd_policy
            and single_case_material_shortage_context
            and not single_case_prewarm_seed_context
            and not non_pass_actions
        ):
            recovery_plan = StaticUtils._single_case_material_shortage_partial_production_param(
                state_snapshot,
            )
            if recovery_plan:
                return [
                    StaticUtils._build_department_action_record(
                        action_name="create_production_plan",
                        action_param=recovery_plan,
                        action_reason=(
                            'Materials and an idle line support a bounded PRODUCT_1 recovery plan; replace an overly conservative pass.'
                        ),
                        department="production",
                        enterprise_name=enterprise_name,
                    )
                ]
        if (
            department == "production"
            and single_case_active
            and not fd_policy
            and single_case_staff_shortage_context
            and not single_case_prewarm_seed_context
            and not non_pass_actions
        ):
            hr_snapshot = StaticUtils._load_sibling_department_state_snapshot(
                action_dir=action_dir,
                enterprise_name=enterprise_name,
                department="hr",
                round_id=round_id,
            )
            recovery_plan = StaticUtils._single_case_staff_shortage_recovery_plan_param(
                state_snapshot=state_snapshot,
                hr_snapshot=hr_snapshot,
            )
            if recovery_plan:
                return [
                    StaticUtils._build_department_action_record(
                        action_name="create_production_plan",
                        action_param=recovery_plan,
                        action_reason=(
                                'Available unallocated PRODUCTION staff, an idle line, materials and backlog support a bounded PRODUCT_1 recovery plan; replace an overly conservative pass.'
                        ),
                        department="production",
                        enterprise_name=enterprise_name,
                    )
                ]
        if (
            department == "sales"
            and single_case_active
            and not fd_policy
            and single_case_material_shortage_context
            and not single_case_prewarm_seed_context
            and not non_pass_actions
        ):
            inventory_snapshot = StaticUtils._load_sibling_department_state_snapshot(
                action_dir=action_dir,
                enterprise_name=enterprise_name,
                department="inventory",
                round_id=round_id,
            )
            accept_param = StaticUtils._single_case_material_shortage_safe_accept_order_param(
                state_snapshot=state_snapshot,
                inventory_snapshot=inventory_snapshot,
                available_order_index=available_sales_order_index,
            )
            if accept_param:
                return [
                    StaticUtils._build_department_action_record(
                        action_name="accept_order",
                        action_param=accept_param,
                        action_reason=(
                            'Finished goods cover a real small order; replace an overly conservative pass with safe acceptance to avoid stalled recovery.'
                        ),
                        department="sales",
                        enterprise_name=enterprise_name,
                    )
                ]
        if not normalized or (
            not non_pass_actions
            and not explicit_pass_actions
            and department in {"procurement", "sales", "production", "hr", "inventory"}
        ):
            return [
                StaticUtils._build_action_pass_record(
                    f'{department} actions normalized; no valid action remains.',
                    department,
                    enterprise_name,
                )
            ]
        return normalized

    @staticmethod
    def new_trace():
        """Generates a short trace_id to enable a serial tool to call the log."""
        return str(uuid.uuid4())[:8]

    @staticmethod
    def build_agent_simulation_context(simulation_context: dict) -> dict:
        """Crop simulation context, only the fields required for Agent decision-making."""
        if not isinstance(simulation_context, dict):
            simulation_context = {}
        agent_context = {
            key: simulation_context.get(key)
            for key in (
                "total_steps",
                "final_round",
                "market_demand_mode",
                "trade_mode",
                "is_scheduled_external_demand_mode",
                "is_beer_game_mode",
                "is_cobweb_mode",
                "is_shared_resource_mode",
                "is_herding_mode",
                "cobweb_config",
                "cobweb_history",
                "cobweb_history_summary",
                "shared_resource_config",
                "shared_resource_state",
                "shared_resource_history",
                "herding_config",
                "herding_state",
                "herding_history",
                "external_environment",
            )
            if key in simulation_context
        }
        if isinstance(agent_context.get("shared_resource_history"), list):
            agent_context["shared_resource_history"] = agent_context["shared_resource_history"][-6:]
        if isinstance(agent_context.get("cobweb_history"), list):
            full_history = [
                item
                for item in agent_context["cobweb_history"]
                if isinstance(item, dict)
            ]
            cobweb_config = agent_context.get("cobweb_config") or {}
            try:
                history_window = int(
                    cobweb_config.get("agent_history_window_rounds", 8) or 8
                )
            except (TypeError, ValueError):
                history_window = 8
            history_window = max(3, min(12, history_window))
            existing_summary = (
                simulation_context.get("cobweb_history_summary")
                if isinstance(
                    simulation_context.get("cobweb_history_summary"),
                    dict,
                )
                else {}
            )
            if (
                existing_summary
                and (
                    StaticUtils._safe_float(
                        existing_summary.get("record_count"),
                        0.0,
                    )
                    or 0.0
                )
                >= len(full_history)
            ):
                history_summary = deepcopy(existing_summary)
                history_summary["visible_recent_rounds"] = min(
                    history_window,
                    len(full_history),
                )
            else:
                history_summary = StaticUtils._build_cobweb_history_summary(
                    full_history,
                    visible_window_rounds=history_window,
                )
            agent_context["cobweb_history_summary"] = history_summary
            agent_context["cobweb_history"] = StaticUtils._compact_cobweb_history(
                full_history[-history_window:]
            )
        if isinstance(agent_context.get("herding_history"), list):
            peer_visible = bool(
                ((agent_context.get("herding_config") or {}).get("peer_visibility_enabled"))
            )
            agent_context["herding_history"] = StaticUtils._compact_herding_history(
                agent_context["herding_history"][-6:],
                peer_visible=peer_visible,
            )
        agent_context["context_scope"] = "agent_decision"
        return agent_context

    @staticmethod
    def _compact_cobweb_history(
        history: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Retain interpretable price - supply evidence, remove duplicate equations and lengthy source details."""
        compacted = []
        for item in history or []:
            if not isinstance(item, dict):
                continue
            source_detail = item.get("market_supply_source_detail") or {}
            compacted.append({
                "round": item.get("round"),
                "product_id": item.get("product_id"),
                "unit_price": item.get("unit_price"),
                "quantity": item.get("quantity"),
                "market_supply_quantity": item.get("market_supply_quantity"),
                "actual_supply_quantity": item.get("actual_supply_quantity"),
                "theoretical_supply_quantity": item.get(
                    "theoretical_planned_supply_quantity",
                    item.get("theoretical_supply_quantity"),
                ),
                "market_supply_source": item.get("market_supply_source"),
                "supply_round": source_detail.get(
                    "supply_round",
                    item.get("supply_round"),
                ),
                "effective_fallback": source_detail.get(
                    "effective_fallback",
                    item.get("effective_fallback"),
                ),
                "lagged_price": item.get("lagged_price"),
                "raw_market_price": item.get("raw_market_price"),
                "stability_label": item.get("stability_label"),
                "production_response_mode": item.get("production_response_mode"),
            })
        return compacted

    @staticmethod
    def _build_cobweb_history_summary(
        history: List[Dict[str, Any]],
        *,
        visible_window_rounds: int,
    ) -> Dict[str, Any]:
        """Provides the whole-way aggregate trend; complete, round-the-clock evidence remains only in the market archive."""
        records = [item for item in history or [] if isinstance(item, dict)]
        prices = [
            value
            for value in (
                StaticUtils._safe_float(item.get("unit_price"))
                for item in records
            )
            if value is not None
        ]
        supplies = [
            value
            for value in (
                StaticUtils._safe_float(item.get("market_supply_quantity"))
                for item in records
            )
            if value is not None
        ]
        source_counts: Dict[str, int] = {}
        fallback_rounds = []
        for item in records:
            source = str(item.get("market_supply_source") or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1
            if source not in {
                "agent_production_plan_created",
                "scripted_production_plan_created",
                "scripted_cobweb_formula",
            }:
                fallback_rounds.append(item.get("round"))

        def numeric_summary(values: List[float]) -> Dict[str, Any]:
            if not values:
                return {"mean": None, "minimum": None, "maximum": None}
            return {
                "mean": sum(values) / len(values),
                "minimum": min(values),
                "maximum": max(values),
            }

        return {
            "record_count": len(records),
            "first_round": records[0].get("round") if records else None,
            "latest_round": records[-1].get("round") if records else None,
            "visible_recent_rounds": min(visible_window_rounds, len(records)),
            "full_history_archived_outside_agent_context": True,
            "market_price": numeric_summary(prices),
            "market_supply": numeric_summary(supplies),
            "market_supply_source_counts": source_counts,
            "non_plan_supply_rounds": fallback_rounds,
        }

    @staticmethod
    def _compact_herding_history(history: List[Dict[str, Any]], peer_visible: bool = True) -> List[Dict[str, Any]]:
        """Keeps the Sheep Effect Trends Index, removes the enterprise detail and avoids the Agent page reading large files."""
        compacted = []
        for item in history or []:
            if not isinstance(item, dict):
                continue
            peer_summary = item.get("peer_summary") or {}
            record = {
                "round": item.get("round"),
                "true_demand_quantity": item.get("true_demand_quantity"),
                "visible_demand_signal": item.get("visible_demand_signal"),
                "market_heat": item.get("market_heat"),
                "market_heat_label": item.get("market_heat_label"),
                "trend_direction": item.get("trend_direction"),
                "cash_stress_index": item.get("cash_stress_index"),
                "peer_summary": {
                    "visible": peer_summary.get("visible"),
                    "lag_rounds": peer_summary.get("lag_rounds"),
                },
            }
            if peer_visible:
                record.update({
                    "total_planned_quantity": item.get("total_planned_quantity"),
                    "average_planned_quantity": item.get("average_planned_quantity"),
                    "synchronization_index": item.get("synchronization_index"),
                    "overproduction_ratio": item.get("overproduction_ratio"),
                    "overproduction_gap": item.get("overproduction_gap"),
                    "production_growth_sync_rate": item.get("production_growth_sync_rate"),
                    "herding_index": item.get("herding_index"),
                    "inventory_pressure": item.get("inventory_pressure"),
                })
                record["peer_summary"].update({
                    "average_planned_quantity": peer_summary.get("average_planned_quantity"),
                    "total_planned_quantity": peer_summary.get("total_planned_quantity"),
                    "synchronization_index": peer_summary.get("synchronization_index"),
                    "planned_quantity_dispersion": peer_summary.get("planned_quantity_dispersion"),
                })
            else:
                record["peer_summary"]["reason"] = "peer_visibility_disabled_by_scenario_config"
                record["peer_metrics_hidden"] = True
                record["hidden_metrics_note"] = (
                    "peer production aggregates and collective overproduction metrics are hidden from agents "
                    "in no-peer-visibility control scenarios."
                )
            compacted.append(record)
        return compacted

    @staticmethod
    def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _get_c3_shared_resource_sustainability_guard(
        policy_context: Dict[str, Any],
        state_snapshot: Optional[Dict[str, Any]] = None,
        agent_simulation_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Calculate single-enterprise sustainable access ceiling for the current round for the C3 community experiment."""
        if not isinstance(policy_context, dict):
            return {"enabled": False}
        active_modes = policy_context.get("active_modes") or {}
        action_constraints = policy_context.get("action_constraints") or {}
        if not (active_modes.get("shared_resource") and active_modes.get("profit_objective")):
            return {"enabled": False, "reason": "not_c3_shared_resource_profit_objective"}
        if action_constraints.get("profit_objective_enabled") is False:
            return {"enabled": False, "reason": "profit_objective_disabled"}
        if action_constraints.get("shared_resource_acquisition_plan_enabled") is False:
            return {"enabled": False, "reason": "shared_resource_plan_disabled"}

        relevant_policies = policy_context.get("relevant_policies") or {}
        shared_resource_policy = relevant_policies.get("shared_resource") or {}
        resource_policy = shared_resource_policy.get("resource") or {}

        state_snapshot = state_snapshot or {}
        simulation_context = (
            agent_simulation_context
            or state_snapshot.get("simulation_context")
            or {}
        )
        self_state = state_snapshot.get("self_state") or {}
        shared_resource_state = (
            simulation_context.get("shared_resource_state")
            or self_state.get("shared_resource_state")
            or {}
        )
        shared_resource_config = (
            simulation_context.get("shared_resource_config")
            or self_state.get("shared_resource_config")
            or {}
        )
        plan_context = self_state.get("shared_resource_plan_context") or {}

        product_id = (
            plan_context.get("product_id")
            or resource_policy.get("product_id")
            or shared_resource_state.get("product_id")
            or shared_resource_config.get("product_id")
        )
        sustainable_total = (
            StaticUtils._safe_float(shared_resource_state.get("sustainable_total_acquisition"))
            or StaticUtils._safe_float(resource_policy.get("sustainable_total_acquisition"))
            or StaticUtils._safe_float(resource_policy.get("sustainable_acquisition_per_round"))
            or StaticUtils._safe_float(shared_resource_config.get("sustainable_acquisition_per_round"))
        )
        if not sustainable_total or sustainable_total <= 0:
            return {"enabled": False, "reason": "missing_sustainable_total_acquisition"}

        target_enterprise_ids = (
            resource_policy.get("target_enterprise_ids")
            or shared_resource_config.get("target_enterprise_ids")
            or []
        )
        enterprise_count = len(target_enterprise_ids) if isinstance(target_enterprise_ids, list) else 0
        if enterprise_count <= 0:
            enterprise_count = 1
        baseline_share = sustainable_total / enterprise_count

        stock_ratio = StaticUtils._safe_float(
            shared_resource_state.get("resource_stock_ratio"),
            StaticUtils._safe_float(shared_resource_state.get("stock_ratio")),
        )
        resource_quality = StaticUtils._safe_float(shared_resource_state.get("resource_quality"))
        warning_level = str(shared_resource_state.get("warning_level") or "normal").lower()
        warning_threshold = (
            StaticUtils._safe_float(resource_policy.get("warning_threshold_ratio"))
            or StaticUtils._safe_float(shared_resource_config.get("warning_threshold_ratio"))
            or 0.45
        )
        collapse_threshold = (
            StaticUtils._safe_float(resource_policy.get("collapse_threshold_ratio"))
            or StaticUtils._safe_float(shared_resource_config.get("collapse_threshold_ratio"))
            or 0.20
        )
        quality_floor = (
            StaticUtils._safe_float(resource_policy.get("resource_quality_floor"))
            or StaticUtils._safe_float(shared_resource_config.get("resource_quality_floor"))
            or 0.0
        )

        severity_multiplier = 1.0
        is_collapse_state = (
            warning_level == "collapse"
            or (stock_ratio is not None and stock_ratio <= collapse_threshold)
            or (
                resource_quality is not None
                and resource_quality <= max(quality_floor, 0.20)
            )
        )
        is_warning_state = (
            warning_level == "warning"
            or (stock_ratio is not None and stock_ratio <= warning_threshold)
        )
        if is_collapse_state:
            severity_multiplier = 0.25
        elif is_warning_state:
            severity_multiplier = 0.50

        dynamic_cap = baseline_share * severity_multiplier
        return {
            "enabled": True,
            "source": "c3_shared_resource_sustainability_policy",
            "product_id": product_id,
            "sustainable_total_acquisition": sustainable_total,
            "target_enterprise_count": enterprise_count,
            "baseline_enterprise_share": baseline_share,
            "dynamic_acquisition_cap": dynamic_cap,
            "severity_multiplier": severity_multiplier,
            "warning_level": warning_level,
            "resource_stock_ratio": stock_ratio,
            "resource_quality": resource_quality,
            "warning_threshold_ratio": warning_threshold,
            "collapse_threshold_ratio": collapse_threshold,
            "resource_quality_floor": quality_floor,
        }

    @staticmethod
    def build_cobweb_decision_signal(
        agent_simulation_context: Dict[str, Any],
        round_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """The current-cycle production signal for the refining of the web model avoids re-exporting department from the backlog."""
        if not isinstance(agent_simulation_context, dict):
            return {}
        is_cobweb_mode = bool(
            agent_simulation_context.get("is_cobweb_mode")
            or agent_simulation_context.get("market_demand_mode") == "cobweb"
        )
        if not is_cobweb_mode:
            return {}

        cobweb_config = agent_simulation_context.get("cobweb_config") or {}
        history = [
            item for item in (agent_simulation_context.get("cobweb_history") or [])
            if isinstance(item, dict)
        ]
        latest_record = None
        if history:
            if round_id is None:
                latest_record = history[-1]
            else:
                eligible_records = [
                    item for item in history
                    if StaticUtils._safe_float(item.get("round"), -1) <= round_id
                ]
                latest_record = eligible_records[-1] if eligible_records else history[-1]

        market_supply = StaticUtils._safe_float(
            (latest_record or {}).get("planned_supply_quantity"),
            StaticUtils._safe_float((latest_record or {}).get("quantity")),
        )
        theoretical_supply = StaticUtils._safe_float(
            (latest_record or {}).get("theoretical_planned_supply_quantity"),
            market_supply,
        )
        actual_supply = StaticUtils._safe_float((latest_record or {}).get("actual_supply_quantity"))
        current_price = StaticUtils._safe_float((latest_record or {}).get("unit_price"))
        production_response_mode = cobweb_config.get("production_response_mode") or "agent_endogenous"
        hard_cap_enabled = production_response_mode in {"guardrail_cap", "auto_controlled"}
        scripted_formula_controls_quantity = production_response_mode == "scripted_formula"
        product_id = (
            (latest_record or {}).get("product_id")
            or cobweb_config.get("product_id")
        )
        recommended_quantity = None
        if market_supply is not None and production_response_mode != "agent_endogenous":
            recommended_quantity = max(1, int(round(market_supply)))

        demand_intercept = StaticUtils._safe_float(cobweb_config.get("demand_intercept"))
        demand_slope = StaticUtils._safe_float(cobweb_config.get("demand_slope"))
        supply_intercept = StaticUtils._safe_float(cobweb_config.get("supply_intercept"))
        supply_slope = StaticUtils._safe_float(cobweb_config.get("supply_slope"))
        equilibrium_price = None
        equilibrium_quantity = None
        if (
            demand_intercept is not None
            and demand_slope is not None
            and supply_intercept is not None
            and supply_slope is not None
            and demand_slope + supply_slope != 0
        ):
            equilibrium_price = (demand_intercept - supply_intercept) / (demand_slope + supply_slope)
            equilibrium_quantity = supply_intercept + supply_slope * equilibrium_price

        price_deviation = (
            current_price - equilibrium_price
            if current_price is not None and equilibrium_price is not None
            else None
        )
        if price_deviation is None:
            supply_direction = "observe"
        elif price_deviation > 0:
            supply_direction = "expand_supply"
        elif price_deviation < 0:
            supply_direction = "contract_supply"
        else:
            supply_direction = "hold_near_equilibrium"

        return {
            "enabled": True,
            "signal_type": (
                "cobweb_agent_endogenous_price_response"
                if production_response_mode == "agent_endogenous"
                else "cobweb_planned_supply"
            ),
            "round_id": round_id,
            "source": "simulation_context.cobweb_history.latest",
            "product_id": product_id,
            "latest_market_round": (latest_record or {}).get("round"),
            "current_market_price": current_price,
            "lagged_price": (latest_record or {}).get("lagged_price"),
            "planned_supply_quantity": market_supply,
            "market_supply_quantity": market_supply,
            "market_supply_source": (latest_record or {}).get("market_supply_source"),
            "market_supply_source_detail": (latest_record or {}).get("market_supply_source_detail"),
            "actual_supply_quantity": actual_supply,
            "theoretical_supply_quantity": theoretical_supply,
            "recommended_plan_quantity": recommended_quantity,
            "recommended_daily_capacity": recommended_quantity,
            "external_order_quantity": (latest_record or {}).get("quantity"),
            "production_response_mode": production_response_mode,
            "hard_cap_enabled": hard_cap_enabled,
            "stability_label": (
                (latest_record or {}).get("stability_label")
                or cobweb_config.get("stability_label")
            ),
            "equilibrium_price": equilibrium_price,
            "equilibrium_quantity": equilibrium_quantity,
            "price_deviation_from_equilibrium": price_deviation,
            "quantity_deviation_from_equilibrium": (
                market_supply - equilibrium_quantity
                if market_supply is not None and equilibrium_quantity is not None
                else None
            ),
            "suggested_supply_direction": supply_direction,
            "production_plan_policy": {
                "primary_quantity_source": (
                    "agent_decision_from_price_signal_and_business_constraints"
                    if production_response_mode == "agent_endogenous"
                    else "planned_supply_quantity"
                ),
                "max_plan_quantity": recommended_quantity,
                "override_recovery_guard": (
                    hard_cap_enabled or scripted_formula_controls_quantity
                ),
                "hard_cap_enabled": hard_cap_enabled,
                "agent_guidance_enabled": production_response_mode == "agent_guided",
                "agent_endogenous_enabled": production_response_mode == "agent_endogenous",
                "directional_supply_signal": supply_direction,
                "ignore_backlog_as_quantity_target": True,
                "allow_build_line_if_no_available_capacity": True,
            },
        }

    @staticmethod
    def build_cobweb_profit_decision_support(
        agent_simulation_context: Dict[str, Any],
        production_state: Dict[str, Any],
        round_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build non-binding C3 cobweb operating evidence for production decisions."""
        if not isinstance(agent_simulation_context, dict):
            return {}
        cobweb_config = agent_simulation_context.get("cobweb_config") or {}
        if (
            not StaticUtils.is_agent_endogenous_cobweb_context(
                agent_simulation_context
            )
            or cobweb_config.get("decision_profile") != "profit_balanced"
        ):
            return {}

        production_state = (
            production_state if isinstance(production_state, dict) else {}
        )
        cobweb_signal = (
            production_state.get("cobweb_decision_signal")
            or StaticUtils.build_cobweb_decision_signal(
                agent_simulation_context,
                round_id=round_id,
            )
        )
        product_id = (
            cobweb_signal.get("product_id")
            or cobweb_config.get("product_id")
        )
        demand_intercept = StaticUtils._safe_float(
            cobweb_config.get("demand_intercept")
        )
        demand_slope = StaticUtils._safe_float(
            cobweb_config.get("demand_slope")
        )
        if (
            not product_id
            or demand_intercept is None
            or demand_slope is None
            or demand_slope <= 0
        ):
            return {}

        price_floor = StaticUtils._safe_float(
            cobweb_config.get("price_floor"),
            0.0,
        )
        price_ceiling = StaticUtils._safe_float(
            cobweb_config.get("price_ceiling"),
            float("inf"),
        )
        quantity_floor = StaticUtils._safe_float(
            cobweb_config.get("quantity_floor"),
            0.0,
        )
        quantity_ceiling = StaticUtils._safe_float(
            cobweb_config.get("quantity_ceiling"),
            float("inf"),
        )

        margin_candidates = (
            (production_state.get("margin_guard") or {}).get("candidates")
            or []
        )
        margin_candidate = next(
            (
                item
                for item in margin_candidates
                if isinstance(item, dict)
                and item.get("product_id") == product_id
            ),
            {},
        )
        estimated_unit_cost = StaticUtils._safe_float(
            margin_candidate.get("estimated_unit_cost")
        )

        recovery_candidates = (
            (production_state.get("recovery_guard") or {}).get("candidates")
            or []
        )
        recovery_candidate = next(
            (
                item
                for item in recovery_candidates
                if isinstance(item, dict)
                and item.get("product_id") == product_id
            ),
            {},
        )
        on_hand = StaticUtils._safe_float(recovery_candidate.get("on_hand"))
        confirmed_backlog = StaticUtils._safe_float(
            recovery_candidate.get("confirmed_order_backlog_quantity"),
            0.0,
        )
        material_feasible_quantity = StaticUtils._safe_float(
            recovery_candidate.get("material_feasible_quantity")
        )
        production_lines = production_state.get("production_lines") or {}
        available_capacity = StaticUtils._safe_float(
            production_lines.get("available_capacity")
        )
        cash_guard = production_state.get("cash_guard") or {}
        available_conversion_budget = StaticUtils._safe_float(
            cash_guard.get("available_conversion_budget")
        )

        current_supply = StaticUtils._safe_float(
            cobweb_signal.get("market_supply_quantity"),
            quantity_floor,
        )
        external_order_quantity = StaticUtils._safe_float(
            cobweb_signal.get("external_order_quantity")
        )
        equilibrium_quantity = StaticUtils._safe_float(
            cobweb_signal.get("equilibrium_quantity")
        )
        history = [
            item
            for item in (
                agent_simulation_context.get("cobweb_history") or []
            )
            if isinstance(item, dict)
        ]
        recent_supplies = [
            value
            for value in (
                StaticUtils._safe_float(
                    item.get(
                        "market_supply_quantity",
                        item.get("planned_supply_quantity"),
                    )
                )
                for item in history
            )
            if value is not None
        ]
        recent_prices = [
            value
            for value in (
                StaticUtils._safe_float(item.get("unit_price"))
                for item in history
            )
            if value is not None
        ]
        mean_abs_adjustment = None
        if len(recent_supplies) >= 2:
            mean_abs_adjustment = sum(
                abs(recent_supplies[index] - recent_supplies[index - 1])
                for index in range(1, len(recent_supplies))
            ) / (len(recent_supplies) - 1)

        feasible_ceiling = quantity_ceiling
        for value in (available_capacity, material_feasible_quantity):
            if value is not None and value >= 0:
                feasible_ceiling = min(feasible_ceiling, value)
        feasible_ceiling = max(quantity_floor, feasible_ceiling)

        candidate_specs = [("action_pass_floor", 0.0)]
        if current_supply is not None:
            candidate_specs.append(("hold_recent_supply", current_supply))
        one_period_reference = None
        if estimated_unit_cost is not None:
            one_period_reference = (
                demand_intercept - demand_slope * estimated_unit_cost
            ) / 2.0
            candidate_specs.append(
                (
                    "one_period_contribution_reference",
                    one_period_reference,
                )
            )
        if equilibrium_quantity is not None:
            candidate_specs.append(
                ("market_equilibrium_reference", equilibrium_quantity)
            )
        if external_order_quantity is not None:
            candidate_specs.append(
                ("current_order_coverage", external_order_quantity)
            )
        if confirmed_backlog and confirmed_backlog > 0:
            candidate_specs.append(
                ("confirmed_backlog_coverage", confirmed_backlog)
            )

        candidates = []
        seen_quantities = set()
        for basis, raw_quantity in candidate_specs:
            requested_quantity = 0.0 if raw_quantity <= 0 else min(
                feasible_ceiling,
                max(quantity_floor, float(raw_quantity)),
            )
            requested_quantity = float(round(requested_quantity))
            key = requested_quantity
            if key in seen_quantities:
                continue
            seen_quantities.add(key)

            effective_market_supply = (
                quantity_floor
                if requested_quantity <= 0
                else min(
                    quantity_ceiling,
                    max(quantity_floor, requested_quantity),
                )
            )
            raw_market_price = (
                demand_intercept - effective_market_supply
            ) / demand_slope
            implied_market_price = min(
                price_ceiling,
                max(price_floor, raw_market_price),
            )
            raw_market_demand = (
                demand_intercept - demand_slope * implied_market_price
            )
            implied_market_demand = min(
                quantity_ceiling,
                max(quantity_floor, raw_market_demand),
            )
            implied_order_quantity = int(math.ceil(implied_market_demand))
            market_revenue = (
                implied_order_quantity * implied_market_price
            )
            production_cost = (
                requested_quantity * estimated_unit_cost
                if estimated_unit_cost is not None
                else None
            )
            candidates.append({
                "basis": basis,
                "action": (
                    "action_pass"
                    if requested_quantity <= 0
                    else "create_production_plan"
                ),
                "candidate_quantity": requested_quantity,
                "effective_next_market_supply": effective_market_supply,
                "implied_next_market_price": round(
                    implied_market_price,
                    4,
                ),
                "implied_next_order_quantity": implied_order_quantity,
                "market_revenue_if_fulfilled": round(
                    market_revenue,
                    4,
                ),
                "estimated_production_cost": (
                    round(production_cost, 4)
                    if production_cost is not None
                    else None
                ),
                "estimated_gross_contribution_if_fulfilled": (
                    round(market_revenue - production_cost, 4)
                    if production_cost is not None
                    else None
                ),
                "adjustment_from_current_supply": (
                    round(abs(requested_quantity - current_supply), 4)
                    if current_supply is not None
                    else None
                ),
                "price_boundary_hit": (
                    implied_market_price in {price_floor, price_ceiling}
                ),
                "supply_boundary_hit": (
                    effective_market_supply
                    in {quantity_floor, quantity_ceiling}
                ),
            })

        return {
            "enabled": True,
            "mode": "c3_cobweb_non_binding_operating_evidence",
            "product_id": product_id,
            "market_response_model": {
                "production_lag_rounds": cobweb_config.get(
                    "endogenous_supply_lag_rounds",
                    cobweb_config.get("production_lag_rounds"),
                ),
                "inverse_demand": "P_next = clip((a - Q_plan) / b)",
                "demand_intercept": demand_intercept,
                "demand_slope": demand_slope,
                "price_floor": price_floor,
                "price_ceiling": price_ceiling,
                "quantity_floor": quantity_floor,
                "quantity_ceiling": quantity_ceiling,
            },
            "operating_evidence": {
                "estimated_unit_cost": estimated_unit_cost,
                "on_hand_inventory": on_hand,
                "confirmed_order_backlog_quantity": confirmed_backlog,
                "available_capacity": available_capacity,
                "material_feasible_quantity": material_feasible_quantity,
                "available_conversion_budget": available_conversion_budget,
                "current_market_supply": current_supply,
                "current_external_order_quantity": external_order_quantity,
                "one_period_contribution_reference_quantity": (
                    round(
                        min(
                            feasible_ceiling,
                            max(quantity_floor, one_period_reference),
                        ),
                        4,
                    )
                    if one_period_reference is not None
                    else None
                ),
            },
            "recent_window": {
                "round_count": len(history),
                "mean_market_supply": (
                    sum(recent_supplies) / len(recent_supplies)
                    if recent_supplies
                    else None
                ),
                "mean_abs_supply_adjustment": mean_abs_adjustment,
                "price_boundary_hit_count": sum(
                    price in {price_floor, price_ceiling}
                    for price in recent_prices
                ),
                "supply_boundary_hit_count": sum(
                    supply in {quantity_floor, quantity_ceiling}
                    for supply in recent_supplies
                ),
            },
            "candidate_outcomes": candidates,
            "interpretation_contract": (
                "These are transparent one-round counterfactuals, not a "
                "recommended quantity or an execution command. The Agent must "
                "choose after considering inventory coverage, backlog, cash, "
                "service, adjustment stability, and the multi-round objective."
            ),
        }

    @staticmethod
    def build_herding_decision_signal(
        agent_simulation_context: Dict[str, Any],
        round_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Visible market/peer aggregating signals to extract sheep effects avoid Agent reading original peer files."""
        if not StaticUtils.is_herding_context(agent_simulation_context):
            return {}
        context = agent_simulation_context or {}
        herding_config = context.get("herding_config") or {}
        herding_state = context.get("herding_state") or {}
        history = [
            item for item in (context.get("herding_history") or [])
            if isinstance(item, dict)
        ]
        latest_record = None
        if history:
            if round_id is None:
                latest_record = history[-1]
            else:
                eligible_records = [
                    item for item in history
                    if StaticUtils._safe_float(item.get("round"), -1) <= round_id
                ]
                latest_record = eligible_records[-1] if eligible_records else history[-1]

        unit_price = herding_config.get("base_unit_price")
        unit_cost = herding_config.get("unit_cost_reference")
        peer_visible = bool(herding_config.get("peer_visibility_enabled"))
        visible_peer_summary = (
            herding_state.get("peer_summary")
            or (latest_record or {}).get("peer_summary")
            or {}
        )
        if not peer_visible:
            visible_peer_summary = {
                "visible": False,
                "lag_rounds": herding_config.get("peer_visibility_lag_rounds"),
                "reason": "peer_visibility_disabled_by_scenario_config",
            }
        estimated_margin = None
        try:
            if unit_price is not None and unit_cost is not None:
                estimated_margin = float(unit_price) - float(unit_cost)
        except (TypeError, ValueError):
            estimated_margin = None

        return {
            "enabled": True,
            "signal_type": "herding_aggregated_market_peer_reference",
            "round_id": round_id,
            "source": "policy_context.relevant_policies.herding_signal",
            "product_id": herding_config.get("product_id") or herding_state.get("product_id"),
            "market_heat": herding_state.get("market_heat") or (latest_record or {}).get("market_heat"),
            "market_heat_label": herding_state.get("market_heat_label") or (latest_record or {}).get("market_heat_label"),
            "visible_demand_signal": herding_state.get("visible_demand_signal") or (latest_record or {}).get("visible_demand_signal"),
            "trend_direction": herding_state.get("trend_direction") or (latest_record or {}).get("trend_direction"),
            "peer_summary": visible_peer_summary,
            "latest_round_metrics": {
                "round": (latest_record or {}).get("round"),
                "average_planned_quantity": (
                    (latest_record or {}).get("average_planned_quantity") if peer_visible else None
                ),
                "synchronization_index": (
                    (latest_record or {}).get("synchronization_index") if peer_visible else None
                ),
                "overproduction_ratio": (
                    (latest_record or {}).get("overproduction_ratio") if peer_visible else None
                ),
                "inventory_pressure": (
                    (latest_record or {}).get("inventory_pressure") if peer_visible else None
                ),
                "cash_stress_index": (latest_record or {}).get("cash_stress_index"),
                "peer_metrics_hidden": not peer_visible,
            },
            "unit_economics": {
                "base_unit_price": unit_price,
                "unit_cost_reference": unit_cost,
                "estimated_unit_margin": estimated_margin,
                "source": "herding_config",
            },
            "decision_policy": {
                "signal_role": "market_expectation_reference_not_command",
                "raw_peer_file_access_allowed": False,
                "peer_aggregate_metrics_visible": peer_visible,
                "must_combine_with": ["cash", "inventory", "capacity", "real_orders"],
                "do_not_use_recovery_guard_as_primary_quantity_target": True,
                "direct_production_empty_recipe_allowed": True,
                "legacy_margin_guard_role": "risk_context_only",
            },
        }

    @staticmethod
    def filter_agent_observation(observation):
        """Filter simulation_context in observation to avoid exposure to redundant fields."""
        if isinstance(observation, dict) and "simulation_context" in observation:
            observation = dict(observation)
            observation["simulation_context"] = StaticUtils.build_agent_simulation_context(
                observation.get("simulation_context") or {}
            )
        return observation

    @staticmethod
    def inject_policy_context_for_observation(observation: Dict[str, Any], enterprise_name: str, round_id: int) -> Dict[str, Any]:
        """parameter Level Structured Context."""
        if not isinstance(observation, dict):
            return observation

        agent_simulation_context = StaticUtils.build_agent_simulation_context(
            observation.get("simulation_context") or {}
        )
        enterprise_spec = StaticUtils._active_enterprise_spec_from_workspace(
            enterprise_name,
            Config.WORKSPACE,
        )
        runtime_injection_config = StaticUtils._active_runtime_injection_config(Config.WORKSPACE)
        departments = list(Config.DEPARTMENTS)
        enterprise_policy_context = build_enterprise_policy_context(
            round_id=int(round_id),
            enterprise_id=enterprise_name,
            enterprise_spec=enterprise_spec,
            agent_simulation_context=agent_simulation_context,
            runtime_injection_config=runtime_injection_config,
            departments=departments,
        )

        observation = dict(observation)
        observation["simulation_context"] = agent_simulation_context
        cobweb_decision_signal = StaticUtils.build_cobweb_decision_signal(
            agent_simulation_context,
            round_id=round_id,
        )
        if cobweb_decision_signal:
            observation["cobweb_decision_signal"] = cobweb_decision_signal
            if isinstance(observation.get("production"), dict):
                observation["production"] = dict(observation["production"])
                observation["production"]["cobweb_decision_signal"] = cobweb_decision_signal
        herding_decision_signal = StaticUtils.build_herding_decision_signal(
            agent_simulation_context,
            round_id=round_id,
        )
        if herding_decision_signal:
            observation["herding_decision_signal"] = herding_decision_signal
            if isinstance(observation.get("production"), dict):
                observation["production"] = dict(observation["production"])
                observation["production"]["herding_decision_signal"] = herding_decision_signal
            if isinstance(observation.get("sales"), dict):
                observation["sales"] = dict(observation["sales"])
                observation["sales"]["herding_decision_signal"] = herding_decision_signal
        observation["enterprise_policy_context"] = enterprise_policy_context
        observation["policy_context_by_department"] = enterprise_policy_context.get("policy_context_by_department") or {}
        return observation

    @staticmethod
    def save_observation(output_dir: Path = Config.OBSERVATION_DIR, enterprise_name: str = "Manufacturer"):
        """Reads from the simulation service and saves it to local files."""
        trace_id = StaticUtils.new_trace()
        extra = {"trace_id": trace_id}

        try:
            r = requests.get(
                f"{Config.BASE_URL}/state",
                json={"enterprise_name": enterprise_name},
                headers=StaticUtils.simulation_session_headers(),
                timeout=30,
            )
            result = r.json()
        except Exception as e:
            logger.error(f"Request /state failed: {e}", extra=extra)
            return

        if result.get("status") != "success":
            logger.warning("Request failed, skip saving.", extra=extra)
            return

        data = result.get("data", {})
        observation = data.get("observation")
        current_time = data.get("current_time")

        if observation is None or current_time is None:
            logger.warning("Missing observation or current_time.", extra=extra)
            return
        current_round = int(current_time)

        observation = StaticUtils.filter_agent_observation(observation)
        observation = StaticUtils.inject_policy_context_for_observation(
            observation,
            enterprise_name,
            current_round,
        )

        filename = f"observation_day{current_round}.txt"
        filepath = output_dir / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)

        filepath.write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Observation saved to {filepath}", extra=extra)
        return filepath

    @staticmethod
    def execute_hr_action(round_id,retry_time):
        """Performs HR actions in the old single-enterprise workspace."""
        response = StaticUtils.execute_action(Config.HR_ACTION, Config.HR_RESULT, "run", Config.HR_ERROR,"hr", round_id,retry_time)
        result = StaticUtils.handle_execute_response(response)
        return result

    @staticmethod
    def execute_inventory_action(round_id,retry_time):
        """Performs the Inventory action in the old single-enterprise workspace."""
        response = StaticUtils.execute_action(Config.INVENTORY_ACTION, Config.INVENTORY_RESULT, "run", Config.INVENTORY_ERROR,"inventory", round_id,retry_time)
        result = StaticUtils.handle_execute_response(response)
        return result

    @staticmethod
    def execute_procurement_action(round_id,retry_time):
        """Performs the Project action in the old single-enterprise workspace."""
        response = StaticUtils.execute_action(Config.PROCUREMENT_ACTION, Config.PROCUREMENT_RESULT, "run", Config.PROCUREMENT_ERROR,"procurement", round_id,retry_time) 
        result = StaticUtils.handle_execute_response(response)
        return result

    @staticmethod
    def execute_sales_action(round_id,retry_time):
        """Execute Sales action in the old single-enterprise working area."""
        response = StaticUtils.execute_action(Config.SALES_ACTION, Config.SALES_RESULT, "run", Config.SALES_ERROR,"sales", round_id,retry_time) 
        result = StaticUtils.handle_execute_response(response)
        return result

    @staticmethod
    def execute_production_action(round_id,retry_time):
        """Performs a project action in the old single-enterprise workspace."""
        response = StaticUtils.execute_action(Config.PRODUCTION_ACTION, Config.PRODUCTION_RESULT, "run", Config.PRODUCTION_ERROR,"production", round_id,retry_time)
        result = StaticUtils.handle_execute_response(response)
        return result

    # ----------------------------

    # ----------------------------
    @staticmethod
    def execute_dept_action(input_file: Path, output_file: Path, error_file: Path, department: str, round_id: int, retry_time: int, enterprise_name: str, execute_type: str = "run",blackboard_path: Path = None):
        """Perform a single enterprise department action, and convert it into a uniform top-level, judgementable result structure."""
        response = StaticUtils.execute_action(input_file, output_file, execute_type, error_file, department, round_id, retry_time, enterprise_name,blackboard_path)
        result = StaticUtils.handle_execute_response(response)
        return result

    
    @staticmethod
    def handle_execute_response(response):
        """Normalize execution-interface return values into a standard session result."""
        if response.get("status") != "success" and response.get("message") == "need-retry":
            return {
                "status": "error", 
                "message": "need-retry",
                "file_path": response.get("file_path")
            }
        return {
            "status": "success",
            "message": "success"
        }

    @staticmethod
    def check_and_reTry():
        """Reads the retry schedule and initiates the retry execution when a failed action exists."""
        trace_id = StaticUtils.new_trace()
        extra = {"trace_id": trace_id}

        try:
            data = json.loads(Config.RE_ACTION_PLAN.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to read re_action_plan.json: {e}", extra=extra)
            return

        if (
            isinstance(data, list)
            and len(data) == 1
            and isinstance(data[0], dict)
            and data[0].get("state") == "pass"
        ):
            logger.info("PASS - no retry needed.", extra=extra)
            return

        StaticUtils.execute_action(Config.RE_ACTION_PLAN, Config.RE_RESULT, "retry")

    @staticmethod
    def run_day():
        """Call next_turn interface to advance simulation into the next round."""
        trace_id = StaticUtils.new_trace()
        extra = {"trace_id": trace_id}

        try:
            r = requests.post(
                f"{Config.BASE_URL}/next_turn",
                headers=StaticUtils.simulation_session_headers(),
            )
            logger.info(f"next_turn response: {r.json()}", extra=extra)
        except Exception as e:
            logger.error(f"next_turn failed: {e}", extra=extra)

    @staticmethod
    def configure_simulation(
        total_steps: int = None,
        market_demand_mode: str = None,
        beer_game_demand_series: list = None,
        beer_game_customer_delivery_lead_time: int = None,
        beer_game_unit_price: float = None,
        beer_game_product_id: str = None,
        cobweb_config: dict = None,
        shared_resource_config: dict = None,
        herding_config: dict = None
    ):
        """Writes operational configuration parameters to the simulation service."""
        trace_id = StaticUtils.new_trace()
        extra = {"trace_id": trace_id}
        payload = {
            "total_steps": total_steps,
            "market_demand_mode": market_demand_mode,
            "beer_game_demand_series": beer_game_demand_series,
            "beer_game_customer_delivery_lead_time": beer_game_customer_delivery_lead_time,
            "beer_game_unit_price": beer_game_unit_price,
            "beer_game_product_id": beer_game_product_id,
            "cobweb_config": cobweb_config,
            "shared_resource_config": shared_resource_config,
            "herding_config": herding_config,
            "enterprise_config": get_active_enterprise_configs(),
            "runtime_injection_config": get_runtime_injection_config(),
        }
        payload = {key: value for key, value in payload.items() if value is not None}

        try:
            r = requests.post(
                f"{Config.BASE_URL}/simulation_config",
                json=payload,
                headers=StaticUtils.simulation_session_headers(),
                timeout=30,
            )
            logger.info(f"simulation_config response: {r.json()}", extra=extra)
            return r.json()
        except Exception as e:
            logger.error(f"simulation_config failed: {e}", extra=extra)
            return {"status": "error", "message": str(e)}

    @staticmethod
    def save_runtime_checkpoint(
        checkpoint_path: Path,
        *,
        run_id: str,
        scenario_id: str,
        completed_steps: int,
        last_complete_round: int,
    ) -> Dict[str, Any]:
        payload = {
            "checkpoint_path": str(Path(checkpoint_path).resolve()),
            "run_id": run_id,
            "scenario_id": scenario_id,
            "completed_steps": int(completed_steps),
            "last_complete_round": int(last_complete_round),
        }
        response = requests.post(
            f"{Config.BASE_URL}/runtime_checkpoint/save",
            json=payload,
            headers=StaticUtils.simulation_session_headers(),
            timeout=120,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "success":
            raise RuntimeError(f"Runtime checkpoint save failed: {result}")
        return result.get("data") or {}

    @staticmethod
    def load_runtime_checkpoint(
        checkpoint_path: Path,
        *,
        run_id: str,
        scenario_id: str,
        last_complete_round: int,
    ) -> Dict[str, Any]:
        payload = {
            "checkpoint_path": str(Path(checkpoint_path).resolve()),
            "run_id": run_id,
            "scenario_id": scenario_id,
            "last_complete_round": int(last_complete_round),
        }
        response = requests.post(
            f"{Config.BASE_URL}/runtime_checkpoint/load",
            json=payload,
            headers=StaticUtils.simulation_session_headers(),
            timeout=120,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "success":
            raise RuntimeError(f"Runtime checkpoint load failed: {result}")
        return result.get("data") or {}

    @staticmethod
    def replay_archived_action(
        action_path: Path,
        *,
        department: str = "",
        enterprise_name: str = "",
        execute_type: str = "run",
        round_id: Optional[int] = None,
        identifier_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Replay an archived action through the original execution normalizer."""
        executable = StaticUtils._prepare_archived_action_payload(
            action_path,
            department=department,
            enterprise_name=enterprise_name,
            round_id=round_id,
            identifier_map=identifier_map,
            prefer_recorded_result=execute_type == "run",
        )
        if not executable:
            return {"status": "skipped", "reason": "action_pass_or_missing"}
        response = StaticUtils._post_execution(
            f"{Config.BASE_URL}/execute",
            json={
                "workflow": executable,
                "execute_type": execute_type,
                "enterprise_name": enterprise_name,
                "dept": department,
            },
            headers=StaticUtils.simulation_session_headers(),
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        execution = result.get("result") or {}
        failed = execution.get("failed") or execution.get("error") or []
        if failed:
            raise RuntimeError(
                "Archived action replay failed: "
                f"path={action_path}, identifier_map={identifier_map or {}}, result={result}"
            )
        return {"status": "success", "result": result}

    @staticmethod
    def _prepare_archived_action_payload(
        action_path: Path,
        *,
        department: str = "",
        enterprise_name: str = "",
        round_id: Optional[int] = None,
        identifier_map: Optional[Dict[str, str]] = None,
        prefer_recorded_result: bool = True,
    ) -> List[Dict[str, Any]]:
        action_path = Path(action_path)
        if not action_path.exists():
            return []
        payload = None
        if prefer_recorded_result:
            result_path = action_path.with_name(
                action_path.name.replace("_action.json", "_result.json")
            )
            if result_path.exists():
                result_payload = json.loads(result_path.read_text(encoding="utf-8"))
                payload = []

                def collect_success(value: Any) -> None:
                    if isinstance(value, dict):
                        action_type = value.get("action_type")
                        params = value.get("params")
                        if (
                            value.get("success") is True
                            and isinstance(action_type, str)
                            and action_type not in {"action_pass", "batch_execute"}
                            and isinstance(params, dict)
                        ):
                            action_param = dict(params)
                            action_param.pop("dry_run", None)
                            payload.append({
                                "action": {
                                    "action_name": action_type,
                                    "action_param": action_param,
                                },
                                "module_type": value.get("module_type"),
                                "executor_id": enterprise_name,
                            })
                            return
                        for child in value.values():
                            collect_success(child)
                    elif isinstance(value, list):
                        for child in value:
                            collect_success(child)

                collect_success(result_payload)
        if payload is None:
            payload = json.loads(action_path.read_text(encoding="utf-8"))
            if isinstance(payload, list) and len(payload) == 1 and isinstance(payload[0], list):
                payload = payload[0]
            if isinstance(payload, dict):
                payload = [payload]
            if not isinstance(payload, list):
                raise ValueError(f"Archived action payload is not a list: {action_path}")
            payload = StaticUtils._normalize_department_workflow(
                payload,
                department=department or None,
                enterprise_name=enterprise_name,
                round_id=round_id,
                action_dir=action_path.parent,
            )
        identifier_map = identifier_map or {}
        if identifier_map:
            def replace_identifiers(value: Any) -> Any:
                if isinstance(value, dict):
                    return {
                        key: replace_identifiers(child)
                        for key, child in value.items()
                    }
                if isinstance(value, list):
                    return [replace_identifiers(child) for child in value]
                if isinstance(value, str):
                    return identifier_map.get(value, value)
                return value

            payload = replace_identifiers(payload)
        executable = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            action = item.get("action") or {}
            if action.get("action_name") == "action_pass":
                continue
            executable.append(item)
        if not executable:
            return []
        return executable

    @staticmethod
    def replay_archived_action_group(
        action_specs: List[tuple[Path, str, str]],
        *,
        round_id: int,
        identifier_map: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Replay historically concurrent actions as one validate-then-execute batch."""
        executable: List[Dict[str, Any]] = []
        for action_path, department, enterprise_name in action_specs:
            executable.extend(StaticUtils._prepare_archived_action_payload(
                action_path,
                department=department,
                enterprise_name=enterprise_name,
                round_id=round_id,
                identifier_map=identifier_map,
                prefer_recorded_result=True,
            ))
        if not executable:
            return {"status": "skipped", "reason": "action_pass_or_missing"}
        response = StaticUtils._post_execution(
            f"{Config.BASE_URL}/execute",
            json={
                "workflow": executable,
                "execute_type": "replay",
                "enterprise_name": "",
                "dept": "",
            },
            headers=StaticUtils.simulation_session_headers(),
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
        execution = result.get("result") or {}
        failed = execution.get("failed") or execution.get("error") or []
        if failed:
            raise RuntimeError(
                "Archived concurrent action replay failed: "
                f"paths={[str(item[0]) for item in action_specs]}, "
                f"identifier_map={identifier_map or {}}, result={result}"
            )
        return {"status": "success", "result": result}

    @staticmethod
    def check_orders():
        """Trigger exchange to check the order and push the setup."""
        trace_id = StaticUtils.new_trace()
        extra = {"trace_id": trace_id}

        try:
            r = requests.post(
                f"{Config.BASE_URL}/check_orders",
                headers=StaticUtils.simulation_session_headers(),
            )
            logger.info(f"check_orders response: {r.json()}", extra=extra)
        except Exception as e:
            logger.error(f"check_orders failed: {e}", extra=extra)

    @staticmethod
    def statistics_orders():
        """Trigger transaction statistics summary."""
        trace_id = StaticUtils.new_trace()
        extra = {"trace_id": trace_id}

        try:
            r = requests.post(
                f"{Config.BASE_URL}/statistics_orders",
                headers=StaticUtils.simulation_session_headers(),
            )
            logger.info(f"statistics_orders response: {r.json()}", extra=extra)
        except Exception as e:
            logger.error(f"statistics_orders failed: {e}", extra=extra)

    @staticmethod
    def handle_init_action():
        """Execute the initialised action file."""
        StaticUtils.execute_action(Config.INIT_ACTION, Config.INIT_RESULT, "init", Config.HR_ERROR)

    @staticmethod
    def handle_daily_action():
        """Execute daily fixed action files."""
        StaticUtils.execute_action(Config.DAILY_ACTION, Config.DAILY_RESULT, "daily")

    @staticmethod
    def handle_test_action():
        """Execute the test action file."""
        StaticUtils.execute_action(Config.TEST_ACTION, Config.TEST_RESULT, "test")

    @staticmethod
    def execute_action(input_file: Path, output_file: Path, execute_type: str, error_file: Path = None, department: str = None, round_id: str = None, retry_time: int = 0, enterprise_name: str = "Manufacturer", blackboard_path: Path = None, execution_observer=None):
        'Read the action file, regularize the payload as required and call the results of the mock implementation interface persist.'
        trace_id = StaticUtils.new_trace()
        extra = {"trace_id": trace_id}
        if not input_file.exists():
            logger.error(f"File not found: {input_file}", extra=extra)
            return {"status": "error", "message": f"File not found: {input_file}"}

        def extract_non_pass_action_names(workflow_payload):
            names = []
            if isinstance(workflow_payload, list) and len(workflow_payload) == 1 and isinstance(workflow_payload[0], list):
                workflow_payload = workflow_payload[0]
            if isinstance(workflow_payload, dict):
                workflow_payload = [workflow_payload]
            if not isinstance(workflow_payload, list):
                return names
            for item in workflow_payload:
                if not isinstance(item, dict):
                    continue
                action = item.get("action") if isinstance(item.get("action"), dict) else item
                if not isinstance(action, dict):
                    continue
                action_name = str(action.get("action_name") or action.get("action_type") or "").strip()
                if action_name and action_name != "action_pass":
                    names.append(action_name)
            return names

        def extract_action_pass_records(workflow_payload):
            records = []
            if isinstance(workflow_payload, list) and len(workflow_payload) == 1 and isinstance(workflow_payload[0], list):
                workflow_payload = workflow_payload[0]
            if isinstance(workflow_payload, dict):
                workflow_payload = [workflow_payload]
            if not isinstance(workflow_payload, list):
                return records
            for item in workflow_payload:
                if not isinstance(item, dict):
                    continue
                action = item.get("action") if isinstance(item.get("action"), dict) else {}
                if not isinstance(action, dict) or action.get("action_name") != "action_pass":
                    continue
                message = action.get("action_param")
                if not isinstance(message, str):
                    message = item.get("action_reason") or "Action normalized to action_pass"
                records.append({
                    "status": "success",
                    "success": True,
                    "consumed_time": 0,
                    "message": message,
                    "module_type": item.get("module_type"),
                    "module_id": f"{department}_{enterprise_name}",
                    "action_type": "action_pass",
                    "timestamp": datetime.now().timestamp(),
                    "params": {"reason": message},
                    "data": {},
                    "errors": [],
                    "warnings": [],
                })
            return records

        def write_need_retry_error(code, message, actions):
            failed_output = [[{
                "code": code,
                "message": message,
                "actions": actions,
            }]]
            if error_file:
                file_name = error_file.with_name(f"{error_file.stem}_{retry_time}.json")
                file_name.write_text(
                    json.dumps(failed_output, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                archive_dir = input_file.parent
                if execute_type == "run":
                    shutil.move(str(input_file), str(archive_dir / f"{department}_action_{retry_time}.json"))
                return {
                    "status": "error",
                    "message": "need-retry",
                    "file_path": str(file_name),
                }
            return {"status": "error", "message": message}

        try:
            data = json.loads(input_file.read_text(encoding="utf-8"))
            raw_attempted_action_names = extract_non_pass_action_names(data)
            payload = StaticUtils._normalize_department_workflow(
                data,
                department=department,
                enterprise_name=enterprise_name,
                round_id=round_id,
                action_dir=input_file.parent,
            )
            attempted_action_names = extract_non_pass_action_names(payload)
            normalized_pass_records = extract_action_pass_records(payload)
            if raw_attempted_action_names and not attempted_action_names:
                if normalized_pass_records:
                    result = {
                        "result": {"success": normalized_pass_records},
                        "current_day": str(round_id) if round_id is not None else "",
                    }
                    output_file.write_text(
                        json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    if execution_observer is not None:
                        execution_observer(payload, result)
                    logger.info(f"Result saved to {output_file}", extra=extra)
                    return {"status": "success", "message": f"Execution finished: {result}"}
                return write_need_retry_error(
                    "ACTION_DROPPED_BY_NORMALIZATION",
                    (
                        "Action payload was removed during normalization before execution: "
                        f"{', '.join(raw_attempted_action_names)}"
                    ),
                    raw_attempted_action_names,
                )
            # if execute_type == "run" and round_id != None:
            #     raw_actions = data[0]
            #     if isinstance(raw_actions, list) and len(raw_actions) == 1 and isinstance(raw_actions[0], list):
            #         payload = raw_actions[0]
            #     else:
            #         payload = raw_actions
            #     # try:

            #     #     if len(data) > 1 and data[1]:
            #     #         if isinstance(data[1], dict):
            #     #             communications = data[1]
            #     #         elif isinstance(data[1], list) and len(data[1]) > 0 and isinstance(data[1][0], dict):
            #     #             communications = data[1][0]
            #     #         else:
            #     #             communications = {}
            #     #     else:
            #     #         communications = {}
            #     # except (KeyError, IndexError) as e:
            #     #     error_msg = f"Error accessing communications data: {e}\n"
            #     #     error_msg += f"data content: {data}\n"
            #     #     error_msg += f"data[1] content: {data[1] if len(data) > 1 else 'N/A'}\n"
            #     #     print(error_msg)

            #     #     error_log_path = Config.WORKSPACE / "error_log.txt"
            #     #     with open(error_log_path, "a", encoding="utf-8") as f:
            #     #         f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]\n{error_msg}\n")
            #     #     communications = {}
            #     # if not blackboard_path:
            #     #     blackboard_path = (
            #     #         Config.WORKSPACE
            #     #         / "department"
            #     #         / "blackboard"
            #     #         / f"day{round_id}"
            #     #         / ("blackboard.json")
            #     #     )
            #     # with open(blackboard_path, "r", encoding="utf-8") as f:
            #     #     blackboard = json.load(f)

            #     # demand = blackboard.get("demand", {})
            #     # for target_dept, message in communications.items():
            #     #     if not message or not message.strip():
            #     #         continue
            #     #     if target_dept not in demand or not isinstance(demand[target_dept], dict):
            #     #         demand[target_dept] = {}
            #     #     demand[target_dept][f"from_{department}"] = message

            #     # blackboard["demand"] = demand
            #     # with open(blackboard_path, "w", encoding="utf-8") as f:
            #     #     json.dump(blackboard, f, ensure_ascii=False, indent=2)
                
            # else:
            #    payload = data    
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in {input_file}: {e}", extra=extra)
            return {"status": "error", "message": f"Invalid JSON in {input_file}: {e}"}
        try:

            request_data = {
                "workflow": payload,
                "execute_type": execute_type,
                "enterprise_name": enterprise_name,
                "dept": department
            }
            
            r = StaticUtils._post_execution(
                f"{Config.BASE_URL}/execute",
                json=request_data,
                headers=StaticUtils.simulation_session_headers(),
                timeout=30,
            )
            
            



            log_dir = Config.LOG_DIR
            log_dir.mkdir(parents=True, exist_ok=True)
            

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = log_dir / f"static_utils_execute_{timestamp}.json"
            
            if r.status_code == 200:
                result = r.json()
            else:

                log_data = {
                    "request": request_data,
                    "status_code": r.status_code,
                    "error": r.text,
                    "timestamp": datetime.now().isoformat()
                }
                
                with open(log_file, "w", encoding="utf-8") as f:
                    json.dump(log_data, f, ensure_ascii=False, indent=2)
                
                r.raise_for_status()
                result = r.json()

            if execution_observer is not None:
                execution_observer(payload, result)

            result_payload = result.get("result") if isinstance(result, dict) else None
            silent_execution_error = ""
            if attempted_action_names:
                if isinstance(result_payload, str):
                    silent_execution_error = result_payload
                elif not isinstance(result_payload, dict):
                    silent_execution_error = (
                        f"Unexpected execution result payload: {result_payload!r}"
                    )
                elif not any(key in result_payload for key in ("success", "failed", "error")):
                    silent_execution_error = (
                        "Execution returned no success/failed/error payload for "
                        f"actions: {', '.join(attempted_action_names)}"
                    )

            if silent_execution_error:
                failed_output = [[{
                    "code": "EXECUTION_RESULT_UNAVAILABLE",
                    "message": silent_execution_error,
                    "actions": attempted_action_names,
                }]]
                if error_file:
                    file_name = error_file.with_name(f"{error_file.stem}_{retry_time}.json")
                    file_name.write_text(
                        json.dumps(failed_output, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                    archive_dir = input_file.parent
                    if execute_type == "run":
                        shutil.move(str(input_file), str(archive_dir / f"{department}_action_{retry_time}.json"))

                    return {
                        "status": "error",
                        "message": "need-retry",
                        "file_path": str(file_name),
                    }
                return {"status": "error", "message": silent_execution_error}

            failed_result = (
                result_payload.get("error")
                if isinstance(result_payload, dict)
                else None
            )
            need_retry = False
            if failed_result:
                failed_actions = []
                if isinstance(failed_result, list):
                    for entry in failed_result:
                        if isinstance(entry, list):
                            failed_actions.extend(
                                item for item in entry if isinstance(item, dict)
                            )
                        elif isinstance(entry, dict):
                            failed_actions.append(entry)
                elif isinstance(failed_result, dict):
                    failed_actions.append(failed_result)
                failed_output = []
                for item in failed_actions:
                    action_type = item.get("action_type") or item.get("action")
                    if action_type == "batch_execute":
                        failed_output.append(item.get("errors") or [])
                        need_retry = True
                if need_retry:
                    print("failed_output",failed_output)
                if need_retry and error_file:
                    file_name = error_file.with_name(f"{error_file.stem}_{retry_time}.json")
                    file_name.write_text(json.dumps(failed_output, ensure_ascii=False, indent=2), encoding="utf-8")

                    archive_dir = input_file.parent

                    # archive_dir = Config.WORKSPACE / "department" / department / f"day{round_id}"
                    # archive_dir.mkdir(parents=True, exist_ok=True)
                    if execute_type == "run":
                        shutil.move(str(input_file), str(archive_dir / f"{department}_action_{retry_time}.json"))

                    return {
                        "status": "error", 
                        "message": "need-retry",
                        "file_path": str(file_name)
                    }
        except FatalModelError:
            raise
        except Exception as e:
            logger.error(f"Execution failed: {e}", extra=extra)
            return {"status": "error", "message": f"Execution failed: {e}"}

        try:
            output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"Result saved to {output_file}", extra=extra)
        except Exception as e:
            logger.error(f"Failed to save result: {e}", extra=extra)
            return {"status": "error", "message": f"Failed to save result: {e}"}

        # logger.info(f"Execution finished: {result}", extra=extra)
        return {"status": "success", "message": f"Execution finished: {result}"}

    @staticmethod
    def save_messages(round_id: int, messages: list):
        """Keep message for the dialogue by round."""
        output_dir = Config.WORKSPACE / "dialogues"
        output_dir.mkdir(parents=True, exist_ok=True)

        path = output_dir / f"round_{round_id}.json"

        data = {
            "round": round_id,
            "messages": messages
        }

        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        logger.info(f"Dialogue saved to {path}")

    @staticmethod
    def archive_json_files(round_id):
        """Archive key JSON files generated by this round in the old working area."""
        if not Config.WORKSPACE.exists():
            raise ValueError(f"Workspace not found: {Config.WORKSPACE}")
        files = [
            "analysis.json",
            "daily_result.json" 
        ]
        # Generate directory name
        archive_dir = Config.WORKSPACE / "records" / f"day{round_id}"
        archive_dir.mkdir(parents=True, exist_ok=True)

        for name in files:
            src = Config.WORKSPACE / name
            if src.exists():
                shutil.move(str(src), str(archive_dir / name))

        StaticUtils.archive_department_files("hr", round_id)
        StaticUtils.archive_department_files("inventory", round_id)
        StaticUtils.archive_department_files("procurement", round_id)
        StaticUtils.archive_department_files("sales", round_id)
        StaticUtils.archive_department_files("production", round_id)

    @staticmethod
    def archive_department_files(
        department: str,
        round_id: int,
        workspace=Config.WORKSPACE
    ):
        """Archive specifies the actions, results and status files department generated during this round."""
        files = [
            f"{department}_action.json",
            f"{department}_result.json",
            f"{department}_error_0.json",
            f"{department}_error_1.json",
            f"{department}_error_2.json",
            f"{department}_failed_result.json",
        ]

        archive_dir = workspace / "department" / department / f"day{round_id}"
        archive_dir.mkdir(parents=True, exist_ok=True)

        for name in files:
            src = workspace / name
            if src.exists():
                shutil.move(src, archive_dir / name)
        src1 = workspace / "department" / department /  f"{department}.json"
        if src1.exists():
            shutil.move(str(src1), str(archive_dir / f"{department}.json"))

    @staticmethod
    def extract_failed():
        """Summarizes department failed results and returns the list of failed department."""
        fail_list = []
        if StaticUtils.extract_failed_results("hr_result.json", "hr_failed_result.json"):
            fail_list.append("hr")
        if StaticUtils.extract_failed_results("inventory_result.json", "inventory_failed_result.json"):
            fail_list.append("inventory")
        if StaticUtils.extract_failed_results("procurement_result.json", "procurement_failed_result.json"):
            fail_list.append("procurement")
        if StaticUtils.extract_failed_results("sales_result.json", "sales_failed_result.json"):
            fail_list.append("sales")
        if StaticUtils.extract_failed_results("production_result.json", "production_failed_result.json"):
            fail_list.append("production")
        return fail_list

    @staticmethod
    def extract_failed_results(
        input_file: str = "result.json",
        output_file: str = "failed_result.json"
    ):
        """Draws failed nodes from the implementation results and separates persist."""
        input_file = Config.WORKSPACE / input_file
        output_file = Config.WORKSPACE / output_file

        if not input_file.exists():
            print(f"{input_file} does not exist")
            return False

        # Read input files
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        failed_list = (
            data.get("result", {})
                .get("failed", [])
        )

        if not isinstance(failed_list, list) or not failed_list:
            print(f"No failed result found in {input_file}; failed_result.json was not created")
            return False

        # Write failed results
        output_file.write_text(
            json.dumps(
                failed_list,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        return True

    @staticmethod
    def _active_run_meta_from_workspace(workspace: Optional[Path] = None) -> Dict[str, Any]:
        workspace = Path(workspace or Config.WORKSPACE)
        run_meta_path = workspace / "run_meta.json"
        if not run_meta_path.exists():
            return {}
        try:
            payload = json.loads(run_meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _active_scenario_config_from_workspace(workspace: Optional[Path] = None) -> Dict[str, Any]:
        run_meta = StaticUtils._active_run_meta_from_workspace(workspace)
        scenario_config = run_meta.get("scenario_config") or {}
        return scenario_config if isinstance(scenario_config, dict) else {}

    @staticmethod
    def _active_runtime_injection_config(workspace: Optional[Path] = None) -> Dict[str, Any]:
        scenario_config = StaticUtils._active_scenario_config_from_workspace(workspace)
        runtime_config = scenario_config.get("runtime_injection") or {}
        if isinstance(runtime_config, dict) and runtime_config:
            return runtime_config
        run_meta = StaticUtils._active_run_meta_from_workspace(workspace)
        runtime_config = run_meta.get("runtime_injection") or {}
        if isinstance(runtime_config, dict) and runtime_config:
            return runtime_config
        return get_runtime_injection_config()

    @staticmethod
    def _active_enterprise_spec_from_workspace(
        enterprise_name: str,
        workspace: Optional[Path] = None,
    ) -> Dict[str, Any]:
        scenario_config = StaticUtils._active_scenario_config_from_workspace(workspace)
        for spec in scenario_config.get("enterprise_specs") or []:
            if not isinstance(spec, dict):
                continue
            identifiers = {
                str(spec.get("enterprise_id") or ""),
                str(spec.get("id") or ""),
                str(spec.get("enterprise_name") or ""),
                str(spec.get("name") or ""),
            }
            if str(enterprise_name) in identifiers:
                return spec
        return next(
            (
                spec for spec in get_active_enterprise_specs()
                if spec.get("enterprise_id") == enterprise_name
            ),
            {},
        )

    @staticmethod
    def _single_enterprise_supplier_candidates_from_runtime(
        runtime_injection_config: Dict[str, Any],
        enterprise_name: str,
    ) -> List[Dict[str, Any]]:
        policy = (
            (runtime_injection_config or {}).get("single_enterprise_supplier_selection_policy")
            or {}
        )
        if not isinstance(policy, dict) or not policy.get("enabled"):
            return []
        target_enterprises = (
            policy.get("target_enterprise_ids")
            or policy.get("enterprise_ids")
            or []
        )
        if target_enterprises and str(enterprise_name) not in {
            str(item) for item in target_enterprises
        }:
            return []
        return [
            deepcopy(item)
            for item in policy.get("candidate_suppliers") or []
            if isinstance(item, dict)
        ]


    @staticmethod
    def handle_observation(
        input_file: str,
        output_file: str = "department",
        workspace: Path = Config.WORKSPACE,
        isDayEnd: bool = False,
        round_id: int = 0,
        analysis_file: str = "analysis.json"
    ):
        'Split observation, generate department status, blackboard and transaction decision card files.'
        with open(input_file, "r", encoding="utf-8") as f:
                obs = json.load(f)

        simulation_context = {
            "market_demand_mode": None,
            "trade_mode": None,
            "is_scheduled_external_demand_mode": False,
            "is_beer_game_mode": False,
            "is_cobweb_mode": False,
            "is_shared_resource_mode": False,
            "is_herding_mode": False,
        }
        saved_simulation_context = obs.get("simulation_context")
        if isinstance(saved_simulation_context, dict) and saved_simulation_context:
            simulation_context.update(saved_simulation_context)
        else:
            try:
                r = requests.get(
                    f"{Config.BASE_URL}/simulation_context",
                    headers=StaticUtils.simulation_session_headers(),
                    timeout=30,
                )
                result = r.json()
                if result.get("status") == "success":
                    simulation_context = result.get("data", simulation_context)
            except Exception as e:
                logger.warning(f"Request /simulation_context failed: {e}")

        agent_simulation_context = StaticUtils.build_agent_simulation_context(simulation_context)
        runtime_injection_config = StaticUtils._active_runtime_injection_config(workspace)
        bullwhip_midstream_pass_through_mode = (
            runtime_injection_config.get("bullwhip_midstream_pass_through_mode") or {}
        )
        bullwhip_proposal_conversion_acceleration = (
            runtime_injection_config.get("bullwhip_proposal_conversion_acceleration") or {}
        )

        def extract_enterprise_name_from_analysis_path() -> str:
            for candidate_path in (Path(str(analysis_file)), Path(str(workspace))):
                candidate_parts = candidate_path.parts
                if "enterprises" in candidate_parts:
                    base_index = candidate_parts.index("enterprises")
                    if len(candidate_parts) > base_index + 1:
                        return candidate_parts[base_index + 1]
            return ""

        active_enterprise_name = extract_enterprise_name_from_analysis_path()
        active_enterprise_spec = StaticUtils._active_enterprise_spec_from_workspace(
            active_enterprise_name,
            workspace,
        )

        def is_runtime_switch_enabled(policy_block: Dict[str, Any]) -> bool:
            if not policy_block or not policy_block.get("enabled"):
                return False
            target_enterprise_ids = set(
                policy_block.get("target_enterprise_ids")
                or policy_block.get("enterprise_ids")
                or []
            )
            target_role_tags = set(policy_block.get("target_role_tags") or [])
            enterprise_match = (not target_enterprise_ids) or active_enterprise_name in target_enterprise_ids
            active_role_tags = set(active_enterprise_spec.get("role_tags") or [])
            role_match = (not target_role_tags) or bool(active_role_tags & target_role_tags)
            return enterprise_match and role_match

        def filter_empty_values(data: dict) -> dict:
            """Recursively filter out keys with empty, null, or empty values at all levels"""
            if not isinstance(data, dict):
                return data
            
            filtered_data = {}
            for k, v in data.items():
                if isinstance(v, dict):
                    # Recursively filter nested dictionary
                    nested_filtered = filter_empty_values(v)
                    if nested_filtered:  # Only keep if not empty after filtering
                        filtered_data[k] = nested_filtered
                elif v is not None and v != {} and v != "" and v != [] and v != 0 and v != 0.0:
                    # Keep non-empty values (excluding null, empty dict, empty string, empty list)
                    filtered_data[k] = v
            return filtered_data

        def extract_department_targets() -> List[Dict]:
            analysis_path = workspace / analysis_file
            if not analysis_path.exists():
                enterprise_dir = analysis_path.parent
                archive_candidates = sorted(
                    enterprise_dir.glob("records/day*/analysis.json"),
                    key=lambda path: int(path.parent.name.removeprefix("day")),
                )
                if archive_candidates:
                    analysis_path = archive_candidates[-1]
                    logger.warning(
                        "Observation fallback: analysis.json missing at %s, using archived analysis %s",
                        workspace / analysis_file,
                        analysis_path,
                    )
                else:
                    logger.warning(
                        "Observation fallback failed: no analysis.json found for %s",
                        workspace / analysis_file,
                    )
                    return []

            with open(analysis_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            dept_targets = data.get("department_targets", {})
            if not isinstance(dept_targets, dict):
                return []
            extracted = []

            for dept, info in dept_targets.items():
                extracted.append({
                    "department": dept,
                    "target": info.get("target"),
                    "evaluation": info.get("evaluation"),
                    "reason": info.get("reason")
                })
            return extracted

        def safe_number(value, default: float = 0.0) -> float:
            try:
                if value is None or value == "":
                    return float(default)
                return float(value)
            except (TypeError, ValueError):
                return float(default)

        def build_inventory_item_index(obs: dict) -> Dict[str, Dict]:
            inventory_items = (obs.get("inventory") or {}).get("inventory_items") or []
            item_index = {}
            for item in inventory_items:
                item_id = item.get("item_id")
                if item_id:
                    item_index[item_id] = item
            return item_index

        def build_sales_signal_summary(obs: dict) -> Dict[str, Dict]:
            sales = obs.get("sales") or {}
            production = obs.get("production") or {}
            demand_backlog = sales.get("demand_backlog") or {}
            by_product = demand_backlog.get("by_product") or {}
            recipes = production.get("product_recipes") or []

            summary = {
                "received_by_item": {},
                "fulfilled_by_item": {},
                "backlog_by_item": {},
                "lost_sales_by_item": {}
            }

            def add_signal(target: Dict[str, float], item_id: str, quantity: float):
                if not item_id:
                    return
                target[item_id] = safe_number(target.get(item_id)) + safe_number(quantity)

            for item_id, item_data in by_product.items():
                add_signal(summary["received_by_item"], item_id, item_data.get("received_quantity"))
                add_signal(summary["fulfilled_by_item"], item_id, item_data.get("fulfilled_quantity"))
                add_signal(summary["backlog_by_item"], item_id, item_data.get("backlog_quantity"))
                add_signal(summary["lost_sales_by_item"], item_id, item_data.get("lost_sales_quantity"))

            for recipe in recipes:
                product_id = recipe.get("product_id")
                product_signal = by_product.get(product_id) or {}
                raw_materials = recipe.get("raw_materials") or {}
                if not product_signal or not raw_materials:
                    continue
                for material_id, units_per_product in raw_materials.items():
                    multiplier = safe_number(units_per_product)
                    if multiplier <= 0:
                        continue
                    add_signal(
                        summary["received_by_item"],
                        material_id,
                        safe_number(product_signal.get("received_quantity")) * multiplier
                    )
                    add_signal(
                        summary["fulfilled_by_item"],
                        material_id,
                        safe_number(product_signal.get("fulfilled_quantity")) * multiplier
                    )
                    add_signal(
                        summary["backlog_by_item"],
                        material_id,
                        safe_number(product_signal.get("backlog_quantity")) * multiplier
                    )
                    add_signal(
                        summary["lost_sales_by_item"],
                        material_id,
                        safe_number(product_signal.get("lost_sales_quantity")) * multiplier
                    )

            return summary

        def build_service_level_summary(obs: dict) -> Dict[str, object]:
            sales = obs.get("sales") or {}
            sales_metrics = sales.get("sales_metrics") or {}
            demand_backlog = sales.get("demand_backlog") or {}
            backlog_breakdown = sales.get("backlog_breakdown") or {}
            backlog_by_product = demand_backlog.get("by_product") or {}
            confirmed_order_backlog = backlog_breakdown.get("confirmed_order_backlog") or {}
            proposal_backlog = backlog_breakdown.get("proposal_backlog") or {}
            stale_backlog = backlog_breakdown.get("stale_backlog") or {}
            total_demand = safe_number(sales_metrics.get("total_downstream_demand"))
            fulfilled_demand = safe_number(sales_metrics.get("fulfilled_downstream_demand"))
            fill_rate = (fulfilled_demand / total_demand) if total_demand > 0 else 0.0

            return {
                "total_downstream_demand": total_demand,
                "fulfilled_downstream_demand": fulfilled_demand,
                "backlog_quantity": safe_number(sales_metrics.get("backlog_quantity")),
                "lost_sales_quantity": safe_number(sales_metrics.get("lost_sales_quantity")),
                "confirmed_order_backlog_quantity": safe_number(
                    sales_metrics.get("confirmed_order_backlog_quantity")
                    or confirmed_order_backlog.get("total_quantity")
                ),
                "overdue_confirmed_order_quantity": safe_number(
                    sales_metrics.get("overdue_confirmed_order_quantity")
                    or confirmed_order_backlog.get("overdue_quantity")
                ),
                "proposal_backlog_quantity": safe_number(
                    sales_metrics.get("proposal_backlog_quantity")
                    or proposal_backlog.get("total_quantity")
                ),
                "stale_backlog_quantity": safe_number(
                    sales_metrics.get("stale_backlog_quantity")
                    or stale_backlog.get("total_quantity")
                ),
                "fill_rate": fill_rate,
                "order_fulfillment_rate": safe_number(sales_metrics.get("order_fulfillment_rate")),
                "on_time_delivery_rate": safe_number(sales_metrics.get("on_time_delivery_rate")),
                "backlog_by_product": {
                    item_id: safe_number(item_data.get("backlog_quantity"))
                    for item_id, item_data in backlog_by_product.items()
                },
                "lost_sales_by_product": {
                    item_id: safe_number(item_data.get("lost_sales_quantity"))
                    for item_id, item_data in backlog_by_product.items()
                },
                "backlog_breakdown": backlog_breakdown,
            }

        def build_cash_summary(obs: dict) -> Dict[str, object]:
            finance = obs.get("finance") or {}
            cash_summary = finance.get("cash_summary") or {}
            current_cash = safe_number(cash_summary.get("current_cash", finance.get("cash")))
            runtime_single_case_policy = (
                (runtime_injection_config or {}).get("single_enterprise_case_policy")
                or {}
            )
            cash_pressure_profile = runtime_single_case_policy.get("cash_pressure_profile") or {}
            scripted_policy = (runtime_injection_config or {}).get("scripted_rule_policy") or {}
            single_case_rules = scripted_policy.get("single_enterprise_rules") or {}
            warning_threshold = safe_number(
                cash_summary.get(
                    "warning_threshold",
                    cash_pressure_profile.get(
                        "warning_threshold",
                        single_case_rules.get("cash_guard_threshold", 0),
                    ),
                )
            )
            critical_threshold = safe_number(
                cash_summary.get(
                    "critical_threshold",
                    cash_pressure_profile.get(
                        "critical_threshold",
                        single_case_rules.get("critical_cash_threshold", 0),
                    ),
                )
            )
            available_after_warning_buffer = safe_number(
                cash_summary.get("available_after_warning_buffer", max(0.0, current_cash - warning_threshold))
            )
            cash_level = cash_summary.get("cash_level")
            if not cash_level:
                if current_cash <= critical_threshold:
                    cash_level = "critical"
                elif current_cash <= warning_threshold:
                    cash_level = "warning"
                else:
                    cash_level = "healthy"
            return {
                "current_cash": current_cash,
                "warning_threshold": warning_threshold,
                "critical_threshold": critical_threshold,
                "available_after_warning_buffer": available_after_warning_buffer,
                "cash_level": cash_level,
                "has_warning_buffer": bool(cash_summary.get("has_warning_buffer", current_cash > warning_threshold)),
                "accounts_payable_balance": safe_number(cash_summary.get("accounts_payable_balance")),
            }

        def build_inventory_policy_summary(obs: dict) -> Dict[str, Dict]:
            item_index = build_inventory_item_index(obs)
            policy_by_item = {}
            for item_id, item in item_index.items():
                policy_by_item[item_id] = {
                    "quantity": safe_number(item.get("quantity")),
                    "su_quantity": safe_number(item.get("su_quantity")),
                    "unit": item.get("unit") or "SU",
                    "item_type": item.get("item_type"),
                    "safety_stock": safe_number(item.get("safety_stock")),
                    "reorder_point": safe_number(item.get("reorder_point")),
                    "is_low_stock": bool(item.get("is_low_stock")),
                    "is_below_reorder_point": bool(item.get("is_below_reorder_point"))
                }
            return policy_by_item

        def build_latest_replenishment_by_material(obs: dict) -> Dict[str, Dict]:
            replenishment = ((obs.get("procurement") or {}).get("replenishment") or {})
            history = replenishment.get("history") or []
            latest_by_material = {}
            for item in history:
                material_id = item.get("material_id")
                if not material_id:
                    continue
                latest_by_material[material_id] = {
                    "round": item.get("round"),
                    "forecast_daily_demand": safe_number(item.get("forecast_daily_demand")),
                    "target_inventory_days": safe_number(item.get("target_inventory_days")),
                    "safety_stock": safe_number(item.get("safety_stock")),
                    "target_inventory": safe_number(item.get("target_inventory")),
                    "inventory_position": safe_number(item.get("inventory_position")),
                    "suggested_order_quantity": safe_number(item.get("suggested_order_quantity")),
                    "requested_quantity": safe_number(item.get("requested_quantity")),
                    "expected_due_round": item.get("expected_due_round"),
                    "created_request": bool(item.get("created_request")),
                    "max_price": item.get("max_price")
                }
            return latest_by_material

        def build_procurement_operational_summary(obs: dict) -> Dict[str, object]:
            procurement = obs.get("procurement") or {}
            inventory_item_index = build_inventory_item_index(obs)
            sales_signal_summary = build_sales_signal_summary(obs)
            replenishment = procurement.get("replenishment") or {}
            pending_by_material = replenishment.get("pending_by_material") or {}
            latest_replenishment_by_material = build_latest_replenishment_by_material(obs)
            recipe_recovery_signal_by_material = procurement.get("recipe_recovery_signal_by_material") or {}
            material_ids = set(procurement.get("purchasable_materials_idList") or [])
            material_ids.update(pending_by_material.keys())
            material_ids.update(sales_signal_summary["backlog_by_item"].keys())
            material_ids.update(recipe_recovery_signal_by_material.keys())

            inventory_position_by_material = {}
            for material_id in sorted(material_ids):
                item = inventory_item_index.get(material_id, {})
                on_hand = safe_number(item.get("quantity"))
                incoming = safe_number(pending_by_material.get(material_id))
                backlog = safe_number(sales_signal_summary["backlog_by_item"].get(material_id))
                inventory_position = on_hand + incoming - backlog
                inventory_position_by_material[material_id] = {
                    "on_hand": on_hand,
                    "incoming": incoming,
                    "backlog": backlog,
                    "inventory_position": inventory_position,
                    "unit": item.get("unit") or "SU",
                    "item_type": item.get("item_type"),
                    "safety_stock": safe_number(item.get("safety_stock")),
                    "reorder_point": safe_number(item.get("reorder_point"))
                }

            return {
                "inventory_position_by_material": inventory_position_by_material,
                "pending_by_material": {
                    material_id: safe_number(quantity)
                    for material_id, quantity in pending_by_material.items()
                },
                "backlog_signal_by_material": sales_signal_summary["backlog_by_item"],
                "latest_replenishment_by_material": latest_replenishment_by_material,
                "recipe_recovery_signal_by_material": recipe_recovery_signal_by_material,
            }

        def safe_int(value) -> int:
            try:
                if value is None or value == "":
                    return 0
                return int(float(value))
            except (TypeError, ValueError):
                return 0

        def read_json_if_exists(path: Path) -> Dict:
            if not path.exists():
                return {}
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}

        def load_previous_department_snapshot(dept: str) -> Dict[str, object]:
            dept_dir = workspace / output_file / dept
            current_name = f"{dept}_DayEnd.json" if isDayEnd else f"{dept}.json"
            same_round_path = dept_dir / f"day{round_id}" / current_name
            if same_round_path.exists():
                return {
                    "scope": "same_round_previous_snapshot",
                    "data": read_json_if_exists(same_round_path)
                }

            if round_id > 0:
                candidates = [
                    dept_dir / f"day{round_id-1}" / f"{dept}.json",
                    dept_dir / f"day{round_id-1}" / f"{dept}_DayEnd.json",
                ]
                for candidate in candidates:
                    if candidate.exists():
                        return {
                            "scope": "previous_round_snapshot",
                            "data": read_json_if_exists(candidate)
                        }

            return {
                "scope": "initial_snapshot",
                "data": {}
            }

        def flatten_order_records(order_block) -> List[Dict]:
            if isinstance(order_block, list):
                return [item for item in order_block if isinstance(item, dict)]
            if isinstance(order_block, dict):
                flattened = []
                for value in order_block.values():
                    if isinstance(value, list):
                        flattened.extend(item for item in value if isinstance(item, dict))
                return flattened
            return []

        def record_fingerprint(record: Dict) -> str:
            try:
                return json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
            except Exception:
                return str(record)

        def build_review_priority(due_risk: str, reason_codes: List[str]) -> int:
            risk_score = {
                "critical": 4,
                "high": 3,
                "medium": 2,
                "low": 1,
            }.get(due_risk, 0)
            reason_bonus = 1 if ("NEW_PROPOSAL" in reason_codes or "UPDATED_PROPOSAL" in reason_codes) else 0
            return risk_score * 10 + reason_bonus

        def build_due_risk(current_round: int, due_round: int, backlog: float, shortage_flag: bool, pending_age_rounds: int) -> str:
            if due_round <= current_round:
                return "critical" if shortage_flag or backlog > 0 else "high"
            if due_round <= current_round + 1:
                return "high" if shortage_flag or backlog > 0 else "medium"
            if shortage_flag or backlog > 0 or pending_age_rounds >= 2:
                return "medium"
            return "low"

        def build_procurement_proposal_card(
            proposal: Dict,
            current_self_state: Dict,
            proposal_event: str
        ) -> Dict:
            material_id = proposal.get("product_id")
            operational_summary = current_self_state.get("operational_summary") or {}
            inventory_position_by_material = operational_summary.get("inventory_position_by_material") or {}
            material_snapshot = inventory_position_by_material.get(material_id) or {}
            latest_replenishment = material_snapshot.get("latest_replenishment") or {}
            due_round = safe_int(proposal.get("proposed_delivery_round"))
            pending_age_rounds = max(0, round_id - safe_int(proposal.get("created_round")))
            backlog = safe_number(material_snapshot.get("backlog"))
            inventory_position = safe_number(material_snapshot.get("inventory_position"))
            on_hand = safe_number(material_snapshot.get("on_hand"))
            incoming = safe_number(material_snapshot.get("incoming"))
            reorder_point = safe_number(material_snapshot.get("reorder_point"))
            safety_stock = safe_number(material_snapshot.get("safety_stock"))
            price_reference = safe_number(latest_replenishment.get("max_price"))
            proposed_price = safe_number(proposal.get("proposed_price"))
            if price_reference <= 0:
                price_reference = proposed_price
            cash_commitment = StaticUtils._procurement_acceptance_cash_guard(
                current_self_state,
                proposal,
            )
            shortage_flag = False
            policy_floor = max(reorder_point, safety_stock)
            if policy_floor > 0:
                shortage_flag = inventory_position <= policy_floor
            elif inventory_position <= 0:
                shortage_flag = True
            due_risk = build_due_risk(round_id, due_round, backlog, shortage_flag, pending_age_rounds)

            reason_codes = []
            if proposal_event:
                reason_codes.append(proposal_event)
            if str(proposal.get("sell_request_id", "")).startswith("implicit_sell_"):
                reason_codes.append("IMPLICIT_ROUTE")
            else:
                reason_codes.append("EXPLICIT_ROUTE")
            if shortage_flag:
                reason_codes.append("LOW_STOCK")
            if backlog > 0:
                reason_codes.append("BACKLOG_PRESSURE")
            if due_round <= round_id + 1:
                reason_codes.append("DUE_SOON")
            if safe_number(price_reference) > 0 and proposed_price > price_reference:
                reason_codes.append("PRICE_ABOVE_HINT")
            if cash_commitment.get("is_hard_blocked"):
                reason_codes.append("CASH_HARD_BLOCK")
            elif cash_commitment.get("commitment_level") == "warning":
                reason_codes.append("CASH_WARNING_AFTER_ACCEPT")

            if "CASH_HARD_BLOCK" in reason_codes:
                recommended_action = "reject_proposal_order"
            elif "PRICE_ABOVE_HINT" in reason_codes:
                recommended_action = "review_price"
            elif due_risk in {"critical", "high"} or shortage_flag or backlog > 0:
                recommended_action = "prioritize_response"
            else:
                recommended_action = "review_now"

            return {
                "proposal_id": proposal.get("proposal_id"),
                "proposal_status": str(proposal.get("status") or "pending").lower(),
                "proposal_event": proposal_event or "UNCHANGED",
                "route": "implicit" if str(proposal.get("sell_request_id", "")).startswith("implicit_sell_") else "explicit",
                "material_id": material_id,
                "quantity": safe_number(proposal.get("quantity")),
                "proposed_price": proposed_price,
                "price_reference": price_reference,
                "pricing_gap": proposed_price - price_reference if price_reference else 0.0,
                "buy_request_id": proposal.get("buy_request_id"),
                "sell_request_id": proposal.get("sell_request_id"),
                "seller_company_id": proposal.get("seller_company_id"),
                "proposed_delivery_round": due_round,
                "pending_age_rounds": pending_age_rounds,
                "inventory_position": inventory_position,
                "on_hand": on_hand,
                "incoming": incoming,
                "backlog": backlog,
                "safety_stock": safety_stock,
                "reorder_point": reorder_point,
                "cash_commitment": cash_commitment,
                "due_risk": due_risk,
                "recommended_action": recommended_action,
                "reason_codes": reason_codes,
                "decision_relevance": {
                    "quantity": "core",
                    "proposed_price": "core",
                    "proposed_delivery_round": "core",
                    "inventory_position": "core",
                    "backlog": "core",
                    "sell_request_id": "secondary"
                }
            }

        def build_sales_proposal_card(
            proposal: Dict,
            current_self_state: Dict,
            inventory_item_index: Dict[str, Dict],
            proposal_event: str
        ) -> Dict:
            midstream_pass_through_enabled = is_runtime_switch_enabled(bullwhip_midstream_pass_through_mode)
            proposal_conversion_acceleration_enabled = is_runtime_switch_enabled(
                bullwhip_proposal_conversion_acceleration
            )
            product_id = proposal.get("product_id")
            item_snapshot = inventory_item_index.get(product_id) or {}
            due_round = safe_int(proposal.get("proposed_delivery_round"))
            pending_age_rounds = max(0, round_id - safe_int(proposal.get("created_round")))
            on_hand = safe_number(item_snapshot.get("quantity"))
            unit = item_snapshot.get("unit") or "SU"
            reference_price = safe_number(item_snapshot.get("unit_price"))
            proposed_price = safe_number(proposal.get("proposed_price"))
            service_level_summary = current_self_state.get("service_level_summary") or {}
            backlog_by_product = (service_level_summary.get("backlog_by_product") or {})
            backlog = safe_number(backlog_by_product.get(product_id))
            confirmed_backlog_quantity = safe_number(service_level_summary.get("confirmed_order_backlog_quantity"))
            stale_backlog_quantity = safe_number(service_level_summary.get("stale_backlog_quantity"))
            fill_rate = safe_number(service_level_summary.get("fill_rate"))
            proposal_quantity = safe_number(proposal.get("quantity"))

            fill_rate_caution_threshold = 0.6
            if midstream_pass_through_enabled:
                fill_rate_caution_threshold = min(
                    fill_rate_caution_threshold,
                    safe_number(
                        bullwhip_midstream_pass_through_mode.get("fill_rate_caution_threshold")
                    ) or 0.45,
                )
            if proposal_conversion_acceleration_enabled:
                future_service_commit_threshold = (
                    bullwhip_proposal_conversion_acceleration.get("future_service_commit_threshold")
                    or ""
                )
                if future_service_commit_threshold == "aggressive":
                    fill_rate_caution_threshold = min(fill_rate_caution_threshold, 0.45)
                elif future_service_commit_threshold == "moderate":
                    fill_rate_caution_threshold = min(fill_rate_caution_threshold, 0.55)

            future_service_backlog_budget_multiplier = 1.0
            if proposal_conversion_acceleration_enabled:
                future_service_backlog_budget_multiplier = 1.5
            future_service_backlog_budget = max(
                proposal_quantity,
                on_hand * 2,
                200.0,
            ) * future_service_backlog_budget_multiplier

            effective_confirmed_backlog_quantity = confirmed_backlog_quantity
            if (
                midstream_pass_through_enabled
                and bullwhip_midstream_pass_through_mode.get("soften_confirmed_backlog_penalty")
            ):
                effective_confirmed_backlog_quantity *= 0.5

            effective_stale_backlog_quantity = stale_backlog_quantity
            if (
                midstream_pass_through_enabled
                and bullwhip_midstream_pass_through_mode.get("ignore_stale_backlog_block_for_new_supply")
            ):
                effective_stale_backlog_quantity = 0.0

            can_commit_future_service = (
                due_round > round_id
                and fill_rate >= fill_rate_caution_threshold
                and (effective_confirmed_backlog_quantity + effective_stale_backlog_quantity)
                <= future_service_backlog_budget
            )
            shortage_flag = on_hand < proposal_quantity and not can_commit_future_service
            if (
                shortage_flag
                and proposal_conversion_acceleration_enabled
                and bullwhip_proposal_conversion_acceleration.get("reduce_rejection_for_inventory_gap_only")
                and due_round > round_id
                and fill_rate >= fill_rate_caution_threshold
            ):
                shortage_flag = False
            due_risk = build_due_risk(round_id, due_round, backlog, shortage_flag, pending_age_rounds)

            reason_codes = []
            if proposal_event:
                reason_codes.append(proposal_event)
            if str(proposal.get("sell_request_id", "")).startswith("implicit_sell_"):
                reason_codes.append("IMPLICIT_ROUTE")
            else:
                reason_codes.append("EXPLICIT_ROUTE")
            if shortage_flag:
                reason_codes.append("STOCK_SHORTAGE")
            elif can_commit_future_service and proposal_quantity > on_hand:
                reason_codes.append("FUTURE_SERVICE_CAPABLE")
            if backlog > 0:
                reason_codes.append("BACKLOG_PRESSURE")
            if due_round <= round_id + 1:
                reason_codes.append("DUE_SOON")
            pricing_gap_tolerance_share = 0.0
            if proposal_conversion_acceleration_enabled:
                pricing_gap_tolerance_share = 0.05 * safe_number(
                    bullwhip_proposal_conversion_acceleration.get("pricing_gap_tolerance_multiplier")
                )
            if (
                reference_price > 0
                and proposed_price < reference_price
                and proposed_price < reference_price * max(0.0, 1.0 - pricing_gap_tolerance_share)
            ):
                reason_codes.append("PRICE_BELOW_REFERENCE")
            if confirmed_backlog_quantity > 0:
                reason_codes.append("CONFIRMED_BACKLOG")
            if stale_backlog_quantity > 0 and effective_stale_backlog_quantity > 0:
                reason_codes.append("STALE_BACKLOG")
            if midstream_pass_through_enabled:
                reason_codes.append("MIDSTREAM_PASS_THROUGH_MODE")
            if proposal_conversion_acceleration_enabled and can_commit_future_service:
                reason_codes.append("PROPOSAL_CONVERSION_ACCELERATED")

            if "PRICE_BELOW_REFERENCE" in reason_codes:
                recommended_action = "review_price"
            elif can_commit_future_service and due_round > round_id:
                recommended_action = "accept_proposal_order"
            elif (
                proposal_conversion_acceleration_enabled
                and bullwhip_proposal_conversion_acceleration.get("prefer_accept_when_due_in_future")
                and due_round > round_id
                and not shortage_flag
                and "PRICE_BELOW_REFERENCE" not in reason_codes
            ):
                recommended_action = "accept_proposal_order"
            elif shortage_flag:
                recommended_action = "review_capacity"
            elif due_risk in {"critical", "high"}:
                recommended_action = "prioritize_response"
            else:
                recommended_action = "review_now"

            return {
                "proposal_id": proposal.get("proposal_id"),
                "proposal_status": str(proposal.get("status") or "pending").lower(),
                "proposal_event": proposal_event or "UNCHANGED",
                "route": "implicit" if str(proposal.get("sell_request_id", "")).startswith("implicit_sell_") else "explicit",
                "product_id": product_id,
                "quantity": safe_number(proposal.get("quantity")),
                "proposed_price": proposed_price,
                "price_reference": reference_price,
                "pricing_gap": proposed_price - reference_price if reference_price else 0.0,
                "buy_request_id": proposal.get("buy_request_id"),
                "buyer_company_id": proposal.get("buyer_company_id"),
                "proposed_delivery_round": due_round,
                "pending_age_rounds": pending_age_rounds,
                "on_hand": on_hand,
                "unit": unit,
                "backlog": backlog,
                "fill_rate": fill_rate,
                "confirmed_order_backlog_quantity": confirmed_backlog_quantity,
                "stale_backlog_quantity": stale_backlog_quantity,
                "can_commit_future_service": can_commit_future_service,
                "future_service_backlog_budget": future_service_backlog_budget,
                "policy_modes": {
                    "midstream_pass_through_enabled": midstream_pass_through_enabled,
                    "proposal_conversion_acceleration_enabled": proposal_conversion_acceleration_enabled,
                },
                "due_risk": due_risk,
                "recommended_action": recommended_action,
                "reason_codes": reason_codes,
                "decision_relevance": {
                    "quantity": "core",
                    "proposed_price": "core",
                    "proposed_delivery_round": "core",
                    "on_hand": "core",
                    "backlog": "core",
                    "fill_rate": "core",
                    "confirmed_order_backlog_quantity": "core",
                    "buy_request_id": "secondary"
                }
            }

        def build_order_delta(current_orders_block, previous_orders_block, order_type: str) -> List[Dict]:
            current_orders = flatten_order_records(current_orders_block)
            previous_orders = flatten_order_records(previous_orders_block)
            previous_ids = {
                order.get("order_id")
                for order in previous_orders
                if order.get("order_id")
            }
            deltas = []
            for order in current_orders:
                order_id = order.get("order_id")
                if not order_id or order_id in previous_ids:
                    continue
                deltas.append({
                    "order_id": order_id,
                    "order_type": order_type,
                    "status": order.get("status"),
                    "item_id": order.get("material_id") or order.get("product_id"),
                    "quantity": safe_number(order.get("quantity")),
                    "arrival_time": order.get("arrival_time"),
                    "delivery_deadline": order.get("delivery_deadline"),
                    "source_id": order.get("source_id") or order.get("supplier_id"),
                })
            return deltas

        def build_numeric_delta(current_map: Dict, previous_map: Dict, fields: List[str] = None) -> List[Dict]:
            fields = fields or []
            keys = set(current_map.keys()) | set(previous_map.keys())
            deltas = []
            for key in sorted(keys):
                current_item = current_map.get(key) or {}
                previous_item = previous_map.get(key) or {}
                changed = False
                item_delta = {"item_id": key}
                for field_name in fields:
                    current_value = safe_number(current_item.get(field_name))
                    previous_value = safe_number(previous_item.get(field_name))
                    if abs(current_value - previous_value) > 1e-9:
                        changed = True
                    item_delta[field_name] = current_value
                    item_delta[f"{field_name}_delta"] = current_value - previous_value
                if changed:
                    deltas.append(item_delta)
            return deltas

        def build_trade_action_candidates(
            dept: str,
            review_queue: List[Dict],
            pressure_delta: List[Dict],
            current_self_state: Dict,
            policy_context: Dict[str, Any],
        ) -> List[Dict]:
            """Build structured, non-binding action candidates for buyer/seller skills."""
            action_constraints = (policy_context or {}).get("action_constraints") or {}
            active_modes = (policy_context or {}).get("active_modes") or {}
            relevant_policies = (policy_context or {}).get("relevant_policies") or {}
            profit_policy = (relevant_policies or {}).get("profit_objective_policy") or {}
            profit_params = profit_policy.get("params") or {}
            objective_weights = profit_params.get("objective_weights") or {}
            priority_rules = (policy_context or {}).get("priority_rules") or []
            profit_ranking_enabled = bool(
                active_modes.get("profit_objective")
                and action_constraints.get("profit_objective_candidate_ranking_enabled")
            )
            candidates: List[Dict] = []
            procurement_allowed_material_ids = (
                StaticUtils._get_procurement_allowed_material_ids(current_self_state)
                if dept == "procurement"
                else set()
            )

            def build_objective_evaluation(item: Dict[str, Any], action_name: str) -> Dict[str, Any]:
                if not profit_ranking_enabled:
                    return {}
                reason_codes = item.get("reason_codes") or []
                due_risk = item.get("due_risk")
                due_score = {"critical": 1.0, "high": 0.8, "medium": 0.45, "low": 0.15}.get(due_risk, 0.0)
                pressure_score = max(
                    due_score,
                    0.85 if ("LOW_STOCK" in reason_codes or "BACKLOG_PRESSURE" in reason_codes) else 0.0,
                    0.75 if "DUE_SOON" in reason_codes else 0.0,
                )
                cash_safety = 1.0
                margin_signal = 0.5
                inventory_risk = 0.0
                feasibility = 0.85
                risk_flags: List[str] = []

                if dept == "procurement":
                    cash_commitment = item.get("cash_commitment") or {}
                    if cash_commitment.get("is_hard_blocked"):
                        cash_safety = 0.0
                        feasibility = 0.15
                        risk_flags.append("cash_hard_block")
                    elif cash_commitment.get("commitment_level") == "warning":
                        cash_safety = 0.35
                        risk_flags.append("cash_warning")
                    if "PRICE_ABOVE_HINT" in reason_codes:
                        margin_signal = 0.25
                        risk_flags.append("price_above_hint")
                    else:
                        margin_signal = 0.62
                    inventory_risk = max(pressure_score, 0.35)
                    accept_score = (
                        0.30 * margin_signal
                        + 0.25 * cash_safety
                        + 0.25 * pressure_score
                        + 0.10 * inventory_risk
                        + 0.10 * feasibility
                    )
                    reject_score = (
                        0.35 * (1.0 - margin_signal)
                        + 0.30 * (1.0 - cash_safety)
                        + 0.20 * (1.0 - pressure_score)
                        + 0.15 * feasibility
                    )
                else:
                    stock_shortage = "STOCK_SHORTAGE" in reason_codes
                    if stock_shortage and not item.get("can_commit_future_service"):
                        feasibility = 0.25
                        inventory_risk = 0.95
                        risk_flags.append("stock_shortage")
                    elif item.get("can_commit_future_service"):
                        feasibility = 0.75
                        inventory_risk = 0.45
                    else:
                        inventory_risk = 0.25
                    if "PRICE_BELOW_REFERENCE" in reason_codes:
                        margin_signal = 0.2
                        risk_flags.append("price_below_reference")
                    elif (item.get("pricing_gap") or 0) >= 0:
                        margin_signal = 0.72
                    else:
                        margin_signal = 0.48
                    accept_score = (
                        0.35 * margin_signal
                        + 0.15 * cash_safety
                        + 0.25 * pressure_score
                        + 0.15 * (1.0 - inventory_risk)
                        + 0.10 * feasibility
                    )
                    reject_score = (
                        0.35 * (1.0 - margin_signal)
                        + 0.30 * inventory_risk
                        + 0.20 * (1.0 - pressure_score)
                        + 0.15 * feasibility
                    )

                chosen_score = accept_score if action_name == "accept_proposal_order" else reject_score
                return {
                    "mode": "C3_profit_objective_candidate_ranking",
                    "score": round(chosen_score, 4),
                    "accept_score": round(accept_score, 4),
                    "reject_score": round(reject_score, 4),
                    "dimensions": {
                        "margin_or_cost_signal": round(margin_signal, 4),
                        "cash_safety": round(cash_safety, 4),
                        "service_pressure": round(pressure_score, 4),
                        "inventory_or_resource_risk": round(inventory_risk, 4),
                        "execution_feasibility": round(feasibility, 4),
                    },
                    "objective_weights": objective_weights,
                    "risk_flags": risk_flags,
                    "note": "Heuristic ranking aid only; environment validation remains authoritative.",
                }

            def add_candidate(
                *,
                action_name: str,
                action_param: Dict[str, Any],
                source: str,
                confidence: str,
                reason_codes: List[str],
                proposal_id: str = None,
                item_id: str = None,
                objective_evaluation: Dict[str, Any] = None,
            ) -> None:
                candidate_key = proposal_id or item_id or len(candidates)
                candidate = {
                    "candidate_id": f"{dept}_{action_name}_{candidate_key}",
                    "department": dept,
                    "action_name": action_name,
                    "action_param": action_param,
                    "source": source,
                    "confidence": confidence,
                    "proposal_id": proposal_id,
                    "item_id": item_id,
                    "reason_codes": reason_codes,
                    "policy_priority_rules": priority_rules,
                }
                if objective_evaluation:
                    candidate["objective_evaluation"] = objective_evaluation
                candidates.append(candidate)

            for item in review_queue[:8]:
                proposal_id = item.get("proposal_id")
                if not proposal_id:
                    continue
                proposal_status = str(item.get("proposal_status") or "pending").lower()
                if proposal_status not in StaticUtils.ACTIONABLE_PROPOSAL_STATUSES:
                    continue
                reason_codes = item.get("reason_codes") or []
                due_risk = item.get("due_risk")
                recommended_action = item.get("recommended_action")

                if dept == "procurement":
                    material_id = item.get("material_id")
                    if (
                        procurement_allowed_material_ids
                        and material_id
                        and str(material_id) not in procurement_allowed_material_ids
                    ):
                        continue
                    cash_commitment = item.get("cash_commitment") or {}
                    proposal_cash_blocked = bool(cash_commitment.get("is_hard_blocked"))
                    cash_guard = current_self_state.get("cash_guard") or {}
                    hard_blocked = cash_guard.get("guard_level") == "hard_blocked"
                    price_above_hint = "PRICE_ABOVE_HINT" in reason_codes
                    pressure_high = (
                        due_risk in {"critical", "high"}
                        or "LOW_STOCK" in reason_codes
                        or "BACKLOG_PRESSURE" in reason_codes
                    )
                    if proposal_cash_blocked or recommended_action == "reject_proposal_order":
                        add_candidate(
                            action_name="reject_proposal_order",
                            action_param={"proposal_id": proposal_id},
                            source="trade_decision_card.review_queue",
                            confidence="high" if proposal_cash_blocked else "medium",
                            proposal_id=proposal_id,
                            reason_codes=reason_codes + (
                                ["PROPOSAL_CASH_HARD_BLOCKED"] if proposal_cash_blocked else []
                            ),
                            objective_evaluation=build_objective_evaluation(item, "reject_proposal_order"),
                        )
                    elif hard_blocked:
                        add_candidate(
                            action_name="reject_proposal_order",
                            action_param={"proposal_id": proposal_id},
                            source="trade_decision_card.review_queue",
                            confidence="medium",
                            proposal_id=proposal_id,
                            reason_codes=reason_codes + ["ENTERPRISE_CASH_GUARD_HARD_BLOCKED"],
                            objective_evaluation=build_objective_evaluation(item, "reject_proposal_order"),
                        )
                    elif price_above_hint and not pressure_high:
                        add_candidate(
                            action_name="reject_proposal_order",
                            action_param={"proposal_id": proposal_id},
                            source="trade_decision_card.review_queue",
                            confidence="low",
                            proposal_id=proposal_id,
                            reason_codes=reason_codes + ["PRICE_RISK_WITHOUT_HIGH_PRESSURE"],
                            objective_evaluation=build_objective_evaluation(item, "reject_proposal_order"),
                        )
                    elif pressure_high or recommended_action in {"prioritize_response", "review_now"}:
                        add_candidate(
                            action_name="accept_proposal_order",
                            action_param={"proposal_id": proposal_id},
                            source="trade_decision_card.review_queue",
                            confidence="high" if pressure_high else "medium",
                            proposal_id=proposal_id,
                            reason_codes=reason_codes,
                            objective_evaluation=build_objective_evaluation(item, "accept_proposal_order"),
                        )
                else:
                    price_below_reference = "PRICE_BELOW_REFERENCE" in reason_codes
                    stock_shortage = "STOCK_SHORTAGE" in reason_codes
                    can_commit_future_service = bool(item.get("can_commit_future_service"))
                    if recommended_action == "accept_proposal_order":
                        add_candidate(
                            action_name="accept_proposal_order",
                            action_param={"proposal_id": proposal_id},
                            source="trade_decision_card.review_queue",
                            confidence="high" if can_commit_future_service else "medium",
                            proposal_id=proposal_id,
                            reason_codes=reason_codes,
                            objective_evaluation=build_objective_evaluation(item, "accept_proposal_order"),
                        )
                    elif price_below_reference or (stock_shortage and not can_commit_future_service):
                        add_candidate(
                            action_name="reject_proposal_order",
                            action_param={"proposal_id": proposal_id},
                            source="trade_decision_card.review_queue",
                            confidence="medium",
                            proposal_id=proposal_id,
                            reason_codes=reason_codes,
                            objective_evaluation=build_objective_evaluation(item, "reject_proposal_order"),
                        )
                    elif due_risk in {"critical", "high"} and not stock_shortage:
                        add_candidate(
                            action_name="accept_proposal_order",
                            action_param={"proposal_id": proposal_id},
                            source="trade_decision_card.review_queue",
                            confidence="low",
                            proposal_id=proposal_id,
                            reason_codes=reason_codes + ["DUE_RISK_WITHOUT_STOCK_SHORTAGE"],
                            objective_evaluation=build_objective_evaluation(item, "accept_proposal_order"),
                        )

            if dept == "procurement" and action_constraints.get("allow_create_replenishment_order") is not False:
                seen_items = set()
                for item in pressure_delta:
                    item_id = item.get("item_id")
                    if not item_id or item_id in seen_items:
                        continue
                    if procurement_allowed_material_ids and str(item_id) not in procurement_allowed_material_ids:
                        continue
                    inventory_position = safe_number(item.get("inventory_position"))
                    inventory_position_delta = safe_number(item.get("inventory_position_delta"))
                    backlog = safe_number(item.get("backlog"))
                    backlog_delta = safe_number(item.get("backlog_delta"))
                    if inventory_position < 0 or inventory_position_delta < 0 or backlog > 0 or backlog_delta > 0:
                        seen_items.add(item_id)
                        add_candidate(
                            action_name="create_replenishment_order",
                            action_param={"material_id": item_id},
                            source="trade_decision_card.delta.pressure_changes",
                            confidence="medium",
                            item_id=item_id,
                            reason_codes=[
                                "PRESSURE_CHANGE",
                                f"inventory_position={inventory_position}",
                                f"backlog={backlog}",
                            ],
                        )

            if profit_ranking_enabled:
                candidates = sorted(
                    candidates,
                    key=lambda candidate: (
                        -float((candidate.get("objective_evaluation") or {}).get("score") or 0.0),
                        candidate.get("candidate_id") or "",
                    ),
                )
            return candidates[:10]

        def build_trade_decision_card(
            dept: str,
            current_self_state: Dict,
            previous_snapshot_meta: Dict,
            policy_context: Dict[str, Any],
        ) -> Dict:
            previous_snapshot = previous_snapshot_meta.get("data") or {}
            previous_self_state = previous_snapshot.get("self_state") or {}
            current_proposals = current_self_state.get("proposals_list") or []
            previous_proposals = previous_self_state.get("proposals_list") or []
            exchange_proposal_index = StaticUtils._get_exchange_proposal_index(round_id)
            exchange_buy_request_index = StaticUtils._get_exchange_buy_request_index(round_id)
            procurement_allowed_material_ids = (
                StaticUtils._get_procurement_allowed_material_ids(current_self_state)
                if dept == "procurement"
                else set()
            )
            previous_proposal_map = {
                proposal.get("proposal_id"): proposal
                for proposal in previous_proposals
                if proposal.get("proposal_id")
            }

            inventory_item_index = build_inventory_item_index(obs)
            new_or_updated = []
            unchanged_queue = []
            filtered_proposals = []

            for proposal in current_proposals:
                proposal_id = proposal.get("proposal_id")
                if not proposal_id:
                    continue
                exchange_proposal = exchange_proposal_index.get(str(proposal_id))
                authoritative_proposal = exchange_proposal or proposal
                actionability = StaticUtils._proposal_actionability_status(authoritative_proposal, dept)
                if not actionability.get("actionable"):
                    filtered_proposals.append({
                        "proposal_id": proposal_id,
                        "status": actionability.get("status"),
                        "reason": actionability.get("reason"),
                        "source": "exchange" if exchange_proposal else "state_snapshot",
                    })
                    continue
                buy_request_id = authoritative_proposal.get("buy_request_id")
                if buy_request_id:
                    buy_request = exchange_buy_request_index.get(str(buy_request_id)) or {}
                    lifecycle_status = str(buy_request.get("lifecycle_status") or "").lower()
                    if lifecycle_status in {"superseded", "expired", "cancelled", "canceled"}:
                        filtered_proposals.append({
                            "proposal_id": proposal_id,
                            "status": actionability.get("status"),
                            "reason": f"buy_request_{lifecycle_status}_not_actionable",
                            "buy_request_id": buy_request_id,
                            "source": "exchange_buy_request",
                        })
                        continue
                if dept == "procurement" and procurement_allowed_material_ids:
                    proposal_material_id = authoritative_proposal.get("product_id") or authoritative_proposal.get("material_id")
                    if (
                        proposal_material_id
                        and str(proposal_material_id) not in procurement_allowed_material_ids
                    ):
                        filtered_proposals.append({
                            "proposal_id": proposal_id,
                            "status": actionability.get("status"),
                            "reason": "material_not_in_procurement_allowed_set",
                            "material_id": proposal_material_id,
                        })
                        continue
                previous_proposal = previous_proposal_map.get(proposal_id)
                proposal_event = ""
                if previous_proposal is None:
                    proposal_event = "NEW_PROPOSAL"
                elif record_fingerprint(previous_proposal) != record_fingerprint(proposal):
                    proposal_event = "UPDATED_PROPOSAL"

                if dept == "procurement":
                    card_item = build_procurement_proposal_card(authoritative_proposal, current_self_state, proposal_event)
                else:
                    card_item = build_sales_proposal_card(authoritative_proposal, current_self_state, inventory_item_index, proposal_event)

                if proposal_event:
                    new_or_updated.append(card_item)
                unchanged_queue.append(card_item)

            review_queue = sorted(
                unchanged_queue,
                key=lambda item: (
                    -build_review_priority(item.get("due_risk"), item.get("reason_codes") or []),
                    item.get("proposed_delivery_round") or 0,
                    item.get("proposal_id") or "",
                )
            )

            if dept == "procurement":
                current_ops = (current_self_state.get("operational_summary") or {}).get("inventory_position_by_material") or {}
                previous_ops = (previous_self_state.get("operational_summary") or {}).get("inventory_position_by_material") or {}
                pressure_delta = build_numeric_delta(
                    current_ops,
                    previous_ops,
                    ["inventory_position", "backlog", "incoming", "on_hand"]
                )
                local_order_delta = build_order_delta(
                    current_self_state.get("orders") or {},
                    previous_self_state.get("orders") or {},
                    "purchase_order"
                )
            else:
                current_backlog = ((current_self_state.get("service_level_summary") or {}).get("backlog_by_product") or {})
                previous_backlog = ((previous_self_state.get("service_level_summary") or {}).get("backlog_by_product") or {})
                current_backlog_map = {
                    item_id: {"backlog": quantity}
                    for item_id, quantity in current_backlog.items()
                }
                previous_backlog_map = {
                    item_id: {"backlog": quantity}
                    for item_id, quantity in previous_backlog.items()
                }
                pressure_delta = build_numeric_delta(
                    current_backlog_map,
                    previous_backlog_map,
                    ["backlog"]
                )
                local_order_delta = build_order_delta(
                    current_self_state.get("sales_orders") or {},
                    previous_self_state.get("sales_orders") or {},
                    "sales_order"
                )
            action_candidates = build_trade_action_candidates(
                dept=dept,
                review_queue=review_queue,
                pressure_delta=pressure_delta,
                current_self_state=current_self_state,
                policy_context=policy_context,
            )

            return {
                "card_type": "trade_decision_card",
                "department": dept,
                "round_id": round_id,
                "comparison_scope": previous_snapshot_meta.get("scope"),
                "policy_context": policy_context,
                "summary": {
                    "current_pending_proposals": len(current_proposals),
                    "current_actionable_proposals": len(unchanged_queue),
                    "filtered_non_actionable_proposals": len(filtered_proposals),
                    "previous_pending_proposals": len(previous_proposals),
                    "new_or_updated_proposals": len(new_or_updated),
                    "new_local_orders": len(local_order_delta),
                    "changed_pressure_items": len(pressure_delta),
                    "action_candidates": len(action_candidates),
                    "c3_profit_candidate_ranking_enabled": bool(
                        ((policy_context or {}).get("active_modes") or {}).get("profit_objective")
                        and ((policy_context or {}).get("action_constraints") or {}).get(
                            "profit_objective_candidate_ranking_enabled"
                        )
                    ),
                },
                "delta": {
                    "new_or_updated_proposals": new_or_updated,
                    "new_local_orders": local_order_delta,
                    "pressure_changes": pressure_delta,
                },
                "review_queue": review_queue[:8],
                "filtered_proposals": filtered_proposals[:8],
                "action_candidates": action_candidates,
            }

        def extract_self_state(dept: str, obs: dict) -> dict:
            if dept == "finance":
                f = obs["finance"]
                finance_data = {
                    "cash": f.get("cash"),
                    "initial_cash": f.get("initial_cash"),
                    "total_revenue": f.get("total_revenue"),
                    "total_cost": f.get("total_cost"),
                    "financial_indicators": f.get("financial_indicators"),
                    "cash_summary": build_cash_summary(obs),
                    # "balance_sheet_summary": {
                    #     "total_assets": f["balance_sheet"]["financial"]["total_assets"],
                    #     "total_liabilities": f["balance_sheet"]["financial"]["total_liabilities"],
                    #     "total_equity": f["balance_sheet"]["financial"]["total_equity"]
                    # }
                }
                return finance_data
                # return filter_empty_values(finance_data)

            if dept == "hr":
                department_staffing = StaticUtils._normalize_hr_staffing_map(obs["hr"])
                hr_data = {
                    "employees": obs["hr"].get("employees"),
                    "department_staffing": department_staffing,
                    "total_payroll": obs["hr"].get("total_payroll"),
                    "recruitment_status": obs["hr"].get("recruitment_status")
                }
                return hr_data
                # return filter_empty_values(hr_data)

            if dept == "production":
                p = obs["production"]
                production_data = {
                    "products_idList": p.get("products_idList"),
                    "production_lines": p.get("production_lines"),
                    "production_plans": p.get("production_plans"),
                    "product_recipes": p.get("product_recipes"),
                    "production_metrics": p.get("production_metrics"),
                    "cobweb_decision_signal": StaticUtils.build_cobweb_decision_signal(
                        agent_simulation_context,
                        round_id=round_id,
                    ),
                    "herding_decision_signal": StaticUtils.build_herding_decision_signal(
                        agent_simulation_context,
                        round_id=round_id,
                    ),
                    "recovery_guard": p.get("recovery_guard"),
                    "cash_guard": p.get("cash_guard"),
                    "margin_guard": p.get("margin_guard"),
                }
                production_data = StaticUtils.redact_production_agent_state_for_cobweb(
                    production_data,
                    agent_simulation_context,
                )
                production_data = StaticUtils.redact_production_agent_state_for_shared_resource(
                    production_data,
                    agent_simulation_context,
                )
                return StaticUtils.redact_production_agent_state_for_herding(
                    production_data,
                    agent_simulation_context,
                )
                # return filter_empty_values(production_data)

            if dept == "sales":
                s = obs["sales"]
                sales_data = {
                    "sales_orders": s.get("sales_orders"),
                    "markets": s.get("markets"),
                    # "customers": s.get("customers"),
                    "sales_metrics": s.get("sales_metrics"),
                    "salable_products_idList": s.get("salable_products_idList"),
                    "pending_proposals_count": s.get("pending_proposals_count"),
                    "proposals_list": s.get("proposals_list"),
                    "herding_decision_signal": StaticUtils.build_herding_decision_signal(
                        agent_simulation_context,
                        round_id=round_id,
                    ),
                    # "proposal_history": s.get("proposal_history"),
                    "demand_backlog": s.get("demand_backlog"),
                    "service_level_summary": build_service_level_summary(obs)
                }
                return sales_data
                # return filter_empty_values(sales_data)

            if dept == "procurement":
                p = obs["procurement"]
                supplier_candidates = (
                    p.get("supplier_candidates")
                    or StaticUtils._single_enterprise_supplier_candidates_from_runtime(
                        runtime_injection_config,
                        active_enterprise_name,
                    )
                )
                materials_suppliers_matrix = deepcopy(
                    p.get("materials_suppliers_matrix") or {}
                )
                explicit_material_ids = {
                    str(item)
                    for item in (p.get("purchasable_materials_idList") or [])
                    if item not in (None, "")
                }
                if explicit_material_ids:
                    materials_suppliers_matrix = {
                        str(material_id): suppliers
                        for material_id, suppliers in materials_suppliers_matrix.items()
                        if str(material_id) in explicit_material_ids
                    }
                    filtered_candidates = []
                    for candidate in supplier_candidates:
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
                    supplier_candidates = filtered_candidates
                if not materials_suppliers_matrix and supplier_candidates:
                    for supplier in supplier_candidates:
                        if not isinstance(supplier, dict):
                            continue
                        for material_id, material_info in (supplier.get("materials") or {}).items():
                            if not isinstance(material_info, dict):
                                continue
                            materials_suppliers_matrix.setdefault(str(material_id), []).append({
                                "supplier_name": supplier.get("supplier_name"),
                                "supplier_type": supplier.get("supplier_type"),
                                "unit_price": material_info.get("unit_price", 0),
                                "available_quantity": material_info.get("quantity", 0),
                                "min_order_quantity": material_info.get("min_order_quantity", 0),
                                "quality_level": supplier.get("quality_level"),
                                "reliability_score": supplier.get("reliability_score"),
                                "processing_time": supplier.get("processing_time", 0),
                                "registration_status": supplier.get("registration_status", "candidate"),
                            })
                procurement_data = {
                    "purchasable_materials_idList": p.get("purchasable_materials_idList"),
                    "suppliers": p.get("suppliers_detail"),
                    "supplier_candidates": supplier_candidates,
                    "supplier_selection_events": p.get("supplier_selection_events"),
                    "materials_suppliers_matrix": materials_suppliers_matrix,
                    "orders": p.get("orders"),
                    "procurement_metrics": p.get("procurement_metrics"),
                    "pending_proposals_count": p.get("pending_proposals_count"),
                    "proposals_list": p.get("proposals_list"),
                    # "proposal_history": p.get("proposal_history"),
                    "replenishment": p.get("replenishment"),
                    "operational_summary": build_procurement_operational_summary(obs),
                    "cash_guard": p.get("cash_guard"),
                    "top_tier_supply_guard": p.get("top_tier_supply_guard"),
                    "top_tier_supply_plan": p.get("top_tier_supply_plan"),
                    "recipe_recovery_signal_by_material": p.get("recipe_recovery_signal_by_material"),
                }
                if p.get("supplier_name_list") and p.get("supplier_name_list") != []:
                    procurement_data["supplier_name_list"] = p.get("supplier_name_list")
                return procurement_data
                # return filter_empty_values(procurement_data)

            if dept == "inventory":
                i = obs["inventory"]
                inventory_data = {
                    "inventory_items": i.get("inventory_items"),
                    "warehouse_capacity": i.get("warehouse_capacity"),
                    "used_capacity": i.get("used_capacity"),
                    "inventory_metrics": i.get("inventory_metrics"),
                    "policy_by_item": build_inventory_policy_summary(obs)
                }
                return inventory_data
                # return filter_empty_values(inventory_data)

            return {}

        public_targets  = extract_department_targets()

        def build_department_target_map(public_targets: list) -> dict:
            """
            Convert the public target list to the map of dept->targetinfo
                        """
            target_map = {}

            for item in public_targets:
                dept = item["department"]
                target_map[dept] = {
                    "target": item.get("target"),
                    "evaluation": item.get("evaluation"),
                    "reason": item.get("reason")
                }

            return target_map

        def line_status_count(production_lines: Dict[str, Any], statuses: Set[str]) -> float:
            by_status = production_lines.get("by_status") or {}
            if isinstance(by_status, dict):
                total = sum(safe_number(by_status.get(status)) for status in statuses)
                if total > 0:
                    return total
            count = 0.0
            for line in production_lines.get("details") or []:
                if isinstance(line, dict) and str(line.get("status") or "").lower() in statuses:
                    count += 1.0
            return count

        def read_chart_export() -> Dict[str, Any]:
            chart_path = workspace / "enterprises" / active_enterprise_name / "charts_data_export.json"
            try:
                if chart_path.exists():
                    with open(chart_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                return {}
            return {}

        chart_export_snapshot = read_chart_export()

        def build_capacity_expansion_signal(self_state: Dict[str, Any]) -> Dict[str, Any]:
            production_lines = (self_state or {}).get("production_lines") or {}
            if not isinstance(production_lines, dict):
                production_lines = {}
            production_metrics = (self_state or {}).get("production_metrics") or {}
            charts = chart_export_snapshot.get("charts") or {}
            capacity_chart = charts.get("capacity_utilization_analysis") or {}
            series = capacity_chart.get("series") or {}
            recent_utilization = [
                safe_number(value)
                for value in (series.get("capacity_utilization_percent") or [])[-3:]
            ]
            recent_total_capacity = [
                safe_number(value)
                for value in (series.get("total_capacity") or [])[-3:]
            ]
            recent_available_capacity = [
                safe_number(value)
                for value in (series.get("available_capacity") or [])[-3:]
            ]
            total_capacity = safe_number(
                production_lines.get("total_capacity")
                if production_lines.get("total_capacity") not in (None, "")
                else (self_state or {}).get("total_capacity")
            )
            available_capacity = safe_number(
                production_lines.get("available_capacity")
                if production_lines.get("available_capacity") not in (None, "")
                else (self_state or {}).get("available_capacity")
            )
            occupied_capacity = safe_number(production_lines.get("occupied_capacity"))
            current_utilization = safe_number(production_metrics.get("capacity_utilization"))
            if current_utilization <= 1.5:
                current_utilization *= 100
            if current_utilization <= 0 and total_capacity > 0:
                current_utilization = max(
                    0.0,
                    min(100.0, occupied_capacity / max(total_capacity, 1.0) * 100),
                )
            if not recent_utilization and current_utilization > 0:
                recent_utilization = [current_utilization]
            if not recent_total_capacity:
                recent_total_capacity = [total_capacity]
            if not recent_available_capacity:
                recent_available_capacity = [available_capacity]

            recent_high_utilization_rounds = sum(1 for value in recent_utilization if value >= 85.0)
            recent_zero_available_rounds = sum(1 for value in recent_available_capacity if value <= 0.0)
            avg_recent_utilization = (
                sum(recent_utilization) / len(recent_utilization)
                if recent_utilization
                else current_utilization
            )
            sales_state = (obs or {}).get("sales") or {}
            sales_orders = sales_state.get("sales_orders") or {}
            committed_order_quantity = 0.0
            valid_available_order_quantity = 0.0
            if isinstance(sales_orders, dict):
                for bucket in ("accepted", "in_progress", "backlog"):
                    for order in sales_orders.get(bucket) or []:
                        if not isinstance(order, dict):
                            continue
                        quantity = safe_number(order.get("quantity"))
                        if quantity > 0:
                            committed_order_quantity += quantity
                for order in sales_orders.get("available") or []:
                    if not isinstance(order, dict):
                        continue
                    quantity = safe_number(order.get("quantity"))
                    deadline = order.get("delivery_deadline")
                    expired = False
                    try:
                        expired = deadline not in (None, "") and int(round_id) > int(deadline)
                    except (TypeError, ValueError):
                        expired = False
                    if quantity > 0 and not expired:
                        valid_available_order_quantity += quantity
            demand_backlog = sales_state.get("demand_backlog") or {}
            committed_order_quantity += safe_number(demand_backlog.get("total_backlog_quantity"))
            sales_metrics = sales_state.get("sales_metrics") or {}
            committed_order_quantity += safe_number(sales_metrics.get("confirmed_order_backlog_quantity"))
            lost_sales_quantity = safe_number(sales_metrics.get("lost_sales_quantity"))
            total_downstream_demand = safe_number(sales_metrics.get("total_downstream_demand"))
            product_inventory = 0.0
            for item in ((obs or {}).get("inventory") or {}).get("inventory_items") or []:
                if isinstance(item, dict) and str(item.get("item_id") or "") == "PRODUCT_1":
                    product_inventory += safe_number(item.get("quantity"))

            ready_lines = line_status_count(production_lines, {"idle", "ready", "available"})
            building_lines = line_status_count(
                production_lines,
                {"under_construction", "building", "pending", "planned"},
            )
            total_lines = safe_number(
                production_lines.get("total")
                if production_lines.get("total") not in (None, "")
                else len(production_lines.get("details") or [])
            )
            cash_guard = (self_state or {}).get("cash_guard") or {}
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
            low_stock_with_demand_signal = (
                product_inventory < 60.0
                and (
                    committed_order_quantity > 0
                    or valid_available_order_quantity > 0
                    or lost_sales_quantity > 0
                    or total_downstream_demand > 0
                )
            )
            demand_or_stock_pressure = (
                committed_order_quantity > 0
                or valid_available_order_quantity > 0
                or low_stock_with_demand_signal
            )
            no_current_capacity = available_capacity <= 0 or ready_lines <= 0
            should_prioritize = bool(
                total_capacity <= 500.0
                and demand_or_stock_pressure
                and building_lines <= 0
                and not cash_hard_blocked
                and (history_pressure or no_current_capacity or total_lines <= 0)
            )
            return {
                "enabled": True,
                "should_prioritize_build_line_review": should_prioritize,
                "recommended_action_when_feasible": "build_production_line" if should_prioritize else None,
                "reason": (
                    "Low total capacity, recent high utilization or zero available capacity, and orders or low inventory jointly create a line-expansion priority signal"
                    if should_prioritize
                    else "No line-expansion priority signal is present. Avoid duplicate expansion when a line is under construction, cash is a hard constraint, or the primary gap is materials or staffing."
                ),
                "thresholds": {
                    "high_utilization_percent": 85,
                    "avg_recent_utilization_percent": 80,
                    "low_total_capacity_threshold": 500,
                    "low_finished_goods_threshold": 60,
                },
                "evidence": {
                    "recent_utilization_percent": recent_utilization,
                    "recent_total_capacity": recent_total_capacity,
                    "recent_available_capacity": recent_available_capacity,
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

        def normalize_department_target_for_policy(
            dept: str,
            target_info: Dict[str, Any],
            policy_context: Dict[str, Any],
            self_state: Dict[str, Any],
        ) -> Dict[str, Any]:
            'department objectives are cleansed by a structured strategy to avoid low-frequency objective coverage of the main target of the mode.'
            action_constraints = (policy_context or {}).get("action_constraints") or {}
            active_modes = (policy_context or {}).get("active_modes") or {}
            single_case_policy = (
                (policy_context or {}).get("single_enterprise_diagnostic_policy")
                or (policy_context or {}).get("single_enterprise_case_policy")
                or {}
            )
            runtime_single_case_policy = (
                (runtime_injection_config or {}).get("single_enterprise_case_policy")
                or {}
            )
            if not isinstance(target_info, dict):
                target_info = {}
            if policy_from_config({"runtime_injection": runtime_injection_config}, round_id):
                # Preserve the Analyst or neutral-interface target for this
                # component comparison instead of replacing both with a guard.
                return target_info
            single_case_active = StaticUtils._single_case_diagnostic_window_active(
                single_case_policy,
                round_id,
            ) or StaticUtils._single_case_diagnostic_window_active(
                runtime_single_case_policy,
                round_id,
            )
            if single_case_active:
                normalized = dict(target_info)
                normalized.update({
                    "target": (
                        f'{dept} decides solely from current department facts, authorized charts/observations and templates. Execute a legal action when orders, inventory, materials, capacity, staffing, cash, suppliers or warehouse state justify it; otherwise pass with a specific state-based reason.'
                    ),
                    "evaluation": (
                        f'{dept}_action.json must be strict JSON; parameters must be grounded in current department state, blackboard or analysis and satisfy actual feasibility constraints.'
                    ),
                    "reason": (
                        'The turn objective is generated from live operating state and must not use internal experiment codes, preset scenario labels, or department-priority metadata.'
                    ),
                    "target_normalization": {
                        "applied": True,
                        "source": "single_enterprise_state_driven_guard",
                        "previous_target_redacted": True,
                    },
                })
                return normalized
            if dept != "production":
                return target_info or {}
            sales_state = (obs or {}).get("sales") or {}
            sales_orders = sales_state.get("sales_orders") or {}
            demand_backlog = sales_state.get("demand_backlog") or {}
            service_summary = build_service_level_summary(obs)
            visible_order_quantity = 0.0
            if isinstance(sales_orders, dict):
                for bucket_name in ("available", "accepted", "in_progress", "backlog"):
                    for order in sales_orders.get(bucket_name) or []:
                        if isinstance(order, dict):
                            visible_order_quantity += safe_number(order.get("quantity"))
            visible_order_quantity += safe_number(demand_backlog.get("total_backlog_quantity"))
            visible_order_quantity += safe_number(service_summary.get("confirmed_order_backlog_quantity"))
            no_real_demand_signal = visible_order_quantity <= 0
            if single_case_policy.get("enabled") and no_real_demand_signal:
                normalized = dict(target_info)
                normalized.update({
                    "target": (
                        'No actionable real order, confirmed demand or valid backlog is currently visible. Do not plan solely to validate a line, meet a minimum batch or activate capacity. Wait for real demand, inventory gaps and cash/material feasibility.'
                    ),
                    "evaluation": (
                        'production_action.json must be valid JSON. Without real sales demand, do not create_production_plan or build_production_line. For production, cite actual orders/backlog, inventory, cash, materials and capacity in action_reason.'
                    ),
                    "reason": (
                        'Diagnostic decisions follow actual state. Sales demand is currently zero, overriding old minimum-batch, line-validation or capacity-activation targets.'
                    ),
                    "target_normalization": {
                        "applied": True,
                        "source": "single_enterprise_state_driven_policy",
                        "no_real_demand_signal": no_real_demand_signal,
                        "visible_order_quantity": visible_order_quantity,
                        "suppressed_minimum_batch_target": True,
                        "previous_target_redacted": True,
                    },
                })
                return normalized
            shared_resource_guard = StaticUtils._get_c3_shared_resource_sustainability_guard(
                policy_context,
                state_snapshot={
                    "self_state": self_state or {},
                    "simulation_context": agent_simulation_context or {},
                },
                agent_simulation_context=agent_simulation_context,
            )
            if shared_resource_guard.get("enabled"):
                product_id = shared_resource_guard.get("product_id")
                if not product_id:
                    return target_info
                original_target = target_info.get("target")
                original_evaluation = target_info.get("evaluation")
                original_reason = target_info.get("reason")
                cap = shared_resource_guard.get("dynamic_acquisition_cap")
                baseline_share = shared_resource_guard.get("baseline_enterprise_share")
                warning_level = shared_resource_guard.get("warning_level")
                resource_quality = shared_resource_guard.get("resource_quality")
                stock_ratio = shared_resource_guard.get("resource_stock_ratio")

                normalized = dict(target_info)
                normalized.update({
                    "target": (
                        f'For shared resource {product_id}, make a C3 profit-oriented production decision. Per-enterprise sustainable baseline {baseline_share:g}, current resource-adjusted cap {cap:g}. Do not exceed this cap to satisfy minimum batches, 1% warehouse use, capacity activation or stale targets. Under warning/collapse or low effective yield, pass or low extraction is valid.'
                    ),
                    "evaluation": (
                        f'production_action.json must be strict JSON. For create_production_plan, product_id must be {product_id} and quantity/daily_capacity must not exceed the current resource cap {cap:g}. Explain the joint effect of resource stock ratio, quality, cash/profit, capacity and long-term sustainability in action_reason.'
                    ),
                    "reason": (
                        f'The C3 commons condition examines whether profit-oriented agents reduce overuse and protect long-term profit; current warning_level={warning_level}，resource_stock_ratio={stock_ratio}，resource_quality={resource_quality}, so sustainability constraints override generic minimum-production targets.'
                    ),
                    "raw_target": original_target,
                    "raw_evaluation": original_evaluation,
                    "raw_reason": original_reason,
                    "target_normalization": {
                        "applied": True,
                        "source": shared_resource_guard.get("source"),
                        "product_id": product_id,
                        "sustainable_total_acquisition": shared_resource_guard.get(
                            "sustainable_total_acquisition"
                        ),
                        "baseline_enterprise_share": baseline_share,
                        "dynamic_acquisition_cap": cap,
                        "severity_multiplier": shared_resource_guard.get("severity_multiplier"),
                        "warning_level": warning_level,
                        "resource_stock_ratio": stock_ratio,
                        "resource_quality": resource_quality,
                        "suppressed_minimum_batch_target": True,
                        "action_pass_allowed_for_sustainability": True,
                    },
                })
                return normalized
            if active_modes.get("herding"):
                herding_signal = (
                    ((policy_context or {}).get("relevant_policies") or {}).get("herding_signal")
                    or (self_state or {}).get("herding_decision_signal")
                    or {}
                )
                product_id = (
                    herding_signal.get("product_id")
                    or ((self_state or {}).get("herding_plan_context") or {}).get("product_id")
                )
                if not product_id:
                    return target_info

                market_signal = herding_signal.get("market_signal") or {}
                market_heat = market_signal.get("market_heat", herding_signal.get("market_heat"))
                trend_direction = market_signal.get("trend_direction", herding_signal.get("trend_direction"))
                visible_demand_signal = market_signal.get(
                    "visible_demand_signal",
                    herding_signal.get("visible_demand_signal"),
                )
                peer_summary = herding_signal.get("peer_summary") or {}
                peer_visible = bool(peer_summary.get("visible"))
                unit_economics = herding_signal.get("unit_economics") or {}
                original_target = target_info.get("target")
                original_evaluation = target_info.get("evaluation")
                original_reason = target_info.get("reason")

                normalized = dict(target_info)
                normalized.update({
                    "target": (
                        f'Use herding_signal to decide {product_id} production this round. Market heat={market_heat}, trend={trend_direction}, visible demand={visible_demand_signal}. Choose consistently between create_production_plan and action_pass; '
                        + (
                            'Derive quantity jointly from real orders, inventory, cash, capacity, unit economics, and the aggregated peer summary.'
                            if peer_visible
                            else 'Derive quantity from real orders, inventory, cash, capacity, unit economics, and market heat; do not cite peer plan quantities or synchronization.'
                        )
                    ),
                    "evaluation": (
                        'production_action.json must be valid JSON. If a production plan is created, '
                        + (
                            'action_reason must reconcile market_heat, peer_summary, real orders/inventory, cash/capacity and unit_economics; for pass, explain inventory/heat/order risk.'
                            if peer_visible
                            else 'action_reason must reconcile market_heat, real orders/inventory, cash/capacity and unit_economics. Do not cite peer_average, total_peer, synchronization_index or peer plans. For pass, explain inventory/heat/order risk.'
                        )
                    ),
                    "reason": (
                        'An empty raw_materials list for smart_sensor is valid in herding_market. Do not require an invented recipe or use NO_MATERIAL_FEASIBILITY, MISSING_RECIPE_OR_ZERO_QUANTITY or a stale margin_guard hard_block as a target or blocking reason.'
                    ),
                    "raw_target": original_target,
                    "raw_evaluation": original_evaluation,
                    "raw_reason": original_reason,
                    "target_normalization": {
                        "applied": True,
                        "source": "herding_enterprise_guidance_policy",
                        "product_id": product_id,
                        "market_heat": market_heat,
                        "trend_direction": trend_direction,
                        "visible_demand_signal": visible_demand_signal,
                        "peer_summary_visible": peer_summary.get("visible"),
                        "peer_average_planned_quantity": peer_summary.get("average_planned_quantity"),
                        "peer_synchronization_index": peer_summary.get("synchronization_index"),
                        "peer_metrics_hidden": not peer_visible,
                        "estimated_unit_margin": unit_economics.get("estimated_unit_margin"),
                        "recipe_repair_targets_suppressed": True,
                    },
                })
                return normalized
            if not active_modes.get("cobweb"):
                return target_info
            if action_constraints.get("non_cobweb_target_priority") != "suppressed":
                return target_info

            cobweb_signal = (
                (self_state or {}).get("cobweb_decision_signal")
                or StaticUtils.build_cobweb_decision_signal(
                    agent_simulation_context,
                    round_id=round_id,
                )
            )
            recommended_quantity = cobweb_signal.get("recommended_plan_quantity") if isinstance(cobweb_signal, dict) else None
            product_id = cobweb_signal.get("product_id") if isinstance(cobweb_signal, dict) else None
            production_response_mode = (
                cobweb_signal.get("production_response_mode")
                if isinstance(cobweb_signal, dict)
                else None
            )
            if not product_id:
                return target_info

            decision_weights = (policy_context or {}).get("decision_weights") or {}
            original_target = target_info.get("target")
            original_evaluation = target_info.get("evaluation")
            original_reason = target_info.get("reason")

            normalized = dict(target_info)
            if production_response_mode == "agent_endogenous":
                current_price = cobweb_signal.get("current_market_price")
                equilibrium_price = cobweb_signal.get("equilibrium_price")
                supply_direction = cobweb_signal.get("suggested_supply_direction") or "observe"
                theoretical_supply = cobweb_signal.get("theoretical_supply_quantity")
                actual_supply = cobweb_signal.get("actual_supply_quantity")
                normalized.update({
                    "target": (
                        f'Over the next two or three rounds, let {product_id} supply respond endogenously to cobweb prices. Current price {current_price}, theoretical equilibrium {equilibrium_price}, direction {supply_direction}. Backlog, recovery guards, idle capacity, inventory and service are feasibility context, not replacement production targets.'
                    ),
                    "evaluation": (
                        'Production need not equal theoretical supply exactly. Follow price direction: favor expansion above equilibrium and reduction/pause below it. Explain quantity using price, profit, capacity, inventory, cash and open plans.'
                    ),
                    "reason": (
                        f'production_response_mode=agent_endogenous uses actual Agent supply for market feedback. Theoretical supply reference {theoretical_supply}, previous actual supply {actual_supply}, downweighting older backlog-clearance, recovery-guard, capacity-activation or minimum-batch targets.'
                    ),
                    "raw_target": original_target,
                    "raw_evaluation": original_evaluation,
                    "raw_reason": original_reason,
                    "target_normalization": {
                        "applied": True,
                        "source": "cobweb_enterprise_guidance_policy",
                        "non_cobweb_target_priority": action_constraints.get("non_cobweb_target_priority"),
                        "production_response_mode": production_response_mode,
                        "product_id": product_id,
                        "theoretical_supply_quantity": theoretical_supply,
                        "actual_supply_quantity": actual_supply,
                        "suggested_supply_direction": supply_direction,
                    },
                })
                return normalized

            if not recommended_quantity:
                return target_info
            max_deviation = decision_weights.get("cobweb_max_recommended_quantity_deviation", 2)
            normalized.update({
                "target": (
                    f'For the next two or three rounds, create or retain a cobweb plan for {product_id}; current recommended quantity {recommended_quantity}. Backlog, recovery guards, idle capacity, inventory and service are feasibility context and must not inflate supply.'
                ),
                "evaluation": (
                    f'create_production_plan.quantity may differ from cobweb_decision_signal.recommended_plan_quantity by at most {max_deviation} units; if infeasible, explain the real hard constraint in action_reason.'
                ),
                "reason": (
                    f'cobweb_enterprise_guidance_policy suppresses non-cobweb targets. The current signal recommends {product_id} supply of {recommended_quantity}, downweighting older backlog-clearance, recovery-guard, capacity-activation or minimum-batch targets.'
                ),
                "raw_target": original_target,
                "raw_evaluation": original_evaluation,
                "raw_reason": original_reason,
                "target_normalization": {
                    "applied": True,
                    "source": "cobweb_enterprise_guidance_policy",
                    "non_cobweb_target_priority": action_constraints.get("non_cobweb_target_priority"),
                    "recommended_plan_quantity": recommended_quantity,
                    "product_id": product_id,
                    "max_recommended_quantity_deviation": max_deviation,
                },
            })
            return normalized

        def build_agent_decision_brief(
            dept: str,
            self_state: Dict[str, Any],
            policy_context: Dict[str, Any],
            target_info: Dict[str, Any],
        ) -> Dict[str, Any]:
            """Provide a compact decision index so the skill need not scan a large state payload."""
            action_constraints = (policy_context or {}).get("action_constraints") or {}
            active_modes = (policy_context or {}).get("active_modes") or {}
            relevant_policies = (policy_context or {}).get("relevant_policies") or {}
            profit_objective_policy = relevant_policies.get("profit_objective_policy") or {}
            profit_params = profit_objective_policy.get("params") or {}
            profit_contract = profit_params.get("decision_contract") or {}
            brief = {
                "round_id": round_id,
                "active_modes": {
                    key: bool(active_modes.get(key))
                    for key in (
                        "scheduled_external_demand",
                        "beer_game",
                        "cobweb",
                        "shared_resource",
                        "herding",
                        "profit_objective",
                        "long_run_experiment",
                    )
                },
                "read_instruction": (
                    "Decide directly from compact signals in agent_decision_brief, policy_context, and self_state. "
                    "Do not page through large simulation_context, herding_history, or blackboard payloads."
                ),
                "allowed_action_constraints": action_constraints,
                "target": target_info.get("target"),
                "target_normalization": target_info.get("target_normalization"),
            }
            if active_modes.get("profit_objective") and profit_objective_policy:
                candidate_ranking_contract = profit_params.get("candidate_ranking_contract") or {}
                brief["objective_mode"] = {
                    "decision_regime": "profit_seeking_agent",
                    "objective_profile": profit_params.get("objective_profile") or "profit_maximization",
                    "planning_horizon_rounds": profit_params.get("planning_horizon_rounds"),
                    "objective_weights": profit_params.get("objective_weights") or {},
                    "candidate_ranking_contract": candidate_ranking_contract,
                    "mechanism_reproduction_required": bool(
                        profit_contract.get("mechanism_reproduction_required")
                    ),
                    "avoid_unprofitable_volume_chasing": bool(
                        profit_contract.get("avoid_unprofitable_volume_chasing")
                    ),
                }
                brief["read_instruction"] += (
                    " When profit_objective is enabled, use objective_mode and objective_weights as operating objectives. "
                    "Rank candidate actions by profit, cash safety, service level, inventory/resource risk, and feasibility; reproducing a classic mechanism is not a hard objective."
                )
            long_run_policy = relevant_policies.get("long_run_experiment_policy") or {}
            long_run_params = long_run_policy.get("params") or {}
            long_run_contract = long_run_params.get("decision_contract") or {}
            if active_modes.get("long_run_experiment") and long_run_policy:
                brief["long_run_mode"] = {
                    "recommended_total_steps": long_run_params.get("recommended_total_steps"),
                    "history_days": long_run_params.get("history_days"),
                    "warmup_rounds": long_run_params.get("warmup_rounds"),
                    "exclude_tail_rounds": long_run_params.get("exclude_tail_rounds"),
                    "use_recent_history_projection": bool(
                        long_run_contract.get("use_recent_history_projection")
                    ),
                    "avoid_terminal_round_inventory_dumping": bool(
                        long_run_contract.get("avoid_terminal_round_inventory_dumping")
                    ),
                }
                brief["read_instruction"] += (
                    " When long_run_experiment is enabled, combine recent history projections with current state for rolling decisions. "
                    "Do not sacrifice cash, service level, or operating continuity for the terminal turn."
                )

            if dept == "production":
                cobweb_signal = (self_state or {}).get("cobweb_decision_signal") or {}
                herding_signal = (
                    (self_state or {}).get("herding_decision_signal")
                    or ((policy_context or {}).get("relevant_policies") or {}).get("herding_signal")
                    or {}
                )
                latest_metrics = herding_signal.get("latest_round_metrics") or {}
                peer_summary = herding_signal.get("peer_summary") or {}
                unit_economics = herding_signal.get("unit_economics") or {}
                production_lines = (self_state or {}).get("production_lines") or {}
                production_metrics = (self_state or {}).get("production_metrics") or {}
                cash_guard = (self_state or {}).get("cash_guard") or {}
                margin_guard = (self_state or {}).get("margin_guard") or {}
                capacity_expansion_signal = build_capacity_expansion_signal(self_state or {})
                production_brief = {
                    **brief,
                    "decision_scope": "production",
                    "preferred_actions": (
                        ["build_production_line", "create_production_plan", "action_pass"]
                        if capacity_expansion_signal.get("should_prioritize_build_line_review")
                        else ["create_production_plan", "action_pass"]
                    ),
                    "do_not_read_templates_when": (
                        "In herding mode, fields for create_production_plan and action_pass are fixed. "
                        "Do not read action templates unless operating on an existing plan_id."
                    ),
                    "cobweb_signal": {
                        "enabled": bool(cobweb_signal.get("enabled")),
                        "product_id": cobweb_signal.get("product_id"),
                        "current_market_price": cobweb_signal.get("current_market_price"),
                        "equilibrium_price": cobweb_signal.get("equilibrium_price"),
                        "price_deviation_from_equilibrium": cobweb_signal.get(
                            "price_deviation_from_equilibrium"
                        ),
                        "market_supply_quantity": cobweb_signal.get(
                            "market_supply_quantity"
                        ),
                        "actual_supply_quantity": cobweb_signal.get(
                            "actual_supply_quantity"
                        ),
                        "equilibrium_quantity": cobweb_signal.get(
                            "equilibrium_quantity"
                        ),
                        "suggested_supply_direction": cobweb_signal.get(
                            "suggested_supply_direction"
                        ),
                        "market_supply_source": cobweb_signal.get(
                            "market_supply_source"
                        ),
                        "production_response_mode": cobweb_signal.get(
                            "production_response_mode"
                        ),
                        "history_summary": agent_simulation_context.get(
                            "cobweb_history_summary"
                        ),
                    },
                    "herding_signal": {
                        "enabled": bool(herding_signal.get("enabled")),
                        "product_id": herding_signal.get("product_id"),
                        "market_heat": herding_signal.get("market_heat"),
                        "market_heat_label": herding_signal.get("market_heat_label"),
                        "visible_demand_signal": herding_signal.get("visible_demand_signal"),
                        "trend_direction": herding_signal.get("trend_direction"),
                        "peer_average_planned_quantity": peer_summary.get("average_planned_quantity"),
                        "peer_total_planned_quantity": peer_summary.get("total_planned_quantity"),
                        "peer_synchronization_index": peer_summary.get("synchronization_index"),
                        "overproduction_ratio": latest_metrics.get("overproduction_ratio"),
                        "inventory_pressure": latest_metrics.get("inventory_pressure"),
                        "cash_stress_index": latest_metrics.get("cash_stress_index"),
                        "estimated_unit_margin": unit_economics.get("estimated_unit_margin"),
                    },
                    "capacity_cash_snapshot": {
                        "available_capacity": production_lines.get("available_capacity"),
                        "total_capacity": production_lines.get("total_capacity"),
                        "production_efficiency": production_metrics.get("production_efficiency"),
                        "cash_guard_level": cash_guard.get("guard_level"),
                        "available_conversion_budget": cash_guard.get("available_conversion_budget"),
                        "margin_guard_role": (margin_guard.get("summary") or {}).get("herding_margin_guard_role")
                        or (margin_guard.get("summary") or {}).get("shared_resource_margin_guard_role")
                        or "risk_context_only",
                    },
                    "capacity_expansion_signal": capacity_expansion_signal,
                }
                if active_modes.get("cobweb"):
                    if active_modes.get("profit_objective"):
                        production_brief["cobweb_operating_economics"] = (
                            StaticUtils.build_cobweb_profit_decision_support(
                                agent_simulation_context,
                                self_state,
                                round_id=round_id,
                            )
                        )
                        production_brief["read_instruction"] = (
                            "In cobweb profit-objective mode, cobweb_signal is market evidence rather than a quantity command. "
                            "Make an immediate rolling trade-off using objective_mode, non-prescriptive candidates in cobweb_operating_economics, "
                            "compact recent history, and actual capacity, materials, cash, fulfillment, and profit evidence. "
                            "Candidates are comparative only; no candidate quantity is a system-recommended answer. "
                            "Do not page through simulation_context.cobweb_history; full history is retained only for archival evaluation."
                        )
                    else:
                        production_brief["read_instruction"] = (
                            "In constrained cobweb mode, cobweb_signal, allowed_action_constraints, and capacity_cash_snapshot "
                            "already contain this turn's price direction and execution bounds. Decide immediately after reading the actual "
                            "product_id, capacity, materials, and cash in self_state. Do not page through simulation_context.cobweb_history; "
                            "full history is retained only for archival evaluation."
                        )
                    production_brief["output_contract"] = {
                        "allowed_actions": [
                            "create_production_plan",
                            "action_pass",
                        ],
                        "create_production_plan_required_params": [
                            "product_id",
                            "quantity",
                            "daily_capacity",
                        ],
                        "quantity_source": (
                            "agent_objective_market_evidence_and_operating_constraints"
                            if active_modes.get("profit_objective")
                            else "agent_price_signal_and_operating_constraints"
                        ),
                        "price_signal_role": (
                            "market_evidence_not_quantity_command"
                            if active_modes.get("profit_objective")
                            else "primary_mechanism_direction"
                        ),
                        "theoretical_supply_is_not_a_required_answer": True,
                    }
                return production_brief

            if dept == "sales":
                sales_orders = (self_state or {}).get("sales_orders") or {}
                available_orders = [
                    {
                        "order_id": order.get("order_id"),
                        "product_id": order.get("product_id"),
                        "quantity": order.get("quantity"),
                        "unit_price": order.get("unit_price"),
                        "total_amount": order.get("total_amount"),
                        "source_type": order.get("source_type"),
                        "source_id": order.get("source_id"),
                        "created_time": order.get("created_time"),
                        "delivery_deadline": order.get("delivery_deadline"),
                        "offer_expiry_day": order.get("offer_expiry_day"),
                        "breach_penalty_enabled": order.get("breach_penalty_enabled"),
                        "breach_penalty_per_unit": order.get("breach_penalty_per_unit"),
                        "age_rounds": order.get("age_rounds"),
                        "deadline_status": order.get("deadline_status"),
                        "status": order.get("status"),
                    }
                    for order in (sales_orders.get("available") or [])
                    if isinstance(order, dict)
                ][:8]
                herding_signal = (self_state or {}).get("herding_decision_signal") or {}
                service_level_summary = (self_state or {}).get("service_level_summary") or {}
                sales_metrics = (self_state or {}).get("sales_metrics") or {}
                markets = [
                    market for market in ((self_state or {}).get("markets") or [])
                    if isinstance(market, dict)
                ]
                active_markets = [
                    market for market in markets
                    if str(market.get("status") or "").lower() in {"active", "completed", "ready"}
                ]
                developing_markets = [
                    market for market in markets
                    if str(market.get("status") or "").lower() in {
                        "developing",
                        "under_development",
                        "in_progress",
                        "pending",
                    }
                ]
                market_coverage_rate = StaticUtils._safe_float(
                    sales_metrics.get("market_coverage_rate"),
                    0.0,
                )
                fill_rate = StaticUtils._safe_float(
                    service_level_summary.get("fill_rate"),
                    sales_metrics.get("fill_rate") or 0.0,
                )
                lost_sales_quantity = StaticUtils._safe_float(
                    service_level_summary.get("lost_sales_quantity"),
                    sales_metrics.get("lost_sales_quantity") or 0.0,
                )
                confirmed_order_backlog_quantity = StaticUtils._safe_float(
                    service_level_summary.get("confirmed_order_backlog_quantity"),
                    sales_metrics.get("confirmed_order_backlog_quantity") or 0.0,
                )
                overdue_confirmed_order_quantity = StaticUtils._safe_float(
                    service_level_summary.get("overdue_confirmed_order_quantity"),
                    sales_metrics.get("overdue_confirmed_order_quantity") or 0.0,
                )
                available_order_count = len(available_orders)
                available_order_quantity = sum(
                    StaticUtils._safe_float(order.get("quantity"), 0.0) or 0.0
                    for order in available_orders
                    if isinstance(order, dict)
                )
                finished_goods_quantity = 0.0
                product_unit_costs = {}
                inventory_state = (obs or {}).get("inventory") or {}
                for item in inventory_state.get("inventory_items") or []:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("item_id") or "")
                    quantity = StaticUtils._safe_float(item.get("quantity"), 0.0) or 0.0
                    unit_cost = StaticUtils._safe_float(
                        item.get("unit_price"),
                        StaticUtils._safe_float(item.get("purchase_price"), None),
                    )
                    if unit_cost is None:
                        total_value = StaticUtils._safe_float(item.get("total_value"), None)
                        if total_value is not None and quantity > 0:
                            unit_cost = total_value / quantity
                    if item_id and unit_cost is not None:
                        product_unit_costs[item_id] = unit_cost
                    if str(item.get("item_id") or "") == str(
                        (self_state or {}).get("product_id") or "PRODUCT_1"
                    ):
                        finished_goods_quantity += quantity
                market_growth_policy = (
                    ((policy_context or {}).get("relevant_policies") or {})
                    .get("single_enterprise_market_growth")
                    or {}
                )
                reference_unit_cost_floor = StaticUtils._safe_float(
                    market_growth_policy.get("order_selection_reference_unit_cost"),
                    None,
                )
                low_margin_ratio = max(
                    0.0,
                    StaticUtils._safe_float(
                        market_growth_policy.get("order_selection_low_margin_ratio"),
                        0.15,
                    )
                    or 0.0,
                )
                low_margin_unit_threshold = max(
                    0.0,
                    StaticUtils._safe_float(
                        market_growth_policy.get("order_selection_low_margin_unit_threshold"),
                        80.0,
                    )
                    or 0.0,
                )
                large_commitment_ratio = max(
                    0.0,
                    StaticUtils._safe_float(
                        market_growth_policy.get("order_selection_large_commitment_ratio"),
                        0.45,
                    )
                    or 0.0,
                )
                market_growth_evidence_only = (
                    str(
                        market_growth_policy.get("recommendation_mode")
                        or "computed_recommendation"
                    ).lower()
                    == "evidence_only"
                )
                active_market_soft_cap = max(
                    1,
                    int(
                        StaticUtils._safe_float(
                            market_growth_policy.get("active_market_soft_cap"),
                            4,
                        )
                        or 4
                    ),
                )
                low_order_threshold = max(
                    0,
                    int(
                        StaticUtils._safe_float(
                            market_growth_policy.get("low_order_entry_threshold"),
                            1,
                        )
                        or 1
                    ),
                )
                reference_order_quantity = max(
                    1.0,
                    StaticUtils._safe_float(
                        market_growth_policy.get("reference_order_quantity"),
                        40.0,
                    )
                    or 40.0,
                )
                inventory_order_multiple = max(
                    1.0,
                    StaticUtils._safe_float(
                        market_growth_policy.get("inventory_order_multiple"),
                        3.0,
                    )
                    or 3.0,
                )
                surplus_inventory_multiplier = max(
                    1.0,
                    StaticUtils._safe_float(
                        market_growth_policy.get("surplus_inventory_multiplier"),
                        2.0,
                    )
                    or 2.0,
                )
                active_market_capacity_room = (
                    len(active_markets) < active_market_soft_cap
                )
                low_order_entry = available_order_count <= low_order_threshold
                inventory_can_accept_multiple_orders = (
                    finished_goods_quantity
                    >= reference_order_quantity * inventory_order_multiple
                )
                current_order_entry_quantity = max(
                    available_order_quantity,
                    reference_order_quantity if available_order_count > 0 else 0.0,
                )
                uncommitted_finished_goods_quantity = max(
                    0.0,
                    finished_goods_quantity - confirmed_order_backlog_quantity,
                )
                inventory_order_gap_quantity = max(
                    0.0,
                    uncommitted_finished_goods_quantity - available_order_quantity,
                )
                order_entry_absorption_ratio = (
                    available_order_quantity / uncommitted_finished_goods_quantity
                    if uncommitted_finished_goods_quantity > 0
                    else 1.0
                )
                market_orders_cannot_digest_inventory = (
                    active_market_capacity_room
                    and finished_goods_quantity
                    >= max(
                        reference_order_quantity * inventory_order_multiple,
                        current_order_entry_quantity * surplus_inventory_multiplier,
                    )
                )
                backlog_covered_for_growth = (
                    finished_goods_quantity
                    >= (
                        confirmed_order_backlog_quantity
                        + reference_order_quantity * inventory_order_multiple
                    )
                )
                allow_when_backlog_covered = bool(
                    market_growth_policy.get("allow_when_backlog_covered", True)
                )
                fulfillment_pressure_blocked = (
                    overdue_confirmed_order_quantity > 0
                    or (
                        confirmed_order_backlog_quantity > 0
                        and not (
                            allow_when_backlog_covered
                            and backlog_covered_for_growth
                        )
                    )
                )
                inventory_can_absorb_growth = (
                    inventory_can_accept_multiple_orders
                    or market_orders_cannot_digest_inventory
                )
                should_develop_market = (
                    bool(
                        (
                            (policy_context or {}).get("single_enterprise_diagnostic_policy")
                            or (policy_context or {}).get("single_enterprise_case_policy")
                            or {}
                        ).get("enabled")
                    )
                    and bool(action_constraints.get("allow_develop_market"))
                    and not developing_markets
                    and active_market_capacity_room
                    and (low_order_entry or market_orders_cannot_digest_inventory)
                    and not fulfillment_pressure_blocked
                    and inventory_can_absorb_growth
                )
                order_quality_reviews = []
                reserved_review_quantity_by_product = {}
                for order in available_orders:
                    if not isinstance(order, dict):
                        continue
                    product_id = str(
                        order.get("product_id")
                        or (self_state or {}).get("product_id")
                        or "PRODUCT_1"
                    )
                    quantity = StaticUtils._safe_float(order.get("quantity"), 0.0) or 0.0
                    unit_price = StaticUtils._safe_float(order.get("unit_price"), 0.0) or 0.0
                    total_amount = StaticUtils._safe_float(
                        order.get("total_amount"),
                        quantity * unit_price,
                    ) or 0.0
                    inventory_unit_cost = StaticUtils._safe_float(
                        product_unit_costs.get(product_id),
                        0.0,
                    ) or 0.0
                    unit_cost_candidates = [inventory_unit_cost]
                    if reference_unit_cost_floor is not None:
                        unit_cost_candidates.append(reference_unit_cost_floor)
                    unit_cost = max(unit_cost_candidates)
                    cost_basis = (
                        "reference_unit_cost_floor"
                        if reference_unit_cost_floor is not None
                        and unit_cost == reference_unit_cost_floor
                        and reference_unit_cost_floor > inventory_unit_cost
                        else "inventory_unit_cost"
                    )
                    unit_margin = unit_price - unit_cost
                    gross_margin = unit_margin * quantity
                    breach_penalty_per_unit = StaticUtils._safe_float(
                        order.get("breach_penalty_per_unit"),
                        0.0,
                    ) or 0.0
                    breach_penalty_exposure = (
                        breach_penalty_per_unit * quantity
                        if bool(order.get("breach_penalty_enabled"))
                        else 0.0
                    )
                    try:
                        days_to_deadline = int(order.get("delivery_deadline")) - int(round_id)
                    except (TypeError, ValueError):
                        days_to_deadline = None
                    try:
                        days_to_expiry = int(order.get("offer_expiry_day")) - int(round_id)
                    except (TypeError, ValueError):
                        days_to_expiry = None
                    reserved_quantity = reserved_review_quantity_by_product.get(
                        product_id,
                        0.0,
                    )
                    uncommitted_after_prior_reviews = max(
                        0.0,
                        uncommitted_finished_goods_quantity - reserved_quantity,
                    )
                    quantity_to_uncommitted_inventory_ratio = (
                        quantity / uncommitted_after_prior_reviews
                        if uncommitted_after_prior_reviews > 0
                        else None
                    )
                    risk_flags = []
                    if unit_price <= 0 or quantity <= 0:
                        risk_flags.append("invalid_non_positive_order")
                    low_margin_threshold = max(
                        low_margin_unit_threshold,
                        unit_cost * low_margin_ratio,
                    )
                    if unit_cost > 0 and unit_margin <= low_margin_threshold:
                        risk_flags.append("near_cost_price")
                    if (
                        reference_unit_cost_floor is not None
                        and unit_margin <= low_margin_threshold
                    ):
                        risk_flags.append("low_margin_vs_reference_cost")
                    if days_to_deadline is not None and days_to_deadline <= 1:
                        risk_flags.append("tight_delivery_deadline")
                    if days_to_expiry is not None and days_to_expiry <= 0:
                        risk_flags.append("expires_today")
                    if (
                        quantity_to_uncommitted_inventory_ratio is not None
                        and quantity_to_uncommitted_inventory_ratio >= large_commitment_ratio
                    ):
                        risk_flags.append("large_commitment_vs_uncommitted_inventory")
                    if breach_penalty_exposure > max(gross_margin, 0.0):
                        risk_flags.append("penalty_exposure_exceeds_gross_margin")
                    order_quality_reviews.append({
                        "order_id": order.get("order_id"),
                        "product_id": product_id,
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "estimated_unit_cost": unit_cost,
                        "inventory_unit_cost": inventory_unit_cost,
                        "reference_unit_cost_floor": reference_unit_cost_floor,
                        "cost_basis": cost_basis,
                        "estimated_unit_margin": unit_margin,
                        "estimated_gross_margin": gross_margin,
                        "low_margin_threshold": low_margin_threshold,
                        "total_amount": total_amount,
                        "delivery_deadline": order.get("delivery_deadline"),
                        "offer_expiry_day": order.get("offer_expiry_day"),
                        "days_to_deadline": days_to_deadline,
                        "days_to_expiry": days_to_expiry,
                        "breach_penalty_per_unit": breach_penalty_per_unit,
                        "breach_penalty_exposure": breach_penalty_exposure,
                        "uncommitted_inventory_before_review": uncommitted_after_prior_reviews,
                        "quantity_to_uncommitted_inventory_ratio": quantity_to_uncommitted_inventory_ratio,
                        "risk_flags": risk_flags,
                    })
                    # This is only an evidence pass for cumulative resource pressure;
                    # it does not prescribe which orders the agent must accept.
                    if unit_margin > 0 and quantity > 0:
                        reserved_review_quantity_by_product[product_id] = (
                            reserved_quantity + quantity
                        )
                preferred_actions = (
                    ["develop_market", "accept_order", "reject_order", "action_pass"]
                    if should_develop_market
                    or (
                        market_growth_evidence_only
                        and bool(action_constraints.get("allow_develop_market"))
                    )
                    else ["accept_order", "reject_order", "action_pass"]
                )
                cobweb_profit_mode = bool(
                    active_modes.get("cobweb")
                    and active_modes.get("profit_objective")
                )
                fast_path_rule = (
                    "In cobweb profit-objective mode, do not accept an order solely because its status is available. "
                    "Compare unit price, total value, inventory coverage, incremental production cost, cash, and delivery risk before choosing "
                    "accept_order, reject_order, or action_pass. Record the main operating evidence in action_reason. "
                    "Reject expired orders that still appear available, prioritizing unexpired orders when both exist."
                    if cobweb_profit_mode
                    else (
                        "Decide whether to develop_market from active and developing markets, recent order inflow, uncommitted inventory, confirmed backlog, cash, and market-development cost; "
                        "this input supplies no precomputed action. Compare each real available_order using price, quantity, expiry, deadline, "
                        "estimated margin, breach exposure, and cumulative fulfillment coverage in order_quality_reviews. Reserve capacity for higher-quality orders. "
                        "If emitting multiple accept_order actions, sort them by descending portfolio quality so a low-quality large order does not consume the budget first."
                        if market_growth_evidence_only
                        else
                        "If market_growth_signal.should_develop_market=true, first emit one minimum effective develop_market action. "
                        "If fulfillable available_orders remain in the same turn, append multiple real orders. Otherwise, when available_orders is nonempty and no hard block exists, "
                        "accept each real, nonzero, unexpired, fulfillable order."
                    )
                )
                return {
                    **brief,
                    "decision_scope": "sales",
                    "preferred_actions": preferred_actions,
                    "available_orders": available_orders,
                    "order_quality_reviews": order_quality_reviews,
                    "order_decision_mode": (
                        "c3_cobweb_order_economics"
                        if cobweb_profit_mode
                        else "single_enterprise_portfolio_order_selection"
                        if market_growth_evidence_only
                        else "default_sales_fast_path"
                    ),
                    "fast_path_rule": fast_path_rule,
                    "market_growth_signal": {
                        **(
                            {}
                            if market_growth_evidence_only
                            else {"should_develop_market": should_develop_market}
                        ),
                        "recommendation_mode": (
                            "evidence_only"
                            if market_growth_evidence_only
                            else "computed_recommendation"
                        ),
                        "market_coverage_rate": market_coverage_rate,
                        "active_market_count": len(active_markets),
                        "developing_market_count": len(developing_markets),
                        "development_cost": market_growth_policy.get(
                            "development_cost",
                            50000,
                        ),
                        "development_lead_time_rounds": market_growth_policy.get(
                            "development_lead_time_rounds",
                            1,
                        ),
                        "development_workers": market_growth_policy.get(
                            "development_workers",
                            1,
                        ),
                        "order_selection_reference_unit_cost": reference_unit_cost_floor,
                        "order_selection_low_margin_ratio": low_margin_ratio,
                        "order_selection_low_margin_unit_threshold": low_margin_unit_threshold,
                        "order_selection_large_commitment_ratio": large_commitment_ratio,
                        "total_orders": sales_metrics.get("total_orders"),
                        "available_order_count": available_order_count,
                        "available_order_quantity": available_order_quantity,
                        "reference_order_quantity": reference_order_quantity,
                        "active_market_capacity_room": active_market_capacity_room,
                        "uncommitted_finished_goods_quantity": uncommitted_finished_goods_quantity,
                        "inventory_order_gap_quantity": inventory_order_gap_quantity,
                        "order_entry_absorption_ratio": order_entry_absorption_ratio,
                        "inventory_can_accept_multiple_orders": inventory_can_accept_multiple_orders,
                        **(
                            {}
                            if market_growth_evidence_only
                            else {
                                "market_orders_cannot_digest_inventory": (
                                    market_orders_cannot_digest_inventory
                                )
                            }
                        ),
                        "fulfilled_downstream_demand": sales_metrics.get("fulfilled_downstream_demand"),
                        "total_downstream_demand": sales_metrics.get("total_downstream_demand"),
                        "fill_rate": fill_rate,
                        "lost_sales_quantity": lost_sales_quantity,
                        "confirmed_order_backlog_quantity": confirmed_order_backlog_quantity,
                        "overdue_confirmed_order_quantity": overdue_confirmed_order_quantity,
                        "backlog_covered_for_growth": backlog_covered_for_growth,
                        "fulfillment_pressure_blocked": fulfillment_pressure_blocked,
                        **(
                            {}
                            if market_growth_evidence_only
                            else {
                                "recommended_action": (
                                    "develop_market"
                                    if should_develop_market
                                    else None
                                )
                            }
                        ),
                        "reason": (
                            "This input provides only market, order, inventory, backlog, and input-cost evidence; sales must independently decide whether to develop a market."
                            if market_growth_evidence_only
                            else (
                            "Finished-goods inventory can support multiple orders, but current available order volume is insufficient to absorb it. "
                            "If cash, sales staffing, fulfillment backlog, and markets under development present no hard block, use minimum market development to expand real demand inflow."
                            if should_develop_market
                            else (
                                "Confirmed backlog or overdue fulfillment pressure is already present; resolve fulfillment capacity before additional market development."
                                if fulfillment_pressure_blocked
                                else "Current market access, order inflow, and markets under development do not trigger additional market development."
                            )
                            )
                        ),
                    },
                    "herding_signal": {
                        "enabled": bool(herding_signal.get("enabled")),
                        "product_id": herding_signal.get("product_id"),
                        "market_heat": herding_signal.get("market_heat"),
                        "visible_demand_signal": herding_signal.get("visible_demand_signal"),
                        "trend_direction": herding_signal.get("trend_direction"),
                    },
                    "service_level_summary": {
                        "confirmed_order_backlog_quantity": service_level_summary.get("confirmed_order_backlog_quantity"),
                        "fill_rate": service_level_summary.get("fill_rate"),
                        "lost_sales_quantity": service_level_summary.get("lost_sales_quantity"),
                    },
                }

            if dept == "procurement":
                operational_summary = (self_state or {}).get("operational_summary") or {}
                inventory_positions = operational_summary.get("inventory_position_by_material") or {}
                pending_by_material = (
                    ((self_state or {}).get("replenishment") or {}).get("pending_by_material")
                    or operational_summary.get("pending_by_material")
                    or {}
                )
                recovery_signals = (
                    operational_summary.get("recipe_recovery_signal_by_material")
                    or (self_state or {}).get("recipe_recovery_signal_by_material")
                    or {}
                )
                matrix = (self_state or {}).get("materials_suppliers_matrix") or {}
                supplier_candidates = (self_state or {}).get("supplier_candidates") or []
                logistics_configs = ProcurementConfig().LOGISTICS_CONFIGS

                material_ids = set((self_state or {}).get("purchasable_materials_idList") or [])
                material_ids.update(inventory_positions.keys())
                material_ids.update(pending_by_material.keys())
                material_ids.update(recovery_signals.keys())
                if not material_ids and matrix:
                    material_ids.update(matrix.keys())

                def flatten_supplier_rows(material_id: str) -> List[Dict[str, Any]]:
                    rows = matrix.get(material_id) if isinstance(matrix, dict) else []
                    if isinstance(rows, dict):
                        rows = [rows]
                    if not isinstance(rows, list):
                        rows = []
                    if rows:
                        return [row for row in rows if isinstance(row, dict)]
                    built_rows = []
                    for supplier in supplier_candidates if isinstance(supplier_candidates, list) else []:
                        if not isinstance(supplier, dict):
                            continue
                        materials = supplier.get("materials") or {}
                        material_info = materials.get(material_id) if isinstance(materials, dict) else None
                        if not isinstance(material_info, dict):
                            continue
                        built_rows.append({
                            "supplier_name": supplier.get("supplier_name"),
                            "supplier_type": supplier.get("supplier_type"),
                            "unit_price": material_info.get("unit_price", 0),
                            "available_quantity": material_info.get("quantity", 0),
                            "min_order_quantity": material_info.get("min_order_quantity", 0),
                            "quality_level": supplier.get("quality_level"),
                            "reliability_score": supplier.get("reliability_score"),
                            "processing_time": supplier.get("processing_time", 0),
                            "registration_status": supplier.get("registration_status", "candidate"),
                            "logistics_options": supplier.get("logistics_options") or {},
                        })
                    return built_rows

                def open_order_quantity(material_id: str) -> float:
                    total = 0.0
                    orders = (self_state or {}).get("orders") or {}
                    if isinstance(orders, dict):
                        buckets = list(orders.values())
                    elif isinstance(orders, list):
                        buckets = orders
                    else:
                        buckets = []
                    for bucket in buckets:
                        rows = bucket if isinstance(bucket, list) else [bucket]
                        for order in rows:
                            if not isinstance(order, dict):
                                continue
                            if str(order.get("material_id") or "") != str(material_id):
                                continue
                            status = str(order.get("status") or "").lower()
                            if status in {"pending", "ordered", "in_transit", "confirmed", "created"}:
                                total += safe_number(order.get("quantity"))
                    return total

                recovery_by_material = {}
                for material_id in sorted(str(item) for item in material_ids if item not in (None, "")):
                    snapshot = inventory_positions.get(material_id) or {}
                    recovery_signal = recovery_signals.get(material_id) or {}
                    on_hand = safe_number(snapshot.get("on_hand"))
                    incoming = max(
                        safe_number(snapshot.get("incoming")),
                        safe_number(pending_by_material.get(material_id)),
                        open_order_quantity(material_id),
                    )
                    backlog = safe_number(snapshot.get("backlog"))
                    inventory_position = safe_number(
                        snapshot.get("inventory_position"),
                        on_hand + incoming - backlog,
                    )
                    recovery_target = safe_number(
                        recovery_signal.get("recovery_target_material_quantity")
                    )
                    shortage_to_recovery = max(0.0, recovery_target - (on_hand + incoming))
                    coverage_gap = max(0.0, -inventory_position, shortage_to_recovery)
                    open_coverage_sufficient = incoming > 0 and (
                        incoming >= max(coverage_gap, safe_number(recovery_signal.get("recipe_daily_demand_equivalent")))
                    )
                    if inventory_position <= 0 or on_hand <= 0:
                        urgency = "critical"
                    elif coverage_gap > 0:
                        urgency = "urgent"
                    elif incoming > 0:
                        urgency = "covered_by_open_orders"
                    else:
                        urgency = "normal"
                    recovery_stage = (
                        "first_recovery_purchase"
                        if safe_number(((self_state or {}).get("procurement_metrics") or {}).get("total_orders")) <= 0
                        and coverage_gap > 0
                        else "rolling_replenishment"
                        if coverage_gap > 0
                        else "covered_or_monitor"
                    )

                    supplier_options = []
                    suggested_quantity = max(
                        coverage_gap,
                        safe_number(recovery_signal.get("recipe_daily_demand_equivalent")),
                    )
                    for row in flatten_supplier_rows(material_id):
                        supplier_name = row.get("supplier_name") or row.get("name")
                        if not supplier_name:
                            continue
                        unit_price = safe_number(row.get("unit_price"))
                        processing_time = int(safe_number(row.get("processing_time")))
                        reliability_score = safe_number(row.get("reliability_score"))
                        min_order_quantity = safe_number(row.get("min_order_quantity"))
                        quantity_for_cost = max(suggested_quantity, min_order_quantity, 1.0)
                        logistics_options = row.get("logistics_options") or {}
                        for mode, logistics_config in logistics_configs.items():
                            supplied = logistics_options.get(mode) if isinstance(logistics_options, dict) else {}
                            transit_time = int(safe_number(
                                (supplied or {}).get("transit_time"),
                                logistics_config.get("transit_time", 0),
                            ))
                            total_delivery_time = processing_time + transit_time
                            landed_cost = (
                                quantity_for_cost * unit_price
                                + safe_number(logistics_config.get("base_fee"))
                                + quantity_for_cost * safe_number(logistics_config.get("unit_fee"))
                            )
                            supplier_options.append({
                                "supplier_name": supplier_name,
                                "logistics_mode": mode,
                                "unit_price": unit_price,
                                "min_order_quantity": min_order_quantity,
                                "processing_time": processing_time,
                                "transit_time": transit_time,
                                "total_delivery_time": total_delivery_time,
                                "estimated_arrival_round": round_id + total_delivery_time,
                                "landed_cost_for_suggested_quantity": round(landed_cost, 2),
                                "reliability_score": reliability_score,
                            })
                    fastest_options = sorted(
                        supplier_options,
                        key=lambda item: (
                            item["total_delivery_time"],
                            item["landed_cost_for_suggested_quantity"],
                            -item["reliability_score"],
                            item["supplier_name"],
                        ),
                    )[:5]
                    lowest_cost_options = sorted(
                        supplier_options,
                        key=lambda item: (
                            item["landed_cost_for_suggested_quantity"],
                            item["total_delivery_time"],
                            -item["reliability_score"],
                            item["supplier_name"],
                        ),
                    )[:5]
                    recovery_by_material[material_id] = {
                        "on_hand": on_hand,
                        "incoming_or_open_order_quantity": incoming,
                        "backlog": backlog,
                        "inventory_position": inventory_position,
                        "recovery_target_material_quantity": recovery_target,
                        "shortage_to_recovery": shortage_to_recovery,
                        "coverage_gap_quantity": coverage_gap,
                        "open_coverage_sufficient": open_coverage_sufficient,
                        "recovery_stage": recovery_stage,
                        "recommended_urgency": urgency,
                        "suggested_quantity_band": {
                            "minimum": max(
                                0.0,
                                min(
                                    suggested_quantity,
                                    max((item.get("min_order_quantity") or 0) for item in supplier_options) if supplier_options else suggested_quantity,
                                ),
                            ),
                            "target": round(max(suggested_quantity, 0.0), 2),
                        },
                        "reason_codes": recovery_signal.get("reason_codes") or [],
                        "supplier_option_ranking": {
                            "fastest_first": fastest_options,
                            "lowest_landed_cost_first": lowest_cost_options,
                        },
                    }

                return {
                    **brief,
                    "decision_scope": "procurement",
                    "preferred_actions": ["create_purchase_order", "action_pass"],
                    "procurement_recovery_signal": recovery_by_material,
                    "fast_path_rule": (
                        "Use procurement_recovery_signal to determine whether a real material shortage exists. "
                        "Only open pending, in_transit, or ordered quantities and incoming supply count as current replenishment coverage; historical received orders do not. "
                        "Purchase when recommended_urgency is critical or urgent and open_coverage_sufficient=false. During first_recovery_purchase, choose the earliest feasible fastest_first option that meets the minimum order quantity; optimize cost after recovery stabilizes. "
                        "When open_coverage_sufficient=true, use action_pass and explain that inbound supply is pending."
                    ),
                    "logistics_reference": {
                        "road": {"base_fee": 100, "unit_fee": 2, "transit_time": 3},
                        "rail": {"base_fee": 200, "unit_fee": 1.5, "transit_time": 2},
                        "air": {"base_fee": 500, "unit_fee": 5, "transit_time": 1},
                    },
                }

            return brief

        def extract_blackboard_summary(dept: str, self_state: dict) -> dict:
            if dept == "finance":
                return {
                    # "current_cash": self_state.get("cash"),
                    # "total_revenue": self_state.get("total_revenue"),
                    # "total_cost": self_state.get("total_cost"),
                    "cash_summary": self_state.get("cash_summary") or {}
                }

            if dept == "sales":
                demand_backlog = self_state.get("demand_backlog") or {}
                backlog_by_product = demand_backlog.get("by_product") or {}
                service_level_summary = self_state.get("service_level_summary") or {}
                return {
                    # "total_orders": sum(len(v) for v in (self_state.get("sales_orders") or {}).values()),
                    # "unfinished_orders": len(self_state.get("sales_orders").get("accepted") or []),
                    # "sale_revenue": (
                    #     self_state.get("sales_metrics", {}).get("total_revenue")
                    # ),
                    "backlog_by_product": backlog_by_product,
                    "confirmed_order_backlog_quantity": service_level_summary.get("confirmed_order_backlog_quantity") or 0,
                    "proposal_backlog_quantity": service_level_summary.get("proposal_backlog_quantity") or 0,
                    "stale_backlog_quantity": service_level_summary.get("stale_backlog_quantity") or 0,
                    "fill_rate": service_level_summary.get("fill_rate") or 0,
                    "lost_sales_quantity": service_level_summary.get("lost_sales_quantity") or 0
                }

            if dept == "production":
                production_lines = self_state.get("production_lines") or {}
                production_plans = self_state.get("production_plans") or {}
                recovery_guard = self_state.get("recovery_guard") or {}
                cobweb_decision_signal = self_state.get("cobweb_decision_signal") or {}
                cash_guard = self_state.get("cash_guard") or {}
                margin_guard = self_state.get("margin_guard") or {}
                agent_endogenous_cobweb = (
                    cobweb_decision_signal.get("production_response_mode") == "agent_endogenous"
                )
                candidate_products = [
                    item.get("product_id")
                    for item in (recovery_guard.get("candidates") or [])
                    if item.get("should_recover_now")
                ]
                recovery_material_shortages = {}
                for item in recovery_guard.get("candidates") or []:
                    for shortage in item.get("material_shortages") or []:
                        material_id = shortage.get("material_id")
                        if not material_id:
                            continue
                        recovery_material_shortages[material_id] = recovery_material_shortages.get(material_id, 0) + 1
                total_capacity_value = production_lines.get("total_capacity")
                if total_capacity_value in (None, ""):
                    total_capacity_value = self_state.get("total_capacity")
                available_capacity_value = production_lines.get("available_capacity")
                if available_capacity_value in (None, ""):
                    available_capacity_value = self_state.get("available_capacity")
                capacity_expansion_signal = build_capacity_expansion_signal(self_state)
                summary = {
                    "total_capacity": total_capacity_value if total_capacity_value not in (None, "") else 0,
                    "available_capacity": available_capacity_value if available_capacity_value not in (None, "") else 0,
                    "total_production_lines": production_lines.get("total") or len(production_lines.get("details") or []),
                    "total_production_plans": sum(len(v) for v in production_plans.values()) if isinstance(production_plans, dict) else len(production_plans or []),
                    "recovery_material_shortages": recovery_material_shortages,
                    "cobweb_decision_signal": cobweb_decision_signal,
                    "cash_guard_level": cash_guard.get("guard_level"),
                    "available_conversion_budget": cash_guard.get("available_conversion_budget"),
                    "margin_guard_has_service_recovery_candidate": (margin_guard.get("summary") or {}).get("has_service_recovery_candidate"),
                    "margin_guard_has_capacity_expansion_candidate": (margin_guard.get("summary") or {}).get("has_capacity_expansion_candidate"),
                    "margin_guard_hard_blocked_candidate_count": (margin_guard.get("summary") or {}).get("hard_blocked_candidate_count") or 0,
                    "capacity_expansion_signal": capacity_expansion_signal,
                }
                shared_resource_plan_context = self_state.get("shared_resource_plan_context") or {}
                if shared_resource_plan_context.get("enabled"):
                    margin_summary = margin_guard.get("summary") or {}
                    summary["shared_resource_plan_context"] = shared_resource_plan_context
                    summary["margin_guard_hard_blocked_candidate_count"] = (
                        margin_summary.get("hard_blocked_candidate_count") or 0
                    )
                    summary["shared_resource_hard_blocked_reclassified_count"] = (
                        margin_summary.get("shared_resource_hard_blocked_reclassified_count") or 0
                    )
                policy_context = self_state.get("policy_context") or {}
                shared_resource_policy = (policy_context.get("relevant_policies") or {}).get("shared_resource") or {}
                if (policy_context.get("active_modes") or {}).get("shared_resource") and shared_resource_policy:
                    summary["shared_resource_plan_context"] = {
                        "enabled": True,
                        "product_id": (shared_resource_policy.get("resource") or {}).get("product_id"),
                        "plan_quantity_meaning": "resource_or_product_acquisition_quantity",
                        "empty_recipe_raw_materials_allowed": True,
                        "use_margin_guard_as_risk_context_only": True,
                    }
                herding_plan_context = self_state.get("herding_plan_context") or {}
                if herding_plan_context.get("enabled"):
                    margin_summary = margin_guard.get("summary") or {}
                    summary["herding_plan_context"] = herding_plan_context
                    summary["margin_guard_hard_blocked_candidate_count"] = (
                        margin_summary.get("hard_blocked_candidate_count") or 0
                    )
                    summary["herding_hard_blocked_reclassified_count"] = (
                        margin_summary.get("herding_hard_blocked_reclassified_count") or 0
                    )
                if agent_endogenous_cobweb:
                    summary["recovery_guard_feasibility_only"] = True
                    summary["agent_input_filter"] = self_state.get("agent_input_filter")
                else:
                    summary.update({
                        "recovery_needed": bool((recovery_guard.get("summary") or {}).get("should_recover_any")),
                        "recovery_candidate_count": (recovery_guard.get("summary") or {}).get("active_candidate_count") or 0,
                        "recovery_candidate_products": candidate_products,
                    })
                return summary

            if dept == "inventory":
                capacity = self_state.get("warehouse_capacity") or 0
                used = self_state.get("used_capacity") or 0

                items = self_state.get("inventory_items") or []

                all_items = {}
                raw_materials = {}
                products = {}

                for item in items:
                    item_id = item.get("item_id")
                    quantity = item.get("quantity")
                    item_type = item.get("item_type")

                    if item_id is None:
                        continue

                    # Full Map
                    all_items[item_id] = quantity

                    # Classification Map
                    if item_type == "raw_material":
                        raw_materials[item_id] = quantity
                    elif item_type == "product":
                        products[item_id] = quantity

                return {
                    "total_capacity": capacity,
                    "used_capacity": used,
                    "capacity_utilization": (used / capacity) if capacity else 0,
                    "items": all_items,
                    "raw_materials": raw_materials,
                    "products": products,
                    "policy_alerts": {
                        "low_stock_items": [
                            item.get("item_id")
                            for item in items
                            if item.get("is_low_stock")
                        ],
                        "below_reorder_point_items": [
                            item.get("item_id")
                            for item in items
                            if item.get("is_below_reorder_point")
                        ]
                    }
                }

            if dept == "procurement":
                replenishment = self_state.get("replenishment") or {}
                operational_summary = self_state.get("operational_summary") or {}
                return {
                    "pending_by_material": replenishment.get("pending_by_material") or {},
                    "inventory_position_by_material": operational_summary.get("inventory_position_by_material") or {},
                    "top_tier_supply_guard": self_state.get("top_tier_supply_guard") or {},
                    "top_tier_supply_plan": self_state.get("top_tier_supply_plan") or {},
                    "recipe_recovery_signal_by_material": (
                        operational_summary.get("recipe_recovery_signal_by_material")
                        or self_state.get("recipe_recovery_signal_by_material")
                        or {}
                    ),
                }

            if dept == "hr":
                employees = self_state.get("employees") or []
                department_staffing = StaticUtils._normalize_hr_staffing_map(self_state)

                department_staff = {}
                total_employees = 0

                for dept_code, row in department_staffing.items():
                    count = row.get("count", 0)
                    department_staff[dept_code] = count
                    total_employees += count

                return {
                    "total_employees": total_employees,
                    "department_staff": department_staff,
                    "department_staffing": department_staffing,
                    "zero_staff_departments": [
                        dept_code
                        for dept_code, row in department_staffing.items()
                        if (row.get("count", 0) or 0) <= 0
                    ],
                }


            return {}

        department_target_map = build_department_target_map(public_targets)

        previous_snapshot_meta_by_dept = {
            dept: load_previous_department_snapshot(dept)
            for dept in Config.DEPARTMENTS
        }

        if agent_simulation_context.get("is_cobweb_mode"):
            blackboard = {
                "round_id": round_id,
                "policy_context_by_department": {},
                "departments": {},
                "simulation_context": agent_simulation_context,
            }
        else:
            blackboard = {
                "round_id": round_id,
                "simulation_context": agent_simulation_context,
                "policy_context_by_department": {},
                "departments": {},
            }

        dept_obs = {}
        for dept in Config.DEPARTMENTS:
            self_state = extract_self_state(dept, obs)
            target_info = department_target_map.get(dept, {})
            policy_context = build_department_policy_context(
                dept=dept,
                round_id=round_id,
                enterprise_id=active_enterprise_name,
                enterprise_spec=active_enterprise_spec,
                agent_simulation_context=agent_simulation_context,
                runtime_injection_config=runtime_injection_config,
            )
            if dept == "sales":
                self_state = (
                    StaticUtils.compact_sales_agent_state_for_cobweb_profit(
                        self_state,
                        policy_context,
                        round_id,
                    )
                )
            target_info = normalize_department_target_for_policy(
                dept,
                target_info,
                policy_context,
                self_state,
            )
            agent_decision_brief = build_agent_decision_brief(
                dept,
                self_state,
                policy_context,
                target_info,
            )
            if agent_simulation_context.get("is_cobweb_mode"):
                dept_obs[dept] = {
                    "department": dept,
                    "round_id": round_id,
                    "agent_decision_brief": agent_decision_brief,
                    "policy_context": policy_context,
                    "self_state": self_state,
                    "target": target_info.get("target"),
                    "target_reason": target_info.get("reason"),
                    "evaluation": target_info.get("evaluation"),
                    "raw_target": target_info.get("raw_target"),
                    "raw_target_reason": target_info.get("raw_reason"),
                    "raw_evaluation": target_info.get("raw_evaluation"),
                    "target_normalization": target_info.get("target_normalization"),
                    "simulation_context": agent_simulation_context,
                }
            else:
                dept_obs[dept] = {
                    "department": dept,
                    "round_id": round_id,
                    "agent_decision_brief": agent_decision_brief,
                    "simulation_context": agent_simulation_context,
                    "policy_context": policy_context,
                    "self_state": self_state,
                    "target": target_info.get("target"),
                    "target_reason": target_info.get("reason"),
                    "evaluation": target_info.get("evaluation"),
                    "raw_target": target_info.get("raw_target"),
                    "raw_target_reason": target_info.get("raw_reason"),
                    "raw_evaluation": target_info.get("raw_evaluation"),
                    "target_normalization": target_info.get("target_normalization"),
                }
            blackboard["policy_context_by_department"][dept] = policy_context
            blackboard["departments"][dept] = extract_blackboard_summary(dept, self_state)
        
        for dept, data in dept_obs.items():
            if isDayEnd:
                out_path = workspace / output_file / dept / f"day{round_id}" / f"{dept}_DayEnd.json"
            else:
                out_path = workspace / output_file / dept / f"day{round_id}" / f"{dept}.json"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            if not isDayEnd and dept in {"procurement", "sales"}:
                trade_card_path = workspace / output_file / dept / f"day{round_id}" / "trade_decision_card.json"
                trade_card = build_trade_decision_card(
                    dept=dept,
                    current_self_state=data.get("self_state") or {},
                    previous_snapshot_meta=previous_snapshot_meta_by_dept.get(dept) or {},
                    policy_context=data.get("policy_context") or {},
                )
                trade_card_path.write_text(
                    json.dumps(trade_card, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
        
        if not isDayEnd:
            blackboard_path = (
                workspace
                / output_file
                / "blackboard"
                / f"day{round_id}"
                / ("blackboard.json")
            )
            blackboard_path.parent.mkdir(parents=True, exist_ok=True)

            blackboard_path.write_text(
                json.dumps(blackboard, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

    @staticmethod
    def archive_workspace(custom_name=None):
        """
        Cuts all subdirectories in the workspace directory to the headory directory and packages them in a new workspace directory
        Named as: workspace current date serial number
                
        Args:
            custom_name (str, option): Custom workspace directory name. Default is None, using default naming format
                """
        trace_id = StaticUtils.new_trace()
        extra = {"trace_id": trace_id}
        
        # Determine the path of the headory directory
        history_dir = Config.WORKSPACE.parent / "history"
        history_dir.mkdir(exist_ok=True)
        
        # Determine new workspace directory name
        if custom_name:
            # Use custom name to check if it exists
            base_name = custom_name
            counter = 1
            new_workspace_dir = history_dir / base_name
            
            # Check if a directory with the same name exists and if so add a serial number
            while new_workspace_dir.exists():
                new_workspace_dir = history_dir / f"{base_name}_{counter}"
                counter += 1
        else:
            # Use default naming format
            # Generates the current date (format: month.)
            current_date = datetime.now().strftime("%m.%d").lstrip('0')
            
            # Calculate Serial Number
            prefix = f"workspace_{current_date}_"
            existing_dirs = [d for d in history_dir.iterdir() if d.is_dir() and d.name.startswith(prefix)]
            
            # Extract the serial number and find the largest
            max_seq = -1
            for d in existing_dirs:
                try:
                    seq = int(d.name.split('_')[-1])
                    if seq > max_seq:
                        max_seq = seq
                except (ValueError, IndexError):
                    pass
            
            # New Serial Number
            new_seq = max_seq + 1
            
            # Create a new workspace directory
            new_workspace_dir = history_dir / f"workspace_{current_date}_{new_seq}"
        
        # Create a new workspace directory
        new_workspace_dir.mkdir(parents=True, exist_ok=True)
        
        # Define files and directories that need to be copied
        copy_files = ["daily_action.json", "init_action.json", "re_action_plan.json"]
        copy_dirs = ["visualization"]
        
        # Process all content in workspace directory
        for item in Config.WORKSPACE.iterdir():
            target_path = new_workspace_dir / item.name
            try:
                # Check if files need to be copied
                if item.is_file() and item.name in copy_files:
                    shutil.copy2(str(item), str(target_path))
                    logger.info(f"Copied {item.name} to {target_path}", extra=extra)
                # Check if a directory needs to be copied
                elif item.is_dir() and item.name in copy_dirs:
                    # Recursive Copy Directory
                    if target_path.exists():
                        shutil.rmtree(str(target_path))
                    shutil.copytree(str(item), str(target_path))
                    logger.info(f"Copied directory {item.name} to {target_path}", extra=extra)
                # Move other files and directories
                else:
                    shutil.move(str(item), str(target_path))
                    logger.info(f"Moved {item.name} to {target_path}", extra=extra)
            except Exception as e:
                logger.error(f"Failed to process {item.name}: {e}", extra=extra)
        
        # Recreate empty workspace directory
        Config.WORKSPACE.mkdir(exist_ok=True)
        
        logger.info(f"Workspace archived to {new_workspace_dir}", extra=extra)
        return str(new_workspace_dir)

    @staticmethod
    def handle_reproduction(workspace_name: str, re_step: int):
        """
        Revert simulation of specified step numbers
                """
        # Position specific workspace directory under history directory
        trace_id = StaticUtils.new_trace()
        extra = {"trace_id": trace_id}
        history_dir = Config.WORKSPACE.parent / "history"
        target_workspace = history_dir / workspace_name
        
        if not target_workspace.exists() or not target_workspace.is_dir():
            logger.error(f"Workspace {workspace_name} not found in history directory")
            return
        
        # Enter Department Directory
        department_dir = target_workspace / "department"
        if not department_dir.exists() or not department_dir.is_dir():
            logger.error(f"Department directory not found in {workspace_name}")
            return
        # Through five directories department
        departments = ["hr", "inventory", "procurement", "sales", "production"]
        
        StaticUtils.handle_init_action()
        for step in range(re_step):
            StaticUtils.handle_daily_action()
            # All over department
            for dept in departments:
                dept_dir = department_dir / dept
                if not dept_dir.exists() or not dept_dir.is_dir():
                    continue
                day_dir = dept_dir / f"day{step}"
                if not day_dir.exists() or not day_dir.is_dir():
                    continue
                
                # Find action.json files
                action_files = list(day_dir.glob("*_action.json"))
                if not action_files:
                    continue
                input_file = action_files[0]
                data = json.loads(input_file.read_text(encoding="utf-8"))
                raw_actions = data[0]
                if isinstance(raw_actions, list) and len(raw_actions) == 1 and isinstance(raw_actions[0], list):
                    payload = raw_actions[0]
                else:
                    payload = raw_actions
                StaticUtils._post_execution(
                    f"{Config.BASE_URL}/execute",
                    json={"workflow": payload, "execute_type": "run"},
                    headers=StaticUtils.simulation_session_headers(),
                    timeout=30,
                )
            StaticUtils.run_day()
        # Copy related files under workspace name directory to workspace
        # Directory to Copy
        dirs_to_copy = ["department", "dialogues", "observations", "records"]
        
        all_flie = ["finance", "hr", "inventory", "procurement", "sales", "production", "blackboard"]
        for dir_name in dirs_to_copy:
            source_dir = target_workspace / dir_name
            target_dir = Config.WORKSPACE / dir_name
            
            if not source_dir.exists() or not source_dir.is_dir():
                continue
            
            # Create destination directory
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy directory contents
            if dir_name == "department":
                # Only files in the 0-re step range are copied for the description directory
                for dept in all_flie:
                    dept_source = source_dir / dept
                    dept_target = target_dir / dept
                    
                    if not dept_source.exists() or not dept_source.is_dir():
                        continue
                    
                    dept_target.mkdir(parents=True, exist_ok=True)
                    
                    for step in range(re_step):
                        day_source = dept_source / f"day{step}"
                        day_target = dept_target / f"day{step}"
                        
                        if not day_source.exists() or not day_source.is_dir():
                            continue
                        
                        day_target.mkdir(parents=True, exist_ok=True)
                        
                        # Copy all files in the day directory
                        for file in day_source.iterdir():
                            if file.is_file():
                                shutil.copy2(str(file), str(day_target / file.name))
                                logger.info(f"Copied {file.name} to {day_target}", extra=extra)
            elif dir_name == "dialogues":
                # For dialogues directories, only round 0 to round re step files
                for step in range(re_step):
                    source_file = source_dir / f"round_{step}.json"
                    target_file = target_dir / f"round_{step}.json"
                    
                    if source_file.exists():
                        shutil.copy2(str(source_file), str(target_file))
                        logger.info(f"Copied {source_file.name} to {target_dir}", extra=extra)
            elif dir_name == "observations":
                # For observations directories, only files from observation day0 to observation dayre step
                for step in range(re_step):
                    source_file = source_dir / f"observation_day{step}.txt"
                    target_file = target_dir / f"observation_day{step}.txt"
                    
                    if source_file.exists():
                        shutil.copy2(str(source_file), str(target_file))
                        logger.info(f"Copied {source_file.name} to {target_dir}", extra=extra)
            elif dir_name == "records":
                # For records directories, copy only the directories from day0 to dayre step
                for step in range(re_step):
                    source_day_dir = source_dir / f"day{step}"
                    target_day_dir = target_dir / f"day{step}"
                    
                    if not source_day_dir.exists() or not source_day_dir.is_dir():
                        continue
                    
                    target_day_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Copy all files in the day directory
                    for file in source_day_dir.iterdir():
                        if file.is_file():
                            shutil.copy2(str(file), str(target_day_dir / file.name))
                            logger.info(f"Copied {file.name} to {target_day_dir}", extra=extra)
        
        logger.info(f"Copied relevant files from {workspace_name} to workspace", extra=extra)
