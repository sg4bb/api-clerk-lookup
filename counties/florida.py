"""Condados de Florida soportados.

Para agregar un condado con una plataforma ya soportada, basta con
agregar su CountyConfig aquí. Si su portal es distinto, primero hace
falta un adaptador nuevo en adapters/.
"""

from core.county import CountyConfig

ORANGE = CountyConfig(
    key="orange",
    name="Orange County",
    state="FL",
    platform="orange_myeclerk",
    base_url="https://myeclerk.myorangeclerk.com",
    case_type_codes={"CF": "74", "CT": "75", "MM": "77"},
    document_priority=[
        r"\barrest affidavit\b",
        r"probable cause affidavit",
        r"complaint\s*/?\s*arrest affidavit",
        r"complaint affidavit",
        # En Orange el formulario "Complaint / Arrest Affidavit" a menudo se
        # registra solo como "Complaint" (visto en 2024-CF-012159-A-O).
        # Coincidencia exacta para no tomar "Motion to Dismiss Complaint".
        r"^\s*complaint\s*$",
        r"affidavit to issue warrant",
    ],
    document_exclude=[r"insolvency", r"indigen"],
    captcha="recaptcha_v2",
    throttle_s=2.0,
)

COUNTIES: dict[str, CountyConfig] = {
    ORANGE.key: ORANGE,
}
