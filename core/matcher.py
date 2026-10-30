"""Puntúa candidatos contra la consulta y elige un único resultado.

No depende del condado: solo usa CaseCandidate / CaseDetails.
El puntaje final es un % de confianza: puntos obtenidos sobre los puntos
posibles con la información que trae la consulta. Así una consulta con
pocos datos (solo nombre y fecha) no queda penalizada si todo coincide.
Los pesos son un punto de partida; los ajustamos con casos reales.
"""

from __future__ import annotations

from datetime import date, timedelta
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
# Solo cuenta si los cargos no traen fechas de ofensa ni de arresto (Civitek).
W_FILED = 15
# Fecha de condena o sentencia de la nota contra la fecha "Court" de los cargos (Civitek).
# Solo cuenta si el caso trae esa fecha: un portal que no la muestra (Orange) no pierde nada.
W_COURT = 20
COURT_NEAR_DAYS = 3      # coincide (puntos completos)
COURT_CLOSE_DAYS = 45    # veredicto y sentencia en días distintos (la mitad)
COURT_FAR_DAYS = 180     # más lejos: es la condena de otro caso (resta)

_AGENCY_ALIASES = {
    "police department": "pd",
    "police dept": "pd",
    "sheriff s office": "so",
    "sheriffs office": "so",
    "sheriff office": "so",
    "florida highway patrol": "fhp",
    "university of central florida": "ucf",
}
# Siglas que usan las notas de prensa ("OPD arrested...").
_AGENCY_ACRONYMS = {
    "opd": "orlando pd",
    "ocso": "orange county so",
    "wppd": "winter park pd",
    "ucfpd": "ucf pd",
    "fhp": "fhp",
}


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def normalize_agency(text: Optional[str]) -> str:
    """'Orlando Police', 'Orlando Police Department', 'OPD' y 'Orlando PD' -> 'orlando pd'."""
    value = normalize(text)
    for long, short in _AGENCY_ALIASES.items():
        value = value.replace(long, short)
    value = re.sub(r"\bpolice\b", "pd", value)
    value = re.sub(r"\bsheriff s?\b|\bsheriffs?\b", "so", value)
    value = re.sub(r"\bdeput(y|ies)\b", "so", value)
    value = re.sub(r"\b(department|dept|office|officers?|detectives?|investigators?|agents?)\b", "", value)
    value = re.sub(r"\b(the|city of)\b", "", value)
    value = re.sub(r"\b(\w+)( \1\b)+", r"\1", re.sub(r"\s+", " ", value).strip())  # "so so" -> "so"
    return _AGENCY_ACRONYMS.get(value.replace(" ", ""), value)


def _name_points(query: LookupQuery, defendant: str, reasons: list[str]) -> Optional[int]:
    """Puntos por nombre. None = el apellido no coincide (descartar)."""
    last, first, middle = split_defendant(defendant)
    points = 0
    use_middle = True
    q_last, q_middle = surname_key(query.last_name), surname_key(query.middle_name)
    # Apellidos compuestos: "Cruz Peraza", "Cruz-Peraza" y "Cruzperaza"
    # se tratan como el mismo apellido.
    if surname_key(last) == q_last:
        pass
    # Doble apellido repartido distinto: el artículo dice "Javier Rosado Martinez"
    # (Rosado como segundo nombre) y el portal "ROSADO MARTINEZ, JAVIER", o al revés.
    elif q_middle and surname_key(last) == q_middle + q_last:
        reasons.append(f"apellido compuesto ({last})")
        use_middle = False
    elif middle and surname_key(middle + " " + last) == q_last:
        reasons.append(f"apellido compuesto ({middle} {last})")
        use_middle = False
    # Solo uno de los dos apellidos: "ROSADO, JAVIER" para "Rosado Martinez".
    elif _one_of_compound(last, query.last_name) or _one_of_compound(query.last_name, last):
        points -= 5
        reasons.append(f"apellido parcial ({last})")
    else:
        reasons.append(f"apellido distinto ({last})")
        return None

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

    if use_middle and query.middle_name and middle:
        if middle.split()[0][:1] == normalize(query.middle_name)[:1]:
            points += W_MIDDLE
            reasons.append("segundo nombre coincide")
        else:
            points -= 10
            reasons.append(f"segundo nombre distinto ({middle})")
    return points


