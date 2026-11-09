from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import asyncio

import pytest

from config.fd_experiment import (
    FD2, FD2_PARTIAL_DELIVERY_PROMPT_VERSION as FD2_AGENT_PROMPT_VERSION,
    FD2_LEGACY_AGENT_PROMPT_VERSION, fd_completion_enabled,
    resolve_fd_experiment,
)
from config.simulation_preset_config import get_scenario_config
from core.fd2_resource_evidence import describe_fd2_resources
from agent.fd_experiment_runtime import (
    configure_input_access, fd2_decision_guidance, materialize_fd_department_inputs,
    project_fd2_decision_evidence,
)


def configure_case(tmp_path):
    config = get_scenario_config(FD2)
    config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"] = {"condition": "full"}
    config = resolve_fd_experiment(config, FD2, 20)
    policy = config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"]
    policy["agent_prompt_policy_version"] = FD2_AGENT_PROMPT_VERSION
    policy.pop("execution_policy_version", None)
    policy.pop("fact_audit_mode", None)
    (tmp_path / "run_meta.json").write_text(json.dumps({"scenario_config": config}))
    return config


def use_v1(tmp_path):
    config = configure_case(tmp_path)
    config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"][
        "agent_prompt_policy_version"] = FD2_LEGACY_AGENT_PROMPT_VERSION
    (tmp_path / "run_meta.json").write_text(json.dumps({"scenario_config": config}))
    return config


def sample():
    return {
        "agent_decision_brief": {"fast_path_rule": "first_recovery_purchase: fastest_first",
            "target": "Buy more, despite coverage", "procurement_recovery_signal": {"M": {
                "on_hand": 1000, "recovery_target_material_quantity": 3126,
                "coverage_gap_quantity": 2126, "shortage_to_recovery": 2126,
                "open_coverage_sufficient": False, "recovery_stage": "rolling_replenishment",
                "supplier_option_ranking": {"fastest_first": [{"supplier_name": "Fast",
                    "unit_price": 13, "estimated_arrival_round": 13,
                    "landed_cost_for_suggested_quantity": 38768}]}}}},
        "self_state": {"recovery_guard": {"summary": {"available_capacity": 500, "capacity_budget": 375},
            "candidates": [{"product_id": "P", "on_hand": 250, "material_feasible_quantity": 500,
                "raw_material_snapshots": {"M": {"on_hand": 1000, "units_per_product": 2}},
                "confirmed_order_backlog_quantity": 1563, "blocking_reasons": [],
                "recommended_plan_quantity": 375}]},
            "recipe_recovery_signal_by_material": {"M": {"enabled": True,
                "linked_products": ["P"], "recovery_target_material_quantity": 3126,
                "recipe_daily_demand_equivalent": 1563}},
            "orders": {"pending": [{"order_id": "inbound", "quantity": 100, "arrival_time": 13}]},
            "supplier_candidates": [{"supplier_name": "Economy", "registration_status": "candidate"}],
            "product_recipes": {"P": {"raw_materials": {"M": 2}, "production_time": 1}},
            "cash_guard": {"guard_level": "healthy", "available_conversion_budget": 100000},
            "margin_guard": {"guard_level": "warning", "allow_service_recovery": True}},
        "capacity_expansion_signal": {"should_prioritize_build_line_review": False,
            "recommended_action_when_feasible": None, "reason": "No build review",
            "evidence": {"committed_order_quantity": 3126, "total_downstream_demand": 2683,
                "current_available_capacity": 500, "recent_utilization_percent": [35.4, 0]}},
        "policy_context": {"action_constraints": {"must_respect_cash_guard": True,
            "allow_build_production_line": True, "must_respect_recovery_guard": True}},
    }


def test_v2_removes_derived_targets_not_operating_records(tmp_path):
    configure_case(tmp_path)
    payload = sample()
    original = deepcopy(payload)
    result = project_fd2_decision_evidence(payload, tmp_path, 12)
    assert payload == original
    assert result["fd_evidence_contract"]["schema_version"] == "fd2_delivery_coverage.v2"
    assert result["fd_evidence_contract"]["agent_prompt_policy_version"] == FD2_AGENT_PROMPT_VERSION
    assert "fast_path_rule" not in result["agent_decision_brief"]
    signal = result["agent_decision_brief"]["procurement_recovery_signal"]["M"]
    assert signal["on_hand"] == 1000
    assert set(signal) == {"on_hand", "supplier_option_ranking"}
    assert signal["supplier_option_ranking"]["fastest_first"][0] == {
        "supplier_name": "Fast", "unit_price": 13, "estimated_arrival_round": 13}
    assert result["self_state"]["recipe_recovery_signal_by_material"]["M"] == {"linked_products": ["P"]}
    for key in ("orders", "supplier_candidates", "product_recipes", "cash_guard", "margin_guard"):
        assert result["self_state"][key] == original["self_state"][key]
    guard = result["self_state"]["recovery_guard"]
    assert guard["summary"] == original["self_state"]["recovery_guard"]["summary"]
    assert guard["candidates"][0]["raw_material_snapshots"] == original["self_state"]["recovery_guard"]["candidates"][0]["raw_material_snapshots"]
    assert result["policy_context"]["action_constraints"] == {
        "must_respect_cash_guard": True, "allow_build_production_line": True}
    assert result["agent_decision_brief"]["target"] == original["agent_decision_brief"]["target"]
    assert result["capacity_expansion_signal"]["evidence"] == {
        "current_available_capacity": 500, "recent_utilization_percent": [35.4, 0]}


