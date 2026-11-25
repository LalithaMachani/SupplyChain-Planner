"""Schemas for ingestion API responses."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class BatchSummary(BaseModel):
    ingestion_batch_id: int
    batch_identifier: str
    source_name: str
    source_file_name: str
    file_checksum: str
    received_at: datetime
    processing_status: str
    validation_version: str
    record_count: int
    finding_count: int

    model_config = ConfigDict(from_attributes=True)


class BatchPage(BaseModel):
    items: list[BatchSummary]
    total: int
    limit: int
    offset: int


class UploadResult(BaseModel):
    ingestion_batch_id: int
    batch_identifier: str
    source_name: str
    source_file_name: str
    file_checksum: str
    processing_status: str
    record_count: int


class StagingRecordRead(BaseModel):
    staging_record_id: int
    ingestion_batch_id: int
    record_type: str
    source_row_number: int
    raw_payload_json: dict[str, Any]
    raw_payload_text: str
    payload_hash: str
    disposition: str
    superseded_record_id: int | None
    created_at: datetime
    finding_count: int

    model_config = ConfigDict(from_attributes=True)


class StagingRecordPage(BaseModel):
    items: list[StagingRecordRead]
    total: int
    limit: int
    offset: int


class ValidationFindingRead(BaseModel):
    validation_finding_id: int
    staging_record_id: int
    rule_code: str
    severity: str
    field_path: str | None
    details: str
    detected_at: datetime
    resolution_status: str | None
    resolved_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class FindingPage(BaseModel):
    items: list[ValidationFindingRead]
    total: int
    limit: int
    offset: int


class BatchValidationResult(BaseModel):
    ingestion_batch_id: int
    processing_status: str
    record_count: int
    accepted_count: int
    rejected_count: int
    finding_count: int
    unsupported_rules: list[str]
