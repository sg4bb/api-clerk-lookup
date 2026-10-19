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


class BlockedPageTest(unittest.TestCase):
    def test_geo_block_page_is_not_an_article(self):
        """WFTV desde fuera de EE. UU. (05/10/2026): 563 caracteres de aviso, no la nota."""
        from extractor.fetch import _blocked, _usable
        page = Article(url="u", title="This website is unavailable in your location. – WFTV",
                       text="We are sorry. " * 40, published=None, via="browser")
        self.assertTrue(_blocked(page))
        self.assertFalse(_usable(page))
        self.assertTrue(_usable(Article(url="u", title="CVS robbery", text=TEXT * 3, published=None, via="direct")))


class OutOfScopeTest(unittest.TestCase):
    """Artículos de fuera de Florida o de condados sin soporte: se avisa sin abrir el portal."""

    def reason(self, **kw):
        from extractor import OutOfScope
        with self.assertRaises(OutOfScope) as ctx:
            build_query(extraction(**kw), ARTICLE)
        return ctx.exception

    def test_other_state(self):
        exc = self.reason(state="GA", county="Fulton", city="Atlanta", agency="Atlanta Police Department")
        self.assertEqual(exc.reason, "state")
        self.assertIn("only Florida cases are searched", str(exc))
        self.assertEqual(self.reason(state="Georgia", county=None, city="Atlanta", agency=None).reason, "state")

    def test_county_not_in_florida_without_state(self):
        self.assertEqual(self.reason(state=None, county="Fulton", city="Atlanta", agency="Atlanta PD").reason, "state")

    def test_city_of_uncovered_county(self):
        exc = self.reason(county=None, city="Daytona Beach", agency="Daytona Beach Police Department")
        self.assertEqual((exc.reason, exc.county), ("county", "Volusia"))
        self.assertIn("Daytona Beach (Volusia County, FL)", str(exc))
        self.assertIn("Orange County", str(exc))

    def test_county_given_as_city_not_repeated(self):
        exc = self.reason(county="Volusia", city="Volusia County", agency="Volusia Sheriff's Office")
        self.assertIn("about Volusia County, FL.", str(exc))
        self.assertNotIn("(Volusia County, FL)", str(exc))

    def test_ocala_is_covered(self):
        """Marion (Ocala) tiene cobertura desde el adaptador de Civitek."""
        aq = build_query(extraction(county=None, city="Ocala", agency="Ocala Police Department"), ARTICLE)
        self.assertEqual(aq.query.county, "marion")

    def test_sumter_is_covered(self):
        """Sumter tiene cobertura (Civitek); The Villages y Wildwood resuelven a Sumter."""
        for city, agency in (("The Villages", "Sumter County Sheriff's Office"), ("Wildwood", "Wildwood Police Department")):
            aq = build_query(extraction(county=None, city=city, agency=agency), ARTICLE)
            self.assertEqual(aq.query.county, "sumter")
        aq = build_query(extraction(county=None, city=None, agency="Sumter County Sheriff's Office"), ARTICLE)
        self.assertEqual(aq.query.county, "sumter")

    def test_victim_age_not_given_to_suspect(self):
        """Hernando (14/10/2026): el único "22-year-old" era la víctima, no Colon."""
        text = ("Federal authorities have charged two men in connection with the 2018 fatal shooting and "
                "severe beating of a 22-year-old father during an armed robbery in Spring Hill. "
                "Arthur Williams, alongside Timothy Colon, were indicted on federal charges. "
                "Colon was taken into custody on Wednesday.")
        art = Article(url="u", title="t", text=text, published=date(2026, 10, 8), via="direct")
        people = [dict(person("Arthur", "Williams"), age=None), dict(person("Timothy", "Colon"), age=None)]
        aq = build_query(extraction(people=people, county="Hernando", city="Spring Hill"), art, suspect_index=1)
        self.assertEqual(aq.query.last_name, "Colon")
        self.assertIsNone(aq.query.age)

    def test_lone_arrest_age_still_used(self):
        """Mues: "A 23-year-old man has been arrested" sigue sirviendo con una sola persona."""
        aq = build_query(extraction(people=[dict(person(), age=None)]), ARTICLE)
        self.assertEqual(aq.query.age, 23)

    def test_hernando_is_covered(self):
        """Hernando (Civitek 27): Spring Hill, Brooksville y el Sheriff resuelven a Hernando."""
        for city, agency in (("Spring Hill", None), ("Brooksville", "Brooksville Police Department"),
                             (None, "Hernando County Sheriff's Office")):
            aq = build_query(extraction(county=None, city=city, agency=agency), ARTICLE)
            self.assertEqual(aq.query.county, "hernando")

    def test_florida_county_named(self):
        exc = self.reason(county="Seminole County", city="Sanford", agency="Sanford Police Department")
        self.assertEqual((exc.reason, exc.county), ("county", "Seminole"))
        self.assertEqual(self.reason(county="Miami-Dade", city="Miami", agency=None).county, "Miami-Dade")
        self.assertEqual(self.reason(county="St. Johns", city=None, agency=None).county, "St. Johns")

    def test_unknown(self):
        self.assertEqual(self.reason(county=None, city=None, state=None, agency="Police").reason, "unknown")

    def test_orange_from_agency_or_city(self):
        self.assertEqual(build_query(extraction(county=None, city=None, agency="OPD"), ARTICLE).query.county, "orange")
        self.assertEqual(build_query(extraction(county=None, city="Apopka", agency=None), ARTICLE).query.county, "orange")


