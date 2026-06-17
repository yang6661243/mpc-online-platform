import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { fetchDashboard, fetchDisplaySeries, fetchMpcStatus, importMpcRun, runMpc, setMonthlyDemandRef, toApiTime } from "./api";
import { displaySeriesToDashboardSeries } from "./displaySeries";
import type { DashboardResponse, DashboardSeriesPoint, DisplaySeriesResponse, MpcHealthStatus, RunMpcResponse } from "./types";
import {
  buildDemandComparisonChartOption,
  buildRevenueComparisonChartOption,
} from "./chartOptions";
import { OptionChart } from "./components/OptionChart";
import { PowerChart } from "./components/PowerChart";
import { RevenueChart } from "./components/RevenueChart";
import { formatKw, formatNumber, formatSoc, formatYuan } from "./format";
import { CONTROL_RAIL_CARD_TITLES, LEFT_CHART_TITLES, OPTIMIZATION_TARGETS, PLANT_OPTIONS, TOPBAR_TIME_LABEL } from "./layout";
import { dashboardStatus } from "./status";
import { formatChinaTime } from "./time";
import "./styles.css";

const DEFAULT_PLANT_ID = "hehong_huajin";
const AUTO_REFRESH_MS = 60_000;

const MONTH_OPTIONS = [
  { label: "实时", value: "realtime" },
  { label: "2026年4月", value: "2026-04" },
  { label: "2026年5月", value: "2026-05" },
  { label: "2026年6月", value: "2026-06" },
] as const;

type MonthValue = (typeof MONTH_OPTIONS)[number]["value"];

function monthToTimeRange(month: MonthValue): { start: string; end: string } | null {
  if (month === "realtime") return null;
  const [y, m] = month.split("-").map(Number);
  const nextM = m === 12 ? 1 : m + 1;
  const nextY = m === 12 ? y + 1 : y;
  const pad = (n: number) => String(n).padStart(2, "0");
  return {
    start: `${y}-${pad(m)}-01T00:00`,
    end: `${nextY}-${pad(nextM)}-01T00:00`,
  };
}

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

function estimateArbitrageRevenue(series: DashboardSeriesPoint[], key: "actual_battery_power_kw" | "mpc_battery_power_kw"): number | null {
  let total = 0;
  let hasValue = false;
  for (const point of series) {
    const batteryPower = point[key];
    if (batteryPower === null || batteryPower === undefined) continue;
    const price = point.buy_price ?? point.sell_price ?? 0.986;
    total += batteryPower * price * 0.25;
    hasValue = true;
  }
  return hasValue ? Math.round(total * 100) / 100 : null;
}

function estimateBatteryCycles(series: DashboardSeriesPoint[], key: "actual_soc" | "mpc_soc"): number | null {
  let previous: number | null = null;
  let movement = 0;
  let hasValue = false;

  for (const point of series) {
    const rawSoc = point[key];
    if (rawSoc === null || rawSoc === undefined) continue;
    const socPercent = rawSoc <= 1 ? rawSoc * 100 : rawSoc;
    if (previous !== null) {
      movement += Math.abs(socPercent - previous);
    }
    previous = socPercent;
    hasValue = true;
  }

  return hasValue ? Math.round((movement / 200) * 100) / 100 : null;
}

function promptForNumber(label: string, current: number | null | undefined): number | null {
  const nextValue = window.prompt(label, current === null || current === undefined ? "" : String(current));
  if (nextValue === null) return null;
  const parsed = Number(nextValue);
  return Number.isFinite(parsed) ? parsed : null;
}

interface ChartFrameProps {
  title: string;
  iconClass: string;
  children: ReactNode;
  expandedChildren: ReactNode;
}

