"""St. Lucie (09/11/2026): la operación de Port St. Lucie PD, los apellidos con "St." y Arvelo.

Johnson, Albert: cinco casos cargados en el arresto del 15/10/2026 ("Initial 10/15/2026"),
cada uno con su reporte:
    2026CF002675 A  incautación del 15/10 (cocaína, armas)   reporte 202625901
    2026CF002674 A  venta del 11/09                          reporte 202622839
    2026CF002673 A  venta del 16/09                          reporte 202623234
    2026CF002672 A  venta del 01/10                          reporte 202624647
    2026CF002671 A  venta del 15/10                          reporte 202625896
"""

import unittest
from datetime import date

from core.matcher import pick_best, related_cases, resolve_same_incident, same_incident, score_case
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
from core.utils import last_name_variants, surname_key

PSLPD = "PORT ST. LUCIE POLICE DEPARTMENT"
INITIAL = date(2026, 10, 15)


def case(number, offense, report, charges, initial=INITIAL, agency=PSLPD, defendant="JOHNSON, ALBERT"):
    candidate = CaseCandidate(number, defendant, "CF", "OPEN", date(1990, 7, 1), None, "x")
    return CaseDetails(candidate, [Charge(offense, text, None, None, agency, report, initial_date=initial)
                                   for text in charges], [])


SEIZURE = case("2026CF002675 A", date(2026, 10, 15), "202625901",
               ["COCAINE-TRAFFIC-TRAFFICKING IN COCAINE 28 GRAMS TO 150 KG (893.135.1B1)",
                "POSS OF WEAPON-OR AMMO BY GANG-RELATED FELON OR DELINQUENT (790.23.4)"])
SALES = [case(n, d, r, ["DRUGS-TRAFFIC-TRAFFIC FENTANYL/FENTANYL ANALOGUES 4 GRAMS (893.135.1C4A)",
                        "PUB ORD CRIMES-USE 2 WAY COMM DEVICE TO FACIL FELONY (934.215)"])
         for n, d, r in (("2026CF002674 A", date(2026, 9, 11), "202622839"),
                         ("2026CF002673 A", date(2026, 9, 16), "202623234"),
                         ("2026CF002672 A", date(2026, 10, 1), "202624647"),
                         ("2026CF002671 A", date(2026, 10, 15), "202625896"))]
QUERY = LookupQuery(first_name="Albert", last_name="Johnson", county="st_lucie", age=36,
                    published_date=date(2026, 10, 16), agency="Port St. Lucie Police Department",
                    charges=["fentanyl and cocaine trafficking", "cocaine sales near a park", "firearm-related offenses"])


class OneArrestManyReportsTest(unittest.TestCase):
    def test_cases_loaded_in_the_same_arrest(self):
        self.assertTrue(all(same_incident(SEIZURE, s) for s in SALES))

    def test_the_seizure_wins_and_the_sales_are_related(self):
        scored = sorted(((score_case(QUERY, d), d) for d in [*SALES, SEIZURE]), key=lambda s: -s[0].score)
        self.assertEqual(pick_best(scored)[1], "ambiguous")
        winner = resolve_same_incident(scored, lambda d: True)
        self.assertEqual(winner[1].candidate.case_number, "2026CF002675 A")
        self.assertEqual(sorted(related_cases(winner[1], scored)), sorted(s.candidate.case_number for s in SALES))

    def test_other_initial_date_is_another_arrest(self):
        later = case("2026CF002900 A", date(2026, 11, 2), "202627000", ["BATTERY"], initial=date(2026, 11, 2))
        self.assertFalse(same_incident(SEIZURE, later))

    def test_other_agency_is_another_arrest(self):
        sheriff = case("2026CF002676 A", date(2026, 10, 15), "26-1234", ["BATTERY"],
                       agency="ST. LUCIE COUNTY SHERIFF'S OFFICE")
        self.assertFalse(same_incident(SEIZURE, sheriff))

    def test_name_written_differently(self):
        """El portal puede escribir el nombre distinto en cada caso (segundo nombre o inicial)."""
        full = case("2026CF002674 A", date(2026, 9, 11), "202622839", ["FENTANYL"], defendant="JOHNSON, ALBERT LEE")
        initial = case("2026CF002673 A", date(2026, 9, 16), "202623234", ["FENTANYL"], defendant="JOHNSON, ALBERT L")
        self.assertTrue(same_incident(SEIZURE, full))
        self.assertTrue(same_incident(full, initial))
        other_middle = case("2026CF002672 A", date(2026, 10, 1), "202624647", ["FENTANYL"],
                            defendant="JOHNSON, ALBERT RAY")
        self.assertFalse(same_incident(full, other_middle))

    def test_other_birth_year_is_another_person(self):
        older = case("2026CF002670 A", date(2026, 10, 15), "202625800", ["FENTANYL"])
        older.candidate.dob = date(1960, 7, 1)
        self.assertFalse(same_incident(SEIZURE, older))

    def test_other_person_is_another_arrest(self):
        other = case("2026CF002677 A", date(2026, 10, 15), "202625999", ["BATTERY"], defendant="SMITH, TRISTAN")
        self.assertFalse(same_incident(SEIZURE, other))


