"""Adaptador de BenchmarkWeb (Pioneer Technology Group / Catalis).

Comprobado en Osceola (01/11/2026, versión 2.9.46.0):

- Búsqueda anónima por nombre ("Apellido, Nombre"), sin cuenta y sin CAPTCHA
  (la página carga el script de reCAPTCHA; si algún día lo muestra, lo resuelve
  una persona). El formulario trae campos ocultos de fechas de apertura
  (openedFrom / openedTo) que el portal respeta aunque no se vean.
- Resultados: tabla DataTables llenada por una consulta JSON, de todos los tipos
  de caso y de parte (DEFENDANT, PLAINTIFF, ATTORNEY...). Si hay un solo caso, el
  portal salta directo a su página.
- El resumen desplegable de cada caso (CourtCase.aspx/CaseThumbnail) trae la
  agencia, el "Agency Report #" y los cargos con "Offense Date" e "Initial Status Date".
- El docket marca "DATE OF ARREST". Cada documento tiene un id y un digest; el PDF se
  pide en dos pasos: ImageAsync.aspx/GetPDFRequestGuid -> guid -> ImageAsync.aspx/GetPDF.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote, urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from core.adapter import ClerkAdapter, DocumentUnavailable, DownloadError, SearchError
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
from core.documents import is_pdf
from core.utils import parse_us_date, portal_text, safe_filename

log = logging.getLogger(__name__)

# "2026 CF 003520" o "2021 CC 002132 EV": año, tipo de corte, número y, a veces, división
# (cualquier sufijo: no se usa).
_CASE_RE = re.compile(r"^\s*(\d{4})\s+([A-Z]{2})\s+(\d{6})\b")
# Otros formatos de número de caso según el condado, todos con el tipo de corte:
#   St. Johns: "26001446CFMA" (año de 2 cifras + 6 cifras + tipo + división), "26009995TR",
#              "CC26-5468" / "CA11-1911" (civiles);  formato Civitek: "2026CF000052AXMA".
_CASE_FORMATS = (
    _CASE_RE,
    re.compile(r"^\s*()\d{8}([A-Z]{2})"),
    re.compile(r"^\s*()\d{8}([A-Z])(?![A-Z])"),   # Charlotte: 26001343F (F = felony)
    re.compile(r"^\s*()\d{4}([A-Z]{2})\d{6}"),
    re.compile(r"^\s*()([A-Z]{2})\d{2}-\d+"),
)
_BIRTH_YEAR_RE = re.compile(r"\((\d{4})\)")
_DATE = r"(\d{1,2}/\d{1,2}/\d{4})"
MAX_PAGES = 5            # de 100 filas; con el rango de fechas casi nunca pasa de una
RESULTS_TIMEOUT_MS = 30_000

_RESULTS_STATE_JS = r"""
() => {
  if (location.href.indexOf('/CourtCase.aspx/Details/') >= 0) return 'case';
  // Flagler: sin CAPTCHA resuelto vuelve al formulario con "?error=Invalid Captcha".
  if (/home\.aspx\/search/i.test(location.pathname) && /captcha/i.test(decodeURIComponent(location.search))) return 'captcha';
  // De nuevo en el formulario (página nueva, sin la marca que se pone al enviar).
  if (location.href.toLowerCase().indexOf('/home.aspx/search') >= 0 && !window.__clerkSubmitted && document.querySelector('#name')) return 'form';
  const box = Array.from(document.querySelectorAll("iframe[src*='recaptcha']"))
    .find(f => f.offsetWidth > 50 && f.offsetHeight > 50 && !/size=invisible/.test(f.src));
  if (box && !document.querySelector('#gridSearchResults')) return 'captcha';
  // "Cases Found 0": el portal no dibuja la tabla (Espinosa y McMilliann, 01/11/2026).
  const found = document.querySelector('.caseFoundFilter');
  if (found && /(^|\s)0\s*$/.test(found.innerText.trim())) return 'empty';
  const grid = document.querySelector('#gridSearchResults');
  if (!grid) return '';
  const processing = document.querySelector('#gridSearchResults_processing');
  if (processing && getComputedStyle(processing).display !== 'none') return '';
  if (grid.querySelector('td.dataTables_empty')) return 'empty';
  return grid.querySelector("a[title^='View Case Details for']") ? 'rows' : '';
}
"""

_READ_ROWS_JS = """
() => Array.from(document.querySelectorAll('#gridSearchResults tbody tr'))
  .filter(tr => tr.querySelector("a[title^='View Case Details for']"))
  .map(tr => {
    const tds = tr.querySelectorAll(':scope > td');
    const link = tr.querySelector("a[title^='View Case Details for']");
    const name = tr.querySelector("a[title^='View Party Details for']");
    const expand = tr.querySelector("img[onclick*='details(']");
    return {
      case: link.innerText.trim(), href: link.getAttribute('href'),
      name: name ? name.innerText.trim() : '', nameCell: tds[1] ? tds[1].innerText : '',
      party: tds[2] ? tds[2].innerText.trim() : '', status: tds[4] ? tds[4].innerText.trim() : '',
      thumb: expand ? expand.getAttribute('onclick') : '',
    };
  })
