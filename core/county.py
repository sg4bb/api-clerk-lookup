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
    # Documento de apoyo que se descarga además del principal, si existe: la
    # orden de captura ejecutada (trae el número de reporte de la agencia que
    # investigó el hecho). Mismas exclusiones que arriba.
    support_documents: list[str] = field(default_factory=list)
    captcha: str = "none"             # "recaptcha_v2" | "turnstile" | "hcaptcha" | "text" | "none"
    # El CAPTCHA se resuelve ANTES de la primera búsqueda de la sesión, nunca después de un
    # "Invalid Captcha". Clay (08/11/2026): Cloudflare bloquea la búsqueda enviada sin él.
    captcha_before_search: bool = False
    # El CAPTCHA se pide en CADA búsqueda, no solo en la primera (St. Lucie, 09/11/2026).
    captcha_every_search: bool = False
    # El docket marca los documentos que no están en línea con un botón "Request" (se piden al
    # Clerk por email). Si el documento buscado está así, se dice al usuario cómo pedirlo.
    documents_on_request: bool = False
    # Texto propio del condado para decir cómo conseguir ese documento, por motivo del docket
    # (DocketEntry.availability: "request", "unpublished"). Admite {document}, {case_number} y
    # {portal}. Sin texto para el motivo, se usa el general (el botón "Request" de Benchmark).
    # Volusia (11/11/2026): su ícono es "Viewable on Request" y hay renglones aún sin publicar.
    request_messages: dict[str, str] = field(default_factory=dict)
    # Cloudflare pone su verificación ("Just a moment...") antes del portal y, recién pasada, niega
    # los scripts y la imagen de la suma (403) hasta recargar. Escambia (10/11/2026). Se espera la
    # verificación (si no pasa sola, la marca la persona) y se recarga sin caché hasta que la
    # página carga completa. Nunca se salta: solo se espera y se recarga.
    cloudflare_gate: bool = False
    # Si Cloudflare bloquea el portal, otra fuente pública de donde sacar el arresto y su número de
    # reporte (sin el documento): "escambia_jail" (registro de la cárcel de Escambia, 10/11/2026).
    blocked_fallback: str | None = None
    # Tiempo máximo por documento descargado en el navegador remoto (todos los condados). Antes se
    # esperaba lo que tardara el portal: Escambia dejó colgada una descarga y con ella la búsqueda
    # entera (Rodriguez, 10/11/2026; el PDF bueno tardó 2 min 15 s). En local, Playwright ya corta a 30 s.
    download_timeout_s: int = 240
    captcha_timeout_s: int = 600     # CAPTCHA_TIMEOUT_S en .env lo cambia
    throttle_s: float = 2.0           # pausa entre requests al portal
    # Ventana de búsqueda alrededor de la fecha del incidente/arresto.
    # El caso puede abrirse ANTES del arresto (orden de arresto), por eso
    # la ventana arranca unos días antes del incidente.
    window_days_before: int = 7
    # 60: Porter (Washington) se arrestó con orden y el caso se abrió 54 días después del hecho.
    window_days_after: int = 60
    max_candidates_to_open: int = 5
    # Personas del artículo que se prueban, una tras otra, si la primera no da
    # con el caso. Cada una es otra búsqueda (y en algunos portales otro CAPTCHA).
    max_people: int = 3
