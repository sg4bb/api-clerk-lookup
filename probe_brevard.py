"""Exploración del portal de casos de Brevard (BECA, vmatrix1.brevardclerk.us), 13/11/2026.

No es parte del worker. Recorre una vez lo que haces tú a mano y guarda lo que ve, para construir el
adaptador con la estructura real:

  1. Abre beca_splash.cfm, marca "Accept" y pulsa Submit.
  2. Pulsa "General Public Court Records Search" y guarda el formulario de búsqueda por nombre
     (nombres de los campos, las opciones de "Begins with / Exactly" y las casillas de categorías).
  3. Busca el nombre (Last / First y, si las das, las fechas "From / To") y guarda los resultados:
     si salen en la misma pestaña o en una ventana nueva, y qué trae cada fila (tipo, fecha, DOB).
  4. Abre el caso (--case, o el primero penal) y guarda la "Charging Agency" (con el número de
     reporte que sale al pasar el mouse) y el Register of Actions con sus íconos.
  5. Pide el documento que coincida con --doc (por defecto ARREST AFFIDAVIT o similares) desde la
     página del caso, dos veces: para ver si es un PDF directo y si se puede pedir más de una vez.
  6. Con --repeat N repite la búsqueda N veces, para ver si el portal empieza a frenar.

El portal no mostró CAPTCHA a mano. Si aparece uno, este probe no lo toca: solo lo anota.

    python probe_brevard.py --name "Thomason, Argentina" --from 10/01/2026 --to 10/10/2026
    python probe_brevard.py --name "Thomason, Argentina" --from 10/01/2026 --to 10/10/2026 --browserbase

Por defecto usa el Chrome de tu PC (no gasta Browserbase). Todo queda en output/probe_brevard/<fecha>/
(output/ no se sube a git). Comprime esa carpeta y envíala.
"""

from __future__ import annotations

import argparse
import base64
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

HOST = "vmatrix1.brevardclerk.us"
SPLASH = "https://vmatrix1.brevardclerk.us/beca/beca_splash.cfm"
NAME_SEARCH = "https://vmatrix1.brevardclerk.us/beca/name_search.cfm"
DEFAULT_DOC = r"arrest affidavit|probable cause affidavit|complaint affidavit|arrest report|charging affidavit"

FORM_JS = r"""() => Array.from(document.querySelectorAll('form')).map(f => ({
  name: f.getAttribute('name'), id: f.id, action: f.getAttribute('action'), method: f.getAttribute('method'),
  target: f.getAttribute('target'), onsubmit: f.getAttribute('onsubmit'),
  fields: Array.from(f.elements).map(e => ({tag: e.tagName, type: e.type, name: e.name, id: e.id,
    value: e.type === 'checkbox' || e.type === 'radio' ? e.value + (e.checked ? ' (checked)' : '') : (e.value || '').slice(0, 40),
    options: e.tagName === 'SELECT' ? Array.from(e.options).map(o => o.value + '=' + o.text.trim()) : undefined,
    label: (e.closest('td, label') || {}).innerText ? e.closest('td, label').innerText.replace(/\s+/g, ' ').trim().slice(0, 60) : ''}))
}))"""

RESULTS_JS = r"""() => {
  const t = (document.body || {}).innerText || '';
  const m = t.match(/(\d+)\s+Current Cases? Returned/i);
  const rows = Array.from(document.querySelectorAll('a[href*="all_results.cfm"]')).map(a => {
    const cells = Array.from(a.closest('tr').querySelectorAll('td')).map(c => c.innerText.replace(/\s+/g, ' ').trim());
    const charges = [];
    let next = a.closest('table').nextElementSibling;
    while (next && !(next.tagName === 'TABLE' && next.querySelector('a[href*="all_results.cfm"]'))) {
      if (next.tagName === 'TABLE') next.querySelectorAll('tr').forEach(tr => {
        const c = Array.from(tr.querySelectorAll('td')).map(x => x.innerText.replace(/\s+/g, ' ').trim());
        if (c.length) charges.push(c);
      });
      next = next.nextElementSibling;
    }
    return {cells, href: a.getAttribute('href'), target: a.getAttribute('target'), charges};
  });
  return {returned: m ? parseInt(m[1], 10) : null, range: (t.match(/BETWEEN\s+'[^']+'\s+AND\s+'[^']+'/i) || [''])[0],
          no_results: /no (cases|records)|not found|0 Current Cases/i.test(t), rows: rows.slice(0, 40)};
}"""

