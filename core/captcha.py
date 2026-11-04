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

# El campo que Cloudflare Turnstile llena al aprobar (sola o con el clic de una persona).
TURNSTILE_SOLVED_JS = """
() => {
  const t = document.querySelector('[name="cf-turnstile-response"]');
  return !!(t && t.value && t.value.length > 0);
}
"""

# hCaptcha (Flagler, BenchmarkWeb): llena h-captcha-response al resolverse (también g-recaptcha-response).
HCAPTCHA_SOLVED_JS = """
() => {
  const t = document.querySelector('textarea[name="h-captcha-response"], [name="h-captcha-response"]');
  const f = document.querySelector('iframe[data-hcaptcha-response]');
  return !!((t && t.value && t.value.length > 0) || (f && f.getAttribute('data-hcaptcha-response')));
}
"""

SOLVED_JS = {"recaptcha": RECAPTCHA_SOLVED_JS, "turnstile": TURNSTILE_SOLVED_JS, "hcaptcha": HCAPTCHA_SOLVED_JS}

# hCaptcha en Chrome local: se centra la casilla (en la ventana remota se usa FOCUS_CAPTCHA_JS).
FOCUS_HCAPTCHA_JS = """
() => {
  const w = document.querySelector('iframe[src*="hcaptcha"][src*="checkbox"], .h-captcha, iframe[src*="hcaptcha"]');
  if (w) w.scrollIntoView({block: 'center', inline: 'center'});
  return !!w;
}
"""

# Respaldo para Turnstile: si no se puede tapar la página, al menos se centra la casilla.
FOCUS_TURNSTILE_JS = """
() => {
  const w = document.querySelector('#cfWidget, .cf-turnstile, iframe[src*="challenges.cloudflare.com"]');
  if (w) w.scrollIntoView({block: 'center', inline: 'center'});
  return !!w;
}
"""


