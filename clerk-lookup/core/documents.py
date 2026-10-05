"""Utilidades para convertir lo que entregan los portales en un PDF.

Algunos visores no entregan el PDF original sino imágenes (TIFF escaneado,
PNG por página). Aquí se detecta el formato y se arma un PDF con Pillow.
"""

from __future__ import annotations

import base64
import binascii
import json
from io import BytesIO
from typing import Any, Iterable, Optional

_IMAGE_MAGIC = (
    b"\x89PNG",          # PNG
    b"\xff\xd8\xff",     # JPEG
    b"GIF8",             # GIF
    b"II*\x00",          # TIFF little-endian
    b"MM\x00*",          # TIFF big-endian
    b"BM",               # BMP
)


def is_pdf(body: bytes) -> bool:
    return body[:5] == b"%PDF-"


def is_image(body: bytes) -> bool:
    return any(body.startswith(m) for m in _IMAGE_MAGIC)


def images_to_pdf(images: Iterable[bytes], dpi: int = 200) -> bytes:
    """Une imágenes (una o varias páginas cada una, p. ej. TIFF multipágina) en un PDF."""
    from PIL import Image, ImageSequence

    pages = []
    for data in images:
        with Image.open(BytesIO(data)) as img:
            for frame in ImageSequence.Iterator(img):
                pages.append(frame.convert("RGB"))
    if not pages:
        raise ValueError("No hay imágenes para armar el PDF")
    out = BytesIO()
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=dpi)
    return out.getvalue()


def to_pdf(body: bytes) -> Optional[bytes]:
    """PDF tal cual, imagen convertida a PDF, o None si no es ninguna de las dos."""
    if is_pdf(body):
        return body
    if is_image(body):
        return images_to_pdf([body])
    return None


def image_from_json(payload: Any) -> Optional[bytes]:
    """Busca una imagen embebida (data URI o base64) en cualquier parte de un JSON."""
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    for text in _strings(payload):
        if text.startswith("data:image"):
            text = text.split(",", 1)[-1]
        elif len(text) < 40:
            continue
        try:
            data = base64.b64decode(text, validate=False)
        except (binascii.Error, ValueError):
            continue
        if is_image(data):
            return data
    return None


def page_count_from_json(payload: Any) -> Optional[int]:
    """Busca un número de páginas ("PageCount", "pages", "totalPages"...) en un JSON."""
    found: list[int] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                k = str(key).lower()
                if isinstance(value, int) and "page" in k and ("count" in k or "total" in k or k == "pages"):
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found[0] if found and 0 < found[0] < 1000 else None


def _strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)
