"""Civitek OCRS (Marion y otros 31 condados): lectura de resultados y del caso,
elección del documento y puntaje sin fechas en los cargos. Datos ficticios con
la misma forma que devuelve el portal (mapeado el 12/10/2026)."""

import unittest
from datetime import date

from adapters.civitek_ocrs import (candidates_from_results, case_number, charges_from_case,
                                   docket_from_case, is_consolidated, wrong_county)
from adapters.civitek_ocrs import CivitekOcrsAdapter
from core.matcher import pick_best, prelim_score, related_cases, resolve_same_incident, same_incident, score_case
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


class CountyCheckTest(unittest.TestCase):
    def test_title_names_the_county(self):
        self.assertIsNone(wrong_county("Marion County OCRS", "Marion County"))
        self.assertIsNone(wrong_county("Sumter County OCRS", "Sumter County"))
        self.assertEqual(wrong_county("Suwannee County OCRS", "Sumter County"), "Suwannee")
        self.assertIsNone(wrong_county("Online Court Records Search", "Sumter County"))   # no se sabe: se sigue

    def test_hernando_config(self):
        hernando = get_county("hernando", "FL")
        self.assertEqual(hernando.base_url, "https://www.civitekflorida.com/ocrs/county/27/")
        self.assertEqual(hernando.name, "Hernando County")

    def test_sumter_config(self):
        sumter = get_county("sumter", "FL")
        self.assertEqual(sumter.base_url, "https://www.civitekflorida.com/ocrs/county/60/")
        self.assertEqual(sumter.platform, "civitek_ocrs")


class ResultsTest(unittest.TestCase):
    def test_one_candidate_per_case(self):
        found = candidates_from_results(RESULTS)
        self.assertEqual([c.case_number for c in found], ["26CF001234AX", "25MM000777AX"])
        self.assertEqual(found[0].defendant, "TESTER, JANE MARIE")
        self.assertEqual(found[0].filed_date, date(2026, 3, 18))
        self.assertIsNone(found[0].dob)

    def test_defendant_from_the_case_parties(self):
        """Una persona con alias: los casos quedan bajo la primera fila, y el nombre
        del acusado sale del propio caso (DEFENDANT), no de la fila (12/10/2026)."""
        people = [{"name": "TESTER-SMITH, JANE", "alias_of": None, "expanded": True,
                   "cases": [dict(RESULTS[0]["cases"][0], defendant="TESTER SMITH, JANE")]},
                  {"name": "SMITH, JANE", "alias_of": "TESTER-SMITH, JANE", "expanded": False, "cases": []}]
        found = candidates_from_results(people)
        self.assertEqual([(c.case_number, c.defendant) for c in found], [("26CF001234AX", "TESTER SMITH, JANE")])
        # Sin DEFENDANT en el caso, se usa el nombre de la fila.
        self.assertEqual(candidates_from_results(RESULTS)[0].defendant, "TESTER, JANE MARIE")

    def test_local_number_or_ucn(self):
        self.assertEqual(case_number({"local": "[26CF001234AX]", "ucn": "x"}), "26CF001234AX")
        self.assertEqual(case_number({"local": "", "ucn": "422026CF001234CFAXMX"}), "422026CF001234CFAXMX")
        # Sumter usa el año con 4 dígitos: no se recorta.
        self.assertEqual(case_number({"local": "[ 2026CF000808AXMX ]", "ucn": "x"}), "2026CF000808AXMX")


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


# Dockets reales de Marion (12/10/2026): número, fecha y título tal como los muestra el portal,
# del más nuevo al más viejo. Sin datos personales.
PARKS_DOCKET = [(165, "09/03/2026", "ANNOUNCEMENT OF NO INFORMATION FOR 26-CF-3428"),
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
                (64, "04/20/2026", "ARREST AFFIDAVIT")]          # fecha mal cargada en el portal
GARCIA_DOCKET = [(13, "06/19/2026", "FIRST APPEARANCE ORDER"),
                 (12, "06/19/2026", "ARREST AFFIDAVIT"),
                 (10, "06/18/2026", "CAPIAS SERVED-INFORMATION/INDICTMENT AND NOTICE OF CONFIDENTIAL FILING"),
                 (7, "06/18/2026", "APPLICATION AND ORDER FOR BAIL"),
                 (6, "06/18/2026", "INFORMATION FILED BY STATE")]
