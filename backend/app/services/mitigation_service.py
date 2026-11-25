"""Generate explainable, read-only mitigation options from planning evidence."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR, localcontext

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models.catalog import SKU, SKUUOM
from ..models.planning import (
    InventoryBalance,
    InventorySnapshot,
    InventoryTransaction,
    PABWeek,
    PlanningItem,
    PlanningPoResidualSnapshot,
    PlanningRun,
    PlanningSupplySnapshot,
)
from ..models.procurement import (
    InboundShipment,
    InboundShipmentLine,
    PurchaseOrderLine,
    SupplierCapacity,
    SupplierLeadTime,
)
from ..models.sourcing import (
    SKUSubstitution,
    SubstitutionApproval,
    Supplier,
    SupplierSKU,
    SupplierSite,
)
from ..schemas.mitigation import MitigationRecommendation

ZERO = Decimal("0")
ONE = Decimal("1")


def _week_end(week_start: date) -> date:
    return week_start + timedelta(days=6)


def _inventory_opening(
    session: Session,
    run: PlanningRun,
    snapshot: InventorySnapshot,
    sku_id: int,
    location_id: int,
) -> Decimal:
    balance = session.scalar(
        select(InventoryBalance.quantity_on_hand_base).where(
            InventoryBalance.inventory_snapshot_id == snapshot.inventory_snapshot_id,
            InventoryBalance.sku_id == sku_id,
            InventoryBalance.location_id == location_id,
        )
    )
    transactions = session.scalars(
        select(InventoryTransaction.quantity_delta_base).where(
            InventoryTransaction.sku_id == sku_id,
            InventoryTransaction.location_id == location_id,
            InventoryTransaction.ledger_version > snapshot.ledger_watermark,
            InventoryTransaction.ledger_version <= run.inventory_cutoff_version,
        )
    ).all()
    with localcontext() as context:
        context.prec = 50
        return Decimal(balance or ZERO) + sum(
            (Decimal(quantity) for quantity in transactions),
            start=ZERO,
        )


def _available_on_hand(
    opening: Decimal,
    pab_rows: list[PABWeek],
    through_week: date,
    previously_allocated: Decimal,
) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        demand = sum(
            (
                Decimal(row.forecast_demand_base)
                for row in pab_rows
                if row.week_start <= through_week
            ),
            start=ZERO,
        )
        return max(ZERO, opening - demand - previously_allocated)


def _pab_lookup(rows: list[PABWeek]) -> dict[tuple[int, int, date], PABWeek]:
    return {(row.sku_id, row.location_id, row.week_start): row for row in rows}


def _current_supplier_ids(
    session: Session,
    run_id: int,
    supplies: list[PlanningSupplySnapshot],
) -> dict[int, set[int]]:
    suppliers: dict[int, set[int]] = defaultdict(set)
    shipment_ids = [
        row.inbound_shipment_line_id
        for row in supplies
        if row.source_kind == "shipment" and row.inbound_shipment_line_id is not None
    ]
    if shipment_ids:
        for line, shipment in session.execute(
            select(InboundShipmentLine, InboundShipment)
            .join(
                InboundShipment,
                InboundShipment.inbound_shipment_id == InboundShipmentLine.inbound_shipment_id,
            )
            .where(InboundShipmentLine.inbound_shipment_line_id.in_(shipment_ids))
        ):
            suppliers[line.sku_id].add(shipment.supplier_id)

    residual_ids = [
        row.planning_po_residual_snapshot_id
        for row in supplies
        if row.source_kind == "po_allocation"
        and row.planning_po_residual_snapshot_id is not None
    ]
    if residual_ids:
        for sku_id, supplier_id in session.execute(
            select(PurchaseOrderLine.sku_id, PurchaseOrderLine.supplier_id)
            .join(
                PlanningPoResidualSnapshot,
                PlanningPoResidualSnapshot.purchase_order_line_id
                == PurchaseOrderLine.purchase_order_line_id,
            )
            .where(
                PlanningPoResidualSnapshot.planning_run_id == run_id,
                PlanningPoResidualSnapshot.planning_po_residual_snapshot_id.in_(residual_ids),
            )
        ):
            suppliers[sku_id].add(supplier_id)
    return suppliers


def _recommendation(
    *,
    run_id: int,
    shortage: PABWeek,
    recommendation_key: str,
    type: str,
    feasibility: str,
    affected: Decimal,
    proposed: Decimal | None,
    rationale: str,
    evidence: list[str],
    **values,
) -> MitigationRecommendation:
    return MitigationRecommendation(
        recommendation_key=recommendation_key,
        type=type,
        feasibility=feasibility,
        planning_run_id=run_id,
        sku_id=shortage.sku_id,
        location_id=shortage.location_id,
        shortage_week=shortage.week_start,
        affected_shortage_quantity_base=affected,
        proposed_quantity_base=proposed,
        rationale=rationale,
        evidence=evidence,
        **values,
    )


def _approved_substitutions(
    session: Session,
    source_sku_id: int,
    effective_date: date,
) -> list[tuple[SKUSubstitution, SubstitutionApproval]]:
    substitutions = session.scalars(
        select(SKUSubstitution)
        .where(
            SKUSubstitution.source_sku_id == source_sku_id,
            SKUSubstitution.status == "active",
            SKUSubstitution.effective_from <= effective_date,
            or_(
                SKUSubstitution.effective_to.is_(None),
                SKUSubstitution.effective_to >= effective_date,
            ),
        )
        .order_by(SKUSubstitution.substitution_id)
    ).all()
    eligible: list[tuple[SKUSubstitution, SubstitutionApproval]] = []
    for substitution in substitutions:
        applicable = session.scalars(
            select(SubstitutionApproval)
            .where(
                SubstitutionApproval.substitution_id == substitution.substitution_id,
                SubstitutionApproval.effective_from <= effective_date,
                or_(
                    SubstitutionApproval.effective_to.is_(None),
                    SubstitutionApproval.effective_to >= effective_date,
                ),
            )
            .order_by(SubstitutionApproval.revision_no.desc())
        ).all()
        if applicable and applicable[0].decision == "approved":
            eligible.append((substitution, applicable[0]))
    return eligible


def _supplier_options(
    session: Session,
    run: PlanningRun,
    shortage: PABWeek,
    current_suppliers: set[int],
    capacity_remaining: dict[int, Decimal],
) -> list[MitigationRecommendation]:
    order_date = run.captured_at.date()
    offers = session.execute(
        select(SupplierSKU, Supplier)
        .join(Supplier, Supplier.supplier_id == SupplierSKU.supplier_id)
        .where(
            SupplierSKU.sku_id == shortage.sku_id,
            SupplierSKU.status == "active",
            Supplier.status == "active",
            SupplierSKU.effective_from <= order_date,
            or_(
                SupplierSKU.effective_to.is_(None),
                SupplierSKU.effective_to >= order_date,
            ),
        )
        .order_by(SupplierSKU.supplier_id)
    ).all()
    recommendations: list[MitigationRecommendation] = []
    for offer, supplier in offers:
        if supplier.supplier_id in current_suppliers:
            continue
        order_uom = session.get(SKUUOM, (shortage.sku_id, offer.order_uom_id))
        if order_uom is None:
            continue
        order_factor = Decimal(order_uom.to_base_factor)
        if order_factor <= ZERO:
            continue

        lead_times = session.scalars(
            select(SupplierLeadTime)
            .join(
                SupplierSite,
                and_(
                    SupplierSite.supplier_id == SupplierLeadTime.supplier_id,
                    SupplierSite.supplier_site_id == SupplierLeadTime.supplier_site_id,
                ),
            )
            .where(
                SupplierLeadTime.supplier_id == supplier.supplier_id,
                SupplierLeadTime.sku_id == shortage.sku_id,
                SupplierLeadTime.location_id == shortage.location_id,
                SupplierSite.status == "active",
                SupplierLeadTime.effective_from <= order_date,
                or_(
                    SupplierLeadTime.effective_to.is_(None),
                    SupplierLeadTime.effective_to >= order_date,
                ),
            )
            .order_by(
                SupplierLeadTime.supplier_site_id,
                SupplierLeadTime.effective_from.desc(),
                SupplierLeadTime.supplier_lead_time_id.desc(),
            )
        ).all()
        latest_lead_by_site: dict[int, SupplierLeadTime] = {}
        for lead in lead_times:
            latest_lead_by_site.setdefault(lead.supplier_site_id, lead)
        applicable_leads = sorted(
            latest_lead_by_site.values(),
            key=lambda row: (row.lead_time_days, row.supplier_site_id),
        )
        for lead in applicable_leads:
            arrival = order_date + timedelta(days=lead.lead_time_days)
            if arrival > _week_end(shortage.week_start):
                continue
            capacity = session.scalar(
                select(SupplierCapacity)
                .where(
                    SupplierCapacity.supplier_id == supplier.supplier_id,
                    SupplierCapacity.sku_id == shortage.sku_id,
                    SupplierCapacity.effective_from <= arrival,
                    or_(
                        SupplierCapacity.effective_to.is_(None),
                        SupplierCapacity.effective_to >= arrival,
                    ),
                )
                .order_by(
                    SupplierCapacity.effective_from.desc(),
                    SupplierCapacity.supplier_capacity_id,
                )
                .limit(1)
            )
            if capacity is None:
                continue
            capacity_uom = session.get(
                SKUUOM,
                (shortage.sku_id, capacity.capacity_uom_id),
            )
            if capacity_uom is None:
                continue
            capacity_factor = Decimal(capacity_uom.to_base_factor)
            if capacity_factor <= ZERO:
                continue
            if capacity.supplier_capacity_id not in capacity_remaining:
                with localcontext() as context:
                    context.prec = 50
                    capacity_remaining[capacity.supplier_capacity_id] = (
                        Decimal(capacity.capacity_quantity)
                        - Decimal(capacity.reserved_quantity)
                    ) * capacity_factor
            available_base = max(
                ZERO,
                capacity_remaining[capacity.supplier_capacity_id],
            )
            with localcontext() as context:
                context.prec = 50
                maximum_order_qty = min(
                    Decimal(shortage.shortage_quantity_base),
                    available_base,
                ) / order_factor
                multiple = Decimal(offer.order_multiple)
                valid_multiple_count = (
                    (maximum_order_qty / multiple).to_integral_value(rounding=ROUND_FLOOR)
                    if multiple > ZERO
                    else ZERO
                )
                order_quantity = valid_multiple_count * multiple
                proposed_base = order_quantity * order_factor
                estimated_cost = order_quantity * Decimal(offer.unit_cost)
            if order_quantity < Decimal(offer.minimum_order_qty):
                continue
            if proposed_base <= ZERO:
                continue
            with localcontext() as context:
                context.prec = 50
                capacity_remaining[capacity.supplier_capacity_id] -= proposed_base
            recommendations.append(
                _recommendation(
                    run_id=run.planning_run_id,
                    shortage=shortage,
                    recommendation_key=(
                        f"supplier:{shortage.sku_id}:{shortage.location_id}:"
                        f"{shortage.week_start}:{offer.supplier_sku_id}"
                    ),
                    type="alternative_supplier",
                    feasibility="feasible",
                    affected=min(Decimal(shortage.shortage_quantity_base), proposed_base),
                    proposed=proposed_base,
                    supplier_id=supplier.supplier_id,
                    supplier_sku_id=offer.supplier_sku_id,
                    expected_arrival_date=arrival,
                    estimated_cost=estimated_cost,
                    cost_currency=offer.currency,
                    rationale=(
                        f"Active supplier offer, effective lead time, and unreserved capacity "
                        f"support {proposed_base} base units by the shortage week. This is a "
                        "review option, not a purchase-order commitment."
                    ),
                    evidence=[
                        f"Supplier-SKU offer {offer.supplier_sku_id} is active on {order_date}.",
                        f"Lead time is {lead.lead_time_days} days for supplier site {lead.supplier_site_id}.",
                        f"Estimated arrival is {arrival}, based on the run capture date plus the recorded lead time.",
                        f"Capacity {capacity.supplier_capacity_id} has at least {proposed_base} "
                        "unreserved base units.",
                        f"Estimated cost uses the recorded offer price in {offer.currency}.",
                    ],
                )
            )
            break
    return recommendations


def get_mitigation_recommendations(
    session: Session,
    run_id: int,
    shortage_filter: tuple[int, int, date] | None = None,
) -> list[MitigationRecommendation]:
    run = session.get(PlanningRun, run_id)
    if run is None:
        raise LookupError(f"Planning run {run_id} was not found.")
    if run.run_status not in {"completed", "published", "superseded"}:
        return []

    pab_rows = session.scalars(
        select(PABWeek)
        .where(
            PABWeek.planning_run_id == run_id,
            PABWeek.shortage_quantity_base > ZERO,
        )
        .order_by(PABWeek.week_start, PABWeek.sku_id, PABWeek.location_id)
    ).all()
    if shortage_filter is not None:
        sku_id, location_id, week_start = shortage_filter
        pab_rows = [
            row
            for row in pab_rows
            if (row.sku_id, row.location_id, row.week_start)
            == (sku_id, location_id, week_start)
        ]
    if not pab_rows:
        return []

    pab_by_pair: dict[tuple[int, int], list[PABWeek]] = defaultdict(list)
    for row in session.scalars(
        select(PABWeek)
        .where(PABWeek.planning_run_id == run_id)
        .order_by(PABWeek.sku_id, PABWeek.location_id, PABWeek.week_start)
    ).all():
        pab_by_pair[(row.sku_id, row.location_id)].append(row)
    pab_index = _pab_lookup([row for rows in pab_by_pair.values() for row in rows])
    items = session.scalars(
        select(PlanningItem).where(PlanningItem.planning_run_id == run_id)
    ).all()
    scope = {(item.sku_id, item.location_id) for item in items}
    supply_rows = session.scalars(
        select(PlanningSupplySnapshot)
        .where(PlanningSupplySnapshot.planning_run_id == run_id)
        .order_by(PlanningSupplySnapshot.planning_supply_snapshot_id)
    ).all()
    current_suppliers = _current_supplier_ids(session, run_id, supply_rows)
    snapshot = session.get(InventorySnapshot, run.inventory_snapshot_id)
    if snapshot is None:
        return []
    opening_by_pair = {
        pair: _inventory_opening(session, run, snapshot, *pair)
        for pair in scope
    }

    recommendations: list[MitigationRecommendation] = []
    used_shipment_ids: set[int] = set()
    allocated_inventory: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    capacity_remaining: dict[int, Decimal] = {}

    # Each inbound line can be proposed for only its earliest eligible shortage.
    for shortage in pab_rows:
        remaining_shortage = Decimal(shortage.shortage_quantity_base)
        candidates = [
            supply
            for supply in supply_rows
            if supply.source_kind == "shipment"
            and supply.sku_id == shortage.sku_id
            and supply.destination_location_id == shortage.location_id
            and supply.firm_quantity_base > ZERO
            and supply.expected_arrival_date is not None
            and supply.expected_arrival_date > _week_end(shortage.week_start)
            and supply.inbound_shipment_line_id not in used_shipment_ids
        ]
        candidates.sort(
            key=lambda row: (
                row.expected_arrival_date or date.max,
                row.inbound_shipment_line_id or 0,
            )
        )
        for supply in candidates:
            proposed = min(remaining_shortage, Decimal(supply.firm_quantity_base))
            if proposed <= ZERO:
                continue
            used_shipment_ids.add(supply.inbound_shipment_line_id)
            remaining_shortage -= proposed
            recommendations.append(
                _recommendation(
                    run_id=run_id,
                    shortage=shortage,
                    recommendation_key=(
                        f"expedite:{supply.inbound_shipment_line_id}:{shortage.week_start}"
                    ),
                    type="expedite_inbound",
                    feasibility="insufficient_evidence",
                    affected=proposed,
                    proposed=proposed,
                    inbound_shipment_line_id=supply.inbound_shipment_line_id,
                    current_expected_arrival_date=supply.expected_arrival_date,
                    expected_arrival_date=None,
                    rationale=(
                        f"Firm shipment line {supply.inbound_shipment_line_id} is currently "
                        f"scheduled after the shortage week and has {supply.firm_quantity_base} "
                        "base units. An earlier arrival could reduce the shortage, but no "
                        "expedited transit commitment is recorded."
                    ),
                    evidence=[
                        f"Run-frozen shipment snapshot {supply.planning_supply_snapshot_id}.",
                        f"Current expected arrival is {supply.expected_arrival_date}.",
                        "No expedited carrier or transit-time evidence is available.",
                    ],
                )
            )
            if remaining_shortage <= ZERO:
                break

    for shortage in pab_rows:
        for source_sku_id, source_location_id in sorted(scope):
            if (
                source_sku_id != shortage.sku_id
                or source_location_id == shortage.location_id
            ):
                continue
            source_week = pab_index.get(
                (source_sku_id, source_location_id, shortage.week_start)
            )
            if source_week is None:
                continue
            available = _available_on_hand(
                opening_by_pair[(source_sku_id, source_location_id)],
                pab_by_pair[(source_sku_id, source_location_id)],
                shortage.week_start,
                allocated_inventory[(source_sku_id, source_location_id)],
            )
            proposed = min(Decimal(shortage.shortage_quantity_base), available)
            if proposed <= ZERO:
                continue
            with localcontext() as context:
                context.prec = 50
                allocated_inventory[(source_sku_id, source_location_id)] += proposed
            recommendations.append(
                _recommendation(
                    run_id=run_id,
                    shortage=shortage,
                    recommendation_key=(
                        f"transfer:{source_sku_id}:{source_location_id}:"
                        f"{shortage.week_start}"
                    ),
                    type="inventory_transfer",
                    feasibility="insufficient_evidence",
                    affected=proposed,
                    proposed=proposed,
                    source_location_id=source_location_id,
                    rationale=(
                        f"Location {source_location_id} has {proposed} base units of opening "
                        "inventory remaining after its own forecast demand through this week. "
                        "No inter-location transit time or transfer lane is recorded, so arrival "
                        "before the shortage is unverified."
                    ),
                    evidence=[
                        "Availability is based on the run's inventory snapshot and ledger cutoff.",
                        "The source location's own forecast demand through the shortage week was deducted.",
                        "Future inbound supply was excluded from transferable stock.",
                    ],
                )
            )

        source_sku = session.get(SKU, shortage.sku_id)
        source_base_mapping = session.get(SKUUOM, (shortage.sku_id, source_sku.base_uom_id))
        if source_base_mapping is None or not source_base_mapping.is_base_uom:
            continue
        for substitution, approval in _approved_substitutions(
            session,
            shortage.sku_id,
            shortage.week_start,
        ):
            substitute = session.get(SKU, substitution.substitute_sku_id)
            if substitute is None or substitute.status != "active":
                continue
            substitute_base_mapping = session.get(
                SKUUOM,
                (substitute.sku_id, substitute.base_uom_id),
            )
            if substitute_base_mapping is None or not substitute_base_mapping.is_base_uom:
                continue
            factor = Decimal(substitution.conversion_factor)
            with localcontext() as context:
                context.prec = 50
                proposed = Decimal(shortage.shortage_quantity_base) * factor
            if proposed <= ZERO:
                continue
            substitute_pair = (substitute.sku_id, shortage.location_id)
            available = ZERO
            has_availability_evidence = (
                substitute_pair in scope
                and (substitute.sku_id, shortage.location_id, shortage.week_start)
                in pab_index
            )
            if has_availability_evidence:
                available = _available_on_hand(
                    opening_by_pair[substitute_pair],
                    pab_by_pair[substitute_pair],
                    shortage.week_start,
                    allocated_inventory[substitute_pair],
                )
                if available > ZERO:
                    proposed = min(proposed, available)
                    with localcontext() as context:
                        context.prec = 50
                        allocated_inventory[substitute_pair] += proposed
            with localcontext() as context:
                context.prec = 50
                affected = min(
                    Decimal(shortage.shortage_quantity_base),
                    proposed / factor,
                )
            evidence = [
                f"Substitution {substitution.substitution_id} is effective on {shortage.week_start}.",
                f"Approval revision {approval.revision_no} is approved and effective on "
                f"{shortage.week_start}.",
                f"Conversion factor {factor} converts source base units to substitute base units.",
            ]
            if has_availability_evidence:
                evidence.append(
                    f"Run inventory after substitute SKU demand has {available} base units available."
                )
            else:
                evidence.append(
                    "The substitute SKU is outside the selected planning scope; its availability "
                    "and arrival cannot be confirmed."
                )
            recommendations.append(
                _recommendation(
                    run_id=run_id,
                    shortage=shortage,
                    recommendation_key=(
                        f"substitute:{substitution.substitution_id}:{shortage.week_start}"
                    ),
                    type="approved_substitute",
                    feasibility=(
                        "feasible"
                        if has_availability_evidence and available > ZERO
                        else "insufficient_evidence"
                    ),
                    affected=affected,
                    proposed=proposed,
                    source_sku_id=substitute.sku_id,
                    substitution_id=substitution.substitution_id,
                    rationale=(
                        f"An approved substitute is effective for this shortage. {proposed} "
                        "substitute base units are proposed using the recorded conversion factor. "
                        + (
                            "Run inventory supports this quantity after the substitute's own demand."
                            if has_availability_evidence and available > ZERO
                            else "Substitute availability is not evidenced in this run."
                        )
                    ),
                    evidence=evidence,
                )
            )

        recommendations.extend(
            _supplier_options(
                session,
                run,
                shortage,
                current_suppliers.get(shortage.sku_id, set()),
                capacity_remaining,
            )
        )

    return recommendations
