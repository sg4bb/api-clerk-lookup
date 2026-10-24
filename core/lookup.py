"""Orquestador: consulta -> un único documento.

Es el mismo flujo que correrá el worker en la Fase 3; ahí solo cambian
el CaptchaHandler y el reporte de estado a Supabase.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from pathlib import Path
from dataclasses import replace
from typing import Callable, Optional, Union

from adapters import get_adapter
from counties import get_county

from .adapter import DocumentUnavailable, DownloadError, SearchError
from .browser import open_browser
from .captcha import CaptchaHandler, CaptchaTimeout, LiveViewCaptcha, ManualLocalCaptcha
from .county import CountyConfig
from .charges import categorize
from .matcher import pick_best, prelim_reasons, prelim_score, related_cases, resolve_same_incident, score_case
from .models import CaseDetails, LookupQuery, LookupResult
from .utils import last_name_variants, portal_text, today

log = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]


WARRANT_LEAD_DAYS = 30   # ver search_window


def search_window(query: LookupQuery, county: CountyConfig) -> tuple[Optional[date], Optional[date]]:
    """Rango de fechas de apertura del caso para el formulario del portal.

    - Con fecha de arresto: el caso se abre a los pocos días -> ventana corta.
    - Sin fecha de arresto: el arresto pudo ser meses después del crimen (en
      Orange, Tyrese Johnson: crimen 24/06/2023, caso abierto 20/10/2023), así
      que la ventana se estira hasta la publicación del artículo.
    """
    published_limit = query.published_date + timedelta(days=14) if query.published_date else None
    start_anchor = query.incident_date or query.arrest_date
    if not start_anchor and (query.date_from or query.date_to):
        date_to = query.date_to or today()
        if not query.arrest_date and published_limit:
            date_to = max(date_to, published_limit)
        return query.date_from, min(date_to, today())
    if not start_anchor:
        if not query.approx_date:
            return None, None
        # Solo hay una fecha aproximada (publicación): el arresto suele ser
        # anterior a la nota, así que la ventana mira sobre todo hacia atrás.
        return query.approx_date - timedelta(days=30), min(query.approx_date + timedelta(days=7), today())
    end_anchor = max(d for d in (query.incident_date, query.arrest_date) if d)
    days_before = county.window_days_before
    if not query.incident_date:
        # Solo se sabe el arresto: en los arrestos con orden el caso se abre al emitirla,
        # antes de la captura (Shaffer, Columbia: orden 19/11/2024, arresto 05/12/2024).
        days_before = max(days_before, WARRANT_LEAD_DAYS)
    date_from = start_anchor - timedelta(days=days_before)
    date_to = end_anchor + timedelta(days=county.window_days_after)
    if not query.arrest_date and published_limit:
        date_to = max(date_to, published_limit)
    return date_from, min(date_to, today())


def run_lookup(
    query: Union[LookupQuery, list[LookupQuery]],
    output_dir: Path,
    captcha: Optional[CaptchaHandler] = None,
    headless: bool = False,
    on_status: StatusCallback = lambda s: log.info("estado: %s", s),
    on_captcha_waiting=None,
    on_captcha_done=None,
) -> LookupResult:
    """Busca el caso de una persona o, con una lista, de varias en orden.

    Con varias (los arrestados que nombra el artículo) se prueban una tras
    otra en la misma sesión del navegador hasta que una da con el caso: para
    pedir el footage cualquiera sirve, es el mismo hecho.
    """
    queries = query if isinstance(query, list) else [query]
    county = get_county(queries[0].county, queries[0].state)
    adapter_cls = get_adapter(county.platform)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open_browser(headless=headless) as session:
        page = session.page
        if captcha is None:
            captcha = (LiveViewCaptcha(session.live_view_url, on_captcha_waiting, on_captcha_done,
                                       output_dir=output_dir)
                       if session.remote else ManualLocalCaptcha(on_captcha_waiting, on_captcha_done))
        adapter = adapter_cls(page, county, captcha)
        adapter.debug_dir = output_dir / "debug"
        adapter.remote = session.remote
        attempts: list[tuple[LookupQuery, LookupResult, list]] = []
        try:
            for index, person in enumerate(queries):
                if index:
                    log.info("Sin caso para %s; pruebo con la siguiente persona del artículo: %s (%d/%d)",
                             _full_name(queries[index - 1]), _full_name(person), index + 1, len(queries))
                    adapter.throttle()
                scored, result = _lookup_person(adapter, county, person, output_dir, on_status)
                attempts.append((person, result, scored))
                if result.status == "found":
                    break
            wide = _wide_search(adapter, attempts) if _wide_search_applies(attempts) else None
        except CaptchaTimeout as exc:
            return LookupResult("captcha_timeout", message=str(exc))
        except (SearchError, DownloadError) as exc:
            # Mensajes ya escritos para la persona (en inglés, se ven en la web).
            log.exception("Fallo en la consulta")
            _save_debug(page, output_dir)
            return LookupResult("error", message=str(exc))
        except Exception as exc:
            # Detalle técnico (timeouts de Playwright, etc.): queda en el log y en debug/.
            log.exception("Fallo en la consulta")
            _save_debug(page, output_dir)
            return LookupResult("error", message=(
                f"The court portal didn't respond as expected ({type(exc).__name__}). "
                "Try again in a few minutes."))
    result = _combine(attempts)
    if wide and result.status == "not_found" and not result.closest:
        result.closest = wide
    return result


# Qué resultado se entrega si se probó a más de una persona.
_RANK = {"found": 0, "no_document": 1, "ambiguous": 2, "not_found": 3}
CLOSEST_MIN = 30   # % mínimo para mostrar "el caso más cercano" cuando nada pasa MIN_SCORE
# Una ofensa a más días que esto del incidente es otro hecho, aunque los cargos se
# parezcan (Hernando, 17/10/2026: un caso de 2019 por "firearm by felon" salía
# más cerca que el home invasion de 2018 solo porque el artículo hablaba de un arma).
CLOSEST_MAX_OFFENSE_GAP = 30


def _combine(attempts: list[tuple[LookupQuery, LookupResult, list]]) -> LookupResult:
    index = min(range(len(attempts)), key=lambda i: _RANK.get(attempts[i][1].status, 9))
    result = attempts[index][1]
    result.person_index = index
    result.people_searched = [
        {"name": _full_name(q), "status": r.status, "cases_checked": len(scored)}
        for q, r, scored in attempts
    ]
    if len(attempts) > 1:
        log.info("Resultado con %s (%s)", _full_name(attempts[index][0]), result.status)
    if result.status == "not_found":
        result.closest = closest_case([s for _, _, scored in attempts for s in scored])
        if result.closest:
            log.info("Caso más cercano: %s (%d%%)", result.closest["case_number"], result.closest["score"])
    return result


def closest_case(scored: list) -> Optional[dict]:
    """El mejor caso que no alcanzó el mínimo, si vale la pena mostrarlo."""
    valid = [(m, d) for m, d in scored if not m.disqualified and m.score >= CLOSEST_MIN
             and (m.offense_gap is None or m.offense_gap <= CLOSEST_MAX_OFFENSE_GAP)]
    if not valid:
        return None
    return _closest_dict(*max(valid, key=lambda s: s[0].score))


# --- Búsqueda sin fechas (último recurso) -------------------------------------
# Si nadie dio caso en la ventana de fechas y no hay un "caso más cercano", se busca
# una vez más sin rango de fechas: la nota puede salir meses o años después del hecho,
# no decir cuándo pasó o traer la fecha mal (Patel, Madison, 23/10/2026). Lo que salga
# NUNCA es "found": se muestra como sugerencia, con la fecha de apertura a la vista,
# y solo si el nombre es exacto y los cargos coinciden.
WIDE_MAX_OPEN = 5
# Un caso abierto mucho después de la nota no es el de la nota (que ya habla del arresto).
WIDE_FILED_AFTER_PUBLISHED_DAYS = 60


def _wide_search_applies(attempts: list) -> bool:
    if not attempts or any(r.status != "not_found" for _, r, _ in attempts):
        return False
    if closest_case([s for _, _, scored in attempts for s in scored]):
        return False
    return any(categorize(q.charges) for q, _, _ in attempts)


def _wide_search(adapter, attempts: list) -> Optional[dict]:
    """Sugerencia sin límite de fechas, o None. Un fallo aquí no cambia el resultado."""
    for person, _, scored in attempts:
        if not categorize(person.charges):
            continue
        # Los que ya se abrieron para esta persona en el rango de fechas no se repiten.
        opened = {d.candidate.case_number for _, d in scored}
        try:
            suggestion = _wide_search_person(adapter, person, opened)
        except Exception as exc:
            log.warning("La búsqueda sin fechas de %s falló (%s); el resultado no cambia",
                        _full_name(person), exc)
            return None
        if suggestion:
            return suggestion
    return None


def _wide_search_person(adapter, person: LookupQuery, opened: set) -> Optional[dict]:
    query = _first_word_only(person)
    search_query = replace(query, first_name=portal_text(query.first_name),
                           middle_name=portal_text(query.middle_name) or None,
                           last_name=portal_text(query.last_name))
    log.info("Sin caso en el rango de fechas: busco a %s sin fechas (solo como sugerencia)", _full_name(person))
    adapter.throttle()
    candidates = adapter.search(search_query, None, None)
    undated = replace(query, incident_date=None, arrest_date=None, approx_date=None, date_from=None, date_to=None)
    latest = (query.published_date + timedelta(days=WIDE_FILED_AFTER_PUBLISHED_DAYS)
              if query.published_date else None)
    pool = [c for c in candidates
            if c.case_number not in opened and prelim_score(undated, c) > 0
            and not (latest and c.filed_date and c.filed_date > latest)]
    # Primero los de mejor nombre y, entre ellos, los más cercanos a la nota.
    pool.sort(key=lambda c: (prelim_score(undated, c), c.filed_date or date.min), reverse=True)
    log.info("Sin fechas: %d caso(s); abro hasta %d", len(pool), WIDE_MAX_OPEN)
    best = None
    for candidate in pool[:WIDE_MAX_OPEN]:
        adapter.throttle()
        details = adapter.open_case(candidate)
        match = score_case(query, details)
        exact = "nombre exacto" in match.reasons or any(r.startswith("coincide con un alias") for r in match.reasons)
        charges = any(r.startswith("cargos coinciden") for r in match.reasons)
        log.info("Sin fechas: %s -> %d %s", candidate.case_number, match.score, match.reasons)
        if exact and charges and not match.disqualified and (best is None or match.score > best[0].score):
            best = (match, details)
    if not best:
        log.info("Sin fechas: ningún caso con el mismo nombre y cargos parecidos")
        return None
    suggestion = _closest_dict(*best)
    suggestion["wide"] = True
    log.info("Sugerencia sin fechas: %s (abierto %s, %d%%)", suggestion["case_number"],
             suggestion["filed_date"], suggestion["score"])
    return suggestion


def _closest_dict(match, details) -> dict:
    c = details.candidate
    return {
        "case_number": c.case_number,
        "score": match.score,
        "defendant": _display_name(c.defendant),
        "case_type": re.sub(r"\s+\d+-[A-Z]$", "", (c.case_type or "").strip()) or None,
        "filed_date": c.filed_date.isoformat() if c.filed_date else None,
        "checks": match.checks,
    }


def _full_name(query: LookupQuery) -> str:
    return f"{query.first_name} {query.last_name}".strip()


def _display_name(defendant: str) -> str:
    """'COLON, TIMOTHY LUIS' -> 'Timothy Colon'; "O'BRIEN, DAVID" -> "David O'Brien"."""
    if "," not in defendant:
        return defendant.strip().title()
    last, rest = (part.strip() for part in defendant.split(",", 1))
    first = rest.split()[0] if rest.split() else ""
    return f"{first} {last}".strip().title()


def _first_word_only(query: LookupQuery) -> LookupQuery:
    """'Frederick John' -> nombre 'Frederick', segundo nombre 'John'.

    El portal busca nombres que EMPIECEN con lo escrito: "Frederick John" no
    encuentra a "COMBS, FREDERICK" (Nassau, 18/10/2026). Con la primera palabra
    sale tanto "FREDERICK" como "FREDERICK JOHN", y el resto se compara como
    segundo nombre. Una inicial ("J. Michael") se deja como está.
    """
    words = query.first_name.split()
    if len(words) < 2 or len(words[0].strip(".")) < 2:
        return query
    log.info("Busco por el primer nombre %r (%r queda como segundo nombre)", words[0], " ".join(words[1:]))
    return replace(query, first_name=words[0], middle_name=query.middle_name or " ".join(words[1:]))


def _lookup_person(adapter, county: CountyConfig, query: LookupQuery, output_dir: Path,
                   on_status: StatusCallback) -> tuple[list, LookupResult]:
    """La búsqueda de siempre para una persona: (casos puntuados, resultado)."""
    query = _first_word_only(query)
    date_from, date_to = search_window(query, county)
    on_status("searching")
    # El formulario se llena sin acentos: los expedientes no los llevan.
    # (Para comparar resultados se sigue usando `query`; el comparador ya ignora acentos.)
    search_query = replace(query, first_name=portal_text(query.first_name),
                           middle_name=portal_text(query.middle_name) or None,
                           last_name=portal_text(query.last_name))
    variants = last_name_variants(search_query.last_name)
    candidates = []
    for attempt, last_name in enumerate(variants, 1):
        if attempt > 1:
            log.info("Sin resultados; reintento con apellido %r (%d/%d, nuevo CAPTCHA)",
                     last_name, attempt, len(variants))
            adapter.throttle()
        candidates = adapter.search(replace(search_query, last_name=last_name), date_from, date_to)
        log.info("%d candidato(s) en resultados", len(candidates))
        if candidates:
            break
    if not candidates:
        return [], LookupResult("not_found", message="The portal search returned no cases"
                            + (f" (last names tried: {', '.join(variants)})" if len(variants) > 1 else ""))

    ranked = sorted(candidates, key=lambda c: prelim_score(query, c), reverse=True)
    for c in ranked:
        if prelim_score(query, c) <= 0:
            log.info("Descarto %s (%s): %s", c.case_number, c.defendant, prelim_reasons(query, c))
    ranked = [c for c in ranked if prelim_score(query, c) > 0]
    # Sin día exacto (solo mes o año) hay más homónimos posibles: se abren más casos.
    limit = county.max_candidates_to_open * (1 if (query.incident_date or query.arrest_date) else 2)
    ranked = ranked[:limit]

    scored = []
    for candidate in ranked:
        adapter.throttle()
        details = adapter.open_case(candidate)
        match = score_case(query, details)
        log.info("%s -> %d %s", candidate.case_number, match.score, match.reasons)
        scored.append((match, details))

    summary = [
        {"case_number": d.candidate.case_number, "score": m.score, "reasons": m.reasons}
        for m, d in scored
    ]
    winner, status = pick_best(scored)
    if status == "ambiguous":
        winner = resolve_same_incident(scored, lambda d: bool(adapter.find_documents(d)))
        if winner:
            status = "found"
            log.info("Los casos empatados son del mismo arresto; elijo %s",
                     winner[1].candidate.case_number)
    if not winner:
        return scored, LookupResult(status, candidates=summary,
                            message="No case on the portal matches the article closely enough" if status == "not_found"
                            else "Several cases match equally; more details are needed to pick one")

    match, details = winner
    related = related_cases(details, scored)
    if related:
        log.info("Casos del mismo arresto: %s", ", ".join(related))
    agency_number = agency_case_number(details)
    if agency_number:
        log.info("Número de reporte de la agencia (docket): %s", agency_number)
    docket = [
        {"date": e.date, "description": e.description, "downloadable": bool(e.doc_ref)}
        for e in details.docket
    ]
    documents = adapter.find_documents(details)
    support = adapter.find_support_documents(details)
    if documents:
        log.info("Documentos en %s (el primero se descarga): %s", details.candidate.case_number,
                 _describe(documents))
    if support:
        log.info("Documento de apoyo en %s: %s", details.candidate.case_number, _describe(support[:3]))
    if not documents and support:
        # Sin arrest affidavit pero con la orden ejecutada: mejor la orden que nada.
        log.info("No hay arrest affidavit; entrego el documento de apoyo como principal")
        documents, support = support, []
    if not documents:
        downloadable = [e for e in docket if e["downloadable"]]
        log.warning("Ningún documento coincide en %s. Descargables en el docket (%d de %d):",
                    details.candidate.case_number, len(downloadable), len(docket))
        for e in downloadable:
            log.warning("  %s  %s", e["date"], e["description"])
        return scored, LookupResult(
            "no_document", case_number=details.candidate.case_number,
            score=match.score, reasons=match.reasons, candidates=summary,
            message="The case exists, but its docket has no incident report or arrest affidavit to download",
            related_cases=related,
            docket=docket,
            agency_case_number=agency_number,
        )

    on_status("downloading")
    # Se prueba el siguiente documento solo si el anterior falló por otra cosa. Si el
    # portal responde "not available at this time", el servidor de documentos no está
    # entregando: insistir con los demás solo gasta minutos de navegador (Buck, Dixie,
    # 23/10/2026: un minuto por intento y nada que bajar).
    chosen, pdf_path, withheld = None, None, []
    for doc in (documents + support)[:MAX_DOWNLOAD_TRIES]:
        adapter.throttle()
        try:
            pdf_path = adapter.download(details, doc, output_dir)
            chosen = doc
            break
        except DocumentUnavailable as exc:
            log.info("El portal no entrega ahora '%s': %s; no pido los demás", doc.description, exc)
            withheld.append(doc.description)
            break
    if chosen is None:
        return scored, LookupResult(
            "no_document", case_number=details.candidate.case_number,
            score=match.score, reasons=match.reasons, candidates=summary,
            # Sumter, 14/10/2026: el mismo documento se bajó a mano a la 1 PM y a las
            # 2:40 PM el portal decía "not available" también a mano: es una caída
            # temporal del servidor de documentos, no que falte publicarlo.
            message=("The case was found, but the court portal isn't handing over its documents "
                     "right now (it answered \"not available at this time\"). This is usually a "
                     "temporary outage on the portal's side. Try again in a few hours; if it keeps "
                     "happening, request the document from the clerk with the case number."),
            related_cases=related, docket=docket, agency_case_number=agency_number,
        )
    support_doc, support_path = None, None
    pending_support = [] if chosen in support else support
    if pending_support:
        try:
            adapter.throttle()
            support_path = adapter.download(details, pending_support[0], output_dir)
            support_doc = pending_support[0].description
        except Exception as exc:
            # El principal ya está: el de apoyo no hace fallar la búsqueda.
            log.warning("No pude descargar el documento de apoyo (%s): %s", pending_support[0].description, exc)
    return scored, LookupResult(
        "found",
        case_number=details.candidate.case_number,
        document=chosen.description,
        pdf_path=pdf_path,
        support_document=support_doc,
        support_pdf_path=support_path,
        score=match.score,
        reasons=match.reasons,
        candidates=summary,
        docket=docket,
        related_cases=related,
        agency_case_number=agency_number,
    )


_AGENCY_NUMBER_RE = re.compile(r"agency case number\s*:\s*([A-Z0-9][A-Z0-9\-/]*)", re.I)
# Dixie no anota "Agency Case Number": "Agency Citation 26OFF000169 filed on 2/27/2026"
# (Reyes, 26CF000037). Solo el formato de reporte del Sheriff (..OFF..), no multas.
_AGENCY_CITATION_RE = re.compile(r"agency citation\s*:?\s*(\d{2}OFF\d+)\b", re.I)


def agency_case_number(details: CaseDetails) -> Optional[str]:
    """El número de reporte que el portal anota en el docket ("Agency Case Number: 13OFF005652").

    Si hay varios, el de la entrada más antigua: la que se cargó al abrir el caso.
    """
    for pattern in (_AGENCY_NUMBER_RE, _AGENCY_CITATION_RE):   # el de siempre tiene prioridad
        found = [(e.date or date.max, m.group(1).upper()) for e in details.docket
                 if (m := pattern.search(e.description or ""))]
        if found:
            return min(found)[1]
    return None


MAX_DOWNLOAD_TRIES = 4   # documentos que se intentan si el portal no publica los primeros


def _describe(entries) -> str:
    return "; ".join((f"#{d.number} " if d.number is not None else "") + f"{d.date or 'sin fecha'} {d.description}"
                     for d in entries[:6])


def _save_debug(page, output_dir: Path) -> None:
    """Captura y HTML de la página donde falló, para depurar juntos."""
    debug_dir = output_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(debug_dir / "error.png"), full_page=True)
        (debug_dir / "error.html").write_text(page.content(), encoding="utf-8")
        log.info("Debug guardado en %s", debug_dir)
    except Exception:
        log.warning("No se pudo guardar el debug")
