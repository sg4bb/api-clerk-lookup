"""Exploración de Civitek OCRS (Marion y otros 31 condados de Florida).

No es parte del worker: recorre el portal una vez, como lo haría el
adaptador, y guarda lo que ve (HTML, capturas, formularios, enlaces y
tráfico) para construir el adaptador con la estructura real del portal.

    python probe_civitek.py                          # Marion (42), Hogg Amy
    python probe_civitek.py --last Hogg --first Amy --county 42
    python probe_civitek.py --local                  # Chrome de tu PC en vez de Browserbase

Cloudflare suele aprobar solo. Si a los 15 segundos no lo hizo, se abre el
navegador en una pestaña: marca ahí la casilla "Verify you are human". Solo
esa casilla: el script hace el resto.

Hace dos búsquedas: la principal (Hogg, Amy, con filtros de tipo y fecha) y
una con un nombre común (Smith, John) para ver cómo se listan varias
personas y varios casos.

Todo queda en output/probe_civitek/<fecha>/ (output/ no se sube a git:
tiene datos de expedientes). Comprime esa carpeta y envíala.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from dotenv import load_dotenv

BASE = "https://www.civitekflorida.com/ocrs/county/{code}/"

# Todo lo que hay en la página que sirve para escribir el adaptador.
INVENTORY_JS = r"""
() => {
  const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const text = (el, n) => ((el.innerText || el.value || '') + '').trim().replace(/\s+/g, ' ').slice(0, n);
  const labelOf = el => {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return text(l, 80);
    }
    const p = el.closest('label, td, th, div');
    return p ? text(p, 80) : '';
  };
  const pick = (sel, f) => Array.from(document.querySelectorAll(sel)).slice(0, 400).map(f);
  const response = document.querySelector('[name="cf-turnstile-response"]');
  return {
    url: location.href,
    title: document.title,
    forms: pick('form', f => ({id: f.id, name: f.getAttribute('name'), action: f.getAttribute('action'), method: f.method})),
    fields: pick('input, select, textarea', e => ({
      tag: e.tagName.toLowerCase(), type: e.type, id: e.id, name: e.name, label: labelOf(e), visible: vis(e),
      options: e.tagName === 'SELECT' ? Array.from(e.options).map(o => [o.value, o.text]) : undefined,
    })),
    buttons: pick('button, input[type=submit], input[type=button], [role=button]', e => ({
      tag: e.tagName.toLowerCase(), id: e.id, name: e.getAttribute('name'), text: text(e, 60), visible: vis(e),
      onclick: (e.getAttribute('onclick') || '').slice(0, 300),
    })),
    links: pick('a', a => ({
      id: a.id, text: text(a, 80), href: a.getAttribute('href'), target: a.target, visible: vis(a),
      onclick: (a.getAttribute('onclick') || '').slice(0, 300),
    })),
    images: pick('img', i => ({
      src: i.getAttribute('src'), alt: i.alt, title: i.title,
      parent: i.parentElement ? i.parentElement.outerHTML.slice(0, 400) : '',
    })),
    iframes: pick('iframe', f => ({src: f.getAttribute('src'), title: f.title, visible: vis(f)})),
    turnstile: {
      widgets: pick('.cf-turnstile, [data-sitekey]', w => ({cls: w.className, sitekey: w.getAttribute('data-sitekey')})),
      responseField: !!response,
      solved: !!(response && response.value),
    },
  };
}
"""

# Encuentra el campo de texto de una etiqueta ("Last Name") y lo marca.
# Primero label[for]; si no, el campo visible más cercano debajo o a la derecha
# de la etiqueta (en Civitek las etiquetas pueden ir en una fila y los campos
# en la siguiente).
FIELD_AFTER_LABEL_JS = r"""
(label) => {
  const want = label.toLowerCase();
  const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const labels = Array.from(document.querySelectorAll('label, td, th, span, div'))
    .filter(e => e.children.length <= 3 && vis(e) && (e.innerText || '').trim().toLowerCase().startsWith(want));
  const inputs = Array.from(document.querySelectorAll('input[type=text], input:not([type])'))
    .filter(i => vis(i) && !i.hasAttribute('data-probe'));
  const mark = input => {
    input.setAttribute('data-probe', label);
    return {selector: '[data-probe="' + label + '"]', id: input.id, name: input.name};
  };
  for (const el of labels) {
    const forId = el.getAttribute('for');
    const input = forId ? document.getElementById(forId) : null;
    if (input) return mark(input);
  }
  let best = null, bestScore = Infinity;
  for (const el of labels) {
    const l = el.getBoundingClientRect();
    for (const input of inputs) {
      const r = input.getBoundingClientRect();
      if (r.top < l.top - 5) continue;            // tiene que estar a la altura o debajo
      const score = Math.abs(r.left - l.left) + (r.top - l.top);
      if (score < bestScore) { bestScore = score; best = input; }
    }
  }
  return best ? mark(best) : null;
}
"""

UCN_RE = re.compile(r"\d{6}[A-Z]{2}\d{6}")


class Probe:
    def __init__(self, page, folder: Path):
        self.page = page
        self.folder = folder
        self.count = 0
        self.steps: list[dict] = []
        self.network: list[dict] = []
        self.notes: dict = {}
        self.errors: list[str] = []
        page.context.on("response", self._on_response)

    def _on_response(self, response) -> None:
        try:
            req = response.request
            if req.resource_type in ("image", "font", "stylesheet", "media"):
                return
            ctype = response.headers.get("content-type", "")
            entry = {"t": round(time.time(), 1), "method": req.method, "type": req.resource_type,
                     "status": response.status, "content_type": ctype[:60], "url": response.url[:300]}
            self.network.append(entry)
            if "pdf" in ctype.lower():
                body = response.body()
                path = self.folder / f"capturado_{len(list(self.folder.glob('capturado_*.pdf'))) + 1}.pdf"
                path.write_bytes(body)
                entry["saved_as"] = path.name
                print(f"  PDF capturado: {path.name} ({len(body) // 1024} KB)")
        except Exception as exc:  # el registro nunca debe frenar la exploración
            self.errors.append(f"response: {exc}")

    def save(self, label: str, page=None) -> dict:
        page = page or self.page
        self.count += 1
        stem = f"{self.count:02d}_{label}"
        inventory: dict = {}
        try:
            (self.folder / f"{stem}.html").write_text(page.content(), encoding="utf-8")
        except Exception as exc:
            self.errors.append(f"{stem} html: {exc}")
        try:
            page.screenshot(path=str(self.folder / f"{stem}.png"), full_page=True)
        except Exception as exc:
            self.errors.append(f"{stem} captura: {exc}")
        try:
            inventory = page.evaluate(INVENTORY_JS)
            (self.folder / f"{stem}.json").write_text(json.dumps(inventory, indent=2, ensure_ascii=False),
                                                       encoding="utf-8")
        except Exception as exc:
            self.errors.append(f"{stem} inventario: {exc}")
        self.steps.append({"step": stem, "url": page.url, "title": _safe(lambda: page.title())})
        print(f"  [{stem}] {page.url}")
        return inventory

    def finish(self) -> None:
        summary = {"steps": self.steps, "notes": self.notes, "errors": self.errors, "network": self.network}
        (self.folder / "resumen.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                   encoding="utf-8")


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def click_first(page, candidates, what: str, timeout_ms: int = 5000) -> bool:
    """Prueba varios localizadores y hace clic en el primero visible."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for loc in candidates:
            try:
                target = loc.first
                if target.count() and target.is_visible():
                    target.click()
                    print(f"  clic: {what}")
                    return True
            except Exception:
                continue
        time.sleep(0.3)
    print(f"  (no encontré: {what})")
    return False


