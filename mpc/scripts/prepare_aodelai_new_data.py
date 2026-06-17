"""
Prepare Aodelai new data (April-May 2026) - NO PV on site.

Usage:
    python scripts/data/prepare_aodelai_new_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

LOAD_BASE_KW = 1300.0
TARIFF = {
    "谷": (0.241, [("00:00", "06:00"), ("22:00", "24:00")]),
    "平": (0.609, [("06:00", "08:00"), ("11:00", "18:00"), ("21:00", "22:00")]),
    "峰": (0.986, [("08:00", "11:00"), ("18:00", "21:00")]),
}
OUTPUT_FILE = PROJECT_ROOT / "scenarios" / "aodelai_new.xlsx"
RAW_DATA = "/Users/yangjiaowei/Desktop/奥来德(上海)光电材料科技有限公司1号站历史数据.xls"


def main():
    print("=" * 60)
    print("1. Loading data...")
    df = pd.read_excel(RAW_DATA, sheet_name="Sheet0").dropna()
    df["time"] = pd.to_datetime(df["时间"])
    df["abf_kw"] = df["防逆流电表-666/666-合相有功功率Pt"]
    df["grid_kw"] = df["计量电表-1352/1352-总有功功率"]
    df["load_kw"] = (df["abf_kw"] - df["grid_kw"]).clip(lower=0)
    df["load_ratio"] = (df["load_kw"] / LOAD_BASE_KW).clip(0, 1.0)
    df = df.set_index("time").sort_index()

    print(f"   {len(df)} rows, {df.index[0]} → {df.index[-1]}")
    print(f"   Load: {df['load_kw'].min():.0f} → {df['load_kw'].max():.0f} kW")

    apr = df[df.index.month == 4]
    may = df[df.index.month == 5]
    print(f"   April: {len(apr)} rows, May: {len(may)} rows")

    print("\n2. Building output Excel...")
    train_apr = pd.DataFrame({"时间": apr.index, "有功": apr["load_ratio"].round(6).values})
    load_may = pd.DataFrame({"时间": may.index, "有功": may["load_ratio"].round(6).values})
    pv_may = pd.DataFrame({
        "time": may.index,
        "global_tilted_irradiance (W/m2)": 0.0,
        "wind_speed_100m (km/h)": 0.0,
    })

    buy = np.zeros(len(may))
    for i, ts in enumerate(may.index):
        t = ts.strftime("%H:%M")
        for _n, (p, ranges) in TARIFF.items():
            if any(s <= t < e for s, e in ranges):
                buy[i] = p; break
    price = pd.DataFrame({"时间": may.index, "购电价": buy, "售电价": 0.0})

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as w:
        train_apr.to_excel(w, sheet_name="train_apr", index=False)
        load_may.to_excel(w, sheet_name="load_may", index=False)
        pv_may.to_excel(w, sheet_name="pv_may", index=False)
        price.to_excel(w, sheet_name="price", index=False)

    print(f"   Saved: {OUTPUT_FILE}")
    print(f"   train_apr: {len(train_apr)} rows, load_ratio avg={train_apr['有功'].mean():.4f}, max={train_apr['有功'].max():.4f}")
    print(f"   load_may:  {len(load_may)} rows, load_ratio avg={load_may['有功'].mean():.4f}, max={load_may['有功'].max():.4f}")
    print("✅ Done.")


if __name__ == "__main__":
    main()
