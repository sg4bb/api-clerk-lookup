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


def supported_counties(state: str = "FL") -> list[str]:
    """Nombres legibles de los condados soportados ('Orange County')."""
    return sorted(c.name for c in _BY_STATE.get(state.upper(), {}).values())


def is_supported(county_key: str, state: str = "FL") -> bool:
    return _key(county_key) in _BY_STATE.get(state.upper(), {})
