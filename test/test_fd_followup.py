import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from config.fd_experiment import FD2, FD4, FD4_EVIDENCE_PROMPT_VERSION, FD4_COMPLETION_PROMPT_VERSION, resolve_fd_experiment, policy_from_meta
from config.integration_profiles import resolve_integration_profiles
from config.simulation_preset_config import get_scenario_config
from agent.fd_experiment_runtime import (
    configure_input_access, materialize_coordination_input, write_neutral_analysis,
    accessed_inputs, input_access_prompt,
)
from agent.scripted_rule_runner import ScriptedRuleRunner
from runtime.operations.facade import OperationsFacade
from runtime.operations.http_router import _scenario_config_defaults


def resolved(scenario, condition):
    config = get_scenario_config(scenario)
    config['runtime_injection']['single_enterprise_case_policy']['fd_experiment'] = {'condition': condition}
    return resolve_fd_experiment(config, scenario, 20)


@pytest.mark.parametrize('scenario,condition', [
    (FD2, 'full'), (FD2 + '_scripted', 'rb_reference'),
    (FD4, 'full'), (FD4, 'no_analyst'), (FD4, 'no_shared_coordination'),
    (FD4 + '_scripted', 'rb_revised'),
])
def test_followup_contract_and_profiles(scenario, condition):
    original = get_scenario_config(scenario)
    config = resolved(scenario, condition)
    profiles = resolve_integration_profiles(config)
    policy = policy_from_meta({'scenario_config': config}, 10)
    assert policy['condition'] == condition
    assert not policy_from_meta({'scenario_config': config}, 9)
    assert config['single_enterprise_case']['fd_experiment'] == policy
    assert config['simulation']['max_department_concurrency'] == 1
    if scenario.removesuffix('_scripted') == FD4:
        from config.fd4_validation_design import fd4_instance
        assert config['runtime_injection']['single_enterprise_case_policy']['prewarm_execution_plan'] == fd4_instance(original)['runtime_injection']['single_enterprise_case_policy']['prewarm_execution_plan']
    else:
        assert config['runtime_injection']['single_enterprise_case_policy']['prewarm_execution_plan'] == original['runtime_injection']['single_enterprise_case_policy']['prewarm_execution_plan']
    assert profiles['capabilities']['coordination']['blackboard_enabled'] == (condition != 'no_shared_coordination')
    assert get_scenario_config(scenario) == original


def test_reject_mismatched_or_shortened_experiments():
    config = resolved(FD4, 'full')
    with pytest.raises(ValueError):
        resolve_fd_experiment(config, FD4, 0)
    with pytest.raises(ValueError):
        resolve_fd_experiment(config, FD4 + '_scripted', 20)
    config['integration_profiles']['simulation']['profile'] = 'multi_enterprise'
    with pytest.raises(ValueError):
        resolve_fd_experiment(config, FD4, 20)


def test_legacy_and_multifirm_untouched():
    for scenario in [FD2, FD4, 'beer_game', 'commons_mining_profit_objective']:
        config = get_scenario_config(scenario)
        assert resolve_fd_experiment(config, scenario) == config


def test_operations_freezes_condition_and_ui_options():
    class Repository:
        def save_version(self, version):
            self.version = version
        def create_job(self, job):
            self.job = job
    repository = Repository()
    OperationsFacade(repository).create_experiment_from_scenario(
        scenario_id=FD4, planned_total_steps=20,
        config_overrides={'runtime_injection': {'single_enterprise_case_policy': {
            'fd_experiment': {'condition': 'no_shared_coordination'}}}},
    )
    assert repository.version['scenario_config']['runtime_injection']['single_enterprise_case_policy']['fd_experiment']['condition'] == 'no_shared_coordination'
    assert not repository.version['integration_profiles']['capabilities']['coordination']['blackboard_enabled']
    assert 'fd-condition:no_shared_coordination' in repository.job['tags']
    defaults = _scenario_config_defaults(get_scenario_config(FD4))
    assert [row['value'] for row in defaults['fd_experiment_options']] == ['full', 'no_analyst', 'no_shared_coordination']


def test_input_boundary_and_neutral_interface(tmp_path):
    config = resolved(FD4, 'no_shared_coordination')
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': config}))
    enterprise = tmp_path / 'enterprises' / 'Manufacturer'
    local = enterprise / 'department' / 'production' / 'day10'
    local.mkdir(parents=True)
    state = local / 'production.json'
    state.write_text('{}')
    bb = enterprise / 'department' / 'blackboard' / 'day10' / 'blackboard.json'
    projected = materialize_coordination_input(tmp_path, 'Manufacturer', 'production', 10, bb)
    assert json.loads(projected.read_text())['departments'] == {}
    write_neutral_analysis(enterprise, 'Manufacturer', 10, ['production', 'sales', 'procurement'])
    neutral = json.loads((enterprise / 'analysis.json').read_text())
    assert neutral['analysis_provenance']['model_generated'] is False
    spec = SimpleNamespace(role='Production', skill_name='production', state_path=state,
                           output_file_path=local / 'production_action.json', blackboard_path=projected,
                           analysis_path=enterprise / 'analysis.json')
    options = SimpleNamespace(cwd=str(enterprise), hooks={})
    configure_input_access(options, spec, tmp_path, tmp_path, 'Manufacturer', 10)
    guard = options.hooks['PreToolUse'][0].hooks[0]
    async def check(tool, path):
        output = await guard({'tool_name': tool, 'tool_input': {'file_path': str(path)}}, 'test', None)
        return output['hookSpecificOutput']['permissionDecision']
    assert asyncio.run(check('Read', state)) == 'allow'
    assert asyncio.run(check('Read', projected)) == 'allow'
    assert asyncio.run(check('Read', bb)) == 'deny'
    assert asyncio.run(check('Read', enterprise / 'department' / 'hr' / 'day10' / 'hr.json')) == 'deny'
    assert asyncio.run(check('Read', tmp_path / 'run_meta.json')) == 'deny'
    assert asyncio.run(check('Write', spec.output_file_path)) == 'allow'
    assert str(state.resolve()) in accessed_inputs(tmp_path, 'Manufacturer', 'Production', 10)
    for invalid in [{}, {'file_path': None}, {'file_path': ''}]:
        output = asyncio.run(guard({'tool_name': 'Read', 'tool_input': invalid}, 'invalid', None))
        assert output['hookSpecificOutput']['permissionDecision'] == 'deny'


