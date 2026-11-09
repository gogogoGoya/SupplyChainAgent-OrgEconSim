#!/usr/bin/env python3
"""Export architecture runs, including failures and recovery, without calling a model."""

import argparse
import csv
import json
from pathlib import Path
import sys
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.firm_decision_protocol import aggregate_quality, read_json, write_json
from config.firm_architecture_experiment import policy_from_config


def mechanism_evidence(root, family):
    """Reuse sealed engine metrics; never use missing values as economic zeros."""
    if family in {"fd1", "fd2", "fd3", "fd4", "fd5"}:
        from core.fd_ablation_metrics import evaluate
        try:
            result = evaluate(root)
            result["fd"] = result["summary"]
            if family == "fd4":
                # Retain old FD4 column aliases while exporting the full common
                # orders/actions/repairs and physical-output contract as well.
                from core.fd4_evaluation import score
                result["fd4"] = score(root)
            return result
        except (OSError, KeyError, ValueError, TypeError) as error:
            return {"status": "incomplete", "reason": str(error), "fd": None}
    if family not in {"bullwhip", "herding_visible", "herding_hidden"}:
        return {"status": "unsupported", "reason": f"Unknown experiment family: {family}"}
    name = "bullwhip" if family == "bullwhip" else "herding"
    rows = []
    for day in sorted((root / "public/exchange").glob("day*"), key=lambda p: int(p.name[3:])):
        path = day / "end_of_day" / f"{name}_metrics.json"
        if not path.exists():
            path = day / f"{name}_metrics.json"
        data = read_json(path)
        data = data.get("data", data) if isinstance(data, dict) else None
        if isinstance(data, dict):
            rows.append({"round_id": int(day.name[3:]), "source": str(path.relative_to(root)), "metrics": data})
    result = {"status": "available" if rows else "missing", "round_count": len(rows), "trajectory": rows}
    if name == "bullwhip":
        result["final"] = rows[-1]["metrics"] if rows else None
    else:
        # Use the existing herding UI's skip-first-three convention, alongside raw rounds.
        late = [r["metrics"] for r in rows if r["round_id"] >= 3]
        result["after_round_2"] = {
            key: mean(values) if (values := [r[key] for r in late if isinstance(r.get(key), (int, float))]) else None
            for key in ("synchronization_index", "herding_index", "overproduction_ratio", "inventory_pressure")
        }
        result["after_round_2"]["observed_rounds"] = len(late)
    return result


