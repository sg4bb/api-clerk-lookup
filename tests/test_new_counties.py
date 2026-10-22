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

# Kody Gilbert (Jackson, 26MM000440) y Roosevelt Peterson (Jackson, 26CF000546).
JACKSON_COMPLAINT_DOCKET = [(26, "08/24/2026", "NOTICE OF APPEARANCE, PLEA OF NOT GUILTY, AND DEMAND FOR DISCOVERY"),
                            (9, "08/21/2026", "FIRST APPEARANCE"),
                            (7, "08/21/2026", "COMPLAINT")]
JACKSON_AFFIDAVIT_DOCKET = [(13, "10/05/2026", "CITATION FILED"),
                            (12, "10/05/2026", "AFFIDAVIT-COMPLAINT"),
                            (3, "10/05/2026", "Agency Case Number: 26-032173")]

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
                          ("jackson", "32"), ("hendry", "26"), ("okeechobee", "47"), ("wakulla", "65"),
                          ("baker", "02"), ("bradford", "04"), ("washington", "67"), ("hardee", "25"),
                          ("holmes", "30"), ("gilchrist", "21"), ("madison", "40"), ("dixie", "15"),
                          ("gulf", "23"), ("union", "63")):
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
                            ("Baker County Sheriff's Office", "baker"), ("Starke Police Department", "bradford"),
                            ("Washington County Sheriff's Office", "washington"), ("Chipley Police Department", "washington"),
                            ("Hardee County Sheriff's Office", "hardee"), ("Wauchula Police Department", "hardee"),
                            ("Holmes County Sheriff's Office", "holmes"), ("Bonifay Police Department", "holmes"),
                            ("Gilchrist County Sheriff's Office", "gilchrist"), ("Madison County Sheriff's Office", "madison"),
                            ("Madison Police Department", "madison"), ("Dixie County Sheriff's Office", "dixie"),
                            ("Gulf County Sheriff's Office", "gulf"), ("Port St. Joe Police Department", "gulf"),
                            ("Union County Sheriff's Office", "union"), ("Lake Butler Police Department", "union"),
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
                                     ("Yulee", None, "nassau"), (None, "Fernandina Beach Police Department", "nassau"),
                                     ("Trenton", None, "gilchrist"), ("Fanning Springs", None, "gilchrist"),
                                     ("Madison", None, "madison"), ("Cross City", None, "dixie"),
                                     ("Port St. Joe", None, "gulf"), ("Wewahitchka", None, "gulf"),
                                     ("Lake Butler", None, "union")):
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
        # Los cargos (robbery contra "OBST OR OPP A POLICE OFFICER") no se pueden comparar: cuentan sin
        # sumar. Sigue encontrándose por nombre y ofensa el día del arresto.
        self.assertEqual(m.score, 79)
        self.assertIn("55/70 puntos posibles", m.reasons)
        self.assertIn("cargos no comparables", m.reasons)
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


class JacksonTest(unittest.TestCase):
    def test_complaint_and_affidavit_complaint(self):
        self.assertEqual(choose("jackson", JACKSON_COMPLAINT_DOCKET), ([7], []))
        self.assertEqual(choose("jackson", JACKSON_AFFIDAVIT_DOCKET), ([12], []))

    def test_other_complaints_are_not_taken(self):
        rows = [(5, "09/09/2026", "COMPLAINT FOR INJUNCTION FOR PROTECTION"), (4, "09/09/2026", "AMENDED COMPLAINT")]
        self.assertEqual(choose("jackson", rows), ([], []))

    def test_names_stay_in_jackson(self):
        self.assertEqual(choose("marion", JACKSON_COMPLAINT_DOCKET), ([], []))
        self.assertEqual(choose("putnam", JACKSON_AFFIDAVIT_DOCKET), ([], []))

    def test_agency_number_with_dash(self):
        from core.lookup import agency_case_number
        self.assertEqual(agency_case_number(details_with_docket(JACKSON_AFFIDAVIT_DOCKET)), "26-032173")


