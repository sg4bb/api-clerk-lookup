"""Registro de la cárcel de Escambia (SmartWEB Jail de SmartCOP, inmatelookup.myescambia.com).

Se usa solo cuando Cloudflare bloquea el portal de la corte de Escambia (10/11/2026). No da el
arrest report, pero sí lo que hace falta para pedir el video: la fecha y el número del booking, la
agencia y su número de reporte ("ECSO23OFF013609 (ESCAMBIA COUNTY SHERIFF'S OFFICE)") y, a veces,
el número de caso de la corte ("2023 CF 3056"). Es una página pública del condado, sin CAPTCHA.

Formulario (ASP.NET, probe_jail 10/11/2026): txbLastName, txbFirstName, txbMiddleName, tbBeginDate y
tbEndDate (fecha de booking), TypeSearch (0 en la cárcel, 1 liberados, 2 ambos), SearchSortOption
(1 = fecha de booking), SearchOrderOption (0 = ascendente) y el botón btnSumit (postback). Muestra 100
y el resto con "Load more results" (LoadMoreButton). Cada booking es un tr.InmateRecordRow seguido de
sus tablas de retenciones y de cargos (td[aria-label^='Case Number'] = "número (agencia)").
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from core.charges import categorize
from core.models import LookupQuery
from core.utils import portal_text, surname_key, today

log = logging.getLogger(__name__)

JAIL_URL = "https://inmatelookup.myescambia.com/smartwebclient/jail.aspx"
MAX_LOAD_MORE = 3
NAV_TIMEOUT_MS = 60_000
# Bookings que cuentan para un hecho: desde 2 días antes (la fecha del artículo puede ser la del
# reporte) hasta los días de la ventana del condado (arresto con orden, semanas después).
BEFORE_DAYS = 2
# Tolerancia por cargos: hasta este número de días entre la fecha de la nota y el booking.
NEAR_DAYS = 400

_READ_JS = r"""
() => {
  const text = el => el ? el.innerText.replace(/ /g, ' ').replace(/\s+/g, ' ').trim() : '';
  const out = [];
  let current = null;
  const table = document.querySelector('#JailInfo > table');
  if (!table) return out;
  for (const tr of table.querySelectorAll(':scope > tbody > tr')) {
    if (tr.classList.contains('InmateRecordRow')) {
      const head = text(tr.querySelector('.SearchHeader'));
      current = {name: head.replace(/\(.*$/, '').trim(), info: {}, charges: []};
      for (const row of tr.querySelectorAll('td[valign=top] table tbody tr')) {
        const cells = Array.from(row.querySelectorAll(':scope > td')).map(text);
        for (let i = 0; i + 1 < cells.length; i += 2) {
          const key = cells[i].replace(/:$/, '');
          if (key) current.info[key] = cells[i + 1];
        }
      }
      out.push(current);
      continue;
    }
    if (!current) continue;
    const charges = tr.querySelector("table[aria-label='Charges for the Subject']");
    if (!charges) continue;
    let last = null;
    for (const row of charges.querySelectorAll('tr')) {
      const ref = row.querySelector("td[aria-label^='Case Number']");
      if (ref) {
        last = {ref: text(ref), statute: text(row.querySelector("td[aria-label^='Statute']")),
                charge: text(row.querySelector("td[aria-label^='Charge']")), more: ''};
        current.charges.push(last);
      } else if (last && row.querySelector("td[id^='expanded']")) {
        last.more = text(row.querySelector("td[id^='expanded']"));
      }
    }
  }
  return out;
}
"""

_COUNT_JS = "() => document.querySelectorAll('#JailInfo tr.InmateRecordRow').length"
_COURT_CASE_RE = re.compile(r"^\d{2,4}\s*[A-Z]{2}\s*\d+")
_WARRANT_RE = re.compile(r"\d{2}WAR\d+", re.I)
_NOT_AN_AGENCY = {"OTHER AGENCY", "COURT TRANSFERS"}


@dataclass
class JailCharge:
    number: str               # "ECSO23OFF013609", "2023 CF 3056", "21WAR010641"
    agency: str               # "ESCAMBIA COUNTY SHERIFF'S OFFICE"
    charge: str               # "HOMICIDE"
    statute: str = ""

    @property
    def is_court_case(self) -> bool:
        return bool(_COURT_CASE_RE.match(self.number))

    @property
    def is_report(self) -> bool:
        """Número de reporte de la agencia (lo que sirve para pedir el video)."""
        return bool(self.number) and not self.is_court_case and not _WARRANT_RE.search(self.number) \
            and self.agency.upper() not in _NOT_AN_AGENCY


@dataclass
class JailBooking:
    name: str                 # "RODRIGUEZ, MICHAEL ANDREW"
    booking_number: str
    booked: Optional[datetime]
    released: Optional[datetime] = None
    age: Optional[int] = None
    charges: list[JailCharge] = field(default_factory=list)
    # Elegido por los cargos y no por la fecha: la fecha que da la nota, para avisarlo en el mensaje.
    story_date: Optional[date] = None
    # Tipos de cargo de la nota (core.charges.categorize): con varios casos o reportes en el mismo
    # booking, se prefiere el del cargo de la nota. Lo pone pick_booking.
    wanted: frozenset = frozenset()

    def _ordered(self) -> list[JailCharge]:
        """Los cargos con los de la nota primero (Brown, 11/11/2026: fraude en 2026 CF 3560 y el
        homicidio en 2026 CF 3561, en el mismo booking)."""
        if not self.wanted:
            return list(self.charges)
        return sorted(self.charges, key=lambda c: 0 if categorize([c.charge]) & self.wanted else 1)

    @property
    def report(self) -> Optional[JailCharge]:
        return next((c for c in self._ordered() if c.is_report), None)

    @property
    def court_case(self) -> Optional[str]:
        return next((c.number for c in self._ordered() if c.is_court_case), None)

    @property
    def other_cases(self) -> list[str]:
        """Los demás números de caso del booking (de otros cargos)."""
        main = self.court_case
        return list(dict.fromkeys(c.number for c in self.charges if c.is_court_case and c.number != main))


def _when(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def _split_ref(text: str) -> tuple[str, str]:
    """"ECSO23OFF013609 (ESCAMBIA COUNTY SHERIFF'S OFFICE)" -> número y agencia."""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", text or "")
    return (m.group(1).strip(), m.group(2).strip()) if m else ((text or "").strip(), "")


def bookings_from_rows(rows: list[dict]) -> list[JailBooking]:
    bookings = []
    for row in rows:
        info = row.get("info", {})
        age = re.sub(r"\D", "", info.get("Age On Booking Date", ""))
        charges = []
        for c in row.get("charges", []):
            number, agency = _split_ref(c.get("ref", ""))
            charge = " ".join(p for p in (c.get("charge", ""), c.get("more", "")) if p).strip()
            charges.append(JailCharge(number, agency, charge, c.get("statute", "")))
        bookings.append(JailBooking(name=row.get("name", ""), booking_number=info.get("Booking No", ""),
                                    booked=_when(info.get("Booking Date", "")),
                                    released=_when(info.get("Released", "")),
                                    age=int(age) if age else None, charges=charges))
    return bookings


def _why_not(query: LookupQuery, booking: JailBooking) -> Optional[str]:
    """Por qué el booking no es de la persona de la nota, o None si puede serlo."""
    last, _, rest = booking.name.partition(",")
    if surname_key(last) != surname_key(query.last_name):
        return "otro apellido"
    given = rest.split()
    if not given or given[0].lower() != (query.first_name or "").strip().lower():
        return "otro nombre"
    if query.middle_name and len(given) > 1 and given[1][0].lower() != query.middle_name.strip()[0].lower():
        return f"otro segundo nombre ({given[1]})"
    # "Age On Booking Date" no siempre es la edad de ese día: a veces es una edad posterior
    # (Ketchup, booking del 23/08/2023 con 27, que cumplió en 2025; Rodriguez, booking de 2011 con 39).
    # Nunca es menor que la de ese día ni mayor que la de hoy: solo se descarta fuera de ese rango.
    as_of = query.age_as_of or query.published_date
    if query.age is not None and booking.age is not None and as_of and booking.booked:
        at_booking = query.age - (as_of - booking.booked.date()).days / 365.25
        now = query.age + (today() - as_of).days / 365.25
        if not at_booking - 1.5 <= booking.age <= now + 1.5:
            return f"edad {booking.age} en el booking, la nota da entre {at_booking:.0f} (ese día) y {now:.0f} (hoy)"
    return None


def _same_person(query: LookupQuery, booking: JailBooking) -> bool:
    return _why_not(query, booking) is None


def pick_booking(bookings: list[JailBooking], query: LookupQuery, window_days_after: int) -> Optional[JailBooking]:
    """El booking del hecho: el primero desde la fecha del arresto o del incidente (2 días antes,
    por si la nota fecha el reporte) hasta la ventana del condado. Sin fecha, solo si hay uno.

    Tolerancia (solo Escambia, porque el portal de la corte puede estar bloqueado): si no hay
    ninguno en esa ventana, el de la misma persona con cargos del mismo tipo que los de la nota más
    cercano a la fecha, hasta NEAR_DAYS antes o después y nunca después de la nota. Queda marcado
    (story_date) para avisar en el mensaje. Blackburn (10/11/2026): la nota fecha el crimen el
    29/10/2023 pero fue en 2022; su booking por homicidio es del 01/11/2022.
    Cada booking descartado queda en el log con el motivo."""
    reference = query.arrest_date or query.incident_date
    start = reference - timedelta(days=BEFORE_DAYS) if reference else None
    end = reference + timedelta(days=window_days_after) if reference else None
    person, mine = [], []
    for b in bookings:
        reason = "sin fecha de booking" if not b.booked else _why_not(query, b)
        if not reason:
            person.append(b)
        if not reason and reference and not (start <= b.booked.date() <= end):
            reason = f"fuera de la ventana del hecho ({start:%m/%d/%Y} a {end:%m/%d/%Y})"
        log.info("  booking %s %s %s (%s años): %s", b.booking_number,
                 f"{b.booked:%m/%d/%Y}" if b.booked else "?", b.name, b.age, reason or "puede ser")
        if not reason:
            mine.append(b)
    wanted = frozenset(categorize(query.charges or []))
    if reference is None:
        chosen = mine[0] if len(mine) == 1 else None
    elif mine:
        chosen = min(mine, key=lambda b: b.booked)
    else:
        chosen = _near_with_same_charges(person, query, reference)
    if chosen:
        chosen.wanted = wanted
    return chosen


def _near_with_same_charges(person: list[JailBooking], query: LookupQuery, reference: date) -> Optional[JailBooking]:
    wanted = categorize(query.charges or [])
    if not wanted:
        return None
    last_day = query.published_date or today()
    near = []
    for b in person:
        day = b.booked.date()
        shared = categorize([c.charge for c in b.charges]) & wanted
        if shared and abs((day - reference).days) <= NEAR_DAYS and day <= last_day:
            near.append(b)
    if not near:
        return None
    chosen = min(near, key=lambda b: abs((b.booked.date() - reference).days))
    chosen.story_date = reference
    log.info("Ninguno en la ventana; elijo por los cargos el más cercano: %s del %s (la nota dice %s)",
             chosen.booking_number, f"{chosen.booked:%m/%d/%Y}", f"{reference:%m/%d/%Y}")
    return chosen


def search_jail(page, query: LookupQuery, begin: Optional[date], end: Optional[date]) -> list[JailBooking]:
    """Busca por apellido y nombre en "Both Current And Released", ordenado por fecha de booking.
    Con fechas primero; si no sale nada, sin ellas (por si el portal las lee distinto)."""
    for dates in ((begin, end), (None, None)) if (begin or end) else ((None, None),):
        # Sin esperar "load": la página trae las fotos de los presos (100 al abrir) y en Browserbase
        # la búsqueda no terminó de cargar en 30 s (Rodriguez, 10/11/2026). Las fotos no se piden.
        page.goto(JAIL_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_selector("#txbLastName", timeout=NAV_TIMEOUT_MS)
        page.fill("#txbLastName", portal_text(query.last_name))
        page.fill("#txbFirstName", portal_text(query.first_name))
        page.fill("#tbBeginDate", f"{dates[0]:%m/%d/%Y}" if dates[0] else "")
        page.fill("#tbEndDate", f"{dates[1]:%m/%d/%Y}" if dates[1] else "")
        page.select_option("#TypeSearch", "2")
        page.select_option("#SearchSortOption", "1")
        page.select_option("#SearchOrderOption", "0")
        started = time.monotonic()
        with page.expect_navigation(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS):
            page.click("#btnSumit", no_wait_after=True)
        page.wait_for_selector("#JailInfo", state="attached", timeout=20_000)
        log.info("Cárcel de Escambia: resultados en %.0f s", time.monotonic() - started)
        page.wait_for_timeout(500)
        for _ in range(MAX_LOAD_MORE):
            more = page.locator("#LoadMoreButton")
            if not (more.count() and more.first.is_visible()):
                break
            before = page.evaluate(_COUNT_JS)
            more.first.click()
            try:
                page.wait_for_function(f"() => ({_COUNT_JS})() > {before}", timeout=15_000, polling=500)
            except PlaywrightTimeout:
                break
        bookings = [b for b in bookings_from_rows(page.evaluate(_READ_JS))
                    if surname_key(b.name.partition(",")[0]) == surname_key(query.last_name)]
        log.info("Cárcel de Escambia: %d booking(s) de %s, %s%s", len(bookings), query.last_name, query.first_name,
                 f" entre {dates[0]} y {dates[1]}" if dates[0] or dates[1] else " (sin fechas)")
        if bookings:
            return bookings
    return []


def _skip_images(route) -> None:
    if route.request.resource_type == "image":
        route.abort()
    else:
        route.fallback()          # sigue su curso (o el siguiente manejador)


def jail_fallback(context, query: LookupQuery, begin: Optional[date], end: Optional[date],
                  window_days_after: int, save_debug=None) -> Optional[JailBooking]:
    """El booking del hecho en el registro de la cárcel, en una pestaña aparte. None si no se pudo.
    save_debug(tab, nombre): guarda captura y HTML de la pestaña si algo falla."""
    tab = context.new_page()
    try:
        tab.route("**/*", _skip_images)
    except PlaywrightError:
        pass
    try:
        bookings = search_jail(tab, query, begin, end)
        booking = pick_booking(bookings, query, window_days_after)
        if booking is None and bookings and (begin or end):
            # Con fechas salió algo, pero no el hecho: sin fechas, por si el booking quedó fuera del
            # rango de la búsqueda (la tolerancia por cargos mira hasta NEAR_DAYS de la fecha).
            log.info("Repito la búsqueda en la cárcel sin fechas")
            booking = pick_booking(search_jail(tab, query, None, None), query, window_days_after)
        if booking:
            report = booking.report
            log.info("Booking del hecho: %s (%s), reporte %s (%s), caso %s", booking.booking_number,
                     booking.booked, report.number if report else None, report.agency if report else None,
                     booking.court_case)
        else:
            log.info("La cárcel de Escambia no tiene un booking de %s %s cerca de la fecha del hecho",
                     query.first_name, query.last_name)
        return booking
    except (PlaywrightError, PlaywrightTimeout) as exc:
        log.warning("No pude consultar la cárcel de Escambia: %s", str(exc).splitlines()[0])
        if save_debug:
            try:
                save_debug(tab, "carcel_error")
            except Exception:
                pass
        return None
    finally:
        tab.close()
