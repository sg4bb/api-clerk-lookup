"""Gulf (24/10/2026): "Dockets are currently unavailable" en todos los casos.

El caso se puntúa con lo que sí se ve (nombre, cargos, fechas) y, si es el bueno,
sale como no_document con el número de caso, en vez de un error tras 25 s.
"""

import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

from core import lookup
from core.models import CaseCandidate, LookupQuery
from tests.test_civitek import AdapterStub
from tests.test_people_and_closest import case

OUTAGE_PAGE = """<title>Gulf County OCRS</title>
<p>Dockets are currently unavailable. Please try again later.</p>
<table><tr><td>232026CF000172CFAXMX</td></tr></table>"""


class AdapterOutageTest(unittest.TestCase):
    def test_outage_page_is_read_without_waiting(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright no instalado")
        from adapters.civitek_ocrs import CivitekOcrsAdapter
        from counties import get_county
        candidate = CaseCandidate("26000172CFAXMX", "KOEPKE, KURTIS CONAN", "Felony", "OPEN", None,
                                  date(2026, 9, 15), "data:text/html," + quote(OUTAGE_PAGE))
        with sync_playwright() as p:
            browser = p.chromium.launch()
            adapter = CivitekOcrsAdapter(browser.new_context().new_page(), get_county("gulf", "FL"), None)
            started = time.monotonic()
            details = adapter.open_case(candidate)
            elapsed = time.monotonic() - started
            browser.close()
        self.assertTrue(details.docket_unavailable)
        self.assertEqual(details.docket, [])
        self.assertLess(elapsed, 10)          # antes: 25 s de espera y error


def run(details, q):
    class FakeCivitek(AdapterStub):
        def search(self, query, date_from, date_to):
            return [details.candidate]

        def open_case(self, candidate):
            return details

        def throttle(self):
            pass

    @contextmanager
    def fake_browser(headless=False):
        yield SimpleNamespace(page=None, remote=False, live_view_url=None)

    with mock.patch.object(lookup, "open_browser", fake_browser), \
         mock.patch.object(lookup, "get_adapter", return_value=FakeCivitek), \
         tempfile.TemporaryDirectory() as tmp:
        return lookup.run_lookup(q, Path(tmp), captcha=object())


class LookupOutageTest(unittest.TestCase):
    def test_case_found_without_docket(self):
        details = case("26000172CFAXMX", "KOEPKE, KURTIS CONAN", date(2026, 9, 11),
                       "POSSESS 10 OR MORE CHILD PORN IMAGE F2 OFFENSE", date(2026, 9, 15))
        details.docket_unavailable = True
        q = LookupQuery(first_name="Kurtis", last_name="Koepke", county="gulf", state="FL",
                        incident_date=date(2026, 9, 11), charges=["possession of child sexual abuse material"])
        result = run(details, q)
        self.assertEqual(result.status, "no_document")
        self.assertEqual(result.case_number, "26000172CFAXMX")
        self.assertIn("Dockets are currently unavailable", result.message)
        self.assertIn("try again later", result.message)
        self.assertIn("arrest affidavit", result.message)


if __name__ == "__main__":
    unittest.main()
