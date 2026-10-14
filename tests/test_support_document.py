"""Búsqueda completa con documento de apoyo (orden de captura ejecutada), sin navegador."""

import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import lookup
from core.adapter import DocumentUnavailable, DownloadError
from core.models import LookupQuery
from tests.test_civitek import GARCIA_DOCKET, HOGG_DOCKET, AdapterStub, candidates_from_results, RESULTS, \
    details_with_docket


def run(rows, fail_on=None, query_date=date(2026, 3, 15), withheld=()):
    """Corre run_lookup con un adaptador de Civitek falso que encuentra un caso con ese docket."""
    downloaded = []

    class FakeCivitek(AdapterStub):
        def search(self, query, date_from, date_to):
            return candidates_from_results(RESULTS)[:1]

        def open_case(self, candidate):
            details = details_with_docket(rows)
            for c in details.charges:
                c.offense_date = query_date
            return details

        def download(self, details, entry, dest_dir):
            if fail_on and fail_on in entry.description:
                raise DownloadError("portal caído")
            if entry.number in withheld:
                raise DocumentUnavailable(f"not released: {entry.number}")
            downloaded.append(entry.number)
            path = Path(dest_dir) / f"{entry.number}.pdf"
            path.write_bytes(b"%PDF-1.4 test")
            return path

        def throttle(self):
            pass

    @contextmanager
    def fake_browser(headless=False):
        yield SimpleNamespace(page=None, remote=False, live_view_url=None)

    query = LookupQuery(first_name="Jane", last_name="Tester", county="marion", state="FL",
                        incident_date=query_date, agency="Marion County Sheriff's Office")
    with mock.patch.object(lookup, "open_browser", fake_browser), \
         mock.patch.object(lookup, "get_adapter", return_value=FakeCivitek), \
         tempfile.TemporaryDirectory() as tmp:
        result = lookup.run_lookup(query, Path(tmp), captcha=object())
        files = (result.pdf_path and Path(result.pdf_path).exists(),
                 result.support_pdf_path and Path(result.support_pdf_path).exists())
    return result, downloaded, files


class SupportDocumentTest(unittest.TestCase):
    def test_capias_comes_with_the_arrest_affidavit(self):
        result, downloaded, files = run(GARCIA_DOCKET)
        self.assertEqual(result.status, "found")
        self.assertEqual(downloaded, [12, 10])
        self.assertEqual(result.document, "ARREST AFFIDAVIT")
        self.assertTrue(result.support_document.startswith("CAPIAS SERVED"))
        self.assertEqual(files, (True, True))

    def test_no_warrant_means_one_document(self):
        result, downloaded, _ = run(HOGG_DOCKET)
        self.assertEqual((result.status, downloaded), ("found", [6]))
        self.assertIsNone(result.support_document)
        self.assertIsNone(result.support_pdf_path)

    def test_support_download_failure_keeps_the_result(self):
        result, downloaded, _ = run(GARCIA_DOCKET, fail_on="CAPIAS")
        self.assertEqual((result.status, downloaded), ("found", [12]))
        self.assertIsNone(result.support_document)

    def test_only_warrant_is_delivered_as_main(self):
        rows = [(10, "06/18/2026", "CAPIAS SERVED-INFORMATION/INDICTMENT AND NOTICE OF CONFIDENTIAL FILING"),
                (6, "06/18/2026", "INFORMATION FILED BY STATE")]
        result, downloaded, _ = run(rows)
        self.assertEqual((result.status, downloaded), ("found", [10]))
        self.assertTrue(result.document.startswith("CAPIAS SERVED"))
        self.assertIsNone(result.support_document)



class WithheldDocumentTest(unittest.TestCase):
    """Sumter (14/10/2026): el portal lista el documento pero responde
    "not available at this time" (caída temporal o aún sin publicar)."""

    TWO_AFFIDAVITS = [(14, "08/26/2026", "ARREST AFFIDAVIT"),
                      (2, "07/24/2026", "SWORN ARREST COMPLAINT AFFIDAVIT FILED"),
                      (1, "07/24/2026", "Case Filed with Clerk")]

    def test_next_document_is_tried(self):
        result, downloaded, _ = run(self.TWO_AFFIDAVITS, withheld={2})
        self.assertEqual((result.status, downloaded, result.document), ("found", [14], "ARREST AFFIDAVIT"))

    def test_nothing_released_is_no_document(self):
        result, downloaded, _ = run(self.TWO_AFFIDAVITS, withheld={2, 14})
        self.assertEqual((result.status, downloaded), ("no_document", []))
        self.assertEqual(result.case_number, "26CF001234AX")
        self.assertIn("right now", result.message)
        self.assertIn("temporary", result.message)

    def test_warrant_used_when_affidavit_withheld(self):
        result, downloaded, _ = run(GARCIA_DOCKET, withheld={12})
        self.assertEqual((result.status, downloaded), ("found", [10]))
        self.assertTrue(result.document.startswith("CAPIAS SERVED"))
        self.assertIsNone(result.support_document)


if __name__ == "__main__":
    unittest.main()
