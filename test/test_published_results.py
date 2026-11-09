import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.result_publication import publish_result, published_member
from persistence.review_projection import build_review
from runtime.operations.http_router import create_operations_router
from runtime.artifact_http import create_artifact_router


def test_publication_preserves_business_metrics_and_excludes_diagnostics(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir()
    (source/'run_meta.json').write_text(json.dumps({'run_id':'fixture','completed_steps':1,'planned_total_steps':1,'api_key':'not-public'}))
    (source/'config.json').write_text('{"credentials":"not-public"}')
    (source/'error.private.json').write_text('not-public')
    folder=source/'enterprises/Firm/department/finance/day0';folder.mkdir(parents=True)
    (folder/'finance.json').write_text(json.dumps({'self_state':{'cash':123,'financial_indicators':{'net_profit_rate':79.90799}}}))
    record=source/'enterprises/Firm/records/day0';record.mkdir(parents=True);(record/'observation.json').write_text('{}')
    dest=tmp_path/'published/runs/fixture'
    manifest=publish_result(source,dest)
    assert 'config.json' not in manifest['files']
    assert 'api_key' not in json.loads((dest/'run_meta.json').read_text())
    review=build_review(dest)
    assert review['metrics']['net_profit_margin']['value']==79.90799
    assert review['metrics']['cash']['unit']=='SCU'
    assert published_member(dest,dest.parent)
    (dest/'credentials.json').write_text('{"api_key":"not-published"}')
    assert not published_member(dest/'credentials.json',dest.parent)
    assert (source/'config.json').exists()


def test_public_catalog_and_file_routes_share_the_publication_boundary(tmp_path,monkeypatch):
    from runtime.operations import http_router
    published=tmp_path/'runs';published.mkdir()
    private=tmp_path/'outside';private.mkdir();(private/'run_meta.json').write_text('{"run_id":"secret"}')
    (published/'escape').symlink_to(private,target_is_directory=True)
    monkeypatch.setattr(http_router,'_artifact_bases',lambda:{'workspace_jobs':published,'simulation_runs':tmp_path/'empty','workspace_multi':tmp_path/'none'})
    app=FastAPI();app.include_router(create_operations_router());app.include_router(create_artifact_router())
    with TestClient(app) as client:
        response=client.get('/operations/artifacts/runs?include_operations_jobs=false')
        assert response.json()['data']['entries']==[]
        assert client.get('/workspace_jobs/escape/run_meta.json').status_code==403
        assert client.get('/workspace_jobs/never-recorded.json').status_code==404


def test_review_accepts_an_enterprise_without_production_lines(tmp_path):
    (tmp_path/'run_meta.json').write_text('{"completed_steps":1,"planned_total_steps":1}')
    folder=tmp_path/'enterprises/Supplier/department/production/day0';folder.mkdir(parents=True)
    (folder/'production.json').write_text('{"self_state":{"production_lines":null}}')
    record=tmp_path/'enterprises/Supplier/records/day0';record.mkdir(parents=True);(record/'state.json').write_text('{}')
    review=build_review(tmp_path)
    assert review['metrics']['available_capacity']['availability']=='missing'


def test_invalid_snapshot_is_distinct_from_a_missing_field(tmp_path):
    (tmp_path/'run_meta.json').write_text('{"completed_steps":1,"planned_total_steps":1}')
    folder=tmp_path/'enterprises/Firm/department/finance/day0';folder.mkdir(parents=True)
    (folder/'finance.json').write_text('not JSON')
    record=tmp_path/'enterprises/Firm/records/day0';record.mkdir(parents=True);(record/'state.json').write_text('{}')
    review=build_review(tmp_path)
    assert review['metrics']['cash']['availability']=='failed_to_load'
    assert review['metrics']['cash']['round']==0


def test_chart_view_endpoint_uses_manifest_and_refuses_unpublished_projection(tmp_path,monkeypatch):
    from runtime.operations import http_router
    from test.test_execution_review import archive
    source=tmp_path/'raw';archive(source)
    published=tmp_path/'runs';destination=published/'fixture';publish_result(source,destination)
    monkeypatch.setattr(http_router,'_artifact_bases',lambda:{'workspace_jobs':published,'simulation_runs':tmp_path/'empty','workspace_multi':tmp_path/'none'})
    app=FastAPI();app.include_router(create_operations_router())
    with TestClient(app) as client:
        result=client.get('/operations/artifacts/view?source=workspace_jobs&name=fixture&view=global').json()
        assert result['data']['availability']=='available'
        assert result['data']['data']['rows'][-1]['finance']['cash']==101
        manifest=json.loads((destination/'public_manifest.json').read_text())
        (destination/manifest['review_projections']['global']).write_text('{}')
        result=client.get('/operations/artifacts/view?source=workspace_jobs&name=fixture&view=global').json()
        assert result['data']['availability']=='changed_since_publication'
        assert result['data']['data'] is None
        result=client.get('/operations/artifacts/view?source=workspace_jobs&name=fixture&view=../../private').json()
        assert result['ok'] is False
