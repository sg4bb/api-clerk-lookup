"""Highlands (30/10/2026): Uriarte, un caso con los cargos de la nota contra un "open container"."""

import unittest
from datetime import date

from core.matcher import pick_best, resolve_by_charges, score_case
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
from tests.test_people_and_closest import run

OFFENSE, ARREST = date(2026, 8, 10), date(2026, 8, 18)
CHARGES = ["lewd or lascivious battery", "lewd or lascivious molestation", "interference with child custody",
           "contributing to the delinquency/dependence of a minor", "providing alcohol to a minor"]


def charge(description, offense, initial):
    return Charge(offense_date=offense, description=description, statute=None, arrest_date=None, agency=None,
                  control_number=None, initial_date=initial)


def uriarte(number, filed, charges, docket=()):
    candidate = CaseCandidate(case_number=number, defendant="URIARTE, FRANKLIN LEONEL", case_type="Felony",
                              status="OPEN", dob=None, filed_date=filed, detail_ref="x")
    entries = [DocketEntry(date=filed, description=d, doc_ref=f"doc{n}", number=n) for n, d in docket]
    return CaseDetails(candidate, charges, entries)


# Los dos casos reales (26000795CFAXMX y 26000602MMAXMX), resumidos.
LEWD = uriarte("26000795CFAXMX", date(2026, 8, 19),
               [charge("LEWD OR LASCIVIOUS BATTERY", OFFENSE, ARREST)] * 5
               + [charge("LEWD/LASC MOLESTN, VICTIM 12-16, DEF OVER 18", OFFENSE, ARREST)],
               docket=[(3, "ARREST AFFIDAVIT")])
OPEN_CONTAINER = uriarte("26000602MMAXMX", date(2026, 8, 21),
                         [charge("OPEN CONTAINER-COUNTY ORDINANCE", ARREST, date(2026, 8, 21))])


def query(**dates):
    return LookupQuery(first_name="Franklin", last_name="Uriarte", county="highlands", state="FL", age=24,
                       charges=CHARGES, agency="Highlands County Sheriff's Office",
                       published_date=date(2026, 8, 20), **dates)


class UriarteTest(unittest.TestCase):
    # Lo que puede sacar el modelo de la nota: el día de publicación (como nemotron),
    # el inicio de la investigación como incidente, o la parada como arresto.
    READINGS = [dict(incident_date=date(2026, 8, 20), arrest_date=date(2026, 8, 20)),
                dict(incident_date=ARREST),
                dict(arrest_date=ARREST)]

    def test_case_with_the_story_charges_wins(self):
        for dates in self.READINGS:
            with self.subTest(**{k: str(v) for k, v in dates.items()}):
                result, _ = run([query(**dates)], {"Uriarte": [LEWD, OPEN_CONTAINER]})
                self.assertEqual((result.status, result.case_number), ("found", "26000795CFAXMX"))

    def test_only_when_the_others_do_not_match(self):
        """Si los dos empatados tienen los cargos de la nota, sigue el empate."""
        other = uriarte("26000900CFAXMX", date(2026, 8, 21),
                        [charge("LEWD/LASC MOLESTN, VICTIM 12-16, DEF OVER 18", OFFENSE, ARREST)])
        q = query(incident_date=date(2026, 8, 20), arrest_date=date(2026, 8, 20))
        scored = [(score_case(q, d), d) for d in (LEWD, other)]
        self.assertEqual(pick_best(scored)[1], "ambiguous")
        self.assertIsNone(resolve_by_charges(scored))

    def test_story_without_known_charges(self):
        """Sin cargos reconocibles en la nota no hay con qué desempatar."""
        q = query(incident_date=date(2026, 8, 20), arrest_date=date(2026, 8, 20))
        q.charges = ["a crime"]
        scored = [(score_case(q, d), d) for d in (LEWD, OPEN_CONTAINER)]
        self.assertIsNone(resolve_by_charges(scored))


if __name__ == "__main__":
    unittest.main()
