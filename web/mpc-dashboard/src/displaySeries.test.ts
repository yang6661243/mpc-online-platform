import { describe, expect, test } from "vitest";
import { displaySeriesToDashboardSeries, qualitySummary } from "./displaySeries";
import type { DisplaySeriesResponse } from "./types";

const response: DisplaySeriesResponse = {
  plant_id: "hehong_huajin",
  window_hours: 2,
  step_minutes: 1,
  display_only: true,
  series: [
    {
      time: "2026-06-16T02:00:00",
      grid_power_kw: 100,
      battery_power_kw: 10,
      soc: 0.5,
      load_minus_pv_kw: 110,
      quality: {
        grid_power_kw: "observed",
        battery_power_kw: "interpolated_quadratic",
        soc: "observed",
        load_minus_pv_kw: "derived",
      },
      display_only: true,
    },
  ],
};

describe("display series conversion", () => {
  test("maps display-only raw fields into dashboard series shape", () => {
    const series = displaySeriesToDashboardSeries(response);

    expect(series).toEqual([
      {
        time: "2026-06-16T02:00:00",
        actual_grid_power_kw: 100,
        actual_battery_power_kw: 10,
        actual_soc: 0.5,
        actual_load_kw: null,
        actual_pv_kw: null,
        load_minus_pv_kw: 110,
        mpc_grid_power_kw: null,
        mpc_battery_power_kw: null,
        mpc_soc: null,
        mpc_load_kw: null,
        mpc_pv_kw: null,
        buy_price: null,
        sell_price: null,
        quality_flag: "display:interpolated_quadratic",
        display_quality: response.series[0].quality,
      },
    ]);
  });

  test("summarizes observed quality as display:observed", () => {
    expect(
      qualitySummary({
        grid_power_kw: "observed",
        battery_power_kw: "observed",
        soc: "observed",
        load_minus_pv_kw: "derived",
      }),
    ).toBe("display:observed");
  });
});
