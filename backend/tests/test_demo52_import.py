from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models import (
    ForecastDemandWeek,
    InboundShipmentLine,
    InventoryBalance,
    InventoryLedgerControl,
    InventoryReceiptPosting,
    InventorySnapshot,
    InventoryTransaction,
    Location,
    PlanningItem,
    PlanningPoResidualSnapshot,
    PlanningRun,
    PlanningRunFinding,
    PlanningSupplySnapshot,
    PurchaseOrder,
    PurchaseOrderLine,
    SKUUOM,
    StagingRecord,
    SupplierCapacity,
    UnitOfMeasure,
    ValidationFinding,
)
from app.services.planning_service import calculate_pab_values
from scripts.import_demo52 import (
    DEFAULT_DATA_ROOT,
    ImportDataError,
    build_import_plan,
    create_planning_run_via_api,
    import_demo52,
    load_dataset,
    validate_staged_batches,
)


def _session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        session.add(InventoryLedgerControl(ledger_key="inventory", committed_version=0))
        session.commit()
    return engine, factory


def test_plan_reports_counts_exclusions_and_separates_ground_truth():
    plan = build_import_plan(DEFAULT_DATA_ROOT)
    counts = plan["counts"]

    assert counts["unit_of_measure"] == 6
    assert counts["location"] == 4
    assert counts["sku"] == 24
    assert counts["sku_uom"] == 48
    assert counts["supplier"] == 8
    assert counts["supplier_site"] == 8
    assert counts["supplier_sku"] == 48
    assert counts["supplier_capacity"] == 48
    assert counts["supplier_lead_time"] == 191
    assert counts["purchase_order"] == counts["purchase_order_line"] == 34
    assert counts["inbound_shipment"] == counts["inbound_shipment_line"] == 1
    assert counts["inventory_snapshot"] == 1
    assert counts["inventory_balance"] == 96
    assert counts["staging_batch"] == 5
    assert counts["staging_record"] == 5139
    assert counts["excluded_partial_po_shipment_pairs"] == 6
    assert counts["excluded_invalid_inventory_rows"] == 1
    assert counts["duplicate_inbound_rows_staged"] == 2
    assert counts["duplicate_inbound_rows_operationally_omitted"] == 1
    assert counts["planning_scope_items"] == 96
    assert counts["forecast_rows"] == counts["pab_rows_if_calculated"] == 4992
    assert plan["ground_truth_loaded"] is False
    assert all(
        entry["classification"] != "ground_truth"
        for entry in load_dataset(DEFAULT_DATA_ROOT).manifest["files"]
        if entry["path"].startswith("input/")
    )


def test_import_validates_inputs_preserves_zero_and_is_idempotent():
    engine, factory = _session_factory()
    try:
        with factory() as session:
            result = import_demo52(session, DEFAULT_DATA_ROOT)
        assert result.already_imported is False

        validation = validate_staged_batches(factory, result.batch_ids)
        assert validation["forecast_demand_0001.csv"].accepted_count == 4992
        assert validation["inbound_shipment_lines.csv"].finding_count >= 2
        assert validation["sku_substitution.csv"].rejected_count == 2
        assert "CAPACITY_EXCEEDED" in validation["purchase_order_lines.csv"].unsupported_rules
        assert "MISSING_LEAD_TIME" in validation["purchase_order_lines.csv"].unsupported_rules

        with factory() as session:
            assert session.scalar(select(func.count()).select_from(PurchaseOrder)) == 34
            assert session.scalar(select(func.count()).select_from(PurchaseOrderLine)) == 34
            assert session.scalar(select(func.count()).select_from(InboundShipmentLine)) == 1
            assert session.scalar(select(func.count()).select_from(InventoryBalance)) == 96
            assert session.scalar(select(func.count()).select_from(StagingRecord)) == 5139
            assert session.scalar(select(func.count()).select_from(ValidationFinding)) > 0
            assert session.scalar(select(func.count()).select_from(InventoryReceiptPosting)) == 0
            assert session.scalar(select(func.count()).select_from(InventoryTransaction)) == 0
            assert session.scalar(select(InventoryLedgerControl.committed_version)) == 0
            assert session.scalar(select(InventoryBalance.quantity_on_hand_base).where(
                InventoryBalance.sku_id == 1,
                InventoryBalance.location_id == 1,
            )) == 0
            assert session.get(SKUUOM, (1, 1)) is not None
            assert session.scalar(select(InboundShipmentLine.quantity_received)) == 0
            invalid_inventory = session.scalar(
                select(StagingRecord).where(
                    StagingRecord.ingestion_batch_id == result.batch_ids["inventory.csv"],
                    StagingRecord.source_row_number == 1,
                )
            )
            valid_zero = session.scalar(
                select(StagingRecord).where(
                    StagingRecord.ingestion_batch_id == result.batch_ids["inventory.csv"],
                    StagingRecord.source_row_number == 2,
                )
            )
            assert invalid_inventory.disposition == "rejected"
            assert valid_zero.disposition == "accepted"
            assert session.scalar(
                select(ValidationFinding.validation_finding_id)
                .join(StagingRecord)
                .where(
                    StagingRecord.staging_record_id == invalid_inventory.staging_record_id,
                    ValidationFinding.rule_code == "UOM_MISMATCH",
                )
            ) is not None

        with factory() as session:
            rerun = import_demo52(session, DEFAULT_DATA_ROOT)
        assert rerun.already_imported is True
        assert rerun.batch_ids == result.batch_ids
    finally:
        engine.dispose()