def _candidate_name_points(query: LookupQuery, candidate: CaseCandidate, reasons: list[str]) -> Optional[int]:
    """Como _name_points, con el nombre del acusado y, si no coincide, sus alias.

    El nombre principal manda: un alias solo se usa cuando el principal no
    coincide. Los portales que no traen alias (Orange) se comportan igual que antes.
    """
    main: list[str] = []
    points = _name_points(query, candidate.defendant, main)
    if points is not None:
        reasons.extend(main)
        return points
    best, best_reasons = None, []
    for alias in candidate.aliases:
        if normalize(alias) == normalize(candidate.defendant):
            continue
        tried: list[str] = []
        alias_points = _name_points(query, alias, tried)
        if alias_points is not None and (best is None or alias_points > best):
            best, best_reasons = alias_points, [f"coincide con un alias ({alias})"] + tried
    reasons.extend(best_reasons if best is not None else main)
    return best


def _one_of_compound(simple: str, compound: str) -> bool:
    """True si 'simple' es uno de los apellidos de 'compound' ('rosado' en 'Rosado Martinez')."""
    parts = [surname_key(p) for p in re.split(r"[\s\-]+", normalize(compound)) if p]
    return len(parts) >= 2 and surname_key(simple) in parts


def prelim_reasons(query: LookupQuery, candidate: CaseCandidate) -> str:
    """Por qué el nombre de un candidato coincide o no (para el log)."""
    reasons: list[str] = []
    _candidate_name_points(query, candidate, reasons)
    return ", ".join(reasons)


