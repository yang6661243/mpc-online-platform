from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RawTelemetry(Base):
    """统一原始电表数据，支持 1min/5min 等任意粒度混存。

    Excel 导入或网页插件的数据均落此表。
    (plant_id, time) 唯一，重复写入自动覆盖。
    """
    __tablename__ = "raw_telemetry"
    __table_args__ = (UniqueConstraint("plant_id", "time", name="uq_raw_telemetry_plant_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    grid_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    soc: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="import")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Telemetry15Min(Base):
    __tablename__ = "telemetry_15min"
    __table_args__ = (UniqueConstraint("plant_id", "start_time", "end_time", name="uq_telemetry_plant_window"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    grid_power_kw_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    grid_power_kw_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_power_kw_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_minus_pv_kw_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    soc_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    soc_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    grid_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    battery_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_flag: Mapped[str] = mapped_column(String(64), default="ok")
    buy_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    irradiance_w_m2: Mapped[float | None] = mapped_column(Float, nullable=True)
    # MPC results persisted after successful run
    mpc_grid_power_kw_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_battery_power_kw_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_soc: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_load_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_pv_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )


class MpcRun(Base):
    __tablename__ = "mpc_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    profile: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="created")
    input_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scenario_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class MpcTarget(Base):
    __tablename__ = "mpc_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    profile: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    target_peak_kw: Mapped[float] = mapped_column(Float)
    target_soc: Mapped[float] = mapped_column(Float)
    mode: Mapped[str] = mapped_column(String(32), default="deployable")
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)


class MonthlyDemandRef(Base):
    """User‑supplied monthly reference maximum demand (entered at month start)."""
    __tablename__ = "monthly_demand_refs"
    __table_args__ = (
        UniqueConstraint("plant_id", "year_month", name="uq_monthly_demand_ref_plant_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    year_month: Mapped[str] = mapped_column(String(7), index=True)       # "2026-06"
    reference_peak_kw: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IntraMinutePoint(Base):
    """Per‑minute PCS command produced by the Fuzzy PID decomposition."""
    __tablename__ = "intra_minute_points"
    __table_args__ = (
        UniqueConstraint("run_id", "plant_id", "profile", "time", name="uq_imp_run_plant_profile_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    profile: Mapped[str] = mapped_column(String(32), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    pcs_command_kw: Mapped[float] = mapped_column(Float)
    pcs_actual_kw: Mapped[float] = mapped_column(Float)
    soc_guide: Mapped[float] = mapped_column(Float)
    soc_actual: Mapped[float] = mapped_column(Float)
    grid_power_kw: Mapped[float] = mapped_column(Float)
    grid_target_kw: Mapped[float] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(16), default="safe")
    quality_flag: Mapped[str] = mapped_column(String(32), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StrategyComparison(Base):
    __tablename__ = "strategy_comparison"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    actual_peak_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_peak_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_reduction_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_reduction_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_yuan: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_cost_yuan: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_saving_yuan: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_saving_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class StrategyCurvePoint(Base):
    __tablename__ = "strategy_curve_points"
    __table_args__ = (UniqueConstraint("run_id", "time", name="uq_strategy_curve_run_time"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actual_grid_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_battery_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_soc: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_load_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_pv_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_minus_pv_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_grid_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_battery_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_soc: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_load_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    mpc_pv_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ControlApiOutbox(Base):
    __tablename__ = "control_api_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(String)
    send_status: Mapped[str] = mapped_column(String(32), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ControlApiAck(Base):
    __tablename__ = "control_api_ack"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    plant_id: Mapped[str] = mapped_column(String(64), index=True)
    control_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MpcRunProgress(Base):
    """Per‑step progress of an MPC run, written by the CLI runner."""

    __tablename__ = "mpc_run_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    step: Mapped[int] = mapped_column(Integer)
    total_steps: Mapped[int] = mapped_column(Integer)
    soc: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    running_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    grid_power_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    pv_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
