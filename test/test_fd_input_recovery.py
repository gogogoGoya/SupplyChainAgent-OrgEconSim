import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.fd_experiment import FD4, FD4_EVIDENCE_PROMPT_VERSION, resolve_fd_experiment
from config.simulation_preset_config import get_scenario_config
from agent.fd_experiment_runtime import configure_input_access, FD_INPUT_DENIAL_LIMIT, accessed_inputs
from agent.multi_tenant_utils import MultiTenantUtils
from agent.skill_runner import SkillRunner


def setup_case(tmp_path, role="Production", condition="full"):
    config = get_scenario_config(FD4)
    config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"] = {"condition": condition}
    config = resolve_fd_experiment(config, FD4, 20)
    if role == "Analyst":
        # Path/timeout recovery retains its v3 fixture; v4 has separate semantic tests.
        config["runtime_injection"]["single_enterprise_case_policy"]["fd_experiment"]["agent_prompt_policy_version"] = FD4_EVIDENCE_PROMPT_VERSION
    (tmp_path / "run_meta.json").write_text(json.dumps({"scenario_config": config}))
    enterprise = tmp_path / "enterprises" / "Manufacturer"
    local = enterprise / "department" / role.lower() / "day13"
    local.mkdir(parents=True)
    projection = tmp_path / "projections" / "fd_inputs" / "day13" / "Manufacturer" / role.lower()
    projection.mkdir(parents=True)
    spec = SimpleNamespace(
        role=role, skill_name=role.lower(), skill_args="--round_id 13 --enterprise_name Manufacturer",
        state_path=str(projection / f"{role.lower()}.json"),
        blackboard_path=str(projection / "blackboard.json"), analysis_path=str(enterprise / "analysis.json"),
        trade_decision_card_path=None, output_file_path=str(local / f"{role.lower()}_action.json"),
        output_file_name=f"{role.lower()}_action.json",
    )
    for path in [spec.state_path, spec.blackboard_path, spec.analysis_path]:
        Path(path).write_text("{}")
    return spec, enterprise, local / f"{role.lower()}.json"


@pytest.mark.parametrize("condition", ["full", "no_analyst", "no_shared_coordination"])
def test_denial_and_skill_context_list_only_supplied_paths(tmp_path, condition):
    spec, enterprise, wrong = setup_case(tmp_path, condition=condition)
    state = {}
    options = configure_input_access(SimpleNamespace(cwd=str(enterprise), hooks={}), spec,
                                     tmp_path, tmp_path, "Manufacturer", 13, state)
    guard = options.hooks["PreToolUse"][-1].hooks[0]
    result = asyncio.run(guard({"tool_name": "Read", "tool_input": {"file_path": str(wrong)}}, "bad", None))
    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert spec.state_path in output["permissionDecisionReason"]
    assert spec.blackboard_path in output["permissionDecisionReason"]
    assert state["failures"][0]["failure_type"] == "input_boundary"
    result = asyncio.run(options.hooks["PostToolUse"][-1].hooks[0](
        {"tool_name": "Skill", "tool_input": {"skill": spec.skill_name}}, "skill", None))
    assert spec.state_path in result["hookSpecificOutput"]["additionalContext"]
    assert "department/hr" not in result["hookSpecificOutput"]["additionalContext"]
    wrong_result = asyncio.run(options.hooks["PostToolUse"][-1].hooks[0](
        {"tool_name": "Skill", "tool_input": {"skill": "other"}}, "other", None))
    assert wrong_result == {}


def test_failed_read_before_guard_is_bounded_and_not_double_counted(tmp_path):
    spec, enterprise, wrong = setup_case(tmp_path)
    state = {}
    options = configure_input_access(SimpleNamespace(cwd=str(enterprise), hooks={}), spec,
                                     tmp_path, tmp_path, "Manufacturer", 13, state)
    failure = options.hooks["PostToolUseFailure"][-1].hooks[0]
    guard = options.hooks["PreToolUse"][-1].hooks[0]
    data = {"tool_name": "Read", "tool_input": {"file_path": str(wrong)}}
    asyncio.run(guard(data, "duplicate", None))
    asyncio.run(failure(data, "duplicate", None))
    assert len(state["failures"]) == 1
    for i in range(FD_INPUT_DENIAL_LIMIT - 1):
        asyncio.run(failure(data, str(i), None))
    assert state["loop_reason"]
    assert state["failures"][-1]["rejected_requests"] == FD_INPUT_DENIAL_LIMIT
    assert not Path(spec.output_file_path).exists()


