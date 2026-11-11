"""Volusia (11/11/2026): Case Inquiry de ccms.clerk.org contra una copia local del portal.

La copia imita lo que vio probe_volusia con Schmalz-Riedt (2026 105517 CFDL): el aviso que hay que
aceptar, el formulario, la tabla de resultados, las pestañas del caso (Custody, Offense, Docket) y
el visor que tacha el documento y entrega el PDF por load_Redact.aspx UNA sola vez.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from adapters.volusia_ccms import VolusiaCcmsAdapter, defendant_from_style, docket_from_rows
from core.models import LookupQuery
from counties import get_county

DISCLAIMER = """<html><body><p>Disclaimer</p>
<a id="Content1_button_accept" href="javascript:__doPostBack('ctl00$Content1$button_accept','')"
   onclick="document.cookie='ok=1; path=/'; location.href='/inquiry.aspx'; return false;">Accept</a></body></html>"""

FORM = """<html><body><form id="frm" method="post" action="/inquiry.aspx">
<input id="Content1_CaseNum"><input id="Content1_fileStartDate" name="from"><input id="Content1_fileEndDate" name="to">
<input id="Content1_lastName_tx" name="last"><input id="Content1_firstName_tx" name="first">
<input id="Content1_middleName_tx" name="middle"><input id="Content1_CaptchaCodeTextBox" style="display:none">
<a id="Content1_lb_submit" href="#" onclick="document.getElementById('frm').submit(); return false;">Submit</a>
</form>%s</body></html>"""

ROW = """<tr class="rowstyle"><td><img src="Images/plus.png" onclick="GetChargeGrid('1')">
<a id="CaseNum" href="caseCR_detail.aspx?d={d}&amp;t={t}" target="_blank">{num}</a></td>
<td>STATE OF FLORIDA v. {style}</td><td><a id="SPN" href="#">1010404</a></td>
<td><span id="partytype">{party}</span></td><td><span id="casetype" title="Felony">{t}</span></td>
<td><span id="category">{t}</span></td><td><span id="div">07</span></td>
<td><span id="status" title="Open">O</span></td><td>{filed}</td><td class="InvisibleCol"></td></tr>"""

RESULTS = ("""<span id="Content1_lblResults">Records Found 3</span><table id="CaseGrid"><tbody>
<tr class="headerstyle"><th>Case Number</th></tr>"""
           + ROW.format(d="3b36", t="CF", num="2026&nbsp;105517&nbsp;CFDL", style="JOHN ERIC SCHMALZ RIEDT",
                        party="Defendant", filed="08/23/2026")
           + ROW.format(d="3b37", t="TR", num="2024&nbsp;113842&nbsp;TRCI", style="JOHN ERIC SCHMALZ RIEDT",
                        party="Defendant", filed="03/20/2024")
           + ROW.format(d="3b38", t="CF", num="2025&nbsp;100001&nbsp;CFDL", style="JOHN SCHMALZ RIEDT",
                        party="Victim", filed="01/02/2025")
           + "</tbody></table>")

GRIDS = {
    "custody": """<table id="tabContainer_custody_GridCustody"><tr class="headerstyle"><th>Arrest Date</th>
<th>Last Location</th><th>Booking Number</th></tr><tr class="rowstyle"><td>08/23/2026  08:33 PM</td>
<td>BRANCH JAIL</td><td>1182683</td></tr></table>""",
    "offense": """<table id="tabContainer_offense_GridOffense"><tr class="headerstyle"><th>&nbsp;</th><th>Seq</th>
<th>Count</th><th>Citation</th><th>Description</th><th>Statute</th><th>Lvl/Deg</th></tr>
<tr class="rowstyle"><td><img src="minus.png"></td><td>1</td><td>1</td><td>AO6REPE</td>
<td>FLEEING OR ATTEMPTING TO ELUDE LIGHTS AND SIREN ACTIVE</td><td>316.1935(2)</td><td>F / T</td></tr></table>""",
    "docket": """<table id="tabContainer_docket_GridDocket"><tr class="headerstyle"><th>Date</th><th>DIN</th>
