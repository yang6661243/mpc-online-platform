import { shortTime } from "./format";
import type { DashboardSeriesPoint } from "./types";

interface AxisOption {
  type?: string;
  name?: string;
  data?: string[];
  boundaryGap?: boolean;
  scale?: boolean;
  min?: number;
  max?: number;
}

interface SeriesOption {
  name: string;
  type: "line" | "bar";
  showSymbol?: boolean;
  smooth?: boolean;
  yAxisIndex?: number;
  xAxisIndex?: number;
  data?: Array<number | null>;
  datasetIndex?: number;
  encode?: Record<string, string>;
  stack?: string;
  barWidth?: string | number;
  barGap?: string;
  label?: Record<string, unknown>;
  lineStyle?: Record<string, string | number>;
  itemStyle?: Record<string, unknown>;
  markLine?: Record<string, unknown>;
}

export interface DashboardChartOption {
  [key: string]: unknown;
  color?: string[];
  textStyle?: Record<string, string>;
  tooltip: { trigger: string; axisPointer?: Record<string, unknown>; formatter?: unknown };
  legend: Record<string, unknown> & { top: number; data: string[] };
  grid: Record<string, number>;
  xAxis: AxisOption | AxisOption[];
  yAxis: AxisOption | AxisOption[];
  dataZoom?: Array<Record<string, string | number>>;
  dataset?: Array<Record<string, unknown>>;
  series: SeriesOption[];
}

export type StrategyKind = "factory" | "mpc";

function socPercent(value: number | null): number | null {
  if (value === null) return null;
  return value <= 1 ? value * 100 : value;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

const darkChartText = { color: "#cfefff" };

function hasData(values: Array<number | null | undefined>): boolean {
  return values.some((value) => value !== null && value !== undefined);
}

function valueOrNull(value: number | null | undefined): number | null {
  return value === undefined ? null : value;
}

function qualityLabel(point: DashboardSeriesPoint, seriesName: string): string {
  const quality = point.display_quality;
  if (!quality) return "";
  const field =
    seriesName === "电网功率"
      ? quality.grid_power_kw
      : seriesName === "储能功率"
        ? quality.battery_power_kw
        : seriesName === "SOC"
          ? quality.soc
          : "";
  if (field === "observed") return "真实";
  if (field === "interpolated_quadratic") return "插值";
  if (field === "gap") return "缺口";
  if (field === "derived") return "派生";
  return "";
}

function formatTooltipValue(value: number | null): string {
  return value === null || value === undefined ? "--" : String(value);
}

export function buildPowerChartOption(series: DashboardSeriesPoint[]): DashboardChartOption {
  const factoryLoad = series.map((point) => valueOrNull(point.actual_load_kw));
  const mpcLoad = series.map((point) => valueOrNull(point.mpc_load_kw));
  const factoryPv = series.map((point) => valueOrNull(point.actual_pv_kw));
  const mpcPv = series.map((point) => valueOrNull(point.mpc_pv_kw));
  const factoryGrid = series.map((point) => point.actual_grid_power_kw);
  const mpcGrid = series.map((point) => point.mpc_grid_power_kw);
  const factoryBattery = series.map((point) => point.actual_battery_power_kw);
  const mpcBattery = series.map((point) => point.mpc_battery_power_kw);
  const factorySoc = series.map((point) => socPercent(point.actual_soc));
  const mpcSoc = series.map((point) => socPercent(point.mpc_soc));
  const optionSeries: SeriesOption[] = [];
  const legendData: string[] = [];
  const selected: Record<string, boolean> = {};

  function addPair(label: string, factoryData: Array<number | null>, mpcData: Array<number | null>, options: { yAxisIndex?: number; defaultVisible?: boolean; factoryPrefix?: string; color?: string } = {}) {
    const prefix = options.factoryPrefix ?? "工厂";
    const factoryName = `${prefix}${label}`;
    const mpcName = `MPC${label}`;
    const visible = Boolean(options.defaultVisible);
    const baseColor = options.color;

    if (hasData(factoryData)) {
      legendData.push(factoryName);
      selected[factoryName] = visible;
      optionSeries.push({
        name: factoryName,
        type: "line",
        showSymbol: false,
        smooth: true,
        yAxisIndex: options.yAxisIndex,
        data: factoryData,
        lineStyle: { type: "solid", width: visible ? 2.4 : 1.6, color: baseColor },
        itemStyle: { color: baseColor },
      });
    }

    if (hasData(mpcData)) {
      legendData.push(mpcName);
      selected[mpcName] = visible;
      optionSeries.push({
        name: mpcName,
        type: "line",
        showSymbol: false,
        smooth: true,
        yAxisIndex: options.yAxisIndex,
        data: mpcData,
        lineStyle: { type: "dashed", width: visible ? 2.4 : 1.6, color: baseColor },
        itemStyle: { color: baseColor },
      });
    }
  }

  addPair("电网功率", factoryGrid, mpcGrid, { defaultVisible: true, factoryPrefix: "", color: "#f4b766" });
  addPair("负荷功率", factoryLoad, mpcLoad, { color: "#ff6b6b" });
  addPair("光伏出力", factoryPv, mpcPv, { color: "#ffd166" });
  addPair("储能功率", factoryBattery, mpcBattery, { defaultVisible: true, factoryPrefix: "", color: "#22c55e" });
  addPair("SOC", factorySoc, mpcSoc, { defaultVisible: true, yAxisIndex: 1, factoryPrefix: "", color: "#9b59b6" });

  return {
    color: [],
    textStyle: darkChartText,
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const pt = series[params[0]?.dataIndex];
        const timeStr = pt ? shortTime(pt.time) : "";
        const header = timeStr
          ? `<div style="margin-bottom:4px;color:#7cc7ff;font-weight:700">${timeStr}</div>`
          : "";
        return (
          header +
          params
            .map((param) => {
              const point = series[param.dataIndex];
              const label = point ? qualityLabel(point, param.seriesName) : "";
              const suffix = label ? ` (${label})` : "";
              return `${param.seriesName}: ${formatTooltipValue(param.data)}${suffix}`;
            })
            .join("<br/>")
        );
      },
    },
    legend: { top: 4, type: "scroll", data: legendData, selected, textStyle: { color: "#a9c9d8" } },
    grid: { left: 54, right: 58, top: 62, bottom: 42 },
    xAxis: {
      type: "category",
      data: series.map((point) => shortTime(point.time)),
      boundaryGap: false,
    },
    yAxis: [
      { type: "value", name: "kW", scale: true },
      { type: "value", name: "%", min: 0, max: 100 },
    ],
    dataZoom: [
      { type: "inside" },
      { type: "slider", height: 18, bottom: 8 },
    ],
    series: optionSeries,
  };
}

