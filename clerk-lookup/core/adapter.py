"""Interfaz base que todo adaptador de portal debe cumplir."""

from __future__ import annotations

import base64
import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from playwright.sync_api import Page

from .captcha import CaptchaHandler
from .county import CountyConfig
from .models import CaseCandidate, CaseDetails, DocketEntry, LookupQuery

log = logging.getLogger(__name__)


_FETCH_JS = """
async ({url, referrer}) => {
  try {
    const options = {credentials: 'include'};
    if (referrer && new URL(referrer).origin === location.origin) options.referrer = referrer;
    const r = await fetch(url, options);
    const bytes = new Uint8Array(await r.arrayBuffer());
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return {status: r.status, type: r.headers.get('content-type') || '', b64: btoa(bin)};
  } catch (e) {
    return {error: String(e)};
  }
}
"""


class SearchError(Exception):
    """El portal rechazó la búsqueda (CAPTCHA inválido, validación, etc.)."""


class DownloadError(Exception):
    pass


class ClerkAdapter(ABC):
    def __init__(self, page: Page, county: CountyConfig, captcha: CaptchaHandler):
        self.page = page
        self.county = county
        self.captcha = captcha
        # Navegador remoto: las descargas se piden DENTRO de la página (fetch),
        # para que salgan del mismo navegador y la misma IP que la sesión.
        self.remote = False

    def fetch(self, page: Page, url: str, referer: Optional[str] = None) -> tuple[int, str, bytes]:
        """GET con las cookies de la sesión -> (status, content-type, cuerpo)."""
        if not self.remote:
            response = page.request.get(url, headers={"Referer": referer} if referer else None)
            return response.status, response.headers.get("content-type", ""), response.body()
        result = page.evaluate(_FETCH_JS, {"url": url, "referrer": referer})
        if result.get("error"):
            raise DownloadError(f"El navegador remoto no pudo descargar {url.split('?')[0]}: {result['error']}")
        return result["status"], result["type"], base64.b64decode(result["b64"])

    # --- Los cuatro pasos que cada plataforma implementa -------------------

    @abstractmethod
    def search(
        self, query: LookupQuery, date_from: Optional[date], date_to: Optional[date]
    ) -> list[CaseCandidate]:
        """Llena el formulario, pasa el CAPTCHA y devuelve los candidatos."""

    @abstractmethod
    def open_case(self, candidate: CaseCandidate) -> CaseDetails:
        """Entra al detalle del caso y lee cargos y docket."""

    def find_documents(self, details: CaseDetails) -> list[DocketEntry]:
        """Documentos útiles del docket, ordenados por preferencia.

        La implementación por defecto usa los patrones del condado; un
        adaptador la sobrescribe solo si su portal lo necesita.
        """
        excluded = [re.compile(p, re.I) for p in self.county.document_exclude]
        ranked: list[DocketEntry] = []
        for pattern in self.county.document_priority:
            regex = re.compile(pattern, re.I)
            for entry in details.docket:
                if entry in ranked or not entry.doc_ref:
                    continue
                if any(x.search(entry.description) for x in excluded):
                    continue
                if regex.search(entry.description):
                    ranked.append(entry)
        return ranked

    @abstractmethod
    def download(self, details: CaseDetails, entry: DocketEntry, dest_dir: Path) -> Path:
        """Descarga el documento y devuelve la ruta del PDF."""

    # --- Utilidades comunes -------------------------------------------------

    def url(self, path: str) -> str:
        return urljoin(self.county.base_url, path)

    def throttle(self) -> None:
        time.sleep(self.county.throttle_s)
