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


def jail_record(name, booking, booked, age, charges, released=""):
    rows = "".join(
        f"<tr><td id='expander{i}' onclick='ToggleExpand(this.id)'>[+]</td>"
        f"<td aria-label='Statute  {st}'>{st}</td><td aria-label='Case Number {ref}'>{ref}</td>"
        f"<td aria-label='Charge  {ch}'>{ch}</td><td>F</td><td>F</td><td>$0.00</td></tr>"
        f"<tr><td colspan='8' id='expanded{i}' style='display:none'>{more}</td></tr>"
        for i, (st, ref, ch, more) in enumerate(charges))
    released_cells = f"<td class='InmateInfoGridTd'>Released:</td><td class='InmateInfoGridTd'>{released}</td>" if released else ""
    return (f"<tr class='InmateRecordRow'><td><img></td><td nowrap valign='top'><table><thead><tr>"
            f"<td class='SearchHeader' colspan='4'>{name} &nbsp; (W/ MALE )</td></tr></thead><tbody>"
            f"<tr><td class='InmateInfoGridTd'>Status:</td><td class='InmateInfoGridTd'>Released</td></tr>"
            f"<tr><td class='InmateInfoGridTd'>Booking No:</td><td class='InmateInfoGridTd'>{booking}</td>"
            f"<td class='InmateInfoGridTd'>MniNo:</td><td class='InmateInfoGridTd'>ECSO07MNI021509</td></tr>"
            f"<tr><td class='InmateInfoGridTd'>Booking Date:</td><td class='InmateInfoGridTd'>{booked}</td>{released_cells}</tr>"
            f"<tr><td class='InmateInfoGridTd'>Age On Booking Date:</td><td class='InmateInfoGridTd'>{age}</td></tr>"
            f"</tbody></table></td></tr><tr class='InmateRecordSeperater'></tr>"
            f"<tr><td colspan='2' valign='top'><table class='JailViewCharges' aria-label='Charges for the Subject'>"
            f"<thead><tr><td class='SearchHeader' colspan='6'>CHARGES</td></tr></thead><tbody>"
            f"<tr class='SearchHeader'><td></td><td>STATUTE</td><td>COURT CASE NUMBER</td><td>CHARGE</td></tr>"
            f"{rows}</tbody></table></td></tr>")


# Como el registro de la cárcel (10/11/2026, PDF de la búsqueda de Rodriguez): cinco bookings.
RODRIGUEZ_JAIL_HTML = (
    "<div id='JailInfo'><table class='JailView'><tbody>"
    + jail_record("RODRIGUEZ, MICHAEL ANDREW", "ECSO11JBN002003", "02/11/2011 12:39 PM", 26,
                  [("784.03.1a1", "PPD11OFF000956 (PENSACOLA POLICE DEPARTMENT )", "BATTERY", "TOUCH OR STRIKE")],
                  "02/12/2011 07:42 PM")
    + jail_record("RODRIGUEZ, MICHAEL ANDREW", "ECC23JBN007745", "07/22/2023 07:24 AM", 39,
                  [("782.04.2", "ECSO23OFF013609 (ESCAMBIA COUNTY SHERIFF'S OFFICE)", "HOMICIDE", "")],
                  "07/24/2023 09:39 PM")
    + jail_record("RODRIGUEZ, MICHAEL ANDREW", "ECC23JBN011847", "10/26/2023 10:20 AM", 40,
                  [("0.00a", "2023 CF 3056 (ESCAMBIA COUNTY SHERIFF'S OFFICE)", "GENERIC STATUTE CODE", "")],
                  "03/19/2025 05:00 AM")
    + jail_record("RODRIGUEZ, MICHAEL JOSE", "ECC24JBN000001", "07/23/2023 01:00 AM", 22,
                  [("784.03.1a1", "ECSO23OFF013700 (ESCAMBIA COUNTY SHERIFF'S OFFICE)", "BATTERY", "")])
    + "</tbody></table></div>")


def jail_bookings():
    from playwright.sync_api import sync_playwright
    from adapters.escambia_jail import _READ_JS, bookings_from_rows
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(RODRIGUEZ_JAIL_HTML)
        rows = page.evaluate(_READ_JS)
        browser.close()
    return bookings_from_rows(rows)


RODRIGUEZ_QUERY = LookupQuery(first_name="Michael", last_name="Rodriguez", county="escambia", age=40,
                              incident_date=date(2023, 7, 21), published_date=date(2025, 3, 6),
                              age_as_of=date(2025, 3, 6))


class JailRecordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bookings = jail_bookings()

    def test_read(self):
        b = self.bookings[1]
        self.assertEqual((b.name, b.booking_number, b.age), ("RODRIGUEZ, MICHAEL ANDREW", "ECC23JBN007745", 39))
        self.assertEqual(b.booked.strftime("%m/%d/%Y %H:%M"), "07/22/2023 07:24")
        self.assertEqual(b.released.strftime("%m/%d/%Y"), "07/24/2023")
        self.assertEqual((b.report.number, b.report.agency), ("ECSO23OFF013609", "ESCAMBIA COUNTY SHERIFF'S OFFICE"))
        self.assertIsNone(b.court_case)
        self.assertEqual(self.bookings[0].charges[0].charge, "BATTERY TOUCH OR STRIKE")
        self.assertEqual(self.bookings[0].report.agency, "PENSACOLA POLICE DEPARTMENT")

    def test_court_case_and_warrants_are_not_reports(self):
        from adapters.escambia_jail import JailCharge
        self.assertEqual(self.bookings[2].court_case, "2023 CF 3056")
        self.assertIsNone(self.bookings[2].report)
        self.assertFalse(JailCharge("21WAR010641", "ESCAMBIA COUNTY SHERIFF'S OFFICE", "x").is_report)
        self.assertFalse(JailCharge("PNS2711000020", "OTHER AGENCY", "x").is_report)
        self.assertTrue(JailCharge("KRO2711001263", "FLORIDA HIGHWAY PATROL", "x").is_report)

    def test_the_booking_of_the_incident(self):
        """El del 22/07/2023, no el de la fianza revocada ni el de otro Michael Rodriguez (22 años)."""
        from adapters.escambia_jail import pick_booking
        self.assertEqual(pick_booking(self.bookings, RODRIGUEZ_QUERY, 60).booking_number, "ECC23JBN007745")

    def test_age_and_middle_name_tell_people_apart(self):
        from dataclasses import replace
        from adapters.escambia_jail import pick_booking
        older = replace(RODRIGUEZ_QUERY, age=60)
        self.assertIsNone(pick_booking(self.bookings, older, 60))
        jose = replace(RODRIGUEZ_QUERY, middle_name="Jose", age=None)
        self.assertEqual(pick_booking(self.bookings, jose, 60).booking_number, "ECC24JBN000001")

    def test_later_age_on_the_booking(self):
        """Ketchup (10/11/2026): booking del 23/08/2023 con "27", la edad que tenía en 2025 (la nota)."""
        from adapters.escambia_jail import JailBooking, pick_booking
        from datetime import datetime
        booking = JailBooking("KETCHUP, CHRISTIAN MICHAEL", "ECC23JBN009099", datetime(2023, 8, 23, 11, 12), age=27)
        query = LookupQuery(first_name="Christian", last_name="Ketchup", county="escambia", age=27,
                            incident_date=date(2023, 8, 23), age_as_of=date(2025, 9, 18))
        self.assertEqual(pick_booking([booking], query, 60), booking)
        booking.age = 22                      # más joven de lo que podía tener ese día
        self.assertIsNone(pick_booking([booking], query, 60))
        booking.age = 35                      # mayor de lo que tiene hoy
        self.assertIsNone(pick_booking([booking], query, 60))

    def test_same_charges_near_the_date(self):
        """Blackburn (10/11/2026): la nota fecha el crimen el 29/10/2023; fue en 2022 y su booking por
        homicidio es del 01/11/2022. Fuera de la ventana, se toma por los cargos y se avisa."""
        from datetime import datetime
        from adapters.benchmark_web import blocked_with_booking
        from adapters.escambia_jail import JailBooking, JailCharge, pick_booking
        theft = JailBooking("BLACKBURN, ALYSSA MARIE", "ECC22JBN006206", datetime(2022, 6, 27, 17, 36), age=22,
                            charges=[JailCharge("2022 MM 2617", "OTHER AGENCY", "LARC PETIT THEFT 1ST DEGREE")])
        murder = JailBooking("BLACKBURN, ALYSSA MARIE", "ECC22JBN010917", datetime(2022, 11, 1, 16, 40), age=26,
                             charges=[JailCharge("220784CT2", "ESCAMBIA COUNTY SHERIFF'S OFFICE",
                                                 "HOMICIDE MURDER FIRST DEGREE PREMEDITATED"),
                                      JailCharge("2022 CF 4540", "ESCAMBIA COUNTY SHERIFF'S OFFICE", "GENERIC STATUTE CODE")])
        query = LookupQuery(first_name="Alyssa", last_name="Blackburn", middle_name="Marie", county="escambia",
                            incident_date=date(2023, 10, 29), published_date=date(2025, 10, 10),
                            charges=["principal to second degree murder", "first degree premeditated murder"])
        chosen = pick_booking([theft, murder], query, 60)
        self.assertEqual(chosen.booking_number, "ECC22JBN010917")
        self.assertEqual(chosen.story_date, date(2023, 10, 29))
        self.assertEqual(chosen.court_case, "2022 CF 4540")
        message = blocked_with_booking("Escambia County", chosen, "at https://x")
        self.assertIn("Note: this booking is 362 days before the date the story gives (10/29/2023); it was picked "
                      "because the charges match, so check that it's the same arrest.", message)

    def test_arrest_months_later(self):
        """Sumler (11/11/2026): triple homicidio del 28/04/2024, arrestado el 12/08/2024 (106 días)."""
        from datetime import datetime
        from adapters.benchmark_web import blocked_with_booking
        from adapters.escambia_jail import JailBooking, JailCharge, pick_booking
        booking = JailBooking("SUMLER, TORRANCE LAMAR", "ECC24JBN009250", datetime(2024, 8, 12, 19, 32), age=24,
                              charges=[JailCharge("2024 CF 003308", "ESCAMBIA COUNTY SHERIFF'S OFFICE",
                                                  "HOMICIDE MURDER FIRST DEGREE PREMEDITATED")])
        query = LookupQuery(first_name="Torrance", last_name="Sumler", county="escambia", age=24,
                            incident_date=date(2024, 4, 28), published_date=date(2026, 2, 6),
                            age_as_of=date(2026, 2, 6), charges=["three counts of first-degree premeditated murder"])
        chosen = pick_booking([booking], query, 60)
        self.assertEqual(chosen.court_case, "2024 CF 003308")
        self.assertIn("this booking is 106 days after the date the story gives (04/28/2024)",
                      blocked_with_booking("Escambia County", chosen, "at https://x"))

    def test_details_for_the_web_card(self):
        """Sin reporte: la agencia sale del cargo con el número de caso; días desde la fecha de la nota."""
        from datetime import datetime
        from adapters.benchmark_web import jail_details
        from adapters.escambia_jail import JailBooking, JailCharge
        booking = JailBooking("SUMLER, TORRANCE LAMAR", "ECC24JBN009250", datetime(2024, 8, 12, 19, 32), age=24,
                              charges=[JailCharge("2024 CF 003308", "ESCAMBIA COUNTY SHERIFF'S OFFICE", "HOMICIDE"),
                                       JailCharge("2024 CF 003308", "ESCAMBIA COUNTY SHERIFF'S OFFICE", "HOMICIDE")],
                              story_date=date(2024, 4, 28))
        d = jail_details(get_county("escambia"), booking, "Sumler, Torrance", date(2024, 4, 21), date(2026, 2, 20))
        self.assertEqual((d["court_case"], d["report"], d["agency"]),
                         ("2024 CF 003308", None, "Escambia County Sheriff's Office"))
        self.assertEqual((d["story_date"], d["days_from_story"], d["charges"]), ("2024-04-28", 106, ["HOMICIDE"]))
        self.assertEqual(d["portal_url"], get_county("escambia").base_url)

    def test_tolerance_needs_the_same_kind_of_charges_and_a_near_date(self):
        from dataclasses import replace
        from datetime import datetime
        from adapters.escambia_jail import JailBooking, JailCharge, pick_booking
        theft = JailBooking("BLACKBURN, ALYSSA MARIE", "ECC22JBN006206", datetime(2022, 6, 27), age=22,
                            charges=[JailCharge("2022 MM 2617", "OTHER AGENCY", "LARC PETIT THEFT 1ST DEGREE")])
        query = LookupQuery(first_name="Alyssa", last_name="Blackburn", county="escambia",
                            incident_date=date(2023, 10, 29), published_date=date(2025, 10, 10),
                            charges=["first degree premeditated murder"])
        self.assertIsNone(pick_booking([theft], query, 60))                          # otros cargos
        old = replace(theft, charges=[JailCharge("x", "ESCAMBIA COUNTY SHERIFF'S OFFICE", "HOMICIDE")],
                      booked=datetime(2019, 1, 1))
        self.assertIsNone(pick_booking([old], query, 60))                            # demasiado lejos
        after = replace(old, booked=datetime(2025, 12, 1))
        self.assertIsNone(pick_booking([after], query, 60))                          # después de la nota
        self.assertIsNone(pick_booking([replace(old, booked=datetime(2023, 3, 1))], replace(query, charges=[]), 60))

    def test_nothing_near_the_date(self):
        from dataclasses import replace
        from adapters.escambia_jail import pick_booking
        self.assertIsNone(pick_booking(self.bookings, replace(RODRIGUEZ_QUERY, incident_date=date(2019, 1, 1)), 60))

    def test_message(self):
        from adapters.benchmark_web import blocked_with_booking
        message = blocked_with_booking("Escambia County", self.bookings[1], 'at https://x with the name "Rodriguez, Michael"')
        self.assertIn("booked 07/22/2023 07:24 AM, booking ECC23JBN007745, "
                      "Escambia County Sheriff's Office report ECSO23OFF013609, charges: homicide.", message)
        self.assertIn("https://inmatelookup.myescambia.com/smartwebclient/jail.aspx", message)
        self.assertIn("or try again later.", message)
        self.assertNotIn("\n", message)