# Mientras se espera el CAPTCHA en un navegador remoto:
#  - lleva la casilla arriba (el cuadro de imágenes se despliega hacia abajo),
#  - impide que el usuario desplace la página (rueda, teclado, táctil),
#  - tapa todo lo demás con cuatro paneles blancos alrededor de la casilla.
# Los paneles SIGUEN a la casilla: se recolocan varias veces por segundo, por
# si la página se reacomoda después de medirla. Si la casilla deja de verse,
# los paneles se retiran solos: nunca se deja al usuario frente a una pantalla
# en blanco. El cuadro de imágenes de reCAPTCHA (z-index 2000000000) queda por
# encima de los paneles.
FOCUS_CAPTCHA_JS = r"""
({message, kind}) => {
  const rectOf = (e) => { const r = e.getBoundingClientRect(); return {top: Math.round(r.top), left: Math.round(r.left), width: Math.round(r.width), height: Math.round(r.height)}; };
  const shown = (e) => { const c = getComputedStyle(e); return c.display !== 'none' && c.visibility !== 'hidden'; };
  const sized = (e) => { const r = e.getBoundingClientRect(); return r.width >= 200 && r.height >= 50 && r.height <= 200; };
  window.__clUnfocus?.();   // un enfoque anterior que haya quedado puesto
  let all, el;
  const narrowed = [];   // [nodo, estilo original]: se devuelven al terminar
  if (kind === 'turnstile') {
    // Cloudflare: la casilla mide ~300x65 dentro de #cfWidget (Civitek) o .cf-turnstile.
    const sels = ['iframe[src*="challenges.cloudflare.com"]', '#cfWidget > div', '.cf-turnstile > div', '#cfWidget', '.cf-turnstile'];
    all = [...new Set(sels.flatMap(s => [...document.querySelectorAll(s)]))];
    el = all.find(e => shown(e) && sized(e) && e.getBoundingClientRect().width <= 500);
    if (!el) {
      // El contenedor ocupa todo el ancho del formulario (Hendry: 1094 px) y la casilla
      // está adentro, en un shadow root que no se puede consultar. Se angosta el
      // contenedor al tamaño de la casilla mientras dura el CAPTCHA.
      const wide = all.find(e => e.tagName === 'DIV' && shown(e) && sized(e));
      if (wide) {
        for (const node of [wide, ...wide.querySelectorAll(':scope > div')]) {
          narrowed.push([node, node.style.cssText]);
          node.style.width = 'fit-content';
          node.style.marginLeft = 'auto';
          node.style.marginRight = 'auto';
        }
        const w = wide.getBoundingClientRect().width;
        if (w >= 200 && w <= 500) el = wide;
      }
    }
  } else if (kind === 'hcaptcha') {
    // hCaptcha (Flagler): la casilla es un iframe "#frame=checkbox" (~300x78); el cuadro de
    // imágenes es otro iframe ("#frame=challenge") que se abre aparte (ver raiseChallenge).
    all = [...document.querySelectorAll('iframe[src*="hcaptcha"], .h-captcha')];
    const ok = (e) => shown(e) && sized(e) && !(e.src || '').includes('challenge');
    const frames = all.filter(e => e.tagName === 'IFRAME' && ok(e));
    el = frames.find(f => f.src.includes('checkbox')) || frames[0];
  } else {
    all = [...document.querySelectorAll('iframe[src*="recaptcha"], .g-recaptcha')];
    const ok = (e) => shown(e) && sized(e) && !e.closest('.grecaptcha-badge') && !(e.src || '').includes('bframe');
    const frames = all.filter(e => e.tagName === 'IFRAME' && ok(e));
    el = frames.find(f => f.src.includes('anchor')) || frames[0] || all.find(e => e.tagName !== 'IFRAME' && ok(e));
  }
  const report = all.map(e => ({tag: e.tagName, kind: (e.src || '').match(/(anchor|bframe)/)?.[1] || e.id || e.className || '',
                                shown: shown(e), badge: !!e.closest('.grecaptcha-badge'), rect: rectOf(e)}));
  const info = {ok: false, kind, candidates: report, viewport: [innerWidth, innerHeight], scrollY: Math.round(scrollY),
                narrowed: narrowed.length > 0};
  const unnarrow = () => { for (const [node, css] of narrowed) node.style.cssText = css; };
  if (!el) { unnarrow(); return info; }   // sin casilla visible no se tapa nada

  // Cloudflare y hCaptcha (Flagler) suelen estar al final del formulario: un espacio
  // temporal abajo deja subir la casilla hasta arriba, como en Orange. Se quita al terminar.
  // Con la casilla arriba, el cuadro de imágenes de hCaptcha se abre debajo de ella.
  const padBefore = document.body.style.paddingBottom;
  if (kind === 'turnstile') document.body.style.paddingBottom = innerHeight + 'px';
  // Flagler anula el padding de body desde su CSS (quedaba en 0px, 03/11/2026): con hCaptcha
  // el espacio es un bloque vacío al final de la página.
  let spacer = null;
  if (kind === 'hcaptcha') {
    spacer = document.createElement('div');
    spacer.id = '__cl_spacer';
    spacer.style.cssText = 'display:block;height:' + innerHeight + 'px;width:1px;';
    document.body.appendChild(spacer);
  }
  el.style.scrollMarginTop = '70px';
  // Si se angostó, la casilla queda al medio de un formulario más ancho que la ventana.
  el.scrollIntoView({block: 'start', inline: narrowed.length ? 'center' : 'nearest'});
  // hCaptcha (Flagler): la página no se desplazó (la casilla siguió en top 424 con el espacio
  // de abajo puesto, 03/11/2026). Se prueba el desplazamiento del documento y, si tampoco
  // alcanza, se sube el contenido con un margen negativo en body. Los paneles son "fixed":
  // no se mueven con el margen. Se quita al terminar.
  const marginBefore = document.body.style.marginTop;
  const lift = {};
  if (kind === 'hcaptcha') {
    const extra = () => Math.round(el.getBoundingClientRect().top - 70);
    const scroller = document.scrollingElement || document.documentElement;
    let fixedParent = null;
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const pos = getComputedStyle(n).position;
      if (pos === 'fixed' || pos === 'sticky') { fixedParent = n.tagName + '#' + n.id + '.' + n.className; break; }
    }
    Object.assign(lift, {afterScrollIntoView: extra(), scrollHeight: scroller.scrollHeight,
                         clientHeight: scroller.clientHeight, scrollTop: scroller.scrollTop,
                         spacer: spacer ? spacer.getBoundingClientRect().height : null, fixedParent});
    if (extra() > 40) {
      scroller.scrollTop += extra();
      lift.afterScrollTop = extra();
    }
    if (extra() > 40) {
      const current = parseFloat(getComputedStyle(document.body).marginTop) || 0;
      document.body.style.setProperty('margin-top', (current - extra()) + 'px', 'important');
      lift.afterMargin = extra();
    }
  }

  // reCAPTCHA (Charlotte, 03/11/2026): la página es más ancha que la ventana (body min-width 990 px)
  // y la casilla quedaba lejos del borde izquierdo; el cuadro de imágenes (400 px) se abría a su
  // izquierda, cortado, aun después de desplazar la página. Se deja la página sin desplazamiento
  // horizontal y la casilla fija arriba a la izquierda, como queda en Orange (x ~80), donde el
  // cuadro se abre completo. En Orange la casilla ya está a la izquierda y esto no hace nada.
  const moved = [];   // [nodo, estilo original]
  if (kind !== 'turnstile' && kind !== 'hcaptcha' && el.getBoundingClientRect().left > innerWidth - 420) {
    lift.left = Math.round(el.getBoundingClientRect().left);
    const box = el.closest('.g-recaptcha') || el.parentElement || el;
    for (const node of [document.documentElement, document.body, box]) moved.push([node, node.style.cssText]);
    document.body.style.setProperty('min-width', '0', 'important');
    document.documentElement.style.setProperty('overflow-x', 'hidden', 'important');
    (document.scrollingElement || document.documentElement).scrollLeft = 0;
    const r = el.getBoundingClientRect();
    box.style.setProperty('position', 'fixed', 'important');
    // Fondo blanco con 10 px alrededor: tapa el hueco que dejan los paneles junto a la casilla, por
    // donde asomaba el logo del portal (Charlotte, 04/11/2026). La casilla queda en (10, 90).
    box.style.setProperty('left', '0', 'important');
    box.style.setProperty('top', '80px', 'important');
    box.style.setProperty('width', Math.round(r.width) + 'px', 'important');
    box.style.setProperty('margin', '0', 'important');
    box.style.setProperty('padding', '10px', 'important');
    box.style.setProperty('box-sizing', 'content-box', 'important');
    box.style.setProperty('background', '#fff', 'important');
    box.style.setProperty('z-index', '1999999995', 'important');
    lift.fixedAt = rectOf(el);
    lift.scrollWidth = (document.scrollingElement || document.documentElement).scrollWidth;
  }

  // Botones flotantes del portal (accesibilidad, etc.) que quedarían por encima.
  const hidden = [];
  const floating = [...document.querySelectorAll('[class*="userway" i], [id*="userway" i], .uwy, iframe[title*="accessib" i]')];
  for (const node of document.querySelectorAll('body *')) {
    const c = getComputedStyle(node);
    if (c.position === 'fixed' && parseInt(c.zIndex, 10) >= 1000000) floating.push(node);
  }
  for (const node of new Set(floating)) {
    if (node.contains(el) || node.querySelector?.('iframe[src*="recaptcha"], iframe[src*="hcaptcha"]')
        || /recaptcha|hcaptcha/.test(node.src || '')) continue;
    hidden.push([node, node.style.getPropertyValue('visibility'), node.style.getPropertyPriority('visibility')]);
    node.style.setProperty('visibility', 'hidden', 'important');
  }

  const mask = document.createElement('div');
  mask.id = '__cl_mask';
  const panels = [0, 1, 2, 3].map(() => {
    const d = document.createElement('div');
    d.style.cssText = 'position:fixed;background:#fff;z-index:1999999990;';
    mask.appendChild(d);
    return d;
  });
  const [head, foot, leftP, rightP] = panels;
  head.style.cssText += 'display:flex;align-items:flex-end;padding:0 0 10px 14px;box-sizing:border-box;'
                      + 'font:15px system-ui,sans-serif;color:#333;overflow:hidden;';
  head.textContent = message;
  document.body.appendChild(mask);

  const hideFloating = () => {
    for (const node of document.querySelectorAll('[class*="userway" i], [id*="userway" i], .uwy')) {
      if (node.style.getPropertyValue('visibility') === 'hidden') continue;
      hidden.push([node, node.style.getPropertyValue('visibility'), node.style.getPropertyPriority('visibility')]);
      node.style.setProperty('visibility', 'hidden', 'important');
    }
    for (const [node] of hidden) {
      if (node.style.getPropertyValue('visibility') !== 'hidden') node.style.setProperty('visibility', 'hidden', 'important');
    }
  };
  // Sin barras de desplazamiento a la vista mientras dura el CAPTCHA.
  const bars = document.createElement('style');
  bars.textContent = '*::-webkit-scrollbar{width:0!important;height:0!important}';
  document.head.appendChild(bars);

  // hCaptcha: el cuadro de imágenes debe quedar por encima de los paneles. Se sube el
  // contenedor del iframe "challenge" (el hijo directo de body) al máximo.
  const raiseChallenge = () => {
    if (kind !== 'hcaptcha') return;
    for (const f of document.querySelectorAll('iframe[src*="hcaptcha"][src*="challenge"]')) {
      let top = f;
      while (top.parentElement && top.parentElement !== document.body) top = top.parentElement;
      if (top.style.zIndex !== '2147483647') top.style.zIndex = '2147483647';
    }
  };

  let covered = false;
  const place = () => {
    hideFloating();
    raiseChallenge();
    if (covered) return true;
    // Turnstile: el hueco es la casilla justa, para que no asomen Search ni Reset debajo.
    const r = el.getBoundingClientRect(), pad = kind === 'turnstile' ? 0 : 10;
    const visible = r.width >= 200 && r.height >= 50 && r.top >= 0 && r.bottom <= innerHeight && r.left >= 0;
    mask.style.display = visible ? '' : 'none';   // si la casilla no se ve, no se tapa nada
    if (!visible) return false;
    const top = Math.max(0, r.top - pad), bottom = r.bottom + pad;
    const left = Math.max(0, r.left - pad), right = r.right + pad;
    head.style.left = '0'; head.style.top = '0'; head.style.width = '100vw'; head.style.height = top + 'px';
    foot.style.left = '0'; foot.style.top = bottom + 'px'; foot.style.width = '100vw'; foot.style.bottom = '0';
    leftP.style.left = '0'; leftP.style.top = top + 'px'; leftP.style.width = left + 'px'; leftP.style.height = (bottom - top) + 'px';
    rightP.style.left = right + 'px'; rightP.style.top = top + 'px'; rightP.style.right = '0'; rightP.style.height = (bottom - top) + 'px';
    return true;
  };
  const timer = setInterval(place, 200);

  // El usuario no puede desplazar la página (los scripts de la página sí).
  const stop = (e) => e.preventDefault();
  // Tab y Enter tampoco: con el teclado se podría llegar a Search y pulsarlo. El worker lo
  // pulsa solo cuando el CAPTCHA está resuelto. (La casilla está en su propio iframe: no se afecta.)
  const keys = (e) => { if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' ', 'Tab', 'Enter'].includes(e.key)) e.preventDefault(); };
  const opts = {capture: true, passive: false};
  window.addEventListener('wheel', stop, opts);
  window.addEventListener('touchmove', stop, opts);
  window.addEventListener('keydown', keys, opts);

  // Al resolverse: se tapa TODO un instante, para que el dashboard quite el
  // recuadro antes de que la página vuelva a verse.
  window.__clCover = (text) => {
    covered = true;
    mask.style.display = '';
    for (const d of [foot, leftP, rightP]) d.style.display = 'none';
    head.style.cssText = 'position:fixed;inset:0;background:#fff;z-index:2147483646;display:flex;align-items:center;'
                       + 'justify-content:center;font:16px system-ui,sans-serif;color:#333;';
    head.textContent = text;
  };
  window.__clUnfocus = () => {
    clearInterval(timer);
    bars.remove();
    delete window.__clCover;
    window.removeEventListener('wheel', stop, opts);
    window.removeEventListener('touchmove', stop, opts);
    window.removeEventListener('keydown', keys, opts);
    mask.remove();
    for (const [node, value, priority] of hidden) {
      if (value) node.style.setProperty('visibility', value, priority); else node.style.removeProperty('visibility');
    }
    el.style.scrollMarginTop = '';
    unnarrow();
    document.body.style.paddingBottom = padBefore;
    document.body.style.removeProperty('margin-top');
    if (marginBefore) document.body.style.marginTop = marginBefore;
    for (const [node, css] of moved) node.style.cssText = css;
    spacer?.remove();
    delete window.__clUnfocus;
  };
  info.ok = place();
  info.box = rectOf(el);
  info.lift = lift;
  info.hidden = hidden.length;
  return info;
}
"""

