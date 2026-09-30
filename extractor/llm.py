"""Extracción de datos con NVIDIA NIM (API compatible con OpenAI).

La librería `openai` solo se usa como cliente: la conexión va a los servidores
de NVIDIA con NVIDIA_API_KEY. Para que el modelo responda siempre con la
estructura de schema.Extraction se usa `guided_json` (recomendado por NVIDIA
en lugar del modo JSON libre). Como su forma exacta varía entre el API
alojado y un NIM propio, se prueban variantes en orden hasta que una funcione.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, timedelta
from typing import Optional

from pydantic import ValidationError

from .schema import Extraction

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"

SYSTEM_PROMPT = """You extract facts from a U.S. local news article about a crime. \
The goal is to find the court case of the person who was ARRESTED or CHARGED.

Rules:
1. people: include ONLY people who were arrested, charged, or identified by police as the suspect. \
Never include victims, witnesses, officers, deputies, attorneys, judges, relatives or officials. \
Each person is an OBJECT (see the template), never a plain string. \
Copy names exactly as written. Put Jr./Sr./III in "suffix", not in the last name. \
Keep compound surnames whole (e.g. "Cruz Peraza"). \
role: "arrested" if the article says the person was arrested, taken into custody or booked; \
"charged" if charged but not arrested; "suspect" only if police are still looking for them. \
age: the number from phrases like "23-year-old" or "Name, 23,". \
"evidence" must be a verbatim sentence from the article naming this person.
2. incident_date: the day the crime happened, ALWAYS as YYYY-MM-DD (never a weekday name). \
It is NOT the publication date and NOT a court date. For relative days ("Friday", "last night", \
"Tuesday morning") use the calendar of recent days given with the article. \
If the article covers several crimes, use the date of the crime that led to the arrest. \
Put the exact words you used in incident_date_evidence. Use null if it cannot be determined.
3. arrest_date: YYYY-MM-DD, only if stated or clearly implied (e.g. "arrived as he was leaving \
the store ... taken into custody" means the same day as the incident); otherwise null.
4. agency: the agency that made the arrest (e.g. "Orlando Police Department").
5. city: where the crime happened (datelines like "ORLANDO, Fla. -" count). state: two letters ("FL"). \
county: if not stated, use the county of that city (Orlando -> Orange).
6. charges: list each charge as the article words it.
7. Never guess. Use null or an empty list when the article does not say it.

Respond only with a JSON object with exactly this shape:
{"people": [{"first_name": "...", "middle_name": null, "last_name": "...", "suffix": null, "age": 23, \
"role": "arrested", "evidence": "..."}], "incident_date": "YYYY-MM-DD", "incident_date_evidence": "...", \
"arrest_date": "YYYY-MM-DD", "city": "...", "county": "...", "state": "FL", "agency": "...", \
"charges": ["..."], "summary": "..."}"""

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def recent_days(published: date) -> str:
    """Calendario de la semana previa a la publicación, para resolver "el viernes"."""
    lines = []
    for back in range(8):
        day = published - timedelta(days=back)
        label = " (publication day)" if back == 0 else " (yesterday)" if back == 1 else ""
        lines.append(f"  {WEEKDAYS[day.weekday()]} = {day.isoformat()}{label}")
    return "\n".join(lines)


class ExtractionError(Exception):
    pass


def extract(article_text: str, title: str, published: Optional[str], url: str) -> Extraction:
    try:
        from openai import BadRequestError, OpenAI
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("Falta la librería openai: python -m pip install -r requirements.txt") from exc

    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not api_key:
        raise ExtractionError("Falta NVIDIA_API_KEY en el archivo .env")
    client = OpenAI(base_url=os.getenv("NVIDIA_BASE_URL", DEFAULT_BASE_URL), api_key=api_key,
                    timeout=float(os.getenv("NVIDIA_TIMEOUT_S", "180")), max_retries=2)
    model = os.getenv("NVIDIA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    # Es un modelo de razonamiento: piensa antes de responder y eso consume tokens.
    max_tokens = int(os.getenv("NVIDIA_MAX_TOKENS", "8192"))
    schema = Extraction.model_json_schema()
    header = f"Publication date: {published or 'unknown'}"
    if published:
        header += f" ({WEEKDAYS[date.fromisoformat(published).weekday()]})\n"
        header += "Recent days (most recent occurrence on or before publication):\n"
        header += recent_days(date.fromisoformat(published))
    user = f"{header}\nTitle: {title}\nURL: {url}\n\nArticle text:\n{article_text}"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]

    # En el API alojado de NVIDIA funciona "guided_json" (el modo nvext responde 200
    # pero lo ignora). NVIDIA_JSON_MODE fija un modo si hiciera falta.
    variants = [
        ("guided_json", {"extra_body": {"guided_json": schema}}),
        ("nvext.guided_json", {"extra_body": {"nvext": {"guided_json": schema}}}),
        ("json_object", {"response_format": {"type": "json_object"}}),
        ("texto libre", {}),
    ]
    forced = os.getenv("NVIDIA_JSON_MODE", "").strip()
    if forced:
        variants = [v for v in variants if v[0] == forced] or variants
    last_error: Optional[Exception] = None
    for name, extra in variants:
        started = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, temperature=0, max_tokens=max_tokens, **extra)
        except BadRequestError as exc:
            log.info("El API no acepta el modo %s (%s); pruebo el siguiente", name, _short(exc))
            last_error = exc
            continue
        content = response.choices[0].message.content or ""
        log.info("Respuesta del modelo en %.0f s (modo %s)", time.monotonic() - started, name)
        try:
            result = Extraction.model_validate(_parse_json(content))
            log.info("Extracción lista (modelo %s, modo %s)", model, name)
            return result
        except (ValueError, ValidationError) as exc:
            log.warning("Respuesta inválida del modelo (modo %s): %s", name, _short(exc))
            last_error = exc
    raise ExtractionError(f"El modelo no devolvió datos válidos: {_short(last_error)}")


def _parse_json(content: str) -> dict:
    """JSON de la respuesta, tolerando <think>...</think> y bloques ```json."""
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return json.loads(text[start:end + 1])


def _short(exc: Optional[Exception]) -> str:
    return str(exc).replace("\n", " ")[:200] if exc else "sin detalle"
