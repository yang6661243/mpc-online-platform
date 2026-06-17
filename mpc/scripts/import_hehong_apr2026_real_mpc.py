from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
import sys

import pandas as pd
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.comparison import compute_actual_strategy_metrics, save_strategy_comparison
from api.database import ensure_runtime_schema
from api.models import Base, MpcRun, StrategyComparison, StrategyCurvePoint, Telemetry15Min, utc_now


ACTUAL_COLUMNS = {
    "time": "时间",
    "pv_kw": "光伏功率(kW)_实际",
    "load_kw": "负载功率(kW)_实际",
    "soc": "SOC_实际",
    "battery_kw": "电池功率(kW)_实际",
    "grid_kw": "电网功率(kW)_实际",
    "buy_price": "购电价(元/kWh)",
    "sell_price": "售电价(元/kWh)",
}

MPC_COLUMNS = {
    "time": "时间",
    "pv_kw": "光伏出力(kW)",
    "load_kw": "负荷功率(kW)",
    "soc": "SOC",
    "battery_kw": "电池功率(kW)",
    "grid_kw": "电网功率(kW)",
    "buy_price": "购电价(元/kWh)",
    "sell_price": "售电价(元/kWh)",
}


@dataclass(frozen=True)
class ImportSummary:
    telemetry_rows: int
    curve_rows: int
    actual_peak_kw: float
    mpc_peak_kw: float
    actual_cost_yuan: float
    mpc_cost_yuan: float
    cost_saving_yuan: float


