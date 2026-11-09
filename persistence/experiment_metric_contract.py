"""Experiment metric contracts for paper-facing C1/C2/C3 evaluation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List


EXPERIMENT_METRIC_CONTRACT_SCHEMA_VERSION = "experiment_metric_contract.v1"

CORE_PAPER_FAMILIES = ("bullwhip", "herding", "commons", "cobweb")
DECISION_GROUPS = ("C1", "C2", "C3")


FAMILY_METRIC_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "bullwhip": {
        "name": 'Bullwhip Effect',
        "research_question": 'Do order, inventory, and shortage pressures amplify upstream, and can an open profit objective reshape that amplification?',
        "headline_metrics": [
            "mechanism_strength",
            "operating_performance",
            "decision_quality",
        ],
        "primary_metrics": [
            "bullwhip_ratio",
            "tier_order_quantity",
            "fill_rate",
            "confirmed_backlog",
            "inventory_position",
            "net_profit",
        ],
        "secondary_metrics": [
            "external_demand",
            "material_shortage",
            "cash",
            "total_revenue",
            "total_cost",
        ],
        "expected_direction": {
            "C1": 'Fixed rules should produce an interpretable order-amplification or fulfillment-pressure trajectory.',
            "C2": 'A mechanism-constrained agent should express amplification through inventory, fulfillment, and replenishment decisions.',
            "C3": 'A profit-oriented agent may avoid unprofitable replenishment, smooth demand, or trade some service level for cash safety.',
        },
    },
    "herding": {
        "name": 'Herding Effect',
        "research_question": 'Does peer visibility increase synchronized expansion and overproduction, and does a profit objective reduce unproductive following?',
        "headline_metrics": [
            "mechanism_strength",
            "operating_performance",
            "decision_quality",
        ],
        "primary_metrics": [
            "peer_visibility_enabled",
            "synchronization_index",
            "herding_index",
            "overproduction_ratio",
            "inventory_pressure",
            "net_profit",
        ],
        "secondary_metrics": [
            "true_demand",
            "market_heat",
            "planned_quantity",
            "total_planned_quantity",
            "cash",
        ],
        "expected_direction": {
            "C1": 'A fixed peer-following rule should produce stronger synchronization in the peer-visible group than in the peer-hidden group.',
            "C2": 'A mechanism-constrained agent should synchronize plans more readily when aggregate peer signals are visible.',
            "C3": 'A profit-oriented agent may discount low-return peer signals and reduce synchronization or overproduction.',
        },
    },
    "commons": {
        "name": 'Tragedy of the Commons',
        "research_question": 'Does locally rational shared-resource acquisition cause resource degradation and financial feedback, and does a long-term profit objective encourage restraint?',
        "headline_metrics": [
            "mechanism_strength",
            "operating_performance",
            "decision_quality",
        ],
        "primary_metrics": [
            "resource_stock_quantity",
            "resource_quality",
            "actual_acquisition_quantity",
            "sustainable_acquisition_per_round",
            "cash",
            "net_profit",
        ],
        "secondary_metrics": [
            "planned_acquisition_quantity",
            "resource_state",
            "production_failure_due_to_cash",
            "inventory_pressure",
        ],
        "expected_direction": {
            "C1": 'A fixed local-return rule should validate the resource degradation and acquisition-loss equations.',
            "C2": 'A mechanism-constrained agent should exhibit over-acquisition, quality decline, or adverse cash feedback in the shared-resource setting.',
            "C3": 'A profit-oriented agent may reduce short-term acquisition, delay degradation, or improve long-term profit.',
        },
    },
    "cobweb": {
        "name": 'Cobweb Model',
        "research_question": (
            'Do lagged prices and supply decisions produce the configured stability trajectory, and can profit-oriented agents improve operating stability under divergent pressure?'
        ),
        "headline_metrics": [
            "mechanism_strength",
            "operating_performance",
            "decision_quality",
        ],
        "primary_metrics": [
            "market_price",
            "actual_supply_quantity",
            "production_plan_quantity",
            "price_deviation_from_equilibrium",
            "supply_deviation_from_equilibrium",
            "late_to_early_price_deviation_ratio",
            "late_to_early_supply_deviation_ratio",
            "production_adjustment_magnitude",
            "equilibrium_price",
            "net_profit",
            "cash",
            "fill_rate",
        ],
        "secondary_metrics": [
            "theoretical_planned_supply_quantity",
            "equilibrium_quantity",
            "supply_source",
            "inventory_position",
            "confirmed_backlog",
            "action_failure_count",
        ],
        "expected_direction": {
            "C1": 'The theoretical supply function should reproduce convergent, approximately constant-amplitude, and bounded-divergent trajectories.',
            "C2": 'Under divergent pressure, a mechanism-constrained agent should form supply plans endogenously from price and cobweb signals.',
            "C3": 'Under divergent pressure, a profit-oriented agent should treat price as operating evidence and balance profit, cash, service, and production-adjustment risk.',
        },
    },
    "financial_constraint": {
        "name": 'Financial-Constraint Propagation',
        "research_question": 'Do cash, credit, and procurement failures propagate into the fulfillment chain?',
        "headline_metrics": [
            "mechanism_strength",
            "operating_performance",
            "decision_quality",
        ],
        "primary_metrics": [
            "fill_rate",
            "net_profit",
            "cash",
            "cash_guard_failure",
            "confirmed_backlog",
            "procurement_failure",
        ],
        "secondary_metrics": [
            "credit_balance",
            "inventory_position",
            "lost_sales_quantity",
        ],
        "expected_direction": {
            "C1": 'Fixed rules combined with budget constraints should trigger interpretable fulfillment blockage.',
            "C2": 'A mechanism-constrained agent should adjust procurement and sales under cash and credit constraints.',
            "C3": 'A profit-oriented agent may reduce order size, defer procurement, or rebalance its portfolio.',
        },
    },
    "topology": {
        "name": 'Complex Topology',
        "research_question": 'Are trading edges, fulfillment, and settlement stable in chain, branching, and networked structures?',
        "headline_metrics": [
            "mechanism_strength",
            "operating_performance",
            "decision_quality",
        ],
        "primary_metrics": [
            "trade_edge_coverage",
            "order_count",
            "gross_transaction_value",
            "fill_rate",
            "net_profit",
        ],
        "secondary_metrics": [
            "multi_source_split",
            "supplier_reliability",
            "on_time_delivery_rate",
        ],
        "expected_direction": {
            "C1": 'Fixed routing should validate topological trading and settlement paths.',
            "C2": 'A mechanism-constrained agent should complete stable trades within the topology.',
            "C3": 'A profit-oriented agent may select more reliable or profitable trading edges.',
        },
    },
    "single_enterprise": {
        "name": 'Single-Enterprise Decision Validation',
        "research_question": 'Can an agent identify an enterprise operating problem and take an appropriate corrective action?',
        "headline_metrics": [
            "diagnostic_accuracy",
            "corrective_action_quality",
            "recovery_performance",
        ],
        "primary_metrics": [
            "issue_identified",
            "primary_department_actioned",
            "preferred_action_taken",
            "discouraged_action_taken",
            "recovery_metric",
        ],
        "secondary_metrics": [
            "handoff_day",
            "takeover_rounds",
            "cash",
            "inventory_position",
            "fill_rate",
        ],
        "expected_direction": {
            "S0": 'The agent should identify the responsible department, avoid discouraged actions, and improve case-specific indicators during the takeover window.',
        },
    },
    "long_horizon_evolution": {
        "name": 'Long-Horizon Multi-Enterprise Evolution',
        "research_question": (
            'Can four supply-chain tiers respond to staged cost, trade and demand changes while maintaining profit, cash and service without unsupported demand amplification?'
        ),
        "headline_metrics": [
            "change_detection_and_response",
            "operating_resilience",
            "decision_quality",
        ],
        "primary_metrics": [
            "event_detection_latency_turns",
            "response_action_latency_turns",
            "phase_net_profit",
            "phase_cash_change",
            "fill_rate",
            "confirmed_backlog",
            "inventory_turnover_proxy",
            "procurement_unit_cost",
            "production_unit_cost",
            "order_variance_ratio",
            "agent_fallback_rate",
        ],
        "secondary_metrics": [
            "external_material_price_factor",
            "production_conversion_cost_factor",
            "external_logistics_cost_factor",
            "external_demand_quantity_factor",
            "external_customer_price_factor",
            "total_revenue",
            "total_cost",
            "cash",
        ],
        "expected_direction": {
            "E1": (
                'Respond to observable environment changes through procurement, production, inventory and sales, shorten recovery and avoid amplifying stable terminal demand without evidence.'
            ),
        },
    },
    "general": {
        "name": 'General Multi-Enterprise Run',
        "research_question": 'Does the run contain the annotations and quality information required for evaluation?',
        "headline_metrics": [
            "operating_performance",
            "decision_quality",
            "sample_quality",
        ],
        "primary_metrics": [
            "net_profit",
            "cash",
            "fill_rate",
            "round_integrity",
            "fallback_triggered",
        ],
        "secondary_metrics": [
            "total_revenue",
            "total_cost",
            "completed_steps",
            "enterprise_count",
        ],
        "expected_direction": {},
    },
}


def infer_experiment_family(scenario_id: str) -> str:
    """Infer the paper mechanism family from a scenario id."""
    value = str(scenario_id or "").lower()
    if value == "long_horizon_evolution" or "long_horizon_evolution" in value:
        return "long_horizon_evolution"
    if value.startswith("single_case_") or value.startswith("firm_ablation_fd"):
        return "single_enterprise"
    if "herding" in value:
        return "herding"
    if "commons" in value or "mining" in value:
        return "commons"
    if "cobweb" in value:
        return "cobweb"
    if "credit_constraint" in value or "financial" in value:
        return "financial_constraint"
    if "architecture" in value or "topology" in value:
        return "topology"
    if "beer_game" in value or value.startswith("baseline_") or value.startswith("firm_ablation_bullwhip_"):
        return "bullwhip"
    return "general"


def build_experiment_metric_contract(
    *,
    scenario_id: str,
    experiment_design: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Return the expected metrics and quality gates for a scenario/run."""
    experiment_design = experiment_design or {}
    experiment_group = experiment_design.get("experiment_group")
    family = infer_experiment_family(scenario_id)
    family_contract = deepcopy(
        FAMILY_METRIC_CONTRACTS.get(family) or FAMILY_METRIC_CONTRACTS["general"]
    )
    expected_direction = family_contract.pop("expected_direction", {})
    group_expected_direction = expected_direction.get(
        experiment_group,
        expected_direction.get("C2", ""),
    )
    if experiment_group in {"MA", "SA", "NA", "NC"}:
        group_expected_direction = 'Compare organizational architectures under shared facts and execution rules; retain failures and retries without assuming an MA or SA winner.'
        family_contract["research_question"] = 'How do enterprise diagnosis, shared coordination and functional organization affect decisions, operating outcomes and runtime overhead?'
        family_contract["secondary_metrics"] += [
            "phase_availability_rate", "first_attempt_valid_tasks", "attempt_count", "timeout_attempts",
            "business_rejections", "unresolved_rejections", "technical_no_submission_phases",
        ]
        if experiment_group == "SA" and experiment_design.get("baseline_group") == "C3":
            group_expected_direction = 'Compare against organizational enterprises under the same C3 operating regime; retain failures and retries without assuming an MA or SA winner.'
    return {
        "schema_version": EXPERIMENT_METRIC_CONTRACT_SCHEMA_VERSION,
        "phase": "phase2_expected_metrics",
        "scenario_id": str(scenario_id or ""),
        "experiment_group": experiment_group,
        "decision_regime": experiment_design.get("decision_regime"),
        "agent_objective_profile": experiment_design.get("agent_objective_profile"),
        "constraint_level": experiment_design.get("constraint_level"),
        "enterprise_architecture": experiment_design.get("enterprise_architecture"),
        "baseline_group": experiment_design.get("baseline_group"),
        "baseline_run_id": experiment_design.get("baseline_run_id"),
        "source_scenario_id": experiment_design.get("source_scenario_id"),
        "family": family,
        **family_contract,
        "expected_direction_for_group": group_expected_direction,
        "quality_gates": [
            "run_status_eligible",
            "round_integrity_ok",
            "decision_regime_labeled",
            "config_snapshot_available",
        ] + (["architecture_decision_quality_ok"] if experiment_group in {"MA", "SA", "NA", "NC"} else []),
        "group_specific_gates": _group_specific_gates(experiment_group) + (
            ["profit_objective_enabled", "objective_function_available", "baseline_regime_matched"]
            if experiment_group == "SA" and experiment_design.get("baseline_group") == "C3" else []
        ),
    }


