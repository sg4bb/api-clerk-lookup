"""Exploración del portal de casos de Volusia (Case Inquiry, ccms.clerk.org), 11/11/2026.

No es parte del worker. Recorre una vez lo que haces tú a mano y guarda lo que ve, para construir el
adaptador con la estructura real:

  1. Abre ccms.clerk.org y acepta el aviso ("Accept").
  2. Guarda el formulario de inquiry.aspx (campos y opciones de "Case Type").
  3. Busca el nombre (Last / First, y fechas de "Filing Date" si las das) y guarda los resultados.
  4. Despliega el "+" de la primera fila (los cargos).
  5. Abre el caso (--case, o el primero penal) y la pestaña "Docket".
  6. Abre el documento que coincida con --doc (por defecto CHARGING AFFIDAVIT o similares), espera
     a que el visor pida el PDF ya tachado (load_Redact.aspx) y lo baja como documento.pdf.
  7. Guarda las pestañas Offense, Custody y Parties del caso.
  8. Con --repeat N repite la búsqueda N veces, para ver si el reCAPTCHA invisible empieza a frenar.

El formulario dice "protected by reCAPTCHA" (la versión invisible, que no muestra casilla). Este
probe no lo toca ni lo resuelve: solo anota si la búsqueda pasa o no.

    python probe_volusia.py --name "Schmalz-Riedt, John"
    python probe_volusia.py --name "Bowers, Sebastian" --from 10/25/2026 --to 11/11/2026
    python probe_volusia.py --name "Schmalz-Riedt, John" --browserbase

Por defecto usa el Chrome de tu PC (no gasta Browserbase). Todo queda en output/probe_volusia/<fecha>/
(output/ no se sube a git). Comprime esa carpeta y envíala.
"""

from __future__ import annotations

import argparse
import os
import re
import time
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from probe_benchmark import Probe
from probe_civitek import _safe

HOST = "ccms.clerk.org"
START = "https://ccms.clerk.org/"
INQUIRY = "https://ccms.clerk.org/inquiry.aspx"
DEFAULT_DOC = r"charging affidavit|arrest affidavit|probable cause|arrest report|complaint affidavit"

# El campo de texto cuyo rótulo (celda o texto de al lado) empieza con `label`.
FIELD_JS = r"""
(label) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').replace(/[: ]/g, ' ').trim().toLowerCase();
  const want = norm(label);
  const inputs = Array.from(document.querySelectorAll("input[type=text], input:not([type])"))
    .filter(i => !/mail|password|user|login/i.test((i.id || '') + ' ' + (i.name || '')));
  const near = el => {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return norm(l.innerText);
    }
    let prev = el.previousSibling;
    while (prev && !(prev.textContent || '').trim()) prev = prev.previousSibling;
    if (prev) return norm(prev.textContent);
    const cell = el.closest('td');
    if (cell && cell.previousElementSibling) return norm(cell.previousElementSibling.innerText);
    return '';
  };
  const hits = inputs.filter(i => { const n = near(i); return n === want || n.endsWith(' ' + want) || n.startsWith(want); });
  const hit = hits[0];
  if (!hit) return null;
  if (!hit.id) hit.id = '__probe_' + want.replace(/[^a-z]/g, '');
  return '#' + CSS.escape(hit.id);
}
"""

NEXT_FIELD_JS = r"""
(sel) => {
  const a = sel && document.querySelector(sel);
  if (!a) return null;
  const all = Array.from(document.querySelectorAll("input[type=text], input:not([type])"));
  const b = all[all.indexOf(a) + 1];
  if (!b) return null;
  if (!b.id) b.id = '__probe_next';
  return '#' + CSS.escape(b.id);
}
"""

SELECTS_JS = r"""() => Array.from(document.querySelectorAll('select')).map(s => ({
  id: s.id, name: s.name,
  options: Array.from(s.options).map(o => ({value: o.value, text: o.text.trim()})).slice(0, 60)}))"""

