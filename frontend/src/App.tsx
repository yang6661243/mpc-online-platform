import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { clearData, fetchDashboard, fetchDisplaySeries, fetchMonthlyDemandRef, fetchMpcProgress, fetchMpcProgressHistory, fetchMpcStatus, importRawData, runMpc, setMonthlyDemandRef, toApiTime } from "./api";
import { displaySeriesToDashboardSeries } from "./displaySeries";
import type { DashboardResponse, DashboardSeriesPoint, DisplaySeriesResponse, ImportRawDataResponse, MpcHealthStatus, MpcProgress, RunMpcResponse } from "./types";
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
const APP_FRONTEND_VERSION = "0.1.18";  // XX 部分，改前端代码时 +1
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
    // actual: 充电为正(需取反); mpc: solver输出放电为正(不变)
    const sign = key === "actual_battery_power_kw" ? -1 : 1;
    total += batteryPower * price * 0.25 * sign;
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
  info?: string;
}

function ChartFrame({ title, iconClass, children, expandedChildren, info }: ChartFrameProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <article className="chart-card">
      <div className="chart-card-head">
        <div className="chart-title">
          <span className={`flat-icon ${iconClass}`} aria-hidden="true" />
          <h2>{title}</h2>
          {info && (
            <span className="info-badge" aria-label={info} data-tooltip={info}>
              !
            </span>
          )}
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
                {info && (
                  <span className="info-badge" aria-label={info} data-tooltip={info}>
                    !
                  </span>
                )}
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
  const [rawImporting, setRawImporting] = useState(false);
  const [rawImportError, setRawImportError] = useState<string | null>(null);
  const [rawImportResult, setRawImportResult] = useState<ImportRawDataResponse | null>(null);
  const [rawImportStep, setRawImportStep] = useState("");
  const rawFileInputRef = useRef<HTMLInputElement>(null);
  const [clearLoading, setClearLoading] = useState(false);
  const [clearError, setClearError] = useState<string | null>(null);
  const [clearResult, setClearResult] = useState<string | null>(null);
  const [mpcRunLoading, setMpcRunLoading] = useState(false);
  const [mpcRunError, setMpcRunError] = useState<string | null>(null);
  const [mpcRunResult, setMpcRunResult] = useState<RunMpcResponse | null>(null);
  const [mpcHealth, setMpcHealth] = useState<MpcHealthStatus | null>(null);
  const [mpcProgress, setMpcProgress] = useState<MpcProgress | null>(null);
  const [mpcProgressHistory, setMpcProgressHistory] = useState<MpcProgress[]>([]);
  const [plantInfo, setPlantInfo] = useState<{
    name: string; latitude: number; longitude: number;
    pv_capacity_kw: number; battery_power_kw: number;
    battery_capacity_kwh: number; transformer_capacity_kw: number;
  } | null>(null);
  const [backendVersion, setBackendVersion] = useState<string>("00");
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

  // Fetch backend version on mount
  useEffect(() => {
    fetch("/api/v1/version")
      .then(r => r.json())
      .then(d => setBackendVersion(String(d.backend_version).padStart(2, "0")))
      .catch(() => setBackendVersion("??"));
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

  useEffect(() => {
    const controller = new AbortController();
    fetchMonthlyDemandRef(plantId, controller.signal)
      .then((ref) => {
        setTargetDemandOverride(ref.reference_peak_kw);
      })
      .catch((err: Error) => {
        if (err.name !== "AbortError") setTargetDemandOverride(null);
      });
    return () => controller.abort();
  }, [plantId, refreshCount]);

  // Poll MPC progress when running (every 3 seconds)
  useEffect(() => {
    const isRunning = mpcHealth?.state === "running" || mpcHealth?.state === "running_fuzzy";
    const runId = isRunning ? mpcHealth.last_run_id : null;
    if (!runId) {
      setMpcProgress(null);
      return;
    }
    let active = true;
    const controller = new AbortController();

    async function poll() {
      try {
        const result = await fetchMpcProgress(runId!, controller.signal);
        if (active) setMpcProgress("step" in result ? (result as MpcProgress) : null);
      } catch {
        // silently ignore
      }
    }

    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [mpcHealth?.state, mpcHealth?.last_run_id]);

  // Poll MPC progress history for real-time curves (every 3 seconds when running)
  useEffect(() => {
    const isRunning = mpcHealth?.state === "running" || mpcHealth?.state === "running_fuzzy";
    const runId = isRunning ? mpcHealth.last_run_id : null;
    if (!runId) {
      setMpcProgressHistory([]);
      return;
    }
    let active = true;
    const controller = new AbortController();

    async function poll() {
      try {
        const result = await fetchMpcProgressHistory(runId!, 1, controller.signal);
        if (active && result.progress) {
          setMpcProgressHistory(result.progress);
        }
      } catch {
        // silently ignore
      }
    }

    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(timer);
    };
  }, [mpcHealth?.state, mpcHealth?.last_run_id]);

  // Compute real-time metrics from progress history
  const realtimeBatteryCycles = useMemo(() => {
    if (!mpcProgressHistory.length) return null;
    let previous: number | null = null;
    let movement = 0;
    for (const p of mpcProgressHistory) {
      if (p.soc === null || p.soc === undefined) continue;
      const socPercent = p.soc <= 1 ? p.soc * 100 : p.soc;
      if (previous !== null) movement += Math.abs(socPercent - previous);
      previous = socPercent;
    }
    return movement > 0 ? Math.round((movement / 200) * 100) / 100 : 0;
  }, [mpcProgressHistory]);

  const realtimeArbitrageRevenue = useMemo(() => {
    if (!mpcProgressHistory.length) return null;
    let total = 0;
    for (const p of mpcProgressHistory) {
      if (p.arbitrage !== null && p.arbitrage !== undefined) {
        total += p.arbitrage;
      }
    }
    return Math.round(total * 100) / 100;
  }, [mpcProgressHistory]);

  const handleImportRawData = useCallback(async () => {
    const input = rawFileInputRef.current;
    if (!input?.files?.length) return;
    const file = input.files[0];
    const fileKB = (file.size / 1024).toFixed(0);
    setRawImporting(true);
    setRawImportError(null);
    setRawImportResult(null);

    // 循环滚动步骤（每 400ms），大文件也能持续显示进度
    const steps = [
      `读取 ${file.name} (${fileKB} KB)`,
      "解析表头与数据行...",
      "写入 raw_telemetry...",
      "15 分钟窗口聚合...",
      "获取辐照度数据...",
      "校验 & 提交...",
    ];
    let stepIdx = 0;
    setRawImportStep(steps[0]);
    const timer = setInterval(() => {
      stepIdx = (stepIdx + 1) % steps.length;
      setRawImportStep(steps[stepIdx]);
    }, 400);

    try {
      const result = await importRawData(file);
      clearInterval(timer);
      setRawImportStep("✅ 完成!");
      // 让用户看清"完成"状态再切换弹窗
      await new Promise(r => setTimeout(r, 800));
      setRawImportStep("");
      setRawImportResult(result);
      setRefreshCount((value) => value + 1);
    } catch (err) {
      clearInterval(timer);
      setRawImportStep("");
      setRawImportError(err instanceof Error ? err.message : "导入失败");
    } finally {
      setRawImporting(false);
      if (input) input.value = "";
    }
  }, []);

  const handleClearData = useCallback(async () => {
    if (!window.confirm("确认清空数据库？\n\n此操作将删除本项目导入和生成的 所有数据（原始电表、聚合数据、MPC 运行记录等），不可恢复。")) return;
    setClearLoading(true);
    setClearError(null);
    setClearResult(null);
    try {
      const result = await clearData();
      const parts = Object.entries(result.deleted)
        .filter(([, c]) => c > 0)
        .map(([t, c]) => `${t}: ${c} 行`);
      setClearResult(parts.length > 0 ? `已清空 ${parts.join("，")}` : "数据库已为空");
      setRefreshCount((v) => v + 1);
    } catch (err) {
      setClearError(err instanceof Error ? err.message : "清空失败");
    } finally {
      setClearLoading(false);
    }
  }, []);

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
        target_peak_kw: targetDemandOverride ?? undefined,
      });
      setMpcRunResult(result);
      setRefreshCount((value) => value + 1);
    } catch (err) {
      setMpcRunError(err instanceof Error ? err.message : "MPC 运行失败");
    } finally {
      setMpcRunLoading(false);
    }
  }, [plantId, optimizationTarget, targetDemandOverride]);

  // Monthly demand ref popup — when state is awaiting_demand_ref, prompt user
  useEffect(() => {
    if (mpcHealth?.state !== "awaiting_demand_ref") return;
    const nextValue = window.prompt(
      `【${mpcHealth.plant_id}】本月目标需量尚未设置。\n\n请输入本月目标需量值（kW）：`,
      "",
    );
    if (nextValue !== null) {
      const parsed = Number(nextValue);
      if (Number.isFinite(parsed) && parsed > 0) {
        setMonthlyDemandRef(plantId, parsed).catch((err) => {
          setMpcRunError(err instanceof Error ? err.message : "设置目标需量失败");
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
        profile: optimizationTarget,
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
  }, [plantId, runId, windowHours, rangeStart, rangeEnd, refreshCount, isRealtimeMode, optimizationTarget]);

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
  const targetSoc = targetSocOverride ?? mpcProgress?.soc ?? latestMpcPoint?.mpc_soc ?? null;
  const targetDemand = targetDemandOverride ?? targetPeak;
  const factoryArbitrageRevenue = estimateArbitrageRevenue(realtimeSeries, "actual_battery_power_kw");
  const mpcArbitrageRevenue = estimateArbitrageRevenue(realtimeSeries, "mpc_battery_power_kw");
  const factoryBatteryCycles = estimateBatteryCycles(realtimeSeries, "actual_soc");
  const mpcBatteryCycles = estimateBatteryCycles(realtimeSeries, "mpc_soc");
  const revenueComparisonOption = buildRevenueComparisonChartOption(0, comparison?.cost_saving_yuan);
  const demandComparisonOption = buildDemandComparisonChartOption(comparison?.actual_peak_kw, comparison?.mpc_peak_kw);
  const mpcStatusText = loading ? "运行中" : error ? "异常" : status?.label || "待运行";

  function editTargetSoc() {
    const nextValue = promptForNumber("请输入目标 SOC（%）", targetSoc === null || targetSoc === undefined ? null : targetSoc <= 1 ? targetSoc * 100 : targetSoc);
    if (nextValue !== null) setTargetSocOverride(nextValue);
  }

  async function editTargetDemand() {
    const nextValue = promptForNumber("请输入目标/给定需量值（kW）", targetDemand);
    if (nextValue === null) return;
    try {
      await setMonthlyDemandRef(plantId, nextValue);
      setTargetDemandOverride(nextValue);
      setRefreshCount((value) => value + 1);
    } catch (err) {
      setMpcRunError(err instanceof Error ? err.message : "设置目标需量失败");
    }
  }

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const handleExportCSV = useCallback(async () => {
    setExporting(true);
    setExportError(null);
    try {
      const startStr = toApiTime(rangeStart);
      const endStr = toApiTime(rangeEnd);
      let totalHours = 24;
      if (startStr && endStr) {
        const ms = new Date(endStr).getTime() - new Date(startStr).getTime();
        totalHours = Math.max(24, Math.ceil(ms / 3600_000));
      }
      const fullData = await fetchDashboard(plantId, {
        windowHours: totalHours,
        runId: runId.trim() || undefined,
        profile: optimizationTarget,
        startTime: startStr,
        endTime: endStr,
      });
      const pts = fullData.series;
      if (!pts.length) {
        setExportError("无数据可导出");
        return;
      }
      const headers = [
        "时间", "工厂电网功率(kW)", "MPC电网功率(kW)",
        "工厂储能功率(kW)", "MPC储能功率(kW)",
        "工厂SOC", "MPC SOC",
        "工厂负荷(kW)", "MPC负荷(kW)",
        "工厂光伏(kW)", "MPC光伏(kW)",
        "净负荷(kW)", "购电价(元/kWh)",
      ];
      const rows = pts.map((p) =>
        [
          p.time,
          p.actual_grid_power_kw ?? "",
          p.mpc_grid_power_kw ?? "",
          p.actual_battery_power_kw ?? "",
          p.mpc_battery_power_kw ?? "",
          p.actual_soc ?? "",
          p.mpc_soc ?? "",
          p.actual_load_kw ?? "",
          p.mpc_load_kw ?? "",
          p.actual_pv_kw ?? "",
          p.mpc_pv_kw ?? "",
          p.load_minus_pv_kw ?? "",
          p.buy_price ?? "",
        ].join(","),
      );
      const csv = "﻿" + [headers.join(","), ...rows].join("\n");
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${plantId}_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "导出失败");
    } finally {
      setExporting(false);
    }
  }, [plantId, runId, rangeStart, rangeEnd, isRealtimeMode, optimizationTarget]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="topbar-time">
          <span>{TOPBAR_TIME_LABEL}</span>
          <strong>{latestTime}</strong>
          <button type="button" className="export-btn" onClick={handleExportCSV} disabled={exporting} title="导出当前时间范围全部曲线数据为 CSV">
            {exporting ? "导出中..." : "导出CSV"}
          </button>
          {exportError && <span className="import-error" style={{marginLeft:6}} title={exportError}>❌</span>}
        </div>
        <h1>则鸣AI+ems实时演示系统 <span style={{fontSize:12,fontWeight:400,color:"var(--muted)",marginLeft:8}}>V{APP_FRONTEND_VERSION}.{backendVersion}</span></h1>
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
              expandedChildren={<RevenueChart series={realtimeSeries} />}
            >
              <RevenueChart series={realtimeSeries} />
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
                  factoryValue={formatYuan(factoryArbitrageRevenue)}
                  mpcValue={formatYuan(mpcArbitrageRevenue)}
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
                info="虚线为则鸣策略，实线为工厂策略"
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
              {mpcHealth && (
                <small className="mpc-status-detail" style={{textAlign:"center",display:"block",marginTop:4,color:"var(--muted)",fontSize:11}}>
                  {PLANT_OPTIONS.find(p => p.value === plantId)?.label ?? plantId}
                  {" · "}
                  {OPTIMIZATION_TARGETS.find(t => t.value === optimizationTarget)?.label ?? optimizationTarget}
                  {mpcHealth.last_run_finished_at && mpcHealth.state !== "running" && mpcHealth.state !== "running_fuzzy" && (
                    <>{" · "}{new Date(mpcHealth.last_run_finished_at).toLocaleTimeString("zh-CN", {hour:"2-digit",minute:"2-digit"})}</>
                  )}
                </small>
              )}
              {(mpcHealth?.state === "running" || mpcHealth?.state === "running_fuzzy") && (
                <>
                  <small className="mpc-status-detail" style={{textAlign:"center",display:"block",marginTop:2,color:"var(--cyan)",fontSize:11}}>
                    {mpcProgress && mpcProgress.step > 0 ? (
                      <>步 {mpcProgress.step}/{mpcProgress.total_steps} · SOC {((mpcProgress.soc ?? 0) * 100).toFixed(1)}% · {Math.round(mpcProgress.elapsed_seconds ?? 0)}秒</>
                    ) : (
                      <>⏳ 计算进行中...</>
                    )}
                  </small>
                  {(realtimeBatteryCycles !== null || realtimeArbitrageRevenue !== null) && (
                    <small className="mpc-status-detail" style={{textAlign:"center",display:"block",marginTop:2,color:"var(--muted)",fontSize:11}}>
                      循环 {realtimeBatteryCycles !== null ? realtimeBatteryCycles.toFixed(2) : "--"} 次
                      {" · "}
                      套利 {realtimeArbitrageRevenue !== null ? `${realtimeArbitrageRevenue.toFixed(2)} 元` : "--"}
                    </small>
                  )}
                </>
              )}
              {mpcHealth?.data_timed_out && (
                <small className="mpc-status-hint gap-warn">数据获取超时 {mpcHealth.minutes_since_latest_data != null ? `${Math.round(mpcHealth.minutes_since_latest_data)} 分钟` : ""}</small>
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
              <span>需量管理</span>
              <div className="demand-rows">
                <div className="demand-row">
                  <span className="demand-label">MPC 最大需量</span>
                  <strong>{formatKw(comparison?.mpc_peak_kw)}</strong>
                </div>
                <div className="demand-row">
                  <span className="demand-label">目标需量</span>
                  <strong>{formatKw(targetDemand)}</strong>
                </div>
              </div>
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
            <div className="rail-card data-import-card" style={{ position: "relative" }}>
              <span>{CONTROL_RAIL_CARD_TITLES[5]}</span>
              <button
                type="button"
                className="mini-action"
                onClick={handleClearData}
                disabled={clearLoading}
                title="清空本项目导入和生成的所有数据"
                style={{ color: "rgba(244,108,108,0.55)" }}
              >
                {clearLoading ? "⏳" : "清空"}
              </button>
              <input
                ref={rawFileInputRef}
                type="file"
                accept=".xlsx,.xls,.numbers"
                style={{ display: "none" }}
                onChange={handleImportRawData}
                aria-label="导入电站历史数据"
              />
              <button
                type="button"
                className="import-action"
                onClick={() => rawFileInputRef.current?.click()}
                disabled={rawImporting}
                title="上传电表 Excel/numbers（时间/电网功率/储能功率/SOC/实时电价），Sheet 名自动识别电站"
              >
                {rawImporting ? "⏳ 导入中..." : "导入电站历史数据"}
              </button>
              {rawImportError && <span className="import-error" title={rawImportError}>❌ 导入失败</span>}
              {rawImportResult && !rawImporting && (
                <span className="import-success" style={{ cursor: "pointer" }} onClick={() => setRawImportResult(rawImportResult)} title="点击查看详情">
                  ✅ {rawImportResult.plant_id} {rawImportResult.records_count}条 {rawImportResult.total_sec}秒
                </span>
              )}
              {clearError && <span className="import-error" title={clearError}>❌ {clearError}</span>}
              {clearResult && !clearLoading && (
                <span className="import-success" style={{ cursor: "pointer" }} onClick={() => setClearResult(null)} title="点击关闭">
                  ✅ {clearResult}
                </span>
              )}
            </div>
          </aside>
        </section>
      </section>

      {rawImportResult && !rawImporting && (
        <div className="chart-modal" role="dialog" aria-modal="true" aria-label="导入完成">
          <div className="chart-modal-panel" style={{ maxWidth: 420 }}>
            <div className="chart-modal-head">
              <h2>✅ 数据导入完成</h2>
              <button className="icon-button" type="button" onClick={() => setRawImportResult(null)} aria-label="关闭">
                <span className="flat-icon icon-close" aria-hidden="true" />
              </button>
            </div>
            <div style={{ padding: "12px 0", lineHeight: 1.8, fontSize: 14 }}>
              <table style={{ width: "100%", borderSpacing: "0 6px" }}>
                <tbody>
                  <tr><td style={{ color: "var(--muted)" }}>文件大小</td><td>{rawImportResult.file_size_kb} KB</td></tr>
                  <tr><td style={{ color: "var(--muted)" }}>Sheet 数量</td><td><strong>{rawImportResult.sheet_count} 个</strong></td></tr>
                  {rawImportResult.sheets?.map((sh, i) => (
                    <tr key={i}>
                      <td style={{ color: "var(--muted)" }}>Sheet {i + 1}</td>
                      <td>
                        <strong style={{ color: "var(--blue)" }}>{sh.sheet_name}</strong>
                        {" → "}{sh.plant_id}，{sh.records} 条
                        {sh.skipped > 0 && <span style={{ color: "var(--muted)" }}>（跳过 {sh.skipped}）</span>}
                      </td>
                    </tr>
                  ))}
                  <tr><td style={{ color: "var(--muted)" }}>总行数</td><td>{rawImportResult.total_rows} 行（跳过 {rawImportResult.skipped_rows} 空行）</td></tr>
                  <tr><td style={{ color: "var(--muted)" }}>写入记录</td><td><strong style={{ color: "var(--green)" }}>{rawImportResult.records_count} 条</strong></td></tr>
                  <tr><td style={{ color: "var(--muted)" }}>解析耗时</td><td>{rawImportResult.parse_sec} 秒</td></tr>
                  {rawImportResult.aggregated_windows != null && (
                    <tr><td style={{ color: "var(--muted)" }}>聚合窗口</td><td><strong style={{ color: "var(--green)" }}>{rawImportResult.aggregated_windows} 个</strong>（15分钟）</td></tr>
                  )}
                  {rawImportResult.irradiance_updated != null && rawImportResult.irradiance_updated > 0 && (
                    <tr><td style={{ color: "var(--muted)" }}>辐照度</td><td>已获取 {rawImportResult.irradiance_updated} 窗口</td></tr>
                  )}
                  <tr><td style={{ color: "var(--muted)" }}>总耗时</td><td>{rawImportResult.total_sec} 秒</td></tr>
                </tbody>
              </table>
              {rawImportResult.log_file && (
                <tr><td style={{ color: "var(--muted)" }}>日志</td><td style={{fontSize:11,fontFamily:"monospace"}}>{rawImportResult.log_file}</td></tr>
              )}
              <p style={{ color: "var(--green)", margin: "12px 0 0", fontWeight: 600 }}>
                数据已就绪，刷新页面即可在图表中查看。
              </p>
            </div>
          </div>
        </div>
      )}

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
      {rawImporting && rawImportStep && (
        <div className="chart-modal" role="dialog" aria-modal="true" aria-label="导入进度">
          <div className="chart-modal-panel" style={{ maxWidth: 360, textAlign: "center" }}>
            <div style={{ padding: "24px 16px" }}>
              <div className="import-spinner" style={{ fontSize: 32, marginBottom: 16 }}>
                ⏳
              </div>
              <h3 style={{ margin: "0 0 16px", color: "var(--muted-strong)" }}>正在导入数据</h3>
              <div style={{
                width: "100%", height: 4, background: "var(--border, #333)",
                borderRadius: 2, overflow: "hidden", marginBottom: 16,
              }}>
                <div style={{
                  height: "100%", width: "60%",
                  background: "linear-gradient(90deg, var(--green), var(--blue))",
                  borderRadius: 2,
                  animation: "progressBar 1.5s ease-in-out infinite",
                }} />
              </div>
              <p style={{ color: "var(--green)", fontSize: 14, margin: 0, fontWeight: 500 }}>
                {rawImportStep}
              </p>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
