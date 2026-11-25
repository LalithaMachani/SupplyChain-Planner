"""HTTP endpoints for CSV ingestion and staged-record validation."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ....db.database import get_db
from ....models.ingestion import IngestionBatch, StagingRecord, ValidationFinding
from ....schemas.ingestion import (
    BatchPage,
    BatchSummary,
    BatchValidationResult,
    FindingPage,
    StagingRecordPage,
    UploadResult,
)
from ....services.ingestion_service import (
    MAX_UPLOAD_SIZE_BYTES,
    CSVInputError,
    IngestionPersistenceError,
    create_staging_batch,
)
from ....services.validation_service import (
    ValidationNotFoundError,
    ValidationPersistenceError,
    validate_batch,
)

router = APIRouter()


def _batch_summary(
    batch: IngestionBatch,
    record_count: int,
    finding_count: int,
) -> BatchSummary:
    return BatchSummary(
        ingestion_batch_id=batch.ingestion_batch_id,
        batch_identifier=batch.batch_identifier,
        source_name=batch.source_name,
        source_file_name=batch.source_file_name,
        file_checksum=batch.file_checksum,
        received_at=batch.received_at,
        processing_status=batch.processing_status,
        validation_version=batch.validation_version,
        record_count=record_count,
        finding_count=finding_count,
    )


@router.post("/upload", response_model=UploadResult, status_code=201)
def upload_csv(
    file: Annotated[UploadFile, File(...)],
    record_type: Annotated[str, Form(min_length=1, max_length=64)],
    source_name: Annotated[str, Form(min_length=1, max_length=120)],
    db: Session = Depends(get_db),
) -> UploadResult:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Only .csv files are supported.")
    content = file.file.read(MAX_UPLOAD_SIZE_BYTES + 1)
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="The CSV exceeds the 10 MiB upload limit.")
    try:
        batch, record_count = create_staging_batch(
            db,
            source_name=source_name,
            file_name=file.filename,
            record_type=record_type,
            content=content,
        )
    except CSVInputError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except IngestionPersistenceError as exc:
        raise HTTPException(status_code=500, detail="Unable to persist the ingestion batch.") from exc
    return UploadResult(
        ingestion_batch_id=batch.ingestion_batch_id,
        batch_identifier=batch.batch_identifier,
        source_name=batch.source_name,
        source_file_name=batch.source_file_name,
        file_checksum=batch.file_checksum,
        processing_status=batch.processing_status,
        record_count=record_count,
    )


@router.get("/batches", response_model=BatchPage)
def list_batches(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> BatchPage:
    record_counts = (
        select(
            StagingRecord.ingestion_batch_id.label("batch_id"),
            func.count(StagingRecord.staging_record_id).label("record_count"),
        )
        .group_by(StagingRecord.ingestion_batch_id)
        .subquery()
    )
    finding_counts = (
        select(
            StagingRecord.ingestion_batch_id.label("batch_id"),
            func.count(ValidationFinding.validation_finding_id).label("finding_count"),
        )
        .join(
            ValidationFinding,
            ValidationFinding.staging_record_id == StagingRecord.staging_record_id,
        )
        .group_by(StagingRecord.ingestion_batch_id)
        .subquery()
    )
    rows = db.execute(
        select(
            IngestionBatch,
            func.coalesce(record_counts.c.record_count, 0),
            func.coalesce(finding_counts.c.finding_count, 0),
        )
        .outerjoin(
            record_counts,
            record_counts.c.batch_id == IngestionBatch.ingestion_batch_id,
        )
        .outerjoin(
            finding_counts,
            finding_counts.c.batch_id == IngestionBatch.ingestion_batch_id,
        )
        .order_by(IngestionBatch.received_at.desc(), IngestionBatch.ingestion_batch_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = db.scalar(select(func.count()).select_from(IngestionBatch)) or 0
    return BatchPage(
        items=[
            _batch_summary(batch, record_count, finding_count)
            for batch, record_count, finding_count in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/batches/{batch_id}", response_model=BatchSummary)
def get_batch(
    batch_id: Annotated[int, Path(gt=0)],
    db: Session = Depends(get_db),
) -> BatchSummary:
    batch = db.get(IngestionBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Ingestion batch not found.")
    record_count = db.scalar(
        select(func.count()).select_from(StagingRecord).where(
            StagingRecord.ingestion_batch_id == batch_id
        )
    ) or 0
    finding_count = db.scalar(
        select(func.count())
        .select_from(ValidationFinding)
        .join(
            StagingRecord,
            StagingRecord.staging_record_id == ValidationFinding.staging_record_id,
        )
        .where(StagingRecord.ingestion_batch_id == batch_id)
    ) or 0
    return _batch_summary(batch, record_count, finding_count)


@router.get("/batches/{batch_id}/records", response_model=StagingRecordPage)
def list_batch_records(
    batch_id: Annotated[int, Path(gt=0)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> StagingRecordPage:
    if db.get(IngestionBatch, batch_id) is None:
        raise HTTPException(status_code=404, detail="Ingestion batch not found.")
    rows = db.execute(
        select(
            StagingRecord,
            func.count(ValidationFinding.validation_finding_id).label("finding_count"),
        )
        .outerjoin(
            ValidationFinding,
            ValidationFinding.staging_record_id == StagingRecord.staging_record_id,
        )
        .where(StagingRecord.ingestion_batch_id == batch_id)
        .group_by(StagingRecord.staging_record_id)
        .order_by(StagingRecord.source_row_number)
        .limit(limit)
        .offset(offset)
    ).all()
    total = db.scalar(
        select(func.count()).select_from(StagingRecord).where(
            StagingRecord.ingestion_batch_id == batch_id
        )
    ) or 0
    return StagingRecordPage(
        items=[
            {
                "staging_record_id": record.staging_record_id,
                "ingestion_batch_id": record.ingestion_batch_id,
                "record_type": record.record_type,
                "source_row_number": record.source_row_number,
                "raw_payload_json": record.raw_payload_json,
                "raw_payload_text": record.raw_payload_text,
                "payload_hash": record.payload_hash,
                "disposition": record.disposition,
                "superseded_record_id": record.superseded_record_id,
                "created_at": record.created_at,
                "finding_count": finding_count,
            }
            for record, finding_count in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/records/{record_id}/findings", response_model=FindingPage)
def list_record_findings(
    record_id: Annotated[int, Path(gt=0)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> FindingPage:
    if db.get(StagingRecord, record_id) is None:
        raise HTTPException(status_code=404, detail="Staging record not found.")
    findings = db.scalars(
        select(ValidationFinding)
        .where(ValidationFinding.staging_record_id == record_id)
        .order_by(ValidationFinding.detected_at, ValidationFinding.validation_finding_id)
        .limit(limit)
        .offset(offset)
    ).all()
    total = db.scalar(
        select(func.count()).select_from(ValidationFinding).where(
            ValidationFinding.staging_record_id == record_id
        )
    ) or 0
    return FindingPage(items=findings, total=total, limit=limit, offset=offset)


@router.post("/batches/{batch_id}/validate", response_model=BatchValidationResult)
def validate_ingestion_batch(
    batch_id: Annotated[int, Path(gt=0)],
    db: Session = Depends(get_db),
) -> BatchValidationResult:
    try:
        result = validate_batch(db, batch_id)
    except ValidationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationPersistenceError as exc:
        raise HTTPException(status_code=500, detail="Unable to persist validation results.") from exc
    return BatchValidationResult(
        ingestion_batch_id=result.ingestion_batch_id,
        processing_status=result.processing_status,
        record_count=result.record_count,
        accepted_count=result.accepted_count,
        rejected_count=result.rejected_count,
        finding_count=result.finding_count,
        unsupported_rules=result.unsupported_rules,
    )