"""

_CAPTCHA_KIND_JS = """
() => (document.querySelector("iframe[src*='hcaptcha'], .h-captcha, [name='h-captcha-response']") ? 'hcaptcha'
       : document.querySelector("iframe[src*='recaptcha'], .g-recaptcha") ? 'recaptcha' : null)
"""

# Firma de las filas visibles: cambia cuando la tabla se vuelve a dibujar (el portal no
# muestra el "Showing 1 to N of M" de DataTables).
_ROWS_SIGNATURE_JS = """
() => Array.from(document.querySelectorAll("#gridSearchResults a[title^='View Case Details for']"))
  .map(a => a.innerText.trim()).join('|')
"""

# "Cases Found N" del encabezado de resultados.
_CASES_FOUND_JS = r"""
() => { const el = document.querySelector('.caseFoundFilter');
        const m = el ? el.innerText.match(/(\d+)\s*$/) : null; return m ? parseInt(m[1], 10) : null; }
"""

_CASE_READY_JS = """
() => !!document.querySelector('#gridDockets') ||
      /no (case )?dockets|dockets are currently unavailable/i.test((document.querySelector('#courtCaseDockets') || {}).innerText || '')
"""

_CASE_SUMMARY_JS = """
() => !!(document.querySelector('#summaryAccordionCollapse dd.casenumber') &&
         document.querySelector('#gridParties tbody tr'))
