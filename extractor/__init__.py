"""Fase 2: link de un artículo -> LookupQuery.

    article = fetch_article(url)          # texto de la nota (trafilatura / navegador)
    extraction = extract(...)             # datos con NVIDIA NIM (guided_json)
    query, warnings = build_query(...)    # validación + consulta para el portal
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Optional

from core.matcher import normalize_agency
from core.models import LookupQuery
from counties import florida, is_supported, supported_counties
from core.utils import normalize

from .fetch import Article, FetchError, fetch_article
from .llm import ExtractionError, extract
from .repair import _written_in_text, date_precision, repair
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
    # Nombres que la IA copió con una letra de más o de menos -> como los escribe la nota.
    name_fixes: dict[str, str] = field(default_factory=dict)


def query_from_url(url: str, suspect_index: int = 0) -> tuple[Article, ArticleQuery]:
    article = fetch_article(url)
    published = article.published.isoformat() if article.published else None
    extraction = extract(article.text, article.title, published, url)
    return article, build_query(extraction, article, suspect_index)


def build_query(extraction: Extraction, article: Article, suspect_index: int = 0) -> ArticleQuery:
    warnings: list[str] = []
    swapped = _swapped_published(article)
    if swapped:
        warnings.append(f"La fecha de publicación de la página ({article.published}) tiene el día y el mes invertidos; "
                        f"uso {swapped}")
        article = replace(article, published=swapped)
    if _facebook_filler_date(article):
        warnings.append(f"La fecha de publicación que dio Facebook ({article.published}) es de relleno "
                        "(1 o 2 de enero de un año que el texto no menciona): no la uso")
        article = replace(article, published=None)
    if _published_is_wrong(extraction, article):
        warnings.append(f"La fecha de publicación de la página ({article.published}) no aparece en el texto y "
                        "la del artículo sí: no la uso")
        article = replace(article, published=None)
    extraction, notes = repair(extraction, article.text, article.published)
    haystack = normalize(article.title + " " + article.text)

    # Anti-invención: el nombre tiene que aparecer en el artículo.
    people = []
    described: list[str] = []      # para el mensaje de la web (en inglés)
    name_fixes: dict[str, str] = {}
    misread: list[str] = []
    for person in extraction.people:
        full = f"{person.first_name} {person.last_name}"
        if _descriptive_name(person.first_name, person.last_name):
            warnings.append(f"Descarté '{full}': es una descripción, no un nombre")
            described.append(full)
            continue
        if _surname_in(person.last_name, haystack) and not _word_in(person.first_name, haystack):
            written = _first_name_as_written(person.first_name, person.last_name, article)
            if written:
                notes.append(f"nombre: el modelo escribió '{person.first_name}'; en el artículo dice '{written}'")
                name_fixes[person.first_name] = written
                person = person.model_copy(update={"first_name": written})
        if _word_in(person.first_name, haystack) and _surname_in(person.last_name, haystack):
            people.append(person)
        else:
            warnings.append(f"Descarté '{full}': el nombre no aparece en el artículo")
            misread.append(full)
    if not people:
        for warning in warnings:
            log.info(warning)
        raise NoSuspect("The article doesn't name the person who was arrested or charged. "
                        "The court portal can only be searched by name." + _discarded_names(described, misread))

    ranked = [p for p in people if p.role in ARRESTED_ROLES] + [p for p in people if p.role not in ARRESTED_ROLES]
    if suspect_index >= len(ranked):
        raise ExtractionError(f"The article names only {len(ranked)} person(s); number {suspect_index + 1} does not exist")
    person = ranked[suspect_index]
    if person.role == "suspect":
        warnings.append(f"{person.first_name} {person.last_name} figura como sospechoso, no como arrestado: "
                        "puede no existir un caso en la corte todavía")

    incident = _valid_date(extraction.incident_date, article.published, "incident_date", warnings)
    arrest = _valid_date(extraction.arrest_date, article.published, "arrest_date", warnings)
    court = sorted({d for d in (_valid_date(v, article.published, "court_date", warnings)
                                for v in extraction.court_dates) if d})
    # En una nota de condena el modelo puede poner la fecha del veredicto como la del hecho
    # (Cox, Liberty, 28/10/2026: "On June 30, 2026, Cox was convicted" -> incident_date 2026-06-30).
    # Un crimen y su condena nunca son el mismo día.
    if incident and incident in court:
        warnings.append(f"La fecha del incidente ({incident}) es la de la condena o sentencia; la descarto")
        incident = None
    if arrest and arrest in court:
        warnings.append(f"La fecha de arresto ({arrest}) es la de la condena o sentencia; la descarto")
        arrest = None

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
    if court and not incident and not arrest and not date_from:
        notes.append(f"fecha: solo hay fecha de condena o sentencia ({', '.join(map(str, court))}); "
                     "busco casos abiertos antes")
    elif not incident and not arrest and not date_from:
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
        court_dates=court,
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
                        warnings=warnings, notes=notes, name_fixes=name_fixes)


def _period(day: date, precision: str) -> tuple[date, date]:
    """Rango de búsqueda para una fecha conocida solo por mes o por año (+45 días para la apertura del caso)."""
    if precision == "year":
        start, end = date(day.year, 1, 1), date(day.year, 12, 31)
    else:
        start = day.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return start - timedelta(days=7), end + timedelta(days=45)


# Palabras que describen a alguien sin nombrarlo ("A Lynn Haven man", "the driver").
_DESCRIPTIVE = {"man", "woman", "men", "women", "male", "female", "suspect", "suspects", "driver", "resident",
                "teen", "teenager", "boy", "girl", "person", "individual", "subject", "victim", "unknown"}


def _descriptive_name(first: Optional[str], last: Optional[str]) -> bool:
    """El modelo puso una descripción como nombre (Fennell, Washington: "A Lynn Haven man" en
    nombre y apellido, porque Facebook cortó la publicación antes del nombre)."""
    a, b = normalize(first or ""), normalize(last or "")
    if not a or not b:
        return False
    words = f"{a} {b}".split()
    return (a == b and len(a.split()) > 1) or a.split()[0] in ("a", "an", "the") \
        or any(w in _DESCRIPTIVE for w in words)


def _discarded_names(described: list[str], misread: list[str]) -> str:
    """Por qué no sirvió el nombre que dio la IA, para la web (los detalles en español quedan en la consola)."""
    parts = []
    if misread:
        names = " and ".join(misread)
        parts.append(f" The AI read the name{'s' if len(misread) > 1 else ''} as {names}, "
                     f"but the article doesn't spell {'them' if len(misread) > 1 else 'it'} that way.")
    if described:
        parts.append(f" The AI took a description (\"{described[0]}\") for the name.")
    return "".join(parts)


def _one_edit(a: str, b: str) -> bool:
    """True si a y b difieren en una sola letra (cambiada, sobrante o faltante)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _first_name_as_written(first_name: str, last_name: str, article: Article) -> Optional[str]:
    """El nombre tal como lo escribe la nota, si la IA lo copió con una letra de más o de menos.

    Gill (Pasco, 29/10/2026): el post dice "BROOKELYNN GILL" y qwen3 escribió "Brooklynn"; el
    nombre se descartaba por no estar en el texto. Solo se corrige si la palabra va pegada al
    apellido (hasta dos palabras en medio, por un segundo nombre), es única y tiene 4 letras o más:
    un nombre inventado o de otra persona se sigue descartando.
    """
    first, last = normalize(first_name), normalize(last_name)
    if len(first) < 4 or " " in first or not last:
        return None
    text = f"{article.title} {article.text}"
    haystack = normalize(text)
    words = haystack.split()
    last_words = last.split()
    found = set()
    for i in range(len(words) - len(last_words) + 1):
        if words[i:i + len(last_words)] == last_words:
            found.update(w for w in words[max(0, i - 3):i] if _one_edit(w, first))
    if len(found) != 1:
        return None
    word = found.pop()
    original = re.search(rf"\b{re.escape(word)}\b", text, re.I)
    return original.group(0) if original else None


