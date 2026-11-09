"""Regression cases for completion evidence, serialization and archive boundaries."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from core.controller import Controller
from core.execution_boundary import ExecutionIndeterminateError, assert_execution_ready
from core.run_integrity import inspect_run
from persistence.sql_mirror import SQLiteMirrorRepository
from runtime.operations.service import OperationsService
from runtime.operations.worker import OperationsWorker


@pytest.mark.parametrize("result", [{}, {"status":"failed","completed_steps":2,"last_complete_round":1},
    {"status":"completed","completed_steps":2,"last_complete_round":1}])
def test_incomplete_or_failed_runner_never_becomes_completed(tmp_path, result):
    repo = SQLiteMirrorRepository(tmp_path / "operations.sqlite")
    job = OperationsService(repo).create_experiment(scenario_id="fixture", scenario_config={}, integration_profiles={}, planned_total_steps=20)
    finished = OperationsWorker(repo, lambda *args: result).run_job(job["job_id"])
    assert finished["status"] == "failed"
    assert finished["completed_steps"] != 20


def test_zero_round_archive_has_no_planned_snapshots(tmp_path):
    report = inspect_run(tmp_path, {"status":"failed","completed_steps":0,"planned_total_steps":20})
    assert report["available_rounds"] == [] and report["last_available_round"] == -1
    assert not report["complete"]


def test_snapshot_gaps_are_preserved(tmp_path):
    for day in (0,2):
        folder=tmp_path/f"public/exchange/day{day}"
        folder.mkdir(parents=True)
        (folder/"exchange.json").write_text('{}')
    report=inspect_run(tmp_path,{"status":"completed","completed_steps":3,"planned_total_steps":3})
    assert report["available_rounds"] == [0,2]
    assert report["missing_declared_rounds"] == [1] and not report["complete"]


def test_commands_serialize_competing_resource_mutations():
    controller=Controller.__new__(Controller)
    state={"cash":100,"accepted":0}
    def spend(*args):
        if state["cash"]<60:return {"status":"error"}
        current=state["cash"];time.sleep(0.01)
        state.update(cash=current-60,accepted=state["accepted"]+1)
        return {"status":"success"}
    controller.time_manager=SimpleNamespace(get_day=lambda:0,get_tick=lambda:0)
    controller.environment=None
    controller.enterprises={"Firm":object()}
    controller.action_executor=SimpleNamespace(_call_department_functions=spend)
    actions=[{"executor_id":"Firm","module_type":d,"action":{"action_name":"spend"}} for d in ("production","hr")]
    asyncio.run(controller._execute_time_step(actions))
    assert state == {"cash":40,"accepted":1}


def test_timeout_quarantines_even_when_the_thread_later_finishes(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPPLY_CHAIN_RUNTIME_ROOT", str(tmp_path))
    controller=Controller.__new__(Controller)
    controller.business_action_timeout_seconds=0.01
    controller.time_manager=SimpleNamespace(get_day=lambda:0,get_tick=lambda:0)
    controller.environment=None;controller.enterprises={"Firm":object()}
    writes=[]
    def slow(*args):time.sleep(0.05);writes.append("late");return {"status":"success"}
    controller.action_executor=SimpleNamespace(_call_department_functions=slow)
    async def run():
        with pytest.raises(ExecutionIndeterminateError):
            await controller._execute_time_step([{"executor_id":"Firm","module_type":"hr","action":{"action_name":"slow"}}])
        await asyncio.sleep(0.08)
        with pytest.raises(ExecutionIndeterminateError):assert_execution_ready(controller)
        assert controller.execution_quarantine["receipt"] == "late_result"
    asyncio.run(run())
    assert writes == ["late"]
    saved = json.loads(next((tmp_path / "quarantine").glob("*.json")).read_text())
    assert saved["receipt"] == "late_result" and saved["protocol"] == "ordered_business_commit.v1"


def test_submission_replay_returns_receipt_without_repeating_business_mutation(monkeypatch):
    from simulate import simulation_server as server
    mutations=[]
    async def step(*args):
        mutations.append("commit")
        return {"success": []}
    controller=SimpleNamespace(time_manager=SimpleNamespace(get_day=lambda:0))
    session=SimpleNamespace(simulation=SimpleNamespace(controller=controller),adapter=SimpleNamespace(step=step))
    monkeypatch.setitem(server.simulation_sessions,"receipt-test",session)
    async def run():
        request=server.ExecuteRequest(workflow=[],execute_type="run",submission_id="proposal-1")
        first=await server.execute_action(request,x_simulation_session_id="receipt-test")
        second=await server.execute_action(request,x_simulation_session_id="receipt-test")
        assert first == second and first["receipt_id"] == "proposal-1"
        with pytest.raises(server.HTTPException) as conflict:
            await server.execute_action(server.ExecuteRequest(workflow=[],execute_type="daily",submission_id="proposal-1"),x_simulation_session_id="receipt-test")
        assert conflict.value.status_code==409
    asyncio.run(run())
    assert mutations==["commit"]


def test_unknown_transport_receipt_is_fatal(monkeypatch):
    from agent.static_utils import StaticUtils
    from core.model_failure import FatalModelError
    import requests
    def timeout(*args,**kwargs):raise requests.Timeout("lost response")
    monkeypatch.setattr(requests,"post",timeout)
    with pytest.raises(FatalModelError,match="environment_execution_indeterminate"):
        StaticUtils._post_execution("http://local/execute",json={"workflow":[]})


def test_session_identifiers_and_checkpoint_restore_do_not_cross_sessions():
    from runtime.session_boundary import session_scope
    from network.exchange_manager import next_id, restore_next_id_counter_value
    with session_scope("counter-check-a"):
        assert next_id("order") == "order_000001"
        restore_next_id_counter_value(50)
    with session_scope("counter-check-b"):
        assert next_id("order") == "order_000001"
    with session_scope("counter-check-a"):
        assert next_id("order") == "order_000050"
    with session_scope("counter-check-b"):
        assert next_id("order") == "order_000002"


def test_market_rng_is_independent_and_restorable():
    import pickle
    from core.market_manager import MarketManager
    one,two=MarketManager(None),MarketManager(None)
    one.random.seed(7);two.random.seed(7)
    assert one.random.random() == two.random.random()
    saved=pickle.dumps(one.random)
    expected=one.random.random()
    assert pickle.loads(saved).random() == expected
    one.random.random()
    assert two.random.random() == expected


def test_scripted_runtime_needs_no_gateway_but_partial_policies_still_do(tmp_path,monkeypatch):
    import agent.EnterpriseRuntime as module
    from agent.data_config import EnterpriseSpec, DepartmentSpec
    from config.environment_config import EnvironmentConfig
    spec=EnterpriseSpec('Firm','Firm',departments=[DepartmentSpec('hr','hr','HR','hr')])
    policy={'scripted_rule_policy':{'enabled':True,'analyst':{'enabled':True},'departments':{'apply_to_departments':['all']}}}
    monkeypatch.setattr(module,'get_runtime_injection_config',lambda:policy)
    monkeypatch.setattr(EnvironmentConfig,'LOCAL_AGENT_GATEWAY_BASE_URL','')
    runtime=module.EnterpriseRuntime(str(tmp_path),spec,object(),'')
    assert runtime.anthropic_base_url==''
    policy['scripted_rule_policy']['analyst']['enabled']=False
    with pytest.raises(RuntimeError,match='gateway is not configured'):
        module.EnterpriseRuntime(str(tmp_path),spec,object(),'')


def test_provenance_detects_template_drift_but_allows_key_rotation(tmp_path):
    from runtime.operations.provenance import capture_provenance, assert_provenance
    folder=tmp_path/'agent/static_commands';folder.mkdir(parents=True)
    template=folder/'init.json';template.write_text('{}')
    before=capture_provenance(tmp_path,{'ANTHROPIC_API_KEY':'first','ANTHROPIC_MODEL':'model'})
    rotated=capture_provenance(tmp_path,{'ANTHROPIC_API_KEY':'second','ANTHROPIC_MODEL':'model'})
    assert_provenance(before,rotated)
    assert 'first' not in json.dumps(before)
    template.write_text('{"action":"changed"}')
    with pytest.raises(RuntimeError,match='source_sha256'):
        assert_provenance(before,capture_provenance(tmp_path,{'ANTHROPIC_MODEL':'model'}))
