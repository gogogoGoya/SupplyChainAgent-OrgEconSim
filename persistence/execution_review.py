"""Derive public execution evidence from archived records, without model calls."""
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import re

from core.run_integrity import read_json, completed_count, observer_stage, prewarm_boundary

COUNT_KEYS = ("business_rejections", "business_repairs", "unresolved_rejections",
              "unknown_execution_results", "pre_execution_rejections")
TASK_KEYS = ("task_id", "role", "attempts", "success", "status", "first_attempt_valid",
             "timeout_attempts", "recovered", "elapsed_seconds", "business_repair_output_valid")


def numeric(value):
    import math
    try:
        return float(value) if value is not None and not isinstance(value, bool) and math.isfinite(float(value)) else None
    except (TypeError, ValueError):
        return None


def measure(value, unit, source, numerator=None, denominator=None, scope=None):
    return {"value": numeric(value), "unit": unit, "source": source,
            "numerator": numerator, "denominator": denominator, "scope": scope,
            "availability": "available" if numeric(value) is not None else "missing"}


def wall_seconds(meta):
    try:
        start = datetime.fromisoformat(str(meta["started_at"]).replace("Z", "+00:00"))
        finish = datetime.fromisoformat(str(meta["finished_at"]).replace("Z", "+00:00"))
        if (start.tzinfo is None) != (finish.tzinfo is None):
            # Legacy writers mixed offset start times with local finish times.
            # Preserve the local-clock interval and expose its basis separately.
            start,finish=start.replace(tzinfo=None),finish.replace(tzinfo=None)
        elapsed = (finish - start).total_seconds()
        return elapsed if elapsed >= 0 else None
    except (KeyError, ValueError, TypeError):
        return None


def timing_basis(meta):
    try:
        values=[datetime.fromisoformat(str(meta[k]).replace("Z","+00:00")) for k in ("started_at","finished_at")]
        return "Offset-qualified timestamps" if all(v.tzinfo is not None for v in values) else "Local-clock timestamps; finish or start offset not recorded"
    except (KeyError,TypeError,ValueError):return "Start or finish timestamp not recorded"


def receipt_leaves(value):
    if isinstance(value, list):
        return [r for v in value for r in receipt_leaves(v)]
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("success"), bool):
        return [value]
    return [r for key in ("result", "success", "error", "failed", "data")
            if key in value for r in receipt_leaves(value[key])]


def action_trace(record, department, phase, source, index):
    proposal = record.get("proposed") or {}
    action = proposal.get("action") or {}
    name = action.get("action_name") or proposal.get("action_name")
    is_pass = name == "action_pass" or bool(proposal.get("pass_reason"))
    leaves = receipt_leaves(record.get("response"))
    receipts = [{k: v for k, v in leaf.items() if k in {
        "action_type", "success", "status", "message", "params", "receipt_id", "submission_id"}}
        for leaf in leaves]
    parents = record.get("parent_action_ids")
    return {"id": record.get("action_id") or f"recorded:{phase}:{department}:{index}",
            "department": department, "phase": phase, "source": source,
            "task_id": record.get("task_id"), "attempt": record.get("attempt_number"),
            "parent_ids": parents if isinstance(parents, list) else [],
            "lineage_recorded": isinstance(parents, list), "proposal": proposal,
            "action_name": name, "is_pass": is_pass,
            "status": record.get("status", "unknown"), "receipts": receipts,
            "feedback": [r.get("message") for r in receipts if r.get("success") is False],
            "outcome": "withdrawal" if is_pass and parents else "no_action" if is_pass
                       else "executed" if record.get("status") == "success" and any(r.get("success") for r in receipts)
                       else record.get("status", "unknown")}


