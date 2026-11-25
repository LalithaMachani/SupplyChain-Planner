"""Purchase orders, inbound shipments, supplier capacity, lead times, and events."""

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
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.database import Base
from .catalog import UNSIGNED_BIGINT, UNSIGNED_SMALLINT, Location, SKU, SKUUOM
from .identity import AppUser
from .sourcing import Supplier, SupplierSite, SupplierSKU


class PurchaseOrder(Base):
    """A supplier purchase order with an explicit lifecycle."""

    __tablename__ = "purchase_order"
    __table_args__ = (
        UniqueConstraint(
            "purchase_order_id",
            "supplier_id",
            name="uq_purchase_order_id_supplier",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "supplier_site_id"],
            ["supplier_site.supplier_id", "supplier_site.supplier_site_id"],
            name="fk_purchase_order_supplier_site",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('draft', 'submitted', 'approved', 'released', "
            "'partially_received', 'received', 'closed', 'cancelled')",
            name="ck_purchase_order_status",
        ),
        CheckConstraint(
            "requested_delivery_date IS NULL OR requested_delivery_date >= order_date",
            name="ck_purchase_order_delivery_date",
        ),
        Index("ix_purchase_order_supplier_status", "supplier_id", "status"),
        Index("ix_purchase_order_delivery_status", "requested_delivery_date", "status"),
    )

    purchase_order_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    purchase_order_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    supplier_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("supplier.supplier_id", ondelete="RESTRICT"),
        nullable=False,
    )
    supplier_site_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    created_by_user_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("app_user.user_id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    supplier: Mapped[Supplier] = relationship(foreign_keys=[supplier_id])
    supplier_site: Mapped[SupplierSite | None] = relationship(
        foreign_keys=[supplier_id, supplier_site_id],
        viewonly=True,
    )
    created_by: Mapped[AppUser | None] = relationship(foreign_keys=[created_by_user_id])
    lines: Mapped[list[PurchaseOrderLine]] = relationship(
        back_populates="purchase_order",
        foreign_keys="PurchaseOrderLine.purchase_order_id,PurchaseOrderLine.supplier_id",
        passive_deletes=True,
    )
    events: Mapped[list[ProcurementEvent]] = relationship(
        foreign_keys="ProcurementEvent.purchase_order_id",
        passive_deletes=True,
        back_populates="purchase_order",
    )


class PurchaseOrderLine(Base):
    """A SKU quantity and commercial snapshot on a purchase order."""

    __tablename__ = "purchase_order_line"
    __table_args__ = (
        UniqueConstraint(
            "purchase_order_id",
            "line_number",
            name="uq_purchase_order_line_number",
        ),
        UniqueConstraint(
            "purchase_order_line_id",
            "supplier_id",
            "sku_id",
            "order_uom_id",
            name="uq_po_line_supplier_sku_uom",
        ),
        UniqueConstraint(
            "purchase_order_line_id",
            "supplier_id",
            "sku_id",
            name="uq_po_line_supplier_sku",
        ),
        ForeignKeyConstraint(
            ["purchase_order_id", "supplier_id"],
            ["purchase_order.purchase_order_id", "purchase_order.supplier_id"],
            name="fk_po_line_order_supplier",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "sku_id"],
            ["supplier_sku.supplier_id", "supplier_sku.sku_id"],
            name="fk_po_line_supplier_sku",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["sku_id", "order_uom_id"],
            ["sku_uom.sku_id", "sku_uom.uom_id"],
            name="fk_po_line_sku_uom",
            ondelete="RESTRICT",
        ),
        CheckConstraint("line_number > 0", name="ck_po_line_number_positive"),
        CheckConstraint("quantity_ordered > 0", name="ck_po_line_quantity_positive"),
        CheckConstraint(
            "order_uom_to_base_factor > 0",
            name="ck_po_line_uom_factor_positive",
        ),
        CheckConstraint(
            "quantity_received_base >= 0",
            name="ck_po_line_received_base_nonnegative",
        ),
        CheckConstraint(
            "quantity_received_base <= quantity_ordered * order_uom_to_base_factor",
            name="ck_po_line_received_base_lte_ordered",
        ),
        CheckConstraint("unit_cost >= 0", name="ck_po_line_unit_cost_nonnegative"),
        CheckConstraint(
            "line_status IN ('open', 'partially_received', 'received', 'closed', 'cancelled')",
            name="ck_po_line_status",
        ),
        Index("ix_po_line_sku_status", "sku_id", "line_status"),
    )

    purchase_order_line_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    purchase_order_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    supplier_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    line_number: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, nullable=False)
    sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku.sku_id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_uom_id: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, nullable=False)
    quantity_ordered: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    order_uom_to_base_factor: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    quantity_received_base: Mapped[Decimal] = mapped_column(
        Numeric(28, 8),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    line_status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    purchase_order: Mapped[PurchaseOrder] = relationship(
        back_populates="lines",
        foreign_keys=[purchase_order_id, supplier_id],
    )
    sku: Mapped[SKU] = relationship(foreign_keys=[sku_id])
    supplier_offer: Mapped[SupplierSKU] = relationship(
        foreign_keys=[supplier_id, sku_id],
        viewonly=True,
    )
    order_uom: Mapped[SKUUOM] = relationship(
        foreign_keys=[sku_id, order_uom_id],
        viewonly=True,
    )
    reservations: Mapped[list[SupplierCapacityReservation]] = relationship(
        foreign_keys="SupplierCapacityReservation.purchase_order_line_id,"
        "SupplierCapacityReservation.supplier_id,"
        "SupplierCapacityReservation.sku_id,"
        "SupplierCapacityReservation.capacity_uom_id",
        viewonly=True,
    )
    inbound_lines: Mapped[list[InboundShipmentLine]] = relationship(
        foreign_keys="InboundShipmentLine.purchase_order_line_id,"
        "InboundShipmentLine.supplier_id,"
        "InboundShipmentLine.sku_id",
        viewonly=True,
    )
    events: Mapped[list[ProcurementEvent]] = relationship(
        foreign_keys="ProcurementEvent.purchase_order_line_id",
        passive_deletes=True,
        back_populates="purchase_order_line",
    )


class InboundShipment(Base):
    """An expected or in-transit supplier shipment to a location."""

    __tablename__ = "inbound_shipment"
    __table_args__ = (
        UniqueConstraint(
            "inbound_shipment_id",
            "supplier_id",
            name="uq_inbound_shipment_id_supplier",
        ),
        UniqueConstraint("supplier_id", "shipment_number", name="uq_inbound_supplier_number"),
        ForeignKeyConstraint(
            ["supplier_id", "supplier_site_id"],
            ["supplier_site.supplier_id", "supplier_site.supplier_site_id"],
            name="fk_inbound_supplier_site",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('draft', 'booked', 'in_transit', 'partially_received', "
            "'received', 'cancelled', 'exception')",
            name="ck_inbound_shipment_status",
        ),
        CheckConstraint(
            "arrived_at IS NULL OR shipped_at IS NULL OR arrived_at >= shipped_at",
            name="ck_inbound_shipment_arrival_order",
        ),
        Index("ix_inbound_shipment_supplier_status", "supplier_id", "status"),
        Index("ix_inbound_shipment_location_eta", "location_id", "expected_arrival_date"),
    )

    inbound_shipment_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    shipment_number: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("supplier.supplier_id", ondelete="RESTRICT"),
        nullable=False,
    )
    supplier_site_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    location_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("location.location_id", ondelete="RESTRICT"),
        nullable=False,
    )
    expected_arrival_date: Mapped[date | None] = mapped_column(Date)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    supplier: Mapped[Supplier] = relationship(foreign_keys=[supplier_id])
    supplier_site: Mapped[SupplierSite | None] = relationship(
        foreign_keys=[supplier_id, supplier_site_id],
        viewonly=True,
    )
    location: Mapped[Location] = relationship(foreign_keys=[location_id])
    lines: Mapped[list[InboundShipmentLine]] = relationship(
        back_populates="shipment",
        foreign_keys="InboundShipmentLine.inbound_shipment_id,"
        "InboundShipmentLine.supplier_id",
        passive_deletes=True,
    )
    events: Mapped[list[ProcurementEvent]] = relationship(
        foreign_keys="ProcurementEvent.inbound_shipment_id",
        passive_deletes=True,
        back_populates="inbound_shipment",
    )


