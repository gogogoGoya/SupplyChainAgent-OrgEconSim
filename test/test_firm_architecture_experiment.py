"""Architecture experiments: real business engine, deterministic model fixtures, no API calls."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import os

import pytest

from config.firm_architecture_experiment import (SCENARIOS, VERSION, LEGACY_VERSION, FD_ABLATION_VERSION, load_baseline, economic_contract,
                                               build_scenario, policy_from_config, validate_scenario)
from config.simulation_preset_config import get_scenario_config, get_available_scenarios
from core.firm_decision_protocol import MODULES, aggregate_quality, read_json, validate_bundle, visible_facts, write_json
from agent.firm_decision_runner import FirmDecisionRunner, classify_execution
from agent.multi_tenant_utils import MultiTenantUtils
from runtime.operations.http_router import _scenario_payload


def action(dept, enterprise, name="action_pass", params="Technical fixture; no new commitment"):
    return {"action": {"action_name": name, "action_param": params}, "action_reason": "Technical fixture",
            "module_type": MODULES[dept], "executor_id": enterprise}


def test_catalog_has_separate_variants_and_does_not_modify_sources():
    before = {spec["source"]: get_scenario_config(spec["source"]) for spec in SCENARIOS.values()}
    rows = {row["scenario_id"]: row for row in _scenario_payload(get_available_scenarios())}
    for key, spec in SCENARIOS.items():
        config = get_scenario_config(key)
        validate_scenario(config)
        assert rows[key]["config_defaults"]["firm_architecture_experiment"]["architecture"] == spec["architecture"]
        assert rows[key]["config_defaults"]["fd_experiment_options"] == []
        assert config["experiment_design"]["experiment_group"] in {"MA", "SA", "NA", "NC"}
    assert all(get_scenario_config(key) == value for key, value in before.items())
    for family in {s["family"] for s in SCENARIOS.values()}:
        ma, sa = (get_scenario_config(f"firm_ablation_{family}_{arm}") for arm in ("ma", "sa"))
        for key in ("initial_action_batches", "daily_actions", "enterprise_configs", "agent_enterprise_layout"):
            assert ma[key] == sa[key]


def test_cannot_mix_scripted_or_fd_component_ablation_or_change_identity():
    config = get_scenario_config("firm_ablation_fd4_sa")
    config["runtime_injection"]["scripted_rule_policy"]["enabled"] = True
    with pytest.raises(ValueError):
        validate_scenario(config)
    config = get_scenario_config("firm_ablation_fd4_sa")
    config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"]["condition"] = "no_analyst"
    with pytest.raises(ValueError):
        validate_scenario(config)
    with pytest.raises(ValueError):
        validate_scenario(get_scenario_config("firm_ablation_fd4_sa"), 10)


def test_fd4_ma_is_a_current_paired_control_and_legacy_remains_readable():
    ma, sa = (get_scenario_config(f"firm_ablation_fd4_{arm}") for arm in ("ma", "sa"))
    assert economic_contract(ma) == economic_contract(sa)
    assert policy_from_config(ma)["version"] == policy_from_config(sa)["version"] == FD_ABLATION_VERSION
    assert policy_from_config(ma)["dispatch_protocol"] == policy_from_config(sa)["dispatch_protocol"]
    assert ma["experiment_design"]["comparison_target"] == sa["experiment_design"]["comparison_target"] == "firm_ablation_fd4_ma"
    assert ma["experiment_design"]["decision_regime"] == sa["experiment_design"]["decision_regime"]
    policy_from_config(ma)["version"] = LEGACY_VERSION
    assert policy_from_config(ma)
    with pytest.raises(ValueError, match="retry policy"):
        validate_scenario(ma, 20)


@pytest.mark.parametrize("arm", ["ma", "sa"])
@pytest.mark.parametrize("change", ["history", "concurrency", "staffing", "coordination"])
def test_fd4_pair_rejects_economic_and_execution_drift(arm, change):
    config = get_scenario_config(f"firm_ablation_fd4_{arm}")
    if change == "history":
        config["integration_profiles"]["capabilities"]["analysis"]["history_days"] = 8
    elif change == "concurrency":
        config["simulation"]["max_department_concurrency"] = 2
    elif change == "staffing":
        config["runtime_injection"]["staffing_relaxation_policy"]["enabled"] = True
    else:
        config["integration_profiles"]["capabilities"]["coordination"]["semantic_auto_repair"] = True
    with pytest.raises(ValueError, match="frozen economic"):
        validate_scenario(config, 20)


@pytest.mark.parametrize("family", ["bullwhip", "herding_visible", "herding_hidden"])
def test_sa_c3_preserves_complete_archived_economic_contract(family):
    baseline, provenance = load_baseline(family)
    config = get_scenario_config(f"firm_ablation_{family}_sa")
    assert economic_contract(config) == economic_contract(baseline)
    assert config["experiment_design"]["decision_regime"] == "profit_seeking_agent"
    assert config["experiment_design"]["baseline_run_id"] == provenance["source_run_id"]
    assert policy_from_config(config)["version"] == VERSION
    assert policy_from_config(config)["dispatch_protocol"] == "enterprise_inline_v2"
    assert baseline["runtime_injection"]["profit_objective_policy"]["enabled"]
    assert config["integration_profiles"]["capabilities"]["analysis"]["history_days"] == 8
    # Original archives are provenance only; portable snapshots are self-contained.
    source = Path(provenance["source_meta_path"])
    if source.exists():
        import hashlib
        assert hashlib.sha256(source.read_bytes()).hexdigest() == provenance["source_meta_sha256"]


@pytest.mark.parametrize("change", ["objective", "visibility", "concurrency", "history", "controller"])
def test_sa_c3_rejects_operating_regime_drift(change):
    config = get_scenario_config("firm_ablation_herding_visible_sa")
    if change == "objective":
        config["runtime_injection"]["profit_objective_policy"]["enabled"] = False
    elif change == "visibility":
        config["simulation"]["herding_config"]["peer_visibility_enabled"] = False
    elif change == "concurrency":
        config["simulation"]["max_enterprise_concurrency"] = 1
    elif change == "history":
        config["integration_profiles"]["capabilities"]["analysis"]["history_days"] = 5
    else:
        config["runtime_injection"]["bullwhip_manufacturer_upstream_amplification"]["enabled"] = True
    with pytest.raises(ValueError, match="frozen C3"):
        validate_scenario(config)


def test_legacy_sa_stays_readable_but_cannot_be_launched_as_new_sa():
    config = get_scenario_config("firm_ablation_bullwhip_sa")
    policy_from_config(config)["version"] = LEGACY_VERSION
    assert policy_from_config(config)
    with pytest.raises(ValueError, match="Legacy architecture"):
        validate_scenario(config)


def test_c3_sa_cannot_be_mislabeled_as_a_new_operating_regime():
    config = get_scenario_config("firm_ablation_bullwhip_sa")
    config["experiment_design"]["decision_regime"] = "centralized"
    with pytest.raises(ValueError, match="metadata"):
        validate_scenario(config)


def test_context_keeps_zero_but_removes_future_and_hidden_peer_information():
    value = {"cash": 0, "staff": [], "true_demand_series": [1, 200], "beer_game_demand_series": [1, 200], "events": [{"turn": 99}],
             "policy": {"peer_summary": {"average_planned_quantity": 999}, "synchronization_index": .9}}
    hidden = visible_facts(value, peer_visible=False)
    assert hidden["cash"] == 0 and hidden["staff"] == []
    assert "true_demand_series" not in hidden and "events" not in hidden
    assert "beer_game_demand_series" not in hidden
    assert hidden["policy"]["peer_summary"] == {"visible": False}
    assert hidden["policy"]["synchronization_index"] is None


def test_bundle_rejects_missing_function_wrong_identity_and_nonfinite_params():
    bundle = {"schema_version": "firm_decision.v1", "enterprise_id": "E", "round_id": 1, "phase": "trade", "snapshot_id": "S",
              "decisions": {"sales": [action("sales", "E")]}}
    kw = dict(enterprise_id="E", round_id=1, phase="trade", snapshot_id="S", departments=["sales"],
              valid_action=MultiTenantUtils._is_valid_action_payload)
    assert not validate_bundle(bundle, **kw)
    assert validate_bundle(bundle, **{**kw, "departments": ["sales", "procurement"]})
    assert validate_bundle(bundle, **{**kw, "snapshot_id": "OLD"})
    bundle["decisions"]["sales"][0]["executor_id"] = "OTHER"
    assert validate_bundle(bundle, **kw)
    bundle["decisions"]["sales"] = [action("sales", "E", "action_pass", {"value": float("nan")})]
    assert validate_bundle(bundle, **kw)


def test_result_classification_does_not_trust_wrapper_success():
    assert classify_execution({}, {"status": "success"}) == "unknown"
    failed = {"action_type": "accept_order", "success": False}
    ok = {"action_type": "handle_recruitment", "success": True}
    assert classify_execution({"result": {"error": [failed]}}, {}) == "rejected"
    assert classify_execution({"result": {"success": [ok], "error": [failed]}}, {}) == "partial"


@pytest.fixture
def model_fixture(monkeypatch, tmp_path):
    from config.environment_config import EnvironmentConfig
    monkeypatch.setattr(EnvironmentConfig, "LOCAL_AGENT_GATEWAY_BASE_URL", "https://gateway.example.invalid")
    monkeypatch.setattr(EnvironmentConfig, "DEFAULT_AGENT_AUTH_TOKEN", "fixture-only")
    monkeypatch.setattr(EnvironmentConfig, "DEFAULT_AGENT_MODEL", "fixture-model")
    monkeypatch.setattr(EnvironmentConfig, "DEFAULT_AGENT_MODEL_LIST", ["fixture-model"] * 4)
    import agent.firm_decision_runner as firm_decision_runner
    monkeypatch.setenv("SIMULATION_PRIVATE_DIAGNOSTICS_DIR", str(tmp_path / "private_diagnostics"))
    registered = {}
    calls = []
    mode = {"invalid_first": False, "always_invalid": False, "business_failure": False,
            "control_stream_once": False, "control_stream_always": False,
            "omit_write_hooks": True,
            "fatal_message": None,
            "timeout_after_write": False, "deny_probe": False,
            "timeout_before_write_once": False, "query_error_once": False, "b2b": False, "herding_production": False,
            "workflow_returns": [], "dispatch_points": []}
    from agent.EnterpriseRuntime import EnterpriseRuntime
    original_workflow, original_trade = EnterpriseRuntime.run_workflow, EnterpriseRuntime.run_trade
    original_dispatch = FirmDecisionRunner.dispatch

    async def workflow(runtime, round_id):
        result = await original_workflow(runtime, round_id)
        mode["workflow_returns"].append((runtime.enterprise_spec.enterprise_id, round_id, "decision"))
        return result

    async def trade(runtime, round_id, **kwargs):
        result = await original_trade(runtime, round_id, **kwargs)
        mode["workflow_returns"].append((runtime.enterprise_spec.enterprise_id, round_id, "trade"))
        return result

    async def dispatch(runner, round_id, phase):
        if phase in runner.pending:
            mode["dispatch_points"].append((runner.enterprise_id, round_id, phase, list(mode["workflow_returns"])))
        return await original_dispatch(runner, round_id, phase)

    monkeypatch.setattr(EnterpriseRuntime, "run_workflow", workflow)
    monkeypatch.setattr(EnterpriseRuntime, "run_trade", trade)
    monkeypatch.setattr(FirmDecisionRunner, "dispatch", dispatch)
    original = FirmDecisionRunner._make_task

    def make(self, *args, **kwargs):
        task = original(self, *args, **kwargs)
        registered[str(task["project"])] = (self, task)
        return task
    monkeypatch.setattr(FirmDecisionRunner, "_make_task", make)

    async def fake_query(*, prompt, options):
        from claude_agent_sdk import ToolUseBlock, ToolResultBlock
        text = (await anext(prompt))["message"]["content"]
        assert options.permission_mode == "default"
        assert not options.allowed_tools and options.resume is None and not options.continue_conversation
        runner, task = registered[str(options.cwd)]
        phase = "trade" if task["root"].parent.name == "trade" else "decision"
        state = runner.pending[phase]["state"]
        number = task["summary"]["attempts"]
        directory = task["root"] / f"attempt_{number}"
        output = directory / task["output_name"]
        role = task["summary"]["role"]
        calls.append((runner.enterprise_id, state["round_id"], phase, role, number, text))
        if mode["fatal_message"] and state["round_id"] >= mode.get("fatal_from_round", 0):
            yield SimpleNamespace(model="<synthetic>", content=[SimpleNamespace(text=mode["fatal_message"])], data={})
            return
        if mode["control_stream_always"] or (mode["control_stream_once"] and number == 1):
            yield SimpleNamespace(content=[ToolUseBlock(id="closed-write", name="Write", input={"file_path": str(output), "content": "{}"})])
            yield SimpleNamespace(content=[ToolResultBlock(tool_use_id="closed-write", content="Tool permission request failed: Error: Stream closed", is_error=True)])
            raise AssertionError("A closed control channel must stop this attempt immediately")
        if role == "Enterprise" and runner.policy.get("baseline_group") == "C3":
            contract_path = next(p for p in task["inputs"] if p.name == "operating_contract.json")
            contract = read_json(contract_path)
            baseline, _ = load_baseline(runner.policy["family"])
            assert contract["profit_objective_policy"] == baseline["runtime_injection"]["profit_objective_policy"]
            assert contract["analysis_profile"]["history_days"] == 8
            assert 'Do not reproduce, strengthen or weaken a preset economic effect' in text
            if phase == "decision":
                assert state["automatic_departments_timing"] == "before_model_decision"
        if number == 1 and mode["query_error_once"]:
            raise RuntimeError("fixture provider temporarily unavailable")
        if number == 1 and mode["timeout_before_write_once"]:
            await asyncio.sleep(1)

        receipts = []

        async def use(tool, args):
            call_id = f"fixture-tool-{len(receipts)}"
            receipts.append(SimpleNamespace(content=[ToolUseBlock(id=call_id, name=tool, input=args)]))
            permission = await options.can_use_tool(tool, args, SimpleNamespace(tool_use_id=call_id))
            if permission.behavior == "deny":
                receipts.append(SimpleNamespace(content=[ToolResultBlock(tool_use_id=call_id, content=permission.message, is_error=True)]))
                return False
            data = {"tool_name": tool, "tool_input": args}
            for matcher in ([] if tool == "Write" and mode["omit_write_hooks"] else options.hooks.get("PreToolUse", [])):
                for hook in matcher.hooks:
                    answer = await hook(data, "fixture-tool", None)
                    if answer.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                        return False
            if tool == "Write":
                Path(args["file_path"]).write_text(args["content"])
            content = Path(args["file_path"]).read_text() if tool == "Read" else "Tool executed successfully"
            receipts.append(SimpleNamespace(content=[ToolResultBlock(tool_use_id=call_id, content=content, is_error=False)]))
            for matcher in ([] if tool == "Write" and mode["omit_write_hooks"] else options.hooks.get("PostToolUse", [])):
                for hook in matcher.hooks:
                    await hook(data, "fixture-tool", None)
            return True

        if mode["deny_probe"]:
            assert not await use("Read", {"file_path": str(runner.workspace / "run_meta.json")})
            assert not await use("Skill", {"skill": "unapproved_other_role"})
            assert not await use("Write", {"file_path": str(task["root"] / "attempt_4" / task["output_name"]), "content": "{}"})
        assert await use("Skill", {"skill": task["skill"]})
        # The action contract is mandatory and private to this task/role, even
        # during business repairs; it contains semantics, never extra facts.
        contract_path = task["action_execution_contract_path"]
        assert contract_path in task["inputs"]
        contract = read_json(contract_path)
        assert contract["schema_version"] == "firm_action_execution.v1"
        assert str(contract_path) in text
        if role not in {"Enterprise", "Analyst"}:
            assert set(contract["departments"]) <= {role.lower()}
        for path in task["inputs"]:
            assert await use("Read", {"file_path": str(path)})
        analysis = {"enterprise_name": runner.enterprise, "round_id": state["round_id"],
                    "enterprise_summarys": "Deterministic test fixture; not an economic experiment result.",
                    "department_targets": {dept: {"target": "Inspect authorized facts", "evaluation": "execution",
                                                   "reason": "fixture"} for dept in state["expected_functions"]}}
        if role == "Analyst":
            payload = analysis
        else:
            decisions = {}
            for dept in state["expected_functions"]:
                chosen = action(dept, runner.enterprise_id)
                if mode["herding_production"] and phase == "decision" and dept == "production":
                    chosen = action(dept, runner.enterprise_id, "create_production_plan",
                                    {"product_id": "smart_sensor", "quantity": 80, "daily_capacity": 80})
                if mode["b2b"]:
                    if phase == "decision" and dept == "procurement" and runner.enterprise == "Retailer":
                        chosen = action(dept, runner.enterprise_id, "create_replenishment_order", {
                            "material_id": "beer", "target_inventory_days": 2, "expected_daily_demand": 150})
                    elif phase == "trade" and dept in {"procurement", "sales"}:
                        card = read_json(runner.root / "department" / dept / f"day{state['round_id']}" / "trade_decision_card.json", {})
                        queue = card.get("review_queue", [])
                        if queue:
                            chosen = action(dept, runner.enterprise_id, "accept_proposal_order", {"proposal_id": queue[0]["proposal_id"]})
                if runner.policy["family"] == "fd4" and phase == "decision":
                    if dept == "hr" and state["round_id"] == 10:
                        chosen = action(dept, runner.enterprise_id, "handle_recruitment", {"department": "PRODUCTION", "num_people": 2})
                    if dept == "production" and state["round_id"] == 11:
                        quantity = 999999 if mode["business_failure"] and number == 1 else 100
                        chosen = action(dept, runner.enterprise_id, "create_production_plan",
                                        {"product_id": "PRODUCT_1", "quantity": quantity, "daily_capacity": min(quantity, 100)})
                if mode.get("fd_operations") and runner.matched_fd and runner.policy["family"] != "fd4" and state["round_id"] == 10 and dept == "production":
                    chosen = (action(dept, runner.enterprise_id, "build_production_line", {"line_type": "small"})
                              if runner.policy["family"] == "fd3" else action(dept, runner.enterprise_id,
                                  "create_production_plan", {"product_id": "PRODUCT_1", "quantity": 20, "daily_capacity": 20}))
                if mode.get("fd_operations") and runner.matched_fd and runner.policy["family"] != "fd4" and state["round_id"] == 10 and dept == "hr":
                    chosen = action(dept, runner.enterprise_id, "handle_recruitment", {"department": "PRODUCTION", "num_people": 1})
                if mode.get("fd_reject_once") and runner.matched_fd and state["round_id"] == 10 and dept == "sales" and number == 1:
                    chosen = action(dept, runner.enterprise_id, "accept_order", {"order_id": "__missing_fixture_order__"})
                decisions[dept] = [chosen]
            if 'Business repair' in text and role == "Enterprise":
                # A single rejected function is the only permitted business repair scope.
                dept = next(d for d in state["expected_functions"] if f'repair only {d}' in text)
                decisions = {dept: decisions[dept]}
            payload = {"schema_version": "firm_decision.v1", "enterprise_id": runner.enterprise_id,
                       "round_id": state["round_id"], "phase": phase, "snapshot_id": state["snapshot_id"],
                       "analysis": analysis, "decisions": decisions} if role == "Enterprise" else decisions[role.lower()]
            if mode.get("fd_messages") and runner.matched_fd and role == "Production" and runner.policy["shared_coordination_enabled"]:
                payload = {"actions": payload, "communications": [{"recipient": "procurement", "message": "Fixture coordination: inspect current material availability."}]}
        invalid = mode["always_invalid"] or (mode["invalid_first"] and number == 1)
        if number > 1:
            assert 'Previous failure and repair requirements' in text
        assert await use("Write", {"file_path": str(output), "content": "{invalid" if invalid else json.dumps(payload)})
        assert await use("Read", {"file_path": str(output)})
        yield SimpleNamespace(data={"session_id": "fixture-prior-session"}, content=[])
        for receipt in receipts:
            yield receipt
        if mode["timeout_after_write"]:
            await asyncio.sleep(1)
        yield SimpleNamespace(data={}, content="fixture")

    monkeypatch.setattr(firm_decision_runner, "query", fake_query)
    return calls, mode


def run_real_manager(tmp_path, monkeypatch, scenario, model_fixture, *, steps=None):
    from fastapi.testclient import TestClient
    from simulate.simulation_server import app
    from config.project_paths import AGENT_ROOT
    from config.simulation_preset_config import SIMULATION_SCENARIO_ENV_VAR, temporary_scenario_config_override
    from runtime.operations.runner_adapter import SimulationRunContext, default_simulation_manager_factory
    from network.exchange_manager import get_next_id_counter_value, restore_next_id_counter_value
    import agent.static_utils as static_utils

    config = get_scenario_config(scenario)
    if steps:
        config["simulation"].update(agent_run_steps=steps, service_total_steps=steps)
    policy = policy_from_config(config)
    if model_fixture[1]["timeout_after_write"] or model_fixture[1]["timeout_before_write_once"]:
        policy["attempt_timeout_seconds"] = .01
    monkeypatch.setenv("SIMULATION_SQL_MIRROR_DISABLED", "1")
    monkeypatch.setenv(SIMULATION_SCENARIO_ENV_VAR, scenario)
    monkeypatch.setattr(MultiTenantUtils, "archive_current_run_snapshot", lambda *a, **kw: None)
    workspace = tmp_path / scenario
    with TestClient(app, headers={"Authorization": "Bearer " + os.environ["SUPPLY_CHAIN_OPERATOR_TOKEN"]}) as client:
        def request(method, url, **kw):
            kw.pop("timeout", None)
            kw["headers"] = {**(kw.get("headers") or {}), "X-Simulation-Session-Id": tmp_path.name + scenario}
            return client.request(method, urlsplit(url).path, **kw)
        monkeypatch.setattr(static_utils.requests, "post", lambda url, **kw: request("POST", url, **kw))
        monkeypatch.setattr(static_utils.requests, "get", lambda url, **kw: request("GET", url, **kw))
        with temporary_scenario_config_override(scenario, config):
            # Use the production operations factory, including explicit market mode
            # and herding parameters (constructor defaults are bound at import time).
            context = SimulationRunContext(job_id="fixture", run_id="fixture", scenario_id=scenario,
                simulation_profile="single_enterprise" if policy.get("family", "").startswith("fd") or scenario.startswith("single_case_") else "multi_enterprise",
                planned_total_steps=steps or 20, client_dir=AGENT_ROOT, workspace_dir=workspace)
            manager = default_simulation_manager_factory(context)
            counter = get_next_id_counter_value()
            try:
                asyncio.run(manager.run(max_step=steps or 20, operations_run_id="run_fixture_" + scenario))
            finally:
                restore_next_id_counter_value(counter)
    return workspace


@pytest.mark.parametrize("arm", ["sa", "ma"])
def test_fd4_twenty_round_real_engine_and_actor_count(tmp_path, monkeypatch, model_fixture, arm):
    workspace = run_real_manager(tmp_path, monkeypatch, "firm_ablation_fd4_" + arm, model_fixture)
    assert read_json(workspace / "run_meta.json")["completed_steps"] == 20
    assert len(model_fixture[0]) == (10 if arm == "sa" else 50)
    quality = aggregate_quality(workspace)
    assert quality["phase_count"] == 10 and quality["available_phases"] == 10
    from core.fd4_evaluation import observation
    initial, final = observation(workspace, 9), observation(workspace, 19)
    assert final["production"]["production_metrics"]["total_production"] > initial["production"]["production_metrics"]["total_production"]
    assert final["hr"]["department_staffing"]['Production']["count"] == 2
    if arm == "ma":
        from core.firm_context import reconstruct_context
        phase = workspace / "enterprises/Manufacturer/firm_agent/day10/decision"
        for role in ["analyst", "hr", "production", "sales", "procurement"]:
            root = next(phase.glob(role + "_*"))
            manifest = read_json(root / "input_manifest.json")
            facts = reconstruct_context(manifest["context_package"]["index_path"])
            contract = read_json(manifest["output_contract"])
            assert facts["role"].lower() == role
            assert contract["output_kind"] == ("analysis_object" if role == "analyst" else "action_array")
            if role != "analyst":
                assert set(facts["departments"]) == {role}
                assert "observation" not in facts
            request = read_json(root / "attempt_1/request.json")
            assert 'losslessly partitioned' in request["prompt"]
            assert "output_kind" in request["prompt"]
            evidence = read_json(root / "attempt_1/tool_evidence.json")
            assert evidence["complete_read_paths"]
            assert set(evidence["complete_read_paths"]) <= set(evidence["successful_reads"])
        assert policy_from_config(read_json(workspace / "run_meta.json")["scenario_config"])["version"] == FD_ABLATION_VERSION
    if arm == "sa":
        inputs = workspace / "enterprises/Manufacturer/firm_agent/day10/decision/inputs"
        assert "bundle_shape" in read_json(inputs / "output_contract.json")
        index = read_json(inputs / "knowledge_index.json")
        assert "hr" in index["references"] and index["references"]["hr"]["action_templates"]
        assert Path(index["references"]["hr"]["business_reference"]).is_file()
        assert read_json(inputs.parent / "input_manifest.json")["execution_evidence_version"] == "firm_tool_receipts.v1"
        assert read_json(inputs.parent / "input_manifest.json")["context_package"]["schema_version"] == "firm_context_parts.v1"
        request = read_json(next(inputs.parent.glob("*/attempt_1/request.json")))
        assert request["input_stream_policy"] == "keep_open_until_result_or_cleanup"
        assert all(Path(p).stat().st_size <= 16384 for p in request["inputs"] if "enterprise_context" in Path(p).name or "enterprise_context_parts" in Path(p).parts[-2:])
    assert quality["unknown_execution_results"] == 0
    assert read_json(workspace / "round_integrity/day19.json")["ok"]
    output = read_json(workspace / "enterprises/Manufacturer/department/production/day11/production_result.json")
    assert any(item["action_type"] == "create_production_plan" for item in output["result"]["success"])


@pytest.mark.parametrize("family", ["bullwhip", "herding_visible", "herding_hidden"])
@pytest.mark.parametrize("arm", ["sa", "ma"])
def test_multifirm_two_stages_and_all_enterprises(tmp_path, monkeypatch, model_fixture, family, arm):
    workspace = run_real_manager(tmp_path, monkeypatch, f"firm_ablation_{family}_{arm}", model_fixture, steps=2)
    assert read_json(workspace / "run_meta.json")["completed_steps"] == 2
    summaries = list((workspace / "enterprises").glob("*/firm_agent/day*/decision/phase_summary.json"))
    assert len(summaries) == 8
    assert all(read_json(path)["decision_available"] for path in summaries)
    assert len({call[0] for call in model_fixture[0]}) == 4
    for enterprise, day, phase, returned in model_fixture[1]["dispatch_points"]:
        if arm == "sa":
            assert (enterprise, day, phase) not in returned  # dispatch happens within this workflow
        else:
            assert sum(d == day and p == phase for _, d, p in returned) == 4  # legacy global barrier
    observer = next(workspace.glob("public/observer_state/day1/end_of_day/*.json"))
    config = get_scenario_config(f"firm_ablation_{family}_{arm}")
    assert read_json(observer)["observation"]["simulation_context"]["market_demand_mode"] == config["simulation"]["market_demand_mode"]
    if arm == "sa":
        projection = read_json(workspace / "projections/run_metrics/day1/run_metrics_projection.json")
        assert projection["group_specific_gate_results"]["baseline_regime_matched"]
        assert projection["group_specific_gate_results"]["profit_objective_enabled"]
    if family == "bullwhip":
        assert any(call[2] == "trade" for call in model_fixture[0])
    else:
        metrics = read_json(workspace / "public/exchange/day1/end_of_day/herding_metrics.json")
        assert metrics["peer_visibility_enabled"] == (family == "herding_visible")
        from scripts.export_firm_ablation_evaluation import mechanism_evidence
        assert mechanism_evidence(workspace, family)["round_count"] == 2


def test_invalid_output_is_corrected_with_feedback_and_access_isolation(tmp_path, monkeypatch, model_fixture):
    model_fixture[1].update(invalid_first=True, deny_probe=True)
    workspace = run_real_manager(tmp_path, monkeypatch, "firm_ablation_bullwhip_sa", model_fixture, steps=1)
    quality = aggregate_quality(workspace)
    assert quality["recovered_tasks"] > 0
    assert quality["unavailable_phases"] == 0
    assert list(workspace.glob("enterprises/*/firm_agent/day*/**/candidate_001.raw"))


def test_unrecoverable_output_does_not_stop_world_or_become_model_pass(tmp_path, monkeypatch, model_fixture):
    model_fixture[1]["always_invalid"] = True
    workspace = run_real_manager(tmp_path, monkeypatch, "firm_ablation_bullwhip_sa", model_fixture, steps=2)
    assert read_json(workspace / "run_meta.json")["completed_steps"] == 2
    quality = aggregate_quality(workspace)
    assert quality["technical_no_submission_phases"] == quality["phase_count"]
    assert quality["available_phases"] == 0


def test_control_channel_is_rebuilt_and_recovers_before_business_dispatch(tmp_path, monkeypatch, model_fixture):
    model_fixture[1]["control_stream_once"] = True
    workspace = run_real_manager(tmp_path, monkeypatch, "firm_ablation_bullwhip_sa", model_fixture, steps=1)
    quality = aggregate_quality(workspace)
    assert quality["available_phases"] == quality["phase_count"]
    outcomes = [read_json(p) for p in workspace.glob("enterprises/*/firm_agent/day*/**/attempt_1/outcome.json")]
    assert outcomes and all(o["transport"] == "tool_control_error" for o in outcomes)
    assert quality["recovered_tasks"] == quality["model_task_count"]
    assert list(workspace.glob("enterprises/*/firm_agent/day*/**/write_intent_001.raw"))
    assert all("write_intent_" in call[-1] for call in model_fixture[0] if call[4] == 2)


@pytest.mark.parametrize("scenario", ["firm_ablation_fd4_sa", "firm_ablation_bullwhip_sa",
                                     "firm_ablation_herding_visible_sa", "firm_ablation_herding_hidden_sa", "firm_ablation_fd4_ma"])
def test_persistent_control_failure_stops_after_bounded_rebuilds(tmp_path, monkeypatch, model_fixture, scenario):
    from core.model_failure import FatalModelError
    model_fixture[1]["control_stream_always"] = True
    fd4 = "fd4" in scenario
    with pytest.raises(FatalModelError, match="agent_tool_control_unavailable"):
        run_real_manager(tmp_path, monkeypatch, scenario, model_fixture, steps=None if fd4 else 2)
    workspace = tmp_path / scenario
    meta = read_json(workspace / "run_meta.json")
    assert meta["status"] == "failed" and meta["completed_steps"] == (10 if fd4 else 0)
    assert meta["model_failure"]["code"] == "agent_tool_control_unavailable"
    assert all(call[1] == (10 if fd4 else 0) for call in model_fixture[0])
    failed = [read_json(p) for p in workspace.glob("enterprises/*/firm_agent/day*/**/task_summary.json") if read_json(p).get("status") == "failed"]
    assert failed and all(t["attempts"] == 3 and t["control_channel_error_attempts"] == 3 for t in failed)
    assert aggregate_quality(workspace)["control_channel_error_attempts"] >= 3


@pytest.mark.parametrize("arm", ["sa", "ma"])
def test_business_repair_uses_actual_rejection_and_keeps_successes(tmp_path, monkeypatch, model_fixture, arm):
    model_fixture[1]["business_failure"] = True
    workspace = run_real_manager(tmp_path, monkeypatch, "firm_ablation_fd4_" + arm, model_fixture)
    quality = aggregate_quality(workspace)
    assert quality["business_rejections"] > 0
    assert any('Business repair' in call[-1] for call in model_fixture[0])
    results = read_json(workspace / "enterprises/Manufacturer/department/production/day11/production_result.json")
    assert any(item["action_type"] == "create_production_plan" for item in results["result"]["success"])
    assert len(results["dispatch_records"]) == 2
    assert results["dispatch_records"][0]["status"] == "normalized_noop"
    assert quality["pre_execution_rejections"] > 0
    assert quality["unresolved_rejections"] == 0


@pytest.mark.parametrize("failure", ["timeout_before_write_once", "query_error_once", "timeout_after_write"])
def test_transport_failures_recover_or_keep_verified_candidate(tmp_path, monkeypatch, model_fixture, failure):
    model_fixture[1][failure] = True
    workspace = run_real_manager(tmp_path, monkeypatch, "firm_ablation_bullwhip_sa", model_fixture, steps=1)
    quality = aggregate_quality(workspace)
    assert quality["available_phases"] == quality["phase_count"]
    if failure == "timeout_after_write":
        assert quality["attempt_count"] == quality["model_task_count"]
    else:
        assert quality["recovered_tasks"] == quality["model_task_count"]
        assert all('Previous failure and repair requirements' in c[-1] for c in model_fixture[0] if c[4] > 1)
    if failure.startswith("timeout"):
        assert quality["timeout_attempts"] > 0


def test_b2b_original_stage_order_and_real_trade_responses(tmp_path, monkeypatch, model_fixture):
    model_fixture[1]["b2b"] = True
    workspace = run_real_manager(tmp_path, monkeypatch, "firm_ablation_bullwhip_sa", model_fixture, steps=1)
    records = [r for p in workspace.glob("enterprises/*/firm_agent/day0/trade/phase_summary.json")
               for group in read_json(p).get("dispatch_records", {}).values() for r in group]
    assert any(r["proposed"]["action"]["action_name"] == "accept_proposal_order" and r["status"] == "success" for r in records)


def test_herding_real_production_reaches_economic_metrics(tmp_path, monkeypatch, model_fixture):
    model_fixture[1]["herding_production"] = True
    workspace = run_real_manager(tmp_path, monkeypatch, "firm_ablation_herding_visible_sa", model_fixture, steps=2)
    metrics = read_json(workspace / "public/exchange/day1/end_of_day/herding_metrics.json")
    assert metrics["total_planned_quantity"] > 0
    results = read_json(workspace / "enterprises/Sensor_A/department/production/day0/production_result.json")
    assert any(item["action_type"] == "create_production_plan" for item in results["result"]["success"])


@pytest.mark.parametrize("scenario", ["firm_ablation_fd4_sa", "firm_ablation_bullwhip_sa",
                                     "firm_ablation_herding_visible_sa", "firm_ablation_herding_hidden_sa",
                                     "firm_ablation_fd4_ma"])
def test_real_engine_authentication_failure_stops_before_next_round(tmp_path, monkeypatch, model_fixture, scenario):
    from core.model_failure import FatalModelError
    from config.environment_config import EnvironmentConfig
    monkeypatch.setattr(EnvironmentConfig, "DEFAULT_AGENT_AUTH_TOKEN", "fixture-auth-key")
    model_fixture[1]["fatal_message"] = "API Error: 401 invalid_api_key"
    fd4 = "fd4" in scenario
    with pytest.raises(FatalModelError, match="model_authentication_failed"):
        run_real_manager(tmp_path, monkeypatch, scenario, model_fixture, steps=None if fd4 else 2)
    workspace = tmp_path / scenario
    meta = read_json(workspace / "run_meta.json")
    assert meta["status"] == "failed"
    assert meta["completed_steps"] == (10 if fd4 else 0)
    assert meta["last_complete_round"] == (9 if fd4 else None)
    assert all(call[4] == 1 for call in model_fixture[0])
    assert all(call[1] == (10 if fd4 else 0) for call in model_fixture[0])
    outcomes = [read_json(p) for p in workspace.glob("enterprises/*/firm_agent/day*/decision/*/attempt_*/outcome.json")]
    assert any(o["failure_codes"] == ["model_authentication_failed"] for o in outcomes)
    assert list((tmp_path / "private_diagnostics").glob("*.private.json"))


@pytest.mark.parametrize("scenario", ["firm_ablation_bullwhip_sa", "firm_ablation_bullwhip_ma",
                                     "firm_ablation_herding_hidden_sa"])
def test_real_engine_quota_exhaustion_stops_at_last_complete_round(tmp_path, monkeypatch, model_fixture, scenario):
    from core.model_failure import FatalModelError

    model_fixture[1]["fatal_message"] = 'API Error: 429 {"error":{"message":"Your token-plan 1-month quota has been exhausted."}}'
    model_fixture[1]["fatal_from_round"] = 1
    with pytest.raises(FatalModelError, match="model_quota_exhausted"):
        run_real_manager(tmp_path, monkeypatch, scenario, model_fixture, steps=3)
    workspace = tmp_path / scenario
    meta = read_json(workspace / "run_meta.json")
    assert meta["status"] == "failed" and meta["completed_steps"] == 1
    assert meta["last_complete_round"] == 0
    assert read_json(workspace / "model_failure.json")["model_failure"]["code"] == "model_quota_exhausted"
    assert all(c[1] <= 1 and c[4] == 1 for c in model_fixture[0])
    assert not list((tmp_path / "private_diagnostics").glob("*.private.json"))


def test_original_full_analyst_authentication_failure_also_aborts(tmp_path, monkeypatch, model_fixture):
    from core.model_failure import FatalModelError
    from config.environment_config import EnvironmentConfig
    import agent.skill_runner as skill_runner

    monkeypatch.setattr(EnvironmentConfig, "DEFAULT_AGENT_AUTH_TOKEN", "fixture-auth-key")
    calls = []

    async def rejected_query(**kwargs):
        calls.append(True)
        yield SimpleNamespace(model="<synthetic>", content=[SimpleNamespace(text="API Error: 401 invalid_api_key")], data={})

    monkeypatch.setattr(skill_runner, "query", rejected_query)
    scenario = "single_case_04_staff_shortage"
    with pytest.raises(FatalModelError):
        run_real_manager(tmp_path, monkeypatch, scenario, model_fixture)
    assert calls == [True]
    assert read_json(tmp_path / scenario / "run_meta.json")["status"] == "failed"


def test_operations_freezes_architecture_and_preserves_retry_override():
    from runtime.operations.facade import OperationsFacade
    class Repository:
        def save_version(self, version):
            self.version = version
        def create_job(self, job):
            self.job = job
    repo = Repository()
    OperationsFacade(repo).create_experiment_from_scenario(
        scenario_id="firm_ablation_bullwhip_sa", planned_total_steps=40,
        config_overrides={"runtime_injection": {"firm_architecture_experiment": {"max_attempts": 4}}})
    assert policy_from_config(repo.version["scenario_config"])["max_attempts"] == 4
    assert "firm-architecture:centralized" in repo.job["tags"]


@pytest.mark.parametrize("scenario,steps", [("firm_ablation_fd4_ma", 20), ("firm_ablation_fd4_sa", 20),
    ("firm_ablation_bullwhip_sa", 40), ("firm_ablation_herding_visible_sa", 40), ("firm_ablation_herding_hidden_sa", 40)])
def test_ui_catalog_defaults_create_the_expected_current_job(scenario, steps):
    from runtime.operations.facade import OperationsFacade
    class Repository:
        def save_version(self, version):
            self.version = version
        def create_job(self, job):
            self.job = job
    row = next(r for r in _scenario_payload(get_available_scenarios()) if r["scenario_id"] == scenario)
    defaults = row["config_defaults"]
    repo = Repository()
    OperationsFacade(repo).create_experiment_from_scenario(scenario_id=scenario, planned_total_steps=steps,
        config_overrides={"simulation": {"agent_run_steps": steps, "service_total_steps": steps},
                          "runtime_injection": {"firm_architecture_experiment": defaults["firm_architecture_experiment"]}})
    config = repo.version["scenario_config"]
    validate_scenario(config, steps)
    policy = policy_from_config(config)
    assert policy["version"] == (FD_ABLATION_VERSION if "fd4_" in scenario else VERSION)
    assert policy["execution_adapter_version"] == "firm_runtime_2026_10_21_v5"
    if policy["baseline_group"] == "C3":
        assert economic_contract(config) == economic_contract(load_baseline(policy["family"])[0])


def test_metric_contract_uses_family_without_assuming_architecture_winner():
    from persistence.experiment_metric_contract import build_experiment_metric_contract
    for family, expected in (("fd4", "single_enterprise"), ("bullwhip", "bullwhip"), ("herding_hidden", "herding")):
        config = get_scenario_config(f"firm_ablation_{family}_sa")
        contract = build_experiment_metric_contract(scenario_id=config["meta"]["scenario_id"], experiment_design=config["experiment_design"])
        assert contract["family"] == expected
        assert 'without assuming an MA or SA winner' in contract["expected_direction_for_group"]


@pytest.mark.parametrize("current_ok,cumulative_gaps,has_quality", [(True, 0, True), (False, 1, True), (True, 1, True), (True, 0, False)])
def test_projection_distinguishes_sealed_rounds_from_cumulative_decision_quality(tmp_path, current_ok, cumulative_gaps, has_quality):
    from persistence.run_metrics_projection import RunMetricsProjectionBuilder
    config = get_scenario_config("firm_ablation_bullwhip_sa")
    write_json(tmp_path / "run_meta.json", {"run_id": "fixture", "status": "completed", "completed_steps": 40,
               "scenario_id": config["meta"]["scenario_id"], "scenario_config": config,
               "experiment_design": config["experiment_design"], "enterprise_ids": []})
    write_json(tmp_path / "round_integrity/day39.json", {"ok": True, "decision_quality_ok": current_ok, "totals": {}})
    if has_quality:
        write_json(tmp_path / "firm_architecture_quality.json", {"schema_version": "firm_architecture_quality.v1",
                   "unavailable_phases": cumulative_gaps, "technical_no_submission_phases": cumulative_gaps})
    projection = RunMetricsProjectionBuilder().build(workspace_dir=tmp_path, round_id=39)
    assert projection["sample_inclusion"]["round_integrity_ok"] is True
    assert projection["round_integrity"]["decision_quality_ok"] is current_ok
    assert projection["sample_inclusion"]["architecture_decision_quality_ok"] is (current_ok and cumulative_gaps == 0 and has_quality)
    assert "architecture_decision_quality_ok" in projection["experiment_metric_contract"]["quality_gates"]


def test_legacy_c3_projection_keeps_existing_quality_gates(tmp_path):
    from persistence.run_metrics_projection import RunMetricsProjectionBuilder
    config = get_scenario_config("beer_game_profit_objective")
    write_json(tmp_path / "run_meta.json", {"scenario_id": config["meta"]["scenario_id"], "scenario_config": config,
               "experiment_design": config["experiment_design"], "enterprise_ids": []})
    projection = RunMetricsProjectionBuilder().build(workspace_dir=tmp_path, round_id=0)
    assert "architecture_decision_quality_ok" not in projection["sample_inclusion"]
    assert "architecture_decision_quality_ok" not in projection["experiment_metric_contract"]["quality_gates"]


def test_export_includes_interrupted_attempt_and_uses_local_archive(tmp_path):
    from scripts.export_firm_ablation_evaluation import export_runs
    workspace = tmp_path / "archive"
    write_json(workspace / "run_meta.json", {"run_id": "fixture", "scenario_id": "firm_ablation_bullwhip_sa",
               "status": "failed", "scenario_config": get_scenario_config("firm_ablation_bullwhip_sa")})
    phase = workspace / "enterprises/E/firm_agent/day0/decision"
    write_json(phase / "phase_started.json", {"schema_version": "firm_phase.v1", "enterprise_id": "E", "round_id": 0, "phase": "decision"})
    task = phase / "enterprise_test"
    write_json(task / "task_summary.json", {"task_id": "test", "status": "running", "attempts": 1,
               "events_path": "/nonexistent/archive/events.jsonl"})
    write_json(task / "attempt_1/request.json", {"prompt": "fixture"})
    write_json(task / "attempt_1/runtime_diagnostics.json", {"sdk_version": "fixture", "input_stream_policy": "keep_open_until_result_or_cleanup"})
    write_json(task / "attempt_4/enterprise_decision.json", {"unauthorized": True})
    result = export_runs([workspace], tmp_path / "export")
    assert result == {"runs": 1, "phases": 1, "tasks": 1, "attempts": 1}
    assert read_json(tmp_path / "export/attempts.json")[0]["transport"] == "interrupted_unknown"
    assert read_json(tmp_path / "export/attempts.json")[0]["runtime_diagnostics"]["sdk_version"] == "fixture"
    assert len(read_json(tmp_path / "export/unregistered_artifacts.json")) == 1
    assert read_json(tmp_path / "export/runs.json")[0]["unsealed_phases"] == 1
    row = read_json(tmp_path / "export/runs.json")[0]
    assert row["baseline_group"] == "C3" and row["policy_version"] == VERSION
    assert row["baseline_run_id"] == "run_5ce2a8bfd692"


def test_cancelled_task_checkpoint_counts_without_parent_task_list(tmp_path):
    phase = tmp_path / "enterprises/E/firm_agent/day0/decision"
    write_json(phase / "phase_summary.json", {"schema_version": "firm_phase.v1", "decision_available": False,
               "technical_no_submission": True, "tasks": []})
    write_json(phase / "enterprise_test/task_summary.json", {"task_id": "test", "status": "cancelled", "attempts": 1})
    quality = aggregate_quality(tmp_path)
    assert quality["attempt_count"] == quality["model_task_count"] == 1
    assert quality["available_phases"] == 0
