from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.database.orm import Base


DEFAULT_DATABASE_URL = "sqlite:///data/mpc_online.db"
STRATEGY_CURVE_OPTIONAL_COLUMNS = {
    "actual_load_kw": "FLOAT",
    "actual_pv_kw": "FLOAT",
    "mpc_load_kw": "FLOAT",
    "mpc_pv_kw": "FLOAT",
}

TELEMETRY_15MIN_OPTIONAL_COLUMNS = {
    "sell_price": "FLOAT",
    "mpc_grid_power_kw_avg": "FLOAT",
    "mpc_battery_power_kw_avg": "FLOAT",
    "mpc_soc": "FLOAT",
    "mpc_load_kw": "FLOAT",
    "mpc_pv_kw": "FLOAT",
}


def resolve_database_url(database_url: str | None = None) -> str:
    return database_url or os.getenv("MPC_DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(database_url: str | None = None):
    database_url = resolve_database_url(database_url)
    if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
        db_path = Path(database_url.removeprefix("sqlite:///"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    if database_url in {"sqlite://", "sqlite+pysqlite:///:memory:"}:
        return create_engine(
            database_url,
            future=True,
            connect_args=connect_args,
            poolclass=StaticPool,
        )
    return create_engine(database_url, future=True, connect_args=connect_args)


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    ensure_runtime_schema(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def create_sqlite_memory_session() -> Session:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    ensure_runtime_schema(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)()


def ensure_runtime_schema(engine) -> None:
    if not engine.dialect.name.startswith("sqlite"):
        return
    inspector = inspect(engine)

    # strategy_curve_points optional columns
    if "strategy_curve_points" in inspector.get_table_names():
        existing = {column["name"] for column in inspector.get_columns("strategy_curve_points")}
        missing = {
            column_name: column_type
            for column_name, column_type in STRATEGY_CURVE_OPTIONAL_COLUMNS.items()
            if column_name not in existing
        }
        if missing:
            with engine.begin() as connection:
                for column_name, column_type in missing.items():
                    connection.execute(text(
                        f"ALTER TABLE strategy_curve_points ADD COLUMN {column_name} {column_type}"
                    ))

    # telemetry_15min optional columns (MPC results + sell_price)
    if "telemetry_15min" in inspector.get_table_names():
        existing = {column["name"] for column in inspector.get_columns("telemetry_15min")}
        missing = {
            column_name: column_type
            for column_name, column_type in TELEMETRY_15MIN_OPTIONAL_COLUMNS.items()
            if column_name not in existing
        }
        if missing:
            with engine.begin() as connection:
                for column_name, column_type in missing.items():
                    connection.execute(text(
                        f"ALTER TABLE telemetry_15min ADD COLUMN {column_name} {column_type}"
                    ))

    tables = inspector.get_table_names()
    if "monthly_demand_ref" in tables and "monthly_demand_refs" in tables:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT OR IGNORE INTO monthly_demand_refs "
                "(plant_id, year_month, reference_peak_kw, created_at) "
                "SELECT plant_id, year_month, reference_peak_kw, created_at "
                "FROM monthly_demand_ref "
                "WHERE reference_peak_kw IS NOT NULL"
            ))
