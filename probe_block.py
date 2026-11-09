"""Prueba corta en Browserbase: ¿el portal bloquea al navegador remoto? (Clay, 08/11/2026)

En el Chrome local, Clay responde "Sorry, you have been blocked" (Cloudflare) y el reCAPTCHA
dice "Try again later". Esto abre la página en Browserbase y se cierra apenas sabe algo, para
no gastar minutos:

  1. Abre el formulario. Si Cloudflare bloquea, guarda la captura y termina.
  2. Con --name: elige "Name", escribe el nombre y espera a que resuelvas el CAPTCHA en el live
     view (enlace en la terminal). Busca una vez y dice si hubo resultados o bloqueo.

    python probe_block.py --url "https://inquiry.clayclerk.com/Home.aspx/Search"
    python probe_block.py --url "https://inquiry.clayclerk.com/Home.aspx/Search" --name "Binderim, Corey"

Guarda capturas, HTML y resumen.json en output/probe_block/<fecha>/.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from probe_benchmark import CAPTCHA_FILLED_JS, CAPTCHA_SLOT_JS, HAS_CAPTCHA_JS, RENDER_JS, RESULTS_JS

BLOCKED_JS = """() => /attention required/i.test(document.title)
  || /you have been blocked|unable to access/i.test((document.body || {}).innerText || '')"""
INVALID_JS = """() => /invalid captcha/i.test(location.href + ' ' + ((document.body || {}).innerText || ''))"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba corta de bloqueo en Browserbase")
    parser.add_argument("--url", required=True, help="formulario de búsqueda")
    parser.add_argument("--name", default="", help='"Apellido, Nombre" para una búsqueda (opcional)')
    parser.add_argument("--wait", type=int, default=180, help="segundos para resolver el CAPTCHA")
    args = parser.parse_args()

    load_dotenv()
    os.environ["BROWSER_PROVIDER"] = "browserbase"
    from core.browser import open_browser

    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "probe_block" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    report: dict = {"url": args.url, "name": args.name, "steps": []}
    started = time.monotonic()

    def save(page, label: str, verdict: str) -> None:
        try:
            page.screenshot(path=str(folder / f"{label}.png"))
            (folder / f"{label}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        report["steps"].append({"step": label, "url": page.url, "verdict": verdict,
                                "s": round(time.monotonic() - started)})
        print(f"  [{label}] {verdict}  ({page.url})")

    def check(page, js: str) -> bool:
        try:
            return bool(page.evaluate(js))
        except Exception:
            return False

    try:
        with open_browser() as session:
            page = session.page
            print(f"Browserbase listo. Live view: {session.live_view_url}\n")
            page.goto(args.url, wait_until="load")
            page.wait_for_timeout(5000)
            if check(page, BLOCKED_JS):
                save(page, "1_abrir", "BLOQUEADO por Cloudflare al abrir")
                report["resultado"] = "bloqueado_al_abrir"
                return 0

            drawn, how = False, None
            for attempt in (1, 2):
                try:
                    page.wait_for_function(HAS_CAPTCHA_JS, timeout=12_000, polling=500)
                    drawn, how = True, "solo" if attempt == 1 else "solo tras recargar"
                    break
                except Exception:
                    pass
                if not check(page, CAPTCHA_SLOT_JS):
                    break
                rendered = page.evaluate(RENDER_JS)
                print(f"  El recuadro del CAPTCHA no apareció; se lo pido al script de Google: {rendered}")
                report.setdefault("dibujar", []).append(rendered)
                try:
                    page.wait_for_function(HAS_CAPTCHA_JS, timeout=8_000, polling=500)
                    drawn, how = True, "pedido al script de Google"
                    break
                except Exception:
                    pass
                if attempt == 1:
                    print("  Sigue sin aparecer; recargo una vez")
                    page.reload(wait_until="load")
                    page.wait_for_timeout(4000)
            save(page, "1_abrir", f"formulario visible; CAPTCHA {'dibujado (' + how + ')' if drawn else 'NO dibujado'}")
            report["captcha_dibujado"] = drawn
            report["como_se_dibujo"] = how
            if not args.name:
                report["resultado"] = "abre_bien"
                return 0
            if not drawn:
                report["resultado"] = "sin_captcha"
                print("  Sin recuadro del CAPTCHA no busco (Clay bloquea una búsqueda sin él).")
                return 0

            radio = page.locator("input.radioButton[searchtype='Name']")
            if radio.count():
                radio.first.check()
                page.wait_for_timeout(700)
            page.fill("#name", args.name)
            print(f"\n  >>> Resuelve el CAPTCHA en el live view (enlace arriba). Espero hasta {args.wait} s.")
            print("      Si aparece 'Try again later', ciérralo: es la respuesta que buscamos.")
            try:
                page.wait_for_function(CAPTCHA_FILLED_JS, timeout=args.wait * 1000, polling=500)
            except Exception:
                save(page, "2_captcha", "CAPTCHA no resuelto a tiempo")
                report["resultado"] = "captcha_no_resuelto"
                return 0
            page.locator("#searchButton").first.click()
            try:
                page.wait_for_load_state("domcontentloaded")
            except Exception:
                pass
            page.wait_for_timeout(5000)
            if check(page, BLOCKED_JS):
                verdict, result = "BLOQUEADO por Cloudflare al buscar (con CAPTCHA resuelto)", "bloqueado_al_buscar"
            elif check(page, RESULTS_JS):
                verdict, result = "RESULTADOS: Browserbase sí pasa", "resultados"
            elif check(page, INVALID_JS):
                verdict, result = "Invalid Captcha", "invalid_captcha"
            else:
                verdict, result = "ni resultados ni bloqueo (ver captura)", "desconocido"
            save(page, "2_busqueda", verdict)
            report["resultado"] = result
            return 0
    finally:
        report["segundos"] = round(time.monotonic() - started)
        (folder / "resumen.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nResultado: {report.get('resultado')}  ({report['segundos']} s)")
        print(f"Carpeta: {folder.resolve()}")


if __name__ == "__main__":
    raise SystemExit(main())
