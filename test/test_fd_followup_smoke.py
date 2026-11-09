"""Optional real 20-round RB smoke, isolated from archives and LLM calls.

Run with FD_SMOKE=1 upgrade/bin/python -m pytest test/test_fd_followup_smoke.py.
"""
import asyncio
import json
import os
from urllib.parse import urlsplit

import pytest


@pytest.mark.skipif(os.getenv('FD_SMOKE') != '1', reason='Explicitly enable the 20-round runtime smoke')
@pytest.mark.parametrize('scenario,condition', [
    ('single_case_04_staff_shortage_scripted', 'rb_revised'),
    ('single_case_04_staff_shortage_scripted', 'rb_simple'),
    ('single_case_02_material_shortage_scripted', 'rb_reference'),
])
def test_rb_twenty_round_runtime(tmp_path, monkeypatch, scenario, condition):
    from fastapi.testclient import TestClient
    from simulate.simulation_server import app
    from config.fd_experiment import FD4, resolve_fd_experiment
    from config.simulation_preset_config import get_scenario_config, temporary_scenario_config_override
    from config.project_paths import AGENT_ROOT
    from runtime.single_enterprise.orchestrator import SingleEnterpriseSimulationManager, build_single_enterprise_specs
    from agent.multi_tenant_utils import MultiTenantUtils
    from network.exchange_manager import get_next_id_counter_value, restore_next_id_counter_value
    import agent.static_utils as static_utils
    import agent.skill_runner as skill_runner

    async def forbid_model(**kwargs):
        raise AssertionError('The RB smoke must never call an LLM')
        yield

    monkeypatch.setattr(skill_runner, 'query', forbid_model)
    monkeypatch.setenv('SIMULATION_SQL_MIRROR_DISABLED', '1')
    monkeypatch.setenv('SUPPLY_CHAIN_SCENARIO', scenario)
    from config.simulation_preset_config import SIMULATION_SCENARIO_ENV_VAR
    monkeypatch.setenv(SIMULATION_SCENARIO_ENV_VAR, scenario)
    monkeypatch.setattr(MultiTenantUtils, 'archive_current_run_snapshot', lambda *args, **kwargs: None)
    config = get_scenario_config(scenario)
    config['runtime_injection']['single_enterprise_case_policy']['fd_experiment'] = {'condition': condition}
    config = resolve_fd_experiment(config, scenario, 20)
    workspace = tmp_path / 'run_fd_rb_smoke'
    with TestClient(app, headers={"Authorization": "Bearer " + os.environ["SUPPLY_CHAIN_OPERATOR_TOKEN"]}) as client:
        def request(method, url, **kwargs):
            kwargs.pop('timeout', None)
            kwargs['headers'] = {**(kwargs.get('headers') or {}),
                                 'X-Simulation-Session-Id': f'fd4_smoke_{tmp_path.name}_{condition}'}
            return client.request(method, urlsplit(url).path, **kwargs)
        monkeypatch.setattr(static_utils.requests, 'post', lambda url, **kwargs: request('POST', url, **kwargs))
        monkeypatch.setattr(static_utils.requests, 'get', lambda url, **kwargs: request('GET', url, **kwargs))
        with temporary_scenario_config_override(scenario, config):
            manager = SingleEnterpriseSimulationManager(
                client_dir=str(AGENT_ROOT), enterprise_spec=build_single_enterprise_specs()[0],
                workspace_dir=str(workspace), simulation_max_step=20, max_department_concurrency=1,
                model_name='unused-scripted-smoke',
            )
            counter = get_next_id_counter_value()
            try:
                asyncio.run(manager.run(max_step=20, operations_run_id='run_fd_rb_smoke'))
            finally:
                restore_next_id_counter_value(counter)
    metadata = json.loads((workspace / 'run_meta.json').read_text())
    assert metadata['status'] == 'completed'
    if scenario == FD4 + '_scripted':
        assert metadata['scenario_config']['fd_validation_instance']['instance_sequence'] == 2
    actions = [json.loads(path.read_text()) for path in
               (workspace / 'enterprises' / 'Manufacturer' / 'department' / 'production').glob('day1*/production_action.json')]
    assert any('create_production_plan' in json.dumps(action) for action in actions)
    production_root = workspace / 'enterprises' / 'Manufacturer' / 'department' / 'production'
    results = [json.loads(path.read_text()) for path in production_root.glob('day1*/production_result.json')]
    assert any(record.get('action_type') == 'create_production_plan'
               for payload in results for record in (payload.get('result') or {}).get('success', []))
    def output(day):
        snapshot = workspace / 'public' / 'observer_state' / f'day{day}' / ('prewarm_end_of_day' if day == 9 else 'end_of_day') / 'Manufacturer.json'
        data = json.loads(snapshot.read_text())['observation']['production']
        return sum(line['total_produced'] for line in data['production_lines']['details'])
    assert output(19) > output(9)
    handover = json.loads((workspace / 'public' / 'observer_state' / 'day9' / 'prewarm_end_of_day' / 'Manufacturer.json').read_text())['observation']
    if scenario == FD4 + '_scripted':
        assert handover['hr']['department_staffing']['Production']['count'] == 0
        assert not handover['production']['production_plans'].get('in_progress')
    else:
        assert handover['hr']['department_staffing']['Production']['count'] > 0
        assert handover['inventory']['inventory_items'][0]['item_id'] == 'MATERIAL_1'
        assert handover['inventory']['inventory_items'][0]['quantity'] == 354
    assert (workspace / 'enterprises' / 'Manufacturer' / 'fd_audit' / 'handover_manifest.json').exists()
