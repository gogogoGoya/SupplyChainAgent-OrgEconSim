"""New FD arms exercise the real business engine with receipt-producing model fixtures."""

import pytest
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("fd_architecture_fixtures", Path(__file__).with_name("test_firm_architecture_experiment.py"))
_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
model_fixture, run_real_manager = _fixtures.model_fixture, _fixtures.run_real_manager
from config.fd_ablation_experiment import VERSION, SOURCES
from config.firm_architecture_experiment import policy_from_config, validate_scenario
from config.fd_experiment import fd2_physical_execution_policy
from config.simulation_preset_config import get_scenario_config
from core.firm_decision_protocol import read_json
from core.fd_ablation_metrics import evaluate
from scripts.export_firm_ablation_evaluation import export_runs, mechanism_evidence


@pytest.mark.parametrize("family", SOURCES)
def test_new_arms_share_economics_and_preserve_source(family):
    before = get_scenario_config(SOURCES[family])
    configs = [get_scenario_config(f"firm_ablation_{family}_{arm}") for arm in ("ma", "na", "nc", "sa")]
    for c in configs:
        validate_scenario(c, 20)
        assert policy_from_config(c)["version"] == VERSION
        assert c["simulation"]["max_department_concurrency"] == 1
        if family == "fd2":
            p = c["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"]
            assert fd2_physical_execution_policy(p)
    assert len({policy_from_config(c)["economic_contract_sha256"] for c in configs}) == 1
    assert len({policy_from_config(c)["information_contract_sha256"] for c in configs}) == 4
    assert get_scenario_config(SOURCES[family]) == before


@pytest.mark.parametrize("arm", ["ma", "na", "nc", "sa"])
@pytest.mark.parametrize("change", ["cash", "history", "arm", "retry", "prewarm", "scripted"])
@pytest.mark.parametrize("family", SOURCES)
def test_frozen_contract_rejects_drift(arm, change, family):
    c = get_scenario_config(f"firm_ablation_{family}_{arm}")
    if change == "history":
        c["integration_profiles"]["capabilities"]["analysis"]["history_days"] = 8
    elif change == "arm":
        policy_from_config(c)["arm"] = "nc" if arm != "nc" else "ma"
    elif change == "retry":
        policy_from_config(c)["max_attempts"] = 9
    elif change == "prewarm":
        c["runtime_injection"]["single_enterprise_case_policy"]["handoff_day"] = 11
    elif change == "scripted":
        c["runtime_injection"]["scripted_rule_policy"]["enabled"] = True
    else:
        c["initial_action_batches"] = []
    with pytest.raises(ValueError):
        validate_scenario(c, 20)


@pytest.mark.parametrize("family", SOURCES)
@pytest.mark.parametrize("arm", ["ma", "na", "nc", "sa"])
def test_twenty_round_followup_with_real_dispatch(tmp_path, monkeypatch, model_fixture, family, arm):
    model_fixture[1].update(fd_operations=True, fd_messages=True)
    root = run_real_manager(tmp_path, monkeypatch, f"firm_ablation_{family}_{arm}", model_fixture)
    q = read_json(root / "firm_architecture_quality.json")
    assert read_json(root / "run_meta.json")["status"] == "completed"
    assert q["available_phases"] == q["phase_count"] == 10
    assert q["model_task_count"] == {"ma": 50, "na": 40, "nc": 50, "sa": 10}[arm]
    roles = [c[3] for c in model_fixture[0]]
    assert ("Analyst" in roles) == (arm in {"ma", "nc"})
    assert ("Enterprise" in roles) == (arm == "sa")
    phase = root / "enterprises/Manufacturer/firm_agent/day10/decision"
    if arm == "na":
        analysis = read_json(phase / "inputs/analysis.json")
        assert analysis["analysis_provenance"]["model_generated"] is False
    if arm in {"ma", "na", "nc"}:
        context = read_json(phase / "inputs/procurement_context.json")
        shared = context["departments"]["procurement"]["coordination"]
        if arm == "nc":
            assert shared["coordination_available"] is False
            assert not shared.get("departments")
            assert not shared.get("department_messages")
        else:
            assert shared["department_messages"][0]["sender"] == "production"
    result = evaluate(root)
    assert result["status"] == "complete"
    assert result["summary"]["positive_proposals"] >= 1
    assert any(a["status"] == "success" for a in result["actions"] if a["proposed"]["action_name"] != "action_pass")
    assert len(result["trajectory"]) == 10
    if family == "fd4":
        initial = read_json(root / "public/observer_state/day9/prewarm_end_of_day/Manufacturer.json")["observation"]
        assert initial["hr"]["department_staffing"]['Production']["count"] == 0
        assert {i["item_id"]: i["quantity"] for i in initial["inventory"]["inventory_items"]} == {
            "PRODUCT_1": 741, "MATERIAL_1": 33900}
        assert not initial["production"]["production_plans"].get("in_progress")
        assert result["summary"]["first_new_output_round"] >= 12
    exported = export_runs([root], tmp_path / "export")
    assert exported["runs"] == 1
    run = read_json(tmp_path / "export/runs.json")[0]
    assert run["arm"] == arm and run["mechanism_data_status"] == "complete"
    if family == "fd4":
        assert run["fd_cohort_quantity"] == 3360
        assert run["fd4_cohort_fill"] == result["summary"]["cohort_fill"]
        assert (tmp_path / f"export/{result['run_id']}_actions.json").is_file()
        assert (tmp_path / f"export/{result['run_id']}_repairs.json").is_file()


