import { describe, expect, it } from "vitest";
import { formatKw, formatPercent, formatYuan, shortTime } from "./format";

describe("metric formatting", () => {
  it("formats null values as dash", () => {
    expect(formatKw(null)).toBe("--");
    expect(formatYuan(undefined)).toBe("--");
  });

  it("formats power, money, and percentage values", () => {
    expect(formatKw(216.234)).toBe("216.2 kW");
    expect(formatYuan(12.345)).toBe("12.35 元");
    expect(formatPercent(9.345)).toBe("9.3%");
  });

  it("formats ISO timestamps for compact display", () => {
    expect(shortTime("2026-06-13T16:30:00")).toBe("06-14 00:30");
  });
});
