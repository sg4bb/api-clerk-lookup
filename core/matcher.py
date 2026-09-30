"""Puntúa candidatos contra la consulta y elige un único resultado.

No depende del condado: solo usa CaseCandidate / CaseDetails.
El puntaje final es un % de confianza: puntos obtenidos sobre los puntos
posibles con la información que trae la consulta. Así una consulta con
pocos datos (solo nombre y fecha) no queda penalizada si todo coincide.
Los pesos son un punto de partida; los ajustamos con casos reales.
"""

from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
import re
from typing import Callable, Optional

from .models import CaseCandidate, CaseDetails, LookupQuery, MatchScore
from .charges import categorize
from .utils import age_on, normalize, split_defendant, surname_key

MIN_SCORE = 60      # % de confianza mínimo para devolver un caso
MIN_MARGIN = 15     # ventaja mínima (en puntos %) sobre el segundo mejor

# Puntos máximos por criterio.
W_NAME, W_MIDDLE, W_OFFENSE, W_ARREST, W_AGENCY, W_AGE, W_CHARGES = 30, 5, 25, 15, 20, 10, 15

_AGENCY_ALIASES = {
    "police department": "pd",
    "police dept": "pd",
    "sheriff s office": "so",
    "sheriffs office": "so",
    "sheriff office": "so",
    "florida highway patrol": "fhp",
}


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def normalize_agency(text: Optional[str]) -> str:
    value = normalize(text)
    for long, short in _AGENCY_ALIASES.items():
        value = value.replace(long, short)
    return value.replace("city of ", "").strip()


def _name_points(query: LookupQuery, defendant: str, reasons: list[str]) -> Optional[int]:
    """Puntos por nombre. None = el apellido no coincide (descartar)."""
    last, first, middle = split_defendant(defendant)
    # Apellidos compuestos: "Cruz Peraza", "Cruz-Peraza" y "Cruzperaza"
    # se tratan como el mismo apellido.
    if surname_key(last) != surname_key(query.last_name):
        reasons.append(f"apellido distinto ({last})")
        return None

    points = 0
    q_first = normalize(query.first_name)
    if first == q_first:
        points += W_NAME
        reasons.append("nombre exacto")
    elif _similar(first, q_first) >= 0.85 or first.startswith(q_first) or q_first.startswith(first):
        points += 20
        reasons.append(f"nombre parecido ({first})")
    else:
        reasons.append(f"nombre distinto ({first})")
        return None

    if query.middle_name and middle:
        if middle.split()[0][:1] == normalize(query.middle_name)[:1]:
            points += W_MIDDLE
            reasons.append("segundo nombre coincide")
        else:
            points -= 10
            reasons.append(f"segundo nombre distinto ({middle})")
    return points


