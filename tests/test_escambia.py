"""Escambia (10/11/2026): BenchmarkWeb 2.6.3.18 detrás de la verificación de Cloudflare.

Rodriguez, Michael Andrew, 2023 CF 003056 A (homicidio en Sam's Lounge, 21/07/2023):
- Docket "Image | Date | Entry", sin número de entrada, de la entrada más nueva a la más vieja.
- Dos "ARREST REPORT": 24/07/2023 (el arresto del hecho) y 27/10/2023 (fianza revocada, capias).
- El arresto sale en "ARREST SEQ: 1 - (782.04(2)) MURDER ..." del 22/07/2023.
- La suma se pide en cada búsqueda; Cloudflare verifica el navegador antes del portal.
"""

import unittest
from datetime import date
from unittest import mock

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from adapters.benchmark_web import BenchmarkWebAdapter, arrest_date_from_docket, court_code, docket_from_case
from core.models import CaseCandidate, CaseDetails, LookupQuery
from counties import get_county

IMAGE = "<a href='javascript:void(0);' class='casedocketimage' id='{id}' digest='d{id}' rel='{id}'>{pages}</a>&#160;"
REQUEST = ("<button type='button' class='popmodal' casedocketid='{id}'>Request</button>"
           "<div class='modal hide fade'><h3>Please Enter Your Email Address</h3>"
           "<button>Submit Request</button></div>")


def row(cell, when, text, ident):
    return (f"<tr><td class='imgCol'>{cell}</td><td> {when} </td>"
            f"<td id='text_{ident}'> {text}&#160; </td></tr>")


# Como el docket del portal: de la más nueva a la más vieja.
RODRIGUEZ_DOCKET_HTML = (
    "<div id='courtCaseDockets'><table id='gridDockets' class='sortableGrid'><thead><tr>"
    "<th id='imgCol'>Image</th><th class='sortable-date'>Date</th><th class='sortable'>Entry</th>"
    "</tr></thead><tbody>"
    + row(REQUEST.format(id=1), "8/3/2026", "MANDATE AND OPINION FROM DISTRICT COURT - AFFIRMED", 1)
    + row(IMAGE.format(id=2, pages=1), "3/11/2025", "JUDGMENT AND SENTENCE-DOC", 2)
    + row(IMAGE.format(id=3, pages=1), "10/30/2023", "CAPIAS RETURNED SERVED", 3)
    + row(IMAGE.format(id=4, pages=2), "10/27/2023", "ARREST REPORT", 4)
    + row("", "10/25/2023", "CAPIAS ISSUED ISSUED: COURT ORDER BOND REVOCATION", 5)
    + row(REQUEST.format(id=6), "8/9/2024", "SEALED DOCKET ENTRY AT DIN 95", 6)
    + row(IMAGE.format(id=7, pages=1), "7/24/2023", "NONADVERSARIAL PROBABLE CAUSE DETERMINATION RULE 3.133", 7)
    + row(IMAGE.format(id=8, pages=3), "7/24/2023", "ARREST REPORT", 8)
    + row("", "7/22/2023", "ARREST SEQ: 1 - (782.04(2)) MURDER DANGEROUS DEPRAVED WO PREMEDITATION", 9)
    + "</tbody></table></div>")


def read_docket(html):
    from playwright.sync_api import sync_playwright
    from adapters.benchmark_web import _READ_CASE_JS
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        rows = page.evaluate(_READ_CASE_JS)["docket"]
        browser.close()
    return rows


def candidate():
    return CaseCandidate("2023 CF 003056 A", "RODRIGUEZ, MICHAEL ANDREW", "CF", "CLOSED", date(1984, 4, 28), None, "x")


class DocketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = read_docket(RODRIGUEZ_DOCKET_HTML)
        cls.docket = docket_from_case(cls.rows)
        cls.adapter = BenchmarkWebAdapter(page=None, county=get_county("escambia"), captcha=None)

    def test_the_image_column_is_not_the_entry_number(self):
        """La primera columna trae la cantidad de páginas ("1", "3") o "Request": no es el número."""
        self.assertEqual({r["seq"] for r in self.rows}, {""})

    def test_dates_from_the_date_column(self):
        self.assertEqual(self.rows[0]["date"], "8/3/2026")
        self.assertTrue(all(e.date for e in self.docket))

    def test_oldest_first_and_numbered(self):
        self.assertEqual([e.number for e in self.docket], list(range(1, 10)))
        self.assertEqual(self.docket[0].description[:13], "ARREST SEQ: 1")
        self.assertEqual(self.docket[-1].date, date(2026, 8, 3))

    def test_arrest_date(self):
        self.assertEqual(arrest_date_from_docket(self.docket), date(2023, 7, 22))

    def test_the_arrest_report_of_the_incident_first(self):
        found = self.adapter.find_documents(CaseDetails(candidate(), [], self.docket))
        self.assertEqual([(e.description, e.date) for e in found],
                         [("ARREST REPORT", date(2023, 7, 24)), ("ARREST REPORT", date(2023, 10, 27))])
        self.assertEqual(found[0].doc_ref, "8|d8")

    def test_probable_cause_determination_is_not_the_document(self):
        only_pc = [e for e in self.docket if e.description != "ARREST REPORT"]
        self.assertEqual(self.adapter.find_documents(CaseDetails(candidate(), [], only_pc)), [])

    def test_support_is_the_served_capias(self):
        found = self.adapter.find_support_documents(CaseDetails(candidate(), [], self.docket))
        self.assertEqual([e.description for e in found], ["CAPIAS RETURNED SERVED"])

    def test_arrest_report_on_request(self):
        html = RODRIGUEZ_DOCKET_HTML.replace(IMAGE.format(id=8, pages=3), REQUEST.format(id=8))
        details = CaseDetails(candidate(), [], docket_from_case(read_docket(html)))
        self.assertEqual([e.date for e in self.adapter.find_documents(details)], [date(2023, 10, 27)])
        self.assertEqual([e.date for e in self.adapter.find_requestable_documents(details)], [date(2023, 7, 24)])


class OtherDocketsUnchangedTest(unittest.TestCase):
    """Con columna de número (DIN o SEQ#) todo sigue igual: el número y el orden del portal."""

    def test_image_din_date_entry(self):
        html = ("<table id='gridDockets'><thead><tr><th>Image</th><th>DIN</th><th>Date</th><th>Entry</th></tr></thead>"
                "<tbody><tr><td>2</td><td>22</td><td> 12/21/2018 </td><td>EP - INFORMATION FILED</td></tr>"
                "<tr><td></td><td>3</td><td> 11/28/2018 </td><td>CASE FILED</td></tr></tbody></table>")
        docket = docket_from_case(read_docket(html))
        self.assertEqual([(e.number, e.date) for e in docket], [(22, date(2018, 12, 21)), (3, date(2018, 11, 28))])

    def test_rows_with_numbers_keep_their_order(self):
        rows = [{"seq": "5", "date": "1/2/2020", "text": "B"}, {"seq": "9", "date": "1/1/2020", "text": "A"}]
        self.assertEqual([e.description for e in docket_from_case(rows)], ["B", "A"])


class CloudflarePageTest(unittest.TestCase):
    def test_challenge_page(self):
        from playwright.sync_api import sync_playwright
        from core.captcha import CLOUDFLARE_PAGE_JS, CLOUDFLARE_PASSED_JS
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content("<title>Just a moment...</title><p>Performing security verification</p>")
            self.assertTrue(page.evaluate(CLOUDFLARE_PAGE_JS))
            self.assertFalse(page.evaluate(CLOUDFLARE_PASSED_JS))
            page.set_content("<title>Escambia County Clerk of Court</title><input id='name'>")
            self.assertFalse(page.evaluate(CLOUDFLARE_PAGE_JS))
            self.assertTrue(page.evaluate(CLOUDFLARE_PASSED_JS))
            browser.close()

    def test_incomplete_page(self):
        from playwright.sync_api import sync_playwright
        from adapters.benchmark_web import _INCOMPLETE_JS
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content("<img alt='Captcha' src='data:,'>")
            self.assertEqual(page.evaluate(_INCOMPLETE_JS), "the sum image")
            browser.close()


