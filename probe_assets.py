"""Prueba corta: ¿el portal carga completo o Cloudflare le niega los scripts y la imagen del CAPTCHA?

Escambia (10/11/2026): en Browserbase la página pasa, pero el bundle de scripts y la imagen de la
suma vuelven con 403 y el formulario queda sin estilos ni campo de nombre. A ti te pasó lo mismo
en tu navegador y se arregló con Ctrl+Shift+R. Esto abre la página, espera a que Cloudflare termine
su revisión, y recarga sin caché hasta 3 veces, anotando qué recursos fallan en cada carga. Se
cierra apenas la página carga completa.

    python probe_assets.py --url "https://public.escambiaclerk.com/BMWebLatest/Home.aspx/Search"
    python probe_assets.py --url "..." --local

Guarda capturas, el HTML de las respuestas 403 y resumen.json en output/probe_assets/<fecha>/.

Diagnóstico de Cloudflare (10/11/2026): en cada carga anota qué cookies del portal hay (solo los
NOMBRES, nunca los valores), si corrió la detección por JavaScript de Cloudflare (cdn-cgi/.../jsd) y
con qué respuesta, si apareció su verificación a página completa y si el navegador dice ser
automatizado (navigator.webdriver). Con --fresh, en local usa un perfil vacío (sin las cookies de
.browser-profile).

    python probe_assets.py --url "..." --local --fresh
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PAGE_STATE_JS = """() => {
  const img = document.querySelector("img[alt='Captcha'], img[src*='CaptchaImage']");
  const name = document.querySelector('#name');
  return {
    title: document.title,
    webdriver: navigator.webdriver,
    jquery: !!window.jQuery,
    captcha_image: img ? (img.complete && img.naturalWidth > 0) : null,
    name_field: !!name,          // en "All" el campo existe pero está oculto hasta elegir "Name"
    styled: getComputedStyle(document.body).fontFamily,
  };
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Recursos que el portal no entrega")
    parser.add_argument("--url", required=True)
    parser.add_argument("--local", action="store_true", help="Chrome local en vez de Browserbase")
    parser.add_argument("--loads", type=int, default=3, help="cargas como máximo")
    parser.add_argument("--fresh", action="store_true", help="local: perfil vacío, sin cookies guardadas")
    args = parser.parse_args()

    load_dotenv()
    os.environ["BROWSER_PROVIDER"] = "local" if args.local else "browserbase"
    if args.fresh:
        os.environ["BROWSER_PROFILE_DIR"] = ""            # perfil temporal, vacío
    from core.browser import open_browser

    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "probe_assets" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    report: dict = {"url": args.url, "provider": os.environ["BROWSER_PROVIDER"], "loads": []}
    started = time.monotonic()
    failed: list[dict] = []
    cloudflare: list[str] = []
    saved_bodies = 0
    host = re.sub(r"^https?://([^/]+).*$", r"\1", args.url)

    def on_response(response):
        nonlocal saved_bodies
        if "/cdn-cgi/challenge-platform/" in response.url:
            kind = "jsd" if "/jsd/" in response.url else (
                "verificacion" if "chl_page" in response.url or "/fo/" in response.url else "otro")
            cloudflare.append(f"{kind} {response.status}")
        if response.status < 400:
            return
        item = {"status": response.status, "type": response.request.resource_type, "url": response.url[:160]}
        try:
            body = response.text()
            title = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
            item["title"] = title.group(1).strip()[:120] if title else None
            if saved_bodies < 3:
                saved_bodies += 1
                (folder / f"respuesta_{response.status}_{saved_bodies}.html").write_text(body, encoding="utf-8")
        except Exception:
            pass
        failed.append(item)

    try:
        with open_browser() as session:
            page = session.page
            if session.live_view_url:
                print(f"Live view: {session.live_view_url}\n")
            page.on("response", on_response)
            cdp = page.context.new_cdp_session(page)
            for n in range(1, args.loads + 1):
                failed.clear()
                cloudflare.clear()
                try:
                    if n == 1:
                        page.goto(args.url, wait_until="load")
                    else:
                        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})     # como Ctrl+Shift+R
                        page.reload(wait_until="load")
                except Exception as exc:
                    # La verificación de Cloudflare puede quedarse girando sin terminar de cargar.
                    print(f"  carga {n}: no terminó de cargar en 30 s ({type(exc).__name__}); miro lo que quedó")
                page.wait_for_timeout(4000)
                try:
                    state = page.evaluate(PAGE_STATE_JS)
                except Exception:
                    page.wait_for_timeout(3000)                 # la página seguía navegando
                    state = page.evaluate(PAGE_STATE_JS)
                cookies = sorted({c["name"] for c in page.context.cookies()
                                  if host.endswith(c["domain"].lstrip("."))})
                load = {"carga": n, "s": round(time.monotonic() - started), "estado": state, "fallidos": list(failed),
                        "cookies": cookies, "cloudflare": list(cloudflare)}
                report["loads"].append(load)
                page.screenshot(path=str(folder / f"carga_{n}.png"))
                ok = state.get("jquery") and state.get("captcha_image") is not False and state.get("name_field") \
                    and "Times" not in (state.get("styled") or "")
                print(f"  carga {n}: {'COMPLETA' if ok else 'incompleta'}  jQuery={state.get('jquery')} "
                      f"suma={state.get('captcha_image')} campo nombre={state.get('name_field')}  "
                      f"fallidos={len(failed)}")
                for f in failed[:6]:
                    print(f"     {f['status']} {f['type']:<8} {f['url'][:90]}  {f.get('title') or ''}")
                print(f"     cookies del portal: {', '.join(cookies) or 'ninguna'}  |  webdriver={state.get('webdriver')}")
                print(f"     scripts de Cloudflare: {'; '.join(cloudflare) or 'ninguno'}")
                if ok:
                    report["resultado"] = f"completa en la carga {n}"
                    return 0
                page.wait_for_timeout(8000)          # que termine la revisión de Cloudflare
            report["resultado"] = "nunca cargó completa"
            return 0
    finally:
        report["segundos"] = round(time.monotonic() - started)
        (folder / "resumen.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nResultado: {report.get('resultado')}  ({report['segundos']} s)")
        print(f"Carpeta: {folder.resolve()}")


if __name__ == "__main__":
    raise SystemExit(main())
