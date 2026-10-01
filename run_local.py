"""Fase 1: pruebas locales con casos definidos en test_cases.json.

Uso (con el entorno virtual activado):
    python run_local.py              # corre todos los casos (salvo los "skip")
    python run_local.py ward         # corre solo los casos indicados por id
    python run_local.py --check      # valida el JSON sin abrir el navegador

Cada caso abre su propio Chrome y pide su propio CAPTCHA. Los resultados
quedan en output/<id>/ (PDF + result.json).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from core.lookup import run_lookup
from core.models import LookupQuery

CASES_FILE = Path("test_cases.json")
# Campos del JSON que no son parte de LookupQuery.
META_FIELDS = {"id", "notes", "skip", "expected_case", "expected_status"}
QUERY_FIELDS = {f.name for f in fields(LookupQuery)}
DATE_FIELDS = {"incident_date", "arrest_date", "approx_date", "date_from", "date_to", "age_as_of", "published_date"}


class CaseFileError(Exception):
    pass


def build_query(raw: dict[str, Any]) -> LookupQuery:
    case_id = raw.get("id", "?")
    unknown = set(raw) - QUERY_FIELDS - META_FIELDS
    if unknown:
        raise CaseFileError(f"[{case_id}] campos desconocidos: {', '.join(sorted(unknown))}")
    for required in ("first_name", "last_name", "county"):
        if not raw.get(required):
            raise CaseFileError(f"[{case_id}] falta '{required}'")

    values = {k: v for k, v in raw.items() if k in QUERY_FIELDS and v is not None}
    for key in DATE_FIELDS & values.keys():
        try:
            values[key] = date.fromisoformat(values[key])
        except (TypeError, ValueError):
            raise CaseFileError(f"[{case_id}] '{key}' debe ser AAAA-MM-DD (recibido {values[key]!r})") from None
    if "age" in values and not isinstance(values["age"], int):
        raise CaseFileError(f"[{case_id}] 'age' debe ser un número entero")
    if not (values.get("incident_date") or values.get("arrest_date")):
        logging.warning("[%s] sin fechas: la búsqueda puede toparse con el límite de 500 resultados", case_id)
    return LookupQuery(**values)


def load_cases(selected: list[str]) -> list[tuple[dict[str, Any], LookupQuery]]:
    if not CASES_FILE.exists():
        raise CaseFileError(f"No existe {CASES_FILE}")
    try:
        raw_cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CaseFileError(f"{CASES_FILE} no es JSON válido (línea {exc.lineno}): {exc.msg}") from None

    ids = [c.get("id") for c in raw_cases]
    duplicated = {i for i in ids if ids.count(i) > 1}
    if None in ids or duplicated:
        raise CaseFileError(f"Cada caso necesita un 'id' único (repetidos: {duplicated or 'ninguno'})")
    missing = set(selected) - set(ids)
    if missing:
        raise CaseFileError(f"Ids no encontrados: {', '.join(sorted(missing))}")

    chosen = []
    for raw in raw_cases:
        if selected and raw["id"] not in selected:
            continue
        if not selected and raw.get("skip"):
            continue
        chosen.append((raw, build_query(raw)))
    return chosen


def verdict(raw: dict[str, Any], result) -> str:
    """OK = resultado esperado. SIN_PDF = caso correcto pero sin documento.

    expected_case puede ser una lista cuando varios casos son igual de válidos
    (p. ej. un CF y un MM del mismo arresto). expected_status permite pruebas
    negativas, p. ej. "not_found".
    """
    expected_status = raw.get("expected_status")
    expected = raw.get("expected_case")
    if expected_status:
        return "OK" if result.status == expected_status else "REVISAR"
    if expected is None:
        return result.status.upper()
    accepted = expected if isinstance(expected, list) else [expected]
    if result.case_number not in accepted:
        return "REVISAR"
    return "OK" if result.status == "found" else "SIN_PDF"


def main() -> int:
    parser = argparse.ArgumentParser(description="Pruebas locales de clerk-lookup")
    parser.add_argument("ids", nargs="*", help="ids de test_cases.json a correr (por defecto, todos)")
    parser.add_argument("--check", action="store_true", help="solo validar el JSON")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        cases = load_cases(args.ids)
    except CaseFileError as exc:
        print(f"ERROR: {exc}")
        return 2
    if not cases:
        print("No hay casos para correr (¿todos tienen 'skip'?)")
        return 2

    if args.check:
        for raw, query in cases:
            print(f"OK  {raw['id']}: {query}")
        return 0

    base_output = Path(os.getenv("OUTPUT_DIR", "output"))
    summary = []
    for i, (raw, query) in enumerate(cases, 1):
        print(f"\n{'#' * 60}\n  Caso {i}/{len(cases)}: {raw['id']}\n{'#' * 60}")
        output_dir = base_output / raw["id"]
        result = run_lookup(query, output_dir)
        data = result.to_dict()
        (output_dir / "result.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        summary.append((raw, result))

    print(f"\n{'=' * 60}\n  RESUMEN\n{'=' * 60}")
    all_ok = True
    for raw, result in summary:
        case_id, expected = raw["id"], raw.get("expected_case")
        mark = verdict(raw, result)
        all_ok &= mark in ("OK", "FOUND")
        if result.case_number:
            detail = f"{result.case_number} ({result.score}% confianza)"
            detail += f" -> {result.document}" if result.document else ""
        else:
            detail = result.message or ""
        print(f"  {mark:<9} {case_id:<18} {detail}")
        if result.related_cases:
            print(f"            mismo arresto: {', '.join(result.related_cases)}")
        if mark == "REVISAR":
            wanted = raw.get("expected_status") or expected
            print(f"            esperado: {wanted}  (obtenido: {result.status})")
        if result.status == "no_document":
            downloadable = [e for e in result.docket if e["downloadable"]]
            print(f"            documentos descargables ({len(downloadable)} de {len(result.docket)};"
                  f" el docket completo está en result.json):")
            for e in downloadable:
                print(f"              {e['date']}  {e['description']}")
    print(f"\nResultados en {base_output}{os.sep}<id>{os.sep}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
