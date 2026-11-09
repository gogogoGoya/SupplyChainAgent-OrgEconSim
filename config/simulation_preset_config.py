'\nCentral configuration for the multi-enterprise supply-chain simulation.\n\nThis module manages scenario-, runtime-, and organization-level defaults, including:\n- Base enterprise definitions\n- External demand series and round parameters\n- Agent, department, and production-line settings\n- Initialization and recurring daily actions\n- Daily system-injection rules\n- Automatic HR and inventory policy thresholds\n\nConfiguration is organized as scenario packages:\n- `baseline_current` preserves the historical baseline for reproducibility.\n- `baseline_rebalanced` preserves a rebalanced comparison baseline.\n- `beer_game` remains as a legacy scenario identifier; its underlying demand mode is now named `scheduled_external_demand`.\n\nScenario selection:\n- The default is `DEFAULT_ACTIVE_SCENARIO_ID`.\n- `SIMULATION_SCENARIO_ID` may override it at runtime.\n'

import json
import os
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List

from config.environment_config import EnvironmentConfig


SIMULATION_SCENARIO_ENV_VAR = "SIMULATION_SCENARIO_ID"
DEFAULT_ACTIVE_SCENARIO_ID = "architecture_linear_chain_short"



MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL = "scheduled_external_demand"
LEGACY_MARKET_DEMAND_MODE_BEER_GAME = "beer_game"
MARKET_DEMAND_MODE_ALIASES = {
    LEGACY_MARKET_DEMAND_MODE_BEER_GAME: MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL,
}

MARKET_DEMAND_MODE_BEER_GAME = MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL


def normalize_market_demand_mode(mode: str = None) -> str:
    """Normalize legacy market mode names to the current canonical value."""
    if mode is None:
        return mode
    return MARKET_DEMAND_MODE_ALIASES.get(str(mode), str(mode))

MARKET_DEMAND_MODE_COBWEB = "cobweb"

MARKET_DEMAND_MODE_SHARED_RESOURCE = "shared_resource_market"

MARKET_DEMAND_MODE_HERDING = "herding_market"

DEFAULT_NETWORK_CONFIG_TEMPLATE = {"structure": "custom"}

DEFAULT_AGENT_MODEL_NAME_LIST = (
    list(EnvironmentConfig.DEFAULT_AGENT_MODEL_LIST)
    or ([EnvironmentConfig.DEFAULT_AGENT_MODEL] if EnvironmentConfig.DEFAULT_AGENT_MODEL else [])
)

DEFAULT_COBWEB_CONFIG_TEMPLATE = {
    "enabled": False,
    "product_id": "beer",
    "initial_price": 280.0,
    "demand_intercept": 160.0,
    "demand_slope": 0.5,
    "supply_intercept": 20.0,
    "supply_slope": 0.25,
    "production_lag_rounds": 1,
    "price_floor": 1.0,
    "price_ceiling": 1000.0,
    "quantity_floor": 1.0,
    "quantity_ceiling": 1000.0,
    "customer_delivery_lead_time": 1,
    "stability_label": "disabled",
    "production_response_mode": "agent_endogenous",
    "endogenous_supply_source": "production_plan_created",
    "endogenous_supply_lag_rounds": 1,
    "endogenous_supply_fallback": "theoretical_lagged_supply",
    "decision_profile": "mechanism_primary",
    "agent_history_window_rounds": 8,
}

DEFAULT_SHARED_RESOURCE_CONFIG_TEMPLATE = {
    "enabled": False,
    "resource_id": "shared_resource_pool",
    "resource_label": 'Shared Resource Pool',
    "product_id": "resource_product",
    "target_enterprise_ids": [],
    "initial_stock_quantity": 12000.0,
    "max_stock_quantity": 12000.0,
    "sustainable_acquisition_per_round": 360.0,
    "regeneration_mode": "fixed",
    "regeneration_quantity": 360.0,
    "resource_quality_floor": 0.35,
    "quality_degradation_enabled": True,
    "quality_function": "stock_ratio_linear",
    "base_unit_price": 180.0,
    "unit_acquisition_cost": 70.0,
    "scarcity_price_enabled": False,
    "acquisition_lag_rounds": 1,
    "initial_acquisition_quantity_per_enterprise": 120.0,
    "collapse_threshold_ratio": 0.20,
    "warning_threshold_ratio": 0.45,
    "customer_delivery_lead_time": 1,
    "apply_acquisition_cost_to_finance": False,
    "acquisition_cost_finance_category": "raw_materials",
    "constrain_production_output_to_effective_acquisition": False,
    "external_order_quantity_mode": "effective_acquisition",
    "apply_breach_penalty_to_finance": False,
    "breach_penalty_per_unit": 0.0,
    "breach_penalty_finance_category": "market_cost",
}

DEFAULT_SHARED_RESOURCE_GOVERNANCE_POLICY_TEMPLATE = {
    "enabled": False,
    "mode": "none",
    "quota_per_enterprise": None,
    "quota_soft_limit": False,
    "over_quota_penalty_per_unit": 0.0,
    "resource_tax_per_unit": 0.0,
    "shared_sustainability_target": 360.0,
    "show_collective_outcome_to_agent": True,
    "show_peer_acquisition_to_agent": True,
}

DEFAULT_HERDING_CONFIG_TEMPLATE = {
    "enabled": False,
    "product_id": "smart_sensor",
    "target_enterprise_ids": [],
    "true_demand_series": [80, 90, 110, 130, 150, 140, 120, 95, 80, 70, 65, 60, 60, 60, 60, 60],
    "market_heat_series": [0.35, 0.45, 0.65, 0.85, 0.95, 0.92, 0.82, 0.70, 0.55, 0.42, 0.35, 0.30, 0.30, 0.30, 0.30, 0.30],
    "base_unit_price": 420.0,
    "unit_cost_reference": 210.0,
    "customer_delivery_lead_time": 1,
    "production_lag_rounds": 1,
    "initial_reference_plan_quantity": 70.0,
    "peer_visibility_enabled": True,
    "peer_visibility_lag_rounds": 1,
    "market_heat_noise_level": 0.0,
    "herding_pressure_weight": 0.65,
    "overproduction_threshold_ratio": 1.15,
    "target_synchronization_threshold": 0.72,
}

DEFAULT_EXTERNAL_MARKET_ORDER_POLICY_TEMPLATE = {
    "enabled": True,
    "mode": "single_target",
    "target_enterprise_ids": [],
    "generation_timing": "daily_start",
}

DEFAULT_SINGLE_ENTERPRISE_CHART_EXPORT_POLICY_TEMPLATE = {
    "enabled": False,
    "archive_per_round": True,
    "inject_to_analyst": True,
    "target_enterprise_ids": [],
}

SINGLE_ENTERPRISE_CASE_IDS = [
    "single_case_01_order_selection",
    "single_case_02_material_shortage",
    "single_case_03_capacity_bottleneck",
    "single_case_04_staff_shortage",
    "single_case_05_cash_pressure",
]

SINGLE_ENTERPRISE_CASE_ALIASES = {
    "single_case_01_market_insufficient": "single_case_01_order_selection",
}

SINGLE_ENTERPRISE_STATIC_COMMAND_ROOT = (
    Path(__file__).resolve().parents[1]
    / 'agent'
    / "static_commands"
    / "single_enterprise"
)

SINGLE_ENTERPRISE_STATIC_CASE_DIRS = {
    "single_case_01_order_selection": "case_01_market_insufficient",
    "single_case_02_material_shortage": "case_02_material_shortage",
    "single_case_03_capacity_bottleneck": "case_03_capacity_bottleneck",
    "single_case_04_staff_shortage": "case_04_staff_shortage",
    "single_case_05_cash_pressure": "case_05_cash_pressure",
}
SINGLE_ENTERPRISE_DEPARTMENT_MODULE_TYPES = {
    "finance": "FinanceManager",
    "hr": "HRManager",
    "sales": "SalesManager",
    "production": "ProductionManager",
    "inventory": "InventoryManager",
    "procurement": "ProcurementManager",
}

# The refreshed case06 seed creates working-capital pressure through ordinary
# market, staffing, warehouse, procurement, and production actions. It must not
# be overwritten by the former artificial initial-capital/cash-shock profile.
SINGLE_ENTERPRISE_CASE_RUNTIME_OVERRIDES: Dict[str, Dict[str, Any]] = {}


def _make_action(action_name: str, action_param: Dict[str, Any], module_type: str, executor_id: str) -> Dict[str, Any]:
    """Generates a unified action structure to be used for initializing and"""
    return {
        "action": {"action_name": action_name, "action_param": deepcopy(action_param)},
        "module_type": module_type,
        "executor_id": executor_id,
    }


