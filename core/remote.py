"""Navegador remoto en Browserbase.

Se usa su API REST directamente (sin SDK): crear la sesión, pedir el enlace
de live view y liberarla al terminar. Playwright se conecta por CDP.

Importante: solveCaptchas=false. El CAPTCHA lo resuelve una persona desde el
live view; no se usa ningún resolvedor automático.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator

import httpx
from playwright.sync_api import sync_playwright

from .browser import CONTEXT_OPTIONS, BrowserSession

log = logging.getLogger(__name__)

API = "https://api.browserbase.com/v1"


class RemoteBrowserError(Exception):
    pass


def _client() -> httpx.Client:
    key = os.getenv("BROWSERBASE_API_KEY", "").strip()
    if not key:
        raise RemoteBrowserError("BROWSERBASE_API_KEY is missing from the .env file")
    return httpx.Client(base_url=os.getenv("BROWSERBASE_API_URL", API), timeout=30,
                        headers={"x-bb-api-key": key, "Content-Type": "application/json"})


def _check(response: httpx.Response, what: str) -> dict:
    if response.status_code >= 400:
        raise RemoteBrowserError(f"Browserbase rejected the request ({what}, {response.status_code}): {response.text[:300]}")
    return response.json()


@contextmanager
def open_browserbase() -> Iterator[BrowserSession]:
    project_id = os.getenv("BROWSERBASE_PROJECT_ID", "").strip()
    body = {
        # El plan gratuito corta cada sesión a los 15 minutos.
        "timeout": int(os.getenv("BROWSERBASE_SESSION_TIMEOUT_S", "900")),
        "region": os.getenv("BROWSERBASE_REGION", "us-east-1"),
        "browserSettings": {
            "solveCaptchas": False,
            "viewport": CONTEXT_OPTIONS["viewport"],
        },
    }
    if project_id:
        body["projectId"] = project_id

    with _client() as api:
        session = _check(api.post("/sessions", json=body), "crear la sesión")
        session_id = session["id"]
        log.info("Sesión remota creada en Browserbase: %s", session_id)
        try:
            debug = _check(api.get(f"/sessions/{session_id}/debug"), "el enlace de live view")
            live_url = debug.get("debuggerFullscreenUrl") or debug.get("debuggerUrl")
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(session["connectUrl"])
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(30_000)
                try:
                    yield BrowserSession(page, provider="browserbase", live_view_url=live_url)
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass
        finally:
            # Liberar la sesión para que deje de consumir tiempo del plan.
            release = {"status": "REQUEST_RELEASE"}
            if project_id:
                release["projectId"] = project_id
            try:
                api.post(f"/sessions/{session_id}", json=release)
                log.info("Sesión remota liberada")
            except Exception as exc:
                log.warning("No pude liberar la sesión %s (se cerrará sola): %s", session_id, exc)