class DatePrecisionTest(unittest.TestCase):
    def test_year_only_becomes_range(self):
        text = TEXT + " The man committed the act back in 2022."
        art = Article(url="u", title="t", text=text, published=date(2026, 7, 14), via="direct")
        aq = build_query(extraction(incident_date="2022-01-01", arrest_date=None,
                                    incident_date_evidence="committed the act back in 2022"), art)
        q = aq.query
        self.assertIsNone(q.incident_date)
        self.assertEqual((q.date_from, q.date_to), (date(2021, 12, 25), date(2023, 2, 14)))
        self.assertIsNone(q.approx_date)
        self.assertEqual(q.age_as_of, date(2026, 7, 14))

    def test_month_only(self):
        aq = build_query(extraction(incident_date="2023-06-01", incident_date_precision="month",
                                    incident_date_evidence="in June 2023"), ARTICLE)
        self.assertEqual((aq.query.date_from, aq.query.date_to), (date(2023, 5, 25), date(2023, 8, 14)))

    def test_exact_day_kept(self):
        aq = build_query(extraction(incident_date="2023-10-20",
                                    incident_date_evidence="on June 24, 2023 around 6 p.m. Friday"), ARTICLE)
        self.assertEqual(aq.query.incident_date, date(2023, 10, 20))
        self.assertIsNone(aq.query.date_from)


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

    def test_agency_beats_dateline_county(self):
        """Almany (14/10/2026): dateline "ORLANDO, Fla." y el modelo de respaldo dijo
        Orange; la agencia es el Sheriff de Sumter, que solo actúa en Sumter."""
        ex = extraction(county="Orange", city="Orlando", agency="Sumter County Sheriff's Office")
        with self.assertLogs("extractor", level="WARNING"):
            aq = build_query(ex, ARTICLE)
        self.assertEqual(aq.query.county, "sumter")

    def test_agreeing_county_untouched(self):
        ex = extraction(county="Orange County", city="Orlando", agency="Orlando Police Department")
        self.assertEqual(build_query(ex, ARTICLE).query.county, "orange")

    def test_unknown_agency_keeps_model_county(self):
        ex = extraction(county="Marion", city=None, agency="Florida Highway Patrol")
        self.assertEqual(build_query(ex, ARTICLE).query.county, "marion")

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


class WrongPublishedDateTest(unittest.TestCase):
    """Facebook dio 2001-01-01 como publicación de un comunicado del 05/10/2026 (Peterson, Jackson)."""

    TEXT = ("FOR IMMEDIATE RELEASE Jackson County, Fla. - October 5, 2026 - While most of us were asleep "
            "early Saturday morning, a deputy stopped a black Dodge Charger. Roosevelt Peterson was charged "
            "with possession of a controlled substance and fleeing or attempting to elude.")

    def query(self, published, text=TEXT, **kw):
        article = Article(url="https://www.facebook.com/x", title="37K views", text=text, published=published,
                          via="browser")
        values = dict(people=[person("Roosevelt", "Peterson")], incident_date="2026-10-03",
                      incident_date_evidence="early Saturday morning", city=None, county="Jackson",
                      agency="Jackson County Sheriff's Office", charges=["possession of a controlled substance"])
        values.update(kw)
        return build_query(Extraction.model_validate(values), article)

    def test_publication_year_missing_from_text_is_ignored(self):
        aq = self.query(date(2001, 1, 1))
        self.assertEqual(aq.query.incident_date, date(2026, 10, 3))   # ni descartada ni movida al año 2000
        self.assertIsNone(aq.query.published_date)
        self.assertTrue(any("publicación de la página" in w for w in aq.warnings))

    def test_real_publication_date_is_kept(self):
        aq = self.query(date(2026, 10, 5))
        self.assertEqual(aq.query.incident_date, date(2026, 10, 3))
        self.assertEqual(aq.query.published_date, date(2026, 10, 5))

    def test_invented_year_is_still_dropped(self):
        # Nota de 2019 sin años en el texto; el modelo inventa 2026: se sigue descartando.
        aq = build_query(extraction(incident_date="2026-03-01"), ARTICLE)
        self.assertIsNone(aq.query.incident_date)
        self.assertEqual(aq.query.published_date, date(2023, 10, 23))
