"""Planning-run API and calculation tests against an isolated SQLite database."""

from datetime import date, timedelta
from decimal import Decimal
import hashlib

import pytest
from sqlalchemy import select

from app.models import (
    InboundShipment,
    InboundShipmentLine,
    InventoryBalance,
    InventoryLedgerControl,
    InventorySnapshot,
    IngestionBatch,
    Location,
    PlanningPoResidualSnapshot,
    PlanningSupplySnapshot,
    PurchaseOrder,
    PurchaseOrderLine,
    SKU,
    SKUUOM,
    Supplier,
    SupplierSKU,
    SupplierSite,
    StagingRecord,
    UnitOfMeasure,
)
from app.services.planning_service import calculate_pab_values


HORIZON = date(2026, 1, 5)


@pytest.fixture
def planning_data(session_factory):
    with session_factory() as session:
        session.add(
            Location(
                location_id=1,
                location_code="LOC-1",
                name="Planning test location",
                location_type="warehouse",
                country_code="US",
                time_zone="UTC",
                status="active",
            )
        )
        session.flush()
        session.add(
            UnitOfMeasure(
                uom_id=2,
                code="CS",
                dimension="count",
                decimal_places=2,
                is_active=True,
            )
        )
        session.add(
            SKUUOM(
                sku_id=1001,
                uom_id=2,
                to_base_factor=12,
                is_base_uom=False,
                is_purchase_uom=True,
            )
        )
        session.flush()
        session.add_all(
            [
                InventoryLedgerControl(ledger_key="inventory", committed_version=0),
                InventorySnapshot(
                    inventory_snapshot_id=1,
                    snapshot_key="planning-test-snapshot",
                    ledger_watermark=0,
                    source_name="planning-test",
                    source_checksum="0" * 64,
                    snapshot_status="validated",
                ),
            ]
        )
        session.flush()
        session.add(
            InventoryBalance(
                inventory_snapshot_id=1,
                sku_id=1001,
                location_id=1,
                quantity_on_hand_base=Decimal("10"),
            )
        )
        session.commit()


def _forecast(session, payload: dict[str, str], row_number: int = 1) -> None:
    batch = IngestionBatch(
        batch_identifier=f"forecast-batch-{row_number}",
        source_name="planning-test",
        source_file_name="forecast.csv",
        file_checksum=hashlib.sha256(str(payload).encode()).hexdigest(),
        processing_status="validated",
        validation_version="test",
    )
    session.add(batch)
    session.flush()
    session.add(
        StagingRecord(
            ingestion_batch_id=batch.ingestion_batch_id,
            record_type="forecast_demand",
            source_row_number=row_number,
            raw_payload_json=payload,
            raw_payload_text=str(payload),
            payload_hash=hashlib.sha256(str(payload).encode()).hexdigest(),
            disposition="accepted",
        )
    )
    session.commit()


def _create_run(client, **overrides):
    payload = {
        "scenario_key": "test-scenario",
        "horizon_start_week": HORIZON.isoformat(),
        "items": [{"sku_id": 1001, "location_id": 1}],
    }
    payload.update(overrides)
    return client.post("/api/v1/planning/runs", json=payload)


def test_normal_supply_and_demand_create_a_52_week_run(
    client, planning_data, session_factory
):
    with session_factory() as session:
        _forecast(
            session,
            {
                "sku_code": "SKU-000001",
                "location_code": "LOC-1",
                "week_start": HORIZON.isoformat(),
                "quantity": "4",
                "uom_code": "EA",
            },
        )

    created = _create_run(client)
    assert created.status_code == 201, created.text
    run_id = created.json()["planning_run_id"]
    assert created.json()["run_status"] == "draft"

    calculated = client.post(f"/api/v1/planning/runs/{run_id}/calculate")
    assert calculated.status_code == 200, calculated.text
    pab = client.get(f"/api/v1/planning/runs/{run_id}/pab").json()
    assert pab["total"] == 52
    assert pab["items"][0]["opening_base"] == "10.00000000"
    assert pab["items"][0]["forecast_demand_base"] == "4.00000000"
    assert pab["items"][0]["ending_firm_base"] == "6.000000000000"


