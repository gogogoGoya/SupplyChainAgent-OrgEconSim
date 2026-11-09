"""HTTP enforcement shared by the operations router and raw simulation endpoints."""
from fastapi import HTTPException, Request

from config.platform_access import get_platform_access


def enforce_platform_access(request: Request) -> None:
    private_reads = {"/state", "/state/json", "/exchange", "/market/external_demand",
                     "/simulation_context", "/bullwhip", "/actions", "/session/status"}
    if request.method == "OPTIONS" or (request.method in {"GET", "HEAD"} and request.url.path not in private_reads):
        return
    policy = get_platform_access()
    if not policy.execution_enabled:
        raise HTTPException(status_code=403, detail="This deployment is read-only. New simulations require a configured operator deployment.")
    if not policy.authenticated(request.headers.get("authorization", "")):
        raise HTTPException(status_code=401, detail="A valid operator access token is required.",
                            headers={"WWW-Authenticate": "Bearer"})
