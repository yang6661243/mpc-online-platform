import { describe, expect, it } from "vitest";
import { dashboardStatus } from "./status";
import type { DashboardResponse } from "./types";

const base: DashboardResponse = {
  plant_id: "ecloud_factory",
  current: {
    time: "2026-06-13T04:30:00",
    grid_power_kw: 216.2,
    battery_power_kw: 0.09,
    load_minus_pv_kw: 216.29,
    soc: 0.5,
    quality_flag: "ok",
  },
  comparison: null,
  series: [],
};

describe("dashboard status", () => {
  it("shows strategy pending when telemetry exists but MPC comparison is absent", () => {
    expect(dashboardStatus(base)).toEqual({
      label: "实时数据已接入，MPC 策略待生成",
      tone: "pending",
    });
  });

  it("shows ok when telemetry and MPC comparison both exist", () => {
    expect(
      dashboardStatus({
        ...base,
        comparison: {
          run_id: "mpc_req",
          actual_peak_kw: 430,
          mpc_peak_kw: 390,
          peak_reduction_kw: 40,
          peak_reduction_pct: 9.3,
          actual_cost_yuan: 210,
          mpc_cost_yuan: 185,
          cost_saving_yuan: 25,
          cost_saving_pct: 11.9,
        },
      }),
    ).toEqual({ label: "实时数据与 MPC 策略已生成", tone: "ok" });
  });

  it("surfaces non-ok telemetry quality", () => {
    expect(
      dashboardStatus({
        ...base,
        current: { ...base.current, quality_flag: "missing_battery" },
      }),
    ).toEqual({ label: "数据质量异常：missing_battery", tone: "warning" });
  });
});
