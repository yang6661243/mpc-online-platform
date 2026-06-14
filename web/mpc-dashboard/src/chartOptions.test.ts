import { describe, expect, it } from "vitest";
import { buildBatteryChartOption, buildPowerChartOption, buildRevenueChartOption } from "./chartOptions";
import type { DashboardSeriesPoint } from "./types";

const sampleSeries: DashboardSeriesPoint[] = [
  {
    time: "2026-06-13T04:15:00",
    actual_grid_power_kw: 216.2,
    actual_battery_power_kw: -12.5,
    actual_soc: 0.5,
    load_minus_pv_kw: 203.7,
    mpc_grid_power_kw: 190.1,
    mpc_battery_power_kw: 13.2,
    mpc_soc: 0.54,
    buy_price: null,
    sell_price: null,
    quality_flag: "ok",
  },
  {
    time: "2026-06-13T04:30:00",
    actual_grid_power_kw: 220.4,
    actual_battery_power_kw: 0,
    actual_soc: 0.49,
    load_minus_pv_kw: 220.4,
    mpc_grid_power_kw: null,
    mpc_battery_power_kw: null,
    mpc_soc: null,
    buy_price: null,
    sell_price: null,
    quality_flag: "ok",
  },
];

describe("chart option builders", () => {
  it("maps actual and MPC grid power into the power chart", () => {
    const option = buildPowerChartOption(sampleSeries);

    expect(option.legend).toEqual({ top: 4, data: ["工厂当前策略", "MPC 策略"] });
    expect(option.xAxis).toMatchObject({ data: ["06-13 12:15", "06-13 12:30"] });
    expect(option.series).toMatchObject([
      { name: "工厂当前策略", data: [216.2, 220.4] },
      { name: "MPC 策略", data: [190.1, null] },
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
});
