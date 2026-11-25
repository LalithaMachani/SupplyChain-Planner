from datetime import date, datetime, timedelta
from decimal import Decimal

from app.models import (
    AppUser,
    InboundShipment,
    InboundShipmentLine,
    InventoryBalance,
    InventorySnapshot,
    Location,
    PABWeek,
    PlanningItem,
    PlanningRun,
    PlanningSupplySnapshot,
    SKU,
    SKUSubstitution,
    SKUUOM,
    SubstitutionApproval,
    Supplier,
    SupplierCapacity,
    SupplierLeadTime,
    SupplierSite,
    SupplierSKU,
    UnitOfMeasure,
)

HORIZON = date(2026, 1, 5)


def _seed_run(session, pab_specs, balances, run_id=1):
    session.add_all(
        [
            Location(
                location_id=location_id,
                location_code=f"MIT-{location_id}",
                name=f"Mitigation location {location_id}",
                location_type="warehouse",
                country_code="US",
                time_zone="UTC",
                status="active",
            )
            for location_id in {spec[1] for spec in pab_specs}
        ]
    )
    session.flush()
    session.add(
        InventorySnapshot(
            inventory_snapshot_id=run_id,
            snapshot_key=f"mitigation-snapshot-{run_id}",
            captured_at=datetime(2026, 1, 1),
            ledger_watermark=0,
            source_name="mitigation-test",
            source_checksum="0" * 64,
            snapshot_status="validated",
        )
    )
    session.flush()
    session.add(
        PlanningRun(
            planning_run_id=run_id,
            run_key=f"mitigation-run-{run_id}",
            scenario_key="mitigation-test",
            horizon_start_week=HORIZON,
            horizon_weeks=52,
            captured_at=datetime(2026, 1, 1),
            inventory_snapshot_id=run_id,
            inventory_cutoff_version=0,
            algorithm_version="test",
            severity_policy_version="test",
            input_fingerprint="0" * 64,
            run_status="completed",
        )
    )
    session.flush()
    pairs = {(spec[0], spec[1]) for spec in pab_specs}
    session.add_all(
        [
            PlanningItem(
                planning_run_id=run_id,
                sku_id=sku_id,
                location_id=location_id,
            )
            for sku_id, location_id in pairs
        ]
    )
    session.flush()
    session.add_all(
        [
            InventoryBalance(
                inventory_snapshot_id=run_id,
                sku_id=sku_id,
                location_id=location_id,
                quantity_on_hand_base=quantity,
            )
            for (sku_id, location_id), quantity in balances.items()
        ]
    )
    for sku_id, location_id, week, opening, demand in pab_specs:
        ending = opening - demand
        session.add(
            PABWeek(
                planning_run_id=run_id,
                sku_id=sku_id,
                location_id=location_id,
                week_start=week,
                opening_base=opening,
                forecast_demand_base=demand,
                firm_inbound_base=Decimal("0"),
                firm_po_remainder_base=Decimal("0"),
                unconfirmed_supply_base=Decimal("0"),
                ending_firm_base=ending,
                shortage_quantity_base=max(Decimal("0"), -ending),
                severity="low" if ending < 0 else "none",
            )
        )
    session.flush()


def _add_supplier(session, supplier_id, sku_id=1001, order_uom_id=1, unit_cost="2"):
    site_id = supplier_id
    session.add_all(
        [
            Supplier(
                supplier_id=supplier_id,
                supplier_code=f"SUP-{supplier_id}",
                name=f"Supplier {supplier_id}",
                status="active",
                default_currency="USD",
            ),
            SupplierSite(
                supplier_site_id=site_id,
                supplier_id=supplier_id,
                site_code=f"SITE-{supplier_id}",
                name="Main site",
                country_code="US",
                time_zone="UTC",
                status="active",
            ),
            SupplierSKU(
                supplier_sku_id=supplier_id,
                supplier_id=supplier_id,
                sku_id=sku_id,
                supplier_item_code=f"ITEM-{supplier_id}",
                order_uom_id=order_uom_id,
                minimum_order_qty=Decimal("1"),
                order_multiple=Decimal("1"),
                unit_cost=Decimal(unit_cost),
                currency="USD",
                effective_from=date(2025, 1, 1),
                status="active",
            ),
        ]
    )
    session.flush()
    return site_id


