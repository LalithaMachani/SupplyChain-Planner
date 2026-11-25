"""Pydantic schemas for reproducible supply-planning runs."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanningScopeItem(BaseModel):
    sku_id: int = Field(gt=0)
    location_id: int = Field(gt=0)


class PlanningRunCreate(BaseModel):
    scenario_key: str = Field(min_length=1, max_length=64)
    horizon_start_week: date
    items: list[PlanningScopeItem] = Field(min_length=1)
    inventory_snapshot_id: int | None = Field(default=None, gt=0)
    severity_thresholds: dict[str, Decimal] = Field(
        default_factory=dict,
        description=(
            "Optional minimum shortage quantities in base units for medium, high, "
            "and critical severity. A shortage at or above a configured threshold "
            "receives that severity; unspecified thresholds are not assumed and "
            "shortages default to low severity."
        ),
    )

    @model_validator(mode="after")
    def validate_request(self):
        if self.horizon_start_week.weekday() != 0:
            raise ValueError("horizon_start_week must be a Monday.")
        if len({(item.sku_id, item.location_id) for item in self.items}) != len(self.items):
            raise ValueError("items must not contain duplicate SKU-location pairs.")
        allowed = {"medium", "high", "critical"}
        if set(self.severity_thresholds) - allowed:
            raise ValueError("severity_thresholds may only contain medium, high, and critical.")
        previous = Decimal("0")
        for severity in ("medium", "high", "critical"):
            threshold = self.severity_thresholds.get(severity)
            if threshold is None:
                continue
            if not threshold.is_finite() or threshold < 0:
                raise ValueError("severity thresholds must be finite and nonnegative.")
            if threshold < previous:
                raise ValueError("severity thresholds must be ordered medium <= high <= critical.")
            previous = threshold
        return self


class PlanningRunSummary(BaseModel):
    planning_run_id: int
    run_key: str
    scenario_key: str
    horizon_start_week: date
    horizon_weeks: int
    inventory_snapshot_id: int
    inventory_cutoff_version: int
    algorithm_version: str
    severity_policy_version: str
    input_fingerprint: str
    run_status: str
    captured_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlanningItemRead(BaseModel):
    sku_id: int
    location_id: int

    model_config = ConfigDict(from_attributes=True)


class PlanningRunRead(PlanningRunSummary):
    items: list[PlanningItemRead]


class PlanningRunPage(BaseModel):
    items: list[PlanningRunSummary]
    total: int
    limit: int
    offset: int


class PABWeekRead(BaseModel):
    planning_run_id: int
    sku_id: int
    location_id: int
    week_start: date
    opening_base: Decimal
    forecast_demand_base: Decimal
    firm_inbound_base: Decimal
    firm_po_remainder_base: Decimal
    unconfirmed_supply_base: Decimal
    ending_firm_base: Decimal
    shortage_quantity_base: Decimal
    severity: str

    model_config = ConfigDict(from_attributes=True)


class PABWeekPage(BaseModel):
    items: list[PABWeekRead]
    total: int


class ShortageCauseRead(BaseModel):
    shortage_cause_id: int
    planning_run_id: int
    sku_id: int
    location_id: int
    week_start: date
    cause_code: str
    contribution_quantity_base: Decimal
    explanation: str

    model_config = ConfigDict(from_attributes=True)


class ShortageRead(BaseModel):
    planning_run_id: int
    sku_id: int
    location_id: int
    week_start: date
    shortage_quantity_base: Decimal
    severity: str
    causes: list[ShortageCauseRead]


class ShortagePage(BaseModel):
    items: list[ShortageRead]
    total: int


class PlanningFindingRead(BaseModel):
    planning_run_finding_id: int
    planning_run_id: int
    finding_origin: str
    finding_code: str | None
    issue_code: str | None
    severity: str
    details: str
    sku_id: int | None
    location_id: int | None
    week_start: date | None

    model_config = ConfigDict(from_attributes=True)


class PlanningFindingPage(BaseModel):
    items: list[PlanningFindingRead]
    total: int