HOGG_DOCKET = [(20, "10/01/2026", "MOTION TO COMPEL ARREST AFFIDAVIT"),
               (9, "09/23/2026", "ORDER APPOINTING PUBLIC DEFENDER"),
               (6, "09/22/2026", "ARREST AFFIDAVIT AND NOTICE OF CONFIDENTIAL FILING"),
               (1, "09/22/2026", "Case Filed with Clerk")]


def details_with_docket(rows) -> CaseDetails:
    data = {**CASE, "docket": [{"cells": ["", str(n), d, desc, "1"], "href": f"docketimage.pdf?query={n}"}
                               for n, d, desc in rows]}
    return CaseDetails(candidates_from_results(RESULTS)[0], charges_from_case(CASE), docket_from_case(data))


class DocumentChoiceTest(unittest.TestCase):
    """Principal: el arrest affidavit que abrió el caso. Apoyo: la orden de captura ejecutada."""

    def choose(self, rows):
        adapter = AdapterStub(None, get_county("marion", "FL"), None)
        details = details_with_docket(rows)
        return ([d.number for d in adapter.find_documents(details)],
                [d.number for d in adapter.find_support_documents(details)])

    def test_arrest_on_warrant(self):
        """Leroy Parks III: #8 es el formulario de la captura; #6 trae la orden y la narración."""
        main, support = self.choose(PARKS_DOCKET)
        self.assertEqual(main, [8, 38, 64, 96, 118, 132, 152])      # #64 en su lugar pese a la fecha
        self.assertEqual(support, [6, 93, 115])

    def test_arrest_on_capias(self):
        """García-Luis: #12 la captura; #10 el capias con el número de la agencia del hecho."""
        self.assertEqual(self.choose(GARCIA_DOCKET), ([12], [10]))

    def test_arrest_on_the_spot(self):
        """Amy Hogg: el arrest affidavit, sin documento de apoyo; la moción no cuenta."""
        self.assertEqual(self.choose(HOGG_DOCKET), ([6], []))

    def test_warrant_not_served_is_not_a_document(self):
        rows = [(30, "11/02/2026", "BENCH WARRANT ISSUED FOR FAILURE TO APPEAR"),
                (5, "09/01/2026", "ARREST AFFIDAVIT")]
        self.assertEqual(self.choose(rows), ([5], []))

    def test_without_numbers_falls_back_to_type_and_date(self):
        entries = [DocketEntry(date(2026, 8, 26), "ARREST AFFIDAVIT", "a"),
                   DocketEntry(date(2026, 8, 17), "ARREST AFFIDAVIT", "b")]
        adapter = AdapterStub(None, get_county("marion", "FL"), None)
        candidate = candidates_from_results(RESULTS)[0]
        docs = adapter.find_documents(CaseDetails(candidate, [], entries))
        self.assertEqual([d.doc_ref for d in docs], ["b", "a"])

    def test_orange_has_no_support_documents(self):
        self.assertEqual(get_county("orange", "FL").support_documents, [])


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


# Brandy Almany (Sumter, 2026CF000803AXMX): el DEFENDANT quedó escrito
# "ALAMANY THOMAS"; "ALMANY" solo aparece como ALSO KNOWN AS y en la fila.
ALMANY_PEOPLE = [{"name": "ALMANY, BRANDY", "alias_of": None, "expanded": True, "cases": [{
    "ucn": "602026CF000803CFAXMX", "local": "[2026CF000803AXMX]", "href": "caseinformation.xhtml?query=x",
    "defendant": "ALAMANY THOMAS, BRANDY RAECHEL",
    "aka": ["ALMANY, BRANDY RAECHELLE", "ALMANY, BRANDY", "ALMANY THOMAS, BRANDY RAECHELL"],
    "file_date": "07/23/2026", "case_type": "Felony 22-D", "status": "CASE OPEN", "charges": []}]}]

