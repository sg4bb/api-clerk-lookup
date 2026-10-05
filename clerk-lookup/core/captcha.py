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


# Mientras se espera el CAPTCHA en un navegador remoto:
#  - lleva el CAPTCHA arriba (el cuadro de imágenes se despliega hacia abajo),
#  - bloquea el desplazamiento de la página,
#  - tapa todo lo demás con cuatro paneles blancos alrededor de la casilla.
# El usuario solo puede ver y tocar el CAPTCHA: no puede pulsar Search ni otros
# enlaces del portal. El cuadro de imágenes de reCAPTCHA usa un z-index mucho
# más alto, así que se muestra por encima de los paneles.
FOCUS_CAPTCHA_JS = """
(message) => {
  // La página tiene varios elementos de reCAPTCHA (la casilla, el cuadro de
  // imágenes oculto, una insignia flotante...). Solo sirve la casilla VISIBLE.
  const size = (e) => { const r = e.getBoundingClientRect(); return r.width >= 200 && r.height >= 50 && r.height <= 200; };
  const shown = (e) => { const c = getComputedStyle(e); return c.display !== 'none' && c.visibility !== 'hidden'; };
  const frames = [...document.querySelectorAll('iframe[src*="recaptcha"]')]
    .filter(f => !f.src.includes('bframe') && !f.closest('.grecaptcha-badge') && shown(f) && size(f));
  const boxes = [...document.querySelectorAll('.g-recaptcha')].filter(e => shown(e) && size(e));
  const el = frames.find(f => f.src.includes('anchor')) || frames[0] || boxes[0];
  if (!el) return false;   // sin casilla visible no se tapa nada

  document.getElementById('__cl_mask')?.remove();
  window.scrollTo(0, Math.max(0, el.getBoundingClientRect().top + window.scrollY - 70));
  for (const node of [document.documentElement, document.body]) {
    node.dataset.clOverflow = node.style.overflow || '';
    node.style.setProperty('overflow', 'hidden', 'important');
  }
  // Botones flotantes del portal (accesibilidad, etc.) que quedarían por encima.
  for (const node of document.querySelectorAll('body *')) {
    const c = getComputedStyle(node);
    if (c.position !== 'fixed' || !(parseInt(c.zIndex, 10) >= 1000000)) continue;
    if (node.querySelector('iframe[src*="recaptcha"]') || (node.tagName === 'IFRAME' && node.src.includes('recaptcha'))) continue;
    node.dataset.clHidden = node.style.visibility || '';
    node.style.setProperty('visibility', 'hidden', 'important');
  }
  const r = el.getBoundingClientRect(), pad = 10;
  const top = Math.max(0, r.top - pad), bottom = r.bottom + pad;
  const left = Math.max(0, r.left - pad), right = r.right + pad;
  const mask = document.createElement('div');
  mask.id = '__cl_mask';
  const panel = (css) => {
    const d = document.createElement('div');
    // Por debajo del cuadro de imágenes de reCAPTCHA (z-index 2000000000).
    d.style.cssText = 'position:fixed;background:#fff;z-index:1999999990;' + css;
    mask.appendChild(d);
    return d;
  };
  const head = panel(`left:0;top:0;width:100vw;height:${top}px;`);
  panel(`left:0;top:${bottom}px;width:100vw;bottom:0;`);
  panel(`left:0;top:${top}px;width:${left}px;height:${bottom - top}px;`);
  panel(`left:${right}px;top:${top}px;right:0;height:${bottom - top}px;`);
  head.style.cssText += 'display:flex;align-items:flex-end;padding:0 0 10px 14px;box-sizing:border-box;'
                      + 'font:15px system-ui,sans-serif;color:#333;';
  head.textContent = message;
  document.body.appendChild(mask);
  return {top: Math.round(r.top), left: Math.round(r.left), width: Math.round(r.width), height: Math.round(r.height)};
}
"""

UNFOCUS_CAPTCHA_JS = """
() => {
  document.getElementById('__cl_mask')?.remove();
  for (const node of [document.documentElement, document.body]) {
    if (node.dataset.clOverflow !== undefined) {
      node.style.overflow = node.dataset.clOverflow;
      delete node.dataset.clOverflow;
    }
  }
  for (const node of document.querySelectorAll('[data-cl-hidden]')) {
    node.style.visibility = node.dataset.clHidden;
    delete node.dataset.clHidden;
  }
}
"""


class CaptchaTimeout(Exception):
    pass


class CaptchaHandler(Protocol):
    def solve(self, page: Page, timeout_s: int) -> None:
        """Bloquea hasta que el CAPTCHA esté resuelto o vence el tiempo."""
        ...


def wait_until_solved(page: Page, timeout_s: int) -> None:
    """Espera a que reCAPTCHA quede resuelto, tolerando recargas de la página."""
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


class ManualLocalCaptcha:
    """El navegador está visible en tu PC y lo resuelves tú."""

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
        wait_until_solved(page, timeout_s)


