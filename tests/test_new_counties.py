"""Putnam, Levy y Nassau (Civitek), con datos reales revisados a mano el 18/10/2026."""

import unittest
from datetime import date

from adapters.civitek_ocrs import case_number
from core.lookup import _first_word_only
from core.matcher import prelim_score, score_case
from core.models import CaseCandidate, CaseDetails, LookupQuery
from counties import get_county
from tests.test_civitek import AdapterStub, details_with_docket

# Caylen Deller (Putnam, 2024CF001197): arrestada con orden; el complaint es el
# Probable Cause Affidavit previo (PCSO24OFF0035711).
PUTNAM_DOCKET = [(36, "11/21/2024", "CAPIAS RETURNED EXECUTED"),
                 (29, "11/19/2024", "FIRST APPEARANCE HRG SUFFICIENT PROBABLE CAUSE FOUND BOND SET AT"),
                 (25, "11/19/2024", "ARREST REPORT: INDICTMENT PCSO 11-18-2024"),
                 (20, "11/14/2024", "FELONY WARRANT RETURNED EXECUTED"),
                 (14, "11/13/2024", "AFFIDAVIT FOR CRIMINAL INDIGENT STATUS DEFENDANT INDIGENT PUBLIC"),
                 (9, "11/13/2024", "ARREST REPORT: WARRANT - PCSO / 11-12-24"),
                 (8, "11/12/2024", "HOLD/ARREST CARD - VOLUSIA CO / 11-08-24"),
                 (4, "11/07/2024", "WARRANT ISSUED - BOND SET AT NONE"),
                 (1, "11/07/2024", "COMPLAINT FILED: PCSO/FUSSELL/09-08-24 CT I - FIRST DEGREE MURDER WITH A FIREARM")]

# Anthony Vessels (Levy, 2017CF000855): el sworn complaint es el formulario del Sheriff.
LEVY_DOCKET = [(14, "12/20/2017", "AFFIDAVIT OF INDIGENT STATUS-INDIGENT"),
               (11, "12/19/2017", "ORIGINAL WARRANT RETURNED AND FILED"),
               (9, "12/19/2017", "ARREST MITTIMUS-COPY FROM BROWARD CTY"),
               (7, "12/19/2017", "ARREST WARRANT SERVED ON 12/11/2017"),
               (4, "12/06/2017", "ARREST WARRANT ISSUED: WARRANT FOR ARREST"),
               (3, "12/06/2017", "SWORN COMPLAINT FILED")]

# Steven Paul Shaffer (Columbia, 24000899CFAXMX): orden emitida el 19/11/2024, arresto el 05/12/2024.
COLUMBIA_WARRANT_DOCKET = [(15, "12/12/2024", "WARRANT SERVED PAPERWORK"),
                           (14, "12/06/2024", "CAPIAS/WARRANT RETURNED SERVED"),
                           (11, "12/05/2024", "Initial status updated to Arrest on 12/5/2024"),
                           (6, "11/19/2024", "ARREST WARRANT AND WARRANT AFFIDAVIT"),
                           (3, "11/19/2024", "Agency Case Number: 23OFF003449")]
# Elbert Gilliard (Columbia, 19000675CFAXMX).
COLUMBIA_ARREST_DOCKET = [(7, "08/08/2019", "ARREST REPORT"),
                          (5, "08/08/2019", "FIRST APPEARANCE ORDER"),
                          (3, "08/08/2019", "Agency Case Number: 19OFF003389")]

# Frederick John Combs (Nassau, 26CF000280).
NASSAU_DOCKET = [(3, "04/15/2026", "ARREST AND BOOKING SHEET:")]


def choose(county: str, rows):
    adapter = AdapterStub(None, get_county(county, "FL"), None)
    details = details_with_docket(rows)
    return ([d.number for d in adapter.find_documents(details)],
            [d.number for d in adapter.find_support_documents(details)])


