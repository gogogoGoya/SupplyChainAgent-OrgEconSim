"""Attempt-local proof from SDK tool calls paired with execution receipts.

Text printed by the model and the mere existence of an output file are not proof.
Hooks enforce permissions and provide feedback; receipts establish execution.
"""

import hashlib
import json
from pathlib import Path
import re

EVIDENCE_VERSION = "firm_tool_receipts.v1"


class AttemptToolEvidence:
    def __init__(self, *, cwd, output, inputs, skill_root, skill, on_event=None, complete_reads=()):
        self.cwd = Path(cwd).resolve()
        self.output = Path(output).resolve()
        self.inputs = {Path(p).resolve() for p in inputs}
        self.skill_root = Path(skill_root).resolve()
        self.skill = skill
        self.complete_reads = {Path(p).resolve() for p in complete_reads}
        self.on_event = on_event or (lambda *args, **kwargs: None)
        self.pending = {}
        self.finished = set()
        self.sequence = 0
        self.reads = set()
        self.skill_invoked = False
        self.write_hash = None
        self.readback_hash = None
        self.write_content = None
        self.write_sequence = None
        self.writes = 0
        self.write_intents = 0
        self.violations = []

    def path(self, args):
        raw = args.get("file_path")
        if not isinstance(raw, str):
            return None
        return (self.cwd / raw).resolve()

    def allowed(self, tool, args):
        if tool == "Skill":
            return args.get("skill") == self.skill
        path = self.path(args)
        if tool == "Write":
            return path == self.output
        return tool == "Read" and path is not None and (
            path == self.output or path in self.inputs or path.is_relative_to(self.skill_root))

    def consume(self, message):
        blocks = getattr(message, "content", None)
        if not isinstance(blocks, list):
            return
        # Only SDK-deserialized blocks count, not JSON/text claims by the model.
        from claude_agent_sdk import ToolUseBlock, ToolResultBlock
        for block in blocks:
            self.sequence += 1
            if isinstance(block, ToolUseBlock):
                if block.id in self.pending or block.id in self.finished:
                    continue
                self.pending[block.id] = (block.name, block.input, self.sequence)
                if block.name == "Write" and self.path(block.input) == self.output:
                    self.write_intents += 1
                    self.write_hash = self.readback_hash = self.write_content = None
                self.on_event("tool_requested", tool_use_id=block.id, tool=block.name,
                              path=str(self.path(block.input)) if self.path(block.input) else None,
                              allowed=self.allowed(block.name, block.input))
            elif isinstance(block, ToolResultBlock):
                request = self.pending.pop(block.tool_use_id, None)
                if request is None:
                    continue
                self.finished.add(block.tool_use_id)
                tool, args, call_sequence = request
                allowed = self.allowed(tool, args)
                success = not block.is_error
                self.on_event("tool_receipt", tool_use_id=block.tool_use_id, tool=tool,
                              allowed=allowed, success=success)
                if not allowed:
                    if success:
                        self.violations.append(f"Unauthorized tool executed: {tool} {self.path(args) or args.get('skill', '')}")
                    continue
                if not success:
                    continue
                path = self.path(args)
                if tool == "Skill":
                    self.skill_invoked = True
                elif tool == "Write" and isinstance(args.get("content"), str):
                    self.write_content = args["content"]
                    self.write_hash = hashlib.sha256(self.write_content.encode()).hexdigest()
                    self.readback_hash = None
                    self.write_sequence = self.sequence
                    self.writes += 1
                    self.on_event("candidate_written", tool_use_id=block.tool_use_id,
                                  sha256=self.write_hash, bytes=len(self.write_content.encode()))
                elif tool == "Read":
                    if path not in self.complete_reads or (path.is_file() and readback_matches(block.content, path.read_text(encoding="utf-8"))):
                        self.reads.add(str(path))
                    else:
                        self.on_event("partial_context_read", tool_use_id=block.tool_use_id, path=str(path))
                    if path == self.output and self.write_content is not None and call_sequence > self.write_sequence:
                        if readback_matches(block.content, self.write_content):
                            self.readback_hash = self.write_hash
                            self.on_event("candidate_readback", tool_use_id=block.tool_use_id, sha256=self.readback_hash)

    def errors(self, required_inputs):
        errors = list(self.violations)
        if not self.skill_invoked:
            errors.append("Selected Skill was not invoked")
        missing = [str(Path(p).resolve()) for p in required_inputs if str(Path(p).resolve()) not in self.reads]
        if missing:
            errors.append("Required authorized reads missing: " + ", ".join(missing))
        actual = hashlib.sha256(self.output.read_bytes()).hexdigest() if self.output.is_file() else None
        if not actual or actual != self.write_hash or actual != self.readback_hash:
            errors.append("Current candidate requires successful Write followed by Read of the same content")
        return errors

    def summary(self):
        return {"version": EVIDENCE_VERSION, "successful_reads": sorted(self.reads),
                "skill_invoked": self.skill_invoked, "successful_writes": self.writes,
                "write_intent_count": self.write_intents,
                "complete_read_paths": sorted(str(p) for p in self.complete_reads),
                "write_sha256": self.write_hash, "readback_sha256": self.readback_hash,
                "unauthorized_executions": self.violations}


def readback_matches(content, written):
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str):
        return False
    # Claude Read prefixes lines with a line number and a tab/arrow. Ignore only
    # the SDK's trailing reminder, never truncate the candidate itself.
    content = content.split("<system-reminder>", 1)[0]
    content = "\n".join(re.sub(r"^\s*\d+(?:\t|→)", "", line) for line in content.splitlines())
    if content.strip() == written.strip():
        return True
    try:
        return json.dumps(json.loads(content), sort_keys=True, allow_nan=False) == json.dumps(
            json.loads(written), sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        return False
