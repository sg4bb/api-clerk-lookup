"""Prueba del registro de la cárcel de Escambia (SmartWEB Jail), 10/11/2026.

No es parte del worker. Hace la misma búsqueda que hará el worker cuando Cloudflare bloquee el
portal de la corte (adapters/escambia_jail.py) y muestra qué booking elegiría: fecha, número de
booking, agencia, número de reporte y número de caso. Guarda el HTML de los resultados.

    python probe_jail.py --name "Rodriguez, Michael" --incident 07/21/2023 --age 40 --age-as-of 03/06/2025
    python probe_jail.py --name "Rodriguez, Michael" --incident 07/21/2023 --from 07/14/2023 --to 03/20/2025
    python probe_jail.py --name "Rodriguez, Michael" --incident 07/21/2023 --browserbase
    python probe_jail.py --name "Blackburn, Alyssa" --incident 10/29/2023 --published 10/10/2025 --charges "murder"

Por defecto usa el Chrome de tu PC (no gasta Browserbase). No toca el login de la página.
Todo queda en output/probe_jail/<fecha>/ (output/ no se sube a git).
"""

from __future__ import annotations

import argparse
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


def _date(value: str):
    return datetime.strptime(value, "%m/%d/%Y").date() if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba del registro de la cárcel de Escambia")
    parser.add_argument("--name", required=True, help='"Apellido, Nombre"')
    parser.add_argument("--middle", default="", help="segundo nombre (opcional)")
    parser.add_argument("--incident", default="", help="fecha del hecho (MM/DD/YYYY)")
    parser.add_argument("--age", type=int, default=None, help="edad que da la nota (opcional)")
    parser.add_argument("--age-as-of", default="", help="fecha de esa edad, la de la nota (MM/DD/YYYY)")
    parser.add_argument("--charges", default="", help='cargos de la nota separados por ";" (tolerancia por cargos)')
    parser.add_argument("--published", default="", help="fecha de la nota (MM/DD/YYYY; si falta, la de --age-as-of)")
    parser.add_argument("--from", dest="begin", default="", help="fecha de booking desde (MM/DD/YYYY)")
    parser.add_argument("--to", dest="end", default="", help="fecha de booking hasta (MM/DD/YYYY)")
    parser.add_argument("--browserbase", action="store_true", help="Browserbase en vez del Chrome de tu PC")
    args = parser.parse_args()
    last, _, first = (p.strip() for p in args.name.partition(","))

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="  %(message)s")      # por qué se descarta cada booking
    os.environ["BROWSER_PROVIDER"] = "browserbase" if args.browserbase else "local"
    from adapters.escambia_jail import JAIL_URL, pick_booking, search_jail
    from core.browser import open_browser
    from core.models import LookupQuery

    query = LookupQuery(first_name=first, last_name=last, middle_name=args.middle or None, county="escambia",
                        incident_date=_date(args.incident), age=args.age, age_as_of=_date(args.age_as_of),
                        published_date=_date(args.published or args.age_as_of),
                        charges=[c.strip() for c in args.charges.split(";") if c.strip()])
    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "probe_jail" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Navegador: {os.environ['BROWSER_PROVIDER']}  |  {JAIL_URL}\nGuardando en {folder.resolve()}\n")
    with open_browser() as session:
        page = session.page
        if session.live_view_url:
            print(f"Live view: {session.live_view_url}\n")
        try:
            bookings = search_jail(page, query, _date(args.begin), _date(args.end))
            (folder / "resultados.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(folder / "resultados.png"), full_page=True)
            print(f"{len(bookings)} booking(s):")
            for b in bookings:
                report = b.report
                print(f"  {b.booked:%m/%d/%Y %I:%M %p}  {b.booking_number}  {b.name} ({b.age})  "
                      f"reporte {report.number + ' (' + report.agency + ')' if report else '-'}  "
                      f"caso {b.court_case or '-'}  cargos: {'; '.join(c.charge for c in b.charges)[:80]}")
            chosen = pick_booking(bookings, query, 60)
            print(f"\nElegido: {chosen.booking_number + ' del ' + f'{chosen.booked:%m/%d/%Y}' if chosen else 'ninguno'}"
                  + (f"  (por los cargos; la nota dice {chosen.story_date:%m/%d/%Y})" if chosen and chosen.story_date else ""))
        except Exception:
            print(traceback.format_exc())
            (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            try:
                (folder / "error.html").write_text(page.content(), encoding="utf-8")
                page.screenshot(path=str(folder / "error.png"), full_page=True)
            except Exception:
                pass
    print(f"\nCarpeta: {folder.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