def prelim_score(query: LookupQuery, candidate: CaseCandidate) -> int:
    """Puntaje rápido con la fila de resultados, para decidir qué casos abrir."""
    reasons: list[str] = []
    points = _name_points(query, candidate.defendant, reasons)
    if points is None:
        return 0
    anchor = query.incident_date or query.arrest_date
    if anchor and candidate.filed_date:
        gap = abs((candidate.filed_date - anchor).days)
        points += max(0, 20 - gap // 3)
    return max(points, 1)


def max_points(query: LookupQuery, details: CaseDetails) -> int:
    """Puntos alcanzables con lo que se sabe de la consulta y lo que trae el caso."""
    c = details.candidate
    has_middle = bool(split_defendant(c.defendant)[2])
    total = W_NAME
    total += W_MIDDLE if query.middle_name and has_middle else 0
    total += W_OFFENSE if query.incident_date and any(x.offense_date for x in details.charges) else 0
    total += W_ARREST if query.arrest_date and any(x.arrest_date for x in details.charges) else 0
    total += W_AGENCY if query.agency and any(x.agency for x in details.charges) else 0
    anchor = query.incident_date or query.arrest_date
    total += W_AGE if query.age is not None and c.dob and anchor else 0
    total += W_CHARGES if categorize(query.charges) and categorize(x.description for x in details.charges) else 0
    return total


def score_case(query: LookupQuery, details: CaseDetails) -> MatchScore:
    reasons: list[str] = []
    name_points = _name_points(query, details.candidate.defendant, reasons)
    if name_points is None:
        return MatchScore(0, reasons, disqualified=True)
    score = name_points

    # Fecha de la ofensa contra la fecha del incidente. Graduada: el mismo día
    # vale más que 1-3 días, para separar incidentes de la misma persona que
    # caen en días seguidos (visto en Orange: casos del 01/08 y 03/08/2023).
    offense_dates = [c.offense_date for c in details.charges if c.offense_date]
    if query.incident_date and offense_dates:
        gap = min(abs((d - query.incident_date).days) for d in offense_dates)
        points = {0: W_OFFENSE, 1: 22, 2: 16, 3: 12}.get(gap)
        if points is not None:
            score += points
            reasons.append(f"fecha de ofensa a {gap} día(s) del incidente")
        elif gap <= 10:
            score += 6
            reasons.append(f"fecha de ofensa a {gap} días del incidente")
        else:
            score -= 15
            reasons.append(f"fecha de ofensa lejana ({gap} días)")

    # Fecha de arresto (también graduada).
    arrest_dates = [c.arrest_date for c in details.charges if c.arrest_date]
    if query.arrest_date and arrest_dates:
        gap = min(abs((d - query.arrest_date).days) for d in arrest_dates)
        points = {0: W_ARREST, 1: 12, 2: 7, 3: 4}.get(gap)
        if points is not None:
            score += points
            reasons.append("fecha de arresto coincide" if gap == 0 else f"fecha de arresto a {gap} día(s)")
        elif gap > 10:
            score -= 10
            reasons.append(f"fecha de arresto lejana ({gap} días)")

    # Agencia que arrestó.
    agencies = {normalize_agency(c.agency) for c in details.charges if c.agency}
    if query.agency and agencies:
        q_agency = normalize_agency(query.agency)
        if any(q_agency == a or _similar(q_agency, a) >= 0.8 for a in agencies):
            score += W_AGENCY
            reasons.append("agencia coincide")
        else:
            score -= 10
            reasons.append(f"agencia distinta ({', '.join(sorted(agencies))})")

    # Edad reportada contra fecha de nacimiento.
    dob = details.candidate.dob
    anchor: Optional[date] = query.incident_date or query.arrest_date
    if query.age is not None and dob and anchor:
        real_age = age_on(dob, anchor)
        if abs(real_age - query.age) <= 1:
            score += W_AGE
            reasons.append(f"edad coincide ({real_age})")
        else:
            score -= 15
            reasons.append(f"edad distinta ({real_age} vs {query.age})")

    # Cargos: categorías del artículo contra categorías del caso. Desempata
    # casos del mismo día (p. ej. un hurto y un burglary en el mismo arresto).
    q_cats = categorize(query.charges)
    c_cats = categorize(c.description for c in details.charges)
    if q_cats and c_cats:
        shared = q_cats & c_cats
        if shared:
            score += round(W_CHARGES * len(shared) / len(q_cats))
            reasons.append(f"cargos coinciden ({', '.join(sorted(shared))})")
        else:
            score -= 10
            reasons.append(f"cargos distintos ({', '.join(sorted(c_cats))})")

    possible = max_points(query, details)
    confidence = round(100 * max(score, 0) / possible)
    reasons.append(f"{max(score, 0)}/{possible} puntos posibles")
    return MatchScore(min(confidence, 100), reasons)


def pick_best(
    scored: list[tuple[MatchScore, CaseDetails]],
    min_score: int = MIN_SCORE,
    min_margin: int = MIN_MARGIN,
) -> tuple[Optional[tuple[MatchScore, CaseDetails]], str]:
    """Devuelve (ganador, estado). Estado: found | not_found | ambiguous."""
    valid = sorted(
        (s for s in scored if not s[0].disqualified),
        key=lambda s: s[0].score,
        reverse=True,
    )
    if not valid or valid[0][0].score < min_score:
        return None, "not_found"
    if len(valid) > 1 and valid[0][0].score - valid[1][0].score < min_margin:
        return None, "ambiguous"
    return valid[0], "found"


# --- Varios casos del mismo arresto -----------------------------------------
# Un mismo arresto a veces genera varios casos. Visto en Orange (CLARKEROSEN,
# 03/08/2023): dos hurtos en el mismo 7-Eleven, con números de reporte
# consecutivos (23-47652 y 23-47653), un solo arresto y dos casos (CF y MM).
# Para pedir el bodycam del arresto da igual cuál se devuelva; los demás se
# informan como relacionados. Casos de arrestos distintos nunca se agrupan.

_CASE_TYPE_RE = re.compile(r"^\d{4}-([A-Z]{2})-")
_TYPE_RANK = {"CF": 0, "MM": 1, "CT": 2}


def same_incident(a: CaseDetails, b: CaseDetails) -> bool:
    """True si dos casos vienen del mismo arresto.

    - Comparten el número de reporte de la agencia (Control Number): seguro.
    - Si no: misma fecha de arresto, mismas fechas de ofensa y misma agencia.
    """
    controls_a = {c.control_number for c in a.charges if c.control_number}
    controls_b = {c.control_number for c in b.charges if c.control_number}
    if controls_a & controls_b:
        return True

    def key(d: CaseDetails):
        return ({c.offense_date for c in d.charges if c.offense_date},
                {c.arrest_date for c in d.charges if c.arrest_date},
                {normalize_agency(c.agency) for c in d.charges if c.agency})

    ka, kb = key(a), key(b)
    return bool(ka[0]) and bool(ka[1]) and ka == kb


def _preference(details: CaseDetails, has_document: Callable[[CaseDetails], bool]):
    match = _CASE_TYPE_RE.match(details.candidate.case_number)
    rank = _TYPE_RANK.get(match.group(1) if match else "", 9)
    filed = details.candidate.filed_date or date.max
    return (0 if has_document(details) else 1, rank, filed)


def resolve_same_incident(
    scored: list[tuple[MatchScore, CaseDetails]],
    has_document: Callable[[CaseDetails], bool],
    min_score: int = MIN_SCORE,
    min_margin: int = MIN_MARGIN,
) -> Optional[tuple[MatchScore, CaseDetails]]:
    """Desempata candidatos que son del mismo arresto. None si no lo son."""
    valid = sorted((s for s in scored if not s[0].disqualified), key=lambda s: s[0].score, reverse=True)
    if not valid or valid[0][0].score < min_score:
        return None
    top = valid[0]
    close = [s for s in valid if top[0].score - s[0].score < min_margin]
    if len(close) < 2 or not all(same_incident(top[1], s[1]) for s in close[1:]):
        return None
    return min(close, key=lambda s: _preference(s[1], has_document))


def related_cases(winner: CaseDetails, scored: list[tuple[MatchScore, CaseDetails]]) -> list[str]:
    """Otros casos del mismo arresto que el elegido (para informarlos)."""
    return [d.candidate.case_number for m, d in scored
            if d is not winner and not m.disqualified and same_incident(winner, d)]
