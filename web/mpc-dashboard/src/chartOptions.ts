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
  type: "line";
  showSymbol: boolean;
  smooth: boolean;
  yAxisIndex?: number;
  data: Array<number | null>;
}

export interface DashboardChartOption {
  [key: string]: unknown;
  color: string[];
  tooltip: { trigger: "axis" };
  legend: { top: number; data: string[] };
  grid: Record<string, number>;
  xAxis: AxisOption;
  yAxis: AxisOption | AxisOption[];
  dataZoom: Array<Record<string, string | number>>;
  series: SeriesOption[];
}

function socPercent(value: number | null): number | null {
  if (value === null) return null;
  return value <= 1 ? value * 100 : value;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

const darkChartText = { color: "#cfefff" };

export function buildPowerChartOption(series: DashboardSeriesPoint[]): DashboardChartOption {
  return {
    color: ["#1f6fd1", "#d56a1c"],
    textStyle: darkChartText,
    tooltip: { trigger: "axis" },
    legend: { top: 4, data: ["工厂当前策略", "MPC 策略"] },
    grid: { left: 54, right: 28, top: 48, bottom: 42 },
    xAxis: {
      type: "category",
      data: series.map((point) => shortTime(point.time)),
      boundaryGap: false,
    },
    yAxis: { type: "value", name: "kW", scale: true },
    dataZoom: [
      { type: "inside" },
      { type: "slider", height: 18, bottom: 8 },
    ],
    series: [
      {
        name: "工厂当前策略",
        type: "line",
        showSymbol: false,
        smooth: true,
        data: series.map((point) => point.actual_grid_power_kw),
      },
      {
        name: "MPC 策略",
        type: "line",
        showSymbol: false,
        smooth: true,
        data: series.map((point) => point.mpc_grid_power_kw),
      },
    ],
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
