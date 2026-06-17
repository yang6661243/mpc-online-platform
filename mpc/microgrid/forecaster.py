"""Load forecasting model trainer — python -m mpc.microgrid.forecaster --config forecaster.yaml"""
import argparse, os, sys, time
import yaml
import numpy as np
import pandas as pd
from openpyxl import load_workbook

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from mpc.solvers.forecasting import LoadForecaster, seasonal_naive_t96, seasonal_naive_t672
from mpc.solvers.forecasting.covariates import build_calendar_covariates, align_covariates
from mpc.solvers.forecasting.metrics import compute_peak_risk_metrics
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from mpc.microgrid.config_profiles import load_profiled_config


def _resolve_path(path):
    if os.path.isabs(path):
        return path
    return os.path.join(ROOT, path)


def _load_sheet_data(filepath, sheet):
    """Read load ratio time-series from a specific sheet in xlsx.
    Returns (values: np.ndarray, timestamps: pd.DatetimeIndex)."""
    wb = load_workbook(filepath, read_only=True, data_only=True)
    ws = wb[sheet]
    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(c or '').lower() for c in next(rows_iter)]

    # Find time and value columns
    time_idx = 0
    for kw in ['time', 'time', 'date', 'timestamp']:
        for i, h in enumerate(headers):
            if kw in h:
                time_idx = i
                break
        else:
            continue
        break

    val_idx = None
    for kw in ['load', 'demand', 'value']:
        for i, h in enumerate(headers):
            if kw in h:
                val_idx = i
                break
        if val_idx is not None:
            break
    if val_idx is None:
        val_idx = 1  # fallback: second column

    timestamps = []
    values = []
    for row in rows_iter:
        if row[time_idx] is None or row[val_idx] is None:
            continue
        try:
            timestamps.append(pd.Timestamp(row[time_idx]))
            values.append(float(row[val_idx]))
        except (ValueError, TypeError):
            continue
    wb.close()
    return np.array(values), pd.DatetimeIndex(timestamps)


