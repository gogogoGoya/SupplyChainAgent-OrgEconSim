"""Request-local selection of published results or authorized operator archives."""
from contextvars import ContextVar
from fastapi import Request
from config.platform_access import get_platform_access

private_results = ContextVar("private_results", default=False)


async def result_access_scope(request: Request):
    token = private_results.set(get_platform_access().authenticated(request.headers.get("authorization", "")))
    try:
        yield
    finally:
        private_results.reset(token)