class InboundShipmentLine(Base):
    """A partial or complete shipment quantity for a SKU and optional PO line."""

    __tablename__ = "inbound_shipment_line"
    __table_args__ = (
        UniqueConstraint(
            "inbound_shipment_id",
            "line_number",
            name="uq_inbound_shipment_line_number",
        ),
        ForeignKeyConstraint(
            ["inbound_shipment_id", "supplier_id"],
            ["inbound_shipment.inbound_shipment_id", "inbound_shipment.supplier_id"],
            name="fk_inbound_line_shipment_supplier",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "sku_id"],
            ["supplier_sku.supplier_id", "supplier_sku.sku_id"],
            name="fk_inbound_line_supplier_sku",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["sku_id", "uom_id"],
            ["sku_uom.sku_id", "sku_uom.uom_id"],
            name="fk_inbound_line_sku_uom",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["purchase_order_line_id", "supplier_id", "sku_id"],
            [
                "purchase_order_line.purchase_order_line_id",
                "purchase_order_line.supplier_id",
                "purchase_order_line.sku_id",
            ],
            name="fk_inbound_line_po_line",
            ondelete="RESTRICT",
        ),
        CheckConstraint("line_number > 0", name="ck_inbound_line_number_positive"),
        CheckConstraint("quantity_shipped > 0", name="ck_inbound_line_shipped_positive"),
        CheckConstraint("quantity_received >= 0", name="ck_inbound_line_received_nonnegative"),
        CheckConstraint(
            "quantity_received <= quantity_shipped",
            name="ck_inbound_line_received_lte_shipped",
        ),
        CheckConstraint(
            "uom_to_base_factor > 0",
            name="ck_inbound_line_uom_factor_positive",
        ),
        Index("ix_inbound_line_sku", "sku_id", "inbound_shipment_id"),
        Index("ix_inbound_line_po_line", "purchase_order_line_id"),
    )

    inbound_shipment_line_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    inbound_shipment_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    supplier_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    line_number: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, nullable=False)
    purchase_order_line_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku.sku_id", ondelete="RESTRICT"),
        nullable=False,
    )
    uom_id: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, nullable=False)
    quantity_shipped: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    quantity_received: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        default=0,
    )
    uom_to_base_factor: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    lot_number: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    shipment: Mapped[InboundShipment] = relationship(
        back_populates="lines",
        foreign_keys=[inbound_shipment_id, supplier_id],
    )
    sku: Mapped[SKU] = relationship(foreign_keys=[sku_id])
    supplier_offer: Mapped[SupplierSKU] = relationship(
        foreign_keys=[supplier_id, sku_id],
        viewonly=True,
    )
    uom: Mapped[SKUUOM] = relationship(
        foreign_keys=[sku_id, uom_id],
        viewonly=True,
    )
    purchase_order_line: Mapped[PurchaseOrderLine | None] = relationship(
        foreign_keys=[purchase_order_line_id, supplier_id, sku_id],
        viewonly=True,
    )
    events: Mapped[list[ProcurementEvent]] = relationship(
        foreign_keys="ProcurementEvent.inbound_shipment_line_id",
        passive_deletes=True,
        back_populates="inbound_shipment_line",
    )


