"""Fase 3: worker que procesa la tabla lookup_jobs de Supabase.

    python worker.py            # queda escuchando la cola (Ctrl+C para salir)
    python worker.py --once     # procesa un pedido (si hay) y sale

Por cada pedido pendiente: lo toma, corre el proceso completo (leer el
artículo, extraer con la IA, buscar en el portal, descargar el PDF), va
escribiendo el avance en la fila y al final sube el PDF al bucket privado.

El CAPTCHA:
  - BROWSER_PROVIDER=browserbase: escribe live_view_url en la fila y pone el
    status en 'awaiting_captcha'; el dashboard muestra ese enlace en un iframe.
  - BROWSER_PROVIDER=local: se resuelve en la ventana de Chrome de esta PC.

Procesa un pedido a la vez.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.jobs import JobsError, JobStore, now_iso
from core.pipeline import PipelineResult, run_article

log = logging.getLogger("worker")

# Texto que el sitio muestra tal cual en cada etapa (en inglés: es el idioma del sitio).
STATUS_TEXT = {
    "fetching": "Reading the article…",
    "extracting": "Extracting the details from the article…",
    "searching": "Searching the county court portal…",
    "downloading": "Downloading the document…",
}
FINAL_TEXT = {
    "found": "Document found.",
    "no_document": "The case was found, but it has no downloadable document.",
    "not_found": "No case matching the article was found.",
    "ambiguous": "Several cases match and none stands out.",
    "captcha_timeout": "The CAPTCHA was not solved in time.",
}

_stop = False


def _ask_stop(*_: Any) -> None:
    global _stop
    _stop = True
    log.info("Cierre pedido: termino el pedido actual y salgo")


def _short(message) -> str:
    """Primera línea del mensaje, acotada: es lo que verá el usuario."""
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    return lines[0][:400] if lines else ""


def handle(store: JobStore, job: dict[str, Any], base_output: Path) -> str:
    job_id = job["id"]
    log.info("Pedido %s: %s", job_id, job["article_url"])

    def set_status(status: str) -> None:
        store.update(job_id, status=status, status_detail=STATUS_TEXT.get(status))

    def captcha_waiting(live_url, timeout_s: int) -> None:
        detail = ("Solve the CAPTCHA to continue." if live_url
                  else "Solve the CAPTCHA in the worker's Chrome window.")
        store.update(job_id, status="awaiting_captcha", status_detail=detail,
                     live_view_url=live_url, captcha_expires_at=now_iso(timeout_s))
        log.info("Esperando el CAPTCHA (%s)", "live view publicado en el job" if live_url else "ventana local")

    def captcha_done() -> None:
        store.update(job_id, status="searching", status_detail=STATUS_TEXT["searching"],
                     live_view_url=None, captcha_expires_at=None)

    try:
        result = run_article(job["article_url"], base_output / job_id,
                             suspect_index=max(0, int(job.get("suspect_index") or 1) - 1),
                             article_text=job.get("article_text"),
                             on_status=set_status,
                             on_captcha_waiting=captcha_waiting, on_captcha_done=captcha_done)
    except Exception as exc:   # nada debe tumbar al worker
        log.exception("Fallo inesperado en el pedido %s", job_id)
        result = PipelineResult(status="error", message=f"{type(exc).__name__}: {exc}")

    fields: dict[str, Any] = {
        "status": result.status,
        "status_detail": _short(result.message) or FINAL_TEXT.get(result.status),
        "live_view_url": None, "captcha_expires_at": None, "finished_at": now_iso(),
        "article_title": result.article_title,
        "article_published": str(result.article_published) if result.article_published else None,
        "extraction": result.extraction, "query": result.query,
    }
    lookup = result.lookup
    if lookup:
        fields.update(result=lookup.to_dict(), case_number=lookup.case_number, document_name=lookup.document,
                      confidence=lookup.score, related_cases=lookup.related_cases or [])
        if result.pdf_path and result.pdf_path.exists():
            try:
                fields["pdf_path"] = store.upload_pdf(job_id, result.pdf_path)
            except JobsError as exc:
                log.error("No pude subir el PDF: %s", exc)
                fields.update(status="error", status_detail=f"The document was downloaded but could not be saved: {exc}")
    if fields["status"] == "found" and not fields.get("status_detail"):
        fields["status_detail"] = FINAL_TEXT["found"]
    store.update(job_id, **fields)
    log.info("Pedido %s terminado: %s %s", job_id, fields["status"], fields.get("case_number") or "")
    return fields["status"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker de lookup_jobs")
    parser.add_argument("--once", action="store_true", help="procesar un pedido (si hay) y salir")
    parser.add_argument("--interval", type=float, default=float(os.getenv("WORKER_POLL_S", "4")),
                        help="segundos entre consultas a la cola")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    for noisy in ("httpx", "openai", "trafilatura", "htmldate", "courlan"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        store = JobStore()
    except JobsError as exc:
        print(f"ERROR: {exc}")
        return 2
    worker_id = os.getenv("WORKER_ID", "").strip() or f"{socket.gethostname()}-{os.getpid()}"
    base_output = Path(os.getenv("OUTPUT_DIR", "output")) / "jobs"
    signal.signal(signal.SIGINT, _ask_stop)

    provider = os.getenv("BROWSER_PROVIDER", "local").strip() or "local"
    log.info("Worker %s escuchando la cola (navegador: %s). Ctrl+C para salir.", worker_id, provider)
    last_cleanup = 0.0
    failures = 0
    while not _stop:
        try:
            if time.monotonic() - last_cleanup > 300:
                released = store.release_stale(int(os.getenv("WORKER_STALE_MIN", "20")))
                if released:
                    log.warning("%d pedido(s) colgado(s) marcados como error", released)
                last_cleanup = time.monotonic()
            job = store.claim(worker_id)
            failures = 0
        except (JobsError, OSError, Exception) as exc:   # red caída, Supabase reiniciando...
            failures += 1
            if failures == 1 or failures % 10 == 0:
                log.error("No pude consultar la cola: %s", exc)
            if args.once:
                return 1
            time.sleep(min(60, args.interval * failures))
            continue

        if job:
            handle(store, job, base_output)
        if args.once:
            if not job:
                log.info("No hay pedidos pendientes")
            break
        if not job:
            time.sleep(args.interval)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
