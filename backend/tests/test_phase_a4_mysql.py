"""Phase A4 MySQL integration tests requiring a fresh isolated database."""

from __future__ import annotations

import importlib.util
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.services.inventory_receipt_service import (
    ReceiptPostingConflict,
    post_inventory_receipt,
)

A4_MIGRATION = "a4f6b2c9d817_phase_a4_demand_inventory.py"
MIGRATION_FILES = (
    "e4c59ffff8ad_phase_a1_catalog_identity.py",
    "0f6024cb23c9_phase_a2_1_ingestion_foundation.py",
    "ed1d5fd97142_phase_a3_procurement_inbound.py",
    A4_MIGRATION,
)
A4_TABLES = {
    "inventory_ledger_control",
    "inventory_snapshot",
    "inventory_balance",
    "inventory_receipt_posting",
    "inventory_transaction",
    "supply_cancellation",
    "planning_run",
    "planning_item",
    "forecast_demand_week",
    "planning_po_residual_snapshot",
    "planning_supply_snapshot",
    "pab_week",
    "planning_run_finding",
    "shortage_cause",
}
A4_TRIGGERS = {
    "trg_inventory_snapshot_bi",
    "trg_inventory_transaction_bi",
    "trg_inventory_transaction_bu",
    "trg_inventory_transaction_bd",
    "trg_inventory_receipt_posting_bu",
    "trg_inventory_receipt_posting_bd",
    "trg_inbound_shipment_line_a4_bi",
    "trg_inbound_shipment_line_a4_bu",
    "trg_inbound_shipment_line_a4_au",
    "trg_inbound_shipment_line_a4_bd",
    "trg_planning_supply_snapshot_bi",
    "trg_planning_supply_snapshot_bu",
    "trg_planning_supply_snapshot_bd",
    "trg_planning_run_bu",
    "trg_planning_run_bi",
}
A3_TABLES = {
    "purchase_order",
    "purchase_order_line",
    "inbound_shipment",
    "inbound_shipment_line",
    "supplier_capacity",
    "supplier_capacity_reservation",
    "supplier_lead_time",
    "procurement_event",
}
A3_TRIGGERS = {
    "trg_supplier_capacity_bi",
    "trg_supplier_capacity_bu",
    "trg_supplier_lead_time_bi",
    "trg_supplier_lead_time_bu",
    "trg_supplier_capacity_reservation_bi",
    "trg_supplier_capacity_reservation_bu",
    "trg_supplier_capacity_reservation_bd",
    "trg_purchase_order_line_bi",
    "trg_purchase_order_line_bu",
    "trg_inbound_shipment_line_bi",
    "trg_inbound_shipment_line_bu",
    "trg_inbound_shipment_line_ai",
    "trg_inbound_shipment_line_au",
    "trg_inbound_shipment_line_ad",
    "trg_procurement_event_bu",
    "trg_procurement_event_bd",
}


