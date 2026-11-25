"""HTTP endpoints for planning runs and their frozen calculation results."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from ....db.database import get_db
from ....models.planning import (
    PABWeek,
    PlanningItem,
    PlanningRun,
    PlanningRunFinding,
    ShortageCause,
)
from ....schemas.planning import (
    PABWeekPage,
    PlanningFindingPage,
    PlanningRunCreate,
    PlanningRunPage,
    PlanningRunRead,
    PlanningRunSummary,
    ShortagePage,
)
from ....schemas.mitigation import MitigationPage
from ....services.mitigation_service import get_mitigation_recommendations
from ....services.planning_service import (
    PlanningCalculationError,
    PlanningInputError,
    PlanningNotFoundError,
    calculate_planning_run,
    create_planning_run,
)

router = APIRouter()


def _get_run(db: Session, run_id: int) -> PlanningRun:
    run = db.get(PlanningRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Planning run not found.")
    return run


@router.post("/runs", response_model=PlanningRunRead, status_code=201)
def create_run(
    request: PlanningRunCreate,
    db: Session = Depends(get_db),
) -> PlanningRunRead:
    try:
        run = create_planning_run(db, request)
    except PlanningInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return PlanningRunRead(
        **PlanningRunSummary.model_validate(run).model_dump(),
        items=[
            {"sku_id": item.sku_id, "location_id": item.location_id}
            for item in sorted(request.items, key=lambda value: (value.sku_id, value.location_id))
        ],
    )


@router.get("/runs", response_model=PlanningRunPage)
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> PlanningRunPage:
    runs = db.scalars(
        select(PlanningRun)
        .order_by(PlanningRun.created_at.desc(), PlanningRun.planning_run_id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = db.scalar(select(func.count()).select_from(PlanningRun)) or 0
    return PlanningRunPage(
        items=[PlanningRunSummary.model_validate(run) for run in runs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=PlanningRunRead)
def get_run(
    run_id: Annotated[int, Path(gt=0)],
    db: Session = Depends(get_db),
) -> PlanningRunRead:
    run = _get_run(db, run_id)
    items = db.scalars(
        select(PlanningItem)
        .where(PlanningItem.planning_run_id == run_id)
        .order_by(PlanningItem.sku_id, PlanningItem.location_id)
    ).all()
    return PlanningRunRead(
        **PlanningRunSummary.model_validate(run).model_dump(),
        items=items,
    )


@router.post("/runs/{run_id}/calculate", response_model=PlanningRunRead)
def calculate_run(
    run_id: Annotated[int, Path(gt=0)],
    db: Session = Depends(get_db),
) -> PlanningRunRead:
    try:
        run = calculate_planning_run(db, run_id)
    except PlanningNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except PlanningInputError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except PlanningCalculationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    items = db.scalars(
        select(PlanningItem)
        .where(PlanningItem.planning_run_id == run_id)
        .order_by(PlanningItem.sku_id, PlanningItem.location_id)
    ).all()
    return PlanningRunRead(
        **PlanningRunSummary.model_validate(run).model_dump(),
        items=items,
    )


@router.get("/runs/{run_id}/pab", response_model=PABWeekPage)
def list_pab(
    run_id: Annotated[int, Path(gt=0)],
    sku_id: int | None = Query(default=None, gt=0),
    location_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> PABWeekPage:
    _get_run(db, run_id)
    query = select(PABWeek).where(PABWeek.planning_run_id == run_id)
    count_query = select(func.count()).select_from(PABWeek).where(PABWeek.planning_run_id == run_id)
    if sku_id is not None:
        query = query.where(PABWeek.sku_id == sku_id)
        count_query = count_query.where(PABWeek.sku_id == sku_id)
    if location_id is not None:
        query = query.where(PABWeek.location_id == location_id)
        count_query = count_query.where(PABWeek.location_id == location_id)
    rows = db.scalars(
        query.order_by(PABWeek.week_start, PABWeek.sku_id, PABWeek.location_id)
        .limit(limit)
        .offset(offset)
    ).all()
    total = db.scalar(count_query) or 0
    return PABWeekPage(items=rows, total=total)


@router.get("/runs/{run_id}/shortages", response_model=ShortagePage)
def list_shortages(
    run_id: Annotated[int, Path(gt=0)],
    sku_id: int | None = Query(default=None, gt=0),
    location_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ShortagePage:
    _get_run(db, run_id)
    query = select(PABWeek).where(
        PABWeek.planning_run_id == run_id,
        PABWeek.shortage_quantity_base > 0,
    )
    count_query = select(func.count()).select_from(PABWeek).where(
        PABWeek.planning_run_id == run_id,
        PABWeek.shortage_quantity_base > 0,
    )
    if sku_id is not None:
        query = query.where(PABWeek.sku_id == sku_id)
        count_query = count_query.where(PABWeek.sku_id == sku_id)
    if location_id is not None:
        query = query.where(PABWeek.location_id == location_id)
        count_query = count_query.where(PABWeek.location_id == location_id)
    rows = db.scalars(
        query.order_by(PABWeek.week_start, PABWeek.sku_id, PABWeek.location_id)
        .limit(limit)
        .offset(offset)
    ).all()
    total = db.scalar(count_query) or 0
    keys = [(row.planning_run_id, row.sku_id, row.location_id, row.week_start) for row in rows]
    causes_by_key = {}
    if keys:
        causes = db.scalars(
            select(ShortageCause).where(
                tuple_(
                    ShortageCause.planning_run_id,
                    ShortageCause.sku_id,
                    ShortageCause.location_id,
                    ShortageCause.week_start,
                ).in_(keys)
            )
        ).all()
        for cause in causes:
            causes_by_key.setdefault(
                (
                    cause.planning_run_id,
                    cause.sku_id,
                    cause.location_id,
                    cause.week_start,
                ),
                [],
            ).append(cause)
    return ShortagePage(
        items=[
            {
                "planning_run_id": row.planning_run_id,
                "sku_id": row.sku_id,
                "location_id": row.location_id,
                "week_start": row.week_start,
                "shortage_quantity_base": row.shortage_quantity_base,
                "severity": row.severity,
                "causes": causes_by_key.get(
                    (row.planning_run_id, row.sku_id, row.location_id, row.week_start),
                    [],
                ),
            }
            for row in rows
        ],
        total=total,
    )


@router.get("/runs/{run_id}/findings", response_model=PlanningFindingPage)
def list_findings(
    run_id: Annotated[int, Path(gt=0)],
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> PlanningFindingPage:
    _get_run(db, run_id)
    rows = db.scalars(
        select(PlanningRunFinding)
        .where(PlanningRunFinding.planning_run_id == run_id)
        .order_by(
            PlanningRunFinding.severity,
            PlanningRunFinding.planning_run_finding_id,
        )
        .limit(limit)
        .offset(offset)
    ).all()
    total = db.scalar(
        select(func.count())
        .select_from(PlanningRunFinding)
        .where(PlanningRunFinding.planning_run_id == run_id)
    ) or 0
    return PlanningFindingPage(items=rows, total=total)


@router.get("/runs/{run_id}/mitigations", response_model=MitigationPage)
def list_mitigations(
    run_id: Annotated[int, Path(gt=0)],
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> MitigationPage:
    _get_run(db, run_id)
    try:
        items = get_mitigation_recommendations(db, run_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return MitigationPage(
        items=items[offset : offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
    )


@router.get(
    "/runs/{run_id}/shortages/{sku_id}/{location_id}/{week_start}/mitigations",
    response_model=MitigationPage,
)
def list_shortage_mitigations(
    run_id: Annotated[int, Path(gt=0)],
    sku_id: Annotated[int, Path(gt=0)],
    location_id: Annotated[int, Path(gt=0)],
    week_start: date,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> MitigationPage:
    _get_run(db, run_id)
    shortage = db.get(PABWeek, (run_id, sku_id, location_id, week_start))
    if shortage is None or shortage.shortage_quantity_base <= 0:
        raise HTTPException(status_code=404, detail="Planning shortage not found.")
    items = get_mitigation_recommendations(
        db,
        run_id,
        shortage_filter=(sku_id, location_id, week_start),
    )
    return MitigationPage(
        items=items[offset : offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
    )