def build_execution_evidence(root):
    root = Path(root)
    meta = read_json(root / "run_meta.json", {})
    count = completed_count(meta) or 0
    phases, traces = [], defaultdict(list)
    for directory in sorted((root / "enterprises").glob("*/firm_agent/day*/*")):
        if not directory.is_dir() or directory.is_symlink():
            continue
        summary = directory / "phase_summary.json"
        payload = read_json(summary) or read_json(directory / "phase_started.json")
        if not isinstance(payload, dict):
            continue
        round_id = payload.get("round_id")
        firm = directory.parents[2].name
        if not isinstance(round_id, int) or round_id < 0 or round_id >= count:
            continue
        phase = payload.get("phase", directory.name)
        source = str((summary if summary.exists() else directory / "phase_started.json").relative_to(root))
        tasks = {}
        for task in payload.get("tasks", []):
            if isinstance(task, dict) and task.get("task_id"):
                tasks[task["task_id"]] = task
        for path in directory.glob("*/task_summary.json"):
            task = read_json(path, {})
            if isinstance(task, dict) and task.get("task_id"):
                tasks[task["task_id"]] = task
        clean_tasks = [{k: t[k] for k in TASK_KEYS if k in t} for t in tasks.values()]
        phases.append({"enterprise": firm, "round": round_id, "phase": phase,
                       "sealed": summary.exists(), "source": source,
                       "decision_available": payload.get("decision_available"),
                       "elapsed_seconds": payload.get("elapsed_seconds"),
                       "counts": {k: payload.get(k) for k in COUNT_KEYS}, "tasks": clean_tasks})
        dispatch = payload.get("dispatch_records") or {}
        if isinstance(dispatch, dict):
            for dept, records in dispatch.items():
                if isinstance(records, list):
                    traces[(firm, round_id)].extend(action_trace(r, dept, phase, source, i)
                                                  for i, r in enumerate(records) if isinstance(r, dict))
    health = []
    recorded={(p["enterprise"],p["round"],p["phase"]) for p in phases}
    legacy=defaultdict(dict)
    audit_paths=list((root/"enterprises").glob("*/department/*/day*/*execution_audit.json"))
    audit_paths+=list((root/"enterprises").glob("*/records/day*/*execution_audit.json"))
    for path in sorted(audit_paths):
        audit=read_json(path,{})
        if not isinstance(audit,dict):continue
        relative=path.relative_to(root)
        firm=relative.parts[1]
        day=next((int(p[3:]) for p in relative.parts if re.fullmatch(r"day\d+",p)),None)
        phase=audit.get("phase") or "decision"
        key=(firm,day,phase)
        if key in recorded or day is None or day>=count:continue
        attempts=numeric(audit.get("attempts"));success=audit.get("success")
        if attempts is None or not isinstance(success,bool):continue
        role=audit.get("department") or relative.parts[3]
        fallback=audit.get("fallback_used")
        task={"task_id":str(relative),"role":role,"attempts":attempts,"success":success,
              "first_attempt_valid":success and attempts==1 and not fallback,
              "recovered":success and attempts>1 and not fallback,
              "elapsed_seconds":audit.get("elapsed_seconds"),"source_kind":"legacy_skill_audit"}
        legacy[key][role]=task
    for (firm,r,phase),tasks in legacy.items():
        phases.append({"enterprise":firm,"round":r,"phase":phase,"sealed":True,
                       "source":"archived Skill execution audits","record_kind":"legacy_skill_audit",
                       "decision_available":None,"elapsed_seconds":None,
                       "counts":{k:None for k in COUNT_KEYS},"tasks":list(tasks.values())})
    phase_trace_keys=set(traces)
    for path in sorted((root/"enterprises").glob("*/department/*/day*/*_result.json")):
        relative=path.relative_to(root);firm=relative.parts[1];dept=relative.parts[3]
        if not re.fullmatch(r"day\d+",relative.parts[4]):continue
        r=int(relative.parts[4][3:])
        if (firm,r) in phase_trace_keys:continue
        payload=read_json(path,{})
        if not isinstance(payload,dict):continue
        records=payload.get("dispatch_records")
        if isinstance(records,list):
            traces[(firm,r)].extend(action_trace(record,dept,"recorded",str(relative),i)
                                    for i,record in enumerate(records) if isinstance(record,dict))
    for r in range(count):
        relative = f"runtime_health/day{r}_agent_timeout.json"
        data = read_json(root / relative)
        value = data.get("all_agent_calls_timed_out") if isinstance(data, dict) else None
        health.append({"round": r, "all_agent_timeout": value if isinstance(value, bool) else None,
                       "source": relative, "stage": observer_stage(meta, r)})
    return {"schema_version": "execution_review.v1", "run_id": meta.get("run_id"),
            "phases": phases, "health": health,
            "legacy_quality": read_json(root / "firm_architecture_quality.json", {}),
            "whole_run_wall_seconds": wall_seconds(meta),
            "traces": [{"enterprise": f, "round": r, "actions": rows,
                        "end_state_source": f"public/observer_state/day{r}/{observer_stage(meta, r)}/{f}.json"}
                       for (f, r), rows in traces.items()]}


