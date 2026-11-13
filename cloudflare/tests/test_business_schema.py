"""Check the complete generated business schema against its actual storage rules."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
MANIFEST = json.loads((ROOT / "src/erp_cf/business_schema.json").read_text())


def database():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    for path in sorted((ROOT / "migrations").glob("*.sql")):
        connection.executescript(path.read_text())
    return connection


def test_schema_covers_all_current_models_and_source_files():
    repo = ROOT.parent
    for source, expected_hash in MANIFEST["sources"].items():
        assert hashlib.sha256((repo / source).read_bytes()).hexdigest() == expected_hash, source
    expected = {table["name"] for table in MANIFEST["tables"]}
    assert len(expected) == 45
    db = database()
    actual = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    assert {name for name in actual if not name.startswith("platform_")} - {
        "cf_commands",
        "cf_assertions",
        "cf_outbox",
        "cf_stock_move_states",
        "cf_settlement_effects",
        "cf_business_epochs",
        "cf_event_receipts",
        "cf_notification_keys",
        "cf_mail_outbox",
        "cf_mail_attempts",
        "cf_dead_letters",
        "cf_job_runs",
        "cf_scheduler_state",
        "cf_ai_batches",
        "cf_ai_tasks",
        "platform_schema_migrations",
    } == expected
    for table in expected:
        assert "_revision" in {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_decimal_scales_and_default_storage_preserve_the_original_contract():
    tables = {table["name"]: table for table in MANIFEST["tables"]}
    invoice = {col["name"]: col for col in tables["invoicing_invoice_lines"]["columns"]}
    assert {name: invoice[name]["scale"] for name in ["qty", "unit_price", "line_total"]} == {
        "qty": 4,
        "unit_price": 6,
        "line_total": 2,
    }
    db = database()
    identity = "00000000-0000-7000-8000-000000000001"
    db.execute(
        "INSERT INTO catalog_products (id,org_id,sku,name) VALUES (?,?,?,?)",
        (identity, identity, "A", "Product"),
    )
    row = db.execute(
        "SELECT sale_price,cost_price,typeof(sale_price),is_sellable,_revision "
        "FROM catalog_products WHERE id=?",
        (identity,),
    ).fetchone()
    assert row == (0, 0, "integer", 1, 0)
    # SQLite must reject implicit REAL storage and coefficients outside NUMERIC(18,6).
    for amount in ["0.1", "1000000000000000000"]:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE catalog_products SET sale_price=? WHERE id=?", (amount, identity))
    db.execute(
        "UPDATE catalog_products SET sale_price=CAST(? AS INTEGER) WHERE id=?",
        ("999999999999999999", identity),
    )
    assert db.execute("SELECT CAST(sale_price AS TEXT) FROM catalog_products").fetchone()[0] == (
        "999999999999999999"
    )


def test_foreign_keys_json_and_uniqueness_remain_enforced():
    db = database()
    one = "00000000-0000-7000-8000-000000000001"
    two = "00000000-0000-7000-8000-000000000002"
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO core_user_roles (user_id,role_id) VALUES (?,?)", (one, two))
    db.execute(
        "INSERT INTO crm_contacts (id,org_id,code,name) VALUES (?,?,?,?)", (one, one, "C", "C")
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE crm_contacts SET emails='invalid-json' WHERE id=?", (one,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO crm_contacts (id,org_id,code,name) VALUES (?,?,?,?)",
            (two, one, "C", "duplicate"),
        )
