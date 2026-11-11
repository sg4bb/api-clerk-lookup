"""Lectura de la nota en el navegador remoto: un minuto como mucho y sin imágenes ni videos.

wkrg (10/11/2026): la página cargaba un video en streaming y anuncios sin fin, y la lectura se quedó
pegada minutos con la sesión de Browserbase abierta.
"""

import time
import unittest
from unittest import mock

from extractor import fetch


class TimeLimitTest(unittest.TestCase):
    def test_gives_up_and_releases_the_session(self):
        def stuck(url, state):
            state["session_id"] = "s-123"
            time.sleep(1)           # cerrar la sesión corta la conexión: el hilo termina pronto
            return "<html>tarde</html>"

        with mock.patch.object(fetch, "REMOTE_READ_LIMIT_S", 0.3), \
             mock.patch.object(fetch, "_read_remote", side_effect=stuck), \
             mock.patch("core.remote.release_session") as release:
            started = time.monotonic()
            self.assertIsNone(fetch._fetch_with_remote_browser("https://www.wkrg.com/x"))
        release.assert_called_once_with("s-123")
        self.assertLess(time.monotonic() - started, 2.5)

    def test_in_time(self):
        with mock.patch.object(fetch, "_read_remote", return_value="<html>nota</html>"), \
             mock.patch("core.remote.release_session") as release:
            self.assertEqual(fetch._fetch_with_remote_browser("https://x/nota"), "<html>nota</html>")
        release.assert_not_called()

    def test_errors_still_reach_the_caller(self):
        with mock.patch.object(fetch, "_read_remote", side_effect=fetch._PageGone(404)):
            with self.assertRaises(fetch._PageGone):
                fetch._fetch_with_remote_browser("https://x/nota")


class BlockedResourcesTest(unittest.TestCase):
    def test_patterns(self):
        for pattern in ("*.jpg", "*.jpg?*", "*.m3u8", "*.ts?*", "*.mp4", "*.woff2"):
            self.assertIn(pattern, fetch.BLOCKED_URL_PATTERNS)
        self.assertNotIn("*.js", fetch.BLOCKED_URL_PATTERNS)
        self.assertNotIn("*.css", fetch.BLOCKED_URL_PATTERNS)

    def test_the_browser_blocks_them(self):
        from playwright.sync_api import sync_playwright
        seen = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            def serve(route):
                seen.append(route.request.url.rsplit("/", 1)[-1])
                route.fulfill(body="<p>Texto de la nota</p><img src='/foto.jpg?w=800'>"
                                   "<video src='/clip.mp4'></video><script src='/app.js'></script>"
                              if route.request.url.endswith("/nota") else "", content_type="text/html")

            page.route("http://t.test/**", serve)
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.setBlockedURLs", {"urls": fetch.BLOCKED_URL_PATTERNS})
            page.goto("http://t.test/nota")
            page.wait_for_timeout(500)
            browser.close()
        self.assertIn("app.js", seen)
        self.assertNotIn("foto.jpg?w=800", seen)
        self.assertNotIn("clip.mp4", seen)


if __name__ == "__main__":
    unittest.main()
