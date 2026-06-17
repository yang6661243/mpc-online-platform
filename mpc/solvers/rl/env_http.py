"""Gym-style MicrogridEnv for single-instance RL training."""
from .client import RLClient


class MicrogridEnv:
    def __init__(self, base_url: str = "http://localhost:8000",
                 instance_id: str = None, steps_per_action: int = 900):
        self.client = RLClient(base_url, instance_id or "")
        self.steps_per_action = steps_per_action

    def reset(self, run_mode: str = "grid_connected") -> dict:
        result = self.client.reset(run_mode=run_mode)
        return result["observation"]

    def step(self, action: dict):
        result = self.client.step(actions=action, steps=self.steps_per_action)
        return (result["observation"], result["reward"], result["done"],
                {"done_reason": result.get("done_reason"),
                 "step_count": result["step_count"]})

    def set_reward_weights(self, weights: dict):
        return self.client.set_reward_config(weights=weights)

    def connect(self, instance_id: str):
        self.instance_id = instance_id
        self.client.iid = instance_id
