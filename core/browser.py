"""Apertura del navegador.

BROWSER_PROVIDER=local (por defecto): Chrome visible en tu PC.
BROWSER_PROVIDER=browserbase: navegador en la nube (Browserbase). Playwright
lo maneja a distancia y el CAPTCHA se resuelve desde su "live view", que es
lo que el dashboard mostrará en un iframe. Ver core/remote.py.

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
from dataclasses import dataclass
from typing import Iterator, Optional

from playwright.sync_api import Page, sync_playwright

log = logging.getLogger(__name__)

CONTEXT_OPTIONS = dict(
    locale="en-US",
    timezone_id="America/New_York",
    viewport={"width": 1280, "height": 900},
)


@dataclass
class BrowserSession:
    page: Page
    provider: str = "local"
    live_view_url: Optional[str] = None   # solo en navegadores remotos

    @property
    def remote(self) -> bool:
        return self.provider != "local"


def provider_name() -> str:
    return os.getenv("BROWSER_PROVIDER", "local").strip().lower() or "local"


@contextmanager
def open_browser(headless: bool = False) -> Iterator[BrowserSession]:
    if provider_name() == "browserbase":
        from .remote import open_browserbase
        with open_browserbase() as session:
            yield session
        return

    channel = os.getenv("BROWSER_CHANNEL", "chrome").strip() or None
    profile_dir = os.getenv("BROWSER_PROFILE_DIR", ".browser-profile").strip()
    # Solo para pruebas automáticas: sin ventana no hay quien resuelva el CAPTCHA.
    headless = headless or os.getenv("BROWSER_HEADLESS", "").strip() in ("1", "true", "yes")

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
            yield BrowserSession(page)
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
                f"The browser profile {profile_dir} is in use: close the script's other Chrome window") from exc
        log.warning("No se pudo abrir el canal %r (%s); uso Chromium de Playwright", channel, exc)
        return p.chromium.launch_persistent_context(profile_dir, headless=headless, **CONTEXT_OPTIONS)


def _launch(p, channel, headless: bool):
    try:
        return p.chromium.launch(headless=headless, channel=channel)
    except Exception as exc:  # Chrome no instalado u otra ruta
        log.warning("No se pudo abrir el canal %r (%s); uso Chromium de Playwright", channel, exc)
        return p.chromium.launch(headless=headless)