def test_expedite_does_not_recommend_one_shipment_for_multiple_shortages(
    client, session_factory
):
    with session_factory() as session:
        _seed_run(
            session,
            [
                (1001, 1, HORIZON, Decimal("0"), Decimal("4")),
                (1001, 1, HORIZON + timedelta(weeks=1), Decimal("-4"), Decimal("2")),
            ],
            {(1001, 1): Decimal("0")},
        )
        site_id = _add_supplier(session, 1)
        session.add(
            InboundShipment(
                inbound_shipment_id=1,
                shipment_number="MIT-SHIP-1",
                supplier_id=1,
                supplier_site_id=site_id,
                location_id=1,
                expected_arrival_date=HORIZON + timedelta(weeks=2),
                status="booked",
            )
        )
        session.flush()
        session.add(
            InboundShipmentLine(
                inbound_shipment_line_id=1,
                inbound_shipment_id=1,
                supplier_id=1,
                line_number=1,
                sku_id=1001,
                uom_id=1,
                quantity_shipped=Decimal("4"),
                quantity_received=Decimal("0"),
                uom_to_base_factor=Decimal("1"),
            )
        )
        session.flush()
        session.add(
            PlanningSupplySnapshot(
                planning_run_id=1,
                source_kind="shipment",
                inbound_shipment_line_id=1,
                sku_id=1001,
                destination_location_id=1,
                allocation_no=0,
                allocation_idempotency_key="mitigation-shipment-1",
                candidate_quantity_base=Decimal("4"),
                firm_quantity_base=Decimal("4"),
                unconfirmed_quantity_base=Decimal("0"),
                capacity_exceeded_quantity_base=Decimal("0"),
                expected_arrival_date=HORIZON + timedelta(weeks=2),
                expected_week_start=HORIZON + timedelta(weeks=2),
                supply_status="scheduled",
            )
        )
        session.commit()

    response = client.get("/api/v1/planning/runs/1/mitigations")

    assert response.status_code == 200, response.text
    expedites = [item for item in response.json()["items"] if item["type"] == "expedite_inbound"]
    assert len(expedites) == 1
    assert expedites[0]["inbound_shipment_line_id"] == 1
    assert Decimal(expedites[0]["affected_shortage_quantity_base"]) == Decimal("4")
    assert expedites[0]["expected_arrival_date"] is None
    assert expedites[0]["current_expected_arrival_date"] == "2026-01-19"
    assert expedites[0]["feasibility"] == "insufficient_evidence"
    shortage_response = client.get(
        f"/api/v1/planning/runs/1/shortages/1001/1/{HORIZON.isoformat()}/mitigations"
    )
    assert shortage_response.status_code == 200, shortage_response.text
    assert shortage_response.json()["total"] == 1


def test_transfer_quantity_is_limited_to_inventory_after_source_demand(
    client, session_factory
):
    with session_factory() as session:
        _seed_run(
            session,
            [
                (1001, 1, HORIZON, Decimal("0"), Decimal("4")),
                (1001, 1, HORIZON + timedelta(weeks=1), Decimal("-4"), Decimal("3")),
                (1001, 2, HORIZON, Decimal("8"), Decimal("1")),
                (1001, 2, HORIZON + timedelta(weeks=1), Decimal("7"), Decimal("1")),
            ],
            {
                (1001, 1): Decimal("0"),
                (1001, 2): Decimal("8"),
            },
        )
        session.commit()

    response = client.get("/api/v1/planning/runs/1/mitigations")

    assert response.status_code == 200, response.text
    transfers = [item for item in response.json()["items"] if item["type"] == "inventory_transfer"]
    assert [Decimal(item["proposed_quantity_base"]) for item in transfers] == [
        Decimal("4"),
        Decimal("2"),
    ]
    assert sum(Decimal(item["proposed_quantity_base"]) for item in transfers) == Decimal("6")
    assert all(item["source_location_id"] == 2 for item in transfers)
    assert all(item["expected_arrival_date"] is None for item in transfers)
    assert all(item["feasibility"] == "insufficient_evidence" for item in transfers)


