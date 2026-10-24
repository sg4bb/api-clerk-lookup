"""Varias personas en el artículo y el caso más cercano (Hernando, Alek Smith, 14/10/2026)."""

import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import lookup
from core.matcher import pick_best, score_case
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
from tests.test_civitek import AdapterStub

INCIDENT = date(2018, 10, 16)


def query(first, last, **kw) -> LookupQuery:
    return LookupQuery(first_name=first, last_name=last, county="hernando", state="FL",
                       incident_date=INCIDENT, charges=["using a firearm during a crime of violence "
                                                        "that resulted in the premeditated murder"], **kw)


def case(number, defendant, offense, description, filed, docket=()) -> CaseDetails:
    candidate = CaseCandidate(case_number=number, defendant=defendant, case_type="Felony 22-D", status="CLOSED",
                              dob=None, filed_date=filed, detail_ref="x")
    charges = [Charge(offense_date=offense, description=description, statute=None, arrest_date=None,
                      agency=None, control_number=None)]
    entries = [DocketEntry(date=filed, description=d, doc_ref=f"doc{n}", number=n) for n, d in docket]
    return CaseDetails(candidate, charges, entries)


# Lo que hay en el portal para cada persona (apellido -> casos).
COLON_2023 = case("23000080CFAXMX", "COLON, TIMOTHY LUIS", date(2018, 10, 9), "HOME INVASION ROBBERY",
                  date(2023, 1, 12))
COLON_2020 = case("20002047CFAXMX", "COLON, TIMOTHY LUIS", date(2020, 11, 25), "GRAND THEFT MOTOR VEHICLE",
                  date(2020, 12, 14))
# Otro hecho (2019), con un cargo de arma: no debe salir como el más cercano.
COLON_2019 = case("19000624CFAXMX", "COLON, TIMOTHY LUIS", date(2019, 3, 29), "POSSESS OF FIREARM BY FELON",
                  date(2019, 4, 1))
WILLIAMS_DRUGS = case("19000028CFAXMX", "WILLIAMS, ARTHUR", date(2018, 12, 28),
                      "POSSESSION OF A CONTROLLED SUBSTANCE", date(2019, 1, 9))
JONES_FOUND = case("26CF000111AX", "JONES, MARK", INCIDENT, "MURDER IN THE FIRST DEGREE WITH A FIREARM",
                   date(2018, 10, 17), docket=[(2, "ARREST AFFIDAVIT")])


def run(people, portal):
    """run_lookup con un adaptador falso: `portal` dice qué casos tiene cada apellido."""
    searched = []

    class FakeCivitek(AdapterStub):
        def search(self, q, date_from, date_to):
            searched.append(q.last_name)
            return [d.candidate for d in portal.get(q.last_name, [])]

        def open_case(self, candidate):
            return next(d for ds in portal.values() for d in ds if d.candidate is candidate)

        def download(self, details, entry, dest_dir):
            path = Path(dest_dir) / f"{entry.number}.pdf"
            path.write_bytes(b"%PDF-1.4 test")
            return path

        def throttle(self):
            pass

    @contextmanager
    def fake_browser(headless=False):
        yield SimpleNamespace(page=None, remote=False, live_view_url=None)

    with mock.patch.object(lookup, "open_browser", fake_browser), \
         mock.patch.object(lookup, "get_adapter", return_value=FakeCivitek), \
         tempfile.TemporaryDirectory() as tmp:
        result = lookup.run_lookup(people, Path(tmp), captcha=object())
    return result, searched


