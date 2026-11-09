"""Manifest-aware, bounded caching for read-only archive projections."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=512)
def _load(path, mtime_ns, size, expected_hash):
    data = Path(path).read_bytes()
    if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
        return None, "changed_since_publication"
    try:
        return json.loads(data), "available"
    except (ValueError, UnicodeError):
        return None, "failed_to_load"


class ReviewReader:
    def __init__(self, root):
        self.root = Path(root).resolve()
        manifest = self.root / "public_manifest.json"
        self.published = manifest.is_file()
        self.manifest = {}
        if self.published:
            stat = manifest.stat()
            self.manifest, status = _load(str(manifest), stat.st_mtime_ns, stat.st_size, None)
            if status != "available" or not isinstance(self.manifest, dict):
                raise ValueError("Publication manifest could not be read")
        self.states = {}

    def read(self, relative, default=None):
        relative = str(relative)
        path = self.root / relative
        try:
            path.resolve().relative_to(self.root)
            if path.is_symlink():
                raise ValueError("Linked archive member")
        except ValueError:
            self.states[relative] = "unavailable"
            return default
        entry = self.manifest.get("files", {}).get(relative)
        if self.published and not entry:
            self.states[relative] = "not_published"
            return default
        try:
            stat = path.stat()
            value, status = _load(str(path), stat.st_mtime_ns, stat.st_size,
                                  entry.get("sha256") if entry else None)
        except OSError:
            value, status = None, "missing"
        self.states[relative] = status
        return value if status == "available" else default

    def projection_status(self, key):
        relative = (self.manifest.get("review_projections") or {}).get(key)
        return self.states.get(relative, "not_recorded") if relative else "not_recorded"

    def projection(self, key):
        relative = (self.manifest.get("review_projections") or {}).get(key)
        return self.read(relative) if relative else None
