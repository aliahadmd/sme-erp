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
INVENTORY = load_suite("test_inventory")
INVOICING = load_suite("test_invoicing")
HR = load_suite("test_hr")
AUDIT = load_suite("test_audit_fixes")
PENDING_AUDIT = {
    "test_c2_failed_posting_rolls_back_document",  # Original server monkeypatch.
    "test_h2_h3_invoice_email_queues_and_notification_survives",  # Queue consumers.
    "test_h5_tax_summary_splits_output_and_input",  # Remaining reports.
    "test_m4_credit_notes_are_never_overdue",  # Overdue notification jobs.
}
CASES = [
    function
    for suite in [CORE, MASTER, QUOTES, INVOICING, HR, AUDIT]
    for name, function in vars(suite).items()
    if name.startswith("test_")
    and inspect.iscoroutinefunction(function)
    and set(inspect.signature(function).parameters) == {"client"}
    and name
    not in {"test_ai_description_disabled_without_key", "test_expired_quote_cannot_be_accepted"}
    and name not in PENDING_AUDIT
]
CASES.append(ACCOUNTING.test_manual_entry_must_balance)
CASES.extend(
    [ACCOUNTING.test_business_events_post_balanced_journals, ACCOUNTING.test_void_invoice_reverses]
)
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


async def test_original_inventory_http_scenarios_in_reference_order(client):
    fixture = {}
    for case in [
        INVENTORY.test_moving_average_costing,
        INVENTORY.test_delivery_cogs_and_negative_guard,
        INVENTORY.test_adjustment_and_low_stock,
        INVENTORY.test_receipt_void_reverses_stock,
        INVENTORY.test_warehouse_rbac,
        INVENTORY.test_over_delivery_and_partial_remaining,
        INVENTORY.test_receipt_of_fully_received_po_rejected,
    ]:
        await case(client, fixture)
