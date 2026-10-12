"""Civitek OCRS (Marion y otros 31 condados): lectura de resultados y del caso,
elección del documento y puntaje sin fechas en los cargos. Datos ficticios con
la misma forma que devuelve el portal (mapeado el 12/10/2026)."""

import unittest
from datetime import date

from adapters.civitek_ocrs import (candidates_from_results, case_number, charges_from_case,
                                   docket_from_case)
from core.adapter import ClerkAdapter
from core.matcher import score_case
from core.models import CaseDetails, LookupQuery
from counties import get_county

RESULTS = [{
    "name": "TESTER, JANE MARIE", "expanded": True,
    "cases": [{"ucn": "422026CF001234CFAXMX", "local": "26CF001234AX",
               "href": "caseinformation.xhtml?query=abc&from=partySearchResults&partyIndex=0",
               "file_date": "03/18/2026", "case_type": "Felony 22-D", "status": "OPEN", "charges": []},
              {"ucn": "422025MM000777MMAXMX", "local": "25MM000777AX",
               "href": "caseinformation.xhtml?query=def&from=partySearchResults&partyIndex=0",
               "file_date": "06/02/2025", "case_type": "Misdemeanor", "status": "CLOSED", "charges": []}],
}, {"name": "TESTER, JOHN", "expanded": False, "cases": []}]

CASE = {
    "chargeHeads": ["", "Charge Seq #", "Description", "Date", "Phase", "Trial"],
    "charges": [
        {"cells": ["", "1", "MURDER IN THE SECOND DEGREE", "04/01/2026", "Prosecutor:Filed", ""], "detail": ""},
        {"cells": ["", "2", "AGGRAVATED BATTERY WITH DEADLY WEAPON", "04/01/2026", "Prosecutor:Filed", ""],
         "detail": "Offense Date: 03/15/2026 Arrest Date: 03/16/2026"},
    ],
    "docketHeads": ["Image", "Doc #", "Action Date", "Description", "Pages"],
    "docket": [
        {"cells": ["", "9", "03/18/2026", "ORDER APPOINTING PUBLIC DEFENDER", "1"], "href": "docketimage.pdf?query=a&pages=1"},
        {"cells": ["", "8", "03/18/2026", "APPLICATION FOR INDIGENT STATUS", "1"], "href": "docketimage.pdf?query=b&pages=1"},
        {"cells": ["", "6", "03/18/2026", "ARREST AFFIDAVIT AND NOTICE OF CONFIDENTIAL FILING", "10"],
         "href": "docketimage.pdf?query=c&pages=10"},
        {"cells": ["", "1", "03/18/2026", "Case 422026CF001234CFAXMX Filed with Clerk", ""], "href": None},
    ],
}


class AdapterStub(ClerkAdapter):
    def search(self, *a):
        return []

    def open_case(self, *a):
        return None

    def download(self, *a):
        return None


class ResultsTest(unittest.TestCase):
    def test_one_candidate_per_case(self):
        found = candidates_from_results(RESULTS)
        self.assertEqual([c.case_number for c in found], ["26CF001234AX", "25MM000777AX"])
        self.assertEqual(found[0].defendant, "TESTER, JANE MARIE")
        self.assertEqual(found[0].filed_date, date(2026, 3, 18))
        self.assertIsNone(found[0].dob)

    def test_local_number_or_ucn(self):
        self.assertEqual(case_number({"local": "[26CF001234AX]", "ucn": "x"}), "26CF001234AX")
        self.assertEqual(case_number({"local": "", "ucn": "422026CF001234CFAXMX"}), "422026CF001234CFAXMX")


class CasePageTest(unittest.TestCase):
    def test_charges_and_docket(self):
        charges = charges_from_case(CASE)
        self.assertEqual(charges[0].description, "MURDER IN THE SECOND DEGREE")
        self.assertIsNone(charges[0].offense_date)          # la fecha de la tabla es la de presentación
        self.assertEqual(charges[1].offense_date, date(2026, 3, 15))
        self.assertEqual(charges[1].arrest_date, date(2026, 3, 16))
        docket = docket_from_case(CASE)
        self.assertEqual(len(docket), 4)
        self.assertEqual(docket[2].date, date(2026, 3, 18))
        self.assertIsNone(docket[3].doc_ref)

    def test_real_detail_format(self):
        """Así viene el detalle al pulsar "Expand Charges" (fechas ficticias)."""
        detail = ("Date ActionStatute / TextOffense:03/15/2026Initial:03/15/2026Charged782.04.2 / "
                  "MURDER IN THE SECOND DEGREEProsecutor:04/01/2026Filed782.04.2 / MURDER IN THE SECOND DEGREE")
        data = {**CASE, "charges": [dict(CASE["charges"][0], detail=detail)]}
        charge = charges_from_case(data)[0]
        self.assertEqual(charge.offense_date, date(2026, 3, 15))
        self.assertIsNone(charge.arrest_date)

    def test_picks_arrest_affidavit(self):
        adapter = AdapterStub(None, get_county("marion", "FL"), None)
        candidate = candidates_from_results(RESULTS)[0]
        details = CaseDetails(candidate, charges_from_case(CASE), docket_from_case(CASE))
        docs = adapter.find_documents(details)
        self.assertEqual([d.description for d in docs], ["ARREST AFFIDAVIT AND NOTICE OF CONFIDENTIAL FILING"])


class ScoreWithoutChargeDatesTest(unittest.TestCase):
    """Civitek no muestra fechas de ofensa ni de arresto: cuenta la fecha del caso."""

    def details(self, filed: date) -> CaseDetails:
        candidate = candidates_from_results(RESULTS)[0]
        candidate.filed_date = filed
        no_dates = {**CASE, "charges": [dict(c, detail="") for c in CASE["charges"]]}
        return CaseDetails(candidate, charges_from_case(no_dates), docket_from_case(CASE))

    def query(self) -> LookupQuery:
        return LookupQuery(first_name="Jane", last_name="Tester", county="marion", state="FL",
                           incident_date=date(2026, 3, 15), age=33,
                           agency="Marion County Sheriff's Office",
                           charges=["second-degree murder", "aggravated battery with a deadly weapon"])

    def test_case_opened_days_after_scores_high(self):
        match = score_case(self.query(), self.details(date(2026, 3, 18)))
        self.assertGreaterEqual(match.score, 90)
        self.assertTrue(any("caso abierto 3" in r for r in match.reasons))

    def test_case_opened_much_later_scores_lower(self):
        near = score_case(self.query(), self.details(date(2026, 3, 18))).score
        far = score_case(self.query(), self.details(date(2026, 11, 1))).score
        self.assertLess(far, near)


if __name__ == "__main__":
    unittest.main()
