import { useEffect, useMemo, useState } from "react";
import { fetchDashboard, toApiTime } from "./api";
import { DetailTable } from "./components/DetailTable";
import { MetricCard } from "./components/MetricCard";
import { PowerChart } from "./components/PowerChart";
import { RevenueChart } from "./components/RevenueChart";
import { formatKw, formatPercent, formatSoc, formatYuan } from "./format";
import { dashboardStatus } from "./status";
import { formatChinaTime, formatDataDelay } from "./time";
import type { DashboardResponse } from "./types";
import "./styles.css";

const DEFAULT_PLANT_ID = "hehong_huajin";
const AUTO_REFRESH_MS = 60_000;

function plantFromQuery(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get("plant_id") || DEFAULT_PLANT_ID;
}

function runFromQuery(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get("run_id") || "";
}

function queryValue(name: string): string {
  const params = new URLSearchParams(window.location.search);
  return params.get(name) || "";
}

function toDateTimeLocalValue(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function monthRange(year: number, month: number): { start: string; end: string } {
  const start = new Date(year, month - 1, 1, 0, 0, 0);
  const nextMonthStart = new Date(year, month, 1, 0, 0, 0);
  const end = new Date(nextMonthStart.getTime() - 60_000);
  return {
    start: toDateTimeLocalValue(start.toISOString()),
    end: toDateTimeLocalValue(end.toISOString()),
  };
}

export default function App() {
  const [plantId] = useState(plantFromQuery);
  const [runId] = useState(runFromQuery);
  const [windowHours, setWindowHours] = useState(24);
  const [rangeStart, setRangeStart] = useState(() => toDateTimeLocalValue(queryValue("start_time")));
  const [rangeEnd, setRangeEnd] = useState(() => toDateTimeLocalValue(queryValue("end_time")));
  const [refreshCount, setRefreshCount] = useState(0);
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastLoadedAt, setLastLoadedAt] = useState<Date | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchDashboard(
      plantId,
      {
        windowHours,
        runId: runId.trim() || undefined,
        startTime: toApiTime(rangeStart),
        endTime: toApiTime(rangeEnd),
      },
      controller.signal,
    )
      .then((nextData) => {
        setData(nextData);
        setLastLoadedAt(new Date());
      })
      .catch((err: Error) => {
        if (err.name !== "AbortError") {
          setData(null);
          setError(err.message);
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [plantId, runId, windowHours, rangeStart, rangeEnd, refreshCount]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setRefreshCount((value) => value + 1);
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);

  const status = useMemo(() => (data ? dashboardStatus(data) : null), [data]);
  const comparison = data?.comparison;
  const dataDelay = formatDataDelay(data?.current.time);
  const latestTime = data ? formatChinaTime(data.current.time) : "--";
  const targetPeak = comparison?.mpc_peak_kw ?? comparison?.actual_peak_kw;
  const latestMpcPoint = data?.series
    .slice()
    .reverse()
    .find((point) => point.mpc_soc !== null || point.mpc_battery_power_kw !== null);
  const targetSoc = latestMpcPoint?.mpc_soc ?? data?.current.soc;
  const selectedMonth = useMemo(() => {
    const match = rangeStart.match(/^(\d{4})-(\d{2})-/);
    return match ? String(Number(match[2])) : "";
  }, [rangeStart]);

  function selectMonth(month: number) {
    const range = monthRange(2026, month);
    setRangeStart(range.start);
    setRangeEnd(range.end);
  }

  function selectRealtime() {
    setRangeStart("");
    setRangeEnd("");
    setWindowHours(24);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <h1>则鸣 EMS 实时演示系统</h1>
      </header>

      <section className="content">
        <section className="dashboard-grid">
          <aside className="stat-sidebar">
            <div className="section-title">
              <span />
              <h2>统计数据</h2>
            </div>
            <div className="metric-grid">
              <MetricCard label="当前电网功率" value={formatKw(data?.current.grid_power_kw)} sub="防逆流表聚合值" />
              <MetricCard label="当前储能功率" value={formatKw(data?.current.battery_power_kw)} sub="储能计量表聚合值" />
              <MetricCard label="当前 SOC" value={formatSoc(data?.current.soc)} sub="BMS 系统 SOC" />
              <MetricCard label="数据延迟" value={dataDelay} sub="按北京时间计算" />
            </div>
            <div className="reserved-sidebar-space" aria-hidden="true" />
          </aside>

          <section className="center-stage">
            <section className="overview-panel">
              <div className="month-selector" aria-label="时间段选择">
                {[4, 5, 6].map((month) => (
                  <button
                    key={month}
                    type="button"
                    className={selectedMonth === String(month) ? "active" : ""}
                    onClick={() => selectMonth(month)}
                  >
                    {month}月
                  </button>
                ))}
                <button type="button" className={!rangeStart && !rangeEnd ? "active" : ""} onClick={selectRealtime}>
                  实时
                </button>
              </div>
              <div className="overview-grid">
                <div>
                  <span>工厂最大需量</span>
                  <strong>{formatKw(comparison?.actual_peak_kw)}</strong>
                </div>
                <div>
                  <span>MPC 最大需量</span>
                  <strong>{formatKw(comparison?.mpc_peak_kw)}</strong>
                </div>
                <div>
                  <span>削峰量</span>
                  <strong>{formatKw(comparison?.peak_reduction_kw)}</strong>
                  <small>{formatPercent(comparison?.peak_reduction_pct)}</small>
                </div>
                <div>
                  <span>预计节省</span>
                  <strong>{formatYuan(comparison?.cost_saving_yuan)}</strong>
                  <small>{formatPercent(comparison?.cost_saving_pct)}</small>
                </div>
              </div>
            </section>

            <article className="panel power-panel">
              <div className="panel-head">
                <div>
                  <span className="panel-kicker">策略曲线</span>
                  <h2>工厂策略与 MPC 策略对比</h2>
                </div>
                <p>实线为工厂策略，虚线为 MPC 策略；默认展示电网功率，可在图例切换负荷、光伏、储能和 SOC</p>
              </div>
              <PowerChart series={data?.series || []} />
            </article>

            <article className="panel revenue-panel">
              <div className="panel-head">
                <div>
                  <span className="panel-kicker">收益数据</span>
                  <h2>实时收益曲线</h2>
                </div>
                <p>按 15 分钟功率差和购电价估算累计收益</p>
              </div>
              <RevenueChart series={data?.series || []} />
            </article>

            <article className="panel detail-panel">
              <div className="panel-head">
                <div>
                  <span className="panel-kicker">历史数据</span>
                  <h2>15分钟策略明细</h2>
                </div>
                <p>实际值与 MPC 输出逐点对照</p>
              </div>
              <DetailTable series={data?.series || []} />
            </article>
          </section>

          <aside className="status-rail">
            <div className="rail-card rail-highlight">
              <span>MPC 状态</span>
              <strong>{loading ? "数据加载中" : error ? "数据异常" : status?.label || "等待数据"}</strong>
            </div>
            <div className="rail-card">
              <span>当前数据</span>
              <strong>{latestTime}</strong>
            </div>
            <div className="rail-card">
              <span>数据质量</span>
              <strong>{data?.current.quality_flag || "--"}</strong>
            </div>
            <div className="rail-card">
              <span>目标峰值</span>
              <strong>{formatKw(targetPeak)}</strong>
            </div>
            <div className="rail-card">
              <span>目标 SOC</span>
              <strong>{formatSoc(targetSoc)}</strong>
            </div>
            <div className="rail-card">
              <span>电站 ID</span>
              <strong>{data?.plant_id || plantId}</strong>
            </div>
            <div className="rail-card">
              <span>刷新时间</span>
              <strong>{lastLoadedAt ? formatChinaTime(lastLoadedAt.toISOString()) : "--"}</strong>
            </div>
          </aside>
        </section>
      </section>
    </main>
  );
}
