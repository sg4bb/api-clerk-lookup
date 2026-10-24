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

    def test_apostrophe_variants(self):
        """Sumter (13/10/2026): con "O'Brien" el portal devuelve el formulario vacío."""
        self.assertEqual(last_name_variants(portal_text("O'Brien")), ["O'Brien", "OBrien", "O Brien"])
        self.assertEqual(last_name_variants("D'Angelo"), ["D'Angelo", "DAngelo", "D Angelo"])

    def test_variants_without_accents(self):
        self.assertEqual(last_name_variants(portal_text("González Delgado")),
                         ["Gonzalez Delgado", "GonzalezDelgado", "Gonzalez-Delgado", "Delgado", "Gonzalez"])


class GluedMiddleNameTest(unittest.TestCase):
    def test_middle_name_glued_to_the_last_name(self):
        """Gulf (24/10/2026): el modelo dio "Conan Koepke"; el portal tiene KOEPKE, KURTIS CONAN."""
        from core.matcher import score_case
        from core.models import CaseCandidate, CaseDetails
        self.assertIn("Koepke", last_name_variants("Conan Koepke"))
        q = LookupQuery(first_name="Kurtis", last_name="Conan Koepke", county="gulf", state="FL",
                        incident_date=date(2026, 9, 11))
        candidate = CaseCandidate("26000300CFAXMX", "KOEPKE, KURTIS CONAN", "Felony", "OPEN", None,
                                  date(2026, 9, 12), "x")
        match = score_case(q, CaseDetails(candidate, [], []))
        self.assertFalse(match.disqualified, match.reasons)
        self.assertIn("apellido compuesto (conan koepke)", match.reasons)
        self.assertEqual(match.score, 100)


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
                         ["Gonzalez Delgado", "GonzalezDelgado", "Gonzalez-Delgado", "Delgado", "Gonzalez"])
        self.assertTrue(all(first == "Jose" and middle == "Alberto" for first, middle, _ in searched))


class ApostropheSearchTest(unittest.TestCase):
    def test_second_form_of_the_name_is_tried(self):
        searched = []

        class FakeAdapter:
            def __init__(self, page, county, captcha):
                self.remote = False

            def throttle(self):
                pass

            def search(self, query, date_from, date_to):
                searched.append(query.last_name)
                return []          # el portal no acepta ninguna forma: se prueban las tres

        @contextmanager
        def fake_browser(headless=False):
            yield SimpleNamespace(page=None, remote=False, live_view_url=None)

        query = LookupQuery(first_name="David", last_name="O'Brien", county="sumter", state="FL",
                            incident_date=date(2026, 7, 21))
        with mock.patch.object(lookup, "open_browser", fake_browser), \
             mock.patch.object(lookup, "get_adapter", return_value=FakeAdapter), \
             tempfile.TemporaryDirectory() as tmp:
            result = lookup.run_lookup(query, Path(tmp), captcha=object())
        self.assertEqual(result.status, "not_found")
        self.assertEqual(searched, ["O'Brien", "OBrien", "O Brien"])


if __name__ == "__main__":
    unittest.main()
