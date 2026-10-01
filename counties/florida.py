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


# --- Ubicación -------------------------------------------------------------
# Los 67 condados de Florida (clave en minúsculas, sin "County"). Sirve para
# distinguir "condado de Florida aún no soportado" de "fuera de Florida".
ALL_COUNTIES: dict[str, str] = {k: k.replace("_", " ").title().replace("Desoto", "DeSoto").replace("St ", "St. ")
                                for k in (
    "alachua", "baker", "bay", "bradford", "brevard", "broward", "calhoun", "charlotte", "citrus", "clay",
    "collier", "columbia", "desoto", "dixie", "duval", "escambia", "flagler", "franklin", "gadsden",
    "gilchrist", "glades", "gulf", "hamilton", "hardee", "hendry", "hernando", "highlands", "hillsborough",
    "holmes", "indian_river", "jackson", "jefferson", "lafayette", "lake", "lee", "leon", "levy", "liberty",
    "madison", "manatee", "marion", "martin", "miami_dade", "monroe", "nassau", "okaloosa", "okeechobee",
    "orange", "osceola", "palm_beach", "pasco", "pinellas", "polk", "putnam", "st_johns", "st_lucie",
    "santa_rosa", "sarasota", "seminole", "sumter", "suwannee", "taylor", "union", "volusia", "wakulla",
    "walton", "washington")}
ALL_COUNTIES["miami_dade"] = "Miami-Dade"

# Ciudad -> condado. Todas las de Orange (para resolver sin el modelo) y las
# principales del resto del estado (para dar un aviso preciso).
CITY_TO_COUNTY: dict[str, str] = {
    **{c: "orange" for c in (
        "orlando", "winter park", "apopka", "ocoee", "winter garden", "maitland", "windermere",
        "belle isle", "edgewood", "oakland", "eatonville", "pine hills", "pine castle", "azalea park",
        "conway", "union park", "hunters creek", "meadow woods", "doctor phillips", "dr phillips",
        "lake buena vista", "horizon west", "gotha", "christmas", "bithlo", "wedgefield", "zellwood",
        "lockhart", "tangerine", "taft", "oak ridge", "holden heights", "orlovista", "fairview shores",
        "lake nona", "avalon park", "waterford lakes", "alafaya", "goldenrod")},
    "ocala": "marion", "belleview": "marion", "dunnellon": "marion",
    "sanford": "seminole", "altamonte springs": "seminole", "oviedo": "seminole", "lake mary": "seminole",
    "casselberry": "seminole", "longwood": "seminole", "winter springs": "seminole",
    "kissimmee": "osceola", "st cloud": "osceola", "saint cloud": "osceola", "poinciana": "osceola",
    "clermont": "lake", "leesburg": "lake", "eustis": "lake", "tavares": "lake", "mount dora": "lake",
    "daytona beach": "volusia", "deltona": "volusia", "deland": "volusia", "orange city": "volusia",
    "port orange": "volusia", "new smyrna beach": "volusia", "ormond beach": "volusia",
    "melbourne": "brevard", "palm bay": "brevard", "titusville": "brevard", "cocoa": "brevard",
    "cocoa beach": "brevard", "lakeland": "polk", "winter haven": "polk", "haines city": "polk",
    "tampa": "hillsborough", "brandon": "hillsborough", "plant city": "hillsborough",
    "st petersburg": "pinellas", "saint petersburg": "pinellas", "clearwater": "pinellas", "largo": "pinellas",
    "miami": "miami_dade", "hialeah": "miami_dade", "miami beach": "miami_dade", "homestead": "miami_dade",
    "fort lauderdale": "broward", "hollywood": "broward", "pembroke pines": "broward", "coral springs": "broward",
    "west palm beach": "palm_beach", "boca raton": "palm_beach", "boynton beach": "palm_beach",
    "jacksonville": "duval", "tallahassee": "leon", "gainesville": "alachua", "pensacola": "escambia",
    "fort myers": "lee", "cape coral": "lee", "naples": "collier", "sarasota": "sarasota",
    "bradenton": "manatee", "port st lucie": "st_lucie", "fort pierce": "st_lucie", "key west": "monroe",
    "panama city": "bay", "palm coast": "flagler", "st augustine": "st_johns", "spring hill": "hernando",
    "new port richey": "pasco", "the villages": "sumter",
}

# Agencias que solo actúan dentro de un condado (para resolver sin ciudad).
AGENCY_TO_COUNTY: dict[str, str] = {
    "orlando pd": "orange", "orange county so": "orange", "winter park pd": "orange", "apopka pd": "orange",
    "ocoee pd": "orange", "winter garden pd": "orange", "maitland pd": "orange", "windermere pd": "orange",
    "edgewood pd": "orange", "belle isle pd": "orange", "eatonville pd": "orange", "ucf pd": "orange",
    "ocala pd": "marion", "marion county so": "marion", "seminole county so": "seminole",
    "sanford pd": "seminole", "osceola county so": "osceola", "kissimmee pd": "osceola",
    "volusia county so": "volusia", "lake county so": "lake", "polk county so": "polk",
    "brevard county so": "brevard", "hillsborough county so": "hillsborough", "tampa pd": "hillsborough",
    "jacksonville so": "duval", "miami dade pd": "miami_dade", "miami pd": "miami_dade",
    "broward county so": "broward", "palm beach county so": "palm_beach", "pinellas county so": "pinellas",
}