@pytest.mark.parametrize('role', ['hr', 'production', 'sales', 'procurement'])
@pytest.mark.parametrize('condition', ['full', 'no_shared_coordination'])
def test_fd_retry_and_trade_feedback_remains_role_scoped(tmp_path, role, condition):
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': resolved(FD4, condition)}))
    enterprise = tmp_path / 'enterprises' / 'Manufacturer'
    local = enterprise / 'department' / role / 'day10'
    local.mkdir(parents=True)
    spec = SimpleNamespace(role=role, skill_name=role, output_file_path=str(local / f'{role}_action.json'))
    options = configure_input_access(SimpleNamespace(cwd=str(enterprise), hooks={}), spec,
                                     tmp_path, tmp_path, 'Manufacturer', 10)
    guard = options.hooks['PreToolUse'][-1].hooks[0]

    async def read(path):
        result = await guard({'tool_name': 'Read', 'tool_input': {'file_path': str(path)}}, 'test', None)
        return result['hookSpecificOutput']['permissionDecision']

    for filename in [f'{role}_error.json', f'{role}_error_1.json',
                     f'pre_{role}_action.json', f'pre_{role}_result.json']:
        assert asyncio.run(read(local / filename)) == 'allow'
    assert asyncio.run(read(enterprise / 'department' / 'finance' / 'day10' / 'pre_finance_result.json')) == 'deny'
    assert asyncio.run(read(local.parent / 'day11' / f'pre_{role}_result.json')) == 'deny'
    assert asyncio.run(read(local.parent / 'day9' / f'{role}_error_1.json')) == 'deny'


def test_fd_trade_rejects_stale_analysis_before_refresh_or_execution(tmp_path, monkeypatch):
    from agent.EnterpriseRuntime import EnterpriseRuntime
    from agent.multi_tenant_utils import MultiTenantUtils
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': resolved(FD4, 'full')}))
    runtime = EnterpriseRuntime.__new__(EnterpriseRuntime)
    runtime.workspace_dir = tmp_path
    runtime.enterprise_spec = SimpleNamespace(enterprise_name='Manufacturer', departments=[
        SimpleNamespace(enabled=True, need_trade=True)])
    calls = []
    monkeypatch.setattr(MultiTenantUtils, 'ensure_enterprise_analysis_file',
                        lambda *args, **kw: calls.append(kw) or False)
    def forbid(*args, **kwargs):
        raise AssertionError('Trade must stop before preparing or executing stale input')
    monkeypatch.setattr(MultiTenantUtils, 'handle_enterprises_observations', forbid)
    monkeypatch.setattr(MultiTenantUtils, 'save_enterprise_observations', forbid)
    with pytest.raises(FileNotFoundError, match='FD trade requires current analysis'):
        asyncio.run(runtime.run_trade(10))
    assert calls == [{'round_id': 10, 'require_round_id': 10, 'allow_archive_fallback': False}]


def test_fd_on_demand_analyst_cannot_reenable_archive_fallback(tmp_path, monkeypatch):
    from agent.EnterpriseRuntime import EnterpriseRuntime
    from agent.multi_tenant_utils import MultiTenantUtils
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': resolved(FD4, 'full')}))
    runtime = EnterpriseRuntime.__new__(EnterpriseRuntime)
    runtime.workspace_dir = tmp_path
    runtime.enterprise_spec = SimpleNamespace(enterprise_name='Manufacturer')
    runtime.scripted_rule_runner = SimpleNamespace(enabled=lambda: False)
    runtime._should_run_analyst = lambda day: False
    async def analyst(day):
        return []
    runtime.skill_runner = SimpleNamespace(run_analyst=analyst, cobweb_profit_objective_enabled=lambda: False)
    calls = []
    monkeypatch.setattr(MultiTenantUtils, 'ensure_enterprise_analysis_file',
                        lambda *args, **kw: calls.append(kw) or False)
    with pytest.raises(FileNotFoundError, match='analysis.json unavailable'):
        asyncio.run(runtime.run_workflow(10))
    assert len(calls) == 2
    assert all(call['require_round_id'] == 10 and not call['allow_archive_fallback'] for call in calls)