def test_readback_and_distinct_valid_pages_do_not_trigger_loop(tmp_path):
    spec, enterprise, wrong = setup_case(tmp_path)
    Path(spec.output_file_path).write_text("[]")
    state = {}
    options = configure_input_access(SimpleNamespace(cwd=str(enterprise), hooks={}), spec,
                                     tmp_path, tmp_path, "Manufacturer", 13, state)
    guard = options.hooks["PreToolUse"][-1].hooks[0]
    for i in range(10):
        for path, args in [(spec.output_file_path, {}), (spec.state_path, {"offset": i, "limit": 1})]:
            result = asyncio.run(guard({"tool_name": "Read", "tool_input": {
                "file_path": path, **args}}, f"{path}-{i}", None))
            assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert not state.get("loop_reason")


def test_failed_authorized_read_is_not_accepted_as_input_evidence(tmp_path):
    spec, enterprise, wrong = setup_case(tmp_path)
    options = configure_input_access(SimpleNamespace(cwd=str(enterprise), hooks={}), spec,
                                     tmp_path, tmp_path, "Manufacturer", 13)
    data = {"tool_name": "Read", "tool_input": {"file_path": spec.state_path}}
    guard = options.hooks["PreToolUse"][-1].hooks[0]
    failure = options.hooks["PostToolUseFailure"][-1].hooks[0]
    asyncio.run(guard(data, "failed", None))
    asyncio.run(failure(data, "failed", None))
    assert spec.state_path not in accessed_inputs(tmp_path, "Manufacturer", "Production", 13)
    asyncio.run(guard(data, "successful", None))
    assert spec.state_path in accessed_inputs(tmp_path, "Manufacturer", "Production", 13)


def test_non_fd_options_and_existing_hooks_are_preserved(tmp_path):
    spec, enterprise, wrong = setup_case(tmp_path)
    sentinel = object()
    options = SimpleNamespace(cwd=str(enterprise), hooks={"PostToolUse": [sentinel]})
    configured = configure_input_access(options, spec, tmp_path, tmp_path, "Manufacturer", 13)
    assert configured.hooks["PostToolUse"][0] is sentinel
    (tmp_path / "run_meta.json").unlink()
    untouched = SimpleNamespace(cwd=str(enterprise), hooks={"PostToolUse": [sentinel]})
    state = {}
    assert configure_input_access(untouched, spec, tmp_path, tmp_path, "Manufacturer", 13, state) is untouched
    assert untouched.hooks == {"PostToolUse": [sentinel]}
    assert not state


