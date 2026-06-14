import { useEffect, useMemo, useState } from "react";
import { fetchDashboard } from "./api";
import { BatteryChart } from "./components/BatteryChart";
import { DetailTable } from "./components/DetailTable";
import { MetricCard } from "./components/MetricCard";
import { PowerChart } from "./components/PowerChart";
import { RevenueChart } from "./components/RevenueChart";
import { StatusBar } from "./components/StatusBar";
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

export default function App() {
  const [plantId, setPlantId] = useState(plantFromQuery);
  const [runId, setRunId] = useState(runFromQuery);
  const [windowHours, setWindowHours] = useState(24);
  const [refreshCount, setRefreshCount] = useState(0);
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastLoadedAt, setLastLoadedAt] = useState<Date | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchDashboard(plantId, windowHours, runId.trim() || undefined, controller.signal)
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
  }, [plantId, runId, windowHours, refreshCount]);

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
  const targetSoc =
    data?.series
      .slice()
      .reverse()
      .find((point) => point.mpc_soc !== null)?.mpc_soc ?? data?.current.soc;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="topbar-time">
          <span>当前数据</span>
          <strong>{latestTime}</strong>
        </div>
        <h1>和宏华进 MPC 实时演示系统</h1>
        <div className="toolbar">
          <label>
            工厂
            <input value={plantId} onChange={(event) => setPlantId(event.target.value)} />
          </label>
          <label>
            运行ID
            <input value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="可选" />
          </label>
          <label>
            窗口
            <select value={windowHours} onChange={(event) => setWindowHours(Number(event.target.value))}>
              <option value={24}>最近24小时</option>
              <option value={48}>最近48小时</option>
            </select>
          </label>
          <button type="button" onClick={() => setRefreshCount((value) => value + 1)}>
            刷新
          </button>
          <span className="refresh-meta">
            自动刷新 60秒{lastLoadedAt ? ` · ${formatChinaTime(lastLoadedAt.toISOString())}` : ""}
          </span>
        </div>
      </header>

      <section className="content">
        {loading && <StatusBar label="数据加载中" tone="pending" />}
        {error && <StatusBar label={`暂无实时数据：${error}`} tone="error" />}
        {status && <StatusBar label={status.label} tone={status.tone} />}

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
              <MetricCard label="工厂最大需量" value={formatKw(comparison?.actual_peak_kw)} sub="当前策略" />
              <MetricCard label="MPC 最大需量" value={formatKw(comparison?.mpc_peak_kw)} sub="优化策略" />
              <MetricCard
                label="削峰量"
                value={formatKw(comparison?.peak_reduction_kw)}
                sub={formatPercent(comparison?.peak_reduction_pct)}
              />
              <MetricCard
                label="预计节省"
                value={formatYuan(comparison?.cost_saving_yuan)}
                sub={formatPercent(comparison?.cost_saving_pct)}
              />
            </div>
          </aside>

          <section className="center-stage">
            <article className="panel power-panel">
              <div className="panel-head">
                <div>
                  <span className="panel-kicker">运行数据</span>
                  <h2>实时功率曲线</h2>
                </div>
                <p>工厂当前策略与 MPC 策略对比</p>
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

            <section className="bottom-grid">
              <article className="panel">
                <div className="panel-head">
                  <div>
                    <span className="panel-kicker">能量转换与输出</span>
                    <h2>储能功率与 SOC</h2>
                  </div>
                  <p>正值放电，负值充电</p>
                </div>
                <BatteryChart series={data?.series || []} />
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
          </section>

          <aside className="status-rail">
            <div className="rail-card rail-highlight">
              <span>MPC 状态</span>
              <strong>{status?.label || "等待数据"}</strong>
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
          </aside>
        </section>
      </section>
    </main>
  );
}
