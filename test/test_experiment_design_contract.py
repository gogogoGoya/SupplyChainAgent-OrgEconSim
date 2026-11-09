import json
from pathlib import Path
from types import SimpleNamespace

from agent.scripted_rule_runner import ScriptedRuleRunner
from agent.policy_context import build_department_policy_context
from agent.skill_runner import SkillRunner
from agent.static_utils import StaticUtils
from agent.multi_tenant_utils import (
    Config as MultiTenantConfig,
    MultiTenantUtils,
)
from config.integration_profiles import resolve_integration_profiles
from config.simulation_preset_config import get_available_scenarios, get_scenario_config
from core.single_case_evaluation import evaluate_case_action_records
from persistence.experiment_metric_contract import assess_phase1_readiness
from persistence.history_projection import HistoryProjectionBuilder
from persistence.multi_enterprise_projections import EnterpriseDailyMetricsProjectionBuilder
from persistence.run_metrics_projection import RunMetricsProjectionBuilder
from runtime.operations.facade import OperationsFacade
from runtime.operations.http_router import _scenario_config_defaults


FORMAL_MAIN_EXPERIMENT_SCENARIOS = {
    "beer_game_scripted",
    "beer_game",
    "beer_game_profit_objective",
    "herding_baseline_peer_visible_scripted",
    "herding_baseline_peer_visible",
    "herding_peer_visible_profit_objective",
    "herding_no_peer_visibility_scripted",
    "herding_no_peer_visibility",
    "herding_no_peer_visibility_profit_objective",
    "commons_mining_collapse_baseline_differentiated_scripted",
    "commons_mining_collapse_baseline_differentiated",
    "commons_mining_profit_objective",
    "cobweb_convergent_scripted",
    "cobweb_neutral_scripted",
    "cobweb_divergent_scripted",
    "cobweb_divergent",
    "cobweb_divergent_profit_objective",
}


class _MemoryOperationsRepository:
    def __init__(self):
        self.versions = {}
        self.jobs = {}

    def save_version(self, version):
        self.versions[version["version_id"]] = version

    def create_job(self, job):
        self.jobs[job["job_id"]] = job


def _scripted_runner(tmp_path, policy, enterprise_name="Supplier"):
    workspace = tmp_path / "workspace_multi"
    (workspace / "enterprises" / enterprise_name).mkdir(parents=True)
    run_meta = {
        "run_id": "run-scripted-test",
        "status": "running",
        "scenario_id": "beer_game_scripted",
        "enterprise_ids": [enterprise_name],
        "scenario_config": {
            "runtime_injection": {
                "scripted_rule_policy": policy,
            },
            "enterprise_specs": [
                {
                    "enterprise_id": enterprise_name,
                    "id": enterprise_name,
                    "tier": 0,
                }
            ],
        },
    }
    (workspace / "run_meta.json").write_text(json.dumps(run_meta), encoding="utf-8")
    return ScriptedRuleRunner(
        client_dir=tmp_path,
        enterprise_spec=SimpleNamespace(enterprise_name=enterprise_name),
        workspace_dir=workspace,
    )


def _single_case_scripted_runner(tmp_path, scenario_id):
    workspace = tmp_path / "workspace_multi"
    enterprise_name = "Manufacturer"
    (workspace / "enterprises" / enterprise_name).mkdir(parents=True)
    scenario = get_scenario_config(f"{scenario_id}_scripted")
    run_meta = {
        "run_id": f"run-{scenario_id}-scripted-test",
        "status": "running",
        "scenario_id": f"{scenario_id}_scripted",
        "enterprise_ids": [enterprise_name],
        "scenario_config": scenario,
    }
    (workspace / "run_meta.json").write_text(json.dumps(run_meta), encoding="utf-8")
    return ScriptedRuleRunner(
        client_dir=tmp_path,
        enterprise_spec=SimpleNamespace(enterprise_name=enterprise_name),
        workspace_dir=workspace,
    )


def _scenario_scripted_runner(tmp_path, scenario_id, enterprise_name="Manufacturer"):
    workspace = tmp_path / "workspace_multi"
    (workspace / "enterprises" / enterprise_name).mkdir(parents=True)
    scenario = get_scenario_config(scenario_id)
    run_meta = {
        "run_id": f"run-{scenario_id}-test",
        "status": "running",
        "scenario_id": scenario_id,
        "enterprise_ids": [enterprise_name],
        "scenario_config": scenario,
    }
    (workspace / "run_meta.json").write_text(json.dumps(run_meta), encoding="utf-8")
    return ScriptedRuleRunner(
        client_dir=tmp_path,
        enterprise_spec=SimpleNamespace(enterprise_name=enterprise_name),
        workspace_dir=workspace,
    )


def _write_department_state(tmp_path, enterprise_name, department, day, self_state):
    target = (
        tmp_path
        / "workspace_multi"
        / "enterprises"
        / enterprise_name
        / "department"
        / department
        / f"day{day}"
        / f"{department}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"self_state": self_state}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_experiment_design_labels_cover_c1_c2_c3_s0_scenarios():
    expectations = {
        "beer_game_scripted": ("C1", "scripted_rational", False),
        "beer_game": ("C2", "constrained_agent", False),
        "beer_game_profit_objective": ("C3", "profit_seeking_agent", True),
        "single_case_01_order_selection": ("S0", "single_enterprise_validation", False),
    }

    for scenario_id, (group, regime, profit_enabled) in expectations.items():
        scenario = get_scenario_config(scenario_id)
        experiment_design = scenario["experiment_design"]
        assert experiment_design["experiment_group"] == group
        assert experiment_design["decision_regime"] == regime
        assert (
            scenario["runtime_injection"]["profit_objective_policy"]["enabled"]
            is profit_enabled
        )


def test_cobweb_formal_matrix_uses_three_c1_calibrations_and_divergent_agent_comparison():
    formal_roles = {
        "cobweb_convergent_scripted": ("C1", "mechanism_calibration", "convergent"),
        "cobweb_neutral_scripted": ("C1", "mechanism_calibration", "neutral"),
        "cobweb_divergent_scripted": ("C1", "main_comparison", "divergent"),
        "cobweb_divergent": ("C2", "main_comparison", "divergent"),
        "cobweb_divergent_profit_objective": ("C3", "main_comparison", "divergent"),
    }

    for scenario_id, (group, role, regime) in formal_roles.items():
        scenario = get_scenario_config(scenario_id)
        formal_config = scenario["formal_experiment_config"]

        assert scenario["experiment_design"]["experiment_group"] == group
        assert formal_config["ready_for_formal_run"] is True
        assert formal_config["experiment_family"] == "cobweb"
        assert formal_config["experiment_role"] == role
        assert formal_config["parameter_regime"] == regime
        assert formal_config["main_comparison_regime"] == "divergent"

    assert "formal_experiment_config" not in get_scenario_config("cobweb_convergent")
    assert "formal_experiment_config" not in get_scenario_config("cobweb_neutral")
    assert "formal_experiment_config" not in get_scenario_config(
        "cobweb_convergent_profit_objective"
    )


def test_cobweb_c1_uses_lagged_supply_formula_instead_of_fixed_oscillation(tmp_path):
    runner = _scenario_scripted_runner(tmp_path, "cobweb_divergent_scripted")
    scenario = get_scenario_config("cobweb_divergent_scripted")
    cobweb_config = scenario["simulation"]["cobweb_config"]

    assert cobweb_config["production_response_mode"] == "scripted_formula"
    assert cobweb_config["decision_profile"] == "scripted_formula"

    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        0,
        {
            "cobweb_decision_signal": {
                "theoretical_supply_quantity": 188.0,
                "lagged_price": 280.0,
                "current_market_price": 40.0,
            }
        },
    )
    assert runner._scripted_production_quantity(0, {"base_quantity": 999}) == 44.0

    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        1,
        {"cobweb_decision_signal": {"current_market_price": 504.0}},
    )
    assert runner._scripted_production_quantity(1, {"base_quantity": 999}) == 260.0


def test_cobweb_c2_and_c3_keep_market_equation_but_use_different_decision_profiles():
    c2 = get_scenario_config("cobweb_divergent")
    c3 = get_scenario_config("cobweb_divergent_profit_objective")

    for key in (
        "demand_intercept",
        "demand_slope",
        "supply_intercept",
        "supply_slope",
        "price_floor",
        "price_ceiling",
        "quantity_floor",
        "quantity_ceiling",
    ):
        assert c3["simulation"]["cobweb_config"][key] == c2["simulation"]["cobweb_config"][key]

    assert c2["simulation"]["cobweb_config"]["decision_profile"] == "mechanism_primary"
    assert c3["simulation"]["cobweb_config"]["decision_profile"] == "profit_balanced"
    assert c2["runtime_injection"]["cobweb_enterprise_guidance_policy"]["enabled"] is True
    assert c3["runtime_injection"]["cobweb_enterprise_guidance_policy"]["enabled"] is False
    c3_weights = c3["runtime_injection"]["profit_objective_policy"]["objective_weights"]
    assert sum(c3_weights.values()) == 1.0
    assert "inventory_cost_control" not in c3_weights
    assert c3_weights["production_adjustment_stability"] > 0


def test_cobweb_c1_policy_context_makes_formula_quantity_authoritative():
    c1 = get_scenario_config("cobweb_divergent_scripted")
    enterprise_spec = c1["enterprise_specs"][0]

    context = build_department_policy_context(
        dept="production",
        round_id=1,
        enterprise_id="Manufacturer",
        enterprise_spec=enterprise_spec,
        agent_simulation_context=c1["simulation"],
        runtime_injection_config=c1["runtime_injection"],
        scenario_id="cobweb_divergent_scripted",
    )

    assert context["action_constraints"]["cobweb_plan_quantity_overrides_recovery_guard"] is True
    assert (
        "execute_scripted_cobweb_formula_quantity_without_operating_target_rewrite"
        in context["priority_rules"]
    )
    response = context["relevant_policies"]["cobweb_model"]["production_response"]
    assert response["response_mode"] == "scripted_formula"
    assert response["override_recovery_guard"] is True


def test_cobweb_c1_formula_plan_is_not_rewritten_by_generic_operating_guards(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {
            "action_constraints": {
                "allow_create_production_plan": True,
                "cobweb_plan_quantity_overrides_recovery_guard": True,
            },
        },
        "self_state": {
            "cobweb_decision_signal": {
                "enabled": True,
                "product_id": "beer",
                "current_market_price": 504.0,
                "production_response_mode": "scripted_formula",
            },
            "recovery_guard": {
                "candidates": [{
                    "product_id": "beer",
                    "material_feasible_quantity": 9000,
                    "recommended_plan_quantity": 160,
                    "recommended_daily_capacity": 160,
                    "blocking_reasons": [],
                    "raw_material_snapshots": {"Malt": {"on_hand": 900000}},
                }],
            },
            "margin_guard": {
                "candidates": [{
                    "product_id": "beer",
                    "guard_level": "hard_blocked",
                    "estimated_sale_unit_price": 0,
                    "allow_service_recovery": False,
                    "allow_continuity_recovery": False,
                }],
            },
        },
    }
    monkeypatch.setattr(
        StaticUtils,
        "_load_sibling_department_state_snapshot",
        lambda **_: state,
    )

    normalized = StaticUtils._normalize_department_workflow(
        [{
            "action": {
                "action_name": "create_production_plan",
                "action_param": {
                    "product_id": "beer",
                    "quantity": 260,
                    "daily_capacity": 260,
                },
            },
            "module_type": "ProductionManager",
            "executor_id": "Manufacturer",
        }],
        department="production",
        enterprise_name="Manufacturer",
        round_id=1,
        action_dir=tmp_path,
    )

    action = normalized[0]["action"]
    assert action["action_name"] == "create_production_plan"
    assert action["action_param"]["quantity"] == 260
    assert action["action_param"]["daily_capacity"] == 260
    assert action["action_param"]["_cobweb_scripted_formula_override"] is True


