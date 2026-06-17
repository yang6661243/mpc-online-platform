"""MPC health checker — per‑profile background task.

Each (plant_id, profile) pair gets its own MpcHealthChecker that:
1. Periodically assesses data completeness and continuity.
2. Detects and fills small gaps (≤ 5 min) via ``gap_filler``.
3. Detects data timeout when no new telemetry_15min for > 20 min.
4. Coordinates with the shared ``MpcScheduler`` to serialise runs within a
   plant while allowing different plants to run in parallel.
5. After each successful MPC run, invokes the Fuzzy PID decomposition
   pipeline to produce per‑minute ``IntraMinutePoint`` rows.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.services.gap_filler import fill_gaps, max_gap_minutes
from api.database.orm import MonthlyDemandRef, MpcRun, Telemetry15Min

logger = logging.getLogger(__name__)

# ── tunables ────────────────────────────────────────────────────────────────

CHECK_INTERVAL_SECONDS = 30
MIN_WINDOWS_FOR_READY = 96
PERIODIC_TRIGGER_MINUTES = {0, 15, 30, 45}
MAX_GAP_MINUTES = 5
DATA_TIMEOUT_MINUTES = 20

# ── state constants ─────────────────────────────────────────────────────────

STATE_IDLE = "idle"
STATE_ACCUMULATING = "accumulating"
STATE_AWAITING_DEMAND_REF = "awaiting_demand_ref"
STATE_READY = "ready"
STATE_RUNNING = "running"
STATE_RUNNING_FUZZY = "running_fuzzy"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_DATA_GAP = "data_gap"
STATE_DATA_TIMEOUT = "data_timeout"

STATE_LABELS: dict[str, str] = {
    STATE_IDLE: "等待数据",
    STATE_ACCUMULATING: "数据积累中",
    STATE_AWAITING_DEMAND_REF: "等待输入本月参考需量",
    STATE_READY: "数据就绪",
    STATE_RUNNING: "MPC 运行中",
    STATE_RUNNING_FUZZY: "Fuzzy PID 分解中",
    STATE_SUCCEEDED: "运行成功",
    STATE_FAILED: "运行失败",
    STATE_DATA_GAP: "数据中断",
    STATE_DATA_TIMEOUT: "数据获取超时",
}


# ── status snapshot ─────────────────────────────────────────────────────────


@dataclass
class MpcHealthStatus:
    plant_id: str
    profile: str = ""
    state: str = STATE_IDLE
    label: str = ""
    telemetry_windows: int = 0
    ok_windows: int = 0
    interpolated_windows: int = 0
    continuous_ok_windows: int = 0
    last_run_id: str | None = None
    last_run_status: str | None = None
    last_run_finished_at: str | None = None
    last_run_error: str | None = None
    minutes_since_last_run: float | None = None
    has_gap: bool = False
    max_gap_minutes: float = 0.0
    new_windows_since_last: int = 0
    monthly_demand_ref_set: bool = False
    data_timed_out: bool = False
    minutes_since_latest_data: float | None = None
    scheduler_enabled: bool = False
    checked_at: str = ""


# ── MPC runner protocol ─────────────────────────────────────────────────────

RunMpcFunc = Callable[[str, str, str, datetime, datetime], str | None]
"""Signature: (plant_id, profile, run_id, from_time, to_time) → run_id or None."""

RunFuzzyPidFunc = Callable[[str, str, str, datetime, datetime], list]
"""Signature: (plant_id, profile, run_id, from_time, to_time) → list of points."""


# ── MPC scheduler (plant‑level serialisation) ───────────────────────────────


class MpcScheduler:
    """Coordinates MPC runs across profiles within one plant.

    - Default disabled. Manual trigger enables it.
    - Once enabled, auto-runs on periodic triggers (:00/:15/:30/:45).
    - Data timeout disables the scheduler.
    - Only one profile runs at a time per plant.
    """

    def __init__(
        self,
        plant_id: str,
        profiles: list[str],
        run_mpc: RunMpcFunc,
        run_fuzzy: RunFuzzyPidFunc,
    ):
        self.plant_id = plant_id
        self.profiles = profiles
        self._run_mpc = run_mpc
        self._run_fuzzy = run_fuzzy
        self._lock = asyncio.Lock()
        self._last_periodic_trigger: datetime | None = None
        self._last_aggregate_time: datetime | None = None
        self.enabled: bool = False

    def enable(self) -> None:
        self.enabled = True
        logger.info("scheduler ENABLED: %s", self.plant_id)

    def disable(self) -> None:
        self.enabled = False
        logger.info("scheduler DISABLED: %s", self.plant_id)

    async def maybe_trigger(
        self,
        session_factory: Callable[[], Session],
    ) -> None:
        """Called from each checker tick. Only active when enabled."""
        if not self.enabled:
            return
        if self._lock.locked():
            return

        now = datetime.now()
        if now.minute in PERIODIC_TRIGGER_MINUTES:
            return  # let periodic scheduler handle it

        # Catch-up: check for new data and run if available
        session = session_factory()
        try:
            for profile in self.profiles:
                if self._has_new_raw_data(session):
                    async with self._lock:
                        await self._run_full_chain(session, profile)
                    break
        finally:
            session.close()

    async def run_all_periodic(
        self,
        session_factory: Callable[[], Session],
    ) -> None:
        """Run all profiles serially (periodic phase). Only when enabled."""
        if not self.enabled:
            return
        async with self._lock:
            session = session_factory()
            try:
                for profile in self.profiles:
                    if self._has_new_raw_data(session):
                        await self._run_full_chain(session, profile)
            finally:
                session.close()

    def _has_new_raw_data(self, session: Session) -> bool:
        """Check if raw data exists newer than last aggregation time."""
        from api.database.orm import RawTelemetry
        latest_raw = session.scalar(
            select(RawTelemetry.time)
            .where(RawTelemetry.plant_id == self.plant_id)
            .order_by(RawTelemetry.time.desc())
            .limit(1)
        )
        if latest_raw is None:
            return False
        if self._last_aggregate_time is None or latest_raw > self._last_aggregate_time:
            return True
        return False

    def mark_aggregated(self, agg_time: datetime) -> None:
        self._last_aggregate_time = agg_time

    async def _run_full_chain(self, session: Session, profile: str) -> None:
        """Execute the full chain: aggregate → irradiance → MPC → Fuzzy PID."""
        from api.services.aggregation import aggregate_telemetry_15min
        from api.services.irradiance import fetch_and_store_irradiance
        from api.services.plant_config import load_plant_config
        from api.constants import PROJECT_ROOT
        from api.database.orm import RawTelemetry, StrategyCurvePoint

        plant_id = self.plant_id

        # ── Get raw data range ──
        raw_times = list(
            session.scalars(
                select(RawTelemetry.time)
                .where(RawTelemetry.plant_id == plant_id)
                .order_by(RawTelemetry.time)
            )
        )
        if not raw_times:
            logger.info("Scheduler: no raw data for %s, skipping", plant_id)
            return
        raw_start = raw_times[0]
        raw_end = raw_times[-1]

        # ── Aggregate ──
        logger.info("Scheduler: aggregating %s from %s to %s", plant_id, raw_start, raw_end)
        aggregate_telemetry_15min(
            session,
            plant_id=plant_id,
            start_time=raw_start.isoformat(),
            end_time=raw_end.isoformat(),
        )
        self.mark_aggregated(raw_end)

        # ── Irradiance (if plant has PV) ──
        try:
            config_names = {"hehong_huajin": "hehong_huajin", "aolaide": "aodelai"}
            config_name = config_names.get(plant_id, plant_id)
            cfg_path = PROJECT_ROOT / "mpc" / "configs" / "plants" / f"{config_name}.yaml"
            if cfg_path.exists():
                cfg = load_plant_config(cfg_path)
                if getattr(cfg.pv, "capacity_kw", 0) > 0:
                    agg_rows = list(
                        session.scalars(
                            select(Telemetry15Min)
                            .where(Telemetry15Min.plant_id == plant_id)
                            .order_by(Telemetry15Min.start_time)
                        )
                    )
                    if agg_rows:
                        logger.info("Scheduler: fetching irradiance for %s", plant_id)
                        fetch_and_store_irradiance(
                            session, plant_id=plant_id,
                            start_time=agg_rows[0].start_time,
                            end_time=agg_rows[-1].end_time,
                            latitude=cfg.location.latitude,
                            longitude=cfg.location.longitude,
                        )
        except Exception as exc:
            logger.warning("Irradiance fetch skipped for %s: %s", plant_id, exc)

        # ── Run MPC ──
        from api.services.mpc.orchestrator import run_online_mpc
        from api.services.mpc.orchestrator import make_run_id

        run_id = make_run_id(f"auto_{plant_id}_{profile}_{datetime.now():%Y%m%d_%H%M%S}")

        rows = list(
            session.scalars(
                select(Telemetry15Min)
                .where(Telemetry15Min.plant_id == plant_id)
                .order_by(Telemetry15Min.start_time)
            )
        )
        if not rows:
            logger.info("Scheduler: no telemetry for %s, skipping MPC", plant_id)
            return
        from_time = rows[0].start_time
        to_time = rows[-1].end_time or rows[-1].start_time + timedelta(minutes=15)

        run = MpcRun(
            run_id=run_id, plant_id=plant_id, profile=profile,
            status="running", started_at=datetime.now(),
        )
        session.add(run)
        session.commit()

        try:
            logger.info("MPC starting: %s/%s run=%s from=%s to=%s",
                        plant_id, profile, run_id, from_time, to_time)
            self._run_mpc(plant_id, profile, run_id, from_time, to_time)
            run.status = "succeeded"
            run.finished_at = datetime.now()
            session.commit()

            # Persist MPC results to telemetry_15min
            _persist_mpc_results(session, run_id, plant_id)

            # Fuzzy PID decomposition
            logger.info("Fuzzy PID starting: %s/%s run=%s", plant_id, profile, run_id)
            self._run_fuzzy(plant_id, profile, run_id, from_time, to_time)
        except Exception as exc:
            run.status = "failed"
            run.error_message = str(exc)[:1000]
            run.finished_at = datetime.now()
            session.commit()
            logger.exception("MPC chain failed: %s/%s run=%s", plant_id, profile, run_id)

    def _assess_profile(self, session: Session, profile: str) -> MpcHealthStatus:
        """Quick assessment for scheduling decisions."""
        checker = MpcHealthChecker(
            session_factory=lambda: session,
            plant_id=self.plant_id,
            profile=profile,
        )
        return checker._assess(session)


def _persist_mpc_results(session: Session, run_id: str, plant_id: str) -> None:
    """Write MPC strategy curve points back into telemetry_15min as MPC fields."""
    from api.database.orm import StrategyCurvePoint
    points = list(
        session.scalars(
            select(StrategyCurvePoint)
            .where(StrategyCurvePoint.run_id == run_id)
            .order_by(StrategyCurvePoint.time)
        )
    )
    if not points:
        return
    for pt in points:
        window_start = pt.time
        existing = session.scalar(
            select(Telemetry15Min).where(
                Telemetry15Min.plant_id == plant_id,
                Telemetry15Min.start_time == window_start,
            )
        )
        if existing:
            existing.mpc_grid_power_kw_avg = pt.mpc_grid_power_kw
            existing.mpc_battery_power_kw_avg = pt.mpc_battery_power_kw
            existing.mpc_soc = pt.mpc_soc
            existing.mpc_load_kw = pt.mpc_load_kw
            existing.mpc_pv_kw = pt.mpc_pv_kw
            existing.buy_price = existing.buy_price or pt.buy_price
            existing.sell_price = existing.sell_price or pt.sell_price
    session.commit()
    logger.info("Persisted MPC results to telemetry_15min: %d windows for run=%s",
                len(points), run_id)


# ── health checker ──────────────────────────────────────────────────────────


class MpcHealthChecker:
    """Health‑check logic for one (plant_id, profile) pair."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        plant_id: str,
        profile: str = "",
        horizon_steps: int = 96,
    ):
        self._session_factory = session_factory
        self.plant_id = plant_id
        self.profile = profile
        self.horizon_steps = horizon_steps

    def tick(self) -> MpcHealthStatus:
        session = self._session_factory()
        try:
            fill_gaps(session, plant_id=self.plant_id)
            return self._assess(session)
        finally:
            session.close()

    def get_status(self, session: Session | None = None) -> MpcHealthStatus:
        own = session is None
        if own:
            session = self._session_factory()
        try:
            return self._assess(session)
        finally:
            if own and session is not None:
                session.close()

    # ── assessment ──────────────────────────────────────────────────────

    def _get_latest_telemetry_time(self, session: Session) -> datetime | None:
        latest = session.scalar(
            select(Telemetry15Min.end_time)
            .where(Telemetry15Min.plant_id == self.plant_id)
            .order_by(Telemetry15Min.end_time.desc())
            .limit(1)
        )
        return latest

    def _assess(self, session: Session) -> MpcHealthStatus:
        now = datetime.now()
        total = self._count_windows(session)
        ok_count = self._count_windows(session, quality="ok")
        interp_count = self._count_windows(session, quality="interpolated")
        continuous_ok = self._count_continuous_ok(session)
        demand_ref_set = self._check_monthly_demand_ref(session)

        last_run = self._last_run(session)
        last_run_id = last_run.run_id if last_run else None
        last_run_status = last_run.status if last_run else None
        last_run_finished = last_run.finished_at if last_run else None
        last_run_error = last_run.error_message if last_run else None

        mins_since = None
        new_since = 0
        if last_run_finished:
            mins_since = (now - last_run_finished).total_seconds() / 60.0
            new_since = self._count_windows_since(session, last_run_finished)

        max_gap = max_gap_minutes(session, plant_id=self.plant_id)
        has_gap = max_gap > MAX_GAP_MINUTES

        # Data timeout: latest telemetry > DATA_TIMEOUT_MINUTES ago
        latest_time = self._get_latest_telemetry_time(session)
        mins_since_latest = (
            (now - latest_time).total_seconds() / 60.0 if latest_time else None
        )
        data_timed_out = mins_since_latest is not None and mins_since_latest > DATA_TIMEOUT_MINUTES

        state = self._determine_state(
            total=total,
            has_gap=has_gap,
            data_timed_out=data_timed_out,
            demand_ref_set=demand_ref_set,
            last_run_status=last_run_status,
        )

        return MpcHealthStatus(
            plant_id=self.plant_id,
            profile=self.profile,
            state=state,
            label=STATE_LABELS.get(state, state),
            telemetry_windows=total,
            ok_windows=ok_count,
            interpolated_windows=interp_count,
            continuous_ok_windows=continuous_ok,
            last_run_id=last_run_id,
            last_run_status=last_run_status,
            last_run_finished_at=last_run_finished.isoformat() if last_run_finished else None,
            last_run_error=last_run_error,
            minutes_since_last_run=round(mins_since, 1) if mins_since is not None else None,
            has_gap=has_gap,
            max_gap_minutes=round(max_gap, 1),
            new_windows_since_last=new_since,
            monthly_demand_ref_set=demand_ref_set,
            data_timed_out=data_timed_out,
            minutes_since_latest_data=round(mins_since_latest, 1) if mins_since_latest is not None else None,
            scheduler_enabled=False,  # updated by mpc route / scheduler
            checked_at=now.isoformat(),
        )

    # ── state machine ───────────────────────────────────────────────────

    def _determine_state(
        self,
        *,
        total: int,
        has_gap: bool,
        data_timed_out: bool,
        demand_ref_set: bool,
        last_run_status: str | None,
    ) -> str:
        if total == 0:
            return STATE_IDLE
        if last_run_status == "running":
            return STATE_RUNNING
        if last_run_status == "running_fuzzy":
            return STATE_RUNNING_FUZZY
        if has_gap:
            return STATE_DATA_GAP
        if data_timed_out:
            return STATE_DATA_TIMEOUT
        if total < self.horizon_steps:
            return STATE_ACCUMULATING
        if not demand_ref_set:
            return STATE_AWAITING_DEMAND_REF
        if last_run_status == "failed":
            return STATE_FAILED
        if last_run_status == "succeeded":
            return STATE_SUCCEEDED
        return STATE_READY

    # ── monthly demand ref ──────────────────────────────────────────────

    def _check_monthly_demand_ref(self, session: Session) -> bool:
        """True if a MonthlyDemandRef exists for the current month."""
        now = datetime.now()
        year_month = now.strftime("%Y-%m")
        ref = session.scalar(
            select(MonthlyDemandRef).where(
                MonthlyDemandRef.plant_id == self.plant_id,
                MonthlyDemandRef.year_month == year_month,
            )
        )
        return ref is not None

    def _count_continuous_ok(self, session: Session) -> int:
        rows = list(
            session.scalars(
                select(Telemetry15Min)
                .where(Telemetry15Min.plant_id == self.plant_id)
                .order_by(Telemetry15Min.start_time.desc())
            )
        )
        count = 0
        for row in rows:
            if row.quality_flag in ("ok", "interpolated"):
                count += 1
            else:
                break
        return count

    # ── helpers ─────────────────────────────────────────────────────────

    def _count_windows(self, session: Session, quality: str | None = None) -> int:
        stmt = select(func.count()).select_from(Telemetry15Min).where(
            Telemetry15Min.plant_id == self.plant_id
        )
        if quality is not None:
            stmt = stmt.where(Telemetry15Min.quality_flag == quality)
        return session.scalar(stmt) or 0

    def _count_windows_since(self, session: Session, since: datetime) -> int:
        return (
            session.scalar(
                select(func.count())
                .select_from(Telemetry15Min)
                .where(
                    Telemetry15Min.plant_id == self.plant_id,
                    Telemetry15Min.start_time > since,
                )
            )
            or 0
        )

    def _last_run(self, session: Session) -> MpcRun | None:
        return session.scalar(
            select(MpcRun)
            .where(
                MpcRun.plant_id == self.plant_id,
                MpcRun.profile == self.profile,
            )
            .order_by(MpcRun.started_at.desc())
            .limit(1)
        )


