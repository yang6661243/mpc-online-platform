from __future__ import annotations

from collections.abc import Iterable, Mapping
import math


REQUIRED_FIELDS = {
    "grid_meter": ("time", "grid_power_kw"),
    "battery": ("time", "battery_power_kw", "soc"),
}

OPTIONAL_FIELDS = {
    "grid_meter": (),
    "battery": ("battery_available", "pcs_available"),
}


def _as_finite_float(value, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _source_name(field: str, field_mapping: Mapping[str, str]) -> str:
    return field_mapping.get(field, field)


def _normalize_soc(value, soc_unit: str) -> float:
    unit = str(soc_unit or "ratio").strip().lower()
    soc = _as_finite_float(value, "soc")
    if unit in {"ratio", "fraction", "0-1"}:
        return soc
    if unit in {"percent", "pct", "0-100"}:
        return soc / 100.0
    raise ValueError("soc_unit must be 'ratio' or 'percent'")


def _normalize_numeric_power(value, field: str, power_signs: Mapping[str, float]) -> float:
    sign = power_signs.get(field, 1)
    sign_value = _as_finite_float(sign, f"power_signs.{field}")
    if sign_value not in {-1.0, 1.0}:
        raise ValueError(f"power_signs.{field} must be 1 or -1")
    return _as_finite_float(value, field) * sign_value


def normalize_input_records(
    data_type: str,
    records: Iterable[Mapping],
    *,
    field_mapping: Mapping[str, str] | None = None,
    power_signs: Mapping[str, float] | None = None,
    soc_unit: str = "ratio",
) -> list[dict]:
    mapping = field_mapping or {}
    signs = power_signs or {}
    if data_type not in REQUIRED_FIELDS:
        raise ValueError(f"unsupported data_type: {data_type}")

    normalized = []
    for record in records:
        out = {}
        for field in REQUIRED_FIELDS[data_type]:
            source = _source_name(field, mapping)
            if source not in record:
                raise ValueError(f"missing source field for {field}: {source}")
            raw = record[source]
            if field == "soc":
                out[field] = _normalize_soc(raw, soc_unit)
            elif field.endswith("_power_kw"):
                out[field] = _normalize_numeric_power(raw, field, signs)
            else:
                out[field] = raw

        for field in OPTIONAL_FIELDS[data_type]:
            source = _source_name(field, mapping)
            if source in record:
                out[field] = record[source]

        normalized.append(out)

    return normalized
