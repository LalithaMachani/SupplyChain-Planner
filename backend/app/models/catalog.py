"""Catalog, location, and unit-of-measure models."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.database import Base

UNSIGNED_BIGINT = BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")
UNSIGNED_SMALLINT = SmallInteger().with_variant(mysql.SMALLINT(unsigned=True), "mysql")


class UnitOfMeasure(Base):
    """A unit and its physical dimension."""

    __tablename__ = "unit_of_measure"
    __table_args__ = (
        CheckConstraint("decimal_places BETWEEN 0 AND 6", name="ck_uom_decimal_places"),
    )

    uom_id: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)
    dimension: Mapped[str] = mapped_column(String(24), nullable=False)
    decimal_places: Mapped[int] = mapped_column(UNSIGNED_SMALLINT, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    skus: Mapped[list[SKU]] = relationship(
        back_populates="base_uom",
        foreign_keys="SKU.base_uom_id",
    )
    sku_mappings: Mapped[list[SKUUOM]] = relationship(back_populates="uom")


class SKU(Base):
    """A stocked or planned item."""

    __tablename__ = "sku"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive', 'discontinued')", name="ck_sku_status"),
        Index("ix_sku_category_status", "category", "status"),
    )

    sku_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True, autoincrement=True)
    sku_code: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    base_uom_id: Mapped[int] = mapped_column(
        UNSIGNED_SMALLINT,
        ForeignKey("unit_of_measure.uom_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    base_uom: Mapped[UnitOfMeasure] = relationship(
        back_populates="skus",
        foreign_keys=[base_uom_id],
    )
    uom_mappings: Mapped[list[SKUUOM]] = relationship(back_populates="sku")


class SKUUOM(Base):
    """A UOM conversion explicitly valid for one SKU."""

    __tablename__ = "sku_uom"
    __table_args__ = (
        CheckConstraint("to_base_factor > 0", name="ck_sku_uom_factor_positive"),
        CheckConstraint(
            "is_base_uom = 0 OR to_base_factor = 1",
            name="ck_sku_uom_base_factor",
        ),
    )

    sku_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("sku.sku_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    uom_id: Mapped[int] = mapped_column(
        UNSIGNED_SMALLINT,
        ForeignKey("unit_of_measure.uom_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    to_base_factor: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    is_base_uom: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_purchase_uom: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    sku: Mapped[SKU] = relationship(back_populates="uom_mappings")
    uom: Mapped[UnitOfMeasure] = relationship(back_populates="sku_mappings")


class Location(Base):
    """A warehouse, factory, or other planning location."""

    __tablename__ = "location"
    __table_args__ = (
        CheckConstraint(
            "location_type IN ('warehouse', 'factory', 'store', 'distribution_center', 'other')",
            name="ck_location_type",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_location_status"),
        Index("ix_location_type_status", "location_type", "status"),
    )

    location_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True, autoincrement=True)
    location_code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    location_type: Mapped[str] = mapped_column(String(32), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
