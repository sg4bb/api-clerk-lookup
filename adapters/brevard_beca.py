"""Adaptador para BECA, el portal de casos de Brevard (vmatrix1.brevardclerk.us/beca), 13/11/2026.

Flujo mapeado con probe_brevard (Thomason, 05-2026-MM-052463-AXXX-BC):
  1. beca_splash.cfm pide aceptar un aviso (radio RadioChk=Yes y "Submit", una vez por sesión);
     después, StartSearch.cfm ("General Public Court Records Search" solo navega a
     name_search.cfm). Sin CAPTCHA, ni con el Chrome local ni en Browserbase.
  2. name_search.cfm (form "searchform", ColdFusion): fname (con "Begins with" por defecto), mname,
     lname ("Exactly"), dob, fDate / tDate (MM/DD/YYYY; si no se tocan, el último año) y casillas
     CatChk por categoría (CF, MM, CT...; ninguna marcada = todas). El formulario tiene
     target=_blank: los resultados salen en una ventana nueva (Name_Display.cfm).
     En Browserbase ExtJS dibuja los <select> como cajas: los campos se ubican por name.
  3. Resultados: una tabla por caso con el enlace all_results.cfm?x=<cifrado> (depende de la
     sesión), nombre "THOMASON ARGENTINA L" (apellido primero, sin coma), tipo de parte
     "DEFENDANT (1)", tipo de caso, fecha de presentación, estado y FECHA DE NACIMIENTO.
  4. El caso (misma pestaña, un solo HTML con todo): "Charging Agency" con el número de reporte
     al pasar el mouse ("SANTANA LUCIA  SO5247  202600304762": oficial, placa, reporte), Initial
     Charges (fecha del delito, estatuto, descripción) y el Register of Actions con íconos:
     file.png (se ve: get_document.cfm?doc_num=<id de la sesión>), file_vor.png (Viewable On
     Request), clock.png (Pending: aún sin publicar), file-s.png (Sealed), file-c.png (Confidential).
  5. get_document.cfm devuelve el PDF directo (application/pdf) y se puede pedir más de una vez.
     El documento es el ARREST AFFIDAVIT (el "Probable Cause Affidavit" escaneado de la agencia).
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from core.adapter import ClerkAdapter, DownloadError, SearchError
from core.documents import is_pdf
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
from core.utils import parse_us_date, portal_text, safe_filename, surname_key

log = logging.getLogger(__name__)

BECA_URL = "https://vmatrix1.brevardclerk.us/beca/"
SPLASH_PATH = "beca_splash.cfm"
SEARCH_PATH = "name_search.cfm"
# Cuántos casos quedan abiertos (para descargar sin volver a buscarlos).
OPEN_CASE_PAGES = 4

_READ_RESULTS_JS = r"""
() => {
  const text = el => el ? el.innerText.replace(/ /g, ' ').replace(/\s+/g, ' ').trim() : '';
  const body = (document.body || {}).innerText || '';
  const found = body.match(/(\d+)\s+Current Cases? Returned/i);
  return {
    found: found ? parseInt(found[1], 10) : null,
    rows: Array.from(document.querySelectorAll('a[href*="all_results.cfm"]')).map(a => {
      const cells = Array.from(a.closest('tr').querySelectorAll(':scope > td')).map(text);
      return {href: a.getAttribute('href'), cells};
    }),
  };
}
"""

_CASE_JS = r"""
() => {
  const text = el => el ? el.innerText.replace(/ /g, ' ').replace(/\s+/g, ' ').trim() : '';
  // Texto propio de la celda, sin el <span> que aparece al pasar el mouse.
  const own = el => {
    if (!el) return '';
    const box = el.querySelector('div.m, div.l') || el;
    return Array.from(box.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join(' ')
      .replace(/ /g, ' ').replace(/\s+/g, ' ').trim() || text(el);
  };
  const tables = Array.from(document.querySelectorAll('table.TFtable'));
  const heads = t => Array.from(t.querySelectorAll('th')).map(text);
  const info = tables.find(t => heads(t).includes('Charging Agency'));
  let case_info = null, agency = null;
  if (info) {
    const h = heads(info);
    const row = Array.from(info.querySelectorAll('tr')).find(tr => tr.querySelector('td'));
    const cells = row ? Array.from(row.querySelectorAll(':scope > td')) : [];
    case_info = Object.fromEntries(h.map((name, i) => [name, own(cells[i])]));
    const cell = cells[h.indexOf('Charging Agency')];
    const span = cell && cell.querySelector('span');
    agency = {name: own(cell), detail: span ? span.textContent : ''};
  }
  const parties = tables.find(t => heads(t).includes('Participant Id'));
  let dob = '';
  if (parties) {
    const h = heads(parties);
    const def = Array.from(parties.querySelectorAll('tr')).find(tr => /^\s*DEFENDANT/i.test(text(tr.querySelector('td'))));
    if (def) dob = text(def.querySelectorAll(':scope > td')[h.indexOf('DOB')]);
  }
  const charges = tables.find(t => heads(t).includes('Charge Description'));
  const docket = Array.from(document.querySelectorAll('#tbl-content tr')).map(tr => {
    const link = tr.querySelector('a[href*="get_document"]');
    const icon = tr.querySelector('img');
    return {cells: Array.from(tr.querySelectorAll(':scope > td')).map(own),
            href: link ? link.getAttribute('href') : '', icon: icon ? icon.getAttribute('src') : ''};
  });
  return {
    ok: !!info || !!document.querySelector('a[name="docket"]'),
    title: text(document.querySelector('h5')), case_info, agency, dob,
    charges: charges ? {heads: heads(charges), rows: Array.from(charges.querySelectorAll('tr'))
      .filter(tr => tr.querySelector('td')).map(tr => Array.from(tr.querySelectorAll(':scope > td')).map(text))} : null,
    docket,
  };
}
"""

_CASE_CODE_RE = re.compile(r"^\s*\d{2}-\d{4}-([A-Z]{2})-")


def defendant_from_party(name: str, last_name: str) -> str:
    """"THOMASON ARGENTINA L" -> "THOMASON, ARGENTINA L". El apellido buscado dice dónde termina
    ("SCHMALZ RIEDT JOHN ERIC" con "Schmalz-Riedt"); si no aparece al inicio, es la primera palabra."""
    words = (name or "").split()
    want = surname_key(last_name)
    for end in range(len(words) - 1, 0, -1):
        if surname_key(" ".join(words[:end])) == want:
            return f"{' '.join(words[:end])}, {' '.join(words[end:])}"
    if len(words) > 1:
        return f"{words[0]}, {' '.join(words[1:])}"
    return name.strip()


def case_code(case_number: str) -> str:
    """"05-2026-MM-052463-AXXX-BC" -> "MM"."""
    match = _CASE_CODE_RE.match(case_number or "")
    return match.group(1) if match else ""


def report_from_agency(detail: str) -> Optional[str]:
    """El <span> de "Charging Agency": "SANTANA LUCIA   SO5247   202600304762" (oficial, placa,
    número de reporte). El reporte es el último bloque, si tiene al menos 5 dígitos."""
    parts = [p for p in re.split(r"\s{2,}", (detail or "").strip()) if p]
    if len(parts) < 2:
        return None
    last = parts[-1].strip()
    return last if re.fullmatch(r"[A-Z0-9-]*\d{5,}[A-Z0-9-]*", last, re.I) else None


def docket_from_rows(rows: list[dict], base_url: str = BECA_URL) -> list[DocketEntry]:
    """View | Event Date | Document Number | Description | Page Count | Amount. Solo los documentos
    que se ven (file.png con enlace) tienen doc_ref; los demás llevan por qué no:
    file_vor.png -> "request" (se pide al Clerk), clock.png -> "unpublished" (Pending: el portal
    avisa que las imágenes tardan en publicarse), file-c.png -> "(CONFIDENTIAL)" y file-s.png ->
    "(SEALED)" en la descripción, para no ofrecerlos. Los eventos sin documento (CAL:..., BOND
    POSTED) no tienen número de documento ni ícono."""
    docket = []
    for row in rows:
        cells = row.get("cells") or []
        if len(cells) < 4 or not cells[3] or re.match(r"Total Number of Document Pages", cells[3], re.I):
            continue
        description = re.sub(r"\s*-\s*\(Count \d+\)\s*$", "", cells[3]).strip()
        icon = (row.get("icon") or "").lower().rsplit("/", 1)[-1]
        href = row.get("href") or ""
        doc_ref, availability = None, None
        if "vor" in icon:
            availability = "request"
        elif "clock" in icon:
            availability = "unpublished"
        elif "file-c" in icon:
            description += " (CONFIDENTIAL)"
        elif "file-s" in icon:
            description += " (SEALED)"
        elif href:
            doc_ref = urljoin(base_url, href)
        number = re.match(r"\s*(\d+)", cells[2] or "")
        docket.append(DocketEntry(date=parse_us_date(cells[1]), description=description, doc_ref=doc_ref,
                                  number=int(number.group(1)) if number else None, availability=availability))
    return docket


def charges_from_table(table: Optional[dict], agency: Optional[str], report: Optional[str]) -> list[Charge]:
    """Initial Charges: Arrest Number | Count | Offense Date | Florida Statute | Charge Description |
    Class | Degree."""
    if not table:
        return []
    heads = table.get("heads") or []
    charges = []
    for cells in table.get("rows") or []:
        row = dict(zip(heads, cells))
        description = row.get("Charge Description") or ""
        if not description:
            continue
        charges.append(Charge(offense_date=parse_us_date(row.get("Offense Date")), description=description,
                              statute=(row.get("Florida Statute") or "").strip() or None, arrest_date=None,
                              agency=agency, control_number=report))
    return charges


class BrevardBecaAdapter(ClerkAdapter):
    def __init__(self, page: Page, county, captcha):
        super().__init__(page, county, captcha)
        self._results: Optional[Page] = None
        self._case_pages: dict[str, Page] = {}

    # --- 1. Búsqueda --------------------------------------------------------

    def search(self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]) -> list[CaseCandidate]:
        page = self.page
        self._open_form()
        page.fill("input[name='fname']", portal_text(query.first_name))
        page.fill("input[name='mname']",
                  portal_text(query.middle_name) if query.middle_name and query.use_middle_name_in_search else "")
        page.fill("input[name='lname']", portal_text(query.last_name))
        # MM/DD/YYYY. Sin fechas queda lo del portal (el último año).
        if date_from:
            page.fill("input[name='fDate']", f"{date_from:%m/%d/%Y}")
        if date_to:
            page.fill("input[name='tDate']", f"{date_to:%m/%d/%Y}")
        wanted = {self.county.case_type_codes.get(t, t) for t in query.case_types}
        page.evaluate("""(codes) => document.querySelectorAll("input[name='CatChk']").forEach(box => {
            box.checked = codes.includes(box.value); })""", sorted(wanted))
        log.info("Buscando %s, %s entre %s y %s (tipos %s)", query.last_name, query.first_name, date_from, date_to,
                 sorted(wanted) or "todos")
        results = self._submit()
        data = results.evaluate(_READ_RESULTS_JS)
        rows = data.get("rows") or []
        if data.get("found") is not None and data["found"] > len(rows):
            log.warning("El portal informa %s casos y leí %d", data["found"], len(rows))
        candidates, skipped = [], []
        for row in rows:
            cells = row["cells"] + [""] * 9
            number, party, kind = cells[0], cells[2], case_code(cells[0])
            if not party.upper().startswith("DEFENDANT") or (wanted and kind not in wanted):
                skipped.append(f"{number} ({kind or cells[4]}, {party})")
                continue
            candidates.append(CaseCandidate(
                case_number=number, defendant=defendant_from_party(cells[1], query.last_name),
                case_type=kind or cells[4], status=cells[6], dob=parse_us_date(cells[7]),
                filed_date=parse_us_date(cells[5]), detail_ref=urljoin(results.url, row["href"])))
        log.info("%d caso(s) en resultados, %d como acusado del tipo buscado", len(rows), len(candidates))
        if skipped:
            log.info("Fuera (otro tipo de caso o de parte): %s", "; ".join(skipped[:12]))
        return candidates

    def _open_form(self) -> None:
        """El aviso se acepta una vez por sesión; después, el formulario de búsqueda por nombre."""
        page = self.page
        page.goto(urljoin(BECA_URL, SEARCH_PATH), wait_until="load")
        if not page.locator("input[name='lname']").count():
            page.goto(urljoin(BECA_URL, SPLASH_PATH), wait_until="load")
            accept = page.locator("input[name='RadioChk'][value='Yes']")
            if accept.count():
                log.info("Acepto el aviso del portal")
                accept.first.check()
                with page.expect_navigation(wait_until="load", timeout=30_000):
                    page.locator("form[name='myform'] input[type='submit'], input[name='Submit']").first.click()
            # "General Public Court Records Search" solo lleva a name_search.cfm.
            page.goto(urljoin(BECA_URL, SEARCH_PATH), wait_until="load")
        try:
            page.wait_for_selector("input[name='lname']", timeout=20_000)
        except PlaywrightTimeout:
            self._save_debug(page, "formulario")
            raise SearchError("The court portal's search form didn't load (the disclaimer page may have "
                              "changed). Try again in a few minutes.") from None

    def _submit(self) -> Page:
        """El formulario abre los resultados en una ventana nueva (target=_blank)."""
        page = self.page
        if self._results is not None and not self._results.is_closed():
            self._results.close()
        button = page.locator("form[name='searchform'] input[type='submit'][value='Search']").first
        try:
            with page.context.expect_page(timeout=15_000) as popup:
                button.click()
            results = popup.value
        except PlaywrightTimeout:
            results = page                  # por si un día los muestra en la misma pestaña
        self._results = results
        try:
            results.wait_for_load_state("load", timeout=45_000)
            results.wait_for_function(
                "() => /Cases? Returned|no (cases|records)|not found/i.test((document.body || {}).innerText || '')"
                " || !!document.querySelector('a[href*=\"all_results.cfm\"]')", timeout=45_000, polling=500)
        except PlaywrightTimeout:
            self._save_debug(results, "busqueda")
            raise SearchError("The court portal didn't return search results in time. Try again in a few "
                              "minutes.") from None
        results.wait_for_timeout(300)
        return results

    # --- 2. Detalle del caso ------------------------------------------------

    def open_case(self, candidate: CaseCandidate) -> CaseDetails:
        page = self._case_page(candidate)
        data = page.evaluate(_CASE_JS)
        candidate.dob = candidate.dob or parse_us_date(data.get("dob"))
        agency = (data.get("agency") or {}).get("name") or None
        report = report_from_agency((data.get("agency") or {}).get("detail"))
        details = CaseDetails(candidate=candidate, charges=charges_from_table(data.get("charges"), agency, report),
                              docket=docket_from_rows(data.get("docket") or [], page.url), source_url=page.url,
                              agency_report_number=report)
        log.info("%s: %d cargo(s), %d entrada(s) de docket, agencia %s, reporte %s", candidate.case_number,
                 len(details.charges), len(details.docket), agency or "-", report or "-")
        if details.charges:
            log.info("Cargos (%s): %s", candidate.case_number,
                     " | ".join(f"{c.description} ({c.statute})" for c in details.charges))
        return details

    def _case_page(self, candidate: CaseCandidate) -> Page:
        """Cada caso en una pestaña propia, como cuando la persona abre el enlace de los resultados."""
        page = self._case_pages.get(candidate.case_number)
        if page is not None and not page.is_closed():
            return page
        if len(self._case_pages) >= OPEN_CASE_PAGES:
            old = self._case_pages.pop(next(iter(self._case_pages)))
            if not old.is_closed():
                old.close()
        page = self.page.context.new_page()
        referer = self._results.url if self._results is not None and not self._results.is_closed() else None
        page.goto(candidate.detail_ref, wait_until="load", referer=referer)
        try:
            page.wait_for_function("() => !!document.querySelector('a[name=\"docket\"]') || "
                                   "/Case Information/i.test((document.body || {}).innerText || '')", timeout=20_000)
        except PlaywrightTimeout:
            self._save_debug(page, "caso")
            page.close()
            raise SearchError(f"The court portal didn't open case {candidate.case_number}") from None
        self._case_pages[candidate.case_number] = page
        return page

    # --- 3. Descarga ----------------------------------------------------------

    def download(self, details: CaseDetails, entry: DocketEntry, dest_dir: Path) -> Path:
        if not entry.doc_ref:
            raise DownloadError(f"'{entry.description}' has no viewable document")
        page = self._case_page(details.candidate)
        try:
            status, content_type, body = self.fetch(page, entry.doc_ref, referer=page.url)
        except PlaywrightError as exc:
            raise DownloadError(f"The portal didn't hand over '{entry.description}': {str(exc).splitlines()[0]}") \
                from None
        if status >= 400 or not is_pdf(body):
            raise DownloadError(f"The document came back as {content_type or 'nothing'} (HTTP {status}), not a PDF")
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / safe_filename(f"{details.candidate.case_number} - {entry.description}.pdf")
        path.write_bytes(body)
        log.info("PDF guardado: %s (%d KB)", path, len(body) // 1024)
        return path

    # --- Utilidades -----------------------------------------------------------

    def _save_debug(self, page: Page, name: str) -> None:
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            (self.debug_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(self.debug_dir / f"{name}.png"), full_page=True)
        except Exception as exc:
            log.info("No pude guardar el diagnóstico %s: %s", name, exc)