export function buildRevenueChartOption(series: DashboardSeriesPoint[]): DashboardChartOption {
  // Data-transform-filter style: use dataset with dimensions
  let cumulative = 0;
  const source: Array<Record<string, string | number>> = [];
  for (const point of series) {
    if (point.actual_grid_power_kw !== null && point.mpc_grid_power_kw !== null) {
      const price = point.buy_price ?? 0.986;
      cumulative += (point.actual_grid_power_kw - point.mpc_grid_power_kw) * price * 0.25;
    }
    source.push({ time: shortTime(point.time), 累计收益: round2(cumulative) });
  }

  return {
    textStyle: darkChartText,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
    },
    legend: { top: 4, data: ["累计收益"], textStyle: { color: "#a9c9d8" } },
    grid: { left: 54, right: 28, top: 48, bottom: 42 },
    dataset: [{ dimensions: ["time", "累计收益"], source }],
    xAxis: { type: "category" },
    yAxis: { type: "value", name: "元", scale: true },
    dataZoom: [
      { type: "inside" },
      { type: "slider", height: 18, bottom: 8 },
    ],
    series: [
      {
        name: "累计收益",
        type: "line",
        showSymbol: false,
        smooth: true,
        datasetIndex: 0,
        encode: { x: "time", y: "累计收益" },
        lineStyle: { color: "#38d8a8", width: 2.5 },
        itemStyle: { color: "#38d8a8" },
      },
    ],
  };
}

