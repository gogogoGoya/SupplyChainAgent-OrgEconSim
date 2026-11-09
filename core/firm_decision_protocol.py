"""Validation and durable observation for the opt-in architecture comparison."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import uuid

MODULES = {"hr": "HRManager", "production": "ProductionManager", "sales": "SalesManager",
           "procurement": "ProcurementManager", "inventory": "InventoryManager"}
HIDDEN_KEYS = {
    "true_demand_series", "market_heat_series", "beer_game_demand_series", "events", "scheduled_events",
    "expected_primary_departments", "preferred_actions", "discouraged_actions",
    "primary_issue", "scene_goal", "trajectory_success_signals", "target_handoff_signature",
    "prewarm_execution_plan", "case_id", "source_primary_contradiction",
}


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def visible_facts(value, *, peer_visible=True):
    """Remove experiment answers/future schedules while retaining zero and empty observations."""
    if isinstance(value, list):
        return [visible_facts(item, peer_visible=peer_visible) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    peer_fields = {"average_planned_quantity", "total_planned_quantity", "synchronization_index",
                   "planned_quantity_dispersion", "herding_index", "overproduction_ratio", "inventory_pressure"}
    for key, child in value.items():
        if key in HIDDEN_KEYS:
            continue
        if not peer_visible and (key == "peer_summary" or key in peer_fields):
            result[key] = {"visible": False} if key == "peer_summary" else None
            continue
        result[key] = visible_facts(child, peer_visible=peer_visible)
    return result


def validate_analysis(value, enterprise_id, round_id, departments=()):
    if not isinstance(value, dict):
        return ["Analysis must be a JSON object"]
    errors = []
    if value.get("enterprise_name") != enterprise_id or str(value.get("round_id")) != str(round_id):
        errors.append("Analysis must reference the current enterprise_name and round_id")
    if not isinstance(value.get("enterprise_summarys"), str) or not value["enterprise_summarys"].strip():
        errors.append("enterprise_summarys must contain a nonempty diagnosis")
    targets = value.get("department_targets")
    if not isinstance(targets, dict):
        errors.append("department_targets must be an object")
    elif departments:
        for dept in departments:
            target = targets.get(dept)
            if not isinstance(target, dict) or any(not target.get(key) for key in ("target", "evaluation", "reason")):
                errors.append(f"department_targets.{dept} must contain target, evaluation and reason")
    return errors


def validate_workflow(value, department, enterprise_id, valid_action):
    if not valid_action(value, department):
        return [f"{department}: require a nonempty standard action array with legal names and parameters"]
    errors = []
    for item in value:
        if item.get("executor_id") != enterprise_id or item.get("module_type") != MODULES.get(department):
            errors.append(f"{department}: incorrect executor_id or module_type")
        # NaN/Infinity are accepted by Python's default parser, but are not valid action parameters.
        try:
            json.dumps(item, allow_nan=False)
        except ValueError:
            errors.append(f"{department}: non-finite numeric parameter")
    return errors


def validate_bundle(value, *, enterprise_id, round_id, phase, snapshot_id, departments, valid_action, enterprise_name=None):
    if not isinstance(value, dict):
        return ["Enterprise decision must be a JSON object"]
    errors = []
    for key, expected in (("schema_version", "firm_decision.v1"), ("enterprise_id", enterprise_id), ("round_id", round_id),
                          ("phase", phase), ("snapshot_id", snapshot_id)):
        if value.get(key) != expected:
            errors.append(f"{key} must equal {expected!r}")
    decisions = value.get("decisions")
    if not isinstance(decisions, dict) or set(decisions) != set(departments):
        errors.append(f"decisions must contain exactly these functions: {sorted(departments)}")
        return errors
    for dept, workflow in decisions.items():
        errors.extend(validate_workflow(workflow, dept, enterprise_id, valid_action))
    if phase == "decision":
        errors.extend(validate_analysis(value.get("analysis"), enterprise_name or enterprise_id, round_id, departments))
    return errors


class DecisionRecorder:
    """One writer/file per task; invalid candidates and interrupted attempts remain observable."""

    def __init__(self, root, identity):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity = identity
        self.seq = 0

    def event(self, event_type, **payload):
        self.seq += 1
        record = {"schema_version": "firm_decision_event.v1", **self.identity,
                  "event_id": uuid.uuid4().hex, "seq": self.seq, "event_type": event_type,
                  "occurred_at_utc": datetime.now(timezone.utc).isoformat(),
                  "monotonic_ns": time.monotonic_ns(), **payload}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            output.flush()
        return record


def aggregate_quality(workspace):
    """Summarize reached phases, keeping model quality separate from execution success."""
    paths = sorted((Path(workspace) / "enterprises").glob("*/firm_agent/day*/*/phase_summary.json"),
                   key=lambda p: (int(p.parent.parent.name[3:]), p.parent.name, str(p)))
    records = [(path, read_json(path, {})) for path in paths]
    records = [(path, p) for path, p in records if p.get("schema_version") == "firm_phase.v1" and not p.get("not_applicable")]
    phases = [p for _, p in records]
    # A cancelled task may never have been appended to its parent phase's task list.
    # Its durable checkpoint still contributes to task/attempt workload.
    for path, phase in records:
        checkpoints = [read_json(p, {}) for p in path.parent.glob("*/task_summary.json")]
        if checkpoints:
            phase["tasks"] = checkpoints
    tasks = [task for phase in phases for task in phase.get("tasks", [])]
    total = len(phases)
    available = sum(bool(p.get("decision_available")) for p in phases)
    return {
        "schema_version": "firm_architecture_quality.v1", "phase_count": total,
        "available_phases": available, "unavailable_phases": total - available,
        "phase_availability_rate": available / total if total else None,
        "first_attempt_valid_tasks": sum(t.get("first_attempt_valid") is True for t in tasks),
        "model_task_count": len(tasks),
        "attempt_count": sum(int(t.get("attempts") or 0) for t in tasks),
        "timeout_attempts": sum(int(t.get("timeout_attempts") or 0) for t in tasks),
        "control_channel_error_attempts": sum(int(t.get("control_channel_error_attempts") or 0) for t in tasks),
        "execution_adapter_versions": sorted({t["execution_adapter_version"] for t in tasks if t.get("execution_adapter_version")}),
        "recovered_tasks": sum(bool(t.get("recovered")) for t in tasks),
        "business_rejections": sum(p.get("business_rejections", 0) for p in phases),
        "pre_execution_rejections": sum(p.get("pre_execution_rejections", 0) for p in phases),
        "normalization_changes": sum(p.get("normalization_changes", 0) for p in phases),
        "business_repairs": sum(p.get("business_repairs", 0) for p in phases),
        "unresolved_rejections": sum(p.get("unresolved_rejections", 0) for p in phases),
        "unknown_execution_results": sum(p.get("unknown_execution_results", 0) for p in phases),
        "technical_no_submission_phases": sum(bool(p.get("technical_no_submission")) for p in phases),
        "elapsed_seconds": sum(p.get("elapsed_seconds", 0) for p in phases),
        "latest_phases": phases[-8:],
    }


def write_architecture_round_integrity(workspace, round_id, enterprise_ids):
    """Archive integrity is not a filter deleting retries or unavailable decisions."""
    workspace = Path(workspace)
    phases = []
    missing = []
    for enterprise in enterprise_ids:
        root = workspace / "enterprises" / enterprise / "firm_agent" / f"day{round_id}"
        for phase in ("decision", "trade"):
            path = root / phase / "phase_summary.json"
            value = read_json(path)
            if not isinstance(value, dict):
                missing.append(str(path))
            else:
                phases.append(value)
    applicable = [p for p in phases if not p.get("not_applicable")]
    payload = {
        "schema_version": "firm_architecture_round_integrity.v1", "round_id": round_id,
        "ok": not missing, "missing_phase_summaries": missing,
        "decision_quality_ok": not missing and all(p.get("decision_available") for p in applicable),
        "totals": {"enterprise_count": len(enterprise_ids), "phase_expected": len(enterprise_ids) * 2,
                   "phase_present": len(phases), "phase_unavailable": sum(not p.get("decision_available") for p in applicable),
                   "model_tasks": sum(len(p.get("tasks", [])) for p in applicable)},
        "notes": ["ok tests sealed phase records; failures/retries remain included in reliability evaluation."],
    }
    target = workspace / "round_integrity" / f"day{round_id}.json"
    write_json(target, payload)
    return str(target)
