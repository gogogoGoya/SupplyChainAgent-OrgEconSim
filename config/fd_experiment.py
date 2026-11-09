"""Opt-in FD follow-up contracts; historical and multi-firm presets stay intact."""

from copy import deepcopy
import hashlib
import json

VERSION = "fd_followup_2026_10_09_v1"
FD2_LEGACY_AGENT_PROMPT_VERSION = "fd2_delivery_coverage_2026_10_12_v1"
FD2_PARTIAL_DELIVERY_PROMPT_VERSION = "fd2_partial_delivery_2026_10_12_v2"
FD2_AGENT_PROMPT_VERSION = "fd2_autonomous_delivery_2026_10_13_v3"
FD2_SUPPORTED_PROMPT_VERSIONS = frozenset({FD2_LEGACY_AGENT_PROMPT_VERSION,
    FD2_PARTIAL_DELIVERY_PROMPT_VERSION, FD2_AGENT_PROMPT_VERSION})
FD2_EXECUTION_POLICY_VERSION = "fd2_physical_execution_2026_10_13_v1"
FD2_RB_RULE_VERSION = "fd2_demand_response_2026_10_12_v1"
FD4_LEGACY_AGENT_PROMPT_VERSION = "fd4_evidence_balance_2026_10_11_v1"
FD4_NET_NEED_PROMPT_VERSION = "fd4_net_need_2026_10_11_v2"
FD4_EVIDENCE_PROMPT_VERSION = "fd4_evidence_semantics_2026_10_11_v3"
FD4_CONSISTENCY_PROMPT_VERSION = "fd4_decision_consistency_2026_10_11_v4"
FD4_REFERENCE_PROMPT_VERSION = "fd4_reference_contract_2026_10_12_v5"
FD4_COMPLETION_PROMPT_VERSION = "fd4_completion_guard_2026_10_12_v6"
FD4_ROLE_PROMPT_VERSION = "fd4_role_contract_2026_10_12_v7"
FD4_WORKLOAD_PROMPT_VERSION = "fd4_workload_reference_2026_10_12_v8"
FD4_AGENT_PROMPT_VERSION = "fd4_autonomous_forecast_2026_10_12_v9"
FD4_WORKLOAD_REFERENCE_VERSIONS = frozenset({FD4_WORKLOAD_PROMPT_VERSION, FD4_AGENT_PROMPT_VERSION})
FD4_ROLE_CONTRACT_VERSIONS = FD4_WORKLOAD_REFERENCE_VERSIONS | {FD4_ROLE_PROMPT_VERSION}
FD4_COMPLETION_VERSIONS = FD4_ROLE_CONTRACT_VERSIONS | {FD4_COMPLETION_PROMPT_VERSION}
FD4_REFERENCE_VERSIONS = FD4_COMPLETION_VERSIONS | {FD4_REFERENCE_PROMPT_VERSION}
FD4_DECISION_CHECK_VERSIONS = FD4_REFERENCE_VERSIONS | {FD4_CONSISTENCY_PROMPT_VERSION}
FD4_EVIDENCE_VERSIONS = FD4_DECISION_CHECK_VERSIONS | {FD4_EVIDENCE_PROMPT_VERSION}
FD4_NET_NEED_VERSIONS = FD4_EVIDENCE_VERSIONS | {FD4_NET_NEED_PROMPT_VERSION}
FD4_SUPPORTED_PROMPT_VERSIONS = FD4_NET_NEED_VERSIONS | {FD4_LEGACY_AGENT_PROMPT_VERSION}
FD4_SIMPLE_RULE_VERSION = "fd4_simple_rules_v1"
FD2 = "single_case_02_material_shortage"
FD4 = "single_case_04_staff_shortage"


def fd2_agent_policy(policy):
    return bool(policy.get("scenario") == "FD2" and policy.get("condition") in {"full", "no_analyst", "no_shared_coordination"}
                and policy.get("agent_prompt_policy_version") in FD2_SUPPORTED_PROMPT_VERSIONS)


def fd2_physical_execution_policy(policy):
    return bool(policy.get("scenario") == "FD2"
                and policy.get("condition") in {"full", "rb_reference", "no_analyst", "no_shared_coordination"}
                and policy.get("execution_policy_version") == FD2_EXECUTION_POLICY_VERSION)


def fd2_fact_audit_policy(policy):
    return bool(fd2_agent_policy(policy)
                and policy.get("agent_prompt_policy_version") == FD2_AGENT_PROMPT_VERSION
                and policy.get("fact_audit_mode") == "nonblocking")


def fd_completion_enabled(policy, role, round_id):
    """Completion checks concern real files/reads, not business recommendations."""
    role = str(role).lower()
    if fd2_agent_policy(policy):
        return (int(policy.get("formal_start", 10)) <= round_id <= int(policy.get("formal_end", 19))
                and role in {"analyst", "hr", "production", "sales", "procurement"})
    version = policy.get("agent_prompt_policy_version")
    if role == "analyst":
        return version in FD4_COMPLETION_VERSIONS
    return (role in {"hr", "production"} and version in FD4_ROLE_CONTRACT_VERSIONS
            and round_id <= int(policy.get("formal_end", 19)))


def experiment_options(scenario_id, scripted=False):
    base = str(scenario_id).removesuffix("_scripted")
    if base == FD2:
        choices = [("rb_reference", 'FD2 / RB reference (shared execution rules)')] if scripted else [
            ("full", 'FD2 / AG shortage response')
        ]
    elif base == FD4:
        choices = [("rb_revised", "FD4 / RB"), ("rb_simple", "FD4 / RB-Simple")] if scripted else [
            ("full", "FD4 / Full-AG"),
            ("no_analyst", "FD4 / w/o Analyst"),
            ("no_shared_coordination", "FD4 / w/o Shared Coordination"),
        ]
    else:
        return []
    options = [{"value": value, "label": label} for value, label in choices]
    if base == FD4:
        return options
    return options + [
        {"value": "legacy", "label": 'Original configuration (no matched ablation)'}
    ]


