"""Data-gap detection and interpolation for Telemetry15Min windows.

Fills gaps ≤ 5 minutes by linearly interpolating power values from the
neighbouring "ok" windows.  Filled rows are marked `quality_flag = "interpolated"`
so they can be distinguished from real measurements.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from api.database.orm import Telemetry15Min

MAX_GAP_MINUTES = 5


def detect_gaps(
    session: Session,
    *,
    plant_id: str,
) -> list[dict]:
    """Return a list of gap descriptors between successive Telemetry15Min windows.

    Each descriptor is a dict with:
      - gap_start: datetime of the first missing/holed window
      - gap_end:   datetime of the last missing/holed window
      - gap_minutes: total span in minutes
      - fillable:  bool — whether this gap is within the auto‑fill threshold
      - prev_ok:   the last "ok" window before the gap (or None)
      - next_ok:   the first "ok" window after the gap (or None)
    """
    rows = list(
        session.scalars(
            select(Telemetry15Min)
            .where(Telemetry15Min.plant_id == plant_id)
            .order_by(Telemetry15Min.start_time)
        )
    )
    if len(rows) < 2:
        return []

    gaps: list[dict] = []
    i = 0
    while i < len(rows):
        if rows[i].quality_flag in ("ok", "interpolated"):
            i += 1
            continue

        # Found the start of a hole — walk forward to find the end
        gap_start = rows[i]
        j = i + 1
        while j < len(rows) and rows[j].quality_flag not in ("ok", "interpolated"):
            j += 1
        gap_end = rows[j - 1]

        prev_ok = rows[i - 1] if i > 0 and rows[i - 1].quality_flag in ("ok", "interpolated") else None
        next_ok = rows[j] if j < len(rows) and rows[j].quality_flag in ("ok", "interpolated") else None

        span_minutes = (
            (gap_end.end_time - gap_start.start_time).total_seconds() / 60.0
            if gap_end.end_time and gap_start.start_time
            else 0.0
        )

        fillable = (
            prev_ok is not None
            and next_ok is not None
            and span_minutes <= MAX_GAP_MINUTES
        )

        gaps.append(
            {
                "gap_start": gap_start.start_time,
                "gap_end": gap_end.end_time,
                "gap_minutes": span_minutes,
                "fillable": fillable,
                "prev_ok": prev_ok,
                "next_ok": next_ok,
            }
        )
        i = j

    return gaps


def fill_gaps(
    session: Session,
    *,
    plant_id: str,
    dry_run: bool = False,
) -> int:
    """Fill all auto‑fillable gaps (≤ 5 min) and return the number of rows filled.

    Filled rows are upserted with ``quality_flag = "interpolated"``.  Power
    values are linearly interpolated between the neighbouring ok windows; SOC
    is carried forward from the previous window.
    """
    gaps = detect_gaps(session, plant_id=plant_id)
    fillable = [g for g in gaps if g["fillable"]]
    if not fillable:
        return 0

    filled = 0
    for gap in fillable:
        prev: Telemetry15Min = gap["prev_ok"]
        next_: Telemetry15Min = gap["next_ok"]

        # Collect the hole windows
        hole_rows = list(
            session.scalars(
                select(Telemetry15Min)
                .where(
                    Telemetry15Min.plant_id == plant_id,
                    Telemetry15Min.start_time >= gap["gap_start"],
                    Telemetry15Min.end_time <= gap["gap_end"],
                )
                .order_by(Telemetry15Min.start_time)
            )
        )
        if not hole_rows:
            continue

        prev_t = prev.start_time.timestamp()
        next_t = next_.start_time.timestamp()
        total_span = next_t - prev_t
        if total_span <= 0:
            continue

        for row in hole_rows:
            t = row.start_time.timestamp()
            frac = (t - prev_t) / total_span
            frac = max(0.0, min(1.0, frac))

            def _lerp(left_val, right_val):
                if left_val is None or right_val is None:
                    return None
                return float(left_val) + (float(right_val) - float(left_val)) * frac

            row.grid_power_kw_avg = _lerp(
                prev.grid_power_kw_avg, next_.grid_power_kw_avg
            )
            row.grid_power_kw_max = _lerp(
                prev.grid_power_kw_max or prev.grid_power_kw_avg,
                next_.grid_power_kw_max or next_.grid_power_kw_avg,
            )
            row.battery_power_kw_avg = _lerp(
                prev.battery_power_kw_avg, next_.battery_power_kw_avg
            )

            # load_minus_pv = grid + battery
            g = row.grid_power_kw_avg
            b = row.battery_power_kw_avg
            row.load_minus_pv_kw_avg = (g + b) if g is not None and b is not None else None

            # SOC: carry forward from previous window
            row.soc_start = prev.soc_end
            row.soc_end = prev.soc_end

            row.grid_sample_count = 0
            row.battery_sample_count = 0
            row.quality_flag = "interpolated"

            if not dry_run:
                filled += 1

    if not dry_run and filled > 0:
        session.commit()

    return filled


def max_gap_minutes(
    session: Session,
    *,
    plant_id: str,
) -> float:
    """Return the maximum (non‑fillable) gap in minutes, or 0 if none."""
    gaps = detect_gaps(session, plant_id=plant_id)
    unfillable = [g for g in gaps if not g["fillable"]]
    return max((g["gap_minutes"] for g in unfillable), default=0.0)
