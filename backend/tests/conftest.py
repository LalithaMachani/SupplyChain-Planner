"""Isolated SQLite fixtures for ingestion API tests."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.types import BigInteger

from app.db.database import Base, get_db
from app.main import app
from app.models import SKU, SKUUOM, UnitOfMeasure


@compiles(BigInteger, "sqlite")
def _compile_bigint_for_sqlite(type_, compiler, **kwargs):
    return compiler.visit_integer(type_, **kwargs)


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            connection.exec_driver_sql(str(CreateTable(table).compile(engine)))
            for index in table.indexes:
                connection.exec_driver_sql(str(CreateIndex(index).compile(engine)))

    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def seed_catalog(session_factory):
    with session_factory() as session:
        session.add(
            UnitOfMeasure(
                uom_id=1,
                code="EA",
                dimension="count",
                decimal_places=0,
                is_active=True,
            )
        )
        session.add(
            SKU(
                sku_id=1001,
                sku_code="SKU-000001",
                name="Test item",
                base_uom_id=1,
                status="active",
            )
        )
        session.add(
            SKUUOM(
                sku_id=1001,
                uom_id=1,
                to_base_factor=1,
                is_base_uom=True,
                is_purchase_uom=True,
            )
        )
        session.commit()


@pytest.fixture
def client(session_factory) -> Generator[TestClient, None, None]:
    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_db, None)