def test_cobweb_c2_plan_is_not_rewritten_by_hidden_recovery_target(monkeypatch, tmp_path):
    state = {
        "policy_context": {
            "action_constraints": {
                "allow_create_production_plan": True,
                "cobweb_plan_quantity_overrides_recovery_guard": False,
                "must_respect_recovery_guard": False,
            },
        },
        "self_state": {
            "cobweb_decision_signal": {
                "enabled": True,
                "product_id": "beer",
                "current_market_price": 504.0,
                "production_response_mode": "agent_endogenous",
            },
            "recovery_guard": {
                "candidates": [{
                    "product_id": "beer",
                    "material_feasible_quantity": 9000,
                    "recommended_plan_quantity": 160,
                    "recommended_daily_capacity": 160,
                    "blocking_reasons": [],
                    "raw_material_snapshots": {"Malt": {"on_hand": 900000}},
                }],
            },
            "margin_guard": {
                "candidates": [{
                    "product_id": "beer",
                    "guard_level": "healthy",
                    "estimated_sale_unit_price": 200,
                }],
            },
        },
    }
    monkeypatch.setattr(
        StaticUtils,
        "_load_sibling_department_state_snapshot",
        lambda **_: state,
    )

    normalized = StaticUtils._normalize_department_workflow(
        [{
            "action": {
                "action_name": "create_production_plan",
                "action_param": {
                    "product_id": "beer",
                    "quantity": 260,
                    "daily_capacity": 260,
                },
            },
            "module_type": "ProductionManager",
            "executor_id": "Manufacturer",
        }],
        department="production",
        enterprise_name="Manufacturer",
        round_id=1,
        action_dir=tmp_path,
    )

    action_param = normalized[0]["action"]["action_param"]
    assert action_param["quantity"] == 260
    assert action_param["daily_capacity"] == 260
    assert "_cobweb_scripted_formula_override" not in action_param


def test_cobweb_agent_context_compacts_history_without_deleting_evidence():
    history = [
        {
            "round": day,
            "product_id": "beer",
            "unit_price": 40 + day,
            "quantity": 100,
            "market_supply_quantity": 120 + day,
            "actual_supply_quantity": 120 + day,
            "theoretical_planned_supply_quantity": 188,
            "market_supply_source": "agent_production_plan_created",
            "market_supply_source_detail": {
                "source": "agent_production_plan_created",
                "supply_round": day - 1,
                "matched_plan_ids": [f"PLAN_{day}"],
                "verbose_internal_detail": "x" * 200,
            },
            "lagged_price": 280,
            "raw_market_price": 40 + day,
            "equations": {
                "demand": "Qd = a - bP",
                "supply": "Qs = c + dP",
            },
        }
        for day in range(40)
    ]
    simulation_context = {
        "market_demand_mode": "cobweb",
        "is_cobweb_mode": True,
        "cobweb_config": {
            "enabled": True,
            "agent_history_window_rounds": 8,
        },
        "cobweb_history": history,
    }

    agent_context = StaticUtils.build_agent_simulation_context(
        simulation_context
    )

    assert len(simulation_context["cobweb_history"]) == 40
    assert len(agent_context["cobweb_history"]) == 8
    assert agent_context["cobweb_history"][0]["round"] == 32
    assert agent_context["cobweb_history"][-1]["round"] == 39
    assert "equations" not in agent_context["cobweb_history"][-1]
    assert "market_supply_source_detail" not in agent_context[
        "cobweb_history"
    ][-1]
    summary = agent_context["cobweb_history_summary"]
    assert summary["record_count"] == 40
    assert summary["latest_round"] == 39
    assert summary["visible_recent_rounds"] == 8
    assert summary["market_supply_source_counts"] == {
        "agent_production_plan_created": 40,
    }
    assert summary["full_history_archived_outside_agent_context"] is True

    filtered_again = StaticUtils.build_agent_simulation_context(agent_context)
    assert len(filtered_again["cobweb_history"]) == 8
    assert filtered_again["cobweb_history_summary"]["record_count"] == 40
    assert filtered_again["cobweb_history"][-1][
        "theoretical_supply_quantity"
    ] == 188
    assert filtered_again["cobweb_history"][-1]["supply_round"] == 38


def test_cobweb_c2_price_signal_bypasses_cold_start_margin_false_block(
    monkeypatch,
    tmp_path,
):
    state = {
        "policy_context": {
            "action_constraints": {
                "allow_create_production_plan": True,
                "must_respect_recovery_guard": False,
                "margin_guard_is_hard_block_only_when_no_recovery_or_price_signal": True,
            },
        },
        "self_state": {
            "cobweb_decision_signal": {
                "enabled": True,
                "product_id": "beer",
                "current_market_price": 40.0,
                "production_response_mode": "agent_endogenous",
                "suggested_supply_direction": "contract_supply",
            },
            "recovery_guard": {
                "candidates": [{
                    "product_id": "beer",
                    "material_feasible_quantity": 9000,
                    "blocking_reasons": [],
                    "raw_material_snapshots": {
                        "Malt": {"on_hand": 900000},
                    },
                }],
            },
            "margin_guard": {
                "candidates": [{
                    "product_id": "beer",
                    "guard_level": "hard_blocked",
                    "estimated_sale_unit_price": 0,
                    "allow_service_recovery": False,
                    "allow_continuity_recovery": False,
                }],
            },
        },
    }
    monkeypatch.setattr(
        StaticUtils,
        "_load_sibling_department_state_snapshot",
        lambda **_: state,
    )

    normalized = StaticUtils._normalize_department_workflow(
        [{
            "action": {
                "action_name": "create_production_plan",
                "action_param": {
                    "product_id": "beer",
                    "quantity": 60,
                    "daily_capacity": 60,
                },
            },
            "module_type": "ProductionManager",
            "executor_id": "Manufacturer",
        }],
        department="production",
        enterprise_name="Manufacturer",
        round_id=0,
        action_dir=tmp_path,
    )

    assert normalized[0]["action"]["action_name"] == "create_production_plan"
    assert normalized[0]["action"]["action_param"]["quantity"] == 60


def test_operations_cobweb_c2_snapshot_preserves_controller_contract():
    repository = _MemoryOperationsRepository()
    facade = OperationsFacade(repository)

    job = facade.create_experiment_from_scenario(
        scenario_id="cobweb_divergent",
        planned_total_steps=40,
        job_id="job-cobweb-c2-contract",
        config_overrides={
            "simulation": {
                "agent_run_steps": 40,
                "service_total_steps": 40,
            },
            "runtime_injection": {
                "scripted_rule_policy": {
                    "enabled": False,
                    "analyst": {"enabled": False},
                },
            },
        },
    )

    snapshot = repository.versions[job["config_version_id"]]["scenario_config"]
    cobweb_config = snapshot["simulation"]["cobweb_config"]
    runtime_injection = snapshot["runtime_injection"]
    assert snapshot["experiment_design"]["experiment_group"] == "C2"
    assert cobweb_config["production_response_mode"] == "agent_endogenous"
    assert cobweb_config["decision_profile"] == "mechanism_primary"
    assert cobweb_config["agent_history_window_rounds"] == 8
    assert runtime_injection["scripted_rule_policy"]["enabled"] is False
    assert runtime_injection["profit_objective_policy"]["enabled"] is False
    assert runtime_injection["cobweb_enterprise_guidance_policy"]["enabled"] is True


def test_operations_rejects_mixed_cobweb_c2_scripted_controller():
    repository = _MemoryOperationsRepository()
    facade = OperationsFacade(repository)

    try:
        facade.create_experiment_from_scenario(
            scenario_id="cobweb_divergent",
            config_overrides={
                "runtime_injection": {
                    "scripted_rule_policy": {"enabled": True},
                },
            },
        )
    except ValueError as exc:
        assert "Invalid formal cobweb C2 configuration" in str(exc)
    else:
        raise AssertionError("Expected mixed C2/scripted controller configuration to fail")

    assert repository.jobs == {}


def test_operations_rejects_unbounded_cobweb_agent_history_window():
    repository = _MemoryOperationsRepository()
    facade = OperationsFacade(repository)

    try:
        facade.create_experiment_from_scenario(
            scenario_id="cobweb_divergent",
            config_overrides={
                "simulation": {
                    "cobweb_config": {
                        "agent_history_window_rounds": 40,
                    },
                },
            },
        )
    except ValueError as exc:
        assert "agent_history_window_rounds must be between 3 and 12" in str(
            exc
        )
    else:
        raise AssertionError("Expected unbounded cobweb history to fail")

    assert repository.jobs == {}


def test_cobweb_c3_policy_context_treats_price_as_evidence_not_quantity_command():
    c2 = get_scenario_config("cobweb_divergent")
    c3 = get_scenario_config("cobweb_divergent_profit_objective")
    enterprise_spec = c3["enterprise_specs"][0]

    c2_context = build_department_policy_context(
        dept="production",
        round_id=3,
        enterprise_id="Manufacturer",
        enterprise_spec=enterprise_spec,
        agent_simulation_context=c2["simulation"],
        runtime_injection_config=c2["runtime_injection"],
        scenario_id="cobweb_divergent",
    )
    c3_context = build_department_policy_context(
        dept="production",
        round_id=3,
        enterprise_id="Manufacturer",
        enterprise_spec=enterprise_spec,
        agent_simulation_context=c3["simulation"],
        runtime_injection_config=c3["runtime_injection"],
        scenario_id="cobweb_divergent_profit_objective",
    )

    assert "follow_cobweb_decision_signal_before_recovery_guard" in c2_context["priority_rules"]
    assert "use_cobweb_price_signal_as_market_evidence_not_quantity_command" in c3_context["priority_rules"]
    assert "follow_cobweb_decision_signal_before_recovery_guard" not in c3_context["priority_rules"]
    assert c3_context["action_constraints"]["cobweb_signal_available"] is True
    assert c3_context["action_constraints"]["cobweb_signal_guidance_enabled"] is False
    assert c3_context["action_constraints"]["suppress_backlog_recovery_in_cobweb_mode"] is False
    response = c3_context["relevant_policies"]["cobweb_model"]["production_response"]
    assert response["decision_profile"] == "profit_balanced"
    assert response["mechanism_reproduction_required"] is False


