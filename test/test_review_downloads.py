from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier, Lock
import zipfile

from core.result_publication import publish_result
from runtime.operations import http_router


def test_concurrent_public_downloads_share_a_single_archive_build(tmp_path,monkeypatch):
    source=tmp_path/"raw";source.mkdir()
    (source/"run_meta.json").write_text(json.dumps({"run_id":"download-check","completed_steps":0}))
    published=tmp_path/"public/runs/run"
    publish_result(source,published,with_review=False)
    monkeypatch.setattr(http_router,"_artifact_bases",lambda:{"workspace_jobs":published.parent})
    from config import project_paths
    monkeypatch.setattr(project_paths,"RUNTIME_ROOT",tmp_path/"cache")
    original=zipfile.ZipFile;built=[];lock=Lock();start=Barrier(2)
    def observed(*args,**kwargs):
        if kwargs.get("mode",args[1] if len(args)>1 else None)=="w":
            with lock:built.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(zipfile,"ZipFile",observed)
    def download():
        start.wait(timeout=5)
        return str(http_router._build_artifact_zip(published).path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a,b=pool.map(lambda _:download(),range(2))
    assert a==b
    assert len(built)==1
    with original(a) as archive:
        assert any(name.endswith("public_manifest.json") for name in archive.namelist())
