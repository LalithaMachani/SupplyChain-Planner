"""Snapshot accepted demand and eligible supply, then calculate weekly PAB."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from uuid import uuid4

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session

from ..models.catalog import Location, SKU, SKUUOM, UnitOfMeasure
from ..models.ingestion import StagingRecord
from ..models.planning import (
    ForecastDemandWeek,
    InventoryBalance,
    InventoryLedgerControl,
    InventorySnapshot,
    InventoryTransaction,
    PABWeek,
    PlanningItem,
    PlanningPoResidualSnapshot,
    PlanningRun,
    PlanningRunFinding,
    PlanningSupplySnapshot,
    ShortageCause,
    SupplyCancellation,
)
from ..models.procurement import (
    InboundShipment,
    InboundShipmentLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierCapacityReservation,
    SupplierLeadTime,
)
from ..schemas.planning import PlanningRunCreate

ALGORITHM_VERSION = "weekly-pab-v1"
SEVERITY_POLICY_VERSION = "configured-thresholds-v1"
ZERO = Decimal("0")
SUPPLY_ORDER_STATUSES = {"approved", "released", "partially_received"}
SUPPLY_LINE_STATUSES = {"open", "partially_received"}
SHIPMENT_STATUSES = {"booked", "in_transit", "partially_received"}


class PlanningNotFoundError(LookupError):
    """A requested run or inventory snapshot does not exist."""


class PlanningInputError(ValueError):
    """Planning inputs are invalid or not ready for a reproducible run."""


class PlanningCalculationError(RuntimeError):
    """The calculation cannot be persisted without violating the deployed schema."""


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _normalized(payload: dict) -> dict[str, str]:
    return {
        re.sub(r"[^a-z0-9]+", "_", str(key).strip().casefold()).strip("_"):
        "" if value is None else str(value).strip()
        for key, value in payload.items()
    }


def _first(fields: dict[str, str], names: tuple[str, ...]) -> str | None:
    return next((fields[name] for name in names if fields.get(name)), None)


def _decimal(value: str | None, field: str) -> Decimal:
    if value is None:
        raise PlanningInputError(f"Accepted forecast is missing {field}.")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise PlanningInputError(f"Accepted forecast has an invalid {field}.") from error
    if not result.is_finite() or result < 0:
        raise PlanningInputError(f"Accepted forecast {field} must be finite and nonnegative.")
    return result


def _base_quantity(quantity: Decimal, factor: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return quantity * factor


def _find_snapshot(session: Session, snapshot_id: int | None) -> InventorySnapshot:
    if snapshot_id is not None:
        snapshot = session.get(InventorySnapshot, snapshot_id)
        if snapshot is None or snapshot.snapshot_status != "validated":
            raise PlanningInputError("The selected inventory snapshot is not validated.")
        return snapshot
    snapshot = session.scalar(
        select(InventorySnapshot)
        .where(InventorySnapshot.snapshot_status == "validated")
        .order_by(InventorySnapshot.captured_at.desc(), InventorySnapshot.inventory_snapshot_id.desc())
        .limit(1)
    )
    if snapshot is None:
        raise PlanningInputError("A validated inventory snapshot is required to create a run.")
    return snapshot


def _resolve_forecast_entity(
    session: Session,
    fields: dict[str, str],
    *,
    id_names: tuple[str, ...],
    code_names: tuple[str, ...],
    model,
    code_attribute: str,
):
    raw_id = _first(fields, id_names)
    if raw_id:
        try:
            entity_id = int(raw_id)
        except ValueError:
            return None
        return session.get(model, entity_id)
    raw_code = _first(fields, code_names)
    if raw_code:
        return session.scalar(
            select(model).where(func.upper(getattr(model, code_attribute)) == raw_code.upper())
        )
    return None


def _freeze_forecasts(
    session: Session,
    run_id: int,
    scope: set[tuple[int, int]],
    horizon_start: date,
) -> list[tuple[int, int, date, Decimal, str]]:
    horizon_end = horizon_start + timedelta(weeks=52)
    records = session.scalars(
        select(StagingRecord)
        .where(
            StagingRecord.record_type == "forecast_demand",
            StagingRecord.disposition == "accepted",
        )
        .order_by(StagingRecord.staging_record_id)
    ).all()
    aggregates: dict[tuple[int, int, date], Decimal] = defaultdict(lambda: ZERO)
    source_ids: dict[tuple[int, int, date], list[int]] = defaultdict(list)
    source_hashes: dict[tuple[int, int, date], list[str]] = defaultdict(list)

    for record in records:
        fields = _normalized(record.raw_payload_json)
        sku = _resolve_forecast_entity(
            session,
            fields,
            id_names=("sku_id",),
            code_names=("sku_code", "sku"),
            model=SKU,
            code_attribute="sku_code",
        )
        if sku is None:
            continue
        # A selected SKU with no destination is malformed; other locations are out of scope.
        raw_location = _first(fields, ("location_id",))
        raw_location_code = _first(fields, ("location_code", "location"))
        if raw_location:
            try:
                location = session.get(Location, int(raw_location))
            except ValueError:
                location = None
        elif raw_location_code:
            location = session.scalar(
                select(Location).where(func.upper(Location.location_code) == raw_location_code.upper())
            )
        else:
            if any(scope_sku == sku.sku_id for scope_sku, _ in scope):
                raise PlanningInputError(
                    f"Accepted forecast record {record.staging_record_id} has no location."
                )
            continue
        if location is None or (sku.sku_id, location.location_id) not in scope:
            continue

        raw_week = _first(
            fields,
            ("week_start", "forecast_week", "week", "date", "forecast_date"),
        )
        if raw_week is None:
            raise PlanningInputError(
                f"Accepted forecast record {record.staging_record_id} has no forecast week."
            )
        try:
            week = _week_start(date.fromisoformat(raw_week[:10]))
        except ValueError as error:
            raise PlanningInputError(
                f"Accepted forecast record {record.staging_record_id} has an invalid week."
            ) from error
        if not horizon_start <= week < horizon_end:
            continue

        quantity = _decimal(
            _first(fields, ("quantity", "forecast_quantity", "forecast_quantity_base")),
            "quantity",
        )
        raw_uom_id = _first(fields, ("uom_id", "unit_of_measure_id"))
        raw_uom_code = _first(fields, ("uom_code", "unit_of_measure", "unit", "uom"))
        if raw_uom_id or raw_uom_code:
            if raw_uom_id:
                try:
                    uom_id = int(raw_uom_id)
                except ValueError as error:
                    raise PlanningInputError("Accepted forecast has an invalid UOM identifier.") from error
                mapping = session.get(SKUUOM, (sku.sku_id, uom_id))
            else:
                mapping = session.scalar(
                    select(SKUUOM)
                    .join(UnitOfMeasure, UnitOfMeasure.uom_id == SKUUOM.uom_id)
                    .where(
                        SKUUOM.sku_id == sku.sku_id,
                        func.upper(UnitOfMeasure.code) == raw_uom_code.upper(),
                    )
                )
            if mapping is None:
                raise PlanningInputError(
                    f"Accepted forecast record {record.staging_record_id} uses an unmapped UOM."
                )
            factor = Decimal(mapping.to_base_factor)
        else:
            factor = Decimal("1")

        key = (sku.sku_id, location.location_id, week)
        with localcontext() as context:
            context.prec = 50
            aggregates[key] += _base_quantity(quantity, factor)
        source_ids[key].append(record.staging_record_id)
        source_hashes[key].append(record.payload_hash)

    frozen: list[tuple[int, date, Decimal, int, str]] = []
    for (sku_id, location_id, week), quantity in sorted(aggregates.items()):
        record_ids = source_ids[(sku_id, location_id, week)]
        fingerprint = hashlib.sha256(
            "|".join(source_hashes[(sku_id, location_id, week)]).encode()
        ).hexdigest()
        session.add(
            ForecastDemandWeek(
                planning_run_id=run_id,
                sku_id=sku_id,
                location_id=location_id,
                week_start=week,
                forecast_quantity_base=quantity,
                source_staging_record_id=record_ids[0],
                source_fingerprint=fingerprint,
            )
        )
        frozen.append((sku_id, location_id, week, quantity, fingerprint))
    return frozen


def _cancelled_quantity(
    session: Session,
    *,
    po_line_id: int | None = None,
    shipment_line_id: int | None = None,
) -> Decimal:
    query = select(func.coalesce(func.sum(SupplyCancellation.quantity_cancelled_base), 0))
    if po_line_id is not None:
        query = query.where(SupplyCancellation.purchase_order_line_id == po_line_id)
    else:
        query = query.where(SupplyCancellation.inbound_shipment_line_id == shipment_line_id)
    return Decimal(session.scalar(query) or 0)


def _supply_week(arrival: date | None, horizon_start: date) -> tuple[date | None, str]:
    if arrival is None:
        return None, "undated"
    horizon_end = horizon_start + timedelta(weeks=52)
    if arrival < horizon_start:
        return None, "overdue"
    if arrival >= horizon_end:
        return None, "outside_horizon"
    return _week_start(arrival), "scheduled"


def _add_supply(
    session: Session,
    *,
    run_id: int,
    scope: set[tuple[int, int]],
    source_kind: str,
    sku_id: int,
    location_id: int,
    candidate: Decimal,
    firm: Decimal,
    unconfirmed: Decimal,
    arrival: date | None,
    status: str,
    horizon_start: date,
    idempotency_key: str,
    shipment_line_id: int | None = None,
    residual_id: int | None = None,
    allocation_no: int = 0,
    reservation_id: int | None = None,
    capacity_exceeded: Decimal = ZERO,
) -> None:
    if (sku_id, location_id) not in scope:
        return
    week, _ = _supply_week(arrival, horizon_start)
    session.add(
        PlanningSupplySnapshot(
            planning_run_id=run_id,
            source_kind=source_kind,
            inbound_shipment_line_id=shipment_line_id,
            planning_po_residual_snapshot_id=residual_id,
            sku_id=sku_id,
            destination_location_id=location_id,
            allocation_no=allocation_no,
            allocation_idempotency_key=idempotency_key,
            candidate_quantity_base=candidate,
            firm_quantity_base=firm,
            unconfirmed_quantity_base=unconfirmed,
            capacity_exceeded_quantity_base=capacity_exceeded,
            expected_arrival_date=arrival,
            expected_week_start=week,
            supplier_capacity_reservation_id=reservation_id,
            supply_status=status,
        )
    )


def _build_supply(
    session: Session,
    run: PlanningRun,
    scope: set[tuple[int, int]],
) -> list[tuple]:
    horizon_start = run.horizon_start_week
    scope_skus = {sku_id for sku_id, _ in scope}
    rule_findings: list[tuple[str, str, int | None, int | None]] = []

    shipment_rows = session.execute(
        select(InboundShipmentLine, InboundShipment)
        .join(InboundShipment, InboundShipment.inbound_shipment_id == InboundShipmentLine.inbound_shipment_id)
        .where(
            InboundShipmentLine.sku_id.in_(scope_skus),
            InboundShipment.status.in_(SHIPMENT_STATUSES),
        )
        .order_by(InboundShipmentLine.inbound_shipment_line_id)
    ).all() if scope_skus else []
    scheduled_by_po: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for line, shipment in shipment_rows:
        candidate = _base_quantity(
            Decimal(line.quantity_shipped) - Decimal(line.quantity_received),
            Decimal(line.uom_to_base_factor),
        )
        candidate -= _cancelled_quantity(session, shipment_line_id=line.inbound_shipment_line_id)
        candidate = max(ZERO, candidate)
        if line.purchase_order_line_id is not None:
            scheduled_by_po[line.purchase_order_line_id] += candidate
        if candidate == ZERO:
            continue
        week, status = _supply_week(shipment.expected_arrival_date, horizon_start)
        _add_supply(
            session,
            run_id=run.planning_run_id,
            scope=scope,
            source_kind="shipment",
            sku_id=line.sku_id,
            location_id=shipment.location_id,
            candidate=candidate,
            firm=candidate if status == "scheduled" else ZERO,
            unconfirmed=ZERO,
            arrival=shipment.expected_arrival_date,
            status=status,
            horizon_start=horizon_start,
            idempotency_key=f"shipment:{line.inbound_shipment_line_id}",
            shipment_line_id=line.inbound_shipment_line_id,
        )
        if status in {"overdue", "outside_horizon", "undated"}:
            rule_findings.append((
                "readiness",
                f"Shipment supply {line.inbound_shipment_line_id} is {status}.",
                line.sku_id,
                shipment.location_id,
            ))

    po_rows = session.execute(
        select(PurchaseOrderLine, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.purchase_order_id == PurchaseOrderLine.purchase_order_id)
        .where(
            PurchaseOrderLine.sku_id.in_(scope_skus),
            PurchaseOrderLine.line_status.in_(SUPPLY_LINE_STATUSES),
            PurchaseOrder.status.in_(SUPPLY_ORDER_STATUSES),
        )
        .order_by(PurchaseOrderLine.purchase_order_line_id)
    ).all() if scope_skus else []
    destinations_by_sku: dict[int, list[int]] = defaultdict(list)
    for sku_id, location_id in sorted(scope):
        destinations_by_sku[sku_id].append(location_id)

    for line, order in po_rows:
        ordered = _base_quantity(
            Decimal(line.quantity_ordered),
            Decimal(line.order_uom_to_base_factor),
        )
        received = Decimal(line.quantity_received_base)
        cancelled = _cancelled_quantity(session, po_line_id=line.purchase_order_line_id)
        scheduled = min(max(ZERO, ordered - received - cancelled), scheduled_by_po[line.purchase_order_line_id])
        residual = max(ZERO, ordered - received - cancelled - scheduled)
        residual_snapshot = PlanningPoResidualSnapshot(
            planning_run_id=run.planning_run_id,
            purchase_order_line_id=line.purchase_order_line_id,
            ordered_base=ordered,
            received_base=received,
            scheduled_base=scheduled,
            cancelled_base=cancelled,
            residual_base=residual,
        )
        session.add(residual_snapshot)
        session.flush()
        if residual == ZERO:
            continue
        destinations = destinations_by_sku[line.sku_id]
        if len(destinations) != 1:
            rule_findings.append((
                "readiness",
                f"PO line {line.purchase_order_line_id} remainder is not allocated: "
                "the selected scope has multiple destinations for this SKU.",
                line.sku_id,
                None,
            ))
            continue
        location_id = destinations[0]
        arrival = order.requested_delivery_date
        if arrival is None and order.supplier_site_id is not None:
            lead_time = session.scalar(
                select(SupplierLeadTime)
                .where(
                    SupplierLeadTime.supplier_id == line.supplier_id,
                    SupplierLeadTime.supplier_site_id == order.supplier_site_id,
                    SupplierLeadTime.sku_id == line.sku_id,
                    SupplierLeadTime.location_id == location_id,
                    SupplierLeadTime.effective_from <= order.order_date,
                    (SupplierLeadTime.effective_to.is_(None)) | (SupplierLeadTime.effective_to >= order.order_date),
                )
                .order_by(SupplierLeadTime.effective_from.desc())
                .limit(1)
            )
            if lead_time is not None:
                arrival = order.order_date + timedelta(days=lead_time.lead_time_days)
        if arrival is None:
            rule_findings.append((
                "MISSING_LEAD_TIME",
                f"PO line {line.purchase_order_line_id} has no requested delivery date "
                "and no effective supplier lead time; its remainder is undated.",
                line.sku_id,
                location_id,
            ))

        reservation = session.scalar(
            select(SupplierCapacityReservation)
            .where(
                SupplierCapacityReservation.purchase_order_line_id == line.purchase_order_line_id,
                SupplierCapacityReservation.status.in_(("reserved", "committed")),
            )
            .order_by(SupplierCapacityReservation.supplier_capacity_reservation_id)
            .limit(1)
        )
        confirmed = ZERO
        if reservation is not None and reservation.status == "committed":
            factor = session.scalar(
                select(SKUUOM.to_base_factor).where(
                    SKUUOM.sku_id == line.sku_id,
                    SKUUOM.uom_id == reservation.capacity_uom_id,
                )
            )
            if factor is not None:
                confirmed = min(
                    residual,
                    _base_quantity(
                        Decimal(reservation.reservation_quantity),
                        Decimal(factor),
                    ),
                )
        unconfirmed = residual - confirmed
        if unconfirmed > ZERO:
            rule_findings.append((
                "CAPACITY_UNCONFIRMED",
                f"PO line {line.purchase_order_line_id} has {unconfirmed} base units "
                "without committed supplier capacity.",
                line.sku_id,
                location_id,
            ))
        week, status = _supply_week(arrival, horizon_start)
        firm = confirmed if status == "scheduled" else ZERO
        if status != "scheduled":
            unconfirmed = residual
        _add_supply(
            session,
            run_id=run.planning_run_id,
            scope=scope,
            source_kind="po_allocation",
            sku_id=line.sku_id,
            location_id=location_id,
            candidate=residual,
            firm=firm,
            unconfirmed=unconfirmed,
            arrival=arrival,
            status=status if unconfirmed == ZERO else "unconfirmed",
            horizon_start=horizon_start,
            idempotency_key=f"po:{line.purchase_order_line_id}:{location_id}",
            residual_id=residual_snapshot.planning_po_residual_snapshot_id,
            allocation_no=1,
            reservation_id=reservation.supplier_capacity_reservation_id if reservation else None,
        )

    return rule_findings


def _input_source_signature(
    session: Session,
    scope: set[tuple[int, int]],
) -> dict[str, list]:
    scope_skus = {sku_id for sku_id, _ in scope}
    scope_locations = {location_id for _, location_id in scope}
    sku_codes = {
        sku.sku_code.upper(): sku.sku_id
        for sku in session.scalars(select(SKU).where(SKU.sku_id.in_(scope_skus))).all()
    }
    location_codes = {
        location.location_code.upper(): location.location_id
        for location in session.scalars(
            select(Location).where(Location.location_id.in_(scope_locations))
        ).all()
    }
    forecast_sources = []
    for record in session.scalars(
        select(StagingRecord)
        .where(
            StagingRecord.record_type == "forecast_demand",
            StagingRecord.disposition == "accepted",
        )
        .order_by(StagingRecord.staging_record_id)
    ).all():
        fields = _normalized(record.raw_payload_json)
        raw_sku = _first(fields, ("sku_id",))
        try:
            sku_id = int(raw_sku) if raw_sku else sku_codes.get(
                (_first(fields, ("sku_code", "sku")) or "").upper()
            )
        except ValueError:
            sku_id = None
        if sku_id not in scope_skus:
            continue
        raw_location = _first(fields, ("location_id",))
        try:
            location_id = int(raw_location) if raw_location else location_codes.get(
                (_first(fields, ("location_code", "location")) or "").upper()
            )
        except ValueError:
            location_id = None
        if location_id is None or (sku_id, location_id) in scope:
            forecast_sources.append([record.staging_record_id, record.payload_hash])

    shipment_sources = []
    shipment_rows = session.execute(
        select(InboundShipmentLine, InboundShipment)
        .join(
            InboundShipment,
            InboundShipment.inbound_shipment_id == InboundShipmentLine.inbound_shipment_id,
        )
        .where(
            InboundShipmentLine.sku_id.in_(scope_skus),
            InboundShipment.location_id.in_(scope_locations),
            InboundShipment.status.in_(SHIPMENT_STATUSES),
        )
        .order_by(InboundShipmentLine.inbound_shipment_line_id)
    ).all() if scope_skus and scope_locations else []
    shipment_ids: list[int] = []
    po_line_ids: list[int] = []
    for line, shipment in shipment_rows:
        if (line.sku_id, shipment.location_id) not in scope:
            continue
        shipment_ids.append(line.inbound_shipment_line_id)
        if line.purchase_order_line_id is not None:
            po_line_ids.append(line.purchase_order_line_id)
        shipment_sources.append(
            [
                line.inbound_shipment_line_id,
                line.sku_id,
                shipment.location_id,
                shipment.status,
                line.quantity_shipped,
                line.quantity_received,
                line.uom_to_base_factor,
                shipment.expected_arrival_date,
            ]
        )

    po_sources = []
    po_rows = session.execute(
        select(PurchaseOrderLine, PurchaseOrder)
        .join(
            PurchaseOrder,
            PurchaseOrder.purchase_order_id == PurchaseOrderLine.purchase_order_id,
        )
        .where(
            PurchaseOrderLine.sku_id.in_(scope_skus),
            PurchaseOrderLine.line_status.in_(SUPPLY_LINE_STATUSES),
            PurchaseOrder.status.in_(SUPPLY_ORDER_STATUSES),
        )
        .order_by(PurchaseOrderLine.purchase_order_line_id)
    ).all() if scope_skus else []
    for line, order in po_rows:
        po_line_ids.append(line.purchase_order_line_id)
        po_sources.append(
            [
                line.purchase_order_line_id,
                line.sku_id,
                line.line_status,
                order.status,
                order.supplier_id,
                order.supplier_site_id,
                order.order_date,
                line.quantity_ordered,
                line.order_uom_to_base_factor,
                line.quantity_received_base,
                order.requested_delivery_date,
            ]
        )

    cancellations = []
    cancellation_query = select(SupplyCancellation).order_by(
        SupplyCancellation.supply_cancellation_id
    )
    predicates = []
    if po_line_ids:
        predicates.append(SupplyCancellation.purchase_order_line_id.in_(set(po_line_ids)))
    if shipment_ids:
        predicates.append(SupplyCancellation.inbound_shipment_line_id.in_(set(shipment_ids)))
    if predicates:
        cancellations = [
            [
                row.supply_cancellation_id,
                row.purchase_order_line_id,
                row.inbound_shipment_line_id,
                row.quantity_cancelled_base,
            ]
            for row in session.scalars(cancellation_query.where(or_(*predicates))).all()
        ]

    reservations = [
        [
            row.supplier_capacity_reservation_id,
            row.purchase_order_line_id,
            row.reservation_quantity,
            row.capacity_uom_id,
            row.status,
        ]
        for row in session.scalars(
            select(SupplierCapacityReservation)
            .where(SupplierCapacityReservation.purchase_order_line_id.in_(set(po_line_ids)))
            .order_by(SupplierCapacityReservation.supplier_capacity_reservation_id)
        ).all()
    ] if po_line_ids else []
    lead_times = [
        [
            row.supplier_lead_time_id,
            row.supplier_id,
            row.supplier_site_id,
            row.sku_id,
            row.location_id,
            row.lead_time_days,
            row.effective_from,
            row.effective_to,
        ]
        for row in session.scalars(
            select(SupplierLeadTime)
            .where(
                SupplierLeadTime.sku_id.in_(scope_skus),
                SupplierLeadTime.location_id.in_(scope_locations),
            )
            .order_by(SupplierLeadTime.supplier_lead_time_id)
        ).all()
    ] if scope_skus and scope_locations else []
    return {
        "forecast_sources": forecast_sources,
        "shipment_sources": shipment_sources,
        "po_sources": po_sources,
        "cancellations": cancellations,
        "reservations": reservations,
        "lead_times": lead_times,
        "uom_mappings": [
            [row.sku_id, row.uom_id, row.to_base_factor]
            for row in session.scalars(
                select(SKUUOM)
                .where(SKUUOM.sku_id.in_(scope_skus))
                .order_by(SKUUOM.sku_id, SKUUOM.uom_id)
            ).all()
        ] if scope_skus else [],
    }


def create_planning_run(session: Session, request: PlanningRunCreate) -> PlanningRun:
    scope = {(item.sku_id, item.location_id) for item in request.items}
    skus = {
        sku.sku_id: sku
        for sku in session.scalars(select(SKU).where(SKU.sku_id.in_({sku for sku, _ in scope}))).all()
    }
    locations = {
        location.location_id: location
        for location in session.scalars(
            select(Location).where(Location.location_id.in_({location for _, location in scope}))
        ).all()
    }
    if len(skus) != len({sku_id for sku_id, _ in scope}) or any(sku.status != "active" for sku in skus.values()):
        raise PlanningInputError("Every selected SKU must exist and be active.")
    if len(locations) != len({location_id for _, location_id in scope}) or any(
        location.status != "active" for location in locations.values()
    ):
        raise PlanningInputError("Every selected location must exist and be active.")

    snapshot = _find_snapshot(session, request.inventory_snapshot_id)
    ledger = session.scalar(
        select(InventoryLedgerControl)
        .where(InventoryLedgerControl.ledger_key == "inventory")
        .with_for_update()
    )
    if ledger is None:
        raise PlanningInputError("The inventory ledger control row is missing.")
    cutoff = ledger.committed_version
    if snapshot.ledger_watermark > cutoff:
        raise PlanningInputError("The selected snapshot is ahead of the inventory ledger.")

    policy = {
        severity: format(value.normalize(), "f")
        for severity, value in sorted(request.severity_thresholds.items())
    }
    source_signature = _input_source_signature(session, scope)
    source_signature["inventory_balances"] = [
        [row.sku_id, row.location_id, row.quantity_on_hand_base]
        for row in session.scalars(
            select(InventoryBalance)
            .where(
                InventoryBalance.inventory_snapshot_id == snapshot.inventory_snapshot_id,
                tuple_(InventoryBalance.sku_id, InventoryBalance.location_id).in_(scope),
            )
            .order_by(InventoryBalance.sku_id, InventoryBalance.location_id)
        ).all()
    ]
    source_signature["inventory_transactions"] = [
        [row.ledger_version, row.sku_id, row.location_id, row.quantity_delta_base]
        for row in session.scalars(
            select(InventoryTransaction)
            .where(
                InventoryTransaction.ledger_version > snapshot.ledger_watermark,
                InventoryTransaction.ledger_version <= cutoff,
                tuple_(InventoryTransaction.sku_id, InventoryTransaction.location_id).in_(scope),
            )
            .order_by(InventoryTransaction.ledger_version)
        ).all()
    ]
    fingerprint_inputs = {
        "scope": sorted([list(pair) for pair in scope]),
        "horizon_start": request.horizon_start_week.isoformat(),
        "snapshot_id": snapshot.inventory_snapshot_id,
        "snapshot_watermark": snapshot.ledger_watermark,
        "ledger_cutoff": cutoff,
        "severity_policy": policy,
        "source_signature": source_signature,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_inputs,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    run = PlanningRun(
        run_key=uuid4().hex,
        scenario_key=request.scenario_key,
        horizon_start_week=request.horizon_start_week,
        horizon_weeks=52,
        inventory_snapshot_id=snapshot.inventory_snapshot_id,
        inventory_cutoff_version=cutoff,
        algorithm_version=ALGORITHM_VERSION,
        severity_policy_version=SEVERITY_POLICY_VERSION,
        input_fingerprint=fingerprint,
        run_status="draft",
    )
    try:
        session.add(run)
        session.flush()
        session.add_all(
            PlanningItem(
                planning_run_id=run.planning_run_id,
                sku_id=sku_id,
                location_id=location_id,
            )
            for sku_id, location_id in sorted(scope)
        )
        session.flush()
        _freeze_forecasts(
            session,
            run.planning_run_id,
            scope,
            request.horizon_start_week,
        )
        rule_findings = _build_supply(session, run, scope)
        for origin, details, sku_id, location_id in rule_findings:
            if origin == "readiness":
                session.add(
                    PlanningRunFinding(
                        planning_run_id=run.planning_run_id,
                        finding_origin="readiness",
                        issue_code="SUPPLY_READINESS",
                        severity="warning",
                        details=details,
                        sku_id=sku_id,
                        location_id=location_id,
                    )
                )
            else:
                session.add(
                    PlanningRunFinding(
                        planning_run_id=run.planning_run_id,
                        finding_origin="rule",
                        finding_code=origin,
                        severity="warning",
                        details=details,
                        sku_id=sku_id,
                        location_id=location_id,
                    )
                )
        session.add(
            PlanningRunFinding(
                planning_run_id=run.planning_run_id,
                finding_origin="readiness",
                issue_code="SEVERITY_POLICY",
                severity="info",
                details=(
                    "Shortage severity thresholds in base units: "
                    + json.dumps(policy, sort_keys=True, separators=(",", ":"))
                    + ". Any shortage below the configured medium threshold is low; "
                    "when no thresholds are configured, all shortages are low."
                ),
            )
        )
        session.flush()
        session.commit()
        session.refresh(run)
        return run
    except Exception:
        session.rollback()
        raise


def _severity(shortage: Decimal, thresholds: dict[str, Decimal]) -> str:
    severity = "low"
    for level in ("medium", "high", "critical"):
        if level in thresholds and shortage >= thresholds[level]:
            severity = level
    return severity


def calculate_pab_values(
    opening: Decimal,
    demand_by_week: dict[date, Decimal],
    supply_by_week: dict[date, tuple[Decimal, Decimal, Decimal]],
    horizon_start: date,
) -> list[dict]:
    """Pure weekly PAB calculation; preserves negative balances and exact decimals."""
    result: list[dict] = []
    with localcontext() as context:
        context.prec = 50
        balance = opening
        for index in range(52):
            week = horizon_start + timedelta(weeks=index)
            demand = demand_by_week.get(week, ZERO)
            firm_inbound, firm_po, unconfirmed = supply_by_week.get(
                week, (ZERO, ZERO, ZERO)
            )
            ending = balance + firm_inbound + firm_po - demand
            result.append(
                {
                    "week_start": week,
                    "opening_base": balance,
                    "forecast_demand_base": demand,
                    "firm_inbound_base": firm_inbound,
                    "firm_po_remainder_base": firm_po,
                    "unconfirmed_supply_base": unconfirmed,
                    "ending_firm_base": ending,
                    "shortage_quantity_base": max(ZERO, -ending),
                }
            )
            balance = ending
    return result


def calculate_planning_run(session: Session, run_id: int) -> PlanningRun:
    run = session.get(PlanningRun, run_id)
    if run is None:
        raise PlanningNotFoundError(f"Planning run {run_id} was not found.")
    if run.run_status == "completed":
        return run
    if run.run_status != "draft":
        raise PlanningInputError(f"Planning run cannot be calculated from status '{run.run_status}'.")
    try:
        session.query(PABWeek).filter(PABWeek.planning_run_id == run_id).delete(synchronize_session=False)
        session.query(ShortageCause).filter(ShortageCause.planning_run_id == run_id).delete(synchronize_session=False)
        run.run_status = "calculating"
        session.flush()

        scope_items = session.scalars(
            select(PlanningItem)
            .where(PlanningItem.planning_run_id == run_id)
            .order_by(PlanningItem.sku_id, PlanningItem.location_id)
        ).all()
        snapshot = session.get(InventorySnapshot, run.inventory_snapshot_id)
        if snapshot is None or snapshot.snapshot_status != "validated":
            raise PlanningInputError("The run's frozen inventory snapshot is not validated.")
        balances = {
            (row.sku_id, row.location_id): Decimal(row.quantity_on_hand_base)
            for row in session.scalars(
                select(InventoryBalance).where(
                    InventoryBalance.inventory_snapshot_id == run.inventory_snapshot_id
                )
            ).all()
        }
        ledger_deltas: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
        for sku_id, location_id, delta in session.execute(
            select(
                InventoryTransaction.sku_id,
                InventoryTransaction.location_id,
                InventoryTransaction.quantity_delta_base,
            ).where(
                InventoryTransaction.ledger_version > snapshot.ledger_watermark,
                InventoryTransaction.ledger_version <= run.inventory_cutoff_version,
            )
        ):
            ledger_deltas[(sku_id, location_id)] += Decimal(delta)

        demands: dict[tuple[int, int], dict[date, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
        for row in session.scalars(
            select(ForecastDemandWeek).where(ForecastDemandWeek.planning_run_id == run_id)
        ).all():
            demands[(row.sku_id, row.location_id)][row.week_start] += Decimal(row.forecast_quantity_base)

        supplies: dict[tuple[int, int], dict[date, tuple[Decimal, Decimal, Decimal]]] = defaultdict(dict)
        for row in session.scalars(
            select(PlanningSupplySnapshot).where(PlanningSupplySnapshot.planning_run_id == run_id)
        ).all():
            if row.expected_week_start is None:
                continue
            key = (row.sku_id, row.destination_location_id)
            prior = supplies[key].get(row.expected_week_start, (ZERO, ZERO, ZERO))
            supplies[key][row.expected_week_start] = (
                prior[0] + (Decimal(row.firm_quantity_base) if row.source_kind == "shipment" else ZERO),
                prior[1] + (Decimal(row.firm_quantity_base) if row.source_kind == "po_allocation" else ZERO),
                prior[2] + Decimal(row.unconfirmed_quantity_base),
            )

        policy_finding = session.scalar(
            select(PlanningRunFinding).where(
                PlanningRunFinding.planning_run_id == run_id,
                PlanningRunFinding.issue_code == "SEVERITY_POLICY",
            )
        )
        policy_text = (
            policy_finding.details.split("thresholds in base units: ", 1)[1]
            .split(". Any shortage", 1)[0]
            if policy_finding
            else "{}"
        )
        try:
            thresholds = {key: Decimal(value) for key, value in json.loads(policy_text).items()}
        except (json.JSONDecodeError, InvalidOperation) as error:
            raise PlanningCalculationError("The run's frozen severity policy is invalid.") from error

        for item in scope_items:
            key = (item.sku_id, item.location_id)
            opening = balances.get(key, ZERO) + ledger_deltas[key]
            calculated = calculate_pab_values(
                opening,
                demands[key],
                supplies[key],
                run.horizon_start_week,
            )
            for values in calculated:
                shortage = values["shortage_quantity_base"]
                row = PABWeek(
                    planning_run_id=run_id,
                    sku_id=item.sku_id,
                    location_id=item.location_id,
                    severity=_severity(shortage, thresholds) if shortage > ZERO else "none",
                    **values,
                )
                session.add(row)
            session.flush()
            for values in calculated:
                shortage = values["shortage_quantity_base"]
                if shortage <= ZERO:
                    continue
                cause = ShortageCause(
                    planning_run_id=run_id,
                    sku_id=item.sku_id,
                    location_id=item.location_id,
                    week_start=values["week_start"],
                    cause_code="demand_exceeds_firm_supply",
                    contribution_quantity_base=shortage,
                    explanation=(
                        f"Firm opening balance plus firm receipts less forecast demand leaves "
                        f"{shortage} base units short; unconfirmed supply was excluded."
                    ),
                )
                session.add(cause)
                unconfirmed = values["unconfirmed_supply_base"]
                if unconfirmed > ZERO:
                    session.add(
                        ShortageCause(
                            planning_run_id=run_id,
                            sku_id=item.sku_id,
                            location_id=item.location_id,
                            week_start=values["week_start"],
                            cause_code="capacity_unconfirmed_supply",
                            contribution_quantity_base=min(shortage, unconfirmed),
                            explanation=(
                                f"{unconfirmed} base units of supply were unconfirmed and "
                                "excluded from firm PAB."
                            ),
                        )
                    )

        run.run_status = "completed"
        session.commit()
        session.refresh(run)
        return run
    except Exception:
        session.rollback()
        raise
