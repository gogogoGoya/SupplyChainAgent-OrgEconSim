#!/usr/bin/env python3
"""Prepare a frozen FD rerun manifest and API requests; never start simulations."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.fd_ablation_experiment import SOURCES, VERSION, digest
from config.simulation_preset_config import get_scenario_config
from config.firm_architecture_experiment import validate_scenario


def prepare(batch_id, repeats, model_label, families=None):
    if not batch_id.strip() or repeats < 1 or repeats > 100 or not model_label.strip():
        raise ValueError("A batch ID, expected model label and 1-100 repeats are required")
    configurations, requests = {}, []
    for family in families or ["fd2", "fd1", "fd3", "fd4", "fd5"]:
        if family not in SOURCES:
            raise ValueError(f"Unsupported FD family: {family}")
        for arm in ("ma", "na", "nc", "sa"):
            scenario_id = f"firm_ablation_{family}_{arm}"
            config = get_scenario_config(scenario_id)
            validate_scenario(config, 20)
            configurations[scenario_id] = {"sha256": digest(config), "config": config}
        for repeat in range(1, repeats + 1):
            arms = ["ma", "na", "nc", "sa"]
            arms = arms[(repeat-1) % 4:] + arms[:(repeat-1) % 4]
            for arm in arms:
                requests.append({"scenario_id": f"firm_ablation_{family}_{arm}", "planned_total_steps": 20,
                    "tags": [f"fd-batch:{batch_id}", f"fd-repeat:{repeat}"],
                    "config_overrides": {"experiment_design": {"batch_id": batch_id,
                        "repeat_id": repeat, "expected_model_label": model_label}}})
    paths = [ROOT / p for p in ["config/fd_ablation_experiment.py", "config/fd_experiment.py", "config/fd4_validation_design.py",
        "config/firm_architecture_experiment.py", "agent/firm_decision_runner.py",
        "agent/fd_experiment_runtime.py", "core/fd_ablation_metrics.py",
        "core/firm_action_contract.py", "core/firm_tool_evidence.py", "core/firm_decision_protocol.py",
        "core/firm_runtime_support.py", "agent/skill_runner.py",
        "runtime/single_enterprise/orchestrator.py", "config/simulation_preset_config.py"]]
    paths += list((ROOT / "agent/.claude/skills").rglob("SKILL.md"))
    paths += list((ROOT / "agent/.claude/skills").glob("*/actions/*.json"))
    paths += list((ROOT / "enterprise/modules").glob("*_manager.py"))
    return {"schema_version": "fd_ablation_batch.v1", "batch_id": batch_id, "protocol": VERSION,
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(), "repeats": repeats,
        "expected_model_label": model_label, "expected_model_label_note": "Declared route identity; verify actual SDK/provider labels before interpreting results.",
        "simulation_runs_started": 0, "planned_runs": len(requests),
        "configurations": configurations, "requests": requests,
        "sources_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--model-label", required=True)
    parser.add_argument("--families", nargs="+", choices=list(SOURCES))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = prepare(args.batch_id, args.repeats, args.model_label, args.families)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"planned_runs": manifest["planned_runs"], "simulation_runs_started": 0, "output": str(args.output)}))
