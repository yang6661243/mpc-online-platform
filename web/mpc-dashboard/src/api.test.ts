import { describe, expect, test } from "vitest";
import { buildDashboardUrl, buildDisplaySeriesUrl, buildRunMpcRequestUrl, toApiTime } from "./api";

describe("dashboard API", () => {
  test("builds a dashboard URL with run id and explicit time range", () => {
    const url = buildDashboardUrl("hehong_huajin", {
      windowHours: 24,
      runId: "hehong_apr2026_real_mpc",
      startTime: "2026-04-13T00:00",
      endTime: "2026-04-14T00:00",
    });

    expect(url).toBe(
      "/api/v1/plants/hehong_huajin/dashboard?window_hours=24&run_id=hehong_apr2026_real_mpc&start_time=2026-04-13T00%3A00&end_time=2026-04-14T00%3A00",
    );
  });

  test("uses the MPC run endpoint for realtime control requests", () => {
    expect(buildRunMpcRequestUrl()).toBe("/api/v1/mpc/run");
  });

  test("preserves datetime-local values as local API timestamps", () => {
    expect(toApiTime("2026-06-12T02:00")).toBe("2026-06-12T02:00:00");
    expect(toApiTime("2026-06-12T02:00:30")).toBe("2026-06-12T02:00:30");
  });

  test("builds a display series URL for raw realtime chart data", () => {
    const url = buildDisplaySeriesUrl("hehong_huajin", {
      windowHours: 2,
      referenceTime: "2026-06-16T10:04:00",
    });

    expect(url).toBe(
      "/api/v1/plants/hehong_huajin/display-series?window_hours=2&reference_time=2026-06-16T10%3A04%3A00",
    );
  });
});
