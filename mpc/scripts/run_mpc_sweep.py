"""
Batch run MPC with different demand_rate weights (100% / 70% / 40%), 5 days only.

Usage:
    python scripts/data/run_mpc_sweep.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ── Sweep config ──
SWEEPS = [
    {"label": "peak_100", "demand_rate": 39.0, "weight": "100%"},
    {"label": "peak_70",  "demand_rate": 27.3, "weight": "70%"},
    {"label": "peak_40",  "demand_rate": 15.6, "weight": "40%"},
]

BASE_CONFIG = PROJECT_ROOT / "configs" / "mpc" / "hehong_new.yaml"
OUTPUT_DIR = PROJECT_ROOT / "outputs"


def patch_config(base_path: Path, demand_rate: float, days: int) -> Path:
    """Create a temp YAML config with overridden demand_rate and days."""
    import yaml

    with open(base_path) as f:
        cfg = yaml.safe_load(f)

    cfg["cost"]["demand_rate"] = demand_rate
    cfg["mpc"]["days"] = days

    out_path = base_path.parent / f"mpc_sweep_{demand_rate:.1f}.yaml"
    with open(out_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
    return out_path


def run_mpc(config_path: Path) -> Path:
    """Run MPC, return path to output xlsx."""
    cmd = [sys.executable, "-m", "microgrid.mpc", "--config", str(config_path)]
    print(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=600)

    # Parse output path from stdout
    for line in result.stdout.split("\n"):
        if line.startswith("Done: "):
            out_path = Path(line.split("Done: ")[1].strip())
            return out_path

    # Fallback: check stderr and known output path
    print(f"  STDOUT (last 5 lines):")
    for line in result.stdout.split("\n")[-5:]:
        print(f"    {line}")
    if result.stderr:
        print(f"  STDERR:\n{result.stderr[:500]}")

    # Try to find the output file based on config
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    out_file = cfg.get("output", "")
    out_path = PROJECT_ROOT / out_file if out_file else None
    if out_path and out_path.exists():
        return out_path
    raise RuntimeError(f"MPC failed, could not find output file")


def extract_summary(xlsx_path: Path) -> dict:
    """Extract key metrics from MPC output cost_summary sheet."""
    df = pd.read_excel(xlsx_path, sheet_name="cost_summary")
    metrics = {}
    for _, row in df.iterrows():
        key = str(row.iloc[0]).strip()
        val = row.iloc[1] if len(row) > 1 else None
        metrics[key] = val
    return metrics


def main():
    print("=" * 60)
    print("MPC Sweep: demand_rate 100% / 70% / 40%, 5 days")
    print("=" * 60)

    results = []

    for sweep in SWEEPS:
        print(f"\n--- {sweep['label']} (demand_rate={sweep['demand_rate']}, {sweep['weight']}) ---")

        cfg_path = patch_config(BASE_CONFIG, sweep["demand_rate"], days=5)
        print(f"  Config: {cfg_path}")

        out_path = run_mpc(cfg_path)
        print(f"  Output: {out_path}")

        m = extract_summary(out_path)
        m["_label"] = sweep["label"]
        m["_weight"] = sweep["weight"]
        m["_demand_rate"] = sweep["demand_rate"]
        results.append(m)

        # Cleanup temp config
        cfg_path.unlink(missing_ok=True)

    # ── Summary table ──
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    header = f"{'场景':<12} {'需量权重':>8} {'demand_rate':>12} {'目标峰值':>10} {'实际峰值':>10} {'需量费':>10} {'购电费':>10} {'套利收益':>10} {'总成本':>10} {'达成率':>8}"
    print(header)
    print("-" * len(header))

    for r, m in zip(SWEEPS, results):
        print(
            f"{r['label']:<12} "
            f"{r['weight']:>8} "
            f"{r['demand_rate']:>12.1f} "
            f"{m.get('目标峰值(kW)', 'N/A'):>10} "
            f"{m.get('峰值需量(kW)', 'N/A'):>10} "
            f"{m.get('需量费(元)', 'N/A'):>10} "
            f"{m.get('购电费(元)', 'N/A'):>10} "
            f"{m.get('套利收益(元)', 'N/A'):>10} "
            f"{m.get('可控成本(元)', 'N/A'):>10} "
            f"{m.get('成本达成率', 'N/A'):>8}"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
