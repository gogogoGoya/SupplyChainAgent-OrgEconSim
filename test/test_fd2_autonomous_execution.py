import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.fd_experiment import (
    FD2, FD2_AGENT_PROMPT_VERSION, FD2_PARTIAL_DELIVERY_PROMPT_VERSION,
    FD2_EXECUTION_POLICY_VERSION, fd2_physical_execution_policy, resolve_fd_experiment,
)
from config.simulation_preset_config import get_scenario_config
from core.fd2_fact_audit import describe_fd2_coverage, fd2_fact_warnings
from agent.fd_experiment_runtime import configure_input_access, fd2_decision_guidance, project_fd2_decision_evidence
from agent.skill_runner import SkillRunner
from agent.static_utils import StaticUtils


def configure(tmp_path, condition="full", old=False):
    scenario = FD2 + ("_scripted" if condition == "rb_reference" else "")
    config = get_scenario_config(scenario)
    config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"] = {"condition": condition}
    config = resolve_fd_experiment(config, scenario, 20)
    policy = config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"]
    if old:
        policy.pop("execution_policy_version", None)
        policy.pop("fact_audit_mode", None)
        if condition == "full":
            policy["agent_prompt_policy_version"] = FD2_PARTIAL_DELIVERY_PROMPT_VERSION
    (tmp_path / "run_meta.json").write_text(json.dumps({"scenario_config": config,
        "orchestrator": "single_enterprise", "enterprise_ids": ["Manufacturer"],
        "single_enterprise_case": config["single_enterprise_case"]}))
    return config, policy


def operating():
    return {"sales": {"sales_orders": {
        "accepted": [{"order_id": "o", "product_id": "P", "status": "accepted", "quantity": 400, "delivery_deadline": 21}],
        "breached": [{"order_id": "old", "product_id": "P", "status": "breached", "quantity": 100, "delivery_deadline": 12}]}},
        "inventory": {"inventory_items": [{"item_id": "P", "quantity": 700, "item_type": "product"},
                                          {"item_id": "M", "quantity": 1004, "item_type": "raw_material"}]},
        "production": {"production_plans": {}}}


def action(name, params, role):
    return [{"action": {"action_name": name, "action_param": params},
             "action_reason": "Independent operating decision", "module_type": role + "Manager", "executor_id": "Manufacturer"}]


@pytest.mark.parametrize("condition", ["full", "rb_reference"])
def test_shared_execution_contract_preserves_economics_and_old_runs(tmp_path, condition):
    original = get_scenario_config(FD2 + ("_scripted" if condition == "rb_reference" else ""))
    config, policy = configure(tmp_path, condition)
    assert fd2_physical_execution_policy(policy)
    assert policy["execution_policy_version"] == FD2_EXECUTION_POLICY_VERSION
    for field in ("auto_policy", "enterprise_configs", "initial_action_batches", "daily_actions"):
        assert config[field] == original[field]
    _, old = configure(tmp_path, condition, old=True)
    assert not fd2_physical_execution_policy(old)
    assert not fd2_physical_execution_policy({**policy, "scenario": "FD4"})


def normalize(tmp_path, monkeypatch, condition, old, role, payload):
    configure(tmp_path, condition, old)
    snapshots = {
        "sales": {"policy_context": {"action_constraints": {"allow_accept_order": True}},
            "self_state": {"sales_orders": {"available": [
                {"order_id": "new", "product_id": "P", "quantity": 20, "unit_price": 520,
                 "total_amount": 10400, "status": "available", "delivery_deadline": 15}]}}},
        "inventory": {"self_state": {"inventory_items": [{"item_id": "P", "quantity": 3, "item_type": "product"}]}},
        "production": {"policy_context": {"action_constraints": {"must_respect_recovery_guard": True}},
            "self_state": {"production_lines": {"available_capacity": 500, "by_status": {"idle": 1}},
                "recovery_guard": {"candidates": [{"product_id": "P", "material_feasible_quantity": 500,
                    "raw_material_snapshots": {"M": {"on_hand": 1000, "units_per_product": 2}},
                    "recommended_plan_quantity": 375, "recommended_daily_capacity": 375, "blocking_reasons": []}]}}},
    }
    monkeypatch.setattr(StaticUtils, "_load_sibling_department_state_snapshot", lambda **kwargs: snapshots.get(kwargs["department"], {}))
    monkeypatch.setattr(StaticUtils, "_load_department_state_snapshot", lambda **kwargs: snapshots.get(kwargs["department"], {}))
    monkeypatch.setattr(StaticUtils, "_get_exchange_proposal_index", lambda *args: {})
    monkeypatch.setattr(StaticUtils, "_get_exchange_buy_request_index", lambda *args: {})
    folder = tmp_path / f"enterprises/Manufacturer/department/{role}/day13"
    folder.mkdir(parents=True, exist_ok=True)
    return StaticUtils._normalize_department_workflow(payload, department=role, enterprise_name="Manufacturer",
                                                    round_id=13, action_dir=folder)


