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
    # "CTRL SUB" y "PARAPHERNALIA" sin "drug": así escribe Liberty los cargos de Shipley
    # (26000100CFMXAX, "POSS INTENT SELL/MFG/DELIV CTRL SUB SCHEDULE I"); quedaban "no comparables".
    "drugs": ("traffick", "cocaine", "heroin", "fentanyl", "methamph", "amphetamin", "cannabis",
              "marijuana", "oxycodone", "hydrocodone", "alprazolam", "controlled substance",
              "narcotic", "possession of new drug", "drug", "ctrl sub", "cntrl sub", "paraphernal"),
    "weapon": ("firearm", "weapon", "armed", "gun", "knife"),
    # "Obstruct" es resistencia solo frente a un agente ("obstruct or oppose an officer"):
    # "obstructing justice" es alterar pruebas (ver _NOT_RESISTING y "tampering").
    "resisting": ("resist", "obstruct"),
    "tampering": ("tamper", "obstructing justice", "obstruction of justice", "fabricat"),
    # Encubrir a otro ("accessory after the fact"): no es el homicidio que encubre.
    "accessory": ("accessory after", "accessory to", "accessory before"),
    "fleeing": ("flee", "elud"),
    "fraud": ("fraud", "forg", "counterfeit", "uttering", "identity"),
    # "Obscene communication - use computer to seduce/solicit/lure a child" (Johnson, Santa Rosa,
    # 19001033CFMXAX): el portal corta en "LURE CH" y quedaba "no comparables" (53 %). "Solicit" no
    # va: también es "solicitation of prostitution" o solicitar un homicidio.
    "sexual": ("sexual", "lewd", "rape", "molest", "obscene", "seduce", "lure"),
    "child": ("child", "minor"),
    # "DOMEST VIOL" (Gill, Pasco): "domest" cubre también "domestic".
    "domestic": ("domest", "dating violence"),
    # Violar las condiciones de la libertad antes del juicio: "PRE TRIAL RELEASE COND VIOL FOR DOMEST
    # VIOL" en la nota, "CONDIT RELEASE VIOLATION PRE TRIAL RELEASE..." en el portal (Gill, Pasco,
    # 2026MM004374MMAXES: quedaba "no comparables" y pasaba justo con 60 %).
    "release": ("pre trial release", "pretrial release", "condit release", "conditional release",
                "release cond", "release violation"),
    "arson": ("arson",),
    # Amenazas y acoso: "corruption by threat against a public servant", "harassing telephone
    # calls", "written threats to kill" (Reed, Franklin, 28/10/2026: se quedaba en 57 %).
    "threats": ("threat", "harass"),
    # Venta de tabaco o vapeadores a menores: "sale of tobacco to a minor" en la nota,
    # "SALE NICOTINE DEVICE TO PERSONS UNDER 21YOA" en el portal (Patel, Madison, 23/10/2026).
    "tobacco": ("tobacco", "nicotine", "vape", "vaping", "cigarette", "e cig"),
    "mischief": ("criminal mischief", "vandal"),
    "license": ("driver s license", "drivers license", "no valid", "license suspended",
                "suspended license", "dwls", "without a license", "lic susp", "license susp",
                "susp or revoked", "suspended or revoked", "dl susp"),
}

_PATTERNS = {cat: [re.compile(r"\b" + re.escape(k)) for k in keys] for cat, keys in CATEGORIES.items()}


# "Obstructing justice - tamper w/ evidence" (Trueblood, Baker) contaba como resistencia.
_NOT_RESISTING = re.compile(r"\bobstruct\w*\s+(?:of\s+)?justice\b")
# "Dealing stolen property: trafficking" (Williamson, Dixie, 18CF000059) contaba como drogas;
# sigue contando como robo por "stolen".
_NOT_DRUGS = re.compile(r"\bstolen\s+property\s+traffick\w*|\btraffick\w*\s+(?:in\s+)?stolen\s+property\b")


def categorize(charges: Iterable[str]) -> set[str]:
    found: set[str] = set()
    for charge in charges:
        text = normalize(charge)
        for cat, patterns in _PATTERNS.items():
            subject = (_NOT_RESISTING.sub(" ", text) if cat == "resisting"
                       else _NOT_DRUGS.sub(" ", text) if cat == "drugs" else text)
            if any(p.search(subject) for p in patterns):
                found.add(cat)
    return found
