"""Ruma (Indian River, 05/11/2026): seis casos casi iguales de deepfakes, de cuatro arrestos.

    2026 CF 000944 A  arresto 02/09  Vero Beach PD  reporte 2026-000613
    2026 CF 000965 A  arresto 09/09  Vero Beach PD  reporte 2026-007288
    2026 CF 001068 A  arresto 02/10  Vero Beach PD  reporte 2026-007500
    2026 CF 001126 A  arresto 13/10  Vero Beach PD  reporte 2026-007938
    2026 CF 001178 A  orden entregada el 27/10 por el Sheriff, reporte 26-8500
    2026 CF 001179 A  la misma orden (mismo reporte)
"""

import unittest
from datetime import date

from core.matcher import (pick_best, related_cases, resolve_by_arrest_date, resolve_by_publication,
                          resolve_same_incident, score_case)
from core.models import CaseCandidate, CaseDetails, Charge, LookupQuery

VBPD, SHERIFF = "VERO BEACH - POLICE DEPT", "INDIAN RIVER COUNTY - SHERIFF"
CHARGES = ["GENERATING ANY ALTERED SEXUAL DEPICTION OF A PERSON (836.13(2))",
           "PROMOTION OF ALTERED SEXUAL DEPICTION OF PERSON (836.13(4))",
           "SEXUAL CYBERHARASSMENT (784.049 (3a))", "STALKING (784.048(2))"]


def case(number, arrest, agency, report, offense=date(2026, 6, 10), filed=None):
    candidate = CaseCandidate(number, "RUMA, PETER SOLOMON", "CF", "OPEN", date(1989, 1, 9), filed or arrest, "x")
    charges = [Charge(offense, text, None, arrest, agency, report, initial_date=arrest) for text in CHARGES]
    return CaseDetails(candidate, charges, [])


C944 = case("2026 CF 000944 A", date(2026, 9, 2), VBPD, "2026-000613", offense=date(2026, 8, 1))
C965 = case("2026 CF 000965 A", date(2026, 9, 9), VBPD, "2026-007288", offense=date(2026, 6, 5))
C1068 = case("2026 CF 001068 A", date(2026, 10, 2), VBPD, "2026-007500")
C1126 = case("2026 CF 001126 A", date(2026, 10, 13), VBPD, "2026-007938")
C1178 = case("2026 CF 001178 A", date(2026, 10, 27), SHERIFF, "26-8500")
C1179 = case("2026 CF 001179 A", date(2026, 10, 27), SHERIFF, "26-8500")


def query(**kw):
    base = dict(first_name="Peter", middle_name="Solomon", last_name="Ruma", county="indian_river", age=37,
                agency="Vero Beach Police Department",
                charges=["sexual cyberharassment", "stalking", "promotion of an altered sexual depiction"])
    base.update(kw)
    return LookupQuery(**base)


class RumaTest(unittest.TestCase):
    def test_story_without_dates_drops_cases_arrested_after_it(self):
        """Nota de cbs12 del 2/9 sin fechas: empataban el caso del 2/9 y el del arresto del 9/9."""
        q = query(published_date=date(2026, 9, 2), age_as_of=date(2026, 9, 2))
        scored = [(score_case(q, d), d) for d in (C944, C965)]
        self.assertEqual(pick_best(scored)[1], "ambiguous")
        self.assertIsNone(resolve_same_incident(scored, lambda d: True))
        self.assertIsNone(resolve_by_arrest_date(scored))
        self.assertEqual([m.after_publication for m, _ in scored], [False, True])
        self.assertEqual(resolve_by_publication(scored)[1].candidate.case_number, "2026 CF 000944 A")

    def test_publication_needs_every_other_case_to_be_later(self):
        q = query(published_date=date(2026, 10, 30))
        scored = [(score_case(q, d), d) for d in (C1068, C1126)]   # los dos antes de la nota
        self.assertIsNone(resolve_by_publication(scored))

    def test_dates_in_the_story_turn_publication_off(self):
        q = query(arrest_date=date(2026, 9, 2), published_date=date(2026, 9, 2))
        self.assertIsNone(score_case(q, C965).after_publication)

    def test_warrant_served_by_the_sheriff(self):
        """Arresto del 27/10: la orden la investigó Vero Beach PD y la entregó el Sheriff. Las dos
        agencias son de Indian River: la diferencia no resta y la orden gana por la fecha."""
        q = query(arrest_date=date(2026, 10, 27), published_date=date(2026, 10, 30), age_as_of=date(2026, 10, 30))
        scored = [(score_case(q, d), d) for d in (C1179, C1178, C1126, C1068)]
        by_case = {d.candidate.case_number: m for m, d in scored}
        self.assertIn("no resta", " ".join(by_case["2026 CF 001179 A"].reasons))
        self.assertGreater(by_case["2026 CF 001179 A"].score, by_case["2026 CF 001126 A"].score)
        self.assertEqual(pick_best(scored)[1], "ambiguous")
        winner = resolve_by_arrest_date(scored, has_document=lambda d: True)
        self.assertIn(winner[1].candidate.case_number, ("2026 CF 001178 A", "2026 CF 001179 A"))

    def test_two_cases_of_the_same_arrest_on_the_story_date(self):
        """Los dos casos del arresto de la fecha de la nota (mismo reporte) contra otros arrestos
        con más puntos por agencia: aquí la agencia del caso es de otro condado y sí resta."""
        q = query(arrest_date=date(2026, 10, 27), published_date=date(2026, 10, 30), age_as_of=date(2026, 10, 30))
        fhp = [case(n, date(2026, 10, 27), "FLORIDA HIGHWAY PATROL", "26-8500")
               for n in ("2026 CF 001179 A", "2026 CF 001178 A")]
        scored = [(score_case(q, d), d) for d in (*fhp, C1126, C1068)]
        self.assertEqual(pick_best(scored)[1], "ambiguous")
        winner = resolve_by_arrest_date(scored, has_document=lambda d: True)
        self.assertIn(winner[1].candidate.case_number, ("2026 CF 001178 A", "2026 CF 001179 A"))
        other = {"2026 CF 001178 A", "2026 CF 001179 A"} - {winner[1].candidate.case_number}
        self.assertEqual(related_cases(winner[1], scored), sorted(other))

    def test_two_near_cases_of_different_arrests_stay_tied(self):
        q = query(arrest_date=date(2026, 10, 27))
        other_day = case("2026 CF 001180 A", date(2026, 10, 26), VBPD, "2026-009999")
        scored = [(score_case(q, d), d) for d in (C1178, other_day, C1068)]
        self.assertIsNone(resolve_by_arrest_date(scored, has_document=lambda d: True))


if __name__ == "__main__":
    unittest.main()
