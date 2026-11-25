# SupplyChain Planner

A full-stack supply and inventory planning prototype for exploring forecast demand, inventory position, inbound purchase orders, shortages, and evidence-based mitigation recommendations.

> **Project status:** Working academic prototype. This repository documents the implemented scope and known limitations rather than presenting every enterprise planning capability as production-ready.

## What it includes

- **Planning runs:** create and calculate run-scoped planning scenarios over selected SKU-location pairs and a weekly horizon.
- **Demand and inventory planning:** weekly projected available balance (PAB) calculations using opening inventory, supported firm receipts, and forecast demand.
- **Shortage analysis:** shortage quantities, cause records, severity bands, and drill-down details.
- **Mitigation recommendations:** candidate expedite, inventory transfer, approved-substitute, and alternative-supplier options where supported by available evidence. Recommendations are advisory/read-only.
- **Data ingestion and validation:** batch/staging workflow with validation findings and source-level traceability.
- **Synthetic data:** deterministic starter/full data generator and a 52-week demonstration dataset.
- **Frontend:** React + Vite interface with charts and API-backed views.
- **Backend:** FastAPI, SQLAlchemy ORM, Alembic migrations, and MySQL configuration.

## Technology stack

| Layer | Implementation |
|---|---|
| Frontend | React, Vite, Recharts |
| API | Python, FastAPI, Pydantic |
| Persistence | MySQL, SQLAlchemy |
| Schema migrations | Alembic |
| Data generation | Python, Pandas/NumPy where used |
| Tests | Pytest, Vitest |

**Assignment note:** The implementation uses React/FastAPI/MySQL rather than the Streamlit/SQLite combination mentioned in the original brief. This is an implementation deviation and should be disclosed if the evaluator expects the exact requested stack.

## Repository layout

```text
SupplyChain-Planner/
├── backend/
│   ├── app/                 # API, models, schemas, services
│   ├── alembic/             # Database migrations
│   ├── scripts/             # Synthetic generator and demo importer
│   ├── tests/               # Backend and generator tests
│   ├── .env.example         # Configuration template (no real secrets)
│   └── requirements.txt
├── frontend/
│   ├── src/                 # React application
│   ├── package.json
│   └── package-lock.json
├── data/
│   ├── input/               # Starter input dataset
│   ├── demo52/              # 52-week demo input and manifest
│   └── ground_truth/        # Expected outcomes and edge-case definitions
├── docs/
└── .gitignore
```

## Run locally

### Prerequisites

- Python 3.12 or a compatible version supported by the installed dependencies.
- Node.js and npm.
- MySQL Server.

### 1. Configure the backend

Open a terminal in `backend`:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `backend/.env` with your own local MySQL connection values. Do not commit `.env`. Create the configured database in MySQL before starting the API.

### 2. Apply migrations

From the `backend` directory:

```powershell
alembic upgrade head
```

### 3. Start the API

From the `backend` directory:

```powershell
python -m uvicorn app.main:app --reload
```

- API: `http://127.0.0.1:8000`
- Interactive API docs: `http://127.0.0.1:8000/docs`
- Database health endpoint: `http://127.0.0.1:8000/api/v1/health/database`

### 4. Start the frontend

Open a second terminal in `frontend`:

```powershell
npm ci
npm run dev
```

Open the local URL printed by Vite (normally `http://localhost:5173`).

## Demo data

The repository contains:
- A starter dataset under `data/input`.
- A 52-week demonstration dataset under `data/demo52`.
- Ground-truth and expected-outcome files under `data/ground_truth` and `data/demo52/ground_truth`.

The generator supports `starter` and `full` profiles. Example command from `backend`:

```powershell
python scripts/generate_synthetic_data.py --profile starter --seed 20261022
```

The full profile is configured for 3,000 SKUs, 50 locations, 500 suppliers, 52 forecast weeks, and 10,000 purchase orders. It is large and was **not generated as part of the included demo**. Use a separate output directory if generating it.

The demo importer is intended for a clean, prepared database and writes operational records. Review its source and confirm the target database is disposable before running it. Do not rerun it against the already-populated demo database.

## Tests and quality checks

From `backend`:

```powershell
python -m pytest -q
```

Some integration tests require a separately configured MySQL test database and are not covered by an isolated SQLite run. Do not point tests at a database containing data you need to preserve.

From `frontend`:

```powershell
npm ci
npm run lint
npm test
npm run build
```

## Planning interpretation and safeguards

- PAB is calculated by week from the opening balance, supported firm receipts, and forecast demand.
- Unconfirmed or unsupported supply is not silently treated as firm supply.
- A mitigation recommendation is not an execution instruction. Planner review is required.
- Severity in the current demo is based on configured absolute shortage thresholds in base units: medium ≥ 100, high ≥ 500, critical ≥ 1,000; lower positive shortages are low. These are demonstration thresholds, not universal business policy.

## Known limitations

See [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) and [`docs/VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md). In particular, the current demo does not establish full production readiness, full-scale data generation, complete manufacturing/BOM planning, or comprehensive supplier-capacity validation.

## Security

- Never commit `.env`, credentials, database dumps, or private customer data.
- `.env.example` contains placeholder local-development values; replace them before use.
- Use a dedicated development database, not a production database.