def test_incomplete_and_unknown_family_never_become_zero_metrics(tmp_path):
    assert mechanism_evidence(tmp_path, "fd1")["status"] == "incomplete"
    assert mechanism_evidence(tmp_path, "fd_typo")["status"] == "unsupported"


@pytest.mark.parametrize("arm", ["ma", "sa"])
def test_business_repair_lineage_and_safe_withdrawal(tmp_path, monkeypatch, model_fixture, arm):
    model_fixture[1].update(fd_reject_once=True)
    root = run_real_manager(tmp_path, monkeypatch, f"firm_ablation_fd1_{arm}", model_fixture)
    result = evaluate(root)
    sales = [a for a in result["actions"] if a["department"] == "sales" and a["round_id"] == 10]
    assert sales[0]["status"] in {"rejected", "normalized_noop"}
    assert sales[1]["parent_action_ids"] == [sales[0]["action_id"]]
    assert sales[1]["proposed"]["action_name"] == "action_pass"
    assert not any(a["parent_action_ids"] for a in result["actions"] if a["department"] != "sales")


def test_prepare_batch_is_frozen_and_does_not_launch_models():
    from scripts.prepare_fd_ablation_batch import prepare
    d = prepare("test-batch", 5, "expected-server-model")
    assert d["planned_runs"] == 100 and d["simulation_runs_started"] == 0
    assert len(d["configurations"]) == 20
    for request in d["requests"]:
        c = get_scenario_config(request["scenario_id"])
        c["experiment_design"].update(request["config_overrides"]["experiment_design"])
        validate_scenario(c, request["planned_total_steps"])


def test_safe_withdrawal_is_not_completed_business_goal():
    from core.fd_ablation_metrics import repair_records
    records = repair_records([
        {"action_id": "failed", "proposed": {"action_name": "accept_order"}, "status": "rejected", "source": "receipt"},
        {"action_id": "repair", "parent_action_ids": ["failed"], "proposed": {"action_name": "action_pass"},
         "status": "success", "source": "receipt"}])
    assert len(records) == 1
    assert records[0]["outcome"] == "safe_withdrawal"


@pytest.mark.parametrize("family", SOURCES)
@pytest.mark.parametrize("arm", ["ma", "na", "nc", "sa"])
def test_page_launch_payload_passes_actual_creation_facade(family, arm):
    from types import SimpleNamespace
    from runtime.operations.facade import OperationsFacade
    from config.integration_profiles import get_scenario_integration_profiles
    facade = OperationsFacade.__new__(OperationsFacade)
    facade.scenario_loader = get_scenario_config
    facade.profile_loader = get_scenario_integration_profiles
    facade.service = SimpleNamespace(create_experiment=lambda **values: values)
    sid = f"firm_ablation_{family}_{arm}"
    policy = policy_from_config(get_scenario_config(sid))
    captured = facade.create_experiment_from_scenario(scenario_id=sid, planned_total_steps=20,
        config_overrides={"simulation": {"agent_run_steps": 20, "service_total_steps": 20},
                          "runtime_injection": {"firm_architecture_experiment": policy},
                          "experiment_design": {"batch_id": "page-check", "repeat_id": 2}})
    assert captured["scenario_config"]["experiment_design"]["repeat_id"] == 2
    assert "fd-arm:" + arm in captured["tags"]