function ChartFrame({ title, iconClass, children, expandedChildren }: ChartFrameProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <article className="chart-card">
      <div className="chart-card-head">
        <div className="chart-title">
          <span className={`flat-icon ${iconClass}`} aria-hidden="true" />
          <h2>{title}</h2>
        </div>
        <button className="icon-button" type="button" onClick={() => setExpanded(true)} aria-label={`放大${title}`}>
          <span className="flat-icon icon-expand" aria-hidden="true" />
        </button>
      </div>
      {children}
      {expanded && (
        <div className="chart-modal" role="dialog" aria-modal="true" aria-label={title}>
          <div className="chart-modal-panel">
            <div className="chart-modal-head">
              <div className="chart-title">
                <span className={`flat-icon ${iconClass}`} aria-hidden="true" />
                <h2>{title}</h2>
              </div>
              <button className="icon-button" type="button" onClick={() => setExpanded(false)} aria-label="关闭放大图表">
                <span className="flat-icon icon-close" aria-hidden="true" />
              </button>
            </div>
            <div className="chart-modal-body">{expandedChildren}</div>
          </div>
        </div>
      )}
    </article>
  );
}

interface KpiCompareCardProps {
  title: string;
  iconClass: string;
  factoryValue: string;
  mpcValue: string;
}