def test_cobweb_c3_input_filter_keeps_operating_evidence_but_removes_prescriptive_quantity():
    state = {
        "recovery_guard": {
            "summary": {"should_recover_any": True, "active_candidate_count": 1},
            "candidates": [{
                "should_recover_now": True,
                "confirmed_order_backlog_quantity": 20,
                "recommended_plan_quantity": 80,
            }],
        },
        "margin_guard": {
            "candidates": [{
                "should_recover_now": True,
                "recommended_plan_quantity": 80,
                "projected_revenue": 16000,
                "projected_total_cost": 9000,
                "projected_gross_profit": 7000,
            }],
        },
    }
    c3 = get_scenario_config("cobweb_divergent_profit_objective")
    filtered = StaticUtils.redact_production_agent_state_for_cobweb(
        state,
        {
            "is_cobweb_mode": True,
            "cobweb_config": c3["simulation"]["cobweb_config"],
        },
    )

    recovery = filtered["recovery_guard"]["candidates"][0]
    margin = filtered["margin_guard"]["candidates"][0]
    assert recovery["confirmed_order_backlog_quantity"] == 20
    assert "recommended_plan_quantity" not in recovery
    assert margin["projected_gross_profit"] == 7000
    assert "recommended_plan_quantity" not in margin
    assert filtered["agent_input_filter"]["mode"] == "profit_balanced_cobweb"


def test_cobweb_c3_profit_decision_support_is_non_binding_and_c2_disabled():
    c3 = get_scenario_config("cobweb_divergent_profit_objective")
    context = {
        **c3["simulation"],
        "is_cobweb_mode": True,
        "cobweb_history": [
            {
                "round": 1,
                "unit_price": 280,
                "market_supply_quantity": 100,
            },
            {
                "round": 2,
                "unit_price": 320,
                "market_supply_quantity": 80,
            },
        ],
    }
    production_state = {
        "cobweb_decision_signal": {
            "enabled": True,
            "product_id": "beer",
            "market_supply_quantity": 80,
            "external_order_quantity": 90,
            "equilibrium_quantity": 109.4117647059,
        },
        "margin_guard": {
            "candidates": [{
                "product_id": "beer",
                "estimated_unit_cost": 75,
            }],
        },
        "recovery_guard": {
            "candidates": [{
                "product_id": "beer",
                "on_hand": 20,
                "confirmed_order_backlog_quantity": 30,
                "material_feasible_quantity": 500,
            }],
        },
        "production_lines": {
            "available_capacity": 500,
        },
        "cash_guard": {
            "available_conversion_budget": 100000,
        },
    }

    support = StaticUtils.build_cobweb_profit_decision_support(
        context,
        production_state,
        round_id=3,
    )

    assert support["enabled"] is True
    assert support["mode"] == "c3_cobweb_non_binding_operating_evidence"
    contribution_candidate = next(
        item
        for item in support["candidate_outcomes"]
        if item["basis"] == "one_period_contribution_reference"
    )
    assert contribution_candidate["candidate_quantity"] == 76
    assert contribution_candidate["implied_next_market_price"] == 376
    assert "recommended_quantity" not in support
    assert "not a recommended quantity" in support["interpretation_contract"]

    c2 = get_scenario_config("cobweb_divergent")
    assert StaticUtils.build_cobweb_profit_decision_support(
        {
            **c2["simulation"],
            "is_cobweb_mode": True,
        },
        production_state,
        round_id=3,
    ) == {}


def test_cobweb_c3_analyst_runtime_prompt_preserves_open_quantity_choice():
    runner = SkillRunner.__new__(SkillRunner)
    c3 = get_scenario_config("cobweb_divergent_profit_objective")
    runner._runtime_contract = lambda: {"scenario_config": c3}

    prompt = runner._build_cobweb_profit_objective_analyst_prompt()

    assert "Treat price direction as market-risk evidence" in prompt
    assert "next two to three turns" in prompt
    assert "Do not prescribe one exact production quantity" in prompt
    assert "with explicit trade-offs" in prompt

    c2 = get_scenario_config("cobweb_divergent")
    runner._runtime_contract = lambda: {"scenario_config": c2}
    assert runner._build_cobweb_profit_objective_analyst_prompt() == ""


def _cobweb_skill_runner(tmp_path, scenario_id):
    workspace = tmp_path / "workspace"
    enterprise_dir = workspace / "enterprises" / "Manufacturer"
    (enterprise_dir / "observations").mkdir(parents=True)
    scenario = get_scenario_config(scenario_id)
    (workspace / "run_meta.json").write_text(
        json.dumps({
            "run_id": f"run-{scenario_id}-test",
            "scenario_id": scenario_id,
            "scenario_config": scenario,
        }),
        encoding="utf-8",
    )
    runner = SkillRunner.__new__(SkillRunner)
    runner.workspace_dir = workspace
    runner.client_dir = tmp_path
    runner.enterprise_spec = SimpleNamespace(
        enterprise_name="Manufacturer",
        enterprise_id="Manufacturer",
        analyst_skill_name="analyst",
    )
    return runner


def test_cobweb_c3_runtime_contract_reads_real_scenario_config(tmp_path):
    runner = _cobweb_skill_runner(
        tmp_path,
        "cobweb_divergent_profit_objective",
    )

    contract = runner._runtime_contract()

    assert contract["scenario_id"] == "cobweb_divergent_profit_objective"
    assert contract["scenario_config"]["simulation"]["cobweb_config"][
        "decision_profile"
    ] == "profit_balanced"
    assert runner.cobweb_profit_objective_enabled() is True
    assert runner._build_cobweb_profit_objective_analyst_prompt()


def test_cobweb_c3_analyst_uses_compact_non_prescriptive_input(tmp_path):
    runner = _cobweb_skill_runner(
        tmp_path,
        "cobweb_divergent_profit_objective",
    )
    raw_observation = {
        "enterprise_id": "Manufacturer",
        "finance": {
            "cash": 800000,
            "total_revenue": 600000,
            "total_cost": 50000,
            "financial_indicators": {"net_profit": 550000},
        },
        "production": {
            "products_idList": ["beer"],
            "production_lines": {
                "total": 1,
                "total_capacity": 500,
                "available_capacity": 400,
                "details": [{"line_id": "L1", "capacity": 500}],
            },
            "production_metrics": {"total_production": 1000},
            "production_plans": {"completed": [{"plan_id": "old-plan"}]},
            "cobweb_decision_signal": {
                "round_id": 32,
                "product_id": "beer",
                "current_market_price": 376,
                "market_supply_quantity": 76,
                "recommended_plan_quantity": 160,
            },
            "recovery_guard": {
                "candidates": [{
                    "product_id": "beer",
                    "confirmed_order_backlog_quantity": 160,
                    "material_feasible_quantity": 500,
                    "recommended_plan_quantity": 160,
                    "recommended_daily_capacity": 160,
                }],
            },
            "cash_guard": {
                "available_conversion_budget": 100000,
                "affordable_recovery_candidates": [{"quantity": 160}],
            },
            "margin_guard": {
                "candidates": [{
                    "product_id": "beer",
                    "estimated_unit_cost": 75,
                    "projected_gross_profit": 12000,
                    "recommended_plan_quantity": 160,
                }],
            },
        },
        "sales": {
            "sales_orders": {
                "available": [{
                    "order_id": "ORDER-33",
                    "product_id": "beer",
                    "quantity": 76,
                    "unit_price": 376,
                    "created_time": 32,
                    "delivery_deadline": 33,
                    "status": "available",
                }],
                "completed": [
                    {"order_id": f"OLD-{index}", "status": "completed"}
                    for index in range(32)
                ],
            },
            "sales_metrics": {"completed_orders": 32},
            "demand_backlog": {
                "by_product": {"beer": {"backlog_quantity": 160}},
                "received_demand_history": [
                    {"order_id": f"EVENT-{index}"}
                    for index in range(100)
                ],
            },
        },
        "inventory": {
            "inventory_items": [{
                "item_id": "beer",
                "item_type": "product",
                "quantity": 116,
                "unit_price": 75,
            }],
        },
        "hr": {"department_staffing": {"production": {"count": 3}}},
    }
    raw_path = runner._raw_analyst_observation_path(32)
    raw_path.write_text(json.dumps(raw_observation), encoding="utf-8")
    history_path = runner._history_projection_path(32)
    history_path.parent.mkdir(parents=True)
    history_path.write_text(
        json.dumps({
            "source_day_range": [24, 31],
            "no_future_data": True,
            "trends": {
                "operating": {
                    "cobweb_market_price": {"mean_abs_change": 20},
                },
            },
            "days": [
                {
                    "round_id": day,
                    "department_metrics": {
                        "finance": {"net_profit": day * 10},
                    },
                }
                for day in range(24, 32)
            ],
        }),
        encoding="utf-8",
    )

    spec = runner._build_analyst_run_spec(32)
    compact = json.loads(Path(spec.observation_path).read_text(encoding="utf-8"))
    serialized = json.dumps(compact, ensure_ascii=False)
    prompt = runner._build_analyst_launch_prompt(spec, 32)

    assert spec.compact_observation_enabled is True
    assert "recommended_plan_quantity" not in serialized
    assert "recommended_daily_capacity" not in serialized
    assert "affordable_recovery_candidates" not in serialized
    assert "OLD-0" not in serialized
    assert "EVENT-0" not in serialized
    assert compact["sales_state"]["available_orders"][0]["order_id"] == "ORDER-33"
    assert compact["sales_state"]["order_status_counts"]["completed"] == 32
    assert len(compact["recent_history"]["latest_days"]) == 3
    assert spec.observation_path in prompt
    assert "do not open raw observation_day32.txt" in prompt
    assert len(serialized) < len(json.dumps(raw_observation, ensure_ascii=False))


def test_cobweb_c3_analysis_validation_rejects_stale_or_prescriptive_targets(
    tmp_path,
):
    runner = _cobweb_skill_runner(
        tmp_path,
        "cobweb_divergent_profit_objective",
    )
    runner._active_analyst_round_id = 32
    valid = {
        "enterprise_name": "Manufacturer",
        "round_id": 32,
        "enterprise_summarys": "Roll to optimize long-term business performance.",
        "department_targets": {
            "sales": {
                "target": "Orders are screened on the basis of marginal gains and fulfillment risk.",
                "evaluation": "The level of services remained stable.",
                "reason": "To avoid a no-profit expansion.",
            },
            "procurement": {
                "target": "Maintain raw material cover.",
                "evaluation": "No missing material interruption.",
                "reason": "The continuity of production is guaranteed.",
            },
            "production": {
                "target": "An enforceable production range is selected on the basis of profit and cash security.",
                "evaluation": "fulfillment Backlog decreased, and the adjustment for production remained stable.",
                "reason": "Balancing marginal gains, cash, service levels and price risks.",
            },
        },
    }

    assert runner._is_valid_analysis_payload(valid) is True

    stale = json.loads(json.dumps(valid))
    stale["round_id"] = 30
    assert runner._is_valid_analysis_payload(stale) is False
    assert "expected round_id=32" in runner._last_analysis_validation_error

    exact = json.loads(json.dumps(valid))
    exact["department_targets"]["production"][
        "evaluation"
    ] = "The production quantity is 160 units, and cash is stable with fulfillment."
    assert runner._is_valid_analysis_payload(exact) is False
    assert "must not prescribe one exact quantity" in runner._last_analysis_validation_error

    leaked = json.loads(json.dumps(valid))
    leaked["department_targets"]["production"][
        "reason"
    ] = "recommended_plan_quantity=160, cash and fulfillment are feasible."
    assert runner._is_valid_analysis_payload(leaked) is False
    assert "prohibited prescriptive quantity fields" in runner._last_analysis_validation_error


