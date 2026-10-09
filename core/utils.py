"""Utilidades de fechas y nombres."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Optional

_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")


def parse_us_date(text: Optional[str]) -> Optional[date]:
    """Primera fecha M/D/YYYY o M/D/YY que aparezca en el texto."""
    if not text:
        return None
    match = _DATE_RE.search(text)
    if not match:
        return None
    month, day, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def format_short_us(d: date) -> str:
    """M/d/yy sin ceros a la izquierda (strftime %-m no funciona en Windows)."""
    return f"{d.month}/{d.day}/{d:%y}"


def normalize(text: Optional[str]) -> str:
    """Minúsculas, sin acentos ni puntuación, espacios simples."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def portal_text(text: Optional[str]) -> str:
    """Nombre tal como lo guardan los portales: sin acentos ni eñes.

    'José' -> 'Jose', 'González Delgado' -> 'Gonzalez Delgado', 'Muñoz' -> 'Munoz'.
    Conserva mayúsculas, espacios, guiones y apóstrofos (O'Neil).
    Visto en Orange: el expediente dice 'GONZALEZ DELGADO, JOSE ALBERTO' y la
    búsqueda con 'González Delgado' no devuelve nada.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9 '\-.]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _drop_suffixes(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in NAME_SUFFIXES]


def surname_key(last_name: Optional[str]) -> str:
    """Apellido comparable: sin sufijos (Jr, III...) ni espacios/guiones.

    'Cruz Peraza' / 'Cruz-Peraza' / 'CRUZPERAZA' -> 'cruzperaza'
    'Rodriguez Jr.' -> 'rodriguez'
    """
    return "".join(_drop_suffixes(normalize(last_name).split()))


def split_defendant(defendant: str) -> tuple[str, str, str]:
    """'WARD, DARRICA LEANDRA' -> ('ward', 'darrica', 'leandra').

    Los sufijos (JR, SR, III...) no cuentan como segundo nombre:
    'RODRIGUEZ, WILFREDO JR' -> ('rodriguez', 'wilfredo', '').
    """
    if "," in defendant:
        last, rest = defendant.split(",", 1)
    else:
        parts = defendant.split()
        last, rest = (parts[-1], " ".join(parts[:-1])) if parts else ("", "")
    rest_parts = _drop_suffixes(normalize(rest).split())
    first = rest_parts[0] if rest_parts else ""
    middle = " ".join(rest_parts[1:])
    return " ".join(_drop_suffixes(normalize(last).split())), first, middle


def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def today() -> date:
    return datetime.now().date()


def safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip()


MAX_SURNAME_VARIANTS = 3


def last_name_variants(last_name: str) -> list[str]:
    """Formas de escribir un apellido compuesto, empezando por la original.

    "Cruz Peraza" -> ["Cruz Peraza", "CruzPeraza", "Cruz-Peraza"]
    Un apellido simple devuelve solo [el original].
    Visto en Orange: guarda "CRUZPERAZA" y no encuentra "Cruz Peraza".
    """
    original = " ".join(last_name.split())
    parts = [p for p in re.split(r"[\s\-]+", original) if p]
    if len(parts) < 2:
        return [original]
    variants = [original, "".join(parts), "-".join(parts), " ".join(parts)]
    unique: list[str] = []
    for v in variants:
        if v.lower() not in {u.lower() for u in unique}:
            unique.append(v)
    return unique[:MAX_SURNAME_VARIANTS]
