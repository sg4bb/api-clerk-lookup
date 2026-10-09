"""Fase 2: link de un artículo -> incident report.

Uso (con el entorno virtual activado):
    python run_article.py --dry-run              # solo extrae (sin portal ni CAPTCHA) los casos de article_cases.json
    python run_article.py mues --dry-run         # solo el caso "mues"
    python run_article.py mues                   # extrae, busca en el portal y descarga el PDF
    python run_article.py https://...            # un link cualquiera
    python run_article.py https://... --suspect 2   # si el artículo nombra a varios arrestados
    python run_article.py mues --dry-run --model google/gemma-4-31b-it   # probar otro modelo
    python run_article.py mues --reuse           # reusa la extracción guardada (sin volver a llamar a NVIDIA)
    python run_article.py -i                     # modo interactivo: pega links uno tras otro (simula el worker)

Resultados en output/<id>/: article.txt, extraction.json, query.json,
result.json y el PDF. Conviene correr primero --dry-run y revisar
extraction.json: así los errores de extracción no gastan CAPTCHAs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.lookup import run_lookup
from run_local import verdict

CASES_FILE = Path("article_cases.json")


def load_targets(targets: list[str]) -> list[dict[str, Any]]:
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8")) if CASES_FILE.exists() else []
    by_id = {c["id"]: c for c in cases}
    chosen = []
    if not targets:
        return [c for c in cases if not c.get("skip") and _is_url(c.get("url", ""))]
    for target in targets:
        if _is_url(target):
            chosen.append({"id": _slug(target), "url": target})
        elif target in by_id:
            if not _is_url(by_id[target].get("url", "")):
                raise SystemExit(f"ERROR: el caso '{target}' no tiene un link válido en {CASES_FILE}")
            chosen.append(by_id[target])
        else:
            raise SystemExit(f"ERROR: '{target}' no es un link ni un id de {CASES_FILE}")
    return chosen


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _slug(url: str) -> str:
    """Nombre de carpeta a partir del link: el tramo más descriptivo de la ruta."""
    path = re.split(r"[?#]", url)[0]
    parts = [p for p in path.split("/")[3:] if p]
    words = [p for p in parts if "-" in p] or parts or ["articulo"]
    return re.sub(r"[^a-z0-9]+", "-", words[-1].lower())[:50].strip("-") or "articulo"


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def load_saved(out: Path):
    """article.txt + extraction.json de una corrida anterior (para --reuse)."""
    from extractor import Article, Extraction

    title, published, url, _, *body = (out / "article.txt").read_text(encoding="utf-8").split("\n")
    published = published.removeprefix("Publicado:").strip()
    article = Article(url=url, title=title, text="\n".join(body),
                      published=date.fromisoformat(published) if published not in ("", "None") else None,
                      via="guardado")
    extraction = Extraction.model_validate_json((out / "extraction.json").read_text(encoding="utf-8"))
    return article, extraction


def process(case: dict[str, Any], base_output: Path, dry_run: bool, suspect: int,
            reuse: bool = False) -> tuple[str, str]:
    from extractor import ExtractionError, FetchError, OutOfScope, build_query, extract, fetch_article

    out = base_output / case["id"]
    out.mkdir(parents=True, exist_ok=True)
    try:
        if reuse and (out / "extraction.json").exists() and (out / "article.txt").exists():
            print("  (reutilizo article.txt y extraction.json guardados; no llamo a NVIDIA)")
            article, extraction = load_saved(out)
        else:
            article = fetch_article(case["url"])
            (out / "article.txt").write_text(
                f"{article.title}\nPublicado: {article.published}\n{article.url}\n\n{article.text}",
                encoding="utf-8")
            published = article.published.isoformat() if article.published else None
            extraction = extract(article.text, article.title, published, article.url)
            _write(out / "extraction.json", extraction.model_dump())
        aq = build_query(extraction, article, suspect_index=case.get("suspect", suspect) - 1)
    except OutOfScope as exc:  # incluye 'unknown_location'
        # Otro estado u otro condado: se avisa sin abrir el portal (no gasta CAPTCHA).
        return "NO_SOPORTADO", str(exc)
    except (FetchError, ExtractionError) as exc:
        return "ERROR", str(exc)
    except Exception as exc:  # que un artículo con problemas no detenga los demás
        logging.exception("Error inesperado con %s", case["id"])
        return "ERROR", f"{type(exc).__name__}: {exc}"

    _write(out / "query.json", asdict(aq.query))
    q = aq.query
    print(f"\n  Artículo:   {article.title}  (publicado {article.published})")
    name = " ".join(x for x in (q.first_name, q.middle_name, q.last_name, aq.person.suffix) if x)
    print(f"  Persona:    {name}" + (f", {q.age} años" if q.age else "") + f"  [{aq.person.role}]")
    print(f"  Evidencia:  \"{aq.person.evidence}\"")
    if q.incident_date or not q.date_from:
        print(f"  Incidente:  {q.incident_date}  ({aq.extraction.incident_date_evidence or 'sin cita'})")
    else:
        print(f"  Incidente:  entre {q.date_from} y {q.date_to}  ({aq.extraction.incident_date_evidence})")
    print(f"  Arresto:    {q.arrest_date}")
    print(f"  Agencia:    {q.agency}   Condado: {q.county}")
    print(f"  Cargos:     {'; '.join(q.charges) or '-'}")
    for other in aq.others:
        print(f"  Otro:       {other.first_name} {other.last_name} [{other.role}] (usa --suspect para elegirlo)")
    for note in aq.notes:
        print(f"  AJUSTE:     {note}")
    for warning in aq.warnings:
        print(f"  AVISO:      {warning}")

    if dry_run:
        when = q.incident_date or (f"{q.date_from} a {q.date_to}" if q.date_from else "sin fecha")
        return "EXTRAÍDO", f"{q.first_name} {q.last_name}, incidente {when}"

    result = run_lookup(q, out)
    _write(out / "result.json", result.to_dict())
    mark = verdict(case, result)
    if result.case_number:
        detail = f"{result.case_number} ({result.score}% confianza)"
        detail += f" -> {result.pdf_path}" if result.pdf_path else (f" -> {result.document}" if result.document else "")
        if result.related_cases:
            detail += f"  [mismo arresto: {', '.join(result.related_cases)}]"
    else:
        detail = result.message or result.status
    if mark == "REVISAR":
        detail += f"  (esperado: {case.get('expected_status') or case.get('expected_case')})"
    return mark, detail


def interactive(base_output: Path, args) -> int:
    """Pide links por consola y procesa cada uno apenas se pega (como hará el worker con la tabla)."""
    mode = "solo extracción (--dry-run)" if args.dry_run else "extracción + portal + PDF"
    print(f"\nModo interactivo: {mode}. Pega un link y pulsa Enter; Enter vacío para salir.")
    while True:
        try:
            url = input("\nLink> ").strip().strip('"')
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not url:
            return 0
        if not _is_url(url):
            print("  Eso no parece un link (debe empezar con http:// o https://).")
            continue
        case = {"id": _slug(url), "url": url}
        mark, detail = process(case, base_output, args.dry_run, args.suspect, args.reuse)
        print(f"\n  => {mark}: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Artículo -> incident report")
    parser.add_argument("targets", nargs="*", help="links o ids de article_cases.json (por defecto, todos)")
    parser.add_argument("--dry-run", action="store_true", help="solo extraer datos, sin abrir el portal")
    parser.add_argument("--suspect", type=int, default=1, help="qué persona usar si hay varias (1, 2, ...)")
    parser.add_argument("--model", help="modelo de NVIDIA solo para esta corrida (reemplaza NVIDIA_MODEL)")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="pedir links por consola, uno tras otro, hasta dejarlo vacío")
    parser.add_argument("--reuse", action="store_true",
                        help="reusar el artículo y la extracción guardados en output/<id>/ (no llama a NVIDIA)")
    args = parser.parse_args()

    load_dotenv()
    if args.model:
        os.environ["NVIDIA_MODEL"] = args.model
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    for noisy in ("httpx", "openai", "trafilatura", "htmldate", "courlan"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    base_output = Path(os.getenv("OUTPUT_DIR", "output"))
    if args.interactive:
        return interactive(base_output, args)

    cases = load_targets(args.targets)
    if not cases:
        print(f"No hay casos con link en {CASES_FILE}")
        return 2

    summary = []
    for i, case in enumerate(cases, 1):
        print(f"\n{'#' * 60}\n  Artículo {i}/{len(cases)}: {case['id']}\n{'#' * 60}")
        summary.append((case["id"], *process(case, base_output, args.dry_run, args.suspect, args.reuse)))

    print(f"\n{'=' * 60}\n  RESUMEN\n{'=' * 60}")
    for case_id, mark, detail in summary:
        print(f"  {mark:<9} {case_id:<18} {detail}")
    print(f"\nResultados en {base_output}{os.sep}<id>{os.sep}")
    return 0 if all(m in ("OK", "FOUND", "EXTRAÍDO") for _, m, _ in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
