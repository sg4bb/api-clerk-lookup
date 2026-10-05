"""Adaptador para myeClerk (Orange County Clerk of Courts).

Flujo mapeado el 2026-09-29 con el caso 2024-CF-012151-A-O:
  1. /Cases/Search: formulario POST con reCAPTCHA v2 (checkbox), token
     antifalsificación y un campo trampa oculto (#WebRequest) que va vacío.
  2. Resultados en la tabla #caseList (DataTables); cada caso con a.caseLink.
  3. /CaseDetails?cItem=...: tabla de cargos (Offense Date / Charge / Arrest...)
     y docket en #docketTable, con a.dDescription por documento.
  4. /DocView/Doc?eCode=...: devuelve application/pdf directo.
Los links cItem y eCode dependen de la sesión: todo ocurre en una misma
sesión, justo después del CAPTCHA. Solo hay un CAPTCHA por consulta.
"""

from __future__ import annotations

import json

import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from core.adapter import ClerkAdapter, DownloadError, SearchError
from core.models import (
    CaseCandidate,
    CaseDetails,
    Charge,
    DocketEntry,
    LookupQuery,
)
from core.documents import image_from_json, images_to_pdf, is_image, is_pdf, page_count_from_json, to_pdf
from core.utils import format_short_us, parse_us_date, safe_filename

log = logging.getLogger(__name__)

SEARCH_PATH = "/Cases/Search"

# Lee todas las filas de #caseList (quita la paginación de DataTables).
_READ_RESULTS_JS = """
() => {
  const t = document.querySelector('#caseList');
  if (!t) return null;
  const $ = window.jQuery;
  if ($ && $.fn.dataTable && $.fn.dataTable.isDataTable(t)) {
    try { $(t).DataTable().page.len(-1).draw(false); } catch (e) {}
  }
  const headers = [...t.querySelectorAll('thead th')].map(h => h.innerText.trim());
  return [...t.querySelectorAll('tbody tr')]
    .filter(r => !r.querySelector('.dataTables_empty'))
    .map(r => {
      const cells = {};
      [...r.cells].forEach((c, i) => { cells[headers[i] || String(i)] = c.innerText.trim(); });
      const a = r.querySelector('a.caseLink');
      return { cells, href: a ? a.getAttribute('href') : null };
    });
}
"""

# Lee cargos y docket del detalle del caso.
_READ_DETAILS_JS = """
() => {
  const headersOf = t => [...t.querySelectorAll('th')].map(h => h.innerText.trim());
  const chargeTable = [...document.querySelectorAll('table')].find(t => {
    const h = headersOf(t);
    return h.includes('Offense Date') && h.includes('Charge') && h.includes('Arrest');
  });
  let charges = [];
  if (chargeTable) {
    const h = headersOf(chargeTable);
    charges = [...chargeTable.querySelectorAll('tbody tr')].map(r => {
      const o = {};
      [...r.cells].forEach((c, i) => { o[h[i] || String(i)] = c.innerText.trim(); });
      return o;
    });
  }
  const docket = [...document.querySelectorAll('#docketTable tbody tr')].map(r => {
    const a = r.querySelector('a.dDescription');
    return {
      date: r.cells[0] ? r.cells[0].innerText.trim() : null,
      description: (a ? a.innerText : (r.cells[1] ? r.cells[1].innerText : '')).trim(),
      href: a ? a.getAttribute('href') : null,
    };
  });
  return { charges, docket };
}
"""

# Orange usa dos formatos en la columna Description (visto 2026-09-30):
#   "STATE OF FLORIDA - VS - RODRIGUEZ, WILFREDO JR"   (CF / MM)
#   "STATE OF FLORIDA vs. RODRIGUEZ, WILFREDO PUJOL"   (CT)
_DEFENDANT_RE = re.compile(r"\bvs\.?\s*-?\s*(.+)$", re.I)
_INFO_TOTAL_RE = re.compile(r"of\s+([\d,]+)\s+entries", re.I)
PORTAL_RESULT_CAP = 500
_STATUTE_RE = re.compile(r"Statute:\s*(\S+)", re.I)
_AGENCY_RE = re.compile(r"Arresting Agency:\s*([^\n\r]+)", re.I)
_CONTROL_RE = re.compile(r"Control Number:\s*(\S+)", re.I)