def test_cobweb_c3_sales_compaction_keeps_actionable_orders():
    state = {
        "sales_orders": {
            "available": [{
                "order_id": "CURRENT",
                "product_id": "beer",
                "quantity": 76,
                "unit_price": 376,
                "created_time": 32,
                "delivery_deadline": 33,
                "status": "available",
            }],
            "completed": [
                {"order_id": f"OLD-{index}"}
                for index in range(32)
            ],
            "breached": [{"order_id": "BREACHED"}],
        },
        "demand_backlog": {
            "by_product": {"beer": {"backlog_quantity": 160}},
            "received_demand_history": [
                {"order_id": f"EVENT-{index}"}
                for index in range(100)
            ],
        },
        "sales_metrics": {"completed_orders": 32},
    }
    c3_context = {
        "active_modes": {"cobweb": True, "profit_objective": True},
    }

    compact = StaticUtils.compact_sales_agent_state_for_cobweb_profit(
        state,
        c3_context,
        round_id=32,
    )

    assert compact["sales_orders"]["available"][0]["order_id"] == "CURRENT"
    assert compact["sales_orders"]["available"][0]["deadline_status"] == "open"
    assert compact["sales_orders"]["history_summary"]["completed"] == 32
    assert "completed" not in {
        key
        for key in compact["sales_orders"]
        if key != "history_summary"
    }
    assert compact["demand_backlog"]["history_counts"][
        "received_demand_history"
    ] == 100
    assert "EVENT-0" not in json.dumps(compact)

    c2_state = StaticUtils.compact_sales_agent_state_for_cobweb_profit(
        state,
        {"active_modes": {"cobweb": True, "profit_objective": False}},
        round_id=32,
    )
    assert c2_state is state


def test_cobweb_c3_caps_daily_capacity_to_plan_quantity_only(
    monkeypatch,
    tmp_path,
):
    def state(profit_objective):
        return {
            "policy_context": {
                "active_modes": {
                    "cobweb": True,
                    "profit_objective": profit_objective,
                },
                "action_constraints": {
                    "allow_create_production_plan": True,
                    "must_respect_recovery_guard": False,
                },
            },
            "self_state": {
                "cobweb_decision_signal": {
                    "enabled": True,
                    "product_id": "beer",
                    "production_response_mode": "agent_endogenous",
                    "suggested_supply_direction": "expand_supply",
                },
                "recovery_guard": {"candidates": []},
                "margin_guard": {"candidates": []},
            },
        }

    payload = [{
        "action": {
            "action_name": "create_production_plan",
            "action_param": {
                "product_id": "beer",
                "quantity": 126,
                "daily_capacity": 130,
            },
        },
        "action_reason": "Profits mode rolling production scheduling.",
        "module_type": "ProductionManager",
        "executor_id": "Manufacturer",
    }]
    monkeypatch.setattr(
        StaticUtils,
        "_load_sibling_department_state_snapshot",
        lambda **_: state(True),
    )
    c3 = StaticUtils._normalize_department_workflow(
        json.loads(json.dumps(payload)),
        department="production",
        enterprise_name="Manufacturer",
        round_id=24,
        action_dir=tmp_path,
    )
    assert c3[0]["action"]["action_param"]["daily_capacity"] == 126
    assert "c3_cobweb_normalization" in c3[0]["action_reason"]

    monkeypatch.setattr(
        StaticUtils,
        "_load_sibling_department_state_snapshot",
        lambda **_: state(False),
    )
    c2 = StaticUtils._normalize_department_workflow(
        json.loads(json.dumps(payload)),
        department="production",
        enterprise_name="Manufacturer",
        round_id=24,
        action_dir=tmp_path,
    )
    assert c2[0]["action"]["action_param"]["daily_capacity"] == 130


def test_cobweb_c3_strict_analysis_freshness_blocks_archive_fallback(
    monkeypatch,
    tmp_path,
):
    enterprise_dir = tmp_path / "enterprises" / "Manufacturer"
    archive = enterprise_dir / "records" / "day30" / "analysis.json"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        json.dumps({"round_id": 30}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        MultiTenantConfig,
        "ENTERPRISE_DIR",
        tmp_path / "enterprises",
    )

    assert MultiTenantUtils.ensure_enterprise_analysis_file(
        "Manufacturer",
        round_id=32,
        require_round_id=32,
        allow_archive_fallback=False,
    ) is False
    assert not (enterprise_dir / "analysis.json").exists()

    assert MultiTenantUtils.ensure_enterprise_analysis_file(
        "Manufacturer",
        round_id=32,
    ) is True
    restored = json.loads(
        (enterprise_dir / "analysis.json").read_text(encoding="utf-8")
    )
    assert restored["round_id"] == 30