class CloudflareGateTest(unittest.TestCase):
    def adapter(self, key="escambia"):
        adapter = BenchmarkWebAdapter(page=mock.MagicMock(), county=get_county(key), captcha=mock.MagicMock())
        adapter.page.goto.return_value = mock.MagicMock(status=200)
        return adapter

    def test_only_escambia_opens_through_the_gate(self):
        for key, expected in (("escambia", 1), ("st_lucie", 0), ("martin", 0), ("clay", 0), ("bay", 0)):
            adapter = self.adapter(key)
            adapter.page.evaluate.return_value = False          # sin error 52x
            adapter._wait_scripts = lambda: None
            adapter._pass_cloudflare_gate = mock.MagicMock()
            adapter._open_form()
            self.assertEqual(adapter._pass_cloudflare_gate.call_count, expected, key)

    def test_complete_page_needs_nothing(self):
        adapter = self.adapter()
        adapter._wait_cloudflare = mock.MagicMock()
        adapter._evaluate_or = lambda js, default: ""
        adapter._pass_cloudflare_gate()
        adapter.page.reload.assert_not_called()
        adapter.page.goto.assert_not_called()

    def test_reload_then_open_what_was_denied_as_a_page(self):
        """Paso 1: recargar sin caché. Paso 2: abrir como página lo que negó Cloudflare. Luego, recargar."""
        from adapters.benchmark_web import _INCOMPLETE_JS
        adapter = self.adapter()
        adapter._wait_cloudflare = mock.MagicMock()
        bundle = "https://public.escambiaclerk.com/BMWebLatest/Content/bundles/master?v=Q2"
        missing = iter(["the sum image", "the sum image", "the sum image", ""])

        def evaluate(js, default):
            if js == _INCOMPLETE_JS:
                adapter._cf_blocked.append((403, "stylesheet", bundle))
                return next(missing)
            return default

        adapter._evaluate_or = evaluate
        adapter._pass_cloudflare_gate()
        self.assertEqual(adapter.page.reload.call_count, 2)
        self.assertEqual([c.args[0] for c in adapter.page.goto.call_args_list],
                         [bundle, get_county("escambia").base_url])

    def test_sum_image_when_nothing_was_seen(self):
        adapter = self.adapter()
        self.assertEqual(adapter._blocked_page_url([]),
                         "https://public.escambiaclerk.com/BMWebLatest/CourtCase.aspx/CaptchaImage")
        self.assertEqual(adapter._blocked_page_url([(403, "image", "https://x/i"), (403, "script", "https://x/s")]),
                         "https://x/s")

    def test_watch_only_the_portal_host(self):
        adapter = self.adapter()
        adapter._watch_blocked()
        handler = adapter.page.on.call_args.args[1]
        for url, status in (("https://public.escambiaclerk.com/BMWebLatest/Scripts/bundles/master", 403),
                            ("https://www.google.com/recaptcha/api.js", 403),
                            ("https://public.escambiaclerk.com/BMWebLatest/Content/site.css", 200)):
            handler(mock.MagicMock(status=status, url=url, request=mock.MagicMock(resource_type="script")))
        self.assertEqual([u for _, _, u in adapter._cf_blocked],
                         ["https://public.escambiaclerk.com/BMWebLatest/Scripts/bundles/master"])
        adapter._watch_blocked()
        self.assertEqual(adapter.page.on.call_count, 1)          # un solo listener por página
        self.assertEqual(adapter._cf_blocked, [])

    def test_still_incomplete_is_an_error(self):
        from core.adapter import SearchError
        adapter = self.adapter()
        adapter._wait_cloudflare = mock.MagicMock()
        adapter._evaluate_or = lambda js, default: "jQuery" if "jQuery" in js else default
        with self.assertRaises(SearchError):
            adapter._pass_cloudflare_gate()
        self.assertEqual(adapter.page.reload.call_count + adapter.page.goto.call_count, 4)   # 3 cargas, ~1 minuto

    def test_blocked_search_says_how_to_search_by_hand(self):
        from adapters.benchmark_web import CloudflareBlocked
        from core.adapter import SearchError
        adapter = self.adapter()
        adapter._open_form = mock.MagicMock(side_effect=CloudflareBlocked("the page kept loading without jQuery"))
        q = LookupQuery(first_name="Michael", last_name="Rodriguez", county="escambia", case_types=["CF"])
        with self.assertRaises(SearchError) as ctx:
            adapter.search(q, date(2023, 7, 14), date(2025, 3, 20))
        message = str(ctx.exception)
        self.assertNotIsInstance(ctx.exception, CloudflareBlocked)
        self.assertIn("Escambia County's court portal is blocking automated searches right now", message)
        self.assertIn("Try again later", message)
        self.assertIn("https://public.escambiaclerk.com/BMWebLatest/Home.aspx/Search", message)
        self.assertIn('"Rodriguez, Michael", cases opened 07/14/2023 to 03/20/2025', message)

    def test_challenge_that_passes_alone(self):
        adapter = self.adapter()
        adapter._evaluate_or = mock.MagicMock(return_value=True)
        adapter._wait_cloudflare()
        adapter.captcha.solve.assert_not_called()

    def test_challenge_that_shows_up_after_load(self):
        """La verificación puede llegar unos segundos después del "load" (se mira hasta 4 s)."""
        adapter = self.adapter()
        adapter._evaluate_or = mock.MagicMock(side_effect=[False, False, True])
        adapter._wait_cloudflare()
        adapter.page.wait_for_function.assert_called_once()

    def test_challenge_that_needs_the_person(self):
        adapter = self.adapter()
        adapter._evaluate_or = mock.MagicMock(return_value=True)          # sigue la página de Cloudflare
        adapter.page.wait_for_function.side_effect = PlaywrightTimeout("45 s")
        adapter._wait_cloudflare()
        self.assertEqual(adapter.captcha.solve.call_args.kwargs["kind"], "cloudflare")

    def test_spinning_challenge_is_a_block(self):
        """Chrome local con perfil limpio (10/11/2026): el círculo gira sin casilla. 90 s para la persona y,
        si nada, el mensaje de bloqueo (no los 10 minutos de un CAPTCHA)."""
        from adapters.benchmark_web import CloudflareBlocked
        from core.captcha import CaptchaTimeout
        adapter = self.adapter()
        adapter._evaluate_or = mock.MagicMock(return_value=True)
        adapter.page.wait_for_function.side_effect = PlaywrightTimeout("45 s")
        adapter.captcha.solve.side_effect = CaptchaTimeout("no")
        with self.assertRaises(CloudflareBlocked):
            adapter._wait_cloudflare()
        self.assertEqual(adapter.captcha.solve.call_args.kwargs["timeout_s"], 90)

    def test_page_that_never_loads_is_not_a_crash(self):
        adapter = self.adapter()
        adapter.page.goto.side_effect = PlaywrightTimeout("30 s")
        self.assertIsNone(adapter._goto_behind_cloudflare("https://public.escambiaclerk.com/BMWebLatest/Home.aspx/Search"))
        adapter.page.reload.side_effect = PlaywrightTimeout("30 s")
        self.assertIsNone(adapter._goto_behind_cloudflare(None))

    def test_no_challenge(self):
        adapter = self.adapter()
        adapter._evaluate_or = mock.MagicMock(return_value=False)
        adapter._wait_cloudflare()
        self.assertEqual(adapter._evaluate_or.call_count, 5)          # 4 s mirando
        adapter.page.wait_for_function.assert_not_called()
        adapter.captcha.solve.assert_not_called()

    def test_live_view_shows_the_whole_page(self):
        """La página de Cloudflare no se tapa ni se achica: es toda la verificación."""
        from core.captcha import LiveViewCaptcha
        page = mock.MagicMock()
        self.assertIsNone(LiveViewCaptcha("https://live")._focus_on_captcha(page, "cloudflare"))
        page.set_viewport_size.assert_not_called()


