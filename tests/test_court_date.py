"""Fecha de condena o sentencia (28/10/2026): notas de condena como la de Cox (Liberty)."""

import unittest
from datetime import date

from adapters.civitek_ocrs import charges_from_case
from core.lookup import search_window
from core.matcher import pick_best, score_case
from core.models import CaseCandidate, CaseDetails, Charge, LookupQuery
from counties import get_county
from extractor import build_query
from extractor.schema import Extraction
from extractor.fetch import Article
from tests.test_people_and_closest import run

COX_TEXT = ("Today, the Liberty County Sheriff's Office announces that Joe L. Cox has been sentenced to 20 years "
            "in the Florida Department of Corrections. On June 30, 2026, Cox was convicted following a jury trial "
            "in Liberty County on the following charges: Trafficking in Methamphetamine, Trafficking in Cocaine, "
            "Felon in Possession of a Firearm.")
COX_CHARGES = ["Trafficking in Methamphetamine", "Trafficking in Cocaine", "Felon in Possession of a Firearm"]


def cox_extraction(**kw):
    base = dict(people=[{"first_name": "Joe", "last_name": "Cox", "role": "charged", "evidence": "Joe L. Cox"}],
                incident_date="2026-06-30", court_dates=["2026-06-30"], county="Liberty", state="FL",
                agency="Liberty County Sheriff's Office", charges=COX_CHARGES)
    base.update(kw)
    return Extraction.model_validate(base)


def charge(description, offense=None, initial=None, court=None):
    return Charge(offense_date=offense, description=description, statute=None, arrest_date=None, agency=None,
                  control_number=None, initial_date=initial, court_date=court)


def case(number, filed, *charges):
    return CaseDetails(CaseCandidate(number, "COX, JOE L", "Felony", "CLOSED", None, filed, "x"), list(charges), [])


# Los casos de Joe Cox en Liberty (búsqueda sin fechas del 28/10/2026).
COX_RIGHT = case("25000066CFMXAX", date(2025, 6, 9),
                 charge("TRAFFICKING METHAMPHETAMINE 28 GRAMS OR MORE, LESS THAN 200", date(2025, 6, 6),
                        date(2025, 6, 9), date(2026, 6, 30)),
                 charge("TRAFFICKING IN COCAINE 28 GRAMS OR MORE, LESS THAN 200 GRAMS", date(2025, 6, 6),
                        date(2025, 6, 9), date(2026, 6, 30)))
COX_DISMISSED = case("25000015CFMXAX", date(2025, 2, 3),
                     charge("POSS OF FIREARM BY VIOLENT CAREER CRIMINAL (F1)", date(2025, 1, 31),
                            date(2025, 1, 31), date(2025, 11, 4)),
                     charge("POSSESSION OF CONTROLLED SUBSTANCE (F3)", date(2025, 1, 31),
                            date(2025, 1, 31), date(2025, 11, 4)))


class ParseTest(unittest.TestCase):
    def test_court_date_from_the_charge_detail(self):
        detail = ("Date ActionStatute / TextOffense:06/06/2025Trial: Jury TrialTraffic Disposition: GuiltyInitial:"
                  "06/09/2025Charged893.135.1f1 / TRAFFICKING METHAMPHETAMINE 28 GRAMS OR MORE, LESS THAN 200"
                  "Prosecutor:04/08/2026Filed893.135.1f1 / TRAFFICKING METHAMPHETAMINE 28 GRAMS OR MORE, LESS THAN "
                  "200Court:06/30/2026Adjudicated Guilty893.135.1f1")
        data = {"chargeHeads": ["Charge Seq #", "Description"],
                "charges": [{"cells": ["1", "TRAFFICKING METHAMPHETAMINE"], "detail": detail}]}
        self.assertEqual(charges_from_case(data)[0].court_date, date(2026, 6, 30))

    def test_no_court_phase(self):
        detail = ("Date ActionStatute / TextOffense:11/28/2017Initial:12/06/2017Charged893.13.1a1 / POSSESSION OF "
                  "COCAINEProsecutor:02/05/2018Transferred to Another Court893.13.6a / POSSESSION")
        data = {"chargeHeads": ["Charge Seq #", "Description"], "charges": [{"cells": ["1", "X"], "detail": detail}]}
        self.assertIsNone(charges_from_case(data)[0].court_date)

    def test_schema_tolerates_missing_or_single(self):
        self.assertEqual(cox_extraction(court_dates=None).court_dates, [])
        self.assertEqual(cox_extraction(court_dates="2026-06-30").court_dates, ["2026-06-30"])
        self.assertEqual(Extraction.model_validate({"people": []}).court_dates, [])