"""

_READ_CASE_JS = """
() => {
  const text = el => el ? el.innerText.replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim() : '';
  const summary = {};
  document.querySelectorAll('#summaryAccordionCollapse dd').forEach(dd => {
    summary[(dd.className || '').trim()] = text(dd);
  });
  const parties = Array.from(document.querySelectorAll('#gridParties tbody tr')).map(tr => {
    const tds = tr.querySelectorAll(':scope > td');
    const a = tds[1] ? tds[1].querySelector("a[title^='View Party Details for']") : null;
    return {type: text(tds[0]), name: a ? text(a) : text(tds[1])};
  });
  const docket = Array.from(document.querySelectorAll('#gridDockets tbody tr')).map(tr => {
    const tds = tr.querySelectorAll(':scope > td');
    const img = tr.querySelector('a.casedocketimage');
    const entry = tr.querySelector("td[id^='text_']");
    return {
      seq: text(tds[0]), date: tds.length > 3 ? text(tds[tds.length - 2]) : '',
      text: entry ? text(entry) : text(tds[tds.length - 1]),
      cid: img ? img.getAttribute('rel') || img.id : null, digest: img ? img.getAttribute('digest') : null,
    };
  });
  return {title: document.title.replace(/\\s+/g, ' ').trim(), summary, parties, docket,
          unavailable: /dockets are currently unavailable/i.test(text(document.querySelector('#courtCaseDockets')))};
}
"""

# Resumen desplegable: los cargos con sus fechas. Se pide como lo pide el "+" de la fila.
_THUMB_JS = """
async (url) => {
  const r = await fetch(url, {credentials: 'include'});
  const html = await r.text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const text = el => el ? el.textContent.replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim() : '';
  const summary = {};
  doc.querySelectorAll('table.thumbSummary tr').forEach(tr => {
    const tds = tr.querySelectorAll('td');
    if (tds.length >= 2) summary[text(tds[0]).replace(/:$/, '')] = text(tds[1]);
  });
  const charges = [];
  const box = doc.querySelector('div.vscroll table');
  if (box) {
    for (const tr of box.querySelectorAll(':scope > tbody > tr, :scope > tr')) {
      const tds = tr.querySelectorAll(':scope > td');
      if (tds.length >= 2 && /^\\d+$/.test(text(tds[0]))) {
        charges.push({count: text(tds[0]), description: text(tds[1]), offense: '', initial: '', obts: ''});
      } else if (charges.length) {
        // Tabla anidada: una fila de títulos (Offense Date, Initial Status Date, OBTS #) y la de valores.
        const rows = Array.from(tr.querySelectorAll('table tr'));
        const head = rows.findIndex(r => /offense date/i.test(r.textContent));
        if (head >= 0 && rows[head + 1]) {
          const labels = Array.from(rows[head].querySelectorAll('td, th')).map(c => text(c).toLowerCase());
          const values = Array.from(rows[head + 1].querySelectorAll('td')).map(c => text(c));
          const pick = re => { const i = labels.findIndex(l => re.test(l)); return i >= 0 ? (values[i] || '') : ''; };
          const last = charges[charges.length - 1];
          last.offense = pick(/offense/); last.initial = pick(/initial/); last.obts = pick(/obts/);
        }
      }
    }
  }
  return {status: r.status, summary, charges};
}
"""

# El documento: el portal arma el PDF y lo entrega por un guid.
_PDF_JS = """
async ({base, cid, digest}) => {
  try {
    const form = new URLSearchParams({cid: cid, digest: digest, time: new Date().toString(), redacted: 'False'});
    const g = await fetch(base + 'ImageAsync.aspx/GetPDFRequestGuid', {method: 'POST', credentials: 'include',
      headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest'},
      body: form.toString()});
    const guid = (await g.text()).trim().replace(/^"|"$/g, '');
    if (!/^[0-9a-f-]{20,}$/i.test(guid)) return {status: g.status, type: 'guid', b64: btoa(unescape(encodeURIComponent(guid.slice(0, 3000))))};
    const r = await fetch(base + 'ImageAsync.aspx/GetPDF?guid=' + encodeURIComponent(guid), {credentials: 'include'});
    const bytes = new Uint8Array(await r.arrayBuffer());
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return {status: r.status, type: r.headers.get('content-type') || '', b64: btoa(bin)};
  } catch (e) {
    return {error: String(e)};
  }
}
"""


def _date(text: Optional[str]) -> Optional[date]:
    m = re.search(_DATE, text or "")
    return parse_us_date(m.group(1)) if m else None


def court_code(case_number: str) -> str:
    """Tipo de corte del número de caso: '2026 CF 003520' -> 'CF', '26001446CFMA' -> 'CF',
    'CC26-5468' -> 'CC'. Vacío si el número no tiene una forma conocida."""
    for regex in _CASE_FORMATS:
        m = regex.match(case_number or "")
        if m:
            return m.group(2)
    return ""


def clean_name(name_cell: str) -> str:
    """'GARCIA, KATHRYN YOUNG (1966) (Alias)' -> 'GARCIA, KATHRYN YOUNG'."""
    return re.sub(r"\s+", " ", re.sub(r"\(.*?\)", " ", name_cell or "")).strip(" ,")


def birth_date(name_cell: str) -> Optional[date]:
    """El portal solo muestra el año de nacimiento: se toma el 1 de julio, así la edad
    calculada nunca se aleja más de un año de la real."""
    m = _BIRTH_YEAR_RE.search(name_cell or "")
    return date(int(m.group(1)), 7, 1) if m else None


def case_from_title(title: str) -> tuple[str, str]:
    """'2026 CF 003520 - STATE OF FLORIDA vs. GARCIA, KATHRYN' -> ('2026 CF 003520', 'GARCIA, KATHRYN')."""
    title = re.sub(r"\s+", " ", title or "").strip()
    number, _, rest = title.partition(" - ")
    if not court_code(number):
        return "", ""
    m = re.search(r"\bvs?\.?\s+(.+)$", rest, re.I)
    return number.strip(), (m.group(1).strip() if m else "")


def thumb_url(onclick: str) -> Optional[str]:
    """"details(3658470, 'CourtCase.aspx/CaseThumbnail/', '?digest=X');" -> 'CourtCase.aspx/CaseThumbnail/3658470?digest=X'."""
    m = re.search(r"details\((\d+),\s*'([^']+)',\s*'([^']+)'", onclick or "")
    return f"{m.group(2)}{m.group(1)}{m.group(3)}" if m else None


def case_type(case_number: str, codes: Optional[dict[str, str]] = None) -> str:
    """Tipo de caso con los códigos del worker (CF, MM, CT). `codes` traduce las terminaciones
    propias del condado (Charlotte: "F" -> "CF"); sin traducción queda la del número."""
    code = court_code(case_number)
    return (codes or {}).get(code, code)


def candidates_from_rows(rows: list[dict], case_types: list[str],
                         codes: Optional[dict[str, str]] = None) -> list[CaseCandidate]:
    """Solo los casos penales en los que la persona es la acusada; uno por caso (los
    alias repiten la fila: se guardan como otros nombres del mismo caso)."""
    wanted = {t.upper() for t in case_types}
    by_case: dict[str, CaseCandidate] = {}
    # Primero las filas del nombre principal; las de "(Alias)" se agregan como otros nombres.
    ordered = sorted(rows, key=lambda r: "(alias)" in (r.get("nameCell") or "").lower())
    for row in ordered:
        number = re.sub(r"\s+", " ", row.get("case", "")).strip()
        if row.get("party", "").upper() != "DEFENDANT" or case_type(number, codes) not in wanted:
            continue
        name = clean_name(row.get("nameCell") or row.get("name", ""))
        if number in by_case:
            if name and name not in by_case[number].aliases and name != by_case[number].defendant:
                by_case[number].aliases.append(name)
            continue
        by_case[number] = CaseCandidate(
            case_number=number, defendant=name, case_type=case_type(number, codes), status=row.get("status", ""),
            dob=birth_date(row.get("nameCell", "")), filed_date=None, detail_ref=row.get("href", ""),
        )
        # El enlace del resumen desplegable va junto al del caso (mismo id y digest).
        thumb = thumb_url(row.get("thumb", ""))
        if thumb:
            by_case[number].detail_ref = row.get("href", "") + "#thumb=" + thumb
    return list(by_case.values())


def split_ref(detail_ref: str) -> tuple[str, Optional[str]]:
    """detail_ref -> (enlace del caso, enlace del resumen desplegable)."""
    href, _, thumb = detail_ref.partition("#thumb=")
    return href, thumb or None


def thumb_from_details(href: str) -> Optional[str]:
    """Sin fila de resultados (el portal saltó directo al caso): el resumen usa el mismo id y digest."""
    parsed = urlparse(href)
    m = re.search(r"/Details/(\d+)", parsed.path)
    raw = re.search(r"(?:^|&)digest=([^&]+)", parsed.query)
    # El digest es base64: un "+" crudo es un "+" (parse_qs lo volvería espacio).
    digest = unquote(raw.group(1)) if raw else None
    if not (m and digest):
        return None
    return f"CourtCase.aspx/CaseThumbnail/{m.group(1)}?digest={quote(digest, safe='')}"


_AGENCY_PLACE_LAST_RE = re.compile(r"^((?:SHERIFF'?S?|POLICE)\b[^-]*?)\s+-\s+(.+)$", re.I)


def normalize_agency_name(text: str) -> str:
    """'OSCEOLA CO SHERIFF'S OFFICE' -> 'OSCEOLA COUNTY SHERIFF'S OFFICE' y "SHERIFF'S OFFICE -
    CHARLOTTE COUNTY" -> "CHARLOTTE COUNTY SHERIFF'S OFFICE" (solo para comparar con la nota)."""
    text = re.sub(r"\bCO\b(?=\.?\s+SHERIFF)", "COUNTY", text or "", flags=re.I).strip()
    flipped = _AGENCY_PLACE_LAST_RE.match(text)
    return f"{flipped.group(2).strip()} {flipped.group(1).strip()}" if flipped else text


