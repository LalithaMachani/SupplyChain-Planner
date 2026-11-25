
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .api.v1.endpoints.ingestion import router as ingestion_router
from .api.v1.endpoints.planning import router as planning_router
from .core.config import settings
from .db.database import get_db

app = FastAPI(
    title="SupplyChain Planner API",
    description="Demand, Inventory and Supply Planning System",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingestion_router, prefix="/api/v1/ingestion", tags=["ingestion"])
app.include_router(planning_router, prefix="/api/v1/planning", tags=["planning"])


@app.get("/")
def home():
    return {
        "message": "Welcome to SupplyChain Planner API",
        "status": "running"
    }

@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


@app.get("/api/v1/health/database")
def database_health(db: Session = Depends(get_db)):
    """Check database connectivity without creating or changing any data."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail={"status": "unavailable"},
        ) from exc
    return {"status": "connected"}