class EscambiaSearchTest(unittest.TestCase):
    def test_sum_on_every_search(self):
        calls = []
        adapter = BenchmarkWebAdapter(page=mock.MagicMock(), county=get_county("escambia"), captcha=mock.MagicMock())
        adapter.page.url = "https://public.escambiaclerk.com/BMWebLatest/Home.aspx/Search"
        adapter.captcha.solve.side_effect = lambda page, timeout_s, kind: calls.append(f"solve:{kind}")
        for name in ("_open_form", "_fill_form", "_ensure_captcha_box"):
            setattr(adapter, name, (lambda n: (lambda *a, **k: calls.append(n)))(name))
        adapter._click_search = lambda: calls.append("click")
        adapter._wait_results = lambda: "empty"
        q = LookupQuery(first_name="Michael", last_name="Rodriguez", county="escambia", case_types=["CF"])
        for _ in range(2):
            calls.clear()
            adapter.search(q, date(2023, 7, 14), date(2023, 9, 20))
            self.assertEqual(calls, ["_open_form", "_fill_form", "_ensure_captcha_box", "solve:text", "click"])


class EscambiaConfigTest(unittest.TestCase):
    def test_case_numbers(self):
        self.assertEqual([court_code(n) for n in ("2023 CF 003056 A", "2011 MM 022054 A", "2010 TR 027305 IUTC",
                                                  "2024 DR 003699")], ["CF", "MM", "TR", "DR"])

    def test_flags(self):
        county = get_county("escambia")
        self.assertEqual(county.captcha, "text")
        self.assertTrue(county.captcha_before_search and county.captcha_every_search)
        self.assertTrue(county.documents_on_request and county.cloudflare_gate)
        others = [k for k in ("st_lucie", "martin", "clay", "bay", "osceola", "flagler") if get_county(k).cloudflare_gate]
        self.assertEqual(others, [])

    def test_agencies_and_places(self):
        from core.matcher import normalize_agency
        from counties import florida
        for agency in ("Escambia County Sheriff's Office", "ECSO", "ESCAMBIA COUNTY SHERIFF'S OFFICE",
                       "Pensacola Police Department", "PENSACOLA POLICE DEPARTMENT"):
            self.assertEqual(florida.AGENCY_TO_COUNTY[normalize_agency(agency)], "escambia", agency)
        for city in ("pensacola", "cantonment", "century", "molino", "perdido key", "pensacola beach", "ensley"):
            self.assertEqual(florida.CITY_TO_COUNTY[city], "escambia", city)
        self.assertEqual(florida.CITY_TO_COUNTY["gulf breeze"], "santa_rosa")


if __name__ == "__main__":
    unittest.main()
