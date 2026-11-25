"""API and validation tests for Phase A2.2."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.db.database import get_db
from app.main import app
from app.models import (
    AppUser,
    IngestionBatch,
    SKU,
    SKUSubstitution,
    StagingRecord,
    SubstitutionApproval,
)


def _upload(
    client: TestClient,
    content: bytes,
    record_type: str = "inventory",
    source_name: str = "pytest",
):
    return client.post(
        "/api/v1/ingestion/upload",
        data={"record_type": record_type, "source_name": source_name},
        files={"file": ("source.csv", content, "text/csv")},
    )


def test_existing_health_endpoints_remain_available(client):
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "healthy"}
    assert client.get("/api/v1/health/database").json() == {"status": "connected"}


def test_valid_csv_upload_preserves_raw_record(client):
    response = _upload(
        client,
        b"sku_code,quantity,uom_code\r\nSKU-000001,12,EA\r\n",
    )

    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["record_count"] == 1
    batch = client.get(
        f"/api/v1/ingestion/batches/{uploaded['ingestion_batch_id']}"
    )
    assert batch.status_code == 200
    assert batch.json()["record_count"] == 1
    record_response = client.get(
        f"/api/v1/ingestion/batches/{uploaded['ingestion_batch_id']}/records"
    )
    record = record_response.json()["items"][0]
    validation = client.post(
        f"/api/v1/ingestion/batches/{uploaded['ingestion_batch_id']}/validate"
    )
    assert record["raw_payload_json"] == {
        "sku_code": "SKU-000001",
        "quantity": "12",
        "uom_code": "EA",
    }
    assert record["raw_payload_text"] == "SKU-000001,12,EA\r\n"
    assert validation.status_code == 200
    assert validation.json()["accepted_count"] == 1


def test_empty_and_malformed_csv_are_rejected(client):
    empty_response = _upload(client, b"")
    malformed_response = _upload(client, b'id,quantity\n"unterminated,2\n')

    assert empty_response.status_code == 422
    assert malformed_response.status_code == 422
    assert "empty" in empty_response.json()["detail"].lower()
    assert "malformed" in malformed_response.json()["detail"].lower()


def test_upload_rejects_non_csv_and_oversized_files(client):
    wrong_extension = client.post(
        "/api/v1/ingestion/upload",
        data={"record_type": "inventory", "source_name": "pytest"},
        files={"file": ("source.txt", b"sku_code\nSKU-000001\n", "text/plain")},
    )
    oversized = client.post(
        "/api/v1/ingestion/upload",
        data={"record_type": "inventory", "source_name": "pytest"},
        files={"file": ("large.csv", b"x" * (10 * 1024 * 1024 + 1), "text/csv")},
    )

    assert wrong_extension.status_code == 415
    assert oversized.status_code == 413


def test_duplicate_inbound_detection_persists_findings(client):
    response = _upload(
        client,
        b"shipment_id,supplier_code,sku_code,quantity\n"
        b"ASN-1,SUP-1,SKU-000001,10\n"
        b"ASN-1,SUP-1,SKU-000001,10\n",
        record_type="inbound_shipment",
    )
    batch_id = response.json()["ingestion_batch_id"]

    validation = client.post(f"/api/v1/ingestion/batches/{batch_id}/validate")
    records = client.get(f"/api/v1/ingestion/batches/{batch_id}/records").json()["items"]

    assert validation.status_code == 200
    assert validation.json()["rejected_count"] == 2
    assert all(record["disposition"] == "rejected" for record in records)
    findings = client.get(
        f"/api/v1/ingestion/records/{records[0]['staging_record_id']}/findings"
    ).json()["items"]
    assert any(item["rule_code"] == "DUPLICATE_INBOUND" for item in findings)


def test_duplicate_inbound_identifier_across_sources_is_detected(client):
    first_upload = _upload(
        client,
        b"shipment_id,supplier_code,sku_code,quantity\nASN-CROSS-1,SUP-1,SKU-000001,10\n",
        record_type="inbound_shipment",
        source_name="supplier-feed",
    )
    second_upload = _upload(
        client,
        b"shipment_id,supplier_code,sku_code,quantity\nASN-CROSS-1,SUP-1,SKU-000001,10\n",
        record_type="inbound_shipment",
        source_name="warehouse-feed",
    )
    assert first_upload.status_code == 201
    second_batch_id = second_upload.json()["ingestion_batch_id"]

    validation = client.post(f"/api/v1/ingestion/batches/{second_batch_id}/validate")
    record = client.get(
        f"/api/v1/ingestion/batches/{second_batch_id}/records"
    ).json()["items"][0]
    findings = client.get(
        f"/api/v1/ingestion/records/{record['staging_record_id']}/findings"
    ).json()["items"]

    duplicate = next(item for item in findings if item["rule_code"] == "DUPLICATE_INBOUND")
    assert validation.json()["rejected_count"] == 1
    assert duplicate["severity"] == "error"
    assert "does not establish" in duplicate["details"]


def test_unsupported_purchase_order_checks_are_warnings_not_rejections(client):
    response = _upload(
        client,
        b"po_number,line_number,sku_code,uom_code,quantity\nPO-WARN-1,1,SKU-000001,EA,10\n",
        record_type="purchase_order_line",
    )
    batch_id = response.json()["ingestion_batch_id"]

    validation = client.post(f"/api/v1/ingestion/batches/{batch_id}/validate")
    record = client.get(f"/api/v1/ingestion/batches/{batch_id}/records").json()["items"][0]
    findings = client.get(
        f"/api/v1/ingestion/records/{record['staging_record_id']}/findings"
    ).json()["items"]
    severities = {
        finding["rule_code"]: finding["severity"]
        for finding in findings
        if finding["rule_code"] in {"CAPACITY_EXCEEDED", "MISSING_LEAD_TIME"}
    }

    assert validation.json()["accepted_count"] == 1
    assert validation.json()["rejected_count"] == 0
    assert severities == {"CAPACITY_EXCEEDED": "warning", "MISSING_LEAD_TIME": "warning"}
    assert all(
        "NOT_YET_SUPPORTED" in finding["details"]
        for finding in findings
        if finding["rule_code"] in {"CAPACITY_EXCEEDED", "MISSING_LEAD_TIME"}
    )


def test_inbound_operational_duplicate_limitation_is_not_a_rejection(client):
    response = _upload(
        client,
        b"shipment_id,supplier_code,sku_code,quantity\nASN-UNSUPPORTED-1,SUP-1,SKU-000001,10\n",
        record_type="inbound_shipment",
    )
    batch_id = response.json()["ingestion_batch_id"]

    validation = client.post(f"/api/v1/ingestion/batches/{batch_id}/validate")
    record = client.get(f"/api/v1/ingestion/batches/{batch_id}/records").json()["items"][0]
    findings = client.get(
        f"/api/v1/ingestion/records/{record['staging_record_id']}/findings"
    ).json()["items"]
    unsupported_duplicates = [
        finding
        for finding in findings
        if finding["rule_code"] == "DUPLICATE_INBOUND"
        and "NOT_YET_SUPPORTED" in finding["details"]
    ]

    assert validation.json()["accepted_count"] == 1
    assert validation.json()["rejected_count"] == 0
    assert unsupported_duplicates
    assert all(finding["severity"] == "warning" for finding in unsupported_duplicates)


def test_missing_inbound_identifier_is_unsupported_warning(client):
    response = _upload(
        client,
        b"supplier_code,sku_code,quantity\nSUP-1,SKU-000001,10\n",
        record_type="inbound_shipment",
    )
    batch_id = response.json()["ingestion_batch_id"]

    validation = client.post(f"/api/v1/ingestion/batches/{batch_id}/validate")
    record = client.get(f"/api/v1/ingestion/batches/{batch_id}/records").json()["items"][0]
    findings = client.get(
        f"/api/v1/ingestion/records/{record['staging_record_id']}/findings"
    ).json()["items"]
    duplicate = next(
        finding for finding in findings if finding["rule_code"] == "DUPLICATE_INBOUND"
    )

    assert validation.json()["accepted_count"] == 1
    assert validation.json()["rejected_count"] == 0
    assert duplicate["severity"] == "warning"
    assert "NOT_YET_SUPPORTED" in duplicate["details"]


def test_missing_lead_time_is_explicitly_unsupported(client):
    response = _upload(
        client,
        b"po_number,supplier_code,sku_code,location_code,order_date,quantity\n"
        b"PO-1,SUP-1,SKU-000001,LOC-1,2026-01-01,10\n",
        record_type="purchase_order_line",
    )
    batch_id = response.json()["ingestion_batch_id"]

    validation = client.post(f"/api/v1/ingestion/batches/{batch_id}/validate").json()
    record = client.get(f"/api/v1/ingestion/batches/{batch_id}/records").json()["items"][0]
    findings = client.get(
        f"/api/v1/ingestion/records/{record['staging_record_id']}/findings"
    ).json()["items"]

    assert "MISSING_LEAD_TIME" in validation["unsupported_rules"]
    finding = next(item for item in findings if item["rule_code"] == "MISSING_LEAD_TIME")
    assert "NOT_YET_SUPPORTED" in finding["details"]


def test_uom_mismatch_detects_missing_sku_mapping(client):
    response = _upload(
        client,
        b"sku_code,quantity,uom_code\nSKU-000001,12,KG\n",
    )
    batch_id = response.json()["ingestion_batch_id"]

    client.post(f"/api/v1/ingestion/batches/{batch_id}/validate")
    record = client.get(f"/api/v1/ingestion/batches/{batch_id}/records").json()["items"][0]
    findings = client.get(
        f"/api/v1/ingestion/records/{record['staging_record_id']}/findings"
    ).json()["items"]

    assert any(item["rule_code"] == "UOM_MISMATCH" for item in findings)


def test_unapproved_substitute_is_rejected(client, session_factory):
    with session_factory() as session:
        session.add(
            SKU(
                sku_id=1002,
                sku_code="SKU-000002",
                name="Substitute item",
                base_uom_id=1,
                status="active",
            )
        )
        session.add(
            SKUSubstitution(
                substitution_id=5,
                source_sku_id=1001,
                substitute_sku_id=1002,
                conversion_factor=1,
                effective_from=date(2025, 1, 1),
                status="active",
            )
        )
        session.commit()
    response = _upload(
        client,
        b"source_sku_code,substitute_sku_code,effective_date\n"
        b"SKU-000001,SKU-000002,2026-01-01\n",
        record_type="sku_substitution",
    )
    batch_id = response.json()["ingestion_batch_id"]

    client.post(f"/api/v1/ingestion/batches/{batch_id}/validate")
    record = client.get(f"/api/v1/ingestion/batches/{batch_id}/records").json()["items"][0]
    findings = client.get(
        f"/api/v1/ingestion/records/{record['staging_record_id']}/findings"
    ).json()["items"]

    assert any(item["rule_code"] == "UNAPPROVED_SUBSTITUTE" for item in findings)


def test_effective_approved_substitute_is_accepted(client, session_factory):
    with session_factory() as session:
        session.add_all(
            [
                AppUser(
                    user_id=1,
                    email="approver@example.test",
                    display_name="Test approver",
                    password_hash="test-hash-only",
                    status="active",
                ),
                SKU(
                    sku_id=1002,
                    sku_code="SKU-000002",
                    name="Approved substitute",
                    base_uom_id=1,
                    status="active",
                ),
            ]
        )
        session.flush()
        session.add(
            SKUSubstitution(
                substitution_id=6,
                source_sku_id=1001,
                substitute_sku_id=1002,
                conversion_factor=1,
                effective_from=date(2025, 1, 1),
                status="active",
            )
        )
        session.flush()
        session.add(
            SubstitutionApproval(
                substitution_id=6,
                revision_no=1,
                decision="approved",
                approved_by=1,
                effective_from=date(2025, 1, 1),
            )
        )
        session.commit()

    response = _upload(
        client,
        b"source_sku_code,substitute_sku_code,effective_date\n"
        b"SKU-000001,SKU-000002,2026-01-01\n",
        record_type="sku_substitution",
    )
    batch_id = response.json()["ingestion_batch_id"]

    validation = client.post(f"/api/v1/ingestion/batches/{batch_id}/validate")
    record = client.get(f"/api/v1/ingestion/batches/{batch_id}/records").json()["items"][0]

    assert validation.json()["accepted_count"] == 1
    assert record["disposition"] == "accepted"


def test_one_record_can_have_multiple_findings(client):
    response = _upload(
        client,
        b"po_number,line_number,sku_code,uom_code,quantity,supplier_code,location_code\n"
        b"PO-1,1,SKU-000001,KG,10,SUP-1,LOC-1\n",
        record_type="purchase_order_line",
    )
    batch_id = response.json()["ingestion_batch_id"]

    validation = client.post(f"/api/v1/ingestion/batches/{batch_id}/validate").json()
    record = client.get(f"/api/v1/ingestion/batches/{batch_id}/records").json()["items"][0]
    findings = client.get(
        f"/api/v1/ingestion/records/{record['staging_record_id']}/findings"
    ).json()["items"]

    rules = {finding["rule_code"] for finding in findings}
    assert validation["finding_count"] >= 3
    assert {"CAPACITY_EXCEEDED", "MISSING_LEAD_TIME", "UOM_MISMATCH"} <= rules


def test_batch_pagination(client):
    batch_ids = []
    for index in range(3):
        response = _upload(
            client,
            f"sku_code,quantity,uom_code\nSKU-00000{index + 1},1,EA\n".encode(),
        )
        batch_ids.append(response.json()["ingestion_batch_id"])

    page = client.get("/api/v1/ingestion/batches?limit=2&offset=1").json()

    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["limit"] == 2
    assert page["offset"] == 1


def test_database_rollback_on_upload_failure(session_factory, db_engine):
    class FailingCommitSession(Session):
        def commit(self):
            self.flush()
            raise SQLAlchemyError("injected commit failure")

    def override_failing_db():
        with FailingCommitSession(bind=db_engine, autoflush=False) as session:
            yield session

    app.dependency_overrides[get_db] = override_failing_db
    try:
        with TestClient(app, raise_server_exceptions=False) as failing_client:
            response = _upload(
                failing_client,
                b"sku_code,quantity,uom_code\nSKU-000001,1,EA\n",
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    with session_factory() as session:
        batch_count = session.scalar(select(func.count()).select_from(IngestionBatch))
        record_count = session.scalar(select(func.count()).select_from(StagingRecord))

    assert response.status_code == 500
    assert batch_count == 0
    assert record_count == 0