def test_cobweb_history_projection_reads_real_operating_state(tmp_path):
    enterprise_dir = tmp_path / "enterprises" / "Manufacturer"
    for day, price, supply, backlog in (
        (1, 280, 100, 20),
        (2, 320, 80, 10),
    ):
        department_payloads = {
            "sales": {
                "self_state": {
                    "sales_orders": {
                        "available": [{"order_id": f"O-{day}"}],
                        "completed": [{"order_id": f"C-{day}"}],
                    },
                    "sales_metrics": {
                        "total_orders": 2,
                        "accepted_orders": 1,
                    },
                    "service_level_summary": {
                        "total_downstream_demand": 100,
                        "fulfilled_downstream_demand": 90,
                        "fill_rate": 0.9,
                        "confirmed_order_backlog_quantity": backlog,
                        "lost_sales_quantity": 10,
                    },
                },
            },
            "production": {
                "self_state": {
                    "production_plans": {
                        "in_progress": [{"plan_id": f"P-{day}"}],
                    },
                    "production_lines": {
                        "details": [{"line_id": "L-1"}],
                        "available_capacity": 500,
                        "total_capacity": 600,
                    },
                    "production_metrics": {
                        "total_production": supply,
                        "total_planned": supply,
                        "total_cost": supply * 75,
                    },
                    "cobweb_decision_signal": {
                        "current_market_price": price,
                        "market_supply_quantity": supply,
                        "equilibrium_price": 242.3529411765,
                        "equilibrium_quantity": 109.4117647059,
                    },
                },
            },
            "inventory": {
                "self_state": {
                    "inventory_items": [{
                        "item_id": "beer",
                        "item_type": "product",
                        "quantity": day * 5,
                    }],
                    "warehouse_utilization": None,
                    "inventory_metrics": {
                        "warehouse_utilization": 0.2,
                    },
                },
            },
        }
        for department, payload in department_payloads.items():
            path = (
                enterprise_dir
                / "department"
                / department
                / f"day{day}"
                / f"{department}.json"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")

    projection = HistoryProjectionBuilder().build_history_window(
        enterprise_dir=enterprise_dir,
        enterprise_id="Manufacturer",
        current_day=2,
        history_days=2,
    )

    latest = projection["days"][-1]["department_metrics"]
    assert latest["sales"]["available_orders"] == 1
    assert latest["sales"]["confirmed_order_backlog_quantity"] == 10
    assert latest["production"]["available_capacity"] == 500
    assert latest["production"]["cobweb_market_price"] == 320
    assert latest["inventory"]["beer_quantity"] == 10
    assert latest["inventory"]["warehouse_utilization"] == 0.2
    operating = projection["trends"]["operating"]
    assert operating["cobweb_market_price"]["delta"] == 40
    assert operating["cobweb_market_supply"]["mean_abs_change"] == 20
    assert operating["confirmed_order_backlog_quantity"]["delta"] == -10


def test_operations_rejects_incomplete_cobweb_c3_contract():
    invalid_overrides = [
        {
            "runtime_injection": {
                "long_run_experiment_policy": {"enabled": False},
            },
        },
        {
            "runtime_injection": {
                "profit_objective_policy": {
                    "objective_weights": {
                        "net_profit": 0.50,
                        "cash_safety": 0.25,
                        "service_level": 0.25,
                    },
                },
            },
        },
        {
            "integration_profiles": {
                "capabilities": {
                    "analysis": {
                        "profile": "current_state",
                    },
                },
            },
        },
    ]

    for index, overrides in enumerate(invalid_overrides):
        repository = _MemoryOperationsRepository()
        facade = OperationsFacade(repository)
        try:
            facade.create_experiment_from_scenario(
                scenario_id="cobweb_divergent_profit_objective",
                job_id=f"job-invalid-cobweb-c3-{index}",
                config_overrides=overrides,
            )
        except ValueError as exc:
            assert "Invalid formal cobweb C3 configuration" in str(exc)
        else:
            raise AssertionError("Expected incomplete C3 configuration to fail")
        assert repository.jobs == {}


def test_single_enterprise_cases_use_analysis_branch_seed_contract():
    formal_cases = [
        "single_case_01_order_selection",
        "single_case_02_material_shortage",
        "single_case_03_capacity_bottleneck",
        "single_case_04_staff_shortage",
        "single_case_05_cash_pressure",
    ]

    for scenario_id in formal_cases:
        scenario = get_scenario_config(scenario_id)
        assert scenario["simulation"]["beer_game_product_id"] == "PRODUCT_1"
        assert scenario["runtime_injection"]["external_market_order_enterprise_id"] == "Manufacturer"
        assert scenario["enterprise_specs"][0]["enterprise_id"] == "Manufacturer"
        assert scenario["enterprise_specs"][0]["initial_capital"] == 10000000
        assert scenario["enterprise_specs"][0]["production_recipe"]["product_id"] == "PRODUCT_1"
        assert scenario["enterprise_specs"][0]["production_recipe"]["raw_materials"] == {
            "MATERIAL_1": 2.0,
        }
        assert scenario["enterprise_configs"][0]["salable_products_idList"] == ["PRODUCT_1"]
        assert scenario["enterprise_configs"][0]["purchasable_materials_idList"] == [
            "MATERIAL_1",
        ]

        initial_action_names = [
            item["action"]["action_name"]
            for batch in scenario["initial_action_batches"].values()
            for item in batch
        ]
        assert "set_product_recipe" in initial_action_names
        assert "add_inventory" in initial_action_names
        if scenario_id not in {
            "single_case_02_material_shortage",
            "single_case_04_staff_shortage",
        }:
            assert "initialize_suppliers" in initial_action_names

    prewarm_policy = get_scenario_config("single_case_01_order_selection")[
        "single_enterprise_case"
    ]
    assert prewarm_policy["source_scene_id"] == "case_01_order_selection"
    assert prewarm_policy["handoff_day"] == 10
    assert prewarm_policy["prewarm_rounds"] == 10
    assert (
        prewarm_policy["prewarm_execution_plan"]["prewarm_kind"]
        == "custom_json_actions_not_scripted_decision_policy"
    )

    capacity_case = get_scenario_config("single_case_03_capacity_bottleneck")
    assert capacity_case["enterprise_specs"][0]["initial_staffing"]["production"] == 12


def test_single_enterprise_case_policy_hides_case_answer_from_agents():
    scenario = get_scenario_config("single_case_02_material_shortage")
    policy_context = build_department_policy_context(
        dept="sales",
        round_id=10,
        enterprise_id="Manufacturer",
        enterprise_spec=scenario["enterprise_specs"][0],
        agent_simulation_context=scenario["simulation"],
        runtime_injection_config=scenario["runtime_injection"],
        scenario_id="single_case_02_material_shortage",
    )

    case_policy = policy_context["single_enterprise_diagnostic_policy"]
    assert case_policy["enabled"] is True
    assert case_policy["family"] == "single_enterprise_diagnostic"
    assert "real-time orders" in case_policy["instruction"]
    for hidden_key in (
        "case_id",
        "primary_issue",
        "expected_primary_departments",
        "preferred_actions",
        "discouraged_actions",
        "source_primary_contradiction",
    ):
        assert hidden_key not in case_policy
    assert policy_context["scenario_id"] == "single_enterprise_diagnostic"


def test_single_enterprise_cash_pressure_accepts_conservative_pass():
    evaluation = evaluate_case_action_records(
        "single_case_05_cash_pressure",
        {
            "sales": [
                {
                    "actions": [
                        {"action": {"action_name": "action_pass", "action_param": "protect cash"}}
                    ],
                    "execution_failed": False,
                }
            ],
            "production": [
                {
                    "actions": [
                        {"action": {"action_name": "action_pass", "action_param": "avoid capex"}}
                    ],
                    "execution_failed": False,
                }
            ],
        },
        allow_stable_noop_round=True,
        stable_noop_reason="No feasible order under the current cash guard",
    )

    assert evaluation["passed"] is True
    assert evaluation["relaxed_stable_noop"] is True


def test_single_enterprise_order_seed_actions_follow_prewarm_schedule():
    scenario = get_scenario_config("single_case_03_capacity_bottleneck_scripted")

    init3_action_names = [
        item["action"]["action_name"]
        for item in scenario["initial_action_batches"]["init_action_3"]
    ]
    assert init3_action_names == ["set_product_recipe", "build_production_line"]

    assert scenario["single_enterprise_case"]["deferred_init_actions"] == []
    days = scenario["single_enterprise_case"]["prewarm_execution_plan"]["days"]
    assert days["1"]["sales"]["workflow"][0]["action"]["action_name"] == "create_order"
    assert days["2"]["sales"]["workflow"][0]["action"]["action_name"] == "accept_order"


def test_single_enterprise_case01_init_line_and_prewarm_material_buffer():
    scenario = get_scenario_config("single_case_01_order_selection")

    init3_actions = [
        item["action"]
        for item in scenario["initial_action_batches"]["init_action_3"]
    ]
    assert init3_actions[-1]["action_name"] == "initialize_production_line"
    assert init3_actions[-1]["action_param"] == {
        "line_type": "small",
        "ready_at_start": True,
        "initial_status": "idle",
    }

    plan = scenario["single_enterprise_case"]["prewarm_execution_plan"]
    day4_procurement = plan["days"]["4"]["procurement"]["workflow"][0]["action"]
    assert day4_procurement["action_name"] == "create_purchase_order"
    assert day4_procurement["action_param"]["quantity"] == 2000
    assert day4_procurement["action_param"]["logistics_mode"] == "rail"

    day7_production = plan["days"]["7"]["production"]["workflow"][0]["action"]
    assert day7_production["action_name"] == "create_production_plan"
    assert day7_production["action_param"] == {
        "product_id": "PRODUCT_1",
        "quantity": 300,
        "daily_capacity": 100,
    }


def test_single_enterprise_case01_uses_market_bound_sparse_formal_orders():
    scenario = get_scenario_config("single_case_01_order_selection")
    scripted_scenario = get_scenario_config(
        "single_case_01_order_selection_scripted"
    )
    legacy_alias = get_scenario_config("single_case_01_market_insufficient")
    policy = scenario["runtime_injection"]["external_market_order_policy"]

    assert legacy_alias["meta"]["scenario_id"] == "single_case_01_order_selection"
    assert policy["mode"] == "market_bound_fixed_schedule"
    assert policy["start_day"] == 10
    assert policy["end_day"] == 18
    assert set(policy["orders_by_day"]) == {str(day) for day in range(10, 20)}
    day10 = policy["orders_by_day"]["10"]
    assert len(day10) == 1
    assert day10[0]["market_slot"] == 1
    assert day10[0]["offer_expiry_day"] == 11
    assert policy["orders_by_day"]["12"] == []
    assert policy["orders_by_day"]["16"] == []
    assert len(policy["orders_by_day"]["13"]) == 3
    assert any(
        order.get("market_slot") == 2
        for orders in policy["orders_by_day"].values()
        for order in orders
    )
    risky_large_orders = [
        order
        for orders in policy["orders_by_day"].values()
        for order in orders
        if order.get("quantity", 0) >= 500
        and order.get("unit_price", 0) <= 540
        and order.get("breach_penalty_per_unit", 0) >= 260
    ]
    premium_orders = [
        order
        for orders in policy["orders_by_day"].values()
        for order in orders
        if order.get("unit_price", 0) >= 980
        and order.get("quantity", 0) <= 180
    ]
    assert risky_large_orders
    assert premium_orders
    metrics = scenario["single_enterprise_case"]["evaluation_metrics"]
    assert "risky_order_rejection_rate" in metrics
    assert "order_portfolio_quality" in metrics

    rules = scenario["runtime_injection"]["scripted_rule_policy"][
        "single_enterprise_rules"
    ]
    assert rules["market_development_active_market_soft_cap"] == 2
    assert rules["max_market_order_sources_per_round"] == 2
    assert rules["order_selection_reference_unit_cost"] == 500
    assert rules["order_selection_low_margin_ratio"] == 0.15
    assert rules["agent_market_growth_recommendation_mode"] == "evidence_only"
    scripted_rules = scripted_scenario["runtime_injection"]["scripted_rule_policy"][
        "single_enterprise_rules"
    ]
    assert scripted_scenario["runtime_injection"]["external_market_order_policy"] == policy
    assert scripted_rules["market_development_active_market_soft_cap"] == 2
    assert scripted_rules["max_market_order_sources_per_round"] == 2
    assert scripted_rules["order_selection_reference_unit_cost"] == 500
    assert scripted_rules["agent_market_growth_recommendation_mode"] == "evidence_only"

    prewarm = scenario["single_enterprise_case"]["prewarm_execution_plan"]
    prewarm_create_orders = [
        item["action"]
        for day in prewarm["days"].values()
        for payload in day.values()
        for item in payload.get("workflow", [])
        if (item.get("action") or {}).get("action_name") == "create_order"
    ]
    assert len(prewarm_create_orders) == 2


def test_single_enterprise_case01_agent_receives_market_evidence_not_answer():
    scenario = get_scenario_config("single_case_01_order_selection")
    policy_context = build_department_policy_context(
        dept="sales",
        round_id=10,
        enterprise_id="Manufacturer",
        enterprise_spec=scenario["enterprise_specs"][0],
        agent_simulation_context=scenario["simulation"],
        runtime_injection_config=scenario["runtime_injection"],
        scenario_id="single_case_01_order_selection",
    )

    growth_policy = policy_context["relevant_policies"][
        "single_enterprise_market_growth"
    ]
    assert growth_policy["recommendation_mode"] == "evidence_only"
    assert growth_policy["active_market_soft_cap"] == 2
    assert growth_policy["development_cost"] == 50000
    assert growth_policy["development_lead_time_rounds"] == 1
    assert growth_policy["order_selection_reference_unit_cost"] == 500
    assert growth_policy["order_selection_low_margin_unit_threshold"] == 80
    assert "should_develop_market" not in growth_policy
    assert "recommended_action" not in growth_policy


def test_single_enterprise_case01_scripted_can_respond_to_low_order_entry(
    tmp_path,
):
    runner = _single_case_scripted_runner(
        tmp_path,
        "single_case_01_order_selection",
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "finance",
        10,
        {
            "cash_summary": {
                "current_cash": 1000000,
                "available_after_warning_buffer": 1000000,
            }
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        10,
        {
            "products_idList": ["PRODUCT_1"],
            "product_recipes": {
                "PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}
            },
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "inventory",
        10,
        {
            "inventory_items": [
                {
                    "item_id": "PRODUCT_1",
                    "quantity": 1000,
                    "unit_price": 500,
                }
            ]
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "sales",
        10,
        {
            "sales_orders": {
                "available": [
                    {
                        "order_id": "SALE_ORDER_3",
                        "product_id": "PRODUCT_1",
                        "quantity": 180,
                        "unit_price": 920,
                        "delivery_deadline": 13,
                        "offer_expiry_day": 11,
                    }
                ]
            },
            "markets": [{"market_id": "market_1", "status": "active"}],
            "service_level_summary": {
                "confirmed_order_backlog_quantity": 0,
                "overdue_confirmed_order_quantity": 0,
            },
        },
    )

    actions = runner._sales_action(10)
    action_names = [item["action"]["action_name"] for item in actions]

    assert action_names == ["develop_market", "accept_order"]
    assert actions[0]["action"]["action_param"] == {
        "market_type": "regional",
        "assigned_workers": 1,
    }


def test_single_enterprise_case02_exposes_candidates_without_initial_registration():
    scenario = get_scenario_config("single_case_02_material_shortage")
    initial_action_names = [
        item["action"]["action_name"]
        for batch in scenario["initial_action_batches"].values()
        for item in batch
    ]
    policy = scenario["runtime_injection"][
        "single_enterprise_supplier_selection_policy"
    ]

    assert "initialize_suppliers" not in initial_action_names
    assert policy["enabled"] is True
    assert policy["selection_mode"] == "register_on_first_purchase"
    assert [item["supplier_name"] for item in policy["candidate_suppliers"]] == [
        "Supplier_Fast",
        "Supplier_Balanced",
        "Supplier_Economy",
    ]


def test_single_enterprise_case02_policy_allows_candidate_supplier_purchase():
    scenario = get_scenario_config("single_case_02_material_shortage")
    enterprise_spec = scenario["enterprise_specs"][0]

    context = build_department_policy_context(
        dept="procurement",
        round_id=10,
        enterprise_id="Manufacturer",
        enterprise_spec=enterprise_spec,
        agent_simulation_context=scenario["simulation"],
        runtime_injection_config=scenario["runtime_injection"],
        scenario_id="single_case_02_material_shortage",
    )

    assert context["active_modes"]["single_enterprise_supplier_selection"] is True
    assert context["action_constraints"]["allow_external_purchase_order"] is True


def test_single_case_scripted_material_shortage_uses_run_meta_supplier_candidates(tmp_path):
    runner = _single_case_scripted_runner(tmp_path, "single_case_02_material_shortage")

    _write_department_state(
        tmp_path,
        "Manufacturer",
        "finance",
        10,
        {
            "cash_summary": {
                "current_cash": 1_000_000,
                "available_after_warning_buffer": 1_000_000,
            }
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "hr",
        10,
        {"department_staffing": {"PROCUREMENT": {"count": 4, "available": 4}}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        10,
        {"product_recipes": {"PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "inventory",
        10,
        {
            "inventory_items": [
                {
                    "item_id": "MATERIAL_1",
                    "quantity": 200,
                    "item_type": "raw_material",
                }
            ]
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "sales",
        10,
        {
            "sales_orders": {
                "accepted": [
                    {
                        "order_id": "SALE_ORDER_TEST",
                        "status": "accepted",
                        "quantity": 1000,
                        "delivery_deadline": 13,
                    }
                ]
            }
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "procurement",
        10,
        {
            "purchasable_materials_idList": ["MATERIAL_1"],
            "supplier_candidates": [],
            "suppliers": [],
            "materials_suppliers_matrix": {},
            "staff_summary": {"available_workers": 4},
        },
    )

    actions = runner._procurement_action(10)
    action = actions[0]["action"]

    assert action["action_name"] == "create_purchase_order"
    assert action["action_param"]["material_id"] == "MATERIAL_1"
    assert action["action_param"]["supplier_name"] == "Supplier_Balanced"
    assert action["action_param"]["logistics_mode"] == "rail"
    assert action["action_param"]["quantity"] >= 500


def test_single_case_scripted_procurement_respects_reorder_cooldown(tmp_path):
    runner = _single_case_scripted_runner(tmp_path, "single_case_02_material_shortage")
    previous_action = (
        tmp_path
        / "workspace_multi"
        / "enterprises"
        / "Manufacturer"
        / "department"
        / "procurement"
        / "day10"
        / "procurement_action.json"
    )
    previous_action.parent.mkdir(parents=True, exist_ok=True)
    previous_action.write_text(
        json.dumps(
            [
                {
                    "action": {
                        "action_name": "create_purchase_order",
                        "action_param": {
                            "material_id": "MATERIAL_1",
                            "quantity": 500,
                            "supplier_name": "Supplier_Fast",
                            "logistics_mode": "air",
                        },
                    }
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _write_department_state(
        tmp_path,
        "Manufacturer",
        "finance",
        11,
        {
            "cash_summary": {
                "current_cash": 1_000_000,
                "available_after_warning_buffer": 1_000_000,
            }
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "hr",
        11,
        {"department_staffing": {"PROCUREMENT": {"count": 4, "available": 4}}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        11,
        {"product_recipes": {"PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "inventory",
        11,
        {
            "inventory_items": [
                {
                    "item_id": "MATERIAL_1",
                    "quantity": 90,
                    "item_type": "raw_material",
                }
            ]
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "sales",
        11,
        {
            "sales_orders": {
                "accepted": [
                    {
                        "order_id": "SALE_ORDER_TEST",
                        "status": "accepted",
                        "quantity": 100,
                        "delivery_deadline": 15,
                    }
                ]
            }
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "procurement",
        11,
        {
            "purchasable_materials_idList": ["MATERIAL_1"],
            "supplier_candidates": [],
            "suppliers": [],
            "materials_suppliers_matrix": {},
            "staff_summary": {"available_workers": 4},
        },
    )

    actions = runner._procurement_action(11)

    assert actions[0]["action"]["action_name"] == "action_pass"


def test_single_case_scripted_procurement_counts_incoming_as_coverage(tmp_path):
    runner = _single_case_scripted_runner(tmp_path, "single_case_02_material_shortage")
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "finance",
        11,
        {
            "cash_summary": {
                "current_cash": 1_000_000,
                "available_after_warning_buffer": 1_000_000,
            }
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "hr",
        11,
        {"department_staffing": {"PROCUREMENT": {"count": 4, "available": 4}}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        11,
        {"product_recipes": {"PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "inventory",
        11,
        {
            "inventory_items": [
                {
                    "item_id": "MATERIAL_1",
                    "quantity": 50,
                    "item_type": "raw_material",
                }
            ]
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "sales",
        11,
        {
            "sales_orders": {
                "accepted": [
                    {
                        "order_id": "SALE_ORDER_TEST",
                        "status": "accepted",
                        "quantity": 100,
                        "delivery_deadline": 15,
                    }
                ]
            }
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "procurement",
        11,
        {
            "purchasable_materials_idList": ["MATERIAL_1"],
            "supplier_candidates": [],
            "suppliers": [],
            "materials_suppliers_matrix": {},
            "staff_summary": {"available_workers": 4},
            "replenishment": {"pending_by_material": {"MATERIAL_1": 200}},
        },
    )

    actions = runner._procurement_action(11)

    assert actions[0]["action"]["action_name"] == "action_pass"


def test_single_enterprise_cases_do_not_enable_top_tier_credit_caps():
    for case_id in [
        "single_case_01_order_selection",
        "single_case_02_material_shortage",
        "single_case_03_capacity_bottleneck",
        "single_case_04_staff_shortage",
        "single_case_05_cash_pressure",
    ]:
        scenario = get_scenario_config(case_id)
        policy = scenario["runtime_injection"]["top_tier_supply_policy"]

        assert policy["enabled"] is False
        assert policy["external_credit_limit"] == 0
        assert policy["max_single_order_share"] == 1.0


def test_single_enterprise_case05_seeded_market_orders_cover_prewarm_accepts():
    scenario = get_scenario_config("single_case_05_cash_pressure")
    plan = scenario["single_enterprise_case"]["prewarm_execution_plan"]
    handoff_day = scenario["single_enterprise_case"]["handoff_day"]
    orders_by_day = (plan.get("scripted_market_orders") or {}).get("orders_by_day") or {}
    external_order_policy = scenario["runtime_injection"]["external_market_order_policy"]
    demand_series = scenario["simulation"]["beer_game_demand_series"]
    seeded_order_count = 0
    accepted_order_numbers = []

    for day in range(0, 10):
        seeded_order_count += len(orders_by_day.get(str(day)) or [])
        sales_workflow = (
            ((plan.get("days") or {}).get(str(day)) or {})
            .get("sales", {})
            .get("workflow", [])
        )
        for item in sales_workflow:
            action = item.get("action") or {}
            if action.get("action_name") != "accept_order":
                continue
            order_id = (action.get("action_param") or {}).get("order_id")
            order_number = int(str(order_id).rsplit("_", 1)[1])
            accepted_order_numbers.append(order_number)
            assert order_number <= seeded_order_count

    assert seeded_order_count == 16
    assert max(accepted_order_numbers) == 13
    assert seeded_order_count - len(accepted_order_numbers) == 3
    assert external_order_policy["enabled"] is True
    assert external_order_policy["start_day"] == handoff_day
    assert all(quantity == 0 for quantity in demand_series[:handoff_day])
    assert all(quantity > 0 for quantity in demand_series[handoff_day:])


def test_operations_scenario_defaults_expose_market_product_and_integrity():
    defaults = _scenario_config_defaults(get_scenario_config("beer_game"))

    assert defaults["simulation"]["market_demand_mode"] == "scheduled_external_demand"
    assert defaults["simulation"]["beer_game_product_id"] == "beer"
    assert defaults["external_order_integrity"]["demand_product_id"] == "beer"
    assert defaults["external_order_integrity"]["ok"] is True
    assert defaults["external_order_integrity"]["targets"] == [
        {
            "enterprise_id": "Retailer",
            "demand_product_id": "beer",
            "salable_products_idList": ["beer"],
            "matched": True,
        }
    ]


def test_all_operations_scenario_external_order_products_match_targets():
    for scenario in get_available_scenarios():
        scenario_id = scenario["scenario_id"]
        defaults = _scenario_config_defaults(get_scenario_config(scenario_id))
        integrity = defaults["external_order_integrity"]
        assert integrity["ok"] is True, scenario_id


def test_operations_create_rejects_external_order_product_mismatch():
    repository = _MemoryOperationsRepository()
    facade = OperationsFacade(repository)

    try:
        facade.create_experiment_from_scenario(
            scenario_id="beer_game",
            config_overrides={
                "simulation": {
                    "beer_game_product_id": "smart_device",
                }
            },
        )
    except ValueError as exc:
        assert "External order product does not match" in str(exc)
    else:
        raise AssertionError("Expected mismatched external product override to fail")

    assert repository.jobs == {}
    assert repository.versions == {}


def test_operations_create_preserves_formal_cobweb_c3_configuration_snapshot():
    repository = _MemoryOperationsRepository()
    facade = OperationsFacade(repository)

    job = facade.create_experiment_from_scenario(
        scenario_id="cobweb_divergent_profit_objective",
        planned_total_steps=40,
    )
    version = repository.versions[job["config_version_id"]]
    scenario = version["scenario_config"]

    assert job["scenario_id"] == "cobweb_divergent_profit_objective"
    assert job["planned_total_steps"] == 40
    assert scenario["formal_experiment_config"]["experiment_role"] == "main_comparison"
    assert scenario["simulation"]["cobweb_config"]["stability_label"] == "divergent"
    assert scenario["simulation"]["cobweb_config"]["decision_profile"] == "profit_balanced"
    assert scenario["runtime_injection"]["profit_objective_policy"]["enabled"] is True
    assert scenario["runtime_injection"]["cobweb_enterprise_guidance_policy"]["enabled"] is False


def test_run_metrics_projection_exports_paper_variable_labels(tmp_path):
    workspace = tmp_path / "workspace"
    scenario = get_scenario_config("beer_game_profit_objective")
    run_meta = {
        "run_id": "run-paper-vars",
        "status": "running",
        "scenario_id": "beer_game_profit_objective",
        "scenario_config": scenario,
        "experiment_design": scenario["experiment_design"],
        "enterprise_ids": ["Retailer"],
        "planned_total_steps": 20,
    }
    (workspace / "round_integrity").mkdir(parents=True)
    (workspace / "run_meta.json").write_text(
        json.dumps(run_meta),
        encoding="utf-8",
    )
    (workspace / "round_integrity" / "day0.json").write_text(
        json.dumps({"ok": True, "totals": {"missing": 0}}),
        encoding="utf-8",
    )

    payload = RunMetricsProjectionBuilder().build(
        workspace_dir=Path(workspace),
        round_id=0,
    )

    assert payload["experiment_group"] == "C3"
    assert payload["decision_regime"] == "profit_seeking_agent"
    assert payload["profit_objective_enabled"] is True
    assert payload["objective_function"]["net_profit"] > 0
    assert payload["long_run_experiment"]["enabled"] is True
    assert payload["long_run_experiment"]["recommended_total_steps"] >= 40
    assert payload["long_run_experiment"]["history_days"] >= 8
    assert payload["metric_family"] == "bullwhip"
    assert "mechanism_strength" in payload["headline_metrics"]
    assert "bullwhip_ratio" in payload["primary_metrics"]
    assert (
        payload["experiment_metric_contract"]["phase"]
        == "phase2_expected_metrics"
    )
    assert "profit_objective_enabled" in (
        payload["experiment_metric_contract"]["group_specific_gates"]
    )
    assert payload["sample_inclusion"]["decision_regime_labeled"] is True
    assert payload["sample_inclusion"]["round_integrity_ok"] is True
    assert payload["sample_inclusion"]["config_snapshot_available"] is True
    assert payload["sample_inclusion"]["group_specific_gates_ok"] is True


def test_metric_projections_fall_back_to_end_of_day_observer_finance(tmp_path):
    workspace = tmp_path / "workspace"
    scenario = get_scenario_config("cobweb_divergent_scripted")
    run_meta = {
        "run_id": "run-cobweb-observer-fallback",
        "status": "completed",
        "scenario_id": "cobweb_divergent_scripted",
        "scenario_config": scenario,
        "experiment_design": scenario["experiment_design"],
        "enterprise_ids": ["Manufacturer"],
        "planned_total_steps": 40,
        "completed_steps": 40,
    }
    observer_path = (
        workspace
        / "public"
        / "observer_state"
        / "day39"
        / "end_of_day"
        / "Manufacturer.json"
    )
    observer_path.parent.mkdir(parents=True)
    (workspace / "run_meta.json").write_text(
        json.dumps(run_meta),
        encoding="utf-8",
    )
    observer_path.write_text(
        json.dumps({
            "observation": {
                "finance": {
                    "cash": 742958.6,
                    "total_revenue": 542944.0,
                    "total_cost": 59985.4,
                    "financial_indicators": {"net_profit": 482958.6},
                    "cash_summary": {"current_cash": 742958.6},
                },
            },
        }),
        encoding="utf-8",
    )

    run_payload = RunMetricsProjectionBuilder().build(
        workspace_dir=workspace,
        round_id=39,
    )
    run_enterprise = run_payload["enterprise_metrics"]["Manufacturer"]
    assert run_enterprise["history_projection_available"] is False
    assert run_enterprise["observer_state_available"] is True
    assert run_enterprise["finance_source"] == "observer_state_end_of_day"
    assert run_enterprise["finance"]["net_profit"] == 482958.6

    daily_payload = EnterpriseDailyMetricsProjectionBuilder().build(
        workspace_dir=workspace,
        round_id=39,
    )
    daily_enterprise = daily_payload["enterprise_metrics"]["Manufacturer"]
    assert daily_enterprise["history_projection_available"] is False
    assert daily_enterprise["observer_state_available"] is True
    assert daily_enterprise["finance_source"] == "observer_state_end_of_day"
    assert daily_enterprise["department_metrics"]["finance"]["cash"] == 742958.6


def test_cobweb_run_metrics_use_end_of_day_results_and_export_market_dynamics(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    scenario = get_scenario_config("cobweb_divergent_profit_objective")
    run_meta = {
        "run_id": "run-cobweb-c3-metrics",
        "status": "completed",
        "scenario_id": "cobweb_divergent_profit_objective",
        "scenario_config": scenario,
        "experiment_design": scenario["experiment_design"],
        "enterprise_ids": ["Manufacturer"],
        "planned_total_steps": 40,
        "completed_steps": 40,
    }
    (workspace / "run_meta.json").parent.mkdir(parents=True)
    (workspace / "run_meta.json").write_text(
        json.dumps(run_meta),
        encoding="utf-8",
    )
    history_path = (
        workspace
        / "projections"
        / "history"
        / "day39"
        / "Manufacturer"
        / "history_projection.json"
    )
    history_path.parent.mkdir(parents=True)
    history_path.write_text(
        json.dumps({
            "days": [{
                "department_metrics": {
                    "finance": {
                        "cash": 100,
                        "total_revenue": 40,
                        "total_cost": 30,
                        "net_profit": 10,
                    },
                },
            }],
            "trends": {
                "cash_delta": 5,
                "revenue_delta": 8,
                "net_profit_delta": 3,
                "operating": {
                    "cobweb_market_price": {"mean_abs_change": 20},
                },
            },
        }),
        encoding="utf-8",
    )
    observer_path = (
        workspace
        / "public"
        / "observer_state"
        / "day39"
        / "end_of_day"
        / "Manufacturer.json"
    )
    observer_path.parent.mkdir(parents=True)
    observer_path.write_text(
        json.dumps({
            "observation": {
                "finance": {
                    "total_revenue": 700,
                    "total_cost": 300,
                    "financial_indicators": {"net_profit": 400},
                    "cash_summary": {"current_cash": 900},
                },
                "sales": {
                    "sales_metrics": {
                        "total_orders": 40,
                        "accepted_orders": 40,
                        "completed_orders": 39,
                    },
                    "service_level_summary": {
                        "total_downstream_demand": 1000,
                        "fulfilled_downstream_demand": 960,
                        "fill_rate": 0.96,
                        "confirmed_order_backlog_quantity": 40,
                        "lost_sales_quantity": 0,
                    },
                },
                "inventory": {
                    "inventory_items": [{
                        "item_id": "beer",
                        "item_type": "product",
                        "quantity": 25,
                        "unit_price": 0.25,
                    }],
                    "warehouse_utilization": 0.1,
                },
                "production": {
                    "total_capacity": 500,
                    "available_capacity": 500,
                    "production_metrics": {
                        "total_production": 1200,
                        "total_planned": 1200,
                        "total_cost": 300,
                    },
                },
            },
        }),
        encoding="utf-8",
    )
    external_demand_path = (
        workspace
        / "public"
        / "exchange"
        / "day39"
        / "end_of_day"
        / "external_demand.json"
    )
    external_demand_path.parent.mkdir(parents=True)
    external_demand_path.write_text(
        json.dumps({
            "data": {
                "history": [
                    {
                        "round": day,
                        "unit_price": 600 if day % 2 == 0 else 40,
                        "market_supply_quantity": 1 if day % 2 == 0 else 260,
                        "market_supply_source": "agent_production_plan_created",
                    }
                    for day in range(40)
                ],
            },
        }),
        encoding="utf-8",
    )

    payload = RunMetricsProjectionBuilder().build(
        workspace_dir=workspace,
        round_id=39,
    )

    enterprise = payload["enterprise_metrics"]["Manufacturer"]
    assert enterprise["finance_source"] == (
        "observer_state_end_of_day_with_history_trends"
    )
    assert enterprise["finance"]["cash"] == 900
    assert enterprise["finance"]["net_profit"] == 400
    assert enterprise["finance"]["cash_delta"] == 5
    assert enterprise["sales"]["fill_rate"] == 0.96
    assert enterprise["inventory"]["beer_quantity"] == 25
    sensitivity = enterprise["production_cost_sensitivity"]
    assert sensitivity["cost_of_goods_sold_proxy"] == 240
    assert sensitivity["production_cost_adjusted_profit_proxy"] == 160
    assert sensitivity["accounting_status"] == (
        "sensitivity_proxy_not_financial_ledger"
    )
    assert enterprise["history_operating_trends"][
        "cobweb_market_price"
    ]["mean_abs_change"] == 20
    cobweb = payload["cobweb_metrics"]
    assert cobweb["history_round_count"] == 40
    assert cobweb["primary"]["round_count"] == 35
    assert cobweb["primary"]["mean_abs_supply_adjustment"] == 259
    assert cobweb["agent_plan_source_rounds"] == 40


def test_scripted_sales_skips_unaffordable_market_development(tmp_path):
    runner = _scripted_runner(
        tmp_path,
        {
            "enabled": True,
            "mode": "bullwhip",
            "departments": {"apply_to_departments": ["all"]},
            "single_enterprise_rules": {
                "market_development_when_no_available_orders": True,
                "market_development_cost": 50000,
                "market_development_cash_buffer": 0,
            },
        },
        enterprise_name="Retailer",
    )
    _write_department_state(
        tmp_path,
        "Retailer",
        "sales",
        0,
        {"sales_orders": {"available": []}},
    )
    _write_department_state(
        tmp_path,
        "Retailer",
        "finance",
        0,
        {"cash_summary": {"current_cash": 40000, "available_after_warning_buffer": 40000}},
    )

    actions = runner._sales_action(0)

    assert actions[0]["action"]["action_name"] == "action_pass"


def test_scripted_procurement_respects_supplier_moq_and_budget(tmp_path):
    runner = _scripted_runner(
        tmp_path,
        {
            "enabled": True,
            "mode": "bullwhip",
            "departments": {"apply_to_departments": ["all"]},
            "single_enterprise_rules": {
                "critical_cash_threshold": 0,
                "material_coverage_target_quantity": 80,
                "procurement_budget_cash_share": 1.0,
                "procurement_min_budget": 0,
            },
        },
    )
    procurement_state = {
        "purchasable_materials_idList": ["Malt"],
        "supplier_candidates": [
            {"supplier_name": "Upstream", "materials": {"Malt": {"unit_price": 1.0}}},
        ],
        "staff_summary": {"available_workers": 1},
        "materials_suppliers_matrix": {
            "Malt": [
                {
                    "supplier_name": "Upstream",
                    "unit_price": 1.0,
                    "min_order_quantity": 3000,
                }
            ],
        },
    }
    _write_department_state(tmp_path, "Supplier", "procurement", 0, procurement_state)
    _write_department_state(
        tmp_path,
        "Supplier",
        "finance",
        0,
        {"cash_summary": {"current_cash": 10000, "available_after_warning_buffer": 10000}},
    )

    actions = runner._procurement_action(0)

    assert actions[0]["action"]["action_name"] == "create_purchase_order"
    assert actions[0]["action"]["action_param"]["quantity"] == 3000

    _write_department_state(
        tmp_path,
        "Supplier",
        "finance",
        0,
        {"cash_summary": {"current_cash": 1000, "available_after_warning_buffer": 1000}},
    )

    actions = runner._procurement_action(0)

    assert actions[0]["action"]["action_name"] == "action_pass"


def test_scripted_production_caps_daily_capacity_to_available_capacity(tmp_path):
    runner = _scripted_runner(
        tmp_path,
        {
            "enabled": True,
            "mode": "commons",
            "departments": {"apply_to_departments": ["all"]},
            "production": {"base_quantity": 140, "daily_capacity": 140},
            "single_enterprise_rules": {
                "critical_cash_threshold": 0,
                "production_daily_capacity": 140,
            },
        },
        enterprise_name="Miner_A",
    )
    _write_department_state(
        tmp_path,
        "Miner_A",
        "finance",
        0,
        {"cash_summary": {"current_cash": 100000, "available_after_warning_buffer": 100000}},
    )
    _write_department_state(
        tmp_path,
        "Miner_A",
        "production",
        0,
        {
            "product_recipes": {"copper_ore": {}},
            "production_lines": {"total_capacity": 80, "available_capacity": 80},
        },
    )

    actions = runner._production_action(0)

    assert actions[0]["action"]["action_name"] == "create_production_plan"
    assert actions[0]["action"]["action_param"]["quantity"] == 140
    assert actions[0]["action"]["action_param"]["daily_capacity"] == 80

    _write_department_state(
        tmp_path,
        "Miner_A",
        "production",
        0,
        {
            "product_recipes": {"copper_ore": {}},
            "production_lines": {"total_capacity": 80, "available_capacity": 0},
        },
    )

    actions = runner._production_action(0)

    assert actions[0]["action"]["action_name"] == "action_pass"


def test_single_case_scripted_material_shortage_prioritizes_procurement(tmp_path):
    runner = _single_case_scripted_runner(tmp_path, "single_case_02_material_shortage")
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "finance",
        10,
        {"cash_summary": {"current_cash": 100000, "available_after_warning_buffer": 100000}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        10,
        {"product_recipes": {"PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "procurement",
        10,
        {
            "purchasable_materials_idList": ["MATERIAL_1"],
            "supplier_candidates": [{"supplier_name": "Supplier_A", "materials": {"MATERIAL_1": {"unit_price": 10}}}],
            "staff_summary": {"available_workers": 1},
            "materials_suppliers_matrix": {
                "MATERIAL_1": [
                    {
                        "supplier_name": "Supplier_A",
                        "unit_price": 10,
                        "min_order_quantity": 100,
                    }
                ]
            },
        },
    )

    actions = runner._procurement_action(10)

    assert actions[0]["action"]["action_name"] == "create_purchase_order"
    assert actions[0]["action"]["action_param"]["material_id"] == "MATERIAL_1"
    assert actions[0]["action"]["action_param"]["supplier_name"] == "Supplier_A"


def test_single_case_scripted_staff_shortage_prioritizes_hr(tmp_path):
    runner = _single_case_scripted_runner(tmp_path, "single_case_04_staff_shortage")
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "hr",
        10,
        {"department_staffing": {"PRODUCTION": {"count": 0, "available": 0}}},
    )

    actions = runner._hr_action(10)

    assert actions[0]["action"]["action_name"] == "handle_recruitment"
    assert actions[0]["action"]["action_param"]["department"] == "PRODUCTION"


def test_single_case_scripted_capacity_bottleneck_builds_only_on_diagnostic_rounds(tmp_path):
    runner = _single_case_scripted_runner(tmp_path, "single_case_03_capacity_bottleneck")
    for day in [10, 11, 19]:
        _write_department_state(
            tmp_path,
            "Manufacturer",
            "finance",
            day,
            {"cash_summary": {"current_cash": 100000, "available_after_warning_buffer": 100000}},
        )
        _write_department_state(
            tmp_path,
            "Manufacturer",
            "sales",
            day,
            {
                "sales_orders": {
                    "accepted": [
                        {
                            "order_id": "SALE_ORDER_1",
                            "product_id": "PRODUCT_1",
                            "quantity": 8400,
                            "status": "accepted",
                        }
                    ]
                }
            },
        )
        _write_department_state(
            tmp_path,
            "Manufacturer",
            "inventory",
            day,
            {
                "inventory_items": [
                    {"item_id": "MATERIAL_1", "item_type": "raw_material", "quantity": 20000},
                    {"item_id": "PRODUCT_1", "item_type": "product", "quantity": 0},
                ]
            },
        )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        10,
        {
            "product_recipes": {"PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}},
            "production_lines": {
                "total": 1,
                "total_capacity": 250,
                "available_capacity": 0,
                "by_status": {"working": 1},
            },
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        11,
        {
            "product_recipes": {"PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}},
            "production_lines": {
                "total": 1,
                "total_capacity": 250,
                "available_capacity": 0,
                "by_status": {"working": 1, "under_construction": 1},
            },
        },
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        19,
        {
            "product_recipes": {"PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}},
            "production_lines": {
                "total": 2,
                "total_capacity": 500,
                "available_capacity": 250,
                "by_status": {"working": 1, "idle": 1},
            },
        },
    )

    assert runner._production_action(10)[0]["action"]["action_name"] == "build_production_line"
    assert runner._production_action(11)[0]["action"]["action_name"] == "action_pass"
    assert runner._production_action(19)[0]["action"]["action_name"] == "create_production_plan"


def test_single_case_scripted_capacity_bottleneck_does_not_build_every_round(tmp_path):
    runner = _single_case_scripted_runner(tmp_path, "single_case_03_capacity_bottleneck")
    for day in [10, 11, 12]:
        _write_department_state(
            tmp_path,
            "Manufacturer",
            "finance",
            day,
            {"cash_summary": {"current_cash": 100000, "available_after_warning_buffer": 100000}},
        )
        _write_department_state(
            tmp_path,
            "Manufacturer",
            "sales",
            day,
            {
                "sales_orders": {
                    "accepted": [
                        {
                            "order_id": "SALE_ORDER_1",
                            "product_id": "PRODUCT_1",
                            "quantity": 8400,
                            "status": "accepted",
                        }
                    ]
                }
            },
        )
        _write_department_state(
            tmp_path,
            "Manufacturer",
            "inventory",
            day,
            {
                "inventory_items": [
                    {"item_id": "MATERIAL_1", "item_type": "raw_material", "quantity": 20000},
                    {"item_id": "PRODUCT_1", "item_type": "product", "quantity": 0},
                ]
            },
        )
        _write_department_state(
            tmp_path,
            "Manufacturer",
            "production",
            day,
            {
                "product_recipes": {"PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}},
                "production_lines": {
                    "total": 2,
                    "total_capacity": 500,
                    "available_capacity": 0,
                    "by_status": {"working": 2},
                },
            },
        )

    assert runner._production_action(10)[0]["action"]["action_name"] == "action_pass"
    assert runner._production_action(11)[0]["action"]["action_name"] == "action_pass"
    assert runner._production_action(12)[0]["action"]["action_name"] == "action_pass"


def test_single_case_scripted_cash_pressure_avoids_expansion_actions(tmp_path):
    runner = _single_case_scripted_runner(tmp_path, "single_case_05_cash_pressure")
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "finance",
        10,
        {"cash_summary": {"current_cash": 50000, "available_after_warning_buffer": 50000}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "sales",
        10,
        {"sales_orders": {"available": []}},
    )
    _write_department_state(
        tmp_path,
        "Manufacturer",
        "production",
        10,
        {
            "product_recipes": {"PRODUCT_1": {"raw_materials": {"MATERIAL_1": 2}}},
            "production_lines": {"total_capacity": 500, "available_capacity": 500},
        },
    )

    assert runner._sales_action(10)[0]["action"]["action_name"] == "action_pass"
    assert runner._production_action(10)[0]["action"]["action_name"] == "action_pass"
    assert runner._hr_action(10)[0]["action"]["action_name"] == "action_pass"


def test_profit_objective_policy_reaches_agent_decision_context():
    scenario = get_scenario_config("beer_game_profit_objective")
    enterprise_spec = next(
        item for item in scenario["enterprise_specs"] if item["enterprise_id"] == "Retailer"
    )

    context = build_department_policy_context(
        dept="sales",
        round_id=0,
        enterprise_id="Retailer",
        enterprise_spec=enterprise_spec,
        agent_simulation_context={
            "market_demand_mode": "scheduled_external_demand",
            "trade_mode": "scheduled_external_demand",
            "is_scheduled_external_demand_mode": True,
        },
        runtime_injection_config=scenario["runtime_injection"],
        scenario_id="beer_game_profit_objective",
    )

    assert context["active_modes"]["profit_objective"] is True
    assert context["active_modes"]["long_run_experiment"] is True
    assert context["action_constraints"]["profit_objective_enabled"] is True
    assert context["action_constraints"]["long_run_experiment_enabled"] is True
    assert "classic_mechanism_reproduction_is_not_required" in context["priority_rules"]
    assert "avoid_unprofitable_volume_chasing" in context["priority_rules"]
    assert "use_recent_history_projection_for_long_run_stability" in context["priority_rules"]
    assert context["decision_weights"]["profit_objective_net_profit"] > 0
    assert context["decision_weights"]["long_run_history_days"] >= 8
    assert (
        context["relevant_policies"]["profit_objective_policy"]["params"][
            "decision_contract"
        ]["mechanism_reproduction_required"]
        is False
    )


def test_c3_scenarios_enable_long_run_history_diagnosis():
    c3_scenario_ids = [
        item["scenario_id"]
        for item in get_available_scenarios()
        if (item.get("experiment_design") or {}).get("experiment_group") == "C3"
    ]

    assert c3_scenario_ids
    for scenario_id in c3_scenario_ids:
        scenario = get_scenario_config(scenario_id)
        runtime_injection = scenario["runtime_injection"]
        profiles = resolve_integration_profiles(scenario)

        assert runtime_injection["profit_objective_policy"]["enabled"] is True
        assert runtime_injection["profit_objective_policy"]["planning_horizon_rounds"] >= 8
        assert runtime_injection["long_run_experiment_policy"]["enabled"] is True
        assert runtime_injection["long_run_experiment_policy"]["recommended_total_steps"] >= 40
        assert profiles["capabilities"]["analysis"]["profile"] == "historical_diagnosis"
        assert profiles["capabilities"]["analysis"]["history_days"] >= 8


def test_formal_main_experiment_scenarios_are_ready_for_direct_runs():
    for scenario_id in sorted(FORMAL_MAIN_EXPERIMENT_SCENARIOS):
        scenario = get_scenario_config(scenario_id)
        simulation = scenario["simulation"]
        formal_config = scenario["formal_experiment_config"]

        assert formal_config["ready_for_formal_run"] is True
        assert formal_config["total_steps"] == 40
        assert simulation["agent_run_steps"] == 40
        assert simulation["service_total_steps"] == 40
        assert len(simulation["beer_game_demand_series"]) == 40
        assert formal_config["primary_evaluation_window"]["start_round"] == 3
        assert formal_config["primary_evaluation_window"]["end_round_inclusive"] == 37


def test_operations_scenario_defaults_expose_formal_and_long_run_config():
    scenario = get_scenario_config("beer_game_profit_objective")
    defaults = _scenario_config_defaults(scenario)

    assert defaults["simulation"]["agent_run_steps"] == 40
    assert defaults["formal_experiment_config"]["ready_for_formal_run"] is True
    assert defaults["runtime_injection"]["long_run_experiment_policy"]["enabled"] is True
    assert defaults["runtime_injection"]["profit_objective_policy"]["enabled"] is True


def test_operations_scenario_defaults_expose_cobweb_controller_preflight_fields():
    defaults = _scenario_config_defaults(get_scenario_config("cobweb_divergent"))

    assert defaults["simulation"]["cobweb_config"]["production_response_mode"] == (
        "agent_endogenous"
    )
    assert defaults["runtime_injection"]["cobweb_enterprise_guidance_policy"]["enabled"] is True
    assert defaults["runtime_injection"]["analyst_policy"]["full_analysis_interval_rounds"] == 2


def test_phase1_readiness_core_c1_c2_c3_matrix_is_complete():
    scenarios = [
        get_scenario_config(item["scenario_id"])
        | {"scenario_id": item["scenario_id"]}
        for item in get_available_scenarios()
    ]

    readiness = assess_phase1_readiness(scenarios)

    assert readiness["complete"] is True
    assert readiness["missing_groups"] == []
    assert readiness["missing_core_cells"] == []
    assert readiness["failures"] == []
    assert readiness["core_family_matrix"]["bullwhip"]["C1"]
    assert readiness["core_family_matrix"]["bullwhip"]["C2"]
    assert readiness["core_family_matrix"]["bullwhip"]["C3"]
    assert readiness["core_family_matrix"]["herding"]["C1"]
    assert readiness["core_family_matrix"]["herding"]["C2"]
    assert readiness["core_family_matrix"]["herding"]["C3"]
    assert readiness["core_family_matrix"]["commons"]["C1"]
    assert readiness["core_family_matrix"]["commons"]["C2"]
    assert readiness["core_family_matrix"]["commons"]["C3"]
    assert readiness["core_family_matrix"]["cobweb"]["C1"]
    assert readiness["core_family_matrix"]["cobweb"]["C2"]
    assert readiness["core_family_matrix"]["cobweb"]["C3"]
