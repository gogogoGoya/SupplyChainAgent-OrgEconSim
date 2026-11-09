from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from config.simulation_preset_config import (
    MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL,
    get_active_scenario_id,
    normalize_market_demand_mode,
)


POLICY_BLOCK_NAMES = [
    "top_tier_supply_policy",
    "staffing_relaxation_policy",
    "inventory_cost_exemption_policy",
    "bullwhip_midstream_pass_through_mode",
    "bullwhip_proposal_conversion_acceleration",
    "bullwhip_manufacturer_upstream_amplification",
    "bullwhip_supplier_upstream_pull_through_mode",
    "safety_stop_policy",
    "analyst_policy",
    "cobweb_enterprise_guidance_policy",
    "shared_resource_governance_policy",
    "herding_experiment_policy",
    "profit_objective_policy",
    "long_run_experiment_policy",
    "external_environment_policy",
]


RELEVANT_POLICY_NAMES_BY_DEPT = {
    "procurement": [
        "top_tier_supply_policy",
        "bullwhip_proposal_conversion_acceleration",
        "bullwhip_manufacturer_upstream_amplification",
        "bullwhip_supplier_upstream_pull_through_mode",
        "staffing_relaxation_policy",
        "profit_objective_policy",
        "long_run_experiment_policy",
        "external_environment_policy",
    ],
    "sales": [
        "bullwhip_midstream_pass_through_mode",
        "bullwhip_proposal_conversion_acceleration",
        "staffing_relaxation_policy",
        "shared_resource_governance_policy",
        "herding_experiment_policy",
        "profit_objective_policy",
        "long_run_experiment_policy",
        "external_environment_policy",
    ],
    "production": [
        "bullwhip_manufacturer_upstream_amplification",
        "staffing_relaxation_policy",
        "cobweb_enterprise_guidance_policy",
        "shared_resource_governance_policy",
        "herding_experiment_policy",
        "profit_objective_policy",
        "long_run_experiment_policy",
        "external_environment_policy",
    ],
    "inventory": [
        "inventory_cost_exemption_policy",
        "staffing_relaxation_policy",
        "shared_resource_governance_policy",
        "herding_experiment_policy",
        "profit_objective_policy",
        "long_run_experiment_policy",
        "external_environment_policy",
    ],
    "hr": [
        "staffing_relaxation_policy",
        "profit_objective_policy",
        "long_run_experiment_policy",
        "external_environment_policy",
    ],
    "finance": [
        "top_tier_supply_policy",
        "inventory_cost_exemption_policy",
        "safety_stop_policy",
        "shared_resource_governance_policy",
        "herding_experiment_policy",
        "profit_objective_policy",
        "long_run_experiment_policy",
        "external_environment_policy",
    ],
}


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def compact_policy_payload(policy_block: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only decision-relevant policy params for agent-facing context."""
    if not isinstance(policy_block, dict):
        return {}
    hidden_keys = {
        "enabled",
        "target_enterprise_ids",
        "enterprise_ids",
        "target_role_tags",
        "credit_enabled_supplier_types",
        "events",
        "targets",
        "agent_visibility",
    }
    return {
        key: deepcopy(value)
        for key, value in policy_block.items()
        if key not in hidden_keys
    }


def build_shared_resource_policy_payload(
    *,
    enterprise_id: str,
    round_id: int,
    shared_resource_config: Dict[str, Any],
    shared_resource_state: Dict[str, Any],
    shared_resource_history: List[Dict[str, Any]],
    governance_policy: Dict[str, Any],
    strategy_profile: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Build the compact shared-resource signal visible to Agent departments."""
    history = [
        item for item in (shared_resource_history or [])
        if isinstance(item, dict)
    ]
    latest_metrics = None
    if history:
        eligible_records = [
            item for item in history
            if item.get("round") is None or int(item.get("round") or 0) <= int(round_id)
        ]
        latest_metrics = eligible_records[-1] if eligible_records else history[-1]

    enterprise_records = _safe_dict((latest_metrics or {}).get("enterprises"))
    own_record = _safe_dict(enterprise_records.get(enterprise_id))
    peer_records = {
        peer_id: {
            "planned_acquisition": record.get("planned_acquisition"),
            "effective_acquisition": record.get("effective_acquisition"),
            "gross_profit": record.get("gross_profit"),
        }
        for peer_id, record in enterprise_records.items()
        if peer_id != enterprise_id and isinstance(record, dict)
    }
    governance_params = _safe_dict((governance_policy or {}).get("params"))
    governance_enabled = bool((governance_policy or {}).get("enabled"))
    governance_mode = governance_params.get("mode") or "none"

    return {
        "enabled": True,
        "resource": {
            "resource_id": shared_resource_config.get("resource_id"),
            "resource_label": shared_resource_config.get("resource_label"),
            "product_id": shared_resource_config.get("product_id"),
            "target_enterprise_ids": list(shared_resource_config.get("target_enterprise_ids") or []),
            "sustainable_total_acquisition": shared_resource_config.get("sustainable_acquisition_per_round"),
            "regeneration_quantity": shared_resource_config.get("regeneration_quantity"),
            "unit_acquisition_cost": shared_resource_config.get("unit_acquisition_cost"),
            "base_unit_price": shared_resource_config.get("base_unit_price"),
            "collapse_threshold_ratio": shared_resource_config.get("collapse_threshold_ratio"),
            "warning_threshold_ratio": shared_resource_config.get("warning_threshold_ratio"),
            "apply_acquisition_cost_to_finance": shared_resource_config.get("apply_acquisition_cost_to_finance"),
            "acquisition_cost_finance_category": shared_resource_config.get("acquisition_cost_finance_category"),
            "constrain_production_output_to_effective_acquisition": shared_resource_config.get(
                "constrain_production_output_to_effective_acquisition"
            ),
            "external_order_quantity_mode": shared_resource_config.get("external_order_quantity_mode"),
            "apply_breach_penalty_to_finance": shared_resource_config.get("apply_breach_penalty_to_finance"),
            "breach_penalty_per_unit": shared_resource_config.get("breach_penalty_per_unit"),
            "breach_penalty_finance_category": shared_resource_config.get("breach_penalty_finance_category"),
        },
        "current_state": {
            "resource_stock": shared_resource_state.get("resource_stock"),
            "resource_capacity": shared_resource_state.get("resource_capacity"),
            "resource_stock_ratio": shared_resource_state.get("resource_stock_ratio"),
            "resource_quality": shared_resource_state.get("resource_quality"),
            "warning_level": shared_resource_state.get("warning_level"),
            "last_round_total_acquisition": shared_resource_state.get("last_round_total_acquisition"),
            "sustainable_total_acquisition": shared_resource_state.get("sustainable_total_acquisition"),
        },
        "latest_round_metrics": {
            "round": (latest_metrics or {}).get("round"),
            "total_planned_acquisition": (latest_metrics or {}).get("total_planned_acquisition"),
            "total_effective_acquisition": (latest_metrics or {}).get("total_effective_acquisition"),
            "sustainable_total_acquisition": (latest_metrics or {}).get("sustainable_total_acquisition"),
            "overuse_quantity": (latest_metrics or {}).get("overuse_quantity"),
            "resource_shortage_compression_ratio": (latest_metrics or {}).get("resource_shortage_compression_ratio"),
            "unit_price": (latest_metrics or {}).get("unit_price"),
            "unit_acquisition_cost": (latest_metrics or {}).get("unit_acquisition_cost"),
        },
        "own_last_round": {
            "planned_acquisition": own_record.get("planned_acquisition"),
            "effective_acquisition": own_record.get("effective_acquisition"),
            "gross_profit": own_record.get("gross_profit"),
            "acquisition_source": own_record.get("acquisition_source"),
        },
        "peer_last_round_acquisitions": peer_records,
        "governance": {
            "enabled": governance_enabled,
            "mode": governance_mode,
            "quota_per_enterprise": governance_params.get("quota_per_enterprise"),
            "quota_soft_limit": governance_params.get("quota_soft_limit"),
            "over_quota_penalty_per_unit": governance_params.get("over_quota_penalty_per_unit"),
            "resource_tax_per_unit": governance_params.get("resource_tax_per_unit"),
            "shared_sustainability_target": governance_params.get("shared_sustainability_target"),
        },
        "strategy_profile": deepcopy(strategy_profile or {}),
        "decision_contract": {
            "production_plan_quantity_meaning": "resource_or_product_acquisition_quantity",
            "baseline_guidance": (
                "In mode=none, sustainability values are visible context, not a hard cap."
            ),
            "avoid_domain_specific_terms": True,
        },
    }


def build_herding_signal_payload(
    *,
    enterprise_id: str,
    round_id: int,
    herding_config: Dict[str, Any],
    herding_state: Dict[str, Any],
    herding_history: List[Dict[str, Any]],
    experiment_policy: Dict[str, Any],
    strategy_profile: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Build the compact peer/market signal visible to herding-mode agents."""
    history = [
        item for item in (herding_history or [])
        if isinstance(item, dict)
    ]
    latest_metrics = None
    if history:
        eligible_records = [
            item for item in history
            if item.get("round") is None or int(item.get("round") or 0) <= int(round_id)
        ]
        latest_metrics = eligible_records[-1] if eligible_records else history[-1]

    peer_summary = (
        _safe_dict(herding_state.get("peer_summary"))
        or _safe_dict((latest_metrics or {}).get("peer_summary"))
    )
    enterprise_records = _safe_dict((latest_metrics or {}).get("enterprises"))
    own_record = _safe_dict(enterprise_records.get(enterprise_id))
    experiment_params = _safe_dict((experiment_policy or {}).get("params"))
    peer_visible = bool(herding_config.get("peer_visibility_enabled"))
    if not peer_visible:
        peer_summary = {
            "visible": False,
            "lag_rounds": herding_config.get("peer_visibility_lag_rounds"),
            "reason": "peer_visibility_disabled_by_scenario_config",
        }
    unit_price = herding_config.get("base_unit_price")
    unit_cost = herding_config.get("unit_cost_reference")
    estimated_margin = None
    try:
        if unit_price is not None and unit_cost is not None:
            estimated_margin = float(unit_price) - float(unit_cost)
    except (TypeError, ValueError):
        estimated_margin = None

    return {
        "enabled": True,
        "signal_type": "aggregated_peer_market_reference",
        "product_id": herding_config.get("product_id") or herding_state.get("product_id"),
        "target_enterprise_ids": list(herding_config.get("target_enterprise_ids") or []),
        "market_signal": {
            "market_heat": herding_state.get("market_heat"),
            "market_heat_label": herding_state.get("market_heat_label"),
            "visible_demand_signal": herding_state.get("visible_demand_signal"),
            "trend_direction": herding_state.get("trend_direction"),
            "noise_level": herding_state.get("noise_level"),
        },
        "peer_summary": peer_summary,
        "latest_round_metrics": {
            "round": (latest_metrics or {}).get("round"),
            "market_heat": (latest_metrics or {}).get("market_heat"),
            "average_planned_quantity": (
                (latest_metrics or {}).get("average_planned_quantity") if peer_visible else None
            ),
            "planned_quantity_dispersion": (
                (latest_metrics or {}).get("planned_quantity_dispersion") if peer_visible else None
            ),
            "synchronization_index": (
                (latest_metrics or {}).get("synchronization_index") if peer_visible else None
            ),
            "overproduction_ratio": (
                (latest_metrics or {}).get("overproduction_ratio") if peer_visible else None
            ),
            "inventory_pressure": (
                (latest_metrics or {}).get("inventory_pressure") if peer_visible else None
            ),
            "cash_stress_index": (latest_metrics or {}).get("cash_stress_index"),
            "peer_metrics_hidden": not peer_visible,
        },
        "own_last_round": {
            "planned_quantity": own_record.get("planned_quantity"),
            "actual_output": own_record.get("actual_output"),
            "sales_quantity": own_record.get("sales_quantity"),
            "ending_inventory": own_record.get("ending_inventory"),
            "cash_balance": own_record.get("cash_balance"),
        },
        "unit_economics": {
            "base_unit_price": unit_price,
            "unit_cost_reference": unit_cost,
            "estimated_unit_margin": estimated_margin,
            "source": "herding_config",
        },
        "visibility_policy": {
            "peer_visibility_enabled": herding_config.get("peer_visibility_enabled"),
            "peer_visibility_lag_rounds": herding_config.get("peer_visibility_lag_rounds"),
            "peer_signal_source": "environment_aggregated_metrics_only",
            "raw_peer_file_access_allowed": False,
            "peer_aggregate_metrics_visible": peer_visible,
        },
        "experiment_policy": experiment_params,
        "strategy_profile": deepcopy(strategy_profile or {}),
        "decision_contract": {
            "signal_role": "market_expectation_reference_not_command",
            "primary_use": (
                "infer demand trend from market heat only"
                if not peer_visible
                else "infer demand trend and peer pressure from aggregated signals"
            ),
            "must_combine_with": ["cash", "inventory", "capacity", "real_orders"],
            "forbid_raw_peer_files": True,
            "forbid_peer_aggregate_metrics": not peer_visible,
            "direct_production_empty_recipe_allowed": True,
            "legacy_recovery_guard_role": "feasibility_context_only",
            "legacy_margin_guard_role": "risk_context_only",
        },
    }


def is_runtime_switch_enabled(
    policy_block: Dict[str, Any],
    *,
    enterprise_id: str,
    role_tags: List[str],
) -> bool:
    if not policy_block or not policy_block.get("enabled"):
        return False
    target_enterprise_ids = set(
        policy_block.get("target_enterprise_ids")
        or policy_block.get("enterprise_ids")
        or []
    )
    target_role_tags = set(policy_block.get("target_role_tags") or [])
    enterprise_match = (not target_enterprise_ids) or enterprise_id in target_enterprise_ids
    role_match = (not target_role_tags) or bool(set(role_tags or []) & target_role_tags)
    return enterprise_match and role_match


def build_effective_policy_blocks(
    *,
    runtime_injection_config: Dict[str, Any],
    enterprise_id: str,
    role_tags: List[str],
) -> Dict[str, Dict[str, Any]]:
    effective_policy_blocks: Dict[str, Dict[str, Any]] = {}
    for policy_name in POLICY_BLOCK_NAMES:
        block = _safe_dict((runtime_injection_config or {}).get(policy_name))
        if policy_name == "analyst_policy":
            effective_policy_blocks[policy_name] = {
                "enabled": True,
                "params": compact_policy_payload(block),
            }
            continue
        effective_policy_blocks[policy_name] = {
            "enabled": is_runtime_switch_enabled(
                block,
                enterprise_id=enterprise_id,
                role_tags=role_tags,
            ),
            "params": compact_policy_payload(block),
        }
    return effective_policy_blocks


def build_department_policy_context(
    *,
    dept: str,
    round_id: int,
    enterprise_id: str,
    enterprise_spec: Dict[str, Any],
    agent_simulation_context: Dict[str, Any],
    runtime_injection_config: Dict[str, Any],
    scenario_id: str = None,
) -> Dict[str, Any]:
    role_tags = list((enterprise_spec or {}).get("role_tags") or [])
    policy_tags = list((enterprise_spec or {}).get("policy_tags") or [])
    enabled_functions = list((enterprise_spec or {}).get("enabled_functions") or [])
    strategy_profile = _safe_dict((enterprise_spec or {}).get("strategy_profile"))
    policy_tag_set = set(policy_tags)

    effective_policy_blocks = build_effective_policy_blocks(
        runtime_injection_config=runtime_injection_config,
        enterprise_id=enterprise_id,
        role_tags=role_tags,
    )

    normalized_market_mode = normalize_market_demand_mode(
        (agent_simulation_context or {}).get("market_demand_mode")
    )
    normalized_trade_mode = normalize_market_demand_mode(
        (agent_simulation_context or {}).get("trade_mode")
    )
    scheduled_external_demand_mode = bool(
        (agent_simulation_context or {}).get("is_scheduled_external_demand_mode")
        or (
            normalized_market_mode == MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL
            and normalized_trade_mode == MARKET_DEMAND_MODE_SCHEDULED_EXTERNAL
        )
    )
    beer_game_mode = bool(
        (agent_simulation_context or {}).get("is_beer_game_mode")
        or scheduled_external_demand_mode
    )
    cobweb_mode = bool(
        (agent_simulation_context or {}).get("is_cobweb_mode")
        or (agent_simulation_context or {}).get("market_demand_mode") == "cobweb"
    )
    cobweb_config = _safe_dict((agent_simulation_context or {}).get("cobweb_config"))
    cobweb_production_response_mode = (
        cobweb_config.get("production_response_mode")
        or "agent_endogenous"
    )
    cobweb_scripted_formula = cobweb_production_response_mode == "scripted_formula"
    cobweb_hard_cap_enabled = cobweb_production_response_mode in {"guardrail_cap", "auto_controlled"}
    cobweb_quantity_overrides_recovery = (
        cobweb_hard_cap_enabled or cobweb_scripted_formula
    )
    shared_resource_config = _safe_dict((agent_simulation_context or {}).get("shared_resource_config"))
    shared_resource_state = _safe_dict((agent_simulation_context or {}).get("shared_resource_state"))
    shared_resource_history = [
        item for item in ((agent_simulation_context or {}).get("shared_resource_history") or [])
        if isinstance(item, dict)
    ]
    shared_resource_target_enterprises = set(shared_resource_config.get("target_enterprise_ids") or [])
    shared_resource_mode = bool(
        (agent_simulation_context or {}).get("is_shared_resource_mode")
        or (agent_simulation_context or {}).get("market_demand_mode") == "shared_resource_market"
    )
    shared_resource_participant = (
        shared_resource_mode
        and (
            not shared_resource_target_enterprises
            or enterprise_id in shared_resource_target_enterprises
            or "shared_resource_acquisition" in policy_tag_set
        )
    )
    herding_config = _safe_dict((agent_simulation_context or {}).get("herding_config"))
    herding_peer_visible = bool(herding_config.get("peer_visibility_enabled"))
    herding_state = _safe_dict((agent_simulation_context or {}).get("herding_state"))
    herding_history = [
        item for item in ((agent_simulation_context or {}).get("herding_history") or [])
        if isinstance(item, dict)
    ]
    herding_target_enterprises = set(herding_config.get("target_enterprise_ids") or [])
    herding_mode = bool(
        (agent_simulation_context or {}).get("is_herding_mode")
        or (agent_simulation_context or {}).get("market_demand_mode") == "herding_market"
        or herding_config.get("enabled")
    )
    herding_participant = (
        herding_mode
        and (
            not herding_target_enterprises
            or enterprise_id in herding_target_enterprises
            or "herding_participant" in policy_tag_set
        )
    )
    external_environment = _safe_dict(
        (agent_simulation_context or {}).get("external_environment")
    )
    external_environment_enabled = bool(external_environment.get("enabled"))
    supplier_selection_policy = _safe_dict(
        (runtime_injection_config or {}).get("single_enterprise_supplier_selection_policy")
    )
    supplier_selection_enabled = is_runtime_switch_enabled(
        supplier_selection_policy,
        enterprise_id=enterprise_id,
        role_tags=role_tags,
    )
    active_modes = {
        "scheduled_external_demand": scheduled_external_demand_mode,
        "beer_game": beer_game_mode,
        "cobweb": cobweb_mode,
        "shared_resource": shared_resource_participant,
        "herding": herding_participant,
        "external_market_connected": "external_market_connected" in policy_tag_set,
        "distribution_only": "distribution_only" in policy_tag_set,
        "production_enabled": "production_enabled" in policy_tag_set,
        "recipe_driven_procurement": "recipe_driven_procurement" in policy_tag_set,
        "top_tier_supply_enabled": effective_policy_blocks["top_tier_supply_policy"]["enabled"],
        "single_enterprise_supplier_selection": supplier_selection_enabled,
        "shared_resource_governance": effective_policy_blocks["shared_resource_governance_policy"]["enabled"],
        "herding_experiment_policy": effective_policy_blocks["herding_experiment_policy"]["enabled"],
        "profit_objective": effective_policy_blocks["profit_objective_policy"]["enabled"],
        "long_run_experiment": effective_policy_blocks["long_run_experiment_policy"]["enabled"],
        "external_environment_evolution": external_environment_enabled,
        "staffing_relaxation": effective_policy_blocks["staffing_relaxation_policy"]["enabled"],
        "inventory_cost_exemption": effective_policy_blocks["inventory_cost_exemption_policy"]["enabled"],
        "bullwhip_midstream_pass_through": effective_policy_blocks["bullwhip_midstream_pass_through_mode"]["enabled"],
        "bullwhip_proposal_conversion_acceleration": effective_policy_blocks["bullwhip_proposal_conversion_acceleration"]["enabled"],
        "bullwhip_manufacturer_upstream_amplification": effective_policy_blocks["bullwhip_manufacturer_upstream_amplification"]["enabled"],
        "bullwhip_supplier_upstream_pull_through": effective_policy_blocks["bullwhip_supplier_upstream_pull_through_mode"]["enabled"],
    }
    single_case_policy = runtime_injection_config.get("single_enterprise_case_policy") or {}
    single_case_enabled = bool(single_case_policy.get("enabled"))
    scripted_policy = runtime_injection_config.get("scripted_rule_policy") or {}
    single_enterprise_rules = _safe_dict(scripted_policy.get("single_enterprise_rules"))
    market_growth_policy = {}
    if single_case_enabled:
        market_growth_recommendation_mode = str(
            single_enterprise_rules.get(
                "agent_market_growth_recommendation_mode",
                "computed_recommendation",
            )
            or "computed_recommendation"
        )
        market_growth_policy = {
            "enabled": True,
            "recommendation_mode": market_growth_recommendation_mode,
            "active_market_soft_cap": single_enterprise_rules.get(
                "market_development_active_market_soft_cap",
                single_enterprise_rules.get(
                    "max_market_order_sources_per_round",
                    4,
                ),
            ),
            "low_order_entry_threshold": single_enterprise_rules.get(
                "market_development_low_order_entry_threshold",
                1,
            ),
            "reference_order_quantity": single_enterprise_rules.get(
                "market_development_reference_order_quantity",
                single_enterprise_rules.get("production_quantity", 40),
            ),
            "inventory_order_multiple": single_enterprise_rules.get(
                "market_development_inventory_order_multiple",
                3,
            ),
            "surplus_inventory_multiplier": single_enterprise_rules.get(
                "market_development_surplus_inventory_multiplier",
                2.0,
            ),
            "allow_when_backlog_covered": bool(
                single_enterprise_rules.get(
                    "market_development_allow_when_backlog_covered",
                    True,
                )
            ),
            "development_cost": single_enterprise_rules.get(
                "market_development_cost",
                50000,
            ),
            "development_lead_time_rounds": single_enterprise_rules.get(
                "market_development_lead_time_rounds",
                1,
            ),
            "development_workers": single_enterprise_rules.get(
                "market_development_workers",
                1,
            ),
            "order_selection_reference_unit_cost": single_enterprise_rules.get(
                "order_selection_reference_unit_cost"
            ),
            "order_selection_low_margin_ratio": single_enterprise_rules.get(
                "order_selection_low_margin_ratio",
                0.15,
            ),
            "order_selection_low_margin_unit_threshold": single_enterprise_rules.get(
                "order_selection_low_margin_unit_threshold",
                80,
            ),
            "order_selection_large_commitment_ratio": single_enterprise_rules.get(
                "order_selection_large_commitment_ratio",
                0.45,
            ),
        }
    diagnostic_rounds = {
        int(item)
        for item in (single_case_policy.get("diagnostic_evaluation_rounds") or [])
        if str(item).lstrip("-").isdigit()
    }
    handoff_day = int(single_case_policy.get("handoff_day") or 0)
    current_round = int(round_id or 0)
    diagnostic_window_active = single_case_enabled and (
        current_round in diagnostic_rounds
        or current_round >= handoff_day
    )
    agent_visible_single_case_policy = {
        "enabled": True,
        "family": "single_enterprise_diagnostic",
        "diagnostic_window_active": diagnostic_window_active,
        "post_handoff_recovery_active": (
            single_case_enabled
            and current_round >= handoff_day
        ),
        "evaluation_mode": single_case_policy.get("evaluation_mode", "diagnostic_window"),
        "recommended_total_steps": single_case_policy.get("recommended_total_steps"),
        "instruction": (
            "This is a single-enterprise capability diagnostic. Department decisions must use "
            "only real-time orders, inventory, cash, staff, capacity, supplier and production "
            "feasibility signals, and must not rely on experiment metadata outside the current "
            "state files."
        ),
    } if single_case_enabled else {}
    visible_scenario_id = (
        "single_enterprise_diagnostic"
        if single_case_enabled
        else (scenario_id or get_active_scenario_id())
    )
    visible_role_tags = list(role_tags)
    visible_policy_tags = list(policy_tags)
    visible_strategy_profile = deepcopy(strategy_profile or {})
    if single_case_enabled:
        visible_role_tags = [
            "single_enterprise_diagnostic"
            if str(tag) == "single_enterprise_case" else tag
            for tag in visible_role_tags
        ]
        visible_policy_tags = [
            "single_enterprise_diagnostic"
            if str(tag) == "single_enterprise_case" else tag
            for tag in visible_policy_tags
        ]
        visible_strategy_profile = {
            key: value
            for key, value in visible_strategy_profile.items()
            if key not in {"case_id", "primary_issue", "expected_primary_departments"}
        }
        if str(visible_strategy_profile.get("scope") or "") == "single_enterprise_case":
            visible_strategy_profile["scope"] = "single_enterprise_diagnostic"
        if str(visible_strategy_profile.get("decision_style") or "") == "diagnose_primary_constraint_before_expansion":
            visible_strategy_profile["decision_style"] = "state_driven_operating_diagnosis"

    decision_weights = {}
    manufacturer_amp = effective_policy_blocks["bullwhip_manufacturer_upstream_amplification"]
    if dept == "procurement" and manufacturer_amp["enabled"]:
        decision_weights.update({
            key: manufacturer_amp["params"].get(key)
            for key in (
                "proposal_signal_weight_multiplier",
                "recovery_target_quantity_multiplier",
                "bottleneck_quantity_multiplier",
                "replenishment_soft_cap_multiplier",
            )
            if key in manufacturer_amp["params"]
        })
    supplier_pull = effective_policy_blocks["bullwhip_supplier_upstream_pull_through_mode"]
    if dept == "procurement" and supplier_pull["enabled"]:
        decision_weights.update({
            key: supplier_pull["params"].get(key)
            for key in (
                "package_coverage_target_rounds_multiplier",
                "package_quantity_multiplier",
                "bottleneck_priority_boost_multiplier",
                "package_priority_boost_multiplier",
                "backlog_pull_through_multiplier",
            )
            if key in supplier_pull["params"]
        })
    proposal_acceleration = effective_policy_blocks["bullwhip_proposal_conversion_acceleration"]
    if dept in {"procurement", "sales"} and proposal_acceleration["enabled"]:
        decision_weights.update({
            key: proposal_acceleration["params"].get(key)
            for key in (
                "pricing_gap_tolerance_multiplier",
                "future_service_commit_threshold",
            )
            if key in proposal_acceleration["params"]
        })
    cobweb_guidance = effective_policy_blocks["cobweb_enterprise_guidance_policy"]
    if cobweb_mode and dept in {"production", "sales", "procurement"} and cobweb_guidance["enabled"]:
        cobweb_weight_keys = (
            "backlog_target_weight",
            "recovery_guard_target_weight",
            "capacity_utilization_target_weight",
            "inventory_target_weight",
            "service_level_target_weight",
            "max_recommended_quantity_deviation",
        )
        if cobweb_production_response_mode == "agent_endogenous":
            cobweb_weight_keys = tuple(
                key for key in cobweb_weight_keys
                if key != "max_recommended_quantity_deviation"
            )
        decision_weights.update({
            f"cobweb_{key}": cobweb_guidance["params"].get(key)
            for key in cobweb_weight_keys
            if key in cobweb_guidance["params"]
        })
    profit_objective = effective_policy_blocks["profit_objective_policy"]
    cobweb_profit_balanced = bool(
        cobweb_mode
        and profit_objective["enabled"]
        and cobweb_config.get("decision_profile") == "profit_balanced"
    )
    if profit_objective["enabled"]:
        objective_weights = _safe_dict(profit_objective["params"].get("objective_weights"))
        decision_weights.update({
            f"profit_objective_{key}": value
            for key, value in objective_weights.items()
        })
    long_run_experiment = effective_policy_blocks["long_run_experiment_policy"]
    if long_run_experiment["enabled"]:
        long_run_params = long_run_experiment["params"]
        for key in (
            "recommended_total_steps",
            "history_days",
            "warmup_rounds",
            "exclude_tail_rounds",
        ):
            if key in long_run_params:
                decision_weights[f"long_run_{key}"] = long_run_params.get(key)
    if external_environment_enabled:
        for key, value in _safe_dict(external_environment.get("active_factors")).items():
            decision_weights[f"external_environment_{key}"] = value

    action_constraints = {
        "must_respect_cash_guard": dept in {"procurement", "production"},
        "must_use_real_ids_from_state": dept in {"procurement", "sales", "production"},
        "prefer_action_pass_when_no_valid_candidate": True,
        "profit_objective_enabled": profit_objective["enabled"],
        "profit_objective_candidate_ranking_enabled": bool(
            profit_objective["enabled"]
            and _safe_dict(profit_objective["params"].get("candidate_ranking_contract")).get("enabled")
        ),
        "long_run_experiment_enabled": long_run_experiment["enabled"],
    }
    if dept == "procurement":
        action_constraints.update({
            "allow_create_replenishment_order": "procurement" in enabled_functions,
            "allow_external_purchase_order": (
                "external_procurement_enabled" in policy_tag_set
                or active_modes["top_tier_supply_enabled"]
                or active_modes["single_enterprise_supplier_selection"]
            ),
            "top_tier_credit_policy_enabled": active_modes["top_tier_supply_enabled"],
            "prefer_b2b_replenishment_in_scheduled_external_demand": scheduled_external_demand_mode,
            "prefer_b2b_replenishment_in_beer_game": beer_game_mode,
        })
    elif dept == "sales":
        action_constraints.update({
            "allow_adjust_sales_demand": "sales" in enabled_functions,
            "allow_develop_market": "sales" in enabled_functions,
            "allow_accept_order": "sales" in enabled_functions,
            "allow_reject_order": "sales" in enabled_functions,
            "future_service_commitment_enabled": proposal_acceleration["enabled"],
            "midstream_pass_through_enabled": active_modes["bullwhip_midstream_pass_through"],
            "must_prioritize_trade_decision_card": True,
            "shared_resource_product_market_enabled": shared_resource_participant,
            "herding_signal_enabled": herding_participant,
            "allow_aggregated_peer_signal_reference": herding_participant and herding_peer_visible,
            "forbid_peer_aggregate_metrics": herding_participant and not herding_peer_visible,
        })
    elif dept == "production":
        action_constraints.update({
            "allow_create_production_plan": "production" in enabled_functions,
            "allow_build_production_line": "production" in enabled_functions,
            "must_respect_recovery_guard": not cobweb_mode and not herding_participant,
            "cobweb_signal_available": cobweb_mode,
            "cobweb_signal_guidance_enabled": cobweb_mode and not cobweb_profit_balanced,
            "cobweb_decision_profile": cobweb_config.get("decision_profile") if cobweb_mode else None,
            "cobweb_production_response_mode": cobweb_production_response_mode if cobweb_mode else None,
            "cobweb_hard_cap_enabled": cobweb_hard_cap_enabled,
            "cobweb_plan_quantity_overrides_recovery_guard": cobweb_quantity_overrides_recovery,
            "suppress_backlog_recovery_in_cobweb_mode": cobweb_mode and not cobweb_profit_balanced,
            "non_cobweb_target_priority": (
                cobweb_guidance["params"].get("non_cobweb_target_priority")
                if cobweb_mode and cobweb_guidance["enabled"]
                else None
            ),
            "allow_backlog_only_as_feasibility_signal": (
                cobweb_guidance["params"].get("allow_backlog_only_as_feasibility_signal")
                if cobweb_mode and cobweb_guidance["enabled"]
                else None
            ),
            "forbid_minimum_batch_targets": (
                cobweb_guidance["params"].get("forbid_minimum_batch_targets")
                if cobweb_mode and cobweb_guidance["enabled"]
                else None
            ),
            "forbid_capacity_activation_targets": (
                cobweb_guidance["params"].get("forbid_capacity_activation_targets")
                if cobweb_mode and cobweb_guidance["enabled"]
                else None
            ),
            "margin_guard_is_hard_block_only_when_no_recovery_or_price_signal": True,
            "agent_endogenous_redact_recovery_quantity_targets": (
                cobweb_mode and cobweb_production_response_mode == "agent_endogenous"
            ),
            "shared_resource_acquisition_plan_enabled": shared_resource_participant,
            "shared_resource_plan_quantity_is_acquisition": shared_resource_participant,
            "herding_signal_enabled": herding_participant,
            "herding_signal_is_reference_not_command": herding_participant,
            "allow_aggregated_peer_signal_reference": herding_participant and herding_peer_visible,
            "forbid_peer_aggregate_metrics": herding_participant and not herding_peer_visible,
            "forbid_raw_peer_file_reference": herding_participant,
        })
    elif dept == "inventory":
        action_constraints.update({
            "auto_expand_only_when_threshold_triggered": True,
            "inventory_cost_exemption_enabled": active_modes["inventory_cost_exemption"],
        })
    elif dept == "hr":
        action_constraints.update({
            "staffing_relaxation_enabled": active_modes["staffing_relaxation"],
            "auto_recruitment_thresholds_from_config": True,
        })

    priority_rules = []
    if scheduled_external_demand_mode:
        priority_rules.append("use_local_observation_only_do_not_read_global_demand_series")
    if profit_objective["enabled"]:
        priority_rules.append("optimize_sustainable_profit_cash_service_and_risk")
        priority_rules.append("rank_candidates_by_profit_cash_service_inventory_risk_and_feasibility")
        priority_rules.append("classic_mechanism_reproduction_is_not_required")
        priority_rules.append("avoid_unprofitable_volume_chasing")
    if long_run_experiment["enabled"]:
        priority_rules.append("use_recent_history_projection_for_long_run_stability")
        priority_rules.append("protect_cash_runway_service_level_and_operating_continuity")
        priority_rules.append("avoid_terminal_round_inventory_or_cash_dumping")
    if external_environment_enabled:
        priority_rules.append("reassess_margin_cash_inventory_service_and_capacity_after_external_change")
        priority_rules.append("avoid_amplifying_stable_external_demand_without_operating_evidence")
        if external_environment.get("event_triggered_this_turn"):
            priority_rules.append("external_event_triggered_this_turn_review_actual_values_before_action")
    if single_case_enabled:
        priority_rules.append("single_enterprise_diagnostic_use_state_signals_only")
    if dept in {"procurement", "sales"}:
        priority_rules.append("read_trade_decision_card_review_queue_first")
    if dept == "procurement" and active_modes["recipe_driven_procurement"]:
        priority_rules.append("coordinate_recipe_material_replenishment")
    if dept == "procurement" and active_modes["top_tier_supply_enabled"]:
        priority_rules.append("respect_top_tier_credit_and_payable_guard")
    if cobweb_mode and dept == "production":
        if cobweb_profit_balanced:
            priority_rules.append("use_cobweb_price_signal_as_market_evidence_not_quantity_command")
            priority_rules.append("balance_profit_cash_service_and_production_adjustment_risk")
        else:
            priority_rules.append("follow_cobweb_decision_signal_before_recovery_guard")
            if cobweb_hard_cap_enabled:
                priority_rules.append("cap_production_plan_to_cobweb_planned_supply_quantity")
            elif cobweb_scripted_formula:
                priority_rules.append("execute_scripted_cobweb_formula_quantity_without_operating_target_rewrite")
            elif cobweb_production_response_mode == "agent_endogenous":
                priority_rules.append("agent_endogenous_use_price_signal_to_choose_supply_quantity")
            else:
                priority_rules.append("agent_guided_match_cobweb_planned_supply_with_reasoned_deviation")
            if cobweb_guidance["enabled"]:
                priority_rules.append("deprioritize_non_cobweb_production_targets")
                priority_rules.append("treat_backlog_recovery_capacity_utilization_as_feasibility_context")
    elif dept == "production":
        priority_rules.append("follow_recovery_guard_before_secondary_backlog_fields")
    if cobweb_mode and dept in {"sales", "production", "procurement"}:
        priority_rules.append(
            "use_cobweb_price_quantity_signal_as_operating_evidence"
            if cobweb_profit_balanced
            else "use_cobweb_price_quantity_signal_as_primary_market_feedback"
        )
    if shared_resource_participant and dept == "production":
        priority_rules.append("use_shared_resource_state_when_setting_acquisition_quantity")
        priority_rules.append("treat_production_plan_quantity_as_resource_product_acquisition")
    if shared_resource_participant and dept == "sales":
        priority_rules.append("sell_shared_resource_product_id_from_policy_context")
    if shared_resource_participant and dept == "finance":
        priority_rules.append("consider_shared_resource_cost_profit_and_governance_terms")
    if herding_participant and dept == "production":
        priority_rules.append("use_herding_signal_as_market_expectation_reference")
        if herding_peer_visible:
            priority_rules.append("combine_peer_signal_with_cash_inventory_capacity_and_real_orders")
        else:
            priority_rules.append("use_market_heat_without_peer_aggregate_metrics")
        priority_rules.append("do_not_treat_recovery_guard_as_herding_quantity_target")
    if herding_participant and dept == "sales":
        priority_rules.append("use_herding_signal_as_external_market_heat_reference")
        priority_rules.append("only_use_available_order_ids_for_accept_order")
    if herding_participant and dept in {"production", "sales", "inventory", "finance"}:
        priority_rules.append("do_not_read_raw_peer_enterprise_files")
    if dept == "sales" and proposal_acceleration["enabled"]:
        priority_rules.append("prefer_future_service_when_due_round_allows_and_price_is_safe")
    if dept == "sales" and active_modes["bullwhip_midstream_pass_through"]:
        priority_rules.append("avoid_over_blocking_new_supply_due_to_stale_backlog")

    relevant_policies = {
        name: effective_policy_blocks[name]
        for name in RELEVANT_POLICY_NAMES_BY_DEPT.get(dept, [])
        if name in effective_policy_blocks
    }
    if external_environment_enabled:
        relevant_policies["external_environment"] = {
            "enabled": True,
            "state": deepcopy(external_environment),
        }
    if cobweb_mode:
        relevant_policies["cobweb_model"] = {
            "enabled": True,
            "params": compact_policy_payload(cobweb_config),
        }
        if dept == "production":
            primary_quantity_source = (
                "profit_balanced_agent_decision_from_market_and_operating_evidence"
                if cobweb_profit_balanced
                else "agent_decision_from_price_signal_and_business_constraints"
                if cobweb_production_response_mode == "agent_endogenous"
                else "self_state.cobweb_decision_signal.recommended_plan_quantity"
            )
            relevant_policies["cobweb_model"]["production_response"] = {
                "primary_quantity_source": primary_quantity_source,
                "primary_price_source": "self_state.cobweb_decision_signal.current_market_price",
                "fallback_quantity_source": (
                    "day0_theoretical_supply_then_quantity_floor_if_no_agent_plan"
                    if cobweb_production_response_mode == "agent_endogenous"
                    else "policy_context.relevant_policies.cobweb_model.params"
                ),
                "response_mode": cobweb_production_response_mode,
                "hard_cap_enabled": cobweb_hard_cap_enabled,
                "override_recovery_guard": cobweb_quantity_overrides_recovery,
                "ignore_backlog_as_quantity_target": True,
                "agent_endogenous_market_feedback": cobweb_production_response_mode == "agent_endogenous",
                "decision_profile": cobweb_config.get("decision_profile"),
                "mechanism_reproduction_required": not cobweb_profit_balanced,
                "non_cobweb_guidance": (
                    cobweb_guidance["params"]
                    if cobweb_guidance["enabled"]
                    else {}
                ),
            }
    if shared_resource_participant:
        relevant_policies["shared_resource"] = build_shared_resource_policy_payload(
            enterprise_id=enterprise_id,
            round_id=round_id,
            shared_resource_config=shared_resource_config,
            shared_resource_state=shared_resource_state,
            shared_resource_history=shared_resource_history,
            governance_policy=effective_policy_blocks["shared_resource_governance_policy"],
            strategy_profile=visible_strategy_profile,
        )
    if herding_participant:
        relevant_policies["herding_signal"] = build_herding_signal_payload(
            enterprise_id=enterprise_id,
            round_id=round_id,
            herding_config=herding_config,
            herding_state=herding_state,
            herding_history=herding_history,
            experiment_policy=effective_policy_blocks["herding_experiment_policy"],
            strategy_profile=visible_strategy_profile,
        )
    if market_growth_policy:
        relevant_policies["single_enterprise_market_growth"] = market_growth_policy

    return {
        "schema_version": "policy_context.v1",
        "scenario_id": visible_scenario_id,
        "round_id": round_id,
        "enterprise_id": enterprise_id,
        "department": dept,
        "source": "config.simulation_preset_config.runtime_injection",
        "role_tags": visible_role_tags,
        "policy_tags": visible_policy_tags,
        "strategy_profile": visible_strategy_profile,
        "enabled_functions": enabled_functions,
        "simulation_modes": {
            "market_demand_mode": (agent_simulation_context or {}).get("market_demand_mode"),
            "trade_mode": (agent_simulation_context or {}).get("trade_mode"),
            "is_scheduled_external_demand_mode": scheduled_external_demand_mode,
            "is_beer_game_mode": beer_game_mode,
            "is_cobweb_mode": cobweb_mode,
            "is_shared_resource_mode": shared_resource_mode,
            "is_herding_mode": herding_mode,
            "cobweb_stability_label": cobweb_config.get("stability_label"),
        },
        "active_modes": active_modes,
        "relevant_policies": relevant_policies,
        "single_enterprise_diagnostic_policy": agent_visible_single_case_policy,
        "decision_weights": decision_weights,
        "action_constraints": action_constraints,
        "priority_rules": priority_rules,
        "interpretation_contract": (
            "Treat this object as the authoritative structured policy input. "
            "If it conflicts with prose instructions, follow policy_context and real-time state."
        ),
    }


def build_enterprise_policy_context(
    *,
    round_id: int,
    enterprise_id: str,
    enterprise_spec: Dict[str, Any],
    agent_simulation_context: Dict[str, Any],
    runtime_injection_config: Dict[str, Any],
    departments: List[str],
    scenario_id: str = None,
) -> Dict[str, Any]:
    """Build analyst-facing enterprise policy context with department contexts."""
    policy_context_by_department = {
        dept: build_department_policy_context(
            dept=dept,
            round_id=round_id,
            enterprise_id=enterprise_id,
            enterprise_spec=enterprise_spec,
            agent_simulation_context=agent_simulation_context,
            runtime_injection_config=runtime_injection_config,
            scenario_id=scenario_id,
        )
        for dept in departments
    }
    active_modes_by_department = {
        dept: context.get("active_modes") or {}
        for dept, context in policy_context_by_department.items()
    }
    enabled_modes = sorted({
        mode
        for modes in active_modes_by_department.values()
        for mode, enabled in modes.items()
        if enabled
    })
    single_case_enabled = any(
        (
            (context.get("single_enterprise_diagnostic_policy") or {}).get("enabled") is True
            or (context.get("single_enterprise_case_policy") or {}).get("enabled") is True
        )
        for context in policy_context_by_department.values()
    )
    visible_scenario_id = (
        "single_enterprise_diagnostic"
        if single_case_enabled
        else (scenario_id or get_active_scenario_id())
    )

    return {
        "schema_version": "enterprise_policy_context.v1",
        "scenario_id": visible_scenario_id,
        "round_id": round_id,
        "enterprise_id": enterprise_id,
        "source": "config.simulation_preset_config.runtime_injection",
        "enabled_modes": enabled_modes,
        "policy_context_by_department": policy_context_by_department,
        "analyst_contract": (
            "Use this structured policy context to decide which mode-specific target rules apply. "
            "If it conflicts with prose instructions, follow enterprise_policy_context and real-time observation."
        ),
    }