def resolve_fd_experiment(config, scenario_id, planned_total_steps=None):
    """Validate the requested arm and freeze its common execution settings."""
    config = deepcopy(config)
    runtime = config.get("runtime_injection") or {}
    case = runtime.get("single_enterprise_case_policy") or {}
    request = case.get("fd_experiment") or {}
    condition = request.get("condition", "legacy")
    if condition == "legacy":
        return config
    base = str(scenario_id).removesuffix("_scripted")
    scripted = bool((runtime.get("scripted_rule_policy") or {}).get("enabled"))
    if scripted != str(scenario_id).endswith("_scripted"):
        raise ValueError("FD scenario and Scripted controller must agree")
    allowed = {row["value"] for row in experiment_options(base, scripted)}
    profile = (config.get("integration_profiles") or {}).get("simulation") or {}
    if base not in {FD2, FD4} or profile.get("profile") != "single_enterprise":
        raise ValueError("FD follow-up is restricted to single-enterprise FD2/FD4")
    if condition not in allowed or not case.get("enabled") or case.get("case_id") != base:
        raise ValueError("FD condition does not match the scenario/controller")
    simulation = config["simulation"]
    if int(case.get("prewarm_rounds", -1)) != 10 or int(case.get("handoff_day", -1)) != 10:
        raise ValueError("FD follow-up requires the unchanged R0-R9 construction")
    total_steps = simulation.get("agent_run_steps", 0) if planned_total_steps is None else planned_total_steps
    if int(total_steps) != 20:
        raise ValueError("FD follow-up requires 20 total rounds (R0-R19)")
    if base == FD4:
        from config.fd4_validation_design import fd4_instance, FD4_INSTANCE_SEQUENCE
        sequence = request.get("instance_sequence", FD4_INSTANCE_SEQUENCE)
        config = fd4_instance(config, sequence)
        runtime = config["runtime_injection"]
        case = runtime["single_enterprise_case_policy"]
        simulation = config["simulation"]
    simulation.update(agent_run_steps=20, service_total_steps=20,
                      max_enterprise_concurrency=1, max_department_concurrency=1)
    case["fd_experiment"] = {
        "version": VERSION, "condition": condition,
        "scenario": "FD2" if base == FD2 else "FD4",
        "formal_start": 10, "formal_end": 19,
        "fresh_analysis": condition != "no_analyst",
        "semantic_rescue": False,
        "fixed_department_opportunities": True,
        "comparison_scope": "complete_controller" if base == FD2 else "diagnosis_and_shared_context",
    }
    if base == FD4 and not scripted:
        case["fd_experiment"]["agent_prompt_policy_version"] = FD4_AGENT_PROMPT_VERSION
        case["fd_experiment"].update(
            decision_consistency_mode="audit_only", analyst_role="advisory",
            forward_evidence_horizon=3,
        )
    if base == FD2 and not scripted:
        case["fd_experiment"].update(
            agent_prompt_policy_version=FD2_AGENT_PROMPT_VERSION,
            analyst_role="advisory", decision_consistency_mode="off",
            forward_evidence_horizon=3,
            fact_audit_mode="nonblocking",
        )
    if base == FD2:
        case["fd_experiment"]["execution_policy_version"] = FD2_EXECUTION_POLICY_VERSION
    if base == FD2 and scripted:
        case["fd_experiment"]["rule_policy_version"] = FD2_RB_RULE_VERSION
    if base == FD4:
        case["fd_experiment"]["instance_sequence"] = sequence
    if condition == "rb_simple":
        case["fd_experiment"]["rule_policy_version"] = FD4_SIMPLE_RULE_VERSION
        runtime["scripted_rule_policy"]["fd_simple_rules"] = {
            "production_batch": 250, "daily_capacity": 250,
            "finished_goods_threshold": 250, "recruit_people": 2,
        }
    # The duplicate case metadata is used by the orchestrator and the UI.
    if isinstance(config.get("single_enterprise_case"), dict):
        config["single_enterprise_case"]["fd_experiment"] = deepcopy(case["fd_experiment"])
    coordination = config.setdefault("integration_profiles", {}).setdefault(
        "capabilities", {}
    ).setdefault("coordination", {})
    coordination.update(two_phase_enabled=False, semantic_auto_repair=False)
    if condition == "no_shared_coordination":
        coordination.update(blackboard_enabled=False, communication_validation="off",
                            semantic_rule_pack="none")
    else:
        coordination.update(blackboard_enabled=True, communication_validation="warn")
    return config


def policy_from_config(config, round_id=None):
    runtime = (config or {}).get("runtime_injection") or {}
    case = runtime.get("single_enterprise_case_policy") or {}
    policy = case.get("fd_experiment") or {}
    if not case.get("enabled") or policy.get("version") != VERSION:
        return {}
    if case.get("case_id") not in {FD2, FD4, "single_case_01_order_selection", "single_case_03_capacity_bottleneck", "single_case_05_cash_pressure"}:
        return {}
    if round_id is not None and int(round_id) < int(policy.get("formal_start", 10)):
        return {}
    return policy


def policy_from_meta(meta, round_id=None):
    return policy_from_config((meta or {}).get("scenario_config") or {}, round_id)


def config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
