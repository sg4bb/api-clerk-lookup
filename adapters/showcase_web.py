"""Adaptador para ShowCaseWeb de equivant (Polk: showcase.polkcountyclerk.net), 14/11/2026.

Flujo mapeado con probe_polk (Chaneyfield, 53-2026-CF-007518-A000-BA), local y en Browserbase:
  1. La sesión. Al cargar, la página pide un token al Clerk con el reCAPTCHA invisible (v3)
     (POST sci/account/validate-captcha). Con un navegador sin historial Google lo rechaza (401), aun
     a mano, y sin token el portal no responde búsquedas. No se intenta pasar ese control: con la
     cuenta que da el Clerk (en .env: <CONDADO>_SHOWCASE_USERNAME / _PASSWORD) se inicia sesión con
     "Login" (sci/account/authenticate) y el token llega por ahí. Sin cuenta, se espera unos segundos
     por si el portal da la sesión pública; si no la da, se devuelve la búsqueda para hacerla a mano.
  2. El panel "Case Search": el enlace de escritorio solo abre si la sesión empezó con el reCAPTCHA
     (ng-click "i.sessionStarted && toggleSearch('case')"); la lupa de la vista móvil abre el mismo
     panel sin esa condición. Con la cuenta el panel se vuelve a armar cuando llega su configuración
     (aparecen Date of Birth, Incident Number, Arrest Date): se espera a que quede quieto antes de
     escribir y se comprueba lo escrito ("Chaneyfield" llegó como "Chane" la primera vez).
  3. La búsqueda la responde GET sci/case/search?... en JSON: número de caso, sid (para abrirlo),
     partido, tipo de corte, fecha de nacimiento, de presentación, del delito y del arresto, Booking #
     e Incident # (el número de reporte de la agencia).
  4. El caso (SPA, casedetails/<sid>): GET sci/case/<sid>/dockets (cada entrada con hasImages, vor,
     vorPending, sealed, notViewable), /charges y /arrests ("Arresting Agency" e Incident #).
  5. El documento: el clip de la fila (getImage(<posición en el docket>)) pide una clave
     (sci/docket/<sid>/docketsKey) y abre una pestaña case/pdfframe?k=<clave>; esa página pide el PDF
     con el token (POST sci/docket/document) y lo muestra. El PDF se toma de esa respuesta.
     "View On Request Pending" sale con un candado y no se abre.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from core.adapter import ClerkAdapter, DownloadError, SearchError
from core.documents import is_pdf
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
from core.utils import portal_text, safe_filename

log = logging.getLogger(__name__)

# Sin cuenta: cuánto se espera a que el portal dé la sesión pública (validate-captcha).
PUBLIC_SESSION_WAIT_S = 8
# Cuánto se espera a que la página confirme el inicio de sesión con la cuenta.
LOGIN_WAIT_S = 30
SEARCH_FIELD = "#caseSearchPanel input[ng-model='searchArgs.LastName']"

# "Arresting Agency" viene abreviada. Polk (14/11/2026): PCSO. Las demás, las de sus ciudades.
AGENCY_NAMES = {
    "PCSO": "Polk County Sheriff's Office",
    "LPD": "Lakeland Police Department",
    "WHPD": "Winter Haven Police Department",
    "BPD": "Bartow Police Department",
    "LWPD": "Lake Wales Police Department",
    "HCPD": "Haines City Police Department",
    "APD": "Auburndale Police Department",
    "DPD": "Davenport Police Department",
    "FPPD": "Fort Meade Police Department",
    "FHP": "Florida Highway Patrol",
}

TOKEN_JS = r"""() => {
  const raw = sessionStorage.getItem('accessToken');
  if (!raw) return null;
  try {
    const stored = JSON.parse(raw);
    const token = stored.accessToken || stored.access_token || '';
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return {account: payload.unique_name !== 'Public', expires: payload.exp};
  } catch (e) { return {account: false, expires: null}; }
}"""

_CASE_CODE_RE = re.compile(r"^\s*\d{2}-\d{4}-([A-Z]{2})-")


def credentials(county_key: str) -> tuple[str, str]:
    """Cuenta del Clerk en .env: POLK_SHOWCASE_USERNAME / POLK_SHOWCASE_PASSWORD."""
    prefix = county_key.upper()
    return os.getenv(f"{prefix}_SHOWCASE_USERNAME", "").strip(), os.getenv(f"{prefix}_SHOWCASE_PASSWORD", "")


def iso_date(value: Optional[str]) -> Optional[date]:
    """"2026-07-31T00:00:00" -> date(2026, 7, 31)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except ValueError:
        return None


