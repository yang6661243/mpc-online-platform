export type QualityFlag = "ok" | "missing_grid" | "missing_battery" | string;

export interface DashboardCurrent {
  time: string | null;
  grid_power_kw: number | null;
  battery_power_kw: number | null;
  actual_load_kw?: number | null;
  actual_pv_kw?: number | null;
  mpc_load_kw?: number | null;
  mpc_pv_kw?: number | null;
  load_minus_pv_kw: number | null;
  soc: number | null;
  quality_flag: QualityFlag;
}

export interface StrategyComparison {
  run_id: string;
  actual_peak_kw: number | null;
  mpc_peak_kw: number | null;
  peak_reduction_kw: number | null;
  peak_reduction_pct: number | null;
  actual_cost_yuan: number | null;
  mpc_cost_yuan: number | null;
  cost_saving_yuan: number | null;
  cost_saving_pct: number | null;
}

export interface DashboardSeriesPoint {
  time: string;
  actual_grid_power_kw: number | null;
  actual_battery_power_kw: number | null;
  actual_soc: number | null;
  actual_load_kw?: number | null;
  actual_pv_kw?: number | null;
  load_minus_pv_kw: number | null;
  mpc_grid_power_kw: number | null;
  mpc_battery_power_kw: number | null;
  mpc_soc: number | null;
  mpc_load_kw?: number | null;
  mpc_pv_kw?: number | null;
  buy_price: number | null;
  sell_price: number | null;
  quality_flag: QualityFlag;
  display_quality?: DisplaySeriesQuality;
}

export interface DashboardResponse {
  plant_id: string;
  current: DashboardCurrent;
  comparison: StrategyComparison | null;
  series: DashboardSeriesPoint[];
}

export interface RunMpcRequest {
  request_id: string;
  plant_id: string;
  start_time: string;
  end_time: string;
  profile?: string;
  buy_price?: number;
  sell_price?: number;
  c_deg?: number;
  demand_rate?: number;
  billing_days?: number;
  target_peak_kw?: number;
}

export type DisplayQuality = "observed" | "interpolated_quadratic" | "gap" | "derived";

export interface DisplaySeriesQuality {
  grid_power_kw: DisplayQuality;
  battery_power_kw: DisplayQuality;
  soc: DisplayQuality;
  load_minus_pv_kw: DisplayQuality;
}

export interface DisplaySeriesPoint {
  time: string;
  grid_power_kw: number | null;
  battery_power_kw: number | null;
  soc: number | null;
  load_minus_pv_kw: number | null;
  quality: DisplaySeriesQuality;
  display_only: boolean;
}

export interface DisplaySeriesResponse {
  plant_id: string;
  window_hours: number;
  step_minutes: number;
  display_only: boolean;
  series: DisplaySeriesPoint[];
}

export interface RunMpcResponse {
  success: boolean;
  run_id: string;
  plant_id: string;
  status: string;
  target_peak_kw: number | null;
  scenario_path: string;
  comparison: StrategyComparison | null;
  scheduler_enabled?: boolean;
  message: string;
}

export interface ImportRawDataSheetResult {
  sheet_name: string;
  plant_id: string;
  records: number;
  skipped: number;
}

export interface ImportRawDataResponse {
  success: boolean;
  plant_id: string;
  sheet_count: number;
  sheets: ImportRawDataSheetResult[];
  records_count: number;
  total_rows: number;
  skipped_rows: number;
  file_size_kb: number;
  parse_sec: number;
  write_sec: number;
  total_sec: number;
  aggregated_windows?: number;
  irradiance_updated?: number;
  aggregation_error?: string;
  log_file?: string;
}

export interface MpcHealthStatus {
  plant_id: string;
  state: string;
  label: string;
  telemetry_windows: number;
  ok_windows: number;
  interpolated_windows: number;
  continuous_ok_windows: number;
  last_run_id: string | null;
  last_run_status: string | null;
  last_run_finished_at: string | null;
  last_run_error: string | null;
  minutes_since_last_run: number | null;
  has_gap: boolean;
  max_gap_minutes: number;
  new_windows_since_last: number;
  monthly_demand_ref_set: boolean;
  data_timed_out: boolean;
  minutes_since_latest_data: number | null;
  scheduler_enabled: boolean;
  checked_at: string;
}

export interface MpcProgress {
  run_id: string;
  step: number;
  total_steps: number;
  soc: number | null;
  peak_kw: number | null;
  running_cost: number | null;
  battery_power_kw: number | null;
  grid_power_kw: number | null;
  load_kw: number | null;
  pv_kw: number | null;
  arbitrage: number | null;
  elapsed_seconds: number | null;
}

export interface MpcProgressHistory {
  run_id: string;
  status?: string;
  progress: MpcProgress[];
}
