"""Registro de condados por estado."""

from __future__ import annotations

from core.county import CountyConfig

from . import florida

_BY_STATE: dict[str, dict[str, CountyConfig]] = {
    "FL": florida.COUNTIES,
}


def _key(name: str) -> str:
    return name.lower().replace("county", "").strip().replace(" ", "_")


def get_county(name: str, state: str = "FL") -> CountyConfig:
    counties = _BY_STATE.get(state.upper(), {})
    try:
        return counties[_key(name)]
    except KeyError:
        supported = ", ".join(sorted(counties)) or "ninguno"
        raise KeyError(f"Condado '{name}, {state}' no soportado (soportados: {supported})") from None