def test_no_analyst_workflow_never_calls_model_or_archive(tmp_path, monkeypatch):
    from agent.EnterpriseRuntime import EnterpriseRuntime
    from agent.multi_tenant_utils import MultiTenantUtils
    config = resolved(FD4, 'no_analyst')
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': config}))
    runtime = EnterpriseRuntime.__new__(EnterpriseRuntime)
    runtime.workspace_dir = tmp_path
    runtime.enterprise_spec = SimpleNamespace(enterprise_name='Manufacturer', departments=[
        SimpleNamespace(dept_id=role, enabled=True) for role in ['sales', 'procurement', 'production', 'hr']])
    async def forbid(day):
        raise AssertionError('Analyst must remain absent')
    runtime.skill_runner = SimpleNamespace(run_analyst=forbid, cobweb_profit_objective_enabled=lambda: False)
    runtime.scripted_rule_runner = SimpleNamespace(enabled=lambda: False)
    async def departments(day):
        return []
    runtime.handle_department_action = departments
    def ready(name, **kwargs):
        assert kwargs['require_round_id'] == 10
        assert kwargs['allow_archive_fallback'] is False
        return True
    monkeypatch.setattr(MultiTenantUtils, 'ensure_enterprise_analysis_file', ready)
    monkeypatch.setattr(MultiTenantUtils, 'handle_enterprises_observations', lambda **kwargs: None)
    monkeypatch.setattr(MultiTenantUtils, 'save_model_messages', lambda **kwargs: None)
    asyncio.run(runtime.run_workflow(10))
    analysis = json.loads((tmp_path / 'enterprises/Manufacturer/analysis.json').read_text())
    assert analysis['round_id'] == 10
    assert analysis['analysis_provenance']['source'] == 'neutral_interface'


def test_stale_fd_analysis_rejected(tmp_path):
    from agent.skill_runner import SkillRunner
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': resolved(FD4, 'full')}))
    runner = SkillRunner.__new__(SkillRunner)
    runner.workspace_dir = tmp_path
    runner.enterprise_spec = SimpleNamespace(enterprise_name='Manufacturer')
    runner._active_analyst_round_id = 11
    assert not runner._is_valid_analysis_payload({'enterprise_name': 'Manufacturer', 'round_id': 10})
    assert 'round_id' in runner._last_analysis_validation_error


def test_fd_hook_prompt_is_streaming_but_legacy_prompt_is_unchanged(tmp_path):
    assert input_access_prompt('hello', tmp_path, 10) == 'hello'
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': resolved(FD4, 'full')}))
    assert input_access_prompt('hello', tmp_path, 9) == 'hello'

    async def collect():
        return [message async for message in input_access_prompt('hello', tmp_path, 10)]

    messages = asyncio.run(collect())
    assert len(messages) == 1
    assert messages[0]['type'] == 'user'
    assert messages[0]['message'] == {'role': 'user', 'content': 'hello'}


def test_fd_hooks_register_through_sdk_control_protocol(tmp_path):
    from claude_agent_sdk import ClaudeAgentOptions, query
    from claude_agent_sdk._internal.transport import Transport

    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': resolved(FD4, 'full')}))
    state = tmp_path / 'hr.json'
    state.write_text('{}')
    spec = SimpleNamespace(role='HR', skill_name='hr', state_path=str(state),
                           output_file_path=str(tmp_path / 'hr_action.json'))
    options = configure_input_access(ClaudeAgentOptions(cwd=str(tmp_path)), spec,
                                     tmp_path, tmp_path, 'Manufacturer', 10)

    class HookTransport(Transport):
        def __init__(self):
            self.queue = asyncio.Queue()
            self.callback_id = None
            self.decision = None

        async def connect(self):
            pass

        async def write(self, data):
            message = json.loads(data)
            if message['type'] == 'control_request':
                hooks = message['request']['hooks']['PreToolUse']
                assert hooks[0]['matcher'] == '.*'
                self.callback_id = hooks[0]['hookCallbackIds'][0]
                await self.queue.put({'type': 'control_response', 'response': {
                    'subtype': 'success', 'request_id': message['request_id'], 'response': {}}})
            elif message['type'] == 'user':
                assert self.callback_id
                await self.queue.put({'type': 'control_request', 'request_id': 'read-hook',
                                      'request': {'subtype': 'hook_callback', 'callback_id': self.callback_id,
                                                  'tool_use_id': 'read-hr', 'input': {
                                                      'tool_name': 'Read', 'tool_input': {'file_path': str(state)}}}})
            elif message['type'] == 'control_response':
                self.decision = message['response']['response']['hookSpecificOutput']['permissionDecision']
                await self.queue.put({'type': 'result', 'subtype': 'success', 'duration_ms': 0,
                                      'duration_api_ms': 0, 'is_error': False, 'num_turns': 1,
                                      'session_id': 'offline-test'})
                await self.queue.put(None)

        async def read_messages(self):
            while True:
                message = await self.queue.get()
                if message is None:
                    return
                yield message

        async def close(self):
            pass

        def is_ready(self):
            return True

        async def end_input(self):
            pass

    async def run():
        transport = HookTransport()
        messages = [item async for item in query(
            prompt=input_access_prompt('Read HR state.', tmp_path, 10), options=options,
            transport=transport)]
        assert len(messages) == 1
        assert transport.decision == 'allow'

    asyncio.run(asyncio.wait_for(run(), timeout=5))
    assert str(state.resolve()) in accessed_inputs(tmp_path, 'Manufacturer', 'HR', 10)