def charges_from_case(summary: dict, thumb: dict, arrest: Optional[date]) -> list[Charge]:
    agency = normalize_agency_name(summary.get("agency") or thumb.get("summary", {}).get("Agency", "")) or None
    report = (summary.get("agencyreportnumber") or thumb.get("summary", {}).get("Agency Report #", "")).strip() or None
    charges = []
    for c in thumb.get("charges", []):
        charges.append(Charge(
            offense_date=_date(c.get("offense")), description=c.get("description", ""), statute=None,
            arrest_date=arrest, agency=agency, control_number=report, initial_date=_date(c.get("initial")),
        ))
    if not charges and (agency or report or arrest):
        # Sin resumen de cargos: al menos la agencia, el reporte y la fecha de arresto.
        charges.append(Charge(offense_date=None, description="", statute=None, arrest_date=arrest,
                              agency=agency, control_number=report))
    return charges


def docket_from_case(rows: list[dict]) -> list[DocketEntry]:
    docket = []
    for row in rows:
        seq = re.sub(r"\D", "", row.get("seq", ""))
        docket.append(DocketEntry(
            date=_date(row.get("date")), description=row.get("text", ""),
            doc_ref=f"{row['cid']}|{row['digest']}" if row.get("cid") and row.get("digest") else None,
            number=int(seq) if seq else None,
        ))
    return docket


