"""Safely import the checked-in demo52 inputs and run the existing planner."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.database import SessionLocal, get_db
from app.main import app
from app.models import (
    ForecastDemandWeek,
    InboundShipment,
    InboundShipmentLine,
    IngestionBatch,
    InventoryBalance,
    InventoryLedgerControl,
    InventorySnapshot,
    Location,
    PABWeek,
    PlanningItem,
    PlanningPoResidualSnapshot,
    PlanningRun,
    PlanningRunFinding,
    PlanningSupplySnapshot,
    PurchaseOrder,
    PurchaseOrderLine,
    SKU,
    SKUSubstitution,
    SKUUOM,
    StagingRecord,
    SubstitutionApproval,
    Supplier,
    SupplierCapacity,
    SupplierLeadTime,
    SupplierSite,
    SupplierSKU,
    SupplyCancellation,
    UnitOfMeasure,
    ValidationFinding,
)
from app.models.procurement import SupplierCapacityReservation, ProcurementEvent
from app.models.planning import InventoryReceiptPosting, InventoryTransaction, ShortageCause
from app.services.ingestion_service import VALIDATION_VERSION, parse_csv_upload
from app.services.validation_service import ValidationResult, validate_batch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPOSITORY_ROOT / "data" / "demo52"
SCENARIO_KEY = "demo52-import"
HORIZON_START = date(2026, 1, 5)
STAGED_INPUTS = {
    "forecast_demand_0001.csv": "forecast_demand",
    "inventory.csv": "inventory",
    "inbound_shipment_lines.csv": "inbound_shipment_line",
    "sku_substitution.csv": "sku_substitution",
    "purchase_order_lines.csv": "purchase_order_line",
}


class ImportDataError(ValueError):
    """The input dataset is incomplete, inconsistent, or conflicts with stored data."""


@dataclass(frozen=True)
class DemoDataset:
    root: Path
    manifest: dict[str, Any]
    contents: dict[str, bytes]
    rows: dict[str, list[dict[str, str]]]


@dataclass(frozen=True)
class ImportResult:
    inventory_snapshot_id: int
    batch_ids: dict[str, int]
    counts: dict[str, int]
    already_imported: bool


def _decimal(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ImportDataError(f"{field} must be a decimal value.") from error
    if not result.is_finite():
        raise ImportDataError(f"{field} must be finite.")
    return result


def _optional_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def _optional_datetime(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _as_bool(value: str, field: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ImportDataError(f"{field} must be true or false.")


def load_dataset(data_root: Path = DEFAULT_DATA_ROOT) -> DemoDataset:
    """Validate all input-file hashes and row counts; ground truth is never read."""
    root = data_root.resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ImportDataError(f"Dataset manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("profile") != "demo52":
        raise ImportDataError("Only the demo52 dataset profile is supported.")

    contents: dict[str, bytes] = {}
    rows: dict[str, list[dict[str, str]]] = {}
    for entry in manifest.get("files", []):
        if entry.get("classification") != "input" or entry.get("format") != "csv":
            continue
        relative = PurePosixPath(entry["path"])
        if not relative.parts or relative.parts[0] != "input":
            raise ImportDataError("Manifest input paths must be inside input/.")
        path = (root / Path(*relative.parts)).resolve()
        if root not in path.parents or not path.is_file():
            raise ImportDataError(f"Invalid or missing dataset input: {entry['path']}")
        content = path.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        if checksum != entry.get("sha256"):
            raise ImportDataError(f"Checksum mismatch for {entry['path']}.")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            file_rows = list(csv.DictReader(handle))
        if len(file_rows) != entry.get("row_count"):
            raise ImportDataError(f"Row-count mismatch for {entry['path']}.")
        name = relative.name
        if name in rows:
            raise ImportDataError(f"Duplicate input filename in manifest: {name}")
        contents[name] = content
        rows[name] = file_rows

    if set(STAGED_INPUTS) - rows.keys():
        raise ImportDataError("The dataset is missing one or more required input CSV files.")
    return DemoDataset(root, manifest, contents, rows)


def _analysis(dataset: DemoDataset) -> dict[str, Any]:
    rows = dataset.rows
    sku_uoms = {
        (int(row["sku_id"]), int(row["uom_id"])): _decimal(
            row["to_base_factor"], "SKU UOM conversion factor"
        )
        for row in rows["sku_uom.csv"]
    }

    received_lines = [
        row
        for row in rows["inbound_shipment_lines.csv"]
        if _decimal(row["quantity_received"], "quantity_received") > 0
    ]
    excluded_po_line_ids = {
        int(row["purchase_order_line_id"])
        for row in received_lines
        if row.get("purchase_order_line_id")
    }
    excluded_shipment_ids = {row["shipment_id"] for row in received_lines}
    if len(excluded_po_line_ids) != 6 or len(excluded_shipment_ids) != 6:
        raise ImportDataError(
            "Expected six partially received PO/shipment pairs to exclude."
        )

    duplicate_groups: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
    for row in rows["inbound_shipment_lines.csv"]:
        duplicate_groups[(row["shipment_id"], int(row["line_number"]))].append(row)
    duplicate_groups = {
        key: group for key, group in duplicate_groups.items() if len(group) > 1
    }
    if len(duplicate_groups) != 1 or any(
        len({tuple(sorted(row.items())) for row in group}) != 1
        for group in duplicate_groups.values()
    ):
        raise ImportDataError("Expected one identical duplicate inbound shipment line.")

    valid_inventory_rows = []
    invalid_inventory_rows = []
    for row in rows["inventory.csv"]:
        sku_id = int(row["sku_id"])
        uom_id = next(
            (
                int(uom["uom_id"])
                for uom in rows["unit_of_measure.csv"]
                if uom["code"] == row["uom_code"]
            ),
            None,
        )
        factor = sku_uoms.get((sku_id, uom_id)) if uom_id is not None else None
        if factor is None:
            invalid_inventory_rows.append(row)
            continue
        base_quantity = _decimal(row["quantity_on_hand_base"], "quantity_on_hand_base")
        input_quantity = _decimal(row["quantity"], "inventory quantity")
        if base_quantity < 0 or input_quantity * factor != base_quantity:
            raise ImportDataError("A valid inventory row does not reconcile to base units.")
        valid_inventory_rows.append(row)
    if len(valid_inventory_rows) != 96 or len(invalid_inventory_rows) != 1:
        raise ImportDataError("Expected 96 valid inventory positions and one invalid UOM row.")
    manifest_checksums = {
        PurePosixPath(entry["path"]).name: entry["sha256"]
        for entry in dataset.manifest["files"]
        if entry.get("classification") == "input"
    }
    snapshot_row = rows["inventory_snapshot.csv"][0]
    if (
        snapshot_row["source_checksum"] != manifest_checksums.get("inventory.csv")
        or date.fromisoformat(snapshot_row["captured_at"][:10]) != HORIZON_START
        or snapshot_row["snapshot_status"] != "validated"
    ):
        raise ImportDataError("The inventory snapshot metadata does not match the input file.")
    invalid = invalid_inventory_rows[0]
    if not (
        invalid["sku_code"] == "SKU-000001"
        and invalid["location_code"] == "LOC-001"
        and invalid["uom_code"] == "KG"
        and _decimal(invalid["quantity"], "invalid inventory quantity") == Decimal("10")
    ):
        raise ImportDataError("The expected invalid KG inventory row was not identified.")

    po_lines = [
        row
        for row in rows["purchase_order_lines.csv"]
        if int(row["purchase_order_line_id"]) not in excluded_po_line_ids
    ]
    retained_po_ids = {int(row["purchase_order_id"]) for row in po_lines}
    retained_pos = [
        row for row in rows["purchase_orders.csv"]
        if int(row["purchase_order_id"]) in retained_po_ids
    ]
    if len(po_lines) != 34 or len(retained_pos) != 34:
        raise ImportDataError("Expected 34 fully open PO lines and their 34 headers.")

    shipments = [
        row
        for row in rows["inbound_shipments.csv"]
        if row["shipment_id"] not in excluded_shipment_ids
    ]
    if len(shipments) != 1:
        raise ImportDataError("Expected only the unreceived duplicate-edge shipment to remain.")
    shipment_id_by_key = {
        row["shipment_id"]: int(row["inbound_shipment_id"]) for row in shipments
    }
    canonical_lines: list[dict[str, str]] = []
    seen_lines: set[tuple[str, int]] = set()
    for row in rows["inbound_shipment_lines.csv"]:
        key = (row["shipment_id"], int(row["line_number"]))
        if row["shipment_id"] in excluded_shipment_ids or key in seen_lines:
            continue
        if _decimal(row["quantity_received"], "quantity_received") != 0:
            raise ImportDataError("A partially received shipment line would be imported.")
        if row["shipment_id"] not in shipment_id_by_key:
            raise ImportDataError("An inbound line has no retained shipment header.")
        seen_lines.add(key)
        canonical_lines.append(row)
    if len(canonical_lines) != 1:
        raise ImportDataError("Expected one canonical operational inbound shipment line.")

    forecast_rows = rows["forecast_demand_0001.csv"]
    scope = sorted(
        {
            (int(row["sku_id"]), int(row["location_id"]))
            for row in forecast_rows
        }
    )
    weeks = sorted({date.fromisoformat(row["week_start"]) for row in forecast_rows})
    if (
        len(forecast_rows) != 4992
        or len(scope) != 96
        or len(weeks) != 52
        or weeks[0] != HORIZON_START
        or any((right - left).days != 7 for left, right in zip(weeks, weeks[1:]))
    ):
        raise ImportDataError("Forecast inputs must cover 96 pairs and 52 consecutive weeks.")

    stage_row_count = sum(len(rows[name]) for name in STAGED_INPUTS)
    return {
        "unit_of_measure": len(rows["unit_of_measure.csv"]),
        "location": len(rows["location.csv"]),
        "sku": len(rows["sku.csv"]),
        "sku_uom": len(rows["sku_uom.csv"]),
        "supplier": len(rows["supplier.csv"]),
        "supplier_site": len(rows["supplier_site.csv"]),
        "supplier_sku": len(rows["supplier_sku.csv"]),
        "supplier_capacity": len(rows["supplier_capacity.csv"]),
        "supplier_lead_time": len(rows["supplier_lead_time.csv"]),
        "sku_substitution": len(rows["sku_substitution.csv"]),
        "substitution_approval": len(rows["substitution_approval.csv"]),
        "purchase_order": len(retained_pos),
        "purchase_order_line": len(po_lines),
        "inbound_shipment": len(shipments),
        "inbound_shipment_line": len(canonical_lines),
        "inventory_snapshot": len(rows["inventory_snapshot.csv"]),
        "inventory_balance": len(valid_inventory_rows),
        "staging_batch": len(STAGED_INPUTS),
        "staging_record": stage_row_count,
        "excluded_partial_po_shipment_pairs": len(excluded_po_line_ids),
        "excluded_invalid_inventory_rows": len(invalid_inventory_rows),
        "duplicate_inbound_rows_staged": sum(map(len, duplicate_groups.values())),
        "duplicate_inbound_rows_operationally_omitted": 1,
        "planning_scope_items": len(scope),
        "forecast_rows": len(forecast_rows),
        "pab_rows_if_calculated": len(scope) * 52,
        "_po_headers": retained_pos,
        "_po_lines": po_lines,
        "_shipments": shipments,
        "_shipment_lines": canonical_lines,
        "_inventory_rows": valid_inventory_rows,
        "_scope": scope,
        "_excluded_po_ids": excluded_po_line_ids,
        "_excluded_shipment_ids": excluded_shipment_ids,
        "_shipment_id_by_key": shipment_id_by_key,
    }


def build_import_plan(data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, Any]:
    dataset = load_dataset(data_root)
    analysis = _analysis(dataset)
    return {
        "profile": "demo52",
        "seed": dataset.manifest.get("seed"),
        "data_root": str(dataset.root),
        "counts": {key: value for key, value in analysis.items() if not key.startswith("_")},
        "import_order": [
            "unit_of_measure and location",
            "sku and sku_uom",
            "supplier, supplier_site, supplier_sku",
            "supplier_capacity, supplier_lead_time, sku_substitution, substitution_approval",
            "purchase_order and purchase_order_line (34 retained pairs; no destination stored)",
            "inbound_shipment and inbound_shipment_line (one canonical unreceived line)",
            "inventory_snapshot and 96 valid inventory_balance rows",
            "five ingestion batches and 5,139 staging records",
            "validate staged batches; create and calculate a 52-week run through planning services",
        ],
        "ground_truth_loaded": False,
    }


def _count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _batch_identifiers(dataset: DemoDataset) -> dict[str, tuple[str, str]]:
    return {
        filename: (
            f"demo52-import-{Path(filename).stem}-{hashlib.sha256(dataset.contents[filename]).hexdigest()[:20]}",
            hashlib.sha256(dataset.contents[filename]).hexdigest(),
        )
        for filename in STAGED_INPUTS
    }


def _check_imported_state(
    session: Session,
    dataset: DemoDataset,
    analysis: dict[str, Any],
) -> dict[str, int] | None:
    expected_batches = _batch_identifiers(dataset)
    expected_ids = {identity for identity, _ in expected_batches.values()}
    existing = session.scalars(
        select(IngestionBatch).where(IngestionBatch.batch_identifier.like("demo52-import-%"))
    ).all()
    if not existing:
        return None
    if {batch.batch_identifier for batch in existing} != expected_ids:
        raise ImportDataError("Existing demo52 import markers do not match this dataset.")
    for filename, (identifier, checksum) in expected_batches.items():
        batch = next(item for item in existing if item.batch_identifier == identifier)
        if batch.file_checksum != checksum:
            raise ImportDataError(f"Existing staged batch conflicts with {filename}.")
        expected_records = len(dataset.rows[filename])
        actual_records = int(
            session.scalar(
                select(func.count())
                .select_from(StagingRecord)
                .where(StagingRecord.ingestion_batch_id == batch.ingestion_batch_id)
            )
            or 0
        )
        if actual_records != expected_records:
            raise ImportDataError(
                f"Existing staged batch {filename} has {actual_records} records; "
                f"expected {expected_records}."
            )

    expected_counts = {
        UnitOfMeasure: 6,
        Location: 4,
        SKU: 24,
        SKUUOM: 48,
        Supplier: 8,
        SupplierSite: 8,
        SupplierSKU: 48,
        SupplierCapacity: 48,
        SupplierLeadTime: 191,
        SKUSubstitution: 2,
        SubstitutionApproval: 1,
        PurchaseOrder: analysis["purchase_order"],
        PurchaseOrderLine: analysis["purchase_order_line"],
        InboundShipment: analysis["inbound_shipment"],
        InboundShipmentLine: analysis["inbound_shipment_line"],
        InventorySnapshot: 1,
        InventoryBalance: 96,
        IngestionBatch: 5,
        StagingRecord: analysis["staging_record"],
    }
    for model, expected in expected_counts.items():
        if _count(session, model) != expected:
            raise ImportDataError(
                f"Existing demo52 import is inconsistent: {model.__tablename__} "
                f"has {_count(session, model)} rows; expected {expected}."
            )
    ledger = session.get(InventoryLedgerControl, "inventory")
    if ledger is None or ledger.committed_version != 0:
        raise ImportDataError("The demo52 snapshot ledger has changed since import.")
    return {
        filename: next(item.ingestion_batch_id for item in existing if item.batch_identifier == identity)
        for filename, (identity, _) in expected_batches.items()
    }


def _assert_pristine(session: Session) -> None:
    target_models = (
        UnitOfMeasure,
        Location,
        SKU,
        SKUUOM,
        Supplier,
        SupplierSite,
        SupplierSKU,
        SupplierCapacity,
        SupplierCapacityReservation,
        SupplierLeadTime,
        SKUSubstitution,
        SubstitutionApproval,
        PurchaseOrder,
        PurchaseOrderLine,
        InboundShipment,
        InboundShipmentLine,
        ProcurementEvent,
        InventorySnapshot,
        InventoryBalance,
        InventoryReceiptPosting,
        InventoryTransaction,
        IngestionBatch,
        StagingRecord,
        ValidationFinding,
        PlanningRun,
        PlanningItem,
        ForecastDemandWeek,
        PlanningPoResidualSnapshot,
        PlanningSupplySnapshot,
        PABWeek,
        PlanningRunFinding,
        ShortageCause,
        SupplyCancellation,
    )
    populated = {
        model.__tablename__: _count(session, model)
        for model in target_models
        if _count(session, model) != 0
    }
    if populated:
        raise ImportDataError(
            "Import refused because target tables are not empty: "
            + ", ".join(f"{table}={count}" for table, count in sorted(populated.items()))
        )
    ledger_rows = session.scalars(select(InventoryLedgerControl)).all()
    if len(ledger_rows) != 1 or (
        ledger_rows[0].ledger_key,
        ledger_rows[0].committed_version,
    ) != ("inventory", 0):
        raise ImportDataError(
            "Expected the single infrastructure ledger row ('inventory', 0); no changes made."
        )


def _add_staging(
    session: Session,
    dataset: DemoDataset,
) -> dict[str, int]:
    batch_ids: dict[str, int] = {}
    for filename, record_type in STAGED_INPUTS.items():
        normalized_type, parsed_rows = parse_csv_upload(
            filename,
            record_type,
            dataset.contents[filename],
        )
        checksum = hashlib.sha256(dataset.contents[filename]).hexdigest()
        batch = IngestionBatch(
            batch_identifier=_batch_identifiers(dataset)[filename][0],
            source_name="demo52-import",
            source_file_name=filename,
            file_checksum=checksum,
            processing_status="received",
            validation_version=VALIDATION_VERSION,
        )
        session.add(batch)
        session.flush()
        session.add_all(
            StagingRecord(
                ingestion_batch_id=batch.ingestion_batch_id,
                record_type=normalized_type,
                source_row_number=row.source_row_number,
                raw_payload_json=row.payload,
                raw_payload_text=row.raw_text,
                payload_hash=row.payload_hash,
                disposition="pending",
            )
            for row in parsed_rows
        )
        session.flush()
        batch_ids[filename] = batch.ingestion_batch_id
    return batch_ids


def import_demo52(session: Session, data_root: Path = DEFAULT_DATA_ROOT) -> ImportResult:
    """Import ORM rows and raw staging records atomically; never overwrite existing rows."""
    if session.in_transaction():
        raise ImportDataError("A fresh Session is required for the transactional import.")
    dataset = load_dataset(data_root)
    analysis = _analysis(dataset)
    counts = {key: value for key, value in analysis.items() if not key.startswith("_")}

    with session.begin():
        existing_batch_ids = _check_imported_state(session, dataset, analysis)
        if existing_batch_ids is not None:
            snapshot_id = int(dataset.rows["inventory_snapshot.csv"][0]["inventory_snapshot_id"])
            return ImportResult(snapshot_id, existing_batch_ids, counts, True)
        _assert_pristine(session)
        rows = dataset.rows

        session.add_all(
            UnitOfMeasure(
                uom_id=int(row["uom_id"]),
                code=row["code"],
                dimension=row["dimension"],
                decimal_places=int(row["decimal_places"]),
                is_active=_as_bool(row["is_active"], "is_active"),
            )
            for row in rows["unit_of_measure.csv"]
        )
        session.add_all(
            Location(
                location_id=int(row["location_id"]),
                location_code=row["location_code"],
                name=row["name"],
                location_type=row["location_type"],
                country_code=row["country_code"],
                time_zone=row["time_zone"],
                status=row["status"],
            )
            for row in rows["location.csv"]
        )
        session.flush()

        session.add_all(
            SKU(
                sku_id=int(row["sku_id"]),
                sku_code=row["sku_code"],
                name=row["name"],
                category=row["category"] or None,
                base_uom_id=int(row["base_uom_id"]),
                status=row["status"],
            )
            for row in rows["sku.csv"]
        )
        session.flush()
        session.add_all(
            SKUUOM(
                sku_id=int(row["sku_id"]),
                uom_id=int(row["uom_id"]),
                to_base_factor=_decimal(row["to_base_factor"], "to_base_factor"),
                is_base_uom=_as_bool(row["is_base_uom"], "is_base_uom"),
                is_purchase_uom=_as_bool(row["is_purchase_uom"], "is_purchase_uom"),
            )
            for row in rows["sku_uom.csv"]
        )
        session.flush()

        session.add_all(
            Supplier(
                supplier_id=int(row["supplier_id"]),
                supplier_code=row["supplier_code"],
                name=row["name"],
                status=row["status"],
                default_currency=row["default_currency"],
            )
            for row in rows["supplier.csv"]
        )
        session.flush()
        session.add_all(
            SupplierSite(
                supplier_site_id=int(row["supplier_site_id"]),
                supplier_id=int(row["supplier_id"]),
                site_code=row["site_code"],
                name=row["name"],
                country_code=row["country_code"],
                time_zone=row["time_zone"],
                status=row["status"],
            )
            for row in rows["supplier_site.csv"]
        )
        session.add_all(
            SupplierSKU(
                supplier_sku_id=int(row["supplier_sku_id"]),
                supplier_id=int(row["supplier_id"]),
                sku_id=int(row["sku_id"]),
                supplier_item_code=row["supplier_item_code"],
                order_uom_id=int(row["order_uom_id"]),
                minimum_order_qty=_decimal(row["minimum_order_qty"], "minimum_order_qty"),
                order_multiple=_decimal(row["order_multiple"], "order_multiple"),
                unit_cost=_decimal(row["unit_cost"], "unit_cost"),
                currency=row["currency"],
                effective_from=date.fromisoformat(row["effective_from"]),
                effective_to=_optional_date(row["effective_to"]),
                status=row["status"],
            )
            for row in rows["supplier_sku.csv"]
        )
        session.flush()

        session.add_all(
            SupplierCapacity(
                supplier_capacity_id=int(row["supplier_capacity_id"]),
                supplier_id=int(row["supplier_id"]),
                sku_id=int(row["sku_id"]),
                capacity_uom_id=int(row["capacity_uom_id"]),
                effective_from=date.fromisoformat(row["effective_from"]),
                effective_to=_optional_date(row["effective_to"]),
                capacity_quantity=_decimal(row["capacity_quantity"], "capacity_quantity"),
                reserved_quantity=_decimal(row["reserved_quantity"], "reserved_quantity"),
            )
            for row in rows["supplier_capacity.csv"]
        )
        session.add_all(
            SupplierLeadTime(
                supplier_lead_time_id=int(row["supplier_lead_time_id"]),
                supplier_id=int(row["supplier_id"]),
                supplier_site_id=int(row["supplier_site_id"]),
                sku_id=int(row["sku_id"]),
                location_id=int(row["location_id"]),
                lead_time_days=int(row["lead_time_days"]),
                effective_from=date.fromisoformat(row["effective_from"]),
                effective_to=_optional_date(row["effective_to"]),
            )
            for row in rows["supplier_lead_time.csv"]
        )
        session.add_all(
            SKUSubstitution(
                substitution_id=index,
                source_sku_id=next(
                    int(sku["sku_id"])
                    for sku in rows["sku.csv"]
                    if sku["sku_code"] == row["source_sku_code"]
                ),
                substitute_sku_id=next(
                    int(sku["sku_id"])
                    for sku in rows["sku.csv"]
                    if sku["sku_code"] == row["substitute_sku_code"]
                ),
                conversion_factor=_decimal(row["conversion_factor"], "conversion_factor"),
                effective_from=date.fromisoformat(row["effective_date"]),
                effective_to=None,
                status=row["status"],
            )
            for index, row in enumerate(rows["sku_substitution.csv"], start=1)
        )
        session.flush()
        session.add_all(
            SubstitutionApproval(
                substitution_id=int(row["substitution_id"]),
                revision_no=int(row["revision_no"]),
                decision=row["decision"],
                approved_by=int(row["approved_by"]) if row["approved_by"] else None,
                decided_at=None,
                effective_from=date.fromisoformat(row["effective_from"]),
                effective_to=_optional_date(row["effective_to"]),
                reason=row["reason"] or None,
            )
            for row in rows["substitution_approval.csv"]
        )
        session.flush()

        session.add_all(
            PurchaseOrder(
                purchase_order_id=int(row["purchase_order_id"]),
                purchase_order_number=row["purchase_order_number"],
                supplier_id=int(row["supplier_id"]),
                supplier_site_id=int(row["supplier_site_id"]) if row["supplier_site_id"] else None,
                order_date=date.fromisoformat(row["order_date"]),
                requested_delivery_date=_optional_date(row["requested_delivery_date"]),
                currency=row["currency"],
                status=row["status"],
            )
            for row in analysis["_po_headers"]
        )
        session.flush()
        session.add_all(
            PurchaseOrderLine(
                purchase_order_line_id=int(row["purchase_order_line_id"]),
                purchase_order_id=int(row["purchase_order_id"]),
                supplier_id=int(row["supplier_id"]),
                line_number=int(row["line_number"]),
                sku_id=int(row["sku_id"]),
                order_uom_id=int(row["order_uom_id"]),
                quantity_ordered=_decimal(row["quantity_ordered"], "quantity_ordered"),
                order_uom_to_base_factor=_decimal(
                    row["order_uom_to_base_factor"], "order_uom_to_base_factor"
                ),
                quantity_received_base=Decimal("0"),
                unit_cost=_decimal(row["unit_cost"], "unit_cost"),
                line_status=row["line_status"],
            )
            for row in analysis["_po_lines"]
        )
        session.flush()

        session.add_all(
            InboundShipment(
                inbound_shipment_id=int(row["inbound_shipment_id"]),
                shipment_number=row["shipment_number"],
                supplier_id=int(row["supplier_id"]),
                supplier_site_id=int(row["supplier_site_id"]) if row["supplier_site_id"] else None,
                location_id=int(row["location_id"]),
                expected_arrival_date=_optional_date(row["expected_arrival_date"]),
                shipped_at=_optional_datetime(row["shipped_at"]),
                arrived_at=_optional_datetime(row["arrived_at"]),
                status=row["status"],
            )
            for row in analysis["_shipments"]
        )
        session.flush()
        session.add_all(
            InboundShipmentLine(
                inbound_shipment_id=analysis["_shipment_id_by_key"][row["shipment_id"]],
                supplier_id=int(row["supplier_id"]),
                line_number=int(row["line_number"]),
                purchase_order_line_id=None,
                sku_id=int(row["sku_id"]),
                uom_id=int(row["uom_id"]),
                quantity_shipped=_decimal(row["quantity_shipped"], "quantity_shipped"),
                quantity_received=Decimal("0"),
                uom_to_base_factor=_decimal(row["uom_to_base_factor"], "uom_to_base_factor"),
                lot_number=row["lot_number"] or None,
            )
            for row in analysis["_shipment_lines"]
        )
        session.flush()

        snapshot_row = rows["inventory_snapshot.csv"][0]
        snapshot_id = int(snapshot_row["inventory_snapshot_id"])
        session.add(
            InventorySnapshot(
                inventory_snapshot_id=snapshot_id,
                snapshot_key=snapshot_row["snapshot_key"],
                captured_at=_optional_datetime(snapshot_row["captured_at"]),
                ledger_watermark=int(snapshot_row["ledger_watermark"]),
                source_name=snapshot_row["source_name"],
                source_checksum=snapshot_row["source_checksum"],
                snapshot_status=snapshot_row["snapshot_status"],
            )
        )
        session.flush()
        session.add_all(
            InventoryBalance(
                inventory_snapshot_id=snapshot_id,
                sku_id=int(row["sku_id"]),
                location_id=int(row["location_id"]),
                quantity_on_hand_base=_decimal(
                    row["quantity_on_hand_base"], "quantity_on_hand_base"
                ),
            )
            for row in analysis["_inventory_rows"]
        )
        session.flush()
        batch_ids = _add_staging(session, dataset)

    return ImportResult(snapshot_id, batch_ids, counts, False)


def validate_staged_batches(
    session_factory: sessionmaker,
    batch_ids: dict[str, int],
) -> dict[str, ValidationResult]:
    results: dict[str, ValidationResult] = {}
    for filename, batch_id in batch_ids.items():
        with session_factory() as session:
            results[filename] = validate_batch(session, batch_id)
    return results


@contextmanager
def _planning_api_client(session_factory: sessionmaker) -> Iterator[TestClient]:
    sentinel = object()
    previous_override = app.dependency_overrides.get(get_db, sentinel)

    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        if previous_override is sentinel:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override


def create_planning_run_via_api(
    session_factory: sessionmaker,
    *,
    inventory_snapshot_id: int,
    scope: list[tuple[int, int]],
) -> int:
    with session_factory() as session:
        existing = session.scalars(
            select(PlanningRun).where(PlanningRun.scenario_key == SCENARIO_KEY)
        ).all()
        if len(existing) > 1:
            raise ImportDataError("Multiple demo52 planning runs exist; refusing to choose one.")
        if existing:
            run = existing[0]
            actual_scope = {
                (row[0], row[1])
                for row in session.execute(
                    select(PlanningItem.sku_id, PlanningItem.location_id).where(
                        PlanningItem.planning_run_id == run.planning_run_id
                    )
                ).all()
            }
            if (
                run.inventory_snapshot_id != inventory_snapshot_id
                or run.horizon_start_week != HORIZON_START
                or actual_scope != set(scope)
            ):
                raise ImportDataError("Existing demo52 planning run does not match this dataset.")
            return run.planning_run_id

    with _planning_api_client(session_factory) as client:
        response = client.post(
            "/api/v1/planning/runs",
            json={
                "scenario_key": SCENARIO_KEY,
                "horizon_start_week": HORIZON_START.isoformat(),
                "inventory_snapshot_id": inventory_snapshot_id,
                "items": [
                    {"sku_id": sku_id, "location_id": location_id}
                    for sku_id, location_id in scope
                ],
            },
        )
        if response.status_code != 201:
            raise ImportDataError(
                f"Planning-run API returned {response.status_code}: {response.text}"
            )
        return int(response.json()["planning_run_id"])


def create_and_calculate_run(
    session_factory: sessionmaker,
    *,
    inventory_snapshot_id: int,
    scope: list[tuple[int, int]],
) -> dict[str, Any]:
    run_id = create_planning_run_via_api(
        session_factory,
        inventory_snapshot_id=inventory_snapshot_id,
        scope=scope,
    )
    with _planning_api_client(session_factory) as client:
        response = client.post(f"/api/v1/planning/runs/{run_id}/calculate")
        if response.status_code != 200:
            raise ImportDataError(
                f"Planning calculation API returned {response.status_code}: {response.text}"
            )
        if response.json()["run_status"] != "completed":
            raise ImportDataError("Planning calculation API did not complete the run.")

        pab_response = client.get(f"/api/v1/planning/runs/{run_id}/pab?limit=1")
        shortages_response = client.get(
            f"/api/v1/planning/runs/{run_id}/shortages?limit=1"
        )
        findings_response = client.get(
            f"/api/v1/planning/runs/{run_id}/findings?limit=1000"
        )
        for name, result in (
            ("PAB", pab_response),
            ("shortages", shortages_response),
            ("findings", findings_response),
        ):
            if result.status_code != 200:
                raise ImportDataError(
                    f"Planning {name} API returned {result.status_code}: {result.text}"
                )
        first_shortage = (
            shortages_response.json()["items"][0]
            if shortages_response.json()["items"]
            else None
        )
        mitigation_count = 0
        if first_shortage is not None:
            mitigation_response = client.get(
                "/api/v1/planning/runs/"
                f"{run_id}/shortages/{first_shortage['sku_id']}/"
                f"{first_shortage['location_id']}/{first_shortage['week_start']}/mitigations"
            )
            if mitigation_response.status_code != 200:
                raise ImportDataError(
                    "Shortage mitigation API returned "
                    f"{mitigation_response.status_code}: {mitigation_response.text}"
                )
            mitigation_count = mitigation_response.json()["total"]

    with session_factory() as session:
        forecast_count = int(
            session.scalar(
                select(func.count()).select_from(ForecastDemandWeek).where(
                    ForecastDemandWeek.planning_run_id == run_id
                )
            )
            or 0
        )
        pab_rows = session.scalars(
            select(PABWeek)
            .where(PABWeek.planning_run_id == run_id)
            .order_by(PABWeek.sku_id, PABWeek.location_id, PABWeek.week_start)
        ).all()
        if forecast_count != 4992 or len(pab_rows) != 4992:
            raise ImportDataError(
                f"Planning output count mismatch: {forecast_count} forecasts, "
                f"{len(pab_rows)} PAB rows."
            )
        if pab_response.json()["total"] != len(pab_rows):
            raise ImportDataError("PAB API row count differs from the persisted PAB results.")
        opening_balances = {
            (row.sku_id, row.location_id): Decimal(row.quantity_on_hand_base)
            for row in session.scalars(
                select(InventoryBalance).where(
                    InventoryBalance.inventory_snapshot_id == inventory_snapshot_id
                )
            ).all()
        }
        previous_by_pair: dict[tuple[int, int], PABWeek] = {}
        for row in pab_rows:
            pair = (row.sku_id, row.location_id)
            previous = previous_by_pair.get(pair)
            if previous is None and row.opening_base != opening_balances.get(pair, Decimal("0")):
                raise ImportDataError("The first PAB opening does not match the inventory snapshot.")
            if previous is not None and row.opening_base != previous.ending_firm_base:
                raise ImportDataError("Week-by-week PAB opening does not reconcile.")
            if (
                row.opening_base
                + row.firm_inbound_base
                + row.firm_po_remainder_base
                - row.forecast_demand_base
                != row.ending_firm_base
            ):
                raise ImportDataError("A PAB row does not reconcile to its inputs.")
            previous_by_pair[pair] = row

        shortage_count = sum(row.shortage_quantity_base > 0 for row in pab_rows)
        if shortage_count != shortages_response.json()["total"]:
            raise ImportDataError("Shortages API count differs from the persisted PAB results.")
        return {
            "planning_run_id": run_id,
            "run_status": "completed",
            "scope_items": len(scope),
            "forecast_rows": forecast_count,
            "pab_rows": len(pab_rows),
            "shortage_rows": shortages_response.json()["total"],
            "finding_rows": findings_response.json()["total"],
            "recommendations_for_first_shortage": mitigation_count,
        }


def apply_demo52(data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, Any]:
    dataset = load_dataset(data_root)
    analysis = _analysis(dataset)
    with SessionLocal() as session:
        imported = import_demo52(session, data_root)
    validation = validate_staged_batches(SessionLocal, imported.batch_ids)
    plan = create_and_calculate_run(
        SessionLocal,
        inventory_snapshot_id=imported.inventory_snapshot_id,
        scope=analysis["_scope"],
    )
    return {
        "import": {
            "already_imported": imported.already_imported,
            "counts": imported.counts,
            "batch_ids": imported.batch_ids,
        },
        "validation": {
            filename: asdict(result)
            for filename, result in validation.items()
        },
        "planning": plan,
        "edge_case_status": {
            "duplicate_inbound_inventory": (
                "staged for duplicate validation; one canonical operational line imported"
            ),
            "unapproved_substitute": (
                "staged and rejected when approval is absent or pending; no substitute approved"
            ),
            "supplier_capacity_exceeded": (
                "input evidence retained; existing validator reports capacity checks unsupported; "
                "PO supply remains unallocated"
            ),
            "shortage_missing_lead_time": (
                "input evidence retained; PO destination ambiguity prevents a run-level lead-time "
                "finding for that line"
            ),
            "uom_mismatch": (
                "invalid KG row staged and excluded from inventory; valid zero EA balance retained"
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the import and run the existing planning services. Requires prior user approval.",
    )
    args = parser.parse_args()
    if args.apply:
        report = apply_demo52(args.data_root)
    else:
        report = build_import_plan(args.data_root)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