# El cuadro de imágenes mide 400 px y se abre a la derecha de la casilla
# (empieza cerca de x=80): con menos de ~500 px de ancho queda cortado.
FOCUS_VIEWPORT = {"width": 540, "height": 700}

# Segunda lectura, un momento después: ¿la casilla sigue a la vista y destapada?
CHECK_FOCUS_JS = r"""
() => {
  const mask = document.getElementById('__cl_mask');
  if (!mask) return {masked: false};
  const hole = mask.children[0].getBoundingClientRect();   // el panel de arriba termina donde empieza la casilla
  return {masked: mask.style.display !== 'none', captchaTop: Math.round(hole.height), scrollY: Math.round(scrollY)};
}
"""

UNFOCUS_CAPTCHA_JS = "() => { window.__clUnfocus?.(); }"


class CaptchaTimeout(Exception):
    pass


class CaptchaHandler(Protocol):
    def solve(self, page: Page, timeout_s: int, kind: str = "recaptcha") -> None:
        """Bloquea hasta que el CAPTCHA esté resuelto o vence el tiempo.

        kind: "recaptcha" (Orange), "turnstile" (Cloudflare, Civitek) o "hcaptcha" (Flagler).
        """
        ...


def passes_on_its_own(page: Page, kind: str, wait_s: float) -> bool:
    """True si el CAPTCHA se aprueba solo dentro de wait_s (Turnstile suele hacerlo)."""
    try:
        page.wait_for_function(SOLVED_JS[kind], timeout=wait_s * 1000, polling=500)
        return True
    except Exception:
        return False


