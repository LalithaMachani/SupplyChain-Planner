"""MySQL-only Phase A3 trigger tests against a fresh, isolated test database."""

from __future__ import annotations

import importlib.util
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError


PHASE_A3_TABLES = {
    "purchase_order",
    "purchase_order_line",
    "inbound_shipment",
    "inbound_shipment_line",
    "supplier_capacity",
    "supplier_capacity_reservation",
    "supplier_lead_time",
    "procurement_event",
}
PHASE_A3_TRIGGERS = {
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
MIGRATION_FILES = (
    "e4c59ffff8ad_phase_a1_catalog_identity.py",
    "0f6024cb23c9_phase_a2_1_ingestion_foundation.py",
    "ed1d5fd97142_phase_a3_procurement_inbound.py",
)


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
    configured_url = os.environ.get("A3_TEST_MYSQL_URL")
    if not configured_url:
        pytest.skip("Set A3_TEST_MYSQL_URL to a fresh, isolated Phase A3 test database")

    url = make_url(configured_url)
    if (
        url.get_backend_name() != "mysql"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.port is None
        or url.port == 3306
        or url.database is None
        or not url.database.startswith("test_a3_")
    ):
        pytest.fail(
            "A3_TEST_MYSQL_URL must target a loopback MySQL server on a non-default "
            "port and a database whose name starts with 'test_a3_'"
        )

    engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    try:
        with engine.begin() as connection:
            actual_database, actual_port, version = connection.execute(
                text("SELECT DATABASE(), @@port, VERSION()")
            ).one()
            if actual_database != url.database or actual_port != url.port:
                pytest.fail("The MySQL server/database does not match A3_TEST_MYSQL_URL")
            if not str(version).startswith("8."):
                pytest.fail(f"MySQL 8 is required for these triggers; found {version}")
            if _table_names(connection):
                pytest.fail(
                    "A3_TEST_MYSQL_URL must point to an empty temporary database; "
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


def _seed(connection: Connection, order_uom: str = "EA") -> dict[str, int]:
    suffix = uuid.uuid4().hex[:8].upper()
    ea_id = _insert(
        connection,
        "unit_of_measure",
        code=f"E{suffix}",
        dimension="count",
        decimal_places=0,
        is_active=1,
    )
    case_id = _insert(
        connection,
        "unit_of_measure",
        code=f"C{suffix}",
        dimension="count",
        decimal_places=2,
        is_active=1,
    )
    sku_id = _insert(
        connection,
        "sku",
        sku_code=f"S{suffix}",
        name="Phase A3 test SKU",
        base_uom_id=ea_id,
        status="active",
    )
    _insert(
        connection,
        "sku_uom",
        sku_id=sku_id,
        uom_id=ea_id,
        to_base_factor=1,
        is_base_uom=1,
        is_purchase_uom=1,
    )
    _insert(
        connection,
        "sku_uom",
        sku_id=sku_id,
        uom_id=case_id,
        to_base_factor=12,
        is_base_uom=0,
        is_purchase_uom=1,
    )
    supplier_id = _insert(
        connection,
        "supplier",
        supplier_code=f"T-{suffix}",
        name="Phase A3 test supplier",
        status="active",
        default_currency="USD",
    )
    site_id = _insert(
        connection,
        "supplier_site",
        supplier_id=supplier_id,
        site_code=f"TS-{suffix}",
        name="Phase A3 test site",
        country_code="US",
        time_zone="UTC",
        status="active",
    )
    uom_id = ea_id if order_uom == "EA" else case_id
    factor = 1 if order_uom == "EA" else 12
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
        name="Phase A3 test destination",
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
        "case_uom_id": case_id,
        "ea_uom_id": ea_id,
        "factor": factor,
        "location_id": location_id,
        "order_id": order_id,
        "order_uom_id": uom_id,
        "site_id": site_id,
        "sku_id": sku_id,
        "supplier_id": supplier_id,
    }


def _po_line(
    connection: Connection,
    seed: dict[str, int],
    *,
    quantity: int = 20,
    uom_id: int | None = None,
    factor: int | None = None,
    line_number: int = 1,
) -> int:
    return _insert(
        connection,
        "purchase_order_line",
        purchase_order_id=seed["order_id"],
        supplier_id=seed["supplier_id"],
        line_number=line_number,
        sku_id=seed["sku_id"],
        order_uom_id=uom_id if uom_id is not None else seed["order_uom_id"],
        quantity_ordered=quantity,
        order_uom_to_base_factor=factor if factor is not None else seed["factor"],
        quantity_received_base=0,
        unit_cost=1,
        line_status="open",
    )


def _capacity(
    connection: Connection,
    seed: dict[str, int],
    *,
    start: date = date(2026, 1, 1),
    end: date | None = date(2026, 1, 31),
    quantity: int = 10,
) -> int:
    return _insert(
        connection,
        "supplier_capacity",
        supplier_id=seed["supplier_id"],
        sku_id=seed["sku_id"],
        capacity_uom_id=seed["ea_uom_id"],
        effective_from=start,
        effective_to=end,
        capacity_quantity=quantity,
        reserved_quantity=0,
    )


def _reservation(
    connection: Connection,
    seed: dict[str, int],
    capacity_id: int,
    po_line_id: int,
    quantity: int,
) -> int:
    return _insert(
        connection,
        "supplier_capacity_reservation",
        supplier_capacity_id=capacity_id,
        purchase_order_line_id=po_line_id,
        supplier_id=seed["supplier_id"],
        sku_id=seed["sku_id"],
        capacity_uom_id=seed["ea_uom_id"],
        reservation_quantity=quantity,
        status="reserved",
    )


def _capacity_reserved(connection: Connection, capacity_id: int):
    return connection.execute(
        text(
            "SELECT reserved_quantity FROM supplier_capacity "
            "WHERE supplier_capacity_id = :capacity_id"
        ),
        {"capacity_id": capacity_id},
    ).scalar_one()


def _shipment(connection: Connection, seed: dict[str, int], number: int) -> int:
    return _insert(
        connection,
        "inbound_shipment",
        shipment_number=f"ASN-{seed['supplier_id']}-{number}",
        supplier_id=seed["supplier_id"],
        supplier_site_id=seed["site_id"],
        location_id=seed["location_id"],
        status="booked",
    )


def _receipt(
    connection: Connection,
    seed: dict[str, int],
    po_line_id: int,
    number: int,
    *,
    quantity: float,
    received: float,
    uom_id: int | None = None,
    factor: int = 1,
) -> int:
    shipment_id = _shipment(connection, seed, number)
    return _insert(
        connection,
        "inbound_shipment_line",
        inbound_shipment_id=shipment_id,
        supplier_id=seed["supplier_id"],
        line_number=1,
        purchase_order_line_id=po_line_id,
        sku_id=seed["sku_id"],
        uom_id=uom_id if uom_id is not None else seed["ea_uom_id"],
        quantity_shipped=quantity,
        quantity_received=received,
        uom_to_base_factor=factor,
    )


def _received_base(connection: Connection, po_line_id: int):
    return connection.execute(
        text(
            "SELECT quantity_received_base FROM purchase_order_line "
            "WHERE purchase_order_line_id = :line_id"
        ),
        {"line_id": po_line_id},
    ).scalar_one()


def test_capacity_reservation_enforces_limit_and_updates_counter(connection):
    seed = _seed(connection)
    capacity_id = _capacity(connection, seed, quantity=10)
    first_line = _po_line(connection, seed, line_number=1)
    second_line = _po_line(connection, seed, line_number=2)
    reservation_id = _reservation(connection, seed, capacity_id, first_line, 6)

    assert _capacity_reserved(connection, capacity_id) == 6
    with pytest.raises(DBAPIError, match="reservation exceeds capacity"):
        _reservation(connection, seed, capacity_id, second_line, 5)

    connection.execute(
        text(
            "UPDATE supplier_capacity_reservation "
            "SET reservation_quantity = 8 "
            "WHERE supplier_capacity_reservation_id = :reservation_id"
        ),
        {"reservation_id": reservation_id},
    )
    assert _capacity_reserved(connection, capacity_id) == 8
    with pytest.raises(DBAPIError, match="reservation exceeds capacity"):
        connection.execute(
            text(
                "UPDATE supplier_capacity_reservation "
                "SET reservation_quantity = 11 "
                "WHERE supplier_capacity_reservation_id = :reservation_id"
            ),
            {"reservation_id": reservation_id},
        )
    connection.execute(
        text(
            "UPDATE supplier_capacity_reservation SET status = 'released' "
            "WHERE supplier_capacity_reservation_id = :reservation_id"
        ),
        {"reservation_id": reservation_id},
    )
    assert _capacity_reserved(connection, capacity_id) == 0


def test_concurrent_reservations_cannot_exceed_capacity(connection, mysql_engine):
    seed = _seed(connection)
    capacity_id = _capacity(connection, seed, quantity=10)
    lines = [_po_line(connection, seed, line_number=n) for n in (1, 2)]
    connection.commit()
    barrier = threading.Barrier(2)

    def reserve(line_id: int) -> str:
        try:
            with mysql_engine.begin() as worker:
                barrier.wait(timeout=10)
                _reservation(worker, seed, capacity_id, line_id, 6)
            return "reserved"
        except DBAPIError as error:
            if "reservation exceeds capacity" not in str(error).lower():
                raise
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(reserve, lines))

    assert sorted(outcomes) == ["rejected", "reserved"]
    with mysql_engine.connect() as check:
        assert _capacity_reserved(check, capacity_id) == 6


