from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.database.orm import Telemetry15Min
from api.utils.time import parse_timestamp


@dataclass(frozen=True)
class MpcScenarioExport:
    output_path: Path
    load_base_kw: float
    steps: int


def _load_base_from_rows(rows: list[Telemetry15Min], configured: float | None) -> float:
    if configured is not None:
        if configured <= 0:
            raise ValueError("load_base_kw must be positive")
        return float(configured)

    max_net_load = max(float(row.load_minus_pv_kw_avg or 0.0) for row in rows)
    if max_net_load <= 0:
        raise ValueError("cannot infer load_base_kw from non-positive telemetry")
    return max_net_load


def _validate_rows(rows: list[Telemetry15Min]) -> None:
    if not rows:
        raise ValueError("no telemetry rows found")
    for row in rows:
        if row.quality_flag != "ok" or row.load_minus_pv_kw_avg is None:
            raise ValueError(
                "incomplete telemetry: all rows must have quality_flag='ok' "
                "and load_minus_pv_kw_avg"
            )


def export_mpc_scenario_from_telemetry(
    session: Session,
    *,
    plant_id: str,
    start_time,
    end_time,
    output_path: str | Path,
    load_base_kw: float | None = None,
    buy_price: float = 0.8,
    sell_price: float = 0.3,
) -> MpcScenarioExport:
    """Export online telemetry into the workbook shape expected by microgrid.mpc.

    The current MPC reads normalized load ratios plus zero-based PV/wind
    weather-like sheets. Until real PV is available separately, online net load
    is represented as load with PV and wind set to zero.
    """
    start = parse_timestamp(start_time)
    end = parse_timestamp(end_time)
    rows = list(
        session.scalars(
            select(Telemetry15Min)
            .where(
                Telemetry15Min.plant_id == plant_id,
                Telemetry15Min.start_time >= start,
                Telemetry15Min.end_time <= end,
            )
            .order_by(Telemetry15Min.start_time)
        )
    )
    # Filter to only ready windows (skip incomplete/missing ones)
    rows = [r for r in rows if r.quality_flag == "ok" and r.load_minus_pv_kw_avg is not None]
    if not rows:
        raise ValueError("no ready telemetry rows found")
    base_kw = _load_base_from_rows(rows, load_base_kw)

    times = [row.start_time for row in rows]
    load_ratios = [
        max(0.0, min(1.0, float(row.load_minus_pv_kw_avg) / base_kw))
        for row in rows
    ]
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        pd.DataFrame({"time": times, "load": load_ratios}).to_excel(
            writer,
            sheet_name="load",
            index=False,
        )
        pd.DataFrame(
            {
                "time": times,
                "irradiance": [0.0] * len(rows),
                "wind_speed": [0.0] * len(rows),
            }
        ).to_excel(writer, sheet_name="pv", index=False)
        pd.DataFrame(
            {
                "time": times,
                "buy_price": [float(buy_price)] * len(rows),
                "sell_price": [float(sell_price)] * len(rows),
            }
        ).to_excel(writer, sheet_name="price", index=False)

    return MpcScenarioExport(output_path=out, load_base_kw=base_kw, steps=len(rows))
