import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.fd_experiment import FD4, FD4_AGENT_PROMPT_VERSION, FD4_WORKLOAD_PROMPT_VERSION, resolve_fd_experiment
from config.simulation_preset_config import get_scenario_config
from core.fd_forward_evidence import describe_forward_evidence
from agent.fd_experiment_runtime import compact_fd_input, configure_input_access, fd4_decision_guidance
from agent.multi_tenant_utils import MultiTenantUtils
from agent.skill_runner import SkillRunner


def observation():
    return {"hr": {"department_staffing": {"PRODUCTION": {
        "count": 3, "available": 1, "allocated": 2, "pending_recruits": 2}},
        "recruitment_status": {"recruitment_history": [
            {"record_id": "old", "department": "PRODUCTION", "num_people": 3, "complete_time": 9, "status": 'arrived'},
            {"record_id": "new", "department": "PRODUCTION", "num_people": 2, "complete_time": 12, "status": 'in_progress'}]}},
        "production": {"production_plans": {"in_progress": [
            {"plan_id": "p1", "product_id": "PRODUCT_1", "quantity": 1000, "assigned_workers": 2,
             "completion_time": 11, "status": "in_progress", "progress": {"quantity_produced": 600}}]}},
        "sales": {"sales_orders": {"accepted": [
            {"order_id": "o1", "product_id": "PRODUCT_1", "quantity": 400, "delivery_deadline": 12}]}},
        "procurement": {"orders": {"pending": [
            {"order_id": "m1", "material_id": "MATERIAL_1", "quantity": 800, "arrival_time": 11, "status": "pending"}]}}}


def setup_case(tmp_path, version=FD4_AGENT_PROMPT_VERSION, role="HR"):
    config = get_scenario_config(FD4)
    config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"] = {"condition": "full"}
    config = resolve_fd_experiment(config, FD4, 20)
    config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"]["agent_prompt_policy_version"] = version
    (tmp_path / "run_meta.json").write_text(json.dumps({"scenario_config": config}))
    spec = SimpleNamespace(role=role, skill_name=role.lower(), skill_args="--round_id 11 --enterprise_name Manufacturer",
        state_path=str(tmp_path / "state.json"), blackboard_path=str(tmp_path / "coordination.json"),
        analysis_path=str(tmp_path / "analysis.json"), trade_decision_card_path=None,
        output_file_path=str(tmp_path / f"{role.lower()}_action.json"), output_file_name=f"{role.lower()}_action.json")
    for path in (spec.state_path, spec.blackboard_path, spec.analysis_path):
        Path(path).write_text("{}")
    runner = SkillRunner.__new__(SkillRunner)
    runner.workspace_dir = runner.client_dir = tmp_path
    runner.enterprise_spec = SimpleNamespace(enterprise_id="Manufacturer", enterprise_name="Manufacturer")
    return runner, spec


def action(role="HR", check=True):
    # Same business choice that stopped run_0af911f8c0bf: legally recruit HR itself.
    item = {"action": {"action_name": "handle_recruitment", "action_param": {"department": "HR", "num_people": 2}},
            "module_type": "HRManager", "executor_id": "Manufacturer", "action_reason": "Independent department judgment."}
    if role == "Production":
        item["action"] = {"action_name": "create_production_plan", "action_param": {
            "product_id": "PRODUCT_1", "quantity": 240, "daily_capacity": 240}}
        item["module_type"] = "ProductionManager"
    if check:
        item["decision_check"] = {"net_gap": 0, "workload_ref": {"input": "analysis", "pointer": "/wrong"}}
    return [item]


def test_forecast_uses_remaining_work_and_separates_arrivals_from_current_workers():
    data = observation()
    original = deepcopy(data)
    result = describe_forward_evidence(data, 10)
    assert result["staffing"]["PRODUCTION"]["current"]["available"] == 1
    days = result["staffing"]["PRODUCTION"]["conditional_next_rounds"]
    assert [row["available_if_events_complete"] for row in days] == [3, 5, 5]
    assert next(row for row in result["events"] if row["kind"] == "production_completion")["remaining_quantity"] == 400
    assert len([row for row in result["events"] if row["kind"] == "recruitment_arrival"]) == 1
    assert result["operating_capabilities"]["recruitment_requires_hr_headcount"] is False
    assert result["action_recommendations"] is False and result["forecast_is_not_execution_capacity"] is True
    assert data == original


