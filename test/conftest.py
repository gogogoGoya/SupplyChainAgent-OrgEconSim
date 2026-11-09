import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))



import pytest


@pytest.fixture(autouse=True)
def configured_operator_for_business_tests(monkeypatch, request):
    """Business regressions run as an explicitly configured private operator."""
    if request.module.__name__.endswith("test_platform_access"):
        return
    monkeypatch.setenv("SUPPLY_CHAIN_PLATFORM_MODE", "operator")
    monkeypatch.setenv("SUPPLY_CHAIN_OPERATOR_TOKEN", "test-operator-access-" + "x" * 32)
