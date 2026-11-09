"""Fail-closed access policy for the public results viewer and private operators."""
from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import os
from urllib.parse import urlparse

MODE_ENV = "SUPPLY_CHAIN_PLATFORM_MODE"
TOKEN_ENV = "SUPPLY_CHAIN_OPERATOR_TOKEN"
REQUEST_URL_ENV = "SUPPLY_CHAIN_ACCESS_REQUEST_URL"
MIN_TOKEN_LENGTH = 32


@dataclass(frozen=True)
class PlatformAccess:
    mode: str = "visitor"
    token: str = field(default="", repr=False)
    request_url: str = ""

    @property
    def execution_enabled(self) -> bool:
        return self.mode == "operator" and len(self.token) >= MIN_TOKEN_LENGTH

    def authenticated(self, authorization: str = "") -> bool:
        scheme, _, supplied = (authorization or "").partition(" ")
        return bool(self.execution_enabled and scheme.lower() == "bearer"
                    and hmac.compare_digest(supplied.encode(), self.token.encode()))

    def capabilities(self, authorization: str = "") -> dict:
        return {
            "release_version": "2026.11.public.1",
            "instance_id": os.getenv("SUPPLY_CHAIN_INSTANCE_ID"),
            "mode": self.mode,
            "can_browse": True,
            "execution_enabled": self.execution_enabled,
            "can_operate": self.authenticated(authorization),
            "access_request_url": self.request_url,
            "message": (
                "Browse existing results. New simulations require a separately configured operator deployment."
                if self.mode == "visitor" else
                "Enter an operator access token to control experiments. Model credentials stay on the operator host."
                if self.execution_enabled else
                "Execution is disabled: configure an operator token of at least 32 characters and restart."
            ),
        }


def get_platform_access() -> PlatformAccess:
    # Only explicit process settings select the mode; scripts/.env cannot elevate it.
    mode = os.getenv(MODE_ENV, "visitor").strip().lower()
    if mode not in {"visitor", "operator"}:
        mode = "visitor"
    url = os.getenv(REQUEST_URL_ENV, "").strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        url = ""
        parsed = urlparse("")
    if not ((parsed.scheme in {"https", "http"} and parsed.netloc)
            or (parsed.scheme == "mailto" and parsed.path)):
        url = ""
    return PlatformAccess(mode, os.getenv(TOKEN_ENV, "").strip(), url)


def require_execution_enabled() -> None:
    if not get_platform_access().execution_enabled:
        raise PermissionError("Simulation execution is disabled. Configure and enable a private operator deployment.")


def operator_request_headers() -> dict:
    policy = get_platform_access()
    return {"Authorization": f"Bearer {policy.token}"} if policy.execution_enabled else {}
