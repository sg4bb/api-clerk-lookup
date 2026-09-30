"""Correcciones deterministas sobre lo que devuelve el modelo.

El modelo a veces deja campos a medias ("Friday" en vez de la fecha, la edad
en null aunque el artículo diga "23-year-old", sin ciudad pese al "ORLANDO,
Fla." del encabezado). Estas reglas completan o corrigen esos campos a partir
del propio texto del artículo, sin inventar nada: si una regla no encuentra
un dato claro, lo deja como estaba.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from core.utils import normalize

from .schema import Extraction, Person

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_SAME_DAY = ("today", "this morning", "this afternoon", "this evening", "tonight")
_DAY_BEFORE = ("yesterday", "last night", "overnight")
_ARREST_WORDS = re.compile(r"\b(arrested|arrest|custody|booked|jailed|apprehended|detained|turned (?:him|her)self in)\b", re.I)
_DATELINE = re.compile(r"^\s*([A-Z][A-Z .'\-]{2,30}),\s*(?:Fla\.?|Florida|FL)\b", re.M)

# Agencias que solo actúan dentro del condado de Orange.
ORANGE_AGENCIES = (
    "orlando police", "orange county sheriff", "winter park police", "apopka police", "ocoee police",
    "winter garden police", "maitland police", "windermere police", "edgewood police", "belle isle police",
    "eatonville police", "university of central florida police", "ucf police", "opd", "ocso",
)


def resolve_relative_day(text: Optional[str], published: Optional[date]) -> Optional[date]:
    """'Friday', 'Friday night', 'yesterday'... -> fecha, contando hacia atrás desde la publicación."""
    if not text or not published:
        return None
    t = normalize(text)
    for phrase in _DAY_BEFORE:
        if re.search(rf"\b{phrase}\b", t):
            return published - timedelta(days=1)
    found = [i for i, day in enumerate(WEEKDAYS) if re.search(rf"\b{day}\b", t)]
    if len(found) == 1:
        return published - timedelta(days=(published.weekday() - found[0]) % 7)
    for phrase in _SAME_DAY:
        if re.search(rf"\b{phrase}\b", t):
            return published
    return None


def _iso(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _fix_date(label: str, value: Optional[str], evidence: Optional[str], published: Optional[date],
              notes: list[str]) -> Optional[str]:
    parsed = _iso(value)
    if parsed is None and value:
        resolved = resolve_relative_day(value, published) or resolve_relative_day(evidence, published)
        if resolved:
            notes.append(f"{label}: el modelo devolvió {value!r}; lo convertí a {resolved} según la publicación")
            return resolved.isoformat()
        return value
    if parsed and evidence and published:
        # Si la cita dice "Friday" pero la fecha no cae en viernes, el modelo contó mal.
        resolved = resolve_relative_day(evidence, published)
        if resolved and resolved != parsed:
            notes.append(f"{label}: {parsed} no coincide con {evidence!r}; uso {resolved}")
            return resolved.isoformat()
    return value


def _sentences_with(surname: str, text: str) -> list[str]:
    key = normalize(surname)
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if re.search(rf"\b{re.escape(key)}\b", normalize(s))]


def _fix_person(person: Person, text: str, notes: list[str]) -> Person:
    updates = {}
    sentences = _sentences_with(person.last_name, text)
    if person.age is None:
        near = re.search(rf"{re.escape(person.last_name)}\s*,\s*(\d{{1,2}})\s*,", text, re.I)
        ages = set(re.findall(r"\b(\d{1,2})-year-old\b", text))
        if near:
            updates["age"] = int(near.group(1))
        elif len(ages) == 1:
            updates["age"] = int(ages.pop())
        if "age" in updates:
            notes.append(f"edad: la tomé del texto ({updates['age']})")
    if person.role == "suspect" and any(_ARREST_WORDS.search(s) for s in sentences):
        updates["role"] = "arrested"
        notes.append("rol: el artículo dice que fue arrestado; lo cambié de 'suspect' a 'arrested'")
    if sentences and normalize(person.evidence) == normalize(f"{person.first_name} {person.last_name}"):
        updates["evidence"] = sentences[0].strip()
    return person.model_copy(update=updates) if updates else person


def repair(extraction: Extraction, text: str, published: Optional[date]) -> tuple[Extraction, list[str]]:
    notes: list[str] = []
    updates: dict = {
        "people": [_fix_person(p, text, notes) for p in extraction.people],
        "incident_date": _fix_date("incident_date", extraction.incident_date,
                                   extraction.incident_date_evidence, published, notes),
        "arrest_date": _fix_date("arrest_date", extraction.arrest_date, None, published, notes),
    }
    if not extraction.city:
        match = _DATELINE.search(text[:600])
        if match:
            updates["city"] = match.group(1).strip().title()
            updates["state"] = extraction.state or "FL"
            notes.append(f"ciudad: la tomé del encabezado ({updates['city']}, FL)")
    return extraction.model_copy(update=updates), notes


def county_from_agency(agency: Optional[str]) -> Optional[str]:
    a = normalize(agency)
    if a and any(re.search(rf"\b{re.escape(key)}\b", a) for key in ORANGE_AGENCIES):
        return "orange"
    return None