function KpiCompareCard({ title, iconClass, factoryValue, mpcValue }: KpiCompareCardProps) {
  return (
    <div className="compare-card">
      <div className="compare-card-title">
        <span className={`flat-icon ${iconClass}`} aria-hidden="true" />
        <span>{title}</span>
      </div>
      <div className="compare-sides">
        <div>
          <span>工厂策略</span>
          <strong>{factoryValue}</strong>
        </div>
        <div>
          <span>MPC策略</span>
          <strong>{mpcValue}</strong>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [plantId, setPlantId] = useState(plantFromQuery);
  const [runId] = useState(runFromQuery);
  const [windowHours] = useState(24);
  const [selectedMonth, setSelectedMonth] = useState<MonthValue>(() => {
    const urlStart = queryValue("start_time");
    return urlStart ? "realtime" : "realtime";
  });
  const [refreshCount, setRefreshCount] = useState(0);
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [displayData, setDisplayData] = useState<DisplaySeriesResponse | null>(null);
  const [targetSocOverride, setTargetSocOverride] = useState<number | null>(null);
  const [targetDemandOverride, setTargetDemandOverride] = useState<number | null>(null);
  const [optimizationTarget, setOptimizationTarget] = useState(OPTIMIZATION_TARGETS[0].value);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [mpcRunLoading, setMpcRunLoading] = useState(false);
  const [mpcRunError, setMpcRunError] = useState<string | null>(null);
  const [mpcRunResult, setMpcRunResult] = useState<RunMpcResponse | null>(null);
  const [mpcHealth, setMpcHealth] = useState<MpcHealthStatus | null>(null);
  const [plantInfo, setPlantInfo] = useState<{
    name: string; latitude: number; longitude: number;
    pv_capacity_kw: number; battery_power_kw: number;
    battery_capacity_kwh: number; transformer_capacity_kw: number;
  } | null>(null);
  const [plantInfoVisible, setPlantInfoVisible] = useState(false);
  const plantInfoTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handlePlantHover = useCallback(async () => {
    if (plantInfoTimerRef.current) clearTimeout(plantInfoTimerRef.current);
    setPlantInfoVisible(true);
    try {
      const res = await fetch(`/api/v1/plants/${encodeURIComponent(plantId)}/info`);
      if (res.ok) setPlantInfo(await res.json());
      else setPlantInfo(null);
    } catch { setPlantInfo(null); }
  }, [plantId]);

  const handlePlantLeave = useCallback(() => {
    plantInfoTimerRef.current = setTimeout(() => setPlantInfoVisible(false), 200);
  }, []);

  // Poll MPC health status every 5 seconds
  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    async function poll() {
      try {
        const status = await fetchMpcStatus(plantId, optimizationTarget, controller.signal);
        if (active) setMpcHealth(status);
      } catch {
        // silently ignore polling errors; the card shows the last known state
      }
    }

    poll();
    const timer = setInterval(poll, 5000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [plantId, optimizationTarget]);

  const handleImportExcel = useCallback(async () => {
    const input = fileInputRef.current;
    if (!input?.files?.length) return;
    const file = input.files[0];
    setImporting(true);
    setImportError(null);
    try {
      const result = await importMpcRun(plantId, optimizationTarget, file);
      // 导入成功后自动跳转到该 run
      window.history.replaceState(null, "", `?plant_id=${plantId}&run_id=${result.run_id}`);
      window.location.reload();
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "导入失败");
    } finally {
      setImporting(false);
      if (input) input.value = "";
    }
  }, [plantId, optimizationTarget]);

  const handleRunMpc = useCallback(async () => {
    setMpcRunLoading(true);
    setMpcRunError(null);
    setMpcRunResult(null);
    try {
      const now = new Date();
      const startTime = new Date(now.getTime() - 24 * 60 * 60 * 1000);
      const requestId = `web_${Date.now()}`;
      const result = await runMpc({
        request_id: requestId,
        plant_id: plantId,
        start_time: startTime.toISOString(),
        end_time: now.toISOString(),
        profile: optimizationTarget,
      });
      setMpcRunResult(result);
      setRefreshCount((value) => value + 1);
    } catch (err) {
      setMpcRunError(err instanceof Error ? err.message : "MPC 运行失败");
    } finally {
      setMpcRunLoading(false);
    }
  }, [plantId, optimizationTarget]);

  // Monthly demand ref popup — when state is awaiting_demand_ref, prompt user
  useEffect(() => {
    if (mpcHealth?.state !== "awaiting_demand_ref") return;
    const nextValue = window.prompt(
      `【${mpcHealth.plant_id}】本月参考最大需量尚未设置。\n\n请输入本月参考最大需量值（kW）：`,
      "",
    );
    if (nextValue !== null) {
      const parsed = Number(nextValue);
      if (Number.isFinite(parsed) && parsed > 0) {
        setMonthlyDemandRef(plantId, parsed).catch((err) => {
          setMpcRunError(err instanceof Error ? err.message : "设置参考需量失败");
        });
      }
    }
  }, [mpcHealth?.state, mpcHealth?.plant_id, plantId]);

  const rangeStart = useMemo(() => {
    const monthRange = monthToTimeRange(selectedMonth);
    if (monthRange) return monthRange.start;
    const urlStart = queryValue("start_time");
    return urlStart ? toDateTimeLocalValue(urlStart) : "";
  }, [selectedMonth]);

  const rangeEnd = useMemo(() => {
    const monthRange = monthToTimeRange(selectedMonth);
    if (monthRange) return monthRange.end;
    const urlEnd = queryValue("end_time");
    return urlEnd ? toDateTimeLocalValue(urlEnd) : "";
  }, [selectedMonth]);

  const isRealtimeMode = selectedMonth === "realtime" && !runId && !rangeStart && !rangeEnd;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const dashboardPromise = fetchDashboard(
      plantId,
      {
        windowHours,
        runId: runId.trim() || undefined,
        profile: isRealtimeMode ? optimizationTarget : undefined,
        startTime: toApiTime(rangeStart),
        endTime: toApiTime(rangeEnd),
      },
      controller.signal,
    );

    if (!isRealtimeMode) {
      setDisplayData(null);
      dashboardPromise
        .then((nextData) => {
          setData(nextData);
          setError(null);
        })
        .catch((err: Error) => {
          if (err.name !== "AbortError") {
            setData(null);
            setError(err.message);
          }
        })
        .finally(() => setLoading(false));
      return () => controller.abort();
    }

    Promise.allSettled([
      dashboardPromise,
      fetchDisplaySeries(
        plantId,
        {
          windowHours: windowHours === 24 ? 24 : 2,
        },
        controller.signal,
      ),
    ])
      .then(([dashboardResult, displayResult]) => {
        if (dashboardResult.status === "fulfilled") {
          setData(dashboardResult.value);
          setError(null);
        } else if (dashboardResult.reason?.name !== "AbortError") {
          setData(null);
          setError(dashboardResult.reason.message);
        }

        if (displayResult.status === "fulfilled") {
          setDisplayData(displayResult.value);
        } else if (displayResult.reason?.name !== "AbortError") {
          setDisplayData(null);
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [plantId, runId, windowHours, rangeStart, rangeEnd, refreshCount, isRealtimeMode]);

  useEffect(() => {
    if (!isRealtimeMode) return;
    const timer = window.setInterval(() => {
      setRefreshCount((value) => value + 1);
    }, AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [isRealtimeMode]);

  const realtimeSeries = useMemo<DashboardSeriesPoint[]>(() => {
    if (isRealtimeMode && displayData?.series.length) {
      return displaySeriesToDashboardSeries(displayData);
    }
    return data?.series || [];
  }, [data?.series, displayData, isRealtimeMode]);

  const status = useMemo(() => (data ? dashboardStatus(data) : null), [data]);
  const comparison = data?.comparison;
  const latestTime = useMemo(() => {
    if (isRealtimeMode && displayData?.series.length) {
      const lastPoint = displayData.series[displayData.series.length - 1];
      return formatChinaTime(lastPoint.time);
    }
    if (data?.current.time) {
      return formatChinaTime(data.current.time);
    }
    return "--";
  }, [data?.current.time, displayData, isRealtimeMode]);
  const targetPeak = comparison?.mpc_peak_kw ?? comparison?.actual_peak_kw;
  const latestMpcPoint = data?.series
    .slice()
    .reverse()
    .find((point) => point.mpc_soc !== null || point.mpc_battery_power_kw !== null);
  const targetSoc = targetSocOverride ?? latestMpcPoint?.mpc_soc ?? data?.current.soc;
  const targetDemand = targetDemandOverride ?? targetPeak;
  const factoryArbitrageRevenue = estimateArbitrageRevenue(data?.series || [], "actual_battery_power_kw");
  const mpcArbitrageRevenue = estimateArbitrageRevenue(data?.series || [], "mpc_battery_power_kw");
  const factoryBatteryCycles = estimateBatteryCycles(data?.series || [], "actual_soc");
  const mpcBatteryCycles = estimateBatteryCycles(data?.series || [], "mpc_soc");
  const revenueComparisonOption = buildRevenueComparisonChartOption(0, comparison?.cost_saving_yuan);
  const demandComparisonOption = buildDemandComparisonChartOption(comparison?.actual_peak_kw, comparison?.mpc_peak_kw);
  const mpcStatusText = loading ? "运行中" : error ? "异常" : status?.label || "待运行";

  function editTargetSoc() {
    const nextValue = promptForNumber("请输入目标 SOC（%）", targetSoc === null || targetSoc === undefined ? null : targetSoc <= 1 ? targetSoc * 100 : targetSoc);
    if (nextValue !== null) setTargetSocOverride(nextValue);
  }

  function editTargetDemand() {
    const nextValue = promptForNumber("请输入目标/给定需量值（kW）", targetDemand);
    if (nextValue !== null) setTargetDemandOverride(nextValue);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="topbar-time">
          <span>{TOPBAR_TIME_LABEL}</span>
          <strong>{latestTime}</strong>
        </div>
        <h1>则鸣AI+ems实时演示系统</h1>
        <label className="plant-picker"
          onMouseEnter={handlePlantHover}
          onMouseLeave={handlePlantLeave}
        >
          <span>工厂选择</span>
          <select value={plantId} onChange={(event) => setPlantId(event.target.value)}>
            {PLANT_OPTIONS.map((plant) => (
              <option key={plant.value} value={plant.value}>
                {plant.label}
              </option>
            ))}
          </select>
          {plantInfoVisible && (
            <div className="plant-tooltip"
              onMouseEnter={() => {
                if (plantInfoTimerRef.current) clearTimeout(plantInfoTimerRef.current);
                setPlantInfoVisible(true);
              }}
              onMouseLeave={handlePlantLeave}
            >
              {plantInfo ? (
                <>
                  <strong>{plantInfo.name}</strong>
                  <table>
                    <tbody>
                      <tr><td>经纬度</td><td>{plantInfo.latitude}, {plantInfo.longitude}</td></tr>
                      <tr><td>光伏装机</td><td>{plantInfo.pv_capacity_kw} kW</td></tr>
                      <tr><td>储能功率</td><td>{plantInfo.battery_power_kw} kW</td></tr>
                      <tr><td>储能容量</td><td>{plantInfo.battery_capacity_kwh} kWh</td></tr>
                      <tr><td>变压器</td><td>{plantInfo.transformer_capacity_kw} kW</td></tr>
                    </tbody>
                  </table>
                </>
              ) : (
                <span style={{ color: "var(--muted)", fontSize: 12 }}>加载中...</span>
              )}
            </div>
          )}
        </label>
      </header>

      <section className="content">
        <section className="dashboard-grid">
          <aside className="left-chart-rail" aria-label="收益和需量图表">
            <ChartFrame
              title={LEFT_CHART_TITLES[0]}
              iconClass="icon-bars"
              expandedChildren={
                <OptionChart option={revenueComparisonOption} emptyText="暂无收益对比数据" className="modal-chart" />
              }
            >
              <OptionChart option={revenueComparisonOption} emptyText="暂无收益对比数据" className="mini-chart" />
            </ChartFrame>

            <ChartFrame
              title={LEFT_CHART_TITLES[1]}
              iconClass="icon-trend"
              expandedChildren={<RevenueChart series={data?.series || []} />}
            >
              <RevenueChart series={data?.series || []} />
            </ChartFrame>

            <ChartFrame
              title={LEFT_CHART_TITLES[2]}
              iconClass="icon-demand"
              expandedChildren={
                <OptionChart option={demandComparisonOption} emptyText="暂无需量对比数据" className="modal-chart" />
              }
            >
              <OptionChart option={demandComparisonOption} emptyText="暂无需量对比数据" className="mini-chart" />
            </ChartFrame>
          </aside>

          <section className="center-stage">
            <article className="main-panel">
              <div className="main-panel-head">
                <div className="chart-title">
                  <span className="flat-icon icon-realtime" aria-hidden="true" />
                  <h2>实时数据对比</h2>
                </div>
                <div className="main-panel-head-actions">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".xlsx,.xls"
                    style={{ display: "none" }}
                    onChange={handleImportExcel}
                    aria-label="导入 MPC 结果 Excel"
                  />
                  <select
                    className="month-picker"
                    value={selectedMonth}
                    onChange={(event) => setSelectedMonth(event.target.value as MonthValue)}
                    aria-label="选择数据月份"
                  >
                    {MONTH_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="compare-grid">
                <KpiCompareCard
                  title="累计收益"
                  iconClass="icon-yuan"
                  factoryValue={formatYuan(0)}
                  mpcValue={formatYuan(comparison?.cost_saving_yuan)}
                />
                <KpiCompareCard
                  title="最大需量"
                  iconClass="icon-peak"
                  factoryValue={formatKw(comparison?.actual_peak_kw)}
                  mpcValue={formatKw(comparison?.mpc_peak_kw)}
                />
                <KpiCompareCard
                  title="峰谷套利收益"
                  iconClass="icon-arbitrage"
                  factoryValue={formatYuan(factoryArbitrageRevenue)}
                  mpcValue={formatYuan(mpcArbitrageRevenue)}
                />
                <KpiCompareCard
                  title="电池循环次数"
                  iconClass="icon-cycle"
                  factoryValue={formatNumber(factoryBatteryCycles, 2)}
                  mpcValue={formatNumber(mpcBatteryCycles, 2)}
                />
              </div>

              <ChartFrame
                title="净负荷 / 电网功率 / 储能功率 / SOC"
                iconClass="icon-trend"
                expandedChildren={<PowerChart series={realtimeSeries} />}
              >
                <PowerChart series={realtimeSeries} />
              </ChartFrame>
            </article>
          </section>

          <aside className="control-rail" aria-label="状态及控制栏">
            <div className={`rail-card rail-highlight ${mpcHealth ? `status-${mpcHealth.state}` : ""}`}>
              <span>{CONTROL_RAIL_CARD_TITLES[0]}</span>
              <strong className="mpc-status-text">
                {mpcHealth ? mpcHealth.label : mpcStatusText}
              </strong>
              {mpcHealth?.continuous_from_month_start === false && mpcHealth.telemetry_windows > 0 && (
                <small className="mpc-status-hint">本月数据非连续</small>
              )}
              {mpcHealth?.has_gap && (
                <small className="mpc-status-hint gap-warn">缺口 {mpcHealth.max_gap_minutes} 分钟</small>
              )}
              {mpcHealth?.minutes_since_last_run != null && mpcHealth.last_run_status === "succeeded" && (
                <small className="mpc-status-hint">{Math.round(mpcHealth.minutes_since_last_run)} 分钟前运行</small>
              )}
              <button
                type="button"
                className="mini-action"
                onClick={handleRunMpc}
                disabled={mpcRunLoading || mpcHealth?.state === "running"}
              >
                {mpcRunLoading || mpcHealth?.state === "running" ? "运行中..." : "启动"}
              </button>
            </div>
            <div className="rail-card editable-card">
              <span>{CONTROL_RAIL_CARD_TITLES[1]}</span>
              <strong>{formatSoc(targetSoc)}</strong>
              <button type="button" className="mini-action" onClick={editTargetSoc}>
                修改
              </button>
            </div>
            <div className="rail-card editable-card">
              <span>{CONTROL_RAIL_CARD_TITLES[2]}</span>
              <strong>{formatKw(targetDemand)}</strong>
              <button type="button" className="mini-action" onClick={editTargetDemand}>
                修改
              </button>
            </div>
            <div className="rail-card optimization-card">
              <span>{CONTROL_RAIL_CARD_TITLES[3]}</span>
              <div className="piano-switch" role="group" aria-label="优化目标">
                {OPTIMIZATION_TARGETS.map((target) => (
                  <button
                    key={target.value}
                    type="button"
                    className={optimizationTarget === target.value ? "active" : ""}
                    onClick={() => setOptimizationTarget(target.value)}
                  >
                    {target.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="rail-card data-import-card">
              <span>{CONTROL_RAIL_CARD_TITLES[5]}</span>
              <button
                type="button"
                className="import-action"
                onClick={() => fileInputRef.current?.click()}
                disabled={importing}
                title={`导入线下 MPC 结果（${optimizationTarget}）`}
              >
                {importing ? "导入中..." : "导入电站历史数据"}
              </button>
              {importError && <span className="import-error" title={importError}>导入失败</span>}
            </div>
          </aside>
        </section>
      </section>

      {(mpcRunError || mpcRunResult) && (
        <div className="chart-modal" role="dialog" aria-modal="true" aria-label={mpcRunError ? "MPC 运行失败" : "MPC 运行完成"}>
          <div className="chart-modal-panel" style={{ maxWidth: 440 }}>
            <div className="chart-modal-head">
              <h2>{mpcRunError ? "MPC 运行失败" : "MPC 运行完成"}</h2>
              <button
                className="icon-button"
                type="button"
                onClick={() => { setMpcRunError(null); setMpcRunResult(null); }}
                aria-label="关闭"
              >
                <span className="flat-icon icon-close" aria-hidden="true" />
              </button>
            </div>
            <div style={{ padding: "16px 0", lineHeight: 1.7 }}>
              {mpcRunError ? (
                <p style={{ color: "var(--warn)", margin: 0 }}>{mpcRunError}</p>
              ) : mpcRunResult ? (
                <div style={{ color: "var(--muted-strong)" }}>
                  <p style={{ margin: "0 0 8px" }}>
                    Run ID: <code style={{ color: "var(--green)" }}>{mpcRunResult.run_id}</code>
                  </p>
                  <p style={{ margin: "0 0 8px" }}>
                    状态: <span style={{ color: "var(--green)" }}>{mpcRunResult.status}</span>
                  </p>
                  {mpcRunResult.comparison && (
                    <>
                      <p style={{ margin: "0 0 4px" }}>
                        MPC 最大需量: {mpcRunResult.comparison.mpc_peak_kw != null ? `${(mpcRunResult.comparison.mpc_peak_kw).toFixed(1)} kW` : "--"}
                      </p>
                      <p style={{ margin: 0 }}>
                        预计节省: {mpcRunResult.comparison.cost_saving_yuan != null ? `${(mpcRunResult.comparison.cost_saving_yuan).toFixed(2)} 元` : "--"}
                      </p>
                    </>
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
