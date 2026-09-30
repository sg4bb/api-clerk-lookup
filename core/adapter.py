"""Interfaz base que todo adaptador de portal debe cumplir."""

from __future__ import annotations

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


class SearchError(Exception):
    """El portal rechazó la búsqueda (CAPTCHA inválido, validación, etc.)."""


class DownloadError(Exception):
    pass


class ClerkAdapter(ABC):
    def __init__(self, page: Page, county: CountyConfig, captcha: CaptchaHandler):
        self.page = page
        self.county = county
        self.captcha = captcha

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