@pytest.mark.parametrize("role", ["Production", "Analyst"])
@pytest.mark.parametrize("first_failure", ["loop", "timeout"])
@pytest.mark.parametrize("recover", [True, False])
def test_controller_corrects_retry_or_fails_without_substitution(tmp_path, monkeypatch, role, first_failure, recover):
    import agent.skill_runner as skill_runner
    spec, enterprise, wrong = setup_case(tmp_path, role)
    if role == "Analyst":
        spec.output_file_path = str(enterprise / "analysis.json")
        spec.output_file_name = "analysis.json"
        spec.observation_path = spec.state_path
        spec.chart_export_enabled = False
        spec.compact_observation_enabled = True
        spec.compact_observation_kind = ""
        spec.history_projection_path = str(enterprise / "history.json")
    runner = SkillRunner.__new__(SkillRunner)
    runner.workspace_dir = runner.client_dir = tmp_path
    runner.enterprise_spec = SimpleNamespace(enterprise_id="Manufacturer", enterprise_name="Manufacturer")
    runner.session_registry = SimpleNamespace(clear=lambda *args: None)
    runner.hybrid_replay_runner = SimpleNamespace(run_department=lambda *args, **kw: None,
                                                write_analysis=lambda *args: None)
    runner.scripted_rule_runner = SimpleNamespace(department_enabled=lambda role: False)
    runner.build_options = lambda role: SimpleNamespace(cwd=str(enterprise), hooks={})
    runner._build_skill_run_spec = lambda *args: spec
    runner._build_analyst_run_spec = lambda *args: spec
    runner._communication_enabled = lambda: False
    runner._build_context_audit = lambda *args: {}
    runner._strict_context_failure = lambda *args: None
    runner._build_launch_prompt = lambda *args: "Read supplied inputs."
    runner._build_analyst_launch_prompt = lambda *args: "Read supplied inputs."
    runner._skill_profile = lambda: {"max_retries": 2}
    runner._long_run_agent_timeout_seconds = lambda **kw: 120
    audits, executions, prompts, closed = [], [], [], []
    runner._write_skill_execution_audit = lambda **kw: audits.append(kw)
    monkeypatch.setattr(MultiTenantUtils, "file_check_department", lambda *args: Path(spec.output_file_path).exists())
    monkeypatch.setattr(MultiTenantUtils, "execute_enterprise_dept_action",
                        lambda **kw: executions.append(kw) or {"status": "success"})

    async def fake_query(*, prompt, options):
        messages = [m async for m in prompt]
        prompts.append(messages[0]["message"]["content"])
        index = len(prompts)
        if index == 2:
            assert spec.state_path in prompts[-1]
            assert str(wrong) in prompts[-1]
            assert "Previous attempt input-access failures" in prompts[-1]
            assert closed == [1]
        try:
            guard = options.hooks["PreToolUse"][-1].hooks[0]
            if index == 1 or not recover:
                for i in range(FD_INPUT_DENIAL_LIMIT if first_failure == "loop" else 1):
                    await guard({"tool_name": "Read", "tool_input": {
                        "file_path": str(wrong), "offset": i}}, f"bad-{i}", None)
                    yield SimpleNamespace(data={}, content="Rejected path")
                raise asyncio.TimeoutError
            assert not executions
            for path in [spec.state_path, spec.blackboard_path, spec.analysis_path]:
                await guard({"tool_name": "Read", "tool_input": {"file_path": path}}, path, None)
            if role == "Analyst":
                payload = {"enterprise_name": "Manufacturer", "round_id": 13,
                           "enterprise_summarys": "Inventory covers current demand.",
                           "department_targets": {d: {"target": "Check coverage", "evaluation": "Avoid excess",
                                                      "reason": "Current stock"}
                                                  for d in ["sales", "procurement", "production", "hr"]}}
            else:
                payload = [{"action": {"action_name": "action_pass", "action_param": "Inventory covers demand"},
                            "action_reason": "Current inventory covers demand", "module_type": "ProductionManager",
                            "executor_id": "Manufacturer"}]
            Path(spec.output_file_path).write_text(json.dumps(payload))
            for tool in ("Write", "Read"):
                for matcher in options.hooks["PostToolUse"]:
                    if matcher.matcher == "Write|Read":
                        await matcher.hooks[0]({"tool_name": tool,
                            "tool_input": {"file_path": spec.output_file_path}, "tool_response": {}}, tool, None)
            yield SimpleNamespace(data={}, content="Output ready")
        finally:
            closed.append(index)

    monkeypatch.setattr(skill_runner, "query", fake_query)
    dept = SimpleNamespace(role=role, dept_id=role.lower(), type="Agent", name=role, timeout=120)
    task = runner.run_analyst(13) if role == "Analyst" else runner.run_department(dept, 13)
    if recover:
        asyncio.run(task)
        assert audits[-1]["success"] is True
        assert len(executions) == (0 if role == "Analyst" else 1)
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(task)
        assert audits[-1]["success"] is False
        assert not executions
        assert not Path(spec.output_file_path).exists()
    assert audits[-1]["fallback_used"] is False
    assert closed == [1, 2]
    assert len(prompts) == 2
    if first_failure == "loop":
        assert "InputAccessLoop" in str(audits[-1]["all_messages"])