def _build_covariates_from_config(cfg, timestamps):
    """Build covariate DataFrame from config section.

    Args:
        cfg: full config dict (should have optional 'covariates' key).
        timestamps: DatetimeIndex covering training data.

    Returns:
        DataFrame or None if no covariate sources are configured.

    Raises:
        SystemExit: if external_columns requires weather/holiday features but the
                    corresponding data file is missing or unreadable.
    """
    cov_cfg = cfg.get('covariates', {})
    features_cfg = cfg.get('features', {})
    external_cols = features_cfg.get('external_columns', [])
    # Determine which covariate types are required by external_columns
    weather_col_names = set(cov_cfg.get('weather_columns', {}).keys())
    holiday_col_names = {
        'is_public_holiday',
        'is_adjusted_workday',
        'is_day_before_holiday',
        'is_day_after_holiday',
    }
    needs_weather = bool(set(external_cols) & weather_col_names)
    needs_holiday = bool(set(external_cols) & holiday_col_names)

    if not cov_cfg and not external_cols:
        return None

    cov_dfs = []

    # 1. Public holiday calendar
    holiday_file = cov_cfg.get('holiday_calendar_file')
    if holiday_file:
        holiday_path = _resolve_path(holiday_file)
        if not os.path.exists(holiday_path):
            if needs_holiday:
                print(f"ERROR: external_columns requires holiday features but "
                      f"holiday_calendar_file not found: {holiday_path}")
                sys.exit(1)
            print(f"   WARNING: holiday calendar not found: {holiday_path}, skipping.")
        else:
            sheet = cov_cfg.get('holiday_calendar_sheet', 'calendar')
            cal_df = pd.read_excel(holiday_path, sheet_name=sheet)
            # Extend timestamps by 1 day on each end for day-before/after computation
            extended_ts = pd.date_range(
                start=timestamps.min() - pd.Timedelta(days=1),
                end=timestamps.max() + pd.Timedelta(days=1),
                freq='15min'
            )
            cal_cov = build_calendar_covariates(extended_ts, public_calendar=cal_df)
            cov_dfs.append(cal_cov)
            print(f"   Loaded holiday calendar: {holiday_path}")
    elif needs_holiday:
        print("ERROR: external_columns requires holiday features but no "
              "holiday_calendar_file configured in [covariates] section.")
        sys.exit(1)

    # 2. Historical weather
    weather_file = cov_cfg.get('historical_weather_file')
    if weather_file:
        weather_path = _resolve_path(weather_file)
        if not os.path.exists(weather_path):
            if needs_weather:
                print(f"ERROR: external_columns requires weather features but "
                      f"historical_weather_file not found: {weather_path}")
                sys.exit(1)
            print(f"   WARNING: weather file not found: {weather_path}, skipping.")
        else:
            sheet = cov_cfg.get('historical_weather_sheet', 'weather')
            weather_raw = pd.read_excel(weather_path, sheet_name=sheet)

            # Map column names
            col_mapping = cov_cfg.get('weather_columns', {})
            rename = {}
            numeric_cols = []
            for cov_name, candidates in col_mapping.items():
                for col in weather_raw.columns:
                    col_name = str(col).lower()
                    if any(col_name == kw.lower() or kw.lower() in col_name
                           for kw in candidates if isinstance(kw, str)):
                        rename[col] = cov_name
                        numeric_cols.append(cov_name)
                        break

            if numeric_cols:
                weather_raw = weather_raw.rename(columns=rename)

                # Find time column
                time_col = None
                for kw in ['time', 'time', 'date', 'timestamp']:
                    for col in weather_raw.columns:
                        if kw.lower() in str(col).lower():
                            time_col = col
                            break
                    if time_col:
                        break

                if time_col is None:
                    if needs_weather:
                        print("ERROR: no time column found in weather file, "
                              "but external_columns requires weather features.")
                        sys.exit(1)
                    print("   WARNING: no time column found in weather file, skipping weather.")
                else:
                    weather_raw[time_col] = pd.to_datetime(weather_raw[time_col])
                    weather_raw = weather_raw.rename(columns={time_col: 'time'})

                    weather_cov = align_covariates(timestamps, weather_raw, numeric_cols)
                    cov_dfs.append(weather_cov)
                    print(f"   Loaded weather: {weather_path} (cols: {numeric_cols})")
            else:
                if needs_weather:
                    print(f"ERROR: no matching weather columns found in {weather_path}, "
                          f"but external_columns requires weather features.")
                    sys.exit(1)
                print(f"   WARNING: no matching weather columns found in {weather_path}")
    elif needs_weather:
        print("ERROR: external_columns requires weather features but no "
              "historical_weather_file configured in [covariates] section.")
        sys.exit(1)

    if not cov_dfs:
        return None

    # Merge all covariate DataFrames on 'time'
    result = cov_dfs[0]
    for other in cov_dfs[1:]:
        # Only merge columns not already in result
        new_cols = [c for c in other.columns if c not in result.columns]
        if new_cols:
            result = result.merge(other[['time'] + new_cols], on='time', how='left')
    return result