class SupplierCapacity(Base):
    """Capacity for one supplier SKU and unit during an effective date range."""

    __tablename__ = "supplier_capacity"
    __table_args__ = (
        UniqueConstraint(
            "supplier_id",
            "sku_id",
            "capacity_uom_id",
            "effective_from",
            name="uq_supplier_capacity_period",
        ),
        UniqueConstraint(
            "supplier_capacity_id",
            "supplier_id",
            "sku_id",
            "capacity_uom_id",
            name="uq_supplier_capacity_reservation_ref",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "sku_id"],
            ["supplier_sku.supplier_id", "supplier_sku.sku_id"],
            name="fk_supplier_capacity_offer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["sku_id", "capacity_uom_id"],
            ["sku_uom.sku_id", "sku_uom.uom_id"],
            name="fk_supplier_capacity_sku_uom",
            ondelete="RESTRICT",
        ),
        CheckConstraint("capacity_quantity >= 0", name="ck_supplier_capacity_nonnegative"),
        CheckConstraint("reserved_quantity >= 0", name="ck_supplier_capacity_reserved_nonnegative"),
        CheckConstraint(
            "reserved_quantity <= capacity_quantity",
            name="ck_supplier_capacity_not_over_reserved",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_supplier_capacity_dates",
        ),
        Index(
            "ix_supplier_capacity_lookup",
            "supplier_id",
            "sku_id",
            "capacity_uom_id",
            "effective_from",
            "effective_to",
        ),
    )

    supplier_capacity_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    supplier_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("supplier.supplier_id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku.sku_id", ondelete="RESTRICT"),
        nullable=False,
    )
    capacity_uom_id: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    capacity_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    reserved_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
        default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    supplier: Mapped[Supplier] = relationship(foreign_keys=[supplier_id])
    sku: Mapped[SKU] = relationship(foreign_keys=[sku_id])
    supplier_offer: Mapped[SupplierSKU] = relationship(
        foreign_keys=[supplier_id, sku_id],
        viewonly=True,
    )
    uom: Mapped[SKUUOM] = relationship(
        foreign_keys=[sku_id, capacity_uom_id],
        viewonly=True,
    )
    reservations: Mapped[list[SupplierCapacityReservation]] = relationship(
        back_populates="capacity",
        foreign_keys="SupplierCapacityReservation.supplier_capacity_id,"
        "SupplierCapacityReservation.supplier_id,"
        "SupplierCapacityReservation.sku_id,"
        "SupplierCapacityReservation.capacity_uom_id",
        passive_deletes=True,
    )
    events: Mapped[list[ProcurementEvent]] = relationship(
        foreign_keys="ProcurementEvent.supplier_capacity_id",
        passive_deletes=True,
        back_populates="supplier_capacity",
    )


