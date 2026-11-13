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
ACCOUNTING = load_suite("test_accounting")
DOCUMENTS = load_suite("test_documents")
QUOTES = load_suite("test_quotations")
CASES = [
    function
    for suite in [CORE, MASTER, QUOTES]
    for name, function in vars(suite).items()
    if name.startswith("test_")
    and inspect.iscoroutinefunction(function)
    and set(inspect.signature(function).parameters) == {"client"}
    and name
    not in {"test_ai_description_disabled_without_key", "test_expired_quote_cannot_be_accepted"}
]
CASES.append(ACCOUNTING.test_manual_entry_must_balance)
DOCUMENT_CASES = [
    function
    for name, function in vars(DOCUMENTS).items()
    if name.startswith("test_") and inspect.iscoroutinefunction(function)
]


@pytest.mark.parametrize("case", CASES, ids=lambda f: f.__name__)
async def test_original_http_contract(case, client):
    await case(client)


@pytest.mark.parametrize("case", DOCUMENT_CASES, ids=lambda f: f.__name__)
async def test_original_document_contract(case, client):
    await case(client, {})


async def test_original_login_validation_variants(client):
    await CORE.test_login_validation(client, "not-an-email", "longenough1")
    await CORE.test_login_validation(client, "admin@example.com", "")


async def test_original_ledger_contract_after_manual_post(client):
    await ACCOUNTING.test_manual_entry_must_balance(client)
    await ACCOUNTING.test_ledger_running_balance(client)
