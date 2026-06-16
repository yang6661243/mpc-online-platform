import { useEffect, useMemo, useState, type ReactNode } from "react";
import { fetchDashboard, fetchDisplaySeries, toApiTime } from "./api";
import { displaySeriesToDashboardSeries } from "./displaySeries";
import type { DashboardResponse, DashboardSeriesPoint, DisplaySeriesResponse } from "./types";
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
        <label className="plant-picker">
          <span>工厂选择</span>
          <select value={plantId} onChange={(event) => setPlantId(event.target.value)}>
            {PLANT_OPTIONS.map((plant) => (
              <option key={plant.value} value={plant.value}>
                {plant.label}
              </option>
            ))}
          </select>
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
            <div className="rail-card rail-highlight">
              <span>{CONTROL_RAIL_CARD_TITLES[0]}</span>
              <strong className="mpc-status-text">{mpcStatusText}</strong>
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
              <button type="button" className="import-action">
                导入电站历史数据
              </button>
            </div>
          </aside>
        </section>
      </section>
    </main>
  );
}
