"""MPC health checker — per‑profile background task.

Each (plant_id, profile) pair gets its own MpcHealthChecker that:
1. Periodically assesses data completeness and continuity.
2. Detects and fills small gaps (≤ 5 min) via ``gap_filler``.
3. Checks month‑start continuity; blocks auto‑run when the monthly reference
   demand has not been entered by the user (→ ``awaiting_demand_ref``).
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
AUTO_TRIGGER_MIN_NEW_WINDOWS = 4
MAX_GAP_MINUTES = 5

# ── state constants ─────────────────────────────────────────────────────────

STATE_IDLE = "idle"
STATE_ACCUMULATING = "accumulating"
STATE_PARTIAL_MONTH = "partial_month"
STATE_AWAITING_DEMAND_REF = "awaiting_demand_ref"
STATE_READY = "ready"
STATE_CATCHING_UP = "catching_up"
STATE_RUNNING = "running"
STATE_RUNNING_FUZZY = "running_fuzzy"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_DATA_GAP = "data_gap"

STATE_LABELS: dict[str, str] = {
    STATE_IDLE: "等待数据",
    STATE_ACCUMULATING: "数据积累中",
    STATE_PARTIAL_MONTH: "月初数据缺失",
    STATE_AWAITING_DEMAND_REF: "等待输入本月参考需量",
    STATE_READY: "数据就绪",
    STATE_CATCHING_UP: "追赶中",
    STATE_RUNNING: "MPC 运行中",
    STATE_RUNNING_FUZZY: "Fuzzy PID 分解中",
    STATE_SUCCEEDED: "运行成功",
    STATE_FAILED: "运行失败",
    STATE_DATA_GAP: "数据中断",
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
    continuous_from_month_start: bool = False
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
    checked_at: str = ""


# ── MPC runner protocol ─────────────────────────────────────────────────────

RunMpcFunc = Callable[[str, str, str, datetime, datetime], str | None]
"""Signature: (plant_id, profile, run_id, from_time, to_time) → run_id or None."""

RunFuzzyPidFunc = Callable[[str, str, str, datetime, datetime], list]
"""Signature: (plant_id, profile, run_id, from_time, to_time) → list of points."""


# ── MPC scheduler (plant‑level serialisation) ───────────────────────────────


class MpcScheduler:
    """Coordinates MPC runs across profiles within one plant.

    - Catch‑up phase: profiles run round‑robin serially.
    - Periodic phase: at :00/:15/:30/:45 each hour, all profiles run serially.
    - Only one profile runs at a time per plant (different plants may run in
      parallel via separate scheduler instances).
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

    async def maybe_trigger(
        self,
        session_factory: Callable[[], Session],
    ) -> None:
        """Called from each checker tick.  If this plant has a runnable
        profile and the scheduler is free, run it.
        """
        if self._lock.locked():
            return  # a profile is already running

        # Check if we are in a periodic window
        now = datetime.now()
        in_periodic_window = now.minute in PERIODIC_TRIGGER_MINUTES and now.second < 60

        # Only trigger at most once per minute window
        trigger_key = now.replace(second=0, microsecond=0)
        if in_periodic_window:
            if self._last_periodic_trigger == trigger_key:
                return
        else:
            # Catch‑up mode: run whenever a profile is ready
            pass

        session = session_factory()
        try:
            for profile in self.profiles:
                status = self._assess_profile(session, profile)
                if status.state in (STATE_READY, STATE_CATCHING_UP, STATE_FAILED):
                    async with self._lock:
                        await self._run_profile_chain(session, profile)
                    if in_periodic_window:
                        self._last_periodic_trigger = trigger_key
                    break  # one per tick in catch‑up; in periodic we run all
        finally:
            session.close()

    async def run_all_periodic(
        self,
        session_factory: Callable[[], Session],
    ) -> None:
        """Run all profiles serially (periodic phase)."""
        async with self._lock:
            session = session_factory()
            try:
                for profile in self.profiles:
                    await self._run_profile_chain(session, profile)
            finally:
                session.close()

    async def _run_profile_chain(self, session: Session, profile: str) -> None:
        """Execute the full chain for one profile: MPC → Fuzzy PID."""
        from api.mpc_run import run_online_mpc, make_run_id

        plant_id = self.plant_id
        run_id = make_run_id(f"auto_{plant_id}_{profile}_{datetime.now():%Y%m%d_%H%M%S}")

        # Determine time range from telemetry
        rows = list(
            session.scalars(
                select(Telemetry15Min)
                .where(Telemetry15Min.plant_id == plant_id)
                .order_by(Telemetry15Min.start_time)
            )
        )
        if not rows:
            return
        from_time = rows[0].start_time
        to_time = rows[-1].end_time or rows[-1].start_time + timedelta(minutes=15)

        # Mark as running
        run = MpcRun(
            run_id=run_id,
            plant_id=plant_id,
            profile=profile,
            status="running",
            started_at=datetime.now(),
        )
        session.add(run)
        session.commit()

        try:
            logger.info("MPC starting: %s/%s run=%s", plant_id, profile, run_id)
            self._run_mpc(plant_id, profile, run_id, from_time, to_time)
            run.status = "succeeded"
            run.finished_at = datetime.now()
            session.commit()

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

    def _assess(self, session: Session) -> MpcHealthStatus:
        now = datetime.now()
        total = self._count_windows(session)
        ok_count = self._count_windows(session, quality="ok")
        interp_count = self._count_windows(session, quality="interpolated")
        month_continuous = self._check_month_start_continuity(session)
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

        state = self._determine_state(
            total=total,
            month_continuous=month_continuous,
            has_gap=has_gap,
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
            continuous_from_month_start=month_continuous,
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
            checked_at=now.isoformat(),
        )

    # ── state machine ───────────────────────────────────────────────────

    def _determine_state(
        self,
        *,
        total: int,
        month_continuous: bool,
        has_gap: bool,
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
        if total < self.horizon_steps:
            return STATE_ACCUMULATING
        if not month_continuous:
            return STATE_PARTIAL_MONTH
        # Data is sufficient and month‑continuous — check demand ref
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

    def _check_month_start_continuity(self, session: Session) -> bool:
        now = datetime.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        first = session.scalar(
            select(Telemetry15Min)
            .where(
                Telemetry15Min.plant_id == self.plant_id,
                Telemetry15Min.start_time >= month_start,
            )
            .order_by(Telemetry15Min.start_time)
            .limit(1)
        )
        if first is None:
            return False
        if first.start_time and (first.start_time - month_start) > timedelta(minutes=30):
            return False

        expected = first.start_time
        rows = list(
            session.scalars(
                select(Telemetry15Min)
                .where(
                    Telemetry15Min.plant_id == self.plant_id,
                    Telemetry15Min.start_time >= first.start_time,
                )
                .order_by(Telemetry15Min.start_time)
            )
        )
        for row in rows:
            if row.start_time is None:
                continue
            gap = (row.start_time - expected).total_seconds()
            if gap > 15 * 60 + 30:
                return False
            if row.quality_flag not in ("ok", "interpolated"):
                return False
            expected = row.end_time if row.end_time else row.start_time + timedelta(minutes=15)

        latest = rows[-1].end_time if rows else None
        if latest is None or (now - latest) > timedelta(minutes=30):
            return False
        return True

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
    """Wake up at :00, :15, :30, :45 and run all profiles."""
    logger.info("periodic scheduler started: %s", scheduler.plant_id)
    while True:
        now = datetime.now()
        # Sleep until next 15‑minute boundary + 5 s (allow data to settle)
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