def test_only_effectively_approved_substitutions_are_recommended_with_conversion(
    client, session_factory
):
    with session_factory() as session:
        session.add_all(
            [
                SKU(
                    sku_id=1002,
                    sku_code="SKU-000002",
                    name="Approved substitute",
                    base_uom_id=1,
                    status="active",
                ),
                SKU(
                    sku_id=1003,
                    sku_code="SKU-000003",
                    name="Unapproved substitute",
                    base_uom_id=1,
                    status="active",
                ),
            ]
        )
        session.flush()
        _seed_run(
            session,
            [
                (1001, 1, HORIZON, Decimal("0"), Decimal("4")),
                (1002, 1, HORIZON, Decimal("12"), Decimal("2")),
            ],
            {
                (1001, 1): Decimal("0"),
                (1002, 1): Decimal("12"),
            },
        )
        session.add_all(
            [
                SKUUOM(
                    sku_id=1002,
                    uom_id=1,
                    to_base_factor=1,
                    is_base_uom=True,
                    is_purchase_uom=True,
                ),
                SKUUOM(
                    sku_id=1003,
                    uom_id=1,
                    to_base_factor=1,
                    is_base_uom=True,
                    is_purchase_uom=True,
                ),
                AppUser(
                    user_id=1,
                    email="approver@example.test",
                    display_name="Approver",
                    password_hash="test-hash",
                    status="active",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                SKUSubstitution(
                    substitution_id=1,
                    source_sku_id=1001,
                    substitute_sku_id=1002,
                    conversion_factor=Decimal("2"),
                    effective_from=HORIZON,
                    status="active",
                ),
                SKUSubstitution(
                    substitution_id=2,
                    source_sku_id=1001,
                    substitute_sku_id=1003,
                    conversion_factor=Decimal("1"),
                    effective_from=HORIZON,
                    status="active",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                SubstitutionApproval(
                    substitution_id=1,
                    revision_no=1,
                    decision="approved",
                    approved_by=1,
                    effective_from=HORIZON,
                    effective_to=HORIZON + timedelta(days=6),
                ),
                SubstitutionApproval(
                    substitution_id=2,
                    revision_no=1,
                    decision="pending",
                    effective_from=HORIZON,
                ),
            ]
        )
        session.commit()

    response = client.get("/api/v1/planning/runs/1/mitigations")

    assert response.status_code == 200, response.text
    substitutions = [
        item for item in response.json()["items"] if item["type"] == "approved_substitute"
    ]
    assert len(substitutions) == 1
    assert substitutions[0]["substitution_id"] == 1
    assert Decimal(substitutions[0]["proposed_quantity_base"]) == Decimal("8")
    assert Decimal(substitutions[0]["affected_shortage_quantity_base"]) == Decimal("4")
    assert substitutions[0]["feasibility"] == "feasible"


def test_alternative_supplier_requires_effective_lead_time_and_capacity_and_converts_uom(
    client, session_factory
):
    with session_factory() as session:
        _seed_run(
            session,
            [(1001, 1, HORIZON, Decimal("0"), Decimal("10"))],
            {(1001, 1): Decimal("0")},
        )
        session.add(
            UnitOfMeasure(
                uom_id=2,
                code="PK",
                dimension="count",
                decimal_places=0,
                is_active=True,
            )
        )
        session.add(
            SKUUOM(
                sku_id=1001,
                uom_id=2,
                to_base_factor=2,
                is_base_uom=False,
                is_purchase_uom=True,
            )
        )
        session.flush()
        site_id = _add_supplier(session, 2, order_uom_id=2, unit_cost="3.5")
        session.add_all(
            [
                SupplierLeadTime(
                    supplier_id=2,
                    supplier_site_id=site_id,
                    sku_id=1001,
                    location_id=1,
                    lead_time_days=2,
                    effective_from=date(2025, 1, 1),
                ),
                SupplierCapacity(
                    supplier_id=2,
                    sku_id=1001,
                    capacity_uom_id=2,
                    effective_from=HORIZON - timedelta(days=2),
                    capacity_quantity=Decimal("4"),
                    reserved_quantity=Decimal("1"),
                ),
            ]
        )
        session.commit()

    response = client.get("/api/v1/planning/runs/1/mitigations")

    assert response.status_code == 200, response.text
    alternatives = [
        item for item in response.json()["items"] if item["type"] == "alternative_supplier"
    ]
    assert len(alternatives) == 1
    assert alternatives[0]["feasibility"] == "feasible"
    assert Decimal(alternatives[0]["proposed_quantity_base"]) == Decimal("6")
    assert alternatives[0]["expected_arrival_date"] == "2026-01-03"
    assert Decimal(alternatives[0]["estimated_cost"]) == Decimal("10.5")
    assert alternatives[0]["cost_currency"] == "USD"


def test_supplier_without_capacity_is_not_presented_as_a_feasible_alternative(
    client, session_factory
):
    with session_factory() as session:
        _seed_run(
            session,
            [(1001, 1, HORIZON, Decimal("0"), Decimal("5"))],
            {(1001, 1): Decimal("0")},
        )
        site_id = _add_supplier(session, 3)
        session.add(
            SupplierLeadTime(
                supplier_id=3,
                supplier_site_id=site_id,
                sku_id=1001,
                location_id=1,
                lead_time_days=1,
                effective_from=date(2025, 1, 1),
            )
        )
        session.commit()

    response = client.get("/api/v1/planning/runs/1/mitigations")

    assert response.status_code == 200, response.text
    assert not [
        item for item in response.json()["items"] if item["type"] == "alternative_supplier"
    ]
