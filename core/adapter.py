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


# timeoutMs: se corta la petición a ese tiempo (download_timeout_s del condado). Sin eso, un portal
# que deja colgada la descarga deja colgada la búsqueda entera (Escambia, 10/11/2026).
_FETCH_JS = """
async ({url, referrer, timeoutMs}) => {
  const ctl = timeoutMs ? new AbortController() : null;
  const timer = ctl ? setTimeout(() => ctl.abort(), timeoutMs) : null;
  try {
    const options = {credentials: 'include'};
    if (ctl) options.signal = ctl.signal;
    if (referrer && new URL(referrer).origin === location.origin) options.referrer = referrer;
    const r = await fetch(url, options);
    const bytes = new Uint8Array(await r.arrayBuffer());
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return {status: r.status, type: r.headers.get('content-type') || '', b64: btoa(bin)};
  } catch (e) {
    return {error: e && e.name === 'AbortError' ? 'no answer in ' + Math.round(timeoutMs / 1000) + ' s' : String(e)};
  } finally {
    if (timer) clearTimeout(timer);
  }
}
"""


class SearchError(Exception):
    """El portal rechazó la búsqueda (CAPTCHA inválido, validación, etc.)."""


class PortalBlocked(SearchError):
    """El portal bloquea la búsqueda automática, pero otra fuente pública dio lo que sirve para pedir
    el video (Escambia: el registro de la cárcel). Termina como "no_document" con esos números, no
    como error. El mensaje ya está escrito para la persona."""

    def __init__(self, message: str, case_number: str | None = None, agency_case_number: str | None = None,
                 details: dict | None = None):
        super().__init__(message)
        self.case_number = case_number
        self.agency_case_number = agency_case_number
        self.details = details          # el booking por partes, para la tarjeta de la web


class DownloadError(Exception):
    pass


class DocumentUnavailable(DownloadError):
    """El portal tiene el documento listado pero hoy no lo entrega al público
    (Civitek: "The information you have requested is not available at this time")."""


class ClerkAdapter(ABC):
    def __init__(self, page: Page, county: CountyConfig, captcha: CaptchaHandler):
        self.page = page
        self.county = county
        self.captcha = captcha
        # Navegador remoto: las descargas se piden DENTRO de la página (fetch),
        # para que salgan del mismo navegador y la misma IP que la sesión.
        self.remote = False
        # Carpeta para diagnósticos del portal (la pone run_lookup: <pedido>/debug).
        self.debug_dir: Optional[Path] = None

    def fetch(self, page: Page, url: str, referer: Optional[str] = None) -> tuple[int, str, bytes]:
        """GET con las cookies de la sesión -> (status, content-type, cuerpo)."""
        if not self.remote:
            response = page.request.get(url, headers={"Referer": referer} if referer else None)
            return response.status, response.headers.get("content-type", ""), response.body()
        timeout_s = getattr(self.county, "download_timeout_s", None) or 0
        result = page.evaluate(_FETCH_JS, {"url": url, "referrer": referer, "timeoutMs": timeout_s * 1000})
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
        return self._matching(details, self.county.document_priority)

    def find_support_documents(self, details: CaseDetails) -> list[DocketEntry]:
        """Documentos de apoyo (orden de captura ejecutada), en el mismo orden."""
        return self._matching(details, self.county.support_documents)

    def find_requestable_documents(self, details: CaseDetails) -> list[DocketEntry]:
        """Los documentos buscados que el docket lista sin enlace (Benchmark: "Request")."""
        return self._matching(details, self.county.document_priority, without_link=True)

    def _matching(self, details: CaseDetails, priority: list[str], without_link: bool = False) -> list[DocketEntry]:
        excluded = [re.compile(p, re.I) for p in self.county.document_exclude]
        ranked: list[DocketEntry] = []
        for pattern in priority:
            regex = re.compile(pattern, re.I)
            for entry in details.docket:
                if entry in ranked or bool(entry.doc_ref) == without_link:
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
