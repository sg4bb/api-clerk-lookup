"""Pruebas del extractor sin red: validación de la respuesta del modelo."""

import unittest
from datetime import date

from extractor import ExtractionError, build_query
from extractor.fetch import Article
from extractor.llm import _parse_json
from extractor.schema import Extraction

TEXT = ("A 23-year-old man has been arrested after allegedly holding up a CVS Pharmacy in Orlando. "
        "Thomas Mues was arrested and charged with robbery with a deadly weapon and mask. "
        "The incident happened around 6 p.m. Friday. Officer Jane Doe responded.")
ARTICLE = Article(url="https://example.com/a", title="CVS robbery", text=TEXT, published=date(2023, 10, 23), via="direct")


def person(first="Thomas", last="Mues", role="arrested", **kw):
    return dict(first_name=first, last_name=last, role=role, evidence="...", age=23, **kw)


def extraction(**kw):
    base = dict(people=[person()], incident_date="2023-10-20", city="Orlando", state="FL",
                agency="Orlando Police Department", charges=["robbery with a deadly weapon and mask"])
    base.update(kw)
    return Extraction.model_validate(base)


class ParseJsonTest(unittest.TestCase):
    def test_think_and_fence(self):
        raw = '<think>pienso {"no": 1}</think>\n```json\n{"people": []}\n```'
        self.assertEqual(_parse_json(raw), {"people": []})

    def test_plain(self):
        self.assertEqual(_parse_json('Sure: {"a": 1} ok'), {"a": 1})

    def test_no_json(self):
        with self.assertRaises(ValueError):
            _parse_json("no hay datos")


class BuildQueryTest(unittest.TestCase):
    def test_basic(self):
        aq = build_query(extraction(), ARTICLE)
        q = aq.query
        self.assertEqual((q.first_name, q.last_name, q.county), ("Thomas", "Mues", "orange"))
        self.assertEqual(q.incident_date, date(2023, 10, 20))
        self.assertEqual(q.charges, ["robbery with a deadly weapon and mask"])
        self.assertIsNone(q.approx_date)
        self.assertEqual(aq.warnings, [])

    def test_invented_name_rejected(self):
        with self.assertRaises(ExtractionError):
            build_query(extraction(people=[person("John", "Smith")]), ARTICLE)

    def test_invented_name_dropped_keeps_real(self):
        aq = build_query(extraction(people=[person("John", "Smith"), person()]), ARTICLE)
        self.assertEqual(aq.query.last_name, "Mues")
        self.assertTrue(any("John Smith" in w for w in aq.warnings))

    def test_future_date_dropped_uses_publication(self):
        aq = build_query(extraction(incident_date="2023-11-20"), ARTICLE)
        self.assertIsNone(aq.query.incident_date)
        self.assertEqual(aq.query.approx_date, date(2023, 10, 23))

    def test_arrested_before_suspect(self):
        text = TEXT + " Police are still searching for a second man, Mark Twain, who fled."
        article = Article(url="u", title="t", text=text, published=date(2023, 10, 23), via="direct")
        people = [person("Mark", "Twain", role="suspect"), person()]
        aq = build_query(extraction(people=people), article)
        self.assertEqual((aq.query.last_name, aq.person.role), ("Mues", "arrested"))
        self.assertEqual(len(aq.others), 1)
        aq2 = build_query(extraction(people=people), article, suspect_index=1)
        self.assertEqual(aq2.query.last_name, "Twain")
        self.assertTrue(any("sospechoso" in w for w in aq2.warnings))

    def test_county_from_city(self):
        self.assertEqual(build_query(extraction(county=None, city="Winter Park"), ARTICLE).query.county, "orange")
        self.assertEqual(build_query(extraction(county="Orange County"), ARTICLE).query.county, "orange")

    def test_unsupported_county(self):
        with self.assertRaises(ExtractionError):
            build_query(extraction(county="Seminole", city="Sanford", agency="Sanford Police Department"), ARTICLE)
        with self.assertRaises(ExtractionError):
            build_query(extraction(state="GA", county="Fulton", city="Atlanta", agency="Atlanta PD"), ARTICLE)

    def test_arrest_before_incident_dropped(self):
        aq = build_query(extraction(arrest_date="2023-10-01"), ARTICLE)
        self.assertIsNone(aq.query.arrest_date)


class RepairTest(unittest.TestCase):
    PUB = date(2023, 10, 23)  # lunes

    def test_relative_days(self):
        from extractor.repair import resolve_relative_day as r
        self.assertEqual(r("Friday", self.PUB), date(2023, 10, 20))
        self.assertEqual(r("around 6 p.m. Friday", self.PUB), date(2023, 10, 20))
        self.assertEqual(r("Monday morning", self.PUB), date(2023, 10, 23))
        self.assertEqual(r("last night", self.PUB), date(2023, 10, 22))
        self.assertEqual(r("yesterday", self.PUB), date(2023, 10, 22))
        self.assertIsNone(r("Friday or Saturday", self.PUB))
        self.assertIsNone(r("last month", self.PUB))

    def test_wrong_iso_corrected_by_evidence(self):
        aq = build_query(extraction(incident_date="2023-10-21", incident_date_evidence="Friday"), ARTICLE)
        self.assertEqual(aq.query.incident_date, date(2023, 10, 20))
        self.assertTrue(aq.notes)

    def test_county_from_agency(self):
        aq = build_query(extraction(county=None, city=None, agency="Orange County Sheriff's Office"), ARTICLE)
        self.assertEqual(aq.query.county, "orange")

    def test_real_nvidia_response_mues(self):
        """Respuesta real de NVIDIA (30/09/2026): 'Friday', sin edad, rol suspect, sin ciudad."""
        import json
        from pathlib import Path
        fixtures = Path(__file__).parent / "fixtures"
        raw = json.loads((fixtures / "mues_extraction.json").read_text(encoding="utf-8"))
        text = (fixtures / "mues_article.txt").read_text(encoding="utf-8").split("\n", 4)[4]
        article = Article(url="u", title="t", text=text, published=self.PUB, via="direct")
        aq = build_query(Extraction.model_validate(raw), article)
        q = aq.query
        self.assertEqual((q.first_name, q.last_name, q.age, q.county), ("Thomas", "Mues", 23, "orange"))
        self.assertEqual(q.incident_date, date(2023, 10, 20))
        self.assertEqual(aq.person.role, "arrested")
        self.assertEqual(len(q.charges), 6)


if __name__ == "__main__":
    unittest.main()