def _migration(filename: str) -> ModuleType:
    path = Path(__file__).parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Alembic migration {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(connection: Connection, filename: str, action: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = _migration(filename)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, action)()


def _table_names(connection: Connection) -> set[str]:
    return set(connection.exec_driver_sql("SHOW TABLES").scalars())


def _trigger_names(connection: Connection) -> set[str]:
    return set(
        connection.execute(
            text(
                "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                "WHERE TRIGGER_SCHEMA = DATABASE()"
            )
        ).scalars()
    )


@pytest.fixture(scope="session")
def mysql_engine() -> Engine:
    configured_url = os.environ.get("A4_TEST_MYSQL_URL")
    if not configured_url:
        pytest.skip("Set A4_TEST_MYSQL_URL to a fresh, isolated Phase A4 test database")

    url = make_url(configured_url)
    if (
        url.get_backend_name() != "mysql"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.port is None
        or url.port == 3306
        or url.database is None
        or not url.database.startswith("test_a4_")
    ):
        pytest.fail(
            "A4_TEST_MYSQL_URL must target loopback MySQL on a non-default port "
            "and a database whose name starts with 'test_a4_'"
        )

    engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    try:
        with engine.begin() as connection:
            actual_database, actual_port, version, trusted = connection.execute(
                text(
                    "SELECT DATABASE(), @@port, VERSION(), "
                    "@@GLOBAL.log_bin_trust_function_creators"
                )
            ).one()
            if actual_database != url.database or actual_port != url.port:
                pytest.fail("The MySQL server/database does not match A4_TEST_MYSQL_URL")
            if not str(version).startswith("8."):
                pytest.fail(f"MySQL 8 is required for these triggers; found {version}")
            if not trusted:
                pytest.fail(
                    "Enable log_bin_trust_function_creators on the isolated MySQL "
                    "test server before running A4 migrations"
                )
            if _table_names(connection):
                pytest.fail(
                    "A4_TEST_MYSQL_URL must target an empty temporary database; "
                    "existing tables will not be modified"
                )
            for filename in MIGRATION_FILES:
                _run_migration(connection, filename, "upgrade")
    except Exception:
        engine.dispose()
        raise

    yield engine
    engine.dispose()


@pytest.fixture
def connection(mysql_engine: Engine):
    with mysql_engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            if transaction.is_active:
                transaction.rollback()


def _insert(connection: Connection, table: str, **values) -> int:
    columns = ", ".join(f"`{column}`" for column in values)
    parameters = ", ".join(f":{column}" for column in values)
    result = connection.execute(
        text(f"INSERT INTO `{table}` ({columns}) VALUES ({parameters})"),
        values,
    )
    return result.lastrowid


def _seed(connection: Connection) -> dict[str, int]:
    suffix = uuid.uuid4().hex[:8].upper()
    uom_id = _insert(
        connection,
        "unit_of_measure",
        code=f"E{suffix}",
        dimension="count",
        decimal_places=0,
        is_active=1,
    )
    sku_id = _insert(
        connection,
        "sku",
        sku_code=f"S{suffix}",
        name="Phase A4 test SKU",
        base_uom_id=uom_id,
        status="active",
    )
    _insert(
        connection,
        "sku_uom",
        sku_id=sku_id,
        uom_id=uom_id,
        to_base_factor=1,
        is_base_uom=1,
        is_purchase_uom=1,
    )
    supplier_id = _insert(
        connection,
        "supplier",
        supplier_code=f"T-{suffix}",
        name="Phase A4 test supplier",
        status="active",
        default_currency="USD",
    )
    site_id = _insert(
        connection,
        "supplier_site",
        supplier_id=supplier_id,
        site_code=f"TS-{suffix}",
        name="Phase A4 test site",
        country_code="US",
        time_zone="UTC",
        status="active",
    )
    _insert(
        connection,
        "supplier_sku",
        supplier_id=supplier_id,
        sku_id=sku_id,
        supplier_item_code=f"ITEM-{suffix}",
        order_uom_id=uom_id,
        minimum_order_qty=0,
        order_multiple=1,
        unit_cost=1,
        currency="USD",
        effective_from=date(2026, 1, 1),
        status="active",
    )
    location_id = _insert(
        connection,
        "location",
        location_code=f"L-{suffix}",
        name="Phase A4 test location",
        location_type="warehouse",
        country_code="US",
        time_zone="UTC",
        status="active",
    )
    order_id = _insert(
        connection,
        "purchase_order",
        purchase_order_number=f"PO-{suffix}",
        supplier_id=supplier_id,
        supplier_site_id=site_id,
        order_date=date(2026, 1, 1),
        currency="USD",
        status="released",
    )
    return {
        "location_id": location_id,
        "order_id": order_id,
        "site_id": site_id,
        "sku_id": sku_id,
        "supplier_id": supplier_id,
        "uom_id": uom_id,
    }


def _po_line(connection: Connection, seed: dict[str, int], *, quantity: int = 20) -> int:
    return _insert(
        connection,
        "purchase_order_line",
        purchase_order_id=seed["order_id"],
        supplier_id=seed["supplier_id"],
        line_number=1,
        sku_id=seed["sku_id"],
        order_uom_id=seed["uom_id"],
        quantity_ordered=quantity,
        order_uom_to_base_factor=1,
        quantity_received_base=0,
        unit_cost=1,
        line_status="open",
    )


def _inbound_line(
    connection: Connection,
    seed: dict[str, int],
    po_line_id: int,
    shipment_no: int,
    *,
    shipped: int = 10,
) -> int:
    shipment_id = _insert(
        connection,
        "inbound_shipment",
        shipment_number=f"ASN-{seed['supplier_id']}-{shipment_no}",
        supplier_id=seed["supplier_id"],
        supplier_site_id=seed["site_id"],
        location_id=seed["location_id"],
        status="booked",
    )
    return _insert(
        connection,
        "inbound_shipment_line",
        inbound_shipment_id=shipment_id,
        supplier_id=seed["supplier_id"],
        line_number=1,
        purchase_order_line_id=po_line_id,
        sku_id=seed["sku_id"],
        uom_id=seed["uom_id"],
        quantity_shipped=shipped,
        quantity_received=0,
        uom_to_base_factor=1,
    )


def _a4_objects_exist(connection: Connection) -> bool:
    return A4_TABLES <= _table_names(connection) and A4_TRIGGERS <= _trigger_names(connection)


def test_upgrade_downgrade_and_reupgrade_only_touch_phase_a4(mysql_engine: Engine):
    migration = _migration(A4_MIGRATION)
    assert migration.revision == "a4f6b2c9d817"
    assert migration.down_revision == "ed1d5fd97142"

    with mysql_engine.connect() as connection:
        assert _a4_objects_exist(connection)
        assert A3_TABLES <= _table_names(connection)
        assert A3_TRIGGERS <= _trigger_names(connection)
        trigger_order = dict(
            connection.execute(
                text(
                    "SELECT TRIGGER_NAME, ACTION_ORDER "
                    "FROM information_schema.TRIGGERS "
                    "WHERE TRIGGER_SCHEMA = DATABASE() "
                    "AND EVENT_OBJECT_TABLE = 'inbound_shipment_line' "
                    "AND EVENT_MANIPULATION IN ('INSERT', 'UPDATE') "
                    "AND ACTION_TIMING = 'BEFORE'"
                )
            ).all()
        )
        assert trigger_order["trg_inbound_shipment_line_bi"] < trigger_order[
            "trg_inbound_shipment_line_a4_bi"
        ]
        assert trigger_order["trg_inbound_shipment_line_bu"] < trigger_order[
            "trg_inbound_shipment_line_a4_bu"
        ]
        after_update_order = dict(
            connection.execute(
                text(
                    "SELECT TRIGGER_NAME, ACTION_ORDER "
                    "FROM information_schema.TRIGGERS "
                    "WHERE TRIGGER_SCHEMA = DATABASE() "
                    "AND EVENT_OBJECT_TABLE = 'inbound_shipment_line' "
                    "AND EVENT_MANIPULATION = 'UPDATE' "
                    "AND ACTION_TIMING = 'AFTER'"
                )
            ).all()
        )
        assert after_update_order["trg_inbound_shipment_line_au"] < after_update_order[
            "trg_inbound_shipment_line_a4_au"
        ]

        _run_migration(connection, A4_MIGRATION, "downgrade")
        assert not (A4_TABLES & _table_names(connection))
        assert not (A4_TRIGGERS & _trigger_names(connection))
        assert A3_TABLES <= _table_names(connection)
        assert A3_TRIGGERS <= _trigger_names(connection)

        _run_migration(connection, A4_MIGRATION, "upgrade")
        assert _a4_objects_exist(connection)
        assert A3_TABLES <= _table_names(connection)
        assert A3_TRIGGERS <= _trigger_names(connection)


def test_receipt_posting_is_idempotent_and_records_signed_base_units(
    connection,
):
    from sqlalchemy import select

    from app.models.planning import InventoryReceiptPosting, InventoryTransaction

    seed = _seed(connection)
    po_line_id = _po_line(connection, seed)
    line_id = _inbound_line(connection, seed, po_line_id, 1)
    shipment_id = connection.execute(
        text(
            "SELECT inbound_shipment_id FROM inbound_shipment_line "
            "WHERE inbound_shipment_line_id = :line_id"
        ),
        {"line_id": line_id},
    ).scalar_one()
    with pytest.raises(DBAPIError, match="Receipts must be posted"):
        connection.execute(
            text(
                "INSERT INTO inbound_shipment_line "
                "(inbound_shipment_id, supplier_id, line_number, "
                "purchase_order_line_id, sku_id, uom_id, quantity_shipped, "
                "quantity_received, uom_to_base_factor) "
                "VALUES (:shipment_id, :supplier_id, 2, :po_line_id, :sku_id, "
                ":uom_id, 5, 1, 1)"
            ),
            {
                "shipment_id": shipment_id,
                "supplier_id": seed["supplier_id"],
                "po_line_id": po_line_id,
                "sku_id": seed["sku_id"],
                "uom_id": seed["uom_id"],
            },
        )

    with Session(bind=connection) as session:
        first = post_inventory_receipt(
            session,
            inbound_shipment_line_id=line_id,
            quantity_delta_uom=Decimal("2"),
            idempotency_key="a4-receipt-first",
        )
        retry = post_inventory_receipt(
            session,
            inbound_shipment_line_id=line_id,
            quantity_delta_uom=Decimal("2.0000"),
            idempotency_key="a4-receipt-first",
        )
        assert retry == first
        with pytest.raises(ReceiptPostingConflict):
            post_inventory_receipt(
                session,
                inbound_shipment_line_id=line_id,
                quantity_delta_uom=Decimal("1"),
                idempotency_key="a4-receipt-first",
            )

        correction = post_inventory_receipt(
            session,
            inbound_shipment_line_id=line_id,
            quantity_delta_uom=Decimal("-0.5"),
            idempotency_key="a4-receipt-correction",
            posting_type="correction",
        )
        transactions = session.scalars(
            select(InventoryTransaction)
            .where(InventoryTransaction.inbound_shipment_line_id == line_id)
            .order_by(InventoryTransaction.ledger_version)
        ).all()
        postings = session.scalars(
            select(InventoryReceiptPosting).where(
                InventoryReceiptPosting.inbound_shipment_line_id == line_id
            )
        ).all()

    assert [transaction.ledger_version for transaction in transactions] == [1, 2]
    assert [transaction.quantity_delta_base for transaction in transactions] == [
        Decimal("2.000000000000"),
        Decimal("-0.500000000000"),
    ]
    assert correction.ledger_version == 2
    assert len(postings) == 2
    assert {posting.posting_status for posting in postings} == {"applied"}
    assert connection.execute(
        text(
            "SELECT quantity_received_base FROM purchase_order_line "
            "WHERE purchase_order_line_id = :line_id"
        ),
        {"line_id": po_line_id},
    ).scalar_one() == Decimal("1.50000000")
    assert connection.execute(
        text(
            "SELECT quantity_received FROM inbound_shipment_line "
            "WHERE inbound_shipment_line_id = :line_id"
        ),
        {"line_id": line_id},
    ).scalar_one() == Decimal("1.5000")
    with pytest.raises(DBAPIError, match="immutable"):
        connection.execute(
            text(
                "UPDATE inventory_receipt_posting SET payload_hash = :hash "
                "WHERE inventory_receipt_posting_id = :posting_id"
            ),
            {"hash": "e" * 64, "posting_id": first.receipt_posting_id},
        )
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text(
                "UPDATE inventory_transaction SET quantity_delta_base = 99 "
                "WHERE inventory_transaction_id = :transaction_id"
            ),
            {"transaction_id": first.inventory_transaction_id},
        )
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text(
                "DELETE FROM inventory_transaction "
                "WHERE inventory_transaction_id = :transaction_id"
            ),
            {"transaction_id": first.inventory_transaction_id},
        )
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text(
                "DELETE FROM inventory_receipt_posting "
                "WHERE inventory_receipt_posting_id = :posting_id"
            ),
            {"posting_id": first.receipt_posting_id},
        )
    with pytest.raises(DBAPIError, match="receipt-linked"):
        connection.execute(
            text(
                "DELETE FROM inbound_shipment_line "
                "WHERE inbound_shipment_line_id = :line_id"
            ),
            {"line_id": line_id},
        )


