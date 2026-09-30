"""Manejo del CAPTCHA.

El adaptador solo llama `captcha.solve(page)` y no sabe quién lo resuelve.
Eso permite cambiar de "ventana en mi PC" (Fase 1) a "iframe con live view
en el dashboard" (Fase 4) sin tocar ningún adaptador.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Protocol

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

# El textarea que reCAPTCHA v2 llena cuando el usuario lo resuelve.
RECAPTCHA_SOLVED_JS = """
() => {
  const t = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
  return !!(t && t.value && t.value.length > 0);
}
"""


class CaptchaTimeout(Exception):
    pass


class CaptchaHandler(Protocol):
    def solve(self, page: Page, timeout_s: int) -> None:
        """Bloquea hasta que el CAPTCHA esté resuelto o vence el tiempo."""
        ...


class ManualLocalCaptcha:
    """Fase 1: el navegador está visible en tu PC y lo resuelves tú."""

    def solve(self, page: Page, timeout_s: int) -> None:
        timeout_s = int(os.getenv("CAPTCHA_TIMEOUT_S", timeout_s))
        page.bring_to_front()
        print("\n" + "=" * 60)
        print("  CAPTCHA: resuélvelo en la ventana de Chrome que se abrió.")
        print("  Solo marca 'I'm not a robot'; el script pulsa Search solo.")
        print("  Si el desafío se traba, usa el ícono de recargar DENTRO del")
        print("  CAPTCHA. Si recargas la página, el script rellena el formulario.")
        print(f"  Tiempo máximo: {timeout_s // 60} minutos.")
        print("=" * 60 + "\n")
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CaptchaTimeout("Nadie resolvió el CAPTCHA a tiempo")
            try:
                page.wait_for_function(RECAPTCHA_SOLVED_JS, timeout=min(remaining, 15) * 1000, polling=500)
                break
            except PlaywrightTimeout:
                continue
            except Exception:
                # La página se recargó o navegó: el contexto JS anterior ya no existe.
                log.info("La página se recargó; sigo esperando el CAPTCHA")
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                except PlaywrightTimeout:
                    pass
        log.info("CAPTCHA resuelto, continuando")


class LiveViewCaptcha:
    """Fase 4 (pendiente): navegador remoto + iframe en el dashboard.

    Flujo previsto:
      1. Obtener el live view URL de la sesión remota (Browserbase, etc.).
      2. Actualizar el job en Supabase: status='awaiting_captcha',
         live_view_url=..., captcha_expires_at=now()+timeout.
      3. Esperar con el mismo RECAPTCHA_SOLVED_JS de arriba.
      4. Limpiar live_view_url del job y status='searching'.
    """

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def solve(self, page: Page, timeout_s: int) -> None:
        raise NotImplementedError("Se implementa en la Fase 4")
