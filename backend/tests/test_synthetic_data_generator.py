import csv
import hashlib
import json

import pytest

from scripts.generate_synthetic_data import (
    DEFAULT_SEED,
    PROFILES,
    _CSVWriter,
    generate_dataset,
)


def _read_csv(path):
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


def test_starter_profile_files_columns_and_entity_counts(tmp_path):
    output = tmp_path / "data"
    manifest = generate_dataset(output, profile="starter")
    files = {entry["path"]: entry for entry in manifest["files"]}

    required_paths = {
        "input/sku.csv",
        "input/location.csv",
        "input/supplier.csv",
        "input/supplier_sku.csv",
        "input/supplier_capacity.csv",
        "input/supplier_lead_time.csv",
        "input/inventory.csv",
        "input/purchase_orders.csv",
        "input/purchase_order_lines.csv",
        "input/inbound_shipments.csv",
        "input/inbound_shipment_lines.csv",
        "input/sku_substitution.csv",
        "input/forecast_demand_0001.csv",
        "ground_truth/actual_demand_0001.csv",
        "ground_truth/edge_cases.json",
        "ground_truth/edge_case_expected_outcomes.csv",
        "ground_truth/transport_outcomes.csv",
        "data_dictionary.md",
    }
    assert required_paths <= files.keys()
    assert manifest["seed"] == DEFAULT_SEED
    assert manifest["profile"] == "starter"
    assert manifest["counts"] == {
        "skus": 24,
        "locations": 4,
        "suppliers": 8,
        "forecast_weeks": 13,
        "forecast_demand_rows": 24 * 4 * 13,
        "actual_demand_rows": 24 * 4 * 13,
        "inventory_positions": 24 * 4,
        "inventory_input_rows": 24 * 4 + 1,
        "purchase_orders": 40,
        "purchase_order_lines": 40,
        "supplier_sku_eligibilities": 24 * 2,
        "supplier_capacity_records": 24 * 2,
        "supplier_lead_time_records": 24 * 2 * 4 - 1,
    }

    expected_columns = {
        "input/sku.csv": {"sku_id", "sku_code", "base_uom_id", "status"},
        "input/supplier_sku.csv": {"supplier_id", "sku_id", "order_uom_id", "unit_cost"},
        "input/inventory.csv": {"sku_code", "location_code", "quantity_on_hand_base", "uom_code"},
        "input/purchase_order_lines.csv": {
            "purchase_order_id",
            "sku_code",
            "quantity_ordered",
            "order_uom_to_base_factor",
            "location_code",
        },
        "input/forecast_demand_0001.csv": {
            "sku_code",
            "location_code",
            "week_start",
            "forecast_quantity_base",
        },
    }
    for relative_path, required in expected_columns.items():
        columns, _ = _read_csv(output / relative_path)
        assert required <= set(columns)

    assert files["input/sku.csv"]["row_count"] == PROFILES["starter"].skus
    assert files["input/location.csv"]["row_count"] == PROFILES["starter"].locations
    assert files["input/supplier.csv"]["row_count"] == PROFILES["starter"].suppliers
    assert files["input/purchase_orders.csv"]["row_count"] == PROFILES["starter"].purchase_orders
    assert files["input/forecast_demand_0001.csv"]["row_count"] == 24 * 4 * 13


def test_full_profile_declares_assignment_scale():
    full = PROFILES["full"]

    assert (full.skus, full.locations, full.suppliers) == (3000, 50, 500)
    assert (full.forecast_weeks, full.purchase_orders) == (52, 10_000)
    assert full.skus * full.locations * full.forecast_weeks == 7_800_000


def test_large_csv_writer_shards_without_losing_rows(tmp_path):
    files = []
    writer = _CSVWriter(
        tmp_path,
        "input",
        "sample",
        ("row_id",),
        files,
        row_limit=2,
    )
    for row_id in range(5):
        writer.write((row_id,))
    writer.close()

    assert [entry["path"] for entry in files] == [
        "input/sample_0001.csv",
        "input/sample_0002.csv",
        "input/sample_0003.csv",
    ]
    assert [entry["row_count"] for entry in files] == [2, 2, 1]


def test_all_labelled_edge_cases_reference_generated_records(tmp_path):
    output = tmp_path / "data"
    manifest = generate_dataset(output, profile="starter")
    edge_path = output / manifest["edge_cases_manifest"]
    edge_manifest = json.loads(edge_path.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in edge_manifest["cases"]}

    assert set(cases) == {
        "duplicate_inbound_inventory",
        "unapproved_substitute",
        "supplier_capacity_exceeded",
        "shortage_missing_lead_time",
        "uom_mismatch",
    }
    for case in cases.values():
        for record in case["affected_records"]:
            file_path = output / record["file"]
            assert file_path.is_file()
            _, rows = _read_csv(file_path)
            assert 1 <= record["data_row"] <= len(rows)

    duplicate_rows = cases["duplicate_inbound_inventory"]["affected_records"]
    _, shipment_lines = _read_csv(output / "input/inbound_shipment_lines.csv")
    first = shipment_lines[duplicate_rows[0]["data_row"] - 1]
    second = shipment_lines[duplicate_rows[1]["data_row"] - 1]
    assert first["shipment_id"] == second["shipment_id"]
    assert first["line_number"] == second["line_number"]

    assert cases["shortage_missing_lead_time"]["missing_lead_time_key"] == {
        "supplier_code": "SUP-0001",
        "sku_code": "SKU-000001",
        "location_code": "LOC-001",
    }


def test_same_seed_is_reproducible_and_rerun_replaces_not_appends(tmp_path):
    output = tmp_path / "data"
    first = generate_dataset(output, profile="starter", seed=71)
    first_manifest_bytes = (output / "manifest.json").read_bytes()
    first_checksums = {
        entry["path"]: entry["sha256"] for entry in first["files"]
    }

    second = generate_dataset(output, profile="starter", seed=71)

    assert (output / "manifest.json").read_bytes() == first_manifest_bytes
    assert {entry["path"]: entry["sha256"] for entry in second["files"]} == first_checksums
    for entry in second["files"]:
        assert entry["sha256"] == hashlib.sha256(
            (output / entry["path"]).read_bytes()
        ).hexdigest()


def test_ground_truth_is_listed_separately_from_planning_inputs(tmp_path):
    output = tmp_path / "data"
    manifest = generate_dataset(output, profile="starter")
    input_files = [
        item for item in manifest["files"] if item["classification"] == "input"
    ]
    ground_truth_files = [
        item for item in manifest["files"] if item["classification"] == "ground_truth"
    ]

    assert input_files
    assert ground_truth_files
    assert all(item["path"].startswith("input/") for item in input_files)
    assert all(item["path"].startswith("ground_truth/") for item in ground_truth_files)
    assert not {item["path"] for item in input_files} & {
        item["path"] for item in ground_truth_files
    }
    assert any("actual_demand" in item["path"] for item in ground_truth_files)
    assert any("transport_outcomes" in item["path"] for item in ground_truth_files)


def test_unowned_existing_files_are_never_overwritten(tmp_path):
    output = tmp_path / "data"
    output.mkdir()
    user_file = output / "keep.txt"
    user_file.write_text("user data", encoding="utf-8")

    with pytest.raises(FileExistsError, match="not generator-owned"):
        generate_dataset(output, profile="starter")

    assert user_file.read_text(encoding="utf-8") == "user data"