CASE_JS = r"""() => {
  const info = Array.from(document.querySelectorAll('table.TFtable')).find(t => /Charging Agency/i.test(t.innerText));
  let agency = null;
  if (info) {
    const cell = info.querySelector('div.l');
    agency = cell ? {text: cell.innerText.replace(/\s+/g, ' ').trim(), html: cell.innerHTML.slice(0, 500),
                     span: (cell.querySelector('span') || {}).textContent} : null;
  }
  const docket = Array.from(document.querySelectorAll('#tbl-content tr')).map(tr => {
    const a = tr.querySelector('a[href*="get_document"], a[href]');
    const img = tr.querySelector('img');
    return {cells: Array.from(tr.querySelectorAll('td')).map(c => {
              const span = c.querySelector('span');
              return {text: c.innerText.replace(/\s+/g, ' ').trim(), hover: span ? span.textContent.replace(/\s+/g, ' ').trim() : null};
            }),
            href: a ? a.getAttribute('href') : null, title: a ? a.getAttribute('title') : null,
            target: a ? a.getAttribute('target') : null, icon: img ? img.getAttribute('src') : null};
  });
  return {title: (document.querySelector('h5') || {}).innerText, agency, docket: docket.slice(0, 120),
          sections: Array.from(document.querySelectorAll('a[name]')).map(a => a.getAttribute('name'))};
}"""


def enter(page, probe: Probe) -> None:
    page.goto(SPLASH, wait_until="load")
    page.wait_for_timeout(1000)
    probe.save("aviso")
    probe.notes["aviso_formularios"] = _safe(lambda: page.evaluate(FORM_JS), [])
    accept = page.locator("input[type=radio][value*='accept' i]:not([value*='not' i]), input[type=radio]").first
    _safe(lambda: accept.check())
    with page.expect_navigation(wait_until="load", timeout=30_000):
        page.locator("input[type=submit], button:has-text('Submit')").first.click()
    page.wait_for_timeout(1000)
    probe.notes["tras_aviso_url"] = page.url
    probe.save("inicio")
    general = page.locator("text=General Public Court Records Search").first
    if _safe(lambda: general.count(), 0):
        probe.notes["boton_general"] = _safe(lambda: general.evaluate("e => e.outerHTML.slice(0, 400)"))
        try:
            with page.expect_navigation(wait_until="load", timeout=30_000):
                general.click()
        except Exception:
            print("  El botón 'General Public' no navegó en 30 s")
    else:
        print("  No vi el botón 'General Public Court Records Search'")
    page.wait_for_timeout(1000)
    probe.notes["tras_general_url"] = page.url
    if "name_search" not in page.url.lower():
        probe.save("menu")
        page.goto(NAME_SEARCH, wait_until="load")
        probe.notes["formulario_por_url_directa"] = True
    probe.notes["formulario_url"] = page.url


def search(page, probe: Probe, tag: str, last: str, first: str, begin: str, end: str):
    if "name_search" not in page.url.lower():
        page.goto(NAME_SEARCH, wait_until="load")
    # Los nombres del formulario (probe v1, 13/11/2026). Por el rótulo no sirve en Browserbase:
    # ExtJS dibuja los <select> de "Begins with / Exactly" como cajas con ese texto delante del campo.
    for name, value in (("lname", last), ("fname", first), ("fDate", begin), ("tDate", end)):
        if value:
            page.fill(f"input[name='{name}']", value)
    started = time.monotonic()
    button = page.locator("input[type=submit][value='Search'], button:has-text('Search')").first
    try:
        with page.context.expect_page(timeout=8000) as popup_info:
            button.click()
        results = popup_info.value
        probe.notes[f"resultados_{tag}_en"] = "ventana nueva"
    except Exception:
        results = page
        probe.notes[f"resultados_{tag}_en"] = "misma pestaña"
    try:
        results.wait_for_function("() => /Cases? Returned|no (cases|records)|not found|captcha/i.test("
                                  "(document.body || {}).innerText || '')", timeout=45_000, polling=500)
    except Exception:
        print("  No vi 'Cases Returned' en 45 s")
    results.wait_for_timeout(1000)
    data = _safe(lambda: results.evaluate(RESULTS_JS), {}) or {}
    data["segundos"] = round(time.monotonic() - started, 1)
    data["url"] = results.url
    probe.notes[f"resultados_{tag}"] = data
    print(f"  {tag}: {data.get('returned')} caso(s) en {data['segundos']} s ({probe.notes[f'resultados_{tag}_en']})")
    probe.save(f"resultados_{tag}", results)
    return results, data


