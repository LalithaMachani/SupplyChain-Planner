"""Batch ingestion, immutable staging records, and validation findings."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.database import Base
from .catalog import UNSIGNED_BIGINT
from .identity import AppUser


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IngestionBatch(Base):
    """Metadata and lifecycle for one imported source file."""

    __tablename__ = "ingestion_batch"
    __table_args__ = (
        CheckConstraint(
            "processing_status IN "
            "('received', 'validating', 'validated', 'partially_rejected', "
            "'rejected', 'promoted', 'failed')",
            name="ck_ingestion_batch_status",
        ),
        Index("ix_ingestion_batch_status_received", "processing_status", "received_at"),
        Index("ix_ingestion_batch_file_checksum", "file_checksum"),
    )

    ingestion_batch_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    batch_identifier: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_by_user_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("app_user.user_id", ondelete="SET NULL"),
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )
    processing_status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="received",
    )
    validation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        onupdate=_utc_now,
    )

    submitted_by: Mapped[AppUser | None] = relationship(
        foreign_keys=[submitted_by_user_id],
    )
    records: Mapped[list[StagingRecord]] = relationship(
        back_populates="batch",
        passive_deletes=True,
    )


class StagingRecord(Base):
    """An immutable raw source record awaiting validation or promotion."""

    __tablename__ = "staging_record"
    __table_args__ = (
        UniqueConstraint(
            "ingestion_batch_id",
            "source_row_number",
            name="uq_staging_record_batch_row",
        ),
        CheckConstraint("source_row_number > 0", name="ck_staging_record_row_positive"),
        CheckConstraint(
            "disposition IN ('pending', 'accepted', 'rejected', 'promoted', 'superseded')",
            name="ck_staging_record_disposition",
        ),
        Index("ix_staging_record_type", "record_type"),
        Index("ix_staging_record_disposition", "disposition"),
        Index("ix_staging_record_payload_hash", "payload_hash"),
    )

    staging_record_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    ingestion_batch_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("ingestion_batch.ingestion_batch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    record_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_row_number: Mapped[int] = mapped_column(UNSIGNED_BIGINT, nullable=False)
    raw_payload_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    raw_payload_text: Mapped[str] = mapped_column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"),
        nullable=False,
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    superseded_record_id: Mapped[int | None] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("staging_record.staging_record_id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )

    batch: Mapped[IngestionBatch] = relationship(back_populates="records")
    supersedes: Mapped[StagingRecord | None] = relationship(
        "StagingRecord",
        foreign_keys=[superseded_record_id],
        remote_side=[staging_record_id],
        back_populates="superseded_by_records",
    )
    superseded_by_records: Mapped[list[StagingRecord]] = relationship(
        "StagingRecord",
        foreign_keys=[superseded_record_id],
        back_populates="supersedes",
        passive_deletes=True,
    )
    findings: Mapped[list[ValidationFinding]] = relationship(
        back_populates="staging_record",
        passive_deletes=True,
    )


class ValidationFinding(Base):
    """A validation rule result attached to a staged record."""

    __tablename__ = "validation_finding"
    __table_args__ = (
        CheckConstraint(
            "rule_code IN ('DUPLICATE_INBOUND', 'UNAPPROVED_SUBSTITUTE', "
            "'CAPACITY_EXCEEDED', 'MISSING_LEAD_TIME', 'UOM_MISMATCH')",
            name="ck_validation_finding_rule_code",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'error', 'critical')",
            name="ck_validation_finding_severity",
        ),
        CheckConstraint(
            "(resolution_status IS NULL AND resolved_at IS NULL) OR "
            "(resolution_status = 'open' AND resolved_at IS NULL) OR "
            "(resolution_status IN ('resolved', 'waived') AND resolved_at IS NOT NULL)",
            name="ck_validation_finding_resolution",
        ),
        Index(
            "ix_validation_finding_record_rule",
            "staging_record_id",
            "rule_code",
            "detected_at",
        ),
        Index(
            "ix_validation_finding_status_severity",
            "resolution_status",
            "severity",
            "detected_at",
        ),
    )

    validation_finding_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        primary_key=True,
        autoincrement=True,
    )
    staging_record_id: Mapped[int] = mapped_column(
        UNSIGNED_BIGINT,
        ForeignKey("staging_record.staging_record_id", ondelete="RESTRICT"),
        nullable=False,
    )
    rule_code: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    field_path: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
    )
    resolution_status: Mapped[str | None] = mapped_column(String(20))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    staging_record: Mapped[StagingRecord] = relationship(back_populates="findings")