def wait_until_solved(page: Page, timeout_s: int, solved_js: str = RECAPTCHA_SOLVED_JS) -> None:
    """Espera a que el CAPTCHA quede resuelto, tolerando recargas de la página."""
    deadline = time.monotonic() + timeout_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptchaTimeout("The CAPTCHA was not solved in time")
        try:
            page.wait_for_function(solved_js, timeout=min(remaining, 15) * 1000, polling=500)
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
    """El navegador está visible en tu PC y lo resuelves tú.

    `on_waiting(None, timeout_s)` y `on_done()` permiten que el worker marque
    el job como "esperando CAPTCHA" aunque la ventana esté en esta máquina.
    """

    def __init__(self, on_waiting=None, on_done=None) -> None:
        self.on_waiting = on_waiting
        self.on_done = on_done

    def solve(self, page: Page, timeout_s: int, kind: str = "recaptcha") -> None:
        timeout_s = int(os.getenv("CAPTCHA_TIMEOUT_S", timeout_s))
        if self.on_waiting:
            self.on_waiting(None, timeout_s)
        try:
            if kind == "turnstile":
                page.bring_to_front()
                page.evaluate(FOCUS_TURNSTILE_JS)
                print("\n  CAPTCHA: marca 'Verify you are human' en la ventana de Chrome que se abrió.\n")
                wait_until_solved(page, timeout_s, TURNSTILE_SOLVED_JS)
                return
            if kind == "hcaptcha":
                page.bring_to_front()
                page.evaluate(FOCUS_HCAPTCHA_JS)
                print("\n  CAPTCHA: marca 'I am human' en la ventana de Chrome y resuelve las imágenes que pida.")
                print("  No pulses Search: el script busca solo cuando el CAPTCHA queda resuelto.\n")
                wait_until_solved(page, timeout_s, HCAPTCHA_SOLVED_JS)
                return
            self._solve(page, timeout_s)
        finally:
            if self.on_done:
                self.on_done()

    def _solve(self, page: Page, timeout_s: int) -> None:
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
  iframe {{ display: block; width: 540px; height: 700px; max-width: 100vw; margin: 24px auto; border: 0;
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
  }}, 400);
