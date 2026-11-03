"""BenchmarkWeb (Osceola, 01/11/2026): lectura de resultados, caso y documentos.

Las filas y el docket tienen la forma que devuelven los JS del adaptador sobre el
HTML real del portal (exploración con probe_benchmark.py).
"""

import unittest
from datetime import date

from adapters.benchmark_web import (BenchmarkWebAdapter, arrest_date_from_docket, candidates_from_rows, case_from_title, site_root,
                                    charges_from_case, clean_name, court_code, docket_from_case,
                                    normalize_agency_name, split_ref, thumb_from_details, thumb_url)
from core.lookup import agency_case_number
from core.matcher import pick_best, related_cases, resolve_same_incident, same_incident, score_case
from core.models import CaseCandidate, CaseDetails, Charge, LookupQuery
from counties import get_county

GARCIA_ROWS = [
    {"case": "2026 CF 003520", "href": "/BenchmarkWeb/CourtCase.aspx/Details/3658470?digest=LZC",
     "name": "GARCIA, KATHRYN", "nameCell": "GARCIA, KATHRYN \n(1966)\n", "party": "DEFENDANT", "status": "OPEN",
     "thumb": "javascript:details(3658470, 'CourtCase.aspx/CaseThumbnail/', '?digest=LZC');"},
    {"case": "2026 CF 003520", "href": "/BenchmarkWeb/CourtCase.aspx/Details/3658470?digest=LZC",
     "name": "GARCIA, KATHRYN YOUNG", "nameCell": "GARCIA, KATHRYN YOUNG \n(1966)\n(Alias)\n", "party": "DEFENDANT",
     "status": "OPEN", "thumb": "javascript:details(3658470, 'CourtCase.aspx/CaseThumbnail/', '?digest=LZC');"},
    {"case": "2021 CC 002132 EV", "href": "/x", "name": "GARCIA, KATHRYN", "nameCell": "GARCIA, KATHRYN",
     "party": "PLAINTIFF", "status": "CLOSED", "thumb": ""},
    {"case": "2009 CA 011130 CI", "href": "/y", "name": "GARCIA, KATHRYN", "nameCell": "GARCIA, KATHRYN",
     "party": "DEFENDANT", "status": "CLOSED", "thumb": ""},
    {"case": "2004 TR 058204", "href": "/z", "name": "GARCIA, KATHRYN DELORES", "nameCell": "GARCIA, KATHRYN DELORES (1963)",
     "party": "DEFENDANT", "status": "CLOSED", "thumb": ""},
]

GARCIA_SUMMARY = {"judge": "KERESTES, ALISON", "casenumber": "2026 CF 003520", "clerkfiledate": "10/2/2026",
                  "agencyreportnumber": "26004685", "courttype": "FELONY", "status": "OPEN",
                  "agency": "ST CLOUD POLICE DEPARTMENT"}
GARCIA_THUMB = {"status": 200, "summary": {"Agency": "ST CLOUD POLICE DEPARTMENT", "Agency Report #": "26004685"},
                "charges": [{"count": "1", "description": "CRUELTY TOWARD CHILD-ABUSE CHILD WITHOUT GREAT BODILY HARM (827.03 2c)",
                             "offense": "10/01/2026", "initial": "10/02/2026", "obts": "4908022408"}]}
GARCIA_DOCKET = [
    {"seq": "16", "date": "10/20/2026", "text": "Order Allowing Withdrawal of Counsel", "cid": "62522454", "digest": "a"},
    {"seq": "10", "date": "10/5/2026", "text": "WARRANT RETURNED EXECUTED", "cid": "62441589", "digest": "b"},
    {"seq": "6", "date": "10/5/2026", "text": "CRIMINAL CASE - BOOKING REPORT FILED ADVICED TO DEFENDANT FIRST APPEARANCE",
     "cid": "62411637", "digest": "c"},
    {"seq": "3", "date": "10/2/2026", "text": "DATE OF ARREST", "cid": None, "digest": None},
    {"seq": "2", "date": "10/2/2026", "text": "CRIMINAL CASE OPENED - ARREST WARRANT FILED MEMORANDUM FILED "
                                             "AFFIDAVIT OF ARREST WARRANT", "cid": "62401969", "digest": "d"},
    {"seq": "1", "date": "10/2/2026", "text": "CASE FILED 10/02/2026 CASE NUMBER 2026 CF 003520", "cid": None, "digest": None},
]
# McMillian (arresto sin orden del Sheriff): sin "Agency Report Number" en el resumen.
MCMILLIAN_DOCKET = [
    {"seq": "9", "date": "9/28/2026", "text": "ORIGINAL APPLICATION FOR INDIGENT STATUS DEFENDANT DEEMED NOT INDIGENT",
     "cid": "1", "digest": "x"},
    {"seq": "3", "date": "9/28/2026", "text": "INITIAL APPEARANCE INFORMATION SHEET", "cid": "2", "digest": "y"},
    {"seq": "2", "date": "9/28/2026", "text": "BOOKING DOCUMENT", "cid": "3", "digest": "z"},
    {"seq": "1", "date": "9/28/2026", "text": "CASE FILED 09/28/2026 CASE NUMBER 2026 CF 003435", "cid": None, "digest": None},
]