@pytest.mark.parametrize('hooks_active', [True, False])
@pytest.mark.parametrize('legacy_wrapper', [False, True])
@pytest.mark.parametrize('role,module', [('HR', 'HRManager'), ('Production', 'ProductionManager'),
                                         ('Sales', 'SalesManager'), ('Procurement', 'ProcurementManager')])
def test_fd_department_execution_requires_hook_evidence(tmp_path, monkeypatch, hooks_active, legacy_wrapper, role, module):
    from pathlib import Path
    import agent.skill_runner as skill_runner
    from agent.multi_tenant_utils import MultiTenantUtils

    config = resolved(FD4, 'full')
    config['runtime_injection']['single_enterprise_case_policy']['fd_experiment']['agent_prompt_policy_version'] = FD4_COMPLETION_PROMPT_VERSION
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': config}))
    enterprise = tmp_path / 'enterprises' / 'Manufacturer'
    department = role.lower()
    local = enterprise / 'department' / department / 'day10'
    local.mkdir(parents=True)
    spec = SimpleNamespace(
        role=role, skill_name=department, skill_args='--round_id 10 --enterprise_name Manufacturer',
        state_path=str(local / f'{department}.json'), blackboard_path=str(local / 'blackboard.json'),
        analysis_path=str(enterprise / 'analysis.json'), trade_decision_card_path=None,
        output_file_path=str(local / f'{department}_action.json'), output_file_name=f'{department}_action.json',
    )
    for path in [spec.state_path, spec.blackboard_path, spec.analysis_path]:
        Path(path).write_text('{}')
    runner = skill_runner.SkillRunner.__new__(skill_runner.SkillRunner)
    runner.workspace_dir = tmp_path
    runner.client_dir = tmp_path
    runner.enterprise_spec = SimpleNamespace(enterprise_id='Manufacturer', enterprise_name='Manufacturer')
    runner.hybrid_replay_runner = SimpleNamespace(run_department=lambda *a, **kw: None)
    runner.scripted_rule_runner = SimpleNamespace(department_enabled=lambda role: False)
    runner.build_options = lambda role: SimpleNamespace(cwd=str(enterprise), hooks={})
    runner._build_skill_run_spec = lambda *args: spec
    runner._communication_enabled = lambda: False
    runner._build_context_audit = lambda *args: {}
    runner._strict_context_failure = lambda audit: None
    runner._build_launch_prompt = lambda *args: 'Read inputs and write the HR decision.'
    runner._build_same_session_resume_prompt = lambda *args, **kw: 'Repair output.'
    runner._skill_profile = lambda: {'max_retries': 1}
    audits = []
    runner._write_skill_execution_audit = lambda **kw: audits.append(kw)
    executions = []
    monkeypatch.setattr(MultiTenantUtils, 'file_check_department', lambda *args: Path(spec.output_file_path).exists())
    monkeypatch.setattr(MultiTenantUtils, 'execute_enterprise_dept_action',
                        lambda **kw: executions.append(kw) or {'status': 'success'})

    async def fake_query(*, prompt, options):
        # Older SDKs register callbacks only for an AsyncIterable input.
        assert not isinstance(prompt, str)
        assert [item async for item in prompt][0]['message']['content']
        matcher = options.hooks['PreToolUse'][-1]
        assert matcher.matcher == '.*'
        if hooks_active:
            for path in [spec.state_path, spec.blackboard_path, spec.analysis_path]:
                result = await matcher.hooks[0](
                    {'tool_name': 'Read', 'tool_input': {'file_path': path}}, path, None)
                assert result['hookSpecificOutput']['permissionDecision'] == 'allow'
        payload = [{
            'action': {'action_name': 'action_pass', 'action_param': 'No staffing change needed.'},
            'action_reason': 'Idle staff covers current demand.',
            'module_type': module, 'executor_id': 'Manufacturer',
        }]
        Path(spec.output_file_path).write_text(json.dumps([payload] if legacy_wrapper else payload))
        yield SimpleNamespace(data={}, content='Agent output is ready.')

    monkeypatch.setattr(skill_runner, 'query', fake_query)
    dept = SimpleNamespace(role=role, dept_id=department, type='Agent', name=role, timeout=120)
    if hooks_active:
        asyncio.run(runner.run_department(dept, 10))
        assert len(executions) == 1
        assert audits[-1]['success'] is True
        assert audits[-1]['fallback_used'] is False
        assert json.loads(Path(spec.output_file_path).read_text()) == payload_for_department(module)
    else:
        with pytest.raises(RuntimeError, match='no input access evidence was recorded'):
            asyncio.run(runner.run_department(dept, 10))
        assert not executions
        assert audits[-1]['fallback_used'] is False
        assert 'Required authorized reads missing' in audits[-1]['all_messages'][-1]['content']
        assert 'hook initialization' in audits[-1]['execution_result']['reason']


def payload_for_department(module):
    return [{
        'action': {'action_name': 'action_pass', 'action_param': 'No staffing change needed.'},
        'action_reason': 'Idle staff covers current demand.',
        'module_type': module, 'executor_id': 'Manufacturer',
    }]