</script>
</body></html>
"""


class LiveViewCaptcha:
    """Navegador remoto: el CAPTCHA se resuelve desde el live view.

    `on_waiting(url, timeout_s)` avisa que hay un CAPTCHA esperando (el worker
    escribe el enlace en la fila del job para que el dashboard muestre el iframe) y
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

    def solve(self, page: Page, timeout_s: int, kind: str = "recaptcha") -> None:
        # La sesión gratuita dura 15 minutos en total: el CAPTCHA no puede ocuparlos todos.
        timeout_s = min(int(os.getenv("CAPTCHA_TIMEOUT_S", timeout_s)), int(os.getenv("REMOTE_CAPTCHA_TIMEOUT_S", "480")))
        original = self._focus_on_captcha(page, kind)
        if self.on_waiting:
            self.on_waiting(self.embed_url, timeout_s)
        else:
            self._open_test_page(timeout_s)
        try:
            wait_until_solved(page, timeout_s, SOLVED_JS.get(kind, RECAPTCHA_SOLVED_JS))
        finally:
            # Orden: tapar todo -> avisar que ya no hace falta el recuadro ->
            # dar un momento para que desaparezca -> destapar y seguir.
            try:
                page.evaluate("(t) => window.__clCover?.(t)", "Done. Continuing…")
            except Exception:
                pass
            if self.on_done:
                self.on_done()
            elif self._status_file:
                self._status_file.write_text("window.__clStatus = 'done';", encoding="utf-8")
            if original:
                try:
                    page.wait_for_timeout(int(os.getenv("REMOTE_CAPTCHA_HIDE_MS", "1200")))
                    # Hay que destapar la página antes de que el script pulse Search.
                    page.evaluate(UNFOCUS_CAPTCHA_JS)
                    page.set_viewport_size(original)
                except Exception as exc:
                    log.warning("No pude restaurar la ventana remota: %s", exc)

    def _focus_on_captcha(self, page: Page, kind: str = "recaptcha"):
        """Achica la ventana remota y deja a la vista solo la casilla mientras se espera.

        Sirve para reCAPTCHA (Orange), Cloudflare Turnstile (Civitek) y hCaptcha (Flagler). Impide el
        desplazamiento y tapa el resto del portal, así el usuario no puede moverse
        por el sitio ni pulsar nada más. Al resolverse se restaura todo.
        Guarda un diagnóstico (captura y datos) en la carpeta debug.
        REMOTE_CAPTCHA_FOCUS=0 lo desactiva.
        """
        if os.getenv("REMOTE_CAPTCHA_FOCUS", "1").strip() in ("0", "false", "no"):
            return None
        original = page.viewport_size or {"width": 1280, "height": 900}
        try:
            page.set_viewport_size(FOCUS_VIEWPORT)
            page.wait_for_timeout(800)          # la página se reacomoda al cambiar de tamaño
            info = page.evaluate(FOCUS_CAPTCHA_JS, {"message": "Tick the box to continue:", "kind": kind})
            page.wait_for_timeout(700)
            info["after"] = page.evaluate(CHECK_FOCUS_JS)
            if info.get("ok") and info["after"].get("masked"):
                log.info("CAPTCHA enfocado en la ventana remota: casilla en %s", info.get("box"))
            else:
                log.warning("No pude dejar a la vista solo el CAPTCHA; se muestra la página completa")
                if kind == "turnstile":
                    page.evaluate(FOCUS_TURNSTILE_JS)       # al menos la casilla al centro
            self._save_focus_debug(page, info)
            return original
        except Exception as exc:
            log.warning("No pude enfocar el CAPTCHA en la ventana remota (sigo igual): %s", exc)
            return original

    def _save_focus_debug(self, page: Page, info: dict) -> None:
        import json
        from pathlib import Path

        try:
            folder = Path(self.output_dir or os.getenv("OUTPUT_DIR", "output")) / "debug"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "captcha_focus.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
            page.screenshot(path=str(folder / "captcha_focus.png"))
            log.info("Diagnóstico del enfoque guardado en %s", folder)
        except Exception as exc:
            log.info("No pude guardar el diagnóstico del enfoque: %s", exc)

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