def case_code(case_number: str, court_type: str = "") -> str:
    """"53-2026-CF-007518-A000-BA" -> "CF" (si no, el courtType del portal)."""
    match = _CASE_CODE_RE.match(case_number or "")
    return match.group(1) if match else (court_type or "").strip().upper()


def agency_name(code: Optional[str]) -> Optional[str]:
    code = (code or "").strip()
    return AGENCY_NAMES.get(code.upper(), code) or None


def candidates_from_results(rows: list[dict], wanted: set[str]) -> tuple[list[CaseCandidate], list[str], dict]:
    """Las filas de sci/case/search: solo acusado y del tipo buscado. Devuelve también lo que se
    descartó (para el registro) y lo que la fila trae del arresto, por sid."""
    candidates, skipped, extra = [], [], {}
    for row in rows:
        number = (row.get("caseNumber") or "").strip()
        kind = case_code(number, row.get("courtType"))
        party = (row.get("partyType") or "").strip()
        if party.lower() != "defendant" or (wanted and kind not in wanted):
            skipped.append(f"{number} ({kind or row.get('courtTypeDescription')}, {party})")
            continue
        given = " ".join(p for p in (row.get("firstName"), row.get("middleName")) if p and p.strip())
        defendant = f"{(row.get('lastName') or '').strip().upper()}, {given.strip().upper()}".strip(", ")
        sid = row.get("sid") or ""
        candidates.append(CaseCandidate(
            case_number=number, defendant=defendant, case_type=kind, status=row.get("caseStatus") or "",
            dob=iso_date(row.get("dob")), filed_date=iso_date(row.get("fileDate")), detail_ref=sid))
        extra[sid] = {"incident": (row.get("incidentNumber") or "").strip() or None,
                      "arrest": iso_date(row.get("arrestDate")), "offense": iso_date(row.get("offenseDate")),
                      "booking": (row.get("bookingSheetNumber") or "").strip() or None}
    return candidates, skipped, extra


def docket_from_json(rows: list[dict]) -> list[DocketEntry]:
    """sci/case/<sid>/dockets. doc_ref es la posición de la entrada en la lista (la del clip:
    getImage(<posición>)); solo la tienen las que se pueden abrir. Las demás llevan por qué no:
    vorPending -> "request" ("View On Request Pending", candado); sin imagen -> "unpublished";
    sealed -> "(SEALED)" y notViewable -> "(CONFIDENTIAL)" en la descripción, para no ofrecerlas."""
    docket = []
    for index, row in enumerate(rows):
        description = re.sub(r"\s+", " ", row.get("description") or "").strip()
        doc_ref, availability = None, None
        if row.get("sealed"):
            description += " (SEALED)"
        elif row.get("notViewable"):
            description += " (CONFIDENTIAL)"
        elif row.get("vorPending"):
            availability = "request"
        elif not row.get("hasImages"):
            availability = "unpublished"
        else:
            doc_ref = str(index)
        docket.append(DocketEntry(date=iso_date(row.get("effectiveDate")), description=description,
                                  doc_ref=doc_ref, number=row.get("docketNumber"), availability=availability))
    return docket


def charges_from_json(rows: list[dict], arrest: Optional[date], agency: Optional[str],
                      report: Optional[str]) -> list[Charge]:
    """sci/case/<sid>/charges."""
    charges = []
    for row in rows:
        description = (row.get("courtStatuteDescription") or row.get("statuteDescription")
                       or row.get("chargeDescription") or "").strip()
        if not description:
            continue
        charges.append(Charge(offense_date=iso_date(row.get("offenseDate")), description=description,
                              statute=(row.get("courtStatuteNumber") or "").strip() or None, arrest_date=arrest,
                              agency=agency, control_number=report))
    return charges