class SaintSurnameTest(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(last_name_variants("St. Fleur"), ["St. Fleur", "St Fleur", "Saint Fleur", "StFleur"])
        self.assertEqual(last_name_variants("Saint Fleur")[:3], ["Saint Fleur", "St Fleur", "StFleur"])
        self.assertNotIn("St.", last_name_variants("St. Fleur"))
        self.assertNotIn("Fleur", last_name_variants("St. Fleur"))

    def test_same_surname(self):
        keys = {surname_key(s) for s in ("St. Fleur", "ST FLEUR", "Saint Fleur", "SAINT-FLEUR", "STFLEUR")}
        self.assertEqual(keys, {"stfleur"})
        self.assertEqual(surname_key("Saint"), "saint")          # un apellido "Saint" solo no cambia

    def test_other_compound_surnames_unchanged(self):
        self.assertEqual(last_name_variants("Cruz Peraza")[:3], ["Cruz Peraza", "CruzPeraza", "Cruz-Peraza"])


class VariantsCapTest(unittest.TestCase):
    """Con una suma por búsqueda, como mucho tres formas del apellido."""

    def searched_names(self, county):
        import tempfile
        from contextlib import contextmanager
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from adapters.benchmark_web import BenchmarkWebAdapter
        from core import lookup
        names = []

        class Fake(BenchmarkWebAdapter):
            def search(self, query, date_from, date_to):
                names.append(query.last_name)
                return []

            def throttle(self):
                pass

        @contextmanager
        def fake_browser(headless=False):
            yield SimpleNamespace(page=None, remote=False, live_view_url=None)

        q = LookupQuery(first_name="Devontrae", last_name="St. Fleur", county=county)
        with mock.patch.object(lookup, "open_browser", fake_browser), \
             mock.patch.object(lookup, "get_adapter", return_value=Fake), tempfile.TemporaryDirectory() as tmp:
            lookup.run_lookup(q, Path(tmp), captcha=object())
        return names

    def test_st_lucie(self):
        self.assertEqual(self.searched_names("st_lucie")[:3], ["St. Fleur", "St Fleur", "Saint Fleur"])
        self.assertNotIn("StFleur", self.searched_names("st_lucie"))

    def test_clay_keeps_all(self):
        self.assertIn("StFleur", self.searched_names("clay"))


class InitiatingReportTest(unittest.TestCase):
    """Arvelo: el mensaje nombraba "ARREST REPORT - NOT INITIATING CASE" (la detención de 2021)."""

    def test_initiating_case_first(self):
        from adapters.benchmark_web import BenchmarkWebAdapter
        from counties import get_county
        adapter = BenchmarkWebAdapter(page=None, county=get_county("st_lucie"), captcha=None)
        docket = [DocketEntry(date(2021, 9, 28), "ARREST REPORT - NOT INITIATING CASE ( V )", None, 95),
                  DocketEntry(date(2020, 10, 2), "ARREST REPORT - INITIATING CASE ( V )", None, 3)]
        details = CaseDetails(SEIZURE.candidate, [], docket)
        self.assertEqual(adapter.find_requestable_documents(details)[0].description,
                         "ARREST REPORT - INITIATING CASE ( V )")


if __name__ == "__main__":
    unittest.main()