def _read_trajectory(path: Path, columns: dict[str, str], *, sheet_name: str = "15min_trajectory") -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet_name)
    missing = [source for source in columns.values() if source not in df.columns]
    if missing:
        raise ValueError(f"{path} missing required columns: {missing}")
    out = pd.DataFrame({name: df[source] for name, source in columns.items()})
    out["time"] = pd.to_datetime(out["time"], errors="coerce")
    if out["time"].isna().any():
        raise ValueError(f"{path} contains invalid time values")
    for col in [c for c in out.columns if c != "time"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if out.drop(columns=["time"]).isna().any().any():
        bad = out.columns[out.isna().any()].tolist()
        raise ValueError(f"{path} contains non-numeric values in columns: {bad}")
    return out.sort_values("time").reset_index(drop=True)


def load_joined_curves(actual_xlsx: Path, mpc_xlsx: Path) -> pd.DataFrame:
    actual = _read_trajectory(actual_xlsx, ACTUAL_COLUMNS).add_prefix("actual_")
    actual = actual.rename(columns={"actual_time": "time"})
    mpc = _read_trajectory(mpc_xlsx, MPC_COLUMNS).add_prefix("mpc_")
    mpc = mpc.rename(columns={"mpc_time": "time"})

    merged = actual.merge(mpc, on="time", how="inner", validate="one_to_one")
    if len(merged) != len(actual) or len(merged) != len(mpc):
        raise ValueError(f"time alignment mismatch: actual={len(actual)} mpc={len(mpc)} merged={len(merged)}")
    if len(merged) != 2880:
        raise ValueError(f"expected 2880 15-minute rows for April 2026, got {len(merged)}")
    expected_start = pd.Timestamp("2026-04-01 00:00:00")
    expected_end = pd.Timestamp("2026-04-30 23:45:00")
    if merged["time"].iloc[0] != expected_start or merged["time"].iloc[-1] != expected_end:
        raise ValueError(f"unexpected time range: {merged['time'].iloc[0]} -> {merged['time'].iloc[-1]}")
    return merged


def import_curves(
    session: Session,
    *,
    plant_id: str,
    run_id: str,
    joined: pd.DataFrame,
    scenario_path: str,
    c_deg: float,
    demand_rate: float,
    billing_days: float,
    replace: bool,
) -> ImportSummary:
    start = joined["time"].iloc[0].to_pydatetime()
    last_start = joined["time"].iloc[-1].to_pydatetime()
    end = last_start + timedelta(minutes=15)

    existing_run = session.scalar(select(MpcRun).where(MpcRun.run_id == run_id))
    if existing_run is not None and not replace:
        raise ValueError(f"run_id already exists: {run_id}; pass --replace to overwrite")

    if replace:
        session.execute(delete(StrategyCurvePoint).where(StrategyCurvePoint.run_id == run_id))
        session.execute(delete(StrategyComparison).where(StrategyComparison.run_id == run_id))
        session.execute(delete(MpcRun).where(MpcRun.run_id == run_id))
        session.execute(
            delete(Telemetry15Min).where(
                Telemetry15Min.plant_id == plant_id,
                Telemetry15Min.start_time >= start,
                Telemetry15Min.start_time <= last_start,
            )
        )
        session.commit()

    run = MpcRun(
        run_id=run_id,
        plant_id=plant_id,
        profile="hehong_weather",
        status="succeeded",
        input_start_time=start,
        input_end_time=end,
        scenario_path=scenario_path,
        started_at=utc_now(),
        finished_at=utc_now(),
    )
    session.add(run)

    for row in joined.itertuples(index=False):
        timestamp = row.time.to_pydatetime()
        actual_load_minus_pv = float(row.actual_load_kw) - float(row.actual_pv_kw)
        session.add(
            Telemetry15Min(
                plant_id=plant_id,
                start_time=timestamp,
                end_time=timestamp + timedelta(minutes=15),
                grid_power_kw_avg=float(row.actual_grid_kw),
                grid_power_kw_max=float(row.actual_grid_kw),
                battery_power_kw_avg=float(row.actual_battery_kw),
                load_minus_pv_kw_avg=actual_load_minus_pv,
                soc_start=float(row.actual_soc),
                soc_end=float(row.actual_soc),
                grid_sample_count=1,
                battery_sample_count=1,
                quality_flag="ok",
            )
        )
        session.add(
            StrategyCurvePoint(
                run_id=run_id,
                plant_id=plant_id,
                time=timestamp,
                actual_grid_power_kw=float(row.actual_grid_kw),
                actual_battery_power_kw=float(row.actual_battery_kw),
                actual_soc=float(row.actual_soc),
                actual_load_kw=float(row.actual_load_kw),
                actual_pv_kw=float(row.actual_pv_kw),
                load_minus_pv_kw=actual_load_minus_pv,
                mpc_grid_power_kw=float(row.mpc_grid_kw),
                mpc_battery_power_kw=float(row.mpc_battery_kw),
                mpc_soc=float(row.mpc_soc),
                mpc_load_kw=float(row.mpc_load_kw),
                mpc_pv_kw=float(row.mpc_pv_kw),
                buy_price=float(row.actual_buy_price),
                sell_price=float(row.actual_sell_price),
            )
        )
    session.commit()

    actual = compute_actual_strategy_metrics(
        grid_power_kw=joined["actual_grid_kw"].astype(float).tolist(),
        battery_power_kw=joined["actual_battery_kw"].astype(float).tolist(),
        soc=joined["actual_soc"].astype(float).tolist(),
        buy_price=joined["actual_buy_price"].astype(float).tolist(),
        sell_price=joined["actual_sell_price"].astype(float).tolist(),
        c_deg=c_deg,
        demand_rate=demand_rate,
        billing_days=billing_days,
    )
    mpc = compute_actual_strategy_metrics(
        grid_power_kw=joined["mpc_grid_kw"].astype(float).tolist(),
        battery_power_kw=joined["mpc_battery_kw"].astype(float).tolist(),
        soc=joined["mpc_soc"].astype(float).tolist(),
        buy_price=joined["mpc_buy_price"].astype(float).tolist(),
        sell_price=joined["mpc_sell_price"].astype(float).tolist(),
        c_deg=c_deg,
        demand_rate=demand_rate,
        billing_days=billing_days,
    )
    comparison = save_strategy_comparison(session, run_id=run_id, plant_id=plant_id, actual=actual, mpc=mpc)
    return ImportSummary(
        telemetry_rows=session.query(Telemetry15Min)
        .filter(Telemetry15Min.plant_id == plant_id, Telemetry15Min.start_time >= start, Telemetry15Min.start_time <= last_start)
        .count(),
        curve_rows=session.query(StrategyCurvePoint).filter(StrategyCurvePoint.run_id == run_id).count(),
        actual_peak_kw=float(comparison.actual_peak_kw or 0),
        mpc_peak_kw=float(comparison.mpc_peak_kw or 0),
        actual_cost_yuan=float(comparison.actual_cost_yuan or 0),
        mpc_cost_yuan=float(comparison.mpc_cost_yuan or 0),
        cost_saving_yuan=float(comparison.cost_saving_yuan or 0),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Hehong April 2026 real factory strategy and MPC curves.")
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--actual-xlsx", required=True, type=Path)
    parser.add_argument("--mpc-xlsx", required=True, type=Path)
    parser.add_argument("--plant-id", default="hehong_huajin")
    parser.add_argument("--run-id", default="hehong_apr2026_real_mpc")
    parser.add_argument("--c-deg", default=0.05, type=float)
    parser.add_argument("--demand-rate", default=39.0, type=float)
    parser.add_argument("--billing-days", default=30.0, type=float)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    joined = load_joined_curves(args.actual_xlsx, args.mpc_xlsx)
    engine = create_engine(args.db_url, future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    ensure_runtime_schema(engine)
    session_factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)
    with session_factory() as session:
        summary = import_curves(
            session,
            plant_id=args.plant_id,
            run_id=args.run_id,
            joined=joined,
            scenario_path=str(args.mpc_xlsx),
            c_deg=args.c_deg,
            demand_rate=args.demand_rate,
            billing_days=args.billing_days,
            replace=args.replace,
        )
    print(
        {
            "plant_id": args.plant_id,
            "run_id": args.run_id,
            "telemetry_rows": summary.telemetry_rows,
            "curve_rows": summary.curve_rows,
            "actual_peak_kw": round(summary.actual_peak_kw, 3),
            "mpc_peak_kw": round(summary.mpc_peak_kw, 3),
            "actual_cost_yuan": round(summary.actual_cost_yuan, 2),
            "mpc_cost_yuan": round(summary.mpc_cost_yuan, 2),
            "cost_saving_yuan": round(summary.cost_saving_yuan, 2),
        }
    )


if __name__ == "__main__":
    main()
