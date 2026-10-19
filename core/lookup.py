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
from .matcher import pick_best, prelim_reasons, prelim_score, related_cases, resolve_same_incident, score_case
from .models import LookupQuery, LookupResult
from .utils import last_name_variants, portal_text, today

log = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]


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
    date_from = start_anchor - timedelta(days=county.window_days_before)
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
    return _combine(attempts)


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
    match, details = max(valid, key=lambda s: s[0].score)
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
        )

    on_status("downloading")
    # Si el portal no publica todavía un documento, se prueba el siguiente
    # (otros affidavits y, al final, la orden ejecutada).
    chosen, pdf_path, withheld = None, None, []
    for doc in (documents + support)[:MAX_DOWNLOAD_TRIES]:
        adapter.throttle()
        try:
            pdf_path = adapter.download(details, doc, output_dir)
            chosen = doc
            break
        except DocumentUnavailable as exc:
            log.info("El portal no entrega ahora '%s': %s", doc.description, exc)
            withheld.append(doc.description)
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
            related_cases=related, docket=docket,
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
    )


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
