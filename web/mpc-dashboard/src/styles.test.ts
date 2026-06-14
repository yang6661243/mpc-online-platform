import { describe, expect, it } from "vitest";
import { STRATEGY_CHART_HEIGHT_PX, STRATEGY_GRID_TEMPLATE_COLUMNS } from "./layout";

describe("dashboard layout styles", () => {
  it("stacks strategy cards vertically with enough chart height for dense curves", () => {
    expect(STRATEGY_GRID_TEMPLATE_COLUMNS).toBe("minmax(0, 1fr)");
    expect(STRATEGY_CHART_HEIGHT_PX).toBeGreaterThanOrEqual(520);
  });
});
