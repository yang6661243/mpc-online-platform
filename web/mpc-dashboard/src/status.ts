import type { DashboardResponse } from "./types";

export type StatusTone = "ok" | "pending" | "warning" | "error";

export interface DashboardStatus {
  label: string;
  tone: StatusTone;
}

export function dashboardStatus(data: DashboardResponse): DashboardStatus {
  if (data.current.quality_flag !== "ok") {
    return {
      label: `数据质量异常：${data.current.quality_flag}`,
      tone: "warning",
    };
  }
  if (!data.comparison) {
    return {
      label: "实时数据已接入，MPC 策略待生成",
      tone: "pending",
    };
  }
  return {
    label: "实时数据与 MPC 策略已生成",
    tone: "ok",
  };
}
