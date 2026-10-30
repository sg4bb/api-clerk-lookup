"""DeSoto (30/10/2026): Callahan sin fechas y la fecha falsa de Facebook de Lopez."""

import unittest
from datetime import date

from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
from extractor.fetch import facebook_filler_date
from tests.test_people_and_closest import run

FB = "https://www.facebook.com/dcsoflsheriff/posts/1"


def callahan(number, filed, offense, initial, description, docket=()):
    candidate = CaseCandidate(case_number=number, defendant="CALLAHAN, KEVIN", case_type="Felony",
                              status="CLOSED", dob=None, filed_date=filed, detail_ref="x")
    charges = [Charge(offense_date=offense, description=description, statute=None, arrest_date=None,
                      agency=None, control_number=None, initial_date=initial)]
    entries = [DocketEntry(date=filed, description=d, doc_ref=f"doc{n}", number=n) for n, d in docket]
    return CaseDetails(candidate, charges, entries)


# 12 casos viejos (como los de 2005-2009 que salieron primero en el portal) y los 3 de 2025.
OLD = [callahan(f"{2005 + n // 3}CF00{n:04d}AX", date(2005 + n // 3, 3, 1 + n), date(2005 + n // 3, 2, 1 + n),
                date(2005 + n // 3, 2, 2 + n), "BATTERY") for n in range(12)]
ARREST = date(2025, 1, 10)
RECENT = [callahan("2025CF000012AXMA", date(2025, 1, 13), date(2024, 12, 5), ARREST, "TRAFFICKING METHAMPHETAMINE"),
          callahan("2025CF000013AXMA", date(2025, 1, 13), ARREST, ARREST, "TRAFFICKING METHAMPHETAMINE",
                   docket=[(1, "ARREST AFFIDAVIT")]),
          callahan("2025CF000014AXMA", date(2025, 1, 13), date(2024, 12, 19), ARREST, "TRAFFICKING METHAMPHETAMINE")]


class DatelessSearchTest(unittest.TestCase):
    def test_recent_cases_are_opened_first(self):
        """Sin fechas se abrían los más viejos (orden del portal) y los de 2025 nunca."""
        q = LookupQuery(first_name="Kevin", last_name="Callahan", county="desoto", state="FL",
                        agency="DeSoto County Sheriff's Office", charges=["trafficking methamphetamine"])
        result, _ = run([q], {"Callahan": OLD + RECENT})
        self.assertIn(result.status, ("found", "no_document"), result)
        self.assertIn(result.case_number, {d.candidate.case_number for d in RECENT})


class FacebookDateTest(unittest.TestCase):
    LOPEZ = ("Today, 23-year-old Carlos Lopez was sentenced to life in prison for the attempted murder "
             "of a teenager back in 2023.")

    def test_date_before_a_year_the_post_mentions(self):
        """Lopez: Facebook dio 2016-01-07 y el post habla de 2023."""
        self.assertTrue(facebook_filler_date(FB, date(2016, 1, 7), self.LOPEZ))

    def test_real_dates_stay(self):
        self.assertFalse(facebook_filler_date(FB, date(2026, 10, 29), self.LOPEZ))
        # Sin años en el texto no hay cómo saberlo.
        self.assertFalse(facebook_filler_date(FB, date(2016, 1, 7), "Today, Carlos Lopez was sentenced."))
        # Un año futuro (un plan) no prueba nada; fuera de Facebook tampoco aplica.
        self.assertFalse(facebook_filler_date(FB, date(2026, 3, 4), "The new jail opens in 2099."))
        self.assertFalse(facebook_filler_date("https://www.wjhg.com/x", date(2016, 1, 7), self.LOPEZ))


if __name__ == "__main__":
    unittest.main()