@pytest.mark.parametrize("condition", ["full", "rb_reference"])
def test_new_accept_is_not_rejected_only_for_current_stock(tmp_path, monkeypatch, condition):
    proposal = action("accept_order", {"order_id": "new"}, "Sales")
    result = normalize(tmp_path, monkeypatch, condition, False, "sales", proposal)
    assert result[0]["action"] == proposal[0]["action"]
    old = normalize(tmp_path, monkeypatch, condition, True, "sales", proposal)
    assert old[0]["action"]["action_name"] == "reject_order"


@pytest.mark.parametrize("condition", ["full", "rb_reference"])
def test_new_plan_is_not_capped_to_recommended_batch(tmp_path, monkeypatch, condition):
    proposal = action("create_production_plan", {"product_id": "P", "quantity": 500, "daily_capacity": 500}, "Production")
    result = normalize(tmp_path, monkeypatch, condition, False, "production", proposal)
    assert result[0]["action"]["action_param"]["quantity"] == 500
    old = normalize(tmp_path, monkeypatch, condition, True, "production", proposal)
    assert old[0]["action"]["action_param"]["quantity"] == 375


def test_unknown_order_is_forwarded_for_real_error_not_converted_to_pass(tmp_path, monkeypatch):
    result = normalize(tmp_path, monkeypatch, "full", False, "sales",
                       action("accept_order", {"order_id": "invented"}, "Sales"))
    assert result[0]["action"] == {"action_name": "accept_order", "action_param": {"order_id": "invented"}}


def test_missing_purchase_choices_are_not_invented_by_normalization(tmp_path, monkeypatch):
    proposal = action("create_purchase_order", {"material_id": "M", "quantity": 400}, "Procurement")
    result = normalize(tmp_path, monkeypatch, "full", False, "procurement", proposal)
    assert result[0]["action"]["action_param"] == {"material_id": "M", "quantity": 400}


def test_coverage_excludes_terminal_orders_and_does_not_invent_material_demand():
    source = operating()
    original = deepcopy(source)
    facts = describe_fd2_coverage(source, 19)
    assert source == original
    assert not facts["action_recommendations"]
    row = facts["views"][0]
    assert (row["demand"], row["finished_goods"], row["remaining_wip"], row["uncovered_committed_quantity"]) == (400, 700, 0, 0)


@pytest.mark.parametrize("plans", [None, {"in_progress": None}, {"in_progress": [None]},
    {"in_progress": [{"product_id": "P", "status": "in_progress", "quantity": 100,
                      "completion_time": 20, "progress": {"quantity_produced": None}}]}])
def test_nullable_or_incomplete_plan_records_remain_unknown(plans):
    source = operating()
    source["production"]["production_plans"] = plans
    row = describe_fd2_coverage(source, 19)["views"][0]
    assert row["remaining_wip"] is None and row["uncovered_committed_quantity"] is None


def test_late_wip_is_not_credited_to_all_orders():
    source = operating()
    source["sales"]["sales_orders"]["accepted"][0]["delivery_deadline"] = 20
    source["production"]["production_plans"] = {"in_progress": [{"product_id": "P", "status": "in_progress",
        "quantity": 100, "completion_time": 21, "progress": {"quantity_produced": 0}}]}
    assert describe_fd2_coverage(source, 19)["views"][0]["remaining_wip"] is None


