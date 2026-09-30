"""Fase 2: link de un artículo -> LookupQuery.

    article = fetch_article(url)          # texto de la nota (trafilatura / navegador)
    extraction = extract(...)             # datos con NVIDIA NIM (guided_json)
    query, warnings = build_query(...)    # validación + consulta para el portal
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from core.models import LookupQuery
from core.utils import normalize

from .fetch import Article, FetchError, fetch_article
from .llm import ExtractionError, extract
from .repair import county_from_agency, repair
from .schema import Extraction, Person

log = logging.getLogger(__name__)

__all__ = ["Article", "Extraction", "ExtractionError", "FetchError", "ArticleQuery",
           "fetch_article", "extract", "build_query", "query_from_url"]

# Ciudades del condado de Orange (FL), por si el modelo no infiere el condado.
_ORANGE_CITIES = {
    "orlando", "winter park", "apopka", "ocoee", "winter garden", "maitland", "windermere",
    "belle isle", "edgewood", "oakland", "eatonville", "pine hills", "pine castle", "azalea park",
    "conway", "union park", "hunters creek", "meadow woods", "doctor phillips", "dr phillips",
    "lake buena vista", "horizon west", "gotha", "christmas", "bithlo", "wedgefield", "zellwood",
    "lockhart", "tangerine", "taft", "oak ridge", "holden heights", "orlovista", "fairview shores",
    "lake nona", "avalon park", "waterford lakes", "alafaya", "goldenrod",
}
_COUNTY_ALIASES = {"orange": "orange", "orange county": "orange"}
ARRESTED_ROLES = ("arrested", "charged")


@dataclass
class ArticleQuery:
    query: LookupQuery
    person: Person
    others: list[Person]
    extraction: Extraction
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # correcciones hechas sobre la respuesta del modelo


def query_from_url(url: str, suspect_index: int = 0) -> tuple[Article, ArticleQuery]:
    article = fetch_article(url)
    published = article.published.isoformat() if article.published else None
    extraction = extract(article.text, article.title, published, url)
    return article, build_query(extraction, article, suspect_index)


def build_query(extraction: Extraction, article: Article, suspect_index: int = 0) -> ArticleQuery:
    warnings: list[str] = []
    extraction, notes = repair(extraction, article.text, article.published)
    haystack = normalize(article.title + " " + article.text)

    # Anti-invención: el nombre tiene que aparecer en el artículo.
    people = []
    for person in extraction.people:
        if _word_in(person.first_name, haystack) and _surname_in(person.last_name, haystack):
            people.append(person)
        else:
            warnings.append(f"Descarté '{person.first_name} {person.last_name}': el nombre no aparece en el artículo")
    if not people:
        detail = f" ({'; '.join(warnings)})" if warnings else ""
        raise ExtractionError(f"El artículo no nombra a ningún arrestado o acusado{detail}")

    ranked = [p for p in people if p.role in ARRESTED_ROLES] + [p for p in people if p.role not in ARRESTED_ROLES]
    if suspect_index >= len(ranked):
        raise ExtractionError(f"Solo hay {len(ranked)} persona(s); --suspect {suspect_index + 1} no existe")
    person = ranked[suspect_index]
    if person.role == "suspect":
        warnings.append(f"{person.first_name} {person.last_name} figura como sospechoso, no como arrestado: "
                        "puede no existir un caso en la corte todavía")

    incident = _valid_date(extraction.incident_date, article.published, "incident_date", warnings)
    arrest = _valid_date(extraction.arrest_date, article.published, "arrest_date", warnings)
    if incident and arrest and arrest < incident - timedelta(days=1):
        warnings.append(f"La fecha de arresto ({arrest}) es anterior al incidente ({incident}); la descarto")
        arrest = None
    approx = None
    if not incident and not arrest:
        approx = article.published
        warnings.append("El artículo no da fecha del incidente ni del arresto: busco en el mes previo a la publicación"
                        if approx else "Sin ninguna fecha: la búsqueda puede toparse con el límite de 500 resultados")

    county = _county(extraction)
    if not county:
        raise ExtractionError(f"Condado no soportado o desconocido (ciudad: {extraction.city}, "
                              f"condado: {extraction.county}, estado: {extraction.state}, "
                              f"agencia: {extraction.agency})")

    query = LookupQuery(
        first_name=person.first_name.strip(),
        middle_name=(person.middle_name or "").strip(" .") or None,
        last_name=person.last_name.strip(),
        county=county,
        state="FL",
        incident_date=incident,
        arrest_date=arrest,
        age=person.age,
        agency=extraction.agency,
        charges=extraction.charges,
        approx_date=approx,
    )
    others = [p for p in ranked if p is not person]
    return ArticleQuery(query=query, person=person, others=others, extraction=extraction,
                        warnings=warnings, notes=notes)


def _word_in(value: str, haystack: str) -> bool:
    word = normalize(value)
    return bool(word) and re.search(rf"\b{re.escape(word)}\b", haystack) is not None


def _surname_in(last_name: str, haystack: str) -> bool:
    # "Cruz Peraza" también vale si el artículo lo escribe "Cruz-Peraza" o "CruzPeraza".
    return _word_in(last_name, haystack) or _word_in(normalize(last_name).replace(" ", ""), haystack)


def _valid_date(value: Optional[str], published: Optional[date], label: str, warnings: list[str]) -> Optional[date]:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        warnings.append(f"{label} con formato inválido ({value!r}); la descarto")
        return None
    if published and parsed > published + timedelta(days=1):
        warnings.append(f"{label} ({parsed}) es posterior a la publicación ({published}); la descarto")
        return None
    return parsed


def _county(extraction: Extraction) -> Optional[str]:
    state = (extraction.state or "FL").strip().upper()
    if state not in ("FL", "FLORIDA"):
        return None
    county = normalize(extraction.county).replace(" county", "").strip() if extraction.county else ""
    if county in _COUNTY_ALIASES:
        return _COUNTY_ALIASES[county]
    # La agencia que arrestó es la señal más firme; después, la ciudad.
    from_agency = county_from_agency(extraction.agency)
    if from_agency:
        return from_agency
    if normalize(extraction.city) in _ORANGE_CITIES:
        return "orange"
    return None
