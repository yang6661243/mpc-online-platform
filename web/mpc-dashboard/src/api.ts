import type { DashboardResponse } from "./types";

export async function fetchDashboard(
  plantId: string,
  windowHours: number,
  signal?: AbortSignal,
): Promise<DashboardResponse> {
  const params = new URLSearchParams({ window_hours: String(windowHours) });
  const response = await fetch(
    `/api/v1/plants/${encodeURIComponent(plantId)}/dashboard?${params}`,
    { signal },
  );
  if (!response.ok) {
    throw new Error(`dashboard request failed: HTTP ${response.status}`);
  }
  return response.json() as Promise<DashboardResponse>;
}
