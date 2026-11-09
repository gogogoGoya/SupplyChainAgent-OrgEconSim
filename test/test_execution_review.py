import hashlib
import json

import pytest

from core.result_publication import publish_result
from persistence.execution_review import build_execution_evidence, summarize_execution, action_trace, wall_seconds, timing_basis
from persistence.review_projection import build_review, build_trend
from scripts.enrich_result_review import prepare_review, install_review


def write(root, relative, value):
    path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value));return path


def archive(root):
    meta={"run_id":"test-run","status":"completed","enterprise_ids":["Firm"],
          "completed_steps":2,"planned_total_steps":2,
          "started_at":"2026-11-05T00:00:00+00:00","finished_at":"2026-11-05T00:01:30+00:00"}
    write(root,"run_meta.json",meta)
    for r in range(2):
        write(root,f"enterprises/Firm/records/day{r}/analysis.json",{})
        write(root,f"public/observer_state/day{r}/end_of_day/Firm.json",{"observation":{}})
        write(root,f"enterprises/Firm/department/finance/day{r}/finance.json",{"self_state":{"cash":100+r}})
    return meta


def test_runtime_health_missing_is_not_zero_and_full_records_can_confirm_zero(tmp_path):
    meta=archive(tmp_path)
    evidence=build_execution_evidence(tmp_path)
    assert summarize_execution(evidence,meta)["metrics"]["all_agent_timeout_rounds"]["value"] is None
    for r in range(2):write(tmp_path,f"runtime_health/day{r}_agent_timeout.json",{"all_agent_calls_timed_out":False,"api_key":"private"})
    evidence=build_execution_evidence(tmp_path)
    summary=summarize_execution(evidence,meta)
    assert summary["metrics"]["all_agent_timeout_rounds"]["value"]==0
    assert summary["health_coverage"]=={"recorded":2,"expected":2}
    assert "private" not in json.dumps(evidence)


def test_repair_submissions_are_distinct_from_executed_or_withdrawn_chains(tmp_path):
    meta=archive(tmp_path)
    def record(id,parents,status,name="create_order"):
        return action_trace({"action_id":id,"parent_action_ids":parents,"status":status,
            "proposed":{"action":{"action_name":name}},"response":{"success":status=="success"}},"sales","decision","fixture",0)
    evidence={"phases":[],"traces":[{"round":1,"enterprise":"Firm","actions":[
        record("a",[],"rejected"),record("a1",["a"],"success"),record("a2",["a"],"rejected"),
        record("b",[],"rejected"),record("b1",["b"],"success","action_pass"),
        record("c",[],"rejected"),record("c1",["c"],"success")]}]}
    summary=summarize_execution(evidence,meta)
    assert summary["metrics"]["executed_replacements"]["value"]==1
    assert summary["metrics"]["withdrawals"]["value"]==1
    assert summary["metrics"]["repair_submissions"]["value"] is None


def test_window_and_enterprise_scopes_do_not_turn_duration_sums_into_wall_time(tmp_path):
    meta=archive(tmp_path)
    for firm,duration in [("Firm",60),("Other",60)]:
        write(tmp_path,f"enterprises/{firm}/firm_agent/day1/decision/phase_summary.json",{
            "enterprise_id":firm,"round_id":1,"phase":"decision","elapsed_seconds":duration,
            "tasks":[{"task_id":firm,"first_attempt_valid":True,"recovered":True,"attempts":2}],
            "business_repairs":3})
    evidence=build_execution_evidence(tmp_path)
    all_firms=summarize_execution(evidence,meta)
    assert all_firms["timing"]["run_wall"]["value"]==90
    assert all_firms["timing"]["phase_elapsed_sum"]["value"]==120
    single=summarize_execution(evidence,meta,"Firm",start=1,end=1)
    assert single["timing"]["phase_elapsed_sum"]["value"]==60
    assert single["metrics"]["repair_submissions"]["value"]==3
    assert single["metrics"]["retry_recovery_rate"]["value"] is None


def test_legacy_skill_audits_keep_fallback_separate_from_model_recovery(tmp_path):
    meta=archive(tmp_path)
    for dept,success,attempts,fallback in [("sales",True,1,False),("production",False,3,True),("hr",True,2,False)]:
        write(tmp_path,f"enterprises/Firm/department/{dept}/day1/skill_execution_audit.json",{
            "success":success,"attempts":attempts,"fallback_used":fallback,"department":dept,"phase":"decision"})
    summary=summarize_execution(build_execution_evidence(tmp_path),meta)
    assert summary["metrics"]["first_attempt_rate"]["numerator"]==1
    assert summary["metrics"]["first_attempt_rate"]["denominator"]==3
    assert summary["metrics"]["retry_recovery_rate"]["numerator"]==1
    assert summary["metrics"]["retry_recovery_rate"]["denominator"]==2


def test_public_review_rejects_changed_snapshots_and_unlisted_files(tmp_path):
    source=tmp_path/"raw";archive(source)
    published=tmp_path/"public";publish_result(source,published)
    path=published/"enterprises/Firm/department/finance/day1/finance.json"
    path.write_text('{"self_state":{"cash":9999}}')
    metric=build_review(published)["metrics"]["cash"]
    assert metric["value"] is None
    assert metric["availability"]=="changed_since_publication"
    write(published,"enterprises/Firm/department/hr/day1/hr.json",{"self_state":{"recruited_people":123}})
    assert build_review(published)["departments"]["hr"]["availability"]=="missing"


