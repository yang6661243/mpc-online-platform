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

export interface RunMpcResponse {
  success: boolean;
  run_id: string;
  plant_id: string;
  status: string;
  target_peak_kw: number | null;
  scenario_path: string;
  comparison: StrategyComparison | null;
  message: string;
}