ALMANY_DOCKET = [(17, "10/05/2026", "STATES DISCOVERY EXHIBIT"),
                 (14, "08/13/2026", "INFORMATION FILED"),
                 (8, "07/24/2026", "APPLICATION OF CRIMINAL INDIGENT STATUS"),
                 (7, "07/24/2026", "SWORN ARREST COMPLAINT AFFIDAVIT FILED"),
                 (6, "07/24/2026", "WARRANT TO ARREST RETURN EXECUTED"),
                 (5, "07/24/2026", "SHERIFF WARRANT REMOVAL FORM"),
                 (3, "07/23/2026", "WARRANT AFFIDAVIT")]


class AliasNameTest(unittest.TestCase):
    def query(self, last="Almany") -> LookupQuery:
        return LookupQuery(first_name="Brandy", last_name=last, county="sumter", state="FL",
                           incident_date=date(2026, 7, 23))

    def test_aliases_are_kept(self):
        candidate = candidates_from_results(ALMANY_PEOPLE)[0]
        self.assertEqual(candidate.defendant, "ALAMANY THOMAS, BRANDY RAECHEL")
        self.assertEqual(candidate.aliases, ["ALMANY, BRANDY RAECHELLE", "ALMANY, BRANDY",
                                             "ALMANY THOMAS, BRANDY RAECHELL"])

    def test_misspelled_defendant_matches_by_alias(self):
        candidate = candidates_from_results(ALMANY_PEOPLE)[0]
        self.assertGreater(prelim_score(self.query(), candidate), 0)
        match = score_case(self.query(), CaseDetails(candidate, [], []))
        self.assertFalse(match.disqualified)
        self.assertTrue(any("alias" in r for r in match.reasons))

    def test_case_where_she_is_the_officer_is_discarded(self):
        """Las filas "ALMANY, BRANDY" sin sexo ni dirección: casos donde ella es la
        agente. El acusado es otro y no hay alias suyos: no se abre."""
        people = [{"name": "ALMANY, BRANDY", "alias_of": None, "expanded": True, "cases": [{
            "ucn": "602026CT000111CTAXMX", "local": "[2026CT000111AXMX]", "href": "x",
            "defendant": "DOE, JOHN", "aka": [], "file_date": "07/25/2026",
            "case_type": "Criminal Traffic", "status": "CLOSED", "charges": []}]}]
        candidate = candidates_from_results(people)[0]
        self.assertEqual(candidate.aliases, [])
        self.assertEqual(prelim_score(self.query(), candidate), 0)

    def test_other_surname_still_discarded(self):
        candidate = candidates_from_results(ALMANY_PEOPLE)[0]
        self.assertEqual(prelim_score(self.query("Johnson"), candidate), 0)

    def test_main_name_wins_over_alias(self):
        """Si el DEFENDANT coincide, no se mira ningún alias."""
        candidate = candidates_from_results(RESULTS)[0]
        candidate.aliases = ["TESTER, JANE"]
        q = LookupQuery(first_name="Jane", last_name="Tester", middle_name="Marie",
                        county="marion", state="FL", incident_date=date(2026, 3, 15))
        match = score_case(q, CaseDetails(candidate, [], []))
        self.assertFalse(any("alias" in r for r in match.reasons))
        self.assertIn("segundo nombre coincide", match.reasons)

    def test_warrant_affidavit_is_support(self):
        """Principal: #7 (sworn arrest complaint). Apoyo: #3 (warrant affidavit, con la narración)."""
        adapter = AdapterStub(None, get_county("sumter", "FL"), None)
        details = details_with_docket(ALMANY_DOCKET)
        self.assertEqual([d.number for d in adapter.find_documents(details)], [7])
        self.assertEqual([d.number for d in adapter.find_support_documents(details)], [3])



class SearchDoneTest(unittest.TestCase):
    def test_page_without_body_is_not_ready(self):
        """Entre páginas document.body es null: no debe lanzar error."""
        from adapters.civitek_ocrs import _SEARCH_DONE_JS
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright no instalado")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content("<p>loading</p>")
            page.evaluate("document.documentElement.removeChild(document.body)")
            self.assertFalse(page.evaluate(_SEARCH_DONE_JS))
            page.set_content("<p>No matches found</p>")
            self.assertTrue(page.evaluate(_SEARCH_DONE_JS))
            browser.close()

if __name__ == "__main__":
    unittest.main()
