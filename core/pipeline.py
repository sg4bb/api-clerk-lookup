"""Proceso completo: link de un artículo -> incident report.

Es lo que ejecuta el worker por cada job. No imprime ni sabe de Supabase:
avisa de su avance con funciones (`on_status`, `on_captcha_waiting`,
`on_captcha_done`) y devuelve un PipelineResult.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from .lookup import run_lookup
from .models import LookupResult

log = logging.getLogger(__name__)

# Estados finales posibles (además de los de LookupResult):
#   unsupported = otro estado o condado sin soporte; error = no se pudo leer o extraer.


@dataclass
class PipelineResult:
    status: str                                  # found | no_document | not_found | ambiguous | unsupported | captcha_timeout | error
    message: Optional[str] = None
    article_title: Optional[str] = None
    article_published: Optional[date] = None
    extraction: Optional[dict[str, Any]] = None  # lo que devolvió el modelo
    query: Optional[dict[str, Any]] = None       # lo que se buscó en el portal
    notes: list[str] = field(default_factory=list)      # ajustes hechos a la extracción
    warnings: list[str] = field(default_factory=list)
    lookup: Optional[LookupResult] = None

    @property
    def pdf_path(self) -> Optional[Path]:
        return Path(self.lookup.pdf_path) if self.lookup and self.lookup.pdf_path else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, Path)):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def run_article(
    url: str,
    output_dir: Path,
    *,
    suspect_index: int = 0,
    article_text: Optional[str] = None,
    on_status: Callable[[str], None] = lambda s: log.info("estado: %s", s),
    on_captcha_waiting=None,
    on_captcha_done=None,
) -> PipelineResult:
    from extractor import (Article, ExtractionError, FetchError, OutOfScope, build_query, extract,
                           fetch_article)

    output_dir.mkdir(parents=True, exist_ok=True)
    result = PipelineResult(status="error")
    try:
        on_status("fetching")
        if article_text and article_text.strip():
            # El usuario pegó el texto (sitios que bloquean la lectura automática).
            article = Article(url=url, title="", text=article_text.strip()[:15_000], published=None, via="pasted")
        else:
            article = fetch_article(url)
        result.article_title, result.article_published = article.title or None, article.published
        (output_dir / "article.txt").write_text(
            f"{article.title}\nPublicado: {article.published}\n{article.url}\n\n{article.text}", encoding="utf-8")

        on_status("extracting")
        published = article.published.isoformat() if article.published else None
        extraction = extract(article.text, article.title, published, article.url)
        result.extraction = extraction.model_dump()
        aq = build_query(extraction, article, suspect_index=suspect_index)
        result.query, result.notes, result.warnings = _jsonable(asdict(aq.query)), aq.notes, aq.warnings
    except OutOfScope as exc:
        result.status, result.message = "unsupported", str(exc)
        return result
    except (FetchError, ExtractionError) as exc:
        result.message = str(exc)
        return result
    except Exception as exc:
        log.exception("Error inesperado leyendo o extrayendo %s", url)
        result.message = f"{type(exc).__name__}: {exc}"
        return result

    try:
        lookup = run_lookup(aq.query, output_dir, on_status=on_status,
                            on_captcha_waiting=on_captcha_waiting, on_captcha_done=on_captcha_done)
    except Exception as exc:   # p. ej. no se pudo abrir el navegador
        log.exception("No se pudo hacer la búsqueda en el portal")
        result.message = f"Could not open the browser or the court portal: {type(exc).__name__}: {exc}"
        return result
    result.lookup, result.status, result.message = lookup, lookup.status, lookup.message
    return result