class BlockedFallbackTest(unittest.TestCase):
    def blocked_search(self, key, booking):
        from adapters.benchmark_web import CloudflareBlocked
        adapter = BenchmarkWebAdapter(page=mock.MagicMock(), county=get_county(key), captcha=mock.MagicMock())
        adapter._search = mock.MagicMock(side_effect=CloudflareBlocked("x"))
        with mock.patch("adapters.escambia_jail.jail_fallback", return_value=booking) as fallback:
            try:
                adapter.search(RODRIGUEZ_QUERY, date(2023, 7, 14), date(2025, 3, 20))
            except Exception as exc:
                return exc, fallback
        self.fail("no exception")

    def test_jail_gives_the_numbers(self):
        from core.adapter import PortalBlocked
        exc, fallback = self.blocked_search("escambia", jail_bookings()[1])
        self.assertIsInstance(exc, PortalBlocked)
        self.assertEqual((exc.agency_case_number, exc.case_number), ("ECSO23OFF013609", None))
        self.assertEqual(exc.details["booking_number"], "ECC23JBN007745")
        self.assertEqual(exc.details["booked"], "2023-07-22T07:24")
        self.assertEqual(exc.details["agency"], "Escambia County Sheriff's Office")
        self.assertEqual(exc.details["report"], "ECSO23OFF013609")
        self.assertEqual(exc.details["charges"], ["HOMICIDE"])
        self.assertEqual((exc.details["search_name"], exc.details["opened_from"], exc.details["opened_to"]),
                         ("Rodriguez, Michael", "2023-07-14", "2025-03-20"))
        self.assertIsNone(exc.details["days_from_story"])
        self.assertEqual(fallback.call_args.args[2:], (date(2023, 7, 14), date(2025, 3, 20), 60))

    def test_nothing_in_the_jail_keeps_the_blocked_message(self):
        from core.adapter import PortalBlocked, SearchError
        exc, _ = self.blocked_search("escambia", None)
        self.assertIsInstance(exc, SearchError)
        self.assertNotIsInstance(exc, PortalBlocked)
        self.assertIn("is blocking automated searches right now", str(exc))

    def test_other_counties_have_no_fallback(self):
        self.assertEqual([k for k in ("st_lucie", "martin", "clay", "bay") if get_county(k).blocked_fallback], [])
        exc, fallback = self.blocked_search("bay", jail_bookings()[1])
        fallback.assert_not_called()

    def test_lookup_result(self):
        """run_lookup: PortalBlocked termina como no_document con los números, no como error."""
        import tempfile
        from contextlib import contextmanager
        from pathlib import Path
        from types import SimpleNamespace
        from core import lookup
        from core.adapter import PortalBlocked

        class Fake(BenchmarkWebAdapter):
            def search(self, query, date_from, date_to):
                raise PortalBlocked("blocked, report ECSO23OFF013609", case_number="2023 CF 3056",
                                    agency_case_number="ECSO23OFF013609", details={"booking_number": "ECC23JBN007745"})

        @contextmanager
        def fake_browser(headless=False):
            yield SimpleNamespace(page=None, remote=False, live_view_url=None)

        with mock.patch.object(lookup, "open_browser", fake_browser), \
             mock.patch.object(lookup, "get_adapter", return_value=Fake), tempfile.TemporaryDirectory() as tmp:
            result = lookup.run_lookup(RODRIGUEZ_QUERY, Path(tmp), captcha=object())
        self.assertEqual((result.status, result.case_number, result.agency_case_number),
                         ("no_document", "2023 CF 3056", "ECSO23OFF013609"))
        self.assertEqual(result.message, "blocked, report ECSO23OFF013609")
        self.assertEqual(result.jail_booking, {"booking_number": "ECC23JBN007745"})
        self.assertEqual(result.to_dict()["jail_booking"], {"booking_number": "ECC23JBN007745"})


