"""Tests del matcher y del parseo, sin navegador ni red.

    python -m unittest discover tests
"""

import unittest
from datetime import date

from adapters.orange_myeclerk import OrangeMyEClerkAdapter
from core.matcher import pick_best, prelim_score, score_case
from core.models import CaseCandidate, CaseDetails, DocketEntry, LookupQuery
from core.utils import format_short_us, last_name_variants, parse_us_date, split_defendant

QUERY = LookupQuery(
    first_name="Darrica", last_name="Ward", middle_name="Leandra", county="orange",
    incident_date=date(2024, 9, 4), arrest_date=date(2024, 9, 10),
    agency="Orlando Police Department",
)

# Datos reales leídos del portal el 2026-09-29.
WARD_ROW = {
    "cells": {
        "#": "1", "Case Number": "2024-CF-012151-A-O",
        "Description": "STATE OF FLORIDA - VS - WARD, DARRICA LEANDRA",
        "Type": "Criminal Felony", "Status": "Closed", "DOB": "03/07/1995",
        "Judge Name": "Sonia McDowell", "Date": "09/05/2024",
    },
    "href": "/CaseDetails?cItem=abc",
}
WARD_CHARGE = {
    "Offense Date": "9/4/2024",
    "Charge": "1. FALSE IMPRISONMENT\nStatute: 787.02(2)\nThird Degree - Felony",
    "Plea": "",
    "Arrest": "9/10/2024\nOBTS:8888888888\nSequence:1\nControl Number:2024-336326\nArresting Agency:Orlando Police Department",
    "Disposition": "", "Sentence": "",
}


def ward_details() -> CaseDetails:
    candidate = OrangeMyEClerkAdapter._parse_result_row(WARD_ROW)
    return CaseDetails(
        candidate=candidate,
        charges=[OrangeMyEClerkAdapter._parse_charge(WARD_CHARGE)],
        docket=[
            DocketEntry(date(2024, 9, 11), "Affidavit of Insolvency and Indigency", "/DocView/Doc?eCode=1"),
            DocketEntry(date(2024, 9, 11), "Arrest Affidavit", "/DocView/Doc?eCode=2"),
            DocketEntry(date(2024, 9, 5), "Affidavit to Issue Warrant", "/DocView/Doc?eCode=3"),
        ],
    )


