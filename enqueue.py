"""Crea un pedido en lookup_jobs y muestra su avance (simula al dashboard).

    python enqueue.py https://...            # crea el pedido y sigue su estado
    python enqueue.py mues                   # un id de article_cases.json
    python enqueue.py https://... --no-watch # solo lo crea
    python enqueue.py --list                 # últimos pedidos

Necesita un worker corriendo en otra ventana:  python worker.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

from core.captcha import LIVE_VIEW_PAGE
from core.jobs import FINAL, JobsError, JobStore


def resolve(target: str) -> str:
    if target.startswith(("http://", "https://")):
        return target
    cases = Path("article_cases.json")
    if cases.exists():
        for case in json.loads(cases.read_text(encoding="utf-8")):
            if case.get("id") == target:
                return case["url"]
    raise SystemExit(f"ERROR: '{target}' no es un link ni un id de article_cases.json")


def watch(store: JobStore, job_id: str, open_captcha: bool) -> int:
    last, shown_captcha = None, False
    folder = Path(os.getenv("OUTPUT_DIR", "output")) / "dashboard_sim"
    folder.mkdir(parents=True, exist_ok=True)
    page, status_file = folder / "live_view.html", folder / "live_view_status.js"
    while True:
        job = store.get(job_id)
        if not job:
            print("El pedido ya no existe")
            return 1
        state = (job["status"], job.get("status_detail"))
        if state != last:
            print(f"  [{time.strftime('%H:%M:%S')}] {job['status']:<17} {job.get('status_detail') or ''}")
            last = state
        if job["status"] == "awaiting_captcha" and job.get("live_view_url") and not shown_captcha:
            shown_captcha = True
            # Lo mismo que hará el dashboard: mostrar el enlace dentro de un recuadro.
            status_file.write_text("window.__clStatus = 'waiting';", encoding="utf-8")
            page.write_text(LIVE_VIEW_PAGE.format(url=job["live_view_url"]), encoding="utf-8")
            print(f"      CAPTCHA: abre {page.resolve()}")
            if open_captcha:
                webbrowser.open(page.resolve().as_uri())
        if job["status"] != "awaiting_captcha" and shown_captcha:
            shown_captcha = False
            status_file.write_text("window.__clStatus = 'done';", encoding="utf-8")   # el recuadro se cierra
        if job["status"] in FINAL:
            if job.get("case_number"):
                print(f"\n  Caso: {job['case_number']}  ({job.get('confidence')}% confianza)  {job.get('document_name') or ''}")
            if job.get("related_cases"):
                print(f"  Mismo arresto: {', '.join(job['related_cases'])}")
            if job.get("pdf_path"):
                print(f"  PDF en Storage: {job['pdf_path']}")
                try:
                    print(f"  Enlace (1 hora): {store.signed_url(job['pdf_path'])}")
                except JobsError as exc:
                    print(f"  (no pude firmar el enlace: {exc})")
            return 0 if job["status"] == "found" else 1
        time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Crear un pedido en lookup_jobs")
    parser.add_argument("target", nargs="?", help="link del artículo o id de article_cases.json")
    parser.add_argument("--suspect", type=int, default=1, help="qué persona usar si la nota nombra varias")
    parser.add_argument("--no-watch", action="store_true", help="no seguir el avance")
    parser.add_argument("--no-open", action="store_true", help="no abrir el CAPTCHA en el navegador")
    parser.add_argument("--list", action="store_true", help="mostrar los últimos pedidos")
    args = parser.parse_args()

    load_dotenv()
    try:
        store = JobStore()
        if args.list:
            for job in store.recent():
                print(f"  {job['created_at'][:19]}  {job['status']:<17} {job.get('case_number') or '-':<20} {job['article_url'][:70]}")
            return 0
        if not args.target:
            parser.error("falta el link del artículo")
        job = store.insert(resolve(args.target), suspect_index=args.suspect)
        print(f"Pedido creado: {job['id']}")
        if args.no_watch:
            return 0
        return watch(store, job["id"], open_captcha=not args.no_open)
    except JobsError as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