@pytest.mark.parametrize("day", [None, 9])
def test_unknown_or_overdue_pending_completion_does_not_become_guaranteed_headcount(day):
    data = observation()
    data["hr"]["recruitment_status"]["recruitment_history"][1]["complete_time"] = day
    result = describe_forward_evidence(data, 10)
    assert all(row["available_if_events_complete"] is None for row in
               result["staffing"]["PRODUCTION"]["conditional_next_rounds"])


def test_unlocated_pending_counts_and_unknown_release_time_remain_unknown():
    data = observation()
    data["hr"]["recruitment_status"]["recruitment_history"] = []
    data["production"]["production_plans"]["in_progress"][0]["completion_time"] = None
    result = describe_forward_evidence(data, 10)
    assert result["unknowns"]
    assert result["staffing"]["PRODUCTION"]["conditional_next_rounds"][0]["available_if_events_complete"] is None


def test_missing_pending_count_does_not_imply_zero_pending_workers():
    data = observation()
    data["hr"]["department_staffing"]["PRODUCTION"].pop("pending_recruits")
    result = describe_forward_evidence(data, 10)
    assert all(row["available_if_events_complete"] is None for row in
               result["staffing"]["PRODUCTION"]["conditional_next_rounds"])


def test_duplicate_plans_and_recruitments_do_not_double_count_and_releases_are_capped():
    data = observation()
    data["production"]["production_plans"]["in_progress"] *= 2
    data["hr"]["recruitment_status"]["recruitment_history"] *= 2
    data["production"]["production_plans"]["in_progress"][0]["assigned_workers"] = 99
    result = describe_forward_evidence(data, 10)
    assert len([row for row in result["events"] if row["kind"] == "production_completion"]) == 1
    assert [row["available_if_events_complete"] for row in
            result["staffing"]["PRODUCTION"]["conditional_next_rounds"]] == [3, 5, 5]


def test_current_arrived_workers_are_not_added_again_and_completed_plans_are_not_wip():
    data = observation()
    data["hr"]["department_staffing"]["PRODUCTION"].update(count=2, allocated=0, available=2, pending_recruits=0)
    data["hr"]["recruitment_status"]["recruitment_history"][1]["status"] = 'arrived'
    data["production"]["production_plans"]["in_progress"][0]["status"] = "completed"
    result = describe_forward_evidence(data, 10)
    assert all(row["available_if_events_complete"] == 2 for row in
               result["staffing"]["PRODUCTION"]["conditional_next_rounds"])
    assert not any(row["kind"] == "production_completion" for row in result["events"])


def test_compaction_keeps_old_open_recruitment_records():
    data = {"recruitment_history": [{"record_id": "pending", "status": 'in_progress'}]
            + [{"record_id": str(i), "status": 'arrived'} for i in range(8)]}
    assert len(compact_fd_input(data)["recruitment_history"]) == 4
    compact = compact_fd_input(data, preserve_active_records=True)
    assert len(compact["recruitment_history"]) == 5
    assert compact["recruitment_history"][0]["record_id"] == "pending"