class ConfigTest(unittest.TestCase):
    def test_codes(self):
        for key, code in (("putnam", "54"), ("levy", "38"), ("nassau", "45"), ("columbia", "12"),
                          ("jackson", "32"), ("hendry", "26"), ("okeechobee", "47"), ("wakulla", "65")):
            county = get_county(key, "FL")
            self.assertEqual(county.base_url, f"https://www.civitekflorida.com/ocrs/county/{code}/")
            self.assertEqual(county.platform, "civitek_ocrs")

    def test_batch_two_agencies_pick_the_county(self):
        from counties import florida
        from core.matcher import normalize_agency
        for agency, key in (("Columbia County Sheriff's Office", "columbia"), ("Lake City Police Department", "columbia"),
                            ("Jackson County Sheriff's Office", "jackson"), ("Marianna Police Department", "jackson"),
                            ("Hendry County Sheriff's Office", "hendry"), ("Clewiston Police Department", "hendry"),
                            ("Okeechobee County Sheriff's Office", "okeechobee"), ("Wakulla County Sheriff's Office", "wakulla"),
                            ("Jacksonville Sheriff's Office", "duval")):
            self.assertEqual(florida.AGENCY_TO_COUNTY.get(normalize_agency(agency)), key, agency)

    def test_local_case_numbers(self):
        self.assertEqual(case_number({"local": "[2024001197CFAXMX]", "ucn": "x"}), "2024001197CFAXMX")
        self.assertEqual(case_number({"local": "[2017000855CFAXXX]", "ucn": "x"}), "2017000855CFAXXX")
        self.assertEqual(case_number({"local": "[26CF000280AXYX]", "ucn": "x"}), "26CF000280AXYX")


class DocumentTest(unittest.TestCase):
    def test_putnam_arrest_report_and_complaint(self):
        """Principal: el arrest report de la captura (#9). Apoyo: el complaint con la narración (#1)."""
        self.assertEqual(choose("putnam", PUTNAM_DOCKET), ([9, 25], [1]))

    def test_levy_sworn_complaint_is_main(self):
        # #9 "ARREST MITTIMUS-COPY FROM BROWARD" también cuenta, pero se baja el #3 (el primero).
        self.assertEqual(choose("levy", LEVY_DOCKET), ([3, 9], [7]))

    def test_levy_arrest_mittimus(self):
        """McQuiston (2026CF000685): arresto en el momento, el formulario se llama ARREST MITTIMUS."""
        rows = [(9, "10/12/2026", "DESIGNATION OF EMAIL ADDRESS"),
                (7, "10/08/2026", "FIRST APPEARANCE ORDER PD APPOINTED"),
                (6, "10/08/2026", "ARREST MITTIMUS")]
        self.assertEqual(choose("levy", rows), ([6], []))

    def test_county_names_stay_in_their_county(self):
        """Los nombres de Putnam, Levy y Nassau no se aplican a los demás condados de Civitek."""
        rows = [(9, "11/13/2024", "ARREST REPORT: WARRANT - PCSO / 11-12-24"),
                (6, "10/08/2026", "ARREST MITTIMUS"),
                (5, "10/08/2026", "SWORN COMPLAINT FILED"),
                (3, "04/15/2026", "ARREST AND BOOKING SHEET:"),
                (1, "11/07/2024", "COMPLAINT FILED: PCSO/FUSSELL/09-08-24")]
        for county in ("marion", "sumter", "hernando"):
            self.assertEqual(choose(county, rows), ([], []), county)
        self.assertEqual(choose("putnam", rows), ([9], [1]))
        self.assertEqual(choose("nassau", rows), ([3], []))

    def test_nassau_booking_sheet(self):
        self.assertEqual(choose("nassau", NASSAU_DOCKET), ([3], []))

    def test_earlier_counties_unchanged(self):
        """Sumter (Almany): el sworn ARREST complaint sigue siendo el principal."""
        from tests.test_civitek import ALMANY_DOCKET
        self.assertEqual(choose("sumter", ALMANY_DOCKET), ([7], [3]))