def consolidated_into(docket: list[DocketEntry]) -> Optional[str]:
    """El caso al que la fiscalía unió este: "CONSOLIDATED TO 26001422CFMA - ADD ON CHARGE" (St. Johns)."""
    for e in docket:
        m = re.match(r"^\s*consolidated\s+(?:to|into|with)\s+(\S+)", e.description, re.I)
        if m:
            return m.group(1)
    return None


def site_root(base_url: str) -> str:
    """'https://x/BenchmarkWeb/Home.aspx/Search' -> 'https://x/BenchmarkWeb/'; 'https://y/Home.aspx/Search' -> 'https://y/'."""
    path = urlparse(base_url).path
    i = path.lower().find("/home.aspx")
    return urljoin(base_url, path[:i + 1] if i >= 0 else "/")


def arrest_date_from_docket(docket: list[DocketEntry]) -> Optional[date]:
    """La fecha de arresto del docket (la primera, si hay varias): "DATE OF ARREST" (Osceola),
    "ARRESTED ON 10/24/2026" / "REARRESTED ON 10/24/2026" (Flagler), "ARREST COUNT: ..." (St. Johns)
    o "ARREST SEQ: ..." (Charlotte).

    St. Johns anota un "ARREST COUNT" por cargo; si tienen fechas distintas (cargos añadidos al caso
    en otros días) no hay una fecha de arresto fiable y se usan las fechas Initial de los cargos."""
    dates, counts = [], set()
    for e in docket:
        said = re.match(r"^\s*(?:re)?arrested on\s+(\d{1,2}/\d{1,2}/\d{4})", e.description, re.I)
        if said:
            dates.append(_date(said.group(1)))
        elif e.date and re.match(r"^\s*date of arrest", e.description, re.I):
            dates.append(e.date)
        elif e.date and re.match(r"^\s*arrest (?:count|seq)\b", e.description, re.I):
            # St. Johns: "ARREST COUNT: 1 - (828.122 3A) ANIMAL FIGHTING", con la fecha del arresto.
            # Charlotte: "ARREST SEQ: 1 - (316.1935 3A) FLEE/ELUDE POLICE ...".
            counts.add(e.date)
    if len(counts) == 1:
        dates.extend(counts)
    dates = [d for d in dates if d]
    return min(dates) if dates else None


