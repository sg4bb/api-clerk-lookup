"""Acceso a la cola lookup_jobs y al bucket de PDFs en Supabase.

Usa la API REST de Supabase directamente (PostgREST y Storage) con la clave
service_role, que solo vive en el .env del worker. Nunca va en el frontend.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

TABLE = "lookup_jobs"
BUCKET = "incident-reports"
WORKING = ("fetching", "extracting", "searching", "awaiting_captcha", "downloading")
FINAL = ("found", "no_document", "not_found", "ambiguous", "unsupported", "unreadable", "no_suspect",
         "unknown_location", "captcha_timeout", "error", "cancelled")


class JobsError(Exception):
    pass


def now_iso(plus_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=plus_seconds)).isoformat()


class JobStore:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None) -> None:
        url = (url or os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
        key = (key or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")).strip()
        if not url:
            raise JobsError("Falta SUPABASE_URL en el archivo .env")
        if not key:
            raise JobsError("Falta SUPABASE_SERVICE_ROLE_KEY en el archivo .env")
        self._http = httpx.Client(base_url=url, timeout=30,
                                  headers={"apikey": key, "Authorization": f"Bearer {key}"})

    def close(self) -> None:
        self._http.close()

    def _check(self, response: httpx.Response, what: str) -> httpx.Response:
        if response.status_code >= 400:
            hint = ""
            if response.status_code == 404 or "PGRST" in response.text:
                hint = " ¿Ya corriste sql/001_lookup_jobs.sql en el proyecto?"
            raise JobsError(f"Supabase rejected the request ({what}, {response.status_code}): {response.text[:300]}{hint}")
        return response

    # --- Cola -------------------------------------------------------------
    def claim(self, worker_id: str) -> Optional[dict[str, Any]]:
        """Toma el pedido pendiente más antiguo (o None si no hay)."""
        r = self._check(self._http.post("/rest/v1/rpc/claim_lookup_job", json={"p_worker_id": worker_id}),
                        "tomar un pedido")
        rows = r.json()
        return rows[0] if rows else None

    def release_stale(self, minutes: int = 20) -> int:
        r = self._check(self._http.post("/rest/v1/rpc/release_stale_lookup_jobs", json={"p_minutes": minutes}),
                        "liberar pedidos colgados")
        return int(r.json() or 0)

    def update(self, job_id: str, **fields: Any) -> None:
        fields.setdefault("heartbeat_at", now_iso())
        self._check(self._http.patch(f"/rest/v1/{TABLE}", params={"id": f"eq.{job_id}"}, json=fields,
                                     headers={"Prefer": "return=minimal"}), "actualizar el pedido")

    def insert(self, article_url: str, **fields: Any) -> dict[str, Any]:
        r = self._check(self._http.post(f"/rest/v1/{TABLE}", json={"article_url": article_url, **fields},
                                        headers={"Prefer": "return=representation"}), "crear el pedido")
        return r.json()[0]

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        r = self._check(self._http.get(f"/rest/v1/{TABLE}", params={"id": f"eq.{job_id}", "select": "*"}),
                        "leer el pedido")
        rows = r.json()
        return rows[0] if rows else None

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        r = self._check(self._http.get(f"/rest/v1/{TABLE}", params={
            "select": "id,created_at,status,status_detail,case_number,confidence,article_url",
            "order": "created_at.desc", "limit": str(limit)}), "listar pedidos")
        return r.json()

    # --- Storage ------------------------------------------------------------
    def upload_pdf(self, job_id: str, pdf: Path) -> str:
        """Sube el PDF al bucket privado y devuelve su ruta dentro del bucket."""
        # Nombres simples: Storage rechaza algunos caracteres en las rutas.
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", pdf.name).strip("_") or "document.pdf"
        path = f"{job_id}/{safe}"
        r = self._http.post(f"/storage/v1/object/{BUCKET}/{quote(path)}", content=pdf.read_bytes(),
                            headers={"Content-Type": "application/pdf", "x-upsert": "true"}, timeout=120)
        self._check(r, "upload the PDF")
        return path

    def signed_url(self, path: str, seconds: int = 3600) -> str:
        r = self._check(self._http.post(f"/storage/v1/object/sign/{BUCKET}/{quote(path)}",
                                        json={"expiresIn": seconds}), "firmar el enlace del PDF")
        data = r.json()
        signed = data.get("signedURL") or data.get("signedUrl") or ""
        return f"{self._http.base_url}/storage/v1{signed}" if signed.startswith("/") else signed
