"""Generate deterministic, schema-aligned supply-planning CSVs.

Existing output is replaced only when its manifest and every generated-file
checksum verify. Unrecognized or edited files are never overwritten.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence
from uuid import uuid4


DEFAULT_SEED = 20261022
GENERATOR_ID = "supplychain-planner-synthetic-data"
GENERATOR_VERSION = 1
MAX_CSV_ROWS_PER_FILE = 50_000
HORIZON_START = date(2026, 1, 5)
EFFECTIVE_FROM = date(2026, 1, 1)
EFFECTIVE_TO = date(2027, 12, 31)
ZERO = Decimal("0")
FOUR_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class Profile:
    skus: int
    locations: int
    suppliers: int
    forecast_weeks: int
    purchase_orders: int


PROFILES = {
    "starter": Profile(
        skus=24,
        locations=4,
        suppliers=8,
        forecast_weeks=13,
        purchase_orders=40,
    ),
    "full": Profile(
        skus=3_000,
        locations=50,
        suppliers=500,
        forecast_weeks=52,
        purchase_orders=10_000,
    ),
}

UOMS = (
    (1, "EA", "count", 0),
    (2, "CASE", "count", 0),
    (3, "KG", "mass", 3),
    (4, "LB", "mass", 3),
    (5, "L", "volume", 3),
    (6, "ML", "volume", 0),
)

CATEGORIES = ("components", "packaging", "electrical", "fasteners", "materials")
COUNTRIES = ("US", "CA", "MX", "DE", "PL", "JP", "CN")
TIME_ZONES = (
    "America/Chicago",
    "America/Los_Angeles",
    "America/Toronto",
    "Europe/Berlin",
    "Europe/Warsaw",
    "Asia/Tokyo",
    "Asia/Shanghai",
)
CURRENCIES = ("USD", "EUR", "CAD", "GBP")


class _DigestingTextWriter:
    def __init__(self, handle) -> None:
        self.handle = handle
        self.digest = hashlib.sha256()

    def write(self, value: str) -> int:
        self.digest.update(value.encode("utf-8"))
        return self.handle.write(value)


class _CSVWriter:
    def __init__(
        self,
        root: Path,
        relative_directory: str,
        stem: str,
        columns: Sequence[str],
        manifest_files: list[dict[str, object]],
        *,
        row_limit: int | None = None,
    ) -> None:
        self.root = root
        self.relative_directory = relative_directory
        self.stem = Path(stem).stem
        self.columns = columns
        self.manifest_files = manifest_files
        self.row_limit = row_limit
        self.part = 0
        self.current_rows = 0
        self.total_rows = 0
        self.handle = None
        self.digesting_writer = None
        self.writer = None

    def _open_part(self) -> None:
        self.part += 1
        self.current_rows = 0
        if self.row_limit is None:
            filename = f"{self.stem}.csv"
        else:
            filename = f"{self.stem}_{self.part:04d}.csv"
        relative_path = f"{self.relative_directory}/{filename}"
        path = self.root.joinpath(*PurePosixPath(relative_path).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("w", encoding="utf-8", newline="")
        self.digesting_writer = _DigestingTextWriter(self.handle)
        self.writer = csv.writer(self.digesting_writer, lineterminator="\n")
        self.writer.writerow(self.columns)
        self.current_path = relative_path

    def _close_part(self) -> None:
        if self.handle is None:
            return
        self.handle.flush()
        self.handle.close()
        self.manifest_files.append(
            {
                "path": self.current_path,
                "classification": "input" if self.relative_directory == "input" else "ground_truth",
                "format": "csv",
                "row_count": self.current_rows,
                "sha256": self.digesting_writer.digest.hexdigest(),
            }
        )
        self.handle = None
        self.digesting_writer = None
        self.writer = None

    def write(self, values: Sequence[object]) -> int:
        if self.writer is None or (
            self.row_limit is not None and self.current_rows >= self.row_limit
        ):
            self._close_part()
            self._open_part()
        self.writer.writerow(values)
        self.current_rows += 1
        self.total_rows += 1
        return self.total_rows

    def close(self) -> None:
        self._close_part()


def _format_decimal(value: Decimal | int | str) -> str:
    result = Decimal(str(value)).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)
    formatted = format(result, "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def _datetime(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _write_csv(
    root: Path,
    relative_directory: str,
    filename: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
    manifest_files: list[dict[str, object]],
) -> int:
    writer = _CSVWriter(
        root,
        relative_directory,
        Path(filename).stem,
        columns,
        manifest_files,
    )
    try:
        for row in rows:
            writer.write(row)
    finally:
        writer.close()
    return writer.total_rows


def _write_text(
    root: Path,
    relative_path: str,
    content: str,
    manifest_files: list[dict[str, object]],
    *,
    classification: str,
    row_count: int | None = None,
) -> None:
    path = root.joinpath(*PurePosixPath(relative_path).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = content.encode("utf-8")
    with path.open("wb") as handle:
        handle.write(encoded)
    manifest_files.append(
        {
            "path": relative_path,
            "classification": classification,
            "format": "json" if relative_path.endswith(".json") else "markdown",
            "row_count": row_count,
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("Existing manifest contains a non-string file path.")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.as_posix() != value
        or path.parts[0] not in {"input", "ground_truth"}
        and value != "data_dictionary.md"
    ):
        raise ValueError("Existing manifest contains an unsafe generated-file path.")
    return path


def _inspect_existing_output(root: Path) -> dict[str, object] | None:
    if not root.exists():
        return None
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Output path must be a regular directory: {root}")

    manifest_path = root / "manifest.json"
    disk_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Refusing to overwrite a path containing a symlink: {path}")
        if path.is_file():
            disk_files.add(path.relative_to(root).as_posix())

    if not disk_files:
        return None
    if "manifest.json" not in disk_files:
        raise FileExistsError(
            f"Output directory contains files but is not generator-owned: {root}"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot verify existing output manifest: {manifest_path}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("generator") != GENERATOR_ID
        or manifest.get("generator_version") != GENERATOR_VERSION
        or not isinstance(manifest.get("files"), list)
    ):
        raise FileExistsError(
            f"Output directory is not owned by this generator: {root}"
        )

    owned: dict[str, str] = {}
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ValueError("Existing manifest contains an invalid file entry.")
        relative = _manifest_relative_path(entry.get("path")).as_posix()
        checksum = entry.get("sha256")
        if not isinstance(checksum, str) or relative in owned:
            raise ValueError("Existing manifest has invalid or duplicate checksums.")
        owned[relative] = checksum
    expected_disk_files = set(owned) | {"manifest.json"}
    unowned = disk_files - expected_disk_files
    if unowned:
        raise FileExistsError(
            "Output contains files not listed in its manifest; refusing to replace: "
            + ", ".join(sorted(unowned)[:5])
        )
    for relative, expected_checksum in owned.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        if path.exists() and _sha256_file(path) != expected_checksum:
            raise FileExistsError(
                f"Generated file was modified; refusing to overwrite it: {path}"
            )
    return manifest


def _remove_verified_output(root: Path, manifest: dict[str, object] | None) -> None:
    if manifest is not None:
        for entry in manifest["files"]:
            relative = _manifest_relative_path(entry["path"])
            path = root.joinpath(*relative.parts)
            if path.exists():
                path.unlink()
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            path.rmdir()
    root.rmdir()


def _remove_staging_output(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    root.rmdir()


def _lead_time_days(supplier_id: int, sku_id: int, location_id: int) -> int | None:
    if (supplier_id, sku_id, location_id) == (1, 1, 1):
        return None
    return 4 + ((supplier_id * 7 + sku_id * 3 + location_id * 11) % 25)


def _catalog(
    root: Path,
    profile: Profile,
    rng: random.Random,
    files: list[dict[str, object]],
) -> tuple[dict[int, dict[str, object]], list[dict[str, object]], list[dict[str, object]], dict[int, list[dict[str, object]]], dict[tuple[int, int], Decimal]]:
    _write_csv(
        root,
        "input",
        "unit_of_measure.csv",
        ("uom_id", "code", "dimension", "decimal_places", "is_active"),
        ((uom_id, code, dimension, places, "true") for uom_id, code, dimension, places in UOMS),
        files,
    )

    skus: dict[int, dict[str, object]] = {}
    sku_rows = _CSVWriter(
        root,
        "input",
        "sku.csv",
        ("sku_id", "sku_code", "name", "category", "base_uom_id", "base_uom_code", "status"),
        files,
    )
    sku_uoms = _CSVWriter(
        root,
        "input",
        "sku_uom.csv",
        (
            "sku_id",
            "sku_code",
            "uom_id",
            "uom_code",
            "to_base_factor",
            "is_base_uom",
            "is_purchase_uom",
        ),
        files,
    )
    uom_by_id = {row[0]: row for row in UOMS}
    for sku_id in range(1, profile.skus + 1):
        if sku_id <= 2:
            base_uom_id = 1
        else:
            base_uom_id = (1, 1, 3, 3, 5, 5)[(sku_id - 1) % 6]
        base_uom = uom_by_id[base_uom_id]
        purchase_uom_id, factor = {
            1: (2, Decimal("12")),
            3: (4, Decimal("0.45359237")),
            5: (6, Decimal("0.001")),
        }[base_uom_id]
        sku_code = f"SKU-{sku_id:06d}"
        category = CATEGORIES[(sku_id - 1) % len(CATEGORIES)]
        skus[sku_id] = {
            "sku_code": sku_code,
            "category": category,
            "base_uom_id": base_uom_id,
            "base_uom_code": base_uom[1],
            "purchase_uom_id": purchase_uom_id,
            "purchase_uom_code": uom_by_id[purchase_uom_id][1],
            "purchase_factor": factor,
            "base_demand": 5 + (sku_id * 17 % 46),
        }
        sku_rows.write(
            (sku_id, sku_code, f"Industrial item {sku_id:06d}", category, base_uom_id, base_uom[1], "active")
        )
        sku_uoms.write(
            (sku_id, sku_code, base_uom_id, base_uom[1], "1", "true", "false")
        )
        sku_uoms.write(
            (sku_id, sku_code, purchase_uom_id, uom_by_id[purchase_uom_id][1], _format_decimal(factor), "false", "true")
        )
    sku_rows.close()
    sku_uoms.close()

    locations: list[dict[str, object]] = []
    location_types = ("warehouse", "factory", "store", "distribution_center")
    location_rows = _CSVWriter(
        root,
        "input",
        "location.csv",
        ("location_id", "location_code", "name", "location_type", "country_code", "time_zone", "status"),
        files,
    )
    for location_id in range(1, profile.locations + 1):
        region_index = (location_id - 1) % len(COUNTRIES)
        location = {
            "location_id": location_id,
            "location_code": f"LOC-{location_id:03d}",
            "location_type": location_types[(location_id - 1) % len(location_types)],
            "country_code": COUNTRIES[region_index],
            "time_zone": TIME_ZONES[region_index],
        }
        locations.append(location)
        location_rows.write(
            (
                location_id,
                location["location_code"],
                f"Planning site {location_id:03d}",
                location["location_type"],
                location["country_code"],
                location["time_zone"],
                "active",
            )
        )
    location_rows.close()

    suppliers: list[dict[str, object]] = []
    supplier_rows = _CSVWriter(
        root,
        "input",
        "supplier.csv",
        ("supplier_id", "supplier_code", "name", "status", "default_currency"),
        files,
    )
    site_rows = _CSVWriter(
        root,
        "input",
        "supplier_site.csv",
        ("supplier_site_id", "supplier_id", "supplier_code", "site_code", "name", "country_code", "time_zone", "status"),
        files,
    )
    for supplier_id in range(1, profile.suppliers + 1):
        region_index = (supplier_id - 1) % len(COUNTRIES)
        supplier = {
            "supplier_id": supplier_id,
            "supplier_code": f"SUP-{supplier_id:04d}",
            "supplier_site_id": supplier_id,
            "currency": CURRENCIES[(supplier_id - 1) % len(CURRENCIES)],
            "country_code": COUNTRIES[region_index],
            "time_zone": TIME_ZONES[region_index],
        }
        suppliers.append(supplier)
        supplier_rows.write(
            (
                supplier_id,
                supplier["supplier_code"],
                f"Supply partner {supplier_id:04d}",
                "active",
                supplier["currency"],
            )
        )
        site_rows.write(
            (
                supplier_id,
                supplier_id,
                supplier["supplier_code"],
                f"SITE-{supplier_id:04d}-01",
                f"Ship-from site {supplier_id:04d}",
                supplier["country_code"],
                supplier["time_zone"],
                "active",
            )
        )
    supplier_rows.close()
    site_rows.close()

    offers_by_sku: dict[int, list[dict[str, object]]] = {
        sku_id: [] for sku_id in skus
    }
    offers: list[dict[str, object]] = []
    offer_rows = _CSVWriter(
        root,
        "input",
        "supplier_sku.csv",
        (
            "supplier_sku_id",
            "supplier_id",
            "supplier_code",
            "sku_id",
            "sku_code",
            "supplier_item_code",
            "order_uom_id",
            "order_uom_code",
            "minimum_order_qty",
            "order_multiple",
            "unit_cost",
            "currency",
            "effective_from",
            "effective_to",
            "status",
        ),
        files,
    )
    capacity_rows = _CSVWriter(
        root,
        "input",
        "supplier_capacity.csv",
        (
            "supplier_capacity_id",
            "supplier_id",
            "supplier_code",
            "sku_id",
            "sku_code",
            "capacity_uom_id",
            "capacity_uom_code",
            "effective_from",
            "effective_to",
            "capacity_quantity",
            "reserved_quantity",
        ),
        files,
    )
    lead_time_rows = _CSVWriter(
        root,
        "input",
        "supplier_lead_time.csv",
        (
            "supplier_lead_time_id",
            "supplier_id",
            "supplier_code",
            "supplier_site_id",
            "supplier_site_code",
            "sku_id",
            "sku_code",
            "location_id",
            "location_code",
            "lead_time_days",
            "effective_from",
            "effective_to",
        ),
        files,
        row_limit=(
            MAX_CSV_ROWS_PER_FILE
            if profile.skus * 2 * profile.locations - 1 > MAX_CSV_ROWS_PER_FILE
            else None
        ),
    )
    capacity_by_offer: dict[tuple[int, int], Decimal] = {}
    offer_id = 0
    capacity_id = 0
    for sku_id, sku in skus.items():
        primary_supplier_id = (sku_id - 1) % profile.suppliers + 1
        secondary_supplier_id = primary_supplier_id % profile.suppliers + 1
        for supplier_id in (primary_supplier_id, secondary_supplier_id):
            offer_id += 1
            supplier = suppliers[supplier_id - 1]
            offer = {
                "supplier_sku_id": offer_id,
                "supplier_id": supplier_id,
                "supplier_code": supplier["supplier_code"],
                "sku_id": sku_id,
                "sku_code": sku["sku_code"],
                "order_uom_id": sku["purchase_uom_id"],
                "order_uom_code": sku["purchase_uom_code"],
                "factor": sku["purchase_factor"],
                "currency": supplier["currency"],
                "unit_cost": Decimal(2 + (sku_id * 13 + supplier_id * 3) % 250) / Decimal("10"),
            }
            offers.append(offer)
            offers_by_sku[sku_id].append(offer)
            offer_rows.write(
                (
                    offer_id,
                    supplier_id,
                    supplier["supplier_code"],
                    sku_id,
                    sku["sku_code"],
                    f"{supplier['supplier_code']}-{sku['sku_code']}",
                    sku["purchase_uom_id"],
                    sku["purchase_uom_code"],
                    "1",
                    "1",
                    _format_decimal(offer["unit_cost"]),
                    supplier["currency"],
                    "2025-01-01",
                    "",
                    "active",
                )
            )
            capacity_id += 1
            if (supplier_id, sku_id) == (1, 1):
                capacity = Decimal("25")
                reserved = ZERO
            elif supplier_id % 17 == 0:
                capacity = Decimal(rng.randint(35, 90))
                reserved = Decimal(rng.randint(0, int(capacity * Decimal("0.15"))))
            else:
                capacity = Decimal(rng.randint(100, 500))
                reserved = Decimal(rng.randint(0, int(capacity * Decimal("0.15"))))
            capacity_by_offer[(supplier_id, sku_id)] = capacity
            capacity_rows.write(
                (
                    capacity_id,
                    supplier_id,
                    supplier["supplier_code"],
                    sku_id,
                    sku["sku_code"],
                    sku["purchase_uom_id"],
                    sku["purchase_uom_code"],
                    _date(EFFECTIVE_FROM),
                    _date(EFFECTIVE_TO),
                    _format_decimal(capacity),
                    _format_decimal(reserved),
                )
            )
            for location in locations:
                lead_time = _lead_time_days(
                    supplier_id,
                    sku_id,
                    int(location["location_id"]),
                )
                if lead_time is None:
                    continue
                lead_time_rows.write(
                    (
                        lead_time_rows.total_rows + 1,
                        supplier_id,
                        supplier["supplier_code"],
                        supplier["supplier_site_id"],
                        f"SITE-{supplier_id:04d}-01",
                        sku_id,
                        sku["sku_code"],
                        location["location_id"],
                        location["location_code"],
                        lead_time,
                        _date(EFFECTIVE_FROM),
                        _date(EFFECTIVE_TO),
                    )
                )
    offer_rows.close()
    capacity_rows.close()
    lead_time_rows.close()

    substitution_rows = _CSVWriter(
        root,
        "input",
        "sku_substitution.csv",
        (
            "source_sku_code",
            "substitute_sku_code",
            "effective_date",
            "conversion_factor",
            "status",
        ),
        files,
    )
    approval_rows = _CSVWriter(
        root,
        "input",
        "substitution_approval.csv",
        (
            "substitution_id",
            "revision_no",
            "decision",
            "approved_by",
            "effective_from",
            "effective_to",
            "reason",
        ),
        files,
    )
    substitution_id = 0
    for source_sku_id in (1, *(range(7, profile.skus, 30))):
        substitute_sku_id = source_sku_id + 1
        if substitute_sku_id > profile.skus:
            continue
        substitution_id += 1
        source = skus[source_sku_id]
        substitute = skus[substitute_sku_id]
        substitution_rows.write(
            (
                source["sku_code"],
                substitute["sku_code"],
                _date(HORIZON_START),
                "1",
                "active",
            )
        )
        if source_sku_id != 1:
            approval_rows.write(
                (
                    substitution_id,
                    1,
                    "pending",
                    "",
                    _date(HORIZON_START),
                    "",
                    "Awaiting planner approval.",
                )
            )
    substitution_rows.close()
    approval_rows.close()

    return skus, locations, suppliers, offers_by_sku, capacity_by_offer


def _inventory(
    root: Path,
    profile: Profile,
    skus: dict[int, dict[str, object]],
    locations: list[dict[str, object]],
    files: list[dict[str, object]],
) -> tuple[int, str, int, str]:
    writer = _CSVWriter(
        root,
        "input",
        "inventory",
        (
            "inventory_snapshot_id",
            "sku_id",
            "sku_code",
            "location_id",
            "location_code",
            "quantity_on_hand_base",
            "quantity",
            "uom_code",
        ),
        files,
        row_limit=(
            MAX_CSV_ROWS_PER_FILE
            if profile.skus * profile.locations + 1 > MAX_CSV_ROWS_PER_FILE
            else None
        ),
    )
    mismatch_row = writer.write((1, 1, skus[1]["sku_code"], 1, locations[0]["location_code"], "", "10", "KG"))
    for sku_id, sku in skus.items():
        for location in locations:
            quantity = (
                ZERO
                if (sku_id, location["location_id"]) == (1, 1)
                else Decimal((sku_id * 37 + int(location["location_id"]) * 19) % 480)
            )
            writer.write(
                (
                    1,
                    sku_id,
                    sku["sku_code"],
                    location["location_id"],
                    location["location_code"],
                    _format_decimal(quantity),
                    _format_decimal(quantity),
                    sku["base_uom_code"],
                )
            )
    writer.close()
    inventory_files = sorted(
        (
            entry
            for entry in files
            if str(entry["path"]) == "input/inventory.csv"
            or str(entry["path"]).startswith("input/inventory_")
        ),
        key=lambda entry: str(entry["path"]),
    )
    checksum = (
        str(inventory_files[0]["sha256"])
        if len(inventory_files) == 1
        else hashlib.sha256(
            "".join(
                f"{entry['path']}:{entry['sha256']}\n"
                for entry in inventory_files
            ).encode("utf-8")
        ).hexdigest()
    )
    return writer.total_rows, checksum, mismatch_row, str(inventory_files[0]["path"])


def _write_inventory_snapshot(
    root: Path,
    inventory_checksum: str,
    files: list[dict[str, object]],
) -> None:
    _write_csv(
        root,
        "input",
        "inventory_snapshot.csv",
        (
            "inventory_snapshot_id",
            "snapshot_key",
            "captured_at",
            "ledger_watermark",
            "source_name",
            "source_checksum",
            "snapshot_status",
        ),
        (
            (
                1,
                "SYNTHETIC-INV-2026-01-05",
                "2026-01-05T00:00:00+00:00",
                0,
                "synthetic-generator",
                inventory_checksum,
                "validated",
            ),
        ),
        files,
    )


def _po_row(
    order_id: int,
    offer: dict[str, object],
    location: dict[str, object],
    order_date: date,
    requested_date: date | None,
    quantity: Decimal,
    received_base: Decimal,
    status: str,
    line_status: str,
    lead_days: int | None,
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    supplier_id = int(offer["supplier_id"])
    sku_id = int(offer["sku_id"])
    po_number = f"PO-{order_id:08d}"
    po_header = (
        order_id,
        po_number,
        supplier_id,
        offer["supplier_code"],
        supplier_id,
        _date(order_date),
        _date(requested_date),
        offer["currency"],
        status,
    )
    po_line = (
        order_id,
        order_id,
        po_number,
        1,
        supplier_id,
        offer["supplier_code"],
        sku_id,
        offer["sku_code"],
        offer["order_uom_id"],
        offer["order_uom_code"],
        _format_decimal(quantity),
        _format_decimal(offer["factor"]),
        _format_decimal(received_base),
        _format_decimal(offer["unit_cost"]),
        line_status,
        location["location_id"],
        location["location_code"],
        _date(order_date),
        _date(requested_date),
        "" if lead_days is None else lead_days,
    )
    return po_header, po_line


def _generate_procurement(
    root: Path,
    profile: Profile,
    skus: dict[int, dict[str, object]],
    locations: list[dict[str, object]],
    suppliers: list[dict[str, object]],
    offers_by_sku: dict[int, list[dict[str, object]]],
    capacity_by_offer: dict[tuple[int, int], Decimal],
    rng: random.Random,
    files: list[dict[str, object]],
) -> dict[str, object]:
    po_headers = _CSVWriter(
        root,
        "input",
        "purchase_orders",
        (
            "purchase_order_id",
            "purchase_order_number",
            "supplier_id",
            "supplier_code",
            "supplier_site_id",
            "order_date",
            "requested_delivery_date",
            "currency",
            "status",
        ),
        files,
    )
    po_lines = _CSVWriter(
        root,
        "input",
        "purchase_order_lines",
        (
            "purchase_order_line_id",
            "purchase_order_id",
            "po_number",
            "line_number",
            "supplier_id",
            "supplier_code",
            "sku_id",
            "sku_code",
            "order_uom_id",
            "uom_code",
            "quantity_ordered",
            "order_uom_to_base_factor",
            "quantity_received_base",
            "unit_cost",
            "line_status",
            "location_id",
            "location_code",
            "order_date",
            "requested_delivery_date",
            "lead_time_days",
        ),
        files,
    )
    shipments = _CSVWriter(
        root,
        "input",
        "inbound_shipments",
        (
            "inbound_shipment_id",
            "shipment_id",
            "shipment_number",
            "supplier_id",
            "supplier_code",
            "supplier_site_id",
            "location_id",
            "location_code",
            "expected_arrival_date",
            "shipped_at",
            "arrived_at",
            "status",
        ),
        files,
    )
    shipment_lines = _CSVWriter(
        root,
        "input",
        "inbound_shipment_lines",
        (
            "inbound_shipment_id",
            "shipment_id",
            "line_number",
            "supplier_id",
            "supplier_code",
            "purchase_order_line_id",
            "purchase_order_number",
            "sku_id",
            "sku_code",
            "uom_id",
            "uom_code",
            "quantity_shipped",
            "quantity_received",
            "uom_to_base_factor",
            "lot_number",
        ),
        files,
    )
    transport_truth = _CSVWriter(
        root,
        "ground_truth",
        "transport_outcomes",
        (
            "shipment_id",
            "purchase_order_number",
            "promised_arrival_date",
            "simulated_actual_arrival_date",
            "delay_days",
            "shipment_status",
        ),
        files,
    )

    edge_supplier = suppliers[0]
    edge_sku = skus[1]
    edge_location = locations[0]
    duplicate_shipment_id = "ASN-EDGE-DUP-001"
    duplicate_expected = HORIZON_START + timedelta(days=8)
    shipments.write(
        (
            1,
            duplicate_shipment_id,
            duplicate_shipment_id,
            edge_supplier["supplier_id"],
            edge_supplier["supplier_code"],
            edge_supplier["supplier_site_id"],
            edge_location["location_id"],
            edge_location["location_code"],
            _date(duplicate_expected),
            _datetime(datetime.combine(HORIZON_START, time.min, tzinfo=timezone.utc)),
            "",
            "in_transit",
        )
    )
    duplicate_line = (
        "",
        duplicate_shipment_id,
        1,
        edge_supplier["supplier_id"],
        edge_supplier["supplier_code"],
        "",
        "",
        1,
        edge_sku["sku_code"],
        edge_sku["base_uom_id"],
        edge_sku["base_uom_code"],
        "40",
        "0",
        "1",
        "LOT-EDGE-DUP",
    )
    duplicate_line_rows = [
        shipment_lines.write(duplicate_line),
        shipment_lines.write(duplicate_line),
    ]
    transport_truth.write(
        (
            duplicate_shipment_id,
            "",
            _date(duplicate_expected),
            _date(duplicate_expected + timedelta(days=5)),
            5,
            "in_transit",
        )
    )

    missing_lead_offer = next(
        offer
        for offer in offers_by_sku[1]
        if offer["supplier_id"] == 1
    )
    capacity_offer = missing_lead_offer
    missing_line_row = None
    capacity_line_row = None
    for order_id in range(1, profile.purchase_orders + 1):
        if order_id == 1:
            offer = missing_lead_offer
            location = locations[0]
            order_date = HORIZON_START
            lead_days = None
            requested_date = None
            quantity = Decimal("10")
            received_base = ZERO
            order_status = "released"
            line_status = "open"
        elif order_id == 2:
            offer = capacity_offer
            location = locations[1 % len(locations)]
            order_date = HORIZON_START
            lead_days = _lead_time_days(
                int(offer["supplier_id"]),
                int(offer["sku_id"]),
                int(location["location_id"]),
            )
            requested_date = (
                order_date + timedelta(days=lead_days) if lead_days is not None else None
            )
            quantity = Decimal("100")
            received_base = ZERO
            order_status = "released"
            line_status = "open"
        else:
            sku_id = rng.randint(1, profile.skus)
            offer = rng.choice(offers_by_sku[sku_id])
            location = rng.choice(locations)
            order_date = HORIZON_START + timedelta(days=rng.randint(0, 320))
            lead_days = _lead_time_days(
                int(offer["supplier_id"]),
                sku_id,
                int(location["location_id"]),
            )
            requested_date = (
                order_date + timedelta(days=lead_days) if lead_days is not None else None
            )
            quantity = Decimal(rng.randint(3, 40))
            order_status = rng.choices(
                ("released", "approved", "submitted"),
                weights=(70, 25, 5),
                k=1,
            )[0]
            received_base = ZERO
            line_status = "open"

        if order_id > 2 and lead_days is not None and rng.random() < 0.28:
            shipped_quantity = quantity * Decimal(rng.randint(35, 100)) / Decimal(100)
            if rng.random() < 0.58:
                received_quantity = shipped_quantity * Decimal(
                    rng.randint(20, 100)
                ) / Decimal(100)
            else:
                received_quantity = ZERO
            factor = Decimal(str(offer["factor"]))
            received_base = received_quantity * factor
            if received_base > ZERO:
                line_status = (
                    "received"
                    if received_quantity >= quantity
                    else "partially_received"
                )

            shipment_id = f"ASN-{order_id:08d}"
            shipment_number = shipment_id
            shipment_id_numeric = order_id + 1
            expected_date = requested_date
            delay_days = rng.randint(1, 14) if rng.random() < 0.25 else 0
            actual_arrival = expected_date + timedelta(days=delay_days)
            shipped_at = datetime.combine(
                order_date + timedelta(days=rng.randint(1, 4)),
                time.min,
                tzinfo=timezone.utc,
            )
            if received_quantity >= shipped_quantity:
                shipment_status = "received"
                arrived_at = datetime.combine(actual_arrival, time.min, tzinfo=timezone.utc)
            elif received_quantity > ZERO:
                shipment_status = "partially_received"
                arrived_at = ""
            else:
                shipment_status = "in_transit"
                arrived_at = ""
            shipments.write(
                (
                    shipment_id_numeric,
                    shipment_id,
                    shipment_number,
                    offer["supplier_id"],
                    offer["supplier_code"],
                    offer["supplier_id"],
                    location["location_id"],
                    location["location_code"],
                    _date(expected_date),
                    _datetime(shipped_at),
                    _datetime(arrived_at) if arrived_at else "",
                    shipment_status,
                )
            )
            sku = skus[sku_id]
            shipment_lines.write(
                (
                    shipment_id_numeric,
                    shipment_id,
                    1,
                    offer["supplier_id"],
                    offer["supplier_code"],
                    order_id,
                    f"PO-{order_id:08d}",
                    sku_id,
                    sku["sku_code"],
                    offer["order_uom_id"],
                    offer["order_uom_code"],
                    _format_decimal(shipped_quantity),
                    _format_decimal(received_quantity),
                    _format_decimal(factor),
                    f"LOT-{order_id:08d}-01",
                )
            )
            transport_truth.write(
                (
                    shipment_id,
                    f"PO-{order_id:08d}",
                    _date(expected_date),
                    _date(actual_arrival),
                    delay_days,
                    shipment_status,
                )
            )
        header, line = _po_row(
            order_id,
            offer,
            location,
            order_date,
            requested_date,
            quantity,
            received_base,
            order_status,
            line_status,
            lead_days,
        )
        po_headers.write(header)
        line_row = po_lines.write(line)
        if order_id == 1:
            missing_line_row = line_row
        elif order_id == 2:
            capacity_line_row = line_row

    po_headers.close()
    po_lines.close()
    shipments.close()
    shipment_lines.close()
    transport_truth.close()

    return {
        "duplicate_shipment_id": duplicate_shipment_id,
        "duplicate_line_rows": duplicate_line_rows,
        "missing_lead_po_number": "PO-00000001",
        "missing_lead_line_row": missing_line_row,
        "capacity_po_number": "PO-00000002",
        "capacity_line_row": capacity_line_row,
        "capacity_supplier_sku": {
            "supplier_code": missing_lead_offer["supplier_code"],
            "sku_code": missing_lead_offer["sku_code"],
            "capacity_row": 1,
            "capacity_quantity": _format_decimal(
                capacity_by_offer[(1, 1)]
            ),
            "po_quantity": "100",
            "uom_code": missing_lead_offer["order_uom_code"],
        },
        "missing_lead_key": {
            "supplier_code": missing_lead_offer["supplier_code"],
            "sku_code": missing_lead_offer["sku_code"],
            "location_code": edge_location["location_code"],
        },
    }


def _generate_forecasts(
    root: Path,
    profile: Profile,
    skus: dict[int, dict[str, object]],
    locations: list[dict[str, object]],
    rng: random.Random,
    files: list[dict[str, object]],
) -> int:
    forecast = _CSVWriter(
        root,
        "input",
        "forecast_demand",
        (
            "sku_id",
            "sku_code",
            "location_id",
            "location_code",
            "week_start",
            "forecast_quantity_base",
            "uom_code",
        ),
        files,
        row_limit=MAX_CSV_ROWS_PER_FILE,
    )
    actual = _CSVWriter(
        root,
        "ground_truth",
        "actual_demand",
        (
            "sku_id",
            "sku_code",
            "location_id",
            "location_code",
            "week_start",
            "actual_demand_base",
            "demand_regime",
        ),
        files,
        row_limit=MAX_CSV_ROWS_PER_FILE,
    )
    try:
        for sku_id, sku in skus.items():
            for location in locations:
                location_id = int(location["location_id"])
                location_factor = Decimal(75 + (location_id * 11 % 51)) / Decimal(100)
                for week_index in range(profile.forecast_weeks):
                    week_start = HORIZON_START + timedelta(days=7 * week_index)
                    if (sku_id, location_id, week_index) == (1, 1, 0):
                        forecast_quantity = Decimal("40")
                        is_spike = False
                    else:
                        noise = Decimal(rng.randint(85, 115)) / Decimal(100)
                        seasonal = {
                            8: Decimal("1.3"),
                            25: Decimal("1.2"),
                            46: Decimal("1.45"),
                        }.get(week_index, Decimal("1"))
                        is_spike = (
                            week_index in {8, 25, 46} and sku_id % 3 == 0
                        ) or rng.random() < 0.012
                        spike_factor = Decimal("2.5") if is_spike else Decimal("1")
                        forecast_quantity = (
                            Decimal(int(sku["base_demand"]))
                            * location_factor
                            * noise
                            * seasonal
                            * spike_factor
                        )
                    actual_factor = Decimal(rng.randint(80, 125)) / Decimal(100)
                    actual_quantity = forecast_quantity * actual_factor
                    regime = "spike" if is_spike else "normal"
                    forecast.write(
                        (
                            sku_id,
                            sku["sku_code"],
                            location_id,
                            location["location_code"],
                            _date(week_start),
                            _format_decimal(forecast_quantity),
                            sku["base_uom_code"],
                        )
                    )
                    actual.write(
                        (
                            sku_id,
                            sku["sku_code"],
                            location_id,
                            location["location_code"],
                            _date(week_start),
                            _format_decimal(actual_quantity),
                            regime,
                        )
                    )
    finally:
        forecast.close()
        actual.close()
    return forecast.total_rows


def _edge_case_documents(
    metadata: dict[str, object],
    files: list[dict[str, object]],
) -> None:
    cases = [
        {
            "case_id": "duplicate_inbound_inventory",
            "title": "Repeated inbound shipment line can be counted twice",
            "affected_records": [
                {
                    "file": "input/inbound_shipment_lines.csv",
                    "data_row": row_number,
                    "business_key": f"{metadata['duplicate_shipment_id']}|1",
                }
                for row_number in metadata["duplicate_line_rows"]
            ],
            "expected_rule": "DUPLICATE_INBOUND",
            "expected_outcome": (
                "Staging should flag both rows as duplicate business identifiers. "
                "Do not count both quantities; the current validator warns that "
                "operational quantity reconciliation is not implemented."
            ),
        },
        {
            "case_id": "unapproved_substitute",
            "title": "Substitution is active but has no effective approval",
            "affected_records": [
                {
                    "file": "input/sku_substitution.csv",
                    "data_row": metadata["substitution_row"],
                    "business_key": "SKU-000001 -> SKU-000002",
                }
            ],
            "expected_rule": "UNAPPROVED_SUBSTITUTE",
            "expected_outcome": (
                "Reject the substitution for the requested date and never recommend "
                "SKU-000002 as an approved substitute."
            ),
        },
        {
            "case_id": "supplier_capacity_exceeded",
            "title": "Released order quantity exceeds the supplier's SKU capacity",
            "affected_records": [
                {
                    "file": "input/supplier_capacity.csv",
                    "data_row": metadata["capacity_supplier_sku"]["capacity_row"],
                    "business_key": (
                        metadata["capacity_supplier_sku"]["supplier_code"]
                        + "|"
                        + metadata["capacity_supplier_sku"]["sku_code"]
                    ),
                    "capacity_quantity": metadata["capacity_supplier_sku"]["capacity_quantity"],
                },
                {
                    "file": "input/purchase_order_lines.csv",
                    "data_row": metadata["capacity_line_row"],
                    "business_key": metadata["capacity_po_number"],
                    "ordered_quantity": metadata["capacity_supplier_sku"]["po_quantity"],
                    "uom_code": metadata["capacity_supplier_sku"]["uom_code"],
                },
            ],
            "expected_rule": "CAPACITY_EXCEEDED",
            "expected_outcome": (
                "The PO is over the recorded capacity. The current ingestion validator "
                "reports capacity verification as unsupported; planning must not treat "
                "the excess as validated firm supply."
            ),
        },
        {
            "case_id": "shortage_missing_lead_time",
            "title": "Demand exceeds zero opening inventory with no supplier lead-time row",
            "affected_records": [
                {
                    "file": "input/purchase_order_lines.csv",
                    "data_row": metadata["missing_lead_line_row"],
                    "business_key": metadata["missing_lead_po_number"],
                },
                {
                    "file": metadata["inventory_file"],
                    "data_row": metadata["inventory_shortage_row"],
                    "business_key": "SKU-000001|LOC-001",
                },
                {
                    "file": "input/forecast_demand_0001.csv",
                    "data_row": 1,
                    "business_key": "SKU-000001|LOC-001|2026-01-05",
                },
            ],
            "missing_lead_time_key": metadata["missing_lead_key"],
            "expected_rule": "MISSING_LEAD_TIME",
            "expected_outcome": (
                "No arrival date can be inferred for PO-00000001. Keep the 40 base-unit "
                "week-one demand shortage visible until supported supply timing exists."
            ),
        },
        {
            "case_id": "uom_mismatch",
            "title": "Inventory quantity uses a unit without a SKU conversion",
            "affected_records": [
                {
                    "file": metadata["inventory_file"],
                    "data_row": metadata["uom_mismatch_row"],
                    "business_key": "SKU-000001|LOC-001",
                    "quantity": "10",
                    "uom_code": "KG",
                }
            ],
            "expected_rule": "UOM_MISMATCH",
            "expected_outcome": (
                "Reject the row because SKU-000001 is EA-based and has no KG conversion; "
                "do not infer a base-unit quantity."
            ),
        },
    ]
    metadata["edge_cases"] = cases

    _write_text(
        root=metadata["root"],
        relative_path="ground_truth/edge_cases.json",
        content=json.dumps(
            {
                "generator": GENERATOR_ID,
                "profile": metadata["profile"],
                "seed": metadata["seed"],
                "cases": cases,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        manifest_files=files,
        classification="ground_truth",
        row_count=len(cases),
    )
    _write_csv(
        metadata["root"],
        "ground_truth",
        "edge_case_expected_outcomes.csv",
        ("case_id", "expected_rule", "expected_outcome"),
        (
            (case["case_id"], case["expected_rule"], case["expected_outcome"])
            for case in cases
        ),
        files,
    )


DATA_DICTIONARY = """# Synthetic supply-planning data dictionary