# ── background tasks ────────────────────────────────────────────────────────


async def run_health_checker_loop(
    checker: MpcHealthChecker,
    interval_seconds: float = CHECK_INTERVAL_SECONDS,
) -> None:
    logger.info(
        "health checker started: %s/%s (interval=%ss)",
        checker.plant_id, checker.profile, interval_seconds,
    )
    while True:
        try:
            await asyncio.to_thread(checker.tick)
        except Exception:
            logger.exception("tick failed: %s/%s", checker.plant_id, checker.profile)
        await asyncio.sleep(interval_seconds)


async def run_periodic_scheduler(
    scheduler: MpcScheduler,
    session_factory: Callable[[], Session],
) -> None:
    """Wake up at :00, :15, :30, :45 and run all profiles (if enabled)."""
    logger.info("periodic scheduler started: %s", scheduler.plant_id)
    while True:
        now = datetime.now()
        minute = now.minute
        next_minute = ((minute // 15) + 1) * 15
        if next_minute >= 60:
            next_run = now.replace(minute=0, second=5, microsecond=0) + timedelta(hours=1)
        else:
            next_run = now.replace(minute=next_minute, second=5, microsecond=0)
        sleep_seconds = max(1.0, (next_run - now).total_seconds())
        await asyncio.sleep(sleep_seconds)

        try:
            await scheduler.run_all_periodic(session_factory)
        except Exception:
            logger.exception("periodic run failed: %s", scheduler.plant_id)