class BenchmarkWebAdapter(ClerkAdapter):

    def site_root(self) -> str:
        """Raíz de la aplicación: ".../BenchmarkWeb/" (Osceola) o "/" (Flagler, en su propio dominio)."""
        return site_root(self.county.base_url)

    # --- 1. Búsqueda --------------------------------------------------------

    def search(
        self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]
    ) -> list[CaseCandidate]:
        page = self.page
        name = f"{portal_text(query.last_name)}, {portal_text(query.first_name)}"
        dates = {"type": "Name", "search": name,
                 "openedFrom": f"{date_from:%m/%d/%Y}" if date_from else "",
                 "openedTo": f"{date_to:%m/%d/%Y}" if date_to else ""}
        log.info("Buscando %s entre %s y %s (tipos %s)", name, date_from, date_to, query.case_types)
        for attempt in (1, 2):
            self._submit_search(name, dates)
            state = self._wait_results()
            if state == "captcha":
                state = self._search_with_captcha(name, dates)
            if state != "form":
                break
            # El portal devolvió el formulario vacío (01/11/2026, Chrome local: el nombre se
            # escribió antes de que la página terminara de prepararse y se envió vacío).
            log.warning("El portal volvió al formulario sin resultados (intento %d de 2)", attempt)
        if state == "form":
            raise SearchError("The court portal returned the empty search form instead of results")
        if state == "case":
            candidate = self._candidate_from_case_page()
            log.info("Un solo caso: el portal fue directo a %s", candidate.case_number if candidate else page.url)
            return [candidate] if candidate and candidate.case_type in {t.upper() for t in query.case_types} else []
        if state == "empty":
            log.info("El portal no encontró coincidencias")
            return []
        if state != "rows":
            raise SearchError("The court portal didn't show the search results")

        rows = self._read_all_rows()
        candidates = candidates_from_rows(rows, query.case_types, self.county.case_type_codes)
        log.info("%d fila(s) en resultados, %d caso(s) penal(es) como acusado", len(rows), len(candidates))
        kept = {c.case_number for c in candidates}
        others = [f"{r.get('case')} ({r.get('party')}, {clean_name(r.get('nameCell') or r.get('name', ''))})"
                  for r in rows if re.sub(r"\s+", " ", r.get("case", "")).strip() not in kept]
        if others:
            log.info("Fuera (otro tipo de caso o de parte): %s", "; ".join(dict.fromkeys(others))[:900])
        return candidates

    def _submit_search(self, name: str, dates: dict) -> None:
        self._open_form()
        self._fill_form(name, dates)
        self._click_search()

    def _open_form(self) -> None:
        """La página de búsqueda, con sus scripts listos.

        Los scripts del portal (search.js) eligen la opción "Name" al cargar y eso vacía el
        campo: si se escribe antes, se envía el formulario sin nombre y el portal vuelve a él.
        """
        page = self.page
        page.goto(self.county.base_url, wait_until="load")
        # "attached", no "visible": Charlotte abre en "All" y el campo del nombre está oculto hasta
        # elegir "Name" (_fill_form lo elige).
        page.wait_for_selector("#name", state="attached", timeout=25_000)
        self._wait_scripts()

    def _wait_scripts(self) -> None:
        """Espera a que search.js termine de preparar el formulario. Al cargar, marca la opción por
        defecto: en Charlotte ("All") deshace la elección de "Name" si se hizo antes."""
        page = self.page
        try:
            page.wait_for_load_state("load", timeout=15_000)
            page.wait_for_function("() => !!(window.jQuery && jQuery.isReady)", timeout=15_000, polling=300)
        except PlaywrightTimeout:
            log.warning("Los scripts del portal tardan en cargar; sigo igual")
        page.wait_for_timeout(1000)

    def _fill_form(self, name: str, dates: dict) -> None:
        page = self.page
        if page.locator("#radio_0").count():
            page.locator("#radio_0").check()
        # Charlotte abre con "All" marcado y search.js envía la opción marcada: hay que elegir "Name".
        # En los demás ya viene marcada y esto no hace nada.
        radio = page.locator("input.radioButton[searchtype='Name']")
        if radio.count() and not radio.first.is_checked():
            self._choose_name_option(radio.first)
        page.fill("#name", name)
        page.wait_for_timeout(300)
        if page.input_value("#name") != name:
            page.fill("#name", name)
        # Fechas de apertura. Osceola: solo hay campos ocultos, y el portal los respeta (Jones,
        # James: 369 casos sin fechas, 1 con 2026). Flagler: también hay campos visibles
        # (#openedFrom / #openedTo) y search.js copia esos sobre los ocultos al enviar: se
        # llenan los dos.
        page.evaluate("""(h) => {
            for (const id of ['openedFrom', 'openedTo']) {
                const box = document.getElementById(id);
                if (box) { box.value = h[id]; box.dispatchEvent(new Event('change', {bubbles: true})); }
            }
            const form = document.querySelector('form.searchform');
            const put = () => { for (const [k, v] of Object.entries(h)) {
                const el = form.querySelector('input[type=hidden][name="' + k + '"]'); if (el) el.value = v; } };
            put();
            if (!window.__clerkListener) { form.addEventListener('submit', put, true); window.__clerkListener = true; }
            window.__clerkSubmitted = true;
        }""", dates)

    def _choose_name_option(self, radio) -> None:
        """Marca "Name" y deja visible su campo (Charlotte). Si los scripts del portal lo desmarcan
        o no muestran el campo, se marca desde la página y se muestra el campo a mano."""
        page = self.page
        for _ in range(2):
            radio.click()
            try:
                page.wait_for_selector("#name", state="visible", timeout=4000)
                if radio.is_checked():
                    return
            except PlaywrightTimeout:
                pass
            page.wait_for_timeout(1000)
        log.warning("La opción Name no quedó marcada con un clic; la marco desde la página")
        page.evaluate("""() => {
            const r = document.querySelector("input.radioButton[searchtype='Name']");
            r.checked = true;
            r.dispatchEvent(new Event('change', {bubbles: true}));
            const box = document.getElementById('name');
            if (box && getComputedStyle(box).display === 'none') box.style.display = 'inline-block';
        }""")
        page.wait_for_timeout(300)

    def _click_search(self) -> None:
        button = self.page.get_by_role("button", name=re.compile(r"^\s*search\s*$", re.I))
        if not button.count() and self.page.locator("#searchButton").count():
            # Charlotte: el ícono del botón entra en su nombre accesible y "Search" ya no queda solo.
            button = self.page.locator("#searchButton")
        button.first.click()

    def _search_with_captcha(self, name: str, dates: dict) -> str:
        """El portal pidió el CAPTCHA (Flagler: hCaptcha, "Invalid Captcha" si se busca sin él).

        Solo la primera búsqueda de la sesión lo pide: las siguientes pasan sin él (Flagler,
        02/11/2026). Lo resuelve una persona; después se vuelve a buscar.
        """
        page = self.page
        if not page.locator("#name").count():
            self._open_form()
        else:
            self._wait_scripts()     # la página del "Invalid Captcha" acaba de cargar
        self._fill_form(name, dates)
        kind = page.evaluate(_CAPTCHA_KIND_JS) or "recaptcha"
        log.info("El portal pide un CAPTCHA (%s); se pide a la persona", kind)
        self.captcha.solve(page, timeout_s=self.county.captcha_timeout_s, kind=kind)
        self._click_search()
        state = self._wait_results()
        if state == "captcha":
            raise SearchError("The court portal didn't accept the CAPTCHA")
        return state

    def _wait_results(self) -> str:
        try:
            handle = self.page.wait_for_function(_RESULTS_STATE_JS, timeout=RESULTS_TIMEOUT_MS, polling=500)
            return handle.json_value()
        except PlaywrightTimeout:
            return ""
        except PlaywrightError as exc:
            if "context was destroyed" in str(exc) or "navigat" in str(exc).lower():
                self.page.wait_for_load_state("domcontentloaded")
                return self._wait_results()
            raise

    def _read_all_rows(self) -> list[dict]:
        """Todas las filas (hasta MAX_PAGES de 100). Solo se toca la tabla si hay más casos
        de los que ya se ven: con pocos resultados, la primera página alcanza."""
        page = self.page
        total = page.evaluate(_CASES_FOUND_JS)
        rows = page.evaluate(_READ_ROWS_JS)
        if total is None or len(rows) >= total:
            return rows
        length = page.locator("select[name='gridSearchResults_length']")
        if length.count():
            before = page.evaluate(_ROWS_SIGNATURE_JS)
            length.select_option("100")
            self._wait_redraw(before)
            rows = page.evaluate(_READ_ROWS_JS)
        for _ in range(MAX_PAGES - 1):
            if len(rows) >= total:
                break
            nxt = page.locator("#gridSearchResults_next")
            if not nxt.count() or "disabled" in (nxt.get_attribute("class") or ""):
                break
            before = page.evaluate(_ROWS_SIGNATURE_JS)
            nxt.click()
            self._wait_redraw(before)
            rows += page.evaluate(_READ_ROWS_JS)
        if len(rows) < total:
            log.warning("El portal tiene %d filas; leí %d", total, len(rows))
        return rows

    def _wait_redraw(self, before: str) -> None:
        try:
            self.page.wait_for_function(f"(b) => ({_ROWS_SIGNATURE_JS.strip()})() !== b",
                                        arg=before, timeout=15_000, polling=300)
        except PlaywrightTimeout:
            log.warning("La tabla de resultados no cambió en 15 s")
        self.page.wait_for_timeout(500)

    def _candidate_from_case_page(self) -> Optional[CaseCandidate]:
        # El resumen y las partes llegan después de la página (otra consulta): se esperan.
        # Garcia, McMillian y Jeanbaptiste (01/11/2026): leídos al instante salían vacíos.
        try:
            self.page.wait_for_function(_CASE_SUMMARY_JS, timeout=20_000, polling=500)
        except PlaywrightTimeout:
            log.warning("El resumen del caso no cargó; uso el título de la página")
        data = self.page.evaluate(_READ_CASE_JS)
        title_number, title_defendant = case_from_title(data.get("title", ""))
        number = re.sub(r"\s+", " ", data.get("summary", {}).get("casenumber", "")).strip() or title_number
        defendant = next((p["name"] for p in data.get("parties", [])
                          if p.get("type", "").upper().startswith("DEFENDANT")), "") or title_defendant
        if not number or not defendant:
            log.warning("No pude leer el caso al que saltó el portal: %r", data.get("title"))
            return None
        href = urlparse(self.page.url)
        return CaseCandidate(case_number=number, defendant=defendant, case_type=case_type(number, self.county.case_type_codes),
                             status=data.get("summary", {}).get("status", ""), dob=None,
                             filed_date=_date(data.get("summary", {}).get("clerkfiledate")),
                             detail_ref=href.path + ("?" + href.query if href.query else ""))

    # --- 2. Caso --------------------------------------------------------------

    def open_case(self, candidate: CaseCandidate) -> CaseDetails:
        href, thumb = split_ref(candidate.detail_ref)
        url = urljoin(self.county.base_url, href)
        thumb = thumb or thumb_from_details(href)
        tab = self.page.context.new_page()
        try:
            tab.goto(url, wait_until="domcontentloaded")
            tab.wait_for_function(_CASE_READY_JS, timeout=25_000, polling=500)
            data = tab.evaluate(_READ_CASE_JS)
            thumb_data = {}
            if thumb:
                try:
                    thumb_data = tab.evaluate(_THUMB_JS, urljoin(self.site_root(), thumb.lstrip("/")))
                except PlaywrightError as exc:
                    log.warning("%s: no pude leer el resumen de cargos (%s)", candidate.case_number, exc)
            summary = data.get("summary", {})
            filed = _date(summary.get("clerkfiledate"))
            if filed:
                candidate.filed_date = filed
            docket = docket_from_case(data.get("docket", []))
            arrest = arrest_date_from_docket(docket)
            charges = charges_from_case(summary, thumb_data, arrest)
            report = (summary.get("agencyreportnumber") or "").strip() or None
            details = CaseDetails(candidate=candidate, charges=charges, docket=docket, source_url=tab.url,
                                  docket_unavailable=bool(data.get("unavailable")), agency_report_number=report,
                                  consolidated=consolidated_into(docket) is not None)
            log.info("%s: %d cargo(s), %d entrada(s) de docket, agencia %s, reporte %s, arresto %s",
                     candidate.case_number, len(charges), len(docket), charges[0].agency if charges else None,
                     report, arrest)
            log.info("Cargos (%s): %s", candidate.case_number, " | ".join(
                f"{c.description} (ofensa {c.offense_date}, inicial {c.initial_date})" for c in charges)[:900])
            return details
        except PlaywrightTimeout:
            self._save_debug(tab, f"case_{safe_filename(candidate.case_number)}")
            raise SearchError(f"The case page for {candidate.case_number} did not load") from None
        finally:
            tab.close()

    def find_documents(self, details: CaseDetails) -> list[DocketEntry]:
        """El primero que entró al expediente primero (por "Seq #")."""
        return sorted(super().find_documents(details), key=lambda e: (e.number is None, e.number or 0))

    def find_support_documents(self, details: CaseDetails) -> list[DocketEntry]:
        return sorted(super().find_support_documents(details), key=lambda e: (e.number is None, e.number or 0))

    # --- 3. Descarga ----------------------------------------------------------

    def download(self, details: CaseDetails, entry: DocketEntry, dest_dir: Path) -> Path:
        if not entry.doc_ref or "|" not in entry.doc_ref:
            raise DownloadError(f"'{entry.description}' has no document link")
        cid, digest = entry.doc_ref.split("|", 1)
        base = self.site_root()
        result = self.page.evaluate(_PDF_JS, {"base": base, "cid": cid, "digest": digest})
        if result.get("error"):
            raise DownloadError(f"The court portal didn't hand over '{entry.description}': {result['error']}")
        body = base64.b64decode(result["b64"])
        status, content_type = result["status"], result["type"]
        if status >= 400 or not is_pdf(body):
            self._save_download_debug(entry, status, content_type, body)
            text = body[:4000].decode("utf-8", "replace").lower()
            if content_type == "guid" or "not available" in text or "request" in text:
                raise DocumentUnavailable(f"The court portal isn't handing over '{entry.description}' right now")
            raise DownloadError(f"The court portal didn't hand over the document '{entry.description}' "
                                f"(HTTP {status}, {content_type or 'no content type'}, {len(body)} bytes)")
        path = dest_dir / safe_filename(f"{details.candidate.case_number} - {entry.description}.pdf")
        path.write_bytes(body)
        log.info("PDF guardado: %s (%d KB)", path, len(body) // 1024)
        return path

    # --- Diagnóstico ----------------------------------------------------------

    def _save_debug(self, tab, name: str) -> None:
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            tab.screenshot(path=str(self.debug_dir / f"{name}.png"), full_page=True)
            (self.debug_dir / f"{name}.html").write_text(tab.content(), encoding="utf-8")
        except Exception as exc:
            log.warning("No pude guardar el debug del caso: %s", exc)

    def _save_download_debug(self, entry: DocketEntry, status: int, content_type: str, body: bytes) -> None:
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            (self.debug_dir / "download_response.txt").write_text(
                f"documento: {entry.description} (Seq #{entry.number})\nref: {entry.doc_ref}\n"
                f"HTTP {status}\ncontent-type: {content_type!r}\nbytes: {len(body)}\n\n"
                + body[:4000].decode("utf-8", "replace"), encoding="utf-8")
        except Exception as exc:
            log.warning("No pude guardar la respuesta de la descarga: %s", exc)