def manual_search_message(county_name: str, portal: str, query: LookupQuery, date_from: Optional[date],
                          date_to: Optional[date], why: str) -> str:
    filed = (f", cases filed {date_from:%m/%d/%Y} to {date_to:%m/%d/%Y}" if date_from and date_to else "")
    return (f"{county_name}'s court portal {why}. Search it by hand at {portal}: open \"Case Search\" and use "
            f"Last Name \"{query.last_name}\", First Name \"{query.first_name}\"{filed}. If the search panel "
            f"doesn't open, sign in or try a private window in another browser.")


class ShowCaseWebAdapter(ClerkAdapter):
    def __init__(self, page: Page, county, captcha):
        super().__init__(page, county, captcha)
        self._ready = False
        self._extra: dict[str, dict] = {}
        self._docket_json: dict[str, list[dict]] = {}

    # --- 0. Sesión ------------------------------------------------------------

    def _open_session(self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]) -> None:
        if self._ready:
            return
        page = self.page
        page.goto(self.county.base_url, wait_until="load")
        page.wait_for_timeout(1500)
        username, password = credentials(self.county.key)
        why = "didn't open a search session for the automated browser (its invisible reCAPTCHA check " \
              "turned it down)"
        if username and password:
            if self._login(username, password):
                why = None
            else:
                why = "didn't accept the Clerk account set up for this tool (check the username and password)"
        if why:
            # Sin cuenta (o si no entró): la sesión pública, si el portal la da.
            token = self._wait_token(PUBLIC_SESSION_WAIT_S)
            if not token:
                if not (username and password):
                    log.warning("Sin cuenta de %s en .env y sin sesión pública: búsqueda a mano", self.county.name)
                self._save_debug(page, "sin_sesion")
                raise SearchError(manual_search_message(self.county.name, self.county.base_url, query,
                                                        date_from, date_to, why))
            log.info("Sesión pública del portal (sin cuenta)")
        self._ready = True

    def _login(self, username: str, password: str) -> bool:
        page = self.page
        try:
            page.locator("a[ng-click*='showlogin']").first.click()
            page.wait_for_selector("#login_modal #username", state="visible", timeout=15_000)
            page.fill("#login_modal #username", username)
            page.fill("#login_modal #password", password)
            page.locator("#login_modal button[type='submit']").click()
            page.wait_for_function("() => !!document.querySelector('[ng-show=\"i.isAuthenticated\"]:not(.ng-hide)')",
                                   timeout=LOGIN_WAIT_S * 1000, polling=500)
        except PlaywrightTimeout:
            log.warning("No se confirmó el inicio de sesión en %s", self.county.name)
            return False
        token = self._wait_token(10)
        if not token or not token.get("account"):
            log.warning("Inicié sesión pero no llegó el token de la cuenta")
            return False
        log.info("Sesión iniciada con la cuenta del Clerk")
        page.wait_for_timeout(1000)
        return True

    def _wait_token(self, seconds: float) -> Optional[dict]:
        deadline = time.monotonic() + seconds
        while True:
            token = self.page.evaluate(TOKEN_JS)
            if token or time.monotonic() >= deadline:
                return token
            self.page.wait_for_timeout(500)

    # --- 1. Búsqueda --------------------------------------------------------

    def search(self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]) -> list[CaseCandidate]:
        self._open_session(query, date_from, date_to)
        page = self.page
        self._open_panel()
        values = {"LastName": portal_text(query.last_name), "FirstName": portal_text(query.first_name),
                  "MiddleName": portal_text(query.middle_name) if query.middle_name
                  and query.use_middle_name_in_search else "",
                  "FromFileDate": f"{date_from:%m/%d/%Y}" if date_from else "",
                  "ToFileDate": f"{date_to:%m/%d/%Y}" if date_to else ""}
        self._fill_panel(values)
        wanted = {self.county.case_type_codes.get(t, t) for t in query.case_types}
        log.info("Buscando %s, %s entre %s y %s (tipos %s)", query.last_name, query.first_name, date_from, date_to,
                 sorted(wanted) or "todos")
        try:
            with page.expect_response(lambda r: "/sci/case/search" in r.url, timeout=45_000) as answer:
                page.locator("#caseSearchPanel button[type='submit']").click()
            rows = answer.value.json() or []
        except PlaywrightTimeout:
            self._save_debug(page, "busqueda")
            raise SearchError("The court portal didn't answer the search in time. Try again in a few minutes.") \
                from None
        candidates, skipped, extra = candidates_from_results(rows, wanted)
        self._extra.update(extra)
        log.info("%d caso(s) en resultados, %d como acusado del tipo buscado", len(rows), len(candidates))
        if skipped:
            log.info("Fuera (otro tipo de caso o de parte): %s", "; ".join(skipped[:12]))
        if rows:
            try:
                page.wait_for_selector("a[ng-click*='goToCaseDetails']", timeout=20_000)
            except PlaywrightTimeout:
                pass
        return candidates

    def _open_panel(self) -> None:
        page = self.page
        if page.locator(SEARCH_FIELD).is_visible():
            return
        page.locator("a:has-text('Case Search')").first.click()
        try:
            page.wait_for_selector(SEARCH_FIELD, state="visible", timeout=5_000)
            return
        except PlaywrightTimeout:
            pass
        # La lupa de la vista móvil (oculta en escritorio): el mismo panel, sin pedir el reCAPTCHA.
        page.locator("a.navbar-icon[ng-click*=\"toggleSearch('case')\"]").first.dispatch_event("click")
        try:
            page.wait_for_selector(SEARCH_FIELD, state="visible", timeout=15_000)
        except PlaywrightTimeout:
            self._save_debug(page, "panel")
            raise SearchError("The court portal's search panel didn't open. Try again in a few minutes.") from None

    def _fill_panel(self, values: dict[str, str]) -> None:
        page = self.page
        self._wait_panel_still()
        got: dict[str, Any] = {}
        for _ in range(3):
            for model, value in values.items():
                field = page.locator(f"#caseSearchPanel input[ng-model='searchArgs.{model}']")
                if field.count():
                    field.fill(value)
            page.wait_for_timeout(400)
            got = {m: page.locator(f"#caseSearchPanel input[ng-model='searchArgs.{m}']").input_value()
                   for m in values if page.locator(f"#caseSearchPanel input[ng-model='searchArgs.{m}']").count()}
            if all(got[m] == values[m] for m in got):
                return
            log.info("El panel no quedó como debía (%s); lo lleno otra vez", got)
            self._wait_panel_still()
        raise SearchError(f"The court portal's search panel kept changing while typing ({got}). Try again.")

    def _wait_panel_still(self, quiet_s: float = 1.2, limit_s: float = 10) -> None:
        """Espera a que el panel deje de cambiar de campos (se rearma al llegar su configuración)."""
        page = self.page
        last, since, started = page.locator("#caseSearchPanel input").count(), time.monotonic(), time.monotonic()
        while time.monotonic() - started < limit_s:
            page.wait_for_timeout(300)
            now = page.locator("#caseSearchPanel input").count()
            if now != last:
                last, since = now, time.monotonic()
            elif time.monotonic() - since >= quiet_s:
                return

    # --- 2. Detalle del caso ------------------------------------------------

    def open_case(self, candidate: CaseCandidate) -> CaseDetails:
        page = self.page
        self._back_to_results()
        link = f"a[ng-click*='goToCaseDetails'][title='{candidate.case_number}']"
        self._show_row(link)
        try:
            with page.expect_response(lambda r: re.search(r"/sci/case/[^/]+/dockets", r.url) is not None,
                                      timeout=30_000) as answer:
                page.locator(link).first.click()
            docket_rows = answer.value.json() or []
        except PlaywrightTimeout:
            self._save_debug(page, "caso")
            raise SearchError(f"The court portal didn't open case {candidate.case_number}") from None
        self._docket_json[candidate.case_number] = docket_rows
        page.wait_for_selector("button[ng-click*='getImage'], .dataTables_wrapper", timeout=20_000)
        extra = self._extra.get(candidate.detail_ref, {})
        arrests = self._tab_json("Arrests", r"/sci/case/[^/]+/arrests")
        charges_json = self._tab_json("Charges", r"/sci/case/[^/]+/charges")
        first_arrest = arrests[0] if arrests else {}
        agency = agency_name(first_arrest.get("agencyName"))
        report = (first_arrest.get("incidentNumber") or "").strip() or extra.get("incident")
        arrest = iso_date(first_arrest.get("arrestOffenseDate")) or extra.get("arrest")
        details = CaseDetails(candidate=candidate, charges=charges_from_json(charges_json, arrest, agency, report),
                              docket=docket_from_json(docket_rows), source_url=page.url,
                              agency_report_number=report)
        log.info("%s: %d cargo(s), %d entrada(s) de docket, arresto %s, agencia %s, reporte %s",
                 candidate.case_number, len(details.charges), len(details.docket), arrest, agency or "-",
                 report or "-")
        if details.charges:
            log.info("Cargos (%s): %s", candidate.case_number,
                     " | ".join(f"{c.description} ({c.statute})" for c in details.charges))
        self._tab("Dockets")
        return details

    def _tab_json(self, name: str, pattern: str) -> list[dict]:
        """Abre la pestaña y lee lo que el portal le manda. Si no responde, la pestaña queda vacía."""
        try:
            with self.page.expect_response(lambda r: re.search(pattern, r.url) is not None, timeout=15_000) as answer:
                self._tab(name)
            data = answer.value.json()
            return data if isinstance(data, list) else []
        except (PlaywrightTimeout, PlaywrightError, ValueError):
            log.info("La pestaña %s no respondió", name)
            return []

    def _tab(self, name: str) -> None:
        self.page.locator(".scrollmenu a", has_text=name).first.click()
        self.page.wait_for_timeout(600)

    def _back_to_results(self) -> None:
        page = self.page
        if "/casedetails/" not in page.url:
            return
        page.locator("a[ng-click='goBack()']").first.click()
        page.wait_for_selector("a[ng-click*='goToCaseDetails']", timeout=20_000)

    def _show_row(self, selector: str, pages: int = 12) -> None:
        """Las tablas van de a 25 filas: pasa de página hasta que la fila esté a la vista."""
        page = self.page
        for _ in range(pages):
            if page.locator(selector).first.is_visible():
                return
            following = page.locator(".dataTables_paginate li.paginate_button.active + li.paginate_button a").first
            if not following.count():
                return
            following.click()
            page.wait_for_timeout(500)

    # --- 3. Descarga ----------------------------------------------------------

    def download(self, details: CaseDetails, entry: DocketEntry, dest_dir: Path) -> Path:
        if entry.doc_ref is None:
            raise DownloadError(f"'{entry.description}' has no viewable document")
        page = self.page
        if "/casedetails/" not in page.url or details.source_url != page.url:
            self.open_case(details.candidate)
        self._tab("Dockets")
        button = f"button[ng-click='getImage({entry.doc_ref})']"
        self._show_row(button)
        context = page.context
        try:
            with context.expect_event(
                    "response", lambda r: "/sci/docket/document" in r.url and r.request.method == "POST",
                    timeout=self.county.download_timeout_s * 1000) as answer:
                with context.expect_page(timeout=30_000):
                    page.locator(button).first.click()
            response = answer.value
            body = response.body()
            status, content_type = response.status, response.headers.get("content-type", "")
        except PlaywrightTimeout:
            raise DownloadError(f"The portal didn't hand over '{entry.description}' in time") from None
        except PlaywrightError as exc:
            raise DownloadError(f"The portal didn't hand over '{entry.description}': {str(exc).splitlines()[0]}") \
                from None
        finally:
            for other in context.pages:
                if other is not page and "pdfframe" in other.url:
                    other.close()
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
