"""PPO agent factory and evaluation helpers."""
from __future__ import annotations
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
import numpy as np


def create_agent(
    env,
    learning_rate: float = 3e-4,
    n_steps: int = 2048,
    batch_size: int = 64,
    gamma: float = 0.99,
    device: str = "auto",
    tensorboard_log: str | None = None,
) -> PPO:
    """Create a PPO agent wrapped around the given environment."""
    if not hasattr(env, 'num_envs'):
        env = DummyVecEnv([lambda: env])

    return PPO(
        "MlpPolicy",
        env,
        learning_rate=learning_rate,
        n_steps=n_steps,
        batch_size=batch_size,
        gamma=gamma,
        device=device,
        tensorboard_log=tensorboard_log,
        verbose=1,
    )


def load_agent(path: str, env=None) -> PPO:
    """Load a saved PPO agent."""
    return PPO.load(path, env=env)


def run_episode(model: PPO, env, deterministic: bool = True) -> float:
    """Run one full episode and return total cost."""
    obs, _ = env.reset()
    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=deterministic)
        obs, _, terminated, truncated, _ = env.step(action)
        done = terminated or truncated
    return env.total_cost
