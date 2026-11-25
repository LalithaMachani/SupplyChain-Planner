# Validation report

## Recorded verification results

The following results were recorded during the development session on 24 November 2026. They are historical results, not a claim that this clean archive was freshly executed in the current environment.

| Check | Recorded result |
|---|---|
| Backend isolated tests | 42 passed |
| SQLite migration tests | 3 passed |
| Frontend tests | 2 passed |
| Frontend lint | Passed with no lint errors/warnings |
| Frontend production build | Passed; Vite reported a non-blocking large-chunk advisory |
| Runtime HTTP smoke test | Frontend/API probe passed; API health endpoint returned connected |
| Demo planning run #1 | Completed, 96 SKU-location pairs, 4,992 weekly forecast/PAB rows |
| Demo planning run #2 | Completed, same 96 pairs and 52-week horizon, configured severity thresholds |
| Run #2 shortage severity counts | Critical 1,178; High 1,066; Medium 1,264; Low 404; total 3,912 |

## Demo edge-case handling

| Edge case | Observed handling | Status |
|---|---|---|
| Duplicate inbound inventory | Both raw duplicate rows are staged/flagged; one canonical operational row imported | Demonstrated |
| Unapproved substitute | Approval remains pending; not recommended as approved | Demonstrated |
| Supplier capacity exceeded | Over-capacity PO is not allocated as validated firm supply; full capacity validation is unsupported | Partial |
| Missing lead time | No arrival date inferred; shortage remains visible in PAB | Demonstrated |
| UOM mismatch | Incompatible inventory row rejected; no unsupported conversion inferred | Demonstrated |

## Re-run checks before a fresh environment demo

1. Configure a dedicated MySQL development database using `backend/.env`.
2. Apply Alembic migrations.
3. Start the API and verify `/api/v1/health/database`.
4. Start the Vite frontend and verify API connectivity.
5. Run backend tests and frontend lint/tests/build.
6. If running MySQL integration tests, use a dedicated disposable test database and inspect the test configuration first.

## Interpretation

The recorded results support a functional academic prototype. They do not establish production readiness, complete coverage of every original assignment requirement, or validation at the full synthetic-data scale.