def garcia_details() -> CaseDetails:
    candidate = candidates_from_rows(GARCIA_ROWS, ["CF", "MM", "CT"])[0]
    docket = docket_from_case(GARCIA_DOCKET)
    return CaseDetails(candidate, charges_from_case(GARCIA_SUMMARY, GARCIA_THUMB, arrest_date_from_docket(docket)),
                       docket, agency_report_number="26004685")


class ResultsTest(unittest.TestCase):
    def test_only_criminal_cases_as_defendant(self):
        cases = candidates_from_rows(GARCIA_ROWS, ["CF", "MM", "CT"])
        self.assertEqual([c.case_number for c in cases], ["2026 CF 003520"])
        c = cases[0]
        self.assertEqual((c.defendant, c.case_type, c.aliases), ("GARCIA, KATHRYN", "CF", ["GARCIA, KATHRYN YOUNG"]))
        self.assertEqual(c.dob, date(1966, 7, 1))       # solo el año: 1 de julio
        href, thumb = split_ref(c.detail_ref)
        self.assertEqual(href, "/BenchmarkWeb/CourtCase.aspx/Details/3658470?digest=LZC")
        self.assertEqual(thumb, "CourtCase.aspx/CaseThumbnail/3658470?digest=LZC")

    def test_alias_row_first(self):
        cases = candidates_from_rows(list(reversed(GARCIA_ROWS[:2])), ["CF"])
        self.assertEqual((cases[0].defendant, cases[0].aliases), ("GARCIA, KATHRYN", ["GARCIA, KATHRYN YOUNG"]))

    def test_helpers(self):
        self.assertEqual(court_code("2021 CC 002132 EV"), "CC")
        self.assertEqual(court_code("2026 CF 003520"), "CF")
        self.assertEqual(court_code("2024 CF 001234 A"), "CF")      # cualquier sufijo
        self.assertEqual(clean_name("GARCIA, KATHRYN YOUNG (1966) (Alias)"), "GARCIA, KATHRYN YOUNG")
        self.assertIsNone(thumb_url(""))
        # Un solo caso: el portal salta a la página del caso; su título trae número y acusado.
        self.assertEqual(case_from_title("\n 2026 CF 003435\n - STATE OF FLORIDA vs. MCMILLIAN, STACIA REBECCA\n"),
                         ("2026 CF 003435", "MCMILLIAN, STACIA REBECCA"))
        self.assertEqual(case_from_title("Search"), ("", ""))
        self.assertEqual(thumb_from_details("/BenchmarkWeb/CourtCase.aspx/Details/3655327?digest=J9VCu9MP+qD/A"),
                         "CourtCase.aspx/CaseThumbnail/3655327?digest=J9VCu9MP%2BqD%2FA")
        self.assertEqual(normalize_agency_name("OSCEOLA CO SHERIFF'S OFFICE"), "OSCEOLA COUNTY SHERIFF'S OFFICE")
        self.assertEqual(normalize_agency_name("ST CLOUD POLICE DEPARTMENT"), "ST CLOUD POLICE DEPARTMENT")


