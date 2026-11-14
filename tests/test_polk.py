"""Polk (14/11/2026): ShowCaseWeb contra una copia local del portal.

La copia imita lo que vio probe_polk con Chaneyfield (53-2026-CF-007518-A000-BA): sin sesión pública
(el reCAPTCHA la rechaza), el inicio de sesión con la cuenta, el enlace "Case Search" que no abre y la
lupa que sí, el panel que se vuelve a armar al llegar la configuración de la cuenta, la búsqueda y el
caso en JSON, y el documento que la pestaña pdfframe pide con POST sci/docket/document.
"""

import base64
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

from adapters.showcase_web import (ShowCaseWebAdapter, agency_name, candidates_from_results, case_code,
                                   charges_from_json, docket_from_json, iso_date, manual_search_message)
from core.adapter import SearchError
from core.models import LookupQuery
from counties import get_county

HOST = "https://showcase.polkcountyclerk.net"


def jwt(name: str) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"unique_name": name, "exp": 1794619804}).encode()).decode()
    return "eyJhbGciOiJIUzI1NiJ9." + body.rstrip("=") + ".firma"


PANEL_FIELDS = ["CaseNumber", "LastName", "FirstName", "MiddleName", "CitationNumber", "FromFileDate", "ToFileDate"]
ACCOUNT_FIELDS = PANEL_FIELDS[:4] + ["DateOfBirth", "CitationNumber", "IncidentNumber", "ArrestDate",
                                     "FromFileDate", "ToFileDate"]

APP = """<!DOCTYPE html><html><head><title>ShowCase</title></head><body>
<a class="navbar-toggle navbar-icon" style="display:none" ng-click="toggleSearch('case')" onclick="openPanel()">lupa</a>
<a href="#" ng-click="i.sessionStarted &amp;&amp; toggleSearch('case')" onclick="return false">Case Search</a>
<a href="#" ng-click="showlogin()" onclick="document.getElementById('login_modal').style.display='block';return false">Login</a>
<ul ng-show="i.isAuthenticated" class="ng-hide"><li>Hello</li></ul>
<div id="login_modal" style="display:none"><form onsubmit="login();return false">
  <input id="username" name="username"><input id="password" name="password" type="password">
  <button type="submit">Login</button></form></div>
<div id="caseSearchPanel" style="display:none"><form onsubmit="search();return false"><div id="fields"></div>
  <button type="submit">Search</button></form></div>
<div id="view"></div>
<script>
const FIELDS = %(fields)s, ACCOUNT = %(account)s;
let results = [];
function fields(list) {
  document.getElementById('fields').innerHTML = list.map(m => `<p><input ng-model="searchArgs.${m}"></p>`).join('');
}
fields(FIELDS);
function openPanel() { document.getElementById('caseSearchPanel').style.display = 'block'; }
async function login() {
  const r = await fetch('/showcaseweb/sci/account/authenticate', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username: document.getElementById('username').value, password: document.getElementById('password').value})});
  if (!r.ok) return;
  const data = await r.json();
  sessionStorage.setItem('accessToken', JSON.stringify({accessToken: data.access_token}));
  document.getElementById('login_modal').style.display = 'none';
  document.querySelector('[ng-show="i.isAuthenticated"]').classList.remove('ng-hide');
  // Con la cuenta llega otra configuración y el panel se vuelve a armar (con lo escrito hasta ahí).
  setTimeout(() => {
    const kept = {};
    document.querySelectorAll('#fields input').forEach(i => kept[i.getAttribute('ng-model')] = i.value);
    fields(ACCOUNT);
    document.querySelectorAll('#fields input').forEach(i => i.value = kept[i.getAttribute('ng-model')] || '');
  }, 900);
}
function val(m) { const f = document.querySelector(`input[ng-model="searchArgs.${m}"]`); return f ? f.value : ''; }
async function search() {
  const q = new URLSearchParams({FirstName: val('FirstName'), LastName: val('LastName'), FromFileDate: val('FromFileDate'),
                                 ToFileDate: val('ToFileDate'), MatchType: 0});
  const token = JSON.parse(sessionStorage.getItem('accessToken') || '{}').accessToken || '';
  results = await (await fetch('/showcaseweb/sci/case/search?&' + q, {headers: {Authorization: 'Bearer ' + token}})).json();
  document.getElementById('caseSearchPanel').style.display = 'none';
  history.pushState({}, '', '/showcaseweb/search-results');
  showResults();
}
function showResults() {
  document.getElementById('view').innerHTML = '<div class="dataTables_wrapper"><table>' + results.map((r, i) =>
    `<tr><td><a title="${r.caseNumber}" href="#" ng-click="goToCaseDetails(${i})" onclick="openCase(${i});return false">${r.caseNumber}</a></td></tr>`).join('') + '</table></div>';
}
let dockets = [], sid = '';
async function openCase(i) {
  sid = results[i].sid;
  history.pushState({}, '', '/showcaseweb/casedetails/' + sid);
  dockets = await (await fetch(`/showcaseweb/sci/case/${sid}/dockets`)).json();
  document.getElementById('view').innerHTML = `<a href="#" ng-click="goBack()" onclick="history.pushState({}, '', '/showcaseweb/search-results');showResults();return false">Back to Results</a>
    <div class="scrollmenu"><a href="#" onclick="return false">Dockets</a><a href="#" onclick="tab('charges');return false">Charges</a>
    <a href="#" onclick="tab('arrests');return false">Arrests &amp; Bonds</a></div>
    <div class="dataTables_wrapper"><table>` + dockets.map((d, n) => `<tr><td>${d.hasImages && !d.vorPending
      ? `<button ng-click="getImage(${n})" onclick="getImage(${n})">clip</button>` : d.vorPending ? '<button uib-tooltip="View On Request Pending">lock</button>' : ''}</td>
      <td>${d.docketNumber}</td><td>${d.description}</td></tr>`).join('') + '</table></div>';
}
async function tab(name) { await fetch(`/showcaseweb/sci/case/${sid}/${name}`); }
async function getImage(n) {
  const key = await (await fetch(`/showcaseweb/sci/docket/${sid}/docketsKey?searchable=false`, {method: 'POST',
    body: JSON.stringify([dockets[n].docketID])})).text();
  window.open('/showcaseweb/case/pdfframe?k=' + key);
}
</script></body></html>"""

