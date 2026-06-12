from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from microgrid_online.models import Base


DEFAULT_DATABASE_URL = "sqlite:///data/mpc_online.db"


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
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def create_sqlite_memory_session() -> Session:
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)()
