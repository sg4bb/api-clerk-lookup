"""Búsqueda sin fechas como último recurso (Patel, Madison, 23/10/2026).

Solo cuando nadie dio caso en el rango de fechas y no hay "caso más cercano"; lo que
encuentre va como sugerencia (closest con wide=True) y el resultado sigue en not_found.
"""

import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import lookup
from core.models import LookupQuery
from tests.test_civitek import AdapterStub
from tests.test_people_and_closest import case

PUBLISHED = date(2026, 3, 24)


def query(first="Jigar", last="Patel", charges=("armed burglary",), **kw) -> LookupQuery:
    return LookupQuery(first_name=first, last_name=last, county="madison", state="FL",
                       charges=list(charges), published_date=PUBLISHED, approx_date=PUBLISHED, **kw)


OLD_BURGLARY = case("2025000200CFAXMX", "PATEL, JIGAR VISHNUBHAI", date(2025, 9, 30), "BURGLARY WHILE ARMED",
                    date(2025, 10, 7))
OLD_TRAFFIC = case("2017005169TRAXMX", "PATEL, JIGAR", date(2017, 5, 31), "UNLAWFUL SPEED", date(2017, 5, 31))
SIMILAR_NAME = case("2024000001CFAXMX", "PATEL, JIGARKUMAR J", date(2024, 1, 1), "BURGLARY", date(2024, 1, 2))
LATER = case("2026000999CFAXMX", "PATEL, JIGAR", date(2026, 9, 1), "BURGLARY WHILE ARMED", date(2026, 9, 2))
FOUND = case("2026000100CFAXMX", "PATEL, JIGAR", date(2026, 3, 20), "BURGLARY WHILE ARMED", date(2026, 3, 21),
             docket=[(2, "ARREST AFFIDAVIT")])


def run(people, window, wide, wide_error=False):
    """`window`: lo que el portal da con fechas; `wide`: lo que da sin fechas."""
    calls = []
    every = window + wide

    class FakeCivitek(AdapterStub):
        def search(self, q, date_from, date_to):
            calls.append("window" if date_from else "wide")
            if not date_from and wide_error:
                raise RuntimeError("portal caído")
            return [d.candidate for d in (window if date_from else wide)]

        def open_case(self, candidate):
            return next(d for d in every if d.candidate is candidate)

        def download(self, details, entry, dest_dir):
            path = Path(dest_dir) / f"{entry.number}.pdf"
            path.write_bytes(b"%PDF-1.4 test")
            return path

        def throttle(self):
            pass

    @contextmanager
    def fake_browser(headless=False):
        yield SimpleNamespace(page=None, remote=False, live_view_url=None)

    with mock.patch.object(lookup, "open_browser", fake_browser), \
         mock.patch.object(lookup, "get_adapter", return_value=FakeCivitek), \
         tempfile.TemporaryDirectory() as tmp:
        result = lookup.run_lookup(people, Path(tmp), captcha=object())
    return result, calls


class WideSearchTest(unittest.TestCase):
    def test_suggestion_without_date_limits(self):
        result, calls = run([query()], window=[], wide=[OLD_TRAFFIC, OLD_BURGLARY])
        self.assertEqual(result.status, "not_found")          # nunca "found"
        self.assertEqual(calls, ["window", "wide"])
        self.assertEqual(result.closest["case_number"], "2025000200CFAXMX")
        self.assertTrue(result.closest["wide"])
        self.assertEqual(result.closest["filed_date"], "2025-10-07")

    def test_found_in_window_skips_it(self):
        result, calls = run([query(incident_date=date(2026, 3, 20))], window=[FOUND], wide=[OLD_BURGLARY])
        self.assertEqual(result.status, "found")
        self.assertEqual(calls, ["window"])
        self.assertIsNone(result.closest)

    def test_no_charges_no_search(self):
        result, calls = run([query(charges=())], window=[], wide=[OLD_BURGLARY])
        self.assertEqual(calls, ["window"])
        self.assertIsNone(result.closest)

    def test_different_charges_not_suggested(self):
        result, _ = run([query()], window=[], wide=[OLD_TRAFFIC])
        self.assertIsNone(result.closest)

    def test_similar_name_not_suggested(self):
        result, _ = run([query()], window=[], wide=[SIMILAR_NAME])
        self.assertIsNone(result.closest)

    def test_case_opened_long_after_the_story_skipped(self):
        result, _ = run([query()], window=[], wide=[LATER])
        self.assertIsNone(result.closest)

    def test_failure_keeps_not_found(self):
        result, calls = run([query()], window=[], wide=[], wide_error=True)
        self.assertEqual((result.status, calls), ("not_found", ["window", "wide"]))
        self.assertIsNone(result.closest)

    def test_patel_prior_nicotine_case_is_suggested(self):
        """La nota (marzo de 2026) no da fecha; el portal solo tiene casos de 2025 por lo mismo.
        Se sugiere el más cercano a la nota, como sugerencia y con su fecha a la vista."""
        mm4 = case("2025000004MMAXMX", "PATEL, JIGAR VISHNUBHAI", date(2025, 1, 10),
                   "SALE NICOTINE DEVICE TO PERSONS UNDER 21YOA", date(2025, 1, 16))
        mm200 = case("2025000200MMAXMX", "PATEL, JIGAR VISHNUBHAI", date(2025, 10, 1),
                     "SALE NICOTINE DEVICE TO PERSONS UNDER 21YOA", date(2025, 10, 7))
        people = [query(charges=["unlawful sale of tobacco to a minor"]),
                  query("Jaya", charges=["unlawful sale of tobacco to a minor"])]
        result, _ = run(people, window=[], wide=[mm4, mm200])
        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.closest["case_number"], "2025000200MMAXMX")
        self.assertTrue(result.closest["wide"])

    def test_each_person_until_a_suggestion(self):
        people = [query("Jaya", "Patel"), query()]
        result, calls = run(people, window=[], wide=[OLD_BURGLARY])
        # Jaya no tiene casos con su nombre; Jigar sí.
        self.assertEqual(calls, ["window", "window", "wide", "wide"])
        self.assertEqual(result.closest["case_number"], "2025000200CFAXMX")


if __name__ == "__main__":
    unittest.main()