FRAME = """<!DOCTYPE html><html><head><title>Case Document</title></head><body><object type="application/pdf"></object>
<script>
const token = JSON.parse(sessionStorage.getItem('accessToken') || '{}').accessToken;
fetch('/showcaseweb/sci/docket/document', {method: 'POST', headers: {Authorization: 'Bearer ' + token,
  'Content-Type': 'application/json'}, body: JSON.stringify({Key: new URLSearchParams(location.search).get('k')})})
  .then(r => r.arrayBuffer()).then(b => document.querySelector('object').setAttribute('data',
    URL.createObjectURL(new Blob([b], {type: 'application/pdf'}))));
</script></body></html>"""

RESULTS = [
    {"sid": "Dgl9Js34jN", "caseNumber": "53-2026-CF-007643-A000-BA", "lastName": "Chaneyfield", "firstName": "Timothy",
     "middleName": "", "partyType": "Defendant", "courtType": "CF", "caseStatus": "Disposed", "dob": "1983-12-20T00:00:00",
     "fileDate": "2026-08-03T11:54:00", "offenseDate": "2026-06-22T00:00:00", "arrestDate": "2026-07-31T00:00:00",
     "incidentNumber": "260026306", "bookingSheetNumber": "2623265"},
    {"sid": "jwLg5Hb6vM", "caseNumber": "53-2026-CF-007518-A000-BA", "lastName": "Chaneyfield", "firstName": "Timothy",
     "middleName": "", "partyType": "Defendant", "courtType": "CF", "caseStatus": "Pending", "dob": "1983-12-20T00:00:00",
     "fileDate": "2026-07-29T13:11:00", "offenseDate": "2026-07-28T00:00:00", "arrestDate": "2026-07-31T00:00:00",
     "incidentNumber": "260031164", "bookingSheetNumber": "2623265"},
    {"sid": "zzz", "caseNumber": "53-2025-TR-000001-A000-BA", "lastName": "Chaneyfield", "firstName": "Timothy",
     "middleName": "", "partyType": "Defendant", "courtType": "TR", "caseStatus": "Disposed", "dob": None,
     "fileDate": "2025-01-01T00:00:00", "incidentNumber": ""},
]


def docket_row(docket_id, number, description, images=True, vor_pending=False, sealed=False):
    return {"docketID": docket_id, "docketNumber": number, "effectiveDate": "2026-08-03T11:21:00",
            "description": description, "hasImages": images, "sealed": sealed, "vor": vor_pending,
            "vorPending": vor_pending, "notViewable": False}


DOCKETS = [docket_row(1, 2, "AFFIDAVIT/CITATION/NOTICE TO APPEAR FILED", vor_pending=True),
           docket_row(2, 3, "DIVISION ASSIGNED F2 (CIRCUIT)", images=False),
           docket_row(3, 4, "WARRANT ISSUED", vor_pending=True),
           docket_row(4, 12, "AFFIDAVIT/CITATION/NOTICE TO APPEAR FILED"),
           docket_row(5, 13, "BOOKING SHEET")]
