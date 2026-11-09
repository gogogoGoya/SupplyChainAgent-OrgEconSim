"""Matched FD1–FD5 follow-ups, isolated from historical experiment presets."""

from copy import deepcopy
import hashlib
import json

VERSION = "fd_ablation_2026_10_26_v1"
SOURCES = {
    "fd1": "single_case_01_order_selection",
    "fd2": "single_case_02_material_shortage",
    "fd3": "single_case_03_capacity_bottleneck",
    "fd4": "single_case_04_staff_shortage",
    "fd5": "single_case_05_cash_pressure",
}
LABELS = {"ma": 'Full / MA organizational enterprise', "na": 'NA without enterprise diagnosis',
          "nc": 'NC without shared coordination', "sa": 'SA single enterprise Agent'}
CONDITIONS = {"ma": "full", "na": "no_analyst", "nc": "no_shared_coordination", "sa": "full"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def build(scenario_id, source, spec):
    from config.fd_experiment import VERSION as FD_VERSION, resolve_fd_experiment
    config = deepcopy(source)
    family, arm = spec["family"], spec["arm"]
    if family in {"fd2", "fd4"}:
        config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"] = {"condition": "full"}
        config = resolve_fd_experiment(config, SOURCES[family], 20)
    runtime = config["runtime_injection"]
    case = runtime["single_enterprise_case_policy"]
    config["simulation"].update(agent_run_steps=20, service_total_steps=20,
                                max_enterprise_concurrency=1, max_department_concurrency=1)
    config["integration_profiles"]["capabilities"]["analysis"]["history_days"] = 5
    fd = case.setdefault("fd_experiment", {})
    fd.update(version=FD_VERSION, condition=CONDITIONS[arm], scenario=family.upper(),
              formal_start=10, formal_end=19, fresh_analysis=arm != "na", semantic_rescue=False,
              fixed_department_opportunities=True, comparison_scope="matched_component_and_architecture",
              analyst_role="advisory", forward_evidence_horizon=3,
              decision_consistency_mode="audit_only")
    if family != "fd2":
        fd["agent_prompt_policy_version"] = VERSION
    if isinstance(config.get("single_enterprise_case"), dict):
        config["single_enterprise_case"]["fd_experiment"] = deepcopy(fd)
    coord = config["integration_profiles"]["capabilities"].setdefault("coordination", {})
    coord.update(blackboard_enabled=arm != "nc", communication_validation="off" if arm == "nc" else "warn",
                 semantic_rule_pack="none", semantic_auto_repair=False, two_phase_enabled=False)
    policy = {"enabled": True, "version": VERSION, **spec,
              "baseline_group": "S0", "analyst_enabled": arm != "na" and arm != "sa",
              "shared_coordination_enabled": arm != "nc", "max_attempts": 3,
              "attempt_timeout_seconds": 300, "failure_policy": "record_and_continue",
              "dispatch_protocol": "enterprise_inline_v2"}
    from core.firm_runtime_support import EXECUTION_ADAPTER_VERSION
    policy["execution_adapter_version"] = EXECUTION_ADAPTER_VERSION
    runtime["firm_architecture_experiment"] = policy
    config["meta"].update(scenario_id=scenario_id, name=f"{family.upper()} / {LABELS[arm]}",
                          summary='Compare diagnosis, shared coordination and functional organization under a common handover state and execution rules.')
    config["experiment_design"].update(
        experiment_family="fd_matched_ablation", experiment_group=arm.upper(),
        enterprise_architecture=spec["architecture"], architecture_protocol=VERSION,
        source_scenario_id=spec["source"], baseline_group="S0", arm=arm,
        comparison_target=f"firm_ablation_{family}_ma", evidence_status="ready_to_run")
    # MA is the established display identity for the full organizational arm.
    config["experiment_design"]["experiment_group"] = "MA" if arm == "ma" else arm.upper()
    config["formal_experiment_config"] = {
        "total_steps": 20, "prewarm_rounds": 10, "analysis_window": {"start": 10, "end": 19},
        "primary_evaluation_window": {"start_round": 10, "end_round_inclusive": 19},
        "experiment_role": "component_ablation",
        "ready_for_formal_run": True, "protocol_version": VERSION,
    }
    # Hash economic settings separately from the explicit information intervention.
    economic = deepcopy(config)
    for key in ("meta", "experiment_design", "formal_experiment_config"):
        economic.pop(key, None)
    economic["runtime_injection"].pop("firm_architecture_experiment")
    for container in [economic["runtime_injection"]["single_enterprise_case_policy"], economic.get("single_enterprise_case", {})]:
        p = container.get("fd_experiment", {})
        for key in ("condition", "fresh_analysis"):
            p.pop(key, None)
    for key in ("blackboard_enabled", "communication_validation"):
        economic["integration_profiles"]["capabilities"]["coordination"].pop(key, None)
    policy["economic_contract_sha256"] = digest(economic)
    policy["information_contract_sha256"] = digest({"arm": arm, "analyst": policy["analyst_enabled"],
        "shared": policy["shared_coordination_enabled"], "history_days": 5, "forward_horizon": 3})
    return config


def validate(config, steps=None):
    from config.firm_architecture_experiment import SCENARIOS, economic_contract
    from config.simulation_preset_config import get_scenario_config
    scenario_id = config.get("meta", {}).get("scenario_id")
    spec = SCENARIOS[scenario_id]
    expected = build(scenario_id, get_scenario_config(spec["source"]), spec)
    if steps not in (None, 20) or economic_contract(config) != economic_contract(expected):
        raise ValueError("Matched FD ablations require frozen economic/history/execution settings and 20 rounds")
    if config["runtime_injection"].get("firm_architecture_experiment") != expected["runtime_injection"]["firm_architecture_experiment"]:
        raise ValueError("Matched FD arm, information contract and retry policy must match the selected scenario")
    design = config.get("experiment_design", {})
    if "repeat_id" in design and (type(design["repeat_id"]) is not int or design["repeat_id"] < 1):
        raise ValueError("repeat_id must be a positive integer")
    if "batch_id" in design and (not isinstance(design["batch_id"], str) or not design["batch_id"].strip()):
        raise ValueError("batch_id must be a nonempty string")
    for key, value in expected["experiment_design"].items():
        if design.get(key) != value:
            raise ValueError(f"Matched FD experiment metadata mismatch: {key}")
    if config.get("formal_experiment_config") != expected["formal_experiment_config"]:
        raise ValueError("Matched FD formal window must remain R10-R19")
    return config