class CaseTest(unittest.TestCase):
    def test_charges_dates_and_report(self):
        d = garcia_details()
        c = d.charges[0]
        self.assertEqual((c.offense_date, c.initial_date, c.arrest_date), (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 2)))
        self.assertEqual((c.agency, c.control_number), ("ST CLOUD POLICE DEPARTMENT", "26004685"))
        self.assertEqual(agency_case_number(d), "26004685")

    def test_documents(self):
        adapter = BenchmarkWebAdapter(page=None, county=get_county("osceola"), captcha=None)
        d = garcia_details()
        self.assertEqual([e.number for e in adapter.find_documents(d)], [2])
        self.assertEqual([e.number for e in adapter.find_support_documents(d)], [6, 10])
        self.assertEqual(d.docket[4].doc_ref, "62401969|d")
        mcmillian = CaseDetails(d.candidate, [], docket_from_case(MCMILLIAN_DOCKET))
        self.assertEqual([e.description for e in adapter.find_documents(mcmillian)], ["BOOKING DOCUMENT"])
        self.assertIsNone(agency_case_number(mcmillian))

    def test_garcia_is_found(self):
        q = LookupQuery(first_name="Kathryn", last_name="Garcia", county="osceola", incident_date=date(2026, 10, 1),
                        arrest_date=date(2026, 10, 2), age=59, agency="St. Cloud Police Department",
                        charges=["child abuse"])
        match = score_case(q, garcia_details())
        self.assertEqual(pick_best([(match, garcia_details())])[1], "found", match.reasons)
        self.assertGreaterEqual(match.score, 90, match.reasons)


def jones_case(number, description, offense, initial, report):
    candidate = CaseCandidate(number, "JONES, JUSTICE WILLIAMS", "CF", "OPEN", date(1996, 7, 1), date(2026, 5, 22), "x")
    arrest = date(2026, 5, 21)                      # "DATE OF ARREST" en los dos dockets
    return CaseDetails(candidate, [Charge(offense, description, None, arrest, "OSCEOLA COUNTY SHERIFF'S OFFICE",
                                          report, initial_date=initial)], [])


class SameArrestTest(unittest.TestCase):
    """Jones (02/11/2026): la huida al detenerlo y la agresión sexual de enero, un solo arresto."""

    FLEE = jones_case("2026 CF 001741", "FLEE ELUDE LEO WITH LIGHTS SIREN ACTIVE (316.1935 2)",
                      date(2026, 5, 21), date(2026, 5, 22), "26I055423")
    ABUSE = jones_case("2026 CF 001685", "SEXUAL BATTERY WITH A CHILD 12-18 (FAMILIAL/CUSTODIAL AUTHORITY) (794.011 8b)",
                       date(2026, 1, 5), date(2026, 5, 21), "26I053803")

    def query(self, charges):
        return LookupQuery(first_name="Justice", last_name="Jones", county="osceola", age=30,
                           arrest_date=date(2026, 5, 20), agency="Osceola County Sheriff's Office", charges=charges,
                           published_date=date(2026, 5, 22), age_as_of=date(2026, 5, 22))

    def test_same_arrest_date_is_one_arrest(self):
        self.assertTrue(same_incident(self.FLEE, self.ABUSE))
        q = self.query(["attempted sexual battery on a victim aged 16 or 17", "fleeing and eluding"])
        scored = [(score_case(q, d), d) for d in (self.ABUSE, self.FLEE)]
        self.assertEqual(pick_best(scored)[1], "ambiguous")
        winner = resolve_same_incident(scored, lambda d: True)[1]
        # Los dos tienen cargos de la nota: gana el de la ofensa del día del arresto (la detención).
        self.assertEqual(winner.candidate.case_number, "2026 CF 001741")
        self.assertEqual(related_cases(winner, scored), ["2026 CF 001685"])

    def test_case_with_the_story_charges_wins(self):
        """Si la nota solo habla de la agresión, gana ese caso aunque el otro sea del día del arresto."""
        q = self.query(["sexual battery on a child"])
        scored = [(score_case(q, d), d) for d in (self.FLEE, self.ABUSE)]
        winner = resolve_same_incident(scored, lambda d: True, min_margin=100)
        self.assertEqual(winner[1].candidate.case_number, "2026 CF 001685")

    def test_other_person_or_day_is_not_the_same_arrest(self):
        other_day = jones_case("2026 CF 002000", "BATTERY", date(2026, 6, 1), date(2026, 6, 2), "26I060000")
        other_day.charges[0].arrest_date = date(2026, 6, 2)
        self.assertFalse(same_incident(self.FLEE, other_day))