def test_legacy_hr_wrapper_preserves_recruitment_and_rejects_ambiguous_shapes(tmp_path):
    from agent.multi_tenant_utils import MultiTenantUtils
    action = {'action': {'action_name': 'handle_recruitment',
                         'action_param': {'department': 'HR', 'num_people': 2}},
              'action_reason': 'Current state supports recruitment.',
              'module_type': 'HRManager', 'executor_id': 'Manufacturer'}
    path = tmp_path / 'hr_action.json'
    path.write_text(json.dumps([[action]]))
    assert not MultiTenantUtils._is_valid_action_payload([[action]], 'hr')
    assert not MultiTenantUtils._normalize_action_file_if_possible(path, 'hr')
    assert json.loads(path.read_text()) == [[action]]
    assert MultiTenantUtils._normalize_action_file_if_possible(path, 'hr', allow_legacy_wrapper=True)
    assert json.loads(path.read_text()) == [action]
    assert MultiTenantUtils._is_valid_action_file(path, 'hr')
    assert MultiTenantUtils._normalize_action_payload_shape(
        [[action, action]], allow_legacy_wrapper=True,
    ) == [action, action]
    for payload in [[[action], [action]], [[[action]]], [[]], [[action, 'invalid']],
                    [[{'action': {'action_name': 'handle_recruitment'}}]]]:
        path.write_text(json.dumps(payload))
        assert not MultiTenantUtils._normalize_action_file_if_possible(path, 'hr', allow_legacy_wrapper=True)
        assert json.loads(path.read_text()) == payload


@pytest.mark.parametrize('scenario,round_id,allowed', [
    (FD4, 10, True), (FD4, 9, False),
    ('beer_game', 10, False), ('commons_mining_profit_objective', 12, False),
])
def test_recovered_action_wrapper_is_fd_only(tmp_path, scenario, round_id, allowed):
    from agent.skill_runner import SkillRunner
    config = resolved(FD4, 'full') if scenario == FD4 else get_scenario_config(scenario)
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': config}))
    runner = SkillRunner.__new__(SkillRunner)
    runner.workspace_dir = tmp_path
    path = str(tmp_path / 'department' / 'hr' / f'day{round_id}' / 'hr_action.json')
    actions = payload_for_department('HRManager')
    assert runner._is_valid_recovered_output_payload([actions], path) is allowed
    assert runner._normalize_recovered_output_payload([actions], path) == (actions if allowed else [actions])
    assert runner._is_valid_recovered_output_payload(actions, path)
    invalid = deepcopy(actions)
    invalid[0]['action']['action_name'] = 'accept_order'
    assert not runner._is_valid_recovered_output_payload(invalid, path)
    assert not runner._is_valid_recovered_output_payload([invalid], path)


def test_skill_action_shape_contract_and_hr_examples():
    from pathlib import Path
    from agent.multi_tenant_utils import MultiTenantUtils
    root = Path(__file__).resolve().parents[1] / 'agent' / '.claude' / 'skills'
    for role in ['hr', 'procurement', 'sales', 'production', 'buyer', 'seller']:
        text = (root / role / 'SKILL.md').read_text()
        assert 'fixed length of 1' not in text
        assert '[ ACTIONS_ARRAY ]' not in text
    text = (root / 'hr' / 'SKILL.md').read_text()
    for section in ['## 8.1', '## 8.2']:
        example = text.split(section, 1)[1]
        example = example[example.index('['):]
        payload, _ = json.JSONDecoder().raw_decode(example)
        assert MultiTenantUtils._is_valid_action_payload(payload, 'hr')
        assert all(isinstance(item, dict) for item in payload)


def test_department_output_diagnostic_distinguishes_missing_invalid_json_and_shape(tmp_path):
    from agent.skill_runner import SkillRunner
    runner = SkillRunner.__new__(SkillRunner)
    path = tmp_path / 'hr_action.json'
    assert 'was not created' in runner._department_output_failure_reason(str(path), 'hr')
    path.write_text('[')
    assert 'JSONDecodeError' in runner._department_output_failure_reason(str(path), 'hr')
    path.write_text(json.dumps([[{}], [{}]]))
    assert 'item[0] must be an action object' in runner._department_output_failure_reason(str(path), 'hr')
    path.write_text(json.dumps([{}]))
    assert 'item[0] violates the hr action schema' in runner._department_output_failure_reason(str(path), 'hr')


