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

from .browser import open_browser
from .captcha import CaptchaHandler, CaptchaTimeout, LiveViewCaptcha, ManualLocalCaptcha
from .county import CountyConfig
from .matcher import pick_best, prelim_score, related_cases, resolve_same_incident, score_case
from .models import LookupQuery, LookupResult
from .utils import last_name_variants, today

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
        adapter.remote = session.remote
        try:
            on_status("searching")
            variants = last_name_variants(query.last_name)
            candidates = []
            for attempt, last_name in enumerate(variants, 1):
                if attempt > 1:
                    log.info("Sin resultados; reintento con apellido %r (%d/%d, nuevo CAPTCHA)",
                             last_name, attempt, len(variants))
                    adapter.throttle()
                candidates = adapter.search(replace(query, last_name=last_name), date_from, date_to)
                log.info("%d candidato(s) en resultados", len(candidates))
                if candidates:
                    break
            if not candidates:
                return LookupResult("not_found", message="La búsqueda no devolvió casos"
                                    + (f" (apellidos probados: {', '.join(variants)})" if len(variants) > 1 else ""))

            ranked = sorted(candidates, key=lambda c: prelim_score(query, c), reverse=True)
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
                                    message="Ningún caso supera el umbral" if status == "not_found"
                                    else "Varios casos empatan; hace falta más información")

            match, details = winner
            related = related_cases(details, scored)
            if related:
                log.info("Casos del mismo arresto: %s", ", ".join(related))
            docket = [
                {"date": e.date, "description": e.description, "downloadable": bool(e.doc_ref)}
                for e in details.docket
            ]
            documents = adapter.find_documents(details)
            if not documents:
                downloadable = [e for e in docket if e["downloadable"]]
                log.warning("Ningún documento coincide en %s. Descargables en el docket (%d de %d):",
                            details.candidate.case_number, len(downloadable), len(docket))
                for e in downloadable:
                    log.warning("  %s  %s", e["date"], e["description"])
                return LookupResult(
                    "no_document", case_number=details.candidate.case_number,
                    score=match.score, reasons=match.reasons, candidates=summary,
                    message="El caso existe pero ningún documento del docket coincide con los buscados",
                    related_cases=related,
                    docket=docket,
                )

            on_status("downloading")
            adapter.throttle()
            pdf_path = adapter.download(details, documents[0], output_dir)
            return LookupResult(
                "found",
                case_number=details.candidate.case_number,
                document=documents[0].description,
                pdf_path=pdf_path,
                score=match.score,
                reasons=match.reasons,
                candidates=summary,
                docket=docket,
                related_cases=related,
            )
        except CaptchaTimeout as exc:
            return LookupResult("captcha_timeout", message=str(exc))
        except Exception as exc:
            log.exception("Fallo en la consulta")
            _save_debug(page, output_dir)
            return LookupResult("error", message=f"{type(exc).__name__}: {exc}")


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
