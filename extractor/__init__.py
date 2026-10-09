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

from core.matcher import normalize_agency
from core.models import LookupQuery
from counties import florida, is_supported, supported_counties
from core.utils import normalize

from .fetch import Article, FetchError, fetch_article
from .llm import ExtractionError, extract
from .repair import date_precision, repair
from .schema import Extraction, Person

log = logging.getLogger(__name__)

__all__ = ["Article", "Extraction", "ExtractionError", "NoSuspect", "OutOfScope", "FetchError", "ArticleQuery",
           "fetch_article", "extract", "build_query", "query_from_url"]

class NoSuspect(ExtractionError):
    """La nota no nombra a ningún arrestado o acusado: el portal se busca por
    nombre, así que no hay nada que buscar."""


class OutOfScope(ExtractionError):
    """El artículo es de un lugar que todavía no se busca (otro estado u otro condado).

    reason: "state" (fuera de Florida), "county" (condado de Florida sin soporte)
    o "unknown" (no se pudo determinar). Se detecta antes de abrir el portal,
    así que no gasta CAPTCHA.
    """

    def __init__(self, reason: str, message: str, state: Optional[str] = None,
                 county: Optional[str] = None, city: Optional[str] = None):
        super().__init__(message)
        self.reason, self.state, self.county, self.city = reason, state, county, city


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
        raise NoSuspect("The article doesn't name the person who was arrested or charged. "
                        f"The court portal can only be searched by name.{detail}")

    ranked = [p for p in people if p.role in ARRESTED_ROLES] + [p for p in people if p.role not in ARRESTED_ROLES]
    if suspect_index >= len(ranked):
        raise ExtractionError(f"The article names only {len(ranked)} person(s); number {suspect_index + 1} does not exist")
    person = ranked[suspect_index]
    if person.role == "suspect":
        warnings.append(f"{person.first_name} {person.last_name} figura como sospechoso, no como arrestado: "
                        "puede no existir un caso en la corte todavía")

    incident = _valid_date(extraction.incident_date, article.published, "incident_date", warnings)
    arrest = _valid_date(extraction.arrest_date, article.published, "arrest_date", warnings)

    # "back in 2022" o "in June 2023": no hay día exacto. Se busca en todo ese
    # período en vez de fingir un 1 de enero o un 1 de junio.
    date_from = date_to = None
    precision = date_precision(extraction.incident_date_precision, extraction.incident_date_evidence)
    if incident and precision != "day":
        date_from, date_to = _period(incident, precision)
        notes.append(f"fecha: el artículo solo da el {'año' if precision == 'year' else 'mes'} "
                     f"({extraction.incident_date_evidence!r}); busco entre {date_from} y {date_to}")
        if arrest == incident:
            arrest = None
        incident = None
    if incident and arrest and arrest < incident - timedelta(days=1):
        warnings.append(f"La fecha de arresto ({arrest}) es anterior al incidente ({incident}); la descarto")
        arrest = None
    approx = None
    if not incident and not arrest and not date_from:
        approx = article.published
        warnings.append("El artículo no da fecha del incidente ni del arresto: busco en el mes previo a la publicación"
                        if approx else "Sin ninguna fecha: la búsqueda puede toparse con el límite de 500 resultados")

    county = resolve_county(extraction)

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
        date_from=date_from,
        date_to=date_to,
        age_as_of=article.published if person.age is not None else None,
        published_date=article.published,
    )
    others = [p for p in ranked if p is not person]
    return ArticleQuery(query=query, person=person, others=others, extraction=extraction,
                        warnings=warnings, notes=notes)


def _period(day: date, precision: str) -> tuple[date, date]:
    """Rango de búsqueda para una fecha conocida solo por mes o por año (+45 días para la apertura del caso)."""
    if precision == "year":
        start, end = date(day.year, 1, 1), date(day.year, 12, 31)
    else:
        start = day.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return start - timedelta(days=7), end + timedelta(days=45)


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


_STATES = {"florida": "FL", "fla": "FL", "georgia": "GA", "alabama": "AL", "texas": "TX",
           "california": "CA", "new york": "NY", "north carolina": "NC", "south carolina": "SC"}


def _state_code(value: Optional[str]) -> Optional[str]:
    v = normalize(value).replace(".", "").strip()
    if not v:
        return None
    return _STATES.get(v, v.upper() if len(v) == 2 else v.title())


def _county_key(value: Optional[str]) -> Optional[str]:
    v = normalize(value)
    v = re.sub(r"\b(county|parish)\b", "", v).strip().replace("-", " ")
    v = re.sub(r"\bsaint\b", "st", v)
    return re.sub(r"\s+", "_", v) or None


def resolve_county(extraction: Extraction) -> str:
    """Clave del condado soportado ('orange') o OutOfScope con un mensaje claro."""
    state = _state_code(extraction.state)
    city = (extraction.city or "").strip() or None
    place = ", ".join(p for p in (city, state) if p) or "an unknown location"
    supported = ", ".join(supported_counties("FL"))

    if state and state != "FL":
        raise OutOfScope("state", f"The article is about {place}. For now, only Florida cases are searched.",
                         state=state, city=city)

    # 1) El condado que dio el modelo; 2) la agencia; 3) la ciudad.
    candidates = [_county_key(extraction.county),
                  florida.AGENCY_TO_COUNTY.get(normalize_agency(extraction.agency)),
                  florida.CITY_TO_COUNTY.get(normalize(city))]
    county = next((c for c in candidates if c in florida.ALL_COUNTIES), None)

    if county is None:
        if extraction.county and not state:
            raise OutOfScope("state", f"'{extraction.county}' County is not in Florida ({place}). "
                                      "For now, only Florida cases are searched.", county=extraction.county, city=city)
        log.info("Sin condado: ciudad=%r condado=%r agencia=%r", city, extraction.county, extraction.agency)
        raise OutOfScope("unknown", "The article doesn't say which city or county the arrest was in, "
                                    "so the right court portal can't be chosen.",
                         state=state, county=extraction.county, city=city)
    if not is_supported(county, "FL"):
        name = florida.ALL_COUNTIES[county]
        where = f"{city} ({name} County, FL)" if city else f"{name} County, FL"
        raise OutOfScope("county", f"The article is about {where}. That county is not covered yet "
                                   f"(covered: {supported}).", state="FL", county=name, city=city)
    return county