def test_capacity_periods_reject_overlap_and_allow_separate_periods(connection):
    seed = _seed(connection)
    _capacity(
        connection,
        seed,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
    )

    with pytest.raises(DBAPIError, match="capacity effective dates overlap"):
        _capacity(
            connection,
            seed,
            start=date(2026, 1, 31),
            end=date(2026, 2, 15),
        )
    _capacity(
        connection,
        seed,
        start=date(2026, 2, 1),
        end=date(2026, 2, 28),
    )


def test_lead_time_periods_reject_overlap_and_allow_separate_periods(connection):
    seed = _seed(connection)
    values = {
        "supplier_id": seed["supplier_id"],
        "supplier_site_id": seed["site_id"],
        "sku_id": seed["sku_id"],
        "location_id": seed["location_id"],
        "lead_time_days": 5,
    }
    _insert(
        connection,
        "supplier_lead_time",
        **values,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 1, 31),
    )
    with pytest.raises(DBAPIError, match="lead-time effective dates overlap"):
        _insert(
            connection,
            "supplier_lead_time",
            **values,
            effective_from=date(2026, 1, 31),
            effective_to=date(2026, 2, 15),
        )
    _insert(
        connection,
        "supplier_lead_time",
        **values,
        effective_from=date(2026, 2, 1),
        effective_to=date(2026, 2, 28),
    )


