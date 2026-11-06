"""Ranglin (Bay, 06/11/2026): dos "CASE FILED" en el mismo caso.

    24002615CFMA  principal; cargos del 01/08 (pistola en el restaurante) y del 06/08 (asesinato)
        #1   CASE FILED 08/07/2024 CASE NUMBER 24002615CFMA  -> pedido de orden por lo del 01/08
        #40  CASE FILED 08/09/2024 CASE NUMBER 24002626CFMC  -> affidavit del asesinato (reporte 2435098)
    24002626CFMC  consolidado en el 2615; todos los cargos del 06/08

La nota es del asesinato del 06/08: se baja la #40 y el caso informado sigue siendo el 2615.
"""

import unittest
from datetime import date

from core.lookup import order_by_named_case
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery

PCB = "PANAMA CITY BEACH POLICE"
ARREST = date(2024, 8, 9)
MURDER, GUN = date(2024, 8, 6), date(2024, 8, 1)


def case(number, offenses, filed):
    candidate = CaseCandidate(number, "RANGLIN, ANDRE JERMAINE", "CF", "CLOSED", date(2001, 1, 26), filed, "x")
    charges = [Charge(o, "CHARGE", None, ARREST, PCB, "2435098", initial_date=ARREST) for o in offenses]
    return CaseDetails(candidate, charges, [])


C2615 = case("24002615CFMA", [MURDER] * 8 + [GUN] * 2, date(2024, 8, 7))
C2626 = case("24002626CFMC", [MURDER] * 9, date(2024, 8, 9))
FIRST = DocketEntry(date(2024, 8, 7), "CASE FILED 08/07/2024 CASE NUMBER 24002615CFMA", "a|1", 1)
MERGED = DocketEntry(date(2024, 8, 8), "CASE FILED 08/09/2024 CASE NUMBER 24002626CFMC", "b|2", 40)
SCORED = [(None, C2626), (None, C2615)]


def query(**kw):
    base = dict(first_name="Andre", last_name="Ranglin", county="bay", incident_date=MURDER,
                arrest_date=date(2024, 8, 7), agency="Panama City Beach Police Department")
    base.update(kw)
    return LookupQuery(**base)


class RanglinTest(unittest.TestCase):
    def test_filing_of_the_case_with_the_story_incident_goes_first(self):
        self.assertEqual(order_by_named_case([FIRST, MERGED], query(), SCORED), [MERGED, FIRST])

    def test_story_about_the_other_incident(self):
        """Una nota de lo del 01/08 se queda con la #1 (el 2626 no tiene cargos de ese día)."""
        self.assertEqual(order_by_named_case([FIRST, MERGED], query(incident_date=GUN, arrest_date=None),
                                             SCORED), [FIRST, MERGED])

    def test_without_story_date_keeps_the_order(self):
        q = query(incident_date=None, arrest_date=None)
        self.assertEqual(order_by_named_case([FIRST, MERGED], q, SCORED), [FIRST, MERGED])

    def test_named_case_not_in_the_search_keeps_the_order(self):
        self.assertEqual(order_by_named_case([FIRST, MERGED], query(), [(None, C2615)]), [FIRST, MERGED])

    def test_documents_without_case_number_keep_the_order(self):
        report = DocketEntry(date(2024, 8, 9), "ARREST REPORT", "c|3", 41)
        self.assertEqual(order_by_named_case([FIRST, report], query(), SCORED), [FIRST, report])
        self.assertEqual(order_by_named_case([FIRST], query(), SCORED), [FIRST])


if __name__ == "__main__":
    unittest.main()
