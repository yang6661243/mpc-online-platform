"""
Prepare Hehong Huajin new data (April-May 2026) for offline MPC training + validation.

Usage:
    python scripts/data/prepare_hehong_new_data.py

Reads:
    - /tmp/data.xlsx  (converted from .numbers: grid meter, anti-backflow, SOC)
    - scenarios/hehong_weather_forecast_open_meteo_apr.xlsx  (April irradiance)
    - ~/Desktop/open-meteo-30.83N121.17E4m (3).xlsx  (May irradiance)

Outputs:
    - scenarios/hehonghuajin_new.xlsx  (sheets: train_apr, load_may, pv_may, price)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# ── Config ──────────────────────────────────────────────────────────
LOAD_BASE_KW = 1000.0
PV_CAPACITY_KW = 350.0
PV_EFFICIENCY = 0.95
# tariff: 上海 35kV 大工业两部制
TARIFF = {
    "谷": (0.241, [("00:00", "06:00"), ("22:00", "24:00")]),
    "平": (0.609, [("06:00", "08:00"), ("11:00", "18:00"), ("21:00", "22:00")]),
    "峰": (0.986, [("08:00", "11:00"), ("18:00", "21:00")]),
}
SELL_PRICE = 0.0  # anti-backflow → no export
OUTPUT_FILE = PROJECT_ROOT / "scenarios" / "hehonghuajin_new.xlsx"


def read_meter_data(path: str) -> pd.DataFrame:
    """Read the converted .numbers → xlsx, return 15-min DataFrame with derived columns."""
    df = pd.read_excel(path, sheet_name="Sheet0")
    df["time"] = pd.to_datetime(df["时间"])
    df = df.rename(
        columns={
            "计量电表/总有功功率": "grid_kw",
            "防逆流电表/ADW-总有功功率": "abf_kw",
            "3-BMS/BMS-系统SOC": "soc_pct",
        }
    )
    df = df.set_index("time").sort_index()
    # net load (Load - PV_self_consumed) = ABF - Grid
    df["net_load_kw"] = df["abf_kw"] - df["grid_kw"]
    df["net_load_kw"] = df["net_load_kw"].clip(lower=0)
    return df


def read_irradiance_standard(path: str, irradiance_col: str = None) -> pd.DataFrame:
    """Read hourly irradiance from standard format (has a proper header row).

    Handles files like hehong_weather_forecast_open_meteo_apr.xlsx
    with columns: time, ..., shortwave_radiation, ...
    """
    df = pd.read_excel(path)
    # find time column
    time_col = None
    for col in df.columns:
        if any(kw in str(col).lower() for kw in ["time", "时间"]):
            time_col = col
            break
    if time_col is None:
        raise ValueError(f"No time column found in {path}")

    df["time"] = pd.to_datetime(df[time_col])

    # find irradiance column
    if irradiance_col is None:
        for col in df.columns:
            if any(kw in str(col).lower() for kw in
                   ["shortwave_radiation", "irradiance", "ghi", "solar", "radiation"]):
                irradiance_col = col
                break
    if irradiance_col is None:
        raise ValueError(f"No irradiance column found in {path}")

    df = df[["time", irradiance_col]].rename(
        columns={irradiance_col: "ghi_w_per_m2"}
    )
    df = df.set_index("time").sort_index()
    return df


def read_irradiance_openmeteo(path: str) -> pd.DataFrame:
    """Read hourly irradiance from Open-Meteo API export format.

    Has metadata rows before the actual header. E.g.:
      Row 0: latitude, longitude, ...
      Row 1: <values>
      Row 2: NaN
      Row 3: time, shortwave_radiation (W/m²), ...
      Row 4+: data
    """
    raw = pd.read_excel(path, sheet_name="Sheet1", header=None)
    header_row = None
    for i, row in raw.iterrows():
        if str(row.iloc[0]).strip().lower() == "time":
            header_row = i
            break
    if header_row is None:
        raise ValueError(f"Could not find 'time' header in {path}")

    df = pd.read_excel(path, sheet_name="Sheet1", skiprows=header_row)
    time_col = df.columns[0]
    irrad_col = [c for c in df.columns if "radiation" in str(c).lower()][0]

    df["time"] = pd.to_datetime(df[time_col])
    # Shift year 2025 → 2026
    df["time"] = df["time"] + pd.DateOffset(years=1)

    df = df[["time", irrad_col]].rename(columns={irrad_col: "ghi_w_per_m2"})
    df = df.set_index("time").sort_index()
    return df


def upsample_to_15min(df: pd.DataFrame, value_col: str = "ghi_w_per_m2") -> pd.DataFrame:
    """Upsample hourly irradiance to 15-min using linear interpolation."""
    new_index = pd.date_range(
        start=df.index.min(),
        end=df.index.max(),
        freq="15min",
    )
    df_15min = df.reindex(df.index.union(new_index))
    df_15min[value_col] = df_15min[value_col].interpolate(method="linear")
    df_15min = df_15min.loc[new_index]
    df_15min[value_col] = df_15min[value_col].clip(lower=0, upper=1200)
    return df_15min


def compute_pv_kw(irradiance: pd.Series) -> pd.Series:
    """Convert irradiance (W/m²) to PV output (kW)."""
    ratio = (irradiance / 1000.0).clip(upper=1.0)
    return ratio * PV_CAPACITY_KW * PV_EFFICIENCY


def build_price_series(timestamps: pd.DatetimeIndex) -> pd.DataFrame:
    """Build buy/sell price series from tariff schedule."""
    buy = np.zeros(len(timestamps))
    sell = np.full(len(timestamps), SELL_PRICE)

    for i, ts in enumerate(timestamps):
        t_str = ts.strftime("%H:%M")
        for _period_name, (price, ranges) in TARIFF.items():
            matched = False
            for start_str, end_str in ranges:
                if start_str <= t_str < end_str:
                    buy[i] = price
                    matched = True
                    break
            if matched:
                break

    return pd.DataFrame({"时间": timestamps, "购电价": buy, "售电价": sell})


def process_month(meter: pd.DataFrame, irrad_ghi: pd.DataFrame, label: str) -> pd.DataFrame:
    """Merge meter data with irradiance for one month, compute true load."""
    merged = meter.join(irrad_ghi, how="inner")
    merged["pv_kw"] = compute_pv_kw(merged["ghi_w_per_m2"])
    merged["load_kw"] = merged["net_load_kw"] + merged["pv_kw"]
    merged["load_ratio"] = (merged["load_kw"] / LOAD_BASE_KW).clip(0, 1.0)

    print(f"   [{label}] {len(merged)} rows, "
          f"load={merged['load_kw'].min():.0f}→{merged['load_kw'].max():.0f} kW, "
          f"PV={merged['pv_kw'].min():.0f}→{merged['pv_kw'].max():.0f} kW, "
          f"GHI={merged['ghi_w_per_m2'].min():.0f}→{merged['ghi_w_per_m2'].max():.0f} W/m²")
    return merged


def main():
    print("=" * 60)
    print("1. Loading meter data...")
    meter = read_meter_data("/tmp/data.xlsx")
    print(f"   {len(meter)} rows, {meter.index[0]} → {meter.index[-1]}")

    # ── April irradiance ──
    print("\n2a. Loading April irradiance...")
    irrad_apr_raw = read_irradiance_standard(
        str(PROJECT_ROOT / "scenarios" / "hehong_weather_forecast_open_meteo_apr.xlsx"),
        irradiance_col="shortwave_radiation",
    )
    print(f"   Raw: {len(irrad_apr_raw)} rows (hourly), "
          f"{irrad_apr_raw.index[0]} → {irrad_apr_raw.index[-1]}")
    irrad_apr_15m = upsample_to_15min(irrad_apr_raw)
    print(f"   15-min: {len(irrad_apr_15m)} rows")

    # ── May irradiance ──
    print("\n2b. Loading May irradiance...")
    irrad_may_raw = read_irradiance_openmeteo(
        "/Users/yangjiaowei/Desktop/open-meteo-30.83N121.17E4m (3).xlsx"
    )
    print(f"   Raw: {len(irrad_may_raw)} rows (hourly), "
          f"{irrad_may_raw.index[0]} → {irrad_may_raw.index[-1]}")
    irrad_may_15m = upsample_to_15min(irrad_may_raw)
    print(f"   15-min: {len(irrad_may_15m)} rows")

    # ── Process each month ──
    print("\n3. Processing months...")
    apr_data = process_month(meter, irrad_apr_15m, "April")
    may_data = process_month(meter, irrad_may_15m, "May")

    # Trim May to exactly 30 days (2880 steps) to match irradiance
    may_data = may_data.iloc[:2880]

    # ── Build output sheets ──
    print("\n4. Building output Excel...")

    # Sheet: train_apr (April load ratios for training)
    train_apr = pd.DataFrame({
        "时间": apr_data.index,
        "有功": apr_data["load_ratio"].round(6).values,
    })

    # Sheet: load_may (May load ratios for MPC validation)
    load_may = pd.DataFrame({
        "时间": may_data.index,
        "有功": may_data["load_ratio"].round(6).values,
    })

    # Sheet: pv_may (May irradiance + wind speed for MPC)
    pv_may = pd.DataFrame({
        "time": may_data.index,
        "global_tilted_irradiance (W/m2)": may_data["ghi_w_per_m2"].round(1).values,
        "wind_speed_100m (km/h)": 0.0,
    })

    # Sheet: price
    price = build_price_series(may_data.index)

    # Write
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        train_apr.to_excel(writer, sheet_name="train_apr", index=False)
        load_may.to_excel(writer, sheet_name="load_may", index=False)
        pv_may.to_excel(writer, sheet_name="pv_may", index=False)
        price.to_excel(writer, sheet_name="price", index=False)

    print(f"   Saved: {OUTPUT_FILE}")
    print(f"   Sheets: train_apr({len(train_apr)}), load_may({len(load_may)}), "
          f"pv_may({len(pv_may)}), price({len(price)})")

    # ── Quick stats ──
    print(f"\n5. Data summary:")
    print(f"   Train (April): load ratio avg={train_apr['有功'].mean():.4f}, "
          f"max={train_apr['有功'].max():.4f}")
    print(f"   MPC   (May):   load ratio avg={load_may['有功'].mean():.4f}, "
          f"max={load_may['有功'].max():.4f}")
    print(f"   MPC irradiance: avg={pv_may['global_tilted_irradiance (W/m2)'].mean():.0f}, "
          f"max={pv_may['global_tilted_irradiance (W/m2)'].max():.0f} W/m²")

    print("\n✅ Data preparation complete.")


if __name__ == "__main__":
    main()