def _word_in(value: str, haystack: str) -> bool:
    word = normalize(value)
    return bool(word) and re.search(rf"\b{re.escape(word)}\b", haystack) is not None


def _surname_in(last_name: str, haystack: str) -> bool:
    # "Cruz Peraza" también vale si el artículo lo escribe "Cruz-Peraza" o "CruzPeraza".
    return _word_in(last_name, haystack) or _word_in(normalize(last_name).replace(" ", ""), haystack)


def _swapped_published(article: Article) -> Optional[date]:
    """La publicación leída con el día y el mes al revés, si la fecha correcta está escrita en el texto.

    wcso.org muestra "News Release Date 09-07-2022" (7 de septiembre) y se leía como
    9 de julio: la fecha del arresto ("On September 7, 2022") quedaba "posterior a la
    publicación" y se descartaba (Woodward, Wakulla).
    """
    published = article.published
    if not published or published.day > 12 or published.day == published.month:
        return None
    try:
        swapped = published.replace(month=published.day, day=published.month)
    except ValueError:
        return None
    text = f"{article.title} {article.text}"
    if _written_in_text(swapped, text) and not _written_in_text(published, text):
        return swapped
    return None


def _facebook_filler_date(article: Article) -> bool:
    """Fecha de relleno de Facebook (ver fetch.facebook_filler_date). fetch_article ya la quita antes de
    que lea el modelo; esto cubre los artículos que llegan por otro camino (--reuse, texto guardado)."""
    from .fetch import facebook_filler_date
    return facebook_filler_date(article.url, article.published, f"{article.title} {article.text}")


