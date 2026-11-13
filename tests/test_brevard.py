"""Brevard (13/11/2026): BECA (vmatrix1.brevardclerk.us/beca) contra una copia local del portal.

La copia imita lo que vio probe_brevard con Thomason (05-2026-MM-052463-AXXX-BC): el aviso que hay
que aceptar, el formulario que abre los resultados en una ventana nueva, la tabla de resultados
(con fecha de nacimiento), el caso con la "Charging Agency" (el número de reporte al pasar el mouse)
y el Register of Actions, y get_document.cfm, que entrega el PDF directo.
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs

from adapters.brevard_beca import (BrevardBecaAdapter, case_code, defendant_from_party, docket_from_rows,
                                   report_from_agency)
from core.models import LookupQuery
from counties import get_county

SPLASH = """<html><body><form name="myform" action="StartSearch.cfm" method="post">
<input type="radio" name="RadioChk" value="Yes" checked>Accept <input type="radio" name="RadioChk" value="No">Do Not Accept
<input type="submit" name="Submit" value="Submit"></form></body></html>"""

START = """<html><body><button type="button" onclick="window.location.href='name_search.cfm'">General Public
Court Records Search</button></body></html>"""

FORM = """<html><body><form name="searchform" action="Name_Display.cfm" method="post" target="_blank">
First Name: <select name="fkey"><option>Begins with</option><option>Exactly</option></select><input type="text" name="fname">
Middle Name: <select name="mkey"><option>Exactly</option></select><input type="text" name="mname">
Last Name: <select name="lkey"><option>Exactly</option></select><input type="text" name="lname">
Date of Birth: <input type="text" name="dob">
From: <input type="text" name="fDate" id="fDate" value="11/12/2025"> To: <input type="text" name="tDate" id="tDate" value="11/13/2026">
<input type="checkbox" name="CatChk" value="CT">Criminal Traffic <input type="checkbox" name="CatChk" value="MM">Misdemeanor
<input type="checkbox" name="CatChk" value="CF">Felony <input type="checkbox" name="CatChk" value="TR">Traffic
<input type="submit" name="submit" value="Search"></form></body></html>"""

RESULT = """<br><table class="TFtable"><tbody><tr><th>Case Number</th><th>Party Name</th><th>Party Type</th>
<th>Case Title</th><th>Case Type</th><th>Filing Date</th><th>Case Status</th><th>DOB</th><th>Amount Due</th></tr>
<tr><td><a href="all_results.cfm?x={x}">{num}            </a></td><td>{party}       </td><td>{ptype}</td>
<td>STATE VS THOMASON ARGENTINA</td><td>{ctype}</td><td>{filed}</td><td>ORIGINAL PENDING</td><td>10/30/1971</td>
<td>0.00</td></tr></tbody></table><table class="t1"><tbody><tr><td>1</td><td>10/03/2026</td>
<td>PETIT THEFT PROPERTY VALUED AT 100 TO 750 DOLLARS</td></tr></tbody></table>"""

RESULTS = ("<html><body><h4>Name Search - ARGENTINA% THOMASON</h4>"
           + RESULT.format(x="AAA1", num="05-2026-MM-052463-AXXX-BC", party="THOMASON ARGENTINA L",
                           ptype="DEFENDANT (1)", ctype="MISDEMEANOR", filed="10/03/2026")
           + RESULT.format(x="AAA2", num="05-2026-TR-004643-AXXX-BC", party="THOMASON ARGENTINA L",
                           ptype="DEFENDANT (1)", ctype="TRAFFIC INFRACTION", filed="01/30/2026")
           + RESULT.format(x="AAA3", num="05-2025-MM-011111-AXXX-BC", party="THOMASON ARGENTINA L",
                           ptype="VICTIM (1)", ctype="MISDEMEANOR", filed="03/02/2025")
           + "<h4>3 Current Cases Returned</h4><input type='submit' value='Close' onclick='window.close()'></body></html>")

CASE = """<html><body><h5>STATE VS THOMASON ARGENTINA   </h5>
<table class="TFtable"><tbody><tr><th>Case Number</th><th>Case Type</th><th>Filing Date</th><th>Case Status</th>
<th>Status Date</th><th>Judge</th><th>Jurisdiction</th><th>Charging Agency</th></tr>
<tr><td><div class="m">05-2026-MM-052463-AXXX-BC <span>File Location - /CLERK/TITUSVILLE csid: 1</span></div></td>
<td>MISDEMEANOR</td><td>10/03/2026</td><td>ORIGINAL PENDING</td><td>10/03/2026</td><td>JUDGE</td><td>TITUSVILLE</td>
<td><div class="l">BREVARD COUNTY SHERIFF         <span>SANTANA LUCIA                 SO5247    202600304762   </span></div></td>
</tr></tbody></table>
<a name="participants"></a><table class="TFtable"><tbody><tr><th>Type</th><th>Name</th><th>Participant Id</th>
<th>DL Number</th><th>Race</th><th>Gender</th><th>DOB</th></tr>
<tr><td>DEFENDANT (1)</td><td>THOMASON ARGENTINA L</td><td>1</td><td></td><td></td><td>FEMALE</td><td>10/30/1971</td></tr>
</tbody></table>
<a name="docket"></a><table class="TFtable"><tbody><tr><th>View</th><th>Event Date</th><th>Document Number</th>
<th>Description</th><th>Page Count</th><th>Amount</th></tr></tbody></table>
<table id="tbl-content" class="TFtable"><tbody>
<tr><td><a title="Viewable " href="get_document.cfm?doc_num=DOC-1" target="_blank"><img src="images/file.png"></a></td>
<td><div class="m">10/03/2026<span>EFILE-Deputy X-10/03/2026 11:34 PM</span></div></td><td>1</td>
<td><div class="m">ARREST AFFIDAVIT          <span>Event Code 2700-Event Seq 1-Barcode ID 1</span></div></td><td>3</td><td></td></tr>
<tr><td></td><td><div class="m">10/04/2026<span>Deputy</span></div></td><td></td>
<td><div class="m">CAL:ADD-ON INITIAL APPEARANCE<span>Event Code 3967</span></div></td><td></td><td></td></tr>
<tr><td><a title="Viewable " href="get_document.cfm?doc_num=DOC-2" target="_blank"><img src="images/file.png"></a></td>
<td><div class="m">10/04/2026<span>Deputy</span></div></td><td>2</td>
<td><div class="m">PROBABLE CAUSE ORDER<span>Event Code 10291</span></div></td><td>1</td><td></td></tr>
<tr><td></td><td><div class="m">10/04/2026<span>AUTO</span></div></td><td></td>
<td><div class="m">BOND POSTED                - (Count 1)<span>Event Code 5540</span></div></td><td></td><td>0.00</td></tr>
<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>Total Number of Document Pages</td><td>4</td><td></td></tr>
</tbody></table>
<a name="charges"></a><table class="TFtable"><tbody><tr><th>Arrest Number</th><th>Count</th><th>Offense Date</th>
<th>Florida Statute</th><th>Charge Description</th><th>Class</th><th>Degree</th></tr>
<tr><td>1</td><td>1</td><td>10/03/2026</td><td>812.014.2.F         </td>
<td>PETIT THEFT PROPERTY VALUED AT 100 TO 750 DOLLARS </td><td>MISDEMEANOR</td><td>FIRST DEGREE</td></tr>
</tbody></table></body></html>"""

PDF = b"%PDF-1.4 arrest affidavit"


class FakePortal:
    """El portal: sin aceptar el aviso, name_search.cfm vuelve al aviso."""

    def __init__(self):
        self.posts = []
        self.documents = 0

    def handle(self, route):
        request, url = route.request, route.request.url
        accepted = "ok=1" in (request.headers.get("cookie") or "")
        page = url.split("/beca/")[-1].split("?")[0].lower()
        html = lambda body, **kw: route.fulfill(body=body, content_type="text/html", **kw)
        if page == "startsearch.cfm":
            return html(START, headers={"Set-Cookie": "ok=1; Path=/"})
        if page == "name_search.cfm":
            return html(FORM if accepted else SPLASH)
        if page == "name_display.cfm":
            self.posts.append(parse_qs(request.post_data or ""))
            return html(RESULTS)
        if page == "all_results.cfm" and accepted:
            return html(CASE)
        if page == "get_document.cfm" and accepted:
            self.documents += 1
            return route.fulfill(body=PDF, content_type="application/pdf;charset=UTF-8")
        return html(SPLASH)


class PortalFlowTest(unittest.TestCase):
    def test_search_case_and_document(self):
        from playwright.sync_api import sync_playwright
        portal = FakePortal()
        with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.route("https://vmatrix1.brevardclerk.us/**", portal.handle)
            adapter = BrevardBecaAdapter(context.new_page(), get_county("brevard"), captcha=None)
            adapter.remote = True                  # el fetch dentro de la página (pasa por la copia)
            query = LookupQuery(first_name="Argentina", last_name="Thomason", county="brevard")
            candidates = adapter.search(query, date(2026, 10, 1), date(2026, 10, 10))
            self.assertEqual([c.case_number for c in candidates], ["05-2026-MM-052463-AXXX-BC"])
            self.assertEqual((candidates[0].defendant, candidates[0].dob, candidates[0].case_type),
                             ("THOMASON, ARGENTINA L", date(1971, 10, 30), "MM"))
            post = portal.posts[0]
            self.assertEqual((post["lname"], post["fname"], post["fDate"], post["tDate"]),
                             (["Thomason"], ["Argentina"], ["10/01/2026"], ["10/10/2026"]))
            self.assertEqual(sorted(post["CatChk"]), ["CF", "CT", "MM"])
            details = adapter.open_case(candidates[0])
            self.assertEqual(details.agency_report_number, "202600304762")
            self.assertEqual([(c.description, c.statute, c.offense_date, c.agency) for c in details.charges],
                             [("PETIT THEFT PROPERTY VALUED AT 100 TO 750 DOLLARS", "812.014.2.F", date(2026, 10, 3),
                               "BREVARD COUNTY SHERIFF")])
            documents = adapter.find_documents(details)
            self.assertEqual([d.description for d in documents], ["ARREST AFFIDAVIT"])
            path = adapter.download(details, documents[0], Path(tmp))
            self.assertEqual(path.read_bytes(), PDF)
            self.assertEqual(path.name, "05-2026-MM-052463-AXXX-BC - ARREST AFFIDAVIT.pdf")
            browser.close()


class ReadersTest(unittest.TestCase):
    def test_defendant(self):
        self.assertEqual(defendant_from_party("THOMASON ARGENTINA L", "Thomason"), "THOMASON, ARGENTINA L")
        self.assertEqual(defendant_from_party("SCHMALZ RIEDT JOHN ERIC", "Schmalz-Riedt"), "SCHMALZ RIEDT, JOHN ERIC")
        self.assertEqual(defendant_from_party("DAVIS CALEB", "Davies"), "DAVIS, CALEB")

    def test_case_code(self):
        self.assertEqual(case_code("05-2026-MM-052463-AXXX-BC"), "MM")
        self.assertEqual(case_code("05-2026-CF-012345-AXXX-XX"), "CF")
        self.assertEqual(case_code("2026 105517 CFDL"), "")

    def test_report(self):
        self.assertEqual(report_from_agency("SANTANA LUCIA                 SO5247    202600304762   "), "202600304762")
        self.assertIsNone(report_from_agency("SANTANA LUCIA                 SO5247"))
        self.assertIsNone(report_from_agency(""))

    def test_docket_icons(self):
        row = lambda icon, number, text, href="": {"cells": ["", "10/03/2026", number, text, "3", ""],
                                                   "icon": icon, "href": href}
        docket = docket_from_rows([
            row("images/file.png", "1", "ARREST AFFIDAVIT", "get_document.cfm?doc_num=A"),
            row("images/file_vor.png", "2", "PROBABLE CAUSE AFFIDAVIT"),
            row("images/clock.png", "3", "ARREST AFFIDAVIT"),
            row("images/file-c.png", "4", "ARREST AFFIDAVIT"),
            row("images/file-s.png", "5", "ARREST AFFIDAVIT"),
            row("", "", "BOND POSTED                - (Count 1)"),
        ])
        self.assertEqual([(e.number, e.description, e.availability) for e in docket], [
            (1, "ARREST AFFIDAVIT", None), (2, "PROBABLE CAUSE AFFIDAVIT", "request"),
            (3, "ARREST AFFIDAVIT", "unpublished"), (4, "ARREST AFFIDAVIT (CONFIDENTIAL)", None),
            (5, "ARREST AFFIDAVIT (SEALED)", None), (None, "BOND POSTED", None)])
        self.assertEqual(docket[0].doc_ref, "https://vmatrix1.brevardclerk.us/beca/get_document.cfm?doc_num=A")
        self.assertEqual([e.doc_ref for e in docket[1:]], [None] * 5)


class BrevardConfigTest(unittest.TestCase):
    def test_config(self):
        county = get_county("brevard")
        self.assertEqual((county.platform, county.captcha, county.documents_on_request),
                         ("brevard_beca", "none", True))

    def test_places(self):
        from counties.florida import AGENCY_TO_COUNTY as AGENCY_COUNTY, CITY_TO_COUNTY as CITY_COUNTY
        for city in ("melbourne", "palm bay", "titusville", "rockledge", "merritt island", "viera", "west melbourne"):
            self.assertEqual(CITY_COUNTY[city], "brevard", city)
        for agency in ("brevard county so", "melbourne pd", "palm bay pd", "cocoa beach pd"):
            self.assertEqual(AGENCY_COUNTY[agency], "brevard", agency)


if __name__ == "__main__":
    unittest.main()