class SeveralPeopleTest(unittest.TestCase):
    def test_second_person_finds_the_case(self):
        result, searched = run([query("Arthur", "Williams"), query("Mark", "Jones")],
                               {"Williams": [WILLIAMS_DRUGS], "Jones": [JONES_FOUND]})
        self.assertEqual(result.status, "found")
        self.assertEqual(result.case_number, "26CF000111AX")
        self.assertEqual(result.person_index, 1)
        self.assertEqual(searched, ["Williams", "Jones"])
        self.assertEqual([(p["name"], p["status"]) for p in result.people_searched],
                         [("Arthur Williams", "not_found"), ("Mark Jones", "found")])

    def test_stops_at_the_first_found(self):
        result, searched = run([query("Mark", "Jones"), query("Arthur", "Williams")],
                               {"Williams": [WILLIAMS_DRUGS], "Jones": [JONES_FOUND]})
        self.assertEqual((result.status, result.person_index, searched), ("found", 0, ["Jones"]))
        self.assertEqual(len(result.people_searched), 1)

    def test_single_query_still_works(self):
        result, _ = run(query("Mark", "Jones"), {"Jones": [JONES_FOUND]})
        self.assertEqual(result.status, "found")
        self.assertEqual(result.people_searched, [{"name": "Mark Jones", "status": "found", "cases_checked": 1}])


class ClosestCaseTest(unittest.TestCase):
    def test_colon_case_is_the_closest(self):
        """Ninguno alcanza el 60 %: se muestra el de Colon (37 % en el portal real)."""
        result, _ = run([query("Arthur", "Williams"), query("Timothy", "Colon")],
                        {"Williams": [WILLIAMS_DRUGS], "Colon": [COLON_2020, COLON_2023]})
        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.person_index, 0)            # el primero, sin ganador
        closest = result.closest
        self.assertEqual(closest["case_number"], "23000080CFAXMX")
        self.assertEqual(closest["defendant"], "Timothy Colon")
        self.assertEqual(closest["case_type"], "Felony")
        self.assertEqual(closest["filed_date"], "2023-01-12")
        self.assertGreaterEqual(closest["score"], lookup.CLOSEST_MIN)
        texts = [(c["ok"], c["text"]) for c in closest["checks"]]
        self.assertEqual(texts, [
            (True, "Same name"),
            (True, "Offense date 7 days before the incident (Oct 9, 2018)"),
            (False, "Case opened more than 4 years after the incident"),
            (False, "Different charges: home invasion robbery"),
        ])
        self.assertEqual([p["cases_checked"] for p in result.people_searched], [1, 2])

    def test_other_incident_is_not_the_closest(self):
        """Corrida real del 17/10/2026: el artículo solo decía "using a firearm"; el caso de
        2019 (arma, 164 días después) sacaba 43 % y el correcto 37 %."""
        people = [query("Timothy", "Colon")]
        people[0].charges = ["using a firearm during a crime of violence"]
        result, _ = run(people, {"Colon": [COLON_2019, COLON_2020, COLON_2023]})
        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.closest["case_number"], "23000080CFAXMX")

    def test_similar_charges_list_only_the_matching_ones(self):
        q = query("Timothy", "Colon")
        q.charges = ["using a firearm during a crime of violence"]
        details = case("19000624CFAXMX", "COLON, TIMOTHY LUIS", INCIDENT, "TRAFFICKING IN COCAINE", date(2018, 10, 17))
        details.charges.append(Charge(offense_date=INCIDENT, description="POSSESS OF FIREARM BY FELON", statute=None,
                                      arrest_date=None, agency=None, control_number=None))
        texts = [c["text"] for c in score_case(q, details).checks]
        self.assertIn("Similar charges: possess of firearm by felon", texts)

    def test_weak_cases_are_not_shown(self):
        result, _ = run([query("Arthur", "Williams")], {"Williams": [WILLIAMS_DRUGS]})
        self.assertEqual(result.status, "not_found")
        self.assertIsNone(result.closest)

    def test_no_closest_when_found(self):
        result, _ = run([query("Mark", "Jones")], {"Jones": [JONES_FOUND]})
        self.assertIsNone(result.closest)


class ChecksTest(unittest.TestCase):
    def test_checks_follow_the_score(self):
        match = score_case(query("Mark", "Jones"), JONES_FOUND)
        self.assertIn({"ok": True, "text": "Offense date matches the incident"}, match.checks)
        self.assertIn({"ok": True, "text": "Similar charges: murder in the first degree with a firearm"},
                      match.checks)
        self.assertTrue(all(c["ok"] for c in match.checks))


