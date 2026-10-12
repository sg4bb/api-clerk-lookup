"""Registro plataforma -> clase de adaptador.

Un adaptador nuevo se agrega aquí; los condados que usan esa plataforma
se agregan en counties/.
"""

from __future__ import annotations

from core.adapter import ClerkAdapter

from .civitek_ocrs import CivitekOcrsAdapter
from .orange_myeclerk import OrangeMyEClerkAdapter

ADAPTERS: dict[str, type[ClerkAdapter]] = {
    "orange_myeclerk": OrangeMyEClerkAdapter,
    "civitek_ocrs": CivitekOcrsAdapter,
}


def get_adapter(platform: str) -> type[ClerkAdapter]:
    try:
        return ADAPTERS[platform]
    except KeyError:
        raise KeyError(f"No adapter for the platform '{platform}'") from None
