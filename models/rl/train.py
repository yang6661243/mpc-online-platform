"""Training loop with periodic MILP benchmark evaluation."""
from __future__ import annotations
import os, sys, yaml, time

# Ensure project root on path
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

from models.rl.env_offline import OfflineEnv
from models.rl.agent import create_agent, run_episode
from models.benchmark.solver import BenchmarkConfig, solve_benchmark


def _build_benchmark_config(yc: dict) -> BenchmarkConfig:
    bt = yc.get('battery', {})
    gd = yc.get('grid', {})
    cs = yc.get('cost', {})
    s  = yc.get('solver', {})
    return BenchmarkConfig(
        battery_capacity_kwh=bt.get('capacity_kwh', 200.0),
        battery_charge_max_kw=bt.get('charge_max_kw', 100.0),
        battery_discharge_max_kw=bt.get('discharge_max_kw', 100.0),
        battery_soc_init=bt.get('soc_init', 0.5),
        battery_soc_min=bt.get('soc_min', 0.1),
        battery_soc_max=bt.get('soc_max', 0.95),
        battery_charge_eff=bt.get('charge_eff', 0.95),
        battery_discharge_eff=bt.get('discharge_eff', 0.95),
        grid_import_max_kw=gd.get('import_max_kw', 100.0),
        grid_export_max_kw=gd.get('export_max_kw', 100.0),
        transformer_capacity_kw=gd.get('transformer_capacity_kw', 200.0),
        anti_backflow=gd.get('anti_backflow', False),
        c_deg=cs.get('c_deg', 0.05),
        c_pv=cs.get('c_pv', 0.0),
        c_wind=cs.get('c_wind', 0.0),
        battery_unit_cost=cs.get('battery_unit_cost', 0.0),
        battery_annual_decay_rate=cs.get('battery_annual_decay_rate', 0.0),
        r_demand=cs.get('demand_rate', 0.0),
        r_capacity=cs.get('capacity_rate', 0.0),
        use_milp=s.get('use_milp', True),
    )


def evaluate_vs_milp(model, env, config_path: str, days: int) -> dict:
    """Run agent on a full episode and compare to MILP benchmark."""
    # Run agent
    env.reset()
    agent_cost = run_episode(model, env)

    # Run MILP benchmark on same data
    with open(config_path, 'r', encoding='utf-8') as f:
        yc = yaml.safe_load(f)
    cfg = _build_benchmark_config(yc)

    T = min(days * 96, len(env.pv_kw))
    result = solve_benchmark(
        env.pv_kw[:T], env.wind_kw[:T], env.load_kw[:T],
        env.buy_price[:T], env.sell_price[:T],
        config=cfg, verbose=False,
    )

    # Agent also bears capacity + demand charges (same as MILP comprehensive)
    D_days = T * cfg.dt_hours / 24.0
    agent_demand = cfg.r_demand * env.peak_grid_import * D_days / 30.0 if cfg.r_demand > 0 else 0.0
    agent_capacity = cfg.r_capacity * cfg.transformer_capacity_kw * D_days / 30.0 if cfg.r_capacity > 0 else 0.0
    agent_comprehensive = agent_cost + agent_demand + agent_capacity

    rate = 0.0
    if agent_comprehensive > 0 and result.comprehensive_cost > 0:
        rate = (result.comprehensive_cost / agent_comprehensive) * 100.0
    elif agent_comprehensive < 0 and result.comprehensive_cost < 0:
        rate = (result.comprehensive_cost / agent_comprehensive) * 100.0

    return {
        'agent_cost': agent_cost,
        'agent_comprehensive': agent_comprehensive,
        'benchmark_cost': result.comprehensive_cost,
        'achievement_rate': rate,
        'solver_time_s': result.solve_time_s,
        'local_consumption_rate': result.local_consumption_rate,
    }


def train(
    config_path: str,
    total_timesteps: int = 500000,
    eval_freq: int = 50000,
    target_rate: float = 90.0,
    model_dir: str = "outputs/models",
):
    """Train PPO agent to control battery dispatch."""
    env_eval = OfflineEnv(config_path, days=7)
    env_train = OfflineEnv(config_path, days=7)

    model = create_agent(env_train, tensorboard_log=None)

    best_rate = 0.0
    steps_done = 0

    print(f"{'='*60}")
    print(f"Training PPO agent | Total: {total_timesteps} steps | Eval every {eval_freq}")
    print(f"{'='*60}")

    while steps_done < total_timesteps:
        chunk = min(eval_freq, total_timesteps - steps_done)
        model.learn(total_timesteps=chunk, reset_num_timesteps=False)
        steps_done += chunk

        env_eval.reset()
        eval_result = evaluate_vs_milp(model, env_eval, config_path, days=7)

        rate = eval_result['achievement_rate']
        print(f"  [{steps_done:>7d} steps] agent={eval_result['agent_cost']:.1f} | "
              f"MILP={eval_result['benchmark_cost']:.1f} | rate={rate:.1f}%")

        if rate > best_rate:
            best_rate = rate
            os.makedirs(model_dir, exist_ok=True)
            save_path = os.path.join(model_dir, "battery_agent")
            model.save(save_path)
            print(f"  -> Best model saved: {save_path}.zip")

        if rate >= target_rate:
            print(f"  -> Target ({target_rate:.1f}%) reached. Stopping.")
            break

    print(f"\nTraining done. Best rate: {best_rate:.1f}%")
    return best_rate
