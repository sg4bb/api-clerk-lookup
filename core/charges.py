"""Categorías de delito para comparar los cargos del artículo con los del portal.

El artículo dice "robbery with a firearm" y el portal "ROBBERY: ARMED
W/FIREARM" o "ROBBERY WITH A DEADLY WEAPON": en vez de comparar texto, ambos
se reducen a categorías (robbery, weapon, ...) y se comparan las categorías.
Las claves son prefijos de palabra para cubrir abreviaturas del portal.
"""

from __future__ import annotations

import re
from typing import Iterable

from .utils import normalize

CATEGORIES: dict[str, tuple[str, ...]] = {
    "robbery": ("robb", "carjack", "home invasion"),
    "burglary": ("burg", "break in", "broke into"),
    "theft": ("theft", "larcen", "shoplift", "petit", "stole", "stolen", "retail theft"),
    "battery": ("batt",),
    "assault": ("assault", "aslt"),
    "homicide": ("murder", "homicid", "manslaught"),
    "kidnapping": ("kidnap", "false impris", "abduct"),
    "dui": ("dui", "driving under the influence", "drunk driv"),
    "trespass": ("trespass",),
    "drugs": ("traffick", "cocaine", "heroin", "fentanyl", "methamph", "amphetamin", "cannabis",
              "marijuana", "oxycodone", "hydrocodone", "alprazolam", "controlled substance",
              "narcotic", "possession of new drug", "drug"),
    "weapon": ("firearm", "weapon", "armed", "gun", "knife"),
    "resisting": ("resist", "obstruct"),
    "fleeing": ("flee", "elud"),
    "fraud": ("fraud", "forg", "counterfeit", "uttering", "identity"),
    "sexual": ("sexual", "lewd", "rape", "molest"),
    "child": ("child", "minor"),
    "domestic": ("domestic", "dating violence"),
    "arson": ("arson",),
    "mischief": ("criminal mischief", "vandal"),
    "license": ("driver s license", "drivers license", "no valid", "license suspended",
                "suspended license", "dwls", "without a license"),
}

_PATTERNS = {cat: [re.compile(r"\b" + re.escape(k)) for k in keys] for cat, keys in CATEGORIES.items()}


def categorize(charges: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for charge in charges:
        text = normalize(charge)
        for cat, patterns in _PATTERNS.items():
            if any(p.search(text) for p in patterns):
                found.add(cat)
    return found
