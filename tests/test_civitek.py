"""Civitek OCRS (Marion y otros 31 condados): lectura de resultados y del caso,
elección del documento y puntaje sin fechas en los cargos. Datos ficticios con
la misma forma que devuelve el portal (mapeado el 12/10/2026)."""

import unittest
from datetime import date

from adapters.civitek_ocrs import (candidates_from_results, case_number, charges_from_case,
                                   docket_from_case, is_consolidated)
from adapters.civitek_ocrs import CivitekOcrsAdapter
from core.matcher import pick_best, related_cases, resolve_same_incident, same_incident, score_case
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
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


class AdapterStub(CivitekOcrsAdapter):
    """El adaptador real sin navegador (solo se usan sus funciones de docket)."""
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


class DocumentChoiceTest(unittest.TestCase):
    """Qué documento se descarga cuando el docket trae varios (docket real de Marion, 12/10/2026)."""

    def docs(self, rows):
        data = {**CASE, "docket": [{"cells": ["", str(n), d, desc, "1"], "href": f"docketimage.pdf?query={n}"}
                                   for n, d, desc in rows]}
        adapter = AdapterStub(None, get_county("marion", "FL"), None)
        candidate = candidates_from_results(RESULTS)[0]
        return adapter.find_documents(CaseDetails(candidate, charges_from_case(CASE), docket_from_case(data)))

    def test_arrest_on_warrant_takes_warrant_served(self):
        """Así viene el docket (del más nuevo al más viejo) y con una fecha mal cargada (Doc #64)."""
        rows = [(165, "09/03/2026", "ANNOUNCEMENT OF NO INFORMATION FOR 26-CF-3428"),
                (19, "09/03/2026", "INFORMATION EFILED BY STATE"),
                (38, "08/26/2026", "ARREST AFFIDAVIT"),
                (143, "08/20/2026", "DISAGREEMENT ON NOTICE OF CONFIDENTIAL"),
                (152, "08/18/2026", "ARREST AFFIDAVIT AND NOTICE OF CONFIDENTIAL FILING"),
                (132, "08/17/2026", "ARREST AFFIDAVIT AND NOTICE OF CONFIDENTIAL FILING"),
                (118, "08/17/2026", "ARREST AFFIDAVIT"),
                (96, "08/17/2026", "ARREST AFFIDAVIT"),
                (8, "08/17/2026", "ARREST AFFIDAVIT"),
                (115, "08/14/2026", "BENCH WARRANT SERVED AND NOTICE OF CONFIDENTIAL FILING"),
                (93, "08/14/2026", "BENCH WARRANT SERVED AND NOTICE OF CONFIDENTIAL FILING"),
                (6, "08/14/2026", "BENCH WARRANT SERVED AND NOTICE OF CONFIDENTIAL FILING"),
                (64, "04/20/2026", "ARREST AFFIDAVIT")]
        docs = self.docs(rows)
        self.assertEqual(docs[0].number, 6)
        self.assertEqual([d.number for d in docs], [6, 8, 38, 64, 93, 96, 115, 118, 132, 152])

    def test_arrest_on_the_spot_takes_arrest_affidavit(self):
        """Sin orden de captura (como Amy Hogg): el arrest affidavit, aunque haya mociones que lo nombren."""
        rows = [(20, "10/01/2026", "MOTION TO COMPEL ARREST AFFIDAVIT"),
                (9, "09/23/2026", "ORDER APPOINTING PUBLIC DEFENDER"),
                (6, "09/22/2026", "ARREST AFFIDAVIT AND NOTICE OF CONFIDENTIAL FILING"),
                (1, "09/22/2026", "Case Filed with Clerk")]
        self.assertEqual([d.number for d in self.docs(rows)], [6])

    def test_warrant_not_served_is_not_a_document(self):
        rows = [(30, "11/02/2026", "BENCH WARRANT ISSUED FOR FAILURE TO APPEAR"),
                (5, "09/01/2026", "ARREST AFFIDAVIT")]
        self.assertEqual([d.number for d in self.docs(rows)], [5])

    def test_without_numbers_falls_back_to_type_and_date(self):
        entries = [DocketEntry(date(2026, 8, 26), "ARREST AFFIDAVIT", "a"),
                   DocketEntry(date(2026, 8, 17), "ARREST AFFIDAVIT", "b")]
        adapter = AdapterStub(None, get_county("marion", "FL"), None)
        candidate = candidates_from_results(RESULTS)[0]
        docs = adapter.find_documents(CaseDetails(candidate, [], entries))
        self.assertEqual([d.doc_ref for d in docs], ["b", "a"])


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