class BookingReportTest(unittest.TestCase):
    def test_opened_booking_report_is_the_main_document(self):
        """Carlos Moises Garcia (2026 CF 001491): arresto sin orden, la narración va en el "OPENED"."""
        adapter = BenchmarkWebAdapter(page=None, county=get_county("osceola"), captcha=None)
        rows = [{"seq": "4", "date": "5/8/2026", "text": "CRIMINAL CASE OPENED - BOOKING REPORT FILED ADVICED TO "
                 "DEFENDANT FIRST APPEARANCE", "cid": "1", "digest": "a"}]
        d = CaseDetails(garcia_details().candidate, [], docket_from_case(rows))
        self.assertEqual([e.number for e in adapter.find_documents(d)], [4])
        self.assertEqual(adapter.find_support_documents(d), [])
        # Sin "OPENED" (la ficha de un arresto con orden) sigue de apoyo.
        self.assertEqual([e.number for e in adapter.find_support_documents(garcia_details())], [6, 10])


# Moraitis (Flagler, 2026 CF 000915): arresto con orden. La narración está en el "SWORN COMPLAINT"
# (Seq 2); el "ARRESTING AFFIDAVIT" (Seq 12) solo dice que se ejecutó la orden.
MORAITIS_DOCKET = [
    {"seq": "15", "date": "10/26/2026", "text": "ORIGINAL WARRANT RETURNED SERVED", "cid": None, "digest": None},
    {"seq": "13", "date": "10/26/2026", "text": "BOOKING CARD", "cid": "13", "digest": "c"},
    {"seq": "12", "date": "10/26/2026", "text": "ARRESTING AFFIDAVIT", "cid": "12", "digest": "b"},
    {"seq": "10", "date": "10/24/2026", "text": "REARRESTED ON 10/24/2026: OBTS # 1803006523", "cid": None, "digest": None},
    {"seq": "2", "date": "10/16/2026", "text": "SWORN COMPLAINT", "cid": "2", "digest": "a"},
    {"seq": "1", "date": "10/16/2026", "text": "SWORN COMPLAINT COUNT: 1 - (800.04(4)(A)(1)) LEWD OR LASCIVIOUS BATTERY "
                                               "VICTIM AGE 12 TO 16", "cid": None, "digest": None},
]


class FlaglerTest(unittest.TestCase):
    def adapter(self):
        return BenchmarkWebAdapter(page=None, county=get_county("flagler"), captcha=None)

    def test_site_root(self):
        self.assertEqual(site_root("https://cases.flaglerclerk.gov/Home.aspx/Search"), "https://cases.flaglerclerk.gov/")
        self.assertEqual(site_root("https://courts.osceolaclerk.com/BenchmarkWeb/Home.aspx/Search"),
                         "https://courts.osceolaclerk.com/BenchmarkWeb/")

    def test_rearrested_on(self):
        self.assertEqual(arrest_date_from_docket(docket_from_case(MORAITIS_DOCKET)), date(2026, 10, 24))

    def test_warrant_case_documents(self):
        d = CaseDetails(garcia_details().candidate, [], docket_from_case(MORAITIS_DOCKET))
        self.assertEqual([e.description for e in self.adapter().find_documents(d)],
                         ["SWORN COMPLAINT", "ARRESTING AFFIDAVIT"])
        self.assertEqual([e.number for e in self.adapter().find_support_documents(d)], [12])

    def test_warrantless_case_documents(self):
        rows = [r for r in MORAITIS_DOCKET if r["seq"] != "2"]
        d = CaseDetails(garcia_details().candidate, [], docket_from_case(rows))
        self.assertEqual([e.number for e in self.adapter().find_documents(d)], [12])

    def test_hcaptcha_is_known(self):
        from core.captcha import SOLVED_JS
        self.assertIn("h-captcha-response", SOLVED_JS["hcaptcha"])
        self.assertEqual(get_county("flagler").captcha, "hcaptcha")

    def test_animal_category(self):
        from core.charges import categorize
        self.assertEqual(categorize(["CRUELTY TO ANIMALS (828.12 1)"]), {"animal"})
        self.assertIn("animal", categorize(["animal cruelty", "abandonment of an animal"]))
        self.assertNotIn("animal", categorize(["GRAND THEFT CATALYTIC CONVERTER"]))


if __name__ == "__main__":
    unittest.main()