class SupplierCapacityReservation(Base):
    """A purchase-order line's capacity allocation within a capacity period."""

    __tablename__ = "supplier_capacity_reservation"
    __table_args__ = (
        UniqueConstraint(
            "supplier_capacity_id",
            "purchase_order_line_id",
            name="uq_capacity_reservation_po_line",
        ),
        ForeignKeyConstraint(
            ["supplier_capacity_id", "supplier_id", "sku_id", "capacity_uom_id"],
            [
                "supplier_capacity.supplier_capacity_id",
                "supplier_capacity.supplier_id",
                "supplier_capacity.sku_id",
                "supplier_capacity.capacity_uom_id",
            ],
            name="fk_capacity_reservation_capacity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["purchase_order_line_id", "supplier_id", "sku_id", "capacity_uom_id"],
            [
                "purchase_order_line.purchase_order_line_id",
                "purchase_order_line.supplier_id",
                "purchase_order_line.sku_id",
                "purchase_order_line.order_uom_id",
            ],
            name="fk_capacity_reservation_po_line",
            ondelete="RESTRICT",
        ),
        CheckConstraint("reservation_quantity > 0", name="ck_capacity_reservation_positive"),
        CheckConstraint(
            "status IN ('reserved', 'committed', 'released', 'cancelled')",
            name="ck_capacity_reservation_status",
        ),
        Index("ix_capacity_reservation_status", "supplier_capacity_id", "status"),
    )

    supplier_capacity_reservation_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    supplier_capacity_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    purchase_order_line_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    supplier_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    sku_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    capacity_uom_id: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, nullable=False)
    reservation_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="reserved")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    capacity: Mapped[SupplierCapacity] = relationship(
        back_populates="reservations",
        foreign_keys=[
            supplier_capacity_id,
            supplier_id,
            sku_id,
            capacity_uom_id,
        ],
    )
    purchase_order_line: Mapped[PurchaseOrderLine] = relationship(
        foreign_keys=[
            purchase_order_line_id,
            supplier_id,
            sku_id,
            capacity_uom_id,
        ],
        viewonly=True,
    )
    events: Mapped[list[ProcurementEvent]] = relationship(
        foreign_keys="ProcurementEvent.supplier_capacity_reservation_id",
        passive_deletes=True,
        back_populates="supplier_capacity_reservation",
    )


class SupplierLeadTime(Base):
    """Effective supplier-site, SKU, and destination lead-time commitment."""

    __tablename__ = "supplier_lead_time"
    __table_args__ = (
        UniqueConstraint(
            "supplier_id",
            "supplier_site_id",
            "sku_id",
            "location_id",
            "effective_from",
            name="uq_supplier_lead_time_period",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "supplier_site_id"],
            ["supplier_site.supplier_id", "supplier_site.supplier_site_id"],
            name="fk_supplier_lead_time_site",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supplier_id", "sku_id"],
            ["supplier_sku.supplier_id", "supplier_sku.sku_id"],
            name="fk_supplier_lead_time_offer",
            ondelete="RESTRICT",
        ),
        CheckConstraint("lead_time_days >= 0", name="ck_supplier_lead_time_nonnegative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_supplier_lead_time_dates",
        ),
        Index(
            "ix_supplier_lead_time_lookup",
            "supplier_id",
            "supplier_site_id",
            "sku_id",
            "location_id",
            "effective_from",
            "effective_to",
        ),
    )

    supplier_lead_time_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    supplier_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    supplier_site_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
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
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    supplier_site: Mapped[SupplierSite] = relationship(
        foreign_keys=[supplier_id, supplier_site_id],
    )
    supplier_offer: Mapped[SupplierSKU] = relationship(
        foreign_keys=[supplier_id, sku_id],
        viewonly=True,
    )
    sku: Mapped[SKU] = relationship(foreign_keys=[sku_id])
    location: Mapped[Location] = relationship(foreign_keys=[location_id])