class JavaScriptPageTest(unittest.TestCase):
    """hendrysheriff.org: la descarga directa trae menú, título y "JavaScript is required"."""

    MENU = " ".join(["About Us", "Mission & Core Values", "Meet the Sheriff", "Divisions", "Investigations",
                     "Road Patrol", "K-9 Unit", "Civil Division", "Detention Facility", "Marine Unit"] * 12)
    SKELETON = ("JavaScript is required. Enable JavaScript in your browser settings, then refresh this page. "
                + MENU + " HENDRY COUNTY ICE OPERATION LEADS TO THE ARREST OF A 2012 SEXUAL BATTERY AND KIDNAPPING CASE")
    BODY = ("Hendry County deputies arrested John Example, 41, on Friday during a joint operation. "
            "He was wanted on a 2012 warrant for sexual battery and kidnapping. ") * 6

    def article(self, text, via):
        from extractor.fetch import Article
        return Article(url="https://www.hendrysheriff.org/newsdigestlist/x", title="Hendry County Sheriff's Office FL",
                       text=text, published=None, via=via)

    def test_direct_skeleton_is_not_usable_nor_blocked(self):
        from extractor.fetch import _blocked, _usable
        page = self.article(self.SKELETON, "direct")
        self.assertFalse(_usable(page))
        self.assertFalse(_blocked(page))   # no es un bloqueo: el error no cambia

    def test_browser_page_with_the_notice_is_accepted(self):
        from extractor.fetch import _usable
        self.assertTrue(_usable(self.article("JavaScript is required. " + self.BODY, "browser")))

    def test_fetch_moves_on_to_the_browser(self):
        from unittest import mock
        from extractor import fetch
        html = "<html><head><title>{t}</title></head><body><article><p>{b}</p></article></body></html>"
        direct = html.format(t="Hendry County Sheriff's Office FL", b=self.SKELETON)
        rendered = html.format(t="Hendry County Sheriff's Office FL", b=self.BODY)
        with mock.patch.object(fetch.trafilatura, "fetch_url", return_value=direct), \
             mock.patch.object(fetch, "_fetch_with_browser", return_value=rendered), \
             mock.patch.dict("os.environ", {"BROWSER_PROVIDER": "local"}):
            article = fetch.fetch_article("https://www.hendrysheriff.org/newsdigestlist/x")
        self.assertEqual(article.via, "browser")
        self.assertIn("John Example", article.text)


class HendryTest(unittest.TestCase):
    # Flora Norys Gonzalez (Hendry, 26CF000196).
    DOCKET = [(13, "04/30/2026", "Motion for Pre-Trial Detention"),
              (10, "04/30/2026", "BOOKING REPORT FILED"),
              (9, "04/30/2026", "RECORD OF FIRST APPEARANCE FILED"),
              (3, "04/30/2026", "Agency Case Number: 26OFF001275")]

    def test_booking_report(self):
        self.assertEqual(choose("hendry", self.DOCKET), ([10], []))

    def test_booking_report_stays_in_hendry(self):
        self.assertEqual(choose("marion", self.DOCKET), ([], []))

    def test_agency_number(self):
        from core.lookup import agency_case_number
        self.assertEqual(agency_case_number(details_with_docket(self.DOCKET)), "26OFF001275")


