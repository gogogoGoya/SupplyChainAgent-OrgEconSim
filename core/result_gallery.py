"""Inspect archived display coverage without changing simulation records."""
from pathlib import Path

from core.run_integrity import completed_count, inspect_run, observer_stage, prewarm_boundary, read_json

DEPARTMENTS = ("finance", "production", "procurement", "sales", "inventory", "hr")


def _coverage(root, relative_paths):
    missing, invalid = [], []
    for round_id, relative in relative_paths:
        path = root / relative
        if not path.is_file():
            missing.append(round_id)
        elif not isinstance(read_json(path), dict):
            invalid.append(round_id)
    return {"expected": len(relative_paths),
            "valid": len(relative_paths) - len(missing) - len(invalid),
            "missing_rounds": missing, "invalid_rounds": invalid}


def inspect_gallery_run(root):
    """Keep snapshot coverage separate from model and business success."""
    root = Path(root)
    meta = read_json(root / "run_meta.json")
    if not isinstance(meta, dict) or not meta.get("run_id"):
        raise ValueError("Archive requires object metadata with a run_id")
    count = completed_count(meta)
    if count is None or count > 100000:
        raise ValueError("Archive has an invalid completed round count")
    inspection = inspect_run(root, meta)
    enterprise_root = root / "enterprises"
    discovered = sorted(p.name for p in enterprise_root.iterdir()
                        if p.is_dir() and not p.is_symlink()) if enterprise_root.is_dir() else []
    declared = meta.get("enterprise_ids") or discovered
    if not isinstance(declared, list) or any(
            not isinstance(name, str) or not name or Path(name).name != name
            or name in {".", ".."} for name in declared):
        raise ValueError("Archive has invalid enterprise IDs")
    enterprises = sorted(set(declared))
    rounds = list(range(count))
    formal_start = min(count, prewarm_boundary(meta))
    formal_rounds = list(range(formal_start, count))
    coverage = {}
    for firm in enterprises:
        observer = _coverage(root, [(r, f"public/observer_state/day{r}/{observer_stage(meta, r)}/{firm}.json")
                                    for r in rounds])
        departments = {dept: _coverage(root, [
            (r, f"enterprises/{firm}/department/{dept}/day{r}/{dept}.json")
            for r in formal_rounds]) for dept in DEPARTMENTS}
        coverage[firm] = {"end_of_day": observer, "departments": departments}
    end_states_complete = bool(coverage) and all(
        item["end_of_day"]["valid"] == count for item in coverage.values())
    departments_complete = bool(coverage) and all(
        item["valid"] == len(formal_rounds) for firm in coverage.values()
        for item in firm["departments"].values())
    return {"schema_version": "result_gallery_inspection.v1",
            "run_id": meta["run_id"], "scenario_id": meta.get("scenario_id"),
            "original_status": meta.get("status"), "integrity": inspection,
            "enterprise_ids": enterprises, "coverage": coverage,
            "stage_windows": {"prewarm_rounds": list(range(formal_start)),
                              "formal_rounds": formal_rounds},
            "snapshot_coverage_complete": departments_complete,
            "display_ready": bool(meta.get("status") == "completed"
                                  and inspection["complete"] and end_states_complete),
            "quality_recorded": (root / "firm_architecture_quality.json").is_file(),
            "scope": "Recorded display snapshots; not experimental comparability or decision validity"}