def summarize_execution(evidence, meta, enterprise=None, start=None, end=None, phase="all"):
    count = completed_count(meta) or 0
    start = 0 if start is None else int(start)
    end = count - 1 if end is None else int(end)
    source = "derived execution records"
    missing = lambda unit="count": measure(None, unit, source)
    if not evidence:
        return {"window": {"start": start, "end": end}, "phase": phase,
                "metrics": {k: missing() for k in (
                    "model_tasks", "first_attempt_rate", "retry_recovery_rate", "recovered_tasks",
                    "attempts", "timeouts", "business_rejections", "repair_submissions",
                    "unresolved_rejections", "unknown_receipts", "executed_replacements", "withdrawals")},
                "timing": {"basis":timing_basis(meta),"run_wall": measure(wall_seconds(meta), "seconds", "run_meta.json", scope="whole run"),
                           "phase_elapsed_sum": missing("seconds")}}
    phases = [p for p in evidence.get("phases", []) if start <= p["round"] <= end
              and (not enterprise or p["enterprise"] == enterprise) and (phase == "all" or p["phase"] == phase)]
    tasks = [t for p in phases for t in p["tasks"]]
    metrics = {}
    def sum_field(items, field):
        values = [numeric(t.get(field)) for t in items]
        return sum(values) if values and all(v is not None for v in values) else None
    metrics["model_tasks"] = measure(len(tasks) if phases else None, "tasks", source)
    availability=[p.get("decision_available") for p in phases]
    metrics["available_phases"]=measure(sum(availability) if availability and all(isinstance(v,bool) for v in availability) else None,
                                        "phases",source,denominator=len(phases) if phases else None)
    first = [t.get("first_attempt_valid") for t in tasks]
    known = bool(first) and all(isinstance(x, bool) for x in first)
    eligible = sum(x is False for x in first) if known else None
    recovered = sum(t.get("recovered") is True and t.get("first_attempt_valid") is False for t in tasks) if known and all(isinstance(t.get("recovered"), bool) for t in tasks) else None
    metrics["first_attempt_rate"] = measure(sum(first)/len(first) if known else None, "ratio", source,
                                           sum(first) if known else None, len(first) if known else None)
    metrics["retry_recovery_rate"] = measure(recovered/eligible if eligible and recovered is not None else None,
                                             "ratio", source, recovered, eligible)
    if eligible == 0:
        metrics["retry_recovery_rate"].update(availability="not_applicable",missing_reason="No invalid first attempts in this window")
    metrics["recovered_tasks"] = measure(recovered, "tasks", source)
    metrics["attempts"] = measure(sum_field(tasks, "attempts"), "attempts", source)
    metrics["timeouts"] = measure(sum_field(tasks, "timeout_attempts"), "attempts", source)
    for key, label in (("business_rejections", "business_rejections"), ("business_repairs", "repair_submissions"),
                       ("unresolved_rejections", "unresolved_rejections"), ("unknown_execution_results", "unknown_receipts")):
        metrics[label] = measure(sum_field([p["counts"] for p in phases], key), "count", source)
    traces = [t for t in evidence.get("traces", []) if start <= t["round"] <= end
              and (not enterprise or t["enterprise"] == enterprise)]
    actions = [a for t in traces for a in t["actions"] if phase == "all" or a["phase"] == phase]
    lineage_known = bool(actions) and all(a["lineage_recorded"] for a in actions)
    children=defaultdict(list)
    for action in actions:
        for parent in action["parent_ids"]:children[parent].append(action)
    def terminals(action_id, visited=None):
        visited=set() if visited is None else visited
        if action_id in visited:return ["unknown"]
        visited=visited|{action_id}
        return [outcome for child in children[action_id]
                for outcome in (terminals(child["id"],visited) if children.get(child["id"]) else [child["outcome"]])]
    chains=[terminals(a["id"]) for a in actions if not a["parent_ids"] and children.get(a["id"])]
    executed=sum(bool(outcomes) and all(o in {"executed","withdrawal","no_action"} for o in outcomes)
                 and "executed" in outcomes for outcomes in chains)
    withdrawn=sum(bool(outcomes) and all(o in {"withdrawal","no_action"} for o in outcomes) for outcomes in chains)
    metrics["executed_replacements"] = measure(executed if lineage_known else None,
                                               "parent actions", "explicit parent-action links and receipts")
    metrics["withdrawals"] = measure(withdrawn if lineage_known else None,
                                    "parent actions", "explicit parent-action links and pass decisions")
    # Legacy cumulative counts apply only to the entire archived run.
    legacy = evidence.get("legacy_quality") or {}
    single_firm = len(meta.get("enterprise_ids") or []) == 1
    if not phases and start == 0 and end == count-1 and phase == "all" and (not enterprise or single_firm):
        aliases = {"model_tasks":"model_task_count", "attempts":"attempt_count", "timeouts":"timeout_attempts",
                   "available_phases":"available_phases",
                   "recovered_tasks":"recovered_tasks", "business_rejections":"business_rejections",
                   "repair_submissions":"business_repairs", "unresolved_rejections":"unresolved_rejections",
                   "unknown_receipts":"unknown_execution_results"}
        for key, field in aliases.items():
            metrics[key] = measure(legacy.get(field), metrics[key]["unit"], "firm_architecture_quality.json")
        metrics["available_phases"]["denominator"]=legacy.get("phase_count")
        denominator, numerator = legacy.get("model_task_count"), legacy.get("first_attempt_valid_tasks")
        if numeric(denominator) and numeric(numerator) is not None:
            metrics["first_attempt_rate"] = measure(numerator/denominator, "ratio", "firm_architecture_quality.json", numerator, denominator)
    health = [h for h in evidence.get("health", []) if start <= h["round"] <= end]
    valid = [h for h in health if isinstance(h.get("all_agent_timeout"), bool)]
    metrics["all_agent_timeout_rounds"] = measure(sum(h["all_agent_timeout"] for h in valid)
        if len(valid) == end-start+1 and valid else None, "rounds", "runtime health records", scope="all enterprises")
    return {"window": {"start": start, "end": end}, "phase": phase, "metrics": metrics,
            "recorded_phases": len(phases), "unsealed_phases": sum(not p["sealed"] for p in phases),
            "health_coverage": {"recorded": len(valid), "expected": max(0, end-start+1)},
            "timing": {"basis":timing_basis(meta),"run_wall": measure(wall_seconds(meta), "seconds", "run_meta.json", scope="whole run"),
                       "phase_elapsed_sum": measure(sum_field(phases, "elapsed_seconds"), "seconds", source,
                                                     scope="sum of recorded phase durations; includes concurrency")}}