export function buildRevenueComparisonChartOption(factoryRevenue: number | null | undefined, mpcRevenue: number | null | undefined): DashboardChartOption {
  // bar-negative style: horizontal bars, factory (-) on left, MPC (+) on right
  const f = valueOrNull(factoryRevenue) ?? 0;
  const m = valueOrNull(mpcRevenue) ?? 0;

  return {
    textStyle: darkChartText,
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
    },
    legend: {
      top: 4,
      data: ["工厂策略", "MPC策略"],
      textStyle: { color: "#a9c9d8" },
    },
    grid: { left: 18, right: 18, top: 48, bottom: 18 },
    xAxis: {
      type: "value",
      axisLabel: { color: "#a9c9d8" },
      splitLine: { lineStyle: { color: "rgba(92,200,236,0.12)" } },
    },
    yAxis: {
      type: "category",
      data: ["收益"],
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: "#cfefff", fontWeight: 700 },
    },
    series: [
      {
        name: "工厂策略",
        type: "bar",
        stack: "total",
        barWidth: "36%",
        label: {
          show: true,
          position: "left",
          color: "#f4b766",
          fontSize: 12,
          fontWeight: 700,
          formatter: () => `${f.toFixed(2)} 元`,
        },
        itemStyle: { color: "#f4b766", borderRadius: [4, 0, 0, 4] },
        data: [-Math.abs(f)],
      },
      {
        name: "MPC策略",
        type: "bar",
        stack: "total",
        barWidth: "36%",
        label: {
          show: true,
          position: "right",
          color: "#42d6a6",
          fontSize: 12,
          fontWeight: 700,
          formatter: () => `${m.toFixed(2)} 元`,
        },
        itemStyle: { color: "#42d6a6", borderRadius: [0, 4, 4, 0] },
        data: [Math.abs(m)],
      },
    ],
  };
}

export function buildDemandComparisonChartOption(factoryDemand: number | null | undefined, mpcDemand: number | null | undefined): DashboardChartOption {
  // line-tooltip-touch style: bars with touch-friendly crosshair tooltip
  const f = valueOrNull(factoryDemand) ?? 0;
  const m = valueOrNull(mpcDemand) ?? 0;

  return {
    textStyle: darkChartText,
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "shadow",
        shadowStyle: { color: "rgba(81,215,255,0.06)" },
      },
    },
    legend: { top: 4, data: ["最大需量"], textStyle: { color: "#a9c9d8" } },
    grid: { left: 18, right: 18, top: 48, bottom: 18 },
    xAxis: {
      type: "category",
      data: ["工厂策略", "MPC策略"],
      axisLabel: { color: "#cfefff", fontWeight: 700, fontSize: 13 },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      name: "kW",
      scale: true,
      nameTextStyle: { color: "#a9c9d8" },
      axisLabel: { color: "#a9c9d8" },
      splitLine: { lineStyle: { color: "rgba(92,200,236,0.12)" } },
    },
    series: [
      {
        name: "最大需量",
        type: "bar",
        barWidth: "40%",
        barGap: "30%",
        label: {
          show: true,
          position: "top",
          color: "#cfefff",
          fontSize: 13,
          fontWeight: 700,
          formatter: (params: { value: number }) => `${(params.value).toFixed(1)} kW`,
        },
        data: [
          { value: f, itemStyle: { color: "#f4b766", borderRadius: [4, 4, 0, 0] } },
          { value: m, itemStyle: { color: "#42d6a6", borderRadius: [4, 4, 0, 0] } },
        ],
      },
    ],
  };
}

