"""HTTP client wrapping /rl and /batch endpoints."""
import requests
from typing import Optional


class RLClient:
    def __init__(self, base_url: str, instance_id: str = ""):
        self.base = base_url.rstrip("/")
        self.iid = instance_id

    def step(self, actions: dict = None, steps: int = 1) -> dict:
        r = requests.post(
            f"{self.base}/api/simulation/instances/{self.iid}/rl/step",
            json={"actions": actions or {}, "steps": steps})
        r.raise_for_status()
        return r.json()

    def reset(self, run_mode: str = None, initial_devices: dict = None) -> dict:
        r = requests.post(
            f"{self.base}/api/simulation/instances/{self.iid}/rl/reset",
            json={"run_mode": run_mode, "initial_devices": initial_devices})
        r.raise_for_status()
        return r.json()

    def set_reward_config(self, weights: dict = None, soc_target: list = None) -> dict:
        r = requests.post(
            f"{self.base}/api/simulation/instances/{self.iid}/rl/reward-config",
            json={"type": "custom", "weights": weights or {},
                  "soc_target": soc_target or [0.2, 0.8]})
        r.raise_for_status()
        return r.json()

    def create_instances(self, count: int, template_id: str = None,
                         name_prefix: str = "rl-worker",
                         run_mode: str = "grid_connected") -> list:
        r = requests.post(f"{self.base}/api/simulation/batch/create",
                          json={"count": count, "template_id": template_id,
                                "name_prefix": name_prefix, "run_mode": run_mode})
        r.raise_for_status()
        return r.json()["instance_ids"]

    def batch_step(self, steps_map: dict) -> dict:
        r = requests.post(f"{self.base}/api/simulation/batch/rl/step",
                          json={"steps_per_instance": steps_map})
        r.raise_for_status()
        return r.json()

    def batch_reset(self, instance_ids: list, config: dict = None) -> dict:
        r = requests.post(f"{self.base}/api/simulation/batch/rl/reset",
                          json={"instance_ids": instance_ids, "config": config or {}})
        r.raise_for_status()
        return r.json()
