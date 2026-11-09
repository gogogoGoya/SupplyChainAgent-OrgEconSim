"""Control-channel lifetime and diagnostics for opt-in firm architecture runs."""

import asyncio
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform

EXECUTION_ADAPTER_VERSION = "firm_runtime_2026_10_21_v5"


class AttemptInputStream:
    """Keep CLI stdin available for hooks/permissions until this attempt ends.

    Ending a one-message generator hands stdin lifetime back to SDK defaults,
    which differ across deployed SDK versions. Do not exhaust it mid-turn.
    """

    def __init__(self, prompt, on_event=None):
        self.prompt = prompt
        self.on_event = on_event or (lambda *args, **kwargs: None)
        self.released = asyncio.Event()
        self.closed = asyncio.Event()
        self.reason = None

    def release(self, reason):
        if not self.released.is_set():
            self.reason = reason
            self.on_event("input_stream_release_requested", reason=reason)
            self.released.set()

    async def messages(self):
        self.on_event("input_stream_opened")
        try:
            yield {"type": "user", "message": {"role": "user", "content": self.prompt},
                   "parent_tool_use_id": None, "session_id": ""}
            self.on_event("input_message_sent")
            await self.released.wait()
        finally:
            self.on_event("input_stream_closed", reason=self.reason or "cancelled")
            self.closed.set()

    async def finish(self, stream):
        """SDK owns iteration; never aclose a generator it is still reading."""
        self.release("attempt_cleanup")
        if stream.ag_running:
            try:
                await asyncio.wait_for(self.closed.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.on_event("input_stream_cleanup_incomplete")
                return
        await stream.aclose()


class ToolControlChannelError(RuntimeError):
    def __init__(self, tool_use_id, message):
        self.tool_use_id = tool_use_id
        self.message = message
        super().__init__(f"tool_control_channel_closed: {message}")

    def to_dict(self):
        return {"code": "tool_control_channel_closed", "tool_use_id": self.tool_use_id, "message": self.message}


def tool_control_failure(message):
    """Inspect actual failed SDK tool receipts, never arbitrary model prose."""
    from claude_agent_sdk import ToolResultBlock
    blocks = getattr(message, "content", None)
    if not isinstance(blocks, list):
        return None
    for block in blocks:
        if not isinstance(block, ToolResultBlock) or not block.is_error:
            continue
        text = block.content
        if isinstance(text, list):
            text = "\n".join(b.get("text", "") for b in text if isinstance(b, dict))
        text = str(text or "")
        lowered = text.lower()
        if "tool permission request failed" in lowered and ("stream closed" in lowered or "stream is closed" in lowered):
            return ToolControlChannelError(block.tool_use_id, text)
    return None


def runtime_diagnostics(options):
    import claude_agent_sdk
    try:
        sdk_version = version("claude-agent-sdk")
    except PackageNotFoundError:
        sdk_version = "unknown"
    package = Path(claude_agent_sdk.__file__).resolve().parent
    bundled = package / "_bundled" / ("claude.exe" if platform.system() == "Windows" else "claude")
    return {"execution_adapter_version": EXECUTION_ADAPTER_VERSION, "sdk_version": sdk_version,
            "sdk_package_path": str(package), "python_version": platform.python_version(),
            "requested_cli_path": str(options.cli_path) if getattr(options, "cli_path", None) else None,
            "sdk_bundled_cli_path": str(bundled) if bundled.is_file() else None,
            "model": getattr(options, "model", None), "permission_mode": options.permission_mode,
            "input_stream_policy": "keep_open_until_result_or_cleanup"}