@pytest.mark.parametrize('missing_input', [None, 'observation', 'charts', 'history'])
@pytest.mark.parametrize('timeout_after_write', [False, True])
def test_fd_analyst_checks_authorized_inputs_before_accepting_output(tmp_path, monkeypatch, missing_input, timeout_after_write):
    from pathlib import Path
    import agent.skill_runner as skill_runner
    runner = fd_analysis_runner(tmp_path)
    runner.client_dir = tmp_path
    runner.session_registry = SimpleNamespace(clear=lambda *args: None)
    runner.enterprise_spec = SimpleNamespace(enterprise_id='Manufacturer', enterprise_name='Manufacturer')
    spec = SimpleNamespace(
        role='Analyst', skill_name='analyst', skill_args='--round_id 10',
        output_file_path=str(tmp_path / 'analysis.json'), output_file_name='analysis.json',
        observation_path=str(tmp_path / 'observation.json'),
        chart_export_path=str(tmp_path / 'charts.json'), chart_export_enabled=True,
        history_projection_path=str(tmp_path / 'history.json'), compact_observation_enabled=False,
        compact_observation_kind='',
    )
    paths = {'observation': spec.observation_path, 'charts': spec.chart_export_path,
             'history': spec.history_projection_path}
    for path in paths.values():
        Path(path).write_text('{}')
    runner.hybrid_replay_runner = SimpleNamespace(write_analysis=lambda day: None)
    runner._build_analyst_run_spec = lambda day: spec
    runner._build_analyst_launch_prompt = lambda *args: 'Read authorized analyst inputs.'
    runner._build_analyst_same_session_resume_prompt = lambda *args: 'Repair analyst inputs.'
    runner._skill_profile = lambda: {'max_retries': 1}
    runner._long_run_agent_timeout_seconds = lambda **kw: 120
    runner.build_options = lambda role: SimpleNamespace(cwd=str(tmp_path), hooks={})
    audits = []
    runner._write_skill_execution_audit = lambda **kw: audits.append(kw)

    async def fake_query(*, prompt, options):
        assert [item async for item in prompt]
        for name, path in paths.items():
            if name != missing_input:
                await options.hooks['PreToolUse'][-1].hooks[0](
                    {'tool_name': 'Read', 'tool_input': {'file_path': path}}, name, None)
        Path(spec.output_file_path).write_text(json.dumps(valid_fd_analysis()))
        yield SimpleNamespace(data={}, content='Analyst output ready.')
        if timeout_after_write:
            raise asyncio.TimeoutError

    monkeypatch.setattr(skill_runner, 'query', fake_query)
    if missing_input is None:
        asyncio.run(runner.run_analyst(10))
        assert audits[-1]['success'] is True
    else:
        with pytest.raises(RuntimeError, match='Required authorized Analyst reads missing'):
            asyncio.run(runner.run_analyst(10))
        assert audits[-1]['success'] is False
        assert paths[missing_input] in runner._last_analysis_validation_error
    assert audits[-1]['fallback_used'] is False


def test_fd_compaction_preserves_all_open_records_and_source():
    from agent.fd_experiment_runtime import compact_fd_input
    payload = {'self_state': {
        'sales_orders': {'available': [{'order_id': i} for i in range(80)],
                         'accepted': [{'order_id': i} for i in range(70)],
                         'breached': [{'order_id': i} for i in range(50)],
                         'expired': [{'order_id': i} for i in range(50)],
                         'completed': [{'order_id': i} for i in range(50)]},
        'demand_backlog': {'by_product': {'P': 400},
                           'received_demand_history': list(range(100))},
        'proposals_list': list(range(80)), 'recruitment_status': {'pending': list(range(20))},
        'cash_guard': {'hard_blocked': True},
    }}
    original = deepcopy(payload)
    compact = compact_fd_input(payload)
    assert payload == original
    state = compact['self_state']
    for key in ['available', 'accepted', 'breached', 'expired']:
        assert state['sales_orders'][key] == payload['self_state']['sales_orders'][key]
    assert state['sales_orders']['completed'] == original['self_state']['sales_orders']['completed'][-4:]
    assert state['sales_orders']['completed_summary'] == {'total': 50, 'included': 4}
    assert state['demand_backlog']['received_demand_history'] == [96, 97, 98, 99]
    for key in ['proposals_list', 'recruitment_status', 'cash_guard']:
        assert state[key] == original['self_state'][key]


@pytest.mark.parametrize('condition', ['full', 'no_analyst', 'no_shared_coordination'])
def test_fd_projection_scope_and_read_boundary(tmp_path, condition):
    from pathlib import Path
    from agent.fd_experiment_runtime import materialize_fd_department_inputs
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': resolved(FD4, condition)}))
    root = tmp_path / 'enterprises' / 'Manufacturer'
    local = root / 'department' / 'sales' / 'day10'
    local.mkdir(parents=True)
    raw = local / 'sales.json'
    raw.write_text(json.dumps({'self_state': {'sales_orders': {'available': list(range(80))}}}))
    board = local / 'blackboard.json'
    board.write_text(json.dumps({'departments': {'hr': {'available': 2}},
                                'policy_context_by_department': {'sales': {'cash': 10}, 'hr': {'cash': 20}}}))
    original = raw.read_bytes(), board.read_bytes()
    state, shared = materialize_fd_department_inputs(tmp_path, 'Manufacturer', 'sales', 10, raw, board)
    assert (raw.read_bytes(), board.read_bytes()) == original
    assert json.loads(state.read_text())['self_state']['sales_orders']['available'] == list(range(80))
    assert json.loads(shared.read_text())['departments'] == {'hr': {'available': 2}}
    assert set(json.loads(shared.read_text())['policy_context_by_department']) == {'sales'}
    assert materialize_fd_department_inputs(tmp_path, 'Manufacturer', 'sales', 9, raw, board) == (raw, board)
    spec = SimpleNamespace(role='Sales', skill_name='sales', state_path=str(state),
                           blackboard_path=str(shared), output_file_path=str(local / 'sales_action.json'))
    options = configure_input_access(SimpleNamespace(cwd=str(root), hooks={}), spec, tmp_path, tmp_path, 'Manufacturer', 10)

    async def check():
        guard = options.hooks['PreToolUse'][-1].hooks[0]
        for path, allowed in [(state, True), (shared, True), (raw, False),
                              (local / 'sales_DayEnd.json', False), (local / 'sales_result.json', True)]:
            result = await guard({'tool_name': 'Read', 'tool_input': {'file_path': str(path)}}, str(path), None)
            assert result['hookSpecificOutput']['permissionDecision'] == ('allow' if allowed else 'deny')
    asyncio.run(check())
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': get_scenario_config('beer_game')}))
    assert materialize_fd_department_inputs(tmp_path, 'Manufacturer', 'sales', 10, raw, board) == (raw, board)


