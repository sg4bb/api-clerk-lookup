"""Notas que involucran a más de un condado (Flagler / St. Johns, 03/11/2026).

"Over 20 dogs taken from Central Florida home": los perros salieron de Palm Coast (Flagler) y
los dejaron en St. Johns; Moore y Dunham tenían caso en St. Johns y Loveman en Flagler. El worker
busca primero en el condado de la nota y, si ahí no aparece el caso, en los otros que nombra.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import extractor
from core import pipeline
from core.models import LookupQuery, LookupResult
from extractor import OutOfScope, build_query, mentioned_counties, query_for_county
from extractor.fetch import Article
from tests.test_extractor import extraction

DOGS_TEXT = (
    "Investigators found 23 dogs at the intersection of US-1 and County Road 204 in St. Johns County in early May. "
    "Detectives said Austin Moore, 32, took the dogs from his mother's home in Palm Coast and traveled to "
    "St. Johns County with Anthony Dunham, 23, where they abandoned the dogs. The Flagler County Sheriff's "
    "Office said Moore and Dunham each face three counts of aggravated animal cruelty.")
DOGS = Article(url="https://example.com/dogs", title="Over 20 dogs taken from Central Florida home, dumped on roadside",
               text=DOGS_TEXT, published=date(2026, 7, 3), via="direct")


def dogs(**kw):
    base = dict(people=[dict(first_name="Austin", last_name="Moore", role="arrested", evidence="-", age=32),
                        dict(first_name="Anthony", last_name="Dunham", role="arrested", evidence="-", age=23)],
                incident_date="2026-05-01", city="Palm Coast", county="Flagler",
                agency="Flagler County Sheriff's Office", charges=["aggravated animal cruelty"])
    base.update(kw)
    return extraction(**base)


class MentionedCountiesTest(unittest.TestCase):
    def test_counties_in_order(self):
        text = ("Deputies learned the suspects were wanted out of St. Johns County. Charles was booked into the "
                "Volusia County Jail, the Flagler County Sheriff's Office said.")
        self.assertEqual(mentioned_counties(text), ["st_johns", "volusia", "flagler"])

    def test_names_with_several_words_and_plurals(self):
        text = "Deputies from Flagler and St. Johns counties, Miami-Dade County police and Palm Beach County."
        self.assertEqual(mentioned_counties(text), ["flagler", "st_johns", "miami_dade", "palm_beach"])
        self.assertEqual(mentioned_counties("Clay, Putnam and St. Johns counties"), ["clay", "putnam", "st_johns"])

    def test_not_a_county(self):
        self.assertEqual(mentioned_counties("at US-1 and County Road 204, the county said"), [])
        self.assertEqual(mentioned_counties("He moved from Orange County, Calif., last year."), [])
        self.assertEqual(mentioned_counties("He moved from Orange County, Fla., last year."), ["orange"])


class BuildQueryTest(unittest.TestCase):
    def test_other_counties_named_by_the_story(self):
        aq = build_query(dogs(), DOGS)
        self.assertEqual(aq.query.county, "flagler")
        self.assertEqual(aq.other_counties, ["st_johns"])
        self.assertIsNone(aq.uncovered_county)

    def test_only_covered_counties(self):
        article = Article(url="u", title="Chase", published=date(2025, 9, 17), via="direct", text=(
            "Stevens Charles, 40, was arrested after a chase, the Flagler County Sheriff's Office said. "
            "He was booked into the Brevard County Jail."))
        aq = build_query(extraction(people=[dict(first_name="Stevens", last_name="Charles", role="arrested",
                                                 evidence="-", age=40)],
                                    incident_date="2025-09-16", city=None, county="Flagler",
                                    agency="Flagler County Sheriff's Office"), article)
        self.assertEqual((aq.query.county, aq.other_counties), ("flagler", []))

    def test_uncovered_county_falls_back_to_a_named_one(self):
        """La nota es de Brevard (sin cobertura) y nombra St. Johns: se busca en St. Johns."""
        article = Article(url="u", title="Burglary", published=date(2026, 7, 3), via="direct", text=(
            "Austin Moore, 32, was arrested by the Brevard County Sheriff's Office. He was also wanted "
            "in St. Johns County for vehicle burglaries."))
        aq = build_query(dogs(people=[dict(first_name="Austin", last_name="Moore", role="arrested", evidence="-")],
                              city="Melbourne", county="Brevard", agency="Brevard County Sheriff's Office"), article)
        self.assertEqual((aq.query.county, aq.uncovered_county, aq.other_counties), ("st_johns", "Brevard", []))
        self.assertIsNone(aq.query.agency)          # el Sheriff de Brevard no cuenta en St. Johns

    def test_uncovered_county_without_a_covered_one_is_still_unsupported(self):
        article = Article(url="u", title="Arrest", published=date(2026, 7, 3), via="direct",
                          text="Austin Moore, 32, was arrested by the Brevard County Sheriff's Office.")
        with self.assertRaises(OutOfScope) as ctx:
            build_query(dogs(people=[dict(first_name="Austin", last_name="Moore", role="arrested", evidence="-")],
                             city="Melbourne", county="Brevard", agency="Brevard County Sheriff's Office"), article)
        self.assertEqual(ctx.exception.reason, "county")

    def test_agency_of_another_county_does_not_count(self):
        q = LookupQuery(first_name="Austin", last_name="Moore", county="flagler",
                        agency="Flagler County Sheriff's Office")
        other, note = query_for_county(q, "st_johns")
        self.assertEqual((other.county, other.agency), ("st_johns", None))
        self.assertIn("Flagler", note)
        fhp, note = query_for_county(LookupQuery(first_name="A", last_name="B", county="flagler",
                                                 agency="Florida Highway Patrol"), "st_johns")
        self.assertEqual((fhp.agency, note), ("Florida Highway Patrol", None))


def searched(name, status, county):
    return {"name": name, "status": status, "cases_checked": 0, "county": county}


NOT_FOUND_FLAGLER = LookupResult("not_found", people_searched=[searched("Austin Moore", "not_found", "Flagler County"),
                                                               searched("Anthony Dunham", "not_found", "Flagler County")])


class PipelineTest(unittest.TestCase):
    def run_with(self, results, article=DOGS, extracted=None):
        calls = []

        def fake_lookup(queries, output_dir, **kw):
            calls.append([(q.county, q.first_name, q.agency) for q in queries])
            return results[len(calls) - 1]

        with mock.patch.object(extractor, "fetch_article", return_value=article), \
             mock.patch.object(extractor, "extract", return_value=extracted or dogs()), \
             mock.patch.object(pipeline, "run_lookup", side_effect=fake_lookup), \
             tempfile.TemporaryDirectory() as tmp:
            return pipeline.run_article(article.url, Path(tmp)), calls

    def test_found_in_the_other_county(self):
        found = LookupResult("found", case_number="26000999CFMA", person_index=1, people_searched=[
            searched("Austin Moore", "not_found", "St. Johns County"),
            searched("Anthony Dunham", "found", "St. Johns County")])
        result, calls = self.run_with([NOT_FOUND_FLAGLER, found])
        self.assertEqual(calls, [[("flagler", "Austin", "Flagler County Sheriff's Office"),
                                  ("flagler", "Anthony", "Flagler County Sheriff's Office")],
                                 [("st_johns", "Austin", None), ("st_johns", "Anthony", None)]])
        self.assertEqual((result.status, result.lookup.case_number), ("found", "26000999CFMA"))
        self.assertEqual(result.extraction["county"], "St. Johns")
        self.assertEqual((result.query["county"], result.query["first_name"]), ("st_johns", "Anthony"))
        self.assertEqual([(p["name"], p["county"]) for p in result.lookup.people_searched],
                         [("Austin Moore", "Flagler County"), ("Anthony Dunham", "Flagler County"),
                          ("Austin Moore", "St. Johns County"), ("Anthony Dunham", "St. Johns County")])
        self.assertTrue(any("St. Johns" in n for n in result.notes), result.notes)

    def test_found_in_the_first_county_stops(self):
        result, calls = self.run_with([LookupResult("found", case_number="2026 CF 000420")])
        self.assertEqual((result.status, len(calls)), ("found", 1))

    def test_not_found_anywhere_keeps_the_first_result(self):
        other = LookupResult("not_found", closest={"case_number": "26000111CFMA", "score": 40},
                             people_searched=[searched("Austin Moore", "not_found", "St. Johns County")])
        result, calls = self.run_with([NOT_FOUND_FLAGLER, other])
        self.assertEqual((result.status, len(calls)), ("not_found", 2))
        self.assertEqual(result.extraction["county"], "Flagler")
        self.assertEqual(result.lookup.closest["case_number"], "26000111CFMA")
        self.assertEqual(len(result.lookup.people_searched), 3)

    def test_other_county_captcha_timeout(self):
        result, _ = self.run_with([NOT_FOUND_FLAGLER, LookupResult("captcha_timeout", message="x")])
        self.assertEqual(result.status, "not_found")
        self.assertIn("St. Johns County was not searched", result.message)

    def test_uncovered_county_message(self):
        article = Article(url="u", title="Burglary", published=date(2026, 7, 3), via="direct", text=(
            "Austin Moore, 32, was arrested by the Brevard County Sheriff's Office. He was also wanted "
            "in St. Johns County for vehicle burglaries."))
        moore = dogs(people=[dict(first_name="Austin", last_name="Moore", role="arrested", evidence="-")],
                     city="Melbourne", county="Brevard", agency="Brevard County Sheriff's Office")
        result, calls = self.run_with([LookupResult("not_found")], article=article, extracted=moore)
        self.assertEqual(calls[0][0][0], "st_johns")
        self.assertEqual(result.status, "not_found")
        self.assertIn("Brevard County, which is not covered yet", result.message)
        self.assertIn("St. Johns County", result.message)


if __name__ == "__main__":
    unittest.main()
