"""Create an inspectable result copy without SDK diagnostics or host secrets."""
import hashlib
import json
import os
import math
from pathlib import Path
import re
import shutil
from functools import lru_cache

from core.public_artifacts import is_public_artifact

SENSITIVE_KEYS = {"api_key", "auth_token", "authorization", "password", "secret", "credentials",
                  "anthropic_api_key", "anthropic_auth_token", "supply_chain_operator_token",
                  "apikey", "access_token", "client_secret", "private_key", "secret_key"}
HOST_KEYS = {"artifact_root", "checkpoint_uri", "checkpoint_path", "process_log", "process_error_log",
             "process_agent_env_log", "process_pid", "supervisor_pid", "log_path", "operations_job"}


def publication_candidates(source):
    """Prune private trees before walking potentially large SDK archives."""
    source = Path(source)
    roots = {"enterprises", "public", "projections", "round_integrity"}
    single_files = {"run_meta.json", "firm_architecture_quality.json", "charts_data_export.json"}
    excluded = {"firm_agent", "sdk", "messages", "model_messages"}
    for folder, directories, names in os.walk(source, followlinks=False):
        parent = Path(folder)
        directories[:] = sorted(name for name in directories
                                if name not in excluded
                                and (parent != source or name in roots)
                                and not (parent / name).is_symlink()
                                and is_public_artifact(parent / name, source))
        for name in sorted(names):
            path = parent / name
            if path.is_symlink() or not path.is_file() or not is_public_artifact(path, source):
                continue
            if ((parent == source and name in single_files)
                    or (parent != source and path.suffix in {".json", ".txt"})):
                yield path


def sanitize_public(value):
    if isinstance(value, dict):
        return {k:sanitize_public(v) for k,v in value.items() if k.lower() not in SENSITIVE_KEYS | HOST_KEYS and not k.lower().endswith(("_api_key", "_auth_token", "_password"))}
    if isinstance(value, list):
        return [sanitize_public(v) for v in value]
    if isinstance(value, str):
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[redacted]", value)
        for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "SUPPLY_CHAIN_OPERATOR_TOKEN"):
            secret=os.getenv(key, "")
            if len(secret)>=8:
                text=text.replace(secret,"[redacted]")
        text=re.sub(r"(?i)\bBearer\s+[A-Za-z0-9_.~-]{12,}", "Bearer [redacted]", text)
        if text.startswith(("/home/", "/Users/", "/tmp/")) or re.match(r"^[A-Za-z]:[\\/]", text):
            return "[host path omitted]"
        if text.startswith(("https://", "http://")):
            from urllib.parse import urlsplit, urlunsplit
            try:
                url=urlsplit(text)
                text=urlunsplit((url.scheme,url.netloc.rsplit("@",1)[-1],url.path,"",""))
            except ValueError:
                return "[invalid URL omitted]"
        return text
    if isinstance(value, float) and not math.isfinite(value):return None
    return value


def publish_result(source, destination, *, display_inspection=None, with_review=True):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination == source or source in destination.parents or destination.exists():
        raise ValueError("Choose a new publication directory outside the source archive")
    meta = json.loads((source / "run_meta.json").read_text(encoding="utf-8"))
    temporary=destination.with_name(destination.name+".publishing")
    if temporary.exists():
        raise ValueError("A publication is already being prepared at this destination")
    temporary.mkdir(parents=True)
    files={}
    try:
        for path in publication_candidates(source):
            relative=path.relative_to(source)
            target=temporary/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            if path.suffix==".json":
                try:payload=sanitize_public(json.loads(path.read_text(encoding="utf-8")))
                except (ValueError,OSError):continue
                target.write_text(json.dumps(payload,ensure_ascii=False),encoding="utf-8")
            else:
                target.write_text(sanitize_public(path.read_text(encoding="utf-8")),encoding="utf-8")
            files[relative.as_posix()]={"sha256":hashlib.sha256(target.read_bytes()).hexdigest(),"bytes":target.stat().st_size}
        from core.run_integrity import inspect_run
        inspection=inspect_run(temporary,sanitize_public(meta))
        manifest={"inspection":inspection,"schema_version":"published_results.v1","run_id":meta.get("run_id"),"files":files,
                  "source_meta_sha256":hashlib.sha256((source/"run_meta.json").read_bytes()).hexdigest()}
        if display_inspection is not None:
            manifest["display_inspection"] = sanitize_public(display_inspection)
        (temporary/"public_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
        if with_review:
            import tempfile
            from scripts.enrich_result_review import prepare_review, install_review
            with tempfile.TemporaryDirectory(prefix="result-review-") as folder:
                prepared=Path(folder)/"prepared"
                prepare_review(source,temporary,prepared)
                manifest=install_review(prepared,temporary)
        temporary.replace(destination)
        return manifest
    except BaseException:
        shutil.rmtree(temporary)
        raise


@lru_cache(maxsize=128)
def _manifest_files(path, modified_ns, size):
    return json.loads(Path(path).read_text())["files"]


def published_member(path, base):
    path,base=Path(path),Path(base)
    if not is_public_artifact(path,base):return False
    if path==base:return True
    for parent in [path if path.is_dir() else path.parent,*path.parents]:
        try: parent.relative_to(base)
        except ValueError: break
        manifest=parent/"public_manifest.json"
        if manifest.is_file() and is_public_artifact(manifest,base):
            try:
                stat=manifest.stat()
                files=_manifest_files(str(manifest),stat.st_mtime_ns,stat.st_size)
            except (OSError,ValueError,KeyError):return False
            if path == parent:return True
            relative=path.relative_to(parent).as_posix()
            return relative in files or path==manifest or (path.is_dir() and any(p.startswith(relative+"/") for p in files))
    # A category directory may contain published runs, but never arbitrary raw files.
    return path.is_dir() and any(p.is_file() and is_public_artifact(p,base) for p in path.glob("*/public_manifest.json"))
