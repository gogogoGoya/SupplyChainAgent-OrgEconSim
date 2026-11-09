import pytest

from config.fd_experiment import FD2, FD4, experiment_options, resolve_fd_experiment
from config.fd4_validation_design import fd4_instance
from config.simulation_preset_config import get_scenario_config
from runtime.operations.facade import OperationsFacade
from runtime.operations.http_router import _scenario_payload
from agent.scripted_rule_runner import ScriptedRuleRunner


def resolved(condition, sequence=2):
    scenario = FD4 + "_scripted" if condition in {"rb_revised", "rb_simple"} else FD4
    config = get_scenario_config(scenario)
    config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"] = {
        "condition": condition, "instance_sequence": sequence}
    return resolve_fd_experiment(config, scenario, 20)


def test_all_arms_use_same_numbered_construction_without_mutating_preset():
    original = get_scenario_config(FD4)
    plans = []
    for condition in ["rb_revised", "rb_simple", "full", "no_analyst", "no_shared_coordination"]:
        config = resolved(condition)
        assert config["fd_validation_instance"]["instance_sequence"] == 2
        case = config["runtime_injection"]["single_enterprise_case_policy"]
        plans.append(case["prewarm_execution_plan"])
        assert case["target_handoff_signature"]["organization"]["production_headcount_range"] == [0, 0]
    assert all(plan == plans[0] for plan in plans)
    assert get_scenario_config(FD4) == original
    assert resolved("full", 1)["runtime_injection"]["single_enterprise_case_policy"]["prewarm_execution_plan"] == original["runtime_injection"]["single_enterprise_case_policy"]["prewarm_execution_plan"]
    with pytest.raises(ValueError):
        fd4_instance(resolved("full"), 1)


def test_catalog_shows_current_instance_and_independent_rb_simple():
    for scenario in [FD4, FD4 + "_scripted"]:
        row = _scenario_payload([{"scenario_id": scenario, "name": "Historical display"}])[0]
        assert 'Staffing and fulfillment recovery' in row["label"]
        assert 'No production workers' in row["description"]
        assert "legacy" not in [option["value"] for option in row["config_defaults"]["fd_experiment_options"]]
    assert [row["value"] for row in experiment_options(FD4, True)] == ["rb_revised", "rb_simple"]
    assert "legacy" in [row["value"] for row in experiment_options(FD2)]


@pytest.mark.parametrize("condition", ["rb_revised", "rb_simple", "full"])
def test_operations_freezes_current_instance_and_condition(condition):
    class Repository:
        def save_version(self, version):
            self.version = version
        def create_job(self, job):
            self.job = job
    repository = Repository()
    scenario = FD4 if condition == "full" else FD4 + "_scripted"
    OperationsFacade(repository).create_experiment_from_scenario(
        scenario_id=scenario, planned_total_steps=20,
        config_overrides={"runtime_injection": {"single_enterprise_case_policy": {
            "fd_experiment": {"condition": condition}}}})
    config = repository.version["scenario_config"]
    assert config["fd_validation_instance"]["instance_sequence"] == 2
    assert config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"]["condition"] == condition
    assert "fd-condition:" + condition in repository.job["tags"]
    assert "fd-instance:02" in repository.job["tags"]


def rule_runner(condition):
    config = resolved(condition)
    runner = ScriptedRuleRunner.__new__(ScriptedRuleRunner)
    runner.enterprise_name = "Manufacturer"
    runner._run_meta = lambda: {"scenario_config": config}
    runner._policy = lambda: config["runtime_injection"]["scripted_rule_policy"]
    runner._self_state = lambda *a: {"production_plans": {}}
    runner._confirmed_demand_quantity = lambda day: 700
    runner._finished_goods_quantity = lambda day: 0
    runner._production_capacity_status = lambda day: (1000, 1000)
    runner._department_staff_status = lambda *a: {"seen": True, "count": 0, "pending": 0}
    return runner


def test_simple_fixed_batch_stops_for_stock_or_open_plan_and_does_not_change_rb():
    runner = rule_runner("rb_simple")
    assert runner._scripted_production_quantity(10, {}) == 250
    assert runner._fd_simple_rules(10)["daily_capacity"] == 250
    runner._finished_goods_quantity = lambda day: 250
    assert runner._scripted_production_quantity(10, {}) == 0
    runner._finished_goods_quantity = lambda day: 0
    runner._self_state = lambda *a: {"production_plans": {"in_progress": [{"status": "in_progress"}]}}
    assert runner._scripted_production_quantity(10, {}) == 0
    revised = rule_runner("rb_revised")
    revised._fd_revised_production_quantity = lambda day: 700
    assert revised._scripted_production_quantity(10, {}) == 700
    assert not revised._fd_simple_rules(10)
    assert not runner._fd_simple_rules(9)


def test_simple_hires_fixed_production_batch_but_not_pending_or_other_roles():
    runner = rule_runner("rb_simple")
    action = runner._hr_action(10)[0]["action"]
    assert action["action_param"] == {"department": "PRODUCTION", "num_people": 2}
    runner._department_staff_status = lambda *a: {"seen": True, "count": 0, "pending": 2}
    assert runner._hr_action(10)[0]["action"]["action_name"] == "action_pass"
    runner._department_staff_status = lambda *a: {"seen": True, "count": 2, "pending": 0}
    assert runner._hr_action(10)[0]["action"]["action_name"] == "action_pass"
