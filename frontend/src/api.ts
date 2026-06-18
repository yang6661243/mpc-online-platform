import type { DashboardResponse, DisplaySeriesResponse, ImportRawDataResponse, MpcHealthStatus, MpcProgress, MpcProgressHistory, RunMpcRequest, RunMpcResponse } from "./types";

export interface DashboardRequestOptions {
  windowHours: number;
  runId?: string;
  profile?: string;
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
  if (options.profile) {
    params.set("profile", options.profile);
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

export function buildPlantInfoUrl(plantId: string): string {
  return `/api/v1/plants/${encodeURIComponent(plantId)}/info`;
}

export function buildImportMpcRunUrl(plantId: string, profile: string): string {
  return `/api/v1/plants/${encodeURIComponent(plantId)}/import-mpc-run?profile=${encodeURIComponent(profile)}`;
}

export function buildImportRawDataUrl(): string {
  return "/api/v1/plants/import-raw-data?auto_aggregate=true&include_irradiance=false";
}

export async function importRawData(
  file: File,
  signal?: AbortSignal,
): Promise<ImportRawDataResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(buildImportRawDataUrl(), {
    method: "POST",
    body: formData,
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
    throw new Error(`原始数据导入失败: HTTP ${response.status}${detail}`);
  }
  return response.json();
}

export async function importMpcRun(
  plantId: string,
  profile: string,
  file: File,
  signal?: AbortSignal,
): Promise<{ success: boolean; run_id: string; point_count: number; time_range: { start: string; end: string } }> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(buildImportMpcRunUrl(plantId, profile), {
    method: "POST",
    body: formData,
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
    throw new Error(`导入失败: HTTP ${response.status}${detail}`);
  }
  return response.json();
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

export function buildMpcStatusUrl(plantId: string, profile?: string): string {
  const params = profile ? `?profile=${encodeURIComponent(profile)}` : "";
  return `/api/v1/plants/${encodeURIComponent(plantId)}/mpc-status${params}`;
}

export async function fetchMpcStatus(
  plantId: string,
  profile?: string,
  signal?: AbortSignal,
): Promise<MpcHealthStatus> {
  const response = await fetch(buildMpcStatusUrl(plantId, profile), { signal });
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? `: ${body.detail}` : "";
    } catch {
      detail = "";
    }
    throw new Error(`mpc status request failed: HTTP ${response.status}${detail}`);
  }
  return response.json() as Promise<MpcHealthStatus>;
}

export function buildMonthlyDemandRefUrl(plantId: string): string {
  return `/api/v1/plants/${encodeURIComponent(plantId)}/monthly-demand-ref`;
}

export async function fetchMonthlyDemandRef(
  plantId: string,
  signal?: AbortSignal,
): Promise<{ plant_id: string; year_month: string; reference_peak_kw: number | null }> {
  const response = await fetch(buildMonthlyDemandRefUrl(plantId), { signal });
  if (!response.ok) throw new Error(`demand ref request failed: HTTP ${response.status}`);
  return response.json();
}

export async function setMonthlyDemandRef(
  plantId: string,
  referencePeakKw: number,
  signal?: AbortSignal,
): Promise<{ success: boolean; reference_peak_kw: number }> {
  const response = await fetch(
    `${buildMonthlyDemandRefUrl(plantId)}?reference_peak_kw=${encodeURIComponent(referencePeakKw)}`,
    { method: "POST", signal },
  );
  if (!response.ok) {
    let detail = "";
    try { const body = await response.json(); detail = typeof body.detail === "string" ? `: ${body.detail}` : ""; } catch { detail = ""; }
    throw new Error(`set demand ref failed: HTTP ${response.status}${detail}`);
  }
  return response.json();
}

export async function clearData(signal?: AbortSignal): Promise<{ success: boolean; deleted: Record<string, number> }> {
  const response = await fetch("/api/v1/admin/clear-data", { method: "POST", signal });
  if (!response.ok) {
    let detail = "";
    try { const body = await response.json(); detail = typeof body.detail === "string" ? `: ${body.detail}` : ""; } catch { detail = ""; }
    throw new Error(`清空数据失败: HTTP ${response.status}${detail}`);
  }
  return response.json();
}

export async function fetchMpcProgress(
  runId: string,
  signal?: AbortSignal,
): Promise<MpcProgress | { run_id: string; status: string }> {
  const response = await fetch(
    `/api/v1/mpc/runs/${encodeURIComponent(runId)}/progress/latest`,
    { signal },
  );
  if (!response.ok) throw new Error(`progress request failed: HTTP ${response.status}`);
  return response.json();
}

export async function fetchMpcProgressHistory(
  runId: string,
  sample: number = 1,
  signal?: AbortSignal,
): Promise<MpcProgressHistory> {
  const params = new URLSearchParams();
  if (sample > 1) params.set("sample", String(sample));
  const qs = params.toString();
  const url = `/api/v1/mpc/runs/${encodeURIComponent(runId)}/progress${qs ? `?${qs}` : ""}`;
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`progress history request failed: HTTP ${response.status}`);
  return response.json();
}