RESULTS_JS = r"""() => {
  const t = (document.body || {}).innerText || '';
  const m = t.match(/Records Found\s*(\d+)/i);
  const links = Array.from(document.querySelectorAll('a')).filter(a => /^\s*\d{4}\s+\d{5,6}\s+[A-Z]{4}\s*$/.test(a.innerText));
  return {found: m ? parseInt(m[1], 10) : null, case_links: links.slice(0, 25).map(a => ({
    text: a.innerText.trim(), href: a.getAttribute('href'), target: a.getAttribute('target'),
    onclick: a.getAttribute('onclick')}))};
}"""

DOCKET_ROWS_JS = r"""() => {
  const grid = document.querySelector('#divDocketGrid table, [id*=GridDocket]');
  if (!grid) return null;
  return Array.from(grid.querySelectorAll('tr')).map(tr => {
    const cells = Array.from(tr.querySelectorAll(':scope > td, :scope > th')).map(c => c.innerText.replace(/\s+/g, ' ').trim());
    const link = tr.querySelector('a, input[type=image], img[onclick]');
    const img = tr.querySelector('img');
    return {cells, link: link ? {tag: link.tagName, id: link.id, href: link.getAttribute('href'),
            onclick: link.getAttribute('onclick'), outer: link.outerHTML.slice(0, 400)} : null,
            icon: img ? img.getAttribute('src') : null};
  });
}"""


def fill(page, probe: Probe, label: str, value: str) -> None:
    if not value:
        return
    selector = _safe(lambda: page.evaluate(FIELD_JS, label))
    probe.notes.setdefault("campos", {})[label] = selector
    if selector:
        page.fill(selector, value)
    else:
        print(f"  No encontré el campo '{label}'")


def accept_disclaimer(page, probe: Probe) -> None:
    page.goto(START, wait_until="load")
    page.wait_for_timeout(1500)
    probe.save("aviso")
    accept = page.locator("a[href*='button_accept'], input[value='Accept'], a:has-text('Accept')")
    if _safe(lambda: accept.count(), 0):
        with page.expect_navigation(wait_until="load", timeout=30_000):
            accept.first.click()
        probe.notes["aviso_aceptado"] = True
    else:
        probe.notes["aviso_aceptado"] = "no vi el botón Accept"
    page.wait_for_timeout(1000)
    if "inquiry.aspx" not in page.url:
        page.goto(INQUIRY, wait_until="load")
    probe.notes["formulario_url"] = page.url


def search(page, probe: Probe, tag: str, last: str, first: str, begin: str, end: str) -> dict:
    if "inquiry.aspx" not in page.url:
        page.goto(INQUIRY, wait_until="load")
    fill(page, probe, "Last", last)
    fill(page, probe, "First", first)
    fill(page, probe, "Filing Date From", begin)
    if end:
        # El "to" de "Filing Date From" es el campo de texto que le sigue.
        to_sel = _safe(lambda: page.evaluate(NEXT_FIELD_JS, page.evaluate(FIELD_JS, "Filing Date From")))
        probe.notes.setdefault("campos", {})["Filing Date To"] = to_sel
        if to_sel:
            page.fill(to_sel, end)
    started = time.monotonic()
    submit = page.locator("a:has-text('Submit'), input[value='Submit'], input[type=image][alt*='Submit' i]")
    submit.first.click()
    try:
        page.wait_for_function("() => /Records Found|No records|not found|recaptcha|invalid/i.test("
                               "(document.body || {}).innerText || '')", timeout=45_000, polling=500)
    except Exception:
        print("  No vi 'Records Found' en 45 s")
    page.wait_for_timeout(1500)
    result = _safe(lambda: page.evaluate(RESULTS_JS), {}) or {}
    result["segundos"] = round(time.monotonic() - started, 1)
    probe.notes[f"resultados_{tag}"] = result
    print(f"  {tag}: {result.get('found')} resultado(s) en {result['segundos']} s")
    probe.save(f"resultados_{tag}")
    return result