def test_generated_analyst_projection_has_resolvable_event_refs(tmp_path):
    runner, _ = setup_case(tmp_path)
    source = runner._raw_analyst_observation_path(10)
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(observation()))
    projection = json.loads(runner._prepare_single_enterprise_analyst_observation(10).read_text())
    forward = projection["fd_forward_evidence"]
    for event in forward["events"]:
        node = projection
        for token in event["source_ref"]["pointer"][1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[int(token)] if isinstance(node, list) else node[token]
        assert isinstance(node, dict)


@pytest.mark.parametrize("role", ["HR", "Production"])
@pytest.mark.parametrize("version,blocking", [(FD4_AGENT_PROMPT_VERSION, False), (FD4_WORKLOAD_PROMPT_VERSION, True)])
def test_semantic_diagnostics_are_warnings_only_in_new_version(tmp_path, role, version, blocking):
    runner, spec = setup_case(tmp_path, version, role)
    Path(spec.output_file_path).write_text(json.dumps(action(role)))
    assert bool(runner._department_decision_errors(spec, 11)) is blocking
    assert runner._department_decision_warnings(spec, 11)


@pytest.mark.parametrize("role", ["HR", "Production"])
@pytest.mark.parametrize("check", [True, False])
@pytest.mark.parametrize("execution_ok", [True, False])
def test_autonomous_action_reaches_executor_and_real_audit_once(tmp_path, monkeypatch, role, check, execution_ok):
    import agent.skill_runner as skill_runner
    runner, spec = setup_case(tmp_path, role=role)
    runner.session_registry = SimpleNamespace(clear=lambda *a: None)
    runner.hybrid_replay_runner = SimpleNamespace(run_department=lambda *a, **kw: None)
    runner.scripted_rule_runner = SimpleNamespace(department_enabled=lambda *a: False)
    runner.build_options = lambda *a: SimpleNamespace(cwd=str(tmp_path), hooks={})
    runner._build_skill_run_spec = lambda *a: spec
    runner._communication_enabled = lambda: False
    runner._build_context_audit = lambda *a: {}
    runner._strict_context_failure = lambda *a: None
    runner._build_launch_prompt = lambda *a: "Analyst is advisory. Read local inputs."
    runner._skill_profile = lambda: {"max_retries": 1}
    runner._long_run_agent_timeout_seconds = lambda **kw: 120
    runner._next_prompt_by_failure = lambda *a, **kw: "Repair actual execution error."
    executed = []
    monkeypatch.setattr(MultiTenantUtils, "file_check_department", lambda *a: Path(spec.output_file_path).exists())
    def execute(**kwargs):
        executed.append(json.loads(Path(spec.output_file_path).read_text()))
        return {"status": "success"} if execution_ok else {"status": "error", "message": "INSUFFICIENT_STAFF"}
    monkeypatch.setattr(MultiTenantUtils, "execute_enterprise_dept_action", execute)

    async def query(*, prompt, options):
        assert [m async for m in prompt]
        guard = options.hooks["PreToolUse"][-1].hooks[0]
        post = options.hooks["PostToolUse"][-1].hooks[0]
        for path in (spec.state_path, spec.blackboard_path, spec.analysis_path):
            await guard({"tool_name": "Read", "tool_input": {"file_path": path}}, path, None)
        Path(spec.output_file_path).write_text(json.dumps(action(role, check)))
        for tool in ("Write", "Read"):
            data = {"tool_name": tool, "tool_input": {"file_path": spec.output_file_path}, "tool_response": {}}
            await guard(data, tool, None)
            feedback = await post(data, tool, None)
            assert feedback == {} or 'Nonblocking audit warning' in feedback["hookSpecificOutput"]["additionalContext"]
        assert await options.hooks["Stop"][-1].hooks[0]({}, "stop", None) == {}
        yield SimpleNamespace(data={}, content="Done")
    monkeypatch.setattr(skill_runner, "query", query)
    dept = SimpleNamespace(role=role, dept_id=role.lower(), type="Agent", name=role, timeout=120)
    if execution_ok:
        asyncio.run(runner.run_department(dept, 11))
    else:
        with pytest.raises(RuntimeError, match="Business execution failed.*INSUFFICIENT_STAFF"):
            asyncio.run(runner.run_department(dept, 11))
    assert executed == [action(role, check)]
    audit = json.loads((tmp_path / "skill_execution_audit.json").read_text())
    assert audit["success"] is execution_ok and not audit["fallback_used"]
    assert audit["decision_consistency_mode"] == "audit_only"
    assert audit["decision_consistency_warnings"]


def test_missing_output_and_readback_still_block_completion(tmp_path):
    runner, spec = setup_case(tmp_path)
    assert "missing" in runner._department_completion_error(spec, 11, {})
    Path(spec.output_file_path).write_text(json.dumps(action()))
    assert "using Write" in runner._department_completion_error(spec, 11, {})


def test_analyst_optional_check_is_not_a_semantic_gate(tmp_path):
    runner, _ = setup_case(tmp_path)
    runner._active_analyst_round_id = 11
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(observation()))
    runner._active_analyst_observation_path = str(path)
    data = {"enterprise_name": "Manufacturer", "round_id": 11, "enterprise_summarys": "Review conditional events.",
        "department_targets": {d: {"target": "Review current tasks", "reason": "Current staffing and pending events",
        "evaluation": "Actual service and cost"} for d in ("hr", "production", "procurement", "sales")}}
    assert runner._is_valid_analysis_payload(data)
    assert runner._analysis_decision_warnings
    prompt = fd4_decision_guidance(tmp_path, 11, "Analyst")
    assert "fd_forward_evidence" in prompt and 'advisory' in prompt
