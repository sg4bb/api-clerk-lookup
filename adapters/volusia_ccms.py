"""Adaptador para el Case Inquiry de Volusia (ccms.clerk.org), 11/11/2026.

Flujo mapeado con probe_volusia (Schmalz-Riedt, 2026 105517 CFDL):
  1. ccms.clerk.org pide aceptar un aviso ("Accept", postback) una vez por sesión; después,
     inquiry.aspx: Last / First / Middle, "Filing Date From/To" (MM/DD/YYYY) y "Submit"
     (#Content1_lb_submit). El formulario lleva reCAPTCHA v3 (invisible, sin casilla): en las
     pruebas no frenó nada, ni con el Chrome local ni en Browserbase. No se toca.
  2. Resultados en #CaseGrid: a#CaseNum (caseCR_detail.aspx?d=...&t=CF, en pestaña nueva), estilo
     "STATE OF FLORIDA v. JOHN ERIC SCHMALZ RIEDT" (nombre en orden natural), tipo (CF/MM/CT...),
     estado, fecha de presentación. Sin fecha de nacimiento: sale en el caso.
  3. El caso tiene pestañas que se cargan con postback (una por vez): Summary (DOB, Filing Date,
     Offense Date), Offense (cargos: descripción, estatuto, nivel), Custody (fecha y hora del
     arresto, booking) y Docket (Date | DIN | Docket Type | Description | View). El ícono de View
     dice si el documento se ve: file.png / file_r.png (sí), file_archive.png ("Viewable on
     Request", se pide) y file_x.png (confidencial).
  4. El documento: viewDocNoQueryString('<id>') abre "viewDocument.aspx", que tacha datos privados
     ("Processing...", se refresca cada 3 s) y al terminar abre load_Redact.aspx con el PDF.
     load_Redact.aspx entrega el PDF UNA sola vez (el segundo pedido es una página vacía): por eso
     la apertura del visor se corta en el navegador antes de salir y el PDF se pide con fetch en
     la misma sesión (probe v3: 2 MB, %PDF, local y Browserbase).
El portal no muestra la agencia ni su número de caso: están en el CHARGING AFFIDAVIT (escaneado).
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

START_URL = "https://ccms.clerk.org/"
INQUIRY_PATH = "inquiry.aspx"
# Tiempo para que el visor termine de tachar el documento y pida el PDF.
REDACTION_WAIT_S = 150
# Cuántos detalles de caso quedan abiertos (para descargar sin volver a buscarlos).
OPEN_CASE_PAGES = 4

_READ_RESULTS_JS = r"""
() => {
  const text = el => el ? el.innerText.replace(/ /g, ' ').replace(/\s+/g, ' ').trim() : '';
  const found = ((document.querySelector('#Content1_lblResults') || {}).innerText || '').match(/(\d+)/);
  const rows = Array.from(document.querySelectorAll('#CaseGrid tr')).filter(tr => tr.querySelector('a#CaseNum'));
  return {
    found: found ? parseInt(found[1], 10) : null,
    rows: rows.map(tr => {
      const cells = Array.from(tr.querySelectorAll(':scope > td'));
      const link = tr.querySelector('a#CaseNum');
      return {
        case_number: text(link), href: link.getAttribute('href'),
        style: text(cells[1]), party_type: text(tr.querySelector('#partytype')),
        case_type: text(tr.querySelector('#casetype')), status: text(tr.querySelector('#status')),
        status_title: (tr.querySelector('#status') || {}).title || '',
        filed: text(cells[8]),
      };
    }),
  };
}
"""

_SUMMARY_JS = r"""
() => {
  const t = id => { const el = document.getElementById(id); return el ? el.innerText.replace(/\s+/g, ' ').trim() : ''; };
  return {name: t('tabContainer_summary_name'), dob: t('tabContainer_summary_DOBvalue'),
          filed: t('tabContainer_summary_filingDate'), offense: t('tabContainer_summary_offenseDate'),
          case_type: t('caseTypelbl'), status: t('status')};
}
"""

# Tabla de una pestaña: los encabezados y las filas de datos (sin las de filtros).
_GRID_JS = r"""
(id) => {
  const table = document.getElementById(id);
  if (!table) return null;
  const text = el => el ? el.innerText.replace(/ /g, ' ').replace(/\s+/g, ' ').trim() : '';
  const heads = Array.from(table.querySelectorAll('tr.headerstyle th')).map(text).filter(Boolean);
  return Array.from(table.querySelectorAll('tr')).filter(tr => !tr.classList.contains('headerstyle'))
    .map(tr => {
      const cells = Array.from(tr.querySelectorAll(':scope > td'));
      const view = tr.querySelector("a[onclick*='viewDoc'], a[onclick*='viewVor']");
      const icon = tr.querySelector("img[src*='file']");
      return {cells: cells.map(text), heads,
              onclick: view ? view.getAttribute('onclick') : '', icon: icon ? icon.getAttribute('src') : ''};
    })
    .filter(r => r.cells.some(Boolean));
}
"""

_DOC_ID_RE = re.compile(r"viewDocNoQueryString\('([^']+)'")
_STYLE_RE = re.compile(r"^\s*STATE OF FLORIDA\s+v\.?\s*", re.I)


def defendant_from_style(style: str, last_name: str) -> str:
    """"STATE OF FLORIDA v. JOHN ERIC SCHMALZ RIEDT" -> "SCHMALZ RIEDT, JOHN ERIC" (el apellido que se
    buscó dice dónde termina el nombre; si no aparece al final, el apellido es la última palabra)."""
    name = _STYLE_RE.sub("", style or "").strip()
    words = name.split()
    want = surname_key(last_name)
    for start in range(1, len(words)):
        if surname_key(" ".join(words[start:])) == want:
            return f"{' '.join(words[start:])}, {' '.join(words[:start])}"
    if len(words) > 1:
        return f"{words[-1]}, {' '.join(words[:-1])}"
    return name


def docket_from_rows(rows: list[dict]) -> list[DocketEntry]:
    """Date | DIN | Docket Type | Description | View. Solo los documentos que se ven tienen doc_ref (el
    id del documento); "Viewable on Request" queda sin doc_ref (availability "request": se pide al
    Clerk), el renglón sin ícono también (availability "unpublished": listado pero aún sin publicar;
    Stripling, 11/11/2026, el charging affidavit a los 11 días) y los confidenciales llevan
    "(CONFIDENTIAL)" en la descripción, para no ofrecerlos."""
    docket = []
    for row in rows:
        cells = row.get("cells", [])
        if len(cells) < 3:
            continue
        kind, extra = cells[2], (cells[3] if len(cells) > 3 else "")
        description = f"{kind} - {extra}" if extra else kind
        icon = (row.get("icon") or "").lower()
        match = _DOC_ID_RE.search(row.get("onclick") or "")
        doc_ref = match.group(1) if match and "file_x" not in icon and "archive" not in icon else None
        if "file_x" in icon:
            description += " (CONFIDENTIAL)"
        availability = "request" if "archive" in icon else "unpublished" if not icon else None
        din = re.match(r"\s*(\d+)", cells[1] or "")
        docket.append(DocketEntry(date=parse_us_date(cells[0]), description=description, doc_ref=doc_ref,
                                  number=int(din.group(1)) if din else None, availability=availability))
    return docket


def charges_from_rows(rows: list[dict], offense: Optional[date], arrest: Optional[date]) -> list[Charge]:
    """Pestaña Offense: | Seq | Count | Citation | Description | Statute | Lvl/Deg | Plea | ..."""
    charges = []
    for row in rows:
        by_head = dict(zip(row.get("heads", []), row.get("cells", [])[1:] if row.get("cells", [""])[0] == ""
                           else row.get("cells", [])))
        description = by_head.get("Description") or ""
        if not description:
            continue
        charges.append(Charge(offense_date=offense, description=description, statute=by_head.get("Statute") or None,
                              arrest_date=arrest, agency=None, control_number=None))
    return charges


class VolusiaCcmsAdapter(ClerkAdapter):
    def __init__(self, page: Page, county, captcha):
        super().__init__(page, county, captcha)
        self._case_pages: dict[str, Page] = {}

    # --- 1. Búsqueda --------------------------------------------------------

    def search(self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]) -> list[CaseCandidate]:
        page = self.page
        self._open_form()
        page.fill("#Content1_lastName_tx", portal_text(query.last_name))
        page.fill("#Content1_firstName_tx", portal_text(query.first_name))
        page.fill("#Content1_middleName_tx",
                  portal_text(query.middle_name) if query.middle_name and query.use_middle_name_in_search else "")
        # El formulario pide MM/DD/YYYY (con ceros y el año completo).
        page.fill("#Content1_fileStartDate", f"{date_from:%m/%d/%Y}" if date_from else "")
        page.fill("#Content1_fileEndDate", f"{date_to:%m/%d/%Y}" if date_to else "")
        log.info("Buscando %s, %s entre %s y %s (tipos %s)", query.last_name, query.first_name, date_from, date_to,
                 query.case_types)
        page.locator("#Content1_lb_submit").first.click()
        try:
            page.wait_for_function(
                "() => /Records Found|No records|not found/i.test((document.querySelector('#Content1_lblResults')"
                " || {}).innerText || '') || !!document.querySelector('#CaseGrid')", timeout=45_000, polling=500)
        except PlaywrightTimeout:
            self._raise_search_problem()
        page.wait_for_timeout(500)
        data = page.evaluate(_READ_RESULTS_JS)
        rows = data.get("rows") or []
        if data.get("found") and data["found"] > len(rows):
            log.warning("El portal informa %s resultados y leí %d (¿paginado?)", data["found"], len(rows))
        wanted = {self.county.case_type_codes.get(t, t) for t in query.case_types}
        candidates, skipped = [], []
        for row in rows:
            if row["party_type"].lower() != "defendant" or (wanted and row["case_type"] not in wanted):
                skipped.append(f"{row['case_number']} ({row['case_type']}, {row['party_type']})")
                continue
            candidates.append(CaseCandidate(
                case_number=row["case_number"], defendant=defendant_from_style(row["style"], query.last_name),
                case_type=row["case_type"], status=row["status_title"] or row["status"], dob=None,
                filed_date=parse_us_date(row["filed"]), detail_ref=urljoin(START_URL, row["href"])))
        log.info("%d fila(s) en resultados, %d caso(s) como acusado del tipo buscado", len(rows), len(candidates))
        if skipped:
            log.info("Fuera (otro tipo de caso o de parte): %s", "; ".join(skipped[:12]))
        return candidates

    def _open_form(self) -> None:
        """El aviso se acepta una vez por sesión; después, el formulario de búsqueda."""
        page = self.page
        page.goto(self.url(INQUIRY_PATH), wait_until="load")
        if not page.locator("#Content1_lastName_tx").count():
            page.goto(START_URL, wait_until="load")
            accept = page.locator("a[href*='button_accept'], input[value='Accept']")
            if accept.count():
                log.info("Acepto el aviso del portal")
                with page.expect_navigation(wait_until="load", timeout=30_000):
                    accept.first.click()
            if "inquiry.aspx" not in page.url:
                page.goto(self.url(INQUIRY_PATH), wait_until="load")
        try:
            page.wait_for_selector("#Content1_lastName_tx", timeout=20_000)
        except PlaywrightTimeout:
            self._save_debug(page, "formulario")
            raise SearchError("The court portal's search form didn't load (the disclaimer page may have "
                              "changed). Try again in a few minutes.") from None

    def _raise_search_problem(self) -> None:
        page = self.page
        captcha = page.locator("#Content1_CaptchaCodeTextBox")
        self._save_debug(page, "busqueda")
        if captcha.count() and captcha.first.is_visible():
            raise SearchError("The court portal asked for a CAPTCHA on this search, which isn't supported for "
                              "Volusia yet. Try again later.")
        raise SearchError("The court portal didn't return search results in time. Try again in a few minutes.")

    # --- 2. Detalle del caso ------------------------------------------------

    def open_case(self, candidate: CaseCandidate) -> CaseDetails:
        page = self._case_page(candidate)
        summary = page.evaluate(_SUMMARY_JS)
        candidate.dob = candidate.dob or parse_us_date(summary.get("dob"))
        offense = parse_us_date(summary.get("offense"))
        custody = self._tab(page, "custody", "tabContainer_custody_GridCustody") or []
        arrest = next((parse_us_date(r["cells"][0]) for r in custody if r.get("cells")), None)
        offense_rows = self._tab(page, "offense", "tabContainer_offense_GridOffense") or []
        docket_rows = self._tab(page, "docket", "tabContainer_docket_GridDocket")
        details = CaseDetails(candidate=candidate, charges=charges_from_rows(offense_rows, offense, arrest),
                              docket=docket_from_rows(docket_rows or []), source_url=page.url,
                              docket_unavailable=docket_rows is None)
        log.info("%s: %d cargo(s), %d entrada(s) de docket, ofensa %s, arresto %s", candidate.case_number,
                 len(details.charges), len(details.docket), offense, arrest)
        if details.charges:
            log.info("Cargos (%s): %s", candidate.case_number,
                     " | ".join(f"{c.description} ({c.statute})" for c in details.charges))
        return details

    def _case_page(self, candidate: CaseCandidate) -> Page:
        """El detalle en una pestaña propia, como cuando la persona hace clic en el número de caso."""
        page = self._case_pages.get(candidate.case_number)
        if page is not None and not page.is_closed():
            return page
        if len(self._case_pages) >= OPEN_CASE_PAGES:
            oldest = next(iter(self._case_pages))
            old = self._case_pages.pop(oldest)
            if not old.is_closed():
                old.close()
        page = self.page.context.new_page()
        page.goto(candidate.detail_ref, wait_until="load", referer=self.url(INQUIRY_PATH))
        try:
            page.wait_for_selector("#caseNum", timeout=20_000)
        except PlaywrightTimeout:
            self._save_debug(page, "caso")
            page.close()
            raise SearchError(f"The court portal didn't open case {candidate.case_number}") from None
        self._case_pages[candidate.case_number] = page
        return page

    def _tab(self, page: Page, name: str, grid_id: str) -> Optional[list[dict]]:
        """Abre una pestaña del caso (postback) y lee su tabla. None si no cargó."""
        tab = page.locator(f"#__tab_tabContainer_{name}")
        if not tab.count():
            return None
        try:
            tab.first.click()
            page.wait_for_function(f"() => !!document.getElementById('{grid_id}') || "
                                   f"/No .* Returned/i.test((document.getElementById('tabContainer_{name}') || {{}})"
                                   f".innerText || '')", timeout=25_000, polling=500)
        except (PlaywrightTimeout, PlaywrightError) as exc:
            log.warning("La pestaña %s no cargó: %s", name, str(exc).splitlines()[0])
            return None
        page.wait_for_timeout(300)
        return page.evaluate(_GRID_JS, grid_id) or []

    # --- 3. Descarga ----------------------------------------------------------

    def download(self, details: CaseDetails, entry: DocketEntry, dest_dir: Path) -> Path:
        if not entry.doc_ref:
            raise DownloadError(f"'{entry.description}' has no viewable document")
        page = self._case_page(details.candidate)
        link = page.locator(f"a[onclick*=\"viewDocNoQueryString('{entry.doc_ref}'\"]")
        if not link.count():
            # La pestaña Docket no es la que quedó abierta: se vuelve a abrir.
            self._tab(page, "docket", "tabContainer_docket_GridDocket")
            link = page.locator(f"a[onclick*=\"viewDocNoQueryString('{entry.doc_ref}'\"]")
        if not link.count():
            raise DownloadError(f"The link for '{entry.description}' isn't in the docket")
        body = self._redacted_pdf(page, link.first)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / safe_filename(f"{details.candidate.case_number} - {entry.description}.pdf")
        path.write_bytes(body)
        log.info("PDF guardado: %s (%d KB)", path, len(body) // 1024)
        return path

    def _redacted_pdf(self, page: Page, link) -> bytes:
        """Clic en el documento; cuando el visor va a abrir load_Redact.aspx, esa apertura se corta en
        el navegador (no llega al portal) y el PDF se pide con fetch en la misma sesión: el portal lo
        entrega una sola vez."""
        context = page.context
        ready: list[str] = []

        def hold_viewer(route) -> None:
            if route.request.resource_type == "document":
                ready.append(route.request.url)
                route.abort()
            else:
                route.fallback()

        context.route("**/load_Redact.aspx*", hold_viewer)
        viewer = None
        try:
            try:
                with context.expect_page(timeout=15_000) as popup:
                    link.click()
                viewer = popup.value
            except PlaywrightTimeout:
                log.info("El documento no abrió una ventana nueva; espero el PDF igual")
            log.info("Esperando a que el portal tache los datos privados del documento")
            waited = 0.0
            while not ready and waited < REDACTION_WAIT_S:
                page.wait_for_timeout(1000)
                waited += 1
            if not ready:
                if viewer is not None:
                    self._save_debug(viewer, "visor")
                raise DownloadError(f"The portal didn't finish preparing the document in {REDACTION_WAIT_S} s")
            log.info("Documento listo en %.0f s; lo descargo", waited)
            status, content_type, body = self.fetch(page, ready[0], referer=page.url)
        finally:
            try:
                context.unroute("**/load_Redact.aspx*", hold_viewer)
            except PlaywrightError:
                pass
            if viewer is not None and not viewer.is_closed():
                viewer.close()
        if status >= 400 or not is_pdf(body):
            raise DownloadError(f"The document came back as {content_type or 'nothing'} (HTTP {status}), not a PDF")
        return body

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
