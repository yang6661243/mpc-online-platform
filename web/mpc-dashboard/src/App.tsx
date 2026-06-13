import { useEffect, useMemo, useState } from "react";
import { fetchDashboard } from "./api";
import { formatKw, formatSoc, shortTime } from "./format";
import { dashboardStatus } from "./status";
import type { DashboardResponse } from "./types";
import "./styles.css";

const DEFAULT_PLANT_ID = "ecloud_factory";

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

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchDashboard(plantId, windowHours, controller.signal)
      .then(setData)
      .catch((err: Error) => {
        if (err.name !== "AbortError") {
          setData(null);
          setError(err.message);
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [plantId, windowHours, refreshCount]);

  const status = useMemo(() => (data ? dashboardStatus(data) : null), [data]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">虚拟电厂 MPC</p>
          <h1>MPC 策略对比看板</h1>
          <p className="subtitle">
            {data ? `${data.plant_id} · 最新数据 ${shortTime(data.current.time)}` : "等待数据"}
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
        </div>
      </header>

      <section className="content">
        {loading && <div className="status pending">数据加载中</div>}
        {error && <div className="status error">暂无实时数据：{error}</div>}
        {status && <div className={`status ${status.tone}`}>{status.label}</div>}

        <section className="metric-grid">
          <article className="metric-card">
            <span>当前电网功率</span>
            <strong>{formatKw(data?.current.grid_power_kw)}</strong>
          </article>
          <article className="metric-card">
            <span>当前储能功率</span>
            <strong>{formatKw(data?.current.battery_power_kw)}</strong>
          </article>
          <article className="metric-card">
            <span>当前 SOC</span>
            <strong>{formatSoc(data?.current.soc)}</strong>
          </article>
          <article className="metric-card">
            <span>数据质量</span>
            <strong>{data?.current.quality_flag || "--"}</strong>
          </article>
        </section>
      </section>
    </main>
  );
}