def _group_specific_gates(experiment_group: str) -> List[str]:
    if experiment_group in {"MA", "SA", "NA", "NC"}:
        return ["architecture_protocol_labeled", "phase_records_complete", "failures_retained_in_evaluation"]
    if experiment_group == "C1":
        return ["scripted_rule_policy_enabled", "agent_open_reasoning_disabled"]
    if experiment_group == "C2":
        return ["profit_objective_disabled", "mechanism_policy_context_visible"]
    if experiment_group == "C3":
        return ["profit_objective_enabled", "objective_function_available"]
    if experiment_group == "S0":
        return ["single_enterprise_case_policy_enabled", "case_manifest_available"]
    if experiment_group == "E1":
        return [
            "external_environment_policy_enabled",
            "long_run_policy_enabled",
            "future_event_content_hidden",
        ]
    return []


def assess_phase1_readiness(
    scenarios: Iterable[Dict[str, Any]],
    *,
    core_families: Iterable[str] = CORE_PAPER_FAMILIES,
) -> Dict[str, Any]:
    """Assess whether C1/C2/C3 run-preparation assets are present."""
    matrix: Dict[str, Dict[str, List[str]]] = {
        family: {group: [] for group in DECISION_GROUPS}
        for family in core_families
    }
    group_counts = {group: 0 for group in DECISION_GROUPS}
    failures: List[str] = []

    for scenario in scenarios:
        scenario_id = str(scenario.get("scenario_id") or "")
        experiment_design = scenario.get("experiment_design") or {}
        group = experiment_design.get("experiment_group")
        family = infer_experiment_family(scenario_id)
        if group in group_counts:
            group_counts[group] += 1
        if family in matrix and group in matrix[family]:
            matrix[family][group].append(scenario_id)

        runtime = scenario.get("runtime_injection") or {}
        scripted_policy = runtime.get("scripted_rule_policy") or {}
        profit_policy = runtime.get("profit_objective_policy") or {}
        decision_regime = experiment_design.get("decision_regime")
        if group == "C1" and not scripted_policy.get("enabled"):
            failures.append(f"{scenario_id}: C1 missing scripted_rule_policy.enabled")
        if group == "C2" and decision_regime != "constrained_agent":
            failures.append(f"{scenario_id}: C2 decision_regime mismatch")
        if group == "C3" and not profit_policy.get("enabled"):
            failures.append(f"{scenario_id}: C3 missing profit_objective_policy.enabled")
        if group == "C3" and decision_regime != "profit_seeking_agent":
            failures.append(f"{scenario_id}: C3 decision_regime mismatch")

    missing_groups = [
        group for group, count in group_counts.items()
        if count <= 0
    ]
    missing_core_cells = [
        f"{family}:{group}"
        for family, groups in matrix.items()
        for group, scenario_ids in groups.items()
        if not scenario_ids
    ]
    complete = not failures and not missing_groups and not missing_core_cells
    return {
        "schema_version": EXPERIMENT_METRIC_CONTRACT_SCHEMA_VERSION,
        "phase": "phase1_run_preparation_readiness",
        "complete": complete,
        "group_counts": group_counts,
        "core_family_matrix": matrix,
        "missing_groups": missing_groups,
        "missing_core_cells": missing_core_cells,
        "failures": failures,
    }