class WrittenDateTest(unittest.TestCase):
    """hendrysheriff.org da como publicación el día en que se abre la página (19/10/2026)."""

    TEXT = ("On Monday night, April 27, 2026, Hendry County Deputies responded to a home on East State Road 80. "
            "On April 29, 2026, Hendry County Detectives arrested 64-year-old Flora Norys Gonzalez.")

    def fix(self, value, evidence, text):
        import importlib
        repair = importlib.import_module("extractor.repair")
        notes = []
        return repair._fix_date("incident_date", value, evidence, date(2026, 10, 19), notes, text), notes

    def test_date_written_in_the_text_is_kept(self):
        self.assertEqual(self.fix("2026-04-27", "Monday night", self.TEXT), ("2026-04-27", []))

    def test_date_not_in_the_text_is_still_counted_from_the_weekday(self):
        # Sin la fecha escrita, la regla de siempre: "Monday" contado desde la publicación.
        value, notes = self.fix("2026-04-27", "Monday night", "On Monday night deputies responded to a home.")
        self.assertEqual(value, "2026-10-19")
        self.assertTrue(notes)

    def test_model_date_that_is_not_the_written_one_is_still_corrected(self):
        value, _ = self.fix("2026-04-20", "Monday night", self.TEXT)
        self.assertEqual(value, "2026-10-19")

    def test_written_forms(self):
        import importlib
        written = importlib.import_module("extractor.repair")._written_in_text
        self.assertTrue(written(date(2026, 4, 27), "apr. 27th"))
        self.assertTrue(written(date(2026, 4, 27), "on 4/27/26 at night"))
        self.assertTrue(written(date(2026, 9, 3), "Sept. 3, 2026"))
        self.assertFalse(written(date(2025, 4, 27), self.TEXT))    # otro año
        self.assertFalse(written(date(2026, 4, 2), "April 27"))    # otro día


class WakullaTest(unittest.TestCase):
    # Curtis Woodward (Wakulla, 22CF000323).
    DOCKET = [(12, "09/12/2022", "BOND ORDER"),
              (11, "09/12/2022", "ARREST REPORT PACKAGE"),
              (2, "09/06/2022", "Agency Case Number: 22OFF001932")]

    def test_arrest_report_package(self):
        self.assertEqual(choose("wakulla", self.DOCKET), ([11], []))

    def test_stays_in_wakulla(self):
        self.assertEqual(choose("marion", self.DOCKET), ([], []))


class SwappedPublishedDateTest(unittest.TestCase):
    """wcso.org: "News Release Date 09-07-2022" se leía como el 9 de julio."""

    TEXT = ("On September 7, 2022, Wakulla County Sheriff's Office detectives arrested Curtis Woodward "
            "as part of Operation Death Dealer.")

    def build(self, published, text=TEXT, incident="2022-09-07"):
        from extractor import build_query
        from extractor.fetch import Article
        from extractor.schema import Extraction
        article = Article(url="https://www.wcso.org/x", title="WCSO makes arrest", text=text, published=published,
                          via="direct")
        extraction = Extraction.model_validate(dict(
            people=[dict(first_name="Curtis", last_name="Woodward", role="arrested", evidence="...")],
            incident_date=incident, county="Wakulla", state="FL", agency="Wakulla County Sheriff's Office",
            charges=["trafficking"]))
        return build_query(extraction, article)

    def test_swapped_publication_is_fixed(self):
        aq = self.build(date(2022, 7, 9))
        self.assertEqual(aq.query.published_date, date(2022, 9, 7))
        self.assertEqual(aq.query.incident_date, date(2022, 9, 7))   # ya no se descarta

    def test_right_publication_is_kept(self):
        aq = self.build(date(2022, 9, 7))
        self.assertEqual(aq.query.published_date, date(2022, 9, 7))

    def test_not_swapped_when_the_text_does_not_say_it(self):
        aq = self.build(date(2022, 7, 9), text="Detectives arrested Curtis Woodward on Wednesday.", incident=None)
        self.assertEqual(aq.query.published_date, date(2022, 7, 9))

    def test_day_over_12_cannot_be_swapped(self):
        aq = self.build(date(2022, 9, 13), text="On September 13, 2022, detectives arrested Curtis Woodward.",
                        incident="2022-09-13")
        self.assertEqual(aq.query.published_date, date(2022, 9, 13))