<th>Docket Type</th><th>Description</th><th>View</th></tr><tr class="headerstyle"><th><input></th></tr>
<tr class="rowstyle"><td>10/13/2026</td><td><span>12.000</span></td><td>COURT ACTION FORM FILED</td><td></td>
<td><a onclick="viewVorNoQueryString('3234');return false;"><img src="images/file_archive.png"></a></td></tr>
<tr class="rowstyle"><td>08/25/2026</td><td><span>6.000</span></td><td>DRIVING RECORD</td><td></td>
<td><img src="images/file_x.png"></td></tr>
<tr class="rowstyle"><td>08/23/2026</td><td><span>1.000</span></td><td>CHARGING AFFIDAVIT</td><td></td>
<td><a onclick="viewDocNoQueryString('3639','','', this);return false;" href="#"><img src="images/file_r.png"></a></td></tr>
</table>""",
}

CASE = """<html><body><span id="caseNum">2026 105517 CFDL</span><span id="caseTypelbl">FELONY</span>
<span id="tabContainer_summary_name">JOHN ERIC SCHMALZ RIEDT</span>
<span id="tabContainer_summary_DOBvalue">01/21/1961</span>
<span id="tabContainer_summary_filingDate">08/23/2026</span><span id="tabContainer_summary_offenseDate">08/23/2026</span>
<a id="__tab_tabContainer_custody" href="#" onclick="show('custody');return false;">Custody</a>
<a id="__tab_tabContainer_offense" href="#" onclick="show('offense');return false;">Offense</a>
<a id="__tab_tabContainer_docket" href="#" onclick="show('docket');return false;">Docket</a>
<div id="area"></div>
<script>
const grids = %s;
function show(name) { setTimeout(() => { document.getElementById('area').innerHTML = grids[name]; }, 200); }
function viewDocNoQueryString(id) { window.open('/viewDocument.aspx?v1=' + id, '_blank'); }
</script></body></html>"""

VIEWER = """<html><body>Processing...<script>setTimeout(() => { location.href = '/load_Redact.aspx'; }, 600);</script></body></html>"""
PDF = b"%PDF-1.4 arrest affidavit"


class FakePortal:
    """El portal: el aviso hasta aceptarlo, y el PDF una sola vez por documento."""

    def __init__(self):
        self.pdf_served = 0
        self.posts = []

    def handle(self, route):
        import json
        request, url = route.request, route.request.url
        accepted = "ok=1" in (request.headers.get("cookie") or "")
        if url.endswith("/inquiry.aspx"):
            if request.method == "POST":
                self.posts.append(request.post_data)
                return route.fulfill(body=FORM % RESULTS, content_type="text/html")
            return route.fulfill(body=FORM % "" if accepted else DISCLAIMER, content_type="text/html")
        if "caseCR_detail.aspx" in url:
            return route.fulfill(body=CASE % json.dumps(GRIDS), content_type="text/html")
        if "viewDocument.aspx" in url:
            return route.fulfill(body=VIEWER, content_type="text/html")
        if "load_Redact.aspx" in url:
            self.pdf_served += 1
            if self.pdf_served == 1:
                return route.fulfill(body=PDF, content_type="application/pdf")
            return route.fulfill(body="<html><form></form></html>", content_type="text/html")
        return route.fulfill(body=DISCLAIMER, content_type="text/html")


class PortalFlowTest(unittest.TestCase):
    def test_search_case_and_document(self):
        from playwright.sync_api import sync_playwright
        portal = FakePortal()
        with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.route("https://ccms.clerk.org/**", portal.handle)
            adapter = VolusiaCcmsAdapter(context.new_page(), get_county("volusia"), captcha=None)
            adapter.remote = True                  # el fetch dentro de la página (pasa por la copia)
            query = LookupQuery(first_name="John", last_name="Schmalz-Riedt", county="volusia")
            candidates = adapter.search(query, date(2026, 8, 16), date(2026, 10, 22))
            self.assertEqual([c.case_number for c in candidates], ["2026 105517 CFDL"])
            self.assertEqual(candidates[0].defendant, "SCHMALZ RIEDT, JOHN ERIC")
            self.assertIn("from=08%2F16%2F2026", portal.posts[0])
            details = adapter.open_case(candidates[0])
            self.assertEqual(details.candidate.dob, date(1961, 1, 21))
            self.assertEqual([(c.description, c.statute, c.offense_date, c.arrest_date) for c in details.charges],
                             [("FLEEING OR ATTEMPTING TO ELUDE LIGHTS AND SIREN ACTIVE", "316.1935(2)",
                               date(2026, 8, 23), date(2026, 8, 23))])
            documents = adapter.find_documents(details)
            self.assertEqual([d.description for d in documents], ["CHARGING AFFIDAVIT"])
            path = adapter.download(details, documents[0], Path(tmp))
            self.assertEqual(path.read_bytes(), PDF)
            self.assertEqual(portal.pdf_served, 1)          # el visor no gastó la única entrega
            self.assertEqual(path.name, "2026 105517 CFDL - CHARGING AFFIDAVIT.pdf")
            browser.close()


class ReadersTest(unittest.TestCase):
    def test_defendant(self):
        self.assertEqual(defendant_from_style("STATE OF FLORIDA v. JOHN ERIC SCHMALZ RIEDT", "Schmalz-Riedt"),
                         "SCHMALZ RIEDT, JOHN ERIC")
        self.assertEqual(defendant_from_style("STATE OF FLORIDA v. SEBASTIAN KENNETH BOWERS", "Bowers"),
                         "BOWERS, SEBASTIAN KENNETH")
        self.assertEqual(defendant_from_style("STATE OF FLORIDA v. AKRAM BIN AHMED", "Bin Ahmed"),
                         "BIN AHMED, AKRAM")
        self.assertEqual(defendant_from_style("STATE OF FLORIDA v. LOGAN LEVESQUE", "Smith"), "LEVESQUE, LOGAN")

    def test_docket_views(self):
        rows = [
            {"cells": ["08/23/2026", "1.000", "CHARGING AFFIDAVIT", "", ""], "icon": "images/file_r.png",
             "onclick": "viewDocNoQueryString('3639','','', this);return false;"},
            {"cells": ["10/13/2026", "12.000", "ARREST AFFIDAVIT", "", ""], "icon": "images/file_archive.png",
             "onclick": "viewVorNoQueryString('3234');return false;"},
            {"cells": ["08/25/2026", "6.000", "DRIVING RECORD", "", ""], "icon": "images/file_x.png", "onclick": ""},
            {"cells": ["11/10/2026", "16.000", "NOTICE", "OF CANCELLATION", ""], "icon": "images/file.png",
             "onclick": "viewDocNoQueryString('3535','','', this);return false;"},
        ]
        rows.append({"cells": ["10/31/2026", "1.000", "CHARGING AFFIDAVIT", "", ""], "icon": "", "onclick": ""})
        docket = docket_from_rows(rows)
        self.assertEqual([(e.number, e.doc_ref) for e in docket],
                         [(1, "3639"), (12, None), (6, None), (16, "3535"), (1, None)])
        self.assertEqual([e.availability for e in docket], [None, "request", None, None, "unpublished"])
        self.assertEqual(docket[2].description, "DRIVING RECORD (CONFIDENTIAL)")
        self.assertEqual(docket[3].description, "NOTICE - OF CANCELLATION")

    def test_on_request_and_confidential(self):
        from core.models import CaseCandidate, CaseDetails
        adapter = VolusiaCcmsAdapter(page=None, county=get_county("volusia"), captcha=None)
        docket = docket_from_rows([
            {"cells": ["08/23/2026", "1.000", "CHARGING AFFIDAVIT", "", ""], "icon": "images/file_archive.png",
             "onclick": "viewVorNoQueryString('3234');return false;"},
            {"cells": ["08/23/2026", "2.000", "ARREST AFFIDAVIT", "", ""], "icon": "images/file_x.png", "onclick": ""}])
        details = CaseDetails(CaseCandidate("2026 1 CFDL", "X, Y", "CF", "Open", None, None, "x"), [], docket)
        self.assertEqual(adapter.find_documents(details), [])
        self.assertEqual([d.description for d in adapter.find_requestable_documents(details)], ["CHARGING AFFIDAVIT"])


class VolusiaRequestMessageTest(unittest.TestCase):
    """Stripling (11/11/2026): el charging affidavit sin ícono no tiene botón para pedirlo, está
    sin publicar. El de "Viewable on Request" sí se pide."""

    def run_lookup(self, rows):
        import tempfile
        from contextlib import contextmanager
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from core import lookup
        from core.models import CaseCandidate, CaseDetails, Charge
        candidate = CaseCandidate("2026 107137 CFDB", "STRIPLING, CODY MICHAEL", "CF", "Open", None,
                                  date(2026, 10, 31), "x")
        charges = [Charge(date(2026, 10, 23), "AGGRAVATED BATTERY (784.045(1)(a)1)", "784.045(1)(a)1",
                          date(2026, 10, 31), None, None)]
        details = CaseDetails(candidate, charges, docket_from_rows(rows))

        class Fake(VolusiaCcmsAdapter):
            def search(self, query, date_from, date_to):
                return [candidate]

            def open_case(self, c):
                return details

            def throttle(self):
                pass

        @contextmanager
        def fake_browser(headless=False):
            yield SimpleNamespace(page=None, remote=False, live_view_url=None)

        q = LookupQuery(first_name="Cody", last_name="Stripling", county="volusia", arrest_date=date(2026, 10, 31),
                        charges=["aggravated battery"])
        with mock.patch.object(lookup, "open_browser", fake_browser), \
             mock.patch.object(lookup, "get_adapter", return_value=Fake), tempfile.TemporaryDirectory() as tmp:
            return lookup.run_lookup(q, Path(tmp), captcha=object())

    def test_unpublished(self):
        result = self.run_lookup([
            {"cells": ["11/01/2026", "2.000", "FIRST APPEARANCE ACTION FORM FILED", "", ""], "icon": "images/file_x.png",
             "onclick": ""},
            {"cells": ["10/31/2026", "4.000", "CHARGING AFFIDAVIT", "", ""], "icon": "images/file_x.png", "onclick": ""},
            {"cells": ["10/31/2026", "1.000", "CHARGING AFFIDAVIT", "", ""], "icon": "", "onclick": ""}])
        self.assertEqual((result.status, result.case_number), ("no_document", "2026 107137 CFDB"))
        self.assertIn("CHARGING AFFIDAVIT is listed in the case docket", result.message)
        self.assertIn("hasn't put it online yet", result.message)
        self.assertIn("Try again", result.message)
        self.assertNotIn("Request", result.message)

    def test_viewable_on_request(self):
        result = self.run_lookup([
            {"cells": ["10/31/2026", "1.000", "CHARGING AFFIDAVIT", "", ""], "icon": "images/file_archive.png",
             "onclick": "viewVorNoQueryString('3234');return false;"}])
        self.assertEqual(result.status, "no_document")
        self.assertIn("\"Viewable on Request\" icon", result.message)
        self.assertIn("2026 107137 CFDB", result.message)
        self.assertIn("https://ccms.clerk.org/inquiry.aspx", result.message)


class VolusiaConfigTest(unittest.TestCase):
    def test_places_and_agencies(self):
        from core.matcher import normalize_agency
        from counties import florida
        for agency in ("Volusia Sheriff's Office", "Volusia County Sheriff's Office", "Daytona Beach Police Department",
                       "DeLand Police Department", "Port Orange Police Department", "Edgewater Police Department"):
            self.assertEqual(florida.AGENCY_TO_COUNTY[normalize_agency(agency)], "volusia", agency)
        for city in ("daytona beach", "deltona", "deland", "ormond beach", "edgewater", "debary", "holly hill"):
            self.assertEqual(florida.CITY_TO_COUNTY[city], "volusia", city)

    def test_platform(self):
        from adapters import get_adapter
        county = get_county("volusia")
        self.assertIs(get_adapter(county.platform), VolusiaCcmsAdapter)
        self.assertTrue(county.documents_on_request)
        self.assertEqual(county.captcha, "none")


if __name__ == "__main__":
    unittest.main()
