import type { DashboardResponse } from "./types";

export async function fetchDashboard(
  plantId: string,
  windowHours: number,
  runId?: string,
  signal?: AbortSignal,
): Promise<DashboardResponse> {
  const params = new URLSearchParams({ window_hours: String(windowHours) });
  if (runId) {
    params.set("run_id", runId);
  }
  const response = await fetch(
    `/api/v1/plants/${encodeURIComponent(plantId)}/dashboard?${params}`,
    { signal },
  );
  if (!response.ok) {
    throw new Error(`dashboard request failed: HTTP ${response.status}`);
  }
  return response.json() as Promise<DashboardResponse>;
}