export function buildBatteryChartOption(series: DashboardSeriesPoint[]): DashboardChartOption {
  return {
    color: ["#7445a8", "#d56a1c", "#17815f", "#38a388"],
    textStyle: darkChartText,
    tooltip: { trigger: "axis" },
    legend: { top: 4, data: ["实际储能功率", "MPC 储能功率", "实际 SOC", "MPC SOC"], textStyle: { color: "#a9c9d8" } },
    grid: { left: 54, right: 54, top: 48, bottom: 42 },
    xAxis: {
      type: "category",
      data: series.map((point) => shortTime(point.time)),
      boundaryGap: false,
    },
    yAxis: [
      { type: "value", name: "kW", scale: true },
      { type: "value", name: "%", min: 0, max: 100 },
    ],
    dataZoom: [
      { type: "inside" },
      { type: "slider", height: 18, bottom: 8 },
    ],
    series: [
      {
        name: "实际储能功率",
        type: "line",
        showSymbol: false,
        smooth: true,
        data: series.map((point) => point.actual_battery_power_kw),
      },
      {
        name: "MPC 储能功率",
        type: "line",
        showSymbol: false,
        smooth: true,
        data: series.map((point) => point.mpc_battery_power_kw),
      },
      {
        name: "实际 SOC",
        type: "line",
        showSymbol: false,
        smooth: true,
        yAxisIndex: 1,
        data: series.map((point) => socPercent(point.actual_soc)),
      },
      {
        name: "MPC SOC",
        type: "line",
        showSymbol: false,
        smooth: true,
        yAxisIndex: 1,
        data: series.map((point) => socPercent(point.mpc_soc)),
      },
    ],
  };
}

export function buildStrategyChartOption(series: DashboardSeriesPoint[], strategy: StrategyKind): DashboardChartOption {
  const isFactory = strategy === "factory";
  const load = series.map((point) => valueOrNull(isFactory ? point.actual_load_kw : point.mpc_load_kw));
  const pv = series.map((point) => valueOrNull(isFactory ? point.actual_pv_kw : point.mpc_pv_kw));
  const netLoad = series.map((point) => {
    const loadKw = isFactory ? point.actual_load_kw : point.mpc_load_kw;
    const pvKw = isFactory ? point.actual_pv_kw : point.mpc_pv_kw;
    if (loadKw !== null && loadKw !== undefined && pvKw !== null && pvKw !== undefined) {
      return round2(loadKw - pvKw);
    }
    return point.load_minus_pv_kw;
  });
  const grid = series.map((point) => (isFactory ? point.actual_grid_power_kw : point.mpc_grid_power_kw));
  const battery = series.map((point) => (isFactory ? point.actual_battery_power_kw : point.mpc_battery_power_kw));
  const soc = series.map((point) => socPercent(isFactory ? point.actual_soc : point.mpc_soc));
  const optionSeries: SeriesOption[] = [];
  const legendData: string[] = [];

  function addPowerSeries(name: string, data: Array<number | null>) {
    if (!hasData(data)) return;
    legendData.push(name);
    optionSeries.push({
      name,
      type: "line",
      showSymbol: false,
      smooth: true,
      data,
      ...(name === "储能功率"
        ? {
            markLine: {
              symbol: "none",
              label: { formatter: "充放电分界" },
              lineStyle: { color: "#38d8a8", type: "dashed", width: 1 },
              data: [{ yAxis: 0 }],
            },
          }
        : {}),
    });
  }

  addPowerSeries("负荷功率", load);
  addPowerSeries("光伏出力", pv);
  addPowerSeries("净负荷", netLoad);
  addPowerSeries("电网功率", grid);
  addPowerSeries("储能功率", battery);
  legendData.push("SOC");
  optionSeries.push({
    name: "SOC",
    type: "line",
    showSymbol: false,
    smooth: true,
    yAxisIndex: 1,
    data: soc,
  });

  const colors = isFactory
    ? ["#ff6b6b", "#4ecdc4", "#ffd166", "#9b59b6", "#f39c12", "#7cc7ff"]
    : ["#ff7f66", "#39d9ff", "#b7f46a", "#38d8a8", "#20d0c9", "#e6ff68"];

  return {
    color: colors,
    textStyle: darkChartText,
    tooltip: { trigger: "axis" },
    legend: { top: 4, type: "scroll", data: legendData, textStyle: { color: "#a9c9d8" } },
    grid: { left: 52, right: 54, top: 76, bottom: 42 },
    xAxis: {
      type: "category",
      data: series.map((point) => shortTime(point.time)),
      boundaryGap: false,
    },
    yAxis: [
      { type: "value", name: "kW", scale: true },
      { type: "value", name: "%", min: 0, max: 100 },
    ],
    dataZoom: [
      { type: "inside" },
      { type: "slider", height: 18, bottom: 8 },
    ],
    series: optionSeries,
  };
}
