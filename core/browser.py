"""Apertura del navegador.

Fase 1: Chrome visible en tu PC. Fase 4: aquí se cambia a
`chromium.connect_over_cdp(<url del navegador remoto>)` y nada más.

Perfil persistente: por defecto el navegador guarda su perfil en
.browser-profile/ y lo reutiliza entre corridas. Un perfil vacío en cada
corrida (sin cookies de Google) hace que reCAPTCHA ponga desafíos mucho más
difíciles. Es un perfil propio del proyecto, no el tuyo de Chrome.
BROWSER_PROFILE_DIR vacío en .env vuelve al perfil temporal.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import Page, sync_playwright

log = logging.getLogger(__name__)

CONTEXT_OPTIONS = dict(
    locale="en-US",
    timezone_id="America/New_York",
    viewport={"width": 1280, "height": 900},
)


@contextmanager
def open_browser(headless: bool = False) -> Iterator[Page]:
    channel = os.getenv("BROWSER_CHANNEL", "chrome").strip() or None
    profile_dir = os.getenv("BROWSER_PROFILE_DIR", ".browser-profile").strip()

    with sync_playwright() as p:
        if profile_dir:
            context = _launch_persistent(p, profile_dir, channel, headless)
            page = context.pages[0] if context.pages else context.new_page()
            browser = None
        else:
            browser = _launch(p, channel, headless)
            context = browser.new_context(**CONTEXT_OPTIONS)
            page = context.new_page()
        page.set_default_timeout(30_000)
        try:
            yield page
        finally:
            context.close()
            if browser:
                browser.close()


def _launch_persistent(p, profile_dir: str, channel, headless: bool):
    try:
        return p.chromium.launch_persistent_context(
            profile_dir, headless=headless, channel=channel, **CONTEXT_OPTIONS)
    except Exception as exc:
        if "already in use" in str(exc).lower() or "lock" in str(exc).lower():
            raise RuntimeError(
                f"El perfil {profile_dir} está en uso: cierra la otra ventana de Chrome del script") from exc
        log.warning("No se pudo abrir el canal %r (%s); uso Chromium de Playwright", channel, exc)
        return p.chromium.launch_persistent_context(profile_dir, headless=headless, **CONTEXT_OPTIONS)


def _launch(p, channel, headless: bool):
    try:
        return p.chromium.launch(headless=headless, channel=channel)
    except Exception as exc:  # Chrome no instalado u otra ruta
        log.warning("No se pudo abrir el canal %r (%s); uso Chromium de Playwright", channel, exc)
        return p.chromium.launch(headless=headless)