def _published_is_wrong(extraction: Extraction, article: Article) -> bool:
    """La fecha de publicación de la página es falsa: su año no está en el texto y el de la nota sí.

    Facebook entregó 2001-01-01 para un comunicado del 05/10/2026 (Peterson, Jackson):
    la fecha del arresto se descartaba por "posterior a la publicación" y "Saturday" se
    resolvía en el año 2000. Si el modelo inventa un año que el texto no menciona, esto no
    aplica y esa fecha se sigue descartando.
    """
    published = article.published
    if not published:
        return False
    text = f"{article.title} {article.text}"
    if str(published.year) in text:
        return False
    later = []
    for value in (extraction.incident_date, extraction.arrest_date, *extraction.court_dates):
        try:
            parsed = date.fromisoformat((value or "")[:10])
        except ValueError:
            continue
        if parsed > published + timedelta(days=1):
            later.append(parsed)
    return any(str(d.year) in text for d in later)


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
    v = re.sub(r"\bde soto\b", "desoto", v)      # "De Soto County" también se escribe así
    return re.sub(r"\s+", "_", v) or None


def resolve_county(extraction: Extraction) -> str:
    """Clave del condado soportado ('orange') o OutOfScope con un mensaje claro."""
    state = _state_code(extraction.state)
    city = (extraction.city or "").strip() or None
    place = ", ".join(p for p in (city, state) if p) or "an unknown location"
    # El modelo a veces escribe "Madison County": sin repetir "County" en los avisos.
    county_name = re.sub(r"\s+(county|parish)\s*$", "", (extraction.county or "").strip(), flags=re.I) or None
    supported = ", ".join(supported_counties("FL"))

    if state and state != "FL":
        if not city and county_name:
            place = f"{county_name} County, {state}"     # Trevon Brown: "about AL" a secas (22/10/2026)
        raise OutOfScope("state", f"The article is about {place}. For now, only Florida cases are searched.",
                         state=state, city=city)

    # 1) La agencia, si es de un solo condado (un Sheriff solo actúa en el suyo;
    #    una policía municipal, en su ciudad); 2) el condado que dio el modelo;
    #    3) la ciudad. La agencia va primero porque el modelo a veces toma el
    #    dateline ("ORLANDO, Fla. -" = la redacción de FOX 35) como lugar del
    #    hecho: Almany, detective de Sumter, salió como Orange (14/10/2026).
    by_model = _county_key(extraction.county)
    by_agency = florida.AGENCY_TO_COUNTY.get(normalize_agency(extraction.agency))
    if by_agency and by_model in florida.ALL_COUNTIES and by_model != by_agency:
        log.warning("La IA dijo condado %s, pero la agencia (%s) es de %s: uso %s",
                    florida.ALL_COUNTIES[by_model], extraction.agency,
                    florida.ALL_COUNTIES[by_agency], florida.ALL_COUNTIES[by_agency])
    candidates = [by_agency, by_model, florida.CITY_TO_COUNTY.get(normalize(city))]
    county = next((c for c in candidates if c in florida.ALL_COUNTIES), None)

    if county is None:
        if extraction.county and not state:
            raise OutOfScope("state", f"'{county_name}' County is not in Florida ({place}). "
                                      "For now, only Florida cases are searched.", county=extraction.county, city=city)
        log.info("Sin condado: ciudad=%r condado=%r agencia=%r", city, extraction.county, extraction.agency)
        raise OutOfScope("unknown", "The article doesn't say which city or county the arrest was in, "
                                    "so the right court portal can't be chosen.",
                         state=state, county=extraction.county, city=city)
    if not is_supported(county, "FL"):
        name = florida.ALL_COUNTIES[county]
        # A veces la IA pone el condado como ciudad ("Marion County"): no repetirlo.
        city_is_county = normalize(city) in (normalize(name), normalize(f"{name} County"))
        where = f"{city} ({name} County, FL)" if city and not city_is_county else f"{name} County, FL"
        raise OutOfScope("county", f"The article is about {where}. That county is not covered yet "
                                   f"(covered: {supported}).", state="FL", county=name, city=city)
    return county