def test_import_rollback_leaves_no_partial_operational_rows():
    engine, factory = _session_factory()

    def reject_capacity_insert(mapper, connection, target):
        raise RuntimeError("simulated insert failure")

    event.listen(SupplierCapacity, "before_insert", reject_capacity_insert)
    try:
        with factory() as session:
            with pytest.raises(RuntimeError, match="simulated insert failure"):
                import_demo52(session, DEFAULT_DATA_ROOT)
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(UnitOfMeasure)) == 0
            assert session.scalar(select(func.count()).select_from(Location)) == 0
            assert session.scalar(select(func.count()).select_from(InventorySnapshot)) == 0
            assert session.scalar(select(func.count()).select_from(StagingRecord)) == 0
            assert session.scalar(select(InventoryLedgerControl.committed_version)) == 0
    finally:
        event.remove(SupplierCapacity, "before_insert", reject_capacity_insert)
        engine.dispose()


def test_existing_user_rows_abort_without_overwrite():
    engine, factory = _session_factory()
    try:
        with factory() as session:
            session.add(
                Location(
                    location_id=90,
                    location_code="KEEP",
                    name="Existing location",
                    location_type="warehouse",
                    country_code="US",
                    time_zone="UTC",
                    status="active",
                )
            )
            session.commit()
        with factory() as session:
            with pytest.raises(ImportDataError, match="target tables are not empty"):
                import_demo52(session, DEFAULT_DATA_ROOT)
        with factory() as session:
            location = session.get(Location, 90)
            assert location is not None and location.location_code == "KEEP"
            assert session.scalar(select(func.count()).select_from(UnitOfMeasure)) == 0
    finally:
        engine.dispose()


def test_isolated_52_week_run_freezes_forecasts_and_records_unallocated_pos():
    engine, factory = _session_factory()
    try:
        with factory() as session:
            result = import_demo52(session, DEFAULT_DATA_ROOT)
        validate_staged_batches(factory, result.batch_ids)
        dataset = load_dataset(DEFAULT_DATA_ROOT)
        scope = sorted(
            {
                (int(row["sku_id"]), int(row["location_id"]))
                for row in dataset.rows["forecast_demand_0001.csv"]
            }
        )

        run_id = create_planning_run_via_api(
            factory,
            inventory_snapshot_id=result.inventory_snapshot_id,
            scope=scope,
        )
        with factory() as session:
            run = session.get(PlanningRun, run_id)
            assert run is not None
            assert run.run_status == "draft"
            assert session.scalar(select(func.count()).select_from(PlanningRun)) == 1
            assert session.scalar(
                select(func.count()).select_from(PlanningItem).where(
                    PlanningItem.planning_run_id == run.planning_run_id
                )
            ) == 96
            assert session.scalar(
                select(func.count()).select_from(ForecastDemandWeek).where(
                    ForecastDemandWeek.planning_run_id == run.planning_run_id
                )
            ) == 4992
            assert session.scalar(
                select(func.count()).select_from(PlanningPoResidualSnapshot).where(
                    PlanningPoResidualSnapshot.planning_run_id == run.planning_run_id
                )
            ) == 32
            assert session.scalar(
                select(func.count()).select_from(PlanningSupplySnapshot).where(
                    PlanningSupplySnapshot.planning_run_id == run.planning_run_id
                )
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(PlanningRunFinding).where(
                    PlanningRunFinding.planning_run_id == run.planning_run_id,
                    PlanningRunFinding.issue_code == "SUPPLY_READINESS",
                )
            ) == 32
    finally:
        engine.dispose()


def test_pab_values_carry_negative_balance_without_clamping():
    first_week = date(2026, 1, 5)
    second_week = first_week + timedelta(weeks=1)
    values = calculate_pab_values(
        opening=Decimal("0"),
        demand_by_week={
            first_week: Decimal("40"),
            second_week: Decimal("16.6496"),
        },
        supply_by_week={
            second_week: (
                Decimal("40"),
                Decimal("0"),
                Decimal("0"),
            )
        },
        horizon_start=first_week,
    )

    assert values[0]["ending_firm_base"] == Decimal("-40")
    assert values[1]["opening_base"] == Decimal("-40")
    assert values[1]["ending_firm_base"] == Decimal("-16.6496")


def test_checksum_mismatch_is_rejected_before_writes(tmp_path: Path):
    source = DEFAULT_DATA_ROOT
    for path in source.iterdir():
        if path.is_file():
            (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / "input").mkdir()
    for path in (source / "input").iterdir():
        (tmp_path / "input" / path.name).write_bytes(path.read_bytes())
    changed = tmp_path / "input" / "location.csv"
    changed.write_bytes(changed.read_bytes() + b"\n")

    engine, factory = _session_factory()
    try:
        with factory() as session:
            with pytest.raises(ImportDataError, match="Checksum mismatch"):
                import_demo52(session, tmp_path)
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(UnitOfMeasure)) == 0
    finally:
        engine.dispose()