@pytest.mark.parametrize("orders", [None, [None]])
def test_missing_live_order_records_are_unknown_not_zero(orders):
    source = operating()
    source["sales"]["sales_orders"] = {"accepted": orders}
    row = describe_fd2_coverage(source, 19)["views"][0]
    assert row["demand"] is None and row["uncovered_committed_quantity"] is None


def test_fact_warnings_are_optional_and_do_not_change_payload():
    payload = {"coverage_review": {"P": {"demand": 400, "finished_goods": 0, "remaining_wip": 0,
        "buffer": 0, "net_gap": 400, "production_required": True, "procurement_required": True}}}
    original = deepcopy(payload)
    warnings = fd2_fact_warnings(payload, "analyst", {"observation": operating()}, 19)
    assert any("finished_goods" in warning for warning in warnings)
    assert any("separate evidenced benefit" in warning for warning in warnings)
    assert payload == original
    assert fd2_fact_warnings({}, "analyst", {"observation": {}}, 19) == []
    payload["coverage_review"]["P"].update(finished_goods=700, net_gap=0,
        production_required=False, procurement_required=False)
    assert fd2_fact_warnings(payload, "analyst", {"observation": operating()}, 19) == []


def test_v3_market_projection_keeps_cost_and_timing_not_action_commands(tmp_path):
    configure(tmp_path)
    source = {"agent_decision_brief": {"fast_path_rule": "develop_market", "market_growth_signal": {
        "should_develop_market": True, "recommendation_mode": "computed_recommendation",
        "development_cost": 50000, "development_lead_time_rounds": 1, "active_market_count": 1}},
        "self_state": {"recovery_guard": {"summary": {"capacity_budget": 375, "available_capacity": 500}}}}
    original = deepcopy(source)
    result = project_fd2_decision_evidence(source, tmp_path, 19)
    signal = result["agent_decision_brief"]["market_growth_signal"]
    assert signal == {"recommendation_mode": "evidence_only", "development_cost": 50000,
                      "development_lead_time_rounds": 1, "active_market_count": 1}
    assert result["self_state"]["recovery_guard"]["summary"] == {"available_capacity": 500}
    assert result["fd_evidence_contract"]["schema_version"] == "fd2_delivery_coverage.v3"
    assert source == original
    configure(tmp_path, old=True)
    old = project_fd2_decision_evidence(source, tmp_path, 19)
    assert old["agent_decision_brief"]["market_growth_signal"] == source["agent_decision_brief"]["market_growth_signal"]
    assert old["self_state"]["recovery_guard"]["summary"]["capacity_budget"] == 375


@pytest.mark.parametrize("role", ["Production", "Procurement", "Sales", "HR", "Analyst"])
def test_v3_guidance_keeps_autonomy_without_fixed_answers(tmp_path, role):
    configure(tmp_path)
    text = fd2_decision_guidance(tmp_path, 19, role)
    assert 'No decision_check' in text and 'Nonblocking' in text
    assert "SALE_ORDER" not in text and "Supplier_Fast" not in text
    assert not fd2_decision_guidance(tmp_path, 9, role)
    assert not fd2_decision_guidance(tmp_path, 20, role)
    # English guidance uses more characters than the equivalent localized text.
    assert len(text) < 6000


def test_soft_warning_is_once_and_never_blocks_stop(tmp_path):
    configure(tmp_path)
    spec = SimpleNamespace(role="Production", skill_name="production", output_file_path=str(tmp_path / "action.json"))
    Path(spec.output_file_path).write_text(json.dumps(action("create_production_plan",
        {"product_id": "P", "quantity": 100, "daily_capacity": 100}, "Production")))
    options = configure_input_access(SimpleNamespace(cwd=str(tmp_path), hooks={}), spec, tmp_path, tmp_path,
        "Manufacturer", 19, output_validator=lambda: None, output_warning_provider=lambda: ["Known coverage contradiction"])
    post = options.hooks["PostToolUse"][-1].hooks[0]
    args = {"tool_name": "Read", "tool_input": {"file_path": spec.output_file_path}, "tool_response": {}}
    first = asyncio.run(post(args, "r1", None))
    assert 'Nonblocking' in first["hookSpecificOutput"]["additionalContext"]
    assert asyncio.run(post(args, "r2", None)) == {}
    assert asyncio.run(options.hooks["Stop"][-1].hooks[0]({}, "stop", None)) == {}


