# Synthetic supply-planning data dictionary

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