def test_enrichment_preserves_source_and_adds_manifest_bound_trends(tmp_path):
    source=tmp_path/"raw";archive(source)
    original=(source/"run_meta.json").read_bytes()
    published=tmp_path/"public";publish_result(source,published,with_review=False)
    prepared=tmp_path/"prepared";prepare_review(source,published,prepared)
    install_review(prepared,published)
    trend=build_trend(published,"Firm")
    assert trend["rows"][-1]["metrics"]["cash"]==build_review(published)["metrics"]["cash"]
    assert trend["rows"][-1]["series"]["cash"]==101
    assert (source/"run_meta.json").read_bytes()==original
    with pytest.raises(ValueError,match="changed"):
        install_review(prepared,published)


def test_nonproducing_firm_can_publish_null_production_metrics(tmp_path):
    source=tmp_path/"raw";archive(source)
    write(source,"enterprises/Firm/department/production/day1/production.json",{"self_state":{"production_metrics":None}})
    public=tmp_path/"public";publish_result(source,public)
    row=build_trend(public,"Firm")["rows"][-1]
    assert row["series"]["production"] is None


def test_legacy_mixed_timestamp_offsets_keep_explicit_local_clock_basis():
    meta={"started_at":"2026-10-27T12:29:59+08:00","finished_at":"2026-10-27T13:42:19"}
    assert wall_seconds(meta)==4340
    assert "Local-clock" in timing_basis(meta)


def test_existing_compact_publications_keep_their_recorded_quality_counts(tmp_path):
    source=tmp_path/"raw";archive(source)
    write(source,"firm_architecture_quality.json",{"model_task_count":10,"first_attempt_valid_tasks":8})
    public=tmp_path/"public";publish_result(source,public,with_review=False)
    review=build_review(public)
    assert review["execution"]["metrics"]["model_tasks"]["value"]==10
    assert review["execution"]["metrics"]["first_attempt_rate"]["value"]==0.8


def test_view_enrichment_keeps_existing_evidence_and_chart_values(tmp_path):
    from persistence.review_io import ReviewReader
    source=tmp_path/'raw';archive(source)
    for r in range(2):
        write(source,f'enterprises/Firm/department/procurement/day{r}/procurement.json',{'self_state':{'replenishment':{'history':[{'material_id':'M','inventory_position':1},{'material_id':'M','inventory_position':7}]}}})
    public=tmp_path/'public';publish_result(source,public)
    previous=json.loads((public/'public_manifest.json').read_text())['review_projections'].copy()
    prepared=tmp_path/'views';prepare_review(source,public,prepared,views_only=True);install_review(prepared,public)
    reader=ReviewReader(public)
    assert reader.manifest['review_projections']['execution']==previous['execution']
    assert reader.projection('global')['rows'][-1]['finance']['cash']==101
    assert reader.projection('operations')['rows'][-1]['procurement']['replenishment']['history']==[{'material_id':'M','inventory_position':7}]
    assert len(reader.projection('evolution')['rows'])==2


def test_published_corrupt_evidence_does_not_fall_back_to_legacy_counts(tmp_path):
    source=tmp_path/'raw';archive(source)
    write(source,'firm_architecture_quality.json',{'model_task_count':10})
    public=tmp_path/'public';publish_result(source,public)
    manifest=json.loads((public/'public_manifest.json').read_text())
    (public/manifest['review_projections']['execution']).write_text('{}')
    with pytest.raises(ValueError,match='changed since publication'):build_review(public)
    (public/manifest['review_projections']['trends']).write_text('{}')
    assert build_trend(public,'Firm')['availability']=='changed_since_publication'


def test_bullwhip_chart_projection_preserves_observation_stage_and_order_fields(tmp_path):
    from persistence.view_projection import build_views
    meta=archive(tmp_path);meta['scenario_id']='firm_ablation_bullwhip_sa';write(tmp_path,'run_meta.json',meta)
    write(tmp_path,'public/observer_state/day1/end_of_day/Firm.json',{'observation':{'inventory':{'inventory_items':[{'item_id':'beer','quantity':7}]},'sales':{'sales_metrics':{'backlog_quantity':3}}}})
    write(tmp_path,'enterprises/Firm/department/procurement/day1/procurement.json',{'self_state':{'orders':{'received':[{'order_id':'o','quantity':5,'actual_arrival_time':1,'status':'received'}]},'top_tier_supply_plan':{'recommended_actions':[{'material_id':'M','recommended_quantity':3}]}}})
    projection=build_views(tmp_path)['bullwhip']['files']
    observer=projection['public/observer_state/day1/end_of_day/Firm.json']['observation']
    assert observer['inventory']['inventory_items'][0]['quantity']==7
    assert observer['sales']['sales_metrics']['backlog_quantity']==3
    procurement=projection['enterprises/Firm/department/procurement/day1/procurement.json']['self_state']
    assert procurement['orders']['received'][0]['actual_arrival_time']==1
    assert procurement['orders']['received'][0]['status']=='received'