def test_department_audit_warns_but_returns_no_decision_error(tmp_path):
    configure(tmp_path)
    board = {"departments": {"sales": {"confirmed_order_backlog_quantity": 400, "stale_backlog_quantity": 0},
        "inventory": {"products": {"P": 700}}, "production": {"total_production_plans": 0},
        "procurement": {"recipe_recovery_signal_by_material": {"M": {"linked_products": ["P"]}}}}}
    state, coordination, analysis = [tmp_path / name for name in ("state.json", "board.json", "analysis.json")]
    state.write_text("{}")
    coordination.write_text(json.dumps(board))
    analysis.write_text("{}")
    target = tmp_path / "procurement_action.json"
    target.write_text(json.dumps(action("create_purchase_order",
        {"material_id": "M", "quantity": 400, "supplier_name": "Fast", "logistics_mode": "air"}, "Procurement")))
    spec = SimpleNamespace(role="Procurement", output_file_path=str(target), state_path=str(state),
        blackboard_path=str(coordination), analysis_path=str(analysis))
    runner = SkillRunner.__new__(SkillRunner)
    runner.workspace_dir = tmp_path
    assert runner._department_decision_warnings(spec, 19)
    assert runner._department_decision_errors(spec, 19) == []
    assert runner._department_warning_records[str(target.resolve())]


def test_analyst_contradiction_does_not_fail_analysis_validation(tmp_path):
    configure(tmp_path)
    source = tmp_path / "observation.json"
    source.write_text(json.dumps(operating()))
    runner = SkillRunner.__new__(SkillRunner)
    runner.workspace_dir = tmp_path
    runner.enterprise_spec = SimpleNamespace(enterprise_name="Manufacturer")
    runner._active_analyst_round_id = 19
    runner._active_analyst_observation_path = str(source)
    runner._validate_single_enterprise_analysis_payload = lambda data: True
    runner._validate_cobweb_profit_analysis_payload = lambda data: True
    runner._validate_long_horizon_analysis_payload = lambda data: True
    payload = {"enterprise_name": "Manufacturer", "round_id": 19, "enterprise_summarys": "Operating review",
        "department_targets": {role: {"target": "Review", "reason": "Current evidence", "evaluation": "Actual delivery"}
                               for role in ("sales", "procurement", "production", "hr")},
        "coverage_review": {"P": {"demand": 400, "finished_goods": 0, "remaining_wip": 0,
                                  "buffer": 0, "net_gap": 400, "production_required": True}}}
    assert runner._is_valid_analysis_payload(payload)
    assert runner._analysis_decision_warnings


@pytest.mark.skipif(not os.getenv("FD2_ARCHIVE_AUDIT"), reason="Opt-in read-only real archive audit")
def test_current_archive_v3_projection_and_covered_proposals(tmp_path):
    configure(tmp_path)
    root = Path(os.environ["FD2_ARCHIVE_AUDIT"])
    sources = list((root / "projections/fd_inputs").glob("day1*/Manufacturer/*/*.json"))
    sources += list((root / "enterprises/Manufacturer/analyst_inputs").glob("day1*/observation_compact.json"))
    assert len(sources) == 90
    for source in sources:
        original = source.read_bytes()
        day = next(int(part[3:]) for part in source.parts if part.startswith("day") and part[3:].isdigit())
        projected = project_fd2_decision_evidence(json.loads(original), tmp_path, day)
        assert projected["fd_evidence_contract"]["schema_version"] == "fd2_delivery_coverage.v3"
        assert not projected["fd_coverage_evidence"]["action_recommendations"]
        assert source.read_bytes() == original
    inputs = root / "projections/fd_inputs/day19/Manufacturer"
    for role in ("production", "procurement"):
        payload = json.loads((root / f"enterprises/Manufacturer/department/{role}/day19/{role}_action.json").read_text())
        sources = {"state": json.loads((inputs / role / f"{role}.json").read_text()),
                   "coordination": json.loads((inputs / role / "blackboard.json").read_text())}
        assert fd2_fact_warnings(payload, role, sources, 19)
