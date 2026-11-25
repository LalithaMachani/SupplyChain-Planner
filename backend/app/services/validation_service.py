"""Batch validation against supported operational reference data."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from ..models.catalog import SKU, SKUUOM, UnitOfMeasure
from ..models.ingestion import IngestionBatch, StagingRecord, ValidationFinding
from ..models.sourcing import SKUSubstitution, SubstitutionApproval

INBOUND_RECORD_TYPES = frozenset(
    {
        "inbound_shipment",
        "inbound_shipment_line",
        "purchase_order",
        "purchase_order_line",
    }
)
SUBSTITUTION_RECORD_TYPES = frozenset({"sku_substitution"})
UOM_RECORD_TYPES = frozenset(
    {"forecast_demand", "inbound_shipment_line", "inventory", "purchase_order_line"}
)
CAPACITY_RECORD_TYPES = frozenset({"purchase_order_line"})
LEAD_TIME_RECORD_TYPES = frozenset({"inbound_shipment", "inbound_shipment_line", "purchase_order_line"})


class ValidationNotFoundError(LookupError):
    """The requested batch does not exist."""


class ValidationPersistenceError(RuntimeError):
    """A validation transaction could not be committed."""


@dataclass(frozen=True, slots=True)
class RuleIssue:
    rule_code: str
    severity: str
    field_path: str | None
    details: str
    unsupported: bool = False


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ingestion_batch_id: int
    processing_status: str
    record_count: int
    accepted_count: int
    rejected_count: int
    finding_count: int
    unsupported_rules: list[str]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_payload(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in payload.items():
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().casefold()).strip("_")
        result[normalized] = "" if value is None else str(value).strip()
    return result


def _first_value(fields: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = fields.get(name)
        if value:
            return value
    return None


def _reference_values(
    fields: dict[str, str],
    id_names: tuple[str, ...],
    code_names: tuple[str, ...],
) -> tuple[str | None, str | None]:
    return _first_value(fields, id_names), _first_value(fields, code_names)


def _business_identifier(
    record_type: str,
    fields: dict[str, str],
) -> tuple[str, str] | None:
    if record_type in {"inbound_shipment_line", "purchase_order_line"}:
        line_id_names = (
            ("inbound_shipment_line_id", "shipment_line_id", "line_id")
            if record_type == "inbound_shipment_line"
            else ("purchase_order_line_id", "po_line_id", "line_id")
        )
        line_id = _first_value(fields, line_id_names)
        if line_id:
            return "line_id", line_id
        parent_id_names = (
            ("inbound_shipment_id", "shipment_id", "inbound_id", "shipment_number")
            if record_type == "inbound_shipment_line"
            else ("purchase_order_id", "po_id", "po_number", "purchase_order_number")
        )
        parent_id = _first_value(fields, parent_id_names)
        line_number_names = (
            ("line_number", "shipment_line_number")
            if record_type == "inbound_shipment_line"
            else ("line_number", "po_line_number")
        )
        line_number = _first_value(fields, line_number_names)
        if parent_id and line_number:
            return "parent_line", f"{parent_id.casefold()}|{line_number.casefold()}"
        return None
    if record_type == "purchase_order":
        identifier_names = (
            "purchase_order_id",
            "po_id",
            "po_number",
            "purchase_order_number",
            "order_number",
        )
        identifier_type = "purchase_order"
    else:
        identifier_names = (
            "inbound_shipment_id",
            "shipment_id",
            "shipment_number",
            "inbound_id",
            "asn_number",
            "supplier_shipment_id",
        )
        identifier_type = "shipment"
    identifier = _first_value(fields, identifier_names)
    return (identifier_type, identifier.casefold()) if identifier else None


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    fields = _normalized_payload(payload)
    return "|".join(f"{key}={fields[key]}" for key in sorted(fields))


def _check_duplicate_inbound(
    db: Session,
    batch_id: int,
    records: list[StagingRecord],
) -> dict[int, list[RuleIssue]]:
    findings: dict[int, list[RuleIssue]] = defaultdict(list)
    all_rows = db.execute(
        select(
            StagingRecord.staging_record_id,
            StagingRecord.ingestion_batch_id,
            StagingRecord.record_type,
            StagingRecord.raw_payload_json,
            StagingRecord.payload_hash,
        )
        .join(IngestionBatch, IngestionBatch.ingestion_batch_id == StagingRecord.ingestion_batch_id)
        .where(StagingRecord.record_type.in_(INBOUND_RECORD_TYPES))
    ).all()

    existing_by_key: dict[tuple[str, str, str], list[tuple[int, str]]] = defaultdict(list)
    existing_by_payload: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in all_rows:
        if row.ingestion_batch_id != batch_id:
            fields = _normalized_payload(row.raw_payload_json)
            identifier = _business_identifier(row.record_type, fields)
            if identifier:
                existing_by_key[(row.record_type, *identifier)].append(
                    (row.staging_record_id, _payload_fingerprint(row.raw_payload_json))
                )
            existing_by_payload[(row.record_type, row.payload_hash)].append(row.staging_record_id)

    current_by_key: dict[tuple[str, str, str], list[StagingRecord]] = defaultdict(list)
    current_by_payload: dict[str, list[StagingRecord]] = defaultdict(list)
    for record in records:
        if record.record_type not in INBOUND_RECORD_TYPES:
            continue
        fields = _normalized_payload(record.raw_payload_json)
        identifier = _business_identifier(record.record_type, fields)
        if identifier:
            current_by_key[(record.record_type, *identifier)].append(record)
        current_by_payload[(record.record_type, record.payload_hash)].append(record)

    for key, matches in current_by_key.items():
        previous = existing_by_key.get(key, [])
        group = matches + []
        is_duplicate = bool(previous) or len(group) > 1
        if not is_duplicate:
            continue
        fingerprints = [_payload_fingerprint(record.raw_payload_json) for record in group]
        fingerprints.extend(fingerprint for _, fingerprint in previous)
        detail = (
            "Repeated inbound business identifier detected with identical source fields; "
            "this does not establish that inventory or inbound quantities were double-counted."
            if len(set(fingerprints)) == 1
            else "The inbound business identifier is repeated with differing source fields; "
            "quantity reconciliation is not supported."
        )
        for record in group:
            findings[record.staging_record_id].append(
                RuleIssue(
                    "DUPLICATE_INBOUND",
                    "error",
                    "business_identifier",
                    detail,
                )
            )

    for key, group in current_by_payload.items():
        if any(
            _business_identifier(
                record.record_type, _normalized_payload(record.raw_payload_json)
            )
            is not None
            for record in group
        ):
            continue
        exact_row_repeated = len(group) > 1 or bool(existing_by_payload.get(key))
        for record in group:
            reason = (
                "The raw row repeats, but equality alone cannot establish a duplicate business event."
                if exact_row_repeated
                else "A stable inbound business identifier is missing, so duplicate status cannot be established."
            )
            findings[record.staging_record_id].append(
                RuleIssue(
                    "DUPLICATE_INBOUND",
                    "warning",
                    None,
                    f"NOT_YET_SUPPORTED: {reason} Quantity reconciliation requires operational "
                    "inbound and inventory tables.",
                    unsupported=True,
                )
            )

    for record in records:
        if record.record_type in INBOUND_RECORD_TYPES and _business_identifier(
            record.record_type, _normalized_payload(record.raw_payload_json)
        ):
            findings[record.staging_record_id].append(
                RuleIssue(
                    "DUPLICATE_INBOUND",
                    "warning",
                    "business_identifier",
                    "NOT_YET_SUPPORTED: duplicate checks include staged records only; promoted "
                    "inbound/order records and quantity reconciliation cannot be checked because "
                    "operational tables are not implemented.",
                    unsupported=True,
                )
            )

    return findings


def _requested_date(fields: dict[str, str]) -> date | None:
    value = _first_value(
        fields,
        ("effective_date", "requested_date", "need_by_date", "order_date", "planning_date", "date"),
    )
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _collect_refs(
    rows: list[tuple[StagingRecord, dict[str, str]]],
    id_names: tuple[str, ...],
    code_names: tuple[str, ...],
) -> tuple[set[int], set[str]]:
    ids: set[int] = set()
    codes: set[str] = set()
    for _, fields in rows:
        raw_id, raw_code = _reference_values(fields, id_names, code_names)
        if raw_id:
            try:
                ids.add(int(raw_id))
            except ValueError:
                continue
        if raw_code:
            codes.add(raw_code.strip().upper())
    return ids, codes


def _resolve_entity(
    fields: dict[str, str],
    id_names: tuple[str, ...],
    code_names: tuple[str, ...],
    by_id: dict[int, Any],
    by_code: dict[str, Any],
) -> Any | None:
    raw_id, raw_code = _reference_values(fields, id_names, code_names)
    if raw_id:
        try:
            return by_id.get(int(raw_id))
        except ValueError:
            return None
    return by_code.get(raw_code.strip().upper()) if raw_code else None


def _unsupported_issues(record: StagingRecord) -> list[RuleIssue]:
    issues: list[RuleIssue] = []
    if record.record_type in CAPACITY_RECORD_TYPES:
        issues.append(
            RuleIssue(
                "CAPACITY_EXCEEDED",
                "warning",
                "quantity",
                "NOT_YET_SUPPORTED: supplier capacity and reservation entities are not implemented; "
                "capacity cannot be verified.",
                unsupported=True,
            )
        )
    if record.record_type in LEAD_TIME_RECORD_TYPES:
        issues.append(
            RuleIssue(
                "MISSING_LEAD_TIME",
                "warning",
                "supplier_sku",
                "NOT_YET_SUPPORTED: supplier/location lead-time entities are not implemented; "
                "no arrival date has been inferred.",
                unsupported=True,
            )
        )
    return issues


def _approval_rows(
    db: Session,
    source_sku_ids: set[int],
    substitute_sku_ids: set[int],
) -> dict[tuple[int, int], list[tuple[date, date | None, int, str, date, date | None]]]:
    approvals: dict[
        tuple[int, int], list[tuple[date, date | None, int, str, date, date | None]]
    ] = defaultdict(list)
    if not source_sku_ids or not substitute_sku_ids:
        return approvals
    rows = db.execute(
        select(
            SKUSubstitution.source_sku_id,
            SKUSubstitution.substitute_sku_id,
            SKUSubstitution.effective_from,
            SKUSubstitution.effective_to,
            SubstitutionApproval.revision_no,
            SubstitutionApproval.decision,
            SubstitutionApproval.effective_from,
            SubstitutionApproval.effective_to,
        )
        .join(
            SubstitutionApproval,
            SubstitutionApproval.substitution_id == SKUSubstitution.substitution_id,
        )
        .where(
            SKUSubstitution.source_sku_id.in_(source_sku_ids),
            SKUSubstitution.substitute_sku_id.in_(substitute_sku_ids),
            SKUSubstitution.status == "active",
        )
    ).all()
    for row in rows:
        approvals[(row.source_sku_id, row.substitute_sku_id)].append(
            (
                row.effective_from,
                row.effective_to,
                row.revision_no,
                row.decision,
                row[6],
                row[7],
            )
        )
    return approvals


def _validate_references(
    db: Session,
    records: list[StagingRecord],
    issues_by_record: dict[int, list[RuleIssue]],
) -> None:
    substitute_rows = [
        (record, _normalized_payload(record.raw_payload_json))
        for record in records
        if record.record_type in SUBSTITUTION_RECORD_TYPES
    ]
    uom_rows = [
        (record, _normalized_payload(record.raw_payload_json))
        for record in records
        if record.record_type in UOM_RECORD_TYPES
    ]

    sku_id_names = ("sku_id", "original_sku_id", "source_sku_id")
    sku_code_names = ("sku_code", "original_sku_code", "source_sku_code", "sku")
    substitute_id_names = ("substitute_sku_id", "replacement_sku_id")
    substitute_code_names = ("substitute_sku_code", "replacement_sku_code", "substitute_sku")
    all_sku_rows = substitute_rows + uom_rows
    sku_ids, sku_codes = _collect_refs(all_sku_rows, sku_id_names, sku_code_names)
    source_ids, source_codes = _collect_refs(
        substitute_rows, ("source_sku_id", "original_sku_id"), ("source_sku_code", "original_sku_code")
    )
    replacement_ids, replacement_codes = _collect_refs(
        substitute_rows, substitute_id_names, substitute_code_names
    )
    sku_ids.update(source_ids)
    sku_ids.update(replacement_ids)
    sku_codes.update(source_codes)
    sku_codes.update(replacement_codes)

    sku_by_id: dict[int, SKU] = {}
    sku_by_code: dict[str, SKU] = {}
    if sku_ids or sku_codes:
        conditions = []
        if sku_ids:
            conditions.append(SKU.sku_id.in_(sku_ids))
        if sku_codes:
            conditions.append(func.upper(SKU.sku_code).in_(sku_codes))
        for sku in db.scalars(select(SKU).where(or_(*conditions))).all():
            sku_by_id[sku.sku_id] = sku
            sku_by_code[sku.sku_code.upper()] = sku
        sku_ids.update(sku_by_id)

    uom_id_names = ("uom_id", "unit_of_measure_id")
    uom_code_names = ("uom_code", "unit_of_measure", "unit", "uom")
    uom_ids, uom_codes = _collect_refs(uom_rows, uom_id_names, uom_code_names)
    uom_by_id: dict[int, UnitOfMeasure] = {}
    uom_by_code: dict[str, UnitOfMeasure] = {}
    if uom_ids or uom_codes:
        conditions = []
        if uom_ids:
            conditions.append(UnitOfMeasure.uom_id.in_(uom_ids))
        if uom_codes:
            conditions.append(func.upper(UnitOfMeasure.code).in_(uom_codes))
        for uom in db.scalars(select(UnitOfMeasure).where(or_(*conditions))).all():
            uom_by_id[uom.uom_id] = uom
            uom_by_code[uom.code.upper()] = uom

    mappings: dict[tuple[int, int], tuple[str, str, Any, bool]] = {}
    if sku_ids:
        base_uom = aliased(UnitOfMeasure)
        for sku_id, uom_id, input_dimension, base_dimension, factor, uom_active in db.execute(
            select(
                SKU.sku_id,
                SKUUOM.uom_id,
                UnitOfMeasure.dimension,
                base_uom.dimension,
                SKUUOM.to_base_factor,
                UnitOfMeasure.is_active,
            )
            .join(SKU, SKU.sku_id == SKUUOM.sku_id)
            .join(UnitOfMeasure, UnitOfMeasure.uom_id == SKUUOM.uom_id)
            .join(base_uom, base_uom.uom_id == SKU.base_uom_id)
            .where(SKU.sku_id.in_(sku_ids))
        ):
            mappings[(sku_id, uom_id)] = (
                input_dimension,
                base_dimension,
                factor,
                uom_active,
            )

    source_sku_ids = {
        sku.sku_id
        for _, fields in substitute_rows
        if (
            sku := _resolve_entity(
                fields, ("source_sku_id", "original_sku_id"), ("source_sku_code", "original_sku_code"),
                sku_by_id, sku_by_code
            )
        )
        is not None
    }
    substitute_sku_ids = {
        sku.sku_id
        for _, fields in substitute_rows
        if (
            sku := _resolve_entity(
                fields, substitute_id_names, substitute_code_names, sku_by_id, sku_by_code
            )
        )
        is not None
    }
    approvals = _approval_rows(db, source_sku_ids, substitute_sku_ids)

    for record, fields in substitute_rows:
        source_sku = _resolve_entity(
            fields,
            ("source_sku_id", "original_sku_id"),
            ("source_sku_code", "original_sku_code"),
            sku_by_id,
            sku_by_code,
        )
        substitute_sku = _resolve_entity(
            fields, substitute_id_names, substitute_code_names, sku_by_id, sku_by_code
        )
        effective_date = _requested_date(fields)
        if source_sku is None or substitute_sku is None:
            issues_by_record[record.staging_record_id].append(
                RuleIssue(
                    "UNAPPROVED_SUBSTITUTE",
                    "error",
                    "substitute_sku",
                    "The original and substitute SKU identifiers must resolve to known SKUs.",
                )
            )
            continue
        if effective_date is None:
            issues_by_record[record.staging_record_id].append(
                RuleIssue(
                    "UNAPPROVED_SUBSTITUTE",
                    "error",
                    "effective_date",
                    "A valid effective date is required to verify substitution approval.",
                )
            )
            continue
        applicable_revisions = [
            approval
            for approval in approvals.get((source_sku.sku_id, substitute_sku.sku_id), [])
            if approval[0] <= effective_date
            and (approval[1] is None or effective_date <= approval[1])
            and approval[4] <= effective_date
            and (approval[5] is None or effective_date <= approval[5])
        ]
        latest_applicable = max(applicable_revisions, key=lambda approval: approval[2], default=None)
        eligible = latest_applicable is not None and latest_applicable[3] == "approved"
        if not eligible:
            issues_by_record[record.staging_record_id].append(
                RuleIssue(
                    "UNAPPROVED_SUBSTITUTE",
                    "error",
                    "substitute_sku",
                    "No active, approved substitution covers the requested effective date.",
                )
            )

    for record, fields in uom_rows:
        sku = _resolve_entity(fields, ("sku_id",), ("sku_code", "sku"), sku_by_id, sku_by_code)
        uom = _resolve_entity(fields, uom_id_names, uom_code_names, uom_by_id, uom_by_code)
        if sku is None or uom is None:
            issues_by_record[record.staging_record_id].append(
                RuleIssue(
                    "UOM_MISMATCH",
                    "error",
                    "sku_id" if sku is None else "uom",
                    "A known SKU and unit of measure are required; no conversion was inferred.",
                )
            )
            continue
        mapping = mappings.get((sku.sku_id, uom.uom_id))
        if (
            mapping is None
            or not uom.is_active
            or mapping[0] != mapping[1]
            or mapping[2] <= 0
        ):
            issues_by_record[record.staging_record_id].append(
                RuleIssue(
                    "UOM_MISMATCH",
                    "error",
                    "uom",
                    "The unit is inactive, dimensionally incompatible, or has no valid SKU conversion mapping.",
                )
            )


def validate_batch(db: Session, batch_id: int) -> ValidationResult:
    """Validate all pending records in a batch and persist findings atomically."""
    batch = db.get(IngestionBatch, batch_id)
    if batch is None:
        raise ValidationNotFoundError(f"Ingestion batch {batch_id} was not found.")

    records = db.scalars(
        select(StagingRecord)
        .where(StagingRecord.ingestion_batch_id == batch_id)
        .order_by(StagingRecord.source_row_number)
    ).all()
    if not records:
        raise ValidationNotFoundError(f"Ingestion batch {batch_id} has no staged records.")

    pending = [record for record in records if record.disposition == "pending"]
    if not pending:
        finding_rows = db.execute(
            select(ValidationFinding.rule_code, ValidationFinding.details)
            .join(
                StagingRecord,
                StagingRecord.staging_record_id == ValidationFinding.staging_record_id,
            )
            .where(StagingRecord.ingestion_batch_id == batch_id)
        ).all()
        accepted = sum(record.disposition in {"accepted", "promoted"} for record in records)
        rejected = sum(record.disposition == "rejected" for record in records)
        return ValidationResult(
            batch_id,
            batch.processing_status,
            len(records),
            accepted,
            rejected,
            len(finding_rows),
            sorted(
                {
                    rule_code
                    for rule_code, details in finding_rows
                    if "NOT_YET_SUPPORTED:" in details
                }
            ),
        )

    issues_by_record: dict[int, list[RuleIssue]] = defaultdict(list)
    try:
        batch.processing_status = "validating"
        issues_by_record.update(_check_duplicate_inbound(db, batch_id, pending))
        for record in pending:
            issues_by_record[record.staging_record_id].extend(_unsupported_issues(record))
        _validate_references(db, pending, issues_by_record)

        now = _utc_now()
        finding_count = 0
        for record in pending:
            issues = issues_by_record[record.staging_record_id]
            record.disposition = (
                "rejected"
                if any(issue.severity in {"error", "critical"} for issue in issues)
                else "accepted"
            )
            for issue in issues:
                db.add(
                    ValidationFinding(
                        staging_record_id=record.staging_record_id,
                        rule_code=issue.rule_code,
                        severity=issue.severity,
                        field_path=issue.field_path,
                        details=issue.details,
                        detected_at=now,
                        resolution_status="open",
                    )
                )
                finding_count += 1

        accepted_count = sum(record.disposition == "accepted" for record in records)
        rejected_count = sum(record.disposition == "rejected" for record in records)
        batch.processing_status = (
            "validated"
            if rejected_count == 0
            else "partially_rejected"
            if accepted_count > 0
            else "rejected"
        )
        batch.updated_at = now
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise ValidationPersistenceError("Unable to persist validation results.") from exc

    return ValidationResult(
        batch_id,
        batch.processing_status,
        len(records),
        accepted_count,
        rejected_count,
        finding_count,
        sorted(
            {
                issue.rule_code
                for issues in issues_by_record.values()
                for issue in issues
                if issue.unsupported
            }
        ),
    )
