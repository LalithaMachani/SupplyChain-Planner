"""Supplier, sourcing, and substitution models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.database import Base
from .catalog import UNSIGNED_BIGINT, UNSIGNED_SMALLINT, Location, SKU, SKUUOM
from .identity import AppUser


class Supplier(Base):
    """A supplier organization."""

    __tablename__ = "supplier"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inactive', 'blocked')",
            name="ck_supplier_status",
        ),
        Index("ix_supplier_status_name", "status", "name"),
    )

    supplier_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True, autoincrement=True)
    supplier_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    sites: Mapped[list[SupplierSite]] = relationship(back_populates="supplier")
    supplier_skus: Mapped[list[SupplierSKU]] = relationship(back_populates="supplier")
    user_links: Mapped[list[SupplierUser]] = relationship(back_populates="supplier")


class SupplierSite(Base):
    """A supplier's ship-from site."""

    __tablename__ = "supplier_site"
    __table_args__ = (
        UniqueConstraint("supplier_id", "site_code", name="uq_supplier_site_code"),
        UniqueConstraint("supplier_id", "supplier_site_id", name="uq_supplier_site_owner"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_supplier_site_status"),
        Index("ix_supplier_site_status", "supplier_id", "status"),
    )

    supplier_site_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT, primary_key=True, autoincrement=True
    )
    supplier_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("supplier.supplier_id", ondelete="RESTRICT"),
        nullable=False,
    )
    site_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    supplier: Mapped[Supplier] = relationship(back_populates="sites")


class SupplierSKU(Base):
    """A supplier's offer for a SKU, including commercial terms."""

    __tablename__ = "supplier_sku"
    __table_args__ = (
        UniqueConstraint("supplier_id", "sku_id", name="uq_supplier_sku"),
        UniqueConstraint("supplier_id", "supplier_item_code", name="uq_supplier_item_code"),
        ForeignKeyConstraint(
            ["sku_id", "order_uom_id"],
            ["sku_uom.sku_id", "sku_uom.uom_id"],
            name="fk_supplier_sku_order_uom",
            ondelete="RESTRICT",
        ),
        CheckConstraint("minimum_order_qty >= 0", name="ck_supplier_sku_moq_nonnegative"),
        CheckConstraint("order_multiple > 0", name="ck_supplier_sku_multiple_positive"),
        CheckConstraint("unit_cost >= 0", name="ck_supplier_sku_cost_nonnegative"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_supplier_sku_dates",
        ),
        CheckConstraint("status IN ('active', 'inactive', 'blocked')", name="ck_supplier_sku_status"),
        Index("ix_supplier_sku_status", "supplier_id", "status"),
    )

    supplier_sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT, primary_key=True, autoincrement=True
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
    supplier_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    order_uom_id: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, nullable=False)
    minimum_order_qty: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    order_multiple: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=1)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    supplier: Mapped[Supplier] = relationship(back_populates="supplier_skus")
    sku: Mapped[SKU] = relationship(foreign_keys=[sku_id])
    order_uom: Mapped[SKUUOM] = relationship(
        foreign_keys=[sku_id, order_uom_id],
        viewonly=True,
    )


class SKUSubstitution(Base):
    """A directed relationship indicating a possible SKU substitute."""

    __tablename__ = "sku_substitution"
    __table_args__ = (
        UniqueConstraint(
            "source_sku_id",
            "substitute_sku_id",
            name="uq_sku_substitution_pair",
        ),
        CheckConstraint(
            "source_sku_id <> substitute_sku_id",
            name="ck_sku_substitution_not_self",
        ),
        CheckConstraint("conversion_factor > 0", name="ck_sku_substitution_factor"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_sku_substitution_dates",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_sku_substitution_status"),
        Index("ix_substitution_source_status", "source_sku_id", "status"),
    )

    substitution_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT, primary_key=True, autoincrement=True
    )
    source_sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku.sku_id", ondelete="RESTRICT"),
        nullable=False,
    )
    substitute_sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku.sku_id", ondelete="RESTRICT"),
        nullable=False,
    )
    conversion_factor: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    source_sku: Mapped[SKU] = relationship(foreign_keys=[source_sku_id])
    substitute_sku: Mapped[SKU] = relationship(foreign_keys=[substitute_sku_id])
    approvals: Mapped[list[SubstitutionApproval]] = relationship(
        back_populates="substitution",
        order_by="SubstitutionApproval.revision_no",
    )


class SubstitutionApproval(Base):
    """Append-only approval decisions for a substitution relationship."""

    __tablename__ = "substitution_approval"
    __table_args__ = (
        UniqueConstraint("substitution_id", "revision_no", name="uq_substitution_approval_revision"),
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected', 'revoked')",
            name="ck_substitution_approval_decision",
        ),
        CheckConstraint(
            "decision <> 'approved' OR approved_by IS NOT NULL",
            name="ck_substitution_approval_actor",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_substitution_approval_dates",
        ),
        Index("ix_substitution_approval_decision", "substitution_id", "decision"),
    )

    approval_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT, primary_key=True, autoincrement=True
    )
    substitution_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku_substitution.substitution_id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_no: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    approved_by: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("app_user.user_id", ondelete="RESTRICT"),
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(String(500))

    substitution: Mapped[SKUSubstitution] = relationship(back_populates="approvals")
    approver: Mapped[AppUser | None] = relationship(
        foreign_keys=[approved_by],
        back_populates="substitution_approvals",
    )
