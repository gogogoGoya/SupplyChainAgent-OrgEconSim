"""Shared production/development artifact routes and bounded directory listings."""
import mimetypes
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from core.public_artifacts import is_public_artifact
from core.result_publication import published_member
from runtime.result_access import private_results


def create_artifact_router():
    router=APIRouter(tags=["results"])
    def serve(source, artifact_path=""):
        from runtime.operations.http_router import _artifact_bases
        base=_artifact_bases()[source].resolve()
        path=(base/artifact_path).resolve()
        if not is_public_artifact(path,base):raise HTTPException(403,"Artifact path is not public")
        if path == base and not base.exists():return {}
        if not path.exists():raise HTTPException(404,"Artifact was not recorded")
        allowed=lambda p:is_public_artifact(p,base) and (private_results.get() or published_member(p,base))
        if not allowed(path):raise HTTPException(403,"Publish a reviewed result copy before sharing")
        if path.is_dir():
            return {p.name:"directory" if p.is_dir() else "file" for p in sorted(path.iterdir()) if allowed(p)}
        return FileResponse(path,media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            headers={"Cache-Control":"private, no-store" if private_results.get() else "public, max-age=3600"})
    def make_endpoint(source):
        def endpoint(artifact_path: str=""):
            return serve(source,artifact_path)
        return endpoint
    for source in ("workspace_jobs","simulation_runs","workspace_multi"):
        endpoint=make_endpoint(source)
        router.add_api_route(f"/{source}/{{artifact_path:path}}",endpoint,methods=["GET","HEAD"])
        router.add_api_route(f"/{source}",endpoint,methods=["GET","HEAD"])
    return router
