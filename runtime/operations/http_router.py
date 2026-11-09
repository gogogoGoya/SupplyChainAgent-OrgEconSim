"""FastAPI router for repository-backed operations control."""

from __future__ import annotations

import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote
from functools import lru_cache

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from config.integration_profiles import resolve_integration_profiles
from config.simulation_preset_config import (
    get_available_scenarios,
    get_scenario_config,
    normalize_market_demand_mode,
)
from config.project_paths import (
    AGENT_ROOT,
    SIMULATION_RUNS_ROOT,
    WORKSPACE_JOBS_ROOT,
    WORKSPACE_MULTI_ROOT,
    resolve_project_path,
)

from .api import OperationsAPI
from config.platform_access import get_platform_access
from runtime.platform_access import enforce_platform_access
from core.public_artifacts import is_public_artifact
from core.run_integrity import available_rounds, inspect_run
from core.result_publication import published_member
from runtime.result_access import private_results, result_access_scope
from config.project_paths import PUBLIC_RESULTS_ROOT


class CreateExperimentRequest(BaseModel):
    scenario_id: str
    planned_total_steps: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    requested_by: Optional[str] = None
    job_id: Optional[str] = None
    config_overrides: Dict[str, Any] = Field(default_factory=dict)


class CreateBatchRequest(BaseModel):
    items: List[CreateExperimentRequest]
    repeats: int = Field(default=1, ge=1, le=20)
    requested_by: str = "operations-page"


class StartExperimentRequest(BaseModel):
    scenario_id: Optional[str] = None
    run_id: Optional[str] = None
    worker_id: Optional[str] = None
    artifact_root: Optional[str] = None


class StopExperimentRequest(BaseModel):
    scenario_id: Optional[str] = None
    reason: str
    requested_by: Optional[str] = None


class ResumeExperimentRequest(BaseModel):
    scenario_id: Optional[str] = None
    target_total_steps: Optional[int] = None
    requested_by: Optional[str] = None


class ResumeArtifactRequest(BaseModel):
    source: str
    name: Optional[str] = None
    target_total_steps: int = 200
    requested_by: Optional[str] = None


class DeleteExperimentRequest(BaseModel):
    scenario_id: Optional[str] = None
    force: bool = True


class CheckpointRequest(BaseModel):
    scenario_id: Optional[str] = None
    completed_steps: int
    last_complete_round: Optional[int] = None
    checkpoint_uri: Optional[str] = None
    artifact_root: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class RunJobRequest(BaseModel):
    scenario_id: Optional[str] = None
    worker_id: Optional[str] = None
    client_dir: Optional[str] = None
    workspace_dir: Optional[str] = None


DEPARTMENT_LABELS = {
    "finance": 'Finance',
    "hr": 'Human Resources',
    "inventory": 'Inventory',
    "procurement": 'Procurement',
    "production": 'Production',
    "sales": 'Sales',
}


def _status_code_for_error(response: Dict[str, Any]) -> int:
    error = response.get("error") or {}
    error_type = error.get("type")
    message = str(error.get("message") or "")
    if error_type == "RuntimeError" and "repository is not configured" in message:
        return 503
    if error_type == "SQLiteMirrorWriteError":
        return 503
    if error_type == "ValueError" and (
        "Unknown job_id" in message or "Unknown scenario_id" in message
    ):
        return 404
    if error_type in {"ValueError", "KeyError"}:
        return 400
    return 500


def _json_response(response: Dict[str, Any]) -> JSONResponse:
    if response.get("ok"):
        return JSONResponse(status_code=200, content=response)
    return JSONResponse(status_code=_status_code_for_error(response), content=response)


@lru_cache(maxsize=256)
def _cached_metadata(path, modified_ns, size):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.is_file():
            if path.name in {"run_meta.json", "public_manifest.json"}:
                stat = path.stat()
                return _cached_metadata(str(path), stat.st_mtime_ns, stat.st_size)
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _safe_number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sort_artifact_paths(paths: List[Path]) -> List[Path]:
    def sort_key(path: Path) -> tuple[int, str]:
        try:
            modified_ns = path.stat().st_mtime_ns
        except OSError:
            modified_ns = 0
        return modified_ns, path.name

    return sorted(paths, key=sort_key)


