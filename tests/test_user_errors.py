"""Lo que ve el usuario cuando la nota no sirve: cada caso termina con su propio
estado (no como un 'error' técnico) y un mensaje claro. Sin red ni IA."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import extractor
from core import pipeline
from extractor import NoSuspect, OutOfScope, build_query
from extractor import fetch as fetch_mod
from extractor.fetch import Article, FetchError
from extractor.llm import ExtractionError
from extractor.schema import Extraction

VAGUE = Article(url="https://example.com/vague", title="Man arrested after January break-in",
                text=("A man was arrested in January 2023 after a break-in at a business in Orlando, "
                      "according to police. Police did not release the man's name. ") * 4,
                published=date(2023, 2, 2), via="direct")


def extraction(**kw):
    base = dict(people=[], incident_date=None, city="Orlando", state="FL", county="Orange",
                agency="Orlando Police Department", charges=[])
    base.update(kw)
    return Extraction.model_validate(base)


class ArticleContentTest(unittest.TestCase):
    def test_article_without_names(self):
        """'Un hombre fue arrestado en enero de 2023': no hay a quién buscar."""
        with self.assertRaises(NoSuspect) as ctx:
            build_query(extraction(), VAGUE)
        self.assertIn("only be searched by name", str(ctx.exception))

    def test_name_the_model_made_up(self):
        """Si la IA inventa un nombre que no está en la nota, se descarta: mismo resultado."""
        made_up = dict(first_name="John", last_name="Carter", role="arrested", evidence="-", age=30)
        with self.assertRaises(NoSuspect):
            build_query(extraction(people=[made_up]), VAGUE)

    def test_unknown_place(self):
        article = Article(url="u", title="Arrest", text="Police arrested Mark Diaz on Friday. " * 15,
                          published=date(2023, 2, 2), via="direct")
        person = dict(first_name="Mark", last_name="Diaz", role="arrested", evidence="-", age=None)
        with self.assertRaises(OutOfScope) as ctx:
            build_query(extraction(people=[person], city=None, state=None, county=None, agency=None), article)
        self.assertEqual(ctx.exception.reason, "unknown")


class FetchTest(unittest.TestCase):
    def _fetch(self, browser_effect):
        with mock.patch.object(fetch_mod.trafilatura, "fetch_url", return_value=None), \
             mock.patch.object(fetch_mod, "_fetch_with_browser", side_effect=browser_effect), \
             mock.patch.dict("os.environ", {"BROWSER_PROVIDER": "local"}):
            with self.assertRaises(FetchError) as ctx:
                fetch_mod.fetch_article("https://www.wesh.com/article/missing/1")
        return ctx.exception

    def test_page_not_found(self):
        exc = self._fetch(fetch_mod._PageGone(404))
        self.assertEqual(exc.kind, "missing")
        self.assertIn("doesn't exist", str(exc))

    def test_site_does_not_exist(self):
        exc = self._fetch(RuntimeError("Page.goto: net::ERR_NAME_NOT_RESOLVED at https://www.wesh.con/a"))
        self.assertEqual(exc.kind, "unreachable")

    def test_page_without_article(self):
        exc = self._fetch(lambda url: "<html><body><p>Subscribe to read.</p></body></html>")
        self.assertEqual(exc.kind, "unreadable")

    def test_404_page_is_never_read(self):
        """La página de 'no encontrado' puede listar otras noticias con nombres: no se lee."""
        page = mock.Mock()
        page.goto.return_value = mock.Mock(status=404)
        with self.assertRaises(fetch_mod._PageGone):
            fetch_mod._goto(page, "https://example.com/x")


class PipelineStatusTest(unittest.TestCase):
    def run_with(self, **patches):
        targets = {"fetch_article": mock.DEFAULT, "extract": mock.DEFAULT, "build_query": mock.DEFAULT}
        with mock.patch.multiple(extractor, **targets) as m, tempfile.TemporaryDirectory() as tmp:
            m["fetch_article"].return_value = VAGUE
            for name, effect in patches.items():
                m[name].side_effect = effect
            return pipeline.run_article("https://example.com/vague", Path(tmp))

    def test_statuses(self):
        cases = {
            "unreadable": dict(fetch_article=FetchError("x", kind="missing")),
            "no_suspect": dict(build_query=NoSuspect("x")),
            "unknown_location": dict(build_query=OutOfScope("unknown", "x")),
            "unsupported": dict(build_query=OutOfScope("county", "x")),
            "error": dict(extract=ExtractionError("NVIDIA is still overloaded")),
        }
        for status, patches in cases.items():
            with self.subTest(status):
                self.assertEqual(self.run_with(**patches).status, status)


if __name__ == "__main__":
    unittest.main()
