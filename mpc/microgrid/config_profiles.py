"""Helpers for YAML configs that contain named profiles.

Plain configs remain supported. Profiled configs use this shape:

profiles:
  profile_name:
    ...
active_profile: profile_name
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (override or {}).items():
        if (
            isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_profiled_config(config_path: str | Path, profile: str | None = None) -> dict[str, Any]:
    """Load a YAML config, optionally selecting one profile.

    If the file has no top-level ``profiles`` key, the config is returned as-is.
    If profiles are present, ``profile`` wins over ``active_profile``.
    A top-level ``defaults`` mapping is deep-merged into the selected profile.
    """
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    profiles = raw.get("profiles")
    if not profiles:
        if profile:
            raise ValueError(
                f"Config {config_path} has no profiles, but --profile={profile!r} was provided."
            )
        return raw

    selected = profile or raw.get("active_profile")
    if not selected:
        available = ", ".join(sorted(profiles))
        raise ValueError(
            f"Config {config_path} contains profiles but no active_profile. "
            f"Pass --profile. Available profiles: {available}"
        )
    if selected not in profiles:
        available = ", ".join(sorted(profiles))
        raise ValueError(
            f"Profile {selected!r} not found in {config_path}. "
            f"Available profiles: {available}"
        )

    cfg = _deep_merge(raw.get("defaults", {}) or {}, profiles[selected] or {})
    cfg["_profile"] = selected
    return cfg
