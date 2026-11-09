"""Files excluded from public artifact listings and downloadable archives."""
from pathlib import Path

PRIVATE_DIRECTORIES = {"_operations", "_process_logs", "_batches", "__pycache__", "node_modules"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pkl", ".pickle", ".pem", ".key", ".log"}


def is_public_artifact(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    parts = relative.parts
    return not (
        any(part.startswith(".") or part.lower() in PRIVATE_DIRECTORIES for part in parts)
        or path.suffix.lower() in PRIVATE_SUFFIXES
        or path.name.lower().endswith((".private.json", ".agent_env.json"))
        or ".sqlite-" in path.name.lower()
    )