CHARGES = [{"chargeCount": 1, "courtStatuteNumber": "800.04(4A1)", "offenseDate": "2026-07-28T00:00:00",
            "statuteDescription": "LEWD/LASC BATT: VICTIM AGE 12-16",
            "courtStatuteDescription": "LEWD/LASC BATT: VICTIM AGE 12-16"}]
ARRESTS = [{"arrestOffenseDate": "2026-07-31T00:00:00", "agencyName": "PCSO", "agencyNumber": "PCSO",
            "bookingNumber": "", "incidentNumber": "260031164"}]
PDF = b"%PDF-1.4 affidavit #12"


class FakePortal:
    def __init__(self, username="user@example.com", password="secret"):
        self.username, self.password = username, password
        self.searches = []
        self.keys = {}

    def handle(self, route):
        request = route.request
        path = urlparse(request.url).path
        js = lambda data, status=200: route.fulfill(status=status, body=json.dumps(data), content_type="application/json")
        if path.endswith("/sci/account/validate-captcha"):
            return route.fulfill(status=401, body="")
        if path.endswith("/sci/account/authenticate"):
            body = json.loads(request.post_data or "{}")
            if (body.get("username"), body.get("password")) != (self.username, self.password):
                return js({"error": "invalid"}, 400)
            return js({"access_token": jwt(self.username), "expires_in": 3600})
        if path.endswith("/sci/case/search"):
            self.searches.append(parse_qs(urlparse(request.url).query))
            return js(RESULTS)
        if path.endswith("/dockets"):
            return js(DOCKETS)
        if path.endswith("/charges"):
            return js(CHARGES)
        if path.endswith("/arrests"):
            return js(ARRESTS)
        if "/docketsKey" in path:
            docket_id = json.loads(request.post_data)[0]
            self.keys[f"K{docket_id}"] = docket_id
            return route.fulfill(body=f"K{docket_id}", content_type="text/plain")
        if path.endswith("/case/pdfframe"):
            return route.fulfill(body=FRAME, content_type="text/html")
        if path.endswith("/sci/docket/document"):
            key = json.loads(request.post_data)["Key"]
            assert self.keys.get(key) == 4, key
            return route.fulfill(body=PDF, content_type="application/pdf")
        return route.fulfill(body=APP % {"fields": json.dumps(PANEL_FIELDS), "account": json.dumps(ACCOUNT_FIELDS)},
                             content_type="text/html")


class PortalFlowTest(unittest.TestCase):
    def run_portal(self, env, test):
        from playwright.sync_api import sync_playwright
        portal = FakePortal()
        with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env):
            browser = p.chromium.launch()
            context = browser.new_context()
            context.route(f"{HOST}/**", portal.handle)
            adapter = ShowCaseWebAdapter(context.new_page(), get_county("polk"), captcha=None)
            try:
                test(adapter, portal, Path(tmp))
            finally:
                browser.close()

    def test_with_account(self):
        def test(adapter, portal, tmp):
            query = LookupQuery(first_name="Timothy", last_name="Chaneyfield", county="polk")
            with mock.patch("adapters.showcase_web.PUBLIC_SESSION_WAIT_S", 1):
                candidates = adapter.search(query, date(2026, 7, 20), date(2026, 8, 20))
            self.assertEqual([c.case_number for c in candidates],
                             ["53-2026-CF-007643-A000-BA", "53-2026-CF-007518-A000-BA"])
            sent = portal.searches[0]
            self.assertEqual((sent["LastName"], sent["FirstName"], sent["FromFileDate"], sent["ToFileDate"]),
                             (["Chaneyfield"], ["Timothy"], ["07/20/2026"], ["08/20/2026"]))
            self.assertEqual((candidates[1].defendant, candidates[1].dob), ("CHANEYFIELD, TIMOTHY", date(1983, 12, 20)))
            details = adapter.open_case(candidates[1])
            self.assertEqual(details.agency_report_number, "260031164")
            self.assertEqual([(c.description, c.statute, c.arrest_date, c.agency) for c in details.charges],
                             [("LEWD/LASC BATT: VICTIM AGE 12-16", "800.04(4A1)", date(2026, 7, 31),
                               "Polk County Sheriff's Office")])
            documents = adapter.find_documents(details)
            self.assertEqual([(d.number, d.description) for d in documents],
                             [(12, "AFFIDAVIT/CITATION/NOTICE TO APPEAR FILED")])
            path = adapter.download(details, documents[0], tmp)
            self.assertEqual(path.read_bytes(), PDF)
            # Otro caso de la misma búsqueda: vuelve a los resultados y lo abre.
            other = adapter.open_case(candidates[0])
            self.assertEqual(other.candidate.case_number, "53-2026-CF-007643-A000-BA")
        self.run_portal({"POLK_SHOWCASE_USERNAME": "user@example.com", "POLK_SHOWCASE_PASSWORD": "secret"}, test)

    def test_without_account_is_manual(self):
        def test(adapter, portal, tmp):
            query = LookupQuery(first_name="Timothy", last_name="Chaneyfield", county="polk")
            with mock.patch("adapters.showcase_web.PUBLIC_SESSION_WAIT_S", 1), self.assertRaises(SearchError) as caught:
                adapter.search(query, date(2026, 7, 20), date(2026, 8, 20))
            message = str(caught.exception)
            self.assertIn("reCAPTCHA", message)
            self.assertIn('Last Name "Chaneyfield", First Name "Timothy", cases filed 07/20/2026 to 08/20/2026', message)
            self.assertIn("https://showcase.polkcountyclerk.net/showcaseweb/", message)
            self.assertEqual(portal.searches, [])
        self.run_portal({"POLK_SHOWCASE_USERNAME": "", "POLK_SHOWCASE_PASSWORD": ""}, test)

    def test_wrong_password_is_manual(self):
        def test(adapter, portal, tmp):
            query = LookupQuery(first_name="Timothy", last_name="Chaneyfield", county="polk")
            with mock.patch("adapters.showcase_web.PUBLIC_SESSION_WAIT_S", 1), self.assertRaises(SearchError) as caught:
                adapter.search(query, None, None)
            self.assertIn("didn't accept the Clerk account", str(caught.exception))
        with mock.patch("adapters.showcase_web.LOGIN_WAIT_S", 3):
            self.run_portal({"POLK_SHOWCASE_USERNAME": "user@example.com", "POLK_SHOWCASE_PASSWORD": "wrong"}, test)


