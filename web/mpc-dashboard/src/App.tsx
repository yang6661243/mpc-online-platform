import { useEffect, useMemo, useState } from "react";
import { fetchDashboard } from "./api";
import { BatteryChart } from "./components/BatteryChart";
import { DetailTable } from "./components/DetailTable";
import { MetricCard } from "./components/MetricCard";
import { PowerChart } from "./components/PowerChart";
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

export default function App() {
  const [plantId, setPlantId] = useState(plantFromQuery);
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
    fetchDashboard(plantId, windowHours, controller.signal)
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
  }, [plantId, windowHours, refreshCount]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setRefreshCount((value) => value + 1);
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);

  const status = useMemo(() => (data ? dashboardStatus(data) : null), [data]);
  const comparison = data?.comparison;
  const dataDelay = formatDataDelay(data?.current.time);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">虚拟电厂 MPC</p>
          <h1>MPC 策略对比看板</h1>
          <p className="subtitle">
            {data ? `${data.plant_id} · 最新数据 ${formatChinaTime(data.current.time)}` : "等待数据"}
          </p>
        </div>
        <div className="toolbar">
          <label>
            工厂
            <input value={plantId} onChange={(event) => setPlantId(event.target.value)} />
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
            自动刷新 60秒{lastLoadedAt ? ` · 已刷新 ${formatChinaTime(lastLoadedAt.toISOString())}` : ""}
          </span>
        </div>
      </header>

      <section className="content">
        {loading && <StatusBar label="数据加载中" tone="pending" />}
        {error && <StatusBar label={`暂无实时数据：${error}`} tone="error" />}
        {status && <StatusBar label={status.label} tone={status.tone} />}

        <section className="metric-grid">
          <MetricCard label="当前电网功率" value={formatKw(data?.current.grid_power_kw)} sub="防逆流表聚合值" />
          <MetricCard label="当前储能功率" value={formatKw(data?.current.battery_power_kw)} sub="储能计量表聚合值" />
          <MetricCard label="当前 SOC" value={formatSoc(data?.current.soc)} sub="BMS 系统 SOC" />
          <MetricCard label="数据延迟" value={dataDelay} sub="按北京时间计算" />
          <MetricCard label="数据质量" value={data?.current.quality_flag || "--"} sub="15分钟聚合窗口" />
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
        </section>

        <section className="panel-grid">
          <article className="panel">
            <div className="panel-head">
              <h2>电网功率对比</h2>
              <p>工厂当前策略与 MPC 策略的最大需量对比</p>
            </div>
            <PowerChart series={data?.series || []} />
          </article>

          <article className="panel">
            <div className="panel-head">
              <h2>储能功率与 SOC</h2>
              <p>观察储能动作是否连续、SOC 是否在安全范围内</p>
            </div>
            <BatteryChart series={data?.series || []} />
          </article>

          <article className="panel">
            <div className="panel-head">
              <h2>15分钟策略明细</h2>
              <p>实际值与 MPC 输出逐点对照</p>
            </div>
            <DetailTable series={data?.series || []} />
          </article>
        </section>
      </section>
    </main>
  );
}
