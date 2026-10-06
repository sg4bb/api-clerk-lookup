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
    pass


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

    article, blocked = None, False
    for via, download in attempts:
        try:
            article = _parse(download(), url, via)
        except Exception as exc:
            log.info("No pude leer el artículo (%s): %s", via, exc)
            article = None
        if _usable(article):
            break
        if _blocked(article):
            blocked = True
            log.info("El sitio bloqueó la lectura (%s): %r", via, article.title)
        else:
            log.info("Lectura insuficiente (%s); pruebo otra vía", via)

    if not _usable(article):
        if blocked:
            raise FetchError("The news site blocks visits from this location or requires a "
                             "verification, so the article could not be read.")
        raise FetchError("Could not read the article text (the page may be blocked or behind a paywall).")
    article.text = article.text[:MAX_TEXT_CHARS]
    log.info("Artículo: %r (%d caracteres, publicado %s, vía %s)",
             article.title, len(article.text), article.published, article.via)
    return article


def _fetch_with_remote_browser(url: str) -> Optional[str]:
    """Abre la nota en el navegador remoto (sale por una IP de EE. UU.)."""
    from core.browser import open_browser

    log.info("Abro el artículo en el navegador remoto")
    with open_browser() as session:
        session.page.goto(url, wait_until="domcontentloaded", timeout=45_000)
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
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2_500)   # deja que cargue el cuerpo de la nota
            return page.content()
        except Exception as exc:
            log.warning("El navegador tampoco pudo abrir el artículo: %s", exc)
            return None
        finally:
            browser.close()
