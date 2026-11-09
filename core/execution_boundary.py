"""Serialized business execution and quarantine for indeterminate receipts.

Model generation may be concurrent. Economic mutation is ordered, and a timed
out thread is never treated as a cancelled or safely retryable transaction.
"""
import asyncio
import time
import os
import json
import hashlib
import logging
from pathlib import Path
import tempfile
import weakref
from threading import Event

EXECUTION_PROTOCOL = "ordered_business_commit.v1"
_pending = weakref.WeakKeyDictionary()


class ExecutionIndeterminateError(RuntimeError):
    pass


def _record_quarantine(controller):
    """Persist an operator-only receipt independently of a poisoned checkpoint."""
    issue = getattr(controller, "execution_quarantine", None)
    if not issue:
        return
    directory = os.getenv("SUPPLY_CHAIN_RUNTIME_ROOT")
    if not directory:
        return
    target = None
    try:
        session = getattr(controller, "execution_session_id", "standalone")
        identity = hashlib.sha256((os.getenv("SUPPLY_CHAIN_INSTANCE_ID", "") + ":" + session).encode()).hexdigest()[:24]
        folder = Path(directory) / "quarantine"
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / (identity + ".json")
        issue["diagnostic_path"] = str(destination)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as stream:
            target = Path(stream.name)
            json.dump({"session_id": session, **dict(issue)}, stream, default=str)
        target.replace(destination)
    except OSError:
        logging.getLogger(__name__).exception("Could not persist execution quarantine")
    finally:
        if target is not None and target.exists():
            target.unlink()


def assert_execution_ready(controller):
    issue = getattr(controller, "execution_quarantine", None)
    if issue:
        raise ExecutionIndeterminateError(
            "EXECUTION_INDETERMINATE: this session is quarantined; restore a sealed checkpoint into a fresh session"
        )


async def execute_business_action(controller, enterprise, action, department, tick):
    assert_execution_ready(controller)
    timeout = float(getattr(controller, "business_action_timeout_seconds", 30))
    finished = Event()
    def invoke():
        try:
            result = controller.action_executor._call_department_functions(enterprise, action, department, tick)
            if getattr(controller, "execution_quarantine", None):
                controller.execution_quarantine.update(receipt="late_result", late_result=result.to_dict() if hasattr(result,"to_dict") else result)
                _record_quarantine(controller)
            return result
        finally:
            finished.set()
    future = asyncio.create_task(asyncio.to_thread(invoke))
    _pending[controller] = {"task":future,"finished":finished}
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as error:
        controller.execution_quarantine = {
            "code": "EXECUTION_INDETERMINATE", "department": department,
            "action": action.get("action_name"), "recorded_at": time.time(),
            "protocol": EXECUTION_PROTOCOL, "receipt": "pending",
        }
        _record_quarantine(controller)
        def record_late_receipt(done):
            if done.cancelled():return
            try:
                result = done.result()
                controller.execution_quarantine["receipt"] = "late_result"
                controller.execution_quarantine["late_result"] = result.to_dict() if hasattr(result, "to_dict") else result
            except BaseException as exc:
                controller.execution_quarantine["receipt"] = "late_error"
                controller.execution_quarantine["late_error"] = type(exc).__name__
            _record_quarantine(controller)
        future.add_done_callback(record_late_receipt)
        raise ExecutionIndeterminateError(
            f"EXECUTION_INDETERMINATE: {department}/{action.get('action_name')} exceeded {timeout:g}s or was interrupted; session quarantined"
        ) from error
    finally:
        if finished.is_set():
            _pending.pop(controller, None)


def has_pending_action(controller):
    pending = _pending.get(controller)
    return bool(pending and not pending["finished"].is_set())
