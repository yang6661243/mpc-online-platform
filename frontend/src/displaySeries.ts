import type { DashboardSeriesPoint, DisplaySeriesQuality, DisplaySeriesResponse } from "./types";

export function qualitySummary(quality: DisplaySeriesQuality): string {
  const values = Object.values(quality);
  if (values.includes("gap")) return "display:gap";
  if (values.includes("interpolated_quadratic")) return "display:interpolated_quadratic";
  return "display:observed";
}

export function displaySeriesToDashboardSeries(response: DisplaySeriesResponse): Array<DashboardSeriesPoint & { display_quality: DisplaySeriesQuality }> {
  return response.series.map((point) => ({
    time: point.time,
    actual_grid_power_kw: point.grid_power_kw,
    actual_battery_power_kw: point.battery_power_kw,
    actual_soc: point.soc,
    actual_load_kw: null,
    actual_pv_kw: null,
    load_minus_pv_kw: point.load_minus_pv_kw,
    mpc_grid_power_kw: null,
    mpc_battery_power_kw: null,
    mpc_soc: null,
    mpc_load_kw: null,
    mpc_pv_kw: null,
    buy_price: null,
    sell_price: null,
    quality_flag: qualitySummary(point.quality),
    display_quality: point.quality,
  }));
}
