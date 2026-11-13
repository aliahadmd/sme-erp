"""Run existing black-box API scenarios unchanged against the new runtime.

The old suites are loaded without their PostgreSQL fixtures. This checks the
original behavior independently of the new repository/command implementation.
AI cases will join this matrix when the Workers AI/job handlers are ported.
"""

import importlib.util
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def load_suite(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "api/tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = load_suite("test_core")
MASTER = load_suite("test_master_data")
CASES = [
    function
    for suite in [CORE, MASTER]
    for name, function in vars(suite).items()
    if name.startswith("test_")
    and inspect.iscoroutinefunction(function)
    and set(inspect.signature(function).parameters) == {"client"}
    and name != "test_ai_description_disabled_without_key"
]


@pytest.mark.parametrize("case", CASES, ids=lambda f: f.__name__)
async def test_original_http_contract(case, client):
    await case(client)


async def test_original_login_validation_variants(client):
    await CORE.test_login_validation(client, "not-an-email", "longenough1")
    await CORE.test_login_validation(client, "admin@example.com", "")
