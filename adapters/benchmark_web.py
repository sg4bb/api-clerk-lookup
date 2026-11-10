"""Adaptador de BenchmarkWeb (Pioneer Technology Group / Catalis).

Comprobado en Osceola (01/11/2026, versión 2.9.46.0):

- Búsqueda anónima por nombre ("Apellido, Nombre"), sin cuenta y sin CAPTCHA
  (la página carga el script de reCAPTCHA; si algún día lo muestra, lo resuelve
  una persona). El formulario trae campos ocultos de fechas de apertura
  (openedFrom / openedTo) que el portal respeta aunque no se vean.
- Resultados: tabla DataTables llenada por una consulta JSON, de todos los tipos
  de caso y de parte (DEFENDANT, PLAINTIFF, ATTORNEY...). Si hay un solo caso, el
  portal salta directo a su página.
- El resumen desplegable de cada caso (CourtCase.aspx/CaseThumbnail) trae la
  agencia, el "Agency Report #" y los cargos con "Offense Date" e "Initial Status Date".
- El docket marca "DATE OF ARREST". Cada documento tiene un id y un digest; el PDF se
  pide en dos pasos: ImageAsync.aspx/GetPDFRequestGuid -> guid -> ImageAsync.aspx/GetPDF.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote, urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from core.adapter import ClerkAdapter, DocumentUnavailable, DownloadError, SearchError
from core.captcha import CLOUDFLARE_PAGE_JS, CLOUDFLARE_PASSED_JS, CaptchaTimeout
from core.models import CaseCandidate, CaseDetails, Charge, DocketEntry, LookupQuery
from core.documents import is_pdf
from core.utils import parse_us_date, portal_text, safe_filename

log = logging.getLogger(__name__)

# "2026 CF 003520" o "2021 CC 002132 EV": año, tipo de corte, número y, a veces, división
# (cualquier sufijo: no se usa).
_CASE_RE = re.compile(r"^\s*(\d{4})\s+([A-Z]{2})\s+(\d{6})\b")
# Otros formatos de número de caso según el condado, todos con el tipo de corte:
#   St. Johns: "26001446CFMA" (año de 2 cifras + 6 cifras + tipo + división), "26009995TR",
#              "CC26-5468" / "CA11-1911" (civiles);  formato Civitek: "2026CF000052AXMA".
_CASE_FORMATS = (
    _CASE_RE,
    re.compile(r"^\s*()\d{8}([A-Z]{2})"),
    re.compile(r"^\s*()\d{8}([A-Z])(?![A-Z])"),   # Charlotte: 26001343F (F = felony)
    re.compile(r"^\s*()\d{4}([A-Z]{2})\d{6}"),
    re.compile(r"^\s*()([A-Z]{2})\d{2}-\d+"),
)
_BIRTH_YEAR_RE = re.compile(r"\((\d{4})\)")
_DATE = r"(\d{1,2}/\d{1,2}/\d{4})"
MAX_PAGES = 5            # de 100 filas; con el rango de fechas casi nunca pasa de una
RESULTS_TIMEOUT_MS = 30_000

_RESULTS_STATE_JS = r"""
() => {
  if (/\|\s*52\d\s*:/.test(document.title) || /error code 52\d/i.test((document.body || {}).innerText || '')) return 'cf_error';
  if (location.href.indexOf('/CourtCase.aspx/Details/') >= 0) return 'case';
  // Flagler: sin CAPTCHA resuelto vuelve al formulario con "?error=Invalid Captcha".
  if (/home\.aspx\/search/i.test(location.pathname) && /captcha/i.test(decodeURIComponent(location.search))) return 'captcha';
  // De nuevo en el formulario (página nueva, sin la marca que se pone al enviar).
  if (location.href.toLowerCase().indexOf('/home.aspx/search') >= 0 && !window.__clerkSubmitted && document.querySelector('#name')) return 'form';
  const box = Array.from(document.querySelectorAll("iframe[src*='recaptcha']"))
    .find(f => f.offsetWidth > 50 && f.offsetHeight > 50 && !/size=invisible/.test(f.src));
  if (box && !document.querySelector('#gridSearchResults')) return 'captcha';
  // "Cases Found 0": el portal no dibuja la tabla (Espinosa y McMilliann, 01/11/2026).
  const found = document.querySelector('.caseFoundFilter');
  if (found && /(^|\s)0\s*$/.test(found.innerText.trim())) return 'empty';
  const grid = document.querySelector('#gridSearchResults');
  if (!grid) return '';
  const processing = document.querySelector('#gridSearchResults_processing');
  if (processing && getComputedStyle(processing).display !== 'none') return '';
  if (grid.querySelector('td.dataTables_empty')) return 'empty';
  return grid.querySelector("a[title^='View Case Details for']") ? 'rows' : '';
}
"""

_READ_ROWS_JS = """
() => {
  // Columnas por su clase en el encabezado: Bay (BenchmarkWeb2) agrega "PIN" antes de "Party Type"
  // (06/11/2026: se leía el PIN como tipo de parte y quedaban todos fuera). Sin encabezado, las de siempre.
  const heads = Array.from(document.querySelectorAll('#gridSearchResults thead th'));
  const col = (cls, dflt) => { const i = heads.findIndex(th => th.classList.contains(cls)); return i >= 0 ? i : dflt; };
  const nameCol = col('nameCol', 1), partyCol = col('partyTypeCol', 2), statusCol = col('statusCol', 4);
  return Array.from(document.querySelectorAll('#gridSearchResults tbody tr'))
  .filter(tr => tr.querySelector("a[title^='View Case Details for']"))
  .map(tr => {
    const tds = tr.querySelectorAll(':scope > td');
    const link = tr.querySelector("a[title^='View Case Details for']");
    const name = tr.querySelector("a[title^='View Party Details for']");
    const expand = tr.querySelector("img[onclick*='details(']");
    return {
      case: link.innerText.trim(), href: link.getAttribute('href'),
      name: name ? name.innerText.trim() : '', nameCell: tds[nameCol] ? tds[nameCol].innerText : '',
      party: tds[partyCol] ? tds[partyCol].innerText.trim() : '',
      status: tds[statusCol] ? tds[statusCol].innerText.trim() : '',
      thumb: expand ? expand.getAttribute('onclick') : '',
    };
  });
}
"""

_CAPTCHA_KIND_JS = """
() => (document.querySelector("iframe[src*='hcaptcha'], .h-captcha, [name='h-captcha-response']") ? 'hcaptcha'
       : document.querySelector("iframe[src*='recaptcha'], .g-recaptcha") ? 'recaptcha' : null)
"""

# Recuadro del CAPTCHA ya dibujado por Google (no solo el lugar donde va).
_CAPTCHA_DRAWN_JS = """
() => !!document.querySelector("iframe[src*='recaptcha/api2/anchor'], iframe[src*='recaptcha/enterprise/anchor'], iframe[src*='hcaptcha']")
"""

# Clay (08/11/2026) mete el div del reCAPTCHA con jQuery al cargar la página; si el script de Google
# terminó antes, el div queda vacío para siempre. Esto le pide a ese mismo script que lo dibuje, con
# la clave del propio div: lo que hace la página cuando el orden le sale bien. Lo resuelve la persona.
_CAPTCHA_RENDER_JS = """
() => {
  const slot = document.querySelector('#WebCaptcha .g-recaptcha[data-sitekey], .g-recaptcha[data-sitekey]');
  if (!slot) return 'sin lugar';
  if (slot.querySelector('iframe')) return 'ya estaba';
  if (!(window.grecaptcha && grecaptcha.render)) return 'sin script de Google';
  try { grecaptcha.render(slot); return 'pedido'; } catch (e) { return 'error: ' + e.message; }
}
"""

# CAPTCHA de texto (St. Lucie): la imagen con la suma y el campo de la respuesta.
_TEXT_CAPTCHA_JS = """
() => !!document.querySelector("input[name='captcha']")
   && !!document.querySelector("img[alt='Captcha'], img[src*='CaptchaImage']")