def test_partial_receipts_adjust_received_base_and_reject_over_receipt(connection):
    seed = _seed(connection, order_uom="CASE")
    po_line_id = _po_line(connection, seed, quantity=2)
    first = _receipt(
        connection, seed, po_line_id, 1, quantity=1, received=1,
        uom_id=seed["case_uom_id"], factor=12,
    )
    second = _receipt(
        connection, seed, po_line_id, 2, quantity=1, received=1,
        uom_id=seed["case_uom_id"], factor=12,
    )
    assert _received_base(connection, po_line_id) == 24

    with pytest.raises(DBAPIError, match="Total receipts exceed"):
        _receipt(
            connection, seed, po_line_id, 3, quantity=0.1, received=0.1,
            uom_id=seed["case_uom_id"], factor=12,
        )
    connection.execute(
        text(
            "UPDATE inbound_shipment_line SET quantity_received = 0.5 "
            "WHERE inbound_shipment_line_id = :line_id"
        ),
        {"line_id": second},
    )
    assert _received_base(connection, po_line_id) == 18
    connection.execute(
        text(
            "DELETE FROM inbound_shipment_line "
            "WHERE inbound_shipment_line_id = :line_id"
        ),
        {"line_id": first},
    )
    assert _received_base(connection, po_line_id) == 6


def test_sku_specific_uom_factor_is_enforced_and_receipt_reconciles_base_units(
    connection,
):
    seed = _seed(connection)
    po_line_id = _po_line(connection, seed, quantity=24)
    _receipt(
        connection, seed, po_line_id, 1, quantity=2, received=2,
        uom_id=seed["case_uom_id"], factor=12,
    )
    assert _received_base(connection, po_line_id) == 24

    with pytest.raises(DBAPIError, match="UOM factor must match"):
        _receipt(
            connection, seed, po_line_id, 2, quantity=1, received=1,
            uom_id=seed["case_uom_id"], factor=11,
        )
    assert _received_base(connection, po_line_id) == 24


def test_procurement_events_are_insertable_and_append_only(connection):
    seed = _seed(connection)
    event_id = _insert(
        connection,
        "procurement_event",
        purchase_order_id=seed["order_id"],
        event_type="created",
        source="phase-a3-test",
    )

    assert connection.execute(
        text(
            "SELECT COUNT(*) FROM procurement_event "
            "WHERE procurement_event_id = :event_id"
        ),
        {"event_id": event_id},
    ).scalar_one() == 1
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text(
                "UPDATE procurement_event SET source = 'modified' "
                "WHERE procurement_event_id = :event_id"
            ),
            {"event_id": event_id},
        )
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(
            text(
                "DELETE FROM procurement_event "
                "WHERE procurement_event_id = :event_id"
            ),
            {"event_id": event_id},
        )


def test_migration_upgrade_downgrade_and_reupgrade_are_scoped_to_phase_a3(
    mysql_engine,
):
    migration_file = MIGRATION_FILES[-1]
    with mysql_engine.connect() as connection:
        before_tables = _table_names(connection)
        assert PHASE_A3_TABLES <= before_tables
        assert len(PHASE_A3_TABLES) == 8
        assert _trigger_names(connection) == PHASE_A3_TRIGGERS

        base_tables = before_tables - PHASE_A3_TABLES
        assert {"supplier", "sku", "supplier_sku", "ingestion_batch"} <= base_tables
        _run_migration(connection, migration_file, "downgrade")
        connection.commit()

        assert _table_names(connection) == base_tables
        assert _trigger_names(connection) == set()

        _run_migration(connection, migration_file, "upgrade")
        connection.commit()
        assert PHASE_A3_TABLES <= _table_names(connection)
        assert _trigger_names(connection) == PHASE_A3_TRIGGERS
