"""Exploración del portal de casos de Polk (ShowCaseWeb de equivant, showcase.polkcountyclerk.net),
13/11/2026.

No es parte del worker. Recorre una vez lo que haces tú a mano y guarda lo que ve, para construir el
adaptador con la estructura real. Lo más importante que tiene que responder:

  - El reCAPTCHA invisible (v3) del portal: si en un navegador automatizado (tu Chrome por Playwright
    y Browserbase) el panel "Case Search" abre y la búsqueda devuelve casos, o si queda "Invalid"
    (a mano: el panel no abría y la búsqueda no traía nada). Este probe no toca el reCAPTCHA: solo
    anota cómo queda el sello y qué responde el portal.
  - Qué pide y qué recibe la aplicación (Angular) al buscar, abrir el caso y abrir un documento: las
    llamadas de la página se anotan (dirección, estado, tipo y el comienzo de la respuesta; del token
    del reCAPTCHA solo el largo).
  - Cómo se abre el documento: el clip abre una pestaña case/pdfframe?k=...; se guarda esa página,
    qué incrusta (iframe/embed) y si el PDF se puede pedir dos veces en la misma sesión.

Cuenta del Clerk (14/11/2026): si .env tiene POLK_SHOWCASE_USERNAME y POLK_SHOWCASE_PASSWORD,
entra con ella antes de buscar (sin cuenta, la sesión pública depende del reCAPTCHA). Antes de buscar
revisa que haya accessToken en la sesión. El panel se abre con el enlace "Case Search" o, si no
responde, con la lupa de la vista móvil. Usuario, clave y token no se imprimen ni se guardan.

Con la cuenta el docket cambia de columnas ("Docket #", "Image", 14/11/2026) y los documentos
"View On Request Pending" salen con un candado en vez del clip.

Pasos: abre el portal, pulsa "Case Search", llena Last / First (y From / To si las das), busca,
abre el caso (--case, o el primero) y guarda la cabecera, el docket completo (todas sus páginas) y
las pestañas Charges y "Arrests & Bonds". Con --repeat N repite la búsqueda N veces.

    python probe_polk.py --name "Chaneyfield, Timothy"
    python probe_polk.py --name "Chaneyfield, Timothy" --case 53-2026-CF-007518 --browserbase
    python probe_polk.py --name x --hold          (solo abre el portal y espera: pruebas tú a mano)

Por defecto usa el Chrome de tu PC (no gasta Browserbase). Todo queda en output/probe_polk/<fecha>/
(output/ no se sube a git). Comprime esa carpeta y envíala.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from probe_benchmark import Probe
from probe_civitek import _safe
from probe_volusia import FETCH_PDF_JS

HOST = "showcase.polkcountyclerk.net"
HOME = "https://showcase.polkcountyclerk.net/showcaseweb/"
DEFAULT_DOC = r"affidavit/citation/notice to appear|arrest affidavit|probable cause|complaint affidavit"

BADGE_JS = r"""() => {
  const badge = document.querySelector('.grecaptcha-badge');
  return {badge: badge ? badge.innerText.replace(/\s+/g, ' ').trim() || '(sin texto)' : null,
          error: (document.querySelector('.grecaptcha-error') || {}).innerText || '',
          webdriver: navigator.webdriver,
          grecaptcha: typeof window.grecaptcha, url: location.href};
}"""

RESULTS_JS = r"""() => {
  const table = Array.from(document.querySelectorAll('table.dataTable')).find(t => /Case #/.test(t.innerText));
  if (!table) return {table: false, text: ((document.body || {}).innerText || '').slice(0, 600)};
  const heads = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent.trim());
  const rows = Array.from(table.querySelectorAll('tbody tr')).filter(tr => !tr.querySelector('td.dataTables_empty')).map(tr => {
    const tds = Array.from(tr.querySelectorAll(':scope > td'));
    const img = tr.querySelector('img[sid]');
    const link = tr.querySelector('a[ng-click*="goToCaseDetails"]');
    return {cells: tds.map(td => td.textContent.replace(/\s+/g, ' ').trim()), sid: img ? img.getAttribute('sid') : null,
            click: link ? link.getAttribute('ng-click') : null};
  });
  return {table: true, heads, rows,
          info: (document.querySelector('.dataTables_info') || {}).innerText || ''};
}"""

HEADER_JS = r"""() => Object.fromEntries(Array.from(document.querySelectorAll('div.col-xs-6 > b'))
  .map(b => [b.textContent.replace(/:\s*$/, '').trim(), b.parentElement.textContent.replace(b.textContent, '').replace(/\s+/g, ' ').trim()]))"""

TABLES_JS = r"""() => Array.from(document.querySelectorAll('table.dataTable, table.table')).map(t => ({
  id: t.id, heads: Array.from(t.querySelectorAll('thead th')).map(th => th.textContent.trim()),
  rows: Array.from(t.querySelectorAll('tbody tr')).map(tr => Array.from(tr.querySelectorAll(':scope > td'))
    .map(td => td.textContent.replace(/\s+/g, ' ').trim())),
  info: ((t.closest('.dataTables_wrapper') || {}).querySelector ? (t.closest('.dataTables_wrapper').querySelector('.dataTables_info') || {}).innerText : '') || ''
}))"""

DOCKET_PAGE_JS = r"""() => {
  const table = Array.from(document.querySelectorAll('table.dataTable')).find(t => /Docket (Number|#)/.test(t.innerText));
  if (!table) return null;
  return {rows: Array.from(table.querySelectorAll('tbody tr')).map(tr => {
            const tds = Array.from(tr.querySelectorAll(':scope > td'));
            const btn = tr.querySelector('button[ng-click*="getImage"]');
            const other = tr.querySelector('button[uib-tooltip]:not([ng-click*="getImage"])');
            return {cells: tds.map(td => td.textContent.replace(/\s+/g, ' ').trim()),
                    image: btn ? btn.getAttribute('ng-click') : null,
                    state: other ? other.getAttribute('uib-tooltip') : null,
                    first_cell: tds[0] ? tds[0].innerHTML.slice(0, 300) : ''};
          }),
          info: (table.closest('.dataTables_wrapper').querySelector('.dataTables_info') || {}).innerText || '',
          length_menu: !!table.closest('.dataTables_wrapper').querySelector('.dataTables_length')};
}"""

FRAME_JS = r"""() => ({title: document.title, url: location.href,
  embeds: Array.from(document.querySelectorAll('iframe, embed, object')).map(e => ({tag: e.tagName,
    src: e.getAttribute('src') || e.getAttribute('data'), type: e.getAttribute('type')})),
  text: ((document.body || {}).innerText || '').slice(0, 300)})"""


def _post_text(request) -> str:
    """El cuerpo de un POST como texto. Algunos no son UTF-8 (14/11/2026: request.post_data fallaba
    con UnicodeDecodeError); se leen como bytes y lo que no se entiende se descarta."""
    try:
        return (request.post_data_buffer or b"").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _secret(request) -> bool:
    """El inicio de sesión: su cuerpo lleva usuario y clave, y la respuesta el token. No se guardan."""
    return "password" in _post_text(request).lower() or bool(
        re.search(r"/(login|token|authenticate)\b", request.url, re.I))


def _hide_account(url: str) -> str:
    """La aplicación pone el usuario de la cuenta en la dirección (mainnav?_ck=<correo>)."""
    return re.sub(r"_ck=[^&]+", "_ck=<cuenta>", url or "")


def _hide_tokens(text: str) -> str:
    return re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "<token>", text or "")


class SafeProbe(Probe):
    """Probe que no guarda el inicio de sesión (usuario, clave, token) ni respuestas con tokens."""

    def _on_response(self, response) -> None:
        if _secret(response.request):
            self.network.append({"t": round(time.time(), 1), "method": response.request.method,
                                 "status": response.status, "url": response.url[:200], "post": "<oculto>"})
            return
        if "accesstoken" in (response.headers.get("content-type", "") + response.url).lower():
            return
        before = len(self.network)
        super()._on_response(response)
        for entry in self.network[before:]:
            entry["url"] = _hide_account(entry.get("url", ""))


TOKEN_JS = r"""() => {
  const raw = sessionStorage.getItem('accessToken');
  if (!raw) return {present: false};
  try {
    const stored = JSON.parse(raw);
    const token = stored.accessToken || stored.access_token || '';
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return {present: true, account: payload.unique_name === 'Public' ? 'Public' : '(cuenta registrada)',
            expires: new Date(payload.exp * 1000).toISOString()};
  } catch (e) { return {present: true, error: String(e)}; }
}"""

USERNAME_ENV = "POLK_SHOWCASE_USERNAME"
PASSWORD_ENV = "POLK_SHOWCASE_PASSWORD"


class Network:
    """Las llamadas de la aplicación (XHR/fetch) al portal: dirección, estado, tipo y el comienzo
    de la respuesta. Del token del reCAPTCHA solo se anota el largo."""

    def __init__(self, context):
        self.calls: list[dict] = []
        context.on("response", self._on_response)

    def _on_response(self, response) -> None:
        request = response.request
        if HOST not in response.url or request.resource_type not in ("xhr", "fetch", "document"):
            return
        entry = {"t": datetime.now().strftime("%H:%M:%S"), "method": request.method,
                 "url": _hide_account(response.url)[:300],
                 "kind": request.resource_type, "status": response.status,
                 "type": response.headers.get("content-type", "")}
        if _secret(request):
            entry["post"] = "<oculto: inicio de sesión>"
            self.calls.append(entry)
            return
        post = _post_text(request)
        if post:
            entry["post"] = re.sub(r'("?[\w-]*(?:captcha|token)[\w-]*"?\s*[:=]\s*"?)([^"&,}]{20,})',
                                   lambda m: f"{m.group(1)}<{len(m.group(2))} caracteres>", post, flags=re.I)[:1500]
        if "json" in entry["type"] or "text/plain" in entry["type"]:
            entry["body"] = _hide_tokens(_safe(lambda: response.text(), "") or "")[:2500]
        self.calls.append(entry)


def login(page, probe: Probe) -> None:
    """Entra con la cuenta del Clerk si está en .env (POLK_SHOWCASE_USERNAME / _PASSWORD). Las
    credenciales no se imprimen ni se guardan; del token solo se anota si está y cuándo vence."""
    username, password = os.getenv(USERNAME_ENV, "").strip(), os.getenv(PASSWORD_ENV, "")
    if not (username and password):
        probe.notes["login"] = "sin cuenta en .env (sesión pública)"
        print(f"  Sin {USERNAME_ENV} / {PASSWORD_ENV} en .env: sigo como público")
        return
    page.locator("a[ng-click*='showlogin']").first.click()
    page.wait_for_selector("#login_modal #username", state="visible", timeout=15_000)
    page.fill("#login_modal #username", username)
    page.fill("#login_modal #password", password)
    page.locator("#login_modal button[type='submit']").click()
    try:
        page.wait_for_function("() => !!document.querySelector('[ng-show=\"i.isAuthenticated\"]:not(.ng-hide)')",
                               timeout=30_000, polling=500)
        probe.notes["login"] = "ok"
        print("  Sesión iniciada con la cuenta")
    except Exception:
        probe.notes["login"] = "no se confirmó en 30 s"
        probe.notes["login_mensaje"] = _safe(lambda: page.locator("#login_modal").inner_text(), "")[-300:]
        print(f"  No se confirmó el inicio de sesión: {probe.notes['login_mensaje']!r}")
    page.wait_for_timeout(1500)


def open_panel(page, probe: Probe) -> bool:
    """El panel "Case Search". El enlace de escritorio solo abre si la sesión empezó con el reCAPTCHA
    (ng-click "i.sessionStarted && toggleSearch('case')"); la lupa de la vista móvil abre el mismo
    panel sin esa condición (14/11/2026, con la cuenta del Clerk). Primero el enlace; si no, la lupa."""
    field = "#caseSearchPanel input[ng-model='searchArgs.LastName']"
    if _safe(lambda: page.locator(field).is_visible(), False):
        return True
    page.locator("a:has-text('Case Search')").first.click()
    try:
        page.wait_for_selector(field, state="visible", timeout=5_000)
        probe.notes.setdefault("panel_por", "enlace Case Search")
        return True
    except Exception:
        pass
    page.locator("a.navbar-icon[ng-click*=\"toggleSearch('case')\"]").first.dispatch_event("click")
    try:
        page.wait_for_selector(field, state="visible", timeout=15_000)
        probe.notes.setdefault("panel_por", "lupa (vista móvil)")
        return True
    except Exception:
        return False


def open_search(page, probe: Probe) -> bool:
    page.goto(HOME, wait_until="load")
    page.wait_for_timeout(3000)
    probe.notes["recaptcha_al_entrar"] = _safe(lambda: page.evaluate(BADGE_JS))
    probe.save("inicio")
    login(page, probe)
    probe.notes["token"] = _safe(lambda: page.evaluate(TOKEN_JS))
    print(f"  accessToken: {probe.notes['token']}")
    if not (probe.notes["token"] or {}).get("present"):
        print("  Sin accessToken en la sesión: el portal no va a responder búsquedas")
        probe.save("sin_token")
        return False
    if open_panel(page, probe):
        print(f"  Panel abierto por: {probe.notes.get('panel_por')}")
        return True
    probe.notes["panel_no_abrio"] = _safe(lambda: page.evaluate(BADGE_JS))
    print("  El panel 'Case Search' no abrió (ni por el enlace ni por la lupa)")
    probe.save("panel_no_abrio")
    return False


def wait_panel_still(page, quiet_ms: int = 1200, limit_s: float = 10) -> None:
    """Espera a que el panel deje de cambiar (cuántos campos tiene) durante quiet_ms."""
    count = lambda: _safe(lambda: page.locator("#caseSearchPanel input").count(), 0)
    last, since, started = count(), time.monotonic(), time.monotonic()
    while time.monotonic() - started < limit_s:
        page.wait_for_timeout(300)
        now = count()
        if now != last:
            last, since = now, time.monotonic()
        elif (time.monotonic() - since) * 1000 >= quiet_ms:
            return


def search(page, probe: Probe, tag: str, last: str, first: str, begin: str, end: str) -> dict:
    panel = page.locator("#caseSearchPanel")
    if not open_panel(page, probe):
        raise RuntimeError("El panel 'Case Search' no abrió")
    # Con la cuenta, el panel se vuelve a armar cuando llega su configuración (aparecen Date of Birth,
    # Incident Number, Arrest Date): lo que se escribía mientras tanto se perdía a medias (14/11/2026:
    # "Chaneyfield" llegó como "Chane" y "Chaney"). Se espera a que el panel quede quieto y cada campo
    # se llena de una vez y se comprueba.
    wait_panel_still(page)
    wanted = {"LastName": last, "FirstName": first, "FromFileDate": begin, "ToFileDate": end}
    for attempt in range(3):
        for model, value in wanted.items():
            field = panel.locator(f"input[ng-model='searchArgs.{model}']")
            if _safe(lambda: field.count(), 0):
                field.fill(value or "")
        page.wait_for_timeout(500)
        got = {m: _safe(lambda: panel.locator(f"input[ng-model='searchArgs.{m}']").input_value(), None)
               for m in wanted}
        if all((got[m] or "") == (v or "") for m, v in wanted.items() if got[m] is not None):
            break
        print(f"  Los campos no quedaron como debían ({got}); los lleno otra vez")
        wait_panel_still(page)
    probe.notes[f"campos_{tag}"] = got
    started = time.monotonic()
    panel.locator("button[type='submit']").click()
    try:
        page.wait_for_function(
            "() => /search-results/.test(location.href) && document.querySelector('table.dataTable tbody tr')"
            " || /no results|no records|not found/i.test((document.querySelector('#caseSearchPanel') || {}).innerText || '')",
            timeout=45_000, polling=500)
    except Exception:
        print("  No vi resultados en 45 s")
    page.wait_for_timeout(1500)
    data = _safe(lambda: page.evaluate(RESULTS_JS), {}) or {}
    data["segundos"] = round(time.monotonic() - started, 1)
    data["recaptcha"] = _safe(lambda: page.evaluate(BADGE_JS))
    data["panel_texto"] = _safe(lambda: page.locator("#caseSearchPanel").inner_text(), "")[-300:]
    probe.notes[f"resultados_{tag}"] = data
    print(f"  {tag}: {len(data.get('rows') or [])} fila(s) en {data['segundos']} s; {data.get('info', '')}; "
          f"reCAPTCHA: {(data.get('recaptcha') or {}).get('badge')}")
    probe.save(f"resultados_{tag}")
    return data


def read_docket(page, probe: Probe) -> list[dict]:
    """Todas las páginas del docket (DataTables, 25 por página)."""
    rows: list[dict] = []
    for n in range(1, 30):
        data = _safe(lambda: page.evaluate(DOCKET_PAGE_JS))
        if not data:
            break
        for r in data["rows"]:
            r["pagina"] = n
        rows += data["rows"]
        probe.notes.setdefault("docket_paginas", []).append(data["info"])
        following = page.locator(".dataTables_wrapper li.next:not(.disabled) a").first
        if not _safe(lambda: following.count(), 0):
            break
        following.click()
        page.wait_for_timeout(800)
    return rows


def open_case(page, probe: Probe, case: str, data: dict):
    rows = data.get("rows") or []
    norm = lambda s: re.sub(r"[\s-]+", "", s or "").upper()
    pick = next((r for r in rows if case and any(norm(case) in norm(c) for c in r["cells"])), rows[0] if rows else None)
    if not pick:
        print("  No hay casos para abrir")
        return None
    number = next((c for c in pick["cells"] if re.match(r"\d{2}-\d{4}-", c)), "")
    if not number:
        print("  No encontré el número de caso en la fila")
        return None
    probe.notes["caso_abierto"] = number
    page.locator(f"a[title='{number}']").first.click()
    page.wait_for_function("() => /casedetails/.test(location.href)", timeout=30_000)
    page.wait_for_function("() => /Docket (Number|#)/.test((document.body || {}).innerText || '')", timeout=30_000)
    page.wait_for_timeout(1500)
    probe.notes["caso_url"] = page.url
    probe.notes["caso_cabecera"] = _safe(lambda: page.evaluate(HEADER_JS))
    probe.save("caso_dockets")
    docket = read_docket(page, probe)
    probe.notes["docket"] = docket
    print(f"  caso {number}: {len(docket)} entrada(s) de docket, {sum(1 for r in docket if r['image'])} con documento")
    for tab in ("Charges", "Arrests & Bonds", "Parties"):
        link = page.locator(".scrollmenu a", has_text=tab).first
        if not _safe(lambda: link.count(), 0):
            continue
        link.click()
        page.wait_for_timeout(2000)
        probe.notes[f"pestana_{tab}"] = _safe(lambda: page.evaluate(TABLES_JS))
        probe.save(f"caso_{re.sub(r'[^a-z]+', '_', tab.lower())}")
    page.locator(".scrollmenu a", has_text="Dockets").first.click()
    page.wait_for_timeout(2000)
    return docket


def open_document(page, probe: Probe, docket: list[dict], doc_regex: str) -> None:
    hits = [r for r in docket if r["image"] and re.search(doc_regex, " ".join(r["cells"]), re.I)]
    probe.notes["documento_fila"] = hits[0] if hits else None
    if not hits:
        print("  No hay documento que coincida con --doc")
        return
    target = hits[0]
    # El docket vuelve a la página 1: ir a la página del documento.
    for _ in range(target["pagina"] - 1):
        page.locator(".dataTables_wrapper li.next:not(.disabled) a").first.click()
        page.wait_for_timeout(800)
    button = page.locator(f"button[ng-click=\"{target['image']}\"]").first
    with page.context.expect_page(timeout=30_000) as popup_info:
        button.click()
    frame = popup_info.value
    _safe(lambda: frame.wait_for_load_state("load", timeout=45_000))
    frame.wait_for_timeout(2000)
    info = _safe(lambda: frame.evaluate(FRAME_JS), {}) or {"url": frame.url}
    probe.notes["documento_pestana"] = info
    print(f"  pestaña del documento: {info.get('url')} ({info.get('title')}); incrusta: {info.get('embeds')}")
    _safe(lambda: (probe.folder / "documento_pestana.html").write_text(frame.content(), encoding="utf-8"))
    targets = [info.get("url") or frame.url] + [e["src"] for e in info.get("embeds") or [] if e.get("src")]
    for n, url in enumerate(dict.fromkeys(t for t in targets if t and not t.startswith(("about:", "blob:")))):
        for attempt in ("primero", "segundo"):
            started = time.monotonic()
            result = _safe(lambda: page.evaluate(FETCH_PDF_JS, url), {}) or {}
            body = base64.b64decode(result.get("b64") or "")
            probe.notes[f"documento_{n}_{attempt}"] = {
                "url": url[:300], "status": result.get("status"), "type": result.get("type"), "bytes": len(body),
                "es_pdf": body[:4] == b"%PDF", "segundos": round(time.monotonic() - started, 1),
                "inicio": None if body[:4] == b"%PDF" else body[:300].decode("latin-1")}
            print(f"  documento {n} ({attempt}): {probe.notes[f'documento_{n}_{attempt}']}")
            if attempt == "primero" and body[:4] == b"%PDF":
                (probe.folder / f"documento_{n}.pdf").write_bytes(body)
    _safe(lambda: frame.close())


def main() -> int:
    parser = argparse.ArgumentParser(description="Exploración del portal de casos de Polk (ShowCaseWeb)")
    parser.add_argument("--name", required=True, help='"Apellido, Nombre"')
    parser.add_argument("--case", default="", help='caso a abrir, p. ej. "53-2026-CF-007518" (si no, el primero)')
    parser.add_argument("--doc", default=DEFAULT_DOC, help="regex del documento del docket")
    parser.add_argument("--from", dest="begin", default="", help="From Date (MM/DD/YYYY)")
    parser.add_argument("--to", dest="end", default="", help="To Date (MM/DD/YYYY)")
    parser.add_argument("--repeat", type=int, default=0, help="búsquedas extra para ver si el reCAPTCHA frena")
    parser.add_argument("--browserbase", action="store_true", help="Browserbase en vez del Chrome de tu PC")
    parser.add_argument("--hold", action="store_true",
                        help="solo abre el portal y espera: tú pruebas a mano (F12 o el live view) y Enter cierra")
    args = parser.parse_args()
    last, _, first = (p.strip() for p in args.name.partition(","))

    load_dotenv()
    os.environ["BROWSER_PROVIDER"] = "browserbase" if args.browserbase else "local"
    from core.browser import open_browser

    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "probe_polk" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Navegador: {os.environ['BROWSER_PROVIDER']}  |  {HOME}\nGuardando en {folder.resolve()}\n")
    with open_browser() as session:
        page = session.page
        if session.live_view_url:
            print(f"Live view: {session.live_view_url}\n")
        probe = SafeProbe(page, folder, host=HOST)
        network = Network(page.context)
        probe.notes["args"] = {"name": args.name, "case": args.case, "doc": args.doc, "from": args.begin,
                               "to": args.end, "repeat": args.repeat}
        try:
            if args.hold:
                page.goto(HOME, wait_until="load")
                page.wait_for_timeout(3000)
                probe.notes["recaptcha_al_entrar"] = _safe(lambda: page.evaluate(BADGE_JS))
                print(f"  reCAPTCHA al entrar: {probe.notes['recaptcha_al_entrar']}")
                print("\n  El navegador queda abierto para que pruebes a mano (en tu Chrome: F12; en Browserbase:"
                      " el live view).\n  Lo que haga la página queda anotado en red.json. Enter aquí para cerrar.")
                input()
                probe.notes["recaptcha_al_salir"] = _safe(lambda: page.evaluate(BADGE_JS))
                probe.save("al_salir")
            elif open_search(page, probe):
                data = search(page, probe, "nombre", last, first, args.begin, args.end)
                if data.get("rows"):
                    docket = open_case(page, probe, args.case, data)
                    if docket:
                        open_document(page, probe, docket, args.doc)
                for n in range(1, args.repeat + 1):
                    page.goto(HOME, wait_until="load")
                    page.wait_for_timeout(2000)
                    probe.notes[f"token_repetida_{n}"] = _safe(lambda: page.evaluate(TOKEN_JS))
                    search(page, probe, f"repetida_{n}", last, first, args.begin, args.end)
        except Exception:
            probe.errors.append(traceback.format_exc())
            print(traceback.format_exc())
            _safe(lambda: probe.save("error"))
        finally:
            probe.notes["red"] = network.calls
            _safe(lambda: (folder / "red.json").write_text(json.dumps(network.calls, indent=1), encoding="utf-8"))
            probe.finish()
    print(f"\nListo.\nCarpeta: {folder.resolve()}\nComprímela y envíala.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
