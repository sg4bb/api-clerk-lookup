"""Lo que se escribe en el formulario del portal: nombres sin acentos."""

import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import lookup
from core.models import LookupQuery
from core.utils import last_name_variants, portal_text


class PortalTextTest(unittest.TestCase):
    def test_accents_removed(self):
        self.assertEqual(portal_text("José"), "Jose")
        self.assertEqual(portal_text("González Delgado"), "Gonzalez Delgado")
        self.assertEqual(portal_text("Muñoz"), "Munoz")
        self.assertEqual(portal_text("Peña-Ríos"), "Pena-Rios")

    def test_plain_names_unchanged(self):
        for name in ("Mues", "O'Neil", "Rosado-Martinez", "St. John", "Smith Jr."):
            self.assertEqual(portal_text(name), name)
        self.assertEqual(portal_text(None), "")

    def test_variants_without_accents(self):
        self.assertEqual(last_name_variants(portal_text("González Delgado")),
                         ["Gonzalez Delgado", "GonzalezDelgado", "Gonzalez-Delgado"])


class SearchFormTest(unittest.TestCase):
    """Caso real (Orange, 2021-CF-015656-A-O): la noticia dice 'José Alberto González
    Delgado'; el portal solo lo encuentra como 'Jose' / 'Gonzalez Delgado'."""

    def test_portal_receives_names_without_accents(self):
        searched = []

        class FakeAdapter:
            def __init__(self, page, county, captcha):
                self.remote = False

            def throttle(self):
                pass

            def search(self, query, date_from, date_to):
                searched.append((query.first_name, query.middle_name, query.last_name))
                return []

        @contextmanager
        def fake_browser(headless=False):
            yield SimpleNamespace(page=None, remote=False, live_view_url=None)

        query = LookupQuery(first_name="José", middle_name="Alberto", last_name="González Delgado",
                            county="orange", state="FL", incident_date=date(2021, 11, 29), age=27,
                            agency="Orlando Police Department")
        with mock.patch.object(lookup, "open_browser", fake_browser), \
             mock.patch.object(lookup, "get_adapter", return_value=FakeAdapter), \
             tempfile.TemporaryDirectory() as tmp:
            result = lookup.run_lookup(query, Path(tmp), captcha=object())

        self.assertEqual(result.status, "not_found")
        self.assertEqual([last for _, _, last in searched],
                         ["Gonzalez Delgado", "GonzalezDelgado", "Gonzalez-Delgado"])
        self.assertTrue(all(first == "Jose" and middle == "Alberto" for first, middle, _ in searched))


if __name__ == "__main__":
    unittest.main()