class BakerTest(unittest.TestCase):
    # Travis Roberts (Baker, 25000249CFMXBX).
    DOCKET = [(30, "07/08/2025", "PLEA OF NOT GUILTY"),
              (29, "07/08/2025", "BOOKING INFORMATION SHEET"),
              (28, "07/08/2025", "ARREST REPORT - INITIATING CASE"),
              (27, "07/08/2025", "FIRST APPEARANCE ORDER"),
              (26, "07/07/2025", "WARRANTS")]

    def test_arrest_report(self):
        self.assertEqual(choose("baker", self.DOCKET)[0], [28])

    def test_stays_in_baker(self):
        self.assertEqual(choose("marion", self.DOCKET)[0], [])


class BakerDoubleMurderTest(unittest.TestCase):
    """Trueblood y Hannah (Baker): una multa ganaba por nombre y el caso principal empataba con el consolidado."""

    QUERY = dict(county="baker", state="FL", date_from=date(2022, 6, 24), date_to=date(2024, 7, 12),
                 charges=["accessory after the fact to first-degree murder", "tampering with evidence",
                          "first-degree murder"])

    def case(self, number, defendant, offense, charges, consolidated=False, filed=date(2023, 1, 30)):
        from core.models import Charge
        candidate = CaseCandidate(number, defendant, "Felony", "OPEN", None, filed, "x")
        return CaseDetails(candidate, [Charge(offense, c, None, None, None, None) for c in charges], [],
                           consolidated=consolidated)

    def pick(self, first, last, cases):
        from core.matcher import pick_best, related_cases, resolve_same_incident
        q = LookupQuery(first_name=first, last_name=last, **self.QUERY)
        scored = [(score_case(q, d), d) for d in cases]
        winner, status = pick_best(scored)
        if status == "ambiguous":
            winner = resolve_same_incident(scored, lambda d: True)
            status = "found" if winner else status
        related = related_cases(winner[1], scored) if winner else []
        return status, winner[1].candidate.case_number if winner else None, related, scored

    def test_trueblood_traffic_ticket_no_longer_wins(self):
        status, number, related, scored = self.pick("Robert", "Trueblood", [
            self.case("23000043CFMXAX", "TRUEBLOOD, ROBERT", date(2023, 1, 27),
                      ["PUBLIC ORDER CRIMES - ACCESSORY AFTER THE FACT OF CAPITAL FE",
                       "OBSTRUCTING JUSTICE - TAMPER W/ EVIDENCE IN CRIMINAL PROCEED"], consolidated=True),
            self.case("23000067CFMXCX", "TRUEBLOOD, ROBERT", date(2022, 8, 1),
                      ["PUBLIC ORDER CRIMES - ACCESSORY AFTER THE FACT OF CAPITAL FE",
                       "OBSTRUCTING JUSTICE - TAMPER W/ EVIDENCE IN CRIMINAL PROCEED", "DEALING STOLEN PROPERTY"],
                      filed=date(2023, 2, 16)),
            self.case("23000010CTMXAX", "TRUEBLOOD, ROBERT", date(2022, 12, 28), ["DRIVING WHILE LIC SUSP OR REVOKED"],
                      filed=date(2023, 1, 5)),
        ])
        scores = {d.candidate.case_number: m.score for m, d in scored}
        self.assertLess(scores["23000010CTMXAX"], 60)                       # antes: 100
        self.assertIn("cargos coinciden (accessory, tampering)", [r for m, d in scored for r in m.reasons])
        self.assertEqual((status, number), ("found", "23000067CFMXCX"))    # el principal, no el consolidado
        self.assertEqual(related, ["23000043CFMXAX"])

    def test_hannah_main_case_over_consolidated(self):
        status, number, related, _ = self.pick("Waylon", "Hannah", [
            self.case("23000044CFMXAX", "HANNAH, WAYLON JOSEPH", date(2023, 1, 27),
                      ["PREMEDITATED MURDER", "GRAND THEFT OF FIREARM"], consolidated=True),
            self.case("23000067CFMXBX", "HANNAH, WAYLON JOSEPH", date(2022, 8, 1),
                      ["PREMEDITATED MURDER", "GRAND THEFT OF FIREARM", "CONSPIRACY"], filed=date(2023, 2, 16)),
        ])
        self.assertEqual((status, number, related), ("found", "23000067CFMXBX", ["23000044CFMXAX"]))

    def test_two_unrelated_cases_still_ambiguous(self):
        # Sin caso consolidado de por medio, dos casos distintos de la misma persona siguen siendo ambiguos.
        status, number, _, _ = self.pick("Waylon", "Hannah", [
            self.case("A", "HANNAH, WAYLON JOSEPH", date(2023, 1, 27), ["PREMEDITATED MURDER"]),
            self.case("B", "HANNAH, WAYLON JOSEPH", date(2022, 8, 1), ["PREMEDITATED MURDER"]),
        ])
        self.assertEqual((status, number), ("ambiguous", None))


