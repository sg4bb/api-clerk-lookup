"""Configuración por condado.

Condados que usan la misma plataforma comparten adaptador (código) y solo
cambian esta configuración (datos).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CountyConfig:
    key: str                          # "orange"
    name: str                         # "Orange County"
    state: str                        # "FL"
    platform: str                     # adaptador a usar: "orange_myeclerk"
    base_url: str
    case_type_codes: dict[str, str]   # {"CF": "74", ...}
    # Regex (en minúsculas) en orden de preferencia para elegir el documento.
    document_priority: list[str]
    # Regex de documentos que nunca sirven aunque coincidan con los de arriba.
    document_exclude: list[str] = field(default_factory=list)
    captcha: str = "none"             # "recaptcha_v2" | "none"
    captcha_timeout_s: int = 600     # CAPTCHA_TIMEOUT_S en .env lo cambia
    throttle_s: float = 2.0           # pausa entre requests al portal
    # Ventana de búsqueda alrededor de la fecha del incidente/arresto.
    # El caso puede abrirse ANTES del arresto (orden de arresto), por eso
    # la ventana arranca unos días antes del incidente.
    window_days_before: int = 7
    window_days_after: int = 45
    max_candidates_to_open: int = 5