def open_case(page, probe: Probe, case: str, results: dict):
    links = results.get("case_links") or []
    wanted = [l for l in links if case and re.sub(r"\s+", "", l["text"]) == re.sub(r"\s+", "", case)] \
        or [l for l in links if re.search(r"\bC[FT]|MM", l["text"])] or links
    if not wanted:
        print("  No hay casos para abrir")
        return None
    text = wanted[0]["text"]
    probe.notes["caso_abierto"] = text
    # El "+" de la fila: los cargos (GetChargeGrid, por AJAX).
    plus = page.locator("img[onclick*='GetChargeGrid']").first
    if _safe(lambda: (plus.click(), True)[1], False):
        _safe(lambda: page.wait_for_function(
            "() => Array.from(document.querySelectorAll(\"div[id^='div']\")).some(d => /^div\\d+$/.test(d.id) && d.innerText.trim())",
            timeout=15_000, polling=500))
        page.wait_for_timeout(1000)
        probe.save("cargos_desplegados")
    else:
        print("  No pude desplegar los cargos (+)")
    link = page.get_by_text(text, exact=True).first
    try:
        with page.context.expect_page(timeout=8000) as popup_info:
            link.click()
        case_page = popup_info.value
        probe.notes["caso_en"] = "ventana nueva"
    except Exception:
        case_page = page
        probe.notes["caso_en"] = "misma pestaña"
    case_page.wait_for_load_state("load")
    case_page.wait_for_timeout(1500)
    probe.save("caso", case_page)
    return case_page


def open_tab(case_page, probe: Probe, tab_id: str, label: str) -> None:
    """Pestañas del caso que se cargan con postback: Offense (cargos y fechas), Custody (arresto)."""
    tab = case_page.locator(f"#__tab_tabContainer_{tab_id}").first
    if not _safe(lambda: (tab.click(), True)[1], False):
        print(f"  No pude abrir la pestaña {label}")
        return
    case_page.wait_for_timeout(4000)
    probe.save(label.lower(), case_page)


def open_docket(case_page, probe: Probe, doc_regex: str):
    tab = case_page.locator("#__tab_tabContainer_docket, a:has-text('Docket')").first
    tab.click()
    try:
        case_page.wait_for_function(f"() => !!({DOCKET_ROWS_JS})()", timeout=30_000, polling=500)
    except Exception:
        print("  La tabla del docket no apareció en 30 s")
    case_page.wait_for_timeout(1500)
    probe.save("docket", case_page)
    rows = _safe(lambda: case_page.evaluate(DOCKET_ROWS_JS), []) or []
    probe.notes["docket"] = rows[:80]
    hits = [i for i, r in enumerate(rows) if re.search(doc_regex, " ".join(r["cells"]), re.I) and r.get("link")]
    probe.notes["documento_fila"] = rows[hits[0]] if hits else None
    print(f"  docket: {len(rows)} fila(s); documento: {rows[hits[0]]['cells'] if hits else 'no encontrado'}")
    return rows, (hits[0] if hits else None)


FETCH_PDF_JS = r"""
async (url) => {
  const r = await fetch(url, {credentials: 'include'});
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = '';
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return {status: r.status, type: r.headers.get('content-type') || '', b64: btoa(bin)};
}
"""


