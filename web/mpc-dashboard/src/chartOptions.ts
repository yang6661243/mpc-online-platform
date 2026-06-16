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
  data: Array<number | null>;
  lineStyle?: Record<string, string | number>;
  itemStyle?: Record<string, unknown>;
  barWidth?: string | number;
  markLine?: Record<string, unknown>;
}

export interface DashboardChartOption {
  [key: string]: unknown;
  color: string[];
  tooltip: { trigger: "axis"; formatter?: (params: Array<{ seriesName: string; data: number | null; dataIndex: number }>) => string };
  legend: Record<string, unknown> & { top: number; data: string[] };
  grid: Record<string, number>;
  xAxis: AxisOption;
  yAxis: AxisOption | AxisOption[];
  dataZoom: Array<Record<string, string | number>>;
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
    seriesName === "工厂电网功率"
      ? quality.grid_power_kw
      : seriesName === "工厂储能功率"
        ? quality.battery_power_kw
        : seriesName === "工厂SOC"
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

  function addPair(label: string, factoryData: Array<number | null>, mpcData: Array<number | null>, options: { yAxisIndex?: number; defaultVisible?: boolean } = {}) {
    const factoryName = `工厂${label}`;
    const mpcName = `MPC${label}`;
    const visible = Boolean(options.defaultVisible);

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
        lineStyle: { type: "solid", width: visible ? 2.4 : 1.6 },
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
        lineStyle: { type: "dashed", width: visible ? 2.4 : 1.6 },
      });
    }
  }

  addPair("电网功率", factoryGrid, mpcGrid, { defaultVisible: true });
  addPair("负荷功率", factoryLoad, mpcLoad);
  addPair("光伏出力", factoryPv, mpcPv);
  addPair("储能功率", factoryBattery, mpcBattery);
  addPair("SOC", factorySoc, mpcSoc, { yAxisIndex: 1 });

  return {
    color: ["#4ecdc4", "#4ecdc4", "#ff6b6b", "#ff6b6b", "#ffd166", "#ffd166", "#f39c12", "#f39c12", "#9b59b6", "#9b59b6"],
    textStyle: darkChartText,
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        return params
          .map((param) => {
            const point = series[param.dataIndex];
            const label = point ? qualityLabel(point, param.seriesName) : "";
            const suffix = label ? ` (${label})` : "";
            return `${param.seriesName}: ${formatTooltipValue(param.data)}${suffix}`;
          })
          .join("<br/>");
      },
    },
    legend: { top: 4, type: "scroll", data: legendData, selected },
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
  let cumulative = 0;
  const revenue = series.map((point) => {
    if (point.actual_grid_power_kw !== null && point.mpc_grid_power_kw !== null) {
      const price = point.buy_price ?? 0.986;
      cumulative += (point.actual_grid_power_kw - point.mpc_grid_power_kw) * price * 0.25;
    }
    return round2(cumulative);
  });

  return {
    color: ["#38d8a8"],
    textStyle: darkChartText,
    tooltip: { trigger: "axis" },
    legend: { top: 4, data: ["累计收益"] },
    grid: { left: 54, right: 28, top: 48, bottom: 42 },
    xAxis: {
      type: "category",
      data: series.map((point) => shortTime(point.time)),
      boundaryGap: false,
    },
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
        data: revenue,
      },
    ],
  };
}

export function buildRevenueComparisonChartOption(factoryRevenue: number | null | undefined, mpcRevenue: number | null | undefined): DashboardChartOption {
  const factory = valueOrNull(factoryRevenue);
  const mpc = valueOrNull(mpcRevenue);

  return {
    color: ["#38d8a8"],
    textStyle: darkChartText,
    tooltip: { trigger: "axis" },
    legend: { top: 4, data: ["收益"] },
    grid: { left: 42, right: 18, top: 38, bottom: 28 },
    xAxis: {
      type: "category",
      data: ["工厂策略", "MPC策略"],
    },
    yAxis: { type: "value", name: "元", scale: true },
    dataZoom: [{ type: "inside" }],
    series: [
      {
        name: "收益",
        type: "bar",
        barWidth: "46%",
        data: [factory, mpc],
        itemStyle: {
          color: (params: { dataIndex: number }) => (params.dataIndex === 0 ? "#8db6c9" : "#38d8a8"),
          borderRadius: [4, 4, 0, 0],
        },
      },
    ],
  };
}

export function buildDemandComparisonChartOption(factoryDemand: number | null | undefined, mpcDemand: number | null | undefined): DashboardChartOption {
  return {
    color: ["#39d9ff"],
    textStyle: darkChartText,
    tooltip: { trigger: "axis" },
    legend: { top: 4, data: ["最大需量"] },
    grid: { left: 42, right: 18, top: 38, bottom: 28 },
    xAxis: {
      type: "category",
      data: ["工厂策略", "MPC策略"],
      boundaryGap: false,
    },
    yAxis: { type: "value", name: "kW", scale: true },
    dataZoom: [{ type: "inside" }],
    series: [
      {
        name: "最大需量",
        type: "line",
        showSymbol: true,
        smooth: true,
        data: [valueOrNull(factoryDemand), valueOrNull(mpcDemand)],
        lineStyle: { width: 2.6 },
      },
    ],
  };
}

export function buildBatteryChartOption(series: DashboardSeriesPoint[]): DashboardChartOption {
  return {
    color: ["#7445a8", "#d56a1c", "#17815f", "#38a388"],
    textStyle: darkChartText,
    tooltip: { trigger: "axis" },
    legend: { top: 4, data: ["实际储能功率", "MPC 储能功率", "实际 SOC", "MPC SOC"] },
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
    legend: { top: 4, type: "scroll", data: legendData },
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
