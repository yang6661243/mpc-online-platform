import { describe, expect, it } from "vitest";
import { formatChinaTime, formatDataDelay, parseDashboardTime } from "./time";

describe("dashboard time utilities", () => {
  it("treats timezone-less API timestamps as UTC and displays Beijing time", () => {
    const parsed = parseDashboardTime("2026-06-13T16:30:00");

    expect(parsed?.toISOString()).toBe("2026-06-13T16:30:00.000Z");
    expect(formatChinaTime("2026-06-13T16:30:00")).toBe("06-14 00:30");
  });

  it("formats data delay against a supplied clock", () => {
    const now = new Date("2026-06-13T16:33:20.000Z");

    expect(formatDataDelay("2026-06-13T16:30:00", now)).toBe("3 分钟");
  });

  it("returns dash when current telemetry time is absent", () => {
    expect(formatDataDelay(null, new Date("2026-06-13T16:33:20.000Z"))).toBe("--");
  });
});
