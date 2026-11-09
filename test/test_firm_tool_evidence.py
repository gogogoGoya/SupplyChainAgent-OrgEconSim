"""Receipt proof cannot be replaced by permission callbacks or model assertions."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk import ToolResultBlock, ToolUseBlock
from core.firm_tool_evidence import AttemptToolEvidence, readback_matches


def make_evidence(tmp_path):
    output = tmp_path / "attempt_1/decision.json"
    output.parent.mkdir()
    source = tmp_path / "input.json"
    source.write_text('{}')
    return AttemptToolEvidence(cwd=tmp_path, output=output, inputs=[source],
                               skill_root=tmp_path / "skills/operator", skill="enterprise_operator"), source


def call(proof, name, args, content="ok", error=False, call_id=None):
    call_id = call_id or f"call-{proof.sequence}"
    proof.consume(SimpleNamespace(content=[ToolUseBlock(call_id, name, args)]))
    proof.consume(SimpleNamespace(content=[ToolResultBlock(call_id, content, error)]))


def complete_inputs(proof, source):
    call(proof, "Skill", {"skill": "enterprise_operator"})
    call(proof, "Read", {"file_path": str(source)}, source.read_text())


def test_success_without_hooks_and_multiline_read_display(tmp_path):
    proof, source = make_evidence(tmp_path)
    complete_inputs(proof, source)
    blob = '{\n  "decision": true\n}'
    proof.output.write_text(blob)
    call(proof, "Write", {"file_path": str(proof.output), "content": blob})
    call(proof, "Read", {"file_path": str(proof.output)}, '  1→{\n  2→  "decision": true\n  3→}\n<system-reminder>display notice</system-reminder>')
    assert proof.errors([source]) == []


@pytest.mark.parametrize("failure", ["file_only", "read_before_write", "failed_write", "failed_read", "stale_read", "partial_read", "changed_file", "rewritten_after_read"])
def test_no_stale_or_failed_receipts_count(tmp_path, failure):
    proof, source = make_evidence(tmp_path)
    complete_inputs(proof, source)
    proof.output.write_text('{"a":1}')
    args = {"file_path": str(proof.output)}
    if failure == "read_before_write":
        call(proof, "Read", args, '{"a":1}')
    if failure != "file_only":
        call(proof, "Write", {**args, "content": '{"a":1}'}, error=failure == "failed_write")
    if failure not in {"file_only", "read_before_write"}:
        call(proof, "Read", args, '{"a":0}' if failure == "stale_read" else '{"a":' if failure == "partial_read" else '{"a":1}',
             error=failure == "failed_read")
    if failure == "changed_file":
        proof.output.write_text('{"a":2}')
    if failure == "rewritten_after_read":
        call(proof, "Write", {**args, "content": '{"a":1}'})
    assert any("Write followed by Read" in e for e in proof.errors([source]))


def test_denied_tools_never_establish_proof_and_executed_violation_is_rejected(tmp_path):
    proof, source = make_evidence(tmp_path)
    assert not proof.allowed("Write", {"file_path": str(tmp_path / "attempt_4/decision.json")})
    assert not proof.allowed("Write", {"file_path": str(source)})
    assert not proof.allowed("Skill", {"skill": "hr"})
    assert not proof.allowed("Bash", {"command": "ls"})
    call(proof, "Read", {"file_path": str(source)}, error=True)
    call(proof, "Skill", {"skill": "enterprise_operator"}, error=True)
    call(proof, "Write", {"file_path": str(tmp_path / "attempt_4/decision.json"), "content": "{}"})
    assert not proof.reads and not proof.skill_invoked
    assert any("Unauthorized tool executed" in e for e in proof.errors([source]))
    proof.consume(SimpleNamespace(content=json.dumps({"name": "Read", "file_path": str(source)})))
    assert not proof.reads


def test_readback_requires_whole_json():
    assert readback_matches([{"type": "text", "text": '1\t{"a":1}'}], '{"a":1}')
    assert not readback_matches('1\t{"a":', '{"a":1}')
    assert not readback_matches('{"a":true}', '{"a":1}')
    assert readback_matches('1\t{"a":1}\n2\t\n<system-reminder>SDK footer</system-reminder>', '{"a":1}\n')


def test_required_context_parts_need_full_successful_read(tmp_path):
    proof, source = make_evidence(tmp_path)
    source.write_text('{"staff":0,"cash":10}')
    proof.complete_reads.add(source.resolve())
    call(proof, "Read", {"file_path": str(source), "offset": 1, "limit": 1}, '1→{"staff":0,')
    assert str(source.resolve()) not in proof.reads
    call(proof, "Read", {"file_path": str(source)}, '1→{"staff":0,"cash":10}')
    assert str(source.resolve()) in proof.reads


def test_output_contract_supplies_full_schema_without_economic_choices():
    from agent.firm_decision_runner import FirmDecisionRunner
    runner = FirmDecisionRunner.__new__(FirmDecisionRunner)
    runner.enterprise_id, runner.enterprise = "E", "Enterprise"
    contract = runner._output_contract({"expected_functions": ["hr", "production"], "round_id": 10,
                                      "phase": "decision", "snapshot_id": "snapshot"})
    bundle = contract["bundle_shape"]
    assert bundle["schema_version"] == "firm_decision.v1"
    assert set(bundle["decisions"]) == {"hr", "production"}
    assert isinstance(bundle["analysis"]["enterprise_summarys"], str)
    assert set(bundle["analysis"]["department_targets"]["hr"]) == {"target", "evaluation", "reason"}
    assert bundle["decisions"]["hr"][0]["action"]["action_name"].startswith("<exact legal")


def test_context_packaging_failure_is_fatal_before_model_decisions(tmp_path, monkeypatch):
    import agent.firm_decision_runner as firm_decision_runner
    from core.model_failure import FatalModelError
    from agent.firm_decision_runner import FirmDecisionRunner
    def broken(path):
        raise ValueError("fixture hash mismatch")
    monkeypatch.setattr(firm_decision_runner, "pack_context", broken)
    runner = FirmDecisionRunner.__new__(FirmDecisionRunner)
    with pytest.raises(FatalModelError, match="agent_input_packaging_failed"):
        runner._pack_context(tmp_path / "input.json")


def _literal(node):
    """Decode only literal SDK dataclass repr nodes from a local archived log."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_literal(v) for v in node.elts]
    if isinstance(node, ast.Dict):
        return {_literal(k): _literal(v) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fields = {kw.arg: _literal(kw.value) for kw in node.keywords}
        if node.func.id == "ToolUseBlock":
            return ToolUseBlock(**fields)
        if node.func.id == "ToolResultBlock":
            return ToolResultBlock(**fields)
        return SimpleNamespace(**fields)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_literal(node.operand)
    raise ValueError(f"Unsupported archived literal: {type(node).__name__}")




def test_runner_schema_feedback_is_bounded_and_can_fix_same_attempt(tmp_path, monkeypatch):
    import agent.firm_decision_runner as firm_decision_runner
    from claude_agent_sdk import ClaudeAgentOptions
    from core.firm_decision_protocol import DecisionRecorder, read_json
    from agent.firm_decision_runner import FirmDecisionRunner

    runner = FirmDecisionRunner.__new__(FirmDecisionRunner)
    runner.workspace = tmp_path
    runner.enterprise_id = "E"
    runner.policy = {"max_attempts": 3, "attempt_timeout_seconds": 3}
    runner.runtime = SimpleNamespace(build_options=lambda role: ClaudeAgentOptions())
    root = tmp_path / "task"
    project = root / "agent_project"
    project.mkdir(parents=True)
    validator = lambda value: [] if isinstance(value, dict) and value.get("valid") is True else ["valid must equal true"]
    task = {"root": root, "project": project, "skill_root": project / "skills/operator", "skill": "enterprise_operator",
            "inputs": [], "output_name": "decision.json", "prompt": "fixture",
            "last_feedback": [], "validator": validator, "payload": None,
            "summary": {"role": "Enterprise", "attempts": 0, "timeout_attempts": 0, "recovered": False, "elapsed_seconds": 0},
            "recorder": DecisionRecorder(root, {"round_id": 0})}

    async def query(*, prompt, options):
        await anext(prompt)
        output = root / "attempt_1/decision.json"
        assert (await options.can_use_tool("Write", {"file_path": str(root / "attempt_4/decision.json")}, None)).behavior == "deny"
        stop = options.hooks["Stop"][0].hooks[0]
        assert (await stop({}, None, None))["decision"] == "block"
        assert (await stop({}, None, None))["decision"] == "block"
        assert await stop({}, None, None) == {}
        yield SimpleNamespace(content=[ToolUseBlock("skill", "Skill", {"skill": task["skill"]})])
        yield SimpleNamespace(content=[ToolResultBlock("skill", "ok", False)])
        for number, blob in enumerate(['{"valid":false}', '{"valid":true}']):
            write_id, read_id = f"write-{number}", f"read-{number}"
            args = {"file_path": str(output), "content": blob}
            yield SimpleNamespace(content=[ToolUseBlock(write_id, "Write", args)])
            output.write_text(blob)
            feedback = await options.hooks["PostToolUse"][0].hooks[0]({"tool_name": "Write", "tool_input": args}, write_id, None)
            text = feedback["hookSpecificOutput"]["additionalContext"]
            assert ("valid must equal true" in text) == (number == 0)
            yield SimpleNamespace(content=[ToolResultBlock(write_id, "created", False)])
            yield SimpleNamespace(content=[ToolUseBlock(read_id, "Read", {"file_path": str(output)})])
            yield SimpleNamespace(content=[ToolResultBlock(read_id, blob, False)])
        assert await stop({}, None, None) == {}

    monkeypatch.setattr(firm_decision_runner, "query", query)
    asyncio.run(runner._run_task(task))
    assert task["summary"]["success"] and task["summary"]["first_attempt_valid"]
    assert task["summary"]["attempts"] == 1
    assert len(list((root / "attempt_1").glob("candidate_*.raw"))) == 2
    assert read_json(root / "attempt_1/tool_evidence.json")["successful_writes"] == 2
    outcome = read_json(root / "attempt_1/outcome.json")
    assert outcome["schema_feedback_count"] == 1 and outcome["stop_correction_count"] == 2
    assert outcome["candidate_write_count"] == 2


@pytest.mark.parametrize("missing", ["skill", "input"])
def test_completion_repairs_only_missing_evidence_without_rewriting(tmp_path, monkeypatch, missing):
    """Reproduce herding C/R31's first Stop: valid output, missing Skill/read.

    The model follows the targeted feedback and completes the same attempt with
    one Write, avoiding an unnecessary rewrite and the CLI read-before-write loop.
    """
    import agent.firm_decision_runner as firm_decision_runner
    from claude_agent_sdk import ClaudeAgentOptions
    from core.firm_decision_protocol import DecisionRecorder, read_json
    from agent.firm_decision_runner import FirmDecisionRunner
    runner = FirmDecisionRunner.__new__(FirmDecisionRunner)
    runner.workspace, runner.enterprise_id = tmp_path, "E"
    runner.policy = {"max_attempts": 3, "attempt_timeout_seconds": 3}
    runner.runtime = SimpleNamespace(build_options=lambda role: ClaudeAgentOptions())
    root = tmp_path / "task"
    project = root / "agent_project"
    project.mkdir(parents=True)
    source = tmp_path / "facts.json"
    source.write_text('{"cash":0}')
    task = {"root": root, "project": project, "skill_root": project / "skills/operator", "skill": "enterprise_operator",
            "inputs": [source], "output_name": "decision.json", "prompt": "fixture", "last_feedback": [],
            "validator": lambda value: [] if value == {"valid": True} else ["valid must equal true"], "payload": None,
            "summary": {"role": "Enterprise", "attempts": 0, "timeout_attempts": 0, "recovered": False, "elapsed_seconds": 0},
            "recorder": DecisionRecorder(root, {"round_id": 0})}

    async def query(*, prompt, options):
        await anext(prompt)
        output = root / "attempt_1/decision.json"
        tools = [("skill", "Skill", {"skill": "enterprise_operator"}, "ok"),
                 ("input", "Read", {"file_path": str(source)}, source.read_text())]
        for name, tool, args, content in tools:
            if name != missing:
                yield SimpleNamespace(content=[ToolUseBlock(name, tool, args)])
                yield SimpleNamespace(content=[ToolResultBlock(name, content, False)])
        blob = '{"valid":true}'
        yield SimpleNamespace(content=[ToolUseBlock("write", "Write", {"file_path": str(output), "content": blob})])
        output.write_text(blob)
        yield SimpleNamespace(content=[ToolResultBlock("write", "created", False)])
        yield SimpleNamespace(content=[ToolUseBlock("read", "Read", {"file_path": str(output)})])
        yield SimpleNamespace(content=[ToolResultBlock("read", blob, False)])
        stop = options.hooks["Stop"][0].hooks[0]
        correction = await stop({}, None, None)
        assert correction["decision"] == "block"
        assert "Keep this file unchanged" in correction["reason"]
        for name, tool, args, content in tools:
            if name == missing:
                yield SimpleNamespace(content=[ToolUseBlock("repair", tool, args)])
                yield SimpleNamespace(content=[ToolResultBlock("repair", content, False)])
        assert await stop({}, None, None) == {}

    monkeypatch.setattr(firm_decision_runner, "query", query)
    asyncio.run(runner._run_task(task))
    assert task["summary"]["success"] and task["summary"]["attempts"] == 1
    assert read_json(root / "attempt_1/tool_evidence.json")["successful_writes"] == 1
    feedback = [json.loads(line) for line in (root / "events.jsonl").read_text().splitlines()
                if json.loads(line)["event_type"] == "stop_protocol_feedback"]
    assert feedback[0]["correction_actions"] == ["invoke_selected_skill" if missing == "skill" else "read_missing_inputs"]


def test_completion_readback_only_and_failed_rewrite_keep_strict_proof(tmp_path):
    from agent.firm_decision_runner import completion_feedback
    proof, source = make_evidence(tmp_path)
    complete_inputs(proof, source)
    blob = '{"valid":true}'
    proof.output.write_text(blob)
    call(proof, "Write", {"file_path": str(proof.output), "content": blob})
    task = {"inputs": [source], "skill": "enterprise_operator", "validator": lambda value: [] if value == {"valid": True} else ["schema invalid"]}
    errors, actions, _ = completion_feedback(task, proof.output, proof)
    assert errors and actions == ["read_current_output"]
    call(proof, "Read", {"file_path": str(proof.output)}, blob)
    assert completion_feedback(task, proof.output, proof)[0] == []
    call(proof, "Write", {"file_path": str(proof.output), "content": '{"valid":false}'}, error=True)
    errors, actions, feedback = completion_feedback(task, proof.output, proof)
    assert errors and actions == ["read_output_before_write", "write_current_output", "read_current_output"]
    assert "before another Write" in feedback
    # Merely reading the old candidate cannot certify the failed new intent.
    call(proof, "Read", {"file_path": str(proof.output)}, blob)
    assert completion_feedback(task, proof.output, proof)[0]
