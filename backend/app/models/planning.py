"""Inventory ledger and versioned demand/inventory planning models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db.database import Base
from .catalog import UNSIGNED_BIGINT, UNSIGNED_SMALLINT

BASE_QUANTITY = Numeric(28, 8)
LEDGER_QUANTITY = Numeric(38, 12)


class InventoryLedgerControl(Base):
    """Transactional high-water mark for posted inventory movements."""

    __tablename__ = "inventory_ledger_control"
    __table_args__ = (
        CheckConstraint("committed_version >= 0", name="ck_inventory_ledger_version_nonnegative"),
    )

    ledger_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    committed_version: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        nullable=False,
        default=0,
        server_default="0",
    )


class InventorySnapshot(Base):
    """A versioned opening inventory snapshot."""

    __tablename__ = "inventory_snapshot"
    __table_args__ = (
        CheckConstraint(
            "snapshot_status IN ('draft', 'validated', 'superseded')",
            name="ck_inventory_snapshot_status",
        ),
        Index("ix_inventory_snapshot_status_capture", "snapshot_status", "captured_at"),
    )

    inventory_snapshot_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    snapshot_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    ledger_watermark: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        nullable=False,
        server_default="0",
    )
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("app_user.user_id", ondelete="SET NULL"),
    )


class InventoryBalance(Base):
    """Base-unit on-hand quantity in one inventory snapshot."""

    __tablename__ = "inventory_balance"
    __table_args__ = (
        CheckConstraint(
            "quantity_on_hand_base >= 0",
            name="ck_inventory_balance_nonnegative",
        ),
        Index(
            "ix_inventory_balance_snapshot_location",
            "inventory_snapshot_id",
            "location_id",
            "sku_id",
        ),
    )

    inventory_snapshot_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("inventory_snapshot.inventory_snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku.sku_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    location_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("location.location_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    quantity_on_hand_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)


class InventoryReceiptPosting(Base):
    """Idempotent receipt request, applied atomically with A3 receipt counters."""

    __tablename__ = "inventory_receipt_posting"
    __table_args__ = (
        UniqueConstraint(
            "inbound_shipment_line_id",
            "pending_line_id",
            name="uq_receipt_posting_line_pending",
        ),
        CheckConstraint(
            "posting_type IN ('receipt', 'correction')",
            name="ck_receipt_posting_type",
        ),
        CheckConstraint(
            "(posting_type = 'receipt' AND quantity_delta_uom > 0) OR "
            "(posting_type = 'correction' AND quantity_delta_uom <> 0)",
            name="ck_receipt_posting_delta_type",
        ),
        CheckConstraint(
            "posting_status IN ('pending', 'applied')",
            name="ck_receipt_posting_status",
        ),
        CheckConstraint("uom_to_base_factor > 0", name="ck_receipt_posting_factor_positive"),
        CheckConstraint(
            "prior_quantity_received >= 0 AND target_quantity_received >= 0",
            name="ck_receipt_posting_line_quantity_nonnegative",
        ),
        CheckConstraint(
            "target_quantity_received - prior_quantity_received = quantity_delta_uom",
            name="ck_receipt_posting_line_delta",
        ),
        CheckConstraint(
            "prior_line_received_base >= 0 AND target_line_received_base >= 0",
            name="ck_receipt_posting_line_base_nonnegative",
        ),
        CheckConstraint(
            "target_line_received_base - prior_line_received_base = "
            "quantity_delta_uom * uom_to_base_factor",
            name="ck_receipt_posting_line_base_delta",
        ),
        CheckConstraint(
            "(purchase_order_line_id IS NULL "
            "AND prior_po_received_base IS NULL AND target_po_received_base IS NULL) OR "
            "(purchase_order_line_id IS NOT NULL "
            "AND prior_po_received_base IS NOT NULL AND target_po_received_base IS NOT NULL)",
            name="ck_receipt_posting_po_counters_pair",
        ),
        CheckConstraint(
            "(posting_status = 'pending' AND pending_line_id = inbound_shipment_line_id "
            "AND applied_at IS NULL) OR "
            "(posting_status = 'applied' AND pending_line_id IS NULL "
            "AND applied_at IS NOT NULL)",
            name="ck_receipt_posting_pending_state",
        ),
        CheckConstraint(
            "pending_line_id IS NULL OR pending_line_id = inbound_shipment_line_id",
            name="ck_receipt_posting_pending_line",
        ),
        Index(
            "ix_receipt_posting_line_created",
            "inbound_shipment_line_id",
            "created_at",
        ),
    )

    inventory_receipt_posting_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    inbound_shipment_line_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("inbound_shipment_line.inbound_shipment_line_id", ondelete="RESTRICT"),
        nullable=False,
    )
    purchase_order_line_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("purchase_order_line.purchase_order_line_id", ondelete="RESTRICT"),
    )
    pending_line_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("inbound_shipment_line.inbound_shipment_line_id", ondelete="RESTRICT"),
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    posting_type: Mapped[str] = mapped_column(String(16), nullable=False)
    prior_quantity_received: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    target_quantity_received: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    quantity_delta_uom: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    uom_to_base_factor: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    prior_line_received_base: Mapped[Decimal] = mapped_column(LEDGER_QUANTITY, nullable=False)
    target_line_received_base: Mapped[Decimal] = mapped_column(LEDGER_QUANTITY, nullable=False)
    prior_po_received_base: Mapped[Decimal | None] = mapped_column(BASE_QUANTITY)
    target_po_received_base: Mapped[Decimal | None] = mapped_column(BASE_QUANTITY)
    posting_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InventoryTransaction(Base):
    """Immutable signed base-unit inventory movement."""

    __tablename__ = "inventory_transaction"
    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('receipt', 'receipt_correction', 'issue', "
            "'adjustment', 'transfer_in', 'transfer_out')",
            name="ck_inventory_transaction_type",
        ),
        CheckConstraint(
            "quantity_delta_base <> 0",
            name="ck_inventory_transaction_nonzero",
        ),
        CheckConstraint(
            "ledger_version > 0",
            name="ck_inventory_transaction_version_positive",
        ),
        CheckConstraint(
            "(transaction_type IN ('receipt', 'receipt_correction') "
            "AND inventory_receipt_posting_id IS NOT NULL "
            "AND inbound_shipment_line_id IS NOT NULL) OR "
            "(transaction_type NOT IN ('receipt', 'receipt_correction') "
            "AND inventory_receipt_posting_id IS NULL)",
            name="ck_inventory_transaction_receipt_source",
        ),
        Index(
            "ix_inventory_transaction_sku_location_version",
            "sku_id",
            "location_id",
            "ledger_version",
        ),
    )

    inventory_transaction_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    ledger_version: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False, unique=True)
    inventory_receipt_posting_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey(
            "inventory_receipt_posting.inventory_receipt_posting_id",
            ondelete="RESTRICT",
        ),
        unique=True,
    )
    inbound_shipment_line_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("inbound_shipment_line.inbound_shipment_line_id", ondelete="RESTRICT"),
    )
    sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku.sku_id", ondelete="RESTRICT"),
        nullable=False,
    )
    location_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("location.location_id", ondelete="RESTRICT"),
        nullable=False,
    )
    transaction_type: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity_delta_base: Mapped[Decimal] = mapped_column(LEDGER_QUANTITY, nullable=False)
    source_event_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class SupplyCancellation(Base):
    """Append-only base-unit cancellation against a PO or shipment line."""

    __tablename__ = "supply_cancellation"
    __table_args__ = (
        CheckConstraint(
            "(purchase_order_line_id IS NOT NULL AND inbound_shipment_line_id IS NULL) OR "
            "(purchase_order_line_id IS NULL AND inbound_shipment_line_id IS NOT NULL)",
            name="ck_supply_cancellation_single_source",
        ),
        CheckConstraint(
            "quantity_cancelled_base > 0",
            name="ck_supply_cancellation_positive",
        ),
        Index(
            "ix_supply_cancellation_po_time",
            "purchase_order_line_id",
            "cancelled_at",
        ),
        Index(
            "ix_supply_cancellation_shipment_time",
            "inbound_shipment_line_id",
            "cancelled_at",
        ),
    )

    supply_cancellation_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    purchase_order_line_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("purchase_order_line.purchase_order_line_id", ondelete="RESTRICT"),
    )
    inbound_shipment_line_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("inbound_shipment_line.inbound_shipment_line_id", ondelete="RESTRICT"),
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    quantity_cancelled_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    cancelled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)


class PlanningRun(Base):
    """Immutable, reproducible planning input and lifecycle header."""

    __tablename__ = "planning_run"
    __table_args__ = (
        CheckConstraint("horizon_weeks = 52", name="ck_planning_run_horizon"),
        CheckConstraint(
            "run_status IN ('draft', 'validating', 'calculating', 'completed', "
            "'published', 'failed', 'superseded')",
            name="ck_planning_run_status",
        ),
        Index("ix_planning_run_status_created", "run_status", "created_at"),
    )

    planning_run_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    run_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scenario_key: Mapped[str] = mapped_column(String(64), nullable=False)
    horizon_start_week: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_weeks: Mapped[int] = mapped_column(
        UNSIGNED_SMALLINT,
        nullable=False,
        default=52,
        server_default="52",
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    inventory_snapshot_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("inventory_snapshot.inventory_snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    inventory_cutoff_version: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        nullable=False,
        server_default="0",
    )
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    severity_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    run_status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    created_by_user_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("app_user.user_id", ondelete="SET NULL"),
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )


class PlanningItem(Base):
    """One active SKU/location pair in a planning run."""

    __tablename__ = "planning_item"
    __table_args__ = (
        ForeignKeyConstraint(
            ["planning_run_id"],
            ["planning_run.planning_run_id"],
            ondelete="RESTRICT",
        ),
        Index("ix_planning_item_run_location_sku", "planning_run_id", "location_id", "sku_id"),
    )

    planning_run_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
    )
    sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku.sku_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    location_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("location.location_id", ondelete="RESTRICT"),
        primary_key=True,
    )


class ForecastDemandWeek(Base):
    """Frozen base-unit forecast for one run, item, and ISO week."""

    __tablename__ = "forecast_demand_week"
    __table_args__ = (
        ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id"],
            [
                "planning_item.planning_run_id",
                "planning_item.sku_id",
                "planning_item.location_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("forecast_quantity_base >= 0", name="ck_forecast_demand_nonnegative"),
        Index(
            "ix_forecast_demand_run_location_week",
            "planning_run_id",
            "location_id",
            "week_start",
            "sku_id",
        ),
    )

    planning_run_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True)
    sku_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True)
    location_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    forecast_quantity_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    source_staging_record_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("staging_record.staging_record_id", ondelete="RESTRICT"),
    )
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))


class PlanningPoResidualSnapshot(Base):
    """Run-frozen unscheduled remainder for one PO line."""

    __tablename__ = "planning_po_residual_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "planning_run_id",
            "purchase_order_line_id",
            name="uq_planning_po_residual_run_line",
        ),
        UniqueConstraint(
            "planning_po_residual_snapshot_id",
            "planning_run_id",
            name="uq_planning_po_residual_id_run",
        ),
        CheckConstraint(
            "ordered_base >= 0 AND received_base >= 0 AND scheduled_base >= 0 "
            "AND cancelled_base >= 0 AND residual_base >= 0",
            name="ck_planning_po_residual_nonnegative",
        ),
        CheckConstraint(
            "ordered_base = received_base + scheduled_base + cancelled_base + residual_base",
            name="ck_planning_po_residual_reconciled",
        ),
        Index(
            "ix_planning_po_residual_line",
            "purchase_order_line_id",
            "planning_run_id",
        ),
    )

    planning_po_residual_snapshot_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    planning_run_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("planning_run.planning_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    purchase_order_line_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("purchase_order_line.purchase_order_line_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ordered_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    received_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    scheduled_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    cancelled_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    residual_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)


class PlanningSupplySnapshot(Base):
    """Run-frozen shipment supply or a destination allocation of PO remainder."""

    __tablename__ = "planning_supply_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "planning_run_id",
            "inbound_shipment_line_id",
            name="uq_planning_supply_run_inbound_line",
        ),
        UniqueConstraint(
            "planning_po_residual_snapshot_id",
            "destination_location_id",
            name="uq_planning_supply_po_destination",
        ),
        UniqueConstraint(
            "planning_po_residual_snapshot_id",
            "allocation_no",
            name="uq_planning_supply_po_allocation_no",
        ),
        UniqueConstraint(
            "planning_run_id",
            "allocation_idempotency_key",
            name="uq_planning_supply_idempotency",
        ),
        ForeignKeyConstraint(
            ["planning_run_id"],
            ["planning_run.planning_run_id"],
            name="fk_planning_supply_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["planning_po_residual_snapshot_id", "planning_run_id"],
            [
                "planning_po_residual_snapshot.planning_po_residual_snapshot_id",
                "planning_po_residual_snapshot.planning_run_id",
            ],
            name="fk_planning_supply_po_residual_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "destination_location_id"],
            [
                "planning_item.planning_run_id",
                "planning_item.sku_id",
                "planning_item.location_id",
            ],
            name="fk_planning_supply_item",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(source_kind = 'shipment' AND inbound_shipment_line_id IS NOT NULL "
            "AND planning_po_residual_snapshot_id IS NULL AND allocation_no = 0) OR "
            "(source_kind = 'po_allocation' AND inbound_shipment_line_id IS NULL "
            "AND planning_po_residual_snapshot_id IS NOT NULL AND allocation_no > 0)",
            name="ck_planning_supply_source_kind",
        ),
        CheckConstraint(
            "source_kind IN ('shipment', 'po_allocation')",
            name="ck_planning_supply_source",
        ),
        CheckConstraint(
            "candidate_quantity_base >= 0 AND firm_quantity_base >= 0 "
            "AND unconfirmed_quantity_base >= 0 "
            "AND capacity_exceeded_quantity_base >= 0",
            name="ck_planning_supply_quantities_nonnegative",
        ),
        CheckConstraint(
            "candidate_quantity_base = firm_quantity_base + unconfirmed_quantity_base",
            name="ck_planning_supply_quantities_reconciled",
        ),
        CheckConstraint(
            "capacity_exceeded_quantity_base <= unconfirmed_quantity_base",
            name="ck_planning_supply_capacity_excess",
        ),
        CheckConstraint(
            "supply_status IN ('scheduled', 'overdue', 'outside_horizon', "
            "'undated', 'unconfirmed')",
            name="ck_planning_supply_status",
        ),
        Index(
            "ix_planning_supply_run_week_location",
            "planning_run_id",
            "expected_week_start",
            "destination_location_id",
        ),
    )

    planning_supply_snapshot_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    planning_run_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    inbound_shipment_line_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("inbound_shipment_line.inbound_shipment_line_id", ondelete="RESTRICT"),
    )
    planning_po_residual_snapshot_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    sku_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    destination_location_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("location.location_id", ondelete="RESTRICT"),
        nullable=False,
    )
    allocation_no: Mapped[int] = mapped_column(
        UNSIGNED_SMALLINT,
        nullable=False,
        default=0,
    )
    allocation_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_quantity_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    firm_quantity_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    unconfirmed_quantity_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    capacity_exceeded_quantity_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    expected_arrival_date: Mapped[date | None] = mapped_column(Date)
    expected_week_start: Mapped[date | None] = mapped_column(Date)
    supplier_capacity_reservation_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey(
            "supplier_capacity_reservation.supplier_capacity_reservation_id",
            ondelete="RESTRICT",
        ),
    )
    supply_status: Mapped[str] = mapped_column(String(24), nullable=False)


class PABWeek(Base):
    """Calculated weekly projected available balance and shortage summary."""

    __tablename__ = "pab_week"
    __table_args__ = (
        ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id"],
            [
                "planning_item.planning_run_id",
                "planning_item.sku_id",
                "planning_item.location_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "opening_base + firm_inbound_base + firm_po_remainder_base "
            "- forecast_demand_base = ending_firm_base",
            name="ck_pab_week_equation",
        ),
        CheckConstraint(
            "shortage_quantity_base >= 0",
            name="ck_pab_week_shortage_nonnegative",
        ),
        CheckConstraint(
            "severity IN ('none', 'low', 'medium', 'high', 'critical')",
            name="ck_pab_week_severity",
        ),
        CheckConstraint(
            "forecast_demand_base >= 0 AND firm_inbound_base >= 0 "
            "AND firm_po_remainder_base >= 0 "
            "AND unconfirmed_supply_base >= 0",
            name="ck_pab_week_input_quantities_nonnegative",
        ),
        Index(
            "ix_pab_run_location_week",
            "planning_run_id",
            "location_id",
            "week_start",
            "sku_id",
        ),
    )

    planning_run_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True)
    sku_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True)
    location_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    opening_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    forecast_demand_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    firm_inbound_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    firm_po_remainder_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    unconfirmed_supply_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    ending_firm_base: Mapped[Decimal] = mapped_column(LEDGER_QUANTITY, nullable=False)
    shortage_quantity_base: Mapped[Decimal] = mapped_column(LEDGER_QUANTITY, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="none")


class PlanningRunFinding(Base):
    """A run-scoped validation finding, optionally tied to a PAB result."""

    __tablename__ = "planning_run_finding"
    __table_args__ = (
        UniqueConstraint(
            "planning_run_id",
            "source_validation_finding_id",
            name="uq_planning_run_source_finding",
        ),
        ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id"],
            [
                "planning_item.planning_run_id",
                "planning_item.sku_id",
                "planning_item.location_id",
            ],
            name="fk_planning_run_finding_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id", "week_start"],
            ["pab_week.planning_run_id", "pab_week.sku_id", "pab_week.location_id", "pab_week.week_start"],
            name="fk_planning_run_finding_pab",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(finding_origin = 'rule' AND finding_code IN "
            "('DUPLICATE_INBOUND', 'UNAPPROVED_SUBSTITUTE', 'CAPACITY_EXCEEDED', "
            "'MISSING_LEAD_TIME', 'UOM_MISMATCH', 'CAPACITY_UNCONFIRMED') "
            "AND issue_code IS NULL) OR "
            "(finding_origin = 'readiness' AND finding_code IS NULL "
            "AND issue_code IS NOT NULL)",
            name="ck_planning_run_finding_code",
        ),
        CheckConstraint(
            "finding_origin IN ('rule', 'readiness')",
            name="ck_planning_run_finding_origin",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'error', 'critical')",
            name="ck_planning_run_finding_severity",
        ),
        CheckConstraint(
            "(resolution_status IS NULL AND resolved_at IS NULL) OR "
            "(resolution_status = 'open' AND resolved_at IS NULL) OR "
            "(resolution_status IN ('resolved', 'waived') AND resolved_at IS NOT NULL)",
            name="ck_planning_run_finding_resolution",
        ),
        Index(
            "ix_planning_run_finding_code_severity",
            "planning_run_id",
            "finding_code",
            "severity",
        ),
    )

    planning_run_finding_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    planning_run_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("planning_run.planning_run_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_validation_finding_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("validation_finding.validation_finding_id", ondelete="RESTRICT"),
    )
    finding_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    finding_code: Mapped[str | None] = mapped_column(String(40))
    issue_code: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    sku_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    location_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    week_start: Mapped[date | None] = mapped_column(Date)
    resolution_status: Mapped[str | None] = mapped_column(String(20))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShortageCause(Base):
    """Explainable cause associated with a negative firm PAB result."""

    __tablename__ = "shortage_cause"
    __table_args__ = (
        ForeignKeyConstraint(
            ["planning_run_id", "sku_id", "location_id", "week_start"],
            ["pab_week.planning_run_id", "pab_week.sku_id", "pab_week.location_id", "pab_week.week_start"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "cause_code IN ('demand_exceeds_firm_supply', 'overdue_supply', "
            "'capacity_unconfirmed_supply', 'opening_inventory_deficit', "
            "'supply_outside_horizon')",
            name="ck_shortage_cause_code",
        ),
        CheckConstraint(
            "contribution_quantity_base >= 0",
            name="ck_shortage_cause_quantity_nonnegative",
        ),
        Index(
            "ix_shortage_cause_pab",
            "planning_run_id",
            "location_id",
            "week_start",
            "sku_id",
        ),
    )

    shortage_cause_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    planning_run_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    sku_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    location_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    planning_run_finding_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("planning_run_finding.planning_run_finding_id", ondelete="RESTRICT"),
    )
    cause_code: Mapped[str] = mapped_column(String(48), nullable=False)
    contribution_quantity_base: Mapped[Decimal] = mapped_column(BASE_QUANTITY, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
