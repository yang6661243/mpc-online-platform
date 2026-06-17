export const STRATEGY_GRID_TEMPLATE_COLUMNS = "minmax(0, 1fr)";
export const STRATEGY_CHART_HEIGHT_PX = 520;

export const PLANT_OPTIONS = [
  { label: "和宏华进", value: "hehong_huajin" },
  { label: "奥来德", value: "aolaide" },
];

export const LEFT_CHART_TITLES = ["收益对比图", "收益折线图", "需量对比图"];
export const MAIN_KPI_TITLES = ["累计收益", "最大需量", "峰谷套利收益", "电池循环次数"];
export const TOPBAR_TIME_LABEL = "数据获取时间";
export const CONTROL_RAIL_CARD_TITLES = ["MPC 状态", "目标 SOC", "目标/给定需量值", "优化目标", "刷新时间", "数据导入", "上传电表数据"];
export const DASHBOARD_VISUAL_SCOPE = {
  layoutLocked: true,
  register: "product",
  style: "dark-industrial-dashboard",
  primaryTask: "show-model-advantage",
};

export const OPTIMIZATION_TARGETS = [
  { label: "100%需量+峰谷套利", value: "demand100" },
  { label: "70%需量+峰谷套利", value: "demand70" },
  { label: "40%需量+峰谷套利", value: "demand40" },
];