class ProcurementEvent(Base):
    """Append-only lifecycle event referencing exactly one procurement entity."""

    __tablename__ = "procurement_event"
    __table_args__ = (
        CheckConstraint(
            "CASE WHEN purchase_order_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN purchase_order_line_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN inbound_shipment_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN inbound_shipment_line_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN supplier_capacity_id IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN supplier_capacity_reservation_id IS NULL THEN 0 ELSE 1 END = 1",
            name="ck_procurement_event_single_entity",
        ),
        ForeignKeyConstraint(
            ["purchase_order_id"],
            ["purchase_order.purchase_order_id"],
            name="fk_procurement_event_purchase_order",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["purchase_order_line_id"],
            ["purchase_order_line.purchase_order_line_id"],
            name="fk_procurement_event_purchase_order_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["inbound_shipment_id"],
            ["inbound_shipment.inbound_shipment_id"],
            name="fk_procurement_event_inbound_shipment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["inbound_shipment_line_id"],
            ["inbound_shipment_line.inbound_shipment_line_id"],
            name="fk_procurement_event_inbound_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supplier_capacity_id"],
            ["supplier_capacity.supplier_capacity_id"],
            name="fk_procurement_event_supplier_capacity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supplier_capacity_reservation_id"],
            ["supplier_capacity_reservation.supplier_capacity_reservation_id"],
            name="fk_procurement_event_capacity_reservation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "event_type IN ('created', 'submitted', 'approved', 'released', 'booked', "
            "'shipped', 'arrived', 'partially_received', 'received', 'closed', "
            "'cancelled', 'exception', 'capacity_reserved', 'capacity_committed', "
            "'capacity_released', 'status_changed')",
            name="ck_procurement_event_type",
        ),
        Index("ix_procurement_event_type_time", "event_type", "event_at"),
        Index("ix_procurement_event_po_time", "purchase_order_id", "event_at"),
        Index("ix_procurement_event_po_line_time", "purchase_order_line_id", "event_at"),
        Index("ix_procurement_event_shipment_time", "inbound_shipment_id", "event_at"),
        Index("ix_procurement_event_shipment_line_time", "inbound_shipment_line_id", "event_at"),
        Index("ix_procurement_event_capacity_time", "supplier_capacity_id", "event_at"),
        Index(
            "ix_procurement_event_reservation_time",
            "supplier_capacity_reservation_id",
            "event_at",
        ),
    )

    procurement_event_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    purchase_order_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    purchase_order_line_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    inbound_shipment_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    inbound_shipment_line_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    supplier_capacity_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    supplier_capacity_reservation_id: Mapped[int | None] = mapped_column(UNSIGNED_BIGINT)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.current_timestamp(),
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("app_user.user_id", ondelete="RESTRICT"),
    )
    source: Mapped[str | None] = mapped_column(String(80))
    details: Mapped[str | None] = mapped_column(Text)
    event_payload: Mapped[dict | None] = mapped_column(JSON)

    purchase_order: Mapped[PurchaseOrder | None] = relationship(
        foreign_keys=[purchase_order_id],
        back_populates="events",
    )
    purchase_order_line: Mapped[PurchaseOrderLine | None] = relationship(
        foreign_keys=[purchase_order_line_id],
        back_populates="events",
    )
    inbound_shipment: Mapped[InboundShipment | None] = relationship(
        foreign_keys=[inbound_shipment_id],
        back_populates="events",
    )
    inbound_shipment_line: Mapped[InboundShipmentLine | None] = relationship(
        foreign_keys=[inbound_shipment_line_id],
        back_populates="events",
    )
    supplier_capacity: Mapped[SupplierCapacity | None] = relationship(
        foreign_keys=[supplier_capacity_id],
        back_populates="events",
    )
    supplier_capacity_reservation: Mapped[SupplierCapacityReservation | None] = relationship(
        foreign_keys=[supplier_capacity_reservation_id],
        back_populates="events",
    )
    actor: Mapped[AppUser | None] = relationship(foreign_keys=[actor_user_id])