"""
# Intentos con el CAPTCHA de texto: una respuesta mal escrita trae una suma nueva.
TEXT_CAPTCHA_TRIES = 3

# Firma de las filas visibles: cambia cuando la tabla se vuelve a dibujar (el portal no
# muestra el "Showing 1 to N of M" de DataTables).
_ROWS_SIGNATURE_JS = """
() => Array.from(document.querySelectorAll("#gridSearchResults a[title^='View Case Details for']"))
  .map(a => a.innerText.trim()).join('|')
"""

# "Cases Found N" del encabezado de resultados.
_CASES_FOUND_JS = r"""
() => { const el = document.querySelector('.caseFoundFilter');
        const m = el ? el.innerText.match(/(\d+)\s*$/) : null; return m ? parseInt(m[1], 10) : null; }
"""

_CASE_READY_JS = """
() => !!document.querySelector('#gridDockets') ||
      /no (case )?dockets|dockets are currently unavailable/i.test((document.querySelector('#courtCaseDockets') || {}).innerText || '')
"""

_CASE_SUMMARY_JS = """
() => !!(document.querySelector('#summaryAccordionCollapse dd.casenumber, #summaryAccordionCollapse .caseSummaryCol') &&
         document.querySelector('#gridParties tbody tr'))
"""

# Cloudflare sin respuesta del servidor del condado (Bay, 06/11/2026: "522: Connection timed out").
_CF_ERROR_JS = r"""
() => /\|\s*52\d\s*:/.test(document.title) || /error code 52\d/i.test((document.body || {}).innerText || '')
"""

# Escambia (10/11/2026): recién pasada la verificación de Cloudflare, el portal carga sin sus scripts,
# sin estilos o sin la imagen de la suma (403). Devuelve lo que falta, o '' si la página está completa.
_INCOMPLETE_JS = """
() => {
  const img = document.querySelector("img[alt='Captcha'], img[src*='CaptchaImage']");
  if (img && !(img.complete && img.naturalWidth > 0)) return 'the sum image';
  if (!window.jQuery) return 'jQuery';
  if (getComputedStyle(document.body).fontFamily.indexOf('Times') >= 0) return 'styles';
  return '';
}
"""
# Segundos que se espera a que la verificación de Cloudflare pase sola antes de pedírsela a la persona
# (Escambia, 10/11/2026: pasó sola en 30 s).
CLOUDFLARE_SELF_PASS_S = 45
# Tiempo para que la persona marque la casilla de Cloudflare, si aparece (en Escambia no apareció:
# el círculo gira y nada más). Corto: cada minuto de Browserbase cuesta.
CLOUDFLARE_PERSON_S = 90
# Pausa antes de cada recarga del portal incompleto (el paso 2 abre el recurso negado como página).
# Tres cargas en ~1 minuto: más no sirvió (Rodriguez, 10/11/2026: 5 cargas en 2 minutos, nada) y
# cada minuto de Browserbase cuesta.
CLOUDFLARE_GATE_PAUSES_S = (8, 3, 15)


class CloudflareBlocked(SearchError):
    """Cloudflare no deja cargar el portal al navegador de búsqueda (Escambia). search() lo cambia
    por un mensaje con lo necesario para buscar a mano."""

# BenchmarkWeb2 (Bay, 2.9.7): la página del caso trae solo el marco; el resumen
# (CourtCase.aspx/DetailsSummary/<cid>) y el docket (CourtCase.aspx/CaseDockets/<cid>) los pide
# casedetails.js, que a veces vuelve con 522 y la página queda en el ícono de carga. Se piden
# directo, con las mismas variables de la página (cid, caseDigest), y se dejan donde los pondría.
_BW2_LOAD_JS = """
async (base) => {
  if (typeof cid === 'undefined' || typeof caseDigest === 'undefined') return {ok: false, why: 'sin cid'};
  const get = async (path) => {
    let r;
    for (let i = 0; i < 3; i++) {
      try {
        r = await fetch(base + path + cid + '?digest=' + encodeURIComponent(caseDigest) + '&time=' + Date.now(),
                        {credentials: 'include'});
        if (r.status === 200) return await r.text();
      } catch (e) {}
      await new Promise(ok => setTimeout(ok, 2000));
    }
    return null;
  };
  const summary = await get('CourtCase.aspx/DetailsSummary/');
  if (summary === null) return {ok: false, why: 'resumen'};
  const box = document.querySelector('#detailsContent');
  box.innerHTML = summary;
  const loading = document.querySelector('#loading');
  if (loading) loading.style.display = 'none';
  const dockets = await get('CourtCase.aspx/CaseDockets/');
  if (dockets === null) return {ok: false, why: 'docket'};
  const holder = document.querySelector('#courtCaseDockets');
  if (holder) holder.innerHTML = dockets; else box.insertAdjacentHTML('beforeend', dockets);
  return {ok: true};
}
"""

_READ_CASE_JS = """
() => {
  const text = el => el ? el.innerText.replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim() : '';
  const summary = {};
  document.querySelectorAll('#summaryAccordionCollapse dd').forEach(dd => {
    summary[(dd.className || '').trim()] = text(dd);
  });
  // BenchmarkWeb2 (Bay): pares "Etiqueta:" / valor en .caseSummaryCol, sin <dd>. Se guardan con la
  // misma clave que las clases de los <dd> ("Agency Report Number" -> "agencyreportnumber").
  document.querySelectorAll('#summaryAccordionCollapse .caseSummaryCol > div').forEach(row => {
    const cells = row.querySelectorAll(':scope > div');
    if (cells.length < 2) return;
    const key = text(cells[0]).toLowerCase().replace(/[^a-z0-9]/g, '');
    if (key && !(key in summary)) summary[key] = text(cells[cells.length - 1]);
  });
  const parties = Array.from(document.querySelectorAll('#gridParties tbody tr')).map(tr => {
    const tds = tr.querySelectorAll(':scope > td');
    const a = tds[1] ? tds[1].querySelector("a[title^='View Party Details for']") : null;
    return {type: text(tds[0]), name: a ? text(a) : text(tds[1])};
  });
  // Columna del número de entrada: la primera en casi todos; Indian River pone antes la de la
  // imagen ("Image | DIN | Date | Entry") y ahí la primera celda es la cantidad de páginas.
  const heads = Array.from(document.querySelectorAll('#gridDockets thead th')).map(th => text(th).toUpperCase());
  // St. Lucie tiene las dos, "SEQ# | Image | DIN | Date | Entry", y su DIN no sirve de orden
  // (0 en lo grabado, repetidos): si hay SEQ, manda SEQ.
  const seq = heads.findIndex(h => /^SEQ *#?$/.test(h));
  const din = seq >= 0 ? seq : Math.max(0, heads.findIndex(h => h === 'DIN'));
  // Escambia (2.6.3.18): "Image | Date | Entry", sin columna de número. La primera es la imagen
  // (cantidad de páginas o "Request"): no es un número de entrada.
  const numbered = !(seq < 0 && heads.indexOf('DIN') < 0 && heads[0] === 'IMAGE');
  const dateCol = heads.indexOf('DATE');
  const docket = Array.from(document.querySelectorAll('#gridDockets tbody tr')).map(tr => {
    const tds = tr.querySelectorAll(':scope > td');
    const img = tr.querySelector('a.casedocketimage');
    const entry = tr.querySelector("td[id^='text_']");
    return {
      seq: numbered ? text(tds[din]) : '',
      date: tds.length > 3 ? text(tds[tds.length - 2]) : (dateCol >= 0 ? text(tds[dateCol]) : ''),
      text: entry ? text(entry) : text(tds[tds.length - 1]),
      cid: img ? img.getAttribute('rel') || img.id : null, digest: img ? img.getAttribute('digest') : null,
    };
  });
  // Cargos del resumen ("Count | Description | Level | ..."), sin fechas. Martin (2.9.29) no tiene
  // la vista previa de la fila, que es de donde salen los cargos con fechas en los demás.
  const summaryCharges = Array.from(document.querySelectorAll('#gridCharges tbody tr'))
    .map(tr => text(tr.querySelectorAll(':scope > td')[1])).filter(Boolean);
  return {title: document.title.replace(/\\s+/g, ' ').trim(), summary, parties, docket, summaryCharges,
          unavailable: /dockets are currently unavailable/i.test(text(document.querySelector('#courtCaseDockets')))};
}
"""

# Resumen desplegable: los cargos con sus fechas. Se pide como lo pide el "+" de la fila.
_THUMB_JS = """
async (url) => {
  // Bay (06/11/2026): Cloudflare devuelve 522 de vez en cuando; se reintenta.
  let r, html;
  for (let i = 0; i < 3; i++) {
    r = await fetch(url, {credentials: 'include'});
    html = await r.text();
    if (r.status < 520) break;
    await new Promise(ok => setTimeout(ok, 2000));
  }
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const text = el => el ? el.textContent.replace(/\\u00a0/g, ' ').replace(/\\s+/g, ' ').trim() : '';
  const summary = {};
  doc.querySelectorAll('table.thumbSummary tr').forEach(tr => {
    const tds = tr.querySelectorAll('td');
    if (tds.length >= 2) summary[text(tds[0]).replace(/:$/, '')] = text(tds[1]);
  });
  const charges = [];
  const box = doc.querySelector('div.vscroll table');
  if (box) {
    for (const tr of box.querySelectorAll(':scope > tbody > tr, :scope > tr')) {
      const tds = tr.querySelectorAll(':scope > td');
      if (tds.length >= 2 && /^\\d+$/.test(text(tds[0]))) {
        charges.push({count: text(tds[0]), description: text(tds[1]), offense: '', initial: '', obts: ''});
      } else if (charges.length) {
        // Tabla anidada: una fila de títulos (Offense Date, Initial Status Date, OBTS #) y la de valores.
        const rows = Array.from(tr.querySelectorAll('table tr'));
        const head = rows.findIndex(r => /offense date/i.test(r.textContent));
        if (head >= 0 && rows[head + 1]) {
          const labels = Array.from(rows[head].querySelectorAll('td, th')).map(c => text(c).toLowerCase());
          const values = Array.from(rows[head + 1].querySelectorAll('td')).map(c => text(c));
          const pick = re => { const i = labels.findIndex(l => re.test(l)); return i >= 0 ? (values[i] || '') : ''; };
          const last = charges[charges.length - 1];
          last.offense = pick(/offense/); last.initial = pick(/initial/); last.obts = pick(/obts/);
        }
      }
    }
  }
  return {status: r.status, summary, charges};
}
"""

# El documento: el portal arma el PDF y lo entrega por un guid.
_PDF_JS = """
async ({base, cid, digest}) => {
  try {
    const form = new URLSearchParams({cid: cid, digest: digest, time: new Date().toString(), redacted: 'False'});
    const g = await fetch(base + 'ImageAsync.aspx/GetPDFRequestGuid', {method: 'POST', credentials: 'include',
      headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest'},
      body: form.toString()});
    const guid = (await g.text()).trim().replace(/^"|"$/g, '');
    if (!/^[0-9a-f-]{20,}$/i.test(guid)) return {status: g.status, type: 'guid', b64: btoa(unescape(encodeURIComponent(guid.slice(0, 3000))))};
    const r = await fetch(base + 'ImageAsync.aspx/GetPDF?guid=' + encodeURIComponent(guid), {credentials: 'include'});
    const bytes = new Uint8Array(await r.arrayBuffer());
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return {status: r.status, type: r.headers.get('content-type') || '', b64: btoa(bin)};
  } catch (e) {
    return {error: String(e)};
  }
}
"""


def _date(text: Optional[str]) -> Optional[date]:
    m = re.search(_DATE, text or "")
    return parse_us_date(m.group(1)) if m else None


def court_code(case_number: str) -> str:
    """Tipo de corte del número de caso: '2026 CF 003520' -> 'CF', '26001446CFMA' -> 'CF',
    'CC26-5468' -> 'CC'. Vacío si el número no tiene una forma conocida."""
    for regex in _CASE_FORMATS:
        m = regex.match(case_number or "")
        if m:
            return m.group(2)
    return ""


def clean_name(name_cell: str) -> str:
    """'GARCIA, KATHRYN YOUNG (1966) (Alias)' -> 'GARCIA, KATHRYN YOUNG'."""
    return re.sub(r"\s+", " ", re.sub(r"\(.*?\)", " ", name_cell or "")).strip(" ,")


def birth_date(name_cell: str) -> Optional[date]:
    """El portal solo muestra el año de nacimiento: se toma el 1 de julio, así la edad
    calculada nunca se aleja más de un año de la real."""
    m = _BIRTH_YEAR_RE.search(name_cell or "")
    return date(int(m.group(1)), 7, 1) if m else None


def case_from_title(title: str) -> tuple[str, str]:
    """'2026 CF 003520 - STATE OF FLORIDA vs. GARCIA, KATHRYN' -> ('2026 CF 003520', 'GARCIA, KATHRYN')."""
    title = re.sub(r"\s+", " ", title or "").strip()
    number, _, rest = title.partition(" - ")
    if not court_code(number):
        return "", ""
    m = re.search(r"\bvs?\.?\s+(.+)$", rest, re.I)
    return number.strip(), (m.group(1).strip() if m else "")


def thumb_url(onclick: str) -> Optional[str]:
    """"details(3658470, 'CourtCase.aspx/CaseThumbnail/', '?digest=X');" -> 'CourtCase.aspx/CaseThumbnail/3658470?digest=X'."""
    m = re.search(r"details\((\d+),\s*'([^']+)',\s*'([^']+)'", onclick or "")
    return f"{m.group(2)}{m.group(1)}{m.group(3)}" if m else None


def case_type(case_number: str, codes: Optional[dict[str, str]] = None) -> str:
    """Tipo de caso con los códigos del worker (CF, MM, CT). `codes` traduce las terminaciones
    propias del condado (Charlotte: "F" -> "CF"); sin traducción queda la del número."""
    code = court_code(case_number)
    return (codes or {}).get(code, code)


def candidates_from_rows(rows: list[dict], case_types: list[str],
                         codes: Optional[dict[str, str]] = None) -> list[CaseCandidate]:
    """Solo los casos penales en los que la persona es la acusada; uno por caso (los
    alias repiten la fila: se guardan como otros nombres del mismo caso)."""
    wanted = {t.upper() for t in case_types}
    by_case: dict[str, CaseCandidate] = {}
    # Primero las filas del nombre principal; las de "(Alias)" se agregan como otros nombres.
    ordered = sorted(rows, key=lambda r: "(alias)" in (r.get("nameCell") or "").lower())
    for row in ordered:
        number = re.sub(r"\s+", " ", row.get("case", "")).strip()
        if row.get("party", "").upper() != "DEFENDANT" or case_type(number, codes) not in wanted:
            continue
        name = clean_name(row.get("nameCell") or row.get("name", ""))
        if number in by_case:
            if name and name not in by_case[number].aliases and name != by_case[number].defendant:
                by_case[number].aliases.append(name)
            continue
        by_case[number] = CaseCandidate(
            case_number=number, defendant=name, case_type=case_type(number, codes), status=row.get("status", ""),
            dob=birth_date(row.get("nameCell", "")), filed_date=None, detail_ref=row.get("href", ""),
        )
        # El enlace del resumen desplegable va junto al del caso (mismo id y digest).
        thumb = thumb_url(row.get("thumb", ""))
        if thumb:
            by_case[number].detail_ref = row.get("href", "") + "#thumb=" + thumb
    return list(by_case.values())


def split_ref(detail_ref: str) -> tuple[str, Optional[str]]:
    """detail_ref -> (enlace del caso, enlace del resumen desplegable)."""
    href, _, thumb = detail_ref.partition("#thumb=")
    return href, thumb or None


def thumb_from_details(href: str) -> Optional[str]:
    """Sin fila de resultados (el portal saltó directo al caso): el resumen usa el mismo id y digest."""
    parsed = urlparse(href)
    m = re.search(r"/Details/(\d+)", parsed.path)
    raw = re.search(r"(?:^|&)digest=([^&]+)", parsed.query)
    # El digest es base64: un "+" crudo es un "+" (parse_qs lo volvería espacio).
    digest = unquote(raw.group(1)) if raw else None
    if not (m and digest):
        return None
    return f"CourtCase.aspx/CaseThumbnail/{m.group(1)}?digest={quote(digest, safe='')}"


_AGENCY_PLACE_LAST_RE = re.compile(r"^((?:SHERIFF'?S?|POLICE)\b[^-]*?)\s+-\s+(.+)$", re.I)


def normalize_agency_name(text: str) -> str:
    """'OSCEOLA CO SHERIFF'S OFFICE' -> 'OSCEOLA COUNTY SHERIFF'S OFFICE' y "SHERIFF'S OFFICE -
    CHARLOTTE COUNTY" -> "CHARLOTTE COUNTY SHERIFF'S OFFICE" (solo para comparar con la nota)."""
    text = re.sub(r"\bCO\b(?=\.?\s+SHERIFF)", "COUNTY", text or "", flags=re.I).strip()
    flipped = _AGENCY_PLACE_LAST_RE.match(text)
    return f"{flipped.group(2).strip()} {flipped.group(1).strip()}" if flipped else text


def charges_from_case(summary: dict, thumb: dict, arrest: Optional[date],
                      summary_charges: Optional[list[str]] = None) -> list[Charge]:
    agency = normalize_agency_name(summary.get("agency") or thumb.get("summary", {}).get("Agency", "")) or None
    report = (summary.get("agencyreportnumber") or thumb.get("summary", {}).get("Agency Report #", "")).strip() or None
    charges = []
    for c in thumb.get("charges", []):
        charges.append(Charge(
            offense_date=_date(c.get("offense")), description=c.get("description", ""), statute=None,
            arrest_date=arrest, agency=agency, control_number=report, initial_date=_date(c.get("initial")),
        ))
    if not charges and summary_charges:
        # Sin vista previa (Martin): los cargos del resumen del caso, sin fechas.
        for description in summary_charges:
            charges.append(Charge(offense_date=None, description=description, statute=None, arrest_date=arrest,
                                  agency=agency, control_number=report))
    if not charges and (agency or report or arrest):
        # Sin resumen de cargos: al menos la agencia, el reporte y la fecha de arresto.
        charges.append(Charge(offense_date=None, description="", statute=None, arrest_date=arrest,
                              agency=agency, control_number=report))
    return charges


def docket_from_case(rows: list[dict]) -> list[DocketEntry]:
    """Las entradas del docket. Sin columna de número (Escambia: "Image | Date | Entry", de la más
    nueva a la más vieja) se ponen en orden cronológico y se numeran así: el primer documento que
    coincide es entonces el del arresto que abrió el caso, no el de un arresto posterior (Rodriguez,
    2023 CF 003056 A: ARREST REPORT del 24/07/2023 y otro del 27/10/2023, por la fianza revocada)."""
    if rows and not any(re.sub(r"\D", "", r.get("seq", "")) for r in rows):
        dates = [d for d in (_date(r.get("date")) for r in rows) if d]
        if len(dates) > 1 and dates[0] > dates[-1]:
            rows = rows[::-1]
        rows = [{**r, "seq": str(i)} for i, r in enumerate(rows, 1)]
    docket = []
    for row in rows:
        seq = re.sub(r"\D", "", row.get("seq", ""))
        docket.append(DocketEntry(
            date=_date(row.get("date")), description=row.get("text", ""),
            doc_ref=f"{row['cid']}|{row['digest']}" if row.get("cid") and row.get("digest") else None,
            number=int(seq) if seq else None,
        ))
    return docket


def consolidated_into(docket: list[DocketEntry]) -> Optional[str]:
    """El caso al que la fiscalía unió este: "CONSOLIDATED TO 26001422CFMA - ADD ON CHARGE" (St. Johns)."""
    for e in docket:
        m = re.match(r"^\s*consolidated\s+(?:to|into|with)\s+(\S+)", e.description, re.I)
        if m:
            return m.group(1)
    return None


def site_root(base_url: str) -> str:
    """'https://x/BenchmarkWeb/Home.aspx/Search' -> 'https://x/BenchmarkWeb/'; 'https://y/Home.aspx/Search' -> 'https://y/'."""
    path = urlparse(base_url).path
    i = path.lower().find("/home.aspx")
    return urljoin(base_url, path[:i + 1] if i >= 0 else "/")


def arrest_date_from_docket(docket: list[DocketEntry]) -> Optional[date]:
    """La fecha de arresto del docket (la primera, si hay varias): "DATE OF ARREST" (Osceola),
    "ARRESTED ON 10/24/2026" / "REARRESTED ON 10/24/2026" (Flagler), "ARREST COUNT: ..." (St. Johns)
    o "ARREST SEQ: ..." (Charlotte).

    St. Johns anota un "ARREST COUNT" por cargo; si tienen fechas distintas (cargos añadidos al caso
    en otros días) no hay una fecha de arresto fiable y se usan las fechas Initial de los cargos."""
    dates, counts = [], set()
    for e in docket:
        said = re.match(r"^\s*(?:re)?arrested on\s+(\d{1,2}/\d{1,2}/\d{4})", e.description, re.I)
        if said:
            dates.append(_date(said.group(1)))
        elif e.date and re.match(r"^\s*date of arrest", e.description, re.I):
            dates.append(e.date)
        elif e.date and re.match(r"^\s*arrest (?:count|seq)\b", e.description, re.I):
            # St. Johns: "ARREST COUNT: 1 - (828.122 3A) ANIMAL FIGHTING", con la fecha del arresto.
            # Charlotte: "ARREST SEQ: 1 - (316.1935 3A) FLEE/ELUDE POLICE ...".
            counts.add(e.date)
    if len(counts) == 1:
        dates.extend(counts)
    dates = [d for d in dates if d]
    return min(dates) if dates else None


class BenchmarkWebAdapter(ClerkAdapter):

    def site_root(self) -> str:
        """Raíz de la aplicación: ".../BenchmarkWeb/" (Osceola) o "/" (Flagler, en su propio dominio)."""
        return site_root(self.county.base_url)

    # --- 1. Búsqueda --------------------------------------------------------

    def search(
        self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]
    ) -> list[CaseCandidate]:
        page = self.page
        name = f"{portal_text(query.last_name)}, {portal_text(query.first_name)}"
        dates = {"type": "Name", "search": name,
                 "openedFrom": f"{date_from:%m/%d/%Y}" if date_from else "",
                 "openedTo": f"{date_to:%m/%d/%Y}" if date_to else ""}
        log.info("Buscando %s entre %s y %s (tipos %s)", name, date_from, date_to, query.case_types)
        try:
            return self._search(query, name, dates)
        except CloudflareBlocked as exc:
            log.warning("Cloudflare bloquea el portal: %s", exc)
            opened = (f", cases opened {dates['openedFrom']} to {dates['openedTo']}"
                      if dates["openedFrom"] and dates["openedTo"] else "")
            raise SearchError(
                f"{self.county.name}'s court portal is blocking automated searches right now (its Cloudflare "
                f"security check didn't let the search browser through). Try again later, or search it by hand "
                f"at {self.county.base_url} with the name \"{name}\"{opened}.") from None

    def _search(self, query: LookupQuery, name: str, dates: dict) -> list[CaseCandidate]:
        page = self.page
        for attempt in (1, 2, 3):
            # St. Lucie pide la suma en cada búsqueda (captcha_every_search); Martin, Clay y los demás
            # solo en la primera de la sesión.
            if self.county.captcha_before_search and (self.county.captcha_every_search
                                                      or not getattr(self, "_captcha_done", False)):
                state = self._search_captcha_first(name, dates)
            else:
                self._submit_search(name, dates)
                state = self._wait_results()
                if state == "captcha":
                    # La suma no tiene recuadro de Google: se pide con el panel del CAPTCHA de texto.
                    state = (self._search_captcha_first(name, dates) if self.county.captcha == "text"
                             else self._search_with_captcha(name, dates))
            if state == "cf_error":
                # Bay (06/11/2026): Cloudflare no llegó al servidor del condado (522); se repite.
                log.warning("El portal no respondió (Cloudflare 52x); repito la búsqueda (intento %d de 3)", attempt)
                page.wait_for_timeout(3000)
                continue
            if state != "form" or attempt == 2:
                break
            # El portal devolvió el formulario vacío (01/11/2026, Chrome local: el nombre se
            # escribió antes de que la página terminara de prepararse y se envió vacío).
            log.warning("El portal volvió al formulario sin resultados (intento %d de 2)", attempt)
        if state == "cf_error":
            raise SearchError("The court portal is not responding right now (Cloudflare 522). Try again in a few minutes.")
        if state == "form":
            raise SearchError("The court portal returned the empty search form instead of results")
        if state == "case":
            candidate = self._candidate_from_case_page()
            log.info("Un solo caso: el portal fue directo a %s", candidate.case_number if candidate else page.url)
            return [candidate] if candidate and candidate.case_type in {t.upper() for t in query.case_types} else []
        if state == "empty":
            log.info("El portal no encontró coincidencias")
            return []
        if state != "rows":
            raise SearchError("The court portal didn't show the search results")

        rows = self._read_all_rows()
        candidates = candidates_from_rows(rows, query.case_types, self.county.case_type_codes)
        log.info("%d fila(s) en resultados, %d caso(s) penal(es) como acusado", len(rows), len(candidates))
        kept = {c.case_number for c in candidates}
        others = [f"{r.get('case')} ({r.get('party')}, {clean_name(r.get('nameCell') or r.get('name', ''))})"
                  for r in rows if re.sub(r"\s+", " ", r.get("case", "")).strip() not in kept]
        if others:
            log.info("Fuera (otro tipo de caso o de parte): %s", "; ".join(dict.fromkeys(others))[:900])
        return candidates

    def _submit_search(self, name: str, dates: dict) -> None:
        self._open_form()
        self._fill_form(name, dates)
        self._click_search()

    def _open_form(self) -> None:
        """La página de búsqueda, con sus scripts listos.

        Los scripts del portal (search.js) eligen la opción "Name" al cargar y eso vacía el
        campo: si se escribe antes, se envía el formulario sin nombre y el portal vuelve a él.
        """
        page = self.page
        if self.county.cloudflare_gate:
            self._watch_blocked()
        for attempt in range(3):
            if self.county.cloudflare_gate:
                self._goto_behind_cloudflare(self.county.base_url)
            else:
                page.goto(self.county.base_url, wait_until="load")
            # Escambia: la página puede seguir navegando (verificación de Cloudflare); sin error 52x a la vista, se sigue.
            cf_error = self._evaluate_or(_CF_ERROR_JS, False) if self.county.cloudflare_gate else page.evaluate(_CF_ERROR_JS)
            if not cf_error:
                break
            log.warning("El portal no respondió (Cloudflare 52x); recargo (intento %d de 3)", attempt + 1)
            page.wait_for_timeout(3000)
        if self.county.cloudflare_gate:
            self._pass_cloudflare_gate()
        # "attached", no "visible": Charlotte abre en "All" y el campo del nombre está oculto hasta
        # elegir "Name" (_fill_form lo elige).
        page.wait_for_selector("#name", state="attached", timeout=25_000)
        self._wait_scripts()

    def _pass_cloudflare_gate(self) -> None:
        """Escambia (10/11/2026): la verificación de Cloudflare y, después, la página completa.

        Tras la verificación, Cloudflare puede negar (403) el bundle de estilos y scripts y la imagen
        de la suma: el formulario queda sin estilos ni suma. Se arregla cuando Cloudflare pone su
        verificación a una página entera y el navegador la pasa (deja la cookie que luego vale para
        todo). En el probe pasó en la tercera carga; en el worker (Rodriguez, 10/11/2026) no salió en
        tres cargas, ni en cinco. Tres cargas en ~1 minuto: recargar sin caché (Ctrl+Shift+R), abrir
        como página el recurso negado (por si Cloudflare pone ahí su verificación) y volver a recargar.
        Si no pasa, CloudflareBlocked: el usuario recibe cómo buscarlo a mano. Nada se salta: se espera a Cloudflare y la casilla, si aparece, la marca la persona.
        Cada paso deja en el log qué recursos negó Cloudflare, para el diagnóstico."""
        page = self.page
        blocked = self._watch_blocked(clear=False)
        self._wait_cloudflare()
        for step, pause_s in enumerate(CLOUDFLARE_GATE_PAUSES_S, 1):
            missing = self._evaluate_or(_INCOMPLETE_JS, "")
            if not missing:
                if step > 1:
                    log.info("La página del portal cargó completa (paso %d)", step)
                return
            denied = [b for b in blocked if b[1] != "document"]
            log.warning("Paso %d de %d: la página cargó sin %s. Cloudflare negó: %s", step,
                        len(CLOUDFLARE_GATE_PAUSES_S), missing,
                        "; ".join(f"{st} {kind} {url[len(self.site_root()):][:70]}" for st, kind, url in denied[:4])
                        or "nada (sin respuestas de error)")
            target = self._blocked_page_url(denied) if step == 2 else None
            blocked.clear()
            page.wait_for_timeout(pause_s * 1000)
            if target:
                log.info("Abro como página lo que negó Cloudflare, para que ponga ahí su verificación: %s", target)
                response = self._goto_behind_cloudflare(target)
                log.info("Respuesta %s, título '%s'", getattr(response, "status", None),
                         self._evaluate_or("() => document.title", "")[:60])
                self._wait_cloudflare(appear_s=8)
                self._goto_behind_cloudflare(self.county.base_url)
            else:
                try:
                    page.context.new_cdp_session(page).send("Network.setCacheDisabled", {"cacheDisabled": True})
                except PlaywrightError as exc:
                    log.info("No pude desactivar la caché (recargo igual): %s", exc)
                self._goto_behind_cloudflare(None)
            self._wait_cloudflare()
            page.wait_for_timeout(2000)
        missing = self._evaluate_or(_INCOMPLETE_JS, "")
        if missing:
            self._save_debug(page, "cloudflare_incompleta")
            raise CloudflareBlocked(f"the page kept loading without {missing}")

    def _goto_behind_cloudflare(self, url: Optional[str]):
        """goto (o reload, sin url) que no falla si la página no termina de cargar: la verificación de
        Cloudflare puede quedarse girando sin disparar "load" (probe en Browserbase, 10/11/2026: 30 s
        sin cargar). Lo que haya quedado lo revisan _wait_cloudflare y la comprobación de la página."""
        try:
            if url is None:
                return self.page.reload(wait_until="load")
            return self.page.goto(url, wait_until="load")
        except PlaywrightTimeout:
            log.warning("La página no terminó de cargar en 30 s (¿la verificación de Cloudflare girando?)")
            return None

    def _watch_blocked(self, clear: bool = True) -> list:
        """Anota las respuestas de error del portal (estado, tipo, URL): lo que Cloudflare negó."""
        if getattr(self, "_cf_blocked", None) is None:
            self._cf_blocked: list[tuple[int, str, str]] = []
            host = urlparse(self.county.base_url).netloc

            def on_response(response) -> None:
                try:
                    if response.status >= 400 and urlparse(response.url).netloc == host:
                        self._cf_blocked.append((response.status, response.request.resource_type, response.url))
                except Exception:
                    pass

            self.page.on("response", on_response)
        if clear:
            self._cf_blocked.clear()
        return self._cf_blocked

    def _blocked_page_url(self, denied: list[tuple[int, str, str]]) -> str:
        """El recurso negado que se abre como página: la hoja de estilos o el script del portal y, si
        no se vio ninguno, la imagen de la suma nueva (CaptchaImage; la del formulario puede ser
        "LastCaptchaImage", que no es la que se negó)."""
        for kinds in (("stylesheet", "script"), ("image",)):
            for _, kind, url in denied:
                if kind in kinds:
                    return url
        return urljoin(self.site_root(), "CourtCase.aspx/CaptchaImage")

    def _wait_cloudflare(self, appear_s: float = 4) -> None:
        """Si aparece la página de verificación de Cloudflare (se mira hasta `appear_s` segundos:
        puede llegar después del "load"), espera a que pase sola; si no pasa, se la pide a la persona
        como un CAPTCHA más (el live view o la ventana local)."""
        page = self.page
        waited = 0.0
        while not self._evaluate_or(CLOUDFLARE_PAGE_JS, False):
            if waited >= appear_s:
                return
            page.wait_for_timeout(1000)
            waited += 1
        log.info("Cloudflare está verificando el navegador; espero hasta %d s a que pase sola", CLOUDFLARE_SELF_PASS_S)
        try:
            page.wait_for_function(CLOUDFLARE_PASSED_JS, timeout=CLOUDFLARE_SELF_PASS_S * 1000, polling=1000)
            log.info("Verificación de Cloudflare superada sola")
        except (PlaywrightTimeout, PlaywrightError):
            if self._evaluate_or(CLOUDFLARE_PAGE_JS, True):
                # Escambia (10/11/2026, Chrome local con perfil limpio): la verificación se queda en el
                # círculo de carga, sin casilla. Se le da a la persona un rato corto por si aparece una
                # casilla; si no, es un bloqueo, no algo que la persona pueda resolver.
                log.info("La verificación de Cloudflare no pasó sola; se pide a la persona (hasta %d s)",
                         CLOUDFLARE_PERSON_S)
                try:
                    self.captcha.solve(page, timeout_s=CLOUDFLARE_PERSON_S, kind="cloudflare")
                except CaptchaTimeout:
                    raise CloudflareBlocked("the security check never finished") from None
        try:
            page.wait_for_load_state("load", timeout=20_000)
        except PlaywrightTimeout:
            pass

    def _evaluate_or(self, js: str, default):
        """page.evaluate, o `default` si la página está navegando (Cloudflare recarga sola al pasar)."""
        try:
            return self.page.evaluate(js)
        except PlaywrightError:
            return default

    def _wait_scripts(self) -> None:
        """Espera a que search.js termine de preparar el formulario. Al cargar, marca la opción por
        defecto: en Charlotte ("All") deshace la elección de "Name" si se hizo antes."""
        page = self.page
        try:
            page.wait_for_load_state("load", timeout=15_000)
            page.wait_for_function("() => !!(window.jQuery && jQuery.isReady)", timeout=15_000, polling=300)
        except PlaywrightTimeout:
            log.warning("Los scripts del portal tardan en cargar; sigo igual")
        page.wait_for_timeout(1000)

    def _fill_form(self, name: str, dates: dict) -> None:
        page = self.page
        if page.locator("#radio_0").count():
            page.locator("#radio_0").check()
        # Charlotte abre con "All" marcado y search.js envía la opción marcada: hay que elegir "Name".
        # En los demás ya viene marcada y esto no hace nada.
        radio = page.locator("input.radioButton[searchtype='Name']")
        if radio.count() and not radio.first.is_checked():
            self._choose_name_option(radio.first)
        page.fill("#name", name)
        page.wait_for_timeout(300)
        if page.input_value("#name") != name:
            page.fill("#name", name)
        # Fechas de apertura. Osceola: solo hay campos ocultos, y el portal los respeta (Jones,
        # James: 369 casos sin fechas, 1 con 2026). Flagler: también hay campos visibles
        # (#openedFrom / #openedTo) y search.js copia esos sobre los ocultos al enviar: se
        # llenan los dos.
        page.evaluate("""(h) => {
            for (const id of ['openedFrom', 'openedTo']) {
                const box = document.getElementById(id);
                if (box) { box.value = h[id]; box.dispatchEvent(new Event('change', {bubbles: true})); }
            }
            const form = document.querySelector('form.searchform');
            const put = () => { for (const [k, v] of Object.entries(h)) {
                const el = form.querySelector('input[type=hidden][name="' + k + '"]'); if (el) el.value = v; } };
            put();
            if (!window.__clerkListener) { form.addEventListener('submit', put, true); window.__clerkListener = true; }
            window.__clerkSubmitted = true;
        }""", dates)

    def _choose_name_option(self, radio) -> None:
        """Marca "Name" y deja visible su campo (Charlotte). Si los scripts del portal lo desmarcan
        o no muestran el campo, se marca desde la página y se muestra el campo a mano."""
        page = self.page
        for _ in range(2):
            radio.click()
            try:
                page.wait_for_selector("#name", state="visible", timeout=4000)
                if radio.is_checked():
                    return
            except PlaywrightTimeout:
                pass
            page.wait_for_timeout(1000)
        log.warning("La opción Name no quedó marcada con un clic; la marco desde la página")
        page.evaluate("""() => {
            const r = document.querySelector("input.radioButton[searchtype='Name']");
            r.checked = true;
            r.dispatchEvent(new Event('change', {bubbles: true}));
            const box = document.getElementById('name');
            if (box && getComputedStyle(box).display === 'none') box.style.display = 'inline-block';
        }""")
        page.wait_for_timeout(300)

    def _click_search(self) -> None:
        button = self.page.get_by_role("button", name=re.compile(r"^\s*search\s*$", re.I))
        if not button.count() and self.page.locator("#searchButton").count():
            # Charlotte: el ícono del botón entra en su nombre accesible y "Search" ya no queda solo.
            button = self.page.locator("#searchButton")
        button.first.click()

    def _search_with_captcha(self, name: str, dates: dict) -> str:
        """El portal pidió el CAPTCHA (Flagler: hCaptcha, "Invalid Captcha" si se busca sin él).

        Solo la primera búsqueda de la sesión lo pide: las siguientes pasan sin él (Flagler,
        02/11/2026). Lo resuelve una persona; después se vuelve a buscar.
        """
        page = self.page
        if not page.locator("#name").count():
            self._open_form()
        else:
            self._wait_scripts()     # la página del "Invalid Captcha" acaba de cargar
        self._fill_form(name, dates)
        kind = page.evaluate(_CAPTCHA_KIND_JS) or "recaptcha"
        log.info("El portal pide un CAPTCHA (%s); se pide a la persona", kind)
        self.captcha.solve(page, timeout_s=self.county.captcha_timeout_s, kind=kind)
        self._click_search()
        state = self._wait_results()
        if state == "captcha":
            raise SearchError("The court portal didn't accept the CAPTCHA")
        return state

    def _search_captcha_first(self, name: str, dates: dict) -> str:
        """Primera búsqueda de la sesión en un portal que bloquea si se busca sin el CAPTCHA
        (Clay, 08/11/2026: Cloudflare, "Sorry, you have been blocked"). Se resuelve antes de
        enviar; las búsquedas siguientes de la sesión ya no lo piden."""
        page = self.page
        text = self.county.captcha == "text"
        tries = TEXT_CAPTCHA_TRIES if text else 1
        for attempt in range(1, tries + 1):
            self._open_form()
            self._fill_form(name, dates)
            self._ensure_captcha_box(name, dates)
            kind = "text" if text else (page.evaluate(_CAPTCHA_KIND_JS) or "recaptcha")
            log.info("El portal pide el CAPTCHA antes de buscar (%s); se pide a la persona", kind)
            self.captcha.solve(page, timeout_s=self.county.captcha_timeout_s, kind=kind)
            if self._still_on_form():
                self._click_search()
            else:
                # La persona envió el formulario desde el live view (St. Lucie, 09/11/2026).
                log.info("La búsqueda ya se envió desde la ventana del CAPTCHA")
            state = self._wait_results()
            if state != "captcha":
                break
            if attempt < tries:
                log.warning("El portal no aceptó la respuesta del CAPTCHA; pido una suma nueva (intento %d de %d)",
                            attempt + 1, tries)
        else:
            raise SearchError("The court portal didn't accept the CAPTCHA")
        if state in ("rows", "empty", "case"):
            self._captcha_done = True
        return state

    def _still_on_form(self) -> bool:
        url = (self.page.url or "").lower()
        return "/home.aspx/search" in url and "error=" not in url

    def _ensure_captcha_box(self, name: str, dates: dict) -> None:
        """Espera el recuadro del CAPTCHA; si no aparece, se lo pide al script de Google y, si
        aun así no sale, recarga el formulario (hasta 3 veces). Nunca se busca sin él."""
        page = self.page
        text = self.county.captcha == "text"
        for attempt in (1, 2, 3):
            try:
                page.wait_for_function(_TEXT_CAPTCHA_JS if text else _CAPTCHA_DRAWN_JS, timeout=10_000, polling=500)
                return
            except PlaywrightTimeout:
                pass
            if text:
                if attempt < 3:
                    log.warning("No veo el CAPTCHA de texto; recargo el formulario (intento %d de 3)", attempt)
                    self._open_form()
                    self._fill_form(name, dates)
                continue
            result = page.evaluate(_CAPTCHA_RENDER_JS)
            log.info("El recuadro del CAPTCHA no apareció; se lo pido al script de Google: %s", result)
            try:
                page.wait_for_function(_CAPTCHA_DRAWN_JS, timeout=8_000, polling=500)
                return
            except PlaywrightTimeout:
                pass
            if attempt < 3:
                log.warning("Sigue sin aparecer el CAPTCHA; recargo el formulario (intento %d de 3)", attempt)
                self._open_form()
                self._fill_form(name, dates)
        raise SearchError("The court portal didn't show its CAPTCHA, so the search wasn't sent "
                          "(this portal blocks searches without it). Try again in a few minutes.")

    def _wait_results(self) -> str:
        try:
            handle = self.page.wait_for_function(_RESULTS_STATE_JS, timeout=RESULTS_TIMEOUT_MS, polling=500)
            return handle.json_value()
        except PlaywrightTimeout:
            return ""
        except PlaywrightError as exc:
            if "context was destroyed" in str(exc) or "navigat" in str(exc).lower():
                self.page.wait_for_load_state("domcontentloaded")
                return self._wait_results()
            raise

    def _read_all_rows(self) -> list[dict]:
        """Todas las filas (hasta MAX_PAGES de 100). Solo se toca la tabla si hay más casos
        de los que ya se ven: con pocos resultados, la primera página alcanza."""
        page = self.page
        total = page.evaluate(_CASES_FOUND_JS)
        rows = page.evaluate(_READ_ROWS_JS)
        if total is None or len(rows) >= total:
            return rows
        length = page.locator("select[name='gridSearchResults_length']")
        if length.count():
            before = page.evaluate(_ROWS_SIGNATURE_JS)
            length.select_option("100")
            self._wait_redraw(before)
            rows = page.evaluate(_READ_ROWS_JS)
        for _ in range(MAX_PAGES - 1):
            if len(rows) >= total:
                break
            nxt = page.locator("#gridSearchResults_next")
            if not nxt.count() or "disabled" in (nxt.get_attribute("class") or ""):
                break
            before = page.evaluate(_ROWS_SIGNATURE_JS)
            nxt.click()
            self._wait_redraw(before)
            rows += page.evaluate(_READ_ROWS_JS)
        if len(rows) < total:
            log.warning("El portal tiene %d filas; leí %d", total, len(rows))
        return rows

    def _wait_redraw(self, before: str) -> None:
        try:
            self.page.wait_for_function(f"(b) => ({_ROWS_SIGNATURE_JS.strip()})() !== b",
                                        arg=before, timeout=15_000, polling=300)
        except PlaywrightTimeout:
            log.warning("La tabla de resultados no cambió en 15 s")
        self.page.wait_for_timeout(500)

    def _candidate_from_case_page(self) -> Optional[CaseCandidate]:
        # El resumen y las partes llegan después de la página (otra consulta): se esperan.
        # Garcia, McMillian y Jeanbaptiste (01/11/2026): leídos al instante salían vacíos.
        try:
            self._wait_case(self.page, _CASE_SUMMARY_JS, "caso único")
        except PlaywrightTimeout:
            log.warning("El resumen del caso no cargó; uso el título de la página")
        data = self.page.evaluate(_READ_CASE_JS)
        title_number, title_defendant = case_from_title(data.get("title", ""))
        number = re.sub(r"\s+", " ", data.get("summary", {}).get("casenumber", "")).strip() or title_number
        defendant = next((p["name"] for p in data.get("parties", [])
                          if p.get("type", "").upper().startswith("DEFENDANT")), "") or title_defendant
        if not number or not defendant:
            log.warning("No pude leer el caso al que saltó el portal: %r", data.get("title"))
            return None
        href = urlparse(self.page.url)
        return CaseCandidate(case_number=number, defendant=defendant, case_type=case_type(number, self.county.case_type_codes),
                             status=data.get("summary", {}).get("status", ""), dob=None,
                             filed_date=_date(data.get("summary", {}).get("clerkfiledate")),
                             detail_ref=href.path + ("?" + href.query if href.query else ""))

    # --- 2. Caso --------------------------------------------------------------

    def open_case(self, candidate: CaseCandidate) -> CaseDetails:
        href, thumb = split_ref(candidate.detail_ref)
        url = urljoin(self.county.base_url, href)
        thumb = thumb or thumb_from_details(href)
        tab = self.page.context.new_page()
        try:
            tab.goto(url, wait_until="domcontentloaded")
            self._wait_case(tab, _CASE_READY_JS, candidate.case_number)
            data = tab.evaluate(_READ_CASE_JS)
            thumb_data = {}
            if thumb:
                try:
                    thumb_data = tab.evaluate(_THUMB_JS, urljoin(self.site_root(), thumb.lstrip("/")))
                except PlaywrightError as exc:
                    log.warning("%s: no pude leer el resumen de cargos (%s)", candidate.case_number, exc)
            summary = data.get("summary", {})
            filed = _date(summary.get("clerkfiledate"))
            if filed:
                candidate.filed_date = filed
            docket = docket_from_case(data.get("docket", []))
            arrest = arrest_date_from_docket(docket)
            charges = charges_from_case(summary, thumb_data, arrest, data.get("summaryCharges"))
            report = (summary.get("agencyreportnumber") or "").strip() or None
            details = CaseDetails(candidate=candidate, charges=charges, docket=docket, source_url=tab.url,
                                  docket_unavailable=bool(data.get("unavailable")), agency_report_number=report,
                                  consolidated=consolidated_into(docket) is not None)
            log.info("%s: %d cargo(s), %d entrada(s) de docket, agencia %s, reporte %s, arresto %s",
                     candidate.case_number, len(charges), len(docket), charges[0].agency if charges else None,
                     report, arrest)
            log.info("Cargos (%s): %s", candidate.case_number, " | ".join(
                f"{c.description} (ofensa {c.offense_date}, inicial {c.initial_date})" for c in charges)[:900])
            return details
        except PlaywrightTimeout:
            self._save_debug(tab, f"case_{safe_filename(candidate.case_number)}")
            raise SearchError(f"The case page for {candidate.case_number} did not load") from None
        finally:
            tab.close()

    def _wait_case(self, tab, ready_js: str, label: str) -> None:
        """Espera el caso. Si la página quedó en el error de Cloudflare se recarga; en BenchmarkWeb2
        (Bay), si el caso no aparece solo, se piden el resumen y el docket directo (_BW2_LOAD_JS)."""
        for attempt in range(3):
            if not tab.evaluate(_CF_ERROR_JS):
                break
            log.warning("%s: el portal no respondió (Cloudflare 52x); recargo", label)
            tab.wait_for_timeout(3000)
            tab.reload(wait_until="domcontentloaded")
        bw2 = tab.locator("#detailsContent").count() > 0
        try:
            tab.wait_for_function(ready_js, timeout=10_000 if bw2 else 25_000, polling=500)
            return
        except PlaywrightTimeout:
            if not bw2:
                raise
        result = tab.evaluate(_BW2_LOAD_JS, self.site_root())
        if not result.get("ok"):
            log.warning("%s: no pude traer el caso (%s)", label, result.get("why"))
        tab.wait_for_function(ready_js, timeout=15_000, polling=500)

    def find_documents(self, details: CaseDetails) -> list[DocketEntry]:
        """El primero que entró al expediente primero (por "Seq #")."""
        return sorted(super().find_documents(details), key=lambda e: (e.number is None, e.number or 0))

    def find_support_documents(self, details: CaseDetails) -> list[DocketEntry]:
        return sorted(super().find_support_documents(details), key=lambda e: (e.number is None, e.number or 0))

    # --- 3. Descarga ----------------------------------------------------------

    def download(self, details: CaseDetails, entry: DocketEntry, dest_dir: Path) -> Path:
        if not entry.doc_ref or "|" not in entry.doc_ref:
            raise DownloadError(f"'{entry.description}' has no document link")
        cid, digest = entry.doc_ref.split("|", 1)
        base = self.site_root()
        result = self.page.evaluate(_PDF_JS, {"base": base, "cid": cid, "digest": digest})
        if result.get("error"):
            raise DownloadError(f"The court portal didn't hand over '{entry.description}': {result['error']}")
        body = base64.b64decode(result["b64"])
        status, content_type = result["status"], result["type"]
        if status >= 400 or not is_pdf(body):
            self._save_download_debug(entry, status, content_type, body)
            text = body[:4000].decode("utf-8", "replace").lower()
            if content_type == "guid" or "not available" in text or "request" in text:
                raise DocumentUnavailable(f"The court portal isn't handing over '{entry.description}' right now")
            raise DownloadError(f"The court portal didn't hand over the document '{entry.description}' "
                                f"(HTTP {status}, {content_type or 'no content type'}, {len(body)} bytes)")
        path = dest_dir / safe_filename(f"{details.candidate.case_number} - {entry.description}.pdf")
        path.write_bytes(body)
        log.info("PDF guardado: %s (%d KB)", path, len(body) // 1024)
        return path

    # --- Diagnóstico ----------------------------------------------------------

    def _save_debug(self, tab, name: str) -> None:
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            tab.screenshot(path=str(self.debug_dir / f"{name}.png"), full_page=True)
            (self.debug_dir / f"{name}.html").write_text(tab.content(), encoding="utf-8")
        except Exception as exc:
            log.warning("No pude guardar el debug del caso: %s", exc)

    def _save_download_debug(self, entry: DocketEntry, status: int, content_type: str, body: bytes) -> None:
        if not self.debug_dir:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            (self.debug_dir / "download_response.txt").write_text(
                f"documento: {entry.description} (Seq #{entry.number})\nref: {entry.doc_ref}\n"
                f"HTTP {status}\ncontent-type: {content_type!r}\nbytes: {len(body)}\n\n"
                + body[:4000].decode("utf-8", "replace"), encoding="utf-8")
        except Exception as exc:
            log.warning("No pude guardar la respuesta de la descarga: %s", exc)