def open_case(results, probe: Probe, case: str, data: dict):
    rows = data.get("rows") or []
    norm = lambda s: re.sub(r"[\s-]+", "", s or "").upper()
    wanted = [r for r in rows if case and norm(case) in norm(r["cells"][0])] \
        or [r for r in rows if re.search(r"-(CF|MM|CT)-", r["cells"][0])] or rows
    if not wanted:
        print("  No hay casos para abrir")
        return None
    probe.notes["caso_abierto"] = wanted[0]["cells"][0]
    link = results.locator(f"a[href='{wanted[0]['href']}']").first
    try:
        with results.context.expect_page(timeout=6000) as popup_info:
            link.click()
        case_page = popup_info.value
        probe.notes["caso_en"] = "ventana nueva"
    except Exception:
        case_page = results
        probe.notes["caso_en"] = "misma pestaña"
    case_page.wait_for_load_state("load")
    case_page.wait_for_timeout(1000)
    probe.save("caso", case_page)
    info = _safe(lambda: case_page.evaluate(CASE_JS), {}) or {}
    probe.notes["caso"] = info
    print(f"  caso: {probe.notes['caso_abierto']}; agencia: {(info.get('agency') or {}).get('text')}; "
          f"docket: {len(info.get('docket') or [])} fila(s)")
    return case_page


def fetch_document(case_page, probe: Probe, doc_regex: str) -> None:
    rows = (probe.notes.get("caso") or {}).get("docket") or []
    hits = [r for r in rows if r.get("href") and "get_document" in r["href"]
            and re.search(doc_regex, " ".join(c["text"] for c in r["cells"]), re.I)]
    probe.notes["documento_fila"] = hits[0] if hits else None
    if not hits:
        print("  No hay documento visible que coincida con --doc")
        return
    url = hits[0]["href"]
    for attempt in ("primero", "segundo"):
        started = time.monotonic()
        result = _safe(lambda: case_page.evaluate(FETCH_PDF_JS, url), {}) or {}
        body = base64.b64decode(result.get("b64") or "")
        probe.notes[f"documento_{attempt}"] = {"status": result.get("status"), "type": result.get("type"),
                                               "bytes": len(body), "es_pdf": body[:4] == b"%PDF",
                                               "segundos": round(time.monotonic() - started, 1),
                                               "inicio": body[:200].decode("latin-1") if body[:4] != b"%PDF" else None}
        if attempt == "primero" and body:
            (probe.folder / ("documento.pdf" if body[:4] == b"%PDF" else "documento.html")).write_bytes(body)
        print(f"  documento ({attempt}): {probe.notes[f'documento_{attempt}']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Exploración del portal de casos de Brevard (BECA)")
    parser.add_argument("--name", required=True, help='"Apellido, Nombre"')
    parser.add_argument("--case", default="", help='caso a abrir, p. ej. "05-2026-MM-052463" (si no, el primero penal)')
    parser.add_argument("--doc", default=DEFAULT_DOC, help="regex del documento del Register of Actions")
    parser.add_argument("--from", dest="begin", default="", help="Filing Date From (MM/DD/YYYY)")
    parser.add_argument("--to", dest="end", default="", help="Filing Date To (MM/DD/YYYY)")
    parser.add_argument("--repeat", type=int, default=0, help="búsquedas extra para ver si el portal frena")
    parser.add_argument("--browserbase", action="store_true", help="Browserbase en vez del Chrome de tu PC")
    args = parser.parse_args()
    last, _, first = (p.strip() for p in args.name.partition(","))

    load_dotenv()
    os.environ["BROWSER_PROVIDER"] = "browserbase" if args.browserbase else "local"
    from core.browser import open_browser

    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "probe_brevard" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Navegador: {os.environ['BROWSER_PROVIDER']}  |  {SPLASH}\nGuardando en {folder.resolve()}\n")
    with open_browser() as session:
        page = session.page
        if session.live_view_url:
            print(f"Live view: {session.live_view_url}\n")
        probe = Probe(page, folder, host=HOST)
        probe.notes["args"] = {"name": args.name, "case": args.case, "doc": args.doc, "from": args.begin,
                               "to": args.end, "repeat": args.repeat}
        try:
            enter(page, probe)
            probe.save("formulario")
            probe.notes["formulario"] = _safe(lambda: page.evaluate(FORM_JS), [])
            results, data = search(page, probe, "nombre", last, first, args.begin, args.end)
            case_page = open_case(results, probe, args.case, data)
            if case_page is not None:
                fetch_document(case_page, probe, args.doc)
            for n in range(1, args.repeat + 1):
                page.bring_to_front()
                page.goto(NAME_SEARCH, wait_until="load")
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
