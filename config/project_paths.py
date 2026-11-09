"""Portable project and runtime path resolution helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union


PathLike = Union[str, os.PathLike]
PROJECT_ROOT_ENV = "SUPPLY_CHAIN_PROJECT_ROOT"
AGENT_ROOT_ENV = "SUPPLY_CHAIN_AGENT_ROOT"

_SOURCE_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _configured_root(env_name: str, default: Path, *, relative_to: Path) -> Path:
    raw_value = str(os.getenv(env_name) or "").strip()
    if not raw_value:
        return default
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = relative_to / path
    resolved = path.resolve(strict=False)
    # A copied .env may still contain the previous host's absolute root. A
    # project root that does not exist cannot be useful at runtime, so derive
    # it from this checkout instead.
    return resolved if resolved.is_dir() else default.resolve(strict=False)


PROJECT_ROOT = _configured_root(
    PROJECT_ROOT_ENV,
    _SOURCE_PROJECT_ROOT,
    relative_to=_SOURCE_PROJECT_ROOT,
)
AGENT_ROOT = _configured_root(
    AGENT_ROOT_ENV,
    PROJECT_ROOT / "agent",
    relative_to=PROJECT_ROOT,
)
WORKSPACE_JOBS_ROOT = AGENT_ROOT / "workspace_jobs"
WORKSPACE_MULTI_ROOT = AGENT_ROOT / "workspace_multi"
SIMULATION_RUNS_ROOT = AGENT_ROOT / "simulation_runs"


def resolve_project_path(
    value: Optional[PathLike],
    *,
    default: Optional[PathLike] = None,
    relative_to: Optional[PathLike] = None,
    rebase_legacy_absolute: bool = True,
) -> Optional[Path]:
    """Resolve a portable path and rebase stale paths copied from another host."""
    raw_value = str(value or "").strip()
    if not raw_value:
        return Path(default).expanduser().resolve(strict=False) if default else None

    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        base = Path(relative_to or PROJECT_ROOT).expanduser()
        return (base / path).resolve(strict=False)
    if path.exists() or not rebase_legacy_absolute:
        return path.resolve(strict=False)

    parts = path.parts
    project_markers = {PROJECT_ROOT.name, "SupplyChainAgent"}
    for index in range(len(parts) - 1):
        if parts[index] in project_markers and parts[index + 1] == "agent":
            return AGENT_ROOT.joinpath(*parts[index + 2:]).resolve(strict=False)

    for marker in (PROJECT_ROOT.name, "SupplyChainAgent"):
        if marker not in parts:
            continue
        index = parts.index(marker)
        return PROJECT_ROOT.joinpath(*parts[index + 1:]).resolve(strict=False)
    return path.resolve(strict=False)


def project_relative_path(value: PathLike) -> str:
    """Return a project-relative display path when possible."""
    path = Path(value).expanduser().resolve(strict=False)
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)

# Public copies and mutable runtime files have explicit deployment roots.
PUBLIC_RESULTS_ROOT = Path(os.getenv("SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT", str(AGENT_ROOT / "public_results"))).expanduser().resolve()
RUNTIME_ROOT = Path(os.getenv("SUPPLY_CHAIN_RUNTIME_ROOT", str(AGENT_ROOT / "runtime"))).expanduser().resolve()