class FirstNameTest(unittest.TestCase):
    def query(self, first, middle=None):
        return LookupQuery(first_name=first, middle_name=middle, last_name="Combs", county="nassau", state="FL",
                           incident_date=date(2026, 4, 14))

    def test_only_the_first_word_goes_to_the_portal(self):
        q = _first_word_only(self.query("Frederick John"))
        self.assertEqual((q.first_name, q.middle_name), ("Frederick", "John"))

    def test_existing_middle_name_is_kept(self):
        q = _first_word_only(self.query("Frederick John", middle="J"))
        self.assertEqual((q.first_name, q.middle_name), ("Frederick", "J"))

    def test_initial_is_left_alone(self):
        q = _first_word_only(self.query("J. Michael"))
        self.assertEqual(q.first_name, "J. Michael")

    def test_combs_matches_with_and_without_middle_in_record(self):
        q = _first_word_only(self.query("Frederick John"))
        for record in ("COMBS, FREDERICK JOHN", "COMBS, FREDERICK"):
            candidate = CaseCandidate("26CF000280AXYX", record, "Felony", "OPEN", None, date(2026, 4, 15), "x")
            self.assertGreater(prelim_score(q, candidate), 0, record)
            self.assertIn("nombre exacto", score_case(q, CaseDetails(candidate, [], [])).reasons)


class LocationTest(unittest.TestCase):
    def test_cities_and_agencies(self):
        from extractor import build_query
        from tests.test_extractor import ARTICLE, extraction
        for city, agency, county in (("Palatka", None, "putnam"), (None, "Putnam County Sheriff's Office", "putnam"),
                                     ("Williston", None, "levy"), (None, "Levy County Sheriff's Office", "levy"),
                                     ("Yulee", None, "nassau"), (None, "Fernandina Beach Police Department", "nassau")):
            aq = build_query(extraction(county=None, city=city, agency=agency), ARTICLE)
            self.assertEqual(aq.query.county, county, (city, agency))


if __name__ == "__main__":
    unittest.main()