def test_receipt_trigger_failure_rolls_back_a3_counter_and_ledger_version(connection):
    seed = _seed(connection)
    po_line_id = _po_line(connection, seed)
    line_id = _inbound_line(connection, seed, po_line_id, 2)

    posting_id = _insert(
        connection,
        "inventory_receipt_posting",
        inbound_shipment_line_id=line_id,
        purchase_order_line_id=po_line_id,
        pending_line_id=line_id,
        idempotency_key="a4-atomicity-failure",
        payload_hash="f" * 64,
        posting_type="receipt",
        prior_quantity_received=0,
        target_quantity_received=1,
        quantity_delta_uom=1,
        uom_to_base_factor=1,
        prior_line_received_base=0,
        target_line_received_base=1,
        prior_po_received_base=0,
        target_po_received_base=1,
        posting_status="pending",
    )
    _insert(
        connection,
        "inventory_transaction",
        inventory_receipt_posting_id=None,
        inbound_shipment_line_id=None,
        sku_id=seed["sku_id"],
        location_id=seed["location_id"],
        transaction_type="adjustment",
        quantity_delta_base=1,
        source_event_key=f"receipt-posting:{posting_id}",
    )

    with pytest.raises(DBAPIError):
        connection.execute(
            text(
                "UPDATE inbound_shipment_line SET quantity_received = 1 "
                "WHERE inbound_shipment_line_id = :line_id"
            ),
            {"line_id": line_id},
        )

    assert connection.execute(
        text(
            "SELECT quantity_received FROM inbound_shipment_line "
            "WHERE inbound_shipment_line_id = :line_id"
        ),
        {"line_id": line_id},
    ).scalar_one() == Decimal("0.0000")
    assert connection.execute(
        text(
            "SELECT quantity_received_base FROM purchase_order_line "
            "WHERE purchase_order_line_id = :line_id"
        ),
        {"line_id": po_line_id},
    ).scalar_one() == Decimal("0E-8")
    assert connection.execute(
        text(
            "SELECT posting_status FROM inventory_receipt_posting "
            "WHERE inventory_receipt_posting_id = :posting_id"
        ),
        {"posting_id": posting_id},
    ).scalar_one() == "pending"
    assert connection.execute(
        text(
            "SELECT committed_version FROM inventory_ledger_control "
            "WHERE ledger_key = 'inventory'"
        )
    ).scalar_one() == 1
    assert connection.execute(
        text(
            "SELECT COUNT(*) FROM inventory_transaction "
            "WHERE inventory_receipt_posting_id = :posting_id"
        ),
        {"posting_id": posting_id},
    ).scalar_one() == 0