All IDs are deterministic synthetic identifiers. Quantities use the row's
`uom_code`; fields ending in `_base` are expressed in the SKU's base unit.
Date ranges are ISO-8601. `input/` files are candidate planning/ingestion
inputs. `ground_truth/` files are evaluation-only and must not be loaded as
planning features.

| File(s) | Classification | Important columns, units, and relationships |
|---|---|---|
| `input/unit_of_measure.csv` | Input | `uom_id`, code, physical dimension, decimal places. |
| `input/sku.csv` | Input | `sku_id`/`sku_code`, category, base UOM; referenced by all item-level files. |
| `input/sku_uom.csv` | Input | Per-SKU UOM conversions; `to_base_factor` converts one `uom_code` unit into base units. |
| `input/location.csv` | Input | `location_id`/`location_code`, type, country, time zone. |
| `input/supplier.csv`, `input/supplier_site.csv` | Input | Supplier identity and one ship-from site per supplier. |
| `input/supplier_sku.csv` | Input | Eligible supplier-SKU offers, order UOM, MOQ, order multiple, cost, currency, and effective dates. |
| `input/supplier_capacity.csv` | Input | Supplier-SKU capacity and reservations in `capacity_uom_code`, with effective dates. |
| `input/supplier_lead_time.csv` (sharded when large) | Input | Supplier/site/SKU/location lead time in days; shards contain at most 50,000 rows. SKU 1 to LOC-001 is intentionally absent. |
| `input/inventory.csv` (sharded when large), `input/inventory_snapshot.csv` | Input | Snapshot balances by SKU/location; shards contain at most 50,000 rows. `quantity_on_hand_base` is base units. One deliberately invalid UOM row is included. |
| `input/purchase_orders.csv`, `input/purchase_order_lines.csv` | Input | PO headers and SKU lines; ordered quantity uses `uom_code`, received quantity is base units. |
| `input/inbound_shipments.csv`, `input/inbound_shipment_lines.csv` | Input | Expected shipment dates and shipped/received quantities; line rows link to PO lines where applicable. A duplicate edge pair is intentional. |
| `input/sku_substitution.csv`, `input/substitution_approval.csv` | Input | Directed substitution proposals and pending approval records. The edge substitution has no approval. |
| `input/forecast_demand_NNNN.csv` | Input | Weekly `forecast_quantity_base`, sharded at no more than 50,000 data rows per file. |
| `ground_truth/actual_demand_NNNN.csv` | Ground truth | Matching weekly keys and simulated actual base-unit demand plus demand regime label; never use as a planning feature. |
| `ground_truth/transport_outcomes.csv` | Ground truth | Simulated actual arrival and delay labels; input expected dates remain separate. |
| `ground_truth/edge_cases.json`, `ground_truth/edge_case_expected_outcomes.csv` | Ground truth | Edge-case row references and expected validation/planning outcomes. |
| `data_dictionary.md`, `manifest.json` | Documentation/metadata | File descriptions, row counts, profile/seed, and SHA-256 for every generated file except the manifest itself. |