def prelim_score(query: LookupQuery, candidate: CaseCandidate) -> int:
    """Puntaje rápido con la fila de resultados, para decidir qué casos abrir."""
    reasons: list[str] = []
    points = _candidate_name_points(query, candidate, reasons)
    if points is None:
        return 0
    anchor = query.incident_date or query.arrest_date
    if anchor and candidate.filed_date:
        gap = abs((candidate.filed_date - anchor).days)
        points += max(0, 20 - gap // 3)
    elif query.date_from and candidate.filed_date and query.date_from <= candidate.filed_date <= (query.date_to or candidate.filed_date):
        points += 10   # solo se sabe el mes o el año: basta con caer en el rango
    if query.age is not None and candidate.dob and query.age_as_of:
        points += 10 if abs(age_on(candidate.dob, query.age_as_of) - query.age) <= 1 else -10
    return max(points, 1)


def _filed_points(query: LookupQuery, details: CaseDetails) -> Optional[tuple[int, str]]:
    """Fecha en que se abrió el caso contra la del arresto o el incidente.

    Solo se usa cuando los cargos no traen fechas de ofensa ni de arresto
    (Civitek no las muestra). El caso suele abrirse pocos días después del
    arresto; mucho después no resta (pudo ser por una orden de arresto).
    """
    if any(c.offense_date or c.arrest_date for c in details.charges):
        return None
    anchor = query.arrest_date or query.incident_date
    filed = details.candidate.filed_date
    if not anchor or not filed:
        return None
    gap = (filed - anchor).days
    if -3 <= gap <= 10:
        return W_FILED, f"caso abierto {gap} día(s) después del hecho"
    if -3 <= gap <= 60:
        return W_FILED // 2, f"caso abierto {gap} días después del hecho"
    return 0, f"caso abierto lejos del hecho ({gap} días)"


def _arrest_gap(query: LookupQuery, details: CaseDetails) -> tuple[Optional[int], bool]:
    """(días entre el arresto del artículo y el del caso, si salió de la fecha "Initial").

    La fecha "Initial" de Civitek solo cuenta si coincide (hasta 3 días): a veces
    es la de un cargo agregado después (Vaughn, Sumter), así que lejos no resta.
    """
    if not query.arrest_date:
        return None, False
    real = [c.arrest_date for c in details.charges if c.arrest_date]
    if real:
        return min(abs((d - query.arrest_date).days) for d in real), False
    initial = [c.initial_date for c in details.charges if c.initial_date]
    if initial:
        gap = min(abs((d - query.arrest_date).days) for d in initial)
        return (gap if gap <= 3 else None), True
    return None, False


def _offense_ignored(query: LookupQuery, details: CaseDetails) -> bool:
    """La fecha de ofensa no cuenta si está lejos del incidente pero el arresto coincide.

    En arrestos por orden, registro de ofensores o casos viejos la ofensa puede
    ser de años antes, y a veces viene mal cargada: Gordon (Hernando,
    26001035CFAXMX) tiene ofensa 11/07/2007 en los tres cargos, incluido el
    nombre falso que dio el día del arresto (17/07/2026).
    """
    offense_dates = [c.offense_date for c in details.charges if c.offense_date]
    if not query.incident_date or not offense_dates:
        return False
    offense_gap = min(abs((d - query.incident_date).days) for d in offense_dates)
    arrest_gap, _ = _arrest_gap(query, details)
    return offense_gap > 10 and arrest_gap is not None and arrest_gap <= 1


def _offense_on_arrest_day(query: LookupQuery, details: CaseDetails) -> Optional[int]:
    """Días entre la ofensa y el arresto de la nota, si la nota no da fecha del incidente (hasta 1 día).

    En un arresto en flagrancia (resistencia, huida) la ofensa es del mismo día:
    Rackley (Nassau, 26MM000504AXYX) era buscado por un robo de otro estado sin
    fecha y el caso local es la resistencia del 17/09/2026, el día del arresto.
    Solo suma: en arrestos por orden la ofensa puede ser de meses antes
    (Rodriguez, Orange: ofensa de 2024, arresto en 2025), y entonces no cuenta.
    """
    if query.incident_date or not query.arrest_date:
        return None
    offense_dates = [c.offense_date for c in details.charges if c.offense_date]
    if not offense_dates:
        return None
    gap = min(abs((d - query.arrest_date).days) for d in offense_dates)
    return gap if gap <= 1 else None


def max_points(query: LookupQuery, details: CaseDetails) -> int:
    """Puntos alcanzables con lo que se sabe de la consulta y lo que trae el caso."""
    c = details.candidate
    has_middle = bool(split_defendant(c.defendant)[2])
    total = W_NAME
    total += W_MIDDLE if query.middle_name and has_middle else 0
    # La ofensa ignorada (ver _offense_ignored) no resta, pero sigue contando aquí: así un
    # caso viejo servido el mismo día no empata con el del hecho, cuya ofensa sí coincide.
    total += W_OFFENSE if query.incident_date and any(x.offense_date for x in details.charges) else 0
    total += W_OFFENSE if _offense_on_arrest_day(query, details) is not None else 0
    total += W_ARREST if _arrest_gap(query, details)[0] is not None else 0
    total += W_AGENCY if query.agency and any(x.agency for x in details.charges) else 0
    anchor = query.age_as_of or query.incident_date or query.arrest_date
    total += W_AGE if query.age is not None and c.dob and anchor else 0
    # Si la nota trae cargos reconocibles y el caso tiene cargos, cuentan aunque el caso no
    # tenga ninguno reconocible: así un caso de otra cosa (una multa) no gana solo por el nombre.
    total += W_CHARGES if categorize(query.charges) and any(x.description for x in details.charges) else 0
    total += W_FILED if _filed_points(query, details) is not None else 0
    total += W_COURT if _court_counts(_court_gap(query, details)) else 0
    return total


def _court_counts(gap: Optional[int]) -> bool:
    """Si la fecha de condena entra en el máximo posible.

    Entre COURT_CLOSE_DAYS y COURT_FAR_DAYS no suma ni resta, así que tampoco cuenta en el
    máximo (si contara, restaría). Lopez (DeSoto, 30/10/2026): veredicto el 23/06/2026 y
    sentencia en octubre, 127 días después; con ese tramo en el máximo quedaba en 54 %.
    """
    return gap is not None and not (COURT_CLOSE_DAYS < gap <= COURT_FAR_DAYS)


def _court_gap(query: LookupQuery, details: CaseDetails) -> Optional[int]:
    """Días entre la fecha de condena de la nota y la fecha "Court" más cercana del caso.

    Las fechas "Court" posteriores a la nota no cuentan: son de después (una apelación,
    una nueva sentencia) y no lo que cuenta la nota (Rivers, Glades: nota de noviembre
    de 2025, "Court" del CF en julio de 2026). None si falta alguno de los dos lados.
    """
    if not query.court_dates:
        return None
    limit = query.published_date + timedelta(days=3) if query.published_date else None
    dates = [c.court_date for c in details.charges
             if getattr(c, "court_date", None) and (not limit or c.court_date <= limit)]
    if not dates:
        return None
    return min(abs((d - q).days) for d in dates for q in query.court_dates)


def _day(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _how_long(days: int) -> str:
    """'45 days', '7 months', 'more than 4 years'."""
    if days < 60:
        return _plural(days, "day")
    if days < 730:
        return _plural(round(days / 30.4), "month")
    return f"more than {_plural(days // 365, 'year')}"


def _name_checks(reasons: list[str]) -> list[dict]:
    """Los motivos del nombre (en español, para la consola) en inglés para la web."""
    checks = []
    if any(r.startswith("coincide con un alias") for r in reasons):
        checks.append({"ok": True, "text": "Same name (listed as an alias in the case)"})
    elif "nombre exacto" in reasons:
        checks.append({"ok": True, "text": "Same name"})
    elif any(r.startswith("nombre parecido") for r in reasons):
        checks.append({"ok": True, "text": "Similar name"})
    if any(r.startswith("apellido parcial") for r in reasons):
        checks.append({"ok": False, "text": "Only one of the last names matches"})
    if "segundo nombre coincide" in reasons:
        checks.append({"ok": True, "text": "Same middle initial"})
    for r in reasons:
        if r.startswith("segundo nombre distinto"):
            checks.append({"ok": False, "text": "Different middle name" + r[len("segundo nombre distinto"):]})
    return checks


def score_case(query: LookupQuery, details: CaseDetails) -> MatchScore:
    reasons: list[str] = []
    name_points = _candidate_name_points(query, details.candidate, reasons)
    if name_points is None:
        return MatchScore(0, reasons, disqualified=True)
    score = name_points
    checks = _name_checks(reasons)

    # Fecha de la ofensa contra la fecha del incidente. Graduada: el mismo día
    # vale más que 1-3 días, para separar incidentes de la misma persona que
    # caen en días seguidos (visto en Orange: casos del 01/08 y 03/08/2023).
    offense_dates = [c.offense_date for c in details.charges if c.offense_date]
    offense_gap = None
    if _offense_ignored(query, details):
        gap = min(abs((d - query.incident_date).days) for d in offense_dates)
        nearest = min(offense_dates, key=lambda d: abs((d - query.incident_date).days))
        reasons.append(f"fecha de ofensa ignorada ({gap} días): el arresto coincide")
        checks.append({"ok": False, "text": f"Offense date in the record doesn't fit ({_day(nearest)}), "
                                            "but the arrest date matches"})
    elif query.incident_date and offense_dates:
        gap = min(abs((d - query.incident_date).days) for d in offense_dates)
        nearest = min(offense_dates, key=lambda d: abs((d - query.incident_date).days))
        offense_gap = gap
        if gap == 0:
            checks.append({"ok": True, "text": "Offense date matches the incident"})
        else:
            side = "before" if nearest < query.incident_date else "after"
            checks.append({"ok": gap <= 10, "text": f"Offense date {_plural(gap, 'day')} {side} the incident "
                                                    f"({_day(nearest)})"})
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
    else:
        gap = _offense_on_arrest_day(query, details)
        if gap is not None:
            score += W_OFFENSE if gap == 0 else 22
            reasons.append(f"fecha de ofensa a {gap} día(s) del arresto (la nota no da fecha del incidente)")
            checks.append({"ok": True, "text": f"Offense date matches the arrest date ({_day(query.arrest_date)})"
                                               if gap == 0 else
                                               f"Offense date 1 day from the arrest date ({_day(query.arrest_date)})"})

    # Fecha de arresto (también graduada).
    arrest_gap, from_initial = _arrest_gap(query, details)
    if arrest_gap is not None:
        gap = arrest_gap
        if gap <= 3:
            checks.append({"ok": True, "text": "Arrest date matches" if gap == 0
                           else f"Arrest date {_plural(gap, 'day')} from the story's"})
        elif gap > 10:
            checks.append({"ok": False, "text": f"Arrest date {_plural(gap, 'day')} from the story's"})
        points = {0: W_ARREST, 1: 12, 2: 7, 3: 4}.get(gap)
        if points is not None:
            score += points
            reasons.append(("fecha de arresto coincide" if gap == 0 else f"fecha de arresto a {gap} día(s)")
                           + (" (Initial)" if from_initial else ""))
        elif gap > 10:
            score -= 10
            reasons.append(f"fecha de arresto lejana ({gap} días)")

    # Arresto anotado ("Initial") cerca de la fecha de la nota aunque la nota no dé la
    # del arresto: solo informa (no suma), y deja mostrar el caso como el más cercano
    # cuando la ofensa es de semanas antes (Koepke, Gulf: material enviado el 10/08,
    # allanamiento y arresto el 11/09, "Initial" el 15/09).
    initial_gap = _initial_gap(query, details)
    if arrest_gap is None and initial_gap is not None and initial_gap <= INITIAL_NEAR_DAYS:
        checks.append({"ok": True, "text": "Arrest recorded on the story's date" if initial_gap == 0
                       else f"Arrest recorded {_plural(initial_gap, 'day')} from the story's date"})

    # Fecha de condena o sentencia (notas de condena: muchas veces la única fecha).
    court_gap = _court_gap(query, details)
    if court_gap is not None:
        if court_gap <= COURT_NEAR_DAYS:
            score += W_COURT if court_gap == 0 else W_COURT - 5
            reasons.append("fecha de condena coincide" if court_gap == 0
                           else f"fecha de condena a {court_gap} día(s)")
            checks.append({"ok": True, "text": "Court date matches" if court_gap == 0
                           else f"Court date {_plural(court_gap, 'day')} from the story's"})
        elif court_gap <= COURT_CLOSE_DAYS:
            score += W_COURT // 2
            reasons.append(f"fecha de condena a {court_gap} días")
            checks.append({"ok": True, "text": f"Court date {_plural(court_gap, 'day')} from the story's"})
        elif court_gap > COURT_FAR_DAYS:
            score -= 15
            reasons.append(f"fecha de condena lejana ({court_gap} días)")
            checks.append({"ok": False, "text": f"Court date {_how_long(court_gap)} from the story's"})
        else:
            reasons.append(f"fecha de condena a {court_gap} días (no suma)")

    # Sin fechas en los cargos: la fecha en que se abrió el caso.
    filed = _filed_points(query, details)
    if filed:
        score += filed[0]
        reasons.append(filed[1])
    opened = _opened_after(query, details)
    if opened is not None and (filed or opened > 60):
        # Sin puntos propios cuando hay fechas de ofensa, pero a la persona que revisa le sirve.
        checks.append({"ok": bool(filed and filed[0] > 0),
                       "text": f"Case opened {_how_long(opened)} after the incident"})

    # Agencia que arrestó.
    agencies = {normalize_agency(c.agency) for c in details.charges if c.agency}
    if query.agency and agencies:
        q_agency = normalize_agency(query.agency)
        if any(q_agency == a or _similar(q_agency, a) >= 0.8 for a in agencies):
            score += W_AGENCY
            reasons.append("agencia coincide")
            checks.append({"ok": True, "text": "Same arresting agency"})
        else:
            score -= 10
            reasons.append(f"agencia distinta ({', '.join(sorted(agencies))})")
            checks.append({"ok": False, "text": "Different arresting agency"})

    # Edad reportada contra fecha de nacimiento.
    dob = details.candidate.dob
    anchor: Optional[date] = query.age_as_of or query.incident_date or query.arrest_date
    if query.age is not None and dob and anchor:
        real_age = age_on(dob, anchor)
        if abs(real_age - query.age) <= 1:
            score += W_AGE
            reasons.append(f"edad coincide ({real_age})")
            checks.append({"ok": True, "text": f"Same age ({real_age})"})
        else:
            score -= 15
            reasons.append(f"edad distinta ({real_age} vs {query.age})")
            checks.append({"ok": False, "text": f"Different age ({real_age} in the record, {query.age} in the story)"})

    # Cargos: categorías del artículo contra categorías del caso. Desempata
    # casos del mismo día (p. ej. un hurto y un burglary en el mismo arresto).
    q_cats = categorize(query.charges)
    c_cats = categorize(c.description for c in details.charges)
    if q_cats and c_cats:
        shared = q_cats & c_cats
        if shared:
            score += round(W_CHARGES * len(shared) / len(q_cats))
            reasons.append(f"cargos coinciden ({', '.join(sorted(shared))})")
            checks.append({"ok": True, "text": f"Similar charges: {_charge_list(details, shared)}"})
        else:
            score -= 10
            reasons.append(f"cargos distintos ({', '.join(sorted(c_cats))})")
            checks.append({"ok": False, "text": f"Different charges: {_charge_list(details)}"})
    elif q_cats and any(c.description for c in details.charges):
        # Sin puntos ni resta: no hay con qué comparar, pero tampoco cuenta como coincidencia.
        reasons.append("cargos no comparables")
        checks.append({"ok": False, "text": f"Charges couldn't be compared: {_charge_list(details)}"})

    possible = max_points(query, details)
    confidence = round(100 * max(score, 0) / possible)
    reasons.append(f"{max(score, 0)}/{possible} puntos posibles")
    # Lo que coincide primero, después lo que no (así se lee en la web).
    checks = [c for c in checks if c["ok"]] + [c for c in checks if not c["ok"]]
    return MatchScore(min(confidence, 100), reasons, checks=checks, offense_gap=offense_gap,
                      initial_gap=initial_gap)


INITIAL_NEAR_DAYS = 7


def _initial_gap(query: LookupQuery, details: CaseDetails) -> Optional[int]:
    anchor = query.arrest_date or query.incident_date
    dates = [c.initial_date for c in details.charges if getattr(c, "initial_date", None)]
    if not anchor or not dates:
        return None
    return min(abs((d - anchor).days) for d in dates)


def _charge_list(details: CaseDetails, only: Optional[set] = None, limit: int = 2) -> str:
    """'home invasion robbery' o 'battery, grand theft and 2 more'. Con `only`, solo
    los cargos de esas categorías (los que coinciden con el artículo)."""
    names: list[str] = []
    for c in details.charges:
        if only and not (categorize([c.description or ""]) & only):
            continue
        name = re.sub(r"\s+", " ", c.description or "").strip().lower()
        if name and name not in names:
            names.append(name)
    shown = ", ".join(names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def _opened_after(query: LookupQuery, details: CaseDetails) -> Optional[int]:
    """Días entre el hecho (o el arresto) y la apertura del caso."""
    anchor = query.incident_date or query.arrest_date
    filed = details.candidate.filed_date
    return (filed - anchor).days if anchor and filed and filed >= anchor else None


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


# --- Empate entre el caso del arresto y casos abiertos después --------------
# Kolotka (Glades, 28/10/2026): la nota cuenta el arresto (salió vestido de mujer).
# Empataban el MM del arresto (obstrucción con disfraz, "Initial" a 2 días) y los CF
# de hurto de la misma investigación, con "Initial" 22 días después. Para pedir el
# footage del arresto, el caso es el que registra el arresto en esa fecha.

ARREST_NEAR_DAYS = 3     # "Initial" a esta distancia o menos: es el arresto de la nota
ARREST_FAR_DAYS = 10     # los demás deben tenerla a más de esto (registrados después)


def resolve_by_arrest_date(
    scored: list[tuple[MatchScore, CaseDetails]],
    min_score: int = MIN_SCORE,
    min_margin: int = MIN_MARGIN,
) -> Optional[tuple[MatchScore, CaseDetails]]:
    """Desempata cuando solo uno de los casos empatados registra el arresto en la fecha de la nota.

    Gana si su "Initial" está a ARREST_NEAR_DAYS días o menos y la de todos los demás
    empatados está a más de ARREST_FAR_DAYS. Si a algún otro le falta la fecha, o hay
    dos cerca, sigue el empate. None si no aplica.
    """
    valid = sorted((s for s in scored if not s[0].disqualified), key=lambda s: s[0].score, reverse=True)
    if not valid or valid[0][0].score < min_score:
        return None
    top = valid[0][0].score
    close = [s for s in valid if top - s[0].score < min_margin]
    if len(close) < 2:
        return None
    near = [s for s in close if s[0].initial_gap is not None and s[0].initial_gap <= ARREST_NEAR_DAYS]
    if len(near) != 1 or near[0][0].score < min_score:
        return None
    others = [s for s in close if s is not near[0]]
    if all(s[0].initial_gap is not None and s[0].initial_gap > ARREST_FAR_DAYS for s in others):
        return near[0]
    return None


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
    - Portales que no muestran número de reporte, fecha de arresto ni agencia
      (Civitek): mismo acusado y fechas de ofensa a 1 día o menos. Una racha
      de hechos (robo, tiroteo, huida) se abre como varios casos del mismo día.
      También mismo acusado con la misma fecha "Initial" (la del arresto):
      Callahan (DeSoto) tiene tres compras controladas de diciembre y enero y el
      caso de la escopeta, todos cargados en el arresto del 10/01/2025.
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
    if bool(ka[0]) and bool(ka[1]) and ka == kb:
        return True
    if any(c.control_number or c.arrest_date or c.agency for c in a.charges + b.charges):
        return False                     # el portal sí trae esos datos y no coinciden
    if normalize(a.candidate.defendant) != normalize(b.candidate.defendant):
        return False
    # Misma fecha "Initial" (Civitek): el arresto que abrió los dos casos es el mismo.
    initial_a = {c.initial_date for c in a.charges if getattr(c, "initial_date", None)}
    initial_b = {c.initial_date for c in b.charges if getattr(c, "initial_date", None)}
    if initial_a and initial_a == initial_b:
        return True
    if not (ka[0] and kb[0]):
        return False
    return min(abs((x - y).days) for x in ka[0] for y in kb[0]) <= 1


def _offense_to_initial(details: CaseDetails) -> int:
    """Días entre la ofensa y la fecha "Initial" (el arresto). Grande si no se sabe.

    Entre casos del mismo arresto, el que tiene la ofensa más cerca del arresto es el que
    abrió esa detención; los demás son órdenes viejas servidas ese día. Callahan (DeSoto):
    el 2024MM000053 (parafernalia del 13/09/2023) tiene "Initial: 01/10/2025" como los del
    tráfico, pero su declaración jurada es del hecho de 2023, no del arresto de 2025.
    """
    gaps = [abs((c.initial_date - c.offense_date).days) for c in details.charges
            if getattr(c, "initial_date", None) and c.offense_date]
    return min(gaps) if gaps else 10**6


def _preference(details: CaseDetails, has_document: Callable[[CaseDetails], bool]):
    match = _CASE_TYPE_RE.match(details.candidate.case_number)
    rank = _TYPE_RANK.get(match.group(1) if match else "", 9)
    filed = details.candidate.filed_date or date.max
    # Con documento primero; después el caso principal (no el consolidado en otro); después
    # el de la ofensa más cercana al arresto (solo Civitek trae "Initial"; en los demás es igual).
    return (0 if has_document(details) else 1, 1 if details.consolidated else 0, rank,
            _offense_to_initial(details), filed)


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
    if len(close) < 2:
        return None
    if not all(same_incident(top[1], s[1]) for s in close[1:]):
        close = _drop_consolidated(close)
        if not close or not all(same_incident(close[0][1], s[1]) for s in close[1:]):
            return None
    return min(close, key=lambda s: _preference(s[1], has_document))


def related_cases(winner: CaseDetails, scored: list[tuple[MatchScore, CaseDetails]]) -> list[str]:
    """Otros casos del mismo arresto que el elegido (para informarlos)."""
    top = max((m.score for m, d in scored if d is winner), default=0)
    return [d.candidate.case_number for m, d in scored
            if d is not winner and not m.disqualified
            and (same_incident(winner, d) or same_filing(winner, d) or _merged_into(d, winner, m.score, top))]


def _merged_into(case: CaseDetails, main: CaseDetails, score: int, top: int) -> bool:
    """Caso del mismo acusado que la fiscalía unió al elegido y que empataba con él."""
    return (case.consolidated and not main.consolidated and top - score < MIN_MARGIN
            and normalize(case.candidate.defendant) == normalize(main.candidate.defendant))


def _drop_consolidated(close: list[tuple[MatchScore, CaseDetails]]) -> list[tuple[MatchScore, CaseDetails]]:
    """Sin los casos que la fiscalía unió a otro del mismo acusado que también está en el empate.

    Hannah (Baker): 23000044 dice "CASE CONSOLIDATED INTO 02-2023-CF-067-B" y empataba
    con 23000067 (el principal). Las ofensas difieren, así que no eran "mismo arresto".
    """
    mains = [s for s in close if not s[1].consolidated]
    names = {normalize(s[1].candidate.defendant) for s in mains}
    return [s for s in close if not s[1].consolidated or normalize(s[1].candidate.defendant) not in names]


def same_filing(a: CaseDetails, b: CaseDetails) -> bool:
    """Misma persona, casos abiertos el mismo día: salen del mismo arresto.

    Solo se usa para MOSTRAR el otro caso ("Same arrest, separate case"); no
    cambia cuál se elige. McQuiston (Levy, 07/10/2026): el 685 (agresión al
    oficial) y el 686 (pornografía infantil, la nota) se abrieron el 08/10 con
    números seguidos, cada uno con su mittimus del mismo arresto.
    """
    fa, fb = a.candidate.filed_date, b.candidate.filed_date
    return bool(fa and fb and fa == fb
                and normalize(a.candidate.defendant) == normalize(b.candidate.defendant))
