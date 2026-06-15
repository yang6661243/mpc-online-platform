import { describe, expect, it } from "vitest";
import {
  buildBatteryChartOption,
  buildPowerChartOption,
  buildRevenueChartOption,
  buildStrategyChartOption,
} from "./chartOptions";
import type { DashboardSeriesPoint } from "./types";

const sampleSeries: DashboardSeriesPoint[] = [
  {
    time: "2026-06-13T04:15:00",
    actual_grid_power_kw: 216.2,
    actual_battery_power_kw: -12.5,
    actual_soc: 0.5,
    actual_load_kw: 260.2,
    actual_pv_kw: 56.5,
    load_minus_pv_kw: 203.7,
    mpc_grid_power_kw: 190.1,
    mpc_battery_power_kw: 13.2,
    mpc_soc: 0.54,
    mpc_load_kw: 258.0,
    mpc_pv_kw: 54.3,
    buy_price: null,
    sell_price: null,
    quality_flag: "ok",
  },
  {
    time: "2026-06-13T04:30:00",
    actual_grid_power_kw: 220.4,
    actual_battery_power_kw: 0,
    actual_soc: 0.49,
    actual_load_kw: 280.4,
    actual_pv_kw: 60.0,
    load_minus_pv_kw: 220.4,
    mpc_grid_power_kw: null,
    mpc_battery_power_kw: null,
    mpc_soc: null,
    mpc_load_kw: null,
    mpc_pv_kw: null,
    buy_price: null,
    sell_price: null,
    quality_flag: "ok",
  },
];

describe("chart option builders", () => {
  it("maps factory and MPC strategy curves into one comparable power chart", () => {
    const option = buildPowerChartOption(sampleSeries);

    expect(option.legend.data).toEqual([
      "工厂电网功率",
      "MPC电网功率",
      "工厂负荷功率",
      "MPC负荷功率",
      "工厂光伏出力",
      "MPC光伏出力",
      "工厂储能功率",
      "MPC储能功率",
      "工厂SOC",
      "MPCSOC",
    ]);
    expect(option.legend.selected).toMatchObject({
      工厂电网功率: true,
      MPC电网功率: true,
      工厂负荷功率: false,
      MPC负荷功率: false,
    });
    expect(option.xAxis).toMatchObject({ data: ["06-13 12:15", "06-13 12:30"] });
    expect(option.series).toHaveLength(10);
    expect(option.series.slice(0, 4)).toMatchObject([
      { name: "工厂电网功率", data: [216.2, 220.4], lineStyle: { type: "solid" } },
      { name: "MPC电网功率", data: [190.1, null], lineStyle: { type: "dashed" } },
      { name: "工厂负荷功率", data: [260.2, 280.4], lineStyle: { type: "solid" } },
      { name: "MPC负荷功率", data: [258, null], lineStyle: { type: "dashed" } },
    ]);
  });

  it("converts SOC ratios to percentages in the battery chart", () => {
    const option = buildBatteryChartOption(sampleSeries);

    expect(option.yAxis).toMatchObject([{ name: "kW" }, { name: "%", min: 0, max: 100 }]);
    expect(option.series).toMatchObject([
      { name: "实际储能功率", data: [-12.5, 0] },
      { name: "MPC 储能功率", data: [13.2, null] },
      { name: "实际 SOC", yAxisIndex: 1, data: [50, 49] },
      { name: "MPC SOC", yAxisIndex: 1, data: [54, null] },
    ]);
  });

  it("builds a cumulative revenue chart from actual and MPC grid power", () => {
    const option = buildRevenueChartOption(sampleSeries);

    expect(option.legend).toEqual({ top: 4, data: ["累计收益"] });
    expect(option.series).toMatchObject([
      {
        name: "累计收益",
        data: [6.43, 6.43],
      },
    ]);
  });

  it("builds separate strategy card charts for factory and MPC curves", () => {
    const factoryOption = buildStrategyChartOption(sampleSeries, "factory");
    const mpcOption = buildStrategyChartOption(sampleSeries, "mpc");

    expect(factoryOption.legend.data).toEqual(["负荷功率", "光伏出力", "净负荷", "电网功率", "储能功率", "SOC"]);
    expect(factoryOption.series).toMatchObject([
      { name: "负荷功率", data: [260.2, 280.4] },
      { name: "光伏出力", data: [56.5, 60] },
      { name: "净负荷", data: [203.7, 220.4] },
      { name: "电网功率", data: [216.2, 220.4] },
      { name: "储能功率", data: [-12.5, 0] },
      { name: "SOC", yAxisIndex: 1, data: [50, 49] },
    ]);
    expect(mpcOption.series).toMatchObject([
      { name: "负荷功率", data: [258, null] },
      { name: "光伏出力", data: [54.3, null] },
      { name: "净负荷", data: [203.7, 220.4] },
      { name: "电网功率", data: [190.1, null] },
      { name: "储能功率", data: [13.2, null] },
      { name: "SOC", yAxisIndex: 1, data: [54, null] },
    ]);
  });
});