JAIL_FORM = """<html><body><form id='form1' method='post' action='./jail.aspx'>
<input id='txbLastName' name='txbLastName'><input id='txbFirstName' name='txbFirstName'>
<input id='tbBeginDate' name='tbBeginDate'><input id='tbEndDate' name='tbEndDate'>
<select id='TypeSearch' name='TypeSearch'><option value='0'>c</option><option value='2'>b</option></select>
<select id='SearchSortOption' name='SearchSortOption'><option value='0'>n</option><option value='1'>d</option></select>
<select id='SearchOrderOption' name='SearchOrderOption'><option value='0'>a</option></select>
<input type='submit' id='btnSumit' name='btnSumit' value='Submit'><img src='/photo.jpg'></form>%s</body></html>"""


class JailSearchFlowTest(unittest.TestCase):
    """El formulario real (probe_jail, 10/11/2026) contra una copia local: postback y lectura."""

    def run_search(self, results_html, begin=date(2023, 7, 14)):
        from playwright.sync_api import sync_playwright
        from adapters.escambia_jail import jail_fallback
        posts, images = [], []

        def handler(route):
            if route.request.resource_type == "image":
                images.append(route.request.url)
                return route.fulfill(body=b"", content_type="image/jpeg")
            if route.request.method == "POST":
                posts.append(route.request.post_data)
                return route.fulfill(body=JAIL_FORM % results_html, content_type="text/html")
            return route.fulfill(body=JAIL_FORM % "<div id='JailInfo'><table><tbody></tbody></table></div>",
                                 content_type="text/html")

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.route("https://inmatelookup.myescambia.com/**", handler)
            booking = jail_fallback(context, RODRIGUEZ_QUERY, begin, date(2025, 3, 20), 60)
            browser.close()
        return booking, posts, images

    def test_postback_and_pick(self):
        booking, posts, images = self.run_search(RODRIGUEZ_JAIL_HTML)
        self.assertEqual(booking.booking_number, "ECC23JBN007745")
        self.assertIn("TypeSearch=2", posts[0])
        self.assertIn("tbBeginDate=07%2F14%2F2023", posts[0])
        self.assertEqual(images, [])                      # las fotos de los presos no se piden

    def test_without_dates_when_nothing_comes_back(self):
        empty = "<div id='JailInfo'><table><tbody></tbody></table></div>"
        booking, posts, _ = self.run_search(empty)
        self.assertIsNone(booking)
        self.assertEqual(len(posts), 2)
        self.assertIn("tbBeginDate=&", posts[1])