class ChargeCategoryTest(unittest.TestCase):
    def test_obstructing_justice_is_tampering_not_resisting(self):
        from core.charges import categorize
        self.assertEqual(categorize(["OBSTRUCTING JUSTICE - TAMPER W/ EVIDENCE IN CRIMINAL PROCEED"]), {"tampering"})
        self.assertEqual(categorize(["tampering with evidence"]), {"tampering"})
        self.assertEqual(categorize(["RESIST OR OBSTRUCT WITHOUT VIOLENCE"]), {"resisting"})
        self.assertEqual(categorize(["obstructing an officer"]), {"resisting"})

    def test_accessory_and_suspended_license(self):
        from core.charges import categorize
        self.assertEqual(categorize(["ACCESSORY AFTER THE FACT OF CAPITAL FE"]), {"accessory"})
        self.assertEqual(categorize(["DRIVING WHILE LIC SUSP OR REVOKED"]), {"license"})
        self.assertEqual(categorize(["OPERATING WHILE DL SUSP CANC REVOKED 1ST OFFENSE"]), {"license"})


class BradfordTest(unittest.TestCase):
    # Katelin Drerup (Bradford, 19CF000612): el ARREST REPORT del día del arresto, no la orden por
    # violación de probation de 2022 que hay en otros casos.
    DOCKET = [(110, "01/20/2022", "VIOLATION OF PROBATION WARRANT SERVED"),
              (5, "09/19/2019", "CERTIFICATE OF ADDRESS"),
              (4, "09/19/2019", "ARREST REPORT"),
              (3, "09/19/2019", "FIRST APPEARANCE ORDER")]

    def test_arrest_report(self):
        self.assertEqual(choose("bradford", self.DOCKET)[0], [4])

    def test_stays_in_bradford(self):
        self.assertEqual(choose("marion", self.DOCKET)[0], [])


class WashingtonTest(unittest.TestCase):
    # Kyle Porter (Washington, 24CF000229).
    DOCKET = [(14, "06/21/2024", "FINGERPRINT CARDS AND PALM PRINTS"),
              (12, "06/21/2024", "FIRST APPEARANCE"),
              (6, "06/20/2024", "BENCH WARRANT W/ AFFIDAVIT/COMPLAINT")]

    def test_bench_warrant_affidavit_is_support(self):
        self.assertEqual(choose("washington", self.DOCKET), ([], [6]))

    def test_affidavit_complaint_is_main(self):
        rows = [(9, "08/14/2026", "FIRST APPEARANCE"), (8, "08/14/2026", "AFFIDAVIT - COMPLAINT")]
        self.assertEqual(choose("washington", rows), ([8], []))

    def test_stays_in_washington(self):
        self.assertEqual(choose("marion", self.DOCKET), ([], []))


