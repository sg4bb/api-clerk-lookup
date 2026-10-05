"""Modelos de datos compartidos por todos los adaptadores.

Ningún modelo sabe de qué condado viene: el matcher y el orquestador
trabajan solo con estas estructuras.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional


@dataclass
class LookupQuery:
    """Lo que sabemos del caso (a futuro, extraído del artículo)."""

    first_name: str
    last_name: str
    county: str                       # "orange", "lee", ...
    state: str = "FL"
    middle_name: Optional[str] = None
    incident_date: Optional[date] = None
    arrest_date: Optional[date] = None
    age: Optional[int] = None         # edad que reporta el artículo
    agency: Optional[str] = None      # "Orlando Police Department"
    case_types: list[str] = field(default_factory=lambda: ["CF", "MM", "CT"])
    # Cargos tal como los describe el artículo ("armed robbery", ...).
    charges: list[str] = field(default_factory=list)
    # Fecha aproximada (p. ej. publicación del artículo) cuando no se conoce
    # la del incidente ni la del arresto. Solo sirve para la ventana de búsqueda.
    approx_date: Optional[date] = None
    # Rango de búsqueda explícito cuando el artículo solo da el mes o el año
    # ("back in 2022"). Tiene prioridad sobre approx_date.
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    # Fecha de publicación del artículo. Si no se sabe cuándo fue el arresto,
    # el caso pudo abrirse meses después del crimen (orden de captura tras la
    # investigación), pero siempre antes de la nota: la búsqueda llega hasta aquí.
    published_date: Optional[date] = None
    # Fecha a la que corresponde "age" (la publicación del artículo). Una nota
    # de sentencia dice la edad actual, no la que tenía en el incidente.
    age_as_of: Optional[date] = None
    # Los artículos casi nunca traen el segundo nombre; por defecto se usa
    # solo para puntuar, no para filtrar la búsqueda.
    use_middle_name_in_search: bool = False


@dataclass
class CaseCandidate:
    """Una fila de la tabla de resultados del portal."""

    case_number: str
    defendant: str                    # "WARD, DARRICA LEANDRA"
    case_type: str
    status: str
    dob: Optional[date]
    filed_date: Optional[date]
    detail_ref: str                   # link al detalle; depende de la sesión


@dataclass
class Charge:
    offense_date: Optional[date]
    description: str
    statute: Optional[str]
    arrest_date: Optional[date]
    agency: Optional[str]
    control_number: Optional[str]     # número de reporte de la agencia


@dataclass
class DocketEntry:
    date: Optional[date]
    description: str
    doc_ref: Optional[str]            # link al documento; depende de la sesión


@dataclass
class CaseDetails:
    candidate: CaseCandidate
    charges: list[Charge]
    docket: list[DocketEntry]
    source_url: Optional[str] = None  # URL del detalle (se usa como Referer)


@dataclass
class MatchScore:
    score: int
    reasons: list[str]
    disqualified: bool = False


@dataclass
class LookupResult:
    # found | not_found | ambiguous | no_document | captcha_timeout | error
    status: str
    case_number: Optional[str] = None
    document: Optional[str] = None
    pdf_path: Optional[Path] = None
    score: Optional[int] = None
    reasons: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    message: Optional[str] = None
    # Otros casos del mismo arresto (se informan, no se descargan).
    related_cases: list[str] = field(default_factory=list)
    # Documentos del docket del caso elegido: fecha, nombre y si tiene link.
    docket: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, (date, Path)):
                return str(value)
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            if isinstance(value, list):
                return [convert(v) for v in value]
            return value

        return convert(asdict(self))
