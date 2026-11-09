"""Execution source identity without credentials or caller-supplied claims."""
import hashlib
import os
import json
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError
from urllib.parse import urlsplit
from functools import lru_cache
from importlib.util import find_spec
from core.execution_boundary import EXECUTION_PROTOCOL


@lru_cache(maxsize=8)
def _binary_fingerprint(path, modified_ns, size):
    result=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):result.update(block)
    return result.hexdigest()


def capture_provenance(root, env):
    root = Path(root)
    sources = {}
    excluded = {"node_modules", "__pycache__", "workspace_jobs", "workspace_multi", "simulation_runs", "logs", "public_results", "runtime", "history"}
    folders = [root / name for name in ("core", "config", "enterprise", "network", "simulate", "runtime")]
    folders.append(root / "agent" if (root / "agent").is_dir() else root / "ccAgent/CCSDKAgent")
    for folder in folders:
        for directory, directories, names in os.walk(folder):
            directories[:] = [name for name in directories if name not in excluded]
            for name in names:
                path = Path(directory) / name
                relative = path.relative_to(root)
                if path.suffix == ".py" or (".claude" in relative.parts and path.suffix in {".json", ".md"}) or ("static_commands" in relative.parts and path.suffix == ".json"):
                    sources[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    route = urlsplit(env.get("ANTHROPIC_BASE_URL") or env.get("AGENT_GATEWAY_BASE_URL") or "")
    models = {k:env.get(k) for k in ("ANTHROPIC_MODEL", "ANTHROPIC_MODEL_LIST", "ANTHROPIC_CEO_MODEL") if env.get(k)}
    dependencies = {}
    for name in ("claude-agent-sdk", "fastapi", "pydantic"):
        try: dependencies[name] = version(name)
        except PackageNotFoundError: dependencies[name] = "unavailable"
    cli = {"identity": "not_available_before_launch"}
    package = find_spec("claude_agent_sdk")
    if package and package.origin:
        binary = Path(package.origin).parent / "_bundled" / ("claude.exe" if os.name == "nt" else "claude")
        if binary.is_file():
            stat=binary.stat()
            cli={"identity":"sdk_bundled_binary", "sha256":_binary_fingerprint(str(binary),stat.st_mtime_ns,stat.st_size)}
    return {
        "cli": cli,
        "dependencies": dependencies,
        "schema_version": "execution_provenance.v1", "execution_protocol": EXECUTION_PROTOCOL,
        "source_sha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        "files": sources, "configured_models": models,
        "configured_route": f"{route.scheme}://{route.hostname or ''}:{route.port or ''}{route.path}",
        "provider_model_identity": "not_verified_by_configuration", "rng_policy": "checkpointed_market_rng.v1",
    }


def assert_provenance(expected, actual):
    if not expected:
        return
    changed = [k for k in ("source_sha256", "configured_models", "configured_route", "execution_protocol", "dependencies", "cli") if expected.get(k) != actual.get(k)]
    if changed:
        raise RuntimeError("Execution baseline changed; batch paused before launch: " + ", ".join(changed))