def spree_case(number: str, filed: date, offense: str, action: str, defendant: str = "TESTER, JOHN III") -> CaseDetails:
    """Caso con el formato real del detalle de cargos de Civitek (datos ficticios)."""
    detail = (f"Date ActionStatute / TextOffense:{offense}Initial:{offense}Charged812.014 / GRAND THEFT"
              f"Prosecutor:09/03/2026{action}812.014 / GRAND THEFT")
    data = {"chargeHeads": CASE["chargeHeads"],
            "charges": [{"cells": ["", "1", "GRAND THEFT", "", ""], "detail": detail}]}
    candidate = CaseCandidate(case_number=number, defendant=defendant, dob=None, filed_date=filed,
                              detail_ref="x", case_type="Felony", status="OPEN")
    return CaseDetails(candidate, charges_from_case(data), [], consolidated=is_consolidated(data))


class SameIncidentTest(unittest.TestCase):
    """Racha de hechos del mismo día abierta como varios casos (Marion, 12/10/2026)."""

    def query(self):
        return LookupQuery(first_name="John", last_name="Tester", county="marion", state="FL",
                           incident_date=date(2026, 8, 13), age=49, agency="Marion County Sheriff's Office")

    def test_consolidated_flag(self):
        self.assertTrue(spree_case("26CF000002AX", date(2026, 8, 14), "08/13/2026", "Consolidated").consolidated)
        self.assertFalse(spree_case("26CF000001AX", date(2026, 8, 14), "08/13/2026", "Filed").consolidated)
        self.assertFalse(is_consolidated(CASE))          # sin detalle: no se marca

    def test_picks_lead_case_and_lists_the_rest(self):
        # El principal se abrió DESPUÉS que uno de los consolidados: gana igual por ser el principal.
        cases = [spree_case("26CF000002AX", date(2026, 8, 13), "08/13/2026", "Consolidated"),
                 spree_case("26CF000001AX", date(2026, 8, 14), "08/13/2026", "Filed"),
                 spree_case("26CF000003AX", date(2026, 8, 18), "08/14/2026", "Consolidated")]
        scored = [(score_case(self.query(), d), d) for d in cases]
        self.assertEqual(pick_best(scored)[1], "ambiguous")
        winner = resolve_same_incident(scored, lambda d: True)[1]
        self.assertEqual(winner.candidate.case_number, "26CF000001AX")
        self.assertEqual(sorted(related_cases(winner, scored)), ["26CF000002AX", "26CF000003AX"])

    def test_not_merged(self):
        lead = spree_case("26CF000001AX", date(2026, 8, 14), "08/13/2026", "Filed")
        three_days = spree_case("26CF000009AX", date(2026, 8, 17), "08/16/2026", "Filed")
        other_person = spree_case("26CF000008AX", date(2026, 8, 14), "08/13/2026", "Filed", "TESTER, JOHN JR")
        self.assertFalse(same_incident(lead, three_days))
        self.assertFalse(same_incident(lead, other_person))

    def test_portals_with_arrest_data_keep_strict_rule(self):
        """Orange sí trae agencia y fecha de arresto: si no coinciden, no son el mismo arresto."""
        def orange(number, arrest):
            candidate = CaseCandidate(case_number=number, defendant="TESTER, JOHN", dob=None,
                                      filed_date=date(2026, 8, 14), detail_ref="x", case_type="CF", status="OPEN")
            return CaseDetails(candidate, [Charge(date(2026, 8, 13), "THEFT", None, arrest, "OPD", None)], [])
        self.assertFalse(same_incident(orange("A", date(2026, 8, 13)), orange("B", date(2026, 8, 20))))
        self.assertTrue(same_incident(orange("A", date(2026, 8, 13)), orange("B", date(2026, 8, 13))))


if __name__ == "__main__":
    unittest.main()
