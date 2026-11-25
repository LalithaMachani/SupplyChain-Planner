"""CSV parsing and persistence services for staged ingestion."""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..models.ingestion import IngestionBatch, StagingRecord

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
MAX_RECORDS_PER_UPLOAD = 50_000
VALIDATION_VERSION = "a2.2.1"
SUPPORTED_RECORD_TYPES = frozenset(
    {
        "forecast_demand",
        "inbound_shipment",
        "inbound_shipment_line",
        "inventory",
        "purchase_order",
        "purchase_order_line",
        "sku_substitution",
        "supplier_capacity",
    }
)


class CSVInputError(ValueError):
    """A client-correctable file, CSV, or record-type error."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


class IngestionPersistenceError(RuntimeError):
    """A failed transactional write to the staging tables."""


@dataclass(frozen=True, slots=True)
class ParsedCSVRow:
    source_row_number: int
    payload: dict[str, str]
    raw_text: str
    payload_hash: str


def sanitize_file_name(file_name: str | None) -> str:
    normalized = (file_name or "").replace("\\", "/")
    safe_name = PurePosixPath(normalized).name
    if not safe_name or len(safe_name) > 255:
        raise CSVInputError("A valid CSV file name is required.", status_code=422)
    if not safe_name.lower().endswith(".csv"):
        raise CSVInputError("Only .csv files are supported.", status_code=415)
    return safe_name


def parse_csv_upload(
    file_name: str | None,
    record_type: str,
    content: bytes,
) -> tuple[str, list[ParsedCSVRow]]:
    """Parse UTF-8 CSV while retaining each source record's exact text."""
    sanitize_file_name(file_name)
    normalized_type = record_type.strip().lower()
    if normalized_type not in SUPPORTED_RECORD_TYPES:
        allowed = ", ".join(sorted(SUPPORTED_RECORD_TYPES))
        raise CSVInputError(f"Unsupported record_type. Supported types: {allowed}.")
    if not content:
        raise CSVInputError("The uploaded CSV is empty.")
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise CSVInputError("The CSV exceeds the 10 MiB upload limit.", status_code=413)

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CSVInputError("The CSV must be UTF-8 encoded.") from exc

    stream = io.StringIO(text, newline="")
    reader = csv.reader(stream, strict=True)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise CSVInputError("The CSV must contain a header and at least one data row.") from exc
    except csv.Error as exc:
        raise CSVInputError(f"Malformed CSV header: {exc}") from exc

    headers = [value.strip() for value in header]
    normalized_headers = [value.casefold() for value in headers]
    if not headers or any(not value for value in headers):
        raise CSVInputError("CSV headers must be non-empty.")
    if len(set(normalized_headers)) != len(normalized_headers):
        raise CSVInputError("CSV headers must be unique, ignoring case.")

    rows: list[ParsedCSVRow] = []
    start_offset = stream.tell()
    try:
        for row_number, values in enumerate(reader, start=1):
            end_offset = stream.tell()
            raw_text = text[start_offset:end_offset]
            start_offset = end_offset
            if len(values) != len(headers):
                raise CSVInputError(
                    f"Malformed CSV at data row {row_number}: expected "
                    f"{len(headers)} columns, found {len(values)}."
                )
            payload = dict(zip(headers, values, strict=True))
            payload_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            rows.append(
                ParsedCSVRow(
                    source_row_number=row_number,
                    payload=payload,
                    raw_text=raw_text,
                    payload_hash=payload_hash,
                )
            )
            if len(rows) > MAX_RECORDS_PER_UPLOAD:
                raise CSVInputError(
                    f"The CSV exceeds the {MAX_RECORDS_PER_UPLOAD} record limit.",
                    status_code=413,
                )
    except csv.Error as exc:
        raise CSVInputError(f"Malformed CSV: {exc}") from exc

    if not rows:
        raise CSVInputError("The CSV contains a header but no data rows.")
    return normalized_type, rows


def create_staging_batch(
    db: Session,
    *,
    source_name: str,
    file_name: str | None,
    record_type: str,
    content: bytes,
) -> tuple[IngestionBatch, int]:
    """Parse and atomically persist one upload and its raw records."""
    safe_name = sanitize_file_name(file_name)
    normalized_type, rows = parse_csv_upload(safe_name, record_type, content)
    source = source_name.strip()
    if not source or len(source) > 120:
        raise CSVInputError("source_name must contain between 1 and 120 characters.")

    now = datetime.now(timezone.utc)
    batch = IngestionBatch(
        batch_identifier=uuid4().hex,
        source_name=source,
        source_file_name=safe_name,
        file_checksum=hashlib.sha256(content).hexdigest(),
        processing_status="received",
        validation_version=VALIDATION_VERSION,
        received_at=now,
        created_at=now,
        updated_at=now,
    )
    try:
        db.add(batch)
        db.flush()
        db.add_all(
            [
                StagingRecord(
                    ingestion_batch_id=batch.ingestion_batch_id,
                    record_type=normalized_type,
                    source_row_number=row.source_row_number,
                    raw_payload_json=row.payload,
                    raw_payload_text=row.raw_text,
                    payload_hash=row.payload_hash,
                    disposition="pending",
                )
                for row in rows
            ]
        )
        db.flush()
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise IngestionPersistenceError("Unable to persist the ingestion batch.") from exc
    return batch, len(rows)