def test_v1_archive_keeps_prior_projection_and_completion(tmp_path):
    config = use_v1(tmp_path)
    result = project_fd2_decision_evidence(sample(), tmp_path, 12)
    assert result["fd_evidence_contract"]["schema_version"] == "fd2_delivery_coverage.v1"
    assert "fd_resource_evidence" not in result
    assert "fast_path_rule" in result["agent_decision_brief"]
    assert "recovery_target_material_quantity" in result["agent_decision_brief"]["procurement_recovery_signal"]["M"]
    assert 'Insufficient resources for all demand' not in fd2_decision_guidance(tmp_path, 12, "production")
    assert fd_completion_enabled(config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"], "production", 12)


def test_projection_rebuilds_stale_indexes(tmp_path):
    configure_case(tmp_path)
    payload = sample()
    payload["fd_operating_evidence"] = {"facts": ["stale"]}
    payload["fd_evidence_contract"] = {"schema_version": "old"}
    payload["fd_resource_evidence"] = {"facts": ["invented"]}
    result = project_fd2_decision_evidence(payload, tmp_path, 12)
    assert result["fd_evidence_contract"]["schema_version"] == "fd2_delivery_coverage.v2"
    assert "stale" not in result["fd_operating_evidence"]["facts"]
    assert "invented" not in result["fd_resource_evidence"]["facts"]
    again = project_fd2_decision_evidence(result, tmp_path, 12)
    assert again["fd_resource_evidence"] == result["fd_resource_evidence"]
    assert again["fd_operating_evidence"] == result["fd_operating_evidence"]


@pytest.mark.parametrize("payload", [{}, {"self_state": None}, {"recovery_guard": None},
    {"recovery_guard": {"summary": None, "candidates": [None, "bad", {"on_hand": None}]}},
    {"inventory_position_by_material": {"M": {"on_hand": None, "incoming": None}}}])
def test_resource_index_preserves_unknown_without_arithmetic(payload):
    original = deepcopy(payload)
    evidence = describe_fd2_resources(payload, 12)
    assert not evidence["action_recommendations"]
    assert payload == original
    assert evidence["missing_values_are_unknown"]
    for fact in evidence["facts"]:
        assert "net_gap" not in fact["values"]


def test_resource_index_does_not_sum_copies_or_mix_units():
    payload = sample()
    payload["departments"] = {"procurement": {"inventory_position_by_material": {
        "M/~": {"item_type": "raw_material", "unit": "SU", "on_hand": 1000,
                "inventory_position": -200, "incoming": None}}}}
    evidence = describe_fd2_resources(payload, 12)
    facts = {row["kind"]: row for row in evidence["facts"]}
    assert facts["production_limits"]["values"]["capacity_budget"] == 375
    assert facts["product_and_material_limits"]["values"]["on_hand"] == 250
    assert facts["product_and_material_limits"]["values"]["raw_material_snapshots"]["M"]["on_hand"] == 1000
    assert facts["inventory_position"]["source_pointer"].endswith("/M~1~0")
    assert facts["inventory_position"]["values"]["incoming"] is None
    assert evidence["independent_sources_must_not_be_added"]


@pytest.mark.parametrize("role", ["analyst", "production", "procurement", "sales", "hr"])
def test_guidance_is_role_specific_and_bounded(tmp_path, role):
    configure_case(tmp_path)
    text = fd2_decision_guidance(tmp_path, 12, role)
    assert 'Insufficient resources for all demand do not imply no useful production' in text
    assert 'No decision_check' in text and "R19" in text
    assert "SALE_ORDER" not in text and "Supplier_Fast" not in text
    # Bound the translated English guidance without counting localized characters.
    assert len(text) < 6000
    assert not fd2_decision_guidance(tmp_path, 9, role)
    assert not fd2_decision_guidance(tmp_path, 20, role)
    assert not fd2_decision_guidance(tmp_path, 12, "finance")


def test_skill_expansion_injects_current_semantics_and_paths(tmp_path):
    configure_case(tmp_path)
    spec = SimpleNamespace(role="Production", skill_name="production", state_path=str(tmp_path / "state.json"),
        blackboard_path=str(tmp_path / "blackboard.json"), analysis_path=str(tmp_path / "analysis.json"),
        output_file_path=str(tmp_path / "production_action.json"))
    options = configure_input_access(SimpleNamespace(cwd=str(tmp_path), hooks={}), spec,
        tmp_path, tmp_path, "Manufacturer", 12, output_validator=lambda: None)
    skill_hook = options.hooks["PostToolUse"][0].hooks[0]
    context = asyncio.run(skill_hook({"tool_name": "Skill", "tool_input": {"skill": "production"}}, "s", None))
    text = context["hookSpecificOutput"]["additionalContext"]
    assert 'Insufficient resources for all demand' in text and spec.state_path in text
    assert len(options.hooks["Stop"]) == 1


def test_materialized_inputs_are_role_scoped_and_leave_sources_untouched(tmp_path):
    configure_case(tmp_path)
    state, board = tmp_path / "source.json", tmp_path / "board.json"
    state.write_text(json.dumps(sample()))
    board.write_text(json.dumps({"departments": {}, "coordination_available": False}))
    original = state.read_bytes(), board.read_bytes()
    a, b = materialize_fd_department_inputs(tmp_path, "Manufacturer", "production", 12, state, board)
    assert json.loads(a.read_text())["fd_resource_evidence"]["facts"]
    assert json.loads(b.read_text())["fd_resource_evidence"]["facts"] == []
    assert (state.read_bytes(), board.read_bytes()) == original


def test_real_executor_partial_batch_can_deliver_an_order_without_clearing_backlog():
    from core.async_time_manager import AsyncTimeManager
    from enterprise.enterprise import Enterprise

    enterprise = Enterprise({"enabled_functions": ["finance", "hr", "inventory", "production", "sales"],
                             "initial_capital": 1000000}, id="Manufacturer")
    time = AsyncTimeManager()
    enterprise.set_time_manager(time)
    enterprise.initialize_default_modules()
    modules = {key: enterprise.business_modules[key + "Manager"][0]
               for key in ("HR", "Inventory", "Production", "Sales")}
    assert modules["HR"].initialize_staffing({"production": 6, "sales": 6}).success
    assert modules["Inventory"].set_capacity(10000).success
    assert modules["Inventory"].add_inventory("MATERIAL_1", 1000, 10, "raw_material").success
    assert modules["Production"].initialize_production_line("small", ready_at_start=True).success
    assert modules["Production"].set_product_recipe("PRODUCT_1", {"MATERIAL_1": 2},
        production_time=1, labor_cost_per_unit=10, equipment_cost_per_unit=5).success
    orders = []
    for quantity, deadline in ((300, time.get_day() + 1), (1263, time.get_day() + 3)):
        order = modules["Sales"].create_order("PRODUCT_1", quantity, 500,
                                             "test_market", delivery_deadline=deadline)
        assert order.success
        assert modules["Sales"].accept_order(order.data["order_id"]).success
        orders.append(order.data["order"])
    oversized = modules["Production"].create_production_plan("PRODUCT_1", 1563, daily_capacity=500)
    assert not oversized.success
    assert any(error["code"] == "INSUFFICIENT_MATERIALS" for error in oversized.errors)
    partial = modules["Production"].create_production_plan("PRODUCT_1", 375, daily_capacity=375)
    assert partial.success
    time.fast_forward_to_next_workday_start()
    assert modules["Production"].check_completed_plans().success
    assert modules["Sales"].check_deliverable_orders().success
    assert orders[0]["status"] == "completed" and orders[0]["on_time"]
    assert orders[1]["status"] == "accepted"
    assert modules["Inventory"].inventory["PRODUCT_1"]["quantity"] == 75


@pytest.mark.skipif(not os.getenv("FD2_ARCHIVE_AUDIT"), reason="Opt-in read-only real input projection check")
def test_real_fd2_archive_inputs_project_without_mutation(tmp_path):
    root = Path(os.environ["FD2_ARCHIVE_AUDIT"])
    configure_case(tmp_path)
    sources = sorted((root / "projections/fd_inputs").glob("day1*/Manufacturer/*/*.json"))
    sources += sorted((root / "enterprises/Manufacturer/analyst_inputs").rglob("observation_compact.json"))
    assert len(sources) == 90
    for path in sources:
        raw = path.read_bytes()
        payload = json.loads(raw)
        day = next(int(part[3:]) for part in path.parts if part.startswith("day") and part[3:].isdigit())
        result = project_fd2_decision_evidence(payload, tmp_path, day)
        assert result["fd_evidence_contract"]["schema_version"] == "fd2_delivery_coverage.v2"
        assert not result["fd_resource_evidence"]["action_recommendations"]
        assert path.read_bytes() == raw