TOKEN_JS = "() => !!(document.querySelector('[name=\"cf-turnstile-response\"]') || {}).value"


def wait_for_turnstile(page, probe: Probe, live_url, timeout_s: int, tag: str) -> bool:
    """Cloudflare suele aprobar solo; si en 15 s no lo hace, se pide ayuda a la persona."""
    start = time.time()
    asked = False
    while time.time() - start < timeout_s:
        if _safe(lambda: page.evaluate(TOKEN_JS), False):
            waited = round(time.time() - start)
            probe.notes[f"turnstile_{tag}"] = {"solved_after_s": waited, "needed_person": asked}
            print(f"  Cloudflare aprobó ({waited} s{', con tu clic' if asked else ', sin intervención'}).")
            return True
        if not asked and time.time() - start > 15:
            asked = True
            print("\n  >>> Marca la casilla 'Verify you are human' de Cloudflare.")
            if live_url:
                print(f"      Se abre el navegador remoto en una pestaña. Si no: {live_url}")
                webbrowser.open(live_url)
            else:
                print("      Está en la ventana de Chrome que abrió el script.")
        time.sleep(1)
    probe.notes[f"turnstile_{tag}"] = {"solved_after_s": None, "needed_person": asked}
    print("  Se acabó el tiempo y Cloudflare no aprobó.")
    return False


