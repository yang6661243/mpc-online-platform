"""MultiEnv for parallel RL training across many instances."""
from .client import RLClient


class MultiEnv:
    def __init__(self, base_url: str = "http://localhost:8000", count: int = 4,
                 template_id: str = None, steps_per_action: int = 900):
        self.client = RLClient(base_url, "")
        self.ids = self.client.create_instances(
            count=count, template_id=template_id)
        self.steps_per_action = steps_per_action

    def reset(self, run_mode: str = "grid_connected") -> list:
        self.client.batch_reset(self.ids, {"run_mode": run_mode})
        obs = []
        for iid in self.ids:
            c = RLClient(self.client.base, iid)
            try:
                obs.append(c.step(actions={}, steps=0)["observation"])
            except Exception:
                obs.append({})
        return obs

    def step(self, actions_list: list[dict]):
        steps_map = {}
        for iid, action in zip(self.ids, actions_list):
            steps_map[iid] = {"actions": action, "steps": self.steps_per_action}
        results = self.client.batch_step(steps_map)
        obs, rewards, dones, infos = [], [], [], []
        for iid in self.ids:
            r = results.get(iid, {})
            obs.append(r.get("observation"))
            rewards.append(r.get("reward", 0.0))
            dones.append(r.get("done", False))
            infos.append({"done_reason": r.get("done_reason")})
        return obs, rewards, dones, infos

    def __len__(self):
        return len(self.ids)
