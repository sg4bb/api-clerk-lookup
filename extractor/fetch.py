"""Descarga del artículo y extracción del texto principal (sin IA).

1. trafilatura descarga la página y separa el texto de la nota (sin menús,
   anuncios ni comentarios), junto con título y fecha de publicación.
2. Si el sitio bloquea la descarga directa o arma la nota con JavaScript, se
   abre la página en un navegador (Playwright) y se le pasa el HTML ya
   cargado a trafilatura.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

import trafilatura

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 400      # menos que esto no es el cuerpo de una nota
MAX_TEXT_CHARS = 15_000   # suficiente para cualquier nota local; acota el costo


class FetchError(Exception):
    """No se pudo leer la nota. kind: missing (404), unreachable (el sitio no
    existe o no responde), blocked (bloqueo por país o verificación) o unreadable."""

    def __init__(self, message: str, kind: str = "unreadable"):
        super().__init__(message)
        self.kind = kind


class _PageGone(Exception):
    """La página respondió 404/410: no se lee su contenido (sería la página de
    "no encontrado" del medio, a veces con nombres de otras noticias)."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


_UNREACHABLE = re.compile(r"ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_REFUSED|ERR_ADDRESS_UNREACHABLE|"
                          r"ERR_CONNECTION_TIMED_OUT|ERR_SSL|ERR_CERT|Name or service not known|getaddrinfo", re.I)


def _goto(page, url: str) -> None:
    response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    if response is not None and response.status in (404, 410):
        raise _PageGone(response.status)


@dataclass
class Article:
    url: str
    title: str
    text: str
    published: Optional[date]
    via: str               # "direct" | "browser"


# Páginas que un sitio devuelve en lugar de la nota (bloqueo por país, muro, etc.).
_BLOCK_PATTERNS = re.compile(
    r"unavailable in your (location|country|region)|not available in your (location|country|region)|"
    r"unavailable[- ]location|access denied|verify you are human|are you a robot|enable javascript and cookies|"
    r"unusual traffic|451 unavailable", re.I)


def _blocked(article: Optional[Article]) -> bool:
    return bool(article) and bool(_BLOCK_PATTERNS.search(f"{article.title}\n{article.text[:800]}"))


def _usable(article: Optional[Article]) -> bool:
    return bool(article) and len(article.text) >= MIN_TEXT_CHARS and not _blocked(article)


def fetch_article(url: str) -> Article:
    """Texto de la nota. Intenta, en orden: descarga directa, navegador local
    y, si hay un navegador remoto configurado (en EE. UU.), ese navegador:
    varios medios de Florida bloquean las visitas desde fuera del país."""
    attempts = [("direct", lambda: trafilatura.fetch_url(url)), ("browser", lambda: _fetch_with_browser(url))]
    if os.getenv("BROWSER_PROVIDER", "local").strip().lower() != "local":
        attempts.append(("remote", lambda: _fetch_with_remote_browser(url)))

    article, blocked, gone, unreachable = None, False, None, False
    for via, download in attempts:
        try:
            article = _parse(download(), url, via)
        except _PageGone as exc:
            log.info("La página no existe (%s): %s", via, exc)
            gone, article = exc.status, None
            break   # otra vía daría el mismo 404
        except Exception as exc:
            log.info("No pude leer el artículo (%s): %s", via, exc)
            article = None
            if _UNREACHABLE.search(str(exc)):
                unreachable = True
                break   # el sitio no existe: el navegador remoto tampoco llegaría (y gastaría una sesión)
        if _usable(article):
            break
        if _blocked(article):
            blocked = True
            log.info("El sitio bloqueó la lectura (%s): %r", via, article.title)
        else:
            log.info("Lectura insuficiente (%s); pruebo otra vía", via)

    if not _usable(article):
        if gone:
            raise FetchError(f"The link leads to a page that doesn't exist (HTTP {gone}). "
                             "Check that the link is complete.", kind="missing")
        if blocked:
            raise FetchError("The news site blocks visits from this location or requires a "
                             "verification, so the article could not be read.", kind="blocked")
        if unreachable and article is None:
            raise FetchError("The site could not be reached. Check that the link is spelled correctly.",
                             kind="unreachable")
        raise FetchError("Could not read an article at this link. The page may be behind a paywall, "
                         "or it may not be a news story.")
    article.text = article.text[:MAX_TEXT_CHARS]
    log.info("Artículo: %r (%d caracteres, publicado %s, vía %s)",
             article.title, len(article.text), article.published, article.via)
    return article


def _fetch_with_remote_browser(url: str) -> Optional[str]:
    """Abre la nota en el navegador remoto (sale por una IP de EE. UU.)."""
    from core.browser import open_browser

    log.info("Abro el artículo en el navegador remoto")
    with open_browser() as session:
        _goto(session.page, url)
        session.page.wait_for_timeout(2_500)
        return session.page.content()


def _parse(html: Optional[str], url: str, via: str) -> Optional[Article]:
    if not html:
        return None
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True,
                                      include_comments=False, include_tables=False)
    if doc is None:
        return None
    get = doc.get if isinstance(doc, dict) else (lambda k, d=None: getattr(doc, k, d))
    text = (get("text") or "").strip()
    published = None
    raw_date = get("date")
    if raw_date:
        try:
            published = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            published = None
    return Article(url=url, title=(get("title") or "").strip(), text=text, published=published, via=via)


def _fetch_with_browser(url: str) -> Optional[str]:
    from playwright.sync_api import sync_playwright

    channel = os.getenv("BROWSER_CHANNEL", "chrome").strip() or None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True, channel=channel)
        except Exception:
            browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(locale="en-US")
            _goto(page, url)
            page.wait_for_timeout(2_500)   # deja que cargue el cuerpo de la nota
            return page.content()
        except _PageGone:
            raise
        except Exception as exc:
            log.warning("El navegador tampoco pudo abrir el artículo: %s", exc)
            raise
        finally:
            browser.close()
