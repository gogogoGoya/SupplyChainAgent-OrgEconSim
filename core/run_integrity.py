"""Read-only inspection of archived round evidence, including legacy archives."""
import json
import re
from pathlib import Path


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def completed_count(meta):
    for key in ("completed_steps", "total_steps"):
        if meta.get(key) is not None:
            try:
                return max(0, int(meta[key]))
            except (TypeError, ValueError):
                return None
    return None


def prewarm_boundary(meta):
    """Read the recorded warmup boundary without inferring it from a scenario ID."""
    try:
        return max(0, int(meta.get("prewarm_rounds") or 0))
    except (TypeError, ValueError):
        return 0


def observer_stage(meta, round_id):
    return "prewarm_end_of_day" if 0 <= round_id < prewarm_boundary(meta) else "end_of_day"


def available_rounds(root, meta=None):
    """Discover actual snapshots; a planned horizon is never execution evidence."""
    root = Path(root)
    count = completed_count(meta or {})
    if count == 0:
        return []
    rounds = set()
    parents = [root / "public/exchange", root / "public/observer_state", root / "records"]
    parents += list((root / "enterprises").glob("*/records"))
    for parent in parents:
        for path in parent.glob("day*"):
            match = re.fullmatch(r"day(\d+)", path.name)
            if match and path.is_dir() and any(path.glob("*.json")):
                rounds.add(int(match[1]))
    return sorted(r for r in rounds if count is None or r < count)


def inspect_run(root, meta=None):
    root = Path(root)
    meta = meta if meta is not None else read_json(root / "run_meta.json", {})
    rounds = available_rounds(root, meta)
    count = completed_count(meta)
    try:
        planned = int(meta.get("planned_total_steps"))
    except (TypeError, ValueError):
        planned = None
    missing = sorted(set(range(count or 0)) - set(rounds))
    return {
        "schema_version": "run_inspection.v1", "original_status": meta.get("status"),
        "planned_steps": planned, "declared_completed_steps": count,
        "available_rounds": rounds, "last_available_round": max(rounds, default=-1),
        "missing_declared_rounds": missing,
        "complete": bool(count and planned == count and not missing),
        "evidence": "filesystem_snapshots", "original_archive_modified": False,
    }
