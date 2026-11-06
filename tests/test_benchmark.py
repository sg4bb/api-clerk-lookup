"""BenchmarkWeb (Osceola, 01/11/2026): lectura de resultados, caso y documentos.

Las filas y el docket tienen la forma que devuelven los JS del adaptador sobre el
HTML real del portal (exploración con probe_benchmark.py).
"""

import unittest
from datetime import date

from adapters.benchmark_web import (BenchmarkWebAdapter, arrest_date_from_docket, candidates_from_rows, case_from_title, site_root,
                                    consolidated_into,
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


# Matthew (St. Johns, 26001446CFMA): arresto sin orden del Sheriff, unido después a 26001422CFMA.
MATTHEW_DOCKET = [
    {"seq": "11", "date": "10/6/2026", "text": "CONSOLIDATED TO 26001422CFMA - ADD ON CHARGE", "cid": None, "digest": None},
    {"seq": "8", "date": "9/24/2026", "text": "APPLICATION FOR INDIGENT STATUS (APPROVAL PENDING)", "cid": "8", "digest": "c"},
    {"seq": "6", "date": "9/24/2026", "text": "FIRST APPEARANCE FORM", "cid": "6", "digest": "b"},
    {"seq": "5", "date": "9/24/2026", "text": "ARREST REPORT", "cid": "5", "digest": "a"},
    {"seq": "2", "date": "9/24/2026", "text": "CASE FILED 09/24/2026 CASE NUMBER 26001446CFMA", "cid": None, "digest": None},
    {"seq": "1", "date": "9/17/2026", "text": "ARREST COUNT: 1 - (828.122 3A) ANIMAL FIGHTING", "cid": None, "digest": None},
]


class StJohnsTest(unittest.TestCase):
    def test_case_number_formats(self):
        for number, code in (("26001446CFMA", "CF"), ("26009995TR", "TR"), ("99007733CTMA", "CT"),
                             ("CC26-5468", "CC"), ("2026CF000052AXMA", "CF"), ("2026 MM 000546", "MM")):
            self.assertEqual(court_code(number), code, number)
        self.assertEqual(case_from_title("26001446CFMA - STATE OF FLORIDA vs. MATTHEW, JAKE CAMERON"),
                         ("26001446CFMA", "MATTHEW, JAKE CAMERON"))
        rows = [{"case": n, "href": "/x", "name": "MATTHEW, JAKE CAMERON", "nameCell": "MATTHEW, JAKE CAMERON",
                 "party": p, "status": "OPEN", "thumb": ""}
                for n, p in (("26001446CFMA", "DEFENDANT"), ("26009995TR", "DEFENDANT"), ("CC26-5468", "DEFENDANT"),
                             ("DR26-2231", "RESPONDENT"), ("22001414MMMA", "DEFENDANT"))]
        self.assertEqual([c.case_number for c in candidates_from_rows(rows, ["CF", "MM", "CT"])],
                         ["26001446CFMA", "22001414MMMA"])

    def test_docket(self):
        docket = docket_from_case(MATTHEW_DOCKET)
        self.assertEqual(arrest_date_from_docket(docket), date(2026, 9, 17))
        self.assertEqual(consolidated_into(docket), "26001422CFMA")
        adapter = BenchmarkWebAdapter(page=None, county=get_county("st_johns"), captcha=None)
        d = CaseDetails(garcia_details().candidate, [], docket)
        self.assertEqual([e.description for e in adapter.find_documents(d)], ["ARREST REPORT"])


if __name__ == "__main__":
    unittest.main()


def matthew_case(number, filed, charges, docket, consolidated=False):
    """Cargos (descripción, ofensa, Initial); el arresto sale del docket como en open_case."""
    candidate = CaseCandidate(number, "MATTHEW, JAKE CAMERON", "CF", "OPEN", None, filed, "x")
    docket = docket_from_case(docket)
    arrest = arrest_date_from_docket(docket)
    return CaseDetails(candidate, [Charge(offense, text, None, arrest, "ST JOHNS COUNTY SHERIFF'S OFFICE",
                                          "26OFF008738", initial_date=initial) for text, offense, initial in charges],
                       docket, consolidated=consolidated)


class StJohnsArrestCountTest(unittest.TestCase):
    """Matthew (St. Johns): 26001422CFMA anota un "ARREST COUNT" por cargo, con fechas distintas
    (07/30 a 09/17); no es la fecha del arresto y no debe castigarlo frente a 26001446CFMA."""

    MAIN_DOCKET = [{"seq": str(i), "date": d, "text": f"ARREST COUNT: {i} - (828.12 2) CRUELTY TO ANIMALS",
                    "cid": None, "digest": None}
                   for i, d in enumerate(("8/25/2026", "8/25/2026", "7/30/2026", "8/23/2026", "8/25/2026",
                                          "9/17/2026"), start=1)] + [
        {"seq": "9", "date": "9/24/2026", "text": "ARREST REPORT", "cid": "9", "digest": "a"}]
    MAIN = matthew_case("26001422CFMA", date(2026, 9, 22), [
        ("CRUELTY TO ANIMALS (828.12 2)", date(2026, 8, 25), date(2026, 8, 25)),
        ("CRUELTY TO ANIMALS (828.12 2)", date(2026, 8, 25), date(2026, 8, 25)),
        ("CRUELTY TO ANIMALS (828.12 2)", date(2026, 7, 30), date(2026, 7, 30)),
        ("CRUELTY TO ANIMALS (828.12 2)", date(2026, 8, 23), date(2026, 8, 23)),
        ("CRUELTY TO ANIMALS (828.12 2)", date(2026, 8, 25), date(2026, 8, 25)),
        ("POSSESSION OF FIREARM BY CONVICTED FELON (790.23 1a)", date(2026, 9, 17), date(2026, 9, 17))],
        MAIN_DOCKET)
    ADD_ON = matthew_case("26001446CFMA", date(2026, 9, 24), [
        ("ANIMAL FIGHTING (828.122 3A)", date(2026, 9, 17), date(2026, 9, 17))], MATTHEW_DOCKET, consolidated=True)

    def test_several_arrest_count_dates_are_not_the_arrest(self):
        self.assertIsNone(arrest_date_from_docket(docket_from_case(self.MAIN_DOCKET)))
        self.assertEqual(arrest_date_from_docket(docket_from_case(MATTHEW_DOCKET)), date(2026, 9, 17))

    def test_main_case_wins(self):
        q = LookupQuery(first_name="Jake", last_name="Matthew", county="st_johns", arrest_date=date(2026, 9, 17),
                        agency="St. Johns County Sheriff's Office",
                        charges=["animal cruelty", "possession of a firearm by a convicted felon"],
                        published_date=date(2026, 9, 18))
        scored = [(score_case(q, d), d) for d in (self.ADD_ON, self.MAIN)]
        main = next(s for s, d in scored if d is self.MAIN)
        self.assertFalse(any("lejana" in r for r in main.reasons), main.reasons)
        self.assertTrue(same_incident(self.MAIN, self.ADD_ON))
        winner = resolve_same_incident(scored, lambda d: True)
        self.assertEqual(winner[1].candidate.case_number, "26001422CFMA", [(s.score, s.reasons) for s, _ in scored])
        self.assertEqual(related_cases(winner[1], scored), ["26001446CFMA"])


# Charlotte (03/11/2026): Byrne-Levy, 26001343F. Docket real (DIN, fecha, entrada).
BYRNE_DOCKET = [
    {"seq": "18", "date": "11/3/2026", "text": "APPLICATION FOR INDIGENT STATUS - INCAPABLE OF SCREENING - DISORDERLY",
     "cid": "39273501", "digest": "l"},
    {"seq": "16", "date": "11/3/2026", "text": "RECORD OF FIRST APPEARANCE HEARING", "cid": "39273110", "digest": "z"},
    {"seq": "11", "date": "10/31/2026", "text": "CCSO MEMORANDUM - REFUSAL TO PROCESS", "cid": "39266695", "digest": "m"},
    {"seq": "10", "date": "10/31/2026", "text": "BOOKING REPORT FILED", "cid": "39266641", "digest": "b"},
    {"seq": "9", "date": "10/31/2026", "text": "JESSICA LUNSFORD CERTIFICATE", "cid": None, "digest": None},
    {"seq": "7", "date": "10/31/2026", "text": "AFFIDAVIT OF PROBABLE CAUSE", "cid": "39266638", "digest": "a"},
    {"seq": "4", "date": "10/31/2026",
     "text": "ARREST SEQ: 1 -  (316.1935 3A) FLEE/ELUDE POLICE FLEE W DISREGARD OF SAFETY TO PERSONS OR PROP 4096",
     "cid": None, "digest": None},
    {"seq": "3", "date": "10/31/2026", "text": "ARREST SEQ: 2 -  (843.02) RESIST/OBSTRUCT/OPPOSE OFFICER WITHOUT VIOLENCE 3143",
     "cid": None, "digest": None},
    {"seq": "1", "date": "10/31/2026", "text": "CASE FILED 10/31/2026 CASE NUMBER 26001343F", "cid": None, "digest": None},
]


class CharlotteTest(unittest.TestCase):
    def setUp(self):
        self.county = get_county("charlotte")

    def test_case_numbers(self):
        from adapters.benchmark_web import case_type
        codes = self.county.case_type_codes
        self.assertEqual([court_code(n) for n in ("26001343F", "22001059CC", "21007084TR", "26001446CFMA")],
                         ["F", "CC", "TR", "CF"])
        self.assertEqual(case_type("26001343F", codes), "CF")
        self.assertEqual(case_type("26001343F"), "F")                 # sin la tabla del condado, tal cual
        rows = [{"case": n, "href": "/x", "name": "BYRNE LEVY, MAURIN", "nameCell": "BYRNE LEVY, MAURIN (2000)",
                 "party": "DEFENDANT", "status": "OPEN", "thumb": ""}
                for n in ("26001343F", "22001059CC", "21007084TR", "26000945T", "26000149O")]
        found = candidates_from_rows(rows, ["CF", "MM", "CT"], codes)
        # "T" = TRAFFIC (DUI de Clemens): penal. "TR" (infracción), "CC" (civil) y "O" (ordenanza), fuera.
        self.assertEqual([(c.case_number, c.case_type) for c in found], [("26001343F", "CF"), ("26000945T", "CT")])
        self.assertEqual(case_from_title("26001343F - STATE OF FLORIDA vs. BYRNE LEVY, MAURIN"),
                         ("26001343F", "BYRNE LEVY, MAURIN"))

    def test_docket(self):
        docket = docket_from_case(BYRNE_DOCKET)
        self.assertEqual(arrest_date_from_docket(docket), date(2026, 10, 31))
        adapter = BenchmarkWebAdapter(page=None, county=self.county, captcha=None)
        d = CaseDetails(garcia_details().candidate, [], docket)
        self.assertEqual([e.description for e in adapter.find_documents(d)], ["AFFIDAVIT OF PROBABLE CAUSE"])
        self.assertEqual([e.description for e in adapter.find_support_documents(d)], ["BOOKING REPORT FILED"])

    def test_agency_matches_the_story(self):
        from core.matcher import normalize_agency
        name = normalize_agency_name("SHERIFF'S OFFICE - CHARLOTTE COUNTY")
        self.assertEqual(name, "CHARLOTTE COUNTY SHERIFF'S OFFICE")
        self.assertEqual(normalize_agency(name), normalize_agency("Charlotte County Sheriff's Office"))
        self.assertEqual(normalize_agency_name("ST JOHNS COUNTY SHERIFFS OFFICE"), "ST JOHNS COUNTY SHERIFFS OFFICE")
        self.assertEqual(normalize_agency_name("OSCEOLA CO SHERIFF'S OFFICE"), "OSCEOLA COUNTY SHERIFF'S OFFICE")

    def test_places(self):
        from counties import florida
        self.assertEqual(florida.CITY_TO_COUNTY["punta gorda"], "charlotte")
        self.assertEqual(florida.CITY_TO_COUNTY["port charlotte"], "charlotte")
        self.assertEqual(florida.AGENCY_TO_COUNTY["charlotte county so"], "charlotte")


# Indian River (05/11/2026): Ruma, 2026 CF 001179 A (orden de Vero Beach PD entregada por el Sheriff).
# Filas como las devuelve _READ_CASE_JS (con la columna DIN, que aquí va después de la imagen).
RUMA_DOCKET = [
    {"seq": "54", "date": "11/4/2026", "text": "ARRAIGNMENT SET FOR 12/09/2026 AT 9:00 AM IN 5/ , JDG: NORMAN, ANASTASIA",
     "cid": None, "digest": None},
    {"seq": "50", "date": "10/30/2026", "text": "PUBLIC DEFENDER GRANTED", "cid": "20923507", "digest": "t"},
    {"seq": "33", "date": "10/28/2026", "text": "ARREST SEQ: 30 - (784.048(2)) STALKING", "cid": None, "digest": None},
    {"seq": "3", "date": "10/28/2026",
     "text": "ARREST SEQ: 1 - (836.13(2)) SEX OFFENSE PROMOTE ALTERD SEX DEPICTION PERSON WO CONSENT",
     "cid": None, "digest": None},
    {"seq": "48", "date": "10/28/2026", "text": "ARREST AFFIDAVIT", "cid": "20915447", "digest": "a"},
    {"seq": "47", "date": "10/28/2026", "text": "LIVE WARRANT STATUS CHANGED TO SERVED", "cid": None, "digest": None},
    {"seq": "46", "date": "10/27/2026", "text": "REARRESTED ON 10/27/2026: OBTS # 3101163630", "cid": None, "digest": None},
    {"seq": "36", "date": "10/27/2026", "text": "LIVE WARRANT ISSUED: LIVE WARRANT", "cid": "20912000", "digest": "w"},
    {"seq": "31", "date": "10/27/2026",
     "text": "SWORN COMPLAINT SEQ: 28 - (836.13(4)) SEX OFFENSE - PROMOTE ALTERD SEX DEPICTION PERSON WO CONSENT",
     "cid": None, "digest": None},
    {"seq": "1", "date": "10/27/2026", "text": "CASE FILED 10/27/2026 CASE NUMBER 2026 CF 001179 A", "cid": None, "digest": None},
]


class IndianRiverTest(unittest.TestCase):
    def setUp(self):
        self.county = get_county("indian_river")

    def test_case_numbers(self):
        self.assertEqual([court_code(n) for n in ("2026 CF 001193 A", "1989 CT 012742 A", "2013 TR 008812 A")],
                         ["CF", "CT", "TR"])
        self.assertEqual(case_from_title("2026 CF 001193 A - STATE OF FLORIDA vs. FINNEGAN, JOHN ERIC"),
                         ("2026 CF 001193 A", "FINNEGAN, JOHN ERIC"))
        rows = [{"case": n, "href": "/x", "name": "RUMA, PETER SOLOMON", "nameCell": "RUMA, PETER SOLOMON",
                 "party": "DEFENDANT", "status": "OPEN", "thumb": ""}
                for n in ("2026 CF 001179 A", "2018 TR 008619 A", "2023 SC 002216")]
        found = candidates_from_rows(rows, ["CF", "MM", "CT"], self.county.case_type_codes)
        self.assertEqual([c.case_number for c in found], ["2026 CF 001179 A"])

    def test_docket(self):
        docket = docket_from_case(RUMA_DOCKET)
        self.assertEqual(arrest_date_from_docket(docket), date(2026, 10, 27))
        adapter = BenchmarkWebAdapter(page=None, county=self.county, captcha=None)
        d = CaseDetails(garcia_details().candidate, [], docket)
        self.assertEqual([e.description for e in adapter.find_documents(d)], ["ARREST AFFIDAVIT"])
        self.assertEqual([e.description for e in adapter.find_support_documents(d)],
                         ["LIVE WARRANT ISSUED: LIVE WARRANT"])

    def test_agency_and_places(self):
        from core.matcher import normalize_agency
        from counties import florida
        self.assertEqual(normalize_agency(normalize_agency_name("INDIAN RIVER COUNTY - SHERIFF")),
                         normalize_agency("Indian River County Sheriff's Office"))
        self.assertEqual(florida.CITY_TO_COUNTY["vero beach"], "indian_river")
        self.assertEqual(florida.AGENCY_TO_COUNTY[normalize_agency("Vero Beach Police Department")], "indian_river")


# Bay (06/11/2026): Drinkard, 18005242CFMA. BenchmarkWeb2: "Image | DIN | Date | Entry"; el affidavit
# de arresto va como imagen de la entrada "CASE FILED"; muchas entradas sin imagen pública.
DRINKARD_DOCKET = [
    {"seq": "22", "date": "12/21/2018", "text": "EP - INFORMATION FILED", "cid": "40000022", "digest": "i"},
    {"seq": "16", "date": "11/29/2018", "text": "AFFIDAVIT AND ORDER OF INSOLVENCY FILED", "cid": None, "digest": None},
    {"seq": "3", "date": "11/28/2018", "text": "CASE FILED 11/28/2018 CASE NUMBER 18005242CFMA",
     "cid": "40000003", "digest": "c"},
    {"seq": "9", "date": "11/25/2018", "text": "ARREST SEQ: 1 - (78204 2) MURDER", "cid": None, "digest": None},
]


class BayTest(unittest.TestCase):
    def setUp(self):
        self.county = get_county("bay")

    def test_case_numbers(self):
        self.assertEqual([court_code(n) for n in ("18005242CFMA", "18002455MMMA", "15003444CTMA", "13029183TR",
                                                  "08000292PKMA")], ["CF", "MM", "CT", "TR", "PK"])

    def test_docket(self):
        docket = docket_from_case(DRINKARD_DOCKET)
        self.assertEqual(arrest_date_from_docket(docket), date(2018, 11, 25))
        adapter = BenchmarkWebAdapter(page=None, county=self.county, captcha=None)
        d = CaseDetails(garcia_details().candidate, [], docket)
        self.assertEqual([e.description for e in adapter.find_documents(d)],
                         ["CASE FILED 11/28/2018 CASE NUMBER 18005242CFMA"])

    def test_agency_and_places(self):
        from core.matcher import normalize_agency
        from counties import florida
        self.assertEqual(normalize_agency("BAY COUNTY SHERIFF"), normalize_agency("Bay County Sheriff's Office"))
        self.assertEqual(florida.CITY_TO_COUNTY["lynn haven"], "bay")
        self.assertEqual(florida.AGENCY_TO_COUNTY[normalize_agency("Panama City Police Department")], "bay")


class CloudflareTest(unittest.TestCase):
    """Bay (06/11/2026): "baycoclerk.com | 522: Connection timed out" no son "0 resultados"."""

    def test_522_page(self):
        from playwright.sync_api import sync_playwright
        from adapters.benchmark_web import _CF_ERROR_JS, _RESULTS_STATE_JS
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content("<title>baycoclerk.com | 522: Connection timed out</title>"
                             "<body>Connection timed out Error code 522</body>")
            self.assertTrue(page.evaluate(_CF_ERROR_JS))
            self.assertEqual(page.evaluate(_RESULTS_STATE_JS), "cf_error")
            page.set_content("<title>Search Results</title><body><div class='caseFoundFilter'>Cases Found 0</div></body>")
            self.assertFalse(page.evaluate(_CF_ERROR_JS))
            self.assertEqual(page.evaluate(_RESULTS_STATE_JS), "empty")
            browser.close()


class ResultColumnsTest(unittest.TestCase):
    """Bay (06/11/2026): BenchmarkWeb2 agrega "PIN" antes de "Party Type"; se leía el PIN como tipo
    de parte ("2280303") y todas las filas quedaban fuera."""

    ROW = ("<tr><td><img onclick=\"javascript:details(1, 'CourtCase.aspx/CaseThumbnail/', '?digest=x');\"></td>"
           "<td><a title='View Party Details for MOUSER, SHANE A'>MOUSER, SHANE A</a> (1989)</td>{pin}"
           "<td>DEFENDANT</td><td><a href='/d/1' title='View Case Details for 24000293CFMA'>24000293CFMA</a></td>"
           "<td>CLOSED</td></tr>")
    HEAD = ("<thead><tr><th class='detailCol'>Summary</th><th class='nameCol'>Name</th>{pin}"
            "<th class='partyTypeCol'>Party Type</th><th class='caseNumberCol'>Case Number</th>"
            "<th class='statusCol'>Status</th></tr></thead>")

    def read(self, pin: bool):
        from playwright.sync_api import sync_playwright
        from adapters.benchmark_web import _READ_ROWS_JS
        html = ("<table id='gridSearchResults'>" + self.HEAD.format(pin="<th class='pinCol'>PIN</th>" if pin else "")
                + "<tbody>" + self.ROW.format(pin="<td>2280303</td>" if pin else "") + "</tbody></table>")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content(html)
            rows = page.evaluate(_READ_ROWS_JS)
            browser.close()
        return rows

    def test_with_pin_column(self):
        rows = self.read(pin=True)
        self.assertEqual((rows[0]["party"], rows[0]["status"]), ("DEFENDANT", "CLOSED"))
        self.assertEqual([c.case_number for c in candidates_from_rows(rows, ["CF"])], ["24000293CFMA"])

    def test_without_pin_column(self):
        rows = self.read(pin=False)
        self.assertEqual((rows[0]["party"], rows[0]["status"]), ("DEFENDANT", "CLOSED"))