def test_concurrent_inventory_transactions_receive_serial_ledger_versions(
    connection: Connection,
    mysql_engine: Engine,
):
    seed = _seed(connection)
    initial_version = connection.execute(
        text(
            "SELECT committed_version FROM inventory_ledger_control "
            "WHERE ledger_key = 'inventory'"
        )
    ).scalar_one()
    connection.commit()
    barrier = threading.Barrier(2)

    def insert_transaction(index: int) -> int:
        event_key = f"a4-concurrent-{uuid.uuid4().hex}-{index}"
        with mysql_engine.begin() as worker:
            barrier.wait(timeout=10)
            worker.execute(
                text(
                    "INSERT INTO inventory_transaction "
                    "(sku_id, location_id, transaction_type, quantity_delta_base, "
                    "source_event_key) VALUES (:sku, :location, 'adjustment', 1, :event)"
                ),
                {
                    "sku": seed["sku_id"],
                    "location": seed["location_id"],
                    "event": event_key,
                },
            )
            return worker.execute(
                text(
                    "SELECT ledger_version FROM inventory_transaction "
                    "WHERE source_event_key = :event"
                ),
                {"event": event_key},
            ).scalar_one()

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = list(executor.map(insert_transaction, (1, 2)))

    assert sorted(versions) == [initial_version + 1, initial_version + 2]
    with mysql_engine.connect() as check:
        assert check.execute(
            text(
                "SELECT committed_version FROM inventory_ledger_control "
                "WHERE ledger_key = 'inventory'"
            )
        ).scalar_one() == initial_version + 2


