# Known limitations and scope boundaries

This project is an academic planning prototype. The following limitations should be disclosed in a demo or evaluation.

1. **Technology-stack deviation:** the delivered UI/API/database are React + Vite, FastAPI, and MySQL. The original brief specified Streamlit and SQLite. Do not describe the implementation as using the exact requested stack.
2. **Full-scale data:** the generator defines a full profile (3,000 SKUs, 50 locations, 500 suppliers, 52 weeks, 10,000 purchase orders), but the full dataset has not been generated. The included `demo52` dataset is a smaller 24-SKU, 4-location, 8-supplier, 52-week scenario.
3. **Supplier capacity validation:** capacity data and over-capacity scenarios exist, but complete reservation-level capacity validation is not implemented. Aggregate reserved counters are preserved without fabricated reservation-to-PO links. The capacity edge case is therefore not fully closed end-to-end.
4. **PO location allocation:** the source PO lines contain location identifiers, but the current purchase-order line model does not retain a complete planning-location allocation. Such PO supply is not silently allocated to a SKU-location PAB.
5. **Partial receipts:** six partially received PO/shipment pairs in the demo source lack actual arrival timestamps. They are excluded from operational import rather than assigning invented dates.
6. **Duplicate inbound records:** the duplicate ASN line is represented in staging/validation; only one canonical operational line is imported. This prevents double counting in the demo import.
7. **Substitution approval:** an unapproved substitute remains pending and is not recommended as an approved substitute.
8. **Missing lead time:** supply with no supported lead-time mapping is not assigned an invented arrival date; the associated shortage remains visible.
9. **UOM mismatch:** the incompatible KG inventory row for an EA-based SKU is rejected rather than converted without an approved conversion.
10. **Manufacturing and BOM planning:** production planning, complete BOM explosion, and capacity-constrained manufacturing execution are not fully implemented.
11. **Mitigation evidence:** some expedite and transfer options are returned as candidates but are marked insufficient evidence when arrival, route, or cost information is missing. They are read-only recommendations.
12. **Test coverage:** the reported isolated backend test run does not replace running all MySQL integration tests against a dedicated test database. A frontend bundle-size advisory warning was observed during the build.
13. **Severity policy:** the demo uses fixed absolute base-unit thresholds. Production severity should also consider demand scale, item criticality, customer priority, and business service targets.
