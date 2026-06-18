from sqlalchemy import create_engine, select, text

import server.api.routes.mpc as mpc_routes
from server.api._database_module import ensure_runtime_schema
from server.api.constants import RunMpcRequest
from server.api.database.orm import MonthlyDemandRef


def test_runtime_schema_migrates_legacy_monthly_demand_ref_rows(tmp_path):
    db_path = tmp_path / "mpc_online.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE monthly_demand_ref ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "plant_id VARCHAR(64) NOT NULL,"
            "year_month VARCHAR(7) NOT NULL,"
            "reference_peak_kw FLOAT,"
            "created_at DATETIME NOT NULL DEFAULT (datetime('now')),"
            "updated_at DATETIME NOT NULL DEFAULT (datetime('now')),"
            "CONSTRAINT uq_monthly_demand_ref UNIQUE (plant_id, year_month)"
            ")"
        ))
        connection.execute(text(
            "INSERT INTO monthly_demand_ref "
            "(plant_id, year_month, reference_peak_kw, created_at, updated_at) "
            "VALUES ('hehong_huajin', '2026-06', 400.0, "
            "'2026-06-18 01:23:40', '2026-06-18 01:23:40')"
        ))

    MonthlyDemandRef.__table__.create(engine)
    ensure_runtime_schema(engine)

    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT plant_id, year_month, reference_peak_kw "
            "FROM monthly_demand_refs"
        )).all()

    assert rows == [("hehong_huajin", "2026-06", 400.0)]


def test_resolve_target_peak_uses_saved_monthly_target_when_request_omits_it():
    payload = RunMpcRequest(
        request_id="web_1",
        plant_id="hehong_huajin",
        start_time="2026-06-17T00:00:00",
        end_time="2026-06-18T00:00:00",
        profile="demand100",
    )
    demand_ref = MonthlyDemandRef(
        plant_id="hehong_huajin",
        year_month="2026-06",
        reference_peak_kw=400.0,
    )

    assert mpc_routes._resolve_target_peak_kw(payload, demand_ref) == 400.0


def test_resolve_target_peak_keeps_explicit_request_value():
    payload = RunMpcRequest(
        request_id="web_1",
        plant_id="hehong_huajin",
        start_time="2026-06-17T00:00:00",
        end_time="2026-06-18T00:00:00",
        profile="demand100",
        target_peak_kw=380.0,
    )
    demand_ref = MonthlyDemandRef(
        plant_id="hehong_huajin",
        year_month="2026-06",
        reference_peak_kw=400.0,
    )

    assert mpc_routes._resolve_target_peak_kw(payload, demand_ref) == 380.0