def test_snapshot_watermark_and_run_cutoff_are_locked_ledger_versions(connection):
    seed = _seed(connection)
    initial_version = connection.execute(
        text(
            "SELECT committed_version FROM inventory_ledger_control "
            "WHERE ledger_key = 'inventory'"
        )
    ).scalar_one()
    _insert(
        connection,
        "inventory_transaction",
        sku_id=seed["sku_id"],
        location_id=seed["location_id"],
        transaction_type="adjustment",
        quantity_delta_base=1,
        source_event_key="a4-watermark-before-snapshot",
    )
    snapshot_id = _insert(
        connection,
        "inventory_snapshot",
        snapshot_key=f"snap-{uuid.uuid4().hex}",
        ledger_watermark=0,
        source_name="isolated-test",
        source_checksum="0" * 64,
        snapshot_status="validated",
    )
    run_id = _insert(
        connection,
        "planning_run",
        run_key=f"watermark-{uuid.uuid4().hex}",
        scenario_key="test",
        horizon_start_week=date(2026, 1, 5),
        horizon_weeks=52,
        inventory_snapshot_id=snapshot_id,
        inventory_cutoff_version=0,
        algorithm_version="test",
        severity_policy_version="test",
        input_fingerprint="b" * 64,
        run_status="draft",
    )
    _insert(
        connection,
        "inventory_transaction",
        sku_id=seed["sku_id"],
        location_id=seed["location_id"],
        transaction_type="adjustment",
        quantity_delta_base=1,
        source_event_key="a4-watermark-after-run",
    )

    assert connection.execute(
        text(
            "SELECT ledger_watermark FROM inventory_snapshot "
            "WHERE inventory_snapshot_id = :id"
        ),
        {"id": snapshot_id},
    ).scalar_one() == initial_version + 1
    assert connection.execute(
        text(
            "SELECT inventory_cutoff_version FROM planning_run "
            "WHERE planning_run_id = :id"
        ),
        {"id": run_id},
    ).scalar_one() == initial_version + 1
    assert connection.execute(
        text(
            "SELECT committed_version FROM inventory_ledger_control "
            "WHERE ledger_key = 'inventory'"
        )
    ).scalar_one() == initial_version + 2
    with pytest.raises(DBAPIError, match="Planning run inputs and version are immutable"):
        connection.execute(
            text(
                "UPDATE planning_run SET inventory_cutoff_version = 0 "
                "WHERE planning_run_id = :id"
            ),
            {"id": run_id},
        )


