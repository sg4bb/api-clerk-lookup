"""Lo que el worker guarda en el pedido al terminar: PDF principal y de apoyo."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import worker
from core.jobs import JobsError
from core.models import LookupResult
from core.pipeline import PipelineResult


class FakeStore:
    def __init__(self, fail_on=None):
        self.fail_on, self.updates, self.uploaded = fail_on, {}, []

    def update(self, job_id, **fields):
        self.updates.update(fields)

    def upload_pdf(self, job_id, pdf):
        if self.fail_on and self.fail_on in pdf.name:
            raise JobsError("storage caído")
        self.uploaded.append(pdf.name)
        return f"{job_id}/{pdf.name}"


def finish(tmp: Path, store: FakeStore, with_support=True):
    main = tmp / "26CF002446AX - ARREST AFFIDAVIT.pdf"
    main.write_bytes(b"%PDF")
    support = tmp / "26CF002446AX - CAPIAS SERVED.pdf"
    support.write_bytes(b"%PDF")
    lookup = LookupResult("found", case_number="26CF002446AX", document="ARREST AFFIDAVIT", pdf_path=main,
                          score=86, support_document="CAPIAS SERVED" if with_support else None,
                          support_pdf_path=support if with_support else None)
    with mock.patch.object(worker, "run_article", return_value=PipelineResult(status="found", lookup=lookup)):
        status = worker.handle(store, {"id": "job1", "article_url": "https://example.com/a"}, tmp)
    return status, store.updates


class WorkerUploadTest(unittest.TestCase):
    def test_both_documents_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FakeStore()
            status, fields = finish(Path(tmp), store)
        self.assertEqual(status, "found")
        self.assertEqual(fields["pdf_path"], "job1/26CF002446AX - ARREST AFFIDAVIT.pdf")
        self.assertEqual(fields["support_pdf_path"], "job1/26CF002446AX - CAPIAS SERVED.pdf")
        self.assertEqual(fields["support_document_name"], "CAPIAS SERVED")

    def test_support_upload_failure_keeps_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, fields = finish(Path(tmp), FakeStore(fail_on="CAPIAS"))
        self.assertEqual(status, "found")
        self.assertIn("pdf_path", fields)
        self.assertNotIn("support_pdf_path", fields)

    def test_without_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FakeStore()
            status, fields = finish(Path(tmp), store, with_support=False)
        self.assertEqual((status, store.uploaded), ("found", ["26CF002446AX - ARREST AFFIDAVIT.pdf"]))
        self.assertNotIn("support_pdf_path", fields)


if __name__ == "__main__":
    unittest.main()