Forecast and actual-demand shards share SKU/location/week keys. The actual
demand and transport outcomes are labels only. The generator does not import
any file into the application or database.
"""


def _generate_in_staging(
    root: Path,
    profile_name: str,
    profile: Profile,
    seed: int,
) -> dict[str, object]:
    rng = random.Random(seed)
    files: list[dict[str, object]] = []
    skus, locations, suppliers, offers_by_sku, capacity_by_offer = _catalog(
        root,
        profile,
        rng,
        files,
    )
    inventory_count, inventory_checksum, uom_mismatch_row, inventory_file = _inventory(
        root,
        profile,
        skus,
        locations,
        files,
    )
    _write_inventory_snapshot(root, inventory_checksum, files)
    procurement = _generate_procurement(
        root,
        profile,
        skus,
        locations,
        suppliers,
        offers_by_sku,
        capacity_by_offer,
        rng,
        files,
    )
    forecast_count = _generate_forecasts(
        root,
        profile,
        skus,
        locations,
        rng,
        files,
    )

    edge_metadata: dict[str, object] = {
        **procurement,
        "root": root,
        "profile": profile_name,
        "seed": seed,
        "substitution_row": 1,
        "inventory_shortage_row": 2,
        "uom_mismatch_row": uom_mismatch_row,
        "inventory_file": inventory_file,
    }
    _edge_case_documents(edge_metadata, files)
    _write_text(
        root,
        "data_dictionary.md",
        DATA_DICTIONARY,
        files,
        classification="documentation",
    )
    counts = {
        "skus": profile.skus,
        "locations": profile.locations,
        "suppliers": profile.suppliers,
        "forecast_weeks": profile.forecast_weeks,
        "forecast_demand_rows": forecast_count,
        "actual_demand_rows": forecast_count,
        "inventory_positions": inventory_count - 1,
        "inventory_input_rows": inventory_count,
        "purchase_orders": profile.purchase_orders,
        "purchase_order_lines": profile.purchase_orders,
        "supplier_sku_eligibilities": profile.skus * 2,
        "supplier_capacity_records": profile.skus * 2,
        "supplier_lead_time_records": profile.skus * 2 * profile.locations - 1,
    }
    manifest: dict[str, object] = {
        "generator": GENERATOR_ID,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "profile": profile_name,
        "counts": counts,
        "edge_cases_manifest": "ground_truth/edge_cases.json",
        "files": sorted(files, key=lambda item: str(item["path"])),
    }
    manifest_path = root / "manifest.json"
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest


def generate_dataset(
    output_dir: str | Path,
    profile: str = "starter",
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    """Generate a profile under output_dir without replacing unowned data."""
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile {profile!r}; choose from {', '.join(PROFILES)}.")
    if not isinstance(seed, int):
        raise ValueError("seed must be an integer.")

    requested_root = Path(output_dir).expanduser()
    if requested_root.is_symlink():
        raise ValueError("Output directory may not be a symlink.")
    root = requested_root.absolute()
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True)
    old_manifest = _inspect_existing_output(root)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.synthetic-", dir=str(parent))
    )
    try:
        manifest = _generate_in_staging(staging, profile, PROFILES[profile], seed)
        backup = None
        if root.exists():
            backup = parent / f".{root.name}.previous-{uuid4().hex}"
            root.rename(backup)
        try:
            staging.rename(root)
        except OSError:
            if backup is not None and backup.exists():
                backup.rename(root)
            raise
        if backup is not None:
            _remove_verified_output(backup, old_manifest)
        return manifest
    except Exception:
        if staging.exists():
            _remove_staging_output(staging)
        raise


def _default_output_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="starter")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=_default_output_dir())
    arguments = parser.parse_args(argv)
    try:
        manifest = generate_dataset(
            arguments.output_dir,
            profile=arguments.profile,
            seed=arguments.seed,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "output_dir": str(arguments.output_dir.absolute()),
                "profile": manifest["profile"],
                "seed": manifest["seed"],
                "counts": manifest["counts"],
                "file_count": len(manifest["files"]),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