def search(page, probe: Probe, live_url, args, tag: str, last: str, first: str, date_from, date_to) -> bool:
    """Llena el formulario (nombre, tipos de caso, fechas), pasa Cloudflare y busca."""
    page.wait_for_selector("input[name='form:search_tab:lastname']", timeout=20000)
    page.fill("input[name='form:search_tab:lastname']", last)
    page.fill("input[name='form:search_tab:fname']", first)
    for code in [c.strip().upper() for c in args.types.split(",") if c.strip()]:
        box = page.locator(f"input[name='form:search_tab:ps_court'][value='{code}']")
        if box.count():
            # PrimeFaces oculta el checkbox real; se marca con clic en su recuadro visible.
            _safe(lambda: box.first.check(force=True))
            if not _safe(lambda: box.first.is_checked(), False):
                _safe(lambda: box.first.locator("xpath=ancestor::div[contains(@class,'ui-chkbox')][1]")
                      .locator(".ui-chkbox-box").click())
    if date_from:
        page.fill("input[name='form:search_tab:fromDate']", date_from)
    if date_to:
        page.fill("input[name='form:search_tab:toDate']", date_to)
    probe.notes[f"form_{tag}"] = page.evaluate(
        "() => ({last: document.querySelector(\"[name='form:search_tab:lastname']\").value,"
        " types: Array.from(document.querySelectorAll(\"[name='form:search_tab:ps_court']:checked\")).map(c => c.value),"
        " from: document.querySelector(\"[name='form:search_tab:fromDate']\").value,"
        " to: document.querySelector(\"[name='form:search_tab:toDate']\").value})")
    print(f"  búsqueda {tag}: {probe.notes[f'form_{tag}']}")
    probe.save(f"formulario_{tag}")
    if not wait_for_turnstile(page, probe, live_url, args.captcha_timeout, tag):
        probe.save(f"cloudflare_sin_resolver_{tag}")
        return False
    page.locator("form#form").get_by_role("button", name="Search", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(4000)
    probe.save(f"resultados_{tag}")
    # Desplegar cada persona (hasta 8) para ver sus casos.
    togglers = page.locator(".ui-row-toggler")
    probe.notes[f"personas_{tag}"] = togglers.count()
    for i in range(min(togglers.count(), 8)):
        _safe(lambda i=i: togglers.nth(i).click())
        page.wait_for_timeout(1500)
    probe.save(f"resultados_{tag}_desplegados")
    return True


def run(args) -> int:
    load_dotenv()
    if args.local:
        os.environ["BROWSER_PROVIDER"] = "local"
    from core.adapter import _FETCH_JS
    from core.browser import open_browser, provider_name

    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "probe_civitek" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    url = args.base or BASE.format(code=args.county)
    print(f"Navegador: {provider_name()}  |  {url}")
    print(f"Guardando en {folder.resolve()}\n")

    with open_browser() as session:
        page = session.page
        probe = Probe(page, folder)
        probe.notes.update(provider=session.provider, county=args.county, start_url=url, args=vars(args))
        try:
            # 1. Página del condado: acceso "Public" y aviso legal.
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            probe.save("condado")
            if not click_first(page, [page.get_by_role("link", name=re.compile(r"^\s*public\s*$", re.I)),
                                      page.get_by_role("button", name=re.compile(r"^\s*public\s*$", re.I)),
                                      page.get_by_text(re.compile(r"^\s*public\s*$", re.I))], "Public"):
                probe.errors.append("No encontré la opción Public")
            page.wait_for_timeout(2500)
            if click_first(page, [page.get_by_role("button", name=re.compile(r"i agree|accept|agree", re.I)),
                                  page.get_by_role("link", name=re.compile(r"i agree|accept|agree", re.I))],
                           "aviso legal (I Agree)", timeout_ms=4000):
                page.wait_for_timeout(2500)

            # 2. Búsqueda principal, con filtros (tipo de caso y rango de fechas).
            if not search(page, probe, session.live_view_url, args, "principal", args.last, args.first,
                          args.date_from, args.date_to):
                return 1

            # 3. Abrir el caso: enlace con forma de UCN (422026CF003891CFAXMX).
            ucn = page.get_by_role("link", name=UCN_RE)
            probe.notes["ucn_links"] = [_safe(lambda i=i: ucn.nth(i).inner_text()) for i in range(min(ucn.count(), 20))]
            if not ucn.count():
                probe.errors.append("No encontré enlaces de caso (UCN) en los resultados")
                return 1
            probe.notes["ucn_link_html"] = _safe(lambda: ucn.first.evaluate("e => e.outerHTML"))
            ucn.first.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(4000)
            probe.save("caso")

            # 4. Docket completo ("ALL") y el documento buscado.
            page.locator("select").filter(has=page.locator("option", has_text="ALL")).first.select_option(label="ALL")
            page.wait_for_timeout(3000)
            probe.save("docket_completo")
            row = page.locator("tr", has_text=re.compile(args.doc, re.I)).last
            if row.count():
                href = row.locator("a[href*='docketimage']").first.get_attribute("href")
                full = urljoin(page.url, href)
                probe.notes["documento_href"] = href
                result = page.evaluate(_FETCH_JS, {"url": full, "referrer": page.url})
                if result.get("error"):
                    probe.errors.append(f"descarga: {result['error']}")
                else:
                    import base64
                    body = base64.b64decode(result["b64"])
                    (folder / "documento.pdf").write_bytes(body)
                    probe.notes["documento_descarga"] = {"status": result["status"], "type": result["type"],
                                                         "bytes": len(body), "es_pdf": body[:5] == b"%PDF-"}
                    print(f"  documento: {probe.notes['documento_descarga']}")
            else:
                probe.errors.append(f"No hay en el docket una fila con '{args.doc}'")

            # 5. Segunda búsqueda, con un nombre común: varias personas y varios casos.
            if args.last2:
                page.goto(urljoin(page.url, "/ocrs/app/search.xhtml"), wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                if search(page, probe, session.live_view_url, args, "comun", args.last2, args.first2,
                          args.date_from2, args.date_to2):
                    view = page.get_by_role("button", name=re.compile("view selection", re.I))
                    if view.count():
                        view.first.click()
                        page.wait_for_timeout(4000)
                        probe.save("comun_view_selection")
            print("\nListo.")
            return 0
        except Exception as exc:
            probe.errors.append(f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
            _safe(lambda: probe.save("error"))
            return 1
        finally:
            probe.finish()
            print(f"\nCarpeta: {folder.resolve()}")
            print("Comprímela y envíala (tiene resumen.json, HTML, capturas y el PDF descargado).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Exploración del portal Civitek OCRS")
    parser.add_argument("--county", default="42", help="código del condado en Civitek (Marion = 42)")
    parser.add_argument("--last", default="Hogg")
    parser.add_argument("--first", default="Amy")
    parser.add_argument("--doc", default=r"arrest affidavit", help="texto del documento a abrir (regex)")
    parser.add_argument("--types", default="CF,MM", help="tipos de caso a marcar (CF, MM, CT...)")
    parser.add_argument("--date-from", default="09/01/2026", help="fecha de presentación desde (MM/DD/YYYY)")
    parser.add_argument("--date-to", default="10/12/2026")
    parser.add_argument("--last2", default="Smith", help="segunda búsqueda, nombre común ('' para omitir)")
    parser.add_argument("--first2", default="John")
    parser.add_argument("--date-from2", default="01/01/2026")
    parser.add_argument("--date-to2", default="10/12/2026")
    parser.add_argument("--local", action="store_true", help="usar Chrome local en vez de Browserbase")
    parser.add_argument("--captcha-timeout", type=int, default=300, help="segundos para marcar la casilla")
    parser.add_argument("--base", help=argparse.SUPPRESS)  # solo para pruebas con una réplica local
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())