def test_po_destination_allocations_cannot_exceed_or_underfill_residual(connection):
    seed = _seed(connection)
    second_location_id = _insert(
        connection,
        "location",
        location_code=f"L2-{uuid.uuid4().hex[:8].upper()}",
        name="Phase A4 second location",
        location_type="warehouse",
        country_code="US",
        time_zone="UTC",
        status="active",
    )
    po_line_id = _po_line(connection, seed, quantity=10)
    snapshot_id = _insert(
        connection,
        "inventory_snapshot",
        snapshot_key=f"snap-{uuid.uuid4().hex}",
        ledger_watermark=0,
        source_name="isolated-test",
        source_checksum="0" * 64,
        snapshot_status="validated",
    )

    def make_run(run_key: str) -> tuple[int, int]:
        run_id = _insert(
            connection,
            "planning_run",
            run_key=run_key,
            scenario_key="test",
            horizon_start_week=date(2026, 1, 5),
            horizon_weeks=52,
            inventory_snapshot_id=snapshot_id,
            inventory_cutoff_version=0,
            algorithm_version="test",
            severity_policy_version="test",
            input_fingerprint="a" * 64,
            run_status="draft",
        )
        for location_id in (seed["location_id"], second_location_id):
            _insert(
                connection,
                "planning_item",
                planning_run_id=run_id,
                sku_id=seed["sku_id"],
                location_id=location_id,
            )
        residual_id = _insert(
            connection,
            "planning_po_residual_snapshot",
            planning_run_id=run_id,
            purchase_order_line_id=po_line_id,
            ordered_base=10,
            received_base=0,
            scheduled_base=0,
            cancelled_base=0,
            residual_base=10,
        )
        return run_id, residual_id

    def allocate(
        run_id: int,
        residual_id: int,
        location_id: int,
        allocation_no: int,
        quantity: int,
    ) -> int:
        return _insert(
            connection,
            "planning_supply_snapshot",
            planning_run_id=run_id,
            source_kind="po_allocation",
            planning_po_residual_snapshot_id=residual_id,
            sku_id=seed["sku_id"],
            destination_location_id=location_id,
            allocation_no=allocation_no,
            allocation_idempotency_key=f"{run_id}-{allocation_no}",
            candidate_quantity_base=quantity,
            firm_quantity_base=quantity,
            unconfirmed_quantity_base=0,
            capacity_exceeded_quantity_base=0,
            supply_status="undated",
        )

    incomplete_run, incomplete_residual = make_run("incomplete-allocation")
    incomplete_allocation = allocate(
        incomplete_run,
        incomplete_residual,
        seed["location_id"],
        1,
        4,
    )
    with pytest.raises(DBAPIError, match="allocations exceed"):
        connection.execute(
            text(
                "UPDATE planning_supply_snapshot "
                "SET candidate_quantity_base = 11 "
                "WHERE planning_supply_snapshot_id = :id"
            ),
            {"id": incomplete_allocation},
        )
    connection.execute(
        text("UPDATE planning_run SET run_status = 'completed' WHERE planning_run_id = :id"),
        {"id": incomplete_run},
    )
    with pytest.raises(DBAPIError, match="allocations do not reconcile"):
        connection.execute(
            text("UPDATE planning_run SET run_status = 'published' WHERE planning_run_id = :id"),
            {"id": incomplete_run},
        )
    with pytest.raises(DBAPIError, match="editable run"):
        connection.execute(
            text(
                "DELETE FROM planning_supply_snapshot "
                "WHERE planning_supply_snapshot_id = :id"
            ),
            {"id": incomplete_allocation},
        )

    complete_run, complete_residual = make_run("complete-allocation")
    allocate(complete_run, complete_residual, seed["location_id"], 1, 4)
    with pytest.raises(DBAPIError, match="allocations exceed"):
        allocate(complete_run, complete_residual, second_location_id, 2, 7)
    allocate(complete_run, complete_residual, second_location_id, 2, 6)
    connection.execute(
        text("UPDATE planning_run SET run_status = 'completed' WHERE planning_run_id = :id"),
        {"id": complete_run},
    )
    connection.execute(
        text("UPDATE planning_run SET run_status = 'published' WHERE planning_run_id = :id"),
        {"id": complete_run},
    )
    assert connection.execute(
        text("SELECT run_status FROM planning_run WHERE planning_run_id = :id"),
        {"id": complete_run},
    ).scalar_one() == "published"