class UtilsTest(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(parse_us_date("09/05/2024"), date(2024, 9, 5))
        self.assertEqual(parse_us_date("9/4/24"), date(2024, 9, 4))
        self.assertEqual(format_short_us(date(2024, 9, 1)), "9/1/24")

    def test_last_name_variants(self):
        self.assertEqual(last_name_variants("Ward"), ["Ward"])
        self.assertEqual(last_name_variants("Cruz Peraza"), ["Cruz Peraza", "CruzPeraza", "Cruz-Peraza"])
        self.assertEqual(last_name_variants("Cruzperaza"), ["Cruzperaza"])
        self.assertEqual(last_name_variants("Cruz-Peraza"), ["Cruz-Peraza", "CruzPeraza", "Cruz Peraza"])

    def test_suffix_is_not_middle_name(self):
        self.assertEqual(split_defendant("RODRIGUEZ, WILFREDO JR"), ("rodriguez", "wilfredo", ""))
        self.assertEqual(split_defendant("RODRIGUEZ, WILFREDO PUJOL"), ("rodriguez", "wilfredo", "pujol"))
        self.assertEqual(split_defendant("SMITH III, JOHN"), ("smith", "john", ""))

    def test_split_defendant(self):
        self.assertEqual(split_defendant("WARD, DARRICA LEANDRA"), ("ward", "darrica", "leandra"))


class OrangeParsingTest(unittest.TestCase):
    def test_both_description_formats(self):
        # Textos exactos del portal (capturas del 2026-09-30).
        for description, expected in [
            ("STATE OF FLORIDA - VS - RODRIGUEZ, WILFREDO JR", "RODRIGUEZ, WILFREDO JR"),
            ("STATE OF FLORIDA vs. RODRIGUEZ, WILFREDO PUJOL", "RODRIGUEZ, WILFREDO PUJOL"),
            ("STATE OF FLORIDA vs. RODRIGUEZ, JUSTIN LUIS", "RODRIGUEZ, JUSTIN LUIS"),
        ]:
            row = {"cells": {**WARD_ROW["cells"], "Description": description}, "href": "/x"}
            self.assertEqual(OrangeMyEClerkAdapter._parse_result_row(row).defendant, expected)


    def test_result_row(self):
        c = OrangeMyEClerkAdapter._parse_result_row(WARD_ROW)
        self.assertEqual(c.case_number, "2024-CF-012151-A-O")
        self.assertEqual(c.defendant, "WARD, DARRICA LEANDRA")
        self.assertEqual(c.filed_date, date(2024, 9, 5))

    def test_charge(self):
        ch = OrangeMyEClerkAdapter._parse_charge(WARD_CHARGE)
        self.assertEqual(ch.description, "FALSE IMPRISONMENT")
        self.assertEqual(ch.statute, "787.02(2)")
        self.assertEqual(ch.arrest_date, date(2024, 9, 10))
        self.assertEqual(ch.agency, "Orlando Police Department")
        self.assertEqual(ch.control_number, "2024-336326")


class MatcherTest(unittest.TestCase):
    def test_ward_is_found(self):
        match = score_case(QUERY, ward_details())
        self.assertGreaterEqual(match.score, 90, match.reasons)
        winner, status = pick_best([(match, ward_details())])
        self.assertEqual(status, "found")

    def test_poor_query_still_found(self):
        # Artículo escueto: solo nombre, apellido y fecha del incidente.
        poor = LookupQuery(first_name="Darrica", last_name="Ward", county="orange",
                           incident_date=date(2024, 9, 4))
        match = score_case(poor, ward_details())
        self.assertEqual(match.score, 100, match.reasons)
        self.assertEqual(pick_best([(match, ward_details())])[1], "found")

    def test_wrong_agency_lowers_confidence(self):
        # Nombre y fecha exactos pero otra agencia: baja mucho la confianza
        # pero sigue en el umbral (los artículos a veces citan otra agencia).
        wrong = LookupQuery(first_name="Darrica", last_name="Ward", county="orange",
                            incident_date=date(2024, 9, 4), agency="Orange County Sheriff's Office")
        match = score_case(wrong, ward_details())
        self.assertEqual(match.score, 60, match.reasons)
        self.assertIn("agencia distinta (orlando pd)", match.reasons)

    def test_other_first_name_is_discarded(self):
        details = ward_details()
        details.candidate.defendant = "WARD, MICHAEL JAMES"
        self.assertTrue(score_case(QUERY, details).disqualified)
        self.assertEqual(prelim_score(QUERY, details.candidate), 0)

    def test_two_similar_cases_are_ambiguous(self):
        a, b = ward_details(), ward_details()
        winner, status = pick_best([(score_case(QUERY, a), a), (score_case(QUERY, b), b)])
        self.assertEqual(status, "ambiguous")

    def test_document_priority_skips_indigency(self):
        from counties import get_county
        adapter = OrangeMyEClerkAdapter(page=None, county=get_county("orange"), captcha=None)
        docs = adapter.find_documents(ward_details())
        self.assertEqual(docs[0].description, "Arrest Affidavit")
        self.assertNotIn("Affidavit of Insolvency and Indigency", [d.description for d in docs])


    def test_complaint_is_used_when_no_arrest_affidavit(self):
        # Docket real de 2024-CF-012159-A-O (resumido).
        from counties import get_county
        adapter = OrangeMyEClerkAdapter(page=None, county=get_county("orange"), captcha=None)
        details = ward_details()
        details.docket = [
            DocketEntry(date(2025, 7, 24), "Motion to Dismiss Complaint", "/d?1"),
            DocketEntry(date(2024, 12, 26), "Information Filed", "/d?2"),
            DocketEntry(date(2024, 9, 6), "Affidavit of Insolvency and Indigency", "/d?3"),
            DocketEntry(date(2024, 9, 6), "Complaint", "/d?4"),
        ]
        docs = adapter.find_documents(details)
        self.assertEqual([d.description for d in docs], ["Complaint"])


    def test_compound_surname_variants_match(self):
        details = ward_details()
        details.candidate.defendant = "CRUZ PERAZA, REINALDO"
        for last in ("Cruzperaza", "Cruz Peraza", "Cruz-Peraza"):
            q = LookupQuery(first_name="Reinaldo", last_name=last, county="orange",
                            incident_date=date(2024, 9, 4))
            self.assertFalse(score_case(q, details).disqualified, last)


    def test_vs_dot_format_is_not_discarded(self):
        details = ward_details()
        details.candidate.defendant = OrangeMyEClerkAdapter._parse_result_row(
            {"cells": {**WARD_ROW["cells"], "Description": "STATE OF FLORIDA vs. RODRIGUEZ, WILFREDO PUJOL"},
             "href": "/x"}).defendant
        q = LookupQuery(first_name="Wilfredo", last_name="Rodriguez", county="orange",
                        incident_date=date(2024, 9, 4))
        self.assertFalse(score_case(q, details).disqualified)

    def test_suffix_in_query_or_portal(self):
        details = ward_details()
        details.candidate.defendant = "RODRIGUEZ, WILFREDO JR"
        for last in ("Rodriguez", "Rodriguez Jr."):
            q = LookupQuery(first_name="Wilfredo", last_name=last, middle_name="A", county="orange",
                            incident_date=date(2024, 9, 4))
            m = score_case(q, details)
            self.assertFalse(m.disqualified, last)
            self.assertNotIn("segundo nombre distinto (jr)", m.reasons)


if __name__ == "__main__":
    unittest.main()


class SameIncidentTest(unittest.TestCase):
    """Escenario basado en CLARKEROSEN, DAVID ALEXANDER (Orange, agosto 2023)."""

    QUERY = LookupQuery(first_name="David", last_name="Clarkerosen", county="orange",
                        incident_date=date(2023, 8, 2), arrest_date=date(2023, 8, 2),
                        agency="Orlando Police Department")

    @staticmethod
    def case(number, filed, offense, control, docs=("Complaint",)):
        from core.models import Charge
        cand = CaseCandidate(number, "CLARKEROSEN, DAVID ALEXANDER", "", "Closed",
                             date(1994, 1, 6), filed, "/x")
        charges = [Charge(offense, "X", None, offense, "Orlando Police Department", control)]
        docket = [DocketEntry(filed, d, f"/doc/{number}") for d in docs]
        return CaseDetails(cand, charges, docket)

    def scored(self, *cases):
        return [(score_case(self.QUERY, c), c) for c in cases]

    def adapter(self):
        from counties import get_county
        return OrangeMyEClerkAdapter(page=None, county=get_county("orange"), captcha=None)

    def test_cf_and_mm_same_incident_pick_cf(self):
        from core.matcher import related_cases, resolve_same_incident
        cf = self.case("2023-CF-010421-A-O", date(2023, 8, 3), date(2023, 8, 2), "2023-500001")
        mm = self.case("2023-MM-005527-A-O", date(2023, 8, 3), date(2023, 8, 2), "2023-500001")
        other = self.case("2023-MM-000205-A-E", date(2023, 8, 1), date(2023, 7, 31), "2023-499000")
        scored = self.scored(mm, other, cf)
        self.assertEqual(pick_best(scored)[1], "ambiguous")          # CF y MM empatan
        adapter = self.adapter()
        winner = resolve_same_incident(scored, lambda d: bool(adapter.find_documents(d)))
        self.assertEqual(winner[1].candidate.case_number, "2023-CF-010421-A-O")
        self.assertEqual(related_cases(winner[1], scored), ["2023-MM-005527-A-O"])
        other_score = score_case(self.QUERY, other).score
        self.assertLess(other_score, 90, "el incidente de 2 días antes debe quedar atrás")

    def test_consecutive_reports_same_arrest(self):
        # Datos reales: 23-47652 (CF) y 23-47653 (MM), mismo arresto del 03/08/2023.
        from core.matcher import resolve_same_incident, same_incident
        cf = self.case("2023-CF-010421-A-O", date(2023, 8, 3), date(2023, 8, 3), "23-47652")
        mm = self.case("2023-MM-005527-A-O", date(2023, 8, 3), date(2023, 8, 3), "23-47653")
        self.assertTrue(same_incident(cf, mm))
        winner = resolve_same_incident(self.scored(mm, cf), lambda d: True)
        self.assertEqual(winner[1].candidate.case_number, "2023-CF-010421-A-O")

    def test_prefers_the_case_that_has_the_document(self):
        from core.matcher import resolve_same_incident
        cf = self.case("2023-CF-010421-A-O", date(2023, 8, 3), date(2023, 8, 2), "2023-500001",
                       docs=("Information Filed",))
        mm = self.case("2023-MM-005527-A-O", date(2023, 8, 3), date(2023, 8, 2), "2023-500001")
        adapter = self.adapter()
        winner = resolve_same_incident(self.scored(cf, mm), lambda d: bool(adapter.find_documents(d)))
        self.assertEqual(winner[1].candidate.case_number, "2023-MM-005527-A-O")

    def test_different_incidents_stay_ambiguous(self):
        from core.matcher import resolve_same_incident
        # Mismo día pero otra agencia: arrestos distintos.
        from core.models import Charge
        a = self.case("2023-MM-005527-A-O", date(2023, 8, 3), date(2023, 8, 2), "2023-500001")
        b = self.case("2023-MM-005530-A-O", date(2023, 8, 3), date(2023, 8, 2), "2023-500777")
        b.charges = [Charge(date(2023, 8, 2), "X", None, date(2023, 8, 2), "Orange County Sheriff's Office", "2023-500777")]
        self.QUERY = LookupQuery(first_name="David", last_name="Clarkerosen", county="orange",
                                 incident_date=date(2023, 8, 2), arrest_date=date(2023, 8, 2))
        scored = self.scored(a, b)
        self.assertEqual(pick_best(scored)[1], "ambiguous")
        self.assertIsNone(resolve_same_incident(scored, lambda d: True))

    def test_distant_case_in_same_year_is_not_a_contender(self):
        right = self.case("2023-CF-010421-A-O", date(2023, 8, 3), date(2023, 8, 2), "2023-500001")
        june = self.case("2023-MM-003876-A-O", date(2023, 6, 3), date(2023, 6, 2), "2023-400000")
        winner, status = pick_best(self.scored(june, right))
        self.assertEqual((status, winner[1].candidate.case_number), ("found", "2023-CF-010421-A-O"))
