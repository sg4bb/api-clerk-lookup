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
DEFAULT_MODEL = "google/gemma-4-31b-it"

SYSTEM_PROMPT = """You extract facts from a U.S. local news article about a crime. \
The goal is to find the court case of the person who was ARRESTED or CHARGED.

Rules:
1. people: include ONLY people who were arrested, charged, or identified by police as the suspect. \
Never include victims, witnesses, officers, deputies, attorneys, judges, relatives or officials. \
Each person is an OBJECT (see the template), never a plain string. \
Copy names exactly as written. Put Jr./Sr./III in "suffix", not in the last name. \
Keep compound surnames whole (e.g. "Cruz Peraza"). Hispanic names with two surnames go entirely \
in last_name ("Javier Rosado Martinez" -> first_name "Javier", last_name "Rosado Martinez", middle_name null). \
role: "arrested" if the article says the person was arrested, taken into custody or booked; \
"charged" if charged but not arrested; "suspect" only if police are still looking for them. \
age: the number from phrases like "23-year-old" or "Name, 23,". \
"evidence" must be a verbatim sentence from the article naming this person.
2. incident_date: the day the crime happened, ALWAYS as YYYY-MM-DD (never a weekday name). \
It is NOT the publication date and NOT a court date. For relative days ("Friday", "last night", \
"Tuesday morning") use the calendar of recent days given with the article. \
If the article covers several crimes, use the date of the crime that led to the arrest. \
Put the exact words you used in incident_date_evidence. Use null if it cannot be determined. \
If the article only gives the month or the year (e.g. "in June 2023", "back in 2022"), use the first day \
of that month or year and set incident_date_precision to "month" or "year"; never invent a day. \
Otherwise set incident_date_precision to "day".
3. arrest_date: YYYY-MM-DD, only if stated or clearly implied (e.g. "arrived as he was leaving \
the store ... taken into custody" means the same day as the incident); otherwise null.
4. agency: the agency that made the arrest (e.g. "Orlando Police Department").
5. city: where the crime happened (datelines like "ORLANDO, Fla. -" count). state: two letters ("FL"). \
county: if not stated, use the county of that city (Orlando -> Orange).
6. charges: list each charge as the article words it.
7. Never guess. Use null or an empty list when the article does not say it.

Respond only with a JSON object with exactly this shape:
{"people": [{"first_name": "...", "middle_name": null, "last_name": "...", "suffix": null, "age": 23, \
"role": "arrested", "evidence": "..."}], "incident_date": "YYYY-MM-DD", "incident_date_evidence": "...", "incident_date_precision": "day", \
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
        raise ExtractionError("The openai library is missing: python -m pip install -r requirements.txt") from exc

    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not api_key:
        raise ExtractionError("NVIDIA_API_KEY is missing from the .env file")
    client = OpenAI(base_url=os.getenv("NVIDIA_BASE_URL", DEFAULT_BASE_URL), api_key=api_key,
                    timeout=float(os.getenv("NVIDIA_TIMEOUT_S", "180")), max_retries=0)
    models = model_list()
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
            response, used = _create_with_retry(client, models, messages=messages, temperature=0,
                                                max_tokens=max_tokens, **extra)
        except BadRequestError as exc:
            log.info("El API no acepta el modo %s (%s); pruebo el siguiente", name, _short(exc))
            last_error = exc
            continue
        content = response.choices[0].message.content or ""
        log.info("Respuesta del modelo en %.0f s (modo %s)", time.monotonic() - started, name)
        try:
            result = Extraction.model_validate(_parse_json(content))
            log.info("Extracción lista (modelo %s, modo %s)", used, name)
            return result
        except (ValueError, ValidationError) as exc:
            log.warning("Respuesta inválida del modelo (modo %s): %s", name, _short(exc))
            last_error = exc
    raise ExtractionError(f"The AI model did not return valid data: {_short(last_error)}")


RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


GONE_STATUS = {404, 410}   # modelo inexistente o retirado ("end of life")
_GONE: set[str] = set()    # retirados detectados en esta corrida (no se vuelven a pedir)


def model_list() -> list[str]:
    """NVIDIA_MODEL primero y después los de NVIDIA_FALLBACK_MODEL (separados por coma)."""
    primary = os.getenv("NVIDIA_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    fallbacks = [m.strip() for m in os.getenv("NVIDIA_FALLBACK_MODEL", "").split(",") if m.strip()]
    return [primary] + [m for m in fallbacks if m != primary]


def _create_with_retry(client, models: list[str], **kwargs):
    """Llama al API esperando y reintentando cuando NVIDIA está saturado.

    El plan gratuito responde 503 "Worker local total request limit reached"
    cuando el modelo tiene todas sus plazas ocupadas; suele liberarse en
    segundos o minutos. Primero prueba los modelos de respaldo, sin esperar;
    si todos están saturados espera cada vez más (10 s, 20 s, 40 s, 60 s...)
    hasta NVIDIA_RETRY_MAX_S en total. Un modelo retirado (404/410) se saca
    de la lista con un aviso. Los errores 400 (parámetro no aceptado) suben tal cual.
    """
    from openai import APIConnectionError, APIStatusError, APITimeoutError, BadRequestError

    models = [m for m in models if m not in _GONE] or list(models)
    budget = float(os.getenv("NVIDIA_RETRY_MAX_S", "300"))
    deadline = time.monotonic() + budget
    wait, attempt, index = 10.0, 0, 0
    while True:
        model = models[index % len(models)]
        attempt += 1
        try:
            return client.chat.completions.create(model=model, **kwargs), model
        except BadRequestError:
            raise
        except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
            status = getattr(exc, "status_code", None)
            if status in GONE_STATUS:
                log.warning("El modelo %s no está disponible en NVIDIA (%s): %s", model, status, _short(exc))
                models.remove(model)
                _GONE.add(model)
                if not models:
                    raise ExtractionError(
                        f"The model {model} does not exist or was retired by NVIDIA ({status}). "
                        "Set NVIDIA_MODEL in .env to one from build.nvidia.com.") from exc
                continue
            if status is not None and status not in RETRYABLE_STATUS:
                raise ExtractionError(f"NVIDIA rejected the request ({status}): {_short(exc)}") from exc
            index += 1
            reason = f"error {status}" if status else type(exc).__name__
            if index % len(models):
                log.warning("NVIDIA no disponible con %s (%s); pruebo %s", model, reason, models[index % len(models)])
                continue
            pause = wait
            retry_after = getattr(getattr(exc, "response", None), "headers", {}).get("retry-after", "")
            if retry_after.isdigit():
                pause = max(pause, float(retry_after))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExtractionError(
                    f"NVIDIA is still overloaded after {budget:.0f} s and {attempt} attempt(s): {_short(exc)}. "
                    "Try again in a few minutes (or raise NVIDIA_RETRY_MAX_S).") from exc
            pause = min(pause, remaining)
            log.warning("NVIDIA no disponible con %s (%s); reintento en %.0f s", model, reason, pause)
            time.sleep(pause)
            wait = min(wait * 2, 60.0)


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
    if exc is None:
        return "sin detalle"
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        detail = inner.get("detail") or inner.get("message")
        if detail:
            return str(detail).replace("\n", " ")[:300]
    return str(exc).replace("\n", " ")[:200]