def test_negative_pab_is_carried_forward_without_clamping():
    weeks = calculate_pab_values(
        Decimal("10"),
        {
            HORIZON: Decimal("15"),
        },
        {HORIZON + timedelta(weeks=1): (Decimal("4"), Decimal("0"), Decimal("0"))},
        HORIZON,
    )

    assert weeks[0]["ending_firm_base"] == Decimal("-5")
    assert weeks[0]["shortage_quantity_base"] == Decimal("5")
    assert weeks[1]["opening_base"] == Decimal("-5")
    assert weeks[1]["ending_firm_base"] == Decimal("-1")
    assert weeks[1]["shortage_quantity_base"] == Decimal("1")
    for week in weeks:
        assert (
            week["opening_base"]
            + week["firm_inbound_base"]
            + week["firm_po_remainder_base"]
            - week["forecast_demand_base"]
            == week["ending_firm_base"]
        )


def test_shortage_endpoint_records_quantity_cause_and_configured_severity(
    client, planning_data, session_factory
):
    final_week = HORIZON + timedelta(weeks=51)
    with session_factory() as session:
        _forecast(
            session,
            {
                "sku_code": "SKU-000001",
                "location_code": "LOC-1",
                "week_start": final_week.isoformat(),
                "quantity": "12",
                "uom_code": "EA",
            },
        )
    created = _create_run(
        client,
        severity_thresholds={"medium": "1", "high": "5", "critical": "10"},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["planning_run_id"]

    calculated = client.post(f"/api/v1/planning/runs/{run_id}/calculate")
    shortages = client.get(f"/api/v1/planning/runs/{run_id}/shortages").json()

    assert calculated.status_code == 200, calculated.text
    assert shortages["total"] == 1
    assert shortages["items"][0]["shortage_quantity_base"] == "2.000000000000"
    assert shortages["items"][0]["severity"] == "medium"
    assert shortages["items"][0]["causes"][0]["cause_code"] == "demand_exceeds_firm_supply"


def test_partial_receipts_are_not_counted_again_as_po_remainder(
    client, planning_data, session_factory
):
    with session_factory() as session:
        supplier = Supplier(
            supplier_id=1,
            supplier_code="SUP-1",
            name="Planning supplier",
            status="active",
            default_currency="USD",
        )
        session.add(supplier)
        session.flush()
        site = SupplierSite(
            supplier_site_id=1,
            supplier_id=1,
            site_code="SITE-1",
            name="Supplier site",
            country_code="US",
            time_zone="UTC",
            status="active",
        )
        session.add(site)
        session.flush()
        session.add(
            SupplierSKU(
                supplier_id=1,
                sku_id=1001,
                supplier_item_code="ITEM-1",
                order_uom_id=1,
                minimum_order_qty=0,
                order_multiple=1,
                unit_cost=1,
                currency="USD",
                effective_from=date(2025, 1, 1),
                status="active",
            )
        )
        session.flush()
        order = PurchaseOrder(
            purchase_order_id=1,
            purchase_order_number="PO-1",
            supplier_id=1,
            supplier_site_id=1,
            order_date=date(2026, 1, 1),
            requested_delivery_date=HORIZON,
            currency="USD",
            status="released",
        )
        session.add(order)
        session.flush()
        line = PurchaseOrderLine(
            purchase_order_line_id=1,
            purchase_order_id=1,
            supplier_id=1,
            line_number=1,
            sku_id=1001,
            order_uom_id=1,
            quantity_ordered=10,
            order_uom_to_base_factor=1,
            quantity_received_base=2,
            unit_cost=1,
            line_status="partially_received",
        )
        session.add(line)
        shipment = InboundShipment(
            inbound_shipment_id=1,
            shipment_number="ASN-1",
            supplier_id=1,
            supplier_site_id=1,
            location_id=1,
            expected_arrival_date=HORIZON,
            status="in_transit",
        )
        session.add(shipment)
        session.flush()
        session.add(
            InboundShipmentLine(
                inbound_shipment_line_id=1,
                inbound_shipment_id=1,
                supplier_id=1,
                line_number=1,
                purchase_order_line_id=1,
                sku_id=1001,
                uom_id=1,
                quantity_shipped=5,
                quantity_received=2,
                uom_to_base_factor=1,
            )
        )
        session.add(
            InboundShipment(
                inbound_shipment_id=2,
                shipment_number="ASN-2",
                supplier_id=1,
                supplier_site_id=1,
                location_id=1,
                expected_arrival_date=HORIZON + timedelta(weeks=1),
                status="booked",
            )
        )
        session.flush()
        session.add(
            InboundShipmentLine(
                inbound_shipment_line_id=2,
                inbound_shipment_id=2,
                supplier_id=1,
                line_number=1,
                purchase_order_line_id=None,
                sku_id=1001,
                uom_id=1,
                quantity_shipped=4,
                quantity_received=0,
                uom_to_base_factor=1,
            )
        )
        session.commit()

    with session_factory() as session:
        _forecast(
            session,
            {
                "sku_code": "SKU-000001",
                "location_code": "LOC-1",
                "week_start": HORIZON.isoformat(),
                "quantity": "18",
                "uom_code": "EA",
            },
        )
    created = _create_run(client)
    assert created.status_code == 201, created.text
    run_id = created.json()["planning_run_id"]
    calculated = client.post(f"/api/v1/planning/runs/{run_id}/calculate")
    assert calculated.status_code == 200, calculated.text
    pab = client.get(f"/api/v1/planning/runs/{run_id}/pab").json()["items"]
    with session_factory() as session:
        snapshots = session.scalars(
            select(PlanningSupplySnapshot).where(
                PlanningSupplySnapshot.planning_run_id == run_id
            )
        ).all()
        residual = session.scalar(
            select(PlanningPoResidualSnapshot).where(
                PlanningPoResidualSnapshot.planning_run_id == run_id
            )
        )
        shipment_supply = next(
            row
            for row in snapshots
            if row.source_kind == "shipment" and row.inbound_shipment_line_id == 1
        )
        week_two_shipment = next(
            row
            for row in snapshots
            if row.source_kind == "shipment" and row.inbound_shipment_line_id == 2
        )
        po_supply = next(row for row in snapshots if row.source_kind == "po_allocation")

    assert shipment_supply.candidate_quantity_base == Decimal("3.00000000")
    assert residual.received_base == Decimal("2.00000000")
    assert residual.scheduled_base == Decimal("3.00000000")
    assert residual.residual_base == Decimal("5.00000000")
    assert po_supply.candidate_quantity_base == Decimal("5.00000000")
    assert week_two_shipment.firm_quantity_base == Decimal("4.00000000")
    assert shipment_supply.candidate_quantity_base + po_supply.candidate_quantity_base == Decimal("8.00000000")
    assert pab[0]["firm_inbound_base"] == "3.00000000"
    assert pab[0]["firm_po_remainder_base"] == "0E-8"
    assert pab[0]["forecast_demand_base"] == "18.00000000"
    assert pab[0]["unconfirmed_supply_base"] == "5.00000000"
    assert pab[0]["ending_firm_base"] == "-5.000000000000"
    assert pab[1]["opening_base"] == "-5.00000000"
    assert pab[1]["firm_inbound_base"] == "4.00000000"
    assert pab[1]["ending_firm_base"] == "-1.000000000000"
    for index, week in enumerate(pab):
        assert (
            Decimal(week["opening_base"])
            + Decimal(week["firm_inbound_base"])
            + Decimal(week["firm_po_remainder_base"])
            - Decimal(week["forecast_demand_base"])
            == Decimal(week["ending_firm_base"])
        )
        if index:
            assert Decimal(week["opening_base"]) == Decimal(
                pab[index - 1]["ending_firm_base"]
            )


def test_missing_lead_time_and_unconfirmed_capacity_are_reported(
    client, planning_data, session_factory
):
    with session_factory() as session:
        session.add(
            Supplier(
                supplier_id=1,
                supplier_code="SUP-1",
                name="Planning supplier",
                status="active",
                default_currency="USD",
            )
        )
        session.add(
            SupplierSite(
                supplier_site_id=1,
                supplier_id=1,
                site_code="SITE-1",
                name="Supplier site",
                country_code="US",
                time_zone="UTC",
                status="active",
            )
        )
        session.flush()
        session.add(
            SupplierSKU(
                supplier_id=1,
                sku_id=1001,
                supplier_item_code="ITEM-1",
                order_uom_id=1,
                minimum_order_qty=0,
                order_multiple=1,
                unit_cost=1,
                currency="USD",
                effective_from=date(2025, 1, 1),
                status="active",
            )
        )
        session.flush()
        session.add(
            PurchaseOrder(
                purchase_order_id=1,
                purchase_order_number="PO-1",
                supplier_id=1,
                supplier_site_id=1,
                order_date=date(2026, 1, 1),
                requested_delivery_date=None,
                currency="USD",
                status="released",
            )
        )
        session.flush()
        session.add(
            PurchaseOrderLine(
                purchase_order_line_id=1,
                purchase_order_id=1,
                supplier_id=1,
                line_number=1,
                sku_id=1001,
                order_uom_id=1,
                quantity_ordered=8,
                order_uom_to_base_factor=1,
                quantity_received_base=0,
                unit_cost=1,
                line_status="open",
            )
        )
        session.commit()

    created = _create_run(client)
    assert created.status_code == 201, created.text
    run_id = created.json()["planning_run_id"]
    findings = client.get(f"/api/v1/planning/runs/{run_id}/findings").json()["items"]

    assert any(item["finding_code"] == "MISSING_LEAD_TIME" for item in findings)
    assert any(item["finding_code"] == "CAPACITY_UNCONFIRMED" for item in findings)
    with session_factory() as session:
        supply = session.scalar(
            select(PlanningSupplySnapshot).where(
                PlanningSupplySnapshot.planning_run_id == run_id
            )
        )
    assert supply.firm_quantity_base == Decimal("0E-8")
    assert supply.unconfirmed_quantity_base == Decimal("8.00000000")
    assert supply.expected_week_start is None


def test_forecast_uom_conversion_and_repeated_calculation_are_idempotent(
    client, planning_data, session_factory
):
    with session_factory() as session:
        _forecast(
            session,
            {
                "sku_code": "SKU-000001",
                "location_code": "LOC-1",
                "week_start": HORIZON.isoformat(),
                "quantity": "0.5",
                "uom_code": "CS",
            },
        )
    created = _create_run(
        client,
        severity_thresholds={"medium": "10", "high": "20", "critical": "30"},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["planning_run_id"]

    first = client.post(f"/api/v1/planning/runs/{run_id}/calculate")
    second = client.post(f"/api/v1/planning/runs/{run_id}/calculate")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    pab = client.get(f"/api/v1/planning/runs/{run_id}/pab").json()["items"]
    assert pab[0]["forecast_demand_base"] == "6.00000000"
    assert first.json()["run_status"] == second.json()["run_status"] == "completed"
    assert len(pab) == 52


def test_invalid_sku_location_scope_is_rejected(client, planning_data):
    response = _create_run(
        client,
        items=[{"sku_id": 999999, "location_id": 1}],
    )

    assert response.status_code == 422
    assert "SKU" in response.json()["detail"]