class BuildQueryTest(unittest.TestCase):
    def article(self, published=date(2026, 8, 3)):
        return Article(url="https://facebook.com/x", title="Liberty County Sheriff's Office", text=COX_TEXT,
                       published=published, via="browser")

    def test_conviction_date_is_not_the_incident(self):
        aq = build_query(cox_extraction(), self.article())
        q = aq.query
        self.assertIsNone(q.incident_date)
        self.assertEqual(q.court_dates, [date(2026, 6, 30)])
        self.assertIsNone(q.approx_date)      # no busca alrededor de la publicación
        self.assertTrue(any("condena" in w for w in aq.warnings), aq.warnings)

    def test_search_window_goes_back_from_the_conviction(self):
        q = build_query(cox_extraction(), self.article()).query
        date_from, date_to = search_window(q, get_county("liberty", "FL"))
        self.assertLessEqual(date_from, date(2025, 6, 9))
        self.assertEqual(date_to, date(2026, 7, 1))

    def test_wrong_facebook_date(self):
        """Facebook dio 2016-01-02: no se usa, y la fecha de condena (2026) no se descarta."""
        q = build_query(cox_extraction(), self.article(published=date(2016, 1, 2))).query
        self.assertEqual(q.court_dates, [date(2026, 6, 30)])

    def test_real_incident_date_stays(self):
        q = build_query(cox_extraction(incident_date="2025-06-06"), self.article()).query
        self.assertEqual(q.incident_date, date(2025, 6, 6))
        self.assertEqual(q.court_dates, [date(2026, 6, 30)])


class ScoreTest(unittest.TestCase):
    def query(self, **kw):
        base = dict(first_name="Joe", last_name="Cox", county="liberty", state="FL", charges=COX_CHARGES,
                    court_dates=[date(2026, 6, 30)], published_date=date(2026, 8, 3))
        base.update(kw)
        return LookupQuery(**base)

    def test_cox_is_found(self):
        scored = [(score_case(self.query(), d), d) for d in (COX_RIGHT, COX_DISMISSED)]
        winner, status = pick_best(scored)
        self.assertEqual(status, "found", [(m.score, m.reasons) for m, _ in scored])
        self.assertEqual(winner[1].candidate.case_number, "25000066CFMXAX")
        self.assertIn("fecha de condena coincide", winner[0].reasons)
        self.assertIn({"ok": True, "text": "Court date matches"}, winner[0].checks)
        self.assertLess(scored[1][0].score, 60)

    def test_end_to_end(self):
        result, _ = run([self.query()], {"Cox": [COX_RIGHT, COX_DISMISSED]})
        self.assertEqual((result.status in ("found", "no_document"), result.case_number), (True, "25000066CFMXAX"))

    def test_sentencing_weeks_after_the_verdict(self):
        match = score_case(self.query(court_dates=[date(2026, 8, 3)]), COX_RIGHT)
        self.assertIn("fecha de condena a 34 días", match.reasons)

    def test_court_date_after_the_story_does_not_count(self):
        """Rivers (Glades): nota de noviembre de 2025, "Court" del CF en julio de 2026."""
        rivers = case("24000026CFMXAX", date(2024, 3, 7),
                      charge("ILLEGAL KILLING, POSS, OR CAPTURE OF ALLIGATOR/EGGS", date(2023, 10, 26),
                             date(2024, 3, 7), date(2026, 7, 28)))
        q = self.query(first_name="Herbert", last_name="Rivers", charges=["Illegal killing of an alligator"],
                       court_dates=[date(2025, 11, 20)], published_date=date(2025, 11, 21))
        without = self.query(first_name="Herbert", last_name="Rivers", charges=["Illegal killing of an alligator"],
                             court_dates=[], published_date=date(2025, 11, 21))
        self.assertEqual(score_case(q, rivers).score, score_case(without, rivers).score)

    def test_portal_without_court_dates_loses_nothing(self):
        """Orange no trae fecha "Court": el puntaje es el mismo que sin fecha de condena."""
        plain = case("2025-CF-000001-O", date(2025, 6, 9),
                     charge("TRAFFICKING IN COCAINE", date(2025, 6, 6), date(2025, 6, 9)))
        self.assertEqual(score_case(self.query(), plain).score, score_case(self.query(court_dates=[]), plain).score)

    def test_telci(self):
        """Telci (Glades): "Court: 06/01/2026", nota del 02/06 sobre la sentencia del 01/06."""
        telci = CaseDetails(CaseCandidate("25000121CFMXAX", "TELCI, DAVID CENGIZ", "Felony", "CLOSED", None,
                                          date(2025, 8, 29), "x"),
                            [charge("AGGRAVATED BATTERY", date(2025, 4, 17), date(2025, 4, 17), date(2026, 6, 1))], [])
        q = LookupQuery(first_name="David", last_name="Telci", county="glades", state="FL",
                        charges=["Aggravated Battery"], court_dates=[date(2026, 6, 1)],
                        published_date=date(2026, 6, 2))
        self.assertEqual(score_case(q, telci).score, 100)


if __name__ == "__main__":
    unittest.main()


class PipelineTest(unittest.TestCase):
    def test_web_does_not_show_the_conviction_as_the_incident(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        import extractor
        from core import pipeline
        from core.models import LookupResult
        article = Article(url="https://facebook.com/x", title="Liberty County Sheriff's Office", text=COX_TEXT,
                          published=date(2026, 8, 3), via="browser")
        with mock.patch.object(extractor, "fetch_article", return_value=article), \
             mock.patch.object(extractor, "extract", return_value=cox_extraction()), \
             mock.patch.object(pipeline, "run_lookup", return_value=LookupResult("not_found")), \
             tempfile.TemporaryDirectory() as tmp:
            result = pipeline.run_article("https://facebook.com/x", Path(tmp))
        self.assertIsNone(result.extraction["incident_date"])
        self.assertEqual(result.extraction["court_dates"], ["2026-06-30"])
        self.assertEqual(result.query["court_dates"], ["2026-06-30"])
