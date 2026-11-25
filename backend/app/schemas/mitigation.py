"""Read-only shortage mitigation recommendation schemas."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class MitigationRecommendation(BaseModel):
    recommendation_key: str
    type: Literal[
        "expedite_inbound",
        "inventory_transfer",
        "approved_substitute",
        "alternative_supplier",
    ]
    feasibility: Literal["feasible", "insufficient_evidence"]
    is_alternative_option: bool = True
    planning_run_id: int
    sku_id: int
    location_id: int
    shortage_week: date
    affected_shortage_quantity_base: Decimal
    proposed_quantity_base: Decimal | None
    expected_arrival_date: date | None = None
    current_expected_arrival_date: date | None = None
    source_location_id: int | None = None
    source_sku_id: int | None = None
    supplier_id: int | None = None
    inbound_shipment_line_id: int | None = None
    substitution_id: int | None = None
    supplier_sku_id: int | None = None
    estimated_cost: Decimal | None = None
    cost_currency: str | None = None
    rationale: str
    evidence: list[str]


class MitigationPage(BaseModel):
    items: list[MitigationRecommendation]
    total: int
    limit: int
    offset: int