LIVE_VIEW_PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>clerk-lookup: CAPTCHA</title>
<style>
  body {{ margin: 0; font-family: system-ui, sans-serif; background: #111; color: #eee; }}
  header {{ padding: 12px 16px; font-size: 15px; }}
  header b {{ color: #ffd166; }}
  iframe {{ display: block; width: 440px; height: 680px; max-width: 100vw; margin: 24px auto; border: 0;
            border-radius: 8px; background: #fff; }}
</style></head>
<body>
<header id="msg">Simulación del dashboard: marca <b>I'm not a robot</b> dentro del recuadro. El programa sigue solo.</header>
<iframe id="view" src="{url}" sandbox="allow-same-origin allow-scripts" allow="clipboard-read; clipboard-write"></iframe>
<script>
  // El dashboard quitará el recuadro cuando el job cambie de estado. Aquí se
  // simula leyendo un archivo que el programa actualiza al resolverse el CAPTCHA.
  const timer = setInterval(() => {{
    const s = document.createElement('script');
    s.src = 'live_view_status.js?t=' + Date.now();
    s.onload = () => {{
      s.remove();
      if (window.__clStatus === 'done') {{
        clearInterval(timer);
        document.getElementById('view').remove();
        document.getElementById('msg').innerHTML = '<b>CAPTCHA resuelto.</b> El programa continúa solo; puedes cerrar esta pestaña.';
      }}
    }};
    s.onerror = () => s.remove();
    document.head.appendChild(s);
  }}, 1500);
</script>
</body></html>
"""


class LiveViewCaptcha:
    """Navegador remoto: el CAPTCHA se resuelve desde el live view.

    `on_waiting(url)` avisa que hay un CAPTCHA esperando (en la Fase 3 escribirá
    el enlace en la fila del job para que el dashboard muestre el iframe) y
    `on_done()` avisa que ya no hace falta. Sin `on_waiting`, modo de prueba:
    se crea una página local con el iframe, igual a como lo mostrará el
    dashboard, y se abre en tu navegador.
    """

    def __init__(self, live_view_url: str, on_waiting=None, on_done=None, output_dir=None) -> None:
        self.live_view_url = live_view_url
        self.on_waiting = on_waiting
        self.on_done = on_done
        self.output_dir = output_dir
        self._status_file = None

    @property
    def embed_url(self) -> str:
        sep = "&" if "?" in self.live_view_url else "?"
        return f"{self.live_view_url}{sep}navbar=false"

    def solve(self, page: Page, timeout_s: int) -> None:
        # La sesión gratuita dura 15 minutos en total: el CAPTCHA no puede ocuparlos todos.
        timeout_s = min(int(os.getenv("CAPTCHA_TIMEOUT_S", timeout_s)), int(os.getenv("REMOTE_CAPTCHA_TIMEOUT_S", "480")))
        original = self._focus_on_captcha(page)
        if self.on_waiting:
            self.on_waiting(self.embed_url)
        else:
            self._open_test_page(timeout_s)
        try:
            wait_until_solved(page, timeout_s)
        finally:
            if self.on_done:
                self.on_done()
            elif self._status_file:
                self._status_file.write_text("window.__clStatus = 'done';", encoding="utf-8")
            if original:
                # Hay que destapar la página antes de que el script pulse Search.
                try:
                    page.evaluate(UNFOCUS_CAPTCHA_JS)
                    page.set_viewport_size(original)
                except Exception as exc:
                    log.warning("No pude restaurar la ventana remota: %s", exc)

    def _focus_on_captcha(self, page: Page):
        """Achica la ventana remota y deja a la vista solo el CAPTCHA mientras se espera.

        Bloquea el desplazamiento y tapa el resto del portal, así el usuario no
        puede moverse por el sitio ni pulsar nada más. Al resolverse se restaura todo.
        REMOTE_CAPTCHA_FOCUS=0 lo desactiva.
        """
        if os.getenv("REMOTE_CAPTCHA_FOCUS", "1").strip() in ("0", "false", "no"):
            return None
        original = page.viewport_size or {"width": 1280, "height": 900}
        try:
            page.set_viewport_size({"width": 440, "height": 680})
            box = page.evaluate(FOCUS_CAPTCHA_JS, "Marca la casilla para continuar:")
            if box:
                log.info("CAPTCHA enfocado en la ventana remota: %s", box)
            else:
                log.warning("No encontré la casilla visible del CAPTCHA; se muestra la página completa")
            return original
        except Exception as exc:
            log.warning("No pude enfocar el CAPTCHA en la ventana remota (sigo igual): %s", exc)
            return None

    def _open_test_page(self, timeout_s: int) -> None:
        import webbrowser
        from pathlib import Path

        folder = Path(self.output_dir or os.getenv("OUTPUT_DIR", "output"))
        folder.mkdir(parents=True, exist_ok=True)
        html = folder / "live_view.html"
        self._status_file = folder / "live_view_status.js"
        self._status_file.write_text("window.__clStatus = 'waiting';", encoding="utf-8")
        html.write_text(LIVE_VIEW_PAGE.format(url=self.embed_url), encoding="utf-8")
        print("\n" + "=" * 60)
        print("  CAPTCHA (navegador remoto): se abrió una pestaña en tu navegador")
        print("  con el recuadro, igual a como lo mostrará el dashboard.")
        print("  Marca 'I'm not a robot' dentro del recuadro; el script sigue solo.")
        print(f"  Si no se abrió, abre este archivo: {html.resolve()}")
        print(f"  Enlace directo del live view: {self.live_view_url}")
        print(f"  Tiempo máximo: {timeout_s // 60} minutos.")
        print("=" * 60 + "\n")
        try:
            webbrowser.open(html.resolve().as_uri())
        except Exception as exc:
            log.warning("No pude abrir el navegador automáticamente: %s", exc)