class OtherPeopleTest(unittest.TestCase):
    def test_limit_per_county(self):
        from core.pipeline import _other_people

        built = []

        def fake_build(extraction, article, suspect_index=0):
            built.append(suspect_index)
            return SimpleNamespace(query=SimpleNamespace(first_name="P", last_name=str(suspect_index),
                                                         county="orange", state="FL"))

        first = SimpleNamespace(query=SimpleNamespace(first_name="P", last_name="0", county="orange", state="FL"),
                                others=[object(), object(), object()])
        self.assertEqual(len(_other_people(fake_build, None, None, first)), 1)   # Orange: 2 personas
        first.query.county = "hernando"
        built.clear()
        self.assertEqual(len(_other_people(fake_build, None, None, first)), 2)   # Civitek: 3
        self.assertEqual(built, [1, 2])


if __name__ == "__main__":
    unittest.main()


def charges(*rows):
    """(ofensa, initial, descripción) -> cargos de Civitek."""
    return [Charge(offense_date=o, description=d, statute=None, arrest_date=None, agency=None,
                   control_number=None, initial_date=i) for o, i, d in rows]


class InitialDateTest(unittest.TestCase):
    """Fecha "Initial" de Civitek como arresto (Hernando, 18/10/2026)."""

    def test_parsed_from_the_charge_detail(self):
        from adapters.civitek_ocrs import charges_from_case
        detail = ("Date ActionStatute / TextOffense:07/11/2007Initial:07/17/2026Charged943.0435.9a / "
                  "FAILURE TO REGISTER AS SEXUAL OFFENDERProsecutor:10/15/2026Nolle Prosequi")
        data = {"chargeHeads": ["Charge Seq #", "Description"],
                "charges": [{"cells": ["1", "FAILURE TO REGISTER AS SEXUAL OFFENDER"], "detail": detail}]}
        charge = charges_from_case(data)[0]
        self.assertEqual((charge.offense_date, charge.initial_date), (date(2007, 7, 11), date(2026, 7, 17)))
        self.assertIsNone(charge.arrest_date)        # la lógica de "mismo arresto" no cambia

    def test_gordon_is_found(self):
        """Ofensa mal cargada (2007) en los tres cargos; arrestado el 17/07/2026 como dice el artículo."""
        q = LookupQuery(first_name="Jeremy", last_name="Gordon", county="hernando", state="FL",
                        incident_date=date(2024, 12, 27), arrest_date=date(2026, 7, 17),
                        charges=["Failure to register as a sexual offender",
                                 "Providing false identification to law enforcement",
                                 "Driving while license suspended or revoked"])
        candidate = CaseCandidate("26001035CFAXMX", "GORDON, JEREMY PAUL", "Felony 22-D", "CLOSED", None,
                                  date(2026, 7, 20), "x")
        details = CaseDetails(candidate, charges(
            (date(2007, 7, 11), date(2026, 7, 17), "FAILURE TO REGISTER AS SEXUAL OFFENDER"),
            (date(2007, 7, 11), date(2026, 7, 17), "PROVIDING FALSE NAME TO LAW ENFORCEMNT OFCR"),
            (date(2007, 7, 11), date(2026, 7, 17), "EXPIRED DL OVER 4/MONTHS")), [])
        match = score_case(q, details)
        # Pasa el mínimo (60 %) sin inflarse: la ofensa no resta, pero tampoco se regala.
        self.assertEqual(match.score, 62, match.reasons)
        self.assertIn("fecha de arresto coincide (Initial)", match.reasons)
        self.assertTrue(any(r.startswith("fecha de ofensa ignorada") for r in match.reasons))

    def test_old_warrant_served_same_day_does_not_tie(self):
        """Riesgo visto al revisar (18/10/2026): crimen nuevo y orden vieja servida el mismo día,
        con cargos parecidos. El caso del hecho tiene que ganar, no empatar."""
        q = LookupQuery(first_name="John", last_name="Tester", county="marion", state="FL",
                        incident_date=date(2026, 9, 1), arrest_date=date(2026, 9, 1), charges=["aggravated battery"])

        def same_day(number, offense, description):
            candidate = CaseCandidate(number, "TESTER, JOHN", "Felony", "OPEN", None, date(2026, 9, 2), "x")
            return CaseDetails(candidate, charges((offense, date(2026, 9, 1), description)), [])

        new = same_day("NEW", date(2026, 9, 1), "AGGRAVATED BATTERY")
        old = same_day("OLD", date(2025, 3, 10), "BATTERY")
        scored = [(score_case(q, d), d) for d in (new, old)]
        winner, status = pick_best(scored)
        self.assertEqual(status, "found")
        self.assertEqual(winner[1].candidate.case_number, "NEW")
        self.assertLess(scored[1][0].score, 80)

    def test_colon_unchanged(self):
        """Arrestado en 2026, Initial de 2023: no suma ni resta; sigue en 37 % (caso más cercano)."""
        q = query("Timothy", "Colon", arrest_date=date(2026, 10, 7))
        details = CaseDetails(COLON_2023.candidate, charges(
            (date(2018, 10, 9), date(2023, 8, 21), "HOME INVASION ROBBERY")), [])
        self.assertEqual(score_case(q, details).score, 37)

    def test_same_day_arrest_adds_points(self):
        """Hall (Hernando): ofensa e Initial el 17/09/2026; antes 86 %."""
        q = LookupQuery(first_name="Daniel", last_name="Hall", county="hernando", state="FL",
                        incident_date=date(2026, 9, 17), arrest_date=date(2026, 9, 17),
                        charges=["aggravated assault on a firefighter", "possession of a firearm by a felon"])
        candidate = CaseCandidate("26001359CFAXMX", "HALL, DANIEL THOMAS", "Felony 22-D", "OPEN", None,
                                  date(2026, 9, 18), "x")
        details = CaseDetails(candidate, charges(
            (date(2026, 9, 17), date(2026, 9, 17), "BATTERY ON OFFICER/FIREFIGHTER/EMT ETC"),
            (date(2026, 9, 17), date(2026, 9, 17), "POSSESSION OF WEAPON OR AMMO BY CONVICTED FLA FELON")), [])
        match = score_case(q, details)
        self.assertGreaterEqual(match.score, 90, match.reasons)

    def test_far_initial_with_far_offense_still_not_found(self):
        """Sin arresto coincidente, una ofensa lejana sigue restando como siempre."""
        q = LookupQuery(first_name="Arthur", last_name="Williams", county="hernando", state="FL",
                        incident_date=INCIDENT, arrest_date=date(2026, 10, 7), charges=["murder"])
        details = CaseDetails(WILLIAMS_DRUGS.candidate, charges(
            (date(2018, 12, 28), date(2018, 12, 28), "POSSESSION OF A CONTROLLED SUBSTANCE")), [])
        self.assertLess(score_case(q, details).score, 30)


class JuvenileTest(unittest.TestCase):
    """Braden Thomas (Gulf, 24/10/2026): 16 años; los casos de menores no son públicos."""

    def test_minor_not_found_explains_why(self):
        result, _ = run([query("Braden", "Thomas", age=16)], {})
        self.assertEqual(result.status, "not_found")
        self.assertIn("is 16", result.message)
        self.assertIn("Juvenile cases", result.message)

    def test_adult_keeps_the_usual_message(self):
        result, _ = run([query("Braden", "Thomas", age=19)], {})
        self.assertNotIn("Juvenile", result.message)

    def test_minor_charged_as_adult_is_still_found(self):
        result, _ = run([query("Mark", "Jones", age=17)], {"Jones": [JONES_FOUND]})
        self.assertEqual(result.status, "found")