def _collect_department_day_artifacts(
    day_dir: Path,
    department: str,
    artifact_suffix: str,
) -> List[Path]:
    candidates = [
        day_dir / f"{department}{artifact_suffix}.json",
        day_dir / f"pre_{department}{artifact_suffix}.json",
        *sorted(day_dir.glob(f"{department}{artifact_suffix}_*.json")),
        *sorted(day_dir.glob(f"pre_{department}{artifact_suffix}_*.json")),
        *sorted(day_dir.glob(f"*{artifact_suffix}.json")),
        *sorted(day_dir.glob(f"*{artifact_suffix}_*.json")),
    ]
    unique_paths: List[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        unique_paths.append(candidate)
    return _sort_artifact_paths(unique_paths)


def _artifact_root_for_job(job: Dict[str, Any]) -> Optional[Path]:
    checkpoint = job.get("latest_checkpoint") or {}
    raw_root = checkpoint.get("artifact_root") or job.get("artifact_root")
    if not raw_root:
        checkpoint_uri = checkpoint.get("checkpoint_uri") or ""
        if checkpoint_uri.startswith("file://"):
            raw_root = checkpoint_uri.removeprefix("file://")
    if not raw_root:
        return None
    return resolve_project_path(raw_root, relative_to=AGENT_ROOT)


def _safe_archive_name(value: Any, fallback: str = "simulation_artifact") -> str:
    text = str(value or "").strip() or fallback
    safe = "".join(char if char.isalnum() or char in "._=-" else "_" for char in text)
    return safe.strip("_") or fallback


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _artifact_bases() -> Dict[str, Path]:
    if not private_results.get():
        return {"workspace_jobs": PUBLIC_RESULTS_ROOT / "runs", "simulation_runs": PUBLIC_RESULTS_ROOT / "legacy", "workspace_multi": PUBLIC_RESULTS_ROOT / "current"}
    return {
        "workspace_multi": WORKSPACE_MULTI_ROOT,
        "workspace_jobs": WORKSPACE_JOBS_ROOT,
        "simulation_runs": SIMULATION_RUNS_ROOT,
    }


def _resolve_artifact_root(source: str, name: Optional[str] = None) -> Path:
    source = str(source or "").strip()
    bases = _artifact_bases()
    if source not in bases:
        raise ValueError("Unsupported artifact source")
    base = bases[source].resolve()
    if source == "workspace_multi":
        artifact_root = base
    else:
        if not name:
            raise ValueError("Artifact name is required")
        artifact_root = (base / str(name)).resolve()
    if not _path_is_relative_to(artifact_root, base):
        raise ValueError("Artifact path escapes allowed root")
    if not artifact_root.exists() or not artifact_root.is_dir():
        raise FileNotFoundError(str(artifact_root))
    return artifact_root


def _max_day_from_run_artifact(artifact_root: Path, run_meta: Dict[str, Any]) -> int:
    return _inspection_for_artifact(artifact_root, run_meta)["last_available_round"]


def _last_sealed_round_from_artifact(
    artifact_root: Path,
    run_meta: Dict[str, Any],
) -> int:
    enterprise_ids = [
        str(item)
        for item in (run_meta.get("enterprise_ids") or [])
        if str(item)
    ]
    if not enterprise_ids:
        enterprises_root = artifact_root / "enterprises"
        enterprise_ids = [
            path.name
            for path in enterprises_root.iterdir()
            if enterprises_root.exists() and path.is_dir()
        ]
    observer_root = artifact_root / "public" / "observer_state"
    exchange_root = artifact_root / "public" / "exchange"
    candidates = []
    if observer_root.exists():
        for day_path in observer_root.iterdir():
            match = re.match(r"^day(\d+)$", day_path.name)
            if not match or not day_path.is_dir():
                continue
            round_id = int(match.group(1))
            observer_checkpoint = day_path / "end_of_day"
            exchange_checkpoint = (
                exchange_root
                / day_path.name
                / "end_of_day"
                / "exchange.json"
            )
            if not exchange_checkpoint.is_file():
                continue
            if all(
                (observer_checkpoint / f"{enterprise_id}.json").is_file()
                for enterprise_id in enterprise_ids
            ):
                candidates.append(round_id)
    return max(candidates, default=-1)


def _inspection_for_artifact(root, meta):
    from persistence.review_io import ReviewReader
    manifest = ReviewReader(root).manifest
    if manifest.get("schema_version") == "published_results.v1" and manifest.get("inspection"):
        return manifest["inspection"]
    return inspect_run(root, meta)


def _catalog_meta(meta):
    fields = ("run_id","job_id","scenario_id","scenario_meta","experiment_design","integration_profiles",
              "status","planned_total_steps","completed_steps","total_steps","started_at","finished_at","archived_at",
              "orchestrator","enterprise_ids","single_enterprise_case","tags",
              "prewarm_rounds","handoff_day","agent_takeover_round")
    result = {key:meta[key] for key in fields if key in meta}
    config = meta.get("scenario_config") or {}
    injection = config.get("runtime_injection") or {}
    result["scenario_config"] = {key:config[key] for key in ("simulation","integration_profiles","enterprise_specs","enterprise_configs","single_enterprise_case") if key in config}
    result["scenario_config"]["runtime_injection"] = {key:injection[key] for key in ("firm_architecture_experiment","scripted_rule_policy","single_enterprise_diagnostic_policy") if key in injection}
    return result


def _run_catalog_entry(
    *,
    source: str,
    name: str,
    artifact_root: Path,
    data_root: str,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    if source == "workspace_multi":
        entry_id = "workspace_current"
    elif source == "workspace_jobs":
        entry_id = f"workspace_job:{name}"
    elif source == "operations_jobs":
        entry_id = f"operations_job:{meta.get('job_id') or name}"
    else:
        entry_id = name
    inspection = _inspection_for_artifact(artifact_root, meta)
    return {
        "id": entry_id,
        "source": source,
        "name": name,
        "source_label": name,
        "data_root": data_root,
        "resolved_run_id": meta.get("run_id") or name,
        "meta": _catalog_meta(meta),
        "available_rounds": inspection["available_rounds"],
        "integrity": inspection,
        "max_day": _max_day_from_run_artifact(artifact_root, meta),
        "mtime": artifact_root.stat().st_mtime,
    }


def _quote_artifact_name(name: str) -> str:
    return "/".join(quote(part) for part in Path(str(name)).parts)


def _iter_workspace_job_run_dirs(root: Path) -> List[tuple[Path, str]]:
    run_dirs: List[tuple[Path, str]] = []
    if not root.exists():
        return run_dirs
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.name.startswith("_") or not is_public_artifact(path, root):
            continue
        if (path / "run_meta.json").is_file():
            run_dirs.append((path, path.name))
            continue
        for child in sorted(path.iterdir()):
            if (
                child.is_dir()
                and not child.name.startswith("_")
                and is_public_artifact(child, root)
                and (child / "run_meta.json").is_file()
            ):
                run_dirs.append((child, f"{path.name}/{child.name}"))
    return run_dirs


def _collect_artifact_run_catalog(
    *,
    include_workspace_current: bool = True,
    include_workspace_jobs: bool = True,
    include_simulation_runs: bool = True,
) -> List[Dict[str, Any]]:
    bases = _artifact_bases()
    entries: List[Dict[str, Any]] = []

    if include_workspace_current:
        root = bases["workspace_multi"]
        meta = _read_json(root / "run_meta.json", {})
        if isinstance(meta, dict) and meta and (private_results.get() or published_member(root / "run_meta.json", root)):
            entries.append(
                _run_catalog_entry(
                    source="workspace_multi",
                    name="workspace_multi",
                    artifact_root=root,
                    data_root="/workspace_multi",
                    meta=meta,
                )
            )

    directory_sources = []
    if include_workspace_jobs:
        directory_sources.append(("workspace_jobs", bases["workspace_jobs"], "/workspace_jobs"))
    if include_simulation_runs:
        directory_sources.append(("simulation_runs", bases["simulation_runs"], "/simulation_runs"))

    for source, root, url_prefix in directory_sources:
        if not root.exists():
            continue
        run_paths = (
            _iter_workspace_job_run_dirs(root)
            if source == "workspace_jobs"
            else [
                (path, path.name)
                for path in sorted(root.iterdir())
                if path.is_dir() and not path.name.startswith("_") and is_public_artifact(path, root)
            ]
        )
        for path, artifact_name in run_paths:
            if not is_public_artifact(path / "run_meta.json", root) or (not private_results.get() and not published_member(path / "run_meta.json", root)):
                continue
            meta = _read_json(path / "run_meta.json", {})
            if not isinstance(meta, dict) or not meta:
                continue
            entries.append(
                _run_catalog_entry(
                    source=source,
                    name=artifact_name,
                    artifact_root=path,
                    data_root=f"{url_prefix}/{_quote_artifact_name(artifact_name)}",
                    meta=meta,
                )
            )

    return entries



_catalog_cache = {}

def _artifact_run_catalog(**options):
    import time
    bases = tuple(sorted((name,str(path.resolve()),path.stat().st_mtime_ns if path.exists() else 0) for name,path in _artifact_bases().items()))
    key = (bases,bool(private_results.get()),tuple(sorted(options.items())))
    now = time.monotonic()
    cached = _catalog_cache.get(key)
    if cached and now-cached[0] < 2:
        return list(cached[1])
    entries = _collect_artifact_run_catalog(**options)
    if len(_catalog_cache)>8: _catalog_cache.clear()
    _catalog_cache[key] = (now,entries)
    return list(entries)


def _validate_artifact_root(raw_root: Path) -> Path:
    artifact_root = raw_root.expanduser().resolve()
    for base in _artifact_bases().values():
        resolved_base = base.resolve()
        if artifact_root == resolved_base or _path_is_relative_to(artifact_root, resolved_base):
            if not is_public_artifact(artifact_root, resolved_base):
                raise ValueError("Artifact root is not public")
            if not private_results.get() and not published_member(artifact_root, resolved_base):
                raise ValueError("Archive has not been published")
            if artifact_root.exists() and artifact_root.is_dir():
                return artifact_root
            raise FileNotFoundError(str(artifact_root))
    raise ValueError("Artifact root is outside allowed simulation directories")


def _build_artifact_zip(artifact_root: Path, archive_name: Optional[str] = None) -> FileResponse:
    from runtime.download_cache import download_gate
    artifact_root = _validate_artifact_root(artifact_root)
    with download_gate(artifact_root,private_results.get()):
        return _build_artifact_zip_unlocked(artifact_root,archive_name)


def _build_artifact_zip_unlocked(artifact_root: Path, archive_name: Optional[str] = None) -> FileResponse:
    artifact_root = _validate_artifact_root(artifact_root)
    safe_name = _safe_archive_name(archive_name or artifact_root.name)
    manifest = artifact_root / "public_manifest.json"
    cache_path = None
    if manifest.is_file() and not private_results.get():
        import hashlib
        from config.project_paths import RUNTIME_ROOT
        cache_root = RUNTIME_ROOT / "downloads"
        cache_root.mkdir(parents=True,exist_ok=True)
        cache_path = cache_root / (hashlib.sha256(manifest.read_bytes()).hexdigest() + ".zip")
        if cache_path.is_file():
            return FileResponse(cache_path,media_type="application/zip",filename=f"{safe_name}.zip")
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f"{safe_name}_",
        suffix=".zip",
        delete=False,
        dir=cache_path.parent if cache_path is not None else None,
    )
    temp_path = Path(temp_file.name)
    temp_file.close()
    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(artifact_root.rglob("*")):
            if not path.is_file() or not is_public_artifact(path, artifact_root) or (not private_results.get() and path.name != "public_manifest.json" and not published_member(path, artifact_root)):
                continue
            archive.write(
                path,
                arcname=str(Path(safe_name) / path.relative_to(artifact_root)),
            )
    if cache_path is not None:
        temp_path.replace(cache_path)
        return FileResponse(cache_path,media_type="application/zip",filename=f"{safe_name}.zip")
    return FileResponse(
        path=temp_path,
        media_type="application/zip",
        filename=f"{safe_name}.zip",
        background=BackgroundTask(lambda: temp_path.unlink(missing_ok=True)),
    )


def _extract_action_items(payload: Any, department: str) -> List[Dict[str, Any]]:
    if payload is None:
        return []
    if department == "finance" and isinstance(payload, dict) and payload.get("schema_version") == "finance_advice.v1":
        controls = payload.get("recommended_controls") or []
        return [{
            "action_name": "finance_advice",
            "reason": payload.get("risk_summary") or "Financial recommendations generated",
            "detail": " / ".join(str(item) for item in controls[:3]),
        }]
    raw_items = payload if isinstance(payload, list) else [payload]
    if len(raw_items) == 1 and isinstance(raw_items[0], list):
        raw_items = raw_items[0]
    items: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            items.append({"action_name": str(item), "reason": "", "detail": ""})
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        action_param = action.get("action_param")
        if isinstance(action_param, dict):
            detail = ", ".join(
                f"{key}={value}"
                for key, value in list(action_param.items())[:4]
                if value is not None
            )
        else:
            detail = str(action_param or "")
        items.append({
            "action_name": action.get("action_name") or item.get("action_name") or "unknown_action",
            "reason": item.get("action_reason") or item.get("reason") or "",
            "detail": detail,
        })
    return items


def _department_execution_status(day_dir: Path, department: str) -> Dict[str, Any]:
    error_files = _collect_department_day_artifacts(day_dir, department, "_error")
    if error_files:
        error_payload = _read_json(error_files[0], {})
        return {
            "status": "failed",
            "success": False,
            "message": json.dumps(error_payload, ensure_ascii=False)[:260],
        }
    audit_candidates = [
        day_dir / "skill_execution_audit.json",
        day_dir / "trade_skill_execution_audit.json",
        day_dir / "analyst_execution_audit.json",
    ]
    for audit_path in audit_candidates:
        audit = _read_json(audit_path, {})
        if isinstance(audit, dict) and audit:
            if audit.get("success") is False:
                return {
                    "status": "failed",
                    "success": False,
                    "message": "; ".join(audit.get("validation_errors") or [])[:260],
                }
            return {
                "status": audit.get("status") or "success",
                "success": True,
                "message": "scripted" if audit.get("status") == "scripted" else "",
            }
    if _collect_department_day_artifacts(day_dir, department, "_result"):
        return {"status": "success", "success": True, "message": ""}
    return {"status": "generated", "success": None, "message": "Action generated; no execution result was found"}


def _cash_from_finance(day_dir: Path) -> Optional[float]:
    finance_payload = _read_json(day_dir / "finance.json", {})
    if not isinstance(finance_payload, dict):
        return None
    self_state = finance_payload.get("self_state") or {}
    cash_summary = self_state.get("cash_summary") or finance_payload.get("cash_summary") or {}
    for value in (
        cash_summary.get("current_cash"),
        self_state.get("cash"),
        finance_payload.get("cash"),
    ):
        number = _safe_number(value)
        if number is not None:
            return number
    return None


def _summarize_enterprise_round(enterprise_dir: Path, round_id: int) -> Dict[str, Any]:
    departments_root = enterprise_dir / "department"
    department_summaries: List[Dict[str, Any]] = []
    cash = None
    department_dirs = (
        sorted(path for path in departments_root.iterdir() if path.is_dir())
        if departments_root.exists()
        else []
    )
    for dept_dir in department_dirs:
        department = dept_dir.name
        if department == "blackboard":
            continue
        day_dir = dept_dir / f"day{round_id}"
        if not day_dir.exists():
            continue
        if department == "finance":
            cash = _cash_from_finance(day_dir)
        action_paths = _collect_department_day_artifacts(day_dir, department, "_action")
        finance_advice = day_dir / "finance_advice.json"
        if finance_advice.exists() and finance_advice not in action_paths:
            action_paths.append(finance_advice)
            action_paths = _sort_artifact_paths(action_paths)
        if not action_paths:
            continue
        actions: List[Dict[str, Any]] = []
        for action_path in action_paths:
            actions.extend(_extract_action_items(_read_json(action_path, None), department))
        status = _department_execution_status(day_dir, department)
        department_summaries.append({
            "department": department,
            "department_label": DEPARTMENT_LABELS.get(department, department),
            "status": status["status"],
            "success": status["success"],
            "message": status["message"],
            "actions": actions,
        })
    return {
        "enterprise_id": enterprise_dir.name,
        "cash": cash,
        "departments": department_summaries,
    }


def _job_round_summary(job: Dict[str, Any]) -> Dict[str, Any]:
    completed_steps = int(job.get("completed_steps") or 0)
    planned_total_steps = int(job.get("planned_total_steps") or 0)
    latest_checkpoint = job.get("latest_checkpoint") or {}
    last_complete_round = latest_checkpoint.get("last_complete_round", job.get("last_complete_round"))
    if last_complete_round is None and completed_steps > 0:
        last_complete_round = completed_steps - 1
    artifact_root = _artifact_root_for_job(job)
    summary = {
        "job_id": job.get("job_id"),
        "scenario_id": job.get("scenario_id"),
        "status": job.get("status"),
        "completed_steps": completed_steps,
        "planned_total_steps": planned_total_steps,
        "last_complete_round": last_complete_round,
        "current_round_label": (
            f'Round {int(last_complete_round) + 1}'
            if last_complete_round is not None and int(last_complete_round) >= 0
            else 'First round not completed'
        ),
        "artifact_root": str(artifact_root) if artifact_root else "",
        "enterprises": [],
        "total_cash": None,
        "message": "",
    }
    if artifact_root is not None:
        summary["firm_architecture_quality"] = _read_json(artifact_root / "firm_architecture_quality.json", {})
    if artifact_root is None:
        summary["message"] = 'The run directory has not been created yet.'
        return summary
    enterprises_root = artifact_root / "enterprises"
    if last_complete_round is None or int(last_complete_round) < 0:
        summary["message"] = 'No completed round is available for summary.'
        return summary
    if not enterprises_root.exists():
        summary["message"] = 'The enterprise run directory was not found.'
        return summary
    round_id = int(last_complete_round)
    enterprises = [
        _summarize_enterprise_round(path, round_id)
        for path in sorted(enterprises_root.iterdir())
        if path.is_dir()
    ]
    total_cash = sum(
        enterprise["cash"]
        for enterprise in enterprises
        if enterprise.get("cash") is not None
    )
    summary["enterprises"] = enterprises
    summary["total_cash"] = total_cash if any(e.get("cash") is not None for e in enterprises) else None
    return summary


def _scenario_payload(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    payload = []
    for row in rows or []:
        scenario_id = row.get("scenario_id") or row.get("value") or ""
        scenario_config = _safe_scenario_config(scenario_id)
        if (scenario_config.get("fd_validation_instance") or {}).get("instance_sequence") == 2:
            row = {**row, "name": scenario_config["meta"]["name"],
                   "label": scenario_config["meta"]["name"],
                   "summary": scenario_config["meta"]["summary"],
                   "description": scenario_config["meta"]["summary"]}
        integration_profiles = (
            resolve_integration_profiles(scenario_config)
            if scenario_config
            else {}
        )
        payload.append(
            {
                **row,
                "scenario_id": scenario_id,
                "value": row.get("value") or scenario_id,
                "label": row.get("label") or row.get("name") or scenario_id,
                "description": row.get("description") or row.get("summary") or "",
                "experiment_design": (
                    row.get("experiment_design")
                    or scenario_config.get("experiment_design")
                    or {}
                ),
                "simulation_profile": integration_profiles.get("simulation", {}).get(
                    "profile",
                    "multi_enterprise",
                ),
                "config_defaults": _scenario_config_defaults(scenario_config),
            }
        )
    return payload


def _external_order_targets(scenario_config: Dict[str, Any]) -> List[str]:
    runtime_injection = (scenario_config or {}).get("runtime_injection") or {}
    policy = runtime_injection.get("external_market_order_policy") or {}
    if policy.get("enabled") is False or policy.get("mode") in {"disabled", "none"}:
        return []
    targets = (
        policy.get("target_enterprise_ids")
        or runtime_injection.get("external_market_order_enterprise_ids")
        or []
    )
    if isinstance(targets, str):
        targets = [targets]
    if not targets and runtime_injection.get("external_market_order_enterprise_id"):
        targets = [runtime_injection["external_market_order_enterprise_id"]]
    return [str(target) for target in targets if target]


def _market_product_id(simulation: Dict[str, Any]) -> Optional[str]:
    mode = normalize_market_demand_mode((simulation or {}).get("market_demand_mode"))
    if mode == "scheduled_external_demand":
        return (simulation or {}).get("beer_game_product_id") or "beer"
    if mode == "shared_resource_market":
        return ((simulation or {}).get("shared_resource_config") or {}).get("product_id")
    if mode == "herding_market":
        return ((simulation or {}).get("herding_config") or {}).get("product_id")
    if mode == "cobweb":
        return ((simulation or {}).get("cobweb_config") or {}).get("product_id")
    return None


def _enterprise_by_id(scenario_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    enterprises = {}
    for item in (
        (scenario_config or {}).get("enterprise_specs")
        or (scenario_config or {}).get("enterprise_configs")
        or []
    ):
        enterprise_id = item.get("id") or item.get("enterprise_id")
        if enterprise_id:
            enterprises[str(enterprise_id)] = item
    return enterprises


def _external_order_integrity(scenario_config: Dict[str, Any]) -> Dict[str, Any]:
    simulation = (scenario_config or {}).get("simulation") or {}
    demand_product_id = _market_product_id(simulation)
    targets = _external_order_targets(scenario_config)
    enterprises = _enterprise_by_id(scenario_config)
    target_details = []
    for target in targets:
        salable_products = enterprises.get(target, {}).get("salable_products_idList") or []
        if isinstance(salable_products, str):
            salable_products = [salable_products]
        matched = (
            not demand_product_id
            or not salable_products
            or demand_product_id in salable_products
        )
        target_details.append({
            "enterprise_id": target,
            "demand_product_id": demand_product_id,
            "salable_products_idList": list(salable_products),
            "matched": bool(matched),
        })
    return {
        "enabled": bool(targets),
        "market_demand_mode": normalize_market_demand_mode(simulation.get("market_demand_mode")),
        "demand_product_id": demand_product_id,
        "target_enterprise_ids": targets,
        "targets": target_details,
        "ok": all(item["matched"] for item in target_details),
    }


def _safe_scenario_config(scenario_id: str) -> Dict[str, Any]:
    if not scenario_id:
        return {}
    try:
        config = get_scenario_config(scenario_id)
        from config.fd_experiment import FD4
        if str(scenario_id).removesuffix("_scripted") == FD4:
            from config.fd4_validation_design import fd4_instance
            config = fd4_instance(config)
        return config
    except Exception:
        return {}


def _scenario_config_defaults(scenario_config: Dict[str, Any]) -> Dict[str, Any]:
    from config.fd_experiment import experiment_options
    simulation = (scenario_config or {}).get("simulation") or {}
    runtime_injection = (scenario_config or {}).get("runtime_injection") or {}
    scripted_policy = runtime_injection.get("scripted_rule_policy") or {}
    auto_policy = (scenario_config or {}).get("auto_policy") or {}
    architecture = runtime_injection.get("firm_architecture_experiment") or {}
    return {
        "firm_architecture_experiment": architecture,
        "fd_experiment_options": [] if architecture.get("enabled") else experiment_options(
            (runtime_injection.get("single_enterprise_case_policy") or {}).get("case_id"),
            bool(scripted_policy.get("enabled")),
        ),
        "simulation": {
            "agent_run_steps": simulation.get("agent_run_steps"),
            "service_total_steps": simulation.get("service_total_steps"),
            "max_enterprise_concurrency": simulation.get("max_enterprise_concurrency"),
            "max_department_concurrency": simulation.get("max_department_concurrency"),
            "market_demand_mode": simulation.get("market_demand_mode"),
            "beer_game_product_id": simulation.get("beer_game_product_id"),
            "beer_game_customer_delivery_lead_time": simulation.get(
                "beer_game_customer_delivery_lead_time"
            ),
            "beer_game_unit_price": simulation.get("beer_game_unit_price"),
            "beer_game_demand_series": simulation.get("beer_game_demand_series") or [],
            "cobweb_config": simulation.get("cobweb_config") or {},
            "shared_resource_config": simulation.get("shared_resource_config") or {},
            "herding_config": simulation.get("herding_config") or {},
        },
        "auto_policy": {
            "hr": {
                "recruit_trigger_threshold": (auto_policy.get("hr") or {}).get(
                    "recruit_trigger_threshold"
                ),
                "recruit_ratio": (auto_policy.get("hr") or {}).get("recruit_ratio"),
            },
            "inventory": {
                "expand_trigger_threshold": (
                    auto_policy.get("inventory") or {}
                ).get("expand_trigger_threshold"),
                "expand_ratio": (auto_policy.get("inventory") or {}).get(
                    "expand_ratio"
                ),
            },
        },
        "runtime_injection": {
            "salary_payment_interval_days": runtime_injection.get(
                "salary_payment_interval_days"
            ),
            "inventory_cost_daily_settlement_enabled": runtime_injection.get(
                "inventory_cost_daily_settlement_enabled"
            ),
            "scripted_rule_policy": {
                "enabled": scripted_policy.get("enabled"),
                "mode": scripted_policy.get("mode"),
                "production": scripted_policy.get("production") or {},
                "single_enterprise_rules": scripted_policy.get("single_enterprise_rules") or {},
                "single_case": scripted_policy.get("single_case") or {},
            },
            "profit_objective_policy": runtime_injection.get(
                "profit_objective_policy"
            ) or {},
            "long_run_experiment_policy": runtime_injection.get(
                "long_run_experiment_policy"
            ) or {},
            "external_environment_policy": runtime_injection.get(
                "external_environment_policy"
            ) or {},
            "analyst_policy": runtime_injection.get("analyst_policy") or {},
            "cobweb_enterprise_guidance_policy": runtime_injection.get(
                "cobweb_enterprise_guidance_policy"
            ) or {},
            "single_enterprise_case_policy": runtime_injection.get(
                "single_enterprise_case_policy"
            ) or {},
            "external_market_order_policy": runtime_injection.get(
                "external_market_order_policy"
            ) or {},
            "shared_resource_governance_policy": runtime_injection.get(
                "shared_resource_governance_policy"
            ) or {},
            "herding_experiment_policy": runtime_injection.get(
                "herding_experiment_policy"
            ) or {},
            "bullwhip_midstream_pass_through_mode": runtime_injection.get(
                "bullwhip_midstream_pass_through_mode"
            ) or {},
            "bullwhip_proposal_conversion_acceleration": runtime_injection.get(
                "bullwhip_proposal_conversion_acceleration"
            ) or {},
            "bullwhip_manufacturer_upstream_amplification": runtime_injection.get(
                "bullwhip_manufacturer_upstream_amplification"
            ) or {},
            "bullwhip_supplier_upstream_pull_through_mode": runtime_injection.get(
                "bullwhip_supplier_upstream_pull_through_mode"
            ) or {},
        },
        "external_order_integrity": _external_order_integrity(scenario_config),
        "integration_profiles": (scenario_config or {}).get("integration_profiles") or {},
        "experiment_design": (scenario_config or {}).get("experiment_design") or {},
        "formal_experiment_config": (scenario_config or {}).get("formal_experiment_config") or {},
    }


def create_operations_router(
    *,
    api: OperationsAPI = None,
    scenario_catalog_loader: Callable[[], List[Dict[str, Any]]] = None,
) -> APIRouter:
    """
    Build the operations router.

    Tests and deployment adapters can inject an OperationsAPI with a concrete
    repository. Without injection, the API resolves the repository from the
    configured storage profile and SQL mirror environment variables.
    """

    router = APIRouter(prefix="/operations", tags=["operations"],
                       dependencies=[Depends(enforce_platform_access), Depends(result_access_scope)])
    operations_api = api or OperationsAPI()
    from .batch import BatchService
    batches = BatchService(operations_api)

    @router.get("/batches")
    def list_batches():
        if not private_results.get(): return {"ok": True, "data": []}
        return _json_response(operations_api._capture(lambda: batches.list(reconcile=False)))

    @router.get("/access")
    def access_capabilities(request: Request):
        return {"ok": True, "data": get_platform_access().capabilities(
            request.headers.get("authorization", ""))}

    @router.post("/batches")
    def create_batch(request: CreateBatchRequest):
        items = [item.model_dump(exclude_none=True) if hasattr(item, "model_dump")
                 else item.dict(exclude_none=True) for item in request.items]
        return _json_response(operations_api._capture(
            lambda: batches.create(items, request.repeats, request.requested_by)))

    @router.post("/batches/{batch_id}/pause")
    def pause_batch(batch_id: str):
        return _json_response(operations_api._capture(lambda: batches.pause(batch_id)))

    @router.post("/batches/{batch_id}/resume")
    def resume_batch(batch_id: str):
        return _json_response(operations_api._capture(lambda: batches.resume(batch_id)))

    load_scenarios = scenario_catalog_loader or get_available_scenarios
    scenario_payload_cache = None

    @router.get("/artifacts/review")
    def artifact_review(source: str, name: Optional[str] = None, enterprise: Optional[str] = None,
                        round: Optional[int] = None, offset: int = 0, limit: int = 100, status: Optional[str] = None,
                        start: Optional[int] = None, end: Optional[int] = None, decision_phase: str = "all"):
        from persistence.review_projection import build_review
        def collect():
            root = _validate_artifact_root(_resolve_artifact_root(source, name))
            return build_review(root, enterprise, round, max(0,offset), min(500,max(1,limit)),status,
                                start,end,decision_phase)
        return _json_response(operations_api._capture(collect))

    @router.get("/artifacts/trend")
    def artifact_trend(source: str, name: Optional[str] = None, enterprise: Optional[str] = None,
                       start: Optional[int] = None, end: Optional[int] = None):
        from persistence.review_projection import build_trend
        def collect():
            root = _validate_artifact_root(_resolve_artifact_root(source, name))
            return build_trend(root, enterprise,start,end)
        return _json_response(operations_api._capture(collect))

    @router.get("/artifacts/view")
    def artifact_view(source: str,name: Optional[str]=None,view: str="global"):
        from persistence.review_io import ReviewReader
        def collect():
            if view not in {"global","operations","evolution","bullwhip"}:raise ValueError("Unknown chart view")
            root=_validate_artifact_root(_resolve_artifact_root(source,name))
            reader=ReviewReader(root);data=reader.projection(view)
            relative=reader.manifest.get("review_projections",{}).get(view)
            status=reader.states.get(relative,"not_recorded")
            return {"availability":"available" if data is not None else status,"data":data}
        return _json_response(operations_api._capture(collect))

    @router.post("/jobs/reconcile")
    def reconcile_jobs():
        return _json_response(operations_api.reconcile_jobs())

    @router.get("/jobs")
    def list_jobs(
        scenario_id: Optional[str] = None,
        status: Optional[str] = None,
        active_only: bool = False,
        limit: Optional[int] = 100,
    ):
        if not private_results.get(): return {"ok": True, "data": []}
        return _json_response(
            operations_api.list_jobs(
                scenario_id=scenario_id,
                status=status,
                active_only=active_only,
                limit=limit,
            )
        )

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, scenario_id: Optional[str] = None):
        if not private_results.get(): return JSONResponse(status_code=404, content={"ok": False, "message": "No published job record"})
        return _json_response(
            operations_api.get_job(job_id, scenario_id=scenario_id)
        )

    @router.get("/jobs/{job_id}/summary")
    def get_job_summary(job_id: str, scenario_id: Optional[str] = None):
        if not private_results.get(): return JSONResponse(status_code=403, content={"ok": False, "message": "Use the published run summary"})
        response = operations_api.get_job(job_id, scenario_id=scenario_id)
        if not response.get("ok"):
            return _json_response(response)
        return JSONResponse(
            status_code=200,
            content={"ok": True, "data": _job_round_summary(response.get("data") or {})},
        )

    @router.get("/jobs/{job_id}/archive")
    def download_job_archive(job_id: str, scenario_id: Optional[str] = None):
        if not private_results.get(): return JSONResponse(status_code=403, content={"ok": False, "message": "Use the published artifact archive"})
        response = operations_api.get_job(job_id, scenario_id=scenario_id)
        if not response.get("ok"):
            return _json_response(response)
        job = response.get("data") or {}
        artifact_root = _artifact_root_for_job(job)
        if artifact_root is None:
            return JSONResponse(
                status_code=404,
                content={
                    "ok": False,
                    "error": {
                        "type": "NotFound",
                        "message": "Job has no artifact_root yet",
                    },
                },
            )
        try:
            archive_name = job.get("run_id") or job.get("job_id") or artifact_root.name
            return _build_artifact_zip(artifact_root, archive_name=archive_name)
        except FileNotFoundError as exc:
            return JSONResponse(
                status_code=404,
                content={
                    "ok": False,
                    "error": {
                        "type": "NotFound",
                        "message": f"Artifact root not found: {exc}",
                    },
                },
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": {
                        "type": "ValueError",
                        "message": str(exc),
                    },
                },
            )

    @router.get("/artifacts/runs")
    def list_artifact_runs(
        include_workspace_current: bool = True,
        include_workspace_jobs: bool = True,
        include_simulation_runs: bool = True,
        include_operations_jobs: bool = True,
        limit: Optional[int] = 500,
        offset: int = 0,
    ):
        try:
            entries = _artifact_run_catalog(
                include_workspace_current=include_workspace_current,
                include_workspace_jobs=include_workspace_jobs,
                include_simulation_runs=include_simulation_runs,
            )
            if include_operations_jobs and private_results.get():
                jobs_response = operations_api.list_jobs(limit=limit)
                if jobs_response.get("ok"):
                    for job in jobs_response.get("data") or []:
                        artifact_root = _artifact_root_for_job(job)
                        if artifact_root is None or not artifact_root.exists():
                            continue
                        meta = _read_json(
                            artifact_root / "run_meta.json",
                            {},
                        )
                        if not isinstance(meta, dict) or not meta:
                            meta = {
                                "run_id": job.get("run_id") or job.get("job_id"),
                                "job_id": job.get("job_id"),
                                "scenario_id": job.get("scenario_id"),
                                "scenario_config": job.get("scenario_config") or {},
                                "integration_profiles": (
                                    job.get("integration_profiles")
                                    or (job.get("scenario_config") or {}).get("integration_profiles")
                                    or {}
                                ),
                                "status": job.get("status"),
                                "planned_total_steps": job.get("planned_total_steps"),
                                "completed_steps": job.get("completed_steps"),
                                "started_at": job.get("started_at"),
                                "finished_at": job.get("finished_at"),
                                "artifact_root": str(artifact_root),
                                "operations_job": job,
                                "tags": job.get("tags") or [],
                            }
                        else:
                            meta = dict(meta)
                            meta.setdefault("operations_job", job)
                            meta.setdefault("job_id", job.get("job_id"))
                        workspace_name = artifact_root.name
                        try: workspace_name = artifact_root.resolve().relative_to(WORKSPACE_JOBS_ROOT.resolve()).as_posix()
                        except ValueError: continue
                        entries.append(
                            _run_catalog_entry(
                                source="operations_jobs",
                                name=workspace_name,
                                artifact_root=artifact_root,
                                data_root=f"/workspace_jobs/{_quote_artifact_name(workspace_name)}",
                                meta=meta,
                            )
                        )
                else:
                    # The file-backed catalog is still useful when the SQL mirror
                    # is not configured or temporarily unavailable.
                    pass
            entries.sort(
                key=lambda item: (
                    str(
                        (item.get("meta") or {}).get("finished_at")
                        or (item.get("meta") or {}).get("archived_at")
                        or (item.get("meta") or {}).get("started_at")
                        or ""
                    ),
                    float(item.get("mtime") or 0),
                    str(item.get("id") or ""),
                ),
                reverse=True,
            )
            return JSONResponse(
                status_code=200,
                content={
                    "ok": True,
                    "data": {
                        "entries": entries[max(0, offset):max(0, offset) + min(1000, max(1, limit or 500))],
                        "count": len(entries),
                    },
                },
            )
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "error": {
                        "type": exc.__class__.__name__,
                        "message": str(exc),
                    },
                },
            )

    @router.get("/artifacts/archive")
    def download_artifact_archive(source: str, name: Optional[str] = None):
        try:
            artifact_root = _resolve_artifact_root(source, name)
            archive_name = name or source
            return _build_artifact_zip(artifact_root, archive_name=archive_name)
        except FileNotFoundError as exc:
            return JSONResponse(
                status_code=404,
                content={
                    "ok": False,
                    "error": {
                        "type": "NotFound",
                        "message": f"Artifact root not found: {exc}",
                    },
                },
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": {
                        "type": "ValueError",
                        "message": str(exc),
                    },
                },
            )

    @router.post("/artifacts/resume")
    def resume_artifact(request: ResumeArtifactRequest):
        try:
            artifact_root = _resolve_artifact_root(request.source, request.name)
            run_meta = _read_json(artifact_root / "run_meta.json", {})
            if not isinstance(run_meta, dict) or not run_meta:
                raise ValueError("Artifact does not contain run_meta.json")
            scenario_id = str(run_meta.get("scenario_id") or "")
            last_complete_round = _last_sealed_round_from_artifact(
                artifact_root,
                run_meta,
            )
            if last_complete_round < 0:
                raise ValueError("Artifact does not contain a sealed end-of-turn checkpoint")
            return _json_response(
                operations_api.import_resumable_experiment(
                    scenario_id=scenario_id,
                    artifact_root=str(artifact_root),
                    run_meta=run_meta,
                    last_complete_round=last_complete_round,
                    target_total_steps=request.target_total_steps,
                    requested_by=request.requested_by,
                )
            )
        except FileNotFoundError as exc:
            return JSONResponse(
                status_code=404,
                content={
                    "ok": False,
                    "error": {"type": "NotFound", "message": str(exc)},
                },
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": {"type": "ValueError", "message": str(exc)},
                },
            )

    @router.delete("/jobs/{job_id}")
    def delete_job(job_id: str, scenario_id: Optional[str] = None, force: bool = True):
        return _json_response(
            operations_api.delete_job(job_id, scenario_id=scenario_id, force=force)
        )

    @router.post("/jobs/{job_id}/delete")
    def delete_job_via_post(job_id: str, request: DeleteExperimentRequest = None):
        request = request or DeleteExperimentRequest()
        return _json_response(
            operations_api.delete_job(
                job_id,
                scenario_id=request.scenario_id,
                force=request.force,
            )
        )

    @router.post("/jobs")
    def create_job(request: CreateExperimentRequest):
        return _json_response(
            operations_api.create_experiment(
                scenario_id=request.scenario_id,
                planned_total_steps=request.planned_total_steps,
                tags=request.tags,
                requested_by=request.requested_by,
                job_id=request.job_id,
                config_overrides=request.config_overrides,
            )
        )

    @router.post("/jobs/{job_id}/start")
    def start_job(job_id: str, request: StartExperimentRequest = None):
        request = request or StartExperimentRequest()
        return _json_response(
            operations_api.start_experiment(
                job_id,
                scenario_id=request.scenario_id,
                run_id=request.run_id,
                worker_id=request.worker_id,
                artifact_root=request.artifact_root,
            )
        )

    @router.post("/jobs/{job_id}/stop")
    def stop_job(job_id: str, request: StopExperimentRequest):
        return _json_response(
            operations_api.request_stop(
                job_id,
                scenario_id=request.scenario_id,
                reason=request.reason,
                requested_by=request.requested_by,
            )
        )

    @router.post("/jobs/{job_id}/resume")
    def resume_job(job_id: str, request: ResumeExperimentRequest = None):
        request = request or ResumeExperimentRequest()
        return _json_response(
            operations_api.resume_experiment(
                job_id,
                scenario_id=request.scenario_id,
                target_total_steps=request.target_total_steps,
                requested_by=request.requested_by,
            )
        )

    @router.post("/jobs/{job_id}/checkpoint")
    def checkpoint_job(job_id: str, request: CheckpointRequest):
        return _json_response(
            operations_api.record_checkpoint(
                job_id,
                scenario_id=request.scenario_id,
                completed_steps=request.completed_steps,
                last_complete_round=request.last_complete_round,
                checkpoint_uri=request.checkpoint_uri,
                artifact_root=request.artifact_root,
                details=request.details,
            )
        )

    @router.post("/jobs/{job_id}/run")
    def run_job(job_id: str, request: RunJobRequest = None):
        request = request or RunJobRequest()
        return _json_response(
            operations_api.run_job(
                job_id,
                scenario_id=request.scenario_id,
                worker_id=request.worker_id,
                client_dir=request.client_dir,
                workspace_dir=request.workspace_dir,
            )
        )

    @router.get("/scenarios")
    def list_scenarios():
        nonlocal scenario_payload_cache
        if scenario_payload_cache is None:
            scenario_payload_cache = _scenario_payload(load_scenarios())
        return JSONResponse(
            status_code=200,
            content={"ok": True, "data": scenario_payload_cache},
        )

    return router