def _build_scripted_rule_policy(
    enabled: bool = False,
    mode: str = "scripted_rules",
    apply_to_departments: List[str] = None,
    base_quantity: float = 80,
    single_enterprise_rules: Dict[str, Any] = None,
    policy_overrides: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Construct the rule script control policy; on open, you can replace Agent Skill to generate action files."""
    def _merge_policy(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        merged = deepcopy(base or {})
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _merge_policy(merged[key], value)
            else:
                merged[key] = deepcopy(value)
        return merged

    generic_single_enterprise_rules = {
        "accept_orders_per_round": 99,
        "market_development_interval": 3,
        "market_development_cost": 50000,
        "market_development_cash_buffer": 0,
        "market_development_workers": 1,
        "market_development_when_no_available_orders": True,
        "market_development_active_market_soft_cap": 4,
        "market_development_low_order_entry_threshold": 1,
        "market_development_reference_order_quantity": max(1, base_quantity),
        "market_development_inventory_order_multiple": 3,
        "market_development_surplus_inventory_multiplier": 2.0,
        "market_development_allow_when_backlog_covered": True,
        # In single-enterprise diagnosis mode, this value limits how many active markets can generate orders in one round.
        # Each source market currently generates 1 external order.
        "max_market_order_sources_per_round": 4,
        "cash_guard_threshold": 60000,
        "critical_cash_threshold": 20000,
        "reject_unfulfillable_orders": True,
        "max_procurement_orders_per_round": 1,
        "procurement_budget_cash_share": 0.35,
        "procurement_min_budget": 5000,
        "procurement_reorder_cooldown_rounds": 2,
        "procurement_min_gap_ratio": 0.25,
        "procurement_emergency_gap_ratio": 0.85,
        "material_coverage_target_quantity": 80,
        "material_order_multiplier": 1.0,
        "preferred_logistics_mode": "dynamic",
        "production_round_interval": 1,
        "production_quantity": base_quantity,
        "production_daily_capacity": max(1, base_quantity),
        "low_finished_goods_threshold": 40,
        "target_finished_goods_buffer": 90,
        "capacity_pressure_threshold": 0.85,
        "build_line_type": "small",
        "production_staff_minimum": 2,
        "hr_recruit_people": 2,
        "warehouse_pressure_threshold": 0.88,
        "warehouse_expansion_capacity": 1000,
    }
    generic_single_enterprise_rules.update(deepcopy(single_enterprise_rules or {}))
    policy = {
        "enabled": bool(enabled),
        "mode": mode,
        "analyst": {
            "enabled": bool(enabled),
        },
        "departments": {
            "apply_to_departments": list(apply_to_departments or ["all"]),
        },
        "sales": {
            "accept_first_available_order": True,
        },
        "procurement": {
            "default_order_quantity": 50,
            "logistics_mode": "road",
        },
        "production": {
            "base_quantity": base_quantity,
            "daily_capacity": base_quantity,
            "oscillation_amplitude": 0.25,
            "growth_rate": 0.08,
            "peer_signal_sensitivity": 0.2,
            "single_case_quantity": base_quantity,
            "allow_build_production_line": True,
            "herding_no_peer_enterprise_multipliers": {
                "Sensor_A": 0.35,
                "Sensor_B": 0.75,
                "Sensor_C": 1.25,
                "Sensor_D": 1.75,
            },
        },
        "trade": {
            "fallback_sales_response": "accept_implicit",
            "fallback_procurement_response": "reject_without_candidate",
            "reject_procurement_proposal_when_unaffordable": True,
            "procurement_accept_cash_buffer": 0,
        },
        "single_enterprise_rules": generic_single_enterprise_rules,
    }
    return _merge_policy(policy, policy_overrides or {})


def _build_profit_objective_policy(
    enabled: bool = False,
    objective_profile: str = "profit_maximization",
    objective_weights: Dict[str, float] = None,
    target_enterprise_ids: List[str] = None,
    planning_horizon_rounds: int = 3,
) -> Dict[str, Any]:
    """Build the C3 open-objective policy visible to Agent departments."""
    return {
        "enabled": bool(enabled),
        "objective_profile": objective_profile,
        "target_enterprise_ids": list(target_enterprise_ids or []),
        "planning_horizon_rounds": int(planning_horizon_rounds),
        "objective_weights": deepcopy(objective_weights or {
            "net_profit": 0.40,
            "cash_safety": 0.25,
            "service_level": 0.20,
            "inventory_cost_control": 0.10,
            "risk_buffer": 0.05,
        }),
        "decision_contract": {
            "primary_goal": "maximize sustainable enterprise value under environment constraints",
            "mechanism_reproduction_required": False,
            "may_reduce_classic_effect_strength": True,
            "must_respect_action_constraints": True,
            "avoid_unprofitable_volume_chasing": True,
            "protect_long_run_operating_stability": True,
        },
        "candidate_ranking_contract": {
            "enabled": bool(enabled),
            "scope": "C3_profit_objective_only",
            "rank_trade_candidates_by": [
                "expected_margin_or_cost_saving",
                "cash_safety_after_action",
                "service_level_impact",
                "inventory_or_resource_risk",
                "execution_feasibility",
            ],
            "reject_or_pass_when": [
                "proposal_or_order_is_not_actionable",
                "cash_guard_hard_blocked",
                "stock_or_material_shortage_blocks_fulfillment",
                "price_or_margin_is_unfavorable_without_service_pressure",
                "action_would_chase_volume_without_sustainable_profit",
            ],
            "do_not_change_observation_boundary": True,
            "do_not_enable_blackboard_or_two_phase_by_itself": True,
        },
    }


def _build_long_run_experiment_policy(
    enabled: bool = False,
    recommended_total_steps: int = 40,
    history_days: int = 8,
    warmup_rounds: int = 3,
    exclude_tail_rounds: int = 2,
    target_enterprise_ids: List[str] = None,
) -> Dict[str, Any]:
    """Build C3 long-run guardrails without changing the market mechanism."""
    return {
        "enabled": bool(enabled),
        "target_enterprise_ids": list(target_enterprise_ids or []),
        "recommended_total_steps": int(recommended_total_steps),
        "history_days": int(history_days),
        "warmup_rounds": int(warmup_rounds),
        "exclude_tail_rounds": int(exclude_tail_rounds),
        "decision_contract": {
            "use_recent_history_projection": True,
            "protect_cash_runway_and_service_level": True,
            "avoid_terminal_round_inventory_dumping": True,
            "do_not_treat_last_round_metrics_as_primary_evaluation": True,
            "primary_evaluation_window": "post_warmup_pre_tail_rounds",
        },
        "execution_guard": {
            "cap_terminal_replenishment_recovery": True,
            "normal_replenishment_backlog_recovery_share": 0.60,
            "normal_replenishment_max_forecast_days": 2,
            "tail_replenishment_backlog_recovery_share": 0.25,
            "tail_replenishment_max_forecast_days": 1,
        },
    }


def _enable_c3_long_run_profiles(cloned: Dict[str, Any]) -> None:
    cloned.setdefault("runtime_injection", {})["long_run_experiment_policy"] = (
        _build_long_run_experiment_policy(enabled=True)
    )
    integration_profiles = deepcopy(cloned.get("integration_profiles") or {})
    capabilities = integration_profiles.setdefault("capabilities", {})
    analysis = capabilities.setdefault("analysis", {})
    analysis["profile"] = "historical_diagnosis"
    try:
        existing_history_days = int(analysis.get("history_days") or 0)
    except (TypeError, ValueError):
        existing_history_days = 0
    analysis["history_days"] = max(existing_history_days, 8)
    cloned["integration_profiles"] = integration_profiles


def _build_experiment_design_metadata(
    *,
    decision_regime: str,
    agent_objective_profile: str,
    constraint_level: str,
    experiment_group: str,
    evidence_status: str,
    notes: str = "",
) -> Dict[str, Any]:
    """Paper-facing run-level labels for C1/C2/C3/S0 experiment grouping."""
    return {
        "schema_version": "experiment_design.v1",
        "decision_regime": decision_regime,
        "agent_objective_profile": agent_objective_profile,
        "constraint_level": constraint_level,
        "experiment_group": experiment_group,
        "evidence_status": evidence_status,
        "notes": notes,
    }


def _scriptify_agent_departments(
    departments: List[Dict[str, Any]],
    apply_to_departments: List[str] = None,
) -> List[Dict[str, Any]]:
    """Convert selected departments to Scripted mode while preserving their metadata."""
    apply_to_departments = apply_to_departments or ["all"]
    result: List[Dict[str, Any]] = []
    for department in departments:
        next_department = deepcopy(department)
        dept_id = next_department.get("dept_id")
        if "all" in apply_to_departments or dept_id in apply_to_departments:
            next_department["type"] = "Scripted"
        result.append(next_department)
    return result


def _normalize_single_enterprise_agent_departments(
    departments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Use standard department Skills for a single-enterprise diagnostic scenario without a multi-enterprise trade phase."""
    result: List[Dict[str, Any]] = []
    for department in departments:
        next_department = deepcopy(department)
        dept_id = str(next_department.get("dept_id") or "").lower()
        if dept_id == "sales":
            next_department["skill_name"] = "sales"
            next_department.pop("need_trade", None)
        elif dept_id == "procurement":
            next_department["skill_name"] = "procurement"
            next_department.pop("need_trade", None)
        result.append(next_department)
    return result


def _scriptify_enterprise_blueprints(
    blueprints: List[Dict[str, Any]],
    apply_to_departments: List[str] = None,
) -> List[Dict[str, Any]]:
    result = deepcopy(blueprints)
    for blueprint in result:
        blueprint["agent_departments"] = _scriptify_agent_departments(
            blueprint.get("agent_departments", []),
            apply_to_departments=apply_to_departments,
        )
    return result


def _scriptify_scenario_config(
    scenario_id: str,
    scenario_config: Dict[str, Any],
    mode: str,
    base_quantity: float = 80,
    policy_overrides: Dict[str, Any] = None,
    initial_capital_overrides: Dict[str, int] = None,
) -> Dict[str, Any]:
    """Create a fully scripted copy of a scenario without mutating the original Agent configuration."""
    cloned = deepcopy(scenario_config)
    cloned["meta"]["scenario_id"] = scenario_id
    cloned["meta"]["name"] = f"{cloned['meta'].get('name', scenario_id)} (Scripted Variant)"
    cloned["meta"]["summary"] = (
        f"{cloned['meta'].get('summary', '')} This variant disables departmental Agent Skill decisions "
        "and uses the configured scripted_rule_policy to generate analyst guidance and department actions."
    ).strip()
    runtime_injection = cloned.setdefault("runtime_injection", {})
    runtime_injection["scripted_rule_policy"] = _build_scripted_rule_policy(
        enabled=True,
        mode=mode,
        base_quantity=base_quantity,
        single_enterprise_rules=runtime_injection.get(
            "single_enterprise_rule_overrides"
        ),
        policy_overrides=policy_overrides,
    )
    cloned.setdefault("runtime_injection", {})["profit_objective_policy"] = _build_profit_objective_policy(
        enabled=False,
    )
    if mode == "cobweb":
        cobweb_config = cloned.setdefault("simulation", {}).setdefault("cobweb_config", {})
        cobweb_config.update({
            "production_response_mode": "scripted_formula",
            "decision_profile": "scripted_formula",
        })
    cloned["experiment_design"] = _build_experiment_design_metadata(
        decision_regime="scripted_rational",
        agent_objective_profile="environment_mechanism_validation",
        constraint_level="scripted",
        experiment_group="C1",
        evidence_status="ready_to_run",
        notes="A rule-based script group; used to validate the environment, market equations, trade settlements and classical mechanisms.",
    )
    cloned["agent_enterprise_layout"] = [
        {
            **deepcopy(enterprise),
            "departments": _scriptify_agent_departments(enterprise.get("departments", [])),
        }
        for enterprise in cloned.get("agent_enterprise_layout", [])
    ]
    cloned["enterprise_specs"] = [
        {
            **deepcopy(enterprise),
            "agent_departments": _scriptify_agent_departments(enterprise.get("agent_departments", [])),
        }
        for enterprise in cloned.get("enterprise_specs", [])
    ]
    for enterprise in cloned.get("enterprise_specs", []):
        enterprise_id = enterprise.get("id") or enterprise.get("enterprise_id")
        if enterprise_id in (initial_capital_overrides or {}):
            enterprise["initial_capital"] = int(initial_capital_overrides[enterprise_id])
    return cloned


def _profit_objective_scenario_config(
    scenario_id: str,
    scenario_config: Dict[str, Any],
    objective_weights: Dict[str, float] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """Create a C3 open-objective Agent variant without mutating the base scenario."""
    cloned = deepcopy(scenario_config)
    cloned["meta"]["scenario_id"] = scenario_id
    cloned["meta"]["name"] = f"{cloned['meta'].get('name', scenario_id)} (Open Profit-Objective Variant)"
    cloned["meta"]["summary"] = (
        f"{cloned['meta'].get('summary', '')} This variant does not require agents to reproduce a prescribed economic effect. "
        "Decisions instead balance profit, cash safety, service level, and inventory or resource risk."
    ).strip()
    cloned.setdefault("runtime_injection", {})["scripted_rule_policy"] = _build_scripted_rule_policy(
        enabled=False,
    )
    cloned.setdefault("runtime_injection", {})["profit_objective_policy"] = _build_profit_objective_policy(
        enabled=True,
        objective_profile="profit_maximization",
        objective_weights=objective_weights,
        planning_horizon_rounds=8,
    )
    if cloned.setdefault("simulation", {}).get("market_demand_mode") == MARKET_DEMAND_MODE_COBWEB:
        cloned["simulation"].setdefault("cobweb_config", {})["decision_profile"] = "profit_balanced"
        cloned["runtime_injection"]["cobweb_enterprise_guidance_policy"] = (
            _build_cobweb_enterprise_guidance_policy(
                enabled=False,
                target_enterprise_ids=["Manufacturer"],
                non_cobweb_target_priority="balanced",
                allow_backlog_only_as_feasibility_signal=False,
                forbid_minimum_batch_targets=False,
                forbid_capacity_activation_targets=False,
            )
        )
    _enable_c3_long_run_profiles(cloned)
    cloned["experiment_design"] = _build_experiment_design_metadata(
        decision_regime="profit_seeking_agent",
        agent_objective_profile="profit_maximization",
        constraint_level="open_objective",
        experiment_group="C3",
        evidence_status="ready_to_run",
        notes=notes or "Profit-seeking Agent group used to observe whether an open objective weakens, avoids, or reshapes the classical mechanism.",
    )
    return cloned


DEFAULT_ENTERPRISE_BLUEPRINTS = [
    {
        "id": "Supplier",
        "name": "Supplier",
        "tier": 0,
        "role_tags": ["top_tier_supply_node", "raw_material_supplier"],
        "policy_tags": ["external_procurement_enabled", "top_tier_supply_enabled"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["Malt", "Hops", "Yeast"],
        "purchasable_materials_idList": ["Malt", "Hops", "Yeast"],
        "supplier_name_list": ["Upstream_Supplier"],
        "agent_departments": [
            {"dept_id": "hr", "role": "HR", "name": 'Human Resources', "skill_name": "hr", "type": "Auto"},
            {"dept_id": "inventory", "role": "Inventory", "name": 'Inventory Management', "skill_name": "inventory", "type": "Auto"},
            {
                "dept_id": "procurement",
                "role": "Procurement",
                "name": 'Procurement Management',
                "skill_name": "procurement",
                "type": "Agent",
            },
            {
                "dept_id": "sales",
                "role": "Sales",
                "name": 'Sales Management',
                "skill_name": "seller",
                "type": "Agent",
                "need_trade": True,
            },
        ],
    },
    {
        "id": "Manufacturer",
        "name": "Manufacturer",
        "tier": 1,
        "role_tags": ["finished_goods_manufacturer"],
        "policy_tags": ["production_enabled", "recipe_driven_procurement"],
        "enabled_functions": ["procurement", "sales", "finance", "production", "hr", "inventory"],
        "salable_products_idList": ["beer"],
        "purchasable_materials_idList": ["Malt", "Hops", "Yeast"],
        "agent_departments": [
            {"dept_id": "hr", "role": "HR", "name": 'Human Resources', "skill_name": "hr", "type": "Auto"},
            {"dept_id": "inventory", "role": "Inventory", "name": 'Inventory Management', "skill_name": "inventory", "type": "Auto"},
            {
                "dept_id": "production",
                "role": "Production",
                "name": 'Production Management',
                "skill_name": "production",
                "type": "Agent",
            },
            {
                "dept_id": "sales",
                "role": "Sales",
                "name": 'Sales Management',
                "skill_name": "seller",
                "type": "Agent",
                "need_trade": True,
            },
            {
                "dept_id": "procurement",
                "role": "Procurement",
                "name": 'Procurement Management',
                "skill_name": "buyer",
                "type": "Agent",
                "need_trade": True,
            },
        ],
    },
    {
        "id": "Distributor",
        "name": "Distributor",
        "tier": 2,
        "role_tags": ["intermediate_distributor"],
        "policy_tags": ["distribution_only"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["beer"],
        "purchasable_materials_idList": ["beer"],
        "agent_departments": [
            {"dept_id": "hr", "role": "HR", "name": 'Human Resources', "skill_name": "hr", "type": "Auto"},
            {"dept_id": "inventory", "role": "Inventory", "name": 'Inventory Management', "skill_name": "inventory", "type": "Auto"},
            {
                "dept_id": "sales",
                "role": "Sales",
                "name": 'Sales Management',
                "skill_name": "seller",
                "type": "Agent",
                "need_trade": True,
            },
            {
                "dept_id": "procurement",
                "role": "Procurement",
                "name": 'Procurement Management',
                "skill_name": "buyer",
                "type": "Agent",
                "need_trade": True,
            },
        ],
    },
    {
        "id": "Retailer",
        "name": "Retailer",
        "tier": 3,
        "role_tags": ["retail_market_node"],
        "policy_tags": ["external_market_connected"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["beer"],
        "purchasable_materials_idList": ["beer"],
        "agent_departments": [
            {"dept_id": "hr", "role": "HR", "name": 'Human Resources', "skill_name": "hr", "type": "Auto"},
            {"dept_id": "inventory", "role": "Inventory", "name": 'Inventory Management', "skill_name": "inventory", "type": "Auto"},
            {
                "dept_id": "sales",
                "role": "Sales",
                "name": 'Sales Management',
                "skill_name": "sales",
                "type": "Agent",
            },
            {
                "dept_id": "procurement",
                "role": "Procurement",
                "name": 'Procurement Management',
                "skill_name": "buyer",
                "type": "Agent",
                "need_trade": True,
            },
        ],
    },
]

DIRECT_DISTRIBUTION_EXPERIMENT_BLUEPRINTS = [
    {
        "id": "TopDistributor",
        "name": "TopDistributor",
        "tier": 0,
        "role_tags": ["top_tier_distribution_hub"],
        "policy_tags": ["distribution_only", "unbounded_supply_node"],
        "enabled_functions": ["sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["beer"],
        "purchasable_materials_idList": [],
        "supplier_name_list": [],
        "agent_departments": [
            {"dept_id": "hr", "role": "HR", "name": 'Human Resources', "skill_name": "hr", "type": "Auto"},
            {"dept_id": "inventory", "role": "Inventory", "name": 'Inventory Management', "skill_name": "inventory", "type": "Auto"},
            {
                "dept_id": "sales",
                "role": "Sales",
                "name": 'Sales Management',
                "skill_name": "seller",
                "type": "Agent",
                "need_trade": True,
            },
        ],
    },
    {
        "id": "RegionalDistributor",
        "name": "RegionalDistributor",
        "tier": 1,
        "role_tags": ["intermediate_distributor"],
        "policy_tags": ["distribution_only"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["beer"],
        "purchasable_materials_idList": ["beer"],
        "supplier_name_list": [],
        "agent_departments": [
            {"dept_id": "hr", "role": "HR", "name": 'Human Resources', "skill_name": "hr", "type": "Auto"},
            {"dept_id": "inventory", "role": "Inventory", "name": 'Inventory Management', "skill_name": "inventory", "type": "Auto"},
            {
                "dept_id": "sales",
                "role": "Sales",
                "name": 'Sales Management',
                "skill_name": "seller",
                "type": "Agent",
                "need_trade": True,
            },
            {
                "dept_id": "procurement",
                "role": "Procurement",
                "name": 'Procurement Management',
                "skill_name": "buyer",
                "type": "Agent",
                "need_trade": True,
            },
        ],
    },
    {
        "id": "LocalDistributor",
        "name": "LocalDistributor",
        "tier": 2,
        "role_tags": ["intermediate_distributor"],
        "policy_tags": ["distribution_only"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["beer"],
        "purchasable_materials_idList": ["beer"],
        "supplier_name_list": [],
        "agent_departments": [
            {"dept_id": "hr", "role": "HR", "name": 'Human Resources', "skill_name": "hr", "type": "Auto"},
            {"dept_id": "inventory", "role": "Inventory", "name": 'Inventory Management', "skill_name": "inventory", "type": "Auto"},
            {
                "dept_id": "sales",
                "role": "Sales",
                "name": 'Sales Management',
                "skill_name": "seller",
                "type": "Agent",
                "need_trade": True,
            },
            {
                "dept_id": "procurement",
                "role": "Procurement",
                "name": 'Procurement Management',
                "skill_name": "buyer",
                "type": "Agent",
                "need_trade": True,
            },
        ],
    },
    {
        "id": "Retail",
        "name": "Retail",
        "tier": 3,
        "role_tags": ["retail_market_node"],
        "policy_tags": ["distribution_only", "external_market_connected"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["beer"],
        "purchasable_materials_idList": ["beer"],
        "supplier_name_list": [],
        "agent_departments": [
            {"dept_id": "hr", "role": "HR", "name": 'Human Resources', "skill_name": "hr", "type": "Auto"},
            {"dept_id": "inventory", "role": "Inventory", "name": 'Inventory Management', "skill_name": "inventory", "type": "Auto"},
            {
                "dept_id": "sales",
                "role": "Sales",
                "name": 'Sales Management',
                "skill_name": "sales",
                "type": "Agent",
            },
            {
                "dept_id": "procurement",
                "role": "Procurement",
                "name": 'Procurement Management',
                "skill_name": "buyer",
                "type": "Agent",
                "need_trade": True,
            },
        ],
    },
]

COBWEB_CLEAN_ENTERPRISE_BLUEPRINTS = [
    {
        "id": "Manufacturer",
        "name": "Manufacturer",
        "tier": 0,
        "role_tags": ["single_cobweb_producer", "finished_goods_manufacturer"],
        "policy_tags": ["production_enabled", "external_market_connected", "cobweb_price_response"],
        "enabled_functions": ["sales", "finance", "production", "hr", "inventory"],
        "salable_products_idList": ["beer"],
        "purchasable_materials_idList": [],
        "supplier_name_list": [],
        "agent_departments": [
            {"dept_id": "hr", "role": "HR", "name": 'Human Resources', "skill_name": "hr", "type": "Auto"},
            {"dept_id": "inventory", "role": "Inventory", "name": 'Inventory Management', "skill_name": "inventory", "type": "Auto"},
            {
                "dept_id": "production",
                "role": "Production",
                "name": 'Production Management',
                "skill_name": "production",
                "type": "Agent",
            },
            {
                "dept_id": "sales",
                "role": "Sales",
                "name": 'Sales Management',
                "skill_name": "sales",
                "type": "Agent",
            },
        ],
    },
]

COMMONS_MINING_ENTERPRISE_IDS = ["Miner_A", "Miner_B", "Miner_C", "Miner_D"]
HERDING_SENSOR_ENTERPRISE_IDS = ["Sensor_A", "Sensor_B", "Sensor_C", "Sensor_D"]

COMMONS_STRATEGY_PROFILES = {
    "balanced_growth": {
        "profile_id": "balanced_growth",
        "label": 'Balanced growth',
        "acquisition_bias": "moderate_above_sustainable_share",
        "preferred_acquisition_band": [120, 180],
        "growth_priority": 0.65,
        "profit_priority": 0.70,
        "resource_risk_sensitivity": 0.45,
        "peer_response": "match_market_pressure_without_strictly_copying_peers",
        "decision_note": 'When ungovernable and profit space is positive, it can be slightly higher than a single sustainable share, provided that resource risk is specified.',
    },
    "high_growth_competitive": {
        "profile_id": "high_growth_competitive",
        "label": 'High-growth competitive',
        "acquisition_bias": "aggressive_above_sustainable_share",
        "preferred_acquisition_band": [220, 360],
        "growth_priority": 0.95,
        "profit_priority": 0.92,
        "resource_risk_sensitivity": 0.18,
        "peer_response": "respond_to_peer_acquisition_with_expansion_or_maintained_high_output",
        "decision_note": 'Priority is given to maintaining high resource/product acquisition schemes when unsanctioned and when the order/profit pressure is clear; resource risk is an explanatory factor rather than a rigid indent constraint.',
    },
    "conservative_resilience": {
        "profile_id": "conservative_resilience",
        "label": 'Steady resilience.',
        "acquisition_bias": "near_sustainable_share",
        "preferred_acquisition_band": [90, 130],
        "growth_priority": 0.45,
        "profit_priority": 0.60,
        "resource_risk_sensitivity": 0.75,
        "peer_response": "avoid_chasing_peers_when_resource_quality_declines",
        "decision_note": 'Greater emphasis is placed on the quality of resources and on long-term effective outputs, and when the quality of resources is declining it tends to shrink or to maintain low growth.',
    },
    "opportunistic_follower": {
        "profile_id": "opportunistic_follower",
        "label": 'Opportunity Follower',
        "acquisition_bias": "peer_sensitive_above_sustainable_share",
        "preferred_acquisition_band": [120, 210],
        "growth_priority": 0.70,
        "profit_priority": 0.80,
        "resource_risk_sensitivity": 0.40,
        "peer_response": "increase_acquisition_when_peers_keep_high_acquisition",
        "decision_note": 'If a partner continues to have high levels of access and his/her own profits remain positive, he/she tends to follow higher levels of access to avoid falling behind in market share.',
    },
}


HERDING_STRATEGY_PROFILES = {
    "trend_chaser": {
        "profile_id": "trend_chaser",
        "label": 'Trends Follower',
        "market_heat_sensitivity": 0.92,
        "peer_signal_sensitivity": 0.88,
        "inventory_risk_sensitivity": 0.30,
        "cash_risk_sensitivity": 0.35,
        "decision_note": 'When there is a strong signal of expansion in the market heat and contract industry, there is a tendency to follow it quickly to gain market share.',
    },
    "balanced_follower": {
        "profile_id": "balanced_follower",
        "label": 'Balance Follower',
        "market_heat_sensitivity": 0.70,
        "peer_signal_sensitivity": 0.62,
        "inventory_risk_sensitivity": 0.50,
        "cash_risk_sensitivity": 0.50,
        "decision_note": 'A summary of the heat and trade of the market will be consulted, but the increase will need to be controlled in combination with real orders, stock and cash pressure.',
    },
    "cautious_validator": {
        "profile_id": "cautious_validator",
        "label": 'Precautionary Authentication Type',
        "market_heat_sensitivity": 0.48,
        "peer_signal_sensitivity": 0.38,
        "inventory_risk_sensitivity": 0.75,
        "cash_risk_sensitivity": 0.70,
        "decision_note": 'Increased emphasis is placed on backlogs, cash pressures and real orders, and careful validation of market heat and industry signals is maintained.',
    },
}


def _build_herding_sensor_enterprise_blueprints(
    strategy_profiles_by_enterprise: Dict[str, Dict[str, Any]] = None,
    default_strategy_profile: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    """Build parallel enterprise blueprints for herding; strategy profiles express Agent preferences."""
    strategy_profiles_by_enterprise = strategy_profiles_by_enterprise or {}
    default_strategy_profile = default_strategy_profile or HERDING_STRATEGY_PROFILES["balanced_follower"]
    blueprints = []
    for enterprise_id in HERDING_SENSOR_ENTERPRISE_IDS:
        strategy_profile = deepcopy(strategy_profiles_by_enterprise.get(enterprise_id) or default_strategy_profile)
        blueprints.append({
            "id": enterprise_id,
            "name": enterprise_id,
            "tier": 0,
            "role_tags": ["herding_participant", "parallel_smart_sensor_producer"],
            "policy_tags": ["production_enabled", "external_market_connected", "herding_participant"],
            "strategy_profile": strategy_profile,
            "enabled_functions": ["sales", "finance", "production", "hr", "inventory"],
            "salable_products_idList": ["smart_sensor"],
            "purchasable_materials_idList": [],
            "supplier_name_list": [],
            "agent_departments": [
                {"dept_id": "hr", "role": "HR", "name": "Human Resources", "skill_name": "hr", "type": "Auto"},
                {"dept_id": "inventory", "role": "Inventory", "name": "Inventory Management", "skill_name": "inventory", "type": "Auto"},
                {
                    "dept_id": "production",
                    "role": "Production",
                    "name": "Production Management",
                    "skill_name": "production",
                    "type": "Agent",
                },
                {
                    "dept_id": "sales",
                    "role": "Sales",
                    "name": "Sales Management",
                    "skill_name": "sales",
                    "type": "Agent",
                },
            ],
        })
    return blueprints


def _build_commons_mining_enterprise_blueprints(
    strategy_profiles_by_enterprise: Dict[str, Dict[str, Any]] = None,
    default_strategy_profile: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    """Construct a shared resource enterprise blueprint; strategy_profile only as the Agent decision preference, not a script hard-control number."""
    strategy_profiles_by_enterprise = strategy_profiles_by_enterprise or {}
    default_strategy_profile = default_strategy_profile or COMMONS_STRATEGY_PROFILES["balanced_growth"]
    blueprints = []
    for enterprise_id in COMMONS_MINING_ENTERPRISE_IDS:
        strategy_profile = deepcopy(strategy_profiles_by_enterprise.get(enterprise_id) or default_strategy_profile)
        blueprints.append({
            "id": enterprise_id,
            "name": enterprise_id,
            "tier": 0,
            "role_tags": ["shared_resource_participant", "commons_mining_operator"],
            "policy_tags": ["production_enabled", "external_market_connected", "shared_resource_acquisition"],
            "strategy_profile": strategy_profile,
            "enabled_functions": ["sales", "finance", "production", "hr", "inventory"],
            "salable_products_idList": ["copper_ore"],
            "purchasable_materials_idList": [],
            "supplier_name_list": [],
            "agent_departments": [
                {"dept_id": "hr", "role": "HR", "name": "Human Resources", "skill_name": "hr", "type": "Auto"},
                {"dept_id": "inventory", "role": "Inventory", "name": "Inventory Management", "skill_name": "inventory", "type": "Auto"},
                {
                    "dept_id": "production",
                    "role": "Production",
                    "name": "Production Management",
                    "skill_name": "production",
                    "type": "Agent",
                },
                {
                    "dept_id": "sales",
                    "role": "Sales",
                    "name": "Sales Management",
                    "skill_name": "sales",
                    "type": "Agent",
                },
            ],
        })
    return blueprints


COMMONS_MINING_ENTERPRISE_BLUEPRINTS = _build_commons_mining_enterprise_blueprints()

DEFAULT_BEER_RECIPE = {
    "product_id": "beer",
    "raw_materials": {"Malt": 100.0, "Hops": 10.0, "Yeast": 5},
    "production_time": 1,
    "labor_cost_per_unit": 0.1,
    "equipment_cost_per_unit": 0.05,
}

DEFAULT_COPPER_ORE_RECIPE = {
    "product_id": "copper_ore",
    "raw_materials": {},
    "production_time": 1,
    "labor_cost_per_unit": 0.05,
    "equipment_cost_per_unit": 0.03,
}

DEFAULT_SMART_SENSOR_RECIPE = {
    "product_id": "smart_sensor",
    "raw_materials": {},
    "production_time": 1,
    "labor_cost_per_unit": 12.0,
    "equipment_cost_per_unit": 8.0,
}

SINGLE_ENTERPRISE_FINANCE_AGENT_DEPARTMENT = {
    "dept_id": "finance",
    "role": "Finance",
    "name": 'Financial diagnosis',
    "skill_name": "finance",
    "type": "Agent",
}


def _build_single_enterprise_diagnostic_blueprints(
    finance_agent_enabled: bool = False,
) -> List[Dict[str, Any]]:
    agent_departments = [
        {"dept_id": "hr", "role": "HR", "name": "Human Resources", "skill_name": "hr", "type": "Agent"},
        {"dept_id": "inventory", "role": "Inventory", "name": "Inventory Management", "skill_name": "inventory", "type": "Auto"},
        {
            "dept_id": "production",
            "role": "Production",
            "name": "Production Management",
            "skill_name": "production",
            "type": "Agent",
        },
        {
            "dept_id": "sales",
            "role": "Sales",
            "name": "Sales Management",
            "skill_name": "sales",
            "type": "Agent",
        },
        {
            "dept_id": "procurement",
            "role": "Procurement",
            "name": "Procurement Management",
            "skill_name": "procurement",
            "type": "Agent",
        },
    ]
    if finance_agent_enabled:
        agent_departments.insert(0, deepcopy(SINGLE_ENTERPRISE_FINANCE_AGENT_DEPARTMENT))
    return [
        {
        "id": "SingleManufacturer",
        "name": "SingleManufacturer",
        "tier": 0,
        "role_tags": [
            "single_enterprise_diagnostic",
            "finished_goods_manufacturer",
        ],
        "policy_tags": [
            "production_enabled",
            "external_market_connected",
            "external_procurement_enabled",
            "single_enterprise_case",
        ],
        "strategy_profile": {
            "scope": "single_enterprise_case",
            "decision_style": "diagnose_primary_constraint_before_expansion",
        },
        "enabled_functions": [
            "procurement",
            "sales",
            "finance",
            "production",
            "hr",
            "inventory",
        ],
        "salable_products_idList": ["beer"],
        "purchasable_materials_idList": ["Malt", "Hops", "Yeast"],
        "supplier_name_list": ["Upstream_Supplier"],
        "agent_departments": agent_departments,
    }
]


SINGLE_ENTERPRISE_DIAGNOSTIC_BLUEPRINTS = _build_single_enterprise_diagnostic_blueprints()

ARCHITECTURE_LINEAR_ENTERPRISE_IDS = ["RawSupplier", "ComponentMaker", "FinalAssembler", "Retailer"]
ARCHITECTURE_BRANCHING_ENTERPRISE_IDS = [
    "MaltSupplier",
    "BottleSupplier",
    "YeastSupplier",
    "BreweryAssembler",
    "Retailer",
]
ARCHITECTURE_MESH_ENTERPRISE_IDS = [
    "GrainSupplier",
    "PackagingSupplier",
    "FlavorSupplier",
    "Brewery_A",
    "Brewery_B",
    "DistributionHub",
    "Channel_A",
    "Channel_B",
]

ARCHITECTURE_LINEAR_BLUEPRINTS = [
    {
        "id": "RawSupplier",
        "name": "RawSupplier",
        "tier": 0,
        "role_tags": ["architecture_top_supplier", "raw_material_supplier"],
        "policy_tags": ["external_procurement_enabled", "top_tier_supply_enabled"],
        "enabled_functions": ["sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["raw_malt", "raw_hops"],
        "purchasable_materials_idList": [],
        "supplier_name_list": [],
    },
    {
        "id": "ComponentMaker",
        "name": "ComponentMaker",
        "tier": 1,
        "role_tags": ["architecture_midstream_processor"],
        "policy_tags": ["production_enabled", "recipe_driven_procurement"],
        "enabled_functions": ["procurement", "sales", "finance", "production", "hr", "inventory"],
        "salable_products_idList": ["brew_mix"],
        "purchasable_materials_idList": ["raw_malt", "raw_hops"],
        "supplier_name_list": ["RawSupplier"],
    },
    {
        "id": "FinalAssembler",
        "name": "FinalAssembler",
        "tier": 2,
        "role_tags": ["architecture_final_assembler", "finished_goods_manufacturer"],
        "policy_tags": ["production_enabled", "recipe_driven_procurement"],
        "enabled_functions": ["procurement", "sales", "finance", "production", "hr", "inventory"],
        "salable_products_idList": ["smart_device"],
        "purchasable_materials_idList": ["brew_mix"],
        "supplier_name_list": ["ComponentMaker"],
    },
    {
        "id": "Retailer",
        "name": "Retailer",
        "tier": 3,
        "role_tags": ["architecture_downstream_retailer", "retailer"],
        "policy_tags": ["external_market_connected"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["smart_device"],
        "purchasable_materials_idList": ["smart_device"],
        "supplier_name_list": ["FinalAssembler"],
    },
]

ARCHITECTURE_BRANCHING_BLUEPRINTS = [
    {
        "id": "MaltSupplier",
        "name": "MaltSupplier",
        "tier": 0,
        "role_tags": ["architecture_branch_supplier", "raw_material_supplier"],
        "policy_tags": ["top_tier_supply_enabled"],
        "enabled_functions": ["sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["malt_extract"],
        "purchasable_materials_idList": [],
        "supplier_name_list": [],
    },
    {
        "id": "BottleSupplier",
        "name": "BottleSupplier",
        "tier": 0,
        "role_tags": ["architecture_branch_supplier", "packaging_supplier"],
        "policy_tags": ["top_tier_supply_enabled"],
        "enabled_functions": ["sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["bottle_pack"],
        "purchasable_materials_idList": [],
        "supplier_name_list": [],
    },
    {
        "id": "YeastSupplier",
        "name": "YeastSupplier",
        "tier": 0,
        "role_tags": ["architecture_branch_supplier", "ingredient_supplier"],
        "policy_tags": ["top_tier_supply_enabled"],
        "enabled_functions": ["sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["yeast_culture"],
        "purchasable_materials_idList": [],
        "supplier_name_list": [],
    },
    {
        "id": "BreweryAssembler",
        "name": "BreweryAssembler",
        "tier": 1,
        "role_tags": ["architecture_branch_assembler", "finished_goods_manufacturer"],
        "policy_tags": ["production_enabled", "recipe_driven_procurement"],
        "enabled_functions": ["procurement", "sales", "finance", "production", "hr", "inventory"],
        "salable_products_idList": ["portable_device"],
        "purchasable_materials_idList": ["malt_extract", "bottle_pack", "yeast_culture"],
        "supplier_name_list": ["MaltSupplier", "BottleSupplier", "YeastSupplier"],
    },
    {
        "id": "Retailer",
        "name": "Retailer",
        "tier": 2,
        "role_tags": ["architecture_downstream_retailer", "retailer"],
        "policy_tags": ["external_market_connected"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["portable_device"],
        "purchasable_materials_idList": ["portable_device"],
        "supplier_name_list": ["BreweryAssembler"],
    },
]

ARCHITECTURE_MESH_BLUEPRINTS = [
    {
        "id": "GrainSupplier",
        "name": "GrainSupplier",
        "tier": 0,
        "role_tags": ["architecture_mesh_supplier", "raw_material_supplier"],
        "policy_tags": ["top_tier_supply_enabled"],
        "enabled_functions": ["sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["grain_base"],
        "purchasable_materials_idList": [],
        "supplier_name_list": [],
    },
    {
        "id": "PackagingSupplier",
        "name": "PackagingSupplier",
        "tier": 0,
        "role_tags": ["architecture_mesh_supplier", "packaging_supplier"],
        "policy_tags": ["top_tier_supply_enabled"],
        "enabled_functions": ["sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["bottle_pack"],
        "purchasable_materials_idList": [],
        "supplier_name_list": [],
    },
    {
        "id": "FlavorSupplier",
        "name": "FlavorSupplier",
        "tier": 0,
        "role_tags": ["architecture_mesh_supplier", "ingredient_supplier"],
        "policy_tags": ["top_tier_supply_enabled"],
        "enabled_functions": ["sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["flavor_concentrate"],
        "purchasable_materials_idList": [],
        "supplier_name_list": [],
    },
    {
        "id": "Brewery_A",
        "name": "Brewery_A",
        "tier": 1,
        "role_tags": ["architecture_mesh_module_maker", "finished_goods_manufacturer"],
        "policy_tags": ["production_enabled", "recipe_driven_procurement"],
        "enabled_functions": ["procurement", "sales", "finance", "production", "hr", "inventory"],
        "salable_products_idList": ["industrial_robot_kit"],
        "purchasable_materials_idList": ["grain_base", "bottle_pack"],
        "supplier_name_list": ["GrainSupplier", "PackagingSupplier"],
    },
    {
        "id": "Brewery_B",
        "name": "Brewery_B",
        "tier": 1,
        "role_tags": ["architecture_mesh_module_maker", "finished_goods_manufacturer"],
        "policy_tags": ["production_enabled", "recipe_driven_procurement"],
        "enabled_functions": ["procurement", "sales", "finance", "production", "hr", "inventory"],
        "salable_products_idList": ["industrial_robot_kit"],
        "purchasable_materials_idList": ["grain_base", "flavor_concentrate"],
        "supplier_name_list": ["GrainSupplier", "FlavorSupplier"],
    },
    {
        "id": "DistributionHub",
        "name": "DistributionHub",
        "tier": 2,
        "role_tags": ["architecture_mesh_distribution_hub", "intermediate_distributor"],
        "policy_tags": ["multi_source_procurement"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["industrial_robot_kit"],
        "purchasable_materials_idList": ["industrial_robot_kit"],
        "supplier_name_list": ["Brewery_A", "Brewery_B"],
    },
    {
        "id": "Channel_A",
        "name": "Channel_A",
        "tier": 3,
        "role_tags": ["architecture_mesh_channel", "retailer"],
        "policy_tags": ["external_market_connected"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["industrial_robot_kit"],
        "purchasable_materials_idList": ["industrial_robot_kit"],
        "supplier_name_list": ["DistributionHub"],
    },
    {
        "id": "Channel_B",
        "name": "Channel_B",
        "tier": 3,
        "role_tags": ["architecture_mesh_channel", "retailer"],
        "policy_tags": ["external_market_connected"],
        "enabled_functions": ["procurement", "sales", "finance", "hr", "inventory"],
        "salable_products_idList": ["industrial_robot_kit"],
        "purchasable_materials_idList": ["industrial_robot_kit"],
        "supplier_name_list": ["DistributionHub"],
    },
]

DEFAULT_BREW_MIX_RECIPE = {
    "product_id": "brew_mix",
    "raw_materials": {"raw_malt": 1.0, "raw_hops": 0.15},
    "production_time": 1,
    "labor_cost_per_unit": 0.7,
    "equipment_cost_per_unit": 0.3,
}

DEFAULT_LINEAR_SMART_DEVICE_RECIPE = {
    "product_id": "smart_device",
    "raw_materials": {"brew_mix": 1.0},
    "production_time": 1,
    "labor_cost_per_unit": 2.2,
    "equipment_cost_per_unit": 1.0,
}

DEFAULT_BRANCHING_PORTABLE_DEVICE_RECIPE = {
    "product_id": "portable_device",
    "raw_materials": {"malt_extract": 1.0, "bottle_pack": 1.0, "yeast_culture": 0.1},
    "production_time": 1,
    "labor_cost_per_unit": 9.0,
    "equipment_cost_per_unit": 4.0,
}

DEFAULT_MESH_ROBOT_KIT_RECIPE_A = {
    "product_id": "industrial_robot_kit",
    "raw_materials": {"grain_base": 1.0, "bottle_pack": 1.0},
    "production_time": 1,
    "labor_cost_per_unit": 8.5,
    "equipment_cost_per_unit": 4.0,
}

DEFAULT_MESH_ROBOT_KIT_RECIPE_B = {
    "product_id": "industrial_robot_kit",
    "raw_materials": {"grain_base": 1.0, "flavor_concentrate": 0.2},
    "production_time": 1,
    "labor_cost_per_unit": 10.0,
    "equipment_cost_per_unit": 4.5,
}


def _default_agent_departments_for_enabled_functions(enabled_functions: List[str]) -> List[Dict[str, Any]]:
    """When department layout is not visible, the minimum default layout is given according to the enabled module."""
    departments: List[Dict[str, Any]] = []
    ordered_modules = ["hr", "inventory", "production", "sales", "procurement"]
    for module_name in ordered_modules:
        if module_name not in enabled_functions:
            continue
        if module_name == "hr":
            departments.append({"dept_id": "hr", "role": "HR", "name": "Human Resources", "skill_name": "hr", "type": "Auto"})
        elif module_name == "inventory":
            departments.append({"dept_id": "inventory", "role": "Inventory", "name": "Inventory Management", "skill_name": "inventory", "type": "Auto"})
        elif module_name == "production":
            departments.append({
                "dept_id": "production",
                "role": "Production",
                "name": "Production Management",
                "skill_name": "production",
                "type": "Agent",
            })
        elif module_name == "sales":
            departments.append({
                "dept_id": "sales",
                "role": "Sales",
                "name": "Sales Management",
                "skill_name": "seller",
                "type": "Agent",
                "need_trade": True,
            })
        elif module_name == "procurement":
            departments.append({
                "dept_id": "procurement",
                "role": "Procurement",
                "name": "Procurement Management",
                "skill_name": "buyer",
                "type": "Agent",
                "need_trade": True,
            })
    return departments


def _build_enterprise_specs(
    initial_capitals: Dict[str, int],
    staffing_map: Dict[str, Dict[str, int]],
    capacity_map: Dict[str, int],
    inventory_map: Dict[str, List[Dict[str, Any]]],
    supplier_data_by_enterprise: Dict[str, List[Dict[str, Any]]],
    inventory_policy_map: Dict[str, List[Dict[str, float]]],
    production_recipes_by_enterprise: Dict[str, Dict[str, Any]] = None,
    initial_production_lines_by_enterprise: Dict[str, List[Dict[str, Any]]] = None,
    enterprise_blueprints: List[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Construct the floor level enterprise specs, which combines enterprise definitions, layout and initialization data."""
    production_recipes_by_enterprise = production_recipes_by_enterprise or {}
    initial_production_lines_by_enterprise = initial_production_lines_by_enterprise or {}
    enterprise_blueprints = deepcopy(enterprise_blueprints or DEFAULT_ENTERPRISE_BLUEPRINTS)

    enterprise_specs: List[Dict[str, Any]] = []
    for blueprint in enterprise_blueprints:
        enterprise_id = blueprint["id"]
        departments = deepcopy(
            blueprint.get("agent_departments")
            or _default_agent_departments_for_enabled_functions(blueprint.get("enabled_functions", []))
        )
        enterprise_specs.append(
            {
                "enterprise_id": enterprise_id,
                "enterprise_name": blueprint.get("name", enterprise_id),
                "tier": blueprint["tier"],
                "enabled_functions": list(blueprint.get("enabled_functions", [])),
                "role_tags": list(blueprint.get("role_tags", [])),
                "policy_tags": list(blueprint.get("policy_tags", [])),
                "strategy_profile": deepcopy(blueprint.get("strategy_profile", {})),
                "initial_capital": int(initial_capitals[enterprise_id]),
                "salable_products_idList": list(blueprint.get("salable_products_idList", [])),
                "purchasable_materials_idList": list(blueprint.get("purchasable_materials_idList", [])),
                "supplier_name_list": list(blueprint.get("supplier_name_list", [])),
                "agent_departments": departments,
                "initial_staffing": deepcopy(staffing_map.get(enterprise_id, {})),
                "initial_capacity": capacity_map.get(enterprise_id),
                "initial_inventory": deepcopy(inventory_map.get(enterprise_id, [])),
                "initial_suppliers": deepcopy(supplier_data_by_enterprise.get(enterprise_id, [])),
                "inventory_policies": deepcopy(inventory_policy_map.get(enterprise_id, [])),
                "production_recipe": deepcopy(production_recipes_by_enterprise.get(enterprise_id)),
                "initial_production_lines": deepcopy(initial_production_lines_by_enterprise.get(enterprise_id, [])),
            }
        )
    return enterprise_specs


def _build_enterprise_configs_from_specs(enterprise_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build simulation enterprise configurations from scenario-level specifications."""
    enterprise_configs: List[Dict[str, Any]] = []
    for spec in enterprise_specs:
        enterprise_configs.append(
            {
                "id": spec["enterprise_id"],
                "enabled_functions": list(spec.get("enabled_functions", [])),
                "role_tags": list(spec.get("role_tags", [])),
                "policy_tags": list(spec.get("policy_tags", [])),
                "strategy_profile": deepcopy(spec.get("strategy_profile", {})),
                "initial_capital": spec["initial_capital"],
                "name": spec.get("enterprise_name", spec["enterprise_id"]),
                "tier": spec["tier"],
                "salable_products_idList": list(spec.get("salable_products_idList", [])),
                "purchasable_materials_idList": list(spec.get("purchasable_materials_idList", [])),
                "supplier_name_list": list(spec.get("supplier_name_list", [])),
            }
        )
    return enterprise_configs




# - `MultiEnterpriseAgentManager.build_demo_specs()`

def _build_agent_enterprise_layout_from_specs(enterprise_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Agent enterprise/department from interprise specs."""
    layout: List[Dict[str, Any]] = []
    for spec in enterprise_specs:
        layout.append(
            {
                "enterprise_id": spec["enterprise_id"],
                "enterprise_name": spec.get("enterprise_name", spec["enterprise_id"]),
                "departments": deepcopy(spec.get("agent_departments", [])),
            }
        )
    return layout


AGENT_ENTERPRISE_LAYOUT_TEMPLATE = _build_agent_enterprise_layout_from_specs(
    _build_enterprise_specs(
        initial_capitals={
            "Supplier": 1,
            "Manufacturer": 1,
            "Distributor": 1,
            "Retailer": 1,
        },
        staffing_map={},
        capacity_map={},
        inventory_map={},
        supplier_data_by_enterprise={},
        inventory_policy_map={},
    )
)


def _build_initial_action_batches_from_specs(
    enterprise_specs: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Constructs two sets of initialization actions.

    Scope of use:
    -`agent/multi_tenant_utils.py` Generate `static_commands/*.json`
    - `StaticUtils.execute_action(...)` Real-time environmental execution before simulation starts
        """
    init_action_1: List[Dict[str, Any]] = []
    for spec in enterprise_specs:
        enterprise_id = spec["enterprise_id"]
        staffing = spec.get("initial_staffing") or {}
        capacity = spec.get("initial_capacity")
        init_action_1.append(
            _make_action(
                "initialize_staffing",
                {"initial_staffing": staffing},
                "HRManager",
                enterprise_id,
            )
        )
        if capacity is not None:
            init_action_1.append(_make_action("set_capacity", {"capacity": capacity}, "InventoryManager", enterprise_id))

    init_action_2: List[Dict[str, Any]] = []
    initialized_inventory_items_by_enterprise: Dict[str, set] = {}
    for spec in enterprise_specs:
        enterprise_id = spec["enterprise_id"]
        items = spec.get("initial_inventory") or []
        for item in items:
            quantity = item.get("quantity", 0)
            try:
                quantity_value = float(quantity)
            except (TypeError, ValueError):
                quantity_value = 0
            if quantity_value <= 0:
                continue
            initialized_inventory_items_by_enterprise.setdefault(enterprise_id, set()).add(
                item["item_id"]
            )
            init_action_2.append(
                _make_action(
                    "add_inventory",
                    {
                        "item_id": item["item_id"],
                        "quantity": quantity,
                        "purchase_price": item["purchase_price"],
                        "item_type": item["item_type"],
                    },
                    "InventoryManager",
                    enterprise_id,
                )
            )

        suppliers_data = spec.get("initial_suppliers") or []
        if suppliers_data:
            init_action_2.append(
                _make_action(
                    "initialize_suppliers",
                    {"suppliers_data": suppliers_data},
                    "ProcurementManager",
                    enterprise_id,
                )
            )

    init_action_3: List[Dict[str, Any]] = []
    for spec in enterprise_specs:
        enterprise_id = spec["enterprise_id"]
        policies = spec.get("inventory_policies") or []
        initialized_item_ids = initialized_inventory_items_by_enterprise.get(
            enterprise_id,
            set(),
        )
        for policy in policies:
            if policy["item_id"] not in initialized_item_ids:
                continue
            init_action_3.append(
                _make_action(
                    "set_inventory_policy",
                    {
                        "item_id": policy["item_id"],
                        "reorder_point": policy["reorder_point"],
                        "safety_stock": policy["safety_stock"],
                    },
                    "InventoryManager",
                    enterprise_id,
                )
            )

        recipe = spec.get("production_recipe")
        if recipe:
            init_action_3.append(
                _make_action(
                    "set_product_recipe",
                    deepcopy(recipe),
                    "ProductionManager",
                    enterprise_id,
                )
            )

        for line in spec.get("initial_production_lines") or []:
            line_payload = deepcopy(line)
            action_name = (
                "initialize_production_line"
                if line_payload.pop("ready_at_start", False) or line_payload.get("initial_status") == "idle"
                else "build_production_line"
            )
            init_action_3.append(
                _make_action(
                    action_name,
                    line_payload,
                    "ProductionManager",
                    enterprise_id,
                )
            )
    return {"init_action_1": init_action_1, "init_action_2": init_action_2, "init_action_3": init_action_3}


def _build_daily_actions_from_specs(
    enterprise_specs: List[Dict[str, Any]],
    include_inventory_cost: bool = False,
    inventory_cost_enterprise_ids: List[str] = None,
) -> List[Dict[str, Any]]:
    """
    Constructs a system check action that is fixed for execution every working day.

    Scope of use:
    - `SimulationEnvAdapter.step(execute_type="daily")`
        """
    daily_actions: List[Dict[str, Any]] = []
    inventory_cost_targets = set(inventory_cost_enterprise_ids or [])
    for spec in enterprise_specs:
        enterprise_id = spec["enterprise_id"]
        enabled_functions = set(spec.get("enabled_functions", []))
        if "hr" in enabled_functions:
            daily_actions.append(_make_action("process_recruitment_completion", {}, "HRManager", enterprise_id))
        if "procurement" in enabled_functions:
            daily_actions.append(_make_action("check_arrived_orders", {}, "ProcurementManager", enterprise_id))
        if "production" in enabled_functions:
            daily_actions.append(_make_action("check_construction_completion", {}, "ProductionManager", enterprise_id))
            daily_actions.append(_make_action("check_completed_plans", {}, "ProductionManager", enterprise_id))
        if "sales" in enabled_functions:
            daily_actions.append(_make_action("check_market_development_completion", {}, "SalesManager", enterprise_id))
            daily_actions.append(_make_action("check_deliverable_orders", {}, "SalesManager", enterprise_id))
        if "procurement" in enabled_functions:
            daily_actions.append(_make_action("settle_external_payables", {}, "ProcurementManager", enterprise_id))
        if "inventory" in enabled_functions and include_inventory_cost and enterprise_id in inventory_cost_targets:
            daily_actions.append(_make_action("calculate_inventory_cost", {}, "InventoryManager", enterprise_id))
    return daily_actions


def _build_scenario_enterprise_bundle(
    initial_capitals: Dict[str, int],
    staffing_map: Dict[str, Dict[str, int]],
    capacity_map: Dict[str, int],
    inventory_map: Dict[str, List[Dict[str, Any]]],
    supplier_data_by_enterprise: Dict[str, List[Dict[str, Any]]],
    inventory_policy_map: Dict[str, List[Dict[str, float]]],
    include_inventory_cost: bool,
    inventory_cost_enterprise_ids: List[str],
    production_recipes_by_enterprise: Dict[str, Dict[str, Any]] = None,
    initial_production_lines_by_enterprise: Dict[str, List[Dict[str, Any]]] = None,
    enterprise_blueprints: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The enterprise configuration, the Agent layout and the initialization actions are required for a one-time spin-off from enterprise specs."""
    enterprise_specs = _build_enterprise_specs(
        initial_capitals=initial_capitals,
        staffing_map=staffing_map,
        capacity_map=capacity_map,
        inventory_map=inventory_map,
        supplier_data_by_enterprise=supplier_data_by_enterprise,
        inventory_policy_map=inventory_policy_map,
        production_recipes_by_enterprise=production_recipes_by_enterprise,
        initial_production_lines_by_enterprise=initial_production_lines_by_enterprise,
        enterprise_blueprints=enterprise_blueprints,
    )
    return {
        "enterprise_specs": enterprise_specs,
        "enterprise_configs": _build_enterprise_configs_from_specs(enterprise_specs),
        "agent_enterprise_layout": _build_agent_enterprise_layout_from_specs(enterprise_specs),
        "initial_action_batches": _build_initial_action_batches_from_specs(enterprise_specs),
        "daily_actions": _build_daily_actions_from_specs(
            enterprise_specs,
            include_inventory_cost=include_inventory_cost,
            inventory_cost_enterprise_ids=inventory_cost_enterprise_ids,
        ),
    }


def _make_external_supplier(materials: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    """Construct Supplier initialized data for external upstream suppliers."""
    return {
        "supplier_name": "Upstream_Supplier",
        "supplier_type": "external",
        "materials": deepcopy(materials),
        "processing_time": 1,
        "quality_level": "standard",
        "reliability_score": 1.0,
    }


def _build_top_tier_supply_policy(
    enabled: bool,
    enterprise_ids: List[str],
    external_credit_limit: float,
    payable_delay_rounds: int,
    max_single_order_share: float,
    external_logistics_cost_multiplier: float = 1.0,
    target_role_tags: List[str] = None,
    allow_direct_external_replenishment_without_exchange: bool = False,
) -> Dict[str, Any]:
    """Construct the most upstream external supply lettering strategy to avoid Supplier ' s degradation to an unlimited access."""
    return {
        "enabled": enabled,
        "enterprise_ids": list(enterprise_ids),
        "target_role_tags": list(target_role_tags or []),
        "credit_enabled_supplier_types": ["external"],
        "external_credit_limit": float(external_credit_limit),
        "payable_delay_rounds": int(payable_delay_rounds),
        "max_single_order_share": float(max_single_order_share),
        "external_logistics_cost_multiplier": float(external_logistics_cost_multiplier),
        "allow_direct_external_replenishment_without_exchange": bool(
            allow_direct_external_replenishment_without_exchange
        ),
    }


def _build_analyst_policy(
    full_analysis_interval_rounds: int,
    force_on_round_zero: bool = True,
    fallback_to_latest_archive: bool = True,
) -> Dict[str, Any]:
    """Construct analyst deployment strategy to avoid rewrite of strategic objectives per HF round."""
    return {
        "full_analysis_interval_rounds": max(1, int(full_analysis_interval_rounds)),
        "force_on_round_zero": bool(force_on_round_zero),
        "fallback_to_latest_archive": bool(fallback_to_latest_archive),
    }


def _build_external_market_order_policy(
    enabled: bool = True,
    mode: str = "single_target",
    target_enterprise_ids: List[str] = None,
    generation_timing: str = "daily_start",
    start_day: int = None,
    end_day: int = None,
    orders_by_day: Dict[str, List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Construct an external market order-injection strategy for the Daily phase to avoid the difference in hard-coding scenarios in adapters."""
    policy = deepcopy(DEFAULT_EXTERNAL_MARKET_ORDER_POLICY_TEMPLATE)
    policy.update({
        "enabled": bool(enabled),
        "mode": mode,
        "target_enterprise_ids": list(target_enterprise_ids or []),
        "generation_timing": generation_timing,
    })
    if start_day is not None:
        policy["start_day"] = max(0, int(start_day))
    if end_day is not None:
        policy["end_day"] = max(0, int(end_day))
    if orders_by_day is not None:
        policy["orders_by_day"] = deepcopy(orders_by_day)
    return policy


def _build_single_enterprise_chart_export_policy(
    enabled: bool = False,
    archive_per_round: bool = True,
    inject_to_analyst: bool = True,
    target_enterprise_ids: List[str] = None,
) -> Dict[str, Any]:
    """Construct the old single-enterprise charts_data_export.json compatible export policy."""
    policy = deepcopy(DEFAULT_SINGLE_ENTERPRISE_CHART_EXPORT_POLICY_TEMPLATE)
    policy.update({
        "enabled": bool(enabled),
        "archive_per_round": bool(archive_per_round),
        "inject_to_analyst": bool(inject_to_analyst),
        "target_enterprise_ids": list(target_enterprise_ids or []),
    })
    return policy


def _normalize_external_market_order_policy(runtime_injection: Dict[str, Any]) -> None:
    """Populate legacy scenarios with the order policy while retaining field compatibility."""
    if "external_market_order_policy" in runtime_injection:
        return

    target_enterprise_ids = list(runtime_injection.get("external_market_order_enterprise_ids") or [])
    fallback_enterprise_id = runtime_injection.get("external_market_order_enterprise_id")
    if not target_enterprise_ids and fallback_enterprise_id:
        target_enterprise_ids = [fallback_enterprise_id]

    runtime_injection["external_market_order_policy"] = _build_external_market_order_policy(
        enabled=bool(target_enterprise_ids),
        mode="multi_target" if len(target_enterprise_ids) > 1 else "single_target",
        target_enterprise_ids=target_enterprise_ids,
    )


def _build_shared_resource_config(
    resource_id: str,
    resource_label: str,
    product_id: str,
    target_enterprise_ids: List[str],
    initial_stock_quantity: float = 12000.0,
    max_stock_quantity: float = 12000.0,
    sustainable_acquisition_per_round: float = 360.0,
    regeneration_quantity: float = 360.0,
    base_unit_price: float = 180.0,
    unit_acquisition_cost: float = 70.0,
    regeneration_mode: str = "fixed",
    resource_quality_floor: float = 0.35,
    quality_degradation_enabled: bool = True,
    quality_function: str = "stock_ratio_linear",
    scarcity_price_enabled: bool = False,
    acquisition_lag_rounds: int = 1,
    initial_acquisition_quantity_per_enterprise: float = 120.0,
    collapse_threshold_ratio: float = 0.20,
    warning_threshold_ratio: float = 0.45,
    customer_delivery_lead_time: int = 1,
    apply_acquisition_cost_to_finance: bool = False,
    acquisition_cost_finance_category: str = "raw_materials",
    constrain_production_output_to_effective_acquisition: bool = False,
    external_order_quantity_mode: str = "effective_acquisition",
    apply_breach_penalty_to_finance: bool = False,
    breach_penalty_per_unit: float = 0.0,
    breach_penalty_finance_category: str = "market_cost",
) -> Dict[str, Any]:
    """Construct a shared resource experimental configuration"""
    config = deepcopy(DEFAULT_SHARED_RESOURCE_CONFIG_TEMPLATE)
    config.update({
        "enabled": True,
        "resource_id": resource_id,
        "resource_label": resource_label,
        "product_id": product_id,
        "target_enterprise_ids": list(target_enterprise_ids),
        "initial_stock_quantity": float(initial_stock_quantity),
        "max_stock_quantity": float(max_stock_quantity),
        "sustainable_acquisition_per_round": float(sustainable_acquisition_per_round),
        "regeneration_mode": regeneration_mode,
        "regeneration_quantity": float(regeneration_quantity),
        "resource_quality_floor": float(resource_quality_floor),
        "quality_degradation_enabled": bool(quality_degradation_enabled),
        "quality_function": quality_function,
        "base_unit_price": float(base_unit_price),
        "unit_acquisition_cost": float(unit_acquisition_cost),
        "scarcity_price_enabled": bool(scarcity_price_enabled),
        "acquisition_lag_rounds": max(0, int(acquisition_lag_rounds)),
        "initial_acquisition_quantity_per_enterprise": float(initial_acquisition_quantity_per_enterprise),
        "collapse_threshold_ratio": float(collapse_threshold_ratio),
        "warning_threshold_ratio": float(warning_threshold_ratio),
        "customer_delivery_lead_time": max(0, int(customer_delivery_lead_time)),
        "apply_acquisition_cost_to_finance": bool(apply_acquisition_cost_to_finance),
        "acquisition_cost_finance_category": str(acquisition_cost_finance_category or "raw_materials"),
        "constrain_production_output_to_effective_acquisition": bool(constrain_production_output_to_effective_acquisition),
        "external_order_quantity_mode": str(external_order_quantity_mode or "effective_acquisition"),
        "apply_breach_penalty_to_finance": bool(apply_breach_penalty_to_finance),
        "breach_penalty_per_unit": float(breach_penalty_per_unit),
        "breach_penalty_finance_category": str(breach_penalty_finance_category or "market_cost"),
    })
    return config


def _build_herding_config(
    product_id: str,
    target_enterprise_ids: List[str],
    true_demand_series: List[float] = None,
    market_heat_series: List[float] = None,
    base_unit_price: float = 420.0,
    unit_cost_reference: float = 210.0,
    customer_delivery_lead_time: int = 1,
    production_lag_rounds: int = 1,
    initial_reference_plan_quantity: float = 70.0,
    peer_visibility_enabled: bool = True,
    peer_visibility_lag_rounds: int = 1,
    market_heat_noise_level: float = 0.0,
    herding_pressure_weight: float = 0.65,
    overproduction_threshold_ratio: float = 1.15,
    target_synchronization_threshold: float = 0.72,
) -> Dict[str, Any]:
    """Construct the experimental configuration of the herd effect."""
    config = deepcopy(DEFAULT_HERDING_CONFIG_TEMPLATE)
    config.update({
        "enabled": True,
        "product_id": product_id,
        "target_enterprise_ids": list(target_enterprise_ids),
        "true_demand_series": list(true_demand_series or config["true_demand_series"]),
        "market_heat_series": list(market_heat_series or config["market_heat_series"]),
        "base_unit_price": float(base_unit_price),
        "unit_cost_reference": float(unit_cost_reference),
        "customer_delivery_lead_time": max(0, int(customer_delivery_lead_time)),
        "production_lag_rounds": max(0, int(production_lag_rounds)),
        "initial_reference_plan_quantity": float(initial_reference_plan_quantity),
        "peer_visibility_enabled": bool(peer_visibility_enabled),
        "peer_visibility_lag_rounds": max(0, int(peer_visibility_lag_rounds)),
        "market_heat_noise_level": max(0.0, float(market_heat_noise_level)),
        "herding_pressure_weight": float(herding_pressure_weight),
        "overproduction_threshold_ratio": float(overproduction_threshold_ratio),
        "target_synchronization_threshold": float(target_synchronization_threshold),
    })
    return config


def _build_herding_experiment_policy(
    enabled: bool = True,
    target_enterprise_ids: List[str] = None,
    target_role_tags: List[str] = None,
    signal_usage: str = "market_expectation_reference_not_command",
    allow_raw_peer_files: bool = False,
) -> Dict[str, Any]:
    """Build the Agent-side herding experiment policy."""
    return {
        "enabled": bool(enabled),
        "target_enterprise_ids": list(target_enterprise_ids or []),
        "target_role_tags": list(target_role_tags or []),
        "signal_usage": signal_usage,
        "allow_raw_peer_files": bool(allow_raw_peer_files),
        "peer_summary_scope": "environment_aggregated_only",
    }


def _build_shared_resource_governance_policy(
    enabled: bool = False,
    mode: str = "none",
    quota_per_enterprise: float = None,
    quota_soft_limit: bool = False,
    over_quota_penalty_per_unit: float = 0.0,
    resource_tax_per_unit: float = 0.0,
    shared_sustainability_target: float = 360.0,
    show_collective_outcome_to_agent: bool = True,
    show_peer_acquisition_to_agent: bool = True,
) -> Dict[str, Any]:
    """Construct a shared resource governance strategy; P0 is registered only for switches and the actual settlement is accessed at a subsequent stage."""
    return {
        "enabled": bool(enabled),
        "mode": mode,
        "quota_per_enterprise": None if quota_per_enterprise is None else float(quota_per_enterprise),
        "quota_soft_limit": bool(quota_soft_limit),
        "over_quota_penalty_per_unit": float(over_quota_penalty_per_unit),
        "resource_tax_per_unit": float(resource_tax_per_unit),
        "shared_sustainability_target": float(shared_sustainability_target),
        "show_collective_outcome_to_agent": bool(show_collective_outcome_to_agent),
        "show_peer_acquisition_to_agent": bool(show_peer_acquisition_to_agent),
    }


def _build_cobweb_enterprise_guidance_policy(
    enabled: bool = True,
    target_enterprise_ids: List[str] = None,
    target_role_tags: List[str] = None,
    non_cobweb_target_priority: str = "suppressed",
    backlog_target_weight: float = 0.15,
    recovery_guard_target_weight: float = 0.15,
    capacity_utilization_target_weight: float = 0.05,
    inventory_target_weight: float = 0.05,
    service_level_target_weight: float = 0.10,
    max_recommended_quantity_deviation: int = 2,
    allow_backlog_only_as_feasibility_signal: bool = True,
    forbid_minimum_batch_targets: bool = True,
    forbid_capacity_activation_targets: bool = True,
) -> Dict[str, Any]:
    """Build cobweb guidance that lowers the priority of production targets unrelated to the cobweb mechanism."""
    return {
        "enabled": bool(enabled),
        "target_enterprise_ids": list(target_enterprise_ids or []),
        "target_role_tags": list(target_role_tags or []),
        "non_cobweb_target_priority": non_cobweb_target_priority,
        "backlog_target_weight": float(backlog_target_weight),
        "recovery_guard_target_weight": float(recovery_guard_target_weight),
        "capacity_utilization_target_weight": float(capacity_utilization_target_weight),
        "inventory_target_weight": float(inventory_target_weight),
        "service_level_target_weight": float(service_level_target_weight),
        "max_recommended_quantity_deviation": int(max_recommended_quantity_deviation),
        "allow_backlog_only_as_feasibility_signal": bool(allow_backlog_only_as_feasibility_signal),
        "forbid_minimum_batch_targets": bool(forbid_minimum_batch_targets),
        "forbid_capacity_activation_targets": bool(forbid_capacity_activation_targets),
        "non_cobweb_target_examples": [
            "clear_confirmed_backlog",
            "reduce_backlog_to_zero",
            "follow_recovery_guard_quantity",
            "activate_idle_capacity",
            "minimum_batch_size_target",
            "increase_fill_rate_by_overproduction",
            "inventory_replenishment_target",
        ],
    }


def _build_safety_stop_policy(
    enabled: bool,
    target_enterprise_ids: List[str],
    min_round_to_evaluate: int,
    consecutive_no_order_rounds: int,
    consecutive_decline_rounds: int,
    economic_metric: str = "net_profit",
    require_negative_metric: bool = True,
    metric_floor: float = 0.0,
) -> Dict[str, Any]:
    """The construction of a model security shutdown strategy to prevent the continuation of a long-term, ineffective trade and deteriorating economy."""
    return {
        "enabled": bool(enabled),
        "target_enterprise_ids": list(target_enterprise_ids),
        "min_round_to_evaluate": max(0, int(min_round_to_evaluate)),
        "consecutive_no_order_rounds": max(1, int(consecutive_no_order_rounds)),
        "consecutive_decline_rounds": max(1, int(consecutive_decline_rounds)),
        "economic_metric": economic_metric,
        "require_negative_metric": bool(require_negative_metric),
        "metric_floor": float(metric_floor),
    }


def _build_staffing_relaxation_policy(
    enabled: bool,
    enterprise_ids: List[str],
    virtual_available_workers: int,
    allow_soft_assignment: bool = True,
    allow_soft_release: bool = True,
    target_role_tags: List[str] = None,
) -> Dict[str, Any]:
    """Construct a strategy to restrain the weak, so that the primary demand chain is not broken by staffing fail."""
    return {
        "enabled": bool(enabled),
        "enterprise_ids": list(enterprise_ids),
        "target_role_tags": list(target_role_tags or []),
        "virtual_available_workers": max(0, int(virtual_available_workers)),
        "allow_soft_assignment": bool(allow_soft_assignment),
        "allow_soft_release": bool(allow_soft_release),
    }


def _build_bullwhip_midstream_pass_through_mode(
    enabled: bool,
    target_enterprise_ids: List[str],
    ignore_stale_backlog_block_for_new_supply: bool = True,
    soften_confirmed_backlog_penalty: bool = True,
    fill_rate_caution_threshold: float = 0.45,
    prefer_service_level_over_margin: bool = True,
    minimum_release_ratio_when_inventory_available: float = 0.35,
    target_role_tags: List[str] = None,
) -> Dict[str, Any]:
    """Construct a second layer of amplification to enhance switches to weaken the middle layer ' s operational cut-off of new supplies."""
    return {
        "enabled": bool(enabled),
        "target_enterprise_ids": list(target_enterprise_ids),
        "target_role_tags": list(target_role_tags or []),
        "ignore_stale_backlog_block_for_new_supply": bool(ignore_stale_backlog_block_for_new_supply),
        "soften_confirmed_backlog_penalty": bool(soften_confirmed_backlog_penalty),
        "fill_rate_caution_threshold": float(fill_rate_caution_threshold),
        "prefer_service_level_over_margin": bool(prefer_service_level_over_margin),
        "minimum_release_ratio_when_inventory_available": float(minimum_release_ratio_when_inventory_available),
    }


def _build_bullwhip_proposal_conversion_acceleration(
    enabled: bool,
    target_enterprise_ids: List[str],
    prefer_accept_when_due_in_future: bool = True,
    pricing_gap_tolerance_multiplier: float = 1.5,
    future_service_commit_threshold: str = "aggressive",
    reduce_rejection_for_inventory_gap_only: bool = True,
    target_role_tags: List[str] = None,
) -> Dict[str, Any]:
    """Construct a proposal turnor order acceleration switch to reduce the future blockage of the fulfillment proposal."""
    return {
        "enabled": bool(enabled),
        "target_enterprise_ids": list(target_enterprise_ids),
        "target_role_tags": list(target_role_tags or []),
        "prefer_accept_when_due_in_future": bool(prefer_accept_when_due_in_future),
        "pricing_gap_tolerance_multiplier": float(pricing_gap_tolerance_multiplier),
        "future_service_commit_threshold": future_service_commit_threshold,
        "reduce_rejection_for_inventory_gap_only": bool(reduce_rejection_for_inventory_gap_only),
    }


def _build_bullwhip_manufacturer_upstream_amplification(
    enabled: bool,
    target_enterprise_ids: List[str],
    proposal_signal_weight_multiplier: float = 4.0,
    recovery_target_quantity_multiplier: float = 1.6,
    bottleneck_quantity_multiplier: float = 1.8,
    replenishment_soft_cap_multiplier: float = 1.4,
    target_role_tags: List[str] = None,
) -> Dict[str, Any]:
    """Constructing the third layer of amplification booster switches to drive Manufacturer to scale up raw material replenishment needs more continuously to Supplier."""
    return {
        "enabled": bool(enabled),
        "target_enterprise_ids": list(target_enterprise_ids),
        "target_role_tags": list(target_role_tags or []),
        "proposal_signal_weight_multiplier": float(proposal_signal_weight_multiplier),
        "recovery_target_quantity_multiplier": float(recovery_target_quantity_multiplier),
        "bottleneck_quantity_multiplier": float(bottleneck_quantity_multiplier),
        "replenishment_soft_cap_multiplier": float(replenishment_soft_cap_multiplier),
    }


def _build_bullwhip_supplier_upstream_pull_through_mode(
    enabled: bool,
    target_enterprise_ids: List[str],
    ignore_inventory_headroom_skip: bool = True,
    package_coverage_target_rounds_multiplier: float = 1.75,
    package_quantity_multiplier: float = 1.35,
    bottleneck_priority_boost_multiplier: float = 1.4,
    package_priority_boost_multiplier: float = 1.6,
    backlog_pull_through_multiplier: float = 1.25,
    target_role_tags: List[str] = None,
) -> Dict[str, Any]:
    """Construct a fourth layer of amplification enhanced switches to drive Supplier to more actively magnify the downstream deficit pressure upstream."""
    return {
        "enabled": bool(enabled),
        "target_enterprise_ids": list(target_enterprise_ids),
        "target_role_tags": list(target_role_tags or []),
        "ignore_inventory_headroom_skip": bool(ignore_inventory_headroom_skip),
        "package_coverage_target_rounds_multiplier": float(package_coverage_target_rounds_multiplier),
        "package_quantity_multiplier": float(package_quantity_multiplier),
        "bottleneck_priority_boost_multiplier": float(bottleneck_priority_boost_multiplier),
        "package_priority_boost_multiplier": float(package_priority_boost_multiplier),
        "backlog_pull_through_multiplier": float(backlog_pull_through_multiplier),
    }


def _build_inventory_cost_exemption_policy(
    enabled: bool,
    target_enterprise_ids: List[str] = None,
    target_role_tags: List[str] = None,
) -> Dict[str, Any]:
    """The cost exemption strategy for constructing inventory holdings."""
    return {
        "enabled": bool(enabled),
        "target_enterprise_ids": list(target_enterprise_ids or []),
        "target_role_tags": list(target_role_tags or []),
    }


def _build_single_enterprise_case_policy(
    case_id: str,
    primary_issue: str,
    expected_primary_departments: List[str],
    preferred_actions: List[str],
    discouraged_actions: List[str],
    handoff_day: int = 0,
    prewarm_rounds: int = 0,
    recommended_total_steps: int = 6,
    diagnostic_evaluation_rounds: List[int] = None,
    evaluation_mode: str = "diagnostic_window",
    source_scene_id: str = None,
    source_primary_contradiction: str = None,
    source_run_mode: str = None,
    scene_goal: str = "",
    evidence_focus: List[str] = None,
    trajectory_success_signals: Dict[str, List[str]] = None,
    target_handoff_signature: Dict[str, Any] = None,
    prewarm_execution_plan: Dict[str, Any] = None,
    deferred_init_actions: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Construct the single-enterprise standard case side description and assessment constraints."""
    return {
        "enabled": True,
        "case_id": case_id,
        "family": "single_enterprise_diagnostic",
        "version": "2.0.0",
        "source_scene_id": source_scene_id,
        "source_primary_contradiction": source_primary_contradiction,
        "source_run_mode": source_run_mode,
        "scene_goal": scene_goal,
        "handoff_day": max(0, int(handoff_day)),
        "prewarm_rounds": max(0, int(prewarm_rounds)),
        "primary_issue": primary_issue,
        "expected_primary_departments": list(expected_primary_departments),
        "preferred_actions": list(preferred_actions),
        "discouraged_actions": list(discouraged_actions),
        "evidence_focus": list(evidence_focus or []),
        "trajectory_success_signals": deepcopy(trajectory_success_signals or {}),
        "target_handoff_signature": deepcopy(target_handoff_signature or {}),
        "prewarm_execution_plan": deepcopy(prewarm_execution_plan or {}),
        "deferred_init_actions": deepcopy(deferred_init_actions or []),
        "recommended_total_steps": max(1, int(recommended_total_steps)),
        "diagnostic_evaluation_rounds": list(diagnostic_evaluation_rounds or [0]),
        "evaluation_mode": evaluation_mode,
        "orchestrator": "single_enterprise",
    }


def _extend_case_demand_series(series: List[float], total_steps: int) -> List[float]:
    """Extend short seed demand series for longer scripted diagnostic runs."""
    values = list(series or [])
    if not values:
        values = [0]
    while len(values) < total_steps:
        values.append(values[-1])
    return values[:total_steps]


def _load_single_enterprise_source_json(
    case_id: str,
    filename: str,
    *,
    required: bool = True,
) -> Any:
    """Read single-enterprise runtime JSON from the Agent command templates."""
    static_case_dir = SINGLE_ENTERPRISE_STATIC_CASE_DIRS.get(case_id)
    if not static_case_dir:
        raise KeyError(f"Unknown single enterprise source case_id: {case_id}")
    path = SINGLE_ENTERPRISE_STATIC_COMMAND_ROOT / static_case_dir / filename
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Missing single-enterprise source file: {path}")
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_single_case_action_name(action_name: str) -> str:
    """Maps the external branch assessment with the action name as the current structural action name."""
    if action_name == "pass":
        return "action_pass"
    if action_name == "expand_capacity":
        return "build_production_line"
    return action_name


def _normalize_single_case_action_item(
    item: Dict[str, Any],
    *,
    enterprise_id: str,
) -> Dict[str, Any]:
    normalized = deepcopy(item)
    normalized.setdefault("executor_id", enterprise_id)
    if normalized.get("executor_id") == "Manufacturer":
        normalized["executor_id"] = enterprise_id
    action = normalized.get("action")
    if isinstance(action, dict):
        action["action_name"] = _normalize_single_case_action_name(
            action.get("action_name")
        )
        normalized["action"] = action
    return normalized


def _single_case_action_name(item: Dict[str, Any]) -> str:
    action = item.get("action")
    if isinstance(action, dict):
        return str(action.get("action_name") or "")
    return str(item.get("action_name") or "")


def _single_case_action_param(item: Dict[str, Any]) -> Dict[str, Any]:
    action = item.get("action")
    if isinstance(action, dict) and isinstance(action.get("action_param"), dict):
        return action["action_param"]
    if isinstance(item.get("action_param"), dict):
        return item["action_param"]
    return {}


def _build_single_case_init_batches_from_source(
    init_actions: List[Dict[str, Any]],
    *,
    enterprise_id: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Disaggregation of the external branch init_action by the current three stages of initialization."""
    phase_1_names = {"initialize_staffing", "set_capacity"}
    phase_2_names = {"add_inventory", "initialize_suppliers"}
    phase_3_names = {
        "set_product_recipe",
        "build_production_line",
        "initialize_production_line",
    }
    batches = {"init_action_1": [], "init_action_2": [], "init_action_3": []}
    for item in init_actions:
        normalized = _normalize_single_case_action_item(item, enterprise_id=enterprise_id)
        action_name = _single_case_action_name(normalized)
        if action_name in phase_1_names:
            batches["init_action_1"].append(normalized)
        elif action_name in phase_2_names:
            batches["init_action_2"].append(normalized)
        elif action_name in phase_3_names:
            batches["init_action_3"].append(normalized)
    return batches


def _build_single_case_deferred_init_actions_from_source(
    init_actions: List[Dict[str, Any]],
    *,
    enterprise_id: str,
) -> List[Dict[str, Any]]:
    """Delays the article-by-article implementation of seed actions that rely on front-line side effects to avoid cross-checking."""
    immediate_names = {
        "initialize_staffing",
        "set_capacity",
        "add_inventory",
        "initialize_suppliers",
        "set_product_recipe",
        "build_production_line",
        "initialize_production_line",
    }
    deferred: List[Dict[str, Any]] = []
    for item in init_actions:
        normalized = _normalize_single_case_action_item(item, enterprise_id=enterprise_id)
        if _single_case_action_name(normalized) not in immediate_names:
            deferred.append(normalized)
    return deferred


def _extract_single_case_seed_manifest(
    init_actions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Extracts the enterprise disk field required by the current configuration centre from the external branch init_action."""
    extracted = {
        "staffing": {},
        "capacity": 0,
        "inventory": [],
        "suppliers": [],
        "recipe": {},
        "production_lines": [],
    }
    for item in init_actions:
        action_name = _single_case_action_name(item)
        action_param = deepcopy(_single_case_action_param(item))
        if action_name == "initialize_staffing":
            extracted["staffing"] = deepcopy(action_param.get("initial_staffing") or {})
        elif action_name == "set_capacity":
            extracted["capacity"] = int(action_param.get("capacity") or 0)
        elif action_name == "add_inventory":
            extracted["inventory"].append(
                {
                    "item_id": action_param.get("item_id"),
                    "quantity": action_param.get("quantity", 0),
                    "purchase_price": action_param.get("purchase_price", 0),
                    "item_type": action_param.get("item_type"),
                }
            )
        elif action_name == "initialize_suppliers":
            extracted["suppliers"] = deepcopy(action_param.get("suppliers_data") or [])
        elif action_name == "set_product_recipe":
            extracted["recipe"] = deepcopy(action_param)
        elif action_name in {"build_production_line", "initialize_production_line"}:
            line_payload = {
                "line_type": action_param.get("line_type") or "small",
            }
            if action_name == "initialize_production_line":
                line_payload["ready_at_start"] = bool(
                    action_param.get("ready_at_start", True)
                )
                if action_param.get("initial_status"):
                    line_payload["initial_status"] = action_param.get("initial_status")
            extracted["production_lines"].append(
                line_payload
            )
    return extracted


def _single_case_expected_primary_departments(
    intervention_gold: Dict[str, Any],
) -> List[str]:
    """Resolve the lead department field used by refreshed single-case seeds."""
    raw_targets = intervention_gold.get("expected_department_targets")
    departments: List[str] = []

    if isinstance(raw_targets, str):
        raw_targets = [raw_targets]
    if isinstance(raw_targets, list):
        for item in raw_targets:
            if isinstance(item, str):
                value = item
            elif isinstance(item, dict):
                value = (
                    item.get("department")
                    or item.get("dept_id")
                    or item.get("department_id")
                    or item.get("name")
                )
            else:
                value = None
            value = str(value or "").strip().lower()
            if value and value not in departments:
                departments.append(value)

    if not departments:
        lead_department = str(intervention_gold.get("lead_department") or "").strip().lower()
        if lead_department:
            departments.append(lead_department)

    return departments


def _build_single_case_prewarm_execution_plan(
    case_id: str,
    *,
    enterprise_id: str,
) -> Dict[str, Any]:
    prewarm = _load_single_enterprise_source_json(
        case_id,
        "executable_prewarm_day0_to_day9.json",
        required=False,
    )
    if not prewarm:
        return {}
    normalized = deepcopy(prewarm)
    normalized["source"] = (
        "agent/static_commands/single_enterprise/"
        f"{SINGLE_ENTERPRISE_STATIC_CASE_DIRS.get(case_id, case_id)}"
    )
    normalized["prewarm_kind"] = "custom_json_actions_not_scripted_decision_policy"
    days = normalized.get("days") or {}
    for day_payload in days.values():
        if not isinstance(day_payload, dict):
            continue
        for department, department_payload in day_payload.items():
            if not isinstance(department_payload, dict):
                continue
            workflow = department_payload.get("workflow") or []
            department_payload["workflow"] = [
                _normalize_single_case_action_item(item, enterprise_id=enterprise_id)
                for item in workflow
                if isinstance(item, dict)
            ]
            for item in department_payload["workflow"]:
                item.setdefault(
                    "module_type",
                    SINGLE_ENTERPRISE_DEPARTMENT_MODULE_TYPES.get(department),
                )
    return normalized


def _build_single_case_manifest_from_source(case_id: str) -> Dict[str, Any]:
    enterprise_id = "Manufacturer"
    runtime_overrides = SINGLE_ENTERPRISE_CASE_RUNTIME_OVERRIDES.get(case_id, {})
    init_actions = _load_single_enterprise_source_json(case_id, "init_action.json")
    intervention_gold = _load_single_enterprise_source_json(case_id, "intervention_gold.json")
    prewarm_goal = _load_single_enterprise_source_json(
        case_id,
        "prewarm_plan_day0_to_day10.json",
        required=False,
    ) or {}
    handoff_signature = _load_single_enterprise_source_json(
        case_id,
        "handoff_signature.json",
        required=False,
    ) or {}
    formal_market_orders = _load_single_enterprise_source_json(
        case_id,
        "formal_market_orders_day10_to_day19.json",
        required=False,
    ) or {}
    supplier_candidates = _load_single_enterprise_source_json(
        case_id,
        "supplier_candidates.json",
        required=False,
    ) or {}
    single_enterprise_rules = deepcopy(
        runtime_overrides.get("single_enterprise_rules") or {}
    )
    single_enterprise_rules.update(
        deepcopy(formal_market_orders.get("single_enterprise_rules") or {})
    )
    seed = _extract_single_case_seed_manifest(init_actions)
    source_primary = intervention_gold.get("primary_contradiction")
    primary_issue = source_primary or "cold_start"
    preferred_actions = [
        _normalize_single_case_action_name(action_name)
        for action_name in (
            (intervention_gold.get("acceptable_primary_actions") or [])
            + (intervention_gold.get("acceptable_secondary_actions") or [])
        )
    ]
    discouraged_actions = [
        _normalize_single_case_action_name(action_name)
        for action_name in (intervention_gold.get("discouraged_actions") or [])
    ]
    recommended_total_days = int(intervention_gold.get("recommended_total_days") or 20)
    evaluation_day = int(intervention_gold.get("evaluation_day") or 0)
    takeover_day = int(intervention_gold.get("takeover_day") or 0)
    run_mode = intervention_gold.get("run_mode")
    prewarm_rounds = (
        takeover_day
        if run_mode in {"prewarm_then_takeover", "trajectory_prewarm_then_takeover"}
        else 0
    )
    diagnostic_rounds = sorted({evaluation_day, max(0, recommended_total_days - 1)})
    manifest = {
        "case_id": case_id,
        "source_scene_id": intervention_gold.get("scene_id"),
        "source_primary_contradiction": source_primary,
        "source_run_mode": run_mode,
        "name": f"Enterprise Case {case_id.split('_')[2]} {intervention_gold.get('scene_name')}",
        "summary": intervention_gold.get("scene_goal") or "",
        "scene_goal": intervention_gold.get("scene_goal") or "",
        "simulation_steps": recommended_total_days,
        "handoff_day": takeover_day,
        "prewarm_rounds": prewarm_rounds,
        "diagnostic_evaluation_rounds": diagnostic_rounds,
        "evaluation_mode": "takeover_window" if prewarm_rounds else "direct_from_day0",
        "initial_capital": int(runtime_overrides.get("initial_capital") or 10000000),
        "staffing": seed["staffing"],
        "capacity": seed["capacity"],
        "inventory": seed["inventory"],
        "suppliers": seed["suppliers"],
        "recipe": seed["recipe"],
        "production_lines": seed["production_lines"],
        "init_action_batches": _build_single_case_init_batches_from_source(
            init_actions,
            enterprise_id=enterprise_id,
        ),
        "deferred_init_actions": _build_single_case_deferred_init_actions_from_source(
            init_actions,
            enterprise_id=enterprise_id,
        ),
        "prewarm_goal": deepcopy(prewarm_goal),
        "prewarm_execution_plan": _build_single_case_prewarm_execution_plan(
            case_id,
            enterprise_id=enterprise_id,
        ),
        "primary_issue": primary_issue,
        "expected_primary_departments": _single_case_expected_primary_departments(
            intervention_gold
        ),
        "preferred_actions": preferred_actions,
        "discouraged_actions": discouraged_actions,
        "evidence_focus": list(intervention_gold.get("evidence_focus") or []),
        "trajectory_success_signals": deepcopy(
            intervention_gold.get("trajectory_success_signals") or {}
        ),
        "target_handoff_signature": deepcopy(
            prewarm_goal.get("target_handoff_signature") or handoff_signature
        ),
        "formal_market_orders": deepcopy(formal_market_orders),
        "supplier_candidates": deepcopy(
            supplier_candidates.get("candidate_suppliers") or []
        ),
        "evaluation_metrics": list(
            formal_market_orders.get("evaluation_metrics")
            or supplier_candidates.get("evaluation_metrics")
            or []
        ),
    }
    if runtime_overrides.get("cash_pressure_profile"):
        manifest["cash_pressure_profile"] = deepcopy(runtime_overrides["cash_pressure_profile"])
    if single_enterprise_rules:
        manifest["single_enterprise_rules"] = single_enterprise_rules
    return manifest


def _single_case_external_supplier() -> Dict[str, Any]:
    return {
        "supplier_name": "Supplier A",
        "supplier_type": "external",
        "materials": {
            "MATERIAL_1": {"unit_price": 10, "quantity": 10000},
        },
        "processing_time": 1,
        "quality_level": "standard",
        "reliability_score": 0.95,
    }


def _single_case_inventory_policy() -> Dict[str, List[Dict[str, float]]]:
    return {
        "Manufacturer": [
            {"item_id": "MATERIAL_1", "reorder_point": 1000, "safety_stock": 400},
            {"item_id": "PRODUCT_1", "reorder_point": 120, "safety_stock": 60},
        ],
    }


def _single_case_defaults() -> Dict[str, Any]:
    return {
        "initial_capital": 10000000,
        "staffing": {
            "finance": 5,
            "hr": 5,
            "procurement": 5,
            "production": 8,
            "sales": 8,
            "inventory": 3,
        },
        "capacity": 50000,
        "inventory": [
            {"item_id": "MATERIAL_1", "quantity": 4000, "purchase_price": 10, "item_type": "raw_material"},
            {"item_id": "PRODUCT_1", "quantity": 6000, "purchase_price": 500, "item_type": "product"},
        ],
        "suppliers": [_single_case_external_supplier()],
        "recipe": {
            "product_id": "PRODUCT_1",
            "raw_materials": {"MATERIAL_1": 2.0},
            "production_time": 1,
            "labor_cost_per_unit": 10,
            "equipment_cost_per_unit": 5,
        },
        "production_lines": [],
        "demand_series": [0],
        "summary": "single-enterprise standard diagnostic baseline to verify whether Agent is able to identify current dominant business constraints.",
        "primary_issue": "balanced_operation",
        "expected_primary_departments": ["sales", "procurement", "production"],
        "preferred_actions": ["action_pass"],
        "discouraged_actions": [],
    }


def _single_enterprise_case_manifest(case_id: str) -> Dict[str, Any]:
    if case_id not in SINGLE_ENTERPRISE_CASE_IDS:
        raise KeyError(f"Unknown single enterprise case_id: {case_id}")
    manifest = deepcopy(_single_case_defaults())
    manifest.update(_build_single_case_manifest_from_source(case_id))
    manifest["case_id"] = case_id
    return manifest


def _build_single_enterprise_case_scenario(
    case_id: str,
    finance_agent_enabled: bool = False,
) -> Dict[str, Any]:
    manifest = _single_enterprise_case_manifest(case_id)
    enterprise_id = "Manufacturer"
    prewarm_plan = manifest.get("prewarm_execution_plan") or {}
    formal_market_orders = manifest.get("formal_market_orders") or {}
    fixed_orders_by_day = deepcopy(formal_market_orders.get("orders_by_day") or {})
    supplier_candidates = deepcopy(manifest.get("supplier_candidates") or [])
    uses_seeded_market_actions = bool(
        prewarm_plan.get("scripted_market_orders")
        or prewarm_plan.get("market_order_mode") == "static_actions"
    )
    post_handoff_market_order_case = case_id in {
        "single_case_01_order_selection",
        "single_case_02_material_shortage",
        "single_case_03_capacity_bottleneck",
        "single_case_04_staff_shortage",
        "single_case_05_cash_pressure",
    }
    post_handoff_external_market_orders_enabled = bool(
        post_handoff_market_order_case
    )
    external_market_orders_enabled = (
        not uses_seeded_market_actions
        or post_handoff_external_market_orders_enabled
    )
    case_demand_series = list(manifest.get("demand_series") or [])
    handoff_day = max(0, int(manifest.get("handoff_day", 0) or 0))
    total_steps = max(1, int(manifest.get("simulation_steps", 6)))
    if post_handoff_external_market_orders_enabled and not any(case_demand_series):
        post_handoff_quantity = max(
            1,
            int(
                (manifest.get("single_enterprise_rules") or {}).get("base_quantity")
                or 20
            ),
        )
        case_demand_series = [
            0 if day < handoff_day else post_handoff_quantity
            for day in range(total_steps)
        ]
    elif external_market_orders_enabled and not any(case_demand_series):
        # The old single-case default was [0], which silently disabled the
        # market mechanism for refreshed case02/case06 seeds. Use the normal
        # demand series so the engine creates real, non-zero market events.
        default_demand_series = globals().get("DEFAULT_BEER_GAME_DEMAND_SERIES")
        if default_demand_series is None:
            default_demand_series = (
                (SCENARIO_CONFIGS.get(DEFAULT_ACTIVE_SCENARIO_ID) or {})
                .get("simulation", {})
                .get("beer_game_demand_series", [])
            )
        case_demand_series = list(default_demand_series)
    if not case_demand_series:
        case_demand_series = [0]
    case_policy = _build_single_enterprise_case_policy(
        case_id=case_id,
        source_scene_id=manifest.get("source_scene_id"),
        source_primary_contradiction=manifest.get("source_primary_contradiction"),
        source_run_mode=manifest.get("source_run_mode"),
        primary_issue=manifest["primary_issue"],
        expected_primary_departments=manifest["expected_primary_departments"],
        preferred_actions=manifest["preferred_actions"],
        discouraged_actions=manifest["discouraged_actions"],
        scene_goal=manifest.get("scene_goal", ""),
        handoff_day=manifest.get("handoff_day", 0),
        prewarm_rounds=manifest.get("prewarm_rounds", 0),
        evidence_focus=manifest.get("evidence_focus", []),
        trajectory_success_signals=manifest.get("trajectory_success_signals", {}),
        target_handoff_signature=manifest.get("target_handoff_signature", {}),
        prewarm_execution_plan=manifest.get("prewarm_execution_plan", {}),
        deferred_init_actions=manifest.get("deferred_init_actions", []),
        recommended_total_steps=manifest.get("simulation_steps", 6),
        diagnostic_evaluation_rounds=manifest.get("diagnostic_evaluation_rounds", [0]),
        evaluation_mode=manifest.get("evaluation_mode", "diagnostic_window"),
    )
    case_policy["evaluation_metrics"] = list(manifest.get("evaluation_metrics") or [])
    if manifest.get("cash_pressure_profile"):
        case_policy["cash_pressure_profile"] = deepcopy(manifest["cash_pressure_profile"])
    enterprise_bundle = _build_scenario_enterprise_bundle(
        initial_capitals={enterprise_id: manifest["initial_capital"]},
        staffing_map={enterprise_id: manifest["staffing"]},
        capacity_map={enterprise_id: manifest["capacity"]},
        inventory_map={enterprise_id: manifest["inventory"]},
        supplier_data_by_enterprise={enterprise_id: manifest.get("suppliers", [])},
        inventory_policy_map=_single_case_inventory_policy(),
        production_recipes_by_enterprise={enterprise_id: manifest["recipe"]},
        initial_production_lines_by_enterprise={
            enterprise_id: manifest["production_lines"]
        },
        include_inventory_cost=False,
        inventory_cost_enterprise_ids=[],
        enterprise_blueprints=[
            {
                **blueprint,
                "id": enterprise_id,
                "name": enterprise_id,
                "enterprise_id": enterprise_id,
                "enterprise_name": enterprise_id,
                "salable_products_idList": ["PRODUCT_1"],
                "purchasable_materials_idList": ["MATERIAL_1"],
                "supplier_name_list": [
                    supplier.get("supplier_name")
                    for supplier in (manifest.get("suppliers") or [])
                    if supplier.get("supplier_name")
                ],
                "agent_departments": _normalize_single_enterprise_agent_departments(
                    blueprint.get("agent_departments", [])
                ),
            }
            for blueprint in _build_single_enterprise_diagnostic_blueprints(
                finance_agent_enabled=finance_agent_enabled
            )
        ],
    )
    enterprise_bundle["initial_action_batches"] = deepcopy(
        manifest.get("init_action_batches")
        or enterprise_bundle.get("initial_action_batches", {})
    )
    return {
        "meta": {
            "scenario_id": case_id,
            "name": manifest["name"],
            "summary": manifest["summary"],
        },
        "simulation": {
            "service_total_steps": total_steps,
            "agent_run_steps": total_steps,
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_BEER_GAME,
            "beer_game_demand_series": _extend_case_demand_series(
                case_demand_series,
                total_steps,
            ),
            "beer_game_customer_delivery_lead_time": 1,
            "beer_game_unit_price": 800,
            "beer_game_product_id": "PRODUCT_1",
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 1,
            "max_department_concurrency": 3,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": enterprise_id,
            "external_market_order_policy": _build_external_market_order_policy(
                enabled=external_market_orders_enabled,
                mode=(
                    "market_bound_fixed_schedule"
                    if fixed_orders_by_day
                    and case_id == "single_case_01_order_selection"
                    else "fixed_schedule"
                    if fixed_orders_by_day
                    else "single_target"
                ),
                target_enterprise_ids=[enterprise_id],
                start_day=handoff_day if post_handoff_market_order_case else None,
                end_day=formal_market_orders.get("end_day") if fixed_orders_by_day else None,
                orders_by_day=fixed_orders_by_day if fixed_orders_by_day else None,
            ),
            "single_enterprise_supplier_selection_policy": {
                "enabled": bool(supplier_candidates),
                "target_enterprise_ids": [enterprise_id] if supplier_candidates else [],
                "selection_mode": "register_on_first_purchase",
                "candidate_suppliers": supplier_candidates,
            },
            "single_enterprise_rule_overrides": deepcopy(
                manifest.get("single_enterprise_rules") or {}
            ),
            "salary_payment_interval_days": 10,
            "salary_payment_enterprise_ids": [enterprise_id],
            "inventory_cost_daily_settlement_enabled": False,
            "inventory_cost_enterprise_ids": [],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=1,
                force_on_round_zero=True,
            ),
            "single_enterprise_case_policy": case_policy,
            "scripted_rule_policy": _build_scripted_rule_policy(
                enabled=False,
                mode="single_case",
                base_quantity=20 if case_id == "single_case_05_cash_pressure" else 80,
                single_enterprise_rules=manifest.get("single_enterprise_rules", {}),
            ),
            "single_enterprise_chart_export_policy": _build_single_enterprise_chart_export_policy(
                enabled=True,
                archive_per_round=True,
                inject_to_analyst=True,
                target_enterprise_ids=[enterprise_id],
            ),
            # S0 single-enterprise cases validate internal diagnostic ability.
            # Do not import the multi-enterprise top-tier credit friction here;
            # seeded procurement should be constrained by ordinary cash and
            # supplier availability rather than by external credit caps.
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=False,
                enterprise_ids=[enterprise_id],
                external_credit_limit=0,
                payable_delay_rounds=0,
                max_single_order_share=1.0,
                external_logistics_cost_multiplier=1.0,
                allow_direct_external_replenishment_without_exchange=False,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=0,
                consecutive_no_order_rounds=99,
                consecutive_decline_rounds=99,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=0,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.75,
                "recruit_ratio": 0.35,
                "min_recruit": 1,
                "max_recruit": 4,
                "severe_utilization_threshold": 0.9,
                "available_worker_floor": 0,
                "failure_recruit_boost": 2,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 20,
                "sales_fill_rate_trigger": 0.9,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.85,
                "inventory_alert_count_trigger": 1,
            },
            "inventory": {
                "expand_trigger_threshold": 0.88,
                "expand_ratio": 0.15,
                "expansion_options": [1000, 2000],
                "min_expansion_if_nonzero": 200,
            },
        },
        "integration_profiles": {
            "simulation": {"profile": "single_enterprise"},
            "capabilities": {
                "analysis": {
                    "profile": "historical_diagnosis",
                    "history_days": 5,
                },
                "coordination": {
                    "blackboard_enabled": True,
                    "two_phase_enabled": False,
                    "communication_validation": "warn",
                    "semantic_rule_pack": "single_enterprise_case_v1",
                    "semantic_auto_repair": False,
                },
                "skill": {
                    "context_validation": "warn",
                    "output_validation": "warn",
                    "max_retries": 3,
                    "audit_enabled": True,
                },
                "departments": {
                    "finance_enabled": bool(finance_agent_enabled),
                },
            },
        },
        "single_enterprise_case": deepcopy(case_policy),
        **enterprise_bundle,
    }


SCENARIO_CONFIGS = {
    "baseline_current": {
        "meta": {
            "scenario_id": "baseline_current",
            "name": 'Historical baseline',
            "summary": 'Preserve historical demand, inventory, warehouse capacity, and automatic policies for replay and comparison.',
        },
        "simulation": {
            "service_total_steps": 10,
            "agent_run_steps": 15,
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_BEER_GAME,
            "beer_game_demand_series": [4, 4, 4, 4, 8, 8, 8, 8, 8, 8],
            "beer_game_customer_delivery_lead_time": 1,
            "beer_game_unit_price": 260,
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 4,
            "max_department_concurrency": 2,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": "Retailer",
            "salary_payment_interval_days": 10,
            "salary_payment_enterprise_ids": ["Manufacturer", "Supplier", "Retailer", "Distributor"],
            "inventory_cost_daily_settlement_enabled": False,
            "inventory_cost_enterprise_ids": [],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=1,
            ),
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=False,
                enterprise_ids=[],
                external_credit_limit=0,
                payable_delay_rounds=0,
                max_single_order_share=1.0,
                external_logistics_cost_multiplier=1.0,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=0,
                consecutive_no_order_rounds=99,
                consecutive_decline_rounds=99,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=0,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.6,
                "recruit_ratio": 0.3,
                "min_recruit": 1,
                "max_recruit": 4,
                "severe_utilization_threshold": 0.9,
                "available_worker_floor": 0,
                "failure_recruit_boost": 2,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 1,
                "sales_fill_rate_trigger": 0.85,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.75,
                "inventory_alert_count_trigger": 2,
            },
            "inventory": {
                "expand_trigger_threshold": 0.7,
                "expand_ratio": 0.3,
                "expansion_options": [1000, 2000, 5000],
                "min_expansion_if_nonzero": 1000,
            },
        },
        **_build_scenario_enterprise_bundle(
            initial_capitals={
                "Supplier": 300000,
                "Manufacturer": 220000,
                "Distributor": 120000,
                "Retailer": 90000,
            },
            staffing_map={
                "Supplier": {"finance": 1, "hr": 1, "procurement": 10, "sales": 8, "inventory": 3},
                "Manufacturer": {"finance": 1, "hr": 2, "procurement": 8, "production": 15, "sales": 10, "inventory": 3},
                "Distributor": {"finance": 1, "hr": 5, "procurement": 15, "sales": 10, "inventory": 3},
                "Retailer": {"finance": 1, "hr": 5, "procurement": 5, "sales": 8, "inventory": 3},
            },
            capacity_map={
                "Supplier": 500000,
                "Manufacturer": 500000,
                "Distributor": 100000,
                "Retailer": 100000,
            },
            inventory_map={
                "Supplier": [
                    {"item_id": "Malt", "quantity": 100000, "purchase_price": 0.4, "item_type": "raw_material"},
                    {"item_id": "Hops", "quantity": 10000, "purchase_price": 2, "item_type": "raw_material"},
                    {"item_id": "Yeast", "quantity": 5000, "purchase_price": 3, "item_type": "raw_material"},
                ],
                "Manufacturer": [
                    {"item_id": "Malt", "quantity": 100000, "purchase_price": 0.4, "item_type": "raw_material"},
                    {"item_id": "Hops", "quantity": 10000, "purchase_price": 2, "item_type": "raw_material"},
                    {"item_id": "Yeast", "quantity": 2500, "purchase_price": 3, "item_type": "raw_material"},
                    {"item_id": "beer", "quantity": 10000, "purchase_price": 110, "item_type": "product"},
                ],
                "Distributor": [
                    {"item_id": "beer", "quantity": 2800, "purchase_price": 140, "item_type": "product"},
                ],
                "Retailer": [
                    {"item_id": "beer", "quantity": 2500, "purchase_price": 180, "item_type": "product"},
                ],
            },
            supplier_data_by_enterprise={
                "Supplier": [
                    _make_external_supplier(
                        {
                            "Malt": {"unit_price": 0.3, "quantity": 1000000, "min_order_quantity": 0},
                            "Hops": {"unit_price": 1.8, "quantity": 1000000, "min_order_quantity": 0},
                            "Yeast": {"unit_price": 2.5, "quantity": 1000000, "min_order_quantity": 0},
                        }
                    )
                ]
            },
            inventory_policy_map={
                "Supplier": [
                    {"item_id": "Malt", "reorder_point": 20000, "safety_stock": 500},
                    {"item_id": "Hops", "reorder_point": 2000, "safety_stock": 200},
                    {"item_id": "Yeast", "reorder_point": 1000, "safety_stock": 100},
                ],
                "Manufacturer": [
                    {"item_id": "Malt", "reorder_point": 18000, "safety_stock": 500},
                    {"item_id": "Hops", "reorder_point": 1800, "safety_stock": 150},
                    {"item_id": "Yeast", "reorder_point": 900, "safety_stock": 100},
                    {"item_id": "beer", "reorder_point": 1200, "safety_stock": 300},
                ],
                "Distributor": [
                    {"item_id": "beer", "reorder_point": 600, "safety_stock": 150},
                ],
                "Retailer": [
                    {"item_id": "beer", "reorder_point": 500, "safety_stock": 120},
                ],
            },
            production_recipes_by_enterprise={"Manufacturer": DEFAULT_BEER_RECIPE},
            initial_production_lines_by_enterprise={"Manufacturer": [{"line_type": "small"}]},
            include_inventory_cost=False,
            inventory_cost_enterprise_ids=[],
        ),
    },
    "baseline_rebalanced": {
        "meta": {
            "scenario_id": "baseline_rebalanced",
            "name": 'Rebalanced baseline',
            "summary": 'Reduce inventory and warehouse capacity, increase demand, and tighten automatic replenishment thresholds to expose replenishment needs and shortage signals.',
        },
        "simulation": {
            "service_total_steps": 20,
            "agent_run_steps": 20,
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_BEER_GAME,
            "beer_game_demand_series": [40, 40, 40, 40, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80 ,80, 80, 80],
            "beer_game_customer_delivery_lead_time": 1,
            "beer_game_unit_price": 280,
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 4,
            "max_department_concurrency": 2,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": "Retailer",
            "salary_payment_interval_days": 15,
            "salary_payment_enterprise_ids": ["Manufacturer", "Supplier", "Retailer", "Distributor"],
            "inventory_cost_daily_settlement_enabled": True,
            "inventory_cost_enterprise_ids": ["Manufacturer", "Supplier", "Retailer", "Distributor"],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=3,
            ),
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=True,
                enterprise_ids=["Supplier"],
                external_credit_limit=120000,
                payable_delay_rounds=3,
                max_single_order_share=0.4,
                external_logistics_cost_multiplier=0.35,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=0,
                consecutive_no_order_rounds=99,
                consecutive_decline_rounds=99,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=0,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.85,
                "recruit_ratio": 0.12,
                "min_recruit": 1,
                "max_recruit": 3,
                "severe_utilization_threshold": 0.95,
                "available_worker_floor": 0,
                "failure_recruit_boost": 1,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 40,
                "sales_fill_rate_trigger": 0.9,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.85,
                "inventory_alert_count_trigger": 2,
            },
            "inventory": {
                "expand_trigger_threshold": 0.88,
                "expand_ratio": 0.12,
                "expansion_options": [1000, 2000, 5000],
                "min_expansion_if_nonzero": 200,
            },
        },
        **_build_scenario_enterprise_bundle(
            initial_capitals={
                "Supplier": 150000,
                "Manufacturer": 180000,
                "Distributor": 100000,
                "Retailer": 80000,
            },
            staffing_map={
                "Supplier": {"finance": 1, "hr": 1, "procurement": 3, "sales": 3, "inventory": 2},
                "Manufacturer": {"finance": 1, "hr": 1, "procurement": 3, "production": 5, "sales": 3, "inventory": 2},
                "Distributor": {"finance": 1, "hr": 1, "procurement": 3, "sales": 3, "inventory": 2},
                "Retailer": {"finance": 1, "hr": 1, "procurement": 2, "sales": 3, "inventory": 2},
            },
            capacity_map={
                "Supplier": 22000,
                "Manufacturer": 18000,
                "Distributor": 500,
                "Retailer": 400,
            },
            inventory_map={
                "Supplier": [
                    {"item_id": "Malt", "quantity": 15000, "purchase_price": 0.4, "item_type": "raw_material"},
                    {"item_id": "Hops", "quantity": 1500, "purchase_price": 2, "item_type": "raw_material"},
                    {"item_id": "Yeast", "quantity": 750, "purchase_price": 3, "item_type": "raw_material"},
                ],
                "Manufacturer": [
                    {"item_id": "Malt", "quantity": 12000, "purchase_price": 0.4, "item_type": "raw_material"},
                    {"item_id": "Hops", "quantity": 1200, "purchase_price": 2, "item_type": "raw_material"},
                    {"item_id": "Yeast", "quantity": 600, "purchase_price": 3, "item_type": "raw_material"},
                    {"item_id": "beer", "quantity": 160, "purchase_price": 110, "item_type": "product"},
                ],
                "Distributor": [
                    {"item_id": "beer", "quantity": 160, "purchase_price": 140, "item_type": "product"},
                ],
                "Retailer": [
                    {"item_id": "beer", "quantity": 120, "purchase_price": 180, "item_type": "product"},
                ],
            },
            supplier_data_by_enterprise={
                "Supplier": [
                    _make_external_supplier(
                        {
                            "Malt": {
                                "unit_price": 0.3,
                                "quantity": 30000,
                                "min_order_quantity": 3000,
                            },
                            "Hops": {
                                "unit_price": 1.8,
                                "quantity": 3000,
                                "min_order_quantity": 300,
                            },
                            "Yeast": {
                                "unit_price": 2.5,
                                "quantity": 1500,
                                "min_order_quantity": 150,
                            },
                        }
                    )
                ]
            },
            inventory_policy_map={
                "Supplier": [
                    {"item_id": "Malt", "reorder_point": 18000, "safety_stock": 6000},
                    {"item_id": "Hops", "reorder_point": 1800, "safety_stock": 600},
                    {"item_id": "Yeast", "reorder_point": 900, "safety_stock": 300},
                ],
                "Manufacturer": [
                    {"item_id": "Malt", "reorder_point": 15000, "safety_stock": 7000},
                    {"item_id": "Hops", "reorder_point": 1500, "safety_stock": 700},
                    {"item_id": "Yeast", "reorder_point": 750, "safety_stock": 350},
                    {"item_id": "beer", "reorder_point": 220, "safety_stock": 140},
                ],
                "Distributor": [
                    {"item_id": "beer", "reorder_point": 190, "safety_stock": 120},
                ],
                "Retailer": [
                    {"item_id": "beer", "reorder_point": 140, "safety_stock": 80},
                ],
            },
            production_recipes_by_enterprise={"Manufacturer": DEFAULT_BEER_RECIPE},
            initial_production_lines_by_enterprise={"Manufacturer": [{"line_type": "small"}]},
            include_inventory_cost=True,
            inventory_cost_enterprise_ids=["Manufacturer", "Supplier", "Retailer", "Distributor"],
        ),
    },
    "beer_game": {
        "meta": {
            "scenario_id": "beer_game",
            "name": "Beer Game: Bullwhip Effect",
            "summary": "Increase upstream supplier capacity relative to the rebalanced baseline to study how enterprise operating decisions shape demand amplification beyond startup constraints.",
        },
        "simulation": {
            "service_total_steps": 20,
            "agent_run_steps": 20,
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_BEER_GAME,
            "beer_game_demand_series": [40, 40, 40, 40, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80 ,80, 80, 80],
            "beer_game_customer_delivery_lead_time": 1,
            "beer_game_unit_price": 280,
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 4,
            "max_department_concurrency": 2,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": "Retailer",
            "salary_payment_interval_days": 15,
            "salary_payment_enterprise_ids": ["Manufacturer", "Supplier", "Retailer", "Distributor"],
            "inventory_cost_daily_settlement_enabled": True,
            "inventory_cost_enterprise_ids": ["Manufacturer", "Supplier", "Retailer", "Distributor"],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=3,
            ),
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=False,
                enterprise_ids=[],
                external_credit_limit=180000,
                payable_delay_rounds=4,
                max_single_order_share=0.45,
                external_logistics_cost_multiplier=0.10,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=8,
                consecutive_no_order_rounds=4,
                consecutive_decline_rounds=3,
                economic_metric="net_profit",
                require_negative_metric=True,
                metric_floor=0.0,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=500,
                allow_soft_assignment=True,
                allow_soft_release=True,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
                ignore_stale_backlog_block_for_new_supply=True,
                soften_confirmed_backlog_penalty=True,
                fill_rate_caution_threshold=0.45,
                prefer_service_level_over_margin=True,
                minimum_release_ratio_when_inventory_available=0.35,
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
                prefer_accept_when_due_in_future=True,
                pricing_gap_tolerance_multiplier=1.5,
                future_service_commit_threshold="aggressive",
                reduce_rejection_for_inventory_gap_only=True,
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
                proposal_signal_weight_multiplier=8.0,
                recovery_target_quantity_multiplier=2.2,
                bottleneck_quantity_multiplier=2.6,
                replenishment_soft_cap_multiplier=2.0,
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
                ignore_inventory_headroom_skip=True,
                package_coverage_target_rounds_multiplier=1.75,
                package_quantity_multiplier=1.35,
                bottleneck_priority_boost_multiplier=1.4,
                package_priority_boost_multiplier=1.6,
                backlog_pull_through_multiplier=1.25,
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.85,
                "recruit_ratio": 0.12,
                "min_recruit": 1,
                "max_recruit": 3,
                "severe_utilization_threshold": 0.95,
                "available_worker_floor": 0,
                "failure_recruit_boost": 1,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 40,
                "sales_fill_rate_trigger": 0.9,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.85,
                "inventory_alert_count_trigger": 2,
            },
            "inventory": {
                "expand_trigger_threshold": 0.88,
                "expand_ratio": 0.12,
                "expansion_options": [1000, 2000, 5000],
                "min_expansion_if_nonzero": 200,
            },
        },
        **_build_scenario_enterprise_bundle(
            initial_capitals={
                "Supplier": 180000,
                "Manufacturer": 180000,
                "Distributor": 100000,
                "Retailer": 80000,
            },
            staffing_map={
                "Supplier": {"finance": 1, "hr": 1, "procurement": 4, "sales": 4, "inventory": 2},
                "Manufacturer": {"finance": 1, "hr": 1, "procurement": 4, "production": 8, "sales": 4, "inventory": 2},
                "Distributor": {"finance": 1, "hr": 1, "procurement": 4, "sales": 4, "inventory": 2},
                "Retailer": {"finance": 1, "hr": 1, "procurement": 3, "sales": 4, "inventory": 2},
            },
            capacity_map={
                "Supplier": 22000,
                "Manufacturer": 18000,
                "Distributor": 500,
                "Retailer": 400,
            },
            inventory_map={
                "Supplier": [
                    {"item_id": "Malt", "quantity": 20000, "purchase_price": 0.4, "item_type": "raw_material"},
                    {"item_id": "Hops", "quantity": 2000, "purchase_price": 2, "item_type": "raw_material"},
                    {"item_id": "Yeast", "quantity": 1000, "purchase_price": 3, "item_type": "raw_material"},
                ],
                "Manufacturer": [
                    {"item_id": "Malt", "quantity": 12000, "purchase_price": 0.4, "item_type": "raw_material"},
                    {"item_id": "Hops", "quantity": 1200, "purchase_price": 2, "item_type": "raw_material"},
                    {"item_id": "Yeast", "quantity": 600, "purchase_price": 3, "item_type": "raw_material"},
                    {"item_id": "beer", "quantity": 160, "purchase_price": 110, "item_type": "product"},
                ],
                "Distributor": [
                    {"item_id": "beer", "quantity": 160, "purchase_price": 140, "item_type": "product"},
                ],
                "Retailer": [
                    {"item_id": "beer", "quantity": 120, "purchase_price": 180, "item_type": "product"},
                ],
            },
            supplier_data_by_enterprise={
                "Supplier": [
                    _make_external_supplier(
                        {
                            "Malt": {
                                "unit_price": 0.3,
                                "quantity": 120000,
                                "min_order_quantity": 3000,
                            },
                            "Hops": {
                                "unit_price": 1.8,
                                "quantity": 12000,
                                "min_order_quantity": 300,
                            },
                            "Yeast": {
                                "unit_price": 2.5,
                                "quantity": 6000,
                                "min_order_quantity": 150,
                            },
                        }
                    )
                ]
            },
            inventory_policy_map={
                "Supplier": [
                    {"item_id": "Malt", "reorder_point": 18000, "safety_stock": 6000},
                    {"item_id": "Hops", "reorder_point": 1800, "safety_stock": 600},
                    {"item_id": "Yeast", "reorder_point": 900, "safety_stock": 300},
                ],
                "Manufacturer": [
                    {"item_id": "Malt", "reorder_point": 15000, "safety_stock": 7000},
                    {"item_id": "Hops", "reorder_point": 1500, "safety_stock": 700},
                    {"item_id": "Yeast", "reorder_point": 750, "safety_stock": 350},
                    {"item_id": "beer", "reorder_point": 180, "safety_stock": 100},
                ],
                "Distributor": [
                    {"item_id": "beer", "reorder_point": 190, "safety_stock": 120},
                ],
                "Retailer": [
                    {"item_id": "beer", "reorder_point": 140, "safety_stock": 80},
                ],
            },
            production_recipes_by_enterprise={"Manufacturer": DEFAULT_BEER_RECIPE},
            initial_production_lines_by_enterprise={
                "Manufacturer": [
                    {"line_type": "small"},
                    {"line_type": "small"},
                ]
            },
            include_inventory_cost=True,
            inventory_cost_enterprise_ids=["Manufacturer", "Supplier", "Retailer", "Distributor"],
        ),
    },
    "beer_game_direct_distribution_experiment": {
        "meta": {
            "scenario_id": "beer_game_direct_distribution_experiment",
            "name": 'Beer Game: Direct Distribution Chain',
            "summary": 'Use a four-tier distribution chain with external customer demand and near-unlimited upstream inventory to study demand amplification without supplier and manufacturer production stages.',
        },
        "simulation": {
            "service_total_steps": 20,
            "agent_run_steps": 20,
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_BEER_GAME,
            "beer_game_demand_series": [40, 40, 40, 40, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80],
            "beer_game_customer_delivery_lead_time": 1,
            "beer_game_unit_price": 280,
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 4,
            "max_department_concurrency": 2,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": "Retail",
            "salary_payment_interval_days": 15,
            "salary_payment_enterprise_ids": ["TopDistributor", "RegionalDistributor", "LocalDistributor", "Retail"],
            "inventory_cost_daily_settlement_enabled": True,
            "inventory_cost_enterprise_ids": ["TopDistributor", "RegionalDistributor", "LocalDistributor", "Retail"],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=True,
                target_enterprise_ids=["TopDistributor"],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=3,
            ),
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=False,
                enterprise_ids=[],
                external_credit_limit=180000,
                payable_delay_rounds=4,
                max_single_order_share=0.45,
                external_logistics_cost_multiplier=0.10,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=8,
                consecutive_no_order_rounds=4,
                consecutive_decline_rounds=3,
                economic_metric="net_profit",
                require_negative_metric=True,
                metric_floor=0.0,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=500,
                allow_soft_assignment=True,
                allow_soft_release=True,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
                ignore_stale_backlog_block_for_new_supply=True,
                soften_confirmed_backlog_penalty=True,
                fill_rate_caution_threshold=0.45,
                prefer_service_level_over_margin=True,
                minimum_release_ratio_when_inventory_available=0.35,
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
                prefer_accept_when_due_in_future=True,
                pricing_gap_tolerance_multiplier=1.5,
                future_service_commit_threshold="aggressive",
                reduce_rejection_for_inventory_gap_only=True,
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
                proposal_signal_weight_multiplier=8.0,
                recovery_target_quantity_multiplier=2.2,
                bottleneck_quantity_multiplier=2.6,
                replenishment_soft_cap_multiplier=2.0,
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
                ignore_inventory_headroom_skip=True,
                package_coverage_target_rounds_multiplier=1.75,
                package_quantity_multiplier=1.35,
                bottleneck_priority_boost_multiplier=1.4,
                package_priority_boost_multiplier=1.6,
                backlog_pull_through_multiplier=1.25,
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.85,
                "recruit_ratio": 0.12,
                "min_recruit": 1,
                "max_recruit": 3,
                "severe_utilization_threshold": 0.95,
                "available_worker_floor": 0,
                "failure_recruit_boost": 1,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 40,
                "sales_fill_rate_trigger": 0.9,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.85,
                "inventory_alert_count_trigger": 2,
            },
            "inventory": {
                "expand_trigger_threshold": 0.88,
                "expand_ratio": 0.12,
                "expansion_options": [1000, 2000, 5000],
                "min_expansion_if_nonzero": 200,
            },
        },
        **_build_scenario_enterprise_bundle(
            initial_capitals={
                "TopDistributor": 200000,
                "RegionalDistributor": 140000,
                "LocalDistributor": 110000,
                "Retail": 90000,
            },
            staffing_map={
                "TopDistributor": {"finance": 1, "hr": 1, "sales": 3, "inventory": 2},
                "RegionalDistributor": {"finance": 1, "hr": 1, "procurement": 3, "sales": 3, "inventory": 2},
                "LocalDistributor": {"finance": 1, "hr": 1, "procurement": 3, "sales": 3, "inventory": 2},
                "Retail": {"finance": 1, "hr": 1, "procurement": 2, "sales": 3, "inventory": 2},
            },
            capacity_map={
                "TopDistributor": 2000000,
                "RegionalDistributor": 2500,
                "LocalDistributor": 1800,
                "Retail": 1200,
            },
            inventory_map={
                "TopDistributor": [
                    {"item_id": "beer", "quantity": 1000000, "purchase_price": 80, "item_type": "product"},
                ],
                "RegionalDistributor": [
                    {"item_id": "beer", "quantity": 400, "purchase_price": 110, "item_type": "product"},
                ],
                "LocalDistributor": [
                    {"item_id": "beer", "quantity": 240, "purchase_price": 145, "item_type": "product"},
                ],
                "Retail": [
                    {"item_id": "beer", "quantity": 120, "purchase_price": 180, "item_type": "product"},
                ],
            },
            supplier_data_by_enterprise={},
            inventory_policy_map={
                "TopDistributor": [
                    {"item_id": "beer", "reorder_point": 0, "safety_stock": 0},
                ],
                "RegionalDistributor": [
                    {"item_id": "beer", "reorder_point": 1000, "safety_stock": 400},
                ],
                "LocalDistributor": [
                    {"item_id": "beer", "reorder_point": 700, "safety_stock": 260},
                ],
                "Retail": [
                    {"item_id": "beer", "reorder_point": 160, "safety_stock": 90},
                ],
            },
            include_inventory_cost=True,
            inventory_cost_enterprise_ids=["TopDistributor", "RegionalDistributor", "LocalDistributor", "Retail"],
            enterprise_blueprints=DIRECT_DISTRIBUTION_EXPERIMENT_BLUEPRINTS,
        ),
    },
    "credit_constraint_experiment": {
        "meta": {
            "scenario_id": "credit_constraint_experiment",
            "name": 'Cash and Credit Constraint Propagation',
            "summary": 'Retain the beer-game supply chain and tighten initial cash, upstream credit, and inventory buffers to observe effects on replenishment, fulfillment, backlog, and profit.',
        },
        "simulation": {
            "service_total_steps": 20,
            "agent_run_steps": 20,
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_BEER_GAME,
            "beer_game_demand_series": [40, 40, 40, 40, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80, 80],
            "beer_game_customer_delivery_lead_time": 1,
            "beer_game_unit_price": 280,
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 4,
            "max_department_concurrency": 2,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": "Retailer",
            "salary_payment_interval_days": 10,
            "salary_payment_enterprise_ids": ["Manufacturer", "Supplier", "Retailer", "Distributor"],
            "inventory_cost_daily_settlement_enabled": True,
            "inventory_cost_enterprise_ids": ["Manufacturer", "Supplier", "Retailer", "Distributor"],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=3,
            ),
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=True,
                enterprise_ids=["Supplier"],
                external_credit_limit=70000,
                payable_delay_rounds=2,
                max_single_order_share=0.22,
                external_logistics_cost_multiplier=0.10,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=8,
                consecutive_no_order_rounds=4,
                consecutive_decline_rounds=3,
                economic_metric="net_profit",
                require_negative_metric=True,
                metric_floor=0.0,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=500,
                allow_soft_assignment=True,
                allow_soft_release=True,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.85,
                "recruit_ratio": 0.12,
                "min_recruit": 1,
                "max_recruit": 3,
                "severe_utilization_threshold": 0.95,
                "available_worker_floor": 0,
                "failure_recruit_boost": 1,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 40,
                "sales_fill_rate_trigger": 0.9,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.85,
                "inventory_alert_count_trigger": 2,
            },
            "inventory": {
                "expand_trigger_threshold": 0.88,
                "expand_ratio": 0.12,
                "expansion_options": [1000, 2000, 5000],
                "min_expansion_if_nonzero": 200,
            },
        },
        **_build_scenario_enterprise_bundle(
            initial_capitals={
                "Supplier": 70000,
                "Manufacturer": 90000,
                "Distributor": 65000,
                "Retailer": 50000,
            },
            staffing_map={
                "Supplier": {"finance": 1, "hr": 1, "procurement": 3, "sales": 3, "inventory": 2},
                "Manufacturer": {"finance": 1, "hr": 1, "procurement": 3, "production": 5, "sales": 3, "inventory": 2},
                "Distributor": {"finance": 1, "hr": 1, "procurement": 3, "sales": 3, "inventory": 2},
                "Retailer": {"finance": 1, "hr": 1, "procurement": 2, "sales": 3, "inventory": 2},
            },
            capacity_map={
                "Supplier": 18000,
                "Manufacturer": 15000,
                "Distributor": 450,
                "Retailer": 350,
            },
            inventory_map={
                "Supplier": [
                    {"item_id": "Malt", "quantity": 14000, "purchase_price": 0.4, "item_type": "raw_material"},
                    {"item_id": "Hops", "quantity": 1400, "purchase_price": 2, "item_type": "raw_material"},
                    {"item_id": "Yeast", "quantity": 700, "purchase_price": 3, "item_type": "raw_material"},
                ],
                "Manufacturer": [
                    {"item_id": "Malt", "quantity": 9000, "purchase_price": 0.4, "item_type": "raw_material"},
                    {"item_id": "Hops", "quantity": 900, "purchase_price": 2, "item_type": "raw_material"},
                    {"item_id": "Yeast", "quantity": 450, "purchase_price": 3, "item_type": "raw_material"},
                    {"item_id": "beer", "quantity": 100, "purchase_price": 110, "item_type": "product"},
                ],
                "Distributor": [
                    {"item_id": "beer", "quantity": 120, "purchase_price": 140, "item_type": "product"},
                ],
                "Retailer": [
                    {"item_id": "beer", "quantity": 90, "purchase_price": 180, "item_type": "product"},
                ],
            },
            supplier_data_by_enterprise={
                "Supplier": [
                    _make_external_supplier(
                        {
                            "Malt": {
                                "unit_price": 0.3,
                                "quantity": 80000,
                                "min_order_quantity": 3000,
                            },
                            "Hops": {
                                "unit_price": 1.8,
                                "quantity": 8000,
                                "min_order_quantity": 300,
                            },
                            "Yeast": {
                                "unit_price": 2.5,
                                "quantity": 4000,
                                "min_order_quantity": 150,
                            },
                        }
                    )
                ]
            },
            inventory_policy_map={
                "Supplier": [
                    {"item_id": "Malt", "reorder_point": 16000, "safety_stock": 5000},
                    {"item_id": "Hops", "reorder_point": 1600, "safety_stock": 500},
                    {"item_id": "Yeast", "reorder_point": 800, "safety_stock": 250},
                ],
                "Manufacturer": [
                    {"item_id": "Malt", "reorder_point": 12000, "safety_stock": 5000},
                    {"item_id": "Hops", "reorder_point": 1200, "safety_stock": 500},
                    {"item_id": "Yeast", "reorder_point": 600, "safety_stock": 250},
                    {"item_id": "beer", "reorder_point": 200, "safety_stock": 120},
                ],
                "Distributor": [
                    {"item_id": "beer", "reorder_point": 180, "safety_stock": 110},
                ],
                "Retailer": [
                    {"item_id": "beer", "reorder_point": 150, "safety_stock": 90},
                ],
            },
            production_recipes_by_enterprise={"Manufacturer": DEFAULT_BEER_RECIPE},
            initial_production_lines_by_enterprise={
                "Manufacturer": [
                    {"line_type": "small"},
                ]
            },
            include_inventory_cost=True,
            inventory_cost_enterprise_ids=["Manufacturer", "Supplier", "Retailer", "Distributor"],
        ),
    },
}


def _build_cobweb_config(
    stability_label: str,
    initial_price: float,
    demand_intercept: float,
    demand_slope: float,
    supply_intercept: float,
    supply_slope: float,
    production_lag_rounds: int = 1,
    price_floor: float = 1.0,
    price_ceiling: float = 1000.0,
    quantity_floor: float = 1.0,
    quantity_ceiling: float = 1000.0,
    customer_delivery_lead_time: int = 1,
    product_id: str = "beer",
    production_response_mode: str = "agent_endogenous",
    endogenous_supply_source: str = "production_plan_created",
    endogenous_supply_lag_rounds: int = 1,
    endogenous_supply_fallback: str = "theoretical_lagged_supply",
    decision_profile: str = "mechanism_primary",
    agent_history_window_rounds: int = 8,
) -> Dict[str, Any]:
    config = deepcopy(DEFAULT_COBWEB_CONFIG_TEMPLATE)
    config.update({
        "enabled": True,
        "product_id": product_id,
        "initial_price": float(initial_price),
        "demand_intercept": float(demand_intercept),
        "demand_slope": float(demand_slope),
        "supply_intercept": float(supply_intercept),
        "supply_slope": float(supply_slope),
        "production_lag_rounds": max(1, int(production_lag_rounds)),
        "price_floor": float(price_floor),
        "price_ceiling": float(price_ceiling),
        "quantity_floor": float(quantity_floor),
        "quantity_ceiling": float(quantity_ceiling),
        "customer_delivery_lead_time": max(0, int(customer_delivery_lead_time)),
        "stability_label": stability_label,
        "production_response_mode": production_response_mode,
        "endogenous_supply_source": endogenous_supply_source,
        "endogenous_supply_lag_rounds": max(0, int(endogenous_supply_lag_rounds)),
        "endogenous_supply_fallback": endogenous_supply_fallback,
        "decision_profile": str(decision_profile or "mechanism_primary"),
        "agent_history_window_rounds": max(
            3,
            min(12, int(agent_history_window_rounds or 8)),
        ),
    })
    return config


def _build_cobweb_clean_enterprise_bundle() -> Dict[str, Any]:
    """Cleaning enterprise side of the web model: single producers connected directly to external markets."""
    return _build_scenario_enterprise_bundle(
        initial_capitals={
            "Manufacturer": 260000,
        },
        staffing_map={
            "Manufacturer": {"finance": 1, "hr": 1, "production": 10, "sales": 4, "inventory": 2},
        },
        capacity_map={
            "Manufacturer": 1200000,
        },
        inventory_map={
            "Manufacturer": [
                {"item_id": "Malt", "quantity": 900000, "purchase_price": 0.4, "item_type": "raw_material"},
                {"item_id": "Hops", "quantity": 90000, "purchase_price": 2, "item_type": "raw_material"},
                {"item_id": "Yeast", "quantity": 45000, "purchase_price": 3, "item_type": "raw_material"},
                {"item_id": "beer", "quantity": 40, "purchase_price": 110, "item_type": "product"},
            ],
        },
        supplier_data_by_enterprise={},
        inventory_policy_map={
            "Manufacturer": [
                {"item_id": "Malt", "reorder_point": 0, "safety_stock": 0},
                {"item_id": "Hops", "reorder_point": 0, "safety_stock": 0},
                {"item_id": "Yeast", "reorder_point": 0, "safety_stock": 0},
                {"item_id": "beer", "reorder_point": 20, "safety_stock": 10},
            ],
        },
        production_recipes_by_enterprise={"Manufacturer": DEFAULT_BEER_RECIPE},
        initial_production_lines_by_enterprise={
            "Manufacturer": [
                {"line_type": "small", "ready_at_start": True},
                {"line_type": "small", "ready_at_start": True},
                {"line_type": "small", "ready_at_start": True},
            ]
        },
        include_inventory_cost=False,
        inventory_cost_enterprise_ids=[],
        enterprise_blueprints=COBWEB_CLEAN_ENTERPRISE_BLUEPRINTS,
    )


def _build_cobweb_scenario(
    scenario_id: str,
    name: str,
    summary: str,
    cobweb_config: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "meta": {
            "scenario_id": scenario_id,
            "name": name,
            "summary": summary,
        },
        "simulation": {
            "service_total_steps": 24,
            "agent_run_steps": 24,
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_COBWEB,
            "beer_game_demand_series": [60] * 24,
            "beer_game_customer_delivery_lead_time": 1,
            "beer_game_unit_price": 280,
            "cobweb_config": cobweb_config,
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 4,
            "max_department_concurrency": 2,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": "Manufacturer",
            "salary_payment_interval_days": 12,
            "salary_payment_enterprise_ids": ["Manufacturer"],
            "inventory_cost_daily_settlement_enabled": False,
            "inventory_cost_enterprise_ids": [],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=2,
            ),
            "cobweb_enterprise_guidance_policy": _build_cobweb_enterprise_guidance_policy(
                enabled=True,
                target_enterprise_ids=["Manufacturer"],
                non_cobweb_target_priority="suppressed",
                backlog_target_weight=0.10,
                recovery_guard_target_weight=0.10,
                capacity_utilization_target_weight=0.02,
                inventory_target_weight=0.05,
                service_level_target_weight=0.08,
                max_recommended_quantity_deviation=2,
                allow_backlog_only_as_feasibility_signal=True,
                forbid_minimum_batch_targets=True,
                forbid_capacity_activation_targets=True,
            ),
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=False,
                enterprise_ids=[],
                external_credit_limit=0,
                payable_delay_rounds=0,
                max_single_order_share=1.0,
                external_logistics_cost_multiplier=0.10,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=0,
                consecutive_no_order_rounds=99,
                consecutive_decline_rounds=99,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=0,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.85,
                "recruit_ratio": 0.12,
                "min_recruit": 1,
                "max_recruit": 3,
                "severe_utilization_threshold": 0.95,
                "available_worker_floor": 0,
                "failure_recruit_boost": 1,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 40,
                "sales_fill_rate_trigger": 0.9,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.85,
                "inventory_alert_count_trigger": 2,
            },
            "inventory": {
                "expand_trigger_threshold": 0.9,
                "expand_ratio": 0.1,
                "expansion_options": [1000, 2000, 5000],
                "min_expansion_if_nonzero": 200,
            },
        },
        **_build_cobweb_clean_enterprise_bundle(),
    }


def _build_commons_mining_enterprise_bundle(
    strategy_profiles_by_enterprise: Dict[str, Dict[str, Any]] = None,
    default_strategy_profile: Dict[str, Any] = None,
    initial_capital: int = 220000,
) -> Dict[str, Any]:
    """A parallel enterprise set of scenarios for shared resource areas is constructed; the first edition does not introduce complex procurement chains."""
    miner_ids = list(COMMONS_MINING_ENTERPRISE_IDS)
    return _build_scenario_enterprise_bundle(
        initial_capitals={enterprise_id: int(initial_capital) for enterprise_id in miner_ids},
        staffing_map={
            enterprise_id: {"finance": 1, "hr": 1, "production": 8, "sales": 3, "inventory": 2}
            for enterprise_id in miner_ids
        },
        capacity_map={enterprise_id: 100000 for enterprise_id in miner_ids},
        inventory_map={
            enterprise_id: [
                {"item_id": "copper_ore", "quantity": 10, "purchase_price": 120, "item_type": "product"},
            ]
            for enterprise_id in miner_ids
        },
        supplier_data_by_enterprise={},
        inventory_policy_map={
            enterprise_id: [
                {"item_id": "copper_ore", "reorder_point": 0, "safety_stock": 0},
            ]
            for enterprise_id in miner_ids
        },
        production_recipes_by_enterprise={
            enterprise_id: DEFAULT_COPPER_ORE_RECIPE
            for enterprise_id in miner_ids
        },
        initial_production_lines_by_enterprise={
            enterprise_id: [
                {"line_type": "small", "ready_at_start": True},
            ]
            for enterprise_id in miner_ids
        },
        include_inventory_cost=False,
        inventory_cost_enterprise_ids=[],
        enterprise_blueprints=_build_commons_mining_enterprise_blueprints(
            strategy_profiles_by_enterprise=strategy_profiles_by_enterprise,
            default_strategy_profile=default_strategy_profile,
        ),
    )


def _build_herding_sensor_enterprise_bundle(
    strategy_profiles_by_enterprise: Dict[str, Dict[str, Any]] = None,
    default_strategy_profile: Dict[str, Any] = None,
    initial_capital: int = 260000,
) -> Dict[str, Any]:
    """Build parallel sensor enterprises for the herding scenario."""
    sensor_ids = list(HERDING_SENSOR_ENTERPRISE_IDS)
    return _build_scenario_enterprise_bundle(
        initial_capitals={enterprise_id: int(initial_capital) for enterprise_id in sensor_ids},
        staffing_map={
            enterprise_id: {"finance": 1, "hr": 1, "production": 9, "sales": 4, "inventory": 2}
            for enterprise_id in sensor_ids
        },
        capacity_map={enterprise_id: 180000 for enterprise_id in sensor_ids},
        inventory_map={
            enterprise_id: [
                {"item_id": "smart_sensor", "quantity": 35, "purchase_price": 220, "item_type": "product"},
            ]
            for enterprise_id in sensor_ids
        },
        supplier_data_by_enterprise={},
        inventory_policy_map={
            enterprise_id: [
                {"item_id": "smart_sensor", "reorder_point": 25, "safety_stock": 20},
            ]
            for enterprise_id in sensor_ids
        },
        production_recipes_by_enterprise={
            enterprise_id: DEFAULT_SMART_SENSOR_RECIPE
            for enterprise_id in sensor_ids
        },
        initial_production_lines_by_enterprise={
            enterprise_id: [
                {"line_type": "small", "ready_at_start": True},
                {"line_type": "small", "ready_at_start": True},
            ]
            for enterprise_id in sensor_ids
        },
        include_inventory_cost=False,
        inventory_cost_enterprise_ids=[],
        enterprise_blueprints=_build_herding_sensor_enterprise_blueprints(
            strategy_profiles_by_enterprise=strategy_profiles_by_enterprise,
            default_strategy_profile=default_strategy_profile,
        ),
    )


def _build_herding_scenario(
    scenario_id: str,
    name: str,
    summary: str,
    herding_config: Dict[str, Any],
    strategy_profiles_by_enterprise: Dict[str, Dict[str, Any]] = None,
    default_strategy_profile: Dict[str, Any] = None,
    total_steps: int = 18,
    initial_capital: int = 260000,
) -> Dict[str, Any]:
    """An experimental scenario for the construction of the herd effect."""
    sensor_ids = list(herding_config.get("target_enterprise_ids") or HERDING_SENSOR_ENTERPRISE_IDS)
    return {
        "meta": {
            "scenario_id": scenario_id,
            "name": name,
            "summary": summary,
        },
        "simulation": {
            "service_total_steps": int(total_steps),
            "agent_run_steps": int(total_steps),
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_HERDING,
            "beer_game_demand_series": [0] * int(total_steps),
            "beer_game_customer_delivery_lead_time": 1,
            "beer_game_unit_price": 0,
            "herding_config": herding_config,
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 4,
            "max_department_concurrency": 2,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": sensor_ids[0] if sensor_ids else "Sensor_A",
            "external_market_order_enterprise_ids": sensor_ids,
            "salary_payment_interval_days": 12,
            "salary_payment_enterprise_ids": sensor_ids,
            "inventory_cost_daily_settlement_enabled": False,
            "inventory_cost_enterprise_ids": [],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=2,
            ),
            "herding_experiment_policy": _build_herding_experiment_policy(
                enabled=True,
                target_enterprise_ids=sensor_ids,
                allow_raw_peer_files=False,
            ),
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=False,
                enterprise_ids=[],
                external_credit_limit=0,
                payable_delay_rounds=0,
                max_single_order_share=1.0,
                external_logistics_cost_multiplier=0.10,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=0,
                consecutive_no_order_rounds=99,
                consecutive_decline_rounds=99,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=0,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.85,
                "recruit_ratio": 0.12,
                "min_recruit": 1,
                "max_recruit": 3,
                "severe_utilization_threshold": 0.95,
                "available_worker_floor": 0,
                "failure_recruit_boost": 1,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 40,
                "sales_fill_rate_trigger": 0.9,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.85,
                "inventory_alert_count_trigger": 2,
            },
            "inventory": {
                "expand_trigger_threshold": 0.9,
                "expand_ratio": 0.12,
                "expansion_options": [1000, 2000, 5000],
                "min_expansion_if_nonzero": 300,
            },
        },
        **_build_herding_sensor_enterprise_bundle(
            strategy_profiles_by_enterprise=strategy_profiles_by_enterprise,
            default_strategy_profile=default_strategy_profile,
            initial_capital=initial_capital,
        ),
    }


def _build_commons_mining_scenario(
    scenario_id: str,
    name: str,
    summary: str,
    shared_resource_config: Dict[str, Any],
    shared_resource_governance_policy: Dict[str, Any] = None,
    strategy_profiles_by_enterprise: Dict[str, Dict[str, Any]] = None,
    default_strategy_profile: Dict[str, Any] = None,
    total_steps: int = 24,
    initial_capital: int = 220000,
) -> Dict[str, Any]:
    """Build a commons-mining scenario while retaining the shared_resource contract used by the Agent layer."""
    miner_ids = list(shared_resource_config.get("target_enterprise_ids") or [])
    governance_policy = (
        deepcopy(shared_resource_governance_policy)
        if shared_resource_governance_policy is not None
        else _build_shared_resource_governance_policy(enabled=False)
    )
    return {
        "meta": {
            "scenario_id": scenario_id,
            "name": name,
            "summary": summary,
        },
        "simulation": {
            "service_total_steps": int(total_steps),
            "agent_run_steps": int(total_steps),
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_SHARED_RESOURCE,
            "beer_game_demand_series": [0] * int(total_steps),
            "beer_game_customer_delivery_lead_time": 1,
            "beer_game_unit_price": 0,
            "shared_resource_config": shared_resource_config,
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 4,
            "max_department_concurrency": 2,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": miner_ids[0] if miner_ids else "Miner_A",
            "external_market_order_enterprise_ids": miner_ids,
            "salary_payment_interval_days": 12,
            "salary_payment_enterprise_ids": miner_ids,
            "inventory_cost_daily_settlement_enabled": False,
            "inventory_cost_enterprise_ids": [],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=2,
            ),
            "shared_resource_governance_policy": governance_policy,
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=False,
                enterprise_ids=[],
                external_credit_limit=0,
                payable_delay_rounds=0,
                max_single_order_share=1.0,
                external_logistics_cost_multiplier=0.10,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=0,
                consecutive_no_order_rounds=99,
                consecutive_decline_rounds=99,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=0,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.85,
                "recruit_ratio": 0.12,
                "min_recruit": 1,
                "max_recruit": 3,
                "severe_utilization_threshold": 0.95,
                "available_worker_floor": 0,
                "failure_recruit_boost": 1,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 40,
                "sales_fill_rate_trigger": 0.9,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.85,
                "inventory_alert_count_trigger": 2,
            },
            "inventory": {
                "expand_trigger_threshold": 0.9,
                "expand_ratio": 0.1,
                "expansion_options": [1000, 2000, 5000],
                "min_expansion_if_nonzero": 200,
            },
        },
        **_build_commons_mining_enterprise_bundle(
            strategy_profiles_by_enterprise=strategy_profiles_by_enterprise,
            default_strategy_profile=default_strategy_profile,
            initial_capital=initial_capital,
        ),
    }


def _architecture_inventory(item_id: str, quantity: float, purchase_price: float, item_type: str = "product") -> Dict[str, Any]:
    return {
        "item_id": item_id,
        "quantity": quantity,
        "purchase_price": purchase_price,
        "item_type": item_type,
    }


def _architecture_inventory_policy(item_id: str, reorder_point: float, safety_stock: float) -> Dict[str, float]:
    return {
        "item_id": item_id,
        "reorder_point": reorder_point,
        "safety_stock": safety_stock,
    }


def _build_enterprise_supplier_data_from_blueprints(
    enterprise_blueprints: List[Dict[str, Any]],
    unit_price_map: Dict[str, float] = None,
    default_quantity: float = 100000.0,
    default_processing_time: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """The initialization data for the enterprise supplier are generated automatically according to supplier_name_list in the blueprint."""
    unit_price_map = unit_price_map or {}
    blueprint_by_id = {item["id"]: item for item in enterprise_blueprints}
    blueprint_by_name = {item.get("name", item["id"]): item for item in enterprise_blueprints}
    supplier_data_by_enterprise: Dict[str, List[Dict[str, Any]]] = {}

    for buyer in enterprise_blueprints:
        buyer_id = buyer["id"]
        purchasable = set(buyer.get("purchasable_materials_idList") or [])
        supplier_entries: List[Dict[str, Any]] = []
        for supplier_ref in buyer.get("supplier_name_list") or []:
            supplier = blueprint_by_id.get(supplier_ref) or blueprint_by_name.get(supplier_ref)
            if not supplier:
                continue
            offered = [
                product_id
                for product_id in supplier.get("salable_products_idList", [])
                if product_id in purchasable
            ]
            if not offered:
                continue
            supplier_entries.append({
                "supplier_name": supplier.get("name", supplier["id"]),
                "supplier_type": "enterprise",
                "materials": {
                    product_id: {
                        "unit_price": float(unit_price_map.get(product_id, 1.0)),
                        "quantity": float(default_quantity),
                        "min_order_quantity": 0,
                    }
                    for product_id in offered
                },
                "processing_time": default_processing_time,
                "quality_level": "standard",
                "reliability_score": 1.0,
            })
        if supplier_entries:
            supplier_data_by_enterprise[buyer_id] = supplier_entries

    return supplier_data_by_enterprise


def _build_architecture_validation_scenario(
    scenario_id: str,
    name: str,
    summary: str,
    enterprise_ids: List[str],
    enterprise_blueprints: List[Dict[str, Any]],
    demand_series: List[float],
    external_target_ids: List[str],
    initial_capitals: Dict[str, int],
    staffing_map: Dict[str, Dict[str, int]],
    capacity_map: Dict[str, int],
    inventory_map: Dict[str, List[Dict[str, Any]]],
    inventory_policy_map: Dict[str, List[Dict[str, float]]],
    production_recipes_by_enterprise: Dict[str, Dict[str, Any]] = None,
    initial_production_lines_by_enterprise: Dict[str, List[Dict[str, Any]]] = None,
    supplier_unit_price_map: Dict[str, float] = None,
    final_product_id: str = "beer",
    beer_game_customer_delivery_lead_time: int = 1,
    beer_game_unit_price: float = 320,
    total_steps: int = 8,
    topology_type: str = "linear",
) -> Dict[str, Any]:
    """Build a topology-validation scenario using scheduled external demand for observable short-run behavior."""
    return {
        "meta": {
            "scenario_id": scenario_id,
            "name": name,
            "summary": summary,
            "experiment_family": "architecture_complexity_validation",
        },
        "simulation": {
            "service_total_steps": int(total_steps),
            "agent_run_steps": int(total_steps),
            "step_duration": 0.1,
            "market_demand_mode": MARKET_DEMAND_MODE_BEER_GAME,
            "beer_game_demand_series": list(demand_series),
            "beer_game_customer_delivery_lead_time": int(beer_game_customer_delivery_lead_time),
            "beer_game_unit_price": float(beer_game_unit_price),
            "beer_game_product_id": final_product_id,
            "architecture_validation_config": {
                "enabled": True,
                "topology_type": topology_type,
                "final_product_id": final_product_id,
                "target_enterprise_ids": list(enterprise_ids),
                "external_target_ids": list(external_target_ids),
                "validation_focus": [
                    "topology_expression",
                    "active_trade_edges",
                    "positive_revenue_path",
                    "evolution_explanation",
                ],
                "note": "The initial scenario reuses the scheduled external-order generator; later variants may configure final_product_id explicitly.",
            },
            "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
            "max_enterprise_concurrency": 4,
            "max_department_concurrency": 2,
        },
        "runtime_injection": {
            "external_market_order_enterprise_id": external_target_ids[0] if external_target_ids else enterprise_ids[-1],
            "external_market_order_enterprise_ids": list(external_target_ids),
            "external_market_order_policy": _build_external_market_order_policy(
                enabled=True,
                mode="multi_target" if len(external_target_ids) > 1 else "single_target",
                target_enterprise_ids=external_target_ids,
            ),
            "salary_payment_interval_days": 12,
            "salary_payment_enterprise_ids": list(enterprise_ids),
            "inventory_cost_daily_settlement_enabled": False,
            "inventory_cost_enterprise_ids": [],
            "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "analyst_policy": _build_analyst_policy(
                full_analysis_interval_rounds=2,
            ),
            "top_tier_supply_policy": _build_top_tier_supply_policy(
                enabled=False,
                enterprise_ids=[],
                external_credit_limit=0,
                payable_delay_rounds=0,
                max_single_order_share=1.0,
                external_logistics_cost_multiplier=0.10,
            ),
            "safety_stop_policy": _build_safety_stop_policy(
                enabled=False,
                target_enterprise_ids=[],
                min_round_to_evaluate=0,
                consecutive_no_order_rounds=99,
                consecutive_decline_rounds=99,
            ),
            "staffing_relaxation_policy": _build_staffing_relaxation_policy(
                enabled=False,
                enterprise_ids=[],
                virtual_available_workers=0,
            ),
            "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
                enabled=False,
                target_enterprise_ids=[],
            ),
            "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
                enabled=False,
                target_enterprise_ids=[],
            ),
        },
        "auto_policy": {
            "hr": {
                "idle_threshold": 0.3,
                "recruit_trigger_threshold": 0.85,
                "recruit_ratio": 0.12,
                "min_recruit": 1,
                "max_recruit": 3,
                "severe_utilization_threshold": 0.95,
                "available_worker_floor": 0,
                "failure_recruit_boost": 1,
                "procurement_pending_quantity_trigger": 1,
                "sales_backlog_trigger": 30,
                "sales_fill_rate_trigger": 0.9,
                "production_plan_trigger": 1,
                "inventory_support_threshold": 0.85,
                "inventory_alert_count_trigger": 2,
            },
            "inventory": {
                "expand_trigger_threshold": 0.9,
                "expand_ratio": 0.12,
                "expansion_options": [1000, 2000, 5000],
                "min_expansion_if_nonzero": 200,
            },
        },
        **_build_scenario_enterprise_bundle(
            initial_capitals=initial_capitals,
            staffing_map=staffing_map,
            capacity_map=capacity_map,
            inventory_map=inventory_map,
            supplier_data_by_enterprise=_build_enterprise_supplier_data_from_blueprints(
                enterprise_blueprints,
                unit_price_map=supplier_unit_price_map,
            ),
            inventory_policy_map=inventory_policy_map,
            production_recipes_by_enterprise=production_recipes_by_enterprise or {},
            initial_production_lines_by_enterprise=initial_production_lines_by_enterprise or {},
            include_inventory_cost=False,
            inventory_cost_enterprise_ids=[],
            enterprise_blueprints=enterprise_blueprints,
        ),
    }


def _build_architecture_linear_chain_scenario() -> Dict[str, Any]:
    return _build_architecture_validation_scenario(
        scenario_id="architecture_linear_chain_short",
        name="Topology Validation: Linear Chain",
        summary="Trace demand, procurement, production, and sales through RawSupplier -> ComponentMaker -> FinalAssembler -> Retailer.",
        enterprise_ids=ARCHITECTURE_LINEAR_ENTERPRISE_IDS,
        enterprise_blueprints=ARCHITECTURE_LINEAR_BLUEPRINTS,
        demand_series=[24, 26, 28, 30, 32, 34, 36, 38],
        external_target_ids=["Retailer"],
        final_product_id="smart_device",
        beer_game_customer_delivery_lead_time=3,
        beer_game_unit_price=520,
        total_steps=8,
        topology_type="linear_chain",
        initial_capitals={
            "RawSupplier": 220000,
            "ComponentMaker": 210000,
            "FinalAssembler": 220000,
            "Retailer": 150000,
        },
        staffing_map={
            "RawSupplier": {"finance": 1, "hr": 1, "sales": 4, "inventory": 2},
            "ComponentMaker": {"finance": 1, "hr": 1, "procurement": 4, "production": 6, "sales": 4, "inventory": 2},
            "FinalAssembler": {"finance": 1, "hr": 1, "procurement": 4, "production": 6, "sales": 4, "inventory": 2},
            "Retailer": {"finance": 1, "hr": 1, "procurement": 3, "sales": 4, "inventory": 2},
        },
        capacity_map={
            "RawSupplier": 120000,
            "ComponentMaker": 20000,
            "FinalAssembler": 16000,
            "Retailer": 1200,
        },
        inventory_map={
            "RawSupplier": [
                _architecture_inventory("raw_malt", 8000, 18, "raw_material"),
                _architecture_inventory("raw_hops", 1800, 34, "raw_material"),
            ],
            "ComponentMaker": [
                _architecture_inventory("raw_malt", 120, 20, "raw_material"),
                _architecture_inventory("raw_hops", 30, 36, "raw_material"),
                _architecture_inventory("brew_mix", 120, 75, "product"),
            ],
            "FinalAssembler": [
                _architecture_inventory("brew_mix", 95, 85, "raw_material"),
                _architecture_inventory("smart_device", 120, 165, "product"),
            ],
            "Retailer": [
                _architecture_inventory("smart_device", 90, 190, "product"),
            ],
        },
        inventory_policy_map={
            "RawSupplier": [
                _architecture_inventory_policy("raw_malt", 900, 450),
                _architecture_inventory_policy("raw_hops", 180, 90),
            ],
            "ComponentMaker": [
                _architecture_inventory_policy("raw_malt", 220, 120),
                _architecture_inventory_policy("raw_hops", 45, 24),
                _architecture_inventory_policy("brew_mix", 115, 70),
            ],
            "FinalAssembler": [
                _architecture_inventory_policy("brew_mix", 110, 65),
                _architecture_inventory_policy("smart_device", 115, 70),
            ],
            "Retailer": [
                _architecture_inventory_policy("smart_device", 105, 60),
            ],
        },
        production_recipes_by_enterprise={
            "ComponentMaker": DEFAULT_BREW_MIX_RECIPE,
            "FinalAssembler": DEFAULT_LINEAR_SMART_DEVICE_RECIPE,
        },
        supplier_unit_price_map={
            "raw_malt": 22,
            "raw_hops": 40,
            "brew_mix": 110,
            "smart_device": 250,
        },
        initial_production_lines_by_enterprise={
            "ComponentMaker": [{"line_type": "small", "ready_at_start": True}],
            "FinalAssembler": [{"line_type": "small", "ready_at_start": True}],
        },
    )


def _build_architecture_branching_assembly_scenario() -> Dict[str, Any]:
    return _build_architecture_validation_scenario(
        scenario_id="architecture_branching_assembly_short",
        name="Topology Validation: Branching Assembly",
        summary="Three upstream suppliers feed BreweryAssembler, followed by Retailer and external customers, to test procurement across branches and assembly bottlenecks.",
        enterprise_ids=ARCHITECTURE_BRANCHING_ENTERPRISE_IDS,
        enterprise_blueprints=ARCHITECTURE_BRANCHING_BLUEPRINTS,
        demand_series=[24, 28, 32, 36, 40, 42, 44, 45],
        external_target_ids=["Retailer"],
        final_product_id="portable_device",
        beer_game_customer_delivery_lead_time=3,
        beer_game_unit_price=520,
        total_steps=8,
        topology_type="branching_assembly",
        initial_capitals={
            "MaltSupplier": 170000,
            "BottleSupplier": 160000,
            "YeastSupplier": 150000,
            "BreweryAssembler": 220000,
            "Retailer": 150000,
        },
        staffing_map={
            "MaltSupplier": {"finance": 1, "hr": 1, "sales": 3, "inventory": 2},
            "BottleSupplier": {"finance": 1, "hr": 1, "sales": 3, "inventory": 2},
            "YeastSupplier": {"finance": 1, "hr": 1, "sales": 3, "inventory": 2},
            "BreweryAssembler": {"finance": 1, "hr": 1, "procurement": 5, "production": 7, "sales": 4, "inventory": 2},
            "Retailer": {"finance": 1, "hr": 1, "procurement": 3, "sales": 4, "inventory": 2},
        },
        capacity_map={
            "MaltSupplier": 80000,
            "BottleSupplier": 80000,
            "YeastSupplier": 50000,
            "BreweryAssembler": 18000,
            "Retailer": 1200,
        },
        inventory_map={
            "MaltSupplier": [_architecture_inventory("malt_extract", 4500, 24, "product")],
            "BottleSupplier": [_architecture_inventory("bottle_pack", 4500, 18, "product")],
            "YeastSupplier": [_architecture_inventory("yeast_culture", 900, 30, "product")],
            "BreweryAssembler": [
                _architecture_inventory("malt_extract", 45, 30, "raw_material"),
                _architecture_inventory("bottle_pack", 45, 22, "raw_material"),
                _architecture_inventory("yeast_culture", 8, 36, "raw_material"),
                _architecture_inventory("portable_device", 90, 165, "product"),
            ],
            "Retailer": [_architecture_inventory("portable_device", 85, 240, "product")],
        },
        inventory_policy_map={
            "MaltSupplier": [_architecture_inventory_policy("malt_extract", 300, 150)],
            "BottleSupplier": [_architecture_inventory_policy("bottle_pack", 300, 150)],
            "YeastSupplier": [_architecture_inventory_policy("yeast_culture", 80, 40)],
            "BreweryAssembler": [
                _architecture_inventory_policy("malt_extract", 95, 50),
                _architecture_inventory_policy("bottle_pack", 95, 50),
                _architecture_inventory_policy("yeast_culture", 18, 9),
                _architecture_inventory_policy("portable_device", 105, 65),
            ],
            "Retailer": [_architecture_inventory_policy("portable_device", 100, 60)],
        },
        production_recipes_by_enterprise={
            "BreweryAssembler": DEFAULT_BRANCHING_PORTABLE_DEVICE_RECIPE,
        },
        supplier_unit_price_map={
            "malt_extract": 30,
            "bottle_pack": 22,
            "yeast_culture": 36,
            "portable_device": 250,
        },
        initial_production_lines_by_enterprise={
            "BreweryAssembler": [{"line_type": "small", "ready_at_start": True}],
        },
    )


def _build_architecture_mesh_multi_source_scenario() -> Dict[str, Any]:
    return _build_architecture_validation_scenario(
        scenario_id="architecture_mesh_multi_source_short",
        name="Topology Validation: Multiple Supply Paths",
        summary="Multiple raw-material suppliers, two parallel manufacturers, a distribution hub, and two sales channels support tests of procurement from multiple sources, alternative routes, and transaction routing.",
        enterprise_ids=ARCHITECTURE_MESH_ENTERPRISE_IDS,
        enterprise_blueprints=ARCHITECTURE_MESH_BLUEPRINTS,
        demand_series=[18, 20, 24, 26, 28, 30, 32, 34],
        external_target_ids=["Channel_A", "Channel_B"],
        final_product_id="industrial_robot_kit",
        beer_game_customer_delivery_lead_time=3,
        beer_game_unit_price=560,
        total_steps=8,
        topology_type="mesh_multi_source",
        initial_capitals={
            "GrainSupplier": 190000,
            "PackagingSupplier": 180000,
            "FlavorSupplier": 170000,
            "Brewery_A": 220000,
            "Brewery_B": 215000,
            "DistributionHub": 170000,
            "Channel_A": 130000,
            "Channel_B": 130000,
        },
        staffing_map={
            "GrainSupplier": {"finance": 1, "hr": 1, "sales": 3, "inventory": 2},
            "PackagingSupplier": {"finance": 1, "hr": 1, "sales": 3, "inventory": 2},
            "FlavorSupplier": {"finance": 1, "hr": 1, "sales": 3, "inventory": 2},
            "Brewery_A": {"finance": 1, "hr": 1, "procurement": 4, "production": 6, "sales": 4, "inventory": 2},
            "Brewery_B": {"finance": 1, "hr": 1, "procurement": 4, "production": 6, "sales": 4, "inventory": 2},
            "DistributionHub": {"finance": 1, "hr": 1, "procurement": 4, "sales": 4, "inventory": 2},
            "Channel_A": {"finance": 1, "hr": 1, "procurement": 3, "sales": 4, "inventory": 2},
            "Channel_B": {"finance": 1, "hr": 1, "procurement": 3, "sales": 4, "inventory": 2},
        },
        capacity_map={
            "GrainSupplier": 90000,
            "PackagingSupplier": 90000,
            "FlavorSupplier": 60000,
            "Brewery_A": 14000,
            "Brewery_B": 14000,
            "DistributionHub": 1600,
            "Channel_A": 900,
            "Channel_B": 900,
        },
        inventory_map={
            "GrainSupplier": [_architecture_inventory("grain_base", 5000, 22, "product")],
            "PackagingSupplier": [_architecture_inventory("bottle_pack", 5000, 18, "product")],
            "FlavorSupplier": [_architecture_inventory("flavor_concentrate", 1200, 42, "product")],
            "Brewery_A": [
                _architecture_inventory("grain_base", 40, 25, "raw_material"),
                _architecture_inventory("bottle_pack", 40, 20, "raw_material"),
                _architecture_inventory("industrial_robot_kit", 70, 168, "product"),
            ],
            "Brewery_B": [
                _architecture_inventory("grain_base", 40, 25, "raw_material"),
                _architecture_inventory("flavor_concentrate", 9, 46, "raw_material"),
                _architecture_inventory("industrial_robot_kit", 65, 176, "product"),
            ],
            "DistributionHub": [_architecture_inventory("industrial_robot_kit", 90, 230, "product")],
            "Channel_A": [_architecture_inventory("industrial_robot_kit", 55, 255, "product")],
            "Channel_B": [_architecture_inventory("industrial_robot_kit", 55, 255, "product")],
        },
        inventory_policy_map={
            "GrainSupplier": [_architecture_inventory_policy("grain_base", 300, 150)],
            "PackagingSupplier": [_architecture_inventory_policy("bottle_pack", 300, 150)],
            "FlavorSupplier": [_architecture_inventory_policy("flavor_concentrate", 80, 40)],
            "Brewery_A": [
                _architecture_inventory_policy("grain_base", 85, 45),
                _architecture_inventory_policy("bottle_pack", 85, 45),
                _architecture_inventory_policy("industrial_robot_kit", 80, 50),
            ],
            "Brewery_B": [
                _architecture_inventory_policy("grain_base", 85, 45),
                _architecture_inventory_policy("flavor_concentrate", 18, 9),
                _architecture_inventory_policy("industrial_robot_kit", 75, 45),
            ],
            "DistributionHub": [_architecture_inventory_policy("industrial_robot_kit", 120, 70)],
            "Channel_A": [_architecture_inventory_policy("industrial_robot_kit", 70, 45)],
            "Channel_B": [_architecture_inventory_policy("industrial_robot_kit", 70, 45)],
        },
        production_recipes_by_enterprise={
            "Brewery_A": DEFAULT_MESH_ROBOT_KIT_RECIPE_A,
            "Brewery_B": DEFAULT_MESH_ROBOT_KIT_RECIPE_B,
        },
        supplier_unit_price_map={
            "grain_base": 27,
            "bottle_pack": 22,
            "flavor_concentrate": 48,
            "industrial_robot_kit": 270,
        },
        initial_production_lines_by_enterprise={
            "Brewery_A": [{"line_type": "small", "ready_at_start": True}],
            "Brewery_B": [{"line_type": "small", "ready_at_start": True}],
        },
    )


def _build_commons_mining_collapse_resource_config() -> Dict[str, Any]:
    """Build a more extreme ungoverned commons-resource configuration."""
    return _build_shared_resource_config(
        resource_id="shared_copper_deposit",
        resource_label="Shared copper pool",
        product_id="copper_ore",
        target_enterprise_ids=COMMONS_MINING_ENTERPRISE_IDS,
        initial_stock_quantity=7000,
        max_stock_quantity=7000,
        sustainable_acquisition_per_round=200,
        regeneration_quantity=100,
        resource_quality_floor=0.15,
        base_unit_price=240,
        unit_acquisition_cost=40,
        initial_acquisition_quantity_per_enterprise=240,
        warning_threshold_ratio=0.65,
        collapse_threshold_ratio=0.25,
        apply_acquisition_cost_to_finance=True,
        acquisition_cost_finance_category="raw_materials",
        constrain_production_output_to_effective_acquisition=True,
        external_order_quantity_mode="planned_acquisition",
    )


def _build_commons_mining_financial_damage_resource_config() -> Dict[str, Any]:
    """Build an ungoverned commons-resource configuration with financial-loss signals."""
    return _build_shared_resource_config(
        resource_id="shared_copper_deposit",
        resource_label="Shared copper pool",
        product_id="copper_ore",
        target_enterprise_ids=COMMONS_MINING_ENTERPRISE_IDS,
        initial_stock_quantity=6500,
        max_stock_quantity=6500,
        sustainable_acquisition_per_round=180,
        regeneration_quantity=80,
        resource_quality_floor=0.08,
        base_unit_price=180,
        unit_acquisition_cost=95,
        initial_acquisition_quantity_per_enterprise=260,
        warning_threshold_ratio=0.65,
        collapse_threshold_ratio=0.28,
        apply_acquisition_cost_to_finance=True,
        acquisition_cost_finance_category="raw_materials",
        constrain_production_output_to_effective_acquisition=True,
        external_order_quantity_mode="planned_acquisition",
        apply_breach_penalty_to_finance=False,
        breach_penalty_per_unit=0,
        breach_penalty_finance_category="market_cost",
    )


COMMONS_COLLAPSE_DIFFERENTIATED_STRATEGY_PROFILES = {
    "Miner_A": {
        **COMMONS_STRATEGY_PROFILES["conservative_resilience"],
        "preferred_acquisition_band": [120, 200],
        "growth_priority": 0.58,
        "resource_risk_sensitivity": 0.62,
        "decision_note": 'The collapse experiment remained relatively robust, but allowed higher than single sustainable reference under high-profit and peer pressure.',
    },
    "Miner_B": {
        **COMMONS_STRATEGY_PROFILES["balanced_growth"],
        "preferred_acquisition_band": [160, 260],
        "growth_priority": 0.76,
        "resource_risk_sensitivity": 0.36,
        "decision_note": 'In the collapse experiment, growth was even, but short-term profits and order pressures could drive them significantly above a single sustainable reference.',
    },
    "Miner_C": COMMONS_STRATEGY_PROFILES["high_growth_competitive"],
    "Miner_D": {
        **COMMONS_STRATEGY_PROFILES["opportunistic_follower"],
        "preferred_acquisition_band": [180, 300],
        "growth_priority": 0.82,
        "resource_risk_sensitivity": 0.28,
        "decision_note": 'It is easier to follow high levels of peer acquisition in collapse-reducing experiments to avoid falling behind in resource competition.',
    },
}

HERDING_BASELINE_STRATEGY_PROFILES = {
    "Sensor_A": HERDING_STRATEGY_PROFILES["trend_chaser"],
    "Sensor_B": HERDING_STRATEGY_PROFILES["balanced_follower"],
    "Sensor_C": HERDING_STRATEGY_PROFILES["trend_chaser"],
    "Sensor_D": HERDING_STRATEGY_PROFILES["cautious_validator"],
}


SCENARIO_CONFIGS.update({
    "architecture_linear_chain_short": _build_architecture_linear_chain_scenario(),
    "architecture_branching_assembly_short": _build_architecture_branching_assembly_scenario(),
    "architecture_mesh_multi_source_short": _build_architecture_mesh_multi_source_scenario(),
    "herding_baseline_peer_visible": _build_herding_scenario(
        scenario_id="herding_baseline_peer_visible",
        name='Herding Effect: Peer Signals Visible',
        summary=(
            'Four sensor enterprises observe the same market heat and aggregated peer-production summaries. Observe whether non-command signals lead to synchronized expansion, overproduction and subsequent operating pressure.'
        ),
        herding_config=_build_herding_config(
            product_id="smart_sensor",
            target_enterprise_ids=HERDING_SENSOR_ENTERPRISE_IDS,
            peer_visibility_enabled=True,
            peer_visibility_lag_rounds=1,
        ),
        strategy_profiles_by_enterprise=HERDING_BASELINE_STRATEGY_PROFILES,
        total_steps=18,
    ),
    "herding_no_peer_visibility": _build_herding_scenario(
        scenario_id="herding_no_peer_visibility",
        name='Herding Effect: Peer Signals Hidden',
        summary=(
            'Four sensor enterprises share market heat and real demand without aggregated peer-production summaries, providing a visibility comparison for synchronized expansion.'
        ),
        herding_config=_build_herding_config(
            product_id="smart_sensor",
            target_enterprise_ids=HERDING_SENSOR_ENTERPRISE_IDS,
            peer_visibility_enabled=False,
            peer_visibility_lag_rounds=1,
        ),
        strategy_profiles_by_enterprise=HERDING_BASELINE_STRATEGY_PROFILES,
        total_steps=18,
    ),
    "commons_mining_baseline": _build_commons_mining_scenario(
        scenario_id="commons_mining_baseline",
        name='Tragedy of the Commons: Shared Resource Baseline',
        summary='Four enterprises extract from a shared copper resource pool without governance constraints to study how individual operating decisions affect resource depletion.',
        shared_resource_config=_build_shared_resource_config(
            resource_id="shared_copper_deposit",
            resource_label='Shared copper pool',
            product_id="copper_ore",
            target_enterprise_ids=["Miner_A", "Miner_B", "Miner_C", "Miner_D"],
            initial_stock_quantity=12000,
            max_stock_quantity=12000,
            sustainable_acquisition_per_round=360,
            regeneration_quantity=360,
            base_unit_price=180,
            unit_acquisition_cost=70,
        ),
        shared_resource_governance_policy=_build_shared_resource_governance_policy(
            enabled=False,
            mode="none",
            shared_sustainability_target=360,
        ),
    ),
    "commons_mining_collapse_baseline_uniform": _build_commons_mining_scenario(
        scenario_id="commons_mining_collapse_baseline_uniform",
        name='Ungoverned Commons Stress Test: Shared High-Growth Trend',
        summary=(
            'Four resource enterprises share a high-growth competitive profile, with a fragile resource pool and high short-term returns; observe collective acquisition and resource degradation without governance.'
        ),
        shared_resource_config=_build_commons_mining_collapse_resource_config(),
        shared_resource_governance_policy=_build_shared_resource_governance_policy(
            enabled=False,
            mode="none",
            shared_sustainability_target=200,
        ),
        default_strategy_profile=COMMONS_STRATEGY_PROFILES["high_growth_competitive"],
        total_steps=32,
    ),
    "commons_mining_collapse_baseline_differentiated": _build_commons_mining_scenario(
        scenario_id="commons_mining_collapse_baseline_differentiated",
        name='Ungoverned Commons Stress Test: Divergent Enterprise Trends',
        summary=(
            'Four resource enterprises use conservative, balanced, high-growth and opportunity-following profiles; observe how different preferences jointly create competitive resource degradation.'
        ),
        shared_resource_config=_build_commons_mining_collapse_resource_config(),
        shared_resource_governance_policy=_build_shared_resource_governance_policy(
            enabled=False,
            mode="none",
            shared_sustainability_target=200,
        ),
        strategy_profiles_by_enterprise=COMMONS_COLLAPSE_DIFFERENTIATED_STRATEGY_PROFILES,
        total_steps=32,
    ),
    "commons_mining_financial_damage_baseline": _build_commons_mining_scenario(
        scenario_id="commons_mining_financial_damage_baseline",
        name='Ungoverned Commons Stress Test: Financial-Loss Pressure',
        summary=(
            'Reduce resource resilience and sale prices while raising extraction costs to observe whether resource collapse propagates into cash and profit losses.'
        ),
        shared_resource_config=_build_commons_mining_financial_damage_resource_config(),
        shared_resource_governance_policy=_build_shared_resource_governance_policy(
            enabled=False,
            mode="none",
            shared_sustainability_target=180,
        ),
        strategy_profiles_by_enterprise=COMMONS_COLLAPSE_DIFFERENTIATED_STRATEGY_PROFILES,
        total_steps=32,
        initial_capital=150000,
    ),
})

SCENARIO_CONFIGS["commons_mining_collapse_baseline"] = deepcopy(
    SCENARIO_CONFIGS["commons_mining_collapse_baseline_differentiated"]
)
SCENARIO_CONFIGS["commons_mining_collapse_baseline"]["meta"]["scenario_id"] = "commons_mining_collapse_baseline"

SCENARIO_CONFIGS.update({
    "cobweb_convergent": _build_cobweb_scenario(
        scenario_id="cobweb_convergent",
        name='Convergent Cobweb',
        summary='The supply-curve slope is lower than the demand-curve slope, so price and quantity oscillations contract toward equilibrium.',
        cobweb_config=_build_cobweb_config(
            stability_label="convergent",
            initial_price=280,
            demand_intercept=160,
            demand_slope=0.5,
            supply_intercept=20,
            supply_slope=0.25,
            price_floor=40,
            price_ceiling=600,
            quantity_floor=1,
            quantity_ceiling=240,
        ),
    ),
    "cobweb_neutral": _build_cobweb_scenario(
        scenario_id="cobweb_neutral",
        name='Neutral Cobweb',
        summary='Supply- and demand-curve slopes are similar, so prices and quantities cycle around equilibrium with approximately constant amplitude.',
        cobweb_config=_build_cobweb_config(
            stability_label="neutral",
            initial_price=300,
            demand_intercept=160,
            demand_slope=0.4,
            supply_intercept=20,
            supply_slope=0.4,
            price_floor=40,
            price_ceiling=600,
            quantity_floor=1,
            quantity_ceiling=260,
        ),
    ),
    "cobweb_divergent": _build_cobweb_scenario(
        scenario_id="cobweb_divergent",
        name='Divergent Cobweb',
        summary='The supply-response slope exceeds the demand slope, so deviations in price and quantity from equilibrium can grow within the configured bounds.',
        cobweb_config=_build_cobweb_config(
            stability_label="divergent",
            initial_price=280,
            demand_intercept=170,
            demand_slope=0.25,
            supply_intercept=20,
            supply_slope=0.6,
            price_floor=40,
            price_ceiling=600,
            quantity_floor=1,
            quantity_ceiling=260,
        ),
    ),
})


def _build_long_horizon_demand_series() -> List[float]:
    """Stable deterministic demand; phase changes are applied by the event policy."""
    stable_cycle = [68, 70, 72, 74, 72, 70, 69, 71]
    return stable_cycle * 25


def _build_external_environment_policy() -> Dict[str, Any]:
    """Observable, deterministic forty-turn perturbations for the 200-turn run."""
    return {
        "schema_version": "external_environment_policy.v1",
        "enabled": True,
        "scenario_family": "long_horizon_evolution",
        "turn_unit": "turn",
        "total_turns": 200,
        "targets": {
            "external_material_enterprise_ids": ["Supplier"],
            "production_enterprise_ids": ["Manufacturer"],
            "material_ids": ["Malt", "Hops", "Yeast"],
        },
        "events": [
            {
                "event_id": "upstream_material_price_surge",
                "turn": 40,
                "label": "Price increases upstream raw material",
                "category": "upstream_input_cost",
                "description": "The top-level external supplier raw material increased its catalogue price by 25 per cent relative to the baseline.",
                "factor_updates": {"external_material_price": 1.25},
                "expected_response_dimensions": [
                    "Procurement volume and arrival rhythm",
                    "raw material Secure stock",
                    "Quote and Maori protection",
                    "Cash occupation",
                ],
            },
            {
                "event_id": "manufacturing_conversion_cost_surge",
                "turn": 80,
                "label": "Increased manufacturing conversion costs",
                "category": "production_cost",
                "description": (
                    "The cost of energy, man and equipment compliance is a common shock."
                    "The manufacturer ' s unit conversion cost increased by 80 per cent relative to the baseline."
                ),
                "factor_updates": {"production_conversion_cost": 1.80},
                "expected_response_dimensions": [
                    "Economic aspects of production programmes",
                    "capacity Utilization factor",
                    "Order margin",
                    "Inventory turnover",
                ],
            },
            {
                "event_id": "import_trade_logistics_tightening",
                "turn": 120,
                "label": "Increased import trade and logistics costs",
                "category": "trade_policy",
                "description": "The top-level external procurement logistics cost factor increased to 2.4 times the relative baseline.",
                "factor_updates": {"external_logistics_cost": 2.40},
                "expected_response_dimensions": [
                    "Logistics mode selection",
                    "Purchase frequency and list",
                    "Accounts payable and cash security",
                    "Supply continuity",
                ],
            },
            {
                "event_id": "cost_normalization_and_demand_softening",
                "turn": 160,
                "label": "Cost reversal and terminal demand soften",
                "category": "mixed_recovery",
                "description": (
                    "raw material, manufacturing and logistics costs partly fall, while end demand drops to 86% of baseline,"
                    "The final bargain was reduced to a baseline of 96 per cent."
                ),
                "factor_updates": {
                    "external_material_price": 1.10,
                    "production_conversion_cost": 1.12,
                    "external_logistics_cost": 1.55,
                    "external_demand_quantity": 0.86,
                    "external_customer_price": 0.96,
                },
                "expected_response_dimensions": [
                    "Take the initiative to the inventory.",
                    "replenishment and production rate reduction",
                    "Service level maintained",
                    "Profit and cash recovery",
                ],
            },
        ],
        "agent_visibility": {
            "show_current_event": True,
            "show_active_factors": True,
            "show_recent_events": 4,
            "hide_future_event_content": True,
        },
    }


def _build_long_horizon_evolution_scenario() -> Dict[str, Any]:
    """Independent four-tier adaptive operation scenario for a 200-turn run."""
    scenario = deepcopy(SCENARIO_CONFIGS["beer_game"])
    scenario["meta"] = {
        "scenario_id": "long_horizon_evolution",
        "name": "Long-Horizon Enterprise Evolution",
        "summary": (
            "A four-tier Supplier-Manufacturer-Distributor-Retailer chain operates over 200 rounds. "
            "Study sustained operations, shock recognition, and corrective responses under baseline demand cycles and phased external changes."
        ),
        "experiment_family": "long_horizon_evolution",
    }
    scenario["simulation"] = {
        "service_total_steps": 200,
        "agent_run_steps": 200,
        "step_duration": 0.1,
        "market_demand_mode": MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL,
        "beer_game_demand_series": _build_long_horizon_demand_series(),
        "beer_game_customer_delivery_lead_time": 3,
        "beer_game_unit_price": 520,
        "beer_game_product_id": "beer",
        "network_config": deepcopy(DEFAULT_NETWORK_CONFIG_TEMPLATE),
        "max_enterprise_concurrency": 4,
        "max_department_concurrency": 2,
    }
    long_run_policy = _build_long_run_experiment_policy(
        enabled=True,
        recommended_total_steps=200,
        history_days=12,
        warmup_rounds=10,
        exclude_tail_rounds=10,
        target_enterprise_ids=["Supplier", "Manufacturer", "Distributor", "Retailer"],
    )
    long_run_policy.update({
        "continue_on_agent_failure": True,
        "max_agent_retries_per_turn": 2,
        "checkpoint_every_turn": True,
        "event_turns": [40, 80, 120, 160],
        "event_follow_up_offsets": [0, 2, 5],
        "agent_timeout_policy": {
            "enabled": True,
            "department_first_attempt_seconds": 150,
            "department_retry_seconds": 90,
            "analyst_first_attempt_seconds": 180,
            "analyst_event_attempt_seconds": 210,
            "analyst_retry_seconds": 120,
            "hard_max_seconds": 240,
        },
        "state_driven_fallback_policy": {
            "enabled": True,
            "trigger_after_exhausted_agent_attempts": True,
            "execute_through_normal_action_pipeline": True,
            "visible_reason_prefix": "Think over time. Call the bottom logic link.",
        },
        "agent_timeout_circuit_breaker": {
            "enabled": True,
            "consecutive_all_timeout_rounds": 2,
            "include_trade_phase": True,
            "include_analyst_phase": True,
            "minimum_expected_agent_calls": 8,
            "pause_after_complete_round": True,
        },
        "artifact_retention_policy": {
            "retain_end_of_day_state": True,
            "retain_intermediate_observer_state": False,
            "retain_intermediate_exchange_state": False,
            "deduplicate_end_of_day_exchange_alias": True,
            "retain_department_actions_and_results": True,
            "retain_raw_observations": True,
        },
        "agent_context_compaction": {
            "enabled": True,
            "recent_history_items": 4,
            "max_open_records": 48,
            "preserve_full_state_for_archive": True,
        },
        "execution_guard": {
            "cap_terminal_replenishment_recovery": True,
            "normal_replenishment_backlog_recovery_share": 0.30,
            "normal_replenishment_max_forecast_days": 2,
            "tail_replenishment_backlog_recovery_share": 0.15,
            "tail_replenishment_max_forecast_days": 1,
            "cap_manual_purchase_demand": True,
            "manual_purchase_demand_cap_multiplier": 1.20,
            "max_pending_procurement_cash_share": 0.24,
            "minimum_procurement_commitment_budget": 25_000,
        },
    })
    scenario["runtime_injection"] = {
        "external_market_order_enterprise_id": "Retailer",
        "external_market_order_enterprise_ids": ["Retailer"],
        "external_market_order_policy": _build_external_market_order_policy(
            enabled=True,
            mode="single_target",
            target_enterprise_ids=["Retailer"],
        ),
        "salary_payment_interval_days": 20,
        "salary_payment_enterprise_ids": ["Supplier", "Manufacturer", "Distributor", "Retailer"],
        "inventory_cost_daily_settlement_enabled": True,
        "inventory_cost_enterprise_ids": ["Supplier", "Manufacturer", "Distributor", "Retailer"],
        "inventory_cost_exemption_policy": _build_inventory_cost_exemption_policy(
            enabled=False,
            target_enterprise_ids=[],
        ),
        "analyst_policy": {
            **_build_analyst_policy(full_analysis_interval_rounds=5),
            "force_on_rounds": [0, 40, 80, 120, 160],
        },
        "external_environment_policy": _build_external_environment_policy(),
        "top_tier_supply_policy": _build_top_tier_supply_policy(
            enabled=True,
            enterprise_ids=["Supplier"],
            external_credit_limit=3_000_000,
            payable_delay_rounds=5,
            max_single_order_share=0.50,
            external_logistics_cost_multiplier=0.08,
        ),
        "long_horizon_profitability_policy": {
            "enabled": True,
            "target_enterprise_ids": ["Supplier", "Manufacturer", "Distributor", "Retailer"],
            "b2b_margin_floor_by_role_tag": {
                "top_tier_supply_node": 1.26,
                "finished_goods_manufacturer": 1.28,
                "intermediate_distributor": 1.20,
                "retail_market_node": 1.12,
            },
            "b2b_max_price_floor_by_role_tag": {
                "finished_goods_manufacturer": 1.42,
                "intermediate_distributor": 1.38,
                "retail_market_node": 1.32,
            },
            "protect_margin_during_external_cost_changes": True,
            "avoid_inventory_growth_without_covered_demand": True,
        },
        "long_horizon_cost_policy": {
            "enabled": True,
            "target_enterprise_ids": ["Supplier", "Manufacturer", "Distributor", "Retailer"],
            "salary_cost_multiplier": 0.85,
            "inventory_maintenance_cost_multiplier": 0.50,
            "warehouse_operating_cost_multiplier": 0.50,
            "warehouse_expansion_cost_multiplier": 0.65,
        },
        "scripted_rule_policy": _build_scripted_rule_policy(enabled=False),
        "profit_objective_policy": _build_profit_objective_policy(
            enabled=True,
            objective_profile="adaptive_long_horizon_profitability",
            objective_weights={
                "net_profit": 0.35,
                "cash_safety": 0.25,
                "service_level": 0.22,
                "inventory_cost_control": 0.13,
                "risk_buffer": 0.05,
            },
            target_enterprise_ids=["Supplier", "Manufacturer", "Distributor", "Retailer"],
            planning_horizon_rounds=12,
        ),
        "long_run_experiment_policy": long_run_policy,
        "safety_stop_policy": _build_safety_stop_policy(
            enabled=False,
            target_enterprise_ids=[],
            min_round_to_evaluate=40,
            consecutive_no_order_rounds=20,
            consecutive_decline_rounds=10,
        ),
        "staffing_relaxation_policy": _build_staffing_relaxation_policy(
            enabled=True,
            enterprise_ids=["Supplier", "Manufacturer", "Distributor", "Retailer"],
            virtual_available_workers=20,
            allow_soft_assignment=True,
            allow_soft_release=True,
        ),
        "bullwhip_midstream_pass_through_mode": _build_bullwhip_midstream_pass_through_mode(
            enabled=False,
            target_enterprise_ids=[],
        ),
        "bullwhip_proposal_conversion_acceleration": _build_bullwhip_proposal_conversion_acceleration(
            enabled=False,
            target_enterprise_ids=[],
        ),
        "bullwhip_manufacturer_upstream_amplification": _build_bullwhip_manufacturer_upstream_amplification(
            enabled=False,
            target_enterprise_ids=[],
        ),
        "bullwhip_supplier_upstream_pull_through_mode": _build_bullwhip_supplier_upstream_pull_through_mode(
            enabled=False,
            target_enterprise_ids=[],
        ),
    }
    scenario["auto_policy"] = {
        "hr": {
            "idle_threshold": 0.25,
            "recruit_trigger_threshold": 0.88,
            "recruit_ratio": 0.15,
            "min_recruit": 1,
            "max_recruit": 4,
            "severe_utilization_threshold": 0.95,
            "available_worker_floor": 1,
            "failure_recruit_boost": 1,
            "procurement_pending_quantity_trigger": 1,
            "sales_backlog_trigger": 120,
            "sales_fill_rate_trigger": 0.90,
            "production_plan_trigger": 2,
            "inventory_support_threshold": 0.90,
            "inventory_alert_count_trigger": 2,
        },
        "inventory": {
            "expand_trigger_threshold": 0.90,
            "expand_ratio": 0.15,
            "expansion_options": [2000, 5000, 10000],
            "min_expansion_if_nonzero": 500,
        },
    }
    scenario.update(_build_scenario_enterprise_bundle(
        initial_capitals={
            "Supplier": 4_000_000,
            "Manufacturer": 3_500_000,
            "Distributor": 2_500_000,
            "Retailer": 2_000_000,
        },
        staffing_map={
            "Supplier": {"finance": 1, "hr": 1, "procurement": 3, "sales": 3, "inventory": 2},
            "Manufacturer": {"finance": 1, "hr": 1, "procurement": 4, "production": 8, "sales": 4, "inventory": 2},
            "Distributor": {"finance": 1, "hr": 1, "procurement": 3, "sales": 3, "inventory": 2},
            "Retailer": {"finance": 1, "hr": 1, "procurement": 3, "sales": 3, "inventory": 2},
        },
        capacity_map={
            "Supplier": 50_000,
            "Manufacturer": 30_000,
            "Distributor": 1_500,
            "Retailer": 1_200,
        },
        inventory_map={
            "Supplier": [
                {"item_id": "Malt", "quantity": 30_000, "purchase_price": 0.24, "item_type": "raw_material"},
                {"item_id": "Hops", "quantity": 3_000, "purchase_price": 1.45, "item_type": "raw_material"},
                {"item_id": "Yeast", "quantity": 1_500, "purchase_price": 2.00, "item_type": "raw_material"},
            ],
            "Manufacturer": [
                {"item_id": "Malt", "quantity": 18_000, "purchase_price": 0.75, "item_type": "raw_material"},
                {"item_id": "Hops", "quantity": 1_800, "purchase_price": 2.20, "item_type": "raw_material"},
                {"item_id": "Yeast", "quantity": 900, "purchase_price": 3.00, "item_type": "raw_material"},
                {"item_id": "beer", "quantity": 240, "purchase_price": 125, "item_type": "product"},
            ],
            "Distributor": [
                {"item_id": "beer", "quantity": 260, "purchase_price": 230, "item_type": "product"},
            ],
            "Retailer": [
                {"item_id": "beer", "quantity": 220, "purchase_price": 300, "item_type": "product"},
            ],
        },
        supplier_data_by_enterprise={
            "Supplier": [_make_external_supplier({
                "Malt": {"unit_price": 0.24, "quantity": 2_000_000, "min_order_quantity": 5_000},
                "Hops": {"unit_price": 1.45, "quantity": 200_000, "min_order_quantity": 500},
                "Yeast": {"unit_price": 2.00, "quantity": 100_000, "min_order_quantity": 250},
            })],
        },
        inventory_policy_map={
            "Supplier": [
                {"item_id": "Malt", "reorder_point": 14_000, "safety_stock": 7_000},
                {"item_id": "Hops", "reorder_point": 1_400, "safety_stock": 700},
                {"item_id": "Yeast", "reorder_point": 700, "safety_stock": 350},
            ],
            "Manufacturer": [
                {"item_id": "Malt", "reorder_point": 9_000, "safety_stock": 5_000},
                {"item_id": "Hops", "reorder_point": 900, "safety_stock": 500},
                {"item_id": "Yeast", "reorder_point": 450, "safety_stock": 250},
                {"item_id": "beer", "reorder_point": 180, "safety_stock": 90},
            ],
            "Distributor": [{"item_id": "beer", "reorder_point": 170, "safety_stock": 85}],
            "Retailer": [{"item_id": "beer", "reorder_point": 140, "safety_stock": 70}],
        },
        production_recipes_by_enterprise={
            "Manufacturer": {
                **deepcopy(DEFAULT_BEER_RECIPE),
                "labor_cost_per_unit": 3.5,
                "equipment_cost_per_unit": 1.5,
            },
        },
        initial_production_lines_by_enterprise={
            "Manufacturer": [
                {"line_type": "small", "ready_at_start": True},
                {"line_type": "small", "ready_at_start": True},
            ],
        },
        include_inventory_cost=True,
        inventory_cost_enterprise_ids=["Supplier", "Manufacturer", "Distributor", "Retailer"],
    ))
    scenario["integration_profiles"] = {
        "simulation": {"profile": "multi_enterprise"},
        "capabilities": {
            "analysis": {"profile": "historical_diagnosis", "history_days": 12},
            "skill": {
                "context_validation": "warn",
                "output_validation": "warn",
                "max_retries": 2,
                "audit_enabled": True,
            },
        },
    }
    scenario["experiment_design"] = _build_experiment_design_metadata(
        decision_regime="adaptive_long_horizon_agent",
        agent_objective_profile="sustainable_profit_and_resilience",
        constraint_level="observable_external_change",
        experiment_group="E1",
        evidence_status="ready_for_200_turn_run",
        notes="Independent and continuous evolutionary experiment; does not belong to C1/C2/C3 or S0, and does not aim at the re-emergence of lashes.",
    )
    scenario["formal_experiment_config"] = {
        "schema_version": "long_horizon_experiment_config.v1",
        "ready_for_formal_run": True,
        "experiment_family": "long_horizon_evolution",
        "total_steps": 200,
        "warmup_rounds": 10,
        "exclude_tail_rounds": 10,
        "primary_evaluation_window": {"start_round": 10, "end_round_inclusive": 189},
        "phase_windows": [
            {"phase": "baseline", "start_turn": 0, "end_turn": 39},
            {"phase": "raw_material_cost", "start_turn": 40, "end_turn": 79},
            {"phase": "production_cost", "start_turn": 80, "end_turn": 119},
            {"phase": "trade_policy", "start_turn": 120, "end_turn": 159},
            {"phase": "recovery_and_softening", "start_turn": 160, "end_turn": 199},
        ],
        "primary_metrics": [
            "event_detection_latency_turns",
            "response_action_latency_turns",
            "phase_net_profit",
            "phase_cash_change",
            "service_level",
            "confirmed_backlog_quantity",
            "inventory_turnover_proxy",
            "procurement_unit_cost",
            "production_unit_cost",
            "order_variance_ratio",
            "agent_fallback_rate",
        ],
    }
    return scenario


SCENARIO_CONFIGS["long_horizon_evolution"] = _build_long_horizon_evolution_scenario()

SCRIPTED_RULE_VARIANT_SPECS = {
    "beer_game": {
        "mode": "bullwhip",
        "base_quantity": 80,
        "initial_capital_overrides": {
            "Supplier": 600000,
            "Manufacturer": 600000,
            "Distributor": 450000,
            "Retailer": 360000,
        },
        "policy_overrides": {
            "trade": {
                "reject_procurement_proposal_when_unaffordable": True,
                "procurement_accept_cash_buffer": 20000,
            },
            "single_enterprise_rules": {
                "critical_cash_threshold": 30000,
                "cash_guard_threshold": 80000,
                "procurement_budget_cash_share": 0.28,
                "market_development_when_no_available_orders": False,
            },
        },
    },
    "cobweb_convergent": {
        "mode": "cobweb",
        "base_quantity": 80,
        "policy_overrides": {
            "production": {
                "daily_capacity": 260,
                "cobweb_quantity_source": "theoretical_lagged_supply",
                "allow_build_production_line": False,
            },
            "single_enterprise_rules": {"production_daily_capacity": 260},
        },
    },
    "cobweb_neutral": {
        "mode": "cobweb",
        "base_quantity": 90,
        "policy_overrides": {
            "production": {
                "daily_capacity": 260,
                "cobweb_quantity_source": "theoretical_lagged_supply",
                "allow_build_production_line": False,
            },
            "single_enterprise_rules": {"production_daily_capacity": 260},
        },
    },
    "cobweb_divergent": {
        "mode": "cobweb",
        "base_quantity": 100,
        "policy_overrides": {
            "production": {
                "daily_capacity": 260,
                "cobweb_quantity_source": "theoretical_lagged_supply",
                "allow_build_production_line": False,
            },
            "single_enterprise_rules": {"production_daily_capacity": 260},
        },
    },
    "commons_mining_baseline": {
        "mode": "commons",
        "base_quantity": 140,
        "policy_overrides": {
            "production": {"allow_build_production_line": False},
            "single_enterprise_rules": {"market_development_when_no_available_orders": False},
        },
    },
    "commons_mining_collapse_baseline_uniform": {
        "mode": "commons",
        "base_quantity": 260,
        "policy_overrides": {
            "production": {"allow_build_production_line": False},
            "single_enterprise_rules": {"market_development_when_no_available_orders": False},
        },
    },
    "commons_mining_collapse_baseline_differentiated": {
        "mode": "commons",
        "base_quantity": 220,
        "policy_overrides": {
            "production": {"allow_build_production_line": False},
            "single_enterprise_rules": {"market_development_when_no_available_orders": False},
        },
    },
    "commons_mining_financial_damage_baseline": {
        "mode": "commons",
        "base_quantity": 300,
        "policy_overrides": {
            "production": {"allow_build_production_line": False},
            "single_enterprise_rules": {"market_development_when_no_available_orders": False},
        },
    },
    "herding_baseline_peer_visible": {
        "mode": "herding",
        "base_quantity": 100,
        "policy_overrides": {
            "production": {
                "herding_peer_visibility_multiplier": 1.0,
                "herding_no_peer_use_demand_share": True,
            },
        },
    },
    "herding_no_peer_visibility": {
        "mode": "herding",
        "base_quantity": 80,
        "policy_overrides": {
            "production": {
                "herding_no_peer_use_demand_share": True,
                "herding_no_peer_heat_sensitivity": 0.35,
                "herding_no_peer_enterprise_multipliers": {
                    "Sensor_A": 0.30,
                    "Sensor_B": 0.70,
                    "Sensor_C": 1.30,
                    "Sensor_D": 1.70,
                },
            },
        },
    },
    "single_case_01_order_selection": {"mode": "single_case", "base_quantity": 90},
    "single_case_02_material_shortage": {"mode": "single_case", "base_quantity": 0},
    "single_case_03_capacity_bottleneck": {"mode": "single_case", "base_quantity": 70},
    "single_case_04_staff_shortage": {"mode": "single_case", "base_quantity": 0},
    "single_case_05_cash_pressure": {"mode": "single_case", "base_quantity": 20},
}

SCENARIO_CONFIGS.update({
    f"{base_id}_scripted": _scriptify_scenario_config(
        scenario_id=f"{base_id}_scripted",
        scenario_config=SCENARIO_CONFIGS[base_id],
        mode=spec["mode"],
        base_quantity=spec["base_quantity"],
        policy_overrides=spec.get("policy_overrides"),
        initial_capital_overrides=spec.get("initial_capital_overrides"),
    )
    for base_id, spec in SCRIPTED_RULE_VARIANT_SPECS.items()
    if base_id in SCENARIO_CONFIGS
})

PROFIT_OBJECTIVE_VARIANT_SPECS = {
    "beer_game": {
        "scenario_id": "beer_game_profit_objective",
        "objective_weights": {
            "net_profit": 0.34,
            "cash_safety": 0.24,
            "service_level": 0.22,
            "inventory_cost_control": 0.14,
            "risk_buffer": 0.06,
        },
        "notes": 'C3; observe whether the profit/cash/service level target smooths demand shocks and replenishment fluctuations.',
    },
    "herding_baseline_peer_visible": {
        "scenario_id": "herding_peer_visible_profit_objective",
        "objective_weights": {
            "net_profit": 0.36,
            "cash_safety": 0.24,
            "service_level": 0.18,
            "inventory_cost_control": 0.16,
            "risk_buffer": 0.06,
        },
        "notes": 'Herding C3: aggregated peer-production summaries remain visible; agents decide how to use them while balancing profit and inventory risk.',
    },
    "herding_no_peer_visibility": {
        "scenario_id": "herding_no_peer_visibility_profit_objective",
        "objective_weights": {
            "net_profit": 0.38,
            "cash_safety": 0.24,
            "service_level": 0.18,
            "inventory_cost_control": 0.14,
            "risk_buffer": 0.06,
        },
        "notes": 'Herding C3 visibility comparison: agents operate under profit objectives without aggregated peer-production summaries.',
    },
    "commons_mining_collapse_baseline_differentiated": {
        "scenario_id": "commons_mining_profit_objective",
        "objective_weights": {
            "net_profit": 0.30,
            "cash_safety": 0.24,
            "service_level": 0.12,
            "inventory_cost_control": 0.08,
            "resource_sustainability": 0.20,
            "risk_buffer": 0.06,
        },
        "notes": 'Commons C3 configuration for observing whether long-term profit and resource-sustainability objectives mitigate over-extraction.',
    },
    "cobweb_convergent": {
        "scenario_id": "cobweb_convergent_profit_objective",
        "objective_weights": {
            "net_profit": 0.42,
            "cash_safety": 0.22,
            "service_level": 0.16,
            "production_adjustment_stability": 0.12,
            "price_risk_buffer": 0.08,
        },
        "notes": 'Convergent cobweb C3 exploratory configuration; excluded from the primary comparison.',
    },
    "cobweb_divergent": {
        "scenario_id": "cobweb_divergent_profit_objective",
        "objective_weights": {
            "net_profit": 0.42,
            "cash_safety": 0.22,
            "service_level": 0.16,
            "production_adjustment_stability": 0.12,
            "price_risk_buffer": 0.08,
        },
        "notes": (
            'Formal C3 divergent-cobweb configuration: use price as market evidence and make rolling decisions over profit, cash, service and production-adjustment risk.'
        ),
    },
}

SCENARIO_CONFIGS.update({
    spec["scenario_id"]: _profit_objective_scenario_config(
        scenario_id=spec["scenario_id"],
        scenario_config=SCENARIO_CONFIGS[base_id],
        objective_weights=spec["objective_weights"],
        notes=spec["notes"],
    )
    for base_id, spec in PROFIT_OBJECTIVE_VARIANT_SPECS.items()
    if base_id in SCENARIO_CONFIGS
})


def _enable_c2_beer_game_bullwhip_mechanism_defaults() -> None:
    """Enable C2-only bullwhip mechanism aids after C1/C3 variants are cloned."""
    runtime_injection = SCENARIO_CONFIGS.get("beer_game", {}).setdefault("runtime_injection", {})
    runtime_injection["bullwhip_midstream_pass_through_mode"] = _build_bullwhip_midstream_pass_through_mode(
        enabled=True,
        target_enterprise_ids=["Distributor", "Manufacturer", "Supplier"],
        ignore_stale_backlog_block_for_new_supply=True,
        soften_confirmed_backlog_penalty=True,
        fill_rate_caution_threshold=0.45,
        prefer_service_level_over_margin=True,
        minimum_release_ratio_when_inventory_available=0.35,
    )
    runtime_injection["bullwhip_proposal_conversion_acceleration"] = _build_bullwhip_proposal_conversion_acceleration(
        enabled=True,
        target_enterprise_ids=["Distributor", "Manufacturer", "Supplier"],
        prefer_accept_when_due_in_future=True,
        pricing_gap_tolerance_multiplier=1.5,
        future_service_commit_threshold="aggressive",
        reduce_rejection_for_inventory_gap_only=True,
    )
    runtime_injection["bullwhip_manufacturer_upstream_amplification"] = _build_bullwhip_manufacturer_upstream_amplification(
        enabled=True,
        target_enterprise_ids=["Manufacturer"],
        proposal_signal_weight_multiplier=8.0,
        recovery_target_quantity_multiplier=2.2,
        bottleneck_quantity_multiplier=2.6,
        replenishment_soft_cap_multiplier=2.0,
    )
    runtime_injection["bullwhip_supplier_upstream_pull_through_mode"] = _build_bullwhip_supplier_upstream_pull_through_mode(
        enabled=True,
        target_enterprise_ids=["Supplier"],
        ignore_inventory_headroom_skip=True,
        package_coverage_target_rounds_multiplier=1.75,
        package_quantity_multiplier=1.35,
        bottleneck_priority_boost_multiplier=1.4,
        package_priority_boost_multiplier=1.6,
        backlog_pull_through_multiplier=1.25,
    )


_enable_c2_beer_game_bullwhip_mechanism_defaults()


FORMAL_EXPERIMENT_TOTAL_STEPS = 40
FORMAL_COBWEB_SCENARIO_ROLES = {
    "cobweb_convergent_scripted": "mechanism_calibration",
    "cobweb_neutral_scripted": "mechanism_calibration",
    "cobweb_divergent_scripted": "main_comparison",
    "cobweb_divergent": "main_comparison",
    "cobweb_divergent_profit_objective": "main_comparison",
}
FORMAL_EXPERIMENT_SCENARIO_IDS = {
    "beer_game_scripted",
    "beer_game",
    "beer_game_profit_objective",
    "herding_baseline_peer_visible_scripted",
    "herding_baseline_peer_visible",
    "herding_peer_visible_profit_objective",
    "herding_no_peer_visibility_scripted",
    "herding_no_peer_visibility",
    "herding_no_peer_visibility_profit_objective",
    "commons_mining_baseline_scripted",
    "commons_mining_baseline",
    "commons_mining_collapse_baseline_uniform_scripted",
    "commons_mining_collapse_baseline_uniform",
    "commons_mining_collapse_baseline_differentiated_scripted",
    "commons_mining_collapse_baseline_differentiated",
    "commons_mining_financial_damage_baseline_scripted",
    "commons_mining_financial_damage_baseline",
    "commons_mining_collapse_baseline",
    "commons_mining_profit_objective",
    "cobweb_convergent_scripted",
    "cobweb_neutral_scripted",
    "cobweb_divergent_scripted",
    "cobweb_divergent",
    "cobweb_divergent_profit_objective",
}


def _extend_numeric_series(series: List[Any], total_steps: int, fallback: float = 0) -> List[Any]:
    values = list(series or [])
    if not values:
        values = [fallback]
    if len(values) >= int(total_steps):
        return values[:int(total_steps)]
    return values + [values[-1]] * (int(total_steps) - len(values))


def _apply_formal_experiment_defaults() -> None:
    """Freeze C1/C2/C3 main experiment scenarios for direct formal simulation."""
    for scenario_id in FORMAL_EXPERIMENT_SCENARIO_IDS:
        scenario_config = SCENARIO_CONFIGS.get(scenario_id)
        if not scenario_config:
            continue
        simulation = scenario_config.setdefault("simulation", {})
        simulation["service_total_steps"] = FORMAL_EXPERIMENT_TOTAL_STEPS
        simulation["agent_run_steps"] = FORMAL_EXPERIMENT_TOTAL_STEPS
        simulation["beer_game_demand_series"] = _extend_numeric_series(
            simulation.get("beer_game_demand_series"),
            FORMAL_EXPERIMENT_TOTAL_STEPS,
            fallback=0,
        )
        herding_config = simulation.get("herding_config")
        if isinstance(herding_config, dict):
            for series_key in ("true_demand_series", "market_heat_series"):
                if series_key in herding_config:
                    herding_config[series_key] = _extend_numeric_series(
                        herding_config.get(series_key),
                        FORMAL_EXPERIMENT_TOTAL_STEPS,
                        fallback=0,
                    )
        formal_experiment_config = {
            "schema_version": "formal_experiment_config.v1",
            "ready_for_formal_run": True,
            "total_steps": FORMAL_EXPERIMENT_TOTAL_STEPS,
            "warmup_rounds": 3,
            "exclude_tail_rounds": 2,
            "primary_evaluation_window": {
                "start_round": 3,
                "end_round_inclusive": FORMAL_EXPERIMENT_TOTAL_STEPS - 3,
            },
            "notes": (
                "Primary experiments use 40 turns by default. Primary metrics exclude the first 3 warm-up turns "
                "and the final 2 tail turns, while the complete run remains available for robustness checks."
            ),
        }
        if scenario_id in FORMAL_COBWEB_SCENARIO_ROLES:
            cobweb_config = simulation.get("cobweb_config") or {}
            formal_experiment_config.update({
                "experiment_family": "cobweb",
                "experiment_role": FORMAL_COBWEB_SCENARIO_ROLES[scenario_id],
                "parameter_regime": cobweb_config.get("stability_label"),
                "main_comparison_regime": "divergent",
                "main_comparison_scenario_ids": [
                    "cobweb_divergent_scripted",
                    "cobweb_divergent",
                    "cobweb_divergent_profit_objective",
                ],
                "primary_metrics": [
                    "price_deviation_from_equilibrium",
                    "supply_deviation_from_equilibrium",
                    "late_to_early_price_deviation_ratio",
                    "late_to_early_supply_deviation_ratio",
                    "production_adjustment_magnitude",
                    "net_profit",
                    "cash",
                    "fill_rate",
                ],
            })
        scenario_config["formal_experiment_config"] = formal_experiment_config


_apply_formal_experiment_defaults()


def _infer_external_order_targets(runtime_injection: Dict[str, Any]) -> List[str]:
    policy = (runtime_injection or {}).get("external_market_order_policy") or {}
    targets = (
        policy.get("target_enterprise_ids")
        or (runtime_injection or {}).get("external_market_order_enterprise_ids")
        or []
    )
    if isinstance(targets, str):
        targets = [targets]
    if not targets and (runtime_injection or {}).get("external_market_order_enterprise_id"):
        targets = [(runtime_injection or {})["external_market_order_enterprise_id"]]
    return [str(target) for target in targets if target]


def _infer_scheduled_external_product_id(scenario_config: Dict[str, Any]) -> str:
    runtime_injection = scenario_config.get("runtime_injection") or {}
    targets = _infer_external_order_targets(runtime_injection)
    enterprise_specs = scenario_config.get("enterprise_specs") or scenario_config.get("enterprise_configs") or []
    enterprise_by_id = {
        str(item.get("id") or item.get("enterprise_id")): item
        for item in enterprise_specs
        if item.get("id") or item.get("enterprise_id")
    }
    for target in targets:
        salable_products = enterprise_by_id.get(target, {}).get("salable_products_idList") or []
        if isinstance(salable_products, str):
            salable_products = [salable_products]
        if salable_products:
            return str(salable_products[0])
    return "beer"


def _normalize_scheduled_external_product_ids() -> None:
    """Make external-demand product ids explicit in every scheduled-demand scenario."""
    for scenario_config in SCENARIO_CONFIGS.values():
        simulation = scenario_config.setdefault("simulation", {})
        if normalize_market_demand_mode(simulation.get("market_demand_mode")) != MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL:
            continue
        if not simulation.get("beer_game_product_id"):
            simulation["beer_game_product_id"] = _infer_scheduled_external_product_id(scenario_config)


_normalize_scheduled_external_product_ids()


def _infer_experiment_design_for_scenario(
    scenario_id: str,
    scenario_config: Dict[str, Any],
) -> Dict[str, Any]:
    runtime_injection = scenario_config.get("runtime_injection") or {}
    scripted_policy = runtime_injection.get("scripted_rule_policy") or {}
    profit_policy = runtime_injection.get("profit_objective_policy") or {}
    integration_profile = (
        ((scenario_config.get("integration_profiles") or {}).get("simulation") or {})
        .get("profile")
    )
    if scenario_config.get("experiment_design"):
        return deepcopy(scenario_config["experiment_design"])
    if scripted_policy.get("enabled") or str(scenario_id).endswith("_scripted"):
        return _build_experiment_design_metadata(
            decision_regime="scripted_rational",
            agent_objective_profile="environment_mechanism_validation",
            constraint_level="scripted",
            experiment_group="C1",
            evidence_status="ready_to_run",
            notes="The rule rational script group.",
        )
    if profit_policy.get("enabled"):
        return _build_experiment_design_metadata(
            decision_regime="profit_seeking_agent",
            agent_objective_profile=profit_policy.get("objective_profile") or "profit_maximization",
            constraint_level="open_objective",
            experiment_group="C3",
            evidence_status="ready_to_run",
            notes="Profit most rational Agent group.",
        )
    if integration_profile == "single_enterprise" or str(scenario_id).startswith("single_case_"):
        return _build_experiment_design_metadata(
            decision_regime="single_enterprise_validation",
            agent_objective_profile="single_enterprise_diagnostic",
            constraint_level="case_validation",
            experiment_group="S0",
            evidence_status="case_asset_ready",
            notes="Enterprise-level decision capability validation.",
        )
    experiment_family = (scenario_config.get("meta") or {}).get("experiment_family")
    if experiment_family == "architecture_complexity_validation":
        return _build_experiment_design_metadata(
            decision_regime="constrained_agent",
            agent_objective_profile="platform_topology_validation",
            constraint_level="scenario_constrained",
            experiment_group="C2",
            evidence_status="platform_validation_ready",
            notes="Complex surge capacity validation, not as an estimate of the primary institutional effect.",
        )
    return _build_experiment_design_metadata(
        decision_regime="constrained_agent",
        agent_objective_profile="mechanism_reproduction",
        constraint_level="scenario_constrained",
        experiment_group="C2",
        evidence_status="available_or_ready_to_run",
        notes="Binding Agent Mechanism Group.",
    )


def _finalize_scenario_config(
    scenario_id: str,
    scenario_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply the common defaults normally attached during module setup."""
    scenario_config["runtime_injection"].setdefault(
        "scripted_rule_policy",
        _build_scripted_rule_policy(enabled=False),
    )
    scenario_config["runtime_injection"].setdefault(
        "profit_objective_policy",
        _build_profit_objective_policy(enabled=False),
    )
    scenario_config["runtime_injection"].setdefault(
        "long_run_experiment_policy",
        _build_long_run_experiment_policy(enabled=False),
    )
    scenario_config["runtime_injection"].setdefault(
        "single_enterprise_chart_export_policy",
        _build_single_enterprise_chart_export_policy(enabled=False),
    )
    _normalize_external_market_order_policy(scenario_config["runtime_injection"])
    simulation = scenario_config.setdefault("simulation", {})
    if (
        normalize_market_demand_mode(simulation.get("market_demand_mode"))
        == MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL
        and not simulation.get("beer_game_product_id")
    ):
        simulation["beer_game_product_id"] = _infer_scheduled_external_product_id(
            scenario_config
        )
    scenario_config["experiment_design"] = _infer_experiment_design_for_scenario(
        scenario_id,
        scenario_config,
    )
    return scenario_config


for scenario_id, scenario_config in SCENARIO_CONFIGS.items():
    _finalize_scenario_config(scenario_id, scenario_config)


_SINGLE_ENTERPRISE_SCENARIO_LOAD_LOCK = RLock()


def _single_enterprise_base_case_id(scenario_id: str) -> str:
    base_id = scenario_id.removesuffix("_scripted") if scenario_id.endswith("_scripted") else scenario_id
    base_id = SINGLE_ENTERPRISE_CASE_ALIASES.get(base_id, base_id)
    if base_id in SINGLE_ENTERPRISE_CASE_IDS:
        return base_id
    return ""


def _ensure_single_enterprise_scenario_loaded(scenario_id: str) -> None:
    """Load single-case source assets only when that scenario is requested."""
    if scenario_id in SCENARIO_CONFIGS:
        return
    from config.firm_architecture_experiment import SCENARIOS as FIRM_SCENARIOS, build_scenario
    if scenario_id in FIRM_SCENARIOS:
        with _SINGLE_ENTERPRISE_SCENARIO_LOAD_LOCK:
            source = FIRM_SCENARIOS[scenario_id]["source"]
            _ensure_single_enterprise_scenario_loaded(source)
            SCENARIO_CONFIGS[scenario_id] = _finalize_scenario_config(
                scenario_id, build_scenario(scenario_id, SCENARIO_CONFIGS[source]),
            )
        return
    requested_scripted = scenario_id.endswith("_scripted")
    base_id = _single_enterprise_base_case_id(scenario_id)
    if not base_id:
        return
    canonical_scenario_id = f"{base_id}_scripted" if requested_scripted else base_id
    with _SINGLE_ENTERPRISE_SCENARIO_LOAD_LOCK:
        if base_id not in SCENARIO_CONFIGS:
            base_config = _build_single_enterprise_case_scenario(base_id)
            SCENARIO_CONFIGS[base_id] = _finalize_scenario_config(
                base_id,
                base_config,
            )
        if canonical_scenario_id == base_id or canonical_scenario_id in SCENARIO_CONFIGS:
            return
        spec = SCRIPTED_RULE_VARIANT_SPECS[base_id]
        scripted_config = _scriptify_scenario_config(
            scenario_id=canonical_scenario_id,
            scenario_config=SCENARIO_CONFIGS[base_id],
            mode=spec["mode"],
            base_quantity=spec["base_quantity"],
            policy_overrides=spec.get("policy_overrides"),
            initial_capital_overrides=spec.get("initial_capital_overrides"),
        )
        SCENARIO_CONFIGS[canonical_scenario_id] = _finalize_scenario_config(
            canonical_scenario_id,
            scripted_config,
        )


def _ensure_all_single_enterprise_scenarios_loaded() -> None:
    for case_id in SINGLE_ENTERPRISE_CASE_IDS:
        _ensure_single_enterprise_scenario_loaded(case_id)
        _ensure_single_enterprise_scenario_loaded(f"{case_id}_scripted")


def _resolve_active_scenario_id() -> str:
    """Resolve the active scenario, falling back to the default when the environment value is invalid."""
    requested = os.getenv(SIMULATION_SCENARIO_ENV_VAR, DEFAULT_ACTIVE_SCENARIO_ID)
    canonical_base = _single_enterprise_base_case_id(requested)
    resolved = (
        f"{canonical_base}_scripted"
        if canonical_base and requested.endswith("_scripted")
        else canonical_base or requested
    )
    _ensure_single_enterprise_scenario_loaded(resolved)
    return resolved if resolved in SCENARIO_CONFIGS else DEFAULT_ACTIVE_SCENARIO_ID


ACTIVE_SCENARIO_ID = _resolve_active_scenario_id()
ACTIVE_SCENARIO_CONFIG = deepcopy(SCENARIO_CONFIGS[ACTIVE_SCENARIO_ID])
SCENARIO_CONFIG_OVERRIDES: Dict[str, Dict[str, Any]] = {}


def _deep_merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_config(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


DEFAULT_SERVICE_TOTAL_STEPS = ACTIVE_SCENARIO_CONFIG["simulation"]["service_total_steps"]

DEFAULT_AGENT_RUN_STEPS = ACTIVE_SCENARIO_CONFIG["simulation"]["agent_run_steps"]

DEFAULT_STEP_DURATION = ACTIVE_SCENARIO_CONFIG["simulation"]["step_duration"]


DEFAULT_MARKET_DEMAND_MODE = normalize_market_demand_mode(
    ACTIVE_SCENARIO_CONFIG["simulation"]["market_demand_mode"]
)
DEFAULT_BEER_GAME_DEMAND_SERIES = ACTIVE_SCENARIO_CONFIG["simulation"]["beer_game_demand_series"]
DEFAULT_BEER_GAME_CUSTOMER_DELIVERY_LEAD_TIME = ACTIVE_SCENARIO_CONFIG["simulation"]["beer_game_customer_delivery_lead_time"]
DEFAULT_BEER_GAME_UNIT_PRICE = ACTIVE_SCENARIO_CONFIG["simulation"]["beer_game_unit_price"]
DEFAULT_BEER_GAME_PRODUCT_ID = ACTIVE_SCENARIO_CONFIG["simulation"].get("beer_game_product_id", "beer")
DEFAULT_COBWEB_CONFIG = deepcopy(
    ACTIVE_SCENARIO_CONFIG["simulation"].get("cobweb_config", DEFAULT_COBWEB_CONFIG_TEMPLATE)
)
DEFAULT_SHARED_RESOURCE_CONFIG = deepcopy(
    ACTIVE_SCENARIO_CONFIG["simulation"].get("shared_resource_config", DEFAULT_SHARED_RESOURCE_CONFIG_TEMPLATE)
)
DEFAULT_HERDING_CONFIG = deepcopy(
    ACTIVE_SCENARIO_CONFIG["simulation"].get("herding_config", DEFAULT_HERDING_CONFIG_TEMPLATE)
)


DEFAULT_NETWORK_CONFIG = ACTIVE_SCENARIO_CONFIG["simulation"]["network_config"]

DEFAULT_MAX_ENTERPRISE_CONCURRENCY = ACTIVE_SCENARIO_CONFIG["simulation"]["max_enterprise_concurrency"]

DEFAULT_MAX_DEPARTMENT_CONCURRENCY = ACTIVE_SCENARIO_CONFIG["simulation"]["max_department_concurrency"]


EXTERNAL_MARKET_ORDER_ENTERPRISE_ID = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["external_market_order_enterprise_id"]
EXTERNAL_MARKET_ORDER_ENTERPRISE_IDS = ACTIVE_SCENARIO_CONFIG["runtime_injection"].get(
    "external_market_order_enterprise_ids",
    [EXTERNAL_MARKET_ORDER_ENTERPRISE_ID],
)
EXTERNAL_MARKET_ORDER_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["external_market_order_policy"]
SALARY_PAYMENT_INTERVAL_DAYS = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["salary_payment_interval_days"]
SALARY_PAYMENT_ENTERPRISE_IDS = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["salary_payment_enterprise_ids"]
INVENTORY_COST_DAILY_SETTLEMENT_ENABLED = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["inventory_cost_daily_settlement_enabled"]
INVENTORY_COST_ENTERPRISE_IDS = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["inventory_cost_enterprise_ids"]
INVENTORY_COST_EXEMPTION_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["inventory_cost_exemption_policy"]
TOP_TIER_SUPPLY_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["top_tier_supply_policy"]
ANALYST_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["analyst_policy"]
COBWEB_ENTERPRISE_GUIDANCE_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"].get(
    "cobweb_enterprise_guidance_policy",
    _build_cobweb_enterprise_guidance_policy(enabled=False),
)
SHARED_RESOURCE_GOVERNANCE_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"].get(
    "shared_resource_governance_policy",
    _build_shared_resource_governance_policy(enabled=False),
)
HERDING_EXPERIMENT_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"].get(
    "herding_experiment_policy",
    _build_herding_experiment_policy(enabled=False),
)
SINGLE_ENTERPRISE_CASE_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"].get(
    "single_enterprise_case_policy",
    {"enabled": False},
)
SCRIPTED_RULE_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"].get(
    "scripted_rule_policy",
    _build_scripted_rule_policy(enabled=False),
)
PROFIT_OBJECTIVE_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"].get(
    "profit_objective_policy",
    _build_profit_objective_policy(enabled=False),
)
LONG_RUN_EXPERIMENT_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"].get(
    "long_run_experiment_policy",
    _build_long_run_experiment_policy(enabled=False),
)
SAFETY_STOP_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["safety_stop_policy"]
STAFFING_RELAXATION_POLICY = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["staffing_relaxation_policy"]
BULLWHIP_MIDSTREAM_PASS_THROUGH_MODE = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["bullwhip_midstream_pass_through_mode"]
BULLWHIP_PROPOSAL_CONVERSION_ACCELERATION = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["bullwhip_proposal_conversion_acceleration"]
BULLWHIP_MANUFACTURER_UPSTREAM_AMPLIFICATION = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["bullwhip_manufacturer_upstream_amplification"]
BULLWHIP_SUPPLIER_UPSTREAM_PULL_THROUGH_MODE = ACTIVE_SCENARIO_CONFIG["runtime_injection"]["bullwhip_supplier_upstream_pull_through_mode"]


AUTO_HR_POLICY = ACTIVE_SCENARIO_CONFIG["auto_policy"]["hr"]
AUTO_INVENTORY_POLICY = ACTIVE_SCENARIO_CONFIG["auto_policy"]["inventory"]



# - `simulate/simulation_server.py`
# - `core/controller.py`

ACTIVE_ENTERPRISE_SPECS = ACTIVE_SCENARIO_CONFIG["enterprise_specs"]
ACTIVE_ENTERPRISE_CONFIGS = ACTIVE_SCENARIO_CONFIG["enterprise_configs"]


AGENT_ENTERPRISE_LAYOUT = ACTIVE_SCENARIO_CONFIG["agent_enterprise_layout"]





INITIAL_ACTION_BATCHES = ACTIVE_SCENARIO_CONFIG["initial_action_batches"]


DAILY_ACTIONS = ACTIVE_SCENARIO_CONFIG["daily_actions"]


def get_available_scenarios() -> List[Dict[str, Any]]:
    'Return display metadata for every registered scenario.'
    _ensure_all_single_enterprise_scenarios_loaded()
    from config.firm_architecture_experiment import SCENARIOS as FIRM_SCENARIOS
    for scenario_id in FIRM_SCENARIOS:
        _ensure_single_enterprise_scenario_loaded(scenario_id)
    return [
        {
            "scenario_id": scenario_id,
            "name": scenario_config["meta"]["name"],
            "summary": scenario_config["meta"]["summary"],
            "experiment_design": deepcopy(scenario_config.get("experiment_design") or {}),
        }
        for scenario_id, scenario_config in SCENARIO_CONFIGS.items()
    ]


def get_active_scenario_id() -> str:
    """Return the active scenario ID."""
    return _resolve_active_scenario_id()


def get_active_scenario_metadata() -> Dict[str, str]:
    """Return the active scenario name and summary."""
    return deepcopy(get_scenario_config()["meta"])


def get_scenario_config(scenario_id: str = None) -> Dict[str, Any]:
    """Return a complete configuration snapshot for the requested or active scenario."""
    resolved_id = scenario_id or get_active_scenario_id()
    canonical_base = _single_enterprise_base_case_id(resolved_id)
    if canonical_base:
        resolved_id = (
            f"{canonical_base}_scripted"
            if resolved_id.endswith("_scripted")
            else canonical_base
        )
    if resolved_id in SCENARIO_CONFIG_OVERRIDES:
        return deepcopy(SCENARIO_CONFIG_OVERRIDES[resolved_id])
    _ensure_single_enterprise_scenario_loaded(resolved_id)
    if resolved_id not in SCENARIO_CONFIGS:
        raise KeyError(f"Unknown scenario_id: {resolved_id}")
    return deepcopy(SCENARIO_CONFIGS[resolved_id])


@contextmanager
def temporary_scenario_config_override(
    scenario_id: str,
    scenario_config: Dict[str, Any],
):
    """Temporarily override a scenario configuration within the current process."""
    if not scenario_id:
        raise ValueError("scenario_id is required")
    _ensure_single_enterprise_scenario_loaded(scenario_id)
    previous = SCENARIO_CONFIG_OVERRIDES.get(scenario_id)
    SCENARIO_CONFIG_OVERRIDES[scenario_id] = _deep_merge_config(
        SCENARIO_CONFIGS.get(scenario_id, {}),
        scenario_config or {},
    )
    try:
        yield
    finally:
        if previous is None:
            SCENARIO_CONFIG_OVERRIDES.pop(scenario_id, None)
        else:
            SCENARIO_CONFIG_OVERRIDES[scenario_id] = previous


def get_active_enterprise_configs() -> List[Dict[str, Any]]:
    """Return a deep copy of the active scenario's enterprise configurations."""
    return deepcopy(get_scenario_config()["enterprise_configs"])


def get_active_enterprise_specs() -> List[Dict[str, Any]]:
    """Return a deep copy of the active scenario's enterprise specifications."""
    return deepcopy(get_scenario_config()["enterprise_specs"])


def get_default_market_config() -> Dict[str, Any]:
    """Return active market settings shared by environment services and the Agent manager."""
    simulation_config = get_scenario_config()["simulation"]
    return {
        "market_demand_mode": normalize_market_demand_mode(simulation_config["market_demand_mode"]),
        "beer_game_demand_series": list(simulation_config["beer_game_demand_series"]),
        "beer_game_customer_delivery_lead_time": simulation_config["beer_game_customer_delivery_lead_time"],
        "beer_game_unit_price": simulation_config["beer_game_unit_price"],
        "beer_game_product_id": simulation_config.get("beer_game_product_id", "beer"),
        "cobweb_config": deepcopy(simulation_config.get("cobweb_config", DEFAULT_COBWEB_CONFIG_TEMPLATE)),
        "shared_resource_config": deepcopy(simulation_config.get("shared_resource_config", DEFAULT_SHARED_RESOURCE_CONFIG_TEMPLATE)),
        "herding_config": deepcopy(simulation_config.get("herding_config", DEFAULT_HERDING_CONFIG_TEMPLATE)),
    }


def get_default_simulation_config(total_steps: int = None) -> Dict[str, Any]:
    """
    Generate the default environment-simulation configuration.

    Scope of use:
    - `simulate/simulation.py`
    - Initialize each configured enterprise and its enabled modules.
    """
    scenario_config = get_scenario_config()
    simulation_config = scenario_config["simulation"]
    market_config = get_default_market_config()
    return {
        "time_config": {
            "total_steps": total_steps if total_steps is not None else simulation_config["service_total_steps"],
            "step_duration": simulation_config["step_duration"],
        },
        "network_config": deepcopy(simulation_config["network_config"]),
        "environment_config": {
            "market_demand_mode": market_config["market_demand_mode"],
            "beer_game_demand_series": market_config["beer_game_demand_series"],
            "beer_game_customer_delivery_lead_time": market_config["beer_game_customer_delivery_lead_time"],
            "beer_game_unit_price": market_config["beer_game_unit_price"],
            "beer_game_product_id": market_config["beer_game_product_id"],
            "cobweb_config": deepcopy(market_config["cobweb_config"]),
            "shared_resource_config": deepcopy(market_config["shared_resource_config"]),
            "herding_config": deepcopy(market_config["herding_config"]),
        },
    }


def _is_single_enterprise_runtime_config(
    scenario_id: str,
    scenario_config: Dict[str, Any],
) -> bool:
    """Identify a single-enterprise diagnostic configuration at runtime."""
    integration_profile = (
        ((scenario_config.get("integration_profiles") or {}).get("simulation") or {})
        .get("profile")
    )
    return (
        integration_profile == "single_enterprise"
        or str(scenario_id or "").startswith("single_case_")
        or bool(scenario_config.get("single_enterprise_case"))
    )


def get_agent_enterprise_layout() -> List[Dict[str, Any]]:
    """Return the enterprise and department organization for the active Agent scenario."""
    scenario_id = get_active_scenario_id()
    scenario_config = get_scenario_config(scenario_id)
    layout = deepcopy(scenario_config["agent_enterprise_layout"])
    if _is_single_enterprise_runtime_config(scenario_id, scenario_config):
        for enterprise in layout:
            enterprise["departments"] = _normalize_single_enterprise_agent_departments(
                enterprise.get("departments", [])
            )
    return layout


def get_initial_action_batches() -> Dict[str, List[Dict[str, Any]]]:
    """Return active initialization batches consumed by the persistence layer."""
    return deepcopy(get_scenario_config()["initial_action_batches"])


def get_daily_actions() -> List[Dict[str, Any]]:
    """Return fixed daily actions for the active scenario."""
    return deepcopy(get_scenario_config()["daily_actions"])


def get_runtime_injection_config() -> Dict[str, Any]:
    """
    Returns the additional system-injection rules for the Daily phase.

    Scope of use:
    - `simulate/SimulationEnvAdapter.py`
        """
    runtime_injection = get_scenario_config()["runtime_injection"]
    return {
        "external_market_order_enterprise_id": runtime_injection["external_market_order_enterprise_id"],
        "external_market_order_enterprise_ids": list(runtime_injection.get(
            "external_market_order_enterprise_ids",
            [runtime_injection["external_market_order_enterprise_id"]],
        )),
        "external_market_order_policy": deepcopy(runtime_injection["external_market_order_policy"]),
        "salary_payment_interval_days": runtime_injection["salary_payment_interval_days"],
        "salary_payment_enterprise_ids": list(runtime_injection["salary_payment_enterprise_ids"]),
        "inventory_cost_daily_settlement_enabled": runtime_injection["inventory_cost_daily_settlement_enabled"],
        "inventory_cost_enterprise_ids": list(runtime_injection["inventory_cost_enterprise_ids"]),
        "inventory_cost_exemption_policy": deepcopy(runtime_injection["inventory_cost_exemption_policy"]),
        "analyst_policy": deepcopy(runtime_injection["analyst_policy"]),
        "cobweb_enterprise_guidance_policy": deepcopy(runtime_injection.get(
            "cobweb_enterprise_guidance_policy",
            _build_cobweb_enterprise_guidance_policy(enabled=False),
        )),
        "shared_resource_governance_policy": deepcopy(runtime_injection.get(
            "shared_resource_governance_policy",
            _build_shared_resource_governance_policy(enabled=False),
        )),
        "herding_experiment_policy": deepcopy(runtime_injection.get(
            "herding_experiment_policy",
            _build_herding_experiment_policy(enabled=False),
        )),
        "single_enterprise_case_policy": deepcopy(runtime_injection.get(
            "single_enterprise_case_policy",
            {"enabled": False},
        )),
        "scripted_rule_policy": deepcopy(runtime_injection.get(
            "scripted_rule_policy",
            _build_scripted_rule_policy(enabled=False),
        )),
        "profit_objective_policy": deepcopy(runtime_injection.get(
            "profit_objective_policy",
            _build_profit_objective_policy(enabled=False),
        )),
        "long_run_experiment_policy": deepcopy(runtime_injection.get(
            "long_run_experiment_policy",
            _build_long_run_experiment_policy(enabled=False),
        )),
        "external_environment_policy": deepcopy(runtime_injection.get(
            "external_environment_policy",
            {"enabled": False},
        )),
        "single_enterprise_chart_export_policy": deepcopy(runtime_injection.get(
            "single_enterprise_chart_export_policy",
            _build_single_enterprise_chart_export_policy(enabled=False),
        )),
        "top_tier_supply_policy": deepcopy(runtime_injection["top_tier_supply_policy"]),
        "safety_stop_policy": deepcopy(runtime_injection["safety_stop_policy"]),
        "staffing_relaxation_policy": deepcopy(runtime_injection["staffing_relaxation_policy"]),
        "bullwhip_midstream_pass_through_mode": deepcopy(runtime_injection["bullwhip_midstream_pass_through_mode"]),
        "bullwhip_proposal_conversion_acceleration": deepcopy(runtime_injection["bullwhip_proposal_conversion_acceleration"]),
        "bullwhip_manufacturer_upstream_amplification": deepcopy(runtime_injection["bullwhip_manufacturer_upstream_amplification"]),
        "bullwhip_supplier_upstream_pull_through_mode": deepcopy(runtime_injection["bullwhip_supplier_upstream_pull_through_mode"]),
    }


def get_auto_policy_config() -> Dict[str, Dict[str, Any]]:
    """
    Return automatic HR and inventory policies.

    Scope of use:
    - `agent/multi_tenant_utils.py`
    """
    auto_policy = get_scenario_config()["auto_policy"]
    return {
        "hr": deepcopy(auto_policy["hr"]),
        "inventory": deepcopy(auto_policy["inventory"]),
    }


def get_static_command_payloads() -> Dict[str, List[Dict[str, Any]]]:
    """Return payloads written to the three static command files."""
    payloads = get_initial_action_batches()
    payloads["daily_action"] = get_daily_actions()
    return payloads


def get_available_single_enterprise_cases() -> List[Dict[str, Any]]:
    """Returns the registered single-enterprise standard case metadata."""
    cases: List[Dict[str, Any]] = []
    for case_id in SINGLE_ENTERPRISE_CASE_IDS:
        _ensure_single_enterprise_scenario_loaded(case_id)
        scenario = SCENARIO_CONFIGS[case_id]
        cases.append(
            {
                "case_id": case_id,
                "scenario_id": case_id,
                "name": scenario["meta"]["name"],
                "summary": scenario["meta"]["summary"],
                "primary_issue": scenario["single_enterprise_case"]["primary_issue"],
                "expected_primary_departments": list(
                    scenario["single_enterprise_case"]["expected_primary_departments"]
                ),
                "preferred_actions": list(
                    scenario["single_enterprise_case"]["preferred_actions"]
                ),
                "discouraged_actions": list(
                    scenario["single_enterprise_case"]["discouraged_actions"]
                ),
            }
        )
    return cases


def get_single_enterprise_case_config(
    case_id: str,
    finance_agent_enabled: bool = False,
) -> Dict[str, Any]:
    """Return the complete scenario configuration for a single-enterprise case."""
    case_id = SINGLE_ENTERPRISE_CASE_ALIASES.get(case_id, case_id)
    if case_id not in SINGLE_ENTERPRISE_CASE_IDS:
        raise KeyError(f"Unknown single enterprise case_id: {case_id}")
    if finance_agent_enabled:
        return _build_single_enterprise_case_scenario(
            case_id,
            finance_agent_enabled=True,
        )
    return get_scenario_config(case_id)
