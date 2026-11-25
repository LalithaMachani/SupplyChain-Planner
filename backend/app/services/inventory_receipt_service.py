"""Idempotent receipt posting coordinated with the A3 receipt triggers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.planning import InventoryReceiptPosting, InventoryTransaction
from ..models.procurement import InboundShipmentLine, PurchaseOrderLine

BASE_QUANTUM = Decimal("0.00000001")


class ReceiptPostingError(ValueError):
    """A receipt request is invalid or its database effects did not reconcile."""


class ReceiptPostingConflict(ReceiptPostingError):
    """An idempotency key was reused with a different payload."""


@dataclass(frozen=True)
class ReceiptPostingResult:
    receipt_posting_id: int
    inventory_transaction_id: int
    ledger_version: int
    quantity_delta_base: Decimal


def _payload_hash(
    inbound_shipment_line_id: int,
    quantity_delta_uom: Decimal,
    posting_type: str,
) -> str:
    payload = (
        f"{inbound_shipment_line_id}|{quantity_delta_uom.normalize()}|{posting_type}"
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _result(session: Session, posting: InventoryReceiptPosting) -> ReceiptPostingResult:
    transaction = session.scalar(
        select(InventoryTransaction).where(
            InventoryTransaction.inventory_receipt_posting_id
            == posting.inventory_receipt_posting_id
        )
    )
    if transaction is None or posting.posting_status != "applied":
        raise ReceiptPostingError("Receipt posting did not produce its inventory transaction.")
    return ReceiptPostingResult(
        receipt_posting_id=posting.inventory_receipt_posting_id,
        inventory_transaction_id=transaction.inventory_transaction_id,
        ledger_version=transaction.ledger_version,
        quantity_delta_base=transaction.quantity_delta_base,
    )


def post_inventory_receipt(
    session: Session,
    *,
    inbound_shipment_line_id: int,
    quantity_delta_uom: Decimal,
    idempotency_key: str,
    posting_type: str = "receipt",
) -> ReceiptPostingResult:
    """Post one cumulative A3 receipt change in a single database transaction.

    The caller must pass a Session with no active transaction. The quantity delta
    is expressed in the inbound line's UOM; all recorded base quantities are
    derived from the immutable A3 conversion factor.
    """
    if session.in_transaction():
        raise ReceiptPostingError("Receipt posting requires a Session with no active transaction.")
    delta_uom = Decimal(str(quantity_delta_uom))
    if not delta_uom.is_finite() or delta_uom == 0:
        raise ReceiptPostingError("Receipt quantity delta must be a finite nonzero value.")
    if delta_uom.copy_abs() >= Decimal("100000000000000"):
        raise ReceiptPostingError("Receipt quantity delta exceeds the inbound UOM precision.")
    try:
        quantized_delta = delta_uom.quantize(Decimal("0.0001"))
    except InvalidOperation as error:
        raise ReceiptPostingError(
            "Receipt quantity delta must fit the inbound UOM precision."
        ) from error
    if quantized_delta != delta_uom:
        raise ReceiptPostingError(
            "Receipt quantity delta may not exceed four fractional digits."
        )
    delta_uom = quantized_delta
    if posting_type not in {"receipt", "correction"}:
        raise ReceiptPostingError("posting_type must be 'receipt' or 'correction'.")
    if posting_type == "receipt" and delta_uom < 0:
        raise ReceiptPostingError("A receipt posting must have a positive quantity delta.")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ReceiptPostingError("A nonempty idempotency key of at most 128 characters is required.")

    payload_hash = _payload_hash(inbound_shipment_line_id, delta_uom, posting_type)
    existing_conflict = False
    with session.begin():
        existing = session.scalar(
            select(InventoryReceiptPosting).where(
                InventoryReceiptPosting.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if existing.payload_hash == payload_hash:
                existing_result = _result(session, existing)
            else:
                existing_result = None
                existing_conflict = True
        else:
            existing_result = None
            existing_conflict = False
    if existing is not None:
        if existing_conflict:
            raise ReceiptPostingConflict(
                "Idempotency key was already used with a different receipt payload."
            )
        return existing_result

    try:
        with session.begin():
            line = session.scalar(
                select(InboundShipmentLine)
                .where(
                    InboundShipmentLine.inbound_shipment_line_id
                    == inbound_shipment_line_id
                )
                .with_for_update()
            )
            if line is None:
                raise ReceiptPostingError(
                    f"Inbound shipment line {inbound_shipment_line_id} was not found."
                )

            prior_quantity = Decimal(line.quantity_received)
            target_quantity = prior_quantity + delta_uom
            if target_quantity < 0 or target_quantity > Decimal(line.quantity_shipped):
                raise ReceiptPostingError(
                    "Receipt quantity must remain between zero and shipped quantity."
                )
            factor = Decimal(line.uom_to_base_factor)
            with localcontext() as context:
                context.prec = 50
                prior_line_base = prior_quantity * factor
                target_line_base = target_quantity * factor
                line_delta_base = target_line_base - prior_line_base

            po_line: PurchaseOrderLine | None = None
            prior_po_base: Decimal | None = None
            target_po_base: Decimal | None = None
            if line.purchase_order_line_id is not None:
                po_line = session.scalar(
                    select(PurchaseOrderLine)
                    .where(
                        PurchaseOrderLine.purchase_order_line_id
                        == line.purchase_order_line_id
                    )
                    .with_for_update()
                )
                if po_line is None:
                    raise ReceiptPostingError(
                        "The inbound line references a missing purchase-order line."
                    )
                prior_po_base = Decimal(po_line.quantity_received_base)
                with localcontext() as context:
                    context.prec = 50
                    target_po_base = (prior_po_base + line_delta_base).quantize(
                        BASE_QUANTUM,
                        rounding=ROUND_HALF_UP,
                    )
                with localcontext() as context:
                    context.prec = 50
                    ordered_base = (
                        Decimal(po_line.quantity_ordered)
                        * Decimal(po_line.order_uom_to_base_factor)
                    )
                if target_po_base < 0 or target_po_base > ordered_base:
                    raise ReceiptPostingError(
                        "The receipt would exceed or underflow the PO line's base quantity."
                    )

            posting = InventoryReceiptPosting(
                inbound_shipment_line_id=line.inbound_shipment_line_id,
                purchase_order_line_id=line.purchase_order_line_id,
                pending_line_id=line.inbound_shipment_line_id,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                posting_type=posting_type,
                prior_quantity_received=prior_quantity,
                target_quantity_received=target_quantity,
                quantity_delta_uom=delta_uom,
                uom_to_base_factor=factor,
                prior_line_received_base=prior_line_base,
                target_line_received_base=target_line_base,
                prior_po_received_base=prior_po_base,
                target_po_received_base=target_po_base,
                posting_status="pending",
            )
            session.add(posting)
            session.flush()

            line.quantity_received = target_quantity
            session.flush()
            session.refresh(posting)
            if posting.posting_status != "applied":
                raise ReceiptPostingError(
                    "The A4 receipt trigger did not apply the receipt posting."
                )
            if po_line is not None:
                session.refresh(po_line, attribute_names=["quantity_received_base"])
                if po_line.quantity_received_base != target_po_base:
                    raise ReceiptPostingError(
                        "The A3 purchase-order receipt counter did not reach its target."
                    )
            result = _result(session, posting)
        return result
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(InventoryReceiptPosting).where(
                InventoryReceiptPosting.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise ReceiptPostingConflict(
                    "Idempotency key was already used with a different receipt payload."
                )
            return _result(session, existing)
        raise