class WindowAfterTest(unittest.TestCase):
    def test_porter_case_opened_54_days_after_is_inside(self):
        """Porter (Washington): hecho 27/04/2024, caso abierto con la orden el 20/06/2024; sin publicación."""
        from core.lookup import search_window
        q = LookupQuery(first_name="Kyle", last_name="Porter", county="washington", state="FL",
                        incident_date=date(2024, 4, 27))
        start, end = search_window(q, get_county("washington", "FL"))
        self.assertEqual((start, end), (date(2024, 4, 20), date(2024, 6, 26)))
        self.assertTrue(start <= date(2024, 6, 20) <= end)


class HardeeTest(unittest.TestCase):
    # Elvis Gray (Hardee, 24CF000270) y John Layport (Hardee, 26CF000203).
    GRAY = [(9, "08/13/2024", "BOOKING SUMMARY FILED"), (8, "08/13/2024", "WARRANT RETURNED SERVED"),
            (7, "08/12/2024", "ARREST REPORT FILED")]
    LAYPORT = [(15, "07/01/2026", "FIRST APPEARANCE"), (14, "07/01/2026", "COMPLAINT AFFIDAVIT")]

    def test_arrest_report_filed_and_warrant_served(self):
        self.assertEqual(choose("hardee", self.GRAY), ([7], [8]))

    def test_complaint_affidavit_still_works(self):
        self.assertEqual(choose("hardee", self.LAYPORT), ([14], []))

    def test_stays_in_hardee(self):
        self.assertEqual(choose("marion", self.GRAY), ([], []))


class GilchristTest(unittest.TestCase):
    # Bonilla (19CF000125) y Bartges (26CF000143 con arresto; 26CF000139, solo la orden).
    BONILLA = [(16, "07/17/2019", "COPY OF REQUEST FROM THE SMOKING GUN FOR COPY OF ARREST REPORT"),
               (10, "07/15/2019", "FIRST APPEARANCE ORDER"), (9, "07/15/2019", "ARREST REPORT")]
    BARTGES_ARREST = [(17, "08/18/2026", "CITATION - FLEE/ELUDE"), (10, "08/13/2026", "FIRST APPEARANCE ORDER"),
                      (9, "08/13/2026", "ARREST REPORT")]
    BARTGES_WARRANT = [(8, "08/06/2026", "SWORN COMPLAINT")]

    def test_arrest_report(self):
        self.assertEqual(choose("gilchrist", self.BONILLA), ([9], []))
        self.assertEqual(choose("gilchrist", self.BARTGES_ARREST), ([9], []))

    def test_sworn_complaint_is_support(self):
        self.assertEqual(choose("gilchrist", self.BARTGES_WARRANT), ([], [8]))

    def test_stays_in_gilchrist(self):
        self.assertEqual(choose("marion", self.BONILLA), ([], []))


class MadisonTest(unittest.TestCase):
    # Davis (26CF000227, FHP), McCray (26CF000193) y Barnhart (26CF000107).
    DAVIS = [(18, "09/03/2026", "CITATIONS"), (6, "09/01/2026", "ARREST REPORT - INITIATING CASE")]
    MCCRAY = [(20, "08/04/2026", "ARREST REPORT"), (12, "07/29/2026", "FIRST APPEARANCE ORDER")]
    BARNHART = [(22, "04/22/2026", "ARREST REPORT"), (10, "04/16/2026", "FIRST APPEARANCE ORDER"),
                (8, "04/15/2026", "BOOKING INFORMATION")]

    def test_both_arrest_report_names(self):
        self.assertEqual(choose("madison", self.DAVIS), ([6], []))
        self.assertEqual(choose("madison", self.MCCRAY), ([20], []))
        self.assertEqual(choose("madison", self.BARNHART), ([22], []))

    def test_stays_in_madison(self):
        self.assertEqual(choose("marion", self.DAVIS), ([], []))