def main():
    parser = argparse.ArgumentParser(description="Train load forecasting model")
    parser.add_argument("--config", required=True, help="Path to forecaster YAML config")
    parser.add_argument("--profile", help="Named profile inside a profiled YAML config")
    args = parser.parse_args()

    config_path = _resolve_path(args.config)
    if not os.path.exists(config_path):
        print(f"ERROR: config file not found: {config_path}")
        sys.exit(1)

    cfg = load_profiled_config(config_path, args.profile)

    data_cfg = cfg.get('data', {})
    data_file = _resolve_path(data_cfg.get('file', 'mpc/scenarios/train_load.xlsx'))
    data_sheet = data_cfg.get('sheet', 'train')
    model_path = _resolve_path(cfg.get('model_path', 'outputs/models/forecaster.joblib'))

    profile_label = f" [profile={cfg.get('_profile')}]" if cfg.get('_profile') else ""
    print(f"Config: {config_path}{profile_label}")
    print(f"Data:   {data_file} [{data_sheet}]")

    # ── 1. Load data ──
    print("\n" + "=" * 60)
    print("1. Loading training data...")
    t0 = time.time()
    values, timestamps = _load_sheet_data(data_file, data_sheet)
    T = len(values)
    print(f"   {T} points ({T / 96:.1f} days), range=[{values.min():.4f}, {values.max():.4f}]")
    print(f"   Time: {timestamps[0]} to {timestamps[-1]}")

    # ── 2. Build covariates (if configured) ──
    print("\n" + "=" * 60)
    print("2. Building covariates...")
    covariates = _build_covariates_from_config(cfg, timestamps)
    if covariates is not None:
        print(f"   Covariate columns: {[c for c in covariates.columns if c != 'time']}")
    else:
        print("   No covariates configured (using internal features only).")

    # ── 3. Train ──
    print("\n" + "=" * 60)
    print("3. Training LightGBM model...")
    # Use example config to pick up features.external_columns etc.
    forecaster = LoadForecaster(cfg)
    train_metrics = forecaster.train(values, timestamps, covariates=covariates)
    train_time = time.time() - t0
    print(f"   Train MAE: {train_metrics['train_mae']:.6f}")
    print(f"   Val MAE:   {train_metrics['val_mae']:.6f}")
    print(f"   Val RMSE:  {train_metrics['val_rmse']:.6f}")
    print(f"   Val MAPE:  {train_metrics['val_mape']:.2f}%")
    print(f"   N train:   {train_metrics['n_train']}")
    print(f"   N val:     {train_metrics['n_val']}")
    print(f"   Time:      {train_time:.1f}s")

    # ── 4. Feature importance ──
    print("\n" + "=" * 60)
    print("4. Top 10 feature importance:")
    fi = forecaster.feature_importance
    for i, (feat, imp) in enumerate(sorted(fi.items(), key=lambda x: -x[1])[:10]):
        print(f"   {i + 1:2d}. {feat:30s} {imp:.1f}")

    # ── 5. Backtest ──
    eval_cfg = cfg.get('evaluate', {})
    horizon = eval_cfg.get('horizon_steps', 192)
    backtest_every = eval_cfg.get('backtest_every', 16)
    train_ratio = cfg.get('train_ratio', 0.8)

    print("\n" + "=" * 60)
    print(f"5. Time-series backtest (rolling {horizon//4}h forecasts)...")

    split_idx = int(len(values) * train_ratio)
    test_values = values[split_idx:]
    test_timestamps = timestamps[split_idx:]

    lgb_forecasts = []
    actual_windows = []
    all_errors = []  # per-horizon LightGBM errors
    naive96_errors = []  # per-horizon naive96 errors
    naive672_errors = []  # per-horizon naive672 errors
    forecast_times = []  # timestamps for each forecast point
    n_windows = 0
    t1 = time.time()

    for start in range(0, len(test_values) - horizon, backtest_every):
        hist = values[:split_idx + start]
        start_time = test_timestamps.iloc[start] if hasattr(test_timestamps, 'iloc') else test_timestamps[start]
        actual = test_values[start:start + horizon]

        # Extract future covariates for this window
        future_cov = None
        if covariates is not None:
            future_ts = pd.date_range(
                start=start_time, periods=horizon, freq='15min'
            )
            mask = covariates['time'].isin(future_ts)
            if mask.any():
                future_cov = covariates[mask].reset_index(drop=True)

        try:
            pred = forecaster.predict(np.array(hist, dtype=np.float64), start_time,
                                       horizon, future_covariates=future_cov)
        except ValueError:
            # Hard error from missing covariates — propagate
            raise
        except Exception as e:
            print(f"   ERROR: window at step {start} failed: {e}")
            continue

        lgb_forecasts.append(pred)
        actual_windows.append(actual)
        n_windows += 1

        # Per-horizon errors
        naive96_pred = seasonal_naive_t96(hist, horizon)
        naive672_pred = seasonal_naive_t672(hist, horizon)
        for h in range(horizon):
            ts = start_time + pd.Timedelta(minutes=15 * h)
            all_errors.append({'time': ts, 'horizon': h, 'error': actual[h] - pred[h],
                               'abs_error': abs(actual[h] - pred[h]),
                               'actual': actual[h], 'predicted': pred[h]})
            naive96_errors.append({'horizon': h, 'error': actual[h] - naive96_pred[h],
                                   'abs_error': abs(actual[h] - naive96_pred[h])})
            naive672_errors.append({'horizon': h, 'error': actual[h] - naive672_pred[h],
                                    'abs_error': abs(actual[h] - naive672_pred[h])})
            forecast_times.append(ts)

    backtest_time = time.time() - t1

    if n_windows == 0:
        print("   ERROR: No backtest windows completed")
        sys.exit(1)

    all_actual = np.concatenate(actual_windows)
    all_pred = np.concatenate(lgb_forecasts)
    lgb_mae = float(np.mean(np.abs(all_actual - all_pred)))

    # Convert error lists to DataFrames
    all_err_df = pd.DataFrame(all_errors)
    n96_err_df = pd.DataFrame(naive96_errors)
    n672_err_df = pd.DataFrame(naive672_errors)

    # Build baseline arrays from error DataFrames
    naive96_arr = (all_err_df['actual'].values - n96_err_df['error'].values)
    naive672_arr = (all_err_df['actual'].values - n672_err_df['error'].values)
    naive96_mae = float(n96_err_df['abs_error'].mean())
    naive672_mae = float(n672_err_df['abs_error'].mean())

    backtest_time = time.time() - t1

    print(f"   {n_windows} windows in {backtest_time:.1f}s")
    print(f"   {'Model':<30s} {'MAE':>10s}")
    print(f"   {'-' * 40}")
    print(f"   {'LightGBM':<30s} {lgb_mae:10.6f}")
    print(f"   {'Naive t-96 (yesterday)':<30s} {naive96_mae:10.6f}")
    print(f"   {'Naive t-672 (last week)':<30s} {naive672_mae:10.6f}")

    # Horizon segment errors
    print("\n   Horizon segment MAE:")
    segments = [(0, 24, '0-6h'), (24, 48, '6-12h'), (48, 96, '12-24h'),
                (96, 144, '24-36h'), (144, 192, '36-48h')]
    for h0, h1, label in segments:
        seg = all_err_df[(all_err_df['horizon'] >= h0) & (all_err_df['horizon'] < h1)]
        if len(seg) > 0:
            print(f"   {label:10s}  {seg['abs_error'].mean():.6f}")

    # ── 6. Build comparison Excel ──
    print("\n" + "=" * 60)
    print("6. Building comparison evaluation workbook...")
    eval_excel = _resolve_path(cfg.get('evaluate', {}).get(
        'eval_excel', 'outputs/load_forecast_evaluation.xlsx'))
    os.makedirs(os.path.dirname(eval_excel) or '.', exist_ok=True)

    peak_m = compute_peak_risk_metrics(all_actual, all_pred)
    naive96_peak = compute_peak_risk_metrics(all_actual, naive96_arr)
    naive672_peak = compute_peak_risk_metrics(all_actual, naive672_arr)

    print(f"   Peak risk (LightGBM):      under_p90={peak_m['positive_error_p90']:.6f}  "
          f"peak_err={peak_m['actual_peak_error']:.6f}  peak_mae={peak_m['peak_period_mae']:.6f}")
    print(f"   Peak risk (Naive t-96):    under_p90={naive96_peak['positive_error_p90']:.6f}  "
          f"peak_err={naive96_peak['actual_peak_error']:.6f}  peak_mae={naive96_peak['peak_period_mae']:.6f}")

    wb = Workbook()
    hf = Font(bold=True)
    hfl = PatternFill("solid", fgColor="DDEEFF")
    ha = Alignment(horizontal="center")

    def _set_headers(ws, headers):
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = hf; cell.fill = hfl; cell.alignment = ha

    # Sheet 1: model_summary
    ws_sum = wb.active
    ws_sum.title = 'model_summary'
    _set_headers(ws_sum, ['模型', 'MAE', 'RMSE', 'MAPE(%)', '低估P80', '低估P90',
                          '最大低估', '峰值点误差', '高峰MAE', '特征数', '训练样本', '验证样本'])
    def _add_model_row(ws, name, mae, rmse, mape, peak, n_feat, n_tr, n_val):
        ws.append([name, round(mae, 6), round(rmse, 6), round(mape, 2),
                   round(peak['positive_error_p80'], 6),
                   round(peak['positive_error_p90'], 6),
                   round(peak['max_under_forecast'], 6),
                   round(peak['actual_peak_error'], 6),
                   round(peak['peak_period_mae'], 6),
                   n_feat, n_tr, n_val])

    lgb_rmse = float(np.sqrt(np.mean((all_actual - all_pred) ** 2)))
    lgb_mape = float((np.mean(np.abs(all_actual - all_pred) / all_actual.clip(1e-6))) * 100)
    naive96_rmse = float(np.sqrt(np.mean((all_actual - naive96_arr) ** 2)))
    naive96_mape = float((np.mean(np.abs(all_actual - naive96_arr) / all_actual.clip(1e-6))) * 100)
    naive672_rmse = float(np.sqrt(np.mean((all_actual - naive672_arr) ** 2)))
    naive672_mape = float((np.mean(np.abs(all_actual - naive672_arr) / all_actual.clip(1e-6))) * 100)

    _add_model_row(ws_sum, 'LightGBM', lgb_mae, lgb_rmse, lgb_mape, peak_m,
                   len(forecaster.feature_cols), train_metrics['n_train'], train_metrics['n_val'])
    _add_model_row(ws_sum, 'Naive_t96', naive96_mae, naive96_rmse, naive96_mape, naive96_peak, 0, 0, 0)
    _add_model_row(ws_sum, 'Naive_t672', naive672_mae, naive672_rmse, naive672_mape, naive672_peak, 0, 0, 0)
    for c in range(1, 13):
        ws_sum.column_dimensions[get_column_letter(c)].width = 16

    # Sheet 2: horizon_metrics
    ws_hor = wb.create_sheet('horizon_metrics')
    _set_headers(ws_hor, ['分段', 'LightGBM_MAE', 'LightGBM_RMSE', 'Naive96_MAE', 'Naive672_MAE'])
    segments = [(0, 24, '0-6h'), (24, 48, '6-12h'), (48, 96, '12-24h'),
                (96, 144, '24-36h'), (144, 192, '36-48h')]
    for h0, h1, label in segments:
        lgb_seg = all_err_df[(all_err_df['horizon'] >= h0) & (all_err_df['horizon'] < h1)]
        n96_seg = n96_err_df[(n96_err_df['horizon'] >= h0) & (n96_err_df['horizon'] < h1)]
        n672_seg = n672_err_df[(n672_err_df['horizon'] >= h0) & (n672_err_df['horizon'] < h1)]
        ws_hor.append([label,
                       round(float(lgb_seg['abs_error'].mean()), 6) if len(lgb_seg) > 0 else '',
                       round(float(np.sqrt((lgb_seg['error'] ** 2).mean())), 6) if len(lgb_seg) > 0 else '',
                       round(float(n96_seg['abs_error'].mean()), 6) if len(n96_seg) > 0 else '',
                       round(float(n672_seg['abs_error'].mean()), 6) if len(n672_seg) > 0 else ''])
    for c in range(1, 6):
        ws_hor.column_dimensions[get_column_letter(c)].width = 18

    # Sheet 3: peak_metrics
    ws_peak = wb.create_sheet('peak_metrics')
    _set_headers(ws_peak, ['指标', 'LightGBM', 'Naive_t96', 'Naive_t672'])
    pm_keys = [
        ('positive_error_p80', '正向低估误差P80'),
        ('positive_error_p90', '正向低估误差P90'),
        ('max_under_forecast', '最大低估误差'),
        ('actual_peak_error', '实际峰值点误差'),
        ('peak_period_mae', '高峰时段MAE'),
    ]
    for key, label in pm_keys:
        ws_peak.append([label, round(peak_m[key], 6),
                        round(naive96_peak[key], 6), round(naive672_peak[key], 6)])
    ws_peak.column_dimensions['A'].width = 22
    for c in ['B', 'C', 'D']:
        ws_peak.column_dimensions[c].width = 16

    # Sheet 4: forecast_samples
    ws_samp = wb.create_sheet('forecast_samples')
    _set_headers(ws_samp, ['时间', '实际负荷', 'LightGBM预测', 'Naive96预测',
                           'Naive672预测', 'LGB_误差', 'N96_误差', 'N672_误差'])
    max_samples = min(2000, len(all_actual))
    sample_step = max(1, len(all_actual) // max_samples)
    times_list = all_err_df['time'].tolist() if 'time' in all_err_df.columns else forecast_times
    for idx in range(0, len(all_actual), sample_step):
        ts_str = ''
        if idx < len(times_list):
            t = times_list[idx]
            ts_str = t.strftime('%Y-%m-%d %H:%M:%S') if hasattr(t, 'strftime') else str(t)
        ws_samp.append([
            ts_str,
            round(float(all_actual[idx]), 6),
            round(float(all_pred[idx]), 6),
            round(float(naive96_arr[idx]), 6),
            round(float(naive672_arr[idx]), 6),
            round(float(all_actual[idx] - all_pred[idx]), 6),
            round(float(all_actual[idx] - naive96_arr[idx]), 6),
            round(float(all_actual[idx] - naive672_arr[idx]), 6),
        ])
    for c in range(1, 9):
        ws_samp.column_dimensions[get_column_letter(c)].width = 16

    wb.save(eval_excel)
    print(f"   Saved: {eval_excel}")

    # ── 7. Save model ──
    print("\n" + "=" * 60)
    print("7. Saving model...")
    os.makedirs(os.path.dirname(model_path) or '.', exist_ok=True)
    forecaster.save(model_path)
    print(f"   Saved: {model_path}")

    print("\n" + "=" * 60)
    print("TRAINING COMPLETE")
    print(f"   LightGBM backtest MAE: {lgb_mae:.6f}")
    print(f"   Naive t-96 MAE:        {naive96_mae:.6f}")
    print(f"   Naive t-672 MAE:       {naive672_mae:.6f}")


if __name__ == '__main__':
    main()
