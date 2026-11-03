"""Exploración de BenchmarkWeb (Osceola y los otros condados de Pioneer / Catalis).

No es parte del worker: recorre el portal una vez, como lo haría el
adaptador, y guarda lo que ve (HTML, capturas, scripts del portal, respuestas
JSON y tráfico) para construir el adaptador con la estructura real del portal.

    python probe_benchmark.py --local                 # Osceola, Garcia Kathryn, con Chrome de tu PC
    python probe_benchmark.py --local --name "Mcmillian, Stacia" --case "2026 CF 003435" --doc "booking document"
    python probe_benchmark.py                         # lo mismo en Browserbase

Osceola no pide CAPTCHA, así que con --local alcanza (no gasta Browserbase).
Si apareciera uno, resuélvelo en la ventana y el script sigue solo.

Pasos:
  1. Busca el nombre y guarda la lista (y la respuesta JSON que la llena).
  2. Pide el resumen desplegable del caso (agencia, número de reporte, cargos con fechas).
  3. Abre el caso, la pestaña Charges, y hace clic en el ícono del documento del docket.
  4. Busca un nombre común (Jones, James) dos veces: normal y con fechas de apertura
     en los campos ocultos del formulario, para ver si el portal las respeta.

Todo queda en output/probe_benchmark/<fecha>/ (output/ no se sube a git:
tiene datos de expedientes). Comprime esa carpeta y envíala.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from dotenv import load_dotenv

from adapters.benchmark_web import site_root
from probe_civitek import INVENTORY_JS, _safe

BASE = "https://courts.osceolaclerk.com/BenchmarkWeb/Home.aspx/Search"
SCRIPTS = ("Scripts/home/search.js", "Scripts/CourtCase/casedockets.js", "Scripts/casedetails.js")
FETCH_TEXT_JS = """
async (url) => {
  try {
    const r = await fetch(url, {credentials: 'include'});
    return {status: r.status, type: r.headers.get('content-type'), text: await r.text()};
  } catch (e) { return {error: String(e)}; }
}
"""


class Probe:
    def __init__(self, page, folder: Path):
        self.page = page
        self.folder = folder
        self.count = 0
        self.steps: list[dict] = []
        self.network: list[dict] = []
        self.notes: dict = {}
        self.errors: list[str] = []
        self.popups: list = []
        page.context.on("response", self._on_response)
        page.context.on("page", lambda p: self.popups.append(p))

    def _on_response(self, response) -> None:
        try:
            req = response.request
            ctype = response.headers.get("content-type", "")
            if "/Content/" in response.url or re.search(r"\.(css|woff2?|ttf|gif|png|ico)(\?|$)", response.url, re.I):
                return
            if req.resource_type in ("font", "stylesheet") or "recaptcha" in response.url or "google" in response.url:
                return
            entry = {"t": round(time.time(), 1), "method": req.method, "type": req.resource_type,
                     "status": response.status, "content_type": ctype[:60], "url": response.url[:500],
                     "post": (req.post_data or "")[:1500] if req.method == "POST" else None}
            self.network.append(entry)
            low = ctype.lower()
            if "json" in low or "pdf" in low or re.search(r"image/(tiff|jpeg|png)", low) or "octet" in low:
                body = response.body()
                ext = "json" if "json" in low else "pdf" if "pdf" in low else low.split("/")[-1].split(";")[0]
                path = self.folder / f"respuesta_{len(self.network):03d}.{ext}"
                path.write_bytes(body)
                entry["saved_as"] = path.name
                if ext != "json":
                    print(f"  Archivo capturado: {path.name} ({len(body) // 1024} KB, {ctype})")
        except Exception as exc:  # el registro nunca debe frenar la exploración
            self.errors.append(f"response: {exc}")

    def save(self, label: str, page=None) -> dict:
        page = page or self.page
        self.count += 1
        stem = f"{self.count:02d}_{label}"
        inventory: dict = {}
        try:
            (self.folder / f"{stem}.html").write_text(page.content(), encoding="utf-8")
        except Exception as exc:
            self.errors.append(f"{stem} html: {exc}")
        try:
            page.screenshot(path=str(self.folder / f"{stem}.png"), full_page=True)
        except Exception as exc:
            self.errors.append(f"{stem} captura: {exc}")
        try:
            inventory = page.evaluate(INVENTORY_JS)
            (self.folder / f"{stem}.json").write_text(json.dumps(inventory, indent=2, ensure_ascii=False),
                                                       encoding="utf-8")
        except Exception as exc:
            self.errors.append(f"{stem} inventario: {exc}")
        self.steps.append({"step": stem, "url": page.url, "title": _safe(lambda: page.title())})
        print(f"  [{stem}] {page.url}")
        return inventory

    def fetch_text(self, url: str, name: str) -> str:
        result = self.page.evaluate(FETCH_TEXT_JS, urljoin(self.page.url, url))
        if result.get("error"):
            self.errors.append(f"{name}: {result['error']}")
            return ""
        (self.folder / name).write_text(result["text"], encoding="utf-8")
        print(f"  guardado {name} ({result['status']}, {len(result['text'])} caracteres)")
        return result["text"]

    def finish(self) -> None:
        summary = {"steps": self.steps, "notes": self.notes, "errors": self.errors, "network": self.network}
        (self.folder / "resumen.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False),
                                                   encoding="utf-8")


def search(page, probe: Probe, url: str, name: str, tag: str, hidden: dict | None = None) -> bool:
    """Búsqueda por nombre ("Apellido, Nombre"). `hidden`: valores para los campos ocultos."""
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    _safe(lambda: page.locator("#radio_0").check())
    page.fill("#name", name)
    if hidden:
        # search.js copia los valores al enviar; se fijan en el formulario y, por si los
        # pisa, también justo antes del envío.
        page.evaluate("""(h) => {
            const form = document.querySelector('form.searchform');
            const put = () => { for (const [k, v] of Object.entries(h)) {
                const el = form.querySelector('[name="' + k + '"]'); if (el) el.value = v; } };
            put();
            form.addEventListener('submit', put, true);
        }""", hidden)
        probe.notes[f"ocultos_{tag}"] = hidden
    page.wait_for_timeout(500)
    page.get_by_role("button", name=re.compile(r"^\s*search\s*$", re.I)).first.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(4000)
    if page.locator("iframe[src*='recaptcha']:visible").count():
        probe.notes[f"captcha_{tag}"] = True
        print("\n  >>> Hay un CAPTCHA: resuélvelo en la ventana. Espero 2 minutos.")
        page.wait_for_url(re.compile(r"(?i)CaseSearch|/Details/"), timeout=120_000)
        page.wait_for_timeout(3000)
    text = _safe(lambda: page.inner_text("body"), "")
    found = re.search(r"Name\s+(.+?)\s+(\d+)\s+[\d.]+\s*seconds", text)
    probe.notes[f"casos_encontrados_{tag}"] = found.group(2) if found else None
    probe.save(f"resultados_{tag}")
    print(f"  {tag}: {probe.notes[f'casos_encontrados_{tag}']} casos")
    return True


def run(args) -> int:
    load_dotenv()
    if args.local:
        os.environ["BROWSER_PROVIDER"] = "local"
    from core.browser import open_browser, provider_name

    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "probe_benchmark" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Navegador: {provider_name()}  |  {args.url}")
    print(f"Guardando en {folder.resolve()}\n")

    with open_browser() as session:
        page = session.page
        probe = Probe(page, folder)
        probe.notes.update(provider=session.provider, args=vars(args))
        try:
            # 1. Búsqueda del caso conocido.
            search(page, probe, args.url, args.name, "principal")
            for script in SCRIPTS:
                probe.fetch_text(site_root(args.url) + script, "script_" + script.split("/")[-1])

            # 2. Resumen desplegable del caso (lo que trae el "+" de la fila).
            link = page.locator(f"a[title='View Case Details for {args.case}']").first
            if not link.count():
                probe.errors.append(f"No encontré el caso {args.case} en los resultados")
                return 1
            row = link.locator("xpath=ancestor::tr[1]")
            onclick = _safe(lambda: row.locator("img[onclick*='details(']").first.get_attribute("onclick"), "")
            probe.notes["desplegable_onclick"] = onclick
            m = re.search(r"details\((\d+),\s*'([^']+)',\s*'([^']+)'", onclick or "")
            if m:
                probe.fetch_text(f"{site_root(args.url)}{m.group(2)}{m.group(1)}{m.group(3)}", "resumen_desplegable.html")

            # 3. El caso, sus cargos y el documento.
            link.click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(5000)
            probe.save("caso")
            if page.locator("#tabCharges").count():
                page.locator("#tabCharges").click()
                page.wait_for_timeout(4000)
                probe.save("caso_cargos")
                _safe(lambda: page.locator("#tabSummary").click())
                page.wait_for_timeout(4000)
            icons = page.locator("a.casedocketimage")
            target = None
            for i in range(icons.count()):
                icon = icons.nth(i)
                entry = _safe(lambda icon=icon: page.inner_text(f"#text_{icon.get_attribute('id')}"), "")
                if re.search(args.doc, entry or "", re.I):
                    target = icon
                    probe.notes["documento"] = {"id": icon.get_attribute("id"), "digest": icon.get_attribute("digest"),
                                                "entrada": re.sub(r"\s+", " ", entry).strip()}
                    break
            if target is None:
                probe.errors.append(f"No hay en el docket un documento con '{args.doc}'")
            else:
                print(f"  documento: {probe.notes['documento']}")
                before = len(probe.popups)
                target.click()
                page.wait_for_timeout(10000)
                probe.save("visor")
                for popup in probe.popups[before:]:
                    _safe(lambda popup=popup: popup.wait_for_load_state("domcontentloaded"))
                    popup.wait_for_timeout(6000)
                    probe.save("visor_pestana", popup)
                # Botones del visor (descargar, imprimir) para ver qué piden.
                probe.notes["visor_botones"] = _safe(lambda: page.evaluate(
                    "() => Array.from(document.querySelectorAll('a, button')).filter(e => e.offsetParent)"
                    ".map(e => [e.tagName, e.id, e.className, (e.innerText || e.title || '').trim().slice(0, 40),"
                    " e.getAttribute('href')]).filter(x => /download|print|pdf|save|image/i.test(x.join(' ')))"), [])

            # 4. Nombre común: normal y con fechas de apertura en los campos ocultos.
            if args.name2:
                search(page, probe, args.url, args.name2, "comun")
                search(page, probe, args.url, args.name2, "comun_fechas",
                       {"openedFrom": args.opened_from, "openedTo": args.opened_to})
                search(page, probe, args.url, args.name2, "comun_tipos",
                       {"courtTypes": args.court_types, "partyTypes": "DEFENDANT"})
            print("\nListo.")
            return 0
        except Exception as exc:
            probe.errors.append(f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
            _safe(lambda: probe.save("error"))
            return 1
        finally:
            probe.finish()
            print(f"\nCarpeta: {folder.resolve()}")
            print("Comprímela y envíala (tiene resumen.json, HTML, capturas, scripts y lo que se capturó).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Exploración del portal BenchmarkWeb")
    parser.add_argument("--url", default=BASE, help="página de búsqueda del condado")
    parser.add_argument("--name", default="Garcia, Kathryn", help='"Apellido, Nombre"')
    parser.add_argument("--case", default="2026 CF 003520", help="número de caso a abrir")
    parser.add_argument("--doc", default=r"affidavit of arrest warrant|booking document|arrest affidavit",
                        help="texto del documento a abrir (regex)")
    parser.add_argument("--name2", default="Jones, James", help="nombre común ('' para omitir)")
    parser.add_argument("--opened-from", default="01/01/2026")
    parser.add_argument("--opened-to", default="10/31/2026")
    parser.add_argument("--court-types", default="CF,MM")
    parser.add_argument("--local", action="store_true", help="usar Chrome local en vez de Browserbase")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
