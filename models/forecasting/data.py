"""Load scenario data from xlsx/csv files for forecasting."""

import os
import csv
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from openpyxl import load_workbook


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_col_index(headers: list[str], keywords: list[str]) -> Optional[int]:
    """Find first column index whose header contains any of the keywords."""
    for kw in keywords:
        for idx, h in enumerate(headers):
            if kw.lower() in h.lower():
                return idx
    return None


def read_xlsx_timeseries(filepath: str, col_keywords: list[str]) -> pd.DataFrame:
    """Read time-series from xlsx into DataFrame with 'time' and 'value' columns.

    Deduces the time column (first column with 'time'/'时间'/'date' in header)
    and the value column matching col_keywords.

    Returns DataFrame with columns: time (datetime), value (float).
    """
    wb = load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.worksheets[0]

    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(c) if c else '' for c in next(rows_iter)]

    time_col = _find_col_index(headers, ['time', '时间', 'date', '日期', 'timestamp'])
    if time_col is None:
        time_col = 0  # fallback: first column

    val_col = _find_col_index(headers, col_keywords)
    if val_col is None:
        raise ValueError(f"Cannot find value column with keywords {col_keywords} in headers: {headers}")

    data = []
    for row in rows_iter:
        t_raw = row[time_col] if time_col < len(row) else None
        v_raw = row[val_col] if val_col < len(row) else None
        if t_raw is None or v_raw is None:
            continue
        try:
            v = float(v_raw)
        except (ValueError, TypeError):
            continue
        try:
            t = pd.Timestamp(t_raw)
        except (ValueError, TypeError):
            continue
        data.append({'time': t, 'value': v})

    wb.close()

    df = pd.DataFrame(data)
    if df.empty:
        raise ValueError(f"No data read from {filepath}")
    return df


def read_csv_timeseries(filepath: str, col_keywords: list[str]) -> pd.DataFrame:
    """Read time-series from CSV into DataFrame with 'time' and 'value' columns.

    Same semantics as read_xlsx_timeseries but for CSV input.
    """
    df = pd.read_csv(filepath, encoding='utf-8-sig')
    headers = list(df.columns)

    time_col = _find_col_index(headers, ['time', '时间', 'date', '日期', 'timestamp'])
    time_col_name = headers[time_col] if time_col is not None else headers[0]

    val_col = _find_col_index(headers, col_keywords)
    if val_col is None:
        raise ValueError(f"Cannot find value column with keywords {col_keywords} in headers: {headers}")
    val_col_name = headers[val_col]

    result = pd.DataFrame({
        'time': pd.to_datetime(df[time_col_name]),
        'value': df[val_col_name].astype(float),
    })
    return result


def load_data(config_path: str | None = None, scenario_file: str | None = None) -> pd.DataFrame:
    """Load load ratio data from the scenario file specified in config or directly.

    Returns DataFrame with columns: time, value (load ratio 0-1).
    """
    if scenario_file is None:
        if config_path is None:
            config_path = os.path.join(ROOT, 'models', 'forecasting', 'config.yaml')
        import yaml
        with open(config_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        scenario_file = os.path.join(ROOT, cfg['data']['scenario_file'])
        col_keywords = cfg['data'].get('scenario_load_col_keywords',
                                       ['负荷', 'load', '有功', 'demand'])
    else:
        col_keywords = ['负荷', 'load', '有功', 'demand']
        if not os.path.isabs(scenario_file):
            scenario_file = os.path.join(ROOT, scenario_file)

    if scenario_file.endswith('.xlsx'):
        return read_xlsx_timeseries(scenario_file, col_keywords)
    elif scenario_file.endswith('.csv'):
        return read_csv_timeseries(scenario_file, col_keywords)
    else:
        raise ValueError(f"Unsupported file format: {scenario_file}")


def load_data_as_series(config_path: str | None = None, scenario_file: str | None = None):
    """Load load ratio data and return (values, timestamps) tuple.

    Returns:
        values: np.ndarray of load ratios [0, 1]
        timestamps: pd.DatetimeIndex
    """
    df = load_data(config_path=config_path, scenario_file=scenario_file)
    df = df.sort_values('time').reset_index(drop=True)
    return df['value'].values, df['time']
