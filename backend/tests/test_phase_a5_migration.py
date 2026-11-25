"""SQLite-only tests for the Phase A5 PAB constraint migration."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    MetaData,
    Numeric,
    Table,
    create_engine,
    inspect,
    text,
)
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.planning import PABWeek


REVISION_FILE = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "a5d7e2c91f44_phase_a5_negative_pab_opening.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(REVISION_FILE.stem, REVISION_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Alembic migration {REVISION_FILE.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolated_database():
    engine = create_engine("sqlite+pysqlite://")
    metadata = MetaData()
    Table(
        "pab_week",
        metadata,
        Column("opening_base", Numeric(28, 8), nullable=True),
        Column("forecast_demand_base", Numeric(28, 8), nullable=True),
        Column("firm_inbound_base", Numeric(28, 8), nullable=True),
        Column("firm_po_remainder_base", Numeric(28, 8), nullable=True),
        Column("unconfirmed_supply_base", Numeric(28, 8), nullable=True),
        CheckConstraint(
            "opening_base >= 0 AND forecast_demand_base >= 0 "
            "AND firm_inbound_base >= 0 AND firm_po_remainder_base >= 0 "
            "AND unconfirmed_supply_base >= 0",
            name="ck_pab_week_input_quantities_nonnegative",
        ),
    )
    metadata.create_all(engine)
    return engine


def _run_migration(connection, action: str) -> None:
    migration = _migration()
    with Operations.context(MigrationContext.configure(connection)):
        getattr(migration, action)()


def _input_check(engine) -> str:
    constraint = next(
        item
        for item in inspect(engine).get_check_constraints("pab_week")
        if item["name"] == "ck_pab_week_input_quantities_nonnegative"
    )
    return constraint["sqltext"]


def test_pab_model_allows_negative_opening_but_keeps_inputs_nonnegative():
    constraint = next(
        item
        for item in PABWeek.__table__.constraints
        if isinstance(item, CheckConstraint)
        and item.name == "ck_pab_week_input_quantities_nonnegative"
    )
    expression = str(constraint.sqltext)

    assert "opening_base" not in expression
    assert "forecast_demand_base >= 0" in expression
    assert "firm_inbound_base >= 0" in expression
    assert "firm_po_remainder_base >= 0" in expression
    assert "unconfirmed_supply_base >= 0" in expression


def test_migration_upgrade_and_safe_downgrade_on_isolated_sqlite():
    engine = _isolated_database()
    try:
        with engine.begin() as connection:
            _run_migration(connection, "upgrade")
        upgraded = _input_check(engine)
        assert "opening_base >= 0" not in upgraded
        assert "forecast_demand_base >= 0" in upgraded
        with engine.begin() as connection:
            _run_migration(connection, "downgrade")
        downgraded = _input_check(engine)
        assert "opening_base >= 0" in downgraded
        assert "forecast_demand_base >= 0" in downgraded
    finally:
        engine.dispose()


def test_downgrade_refuses_to_discard_negative_openings():
    engine = _isolated_database()
    try:
        with engine.begin() as connection:
            _run_migration(connection, "upgrade")
            connection.execute(
                text(
                    "INSERT INTO pab_week (opening_base, forecast_demand_base, "
                    "firm_inbound_base, firm_po_remainder_base, unconfirmed_supply_base) "
                    "VALUES (-5, 0, 0, 0, 0)"
                )
            )
        with engine.begin() as connection:
            with pytest.raises(RuntimeError, match="negative opening balance"):
                _run_migration(connection, "downgrade")
    finally:
        engine.dispose()
