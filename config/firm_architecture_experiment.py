"""Opt-in, separately named firm-architecture experiments; never edit source presets."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from core.firm_runtime_support import EXECUTION_ADAPTER_VERSION
from config.fd_ablation_experiment import VERSION as FD_ABLATION_VERSION, SOURCES as FD_SOURCES

LEGACY_VERSION = "firm_architecture_2026_10_19_v1"
VERSION = "firm_architecture_2026_10_19_v2"
FD4_MA_VERSION = "firm_architecture_2026_10_20_v3"
POLICY_KEY = "firm_architecture_experiment"
BASELINES = Path(__file__).with_name("firm_ablation_baselines")
C3_SOURCES = {"bullwhip": "beer_game_profit_objective",
              "herding_visible": "herding_peer_visible_profit_objective",
              "herding_hidden": "herding_no_peer_visibility_profit_objective"}
SCENARIOS = {}
for family, source in (
    ("bullwhip", "beer_game"),
    ("herding_visible", "herding_baseline_peer_visible"),
    ("herding_hidden", "herding_no_peer_visibility"),
):
    for architecture, suffix in (("organizational", "ma"), ("centralized", "sa")):
        SCENARIOS[f"firm_ablation_{family}_{suffix}"] = {
            "source": C3_SOURCES.get(family, source) if suffix == "sa" else source,
            "family": family, "architecture": architecture,
        }

for family, source in FD_SOURCES.items():
    for arm in ("ma", "na", "nc", "sa"):
        SCENARIOS[f"firm_ablation_{family}_{arm}"] = {
            "source": source, "family": family, "arm": arm,
            "architecture": "centralized" if arm == "sa" else "organizational",
        }


def load_baseline(family):
    """Load portable, hashed copies; never depend on or mutate live run archives."""
    manifest = json.loads((BASELINES / "manifest.json").read_text(encoding="utf-8"))
    info = manifest["baselines"][family]
    blob = (BASELINES / info["snapshot_file"]).read_bytes()
    if hashlib.sha256(blob).hexdigest() != info["snapshot_sha256"]:
        raise ValueError(f"C3 baseline snapshot hash mismatch: {family}")
    config = json.loads(blob)
    if config["experiment_design"]["experiment_group"] != "C3":
        raise ValueError("SA economic baseline must be a C3 snapshot")
    if info["source_scenario_id"] != C3_SOURCES[family] or config["meta"]["scenario_id"] != info["source_scenario_id"]:
        raise ValueError("C3 baseline scenario identity mismatch")
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    if hashlib.sha256(canonical).hexdigest() != info["config_sha256"]:
        raise ValueError("C3 baseline configuration hash mismatch")
    return config, deepcopy(info)


def economic_contract(config):
    """Exclude experiment identity and run length, retain economic and execution settings."""
    result = {k: deepcopy(v) for k, v in config.items() if k not in {"meta", "experiment_design", "formal_experiment_config"}}
    result.get("runtime_injection", {}).pop(POLICY_KEY, None)
    for key in ("agent_run_steps", "service_total_steps"):
        result.get("simulation", {}).pop(key, None)
    return result


def fd4_reference(config):
    from config.fd_experiment import resolve_fd_experiment
    config = deepcopy(config)
    config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"] = {"condition": "full"}
    return resolve_fd_experiment(config, "single_case_04_staff_shortage", 20)


def build_scenario(scenario_id, source_config):
    spec = SCENARIOS[scenario_id]
    if spec["family"] in FD_SOURCES:
        from config.fd_ablation_experiment import build
        return build(scenario_id, source_config, spec)
    config = deepcopy(source_config)
    central = spec["architecture"] == "centralized"
    paired_ma = not central and spec["family"] == "fd4"
    c3 = central and spec["family"] in C3_SOURCES
    baseline = None
    if c3:
        config, baseline = load_baseline(spec["family"])
    if spec["family"] == "fd4":
        config = fd4_reference(config)
    runtime = config["runtime_injection"]
    # Retain the optional economic MA development protocol; FD4 MA is paired with SA.
    if not central and not paired_ma:
        for key in (
            "bullwhip_midstream_pass_through_mode", "bullwhip_proposal_conversion_acceleration",
            "bullwhip_manufacturer_upstream_amplification", "bullwhip_supplier_upstream_pull_through_mode",
        ):
            if isinstance(runtime.get(key), dict):
                runtime[key]["enabled"] = False
    version = FD4_MA_VERSION if paired_ma else VERSION if central else LEGACY_VERSION
    runtime[POLICY_KEY] = {
        "enabled": True, "version": version, **spec,
        "execution_adapter_version": EXECUTION_ADAPTER_VERSION,
        "max_attempts": 3, "attempt_timeout_seconds": 300,
        "failure_policy": "record_and_continue",
        "dispatch_protocol": "enterprise_inline_v2" if central or paired_ma else "world_phase_barrier_v1",
        **({"baseline_group": "C3", "baseline": baseline} if c3 else {"baseline_group": "S0" if spec["family"] == "fd4" else "C2_modified"}),
    }
    if not central and not paired_ma:
        config["simulation"]["max_department_concurrency"] = 1
    names = {"fd4": 'FD4 Staffing and fulfillment recovery', "bullwhip": 'Bullwhip Effect',
             "herding_visible": 'Herding / peer visible', "herding_hidden": 'Herding / peer hidden'}
    label = 'MA paired architecture control' if paired_ma else 'MA organizational enterprise' if not central else 'SA single enterprise Agent'
    config["meta"].update(scenario_id=scenario_id, name=f"Architecture ablation / {names[spec['family']]} / {label}",
                          summary=('A single enterprise decision maker inherits the archived C3 operating regime.' if c3 else
                                   'Retry with failure feedback; record unresolved failures and continue simulation.'))
    config["experiment_design"] = {
        **config.get("experiment_design", {}), "experiment_family": "firm_architecture_ablation",
        "experiment_group": "MA" if spec["architecture"] == "organizational" else "SA",
        "decision_regime": config.get("experiment_design", {}).get("decision_regime") if central or paired_ma else "organizational",
        "enterprise_architecture": spec["architecture"], "architecture_protocol": version,
        "source_scenario_id": spec["source"], "evidence_status": "ready_to_run",
        "baseline_group": runtime[POLICY_KEY]["baseline_group"],
        "baseline_run_id": baseline["source_run_id"] if baseline else None,
        "baseline_config_sha256": baseline["config_sha256"] if baseline else None,
        "comparison_target": "firm_ablation_fd4_sa" if paired_ma else spec["source"] if central else "legacy_protocol_development_only",
    }
    # Communication here is an explicit model input/output, not an extra model or auto-repair.
    if not central and not paired_ma:
        coord = config.setdefault("integration_profiles", {}).setdefault("capabilities", {}).setdefault("coordination", {})
        coord.update(communication_validation="off", semantic_auto_repair=False, semantic_rule_pack="none")
    return config


def policy_from_config(config):
    policy = ((config or {}).get("runtime_injection") or {}).get(POLICY_KEY) or {}
    return policy if policy.get("enabled") and policy.get("version") in {VERSION, LEGACY_VERSION, FD4_MA_VERSION, FD_ABLATION_VERSION} else {}


def validate_scenario(config, planned_total_steps=None):
    policy = policy_from_config(config)
    scenario_id = (config.get("meta") or {}).get("scenario_id")
    if scenario_id in SCENARIOS and SCENARIOS[scenario_id]["family"] in FD_SOURCES:
        from config.fd_ablation_experiment import validate
        return validate(config, planned_total_steps)
    if not policy:
        if scenario_id in SCENARIOS:
            raise ValueError("Dedicated architecture scenarios require their versioned architecture policy")
        return config
    if scenario_id not in SCENARIOS:
        raise ValueError("Firm architecture experiments require a dedicated firm_ablation scenario")
    expected = SCENARIOS[scenario_id]
    paired_ma = expected["family"] == "fd4" and expected["architecture"] == "organizational"
    expected_version = FD4_MA_VERSION if paired_ma else VERSION if expected["architecture"] == "centralized" else LEGACY_VERSION
    if policy.get("version") != expected_version:
        raise ValueError("Legacy architecture snapshot cannot create a new matched job; reload the current scenario")
    if any(policy.get(key) != expected[key] for key in expected):
        raise ValueError("Architecture/source/family must match the selected experiment")
    if (config["runtime_injection"].get("scripted_rule_policy") or {}).get("enabled"):
        raise ValueError("Architecture comparison requires Agent decisions; choose a legacy scenario for Scripted")
    fd = ((config["runtime_injection"].get("single_enterprise_case_policy") or {}).get("fd_experiment") or {})
    if expected["family"] == "fd4" and fd.get("condition") != "full":
        raise ValueError("FD4 architecture comparison cannot be combined with NA/NC/RB")
    if expected["family"] == "fd4" and planned_total_steps not in (None, 20):
        raise ValueError("FD4 architecture experiments require the original 20-round handover protocol")
    if expected["family"] == "fd4":
        case = config["runtime_injection"]["single_enterprise_case_policy"]
        if any(config["simulation"].get(key) != 20 for key in ("agent_run_steps", "service_total_steps")):
            raise ValueError("FD4 architecture experiments require 20 configured rounds")
        if case.get("prewarm_rounds") != 10 or case.get("handoff_day") != 10:
            raise ValueError("FD4 construction must remain R0-R9 with handover at R10")
        from config.simulation_preset_config import get_scenario_config
        reference = fd4_reference(get_scenario_config(expected["source"]))
        if economic_contract(config) != economic_contract(reference):
            raise ValueError("FD4 MA/SA must retain the same full-condition operating regime; economic/history/concurrency overrides are not allowed")
        design = config.get("experiment_design") or {}
        required = {"experiment_group": "MA" if paired_ma else "SA", "baseline_group": "S0",
                    "enterprise_architecture": expected["architecture"], "architecture_protocol": expected_version,
                    "source_scenario_id": expected["source"],
                    "decision_regime": reference["experiment_design"]["decision_regime"],
                    "comparison_target": "firm_ablation_fd4_sa" if paired_ma else expected["source"]}
        if policy.get("baseline_group") != "S0" or any(design.get(k) != v for k, v in required.items()):
            raise ValueError("FD4 matched architecture metadata must remain correctly labeled")
    attempts = policy.get("max_attempts")
    timeout = policy.get("attempt_timeout_seconds")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 10:
        raise ValueError("max_attempts must be an integer between 1 and 10")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 3600:
        raise ValueError("attempt_timeout_seconds must be between 1 and 3600")
    protocol = "enterprise_inline_v2" if expected["architecture"] == "centralized" or paired_ma else "world_phase_barrier_v1"
    if policy.get("failure_policy") != "record_and_continue" or policy.get("dispatch_protocol") != protocol:
        raise ValueError("Unsupported architecture failure/dispatch protocol")
    if expected["architecture"] == "centralized" and expected["family"] in C3_SOURCES:
        baseline, provenance = load_baseline(expected["family"])
        if policy.get("baseline_group") != "C3" or policy.get("baseline") != provenance:
            raise ValueError("C3 baseline provenance must match the frozen snapshot")
        if economic_contract(config) != economic_contract(baseline):
            raise ValueError("SA inherits the frozen C3 operating regime; economic/controller/history/concurrency overrides are not allowed")
        design = config.get("experiment_design") or {}
        required = {"experiment_group": "SA", "enterprise_architecture": "centralized", "baseline_group": "C3",
                    "architecture_protocol": VERSION, "source_scenario_id": expected["source"],
                    "baseline_run_id": provenance["source_run_id"], "baseline_config_sha256": provenance["config_sha256"],
                    **{k: baseline["experiment_design"][k] for k in ("decision_regime", "agent_objective_profile", "constraint_level")}}
        if any(design.get(k) != value for k, value in required.items()):
            raise ValueError("SA architecture identity and C3 operating regime metadata must remain separate and correctly labeled")
    return config