def export_runs(runs, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    run_rows, phase_rows, task_rows, attempt_rows, unregistered_rows = [], [], [], [], []
    for root in map(Path, runs):
        meta = read_json(root / "run_meta.json", {})
        policy = policy_from_config(meta.get("scenario_config") or {})
        if not policy:
            raise ValueError(f"Not an architecture experiment: {root}")
        quality = aggregate_quality(root)
        row = {"run_id": meta.get("run_id"), "scenario_id": meta.get("scenario_id"),
               "arm": policy.get("arm"), "batch_id": (meta.get("experiment_design") or {}).get("batch_id"),
               "repeat_id": (meta.get("experiment_design") or {}).get("repeat_id"),
               "economic_contract_sha256": policy.get("economic_contract_sha256"),
               "information_contract_sha256": policy.get("information_contract_sha256"),
               "architecture": policy["architecture"], "family": policy["family"], "status": meta.get("status"),
               "policy_version": policy["version"], "baseline_group": policy.get("baseline_group", "legacy_unlabeled"),
               "source_scenario_id": policy.get("source"),
               "baseline_run_id": (policy.get("baseline") or {}).get("source_run_id"),
               "baseline_config_sha256": (policy.get("baseline") or {}).get("config_sha256"),
               "dispatch_protocol": policy.get("dispatch_protocol"),
               "baseline_rounds_match": (meta.get("planned_total_steps") == (policy.get("baseline") or {}).get("completed_steps"))
                   if policy.get("baseline") else None,
               "planned_total_steps": meta.get("planned_total_steps"), "completed_steps": meta.get("completed_steps"),
               **{k: v for k, v in quality.items() if k not in {"latest_phases", "schema_version"}}}
        run_rows.append(row)
        mechanism = mechanism_evidence(root, policy["family"])
        write_json(output / f"{meta.get('run_id')}_mechanism.json", mechanism)
        row["mechanism_data_status"] = mechanism["status"]
        if mechanism.get("fd"):
            row["handover_economic_sha256"] = mechanism["handover_economic_sha256"]
            row.update({f"fd_{k}": v for k, v in mechanism["fd"].items() if not isinstance(v, (dict, list))})
            for table in ("trajectory", "orders", "purchases", "hires", "capacity_events", "actions", "repairs"):
                write_json(output / f"{meta['run_id']}_{table}.json", mechanism.get(table, []))
        if mechanism.get("fd4"):
            for key in ("cohort_fill", "cohort_unmet_area", "formal_net_profit", "formal_cost", "finished_goods", "recruit_cost"):
                row[f"fd4_{key}"] = mechanism["fd4"].get(key)
        unsealed = 0
        for directory in sorted((root / "enterprises").glob("*/firm_agent/day*/*")):
            if not directory.is_dir():
                continue
            path = directory / "phase_summary.json"
            phase = read_json(path) or read_json(directory / "phase_started.json", {})
            if not phase:
                continue
            phase["record_status"] = "sealed" if path.exists() else "unsealed"
            unsealed += int(not path.exists())
            phase_rows.append({k: v for k, v in phase.items() if k not in {"tasks", "dispatch_records", "proposed_actions"}})
            # Local durable task files are authoritative, including interrupted tasks
            # that never reached their parent phase's completed task list.
            task_files = sorted(directory.glob("*/task_summary.json"))
            for task_file in task_files:
                task = read_json(task_file, {})
                task_row = {"run_id": meta.get("run_id"), "enterprise_id": phase.get("enterprise_id"),
                            "round_id": phase.get("round_id"), "phase": phase.get("phase"),
                            "architecture": policy["architecture"], **task}
                task_rows.append(task_row)
                for attempt in sorted(task_file.parent.glob("attempt_*"), key=lambda p: int(p.name.split("_")[-1])):
                    # A model-created sibling directory is not a registered SDK
                    # invocation. Keep it visible without inflating reliability denominators.
                    if not (attempt / "request.json").is_file():
                        unregistered_rows.append({"run_id": meta.get("run_id"), "task_id": task["task_id"],
                            "attempt_directory": str(attempt.relative_to(root)),
                            "files": [str(p.relative_to(root)) for p in sorted(attempt.rglob("*")) if p.is_file()],
                            "reason": "missing_runner_request"})
                        continue
                    outcome = read_json(attempt / "outcome.json") or {
                        "transport": "interrupted_unknown", "protocol_valid": None,
                        "failure_codes": ["missing_attempt_outcome"],
                    }
                    attempt_rows.append({"run_id": meta.get("run_id"), "task_id": task["task_id"],
                                         "attempt": attempt.name, **outcome,
                                         "tool_evidence": read_json(attempt / "tool_evidence.json"),
                                         "runtime_diagnostics": read_json(attempt / "runtime_diagnostics.json"),
                                         "usage": read_json(attempt / "usage.json")})
        row["unsealed_phases"] = unsealed
        # Economic projection remains authoritative and separate from model/API workload.
        snapshots = sorted((root / "projections" / "run_metrics").glob("day*/run_metrics_projection.json"),
                           key=lambda p: int(p.parent.name.removeprefix("day")))
        if snapshots:
            write_json(output / f"{meta['run_id']}_final_economics.json", read_json(snapshots[-1], {}))
    for name, rows in (("runs", run_rows), ("phases", phase_rows), ("tasks", task_rows), ("attempts", attempt_rows),
                       ("unregistered_artifacts", unregistered_rows)):
        write_json(output / f"{name}.json", rows)
        fields = list(dict.fromkeys(key for row in rows for key in row))
        with (output / f"{name}.csv").open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    return {"runs": len(run_rows), "phases": len(phase_rows), "tasks": len(task_rows), "attempts": len(attempt_rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(export_runs(args.runs, args.output), ensure_ascii=False))