class LongNameTest(unittest.TestCase):
    """Putnam titula los documentos con todos los cargos (Carpenter, Gomez-Hernandez)."""

    TITLE = ("2023001104CFAXMX - COMPLAINT FILED_ PCSO _ CORREA _ 10-25-23 CHARGES _ I - MOLEST VICT LESS 12 YOA "
             "OFFENDER 18 YOA OR OLDER II - MOLEST VICT LESS 12 YOA OFFENDER 18 YOA OR OLDER III - MOLEST VICT "
             "LESS 12 YOA OFFENDER 18 YOA OR OLDER IV - MOLEST VICT LESS 12 YOA OFFENDER 18 YOA OR OLDER.pdf")

    def test_file_name_is_cut_keeping_the_extension(self):
        from core.utils import safe_filename
        name = safe_filename(self.TITLE)
        self.assertLessEqual(len(name), 100)
        self.assertTrue(name.startswith("2023001104CFAXMX - COMPLAINT FILED"))
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(safe_filename("26CF1 - ARREST AFFIDAVIT.pdf"), "26CF1 - ARREST AFFIDAVIT.pdf")

    def test_upload_retries_once_on_5xx(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from core import jobs

        calls = []

        class FakeHttp:
            def post(self, url, **kw):
                calls.append(url)
                return SimpleNamespace(status_code=520 if len(calls) == 1 else 200, text="")

        store = jobs.JobStore.__new__(jobs.JobStore)
        store._http = FakeHttp()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(jobs.time, "sleep"):
            pdf = Path(tmp) / "x.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            path = store.upload_pdf("job1", pdf)
        self.assertEqual(len(calls), 2)
        self.assertEqual(path, "job1/x.pdf")


class UnreadableArticleTest(unittest.TestCase):
    def test_compressed_bytes_are_not_an_article(self):
        """levyso.com (Mitchell, 18/10/2026): 15.000 caracteres de bytes zstd tomados como texto."""
        from extractor.fetch import Article, _usable
        garbage = "(\ufffd/\ufffdX\ufffd\ufffd\ufffd5RD \ufffd\ufffdm\ufffd\ufffd \ufffd%\ufffdyz\x07\x13" * 400
        self.assertFalse(_usable(Article(url="u", title="", text=garbage, published=None, via="direct")))
        text = "The Levy County Sheriff's Office arrested a Chiefland man on Tuesday. " * 20
        self.assertTrue(_usable(Article(url="u", title="t", text=text, published=None, via="direct")))

    def test_direct_download_does_not_offer_zstd(self):
        import extractor.fetch  # noqa: F401  (aplica el ajuste al importarse)
        from trafilatura import downloads
        self.assertNotIn("zstd", downloads.DEFAULT_HEADERS["accept-encoding"])


class SameFilingTest(unittest.TestCase):
    """McQuiston (Levy): el caso de la nota (686) sale como "mismo arresto, otro caso"."""

    def details(self, number, filed, offense, initial, description, defendant="MCQUISTON, EVAN DOUGLAS"):
        from core.models import Charge
        candidate = CaseCandidate(number, defendant, "Felony 22-D", "OPEN", None, filed, "x")
        charge = Charge(offense_date=offense, description=description, statute=None, arrest_date=None,
                        agency=None, control_number=None, initial_date=initial)
        return CaseDetails(candidate, [charge], [])

    def test_case_filed_same_day_is_related(self):
        from core.matcher import pick_best, related_cases
        q = LookupQuery(first_name="Evan", middle_name="Douglas", last_name="McQuiston", county="levy", state="FL",
                        arrest_date=date(2026, 10, 7),
                        charges=["Possession of Child Pornography", "Battery on Law Enforcement",
                                 "Resisting Officer with Violence"])
        battery = self.details("2026000685CFAXMX", date(2026, 10, 8), date(2026, 10, 7), date(2026, 10, 7),
                               "BATTERY-ON OFFICER OR FIREFIGHTER")
        csam = self.details("2026000686CFAXMX", date(2026, 10, 8), date(2026, 6, 11), date(2026, 6, 11),
                            "OBSCENE MATERIAL POSSESS-POSS PHOTOGRAPH OF SEXUAL PERFORMAN")
        older = self.details("2025000100CFAXMX", date(2025, 3, 2), date(2025, 3, 1), date(2025, 3, 1), "THEFT")
        scored = [(score_case(q, d), d) for d in (battery, csam, older)]
        winner, status = pick_best(scored)
        self.assertEqual((status, winner[1].candidate.case_number), ("found", "2026000685CFAXMX"))  # no cambia
        self.assertEqual(related_cases(winner[1], scored), ["2026000686CFAXMX"])

    def test_other_person_same_day_is_not_related(self):
        from core.matcher import same_filing
        a = self.details("A", date(2026, 10, 8), None, None, "X")
        b = self.details("B", date(2026, 10, 8), None, None, "X", defendant="DOE, JOHN")
        self.assertFalse(same_filing(a, b))


class OffenseOnArrestDayTest(unittest.TestCase):
    """La nota no da fecha del incidente pero sí del arresto: la ofensa del mismo día suma."""

    def details(self, offense, initial=None, description="OBST OR OPP A POLICE OFFICER WITHOUT VIOLENCE"):
        from core.models import Charge
        candidate = CaseCandidate("26MM000504AXYX", "RACKLEY, ZION MICHI", "Misdemeanor", "CLOSED", None,
                                  date(2026, 9, 22), "x")
        charge = Charge(offense_date=offense, description=description, statute=None, arrest_date=None,
                        agency=None, control_number=None, initial_date=initial)
        return CaseDetails(candidate, [charge], [])

    def query(self, **extra):
        values = dict(first_name="Zion", last_name="Rackley", county="nassau", state="FL", age=18,
                      agency="Nassau County Sheriff's Office", charges=["aggravated robbery"],
                      published_date=date(2026, 9, 24), age_as_of=date(2026, 9, 24))
        values.update(extra)
        return LookupQuery(**values)

    def test_rackley_offense_on_arrest_day_counts(self):
        m = score_case(self.query(arrest_date=date(2026, 9, 17)), self.details(date(2026, 9, 17), date(2026, 9, 22)))
        self.assertEqual(m.score, 100)
        self.assertIn("55/55 puntos posibles", m.reasons)
        self.assertIn({"ok": True, "text": "Offense date matches the arrest date (Sep 17, 2026)"}, m.checks)

    def test_one_day_off_still_counts(self):
        m = score_case(self.query(arrest_date=date(2026, 9, 17)), self.details(date(2026, 9, 16)))
        self.assertTrue(any("1 día(s) del arresto" in r for r in m.reasons))

    def test_warrant_with_old_offense_is_unchanged(self):
        # Rodriguez (Orange): arresto por orden, ofensa de 2024. Ni suma ni resta: igual que sin fecha de arresto.
        old = self.details(date(2024, 5, 3))
        with_arrest = score_case(self.query(arrest_date=date(2025, 2, 26)), old)
        without = score_case(self.query(), old)
        self.assertEqual((with_arrest.score, with_arrest.reasons), (without.score, without.reasons))

    def test_not_used_when_the_story_has_an_incident_date(self):
        m = score_case(self.query(incident_date=date(2026, 9, 17), arrest_date=date(2026, 9, 17)),
                       self.details(date(2026, 9, 17)))
        self.assertFalse(any("del arresto" in r for r in m.reasons))
        self.assertIn("fecha de ofensa a 0 día(s) del incidente", m.reasons)


class ColumbiaTest(unittest.TestCase):
    def test_arrest_report(self):
        self.assertEqual(choose("columbia", COLUMBIA_ARREST_DOCKET), ([7], []))

    def test_warrant_arrest_report_and_affidavit(self):
        """Principal: el informe del arresto con la orden (#14). Apoyo: la orden y su affidavit (#6)."""
        self.assertEqual(choose("columbia", COLUMBIA_WARRANT_DOCKET), ([14], [6, 15]))

    def test_arrest_report_stays_out_of_other_counties(self):
        self.assertEqual(choose("nassau", COLUMBIA_ARREST_DOCKET), ([], []))
        self.assertEqual(choose("marion", COLUMBIA_ARREST_DOCKET), ([], []))

    def test_agency_case_number_from_docket(self):
        from core.lookup import agency_case_number
        self.assertEqual(agency_case_number(details_with_docket(COLUMBIA_WARRANT_DOCKET)), "23OFF003449")
        self.assertEqual(agency_case_number(details_with_docket(COLUMBIA_ARREST_DOCKET)), "19OFF003389")
        self.assertIsNone(agency_case_number(details_with_docket(NASSAU_DOCKET)))


class WarrantWindowTest(unittest.TestCase):
    """Con fecha de arresto y sin fecha del delito, la búsqueda arranca 30 días antes."""

    def window(self, **dates):
        from core.lookup import search_window
        q = LookupQuery(first_name="Steven", last_name="Shaffer", county="columbia", state="FL", **dates)
        return search_window(q, get_county("columbia", "FL"))

    def test_arrest_only_reaches_the_warrant(self):
        start, _ = self.window(arrest_date=date(2024, 12, 5), published_date=date(2026, 7, 16))
        self.assertEqual(start, date(2024, 11, 5))
        self.assertLessEqual(start, date(2024, 11, 19))   # el caso se abrió con la orden

    def test_with_incident_date_window_is_unchanged(self):
        start, _ = self.window(incident_date=date(2024, 12, 5), arrest_date=date(2024, 12, 5))
        self.assertEqual(start, date(2024, 11, 28))
