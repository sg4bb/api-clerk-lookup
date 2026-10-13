"""Adaptador para Civitek OCRS (Online Court Records Search).

Lo usan 32 condados de Florida con el mismo portal; cada uno se distingue
por su código en la URL (Marion = 42): civitekflorida.com/ocrs/county/NN/.

Flujo mapeado el 2026-10-12 en Marion con el caso 26CF003891AX
(probe_civitek.py, navegador remoto):
  1. /ocrs/county/NN/ -> "Public" -> aviso legal -> /ocrs/app/search.xhtml
  2. Formulario JSF (PrimeFaces): lastname, fname, fromDate/toDate
     (MM/DD/YYYY) y casillas ps_court con valores CF, MM, CT...
     Cloudflare Turnstile antes de "Search": en el navegador remoto aprobó
     solo; si no lo hace, se pide a la persona como con reCAPTCHA.
  3. partySearchResults.xhtml: una fila por persona; al desplegarla muestra
     sus casos (UCN, FILE DATE, CASE TYPE, STATUS) con un enlace GET a
     caseinformation.xhtml?query=... (depende de la sesión).
  4. caseinformation.xhtml: cargos (form:chargeDetailsTable), partes y
     docket (form:docketDataTable1), paginado de a 10: se elige "ALL".
  5. Documento: enlace directo docketimage.pdf?query=...&pages=N (PDF).
Sin cuenta, los documentos con ícono se abren directo. Los resultados NO
traen fecha de nacimiento.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from core.adapter import ClerkAdapter, DownloadError, SearchError
from core.captcha import passes_on_its_own
from core.documents import is_pdf
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
from core.utils import parse_us_date, safe_filename

log = logging.getLogger(__name__)

SEARCH_URL = "/ocrs/app/search.xhtml"
TURNSTILE_AUTO_S = 20      # cuánto se espera a que Cloudflare apruebe solo
MAX_PEOPLE = 15            # personas a desplegar en los resultados

# Personas de los resultados y, de las desplegadas, sus casos.
_READ_RESULTS_JS = r"""
() => {
  const text = e => (e ? (e.innerText || e.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const rows = [...document.querySelectorAll('tbody[id$="partySearchResultsTable_data"] > tr[data-ri]')];
  return rows.map(tr => {
    const bold = tr.querySelector('span[style*="bold"]');
    const name = text(bold || tr.cells[2]);
    const exp = tr.nextElementSibling;
    if (!exp || !exp.classList.contains('ui-expanded-row-content')) return {name, expanded: false, cases: []};
    const cases = [];
    exp.querySelectorAll('a[href*="caseinformation"]').forEach(a => {
      const row = a.closest('tr');
      const grid = a.closest('table.ui-panelgrid');
      const cells = row ? [...row.cells].map(text) : [];
      const local = a.parentElement.querySelector('span');
      const charges = [];
      if (grid) {
        grid.querySelectorAll('table').forEach(t => {
          const head = text(t.querySelector('thead'));
          if (!/statute text/i.test(head)) return;
          t.querySelectorAll('tbody tr').forEach(r => charges.push([...r.cells].map(text)));
        });
      }
      cases.push({ucn: text(a), local: text(local), href: a.getAttribute('href'),
                  file_date: cells[1] || '', case_type: cells[2] || '', status: cells[3] || '', charges});
    });
    return {name, expanded: true, cases};
  });
}
"""

# Cargos (con su detalle desplegado) y docket del caso. Las tablas "reflow" de
# PrimeFaces repiten el título de la columna dentro de cada celda: se quita.
_READ_CASE_JS = r"""
() => {
  const clean = c => {
    if (!c) return '';
    const x = c.cloneNode(true);
    x.querySelectorAll('.ui-column-title').forEach(e => e.remove());
    return (x.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const heads = id => [...document.querySelectorAll('[id="' + id + '"] thead th')]
    .map(h => (h.textContent || '').replace(/\s+/g, ' ').trim());
  const body = id => document.querySelector('[id="' + id + '_data"]');

  const charges = [];
  const cb = body('form:chargeDetailsTable');
  if (cb) {
    let last = null;
    [...cb.children].forEach(tr => {
      if (tr.classList.contains('ui-expanded-row-content')) {
        if (last) last.detail = clean(tr);
      } else if (tr.hasAttribute('data-ri')) {
        last = {cells: [...tr.cells].map(clean), detail: ''};
        charges.push(last);
      }
    });
  }
  const docket = [];
  const db = body('form:docketDataTable1');
  if (db) {
    [...db.querySelectorAll(':scope > tr[data-ri]')].forEach(tr => {
      const a = tr.querySelector('a[href*="docketimage"]');
      docket.push({cells: [...tr.cells].map(clean), href: a ? a.getAttribute('href') : null});
    });
  }
  return {chargeHeads: heads('form:chargeDetailsTable'), charges,
          docketHeads: heads('form:docketDataTable1'), docket};
}
"""

_LOCAL_NUMBER_RE = re.compile(r"\[?\s*([0-9]{2}[A-Z]{2}[0-9]{4,}[A-Z0-9]*)\s*\]?")
_DATE = r"(\d{1,2}/\d{1,2}/\d{4})"
# Detalle real (12/10/2026): "Date ActionStatute / TextOffense:09/19/2026Initial:09/19/2026Charged..."
_OFFENSE_RE = re.compile(r"offen[cs]e(?:\s*date)?\s*:?\s*" + _DATE, re.I)
# "...Prosecutor:09/03/2026Consolidated782.04.2 / ..." o "...Prosecutor:10/05/2026Filed..."
_PROSECUTOR_RE = re.compile(r"prosecutor\s*:?\s*\d{1,2}/\d{1,2}/\d{4}\s*([a-z][a-z ]*?)(?=\d|$)", re.I)
_ARREST_RE = re.compile(r"arrest\s*date\D{0,25}?" + _DATE, re.I)
_AGENCY_RE = re.compile(r"(?:arresting\s+)?agency\s*:?\s*([A-Z][A-Z0-9 .'&/-]{3,60}?)(?=\s{2,}|\s+[A-Z][a-z]|$)", re.I)


def county_entry_url(base_url: str) -> str:
    """https://www.civitekflorida.com/ocrs/county/42/ (la ficha del condado la trae)."""
    return base_url if base_url.endswith("/") else base_url + "/"


def candidates_from_results(people: list[dict]) -> list[CaseCandidate]:
    """Filas de personas desplegadas -> candidatos (uno por caso)."""
    found: list[CaseCandidate] = []
    seen: set[str] = set()
    for person in people:
        for case in person.get("cases", []):
            number = case_number(case)
            if not number or number in seen or not case.get("href"):
                continue
            seen.add(number)
            found.append(CaseCandidate(
                case_number=number,
                defendant=person.get("name", ""),
                case_type=case.get("case_type", ""),
                status=case.get("status", ""),
                dob=None,                       # Civitek no la muestra
                filed_date=parse_us_date(case.get("file_date")),
                detail_ref=case["href"],
            ))
    return found


def case_number(case: dict) -> str:
    """Número local del condado ('26CF003891AX'); si no está, el UCN."""
    match = _LOCAL_NUMBER_RE.search(case.get("local") or "")
    return match.group(1) if match else (case.get("ucn") or "").strip()


def charges_from_case(data: dict) -> list[Charge]:
    heads = [h.lower() for h in data.get("chargeHeads", [])]

    def col(cells: list[str], name: str) -> str:
        for i, h in enumerate(heads):
            if h.startswith(name) and i < len(cells):
                return cells[i]
        return ""

    charges = []
    for row in data.get("charges", []):
        cells, detail = row.get("cells", []), row.get("detail", "")
        offense = _OFFENSE_RE.search(detail)
        arrest = _ARREST_RE.search(detail)
        agency = _AGENCY_RE.search(detail)
        charges.append(Charge(
            offense_date=parse_us_date(offense.group(1)) if offense else None,
            description=col(cells, "description"),
            statute=None,
            arrest_date=parse_us_date(arrest.group(1)) if arrest else None,
            agency=agency.group(1).strip() if agency else None,
            control_number=None,
        ))
    return charges


def is_consolidated(data: dict) -> bool:
    """True si la fiscalía unió todos los cargos de este caso a otro ("Consolidated")."""
    actions = [m.group(1).strip().lower() for row in data.get("charges", [])
               for m in [_PROSECUTOR_RE.search(row.get("detail", ""))] if m]
    return bool(actions) and all(a.startswith("consolidated") for a in actions)


def docket_from_case(data: dict) -> list[DocketEntry]:
    heads = [h.lower() for h in data.get("docketHeads", [])]
    i_date = next((i for i, h in enumerate(heads) if "date" in h), 2)
    i_desc = next((i for i, h in enumerate(heads) if "description" in h), 3)
    i_num = next((i for i, h in enumerate(heads) if h.startswith("doc")), None)
    entries = []
    for row in data.get("docket", []):
        cells = row.get("cells", [])
        number = re.sub(r"\D", "", cells[i_num]) if i_num is not None and i_num < len(cells) else ""
        entries.append(DocketEntry(
            date=parse_us_date(cells[i_date]) if i_date < len(cells) else None,
            description=cells[i_desc] if i_desc < len(cells) else "",
            doc_ref=row.get("href"),
            number=int(number) if number else None,
        ))
    return entries


_FORM_STATE_JS = """() => ({
  last: document.querySelector("[name='form:search_tab:lastname']").value,
  first: document.querySelector("[name='form:search_tab:fname']").value,
  types: Array.from(document.querySelectorAll("[name='form:search_tab:ps_court']:checked")).map(c => c.value),
  from: document.querySelector("[name='form:search_tab:fromDate']").value,
  to: document.querySelector("[name='form:search_tab:toDate']").value,
})"""

_PERSON_ROWS = 'tbody[id$="partySearchResultsTable_data"] > tr[data-ri]'

_PERSON_EXPANDED_JS = """(i) => {
  const rows = document.querySelectorAll('tbody[id$="partySearchResultsTable_data"] > tr[data-ri]');
  const n = rows[i] && rows[i].nextElementSibling;
  return !!(n && n.classList.contains('ui-expanded-row-content'));
}"""

# Todo el docket a la vista: el botón "Next Page" está desactivado (o no hay paginador).
_DOCKET_COMPLETE_JS = """() => {
  const next = document.querySelector('[id="form:docketDataTable1"] .ui-paginator-next');
  return !next || next.classList.contains('ui-state-disabled');
}"""

LASTNAME = "input[name='form:search_tab:lastname']"
PUBLIC_RE = re.compile(r"^\s*public\s*$", re.I)
AGREE_RE = re.compile(r"i agree|accept", re.I)


def _controls(page, name: re.Pattern):
    """Botón o enlace con ese texto (PrimeFaces usa <button>; otros portales, <a>)."""
    return page.get_by_role("button", name=name).or_(page.get_by_role("link", name=name))


def _click_control(page, name: re.Pattern, timeout_ms: int) -> bool:
    target = _controls(page, name).first
    try:
        target.wait_for(timeout=timeout_ms)
    except PlaywrightTimeout:
        return False
    target.click()
    return True


class CivitekOcrsAdapter(ClerkAdapter):

    # --- 1. Búsqueda --------------------------------------------------------

    def search(
        self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]
    ) -> list[CaseCandidate]:
        page = self.page
        self._open_search_form()
        self._ensure_form()
        self._fill_form(query, date_from, date_to)
        log.info("Buscando %s %s entre %s y %s (tipos %s)",
                 query.first_name, query.last_name, date_from, date_to, query.case_types)

        if passes_on_its_own(page, "turnstile", TURNSTILE_AUTO_S):
            log.info("Cloudflare aprobó solo")
        else:
            log.info("Cloudflare no aprobó solo; se pide a la persona")
            self.captcha.solve(page, timeout_s=self.county.captcha_timeout_s, kind="turnstile")

        page.locator("form#form").get_by_role("button", name="Search", exact=True).click()
        page.wait_for_load_state("domcontentloaded")
        try:
            page.wait_for_function(
                "() => document.querySelector('tbody[id$=\"partySearchResultsTable_data\"]')"
                " || /no matches found/i.test(document.body.innerText)"
                " || /noresults=true/.test(location.href)", timeout=30_000)
        except PlaywrightTimeout:
            message = self._page_message() or "no results table"
            raise SearchError(f"The portal returned no results: {message}") from None

        if "noresults=true" in page.url or not page.locator('tbody[id$="partySearchResultsTable_data"]').count():
            log.info("El portal no encontró coincidencias")
            return []

        missing = self._expand_people()
        people = page.evaluate(_READ_RESULTS_JS) or []
        candidates = candidates_from_results(people)
        log.info("%d persona(s), %d caso(s) en resultados", len(people), len(candidates))
        if missing and not candidates:
            # No es "no encontrado": el portal no mostró los casos. Se corta con
            # error (y queda el debug de la página) para poder reintentar.
            raise SearchError("The court portal found matching people but didn't show their cases. "
                              "Try again in a few minutes.")
        if len(people) > MAX_PEOPLE:
            log.warning("Hay %d personas en los resultados; solo se revisan las primeras %d",
                        len(people), MAX_PEOPLE)
        return candidates

    def _open_search_form(self, from_start: bool = False) -> None:
        """Condado -> "Public" -> aviso legal ("I Agree") -> formulario.

        En el portal "Public" e "I Agree" son <button> de PrimeFaces (no
        enlaces), y cada uno carga una página nueva. En un reintento (otra
        forma del apellido), el formulario ya está abierto tras "No matches
        found": se reusa en vez de volver a entrar, porque con la sesión
        abierta la página del condado recarga el formulario y, con el portal
        lento, se perdía (12/10/2026, 23:10).
        """
        page = self.page
        if not from_start and "/app/search" in page.url and page.locator(LASTNAME).count():
            log.info("Uso el formulario que ya está abierto")
            return
        page.goto(county_entry_url(self.county.base_url), wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("load", timeout=30_000)
        except PlaywrightTimeout:
            pass
        if page.locator(LASTNAME).count():
            return                       # la sesión ya estaba dentro
        if not _click_control(page, PUBLIC_RE, 15_000):
            raise SearchError("The court portal didn't show its Public access option") from None
        agree_or_form = page.locator(LASTNAME).or_(_controls(page, AGREE_RE))
        try:
            agree_or_form.first.wait_for(timeout=20_000)
        except PlaywrightTimeout:
            raise SearchError("The court portal didn't open after choosing Public access") from None
        if not page.locator(LASTNAME).count():
            _click_control(page, AGREE_RE, 5_000)
        # Si el formulario tarda, _ensure_form le da más tiempo y, si hace falta,
        # entra de nuevo una vez antes de rendirse.

    def _ensure_form(self) -> None:
        """El formulario tiene que estar a la vista antes de escribir.

        Si el portal recargó la página en el último momento, se le da más
        tiempo; si aun así no está, se entra de nuevo desde el principio, una vez.
        """
        try:
            self.page.wait_for_selector(LASTNAME, state="visible", timeout=45_000)
            return
        except PlaywrightTimeout:
            log.info("El formulario no apareció; entro de nuevo al portal")
        self._open_search_form(from_start=True)
        try:
            self.page.wait_for_selector(LASTNAME, state="visible", timeout=45_000)
        except PlaywrightTimeout:
            raise SearchError("The court portal didn't open its search form") from None

    def _fill_form(self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]) -> None:
        page = self.page
        page.fill(LASTNAME, query.last_name)
        page.fill("input[name='form:search_tab:fname']", query.first_name)
        for code in query.case_types:
            value = self.county.case_type_codes.get(code)
            if value:
                self._tick_case_type(value)
        if date_from and date_to:
            page.fill("input[name='form:search_tab:fromDate']", f"{date_from:%m/%d/%Y}")
            page.fill("input[name='form:search_tab:toDate']", f"{date_to:%m/%d/%Y}")
        log.info("Formulario: %s", page.evaluate(_FORM_STATE_JS))

    def _tick_case_type(self, value: str) -> None:
        """Marca un tipo de caso (CF, MM...).

        PrimeFaces esconde la casilla real fuera de la pantalla y dibuja un
        recuadro (.ui-chkbox-box) encima: el clic va al recuadro. Si aun así
        no queda marcada, se marca la casilla real, que es la que se envía.
        """
        box = self.page.locator(f"input[name='form:search_tab:ps_court'][value='{value}']").first
        if not box.count():
            log.warning("El portal no tiene el tipo de caso %s", value)
            return
        if box.is_checked():
            return
        drawn = box.locator("xpath=ancestor::div[contains(@class,'ui-chkbox')][1]").locator(".ui-chkbox-box")
        try:
            drawn.click(timeout=5_000)
        except PlaywrightTimeout:
            pass
        if not box.is_checked():
            box.evaluate("el => { el.checked = true; el.dispatchEvent(new Event('change', {bubbles: true})); }")
        if not box.is_checked():
            raise SearchError(f"Couldn't select the case type {value} on the court portal")

    def _expand_people(self) -> list[int]:
        """Despliega cada persona para que aparezcan sus casos.

        Devuelve las personas (desde 0) que no se pudieron desplegar.
        El portal dibuja la lista y responde a cada clic con una petición
        aparte; a veces tarda o ignora el clic. Por eso: se espera a que la
        lista deje de crecer, se despliega cada persona y las que fallaron se
        revisan otra vez (pudo llegar tarde) antes de un único reintento.
        """
        rows = self.page.locator(_PERSON_ROWS)
        total, stable_since = rows.count(), time.monotonic()
        while time.monotonic() - stable_since < 1.5:
            self.page.wait_for_timeout(300)
            now = rows.count()
            if now != total:
                total, stable_since = now, time.monotonic()
        total = min(total, MAX_PEOPLE)
        log.info("Despliego %d persona(s)", total)

        pending = [i for i in range(total) if not self._expand_person(i, 15_000, retry=False)]
        if pending:
            self.page.wait_for_timeout(2_000)
            pending = [i for i in pending if not self.page.evaluate(_PERSON_EXPANDED_JS, i)]
        if pending:
            log.info("Reintento desplegar persona(s) %s", [i + 1 for i in pending])
            pending = [i for i in pending if not self._expand_person(i, 15_000, retry=True)]
        for i in pending:
            log.warning("La persona %d no se desplegó: %s", i + 1, self._person_name(i))
        return pending

    def _expand_person(self, i: int, timeout_ms: int, retry: bool) -> bool:
        if self.page.evaluate(_PERSON_EXPANDED_JS, i):
            return True
        toggler = self.page.locator(_PERSON_ROWS).nth(i).locator(".ui-row-toggler").first
        if not toggler.count():
            log.info("La persona %d no tiene casos para desplegar", i + 1)
            return True
        opening = "ui-icon-circle-triangle-s" in (toggler.get_attribute("class") or "")
        try:
            if opening and retry:
                # El portal la marcó como abierta pero no llegó el contenido: un
                # clic la cierra y otro la vuelve a pedir.
                toggler.click(timeout=10_000)
                self.page.wait_for_timeout(1_000)
                toggler.click(timeout=10_000)
            elif not opening:
                toggler.click(timeout=10_000)
            # opening y primer intento: ya la pidió; otro clic la cerraría, se espera.
        except PlaywrightTimeout:
            return False
        try:
            self.page.wait_for_function(_PERSON_EXPANDED_JS, arg=i, timeout=timeout_ms)
            return True
        except PlaywrightTimeout:
            return False

    def _person_name(self, i: int) -> str:
        try:
            return " ".join(self.page.locator(_PERSON_ROWS).nth(i).inner_text(timeout=2_000).split())[:80]
        except Exception:
            return "?"

    def _page_message(self) -> str:
        loc = self.page.locator(".ui-messages-error, .ui-growl-message, .ui-message-error")
        return loc.first.inner_text().strip() if loc.count() else ""

    # --- 2. Detalle del caso ------------------------------------------------
    # Se abre en una pestaña aparte (comparte la sesión) para no perder la
    # página de resultados cuando hay varios candidatos.

    def open_case(self, candidate: CaseCandidate) -> CaseDetails:
        url = urljoin(self.page.url, candidate.detail_ref)
        tab = self.page.context.new_page()
        try:
            tab.goto(url, wait_until="domcontentloaded")
            tab.wait_for_selector('[id="form:docketDataTable1"]', timeout=25_000)
            self._expand_charges(tab)
            self._show_all_docket(tab)
            data = tab.evaluate(_READ_CASE_JS)
            details = CaseDetails(candidate=candidate, charges=charges_from_case(data),
                                  docket=docket_from_case(data), source_url=tab.url,
                                  consolidated=is_consolidated(data))
            log.info("%s: %d cargo(s), %d entrada(s) de docket%s", candidate.case_number,
                     len(details.charges), len(details.docket),
                     " (consolidado en otro caso)" if details.consolidated else "")
            details_text = " | ".join(c.get("detail", "")[:300] for c in data.get("charges", []) if c.get("detail"))
            if details_text:
                log.info("Detalle de cargos (%s): %s", candidate.case_number, details_text[:900])
            return details
        except PlaywrightTimeout:
            raise SearchError(f"The case page for {candidate.case_number} did not load") from None
        finally:
            tab.close()

    @staticmethod
    def _expand_charges(tab: Page) -> None:
        """"Expand Charges" muestra el detalle de cada cargo (fechas, agencia si las hay)."""
        button = tab.locator("#openCharges")
        if not button.count():
            return
        try:
            button.click()
            tab.wait_for_function(
                "() => document.querySelector('[id=\"form:chargeDetailsTable_data\"] .ui-expanded-row-content')",
                timeout=8_000)
        except Exception:
            log.info("No se pudo desplegar el detalle de los cargos; sigo sin él")

    @staticmethod
    def _show_all_docket(tab: Page) -> None:
        """El docket muestra 10 entradas por página: se elige "ALL".

        Se comprueba que de verdad cargó todo: mientras haya más páginas, el
        botón "Next Page" está activo. Si sigue activo, se intenta otra vez.
        """
        select = tab.locator('[id="form:docketDataTable1"] select').filter(
            has=tab.locator("option", has_text="ALL"))
        if not select.count():
            return
        for attempt, wait_ms in enumerate((10_000, 20_000), start=1):
            if tab.evaluate(_DOCKET_COMPLETE_JS):
                return
            if attempt > 1:
                log.info("El docket no mostró todo con ALL; reintento")
            try:
                select.first.select_option(label="ALL", timeout=5_000)
            except Exception:
                # Si el portal lo esconde detrás de su menú dibujado, se cambia directo.
                log.info("Elijo ALL en el docket por script")
                select.first.evaluate(
                    "el => { const o = Array.from(el.options).find(o => o.text.trim() === 'ALL');"
                    " if (o) { el.value = o.value; el.dispatchEvent(new Event('change', {bubbles: true})); } }")
            try:
                tab.wait_for_function(_DOCKET_COMPLETE_JS, timeout=wait_ms)
                return
            except PlaywrightTimeout:
                pass
        log.warning("El docket sigue con más páginas después de elegir ALL; puede faltar algún documento")

    def find_documents(self, details: CaseDetails) -> list[DocketEntry]:
        """Arrest affidavit y similares, el primero que entró al expediente primero.

        Un caso puede tener varios arrest affidavits: el primero es el del
        arresto que abrió el caso; los siguientes suelen ser violaciones de la
        fianza o cargos nuevos (Leroy Parks III: #8 la captura, #38 y #64 desde
        la cárcel). La orden de captura ejecutada va aparte, como apoyo.
        """
        return self._in_filing_order(super().find_documents(details), self.county.document_priority)

    def find_support_documents(self, details: CaseDetails) -> list[DocketEntry]:
        return self._in_filing_order(super().find_support_documents(details), self.county.support_documents)

    @staticmethod
    def _in_filing_order(entries: list[DocketEntry], priority: list[str]) -> list[DocketEntry]:
        """Por "Doc #" (el número que el portal da al recibir cada documento),
        no por fecha: hay fechas mal cargadas (un 20/04 en agosto). Sin número,
        por tipo de documento y fecha."""
        patterns = [re.compile(p, re.I) for p in priority]

        def kind(entry: DocketEntry) -> int:
            return next((i for i, rx in enumerate(patterns) if rx.search(entry.description)), len(patterns))

        def key(entry: DocketEntry):
            if entry.number is not None:
                return (0, entry.number, 0, date.min)
            return (1, 0, kind(entry), entry.date or date.max)

        return sorted(entries, key=key)

    # --- 3. Descarga ----------------------------------------------------------

    def download(self, details: CaseDetails, entry: DocketEntry, dest_dir: Path) -> Path:
        if not entry.doc_ref:
            raise DownloadError(f"'{entry.description}' has no document link")
        url = urljoin(details.source_url or self.page.url, entry.doc_ref)
        status, content_type, body = self.fetch(self.page, url, details.source_url)
        if status >= 400:
            raise DownloadError(f"HTTP {status} while downloading '{entry.description}'")
        if not is_pdf(body):
            raise DownloadError(f"The response is not a PDF ({content_type})")
        filename = safe_filename(f"{details.candidate.case_number} - {entry.description}.pdf")
        path = dest_dir / filename
        path.write_bytes(body)
        log.info("PDF guardado: %s (%d KB)", path, len(body) // 1024)
        return path
