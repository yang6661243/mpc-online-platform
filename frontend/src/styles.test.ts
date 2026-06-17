import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  CONTROL_RAIL_CARD_TITLES,
  DASHBOARD_VISUAL_SCOPE,
  LEFT_CHART_TITLES,
  MAIN_KPI_TITLES,
  OPTIMIZATION_TARGETS,
  PLANT_OPTIONS,
  STRATEGY_CHART_HEIGHT_PX,
  STRATEGY_GRID_TEMPLATE_COLUMNS,
  TOPBAR_TIME_LABEL,
} from "./layout";

const stylesCss = readFileSync(fileURLToPath(new URL("./styles.css", import.meta.url)), "utf8");

describe("dashboard layout styles", () => {
  it("stacks strategy cards vertically with enough chart height for dense curves", () => {
    expect(STRATEGY_GRID_TEMPLATE_COLUMNS).toBe("minmax(0, 1fr)");
    expect(STRATEGY_CHART_HEIGHT_PX).toBeGreaterThanOrEqual(520);
  });

  it("defines the single-screen EMS dashboard layout sections", () => {
    expect(PLANT_OPTIONS).toEqual([
      { label: "和宏华进", value: "hehong_huajin" },
      { label: "奥来德", value: "aolaide" },
    ]);
    expect(LEFT_CHART_TITLES).toEqual(["收益对比图", "收益折线图", "需量对比图"]);
    expect(MAIN_KPI_TITLES).toEqual(["累计收益", "最大需量", "峰谷套利收益", "电池循环次数"]);
    expect(OPTIMIZATION_TARGETS.map((target) => target.label)).toEqual([
      "100%需量+峰谷套利",
      "70%需量+峰谷套利",
      "40%需量+峰谷套利",
    ]);
  });

  it("keeps the revised control rail and card styling copy", () => {
    expect(TOPBAR_TIME_LABEL).toBe("数据获取时间");
    expect(CONTROL_RAIL_CARD_TITLES).toEqual(["MPC 状态", "目标 SOC", "目标/给定需量值", "优化目标", "刷新时间", "数据导入"]);
    expect(CONTROL_RAIL_CARD_TITLES).not.toContain("数据质量");
  });

  it("locks impeccable polish to visual-only dashboard refinement", () => {
    expect(DASHBOARD_VISUAL_SCOPE).toEqual({
      layoutLocked: true,
      register: "product",
      style: "dark-industrial-dashboard",
      primaryTask: "show-model-advantage",
    });
  });

  it("keeps the plant info popup in the DOM and reveals it on hover or focus", () => {
    expect(stylesCss).toContain(".plant-picker:hover .plant-tooltip");
    expect(stylesCss).toContain(".plant-picker:focus-within .plant-tooltip");
    expect(stylesCss).toContain("visibility: hidden");
    expect(stylesCss).toContain("opacity: 0");
  });
});
