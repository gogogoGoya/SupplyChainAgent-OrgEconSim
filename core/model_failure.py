"""Stop simulations on permanent model-service failures, with durable diagnostics."""

import asyncio
from datetime import datetime
from functools import wraps
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import uuid
from zoneinfo import ZoneInfo


class FatalModelError(RuntimeError):
    def __init__(self, code, explanation):
        self.code = code
        self.explanation = explanation
        self.context = {}
        super().__init__(f"{code}: {explanation}")

    def to_dict(self):
        return {"code": self.code, "message": self.explanation, **self.context}

    def __str__(self):
        location = self.context.get("credential_diagnostic_file")
        return super().__str__() + (f"; credential diagnostic: {location}" if location else "")


def classify_model_failure(text):
    """Classify error responses/exceptions, never arbitrary business observations."""
    text = str(text or "").lower()
    if re.search(r"invalid[_ -]api[_ -]key|incorrect api key|invalid x-api-key|authentication[_ -](?:error|failed)|"
                 r"(?:api error|http|status(?:_code)?)[\s:'\"=]*(?:401)\b", text):
        return FatalModelError("model_authentication_failed", "Model gateway/provider rejected authentication (401/invalid_api_key); verify gateway and upstream provider credentials.")
    if re.search(r"permission_error|access_denied|(?:api error|http|status(?:_code)?)[\s:'\"=]*403\b", text):
        return FatalModelError("model_permission_denied", "Model gateway/provider denied access (403); verify account and model permissions.")
    if re.search(r"model_not_found|invalid_model|unsupported model|model[^\n]{0,100}does not exist", text):
        return FatalModelError("model_configuration_invalid", "Configured model is unavailable or unsupported; verify the gateway model mapping.")
    # A provider can return permanent billing/quota errors under HTTP 429.
    # Do not classify ordinary per-minute limits or temporary quota windows here.
    if re.search(r"\binsufficient_quota\b|\bbilling_hard_limit_reached\b|"
                 r"\b(?:token[- ]plan\s+)?(?:1[- ]month|monthly)\s+quota\s+(?:has been|is)\s+exhausted\b", text):
        return FatalModelError("model_quota_exhausted", "Model provider reports exhausted account/monthly quota; restore service resources before starting a new simulation.")
    return None


def attach_model_failure(error, *, workspace, options, enterprise_id, role, round_id):
    """Write raw SDK credentials only to an owner-readable diagnostic outside run archives.

    Explicit diagnostic requested by the operator. SDK/gateway credentials are
    not claimed to be the router's separate upstream Provider credentials.
    """
    error.context.update(enterprise_id=enterprise_id, role=role, round_id=round_id)
    if error.code not in {"model_authentication_failed", "model_permission_denied"}:
        return error
    workspace = Path(workspace)
    try:
        meta = json.loads((workspace / "run_meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    option_env = getattr(options, "env", None) or {}
    effective = {**os.environ, **option_env}
    credentials = {key: effective.get(key) for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")}
    fingerprints = {key: hashlib.sha256(value.encode()).hexdigest()[:16]
                    for key, value in credentials.items() if value}
    error.context["sdk_credential_sha256_prefixes"] = fingerprints
    # workspace_jobs is exposed by the visualization's static file server.
    # Raw credentials must live outside every experiment/static artifact root.
    state_root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    log_dir = Path(os.environ.get("SIMULATION_PRIVATE_DIAGNOSTICS_DIR") or state_root / "supplychainagent" / "model_diagnostics")
    job_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(meta.get("job_id") or workspace.name))
    path = log_dir / f"{job_id}.model_auth_failure.{uuid.uuid4().hex[:8]}.private.json"
    payload = {
        "schema_version": "model_auth_private_diagnostic.v1",
        "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "job_id": meta.get("job_id"), "run_id": meta.get("run_id"),
        "error": error.to_dict(),
        "credential_scope": "SDK gateway credentials; not the router's upstream Provider key",
        "credential_source": {key: "ClaudeAgentOptions.env" if key in option_env else "process environment"
                              for key in credentials},
        "sdk_credentials": credentials,
        "sdk_gateway_base_url": effective.get("ANTHROPIC_BASE_URL"),
        "sdk_model": getattr(options, "model", None) or effective.get("ANTHROPIC_MODEL"),
        "upstream_provider_key": None,
        "upstream_provider_key_note": "Not available to this process. Inspect the active router Provider configuration when the upstream rejects its own key.",
    }
    try:
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
        error.context["credential_diagnostic_file"] = str(path)
    except OSError as exc:
        # An unwritable diagnostic must never turn a fatal error into a soft one.
        error.context["credential_diagnostic_error"] = type(exc).__name__
    return error


def check_model_message(message, **context):
    content = getattr(message, "content", None)
    text = "\n".join(str(getattr(block, "text", "")) for block in content) if isinstance(content, list) else str(content or "")
    result = str(getattr(message, "result", None) or "")
    error_tag = getattr(message, "error", None)
    # Error-tagged results can have subtype='success'. Synthetic API errors may
    # arrive before that result, with error=None on the AssistantMessage.
    is_error = (getattr(message, "is_error", False) is True or bool(error_tag)
                or getattr(message, "model", None) == "<synthetic>"
                or text.lstrip().startswith("API Error:"))
    if is_error:
        fatal = classify_model_failure(f"{error_tag or ''}\n{text}\n{result}")
        if fatal:
            raise attach_model_failure(fatal, **context)


async def gather_model_tasks(*tasks):
    """Cancel sibling enterprises/departments before propagating a fatal error."""
    tasks = [asyncio.ensure_future(task) for task in tasks]
    try:
        return await asyncio.gather(*tasks)
    except FatalModelError:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def abort_on_fatal_model_error(run):
    """Persist failed run/job status for both Operations and direct manager runs."""
    signature = inspect.signature(run)

    @wraps(run)
    async def wrapped(self, *args, **kwargs):
        bound = signature.bind(self, *args, **kwargs)
        progress = {}
        callback = bound.arguments.get("checkpoint_callback")

        def checkpoint(**details):
            progress.update(completed_steps=details["completed_steps"], last_complete_round=details["last_complete_round"])
            if callback is not None:
                return callback(**details)

        bound.arguments["checkpoint_callback"] = checkpoint
        try:
            return await run(*bound.args, **bound.kwargs)
        except FatalModelError as error:
            from core.firm_decision_protocol import read_json, write_json
            from persistence.sql_mirror_runtime import get_sql_mirror_repository

            workspace = Path(self.workspace_dir)
            meta = read_json(workspace / "run_meta.json", {})
            completed = int(progress.get("completed_steps", meta.get("completed_steps") or meta.get("prewarm_completed_steps") or 0))
            patch = {"status": "failed", "completed_steps": completed,
                     "last_complete_round": completed - 1 if completed else None,
                     "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                     "finish_reason": str(error), "model_failure": error.to_dict()}
            meta.update(patch)
            write_json(workspace / "run_meta.json", meta)
            write_json(workspace / "model_failure.json", patch)
            repo = bound.arguments.get("operations_repository") or get_sql_mirror_repository(meta.get("integration_profiles") or {})
            if repo is not None:
                if meta.get("job_id"):
                    repo.update_job(meta["job_id"], patch)
                if meta.get("run_id"):
                    repo.update_run(meta["run_id"], patch)
            raise

    return wrapped
