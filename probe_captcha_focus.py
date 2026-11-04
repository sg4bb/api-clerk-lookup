"""Prueba del recuadro del CAPTCHA, en el Chrome de tu PC (no gasta Browserbase).

Hace con la página lo mismo que el worker en la ventana remota: la achica al tamaño del
recuadro de verificación (540 x 700), deja a la vista solo la casilla y tapa el resto. Tú
marcas la casilla y miras si el cuadro de imágenes se abre completo. No hace falta resolverlo.

    python probe_captcha_focus.py --url "https://courts.charlotteclerk.com/Benchmark/Home.aspx/Search"
    python probe_captcha_focus.py --url "..." --kind hcaptcha

Guarda capturas y el diagnóstico en output/probe_captcha_focus/<fecha>/.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BOX_SHOWN_JS = """() => [...document.querySelectorAll("iframe[src*='bframe'], iframe[src*='hcaptcha'][src*='challenge']")]
  .some(f => { const r = f.getBoundingClientRect(); const c = getComputedStyle(f);
               return r.width > 200 && r.height > 200 && c.visibility !== 'hidden' && r.top > -1000; })"""
BOX_RECT_JS = """() => [...document.querySelectorAll("iframe[src*='bframe'], iframe[src*='hcaptcha'][src*='challenge']")]
  .map(f => { const r = f.getBoundingClientRect();
              return {left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height)}; })"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba local del recuadro del CAPTCHA")
    parser.add_argument("--url", required=True, help="página con el CAPTCHA (el formulario de búsqueda)")
    parser.add_argument("--kind", default="recaptcha", choices=["recaptcha", "hcaptcha", "turnstile"])
    parser.add_argument("--wait", type=int, default=180, help="segundos para marcar la casilla y mirar")
    args = parser.parse_args()

    load_dotenv()
    os.environ["BROWSER_PROVIDER"] = "local"
    from core.browser import open_browser
    from core.captcha import CHECK_FOCUS_JS, FOCUS_CAPTCHA_JS, FOCUS_VIEWPORT

    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "probe_captcha_focus" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    with open_browser() as session:
        page = session.page
        page.goto(args.url, wait_until="load")
        page.wait_for_timeout(4000)                      # que cargue el CAPTCHA
        page.set_viewport_size(FOCUS_VIEWPORT)
        page.wait_for_timeout(800)
        info = page.evaluate(FOCUS_CAPTCHA_JS, {"message": "Tick the box to continue:", "kind": args.kind})
        page.wait_for_timeout(700)
        info["after"] = page.evaluate(CHECK_FOCUS_JS)
        page.screenshot(path=str(folder / "1_casilla.png"))
        print(f"Casilla en {info.get('box')}  (ajustes: {info.get('lift')})")
        print(f"\n>>> Marca la casilla en la ventana de Chrome y mira si el cuadro de imágenes se ve completo.")
        print(f"    No hace falta resolverlo. Espero hasta {args.wait} s.\n")
        try:
            page.wait_for_function(BOX_SHOWN_JS, timeout=args.wait * 1000, polling=500)
            page.wait_for_timeout(2500)
            info["challenge"] = page.evaluate(BOX_RECT_JS)
            page.screenshot(path=str(folder / "2_cuadro.png"))
            boxes = [b for b in info["challenge"] if b["width"] > 200]
            print(f"Cuadro de imágenes en {boxes}")
            fits = all(b["left"] >= 0 and b["left"] + b["width"] <= FOCUS_VIEWPORT["width"] for b in boxes)
            print("El cuadro cabe en la ventana." if fits else "El cuadro queda CORTADO.")
            page.wait_for_timeout(5000)
        except Exception:
            print("No vi abrirse el cuadro de imágenes (¿se marcó sin pedir imágenes?).")
        (folder / "diagnostico.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"\nCarpeta: {folder.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