def fd_analysis_runner(tmp_path):
    from agent.skill_runner import SkillRunner
    config = resolved(FD4, 'full')
    # These fixtures exercise the retained round/access protocol, not v4 decisions.
    config['runtime_injection']['single_enterprise_case_policy']['fd_experiment']['agent_prompt_policy_version'] = FD4_EVIDENCE_PROMPT_VERSION
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': config}))
    runner = SkillRunner.__new__(SkillRunner)
    runner.workspace_dir = tmp_path
    runner.enterprise_spec = SimpleNamespace(enterprise_name='Manufacturer')
    runner._active_analyst_round_id = 10
    runner._single_enterprise_case_policy_for_prompt = lambda: {'enabled': True}
    runner._validate_cobweb_profit_analysis_payload = lambda data: True
    runner._validate_long_horizon_analysis_payload = lambda data: True
    return runner


def valid_fd_analysis(round_id=10):
    return {
        'enterprise_name': 'Manufacturer', 'round_id': round_id,
        'enterprise_summarys': 'Assess current orders, inventory and available staff.',
        'department_targets': {
            dept: {'target': 'Review current state', 'evaluation': 'Check available resources',
                   'reason': 'Use observed department evidence'}
            for dept in ['sales', 'procurement', 'production', 'hr']
        },
    }


@pytest.mark.parametrize('round_id', [10, '10', ' 10 '])
def test_fd_analysis_accepts_current_integer_or_string_round(tmp_path, round_id):
    runner = fd_analysis_runner(tmp_path)
    assert runner._is_valid_analysis_payload(valid_fd_analysis(round_id))


@pytest.mark.parametrize('round_id', [9, '9', None, True, 10.5, '10.5', {}, ''])
def test_fd_analysis_rejects_stale_or_invalid_round(tmp_path, round_id):
    runner = fd_analysis_runner(tmp_path)
    assert not runner._is_valid_analysis_payload(valid_fd_analysis(round_id))
    assert 'round_id' in runner._last_analysis_validation_error


def test_fd_analysis_rejects_wrong_enterprise(tmp_path):
    runner = fd_analysis_runner(tmp_path)
    payload = valid_fd_analysis('10')
    payload['enterprise_name'] = 'Supplier'
    assert not runner._is_valid_analysis_payload(payload)
    assert 'enterprise_name' in runner._last_analysis_validation_error


def test_fd_string_round_does_not_mask_missing_hr(tmp_path):
    runner = fd_analysis_runner(tmp_path)
    payload = valid_fd_analysis('10')
    del payload['department_targets']['hr']
    assert not runner._is_valid_analysis_payload(payload)
    assert 'department_targets.hr' in runner._last_analysis_validation_error


def test_fd_retry_prioritizes_schema_error_over_tool_inspection(tmp_path):
    runner = fd_analysis_runner(tmp_path)
    runner._last_analysis_validation_error = 'department_targets.hr missing'
    runner._build_analyst_same_session_resume_prompt = lambda spec, day, text: text
    spec = SimpleNamespace(skill_args='inputs', output_file_name='analysis.json')
    result = runner._next_analyst_prompt_by_failure(
        spec, 10, SimpleNamespace(failure_type='skill_args_missing_or_wrong'))
    assert 'department_targets.hr missing' in result


def test_fd_runtime_prompt_explicitly_requires_four_targets(tmp_path):
    runner = fd_analysis_runner(tmp_path)
    prompt = runner._build_single_enterprise_analyst_prompt()
    assert 'Include HR based on real staffing even when no hiring is needed' in prompt
    assert 'target, evaluation and reason' in prompt
    assert 'Read observation, charts and history once each' in prompt


@pytest.mark.parametrize('role', ['Analyst', 'HR', 'Production', 'Sales', 'Procurement'])
def test_fd_limits_identical_reads_but_keeps_pages_and_output_readback(tmp_path, role):
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': resolved(FD4, 'full')}))
    enterprise = tmp_path / 'enterprises' / 'Manufacturer'
    enterprise.mkdir(parents=True)
    observation = enterprise / 'observation.json'
    observation.write_text('{"cash": 100}')
    output = enterprise / ('analysis.json' if role == 'Analyst' else f'{role.lower()}_action.json')
    output.write_text('{}')
    spec = SimpleNamespace(role=role, skill_name=role.lower(),
                           observation_path=observation, output_file_path=output)
    options = SimpleNamespace(cwd=str(enterprise), hooks={})
    configure_input_access(options, spec, tmp_path, tmp_path, 'Manufacturer', 10)
    guard = options.hooks['PreToolUse'][0].hooks[0]

    async def check(path, **params):
        result = await guard({'tool_name': 'Read', 'tool_input': {
            'file_path': str(path), **params}}, 'test', None)
        return result['hookSpecificOutput']

    assert asyncio.run(check(observation))['permissionDecision'] == 'allow'
    assert asyncio.run(check(observation))['permissionDecision'] == 'allow'
    blocked = asyncio.run(check(observation))
    assert blocked['permissionDecision'] == 'deny'
    assert 'Write' in blocked['permissionDecisionReason']
    assert output.name in blocked['permissionDecisionReason']
    assert asyncio.run(check(observation, offset=10, limit=20))['permissionDecision'] == 'allow'
    observation.write_text('{"cash": 200}')
    assert asyncio.run(check(observation))['permissionDecision'] == 'allow'
    for _ in range(4):
        assert asyncio.run(check(output))['permissionDecision'] == 'allow'


