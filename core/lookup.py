"""Orquestador: consulta -> un único documento.

Es el mismo flujo que correrá el worker en la Fase 3; ahí solo cambian
el CaptchaHandler y el reporte de estado a Supabase.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from dataclasses import replace
from typing import Callable, Optional

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
    query: LookupQuery,
    output_dir: Path,
    captcha: Optional[CaptchaHandler] = None,
    headless: bool = False,
    on_status: StatusCallback = lambda s: log.info("estado: %s", s),
    on_captcha_waiting=None,
    on_captcha_done=None,
) -> LookupResult:
    county = get_county(query.county, query.state)
    adapter_cls = get_adapter(county.platform)
    date_from, date_to = search_window(query, county)
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
        try:
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
                return LookupResult("not_found", message="The portal search returned no cases"
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
                return LookupResult(status, candidates=summary,
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
                log.info("No hay arrest affidavit; entrego la orden de captura como documento principal")
                documents, support = support, []
            if not documents:
                downloadable = [e for e in docket if e["downloadable"]]
                log.warning("Ningún documento coincide en %s. Descargables en el docket (%d de %d):",
                            details.candidate.case_number, len(downloadable), len(docket))
                for e in downloadable:
                    log.warning("  %s  %s", e["date"], e["description"])
                return LookupResult(
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
                return LookupResult(
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
            return LookupResult(
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
