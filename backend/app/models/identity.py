"""User, role, permission, and supplier-account association models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.database import Base

UNSIGNED_BIGINT = BigInteger().with_variant(mysql.BIGINT(unsigned=True), "mysql")


class AppUser(Base):
    """An authenticated application user; only password hashes are stored."""

    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive', 'locked')", name="ck_app_user_status"),
        Index("ix_app_user_status", "status"),
    )

    user_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    role_links: Mapped[list[UserRole]] = relationship(back_populates="user")
    supplier_links: Mapped[list[SupplierUser]] = relationship(back_populates="user")
    substitution_approvals: Mapped[list[SubstitutionApproval]] = relationship(
        back_populates="approver",
        foreign_keys="SubstitutionApproval.approved_by",
    )


class Role(Base):
    """A named application role."""

    __tablename__ = "role"

    role_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(240))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user_links: Mapped[list[UserRole]] = relationship(back_populates="role")
    permission_links: Mapped[list[RolePermission]] = relationship(back_populates="role")


class Permission(Base):
    """A capability assignable to roles."""

    __tablename__ = "permission"

    permission_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    description: Mapped[str] = mapped_column(String(240), nullable=False)

    role_links: Mapped[list[RolePermission]] = relationship(back_populates="permission")


class UserRole(Base):
    """Many-to-many user/role assignment."""

    __tablename__ = "user_role"
    __table_args__ = (
        CheckConstraint(
            "expires_at IS NULL OR expires_at > granted_at",
            name="ck_user_role_expiry",
        ),
        Index("ix_user_role_active", "role_code", "is_active"),
    )

    user_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("app_user.user_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role_code: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("role.role_code", ondelete="RESTRICT"),
        primary_key=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped[AppUser] = relationship(back_populates="role_links")
    role: Mapped[Role] = relationship(back_populates="user_links")


class RolePermission(Base):
    """Many-to-many role/permission assignment."""

    __tablename__ = "role_permission"

    role_code: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("role.role_code", ondelete="RESTRICT"),
        primary_key=True,
    )
    permission_code: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("permission.permission_code", ondelete="RESTRICT"),
        primary_key=True,
    )

    role: Mapped[Role] = relationship(back_populates="permission_links")
    permission: Mapped[Permission] = relationship(back_populates="role_links")


class SupplierUser(Base):
    """Association between a Supplier-role user and supplier account(s)."""

    __tablename__ = "supplier_user"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["app_user.user_id"],
            name="fk_supplier_user_user",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["user_id", "role_code"],
            ["user_role.user_id", "user_role.role_code"],
            name="fk_supplier_user_supplier_role",
            ondelete="RESTRICT",
        ),
        CheckConstraint("role_code = 'SUPPLIER'", name="ck_supplier_user_role"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_supplier_user_status"),
        Index("ix_supplier_user_user_status", "user_id", "status"),
    )

    supplier_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("supplier.supplier_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(UNSIGNED_BIGINT, primary_key=True)
    role_code: Mapped[str] = mapped_column(String(40), nullable=False, default="SUPPLIER")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    linked_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    supplier: Mapped[Supplier] = relationship(
        "Supplier",
        back_populates="user_links",
        foreign_keys=[supplier_id],
    )
    user: Mapped[AppUser] = relationship(
        back_populates="supplier_links",
        foreign_keys=[user_id],
    )