@pytest.mark.parametrize('role,module', [('procurement', 'ProcurementManager'), ('sales', 'SalesManager'),
                                         ('production', 'ProductionManager'), ('hr', 'HRManager')])
def test_department_pass_template_is_present_readable_and_schema_compatible(tmp_path, role, module):
    from pathlib import Path
    from agent.multi_tenant_utils import MultiTenantUtils
    client = Path(__file__).resolve().parents[1] / 'agent'
    template = client / '.claude' / 'skills' / role / 'actions' / 'action_pass.json'
    data = json.loads(template.read_text())
    assert data['action_name'] == 'action_pass'
    payload = deepcopy(data['output_example'])
    assert payload[0]['module_type'] == module
    payload[0]['action']['action_param'] = 'Current authorized state does not justify a new commitment.'
    payload[0]['action_reason'] = payload[0]['action']['action_param']
    payload[0]['executor_id'] = 'Manufacturer'
    assert MultiTenantUtils._is_valid_action_payload(payload, role)
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': resolved(FD4, 'full')}))
    spec = SimpleNamespace(role=role, skill_name=role, output_file_path=str(tmp_path / f'{role}_action.json'))
    options = configure_input_access(SimpleNamespace(cwd=str(tmp_path), hooks={}), spec,
                                     tmp_path, client, 'Manufacturer', 10)
    result = asyncio.run(options.hooks['PreToolUse'][-1].hooks[0](
        {'tool_name': 'Read', 'tool_input': {'file_path': str(template)}}, 'read-pass-template', None))
    assert result['hookSpecificOutput']['permissionDecision'] == 'allow'


@pytest.mark.parametrize('content,expected', [
    (None, 'was not created'), ('', 'is empty'),
    ('{"round_id":', 'JSONDecodeError'),
])
def test_fd_invalid_analysis_file_has_specific_diagnostic(tmp_path, content, expected):
    runner = fd_analysis_runner(tmp_path)
    path = tmp_path / 'analysis.json'
    if content is not None:
        path.write_text(content)
    assert not runner._is_valid_json_file(str(path))
    assert expected in runner._last_analysis_validation_error


@pytest.mark.parametrize('stock,pending,workers,material,cash,expected', [
    (100, 200, 2, 10000, 100000, 700),
    (1000, 0, 2, 10000, 100000, 0),
    (0, 0, 0, 10000, 100000, 0),
    (0, 0, 2, 120, 100000, 120),
    (0, 0, 2, 10000, 1540, 100),
])
def test_revised_rb_quantity_bounds(tmp_path, stock, pending, workers, material, cash, expected):
    runner = ScriptedRuleRunner(tmp_path, SimpleNamespace(enterprise_name='Manufacturer'), tmp_path)
    state = {'recovery_guard': {'candidates': [{'product_id': 'PRODUCT_1',
             'confirmed_order_backlog_quantity': 1000, 'on_hand': stock, 'active_plan_quantity': pending}]},
             'product_recipes': [{'product_id': 'PRODUCT_1', 'labor_cost_per_unit': 10, 'equipment_cost_per_unit': 5}],
             'production_lines': {'details': [{'line_type': 'small', 'remaining_capacity': 500},
                                              {'line_type': 'small', 'remaining_capacity': 500}]}}
    runner._self_state = lambda *args: state
    runner._department_state = lambda *args: {'self_state': state}
    runner._production_capacity_status = lambda *args: (1000, 1000)
    runner._department_staff_status = lambda *args: {'available': workers}
    runner._feasible_production_quantity = lambda day, qty: min(qty, material)
    runner._available_cash = lambda *args: cash
    assert runner._fd_revised_production_quantity(10) == expected


def test_revised_rb_creates_plan_instead_of_zero(tmp_path):
    runner = ScriptedRuleRunner(tmp_path, SimpleNamespace(enterprise_name='Manufacturer'), tmp_path)
    config = resolved(FD4 + '_scripted', 'rb_revised')
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': config}))
    runner._cash_is_tight = lambda day: False
    runner._cash_is_warning = lambda day: False
    runner._production_staff_shortage = lambda day: False
    runner._production_build_line_reason = lambda day: ''
    runner._department_state = lambda *args: {'self_state': {'product_recipes': [{'product_id': 'PRODUCT_1'}]}}
    runner._fd_revised_production_quantity = lambda day: 700
    runner._production_capacity_status = lambda day: (1000, 1000)
    runner._feasible_production_quantity = lambda day, qty: qty
    action = runner._production_action(10)[0]['action']
    assert action['action_name'] == 'create_production_plan'
    assert action['action_param']['quantity'] == 700
    assert action['action_param']['daily_capacity'] == 700
    legacy = deepcopy(config)
    legacy['runtime_injection']['single_enterprise_case_policy'].pop('fd_experiment')
    (tmp_path / 'run_meta.json').write_text(json.dumps({'scenario_config': legacy}))
    assert runner._production_action(10)[0]['action']['action_name'] == 'action_pass'
