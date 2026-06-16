import type { DashboardResponse, DisplaySeriesResponse, RunMpcRequest, RunMpcResponse } from "./types";

export interface DashboardRequestOptions {
  windowHours: number;
  runId?: string;
  startTime?: string;
  endTime?: string;
}

export interface DisplaySeriesRequestOptions {
  windowHours: number;
  referenceTime?: string;
  maxGapMinutes?: number;
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

export function buildDisplaySeriesUrl(plantId: string, options: DisplaySeriesRequestOptions): string {
  const params = new URLSearchParams({ window_hours: String(options.windowHours) });
  if (options.referenceTime) {
    params.set("reference_time", options.referenceTime);
  }
  if (options.maxGapMinutes) {
    params.set("max_gap_minutes", String(options.maxGapMinutes));
  }
  return `/api/v1/plants/${encodeURIComponent(plantId)}/display-series?${params}`;
}

export async function fetchDisplaySeries(
  plantId: string,
  options: DisplaySeriesRequestOptions,
  signal?: AbortSignal,
): Promise<DisplaySeriesResponse> {
  const response = await fetch(buildDisplaySeriesUrl(plantId, options), { signal });
  if (!response.ok) {
    throw new Error(`display series request failed: HTTP ${response.status}`);
  }
  return response.json() as Promise<DisplaySeriesResponse>;
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