class OrangeMyEClerkAdapter(ClerkAdapter):

    # --- 1. Búsqueda --------------------------------------------------------

    def search(
        self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]
    ) -> list[CaseCandidate]:
        page = self.page
        page.goto(self.url(SEARCH_PATH), wait_until="domcontentloaded")
        self._fill_form(query, date_from, date_to)
        log.info("Buscando %s %s entre %s y %s (tipos %s)",
                 query.first_name, query.last_name, date_from, date_to, query.case_types)

        self.captcha.solve(page, timeout_s=self.county.captcha_timeout_s)
        if page.input_value("#LastName").strip() != query.last_name:
            # Si la página se recargó durante el CAPTCHA, el formulario quedó vacío.
            # Rellenarlo no afecta al CAPTCHA ya resuelto.
            log.warning("El formulario se vació (¿se recargó la página?); lo vuelvo a llenar")
            self._fill_form(query, date_from, date_to)

        with page.expect_navigation(wait_until="domcontentloaded"):
            page.locator("form").get_by_role("button", name="Search", exact=True).click()

        if not page.locator("#caseList").count():
            error = page.locator(".validation-summary-errors, .field-validation-error, .alert-danger")
            message = error.first.inner_text().strip() if error.count() else "sin tabla de resultados"
            raise SearchError(f"El portal no devolvió resultados: {message}")

        rows = self._read_all_results()
        return [c for c in (self._parse_result_row(r) for r in rows) if c]

    def _fill_form(self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]) -> None:
        page = self.page
        page.wait_for_selector("#LastName")
        codes = [self.county.case_type_codes[t] for t in query.case_types
                 if t in self.county.case_type_codes]
        if codes:
            self._select_case_types(codes)
        page.fill("#FirstName", query.first_name)
        if query.middle_name and query.use_middle_name_in_search:
            page.fill("#middleName", query.middle_name)
        page.fill("#LastName", query.last_name)
        if date_from and date_to:
            page.fill("#DateFrom", format_short_us(date_from))
            page.fill("#DateTo", format_short_us(date_to))
        # Campo trampa (honeypot): si lleva algo, el portal rechaza la búsqueda.
        page.evaluate("() => { const w = document.querySelector('#WebRequest'); if (w) w.value = ''; }")

    def _select_case_types(self, codes: list[str]) -> None:
        """Marca los tipos de caso en el <select id="ct">.

        El select real está oculto detrás de un widget bootstrap-multiselect,
        así que Playwright no puede usar select_option (exige visibilidad).
        Se marcan las opciones por JS y, si el widget existe, con su propia
        API para que la UI y el formulario queden iguales.
        """
        selected = self.page.evaluate(
            """(codes) => {
              const select = document.querySelector('#ct');
              [...select.options].forEach(o => { o.selected = codes.includes(o.value); });
              const $ = window.jQuery;
              if ($ && $.fn.multiselect) {
                try {
                  $('#ct').multiselect('deselectAll', false);
                  $('#ct').multiselect('select', codes);
                  $('#ct').multiselect('refresh');
                } catch (e) {}
              }
              select.dispatchEvent(new Event('change', { bubbles: true }));
              return [...select.selectedOptions].map(o => o.value);
            }""",
            codes,
        )
        if sorted(selected) != sorted(codes):
            raise SearchError(f"No se pudieron marcar los tipos de caso {codes} (quedaron {selected})")
        log.info("Tipos de caso marcados: %s", selected)

    def _read_all_results(self) -> list[dict]:
        """Lee TODAS las filas de resultados, no solo la primera página.

        La tabla muestra 15 por página. Se elige "All" en el selector
        "Show N entries" (como haría una persona) y luego se compara lo leído
        con el total que informa el portal ("Showing 1 to X of Y entries").
        """
        page = self.page
        length = page.locator('select[name="caseList_length"]')
        if length.count():
            try:
                length.select_option(label="All")
            except Exception as exc:
                log.warning("No pude elegir 'All' en el paginado: %s", exc)
        rows = page.evaluate(_READ_RESULTS_JS) or []

        total = self._reported_total()
        if total is not None:
            if len(rows) < total:
                log.warning("Leí %d de %d resultados: el paginado no mostró todo", len(rows), total)
            if total >= PORTAL_RESULT_CAP:
                log.warning("El portal cortó en %d resultados; puede faltar el caso. "
                            "Hace falta una ventana de fechas más corta.", PORTAL_RESULT_CAP)
        log.info("%d fila(s) leída(s) de la tabla de resultados%s", len(rows),
                 f" (el portal informa {total})" if total is not None else "")
        return rows

    def _reported_total(self) -> Optional[int]:
        info = self.page.locator("#caseList_info")
        if not info.count():
            return None
        match = _INFO_TOTAL_RE.search(info.inner_text())
        return int(match.group(1).replace(",", "")) if match else None

    @staticmethod
    def _parse_result_row(row: dict) -> Optional[CaseCandidate]:
        cells = row.get("cells", {})
        href = row.get("href")
        case_number = cells.get("Case Number", "").strip()
        if not case_number or not href:
            return None
        description = cells.get("Description", "")
        match = _DEFENDANT_RE.search(description)
        return CaseCandidate(
            case_number=case_number,
            defendant=(match.group(1) if match else description).strip(),
            case_type=cells.get("Type", ""),
            status=cells.get("Status", ""),
            dob=parse_us_date(cells.get("DOB")),
            filed_date=parse_us_date(cells.get("Date")),
            detail_ref=href,
        )

    # --- 2. Detalle del caso ------------------------------------------------
    # El portal no acepta ir directo a /CaseDetails?cItem=... (redirige al
    # inicio): hay que llegar haciendo clic en el link desde los resultados.
    # Se abre en una pestaña nueva (Ctrl+clic) para conservar la página de
    # resultados cuando hay varios candidatos.

    def open_case(self, candidate: CaseCandidate) -> CaseDetails:
        link = self.page.locator("#caseList a.caseLink", has_text=candidate.case_number).first
        if not link.count():
            raise SearchError(f"No encuentro el link de {candidate.case_number} en los resultados")
        link.scroll_into_view_if_needed()

        detail_page = None
        try:
            with self.page.context.expect_page(timeout=10_000) as new_page_info:
                link.click(modifiers=["ControlOrMeta"])
            detail_page = new_page_info.value
            detail_page.wait_for_load_state("domcontentloaded")
            detail_page.wait_for_selector("#docketTable", timeout=20_000)
            return self._read_details(detail_page, candidate)
        except PlaywrightTimeout:
            log.warning("La pestaña nueva no abrió el detalle; pruebo clic normal")
        finally:
            if detail_page:
                detail_page.close()

        # Respaldo: clic normal en la misma pestaña y volver a los resultados.
        with self.page.expect_navigation(wait_until="domcontentloaded"):
            link.click()
        self.page.wait_for_selector("#docketTable", timeout=20_000)
        details = self._read_details(self.page, candidate)
        try:
            self.page.go_back(wait_until="domcontentloaded")
            self.page.wait_for_selector("#caseList", timeout=10_000)
        except PlaywrightTimeout:
            log.warning("No pude volver a los resultados; no se abrirán más candidatos")
        return details

    def _read_details(self, page: Page, candidate: CaseCandidate) -> CaseDetails:
        data = page.evaluate(_READ_DETAILS_JS)
        return CaseDetails(
            candidate=candidate,
            charges=[self._parse_charge(c) for c in data.get("charges", [])],
            docket=[
                DocketEntry(date=parse_us_date(d.get("date")),
                            # Algunas entradas traen comentarios en líneas
                            # siguientes ("Comments: Granted."): solo la 1.ª.
                            description=(d.get("description") or "").strip().splitlines()[0].strip()
                            if (d.get("description") or "").strip() else "",
                            doc_ref=d.get("href"))
                for d in data.get("docket", [])
            ],
            source_url=page.url,
        )

    @staticmethod
    def _parse_charge(cells: dict) -> Charge:
        charge_text = cells.get("Charge", "")
        arrest_text = cells.get("Arrest", "")
        statute = _STATUTE_RE.search(charge_text)
        agency = _AGENCY_RE.search(arrest_text)
        control = _CONTROL_RE.search(arrest_text)
        description = re.split(r"Statute:", charge_text, flags=re.I)[0]
        description = re.sub(r"^\s*\d+\.\s*", "", description).strip()
        return Charge(
            offense_date=parse_us_date(cells.get("Offense Date")),
            description=description,
            statute=statute.group(1) if statute else None,
            arrest_date=parse_us_date(arrest_text),
            agency=agency.group(1).strip() if agency else None,
            control_number=control.group(1) if control else None,
        )

    # --- 4. Descarga ----------------------------------------------------------
    # Casi siempre /DocView/Doc devuelve el PDF directo. Con algunos documentos
    # (visto con uno escaneado de 2021, 2021-CT-001093-A-O) devuelve en cambio
    # un visor HTML5 (DocumentViewer.axd) que NO descarga un PDF: pide cada
    # página por separado (/docs/<id>/pages/<n>, JSON) y la dibuja en un canvas.
    # Recargar no cambia nada: el visor vuelve a aparecer.

    def download(self, details: CaseDetails, entry: DocketEntry, dest_dir: Path) -> Path:
        if not entry.doc_ref:
            raise DownloadError(f"'{entry.description}' no tiene link de documento")
        url = self.url(entry.doc_ref)
        # page.request comparte las cookies de la sesión del navegador; el
        # Referer imita haber hecho clic desde el detalle del caso.
        status, content_type, body = self.fetch(self.page, url, details.source_url)
        if status >= 400:
            raise DownloadError(f"HTTP {status} al descargar '{entry.description}'")

        if not is_pdf(body):
            if "html" not in content_type.lower():
                raise DownloadError(f"La respuesta no es un PDF ni un visor ({content_type})")
            log.info("El documento abre en un visor; obtengo el PDF desde el visor")
            body = self._pdf_from_viewer(url, details.source_url, dest_dir / "debug")

        filename = safe_filename(f"{details.candidate.case_number} - {entry.description}.pdf")
        path = dest_dir / filename
        path.write_bytes(body)
        log.info("PDF guardado: %s (%d KB)", path, len(body) // 1024)
        return path

    def _pdf_from_viewer(self, url: str, referer: Optional[str], debug_dir: Path) -> bytes:
        """Obtiene el PDF desde el visor del portal, en este orden:

        1. Si el visor descarga un PDF por detrás, se captura.
        2. Botón "Download" visible del visor (si baja una imagen, se convierte).
        3. Se piden las páginas al visor en buena resolución y se arma el PDF.
        4. Si nada funciona, se guarda un diagnóstico para investigarlo.
        """
        viewer = self.page.context.new_page()
        responses = []
        viewer.on("response", lambda response: responses.append(response))
        try:
            viewer.goto(url, referer=referer, wait_until="domcontentloaded")
            for step in (self._wait_for_pdf_response, self._click_viewer_download, self._rebuild_from_pages):
                body = step(viewer, responses, debug_dir)
                if body:
                    return body
            self._save_viewer_debug(viewer, responses, debug_dir)
            raise DownloadError("El documento abre en un visor y no pude obtener el PDF; "
                                f"diagnóstico en {debug_dir}")
        finally:
            viewer.close()

    def _wait_for_pdf_response(self, viewer: Page, responses: list, debug_dir: Path) -> Optional[bytes]:
        checked, waited = 0, 0.0
        while waited <= VIEWER_WAIT_S:
            for response in responses[checked:]:
                checked += 1
                try:
                    content_type = response.headers.get("content-type", "").lower()
                    if "pdf" not in content_type and "octet-stream" not in content_type:
                        continue
                    body = response.body()
                except Exception:
                    continue
                if is_pdf(body):
                    log.info("PDF capturado del visor desde %s", response.url.split("?")[0])
                    return body
            viewer.wait_for_timeout(500)
            waited += 0.5
        log.info("El visor no descarga un PDF por detrás (dibuja las páginas)")
        return None

    def _click_viewer_download(self, viewer: Page, responses: list, debug_dir: Path) -> Optional[bytes]:
        # En la página hay dos elementos "download": uno oculto (menú colapsado)
        # y el visible de la barra (title="Download"). Se prueban solo los visibles.
        selector = ('[title="Download" i], .git-docviewer-download, [aria-label*="download" i], '
                    '[id*="download" i]')
        if self.remote:
            # En un navegador remoto la descarga queda en su disco, no en el nuestro:
            # se pasa directo a armar el PDF desde las páginas.
            return None
        for frame in viewer.frames:
            candidates = frame.locator(selector)
            for i in range(candidates.count()):
                button = candidates.nth(i)
                try:
                    if not button.is_visible():
                        continue
                    log.info("Pruebo el botón de descarga del visor")
                    with viewer.expect_download(timeout=DOWNLOAD_WAIT_S * 1000) as download_info:
                        button.click()
                    download = download_info.value
                    body = Path(download.path()).read_bytes() if download.path() else b""
                    pdf = to_pdf(body)
                    if pdf:
                        kind = "PDF" if is_pdf(body) else "imagen convertida a PDF"
                        log.info("Descarga del visor: %s (%s)", download.suggested_filename, kind)
                        return pdf
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    (debug_dir / f"viewer_download_{download.suggested_filename}").write_bytes(body)
                    log.warning("La descarga del visor no es PDF ni imagen (%s); guardada en debug",
                                download.suggested_filename)
                    return None
                except PlaywrightTimeout:
                    log.warning("El botón de descarga del visor no inició ninguna descarga")
                    return None
                except Exception as exc:
                    log.warning("Falló el botón de descarga del visor: %s", exc)
                    return None
        log.info("No encontré un botón de descarga visible en el visor")
        return None

    def _rebuild_from_pages(self, viewer: Page, responses: list, debug_dir: Path) -> Optional[bytes]:
        """Pide cada página al visor (DocumentViewer.axd) y arma el PDF."""
        base = None
        for response in responses:
            match = _VIEWER_DOC_RE.search(response.url)
            if match:
                base = match.group(1)
                break
        if not base:
            log.info("No identifiqué el documento del visor (DocumentViewer.axd)")
            return None

        count = None
        try:
            status, _, info_body = self.fetch(viewer, f"{base}/info", viewer.url)
            if status < 400:
                count = page_count_from_json(json.loads(info_body))
        except Exception as exc:
            log.warning("No pude leer la info del documento del visor: %s", exc)
        if not count:
            seen = [int(m.group(1)) for r in responses if (m := _VIEWER_PAGE_RE.search(r.url))]
            count = max(seen) if seen else None
        if not count:
            log.warning("No sé cuántas páginas tiene el documento del visor")
            return None

        log.info("Armo el PDF a partir de las %d página(s) del visor (%d dpi)", count, VIEWER_PAGE_DPI)
        images = []
        for n in range(1, count + 1):
            status, _, body = self.fetch(viewer, f"{base}/pages/{n}?dpi={VIEWER_PAGE_DPI}", viewer.url)
            body = body if status < 400 else b""
            image = body if is_image(body) else None
            if image is None:
                try:
                    image = image_from_json(json.loads(body))
                except Exception:
                    image = None
            if image is None:
                debug_dir.mkdir(parents=True, exist_ok=True)
                (debug_dir / f"viewer_page{n}.txt").write_bytes(body[:4000])
                log.warning("No encontré la imagen de la página %d en la respuesta del visor "
                            "(muestra guardada en debug)", n)
                return None
            images.append(image)
        return images_to_pdf(images, dpi=VIEWER_PAGE_DPI)

    @staticmethod
    def _save_viewer_debug(viewer: Page, responses: list, debug_dir: Path) -> None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        try:
            viewer.screenshot(path=str(debug_dir / "viewer.png"), full_page=True)
            (debug_dir / "viewer.html").write_text(viewer.content(), encoding="utf-8")
            for i, frame in enumerate(viewer.frames[1:], 1):
                (debug_dir / f"viewer_frame{i}.html").write_text(frame.content(), encoding="utf-8")
        except Exception as exc:
            log.warning("No pude guardar el HTML del visor: %s", exc)
        lines = []
        for r in responses:
            try:
                lines.append(f"{r.status}  {r.headers.get('content-type', '')[:40]:<40}  {r.request.method} {r.url}")
            except Exception:
                pass
        (debug_dir / "viewer_network.txt").write_text("\n".join(lines), encoding="utf-8")
        log.info("Diagnóstico del visor guardado en %s", debug_dir)


VIEWER_WAIT_S = 6          # espera a que el visor descargue un PDF por detrás
DOWNLOAD_WAIT_S = 45       # el botón puede tardar si el portal arma el archivo
VIEWER_PAGE_DPI = 200      # resolución al reconstruir desde las páginas del visor
_VIEWER_DOC_RE = re.compile(r"(.*/DocumentViewer\.axd/docs/[^/?]+)")
_VIEWER_PAGE_RE = re.compile(r"/DocumentViewer\.axd/docs/[^/]+/pages/(\d+)")
