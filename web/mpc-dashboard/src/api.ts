import type { DashboardResponse, RunMpcRequest, RunMpcResponse } from "./types";

export interface DashboardRequestOptions {
  windowHours: number;
  runId?: string;
  startTime?: string;
  endTime?: string;
}

export function toApiTime(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;

  const localDateTime = trimmed.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::(\d{2}))?$/);
  if (localDateTime) {
    return localDateTime[2] ? trimmed : `${trimmed}:00`;
  }

  const date = new Date(trimmed);
  return Number.isNaN(date.getTime()) ? trimmed : date.toISOString();
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

export function buildRunMpcRequestUrl(): string {
  return "/api/v1/mpc/run";
}

export async function runMpc(request: RunMpcRequest, signal?: AbortSignal): Promise<RunMpcResponse> {
  const response = await fetch(buildRunMpcRequestUrl(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? `: ${body.detail}` : "";
    } catch {
      detail = "";
    }
    throw new Error(`mpc run failed: HTTP ${response.status}${detail}`);
  }
  return response.json() as Promise<RunMpcResponse>;
}