class DownloadTimeoutTest(unittest.TestCase):
    """Todos los condados cortan una descarga a los 240 s (Escambia dejó una colgada, 10/11/2026)."""

    def test_every_county(self):
        from counties.florida import COUNTIES
        self.assertEqual({c.download_timeout_s for c in COUNTIES.values()}, {240})

    def test_benchmark_download(self):
        from core.models import DocketEntry
        adapter = BenchmarkWebAdapter(page=mock.MagicMock(), county=get_county("bay"), captcha=None)
        adapter.page.evaluate.return_value = {"error": "no answer in 240 s"}
        entry = DocketEntry(date(2023, 10, 30), "CAPIAS RETURNED SERVED", "1|d", 60)
        with self.assertRaises(Exception):
            adapter.download(CaseDetails(candidate(), [], [entry]), entry, None)
        self.assertEqual(adapter.page.evaluate.call_args.args[1]["timeoutMs"], 240_000)

    def test_remote_fetch_civitek_and_orange(self):
        from core.adapter import DownloadError
        from adapters.civitek_ocrs import CivitekOcrsAdapter
        adapter = CivitekOcrsAdapter(page=None, county=get_county("marion"), captcha=None)
        adapter.remote = True
        page = mock.MagicMock()
        page.evaluate.return_value = {"error": "no answer in 240 s"}
        with self.assertRaises(DownloadError):
            adapter.fetch(page, "https://x/doc.pdf")
        self.assertEqual(page.evaluate.call_args.args[1]["timeoutMs"], 240_000)

    def test_abort_in_a_real_page(self):
        from playwright.sync_api import sync_playwright
        from core.adapter import _FETCH_JS
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.route("http://t.test/", lambda r: r.fulfill(body="<html></html>", content_type="text/html"))
            page.route("http://t.test/slow.pdf", lambda r: page.wait_for_timeout(3000) or r.fulfill(body=b"x"))
            page.goto("http://t.test/")
            result = page.evaluate(_FETCH_JS, {"url": "http://t.test/slow.pdf", "referrer": None, "timeoutMs": 500})
            browser.close()
        self.assertEqual(result, {"error": "no answer in 1 s"})


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