class ReadersTest(unittest.TestCase):
    def test_results(self):
        candidates, skipped, extra = candidates_from_results(RESULTS, {"CF", "MM", "CT"})
        self.assertEqual([c.case_number for c in candidates], ["53-2026-CF-007643-A000-BA", "53-2026-CF-007518-A000-BA"])
        self.assertEqual(skipped, ["53-2025-TR-000001-A000-BA (TR, Defendant)"])
        self.assertEqual(extra["jwLg5Hb6vM"], {"incident": "260031164", "arrest": date(2026, 7, 31),
                                              "offense": date(2026, 7, 28), "booking": "2623265"})
        self.assertEqual((candidates[0].filed_date, candidates[0].detail_ref), (date(2026, 8, 3), "Dgl9Js34jN"))

    def test_docket(self):
        rows = DOCKETS + [docket_row(6, 20, "SEALED ORDER", sealed=True)]
        docket = docket_from_json(rows)
        self.assertEqual([(e.number, e.doc_ref, e.availability) for e in docket],
                         [(2, None, "request"), (3, None, "unpublished"), (4, None, "request"), (12, "3", None),
                          (13, "4", None), (20, None, None)])
        self.assertEqual(docket[-1].description, "SEALED ORDER (SEALED)")

    def test_charges_and_helpers(self):
        charges = charges_from_json(CHARGES, date(2026, 7, 31), "Polk County Sheriff's Office", "260031164")
        self.assertEqual((charges[0].offense_date, charges[0].control_number), (date(2026, 7, 28), "260031164"))
        self.assertEqual(case_code("53-2026-CF-007518-A000-BA"), "CF")
        self.assertEqual(case_code("weird", "MM"), "MM")
        self.assertEqual(agency_name("PCSO"), "Polk County Sheriff's Office")
        self.assertEqual(agency_name("XYZPD"), "XYZPD")
        self.assertIsNone(agency_name(""))
        self.assertEqual(iso_date("2026-07-31T00:00:00"), date(2026, 7, 31))
        self.assertIsNone(iso_date(None))
        query = LookupQuery(first_name="Timothy", last_name="Chaneyfield", county="polk")
        self.assertNotIn("cases filed", manual_search_message("Polk County", "https://x/", query, None, None, "is down"))


class PolkConfigTest(unittest.TestCase):
    def test_config(self):
        county = get_county("polk")
        self.assertEqual((county.platform, county.documents_on_request), ("showcase_web", True))
        self.assertIn("request", county.request_messages)

    def test_places(self):
        from counties.florida import AGENCY_TO_COUNTY, CITY_TO_COUNTY
        for city in ("lakeland", "winter haven", "bartow", "lake wales", "auburndale", "eagle lake"):
            self.assertEqual(CITY_TO_COUNTY[city], "polk", city)
        for agency in ("polk county so", "lakeland pd", "winter haven pd", "bartow pd"):
            self.assertEqual(AGENCY_TO_COUNTY[agency], "polk", agency)


if __name__ == "__main__":
    unittest.main()