def open_document(case_page, probe: Probe, rows: list, index: int) -> None:
    """Abre el documento como lo hace la persona. Cuando el visor va a abrir el PDF ya tachado
    (load_Redact.aspx), esa apertura se corta en el navegador antes de salir (no llega al portal) y
    el PDF se pide con fetch desde la página del caso, en la misma sesión.

    Por qué: load_Redact.aspx entrega el PDF una sola vez (probe v2: el segundo pedido devolvió una
    página vacía de 602 bytes). Si lo abre el visor, el PDF se va al visor de Chrome o, en
    Browserbase, a una descarga, y ya no se puede volver a pedir."""
    import base64
    seen: list[str] = []

    def hold_viewer(route) -> None:
        if route.request.resource_type == "document":
            seen.append(route.request.url)
            route.abort()                 # la ventana del visor no lo pide: lo pide el fetch de abajo
        else:
            route.fallback()

    case_page.context.route("**/load_Redact.aspx*", hold_viewer)
    grid_rows = case_page.locator("#divDocketGrid table tr, [id*=GridDocket] tr")
    target = grid_rows.nth(index).locator("a, input[type=image], img[onclick]").first
    started = time.monotonic()
    try:
        with case_page.context.expect_page(timeout=10_000) as popup_info:
            target.click()
        doc = popup_info.value
    except Exception:
        doc = case_page
    probe.notes["documento_ventana"] = doc is not case_page
    for _ in range(90):                      # hasta 3 minutos
        if seen:
            break
        case_page.wait_for_timeout(2000)
    probe.notes["documento_listo_segundos"] = round(time.monotonic() - started, 1) if seen else None
    probe.notes["documento_url"] = seen[0] if seen else _safe(lambda: doc.url, "")
    if not seen:
        print("  El visor nunca pidió load_Redact.aspx en 3 minutos")
        _safe(lambda: probe.save("documento", doc))
        return
    result = _safe(lambda: case_page.evaluate(FETCH_PDF_JS, seen[0]), {}) or {}
    _safe(lambda: case_page.context.unroute("**/load_Redact.aspx*", hold_viewer))
    body = base64.b64decode(result.get("b64") or "")
    probe.notes["documento_fetch"] = {"status": result.get("status"), "type": result.get("type"), "bytes": len(body),
                                      "es_pdf": body[:4] == b"%PDF"}
    if body:
        (probe.folder / "documento.pdf").write_bytes(body)
    print(f"  documento listo en {probe.notes['documento_listo_segundos']} s; fetch: "
          f"{probe.notes['documento_fetch']}")
    _safe(lambda: doc.close()) if doc is not case_page else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Exploración del portal de casos de Volusia")
    parser.add_argument("--name", required=True, help='"Apellido, Nombre"')
    parser.add_argument("--case", default="", help='caso a abrir, p. ej. "2026 105517 CFDL" (si no, el primero penal)')
    parser.add_argument("--doc", default=DEFAULT_DOC, help="regex del documento del docket")
    parser.add_argument("--from", dest="begin", default="", help="Filing Date From (MM/DD/YYYY)")
    parser.add_argument("--to", dest="end", default="", help="Filing Date To (MM/DD/YYYY)")
    parser.add_argument("--repeat", type=int, default=0, help="búsquedas extra para ver si frena el reCAPTCHA")
    parser.add_argument("--browserbase", action="store_true", help="Browserbase en vez del Chrome de tu PC")
    args = parser.parse_args()
    last, _, first = (p.strip() for p in args.name.partition(","))

    load_dotenv()
    os.environ["BROWSER_PROVIDER"] = "browserbase" if args.browserbase else "local"
    from core.browser import open_browser

    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "probe_volusia" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Navegador: {os.environ['BROWSER_PROVIDER']}  |  {START}\nGuardando en {folder.resolve()}\n")
    with open_browser() as session:
        page = session.page
        if session.live_view_url:
            print(f"Live view: {session.live_view_url}\n")
        probe = Probe(page, folder, host=HOST)
        probe.notes["args"] = {"name": args.name, "case": args.case, "doc": args.doc, "from": args.begin,
                               "to": args.end, "repeat": args.repeat}
        try:
            accept_disclaimer(page, probe)
            probe.save("formulario")
            probe.notes["selects"] = _safe(lambda: page.evaluate(SELECTS_JS), [])
            probe.notes["recaptcha"] = _safe(lambda: page.evaluate(
                "() => ({badge: !!document.querySelector('.grecaptcha-badge'), "
                "scripts: Array.from(document.scripts).map(s => s.src).filter(s => /recaptcha/.test(s)), "
                "token_field: !!document.querySelector('[name=g-recaptcha-response], #g-recaptcha-response')})"))
            results = search(page, probe, "nombre", last, first, args.begin, args.end)
            case_page = open_case(page, probe, args.case, results)
            if case_page is not None:
                rows, index = open_docket(case_page, probe, args.doc)
                if index is not None:
                    open_document(case_page, probe, rows, index)
                open_tab(case_page, probe, "offense", "Offense")
                open_tab(case_page, probe, "custody", "Custody")
                open_tab(case_page, probe, "parties", "Parties")
            for n in range(1, args.repeat + 1):
                page.bring_to_front()
                page.goto(INQUIRY, wait_until="load")
                search(page, probe, f"repetida_{n}", last, first, args.begin, args.end)
        except Exception:
            probe.errors.append(traceback.format_exc())
            print(traceback.format_exc())
            _safe(lambda: probe.save("error"))
        finally:
            probe.finish()
    print(f"\nListo.\nCarpeta: {folder.resolve()}\nComprímela y envíala.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
