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


def fetch_article(url: str) -> Article:
    article = None
    try:
        html = trafilatura.fetch_url(url)
        article = _parse(html, url, "direct") if html else None
    except Exception as exc:
        log.info("Descarga directa falló (%s)", exc)

    if not article or len(article.text) < MIN_TEXT_CHARS:
        log.info("Descarga directa insuficiente; abro el artículo en el navegador")
        article = _parse(_fetch_with_browser(url), url, "browser")

    if not article or len(article.text) < MIN_TEXT_CHARS:
        raise FetchError("No pude extraer el texto del artículo (¿página bloqueada o con muro de pago?)")
    article.text = article.text[:MAX_TEXT_CHARS]
    log.info("Artículo: %r (%d caracteres, publicado %s, vía %s)",
             article.title, len(article.text), article.published, article.via)
    return article


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
