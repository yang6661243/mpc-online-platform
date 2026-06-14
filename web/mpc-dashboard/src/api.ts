import type { DashboardResponse } from "./types";

export interface DashboardRequestOptions {
  windowHours: number;
  runId?: string;
  startTime?: string;
  endTime?: string;
}

export function buildDashboardUrl(plantId: string, options: DashboardRequestOptions): string {
  const params = new URLSearchParams({ window_hours: String(options.windowHours) });
  if (options.runId) {
    params.set("run_id", options.runId);
  }
  if (options.startTime) {
    params.set("start_time", options.startTime);
  }
  if (options.endTime) {
    params.set("end_time", options.endTime);
  }
  return `/api/v1/plants/${encodeURIComponent(plantId)}/dashboard?${params}`;
}

export async function fetchDashboard(
  plantId: string,
  options: DashboardRequestOptions,
  signal?: AbortSignal,
): Promise<DashboardResponse> {
  const response = await fetch(buildDashboardUrl(plantId, options), { signal });
  if (!response.ok) {
    throw new Error(`dashboard request failed: HTTP ${response.status}`);
  }
  return response.json() as Promise<DashboardResponse>;
}
