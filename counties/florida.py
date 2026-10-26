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
    max_people=2,        # cada búsqueda nueva puede pedir otro reCAPTCHA
)

# Civitek OCRS: el mismo portal para 32 condados; cambia solo el código de la
# URL (orden alfabético de los condados de Florida: Marion = 42).
# Documentos vistos en Marion (26CF003891AX, 12/10/2026):
#   "ARREST AFFIDAVIT AND NOTICE OF CONFIDENTIAL FILING".
CIVITEK_DOCUMENTS = [
    r"\barrest affidavit\b",
    r"probable cause affidavit",
    r"complaint\s*/?\s*arrest affidavit",
    r"complaint affidavit",
    r"affidavit (?:for|to issue) (?:arrest )?warrant",
]
# Los nombres propios de cada condado van en su línea (civitek(..., documents=...)),
# no aquí: así un condado no toma documentos que solo existen en otro.
# Documento de apoyo: la orden de captura ejecutada. Cuando el arresto fue con
# orden, el arrest affidavit es solo el formulario de la captura y la orden trae
# el número de reporte de la agencia que investigó el hecho, a veces con su
# narración (Marion: Leroy Parks III Doc #6, Estiben García-Luis Doc #10).
# "WARRANT AFFIDAVIT" (Sumter): la declaración del agente para pedir la orden,
# con la narración y el número de reporte de la agencia (SCSO26OFF...).
CIVITEK_SUPPORT = [r"\bwarrant served\b", r"\bcapias served\b", r"^\s*warrant affidavit\b"]
CIVITEK_EXCLUDE = [r"insolvency", r"indigen", r"\bmotion\b"]


def civitek(key: str, name: str, code: str, documents: tuple = (), support: tuple = ()) -> CountyConfig:
    """Un condado de Civitek. `documents` y `support`: nombres que solo usa ese condado."""
    return CountyConfig(
        key=key,
        name=name,
        state="FL",
        platform="civitek_ocrs",
        base_url=f"https://www.civitekflorida.com/ocrs/county/{code}/",
        case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
        document_priority=CIVITEK_DOCUMENTS + list(documents),
        document_exclude=CIVITEK_EXCLUDE,
        support_documents=CIVITEK_SUPPORT + list(support),
        captcha="turnstile",
        throttle_s=2.0,
    )


MARION = civitek("marion", "Marion County", "42")
SUMTER = civitek("sumter", "Sumter County", "60")
HERNANDO = civitek("hernando", "Hernando County", "27")
PUTNAM = civitek(
    "putnam", "Putnam County", "54",
    # "ARREST REPORT: WARRANT - PCSO / 11-12-24" (Deller, 2024CF001197).
    documents=(r"^\s*arrest report\b",),
    # "COMPLAINT FILED: PCSO/...": el Probable Cause Affidavit escrito antes del
    # arresto para pedir la orden, con la narración y el número PCSO..OFF...
    support=(r"^\s*complaint filed\b",),
)
LEVY = civitek(
    "levy", "Levy County", "38",
    # El formulario de denuncia y arresto del Sheriff (número LCSO, fecha y lugar del
    # arresto, narración): "SWORN COMPLAINT FILED" (Vessels, 2017CF000855) o, en los
    # arrestos en el momento, "ARREST MITTIMUS" (McQuiston, 2026CF000685/686).
    documents=(r"\bsworn complaint\b", r"^\s*arrest mittimus\b"),
)
NASSAU = civitek(
    "nassau", "Nassau County", "45",
    documents=(r"\barrest and booking sheet\b",),   # Combs, 26CF000280
)
# Lote 2 de Civitek (19/10/2026): configurados con los nombres genéricos; los propios
# de cada condado se agregan al ver su docket.
COLUMBIA = civitek(
    "columbia", "Columbia County", "12",
    # "ARREST REPORT" del Sheriff (Gilliard, 19000675CFAXMX: número CCSO19OFF003389) y, en
    # los arrestos con orden, "CAPIAS/WARRANT RETURNED SERVED": el informe del arresto
    # (Warrant Service) más la orden y su affidavit (Shaffer, 24000899CFAXMX, Doc #14).
    documents=(r"^\s*arrest report\b", r"^\s*capias\s*/\s*warrant returned served\b"),
    # La orden con el affidavit del detective, de cuando se abrió el caso (Shaffer, Doc #6).
    support=(r"^\s*arrest warrant and warrant affidavit\b",),
)
JACKSON = civitek(
    "jackson", "Jackson County", "32",
    # El formulario "AFFIDAVIT-COMPLAINT" del Sheriff (número de reporte, fecha y hora de la
    # ofensa y del arresto). En el docket sale como "AFFIDAVIT-COMPLAINT" (Peterson,
    # 26CF000546, Doc #12) o solo "COMPLAINT" (Gilbert, 26MM000440, Doc #7).
    documents=(r"^\s*affidavit\s*-?\s*complaint\b", r"^\s*complaint\s*$"),
)
HENDRY = civitek(
    "hendry", "Hendry County", "26",
    # "BOOKING REPORT FILED": el Arrest Report del Sheriff, con el número HCSO..OFF, la fecha
    # y hora de la ocurrencia y del arresto y el lugar (Gonzalez, 26CF000196, Doc #10).
    documents=(r"^\s*booking report\b",),
)
OKEECHOBEE = civitek("okeechobee", "Okeechobee County", "47")
WAKULLA = civitek(
    "wakulla", "Wakulla County", "65",
    # "ARREST REPORT PACKAGE": el informe del Sheriff (WCSO..ARR, número WCSO..OFF), con la
    # orden y su affidavit cuando el arresto fue con orden (Woodward, 22CF000323, Doc #11).
    documents=(r"^\s*arrest report package\b",),
)

# Lote 3 de Civitek (20/10/2026): configurados con los nombres genéricos; los propios
# de cada condado se agregan al ver su docket. Bradford y Hardee: según la investigación,
# el público ve solo parte de los documentos.
BAKER = civitek(
    "baker", "Baker County", "02",
    # "ARREST REPORT - INITIATING CASE" del Sheriff (Roberts, 25000249CFMXBX, 08/07/2025).
    documents=(r"^\s*arrest report\b",),
)
BRADFORD = civitek(
    "bradford", "Bradford County", "04",
    # "ARREST REPORT" del Sheriff, junto a la First Appearance Order (Drerup, 19CF000612;
    # Jordan, 18CF000593).
    documents=(r"^\s*arrest report\b",),
)
WASHINGTON = civitek(
    "washington", "Washington County", "67",
    # "BENCH WARRANT W/ AFFIDAVIT/COMPLAINT": la orden con el Probable Cause Affidavit del
    # Sheriff (número WCSO..OFF, hora y narración del hecho). Es de antes del arresto, así que
    # va como apoyo; si el caso no tiene informe del arresto, se entrega como principal
    # (Porter, 24CF000229, Doc #6).
    support=(r"^\s*bench warrant w/?\s*affidavit\b",),
    # En los arrestos en el momento: "AFFIDAVIT - COMPLAINT" (Carmona, 26CT000140, Doc #8; con candado).
    documents=(r"^\s*affidavit\s*-?\s*complaint\b",),
)
HARDEE = civitek(
    "hardee", "Hardee County", "25",
    # "ARREST REPORT FILED": en los arrestos con orden es el affidavit para la orden, con la
    # narración (Gray, 24CF000270, Doc #7: FDLE). En el momento sale "COMPLAINT AFFIDAVIT"
    # (genérico; Layport, 26CF000203).
    documents=(r"^\s*arrest report\b",),
    support=(r"^\s*warrant returned served\b",),
)
HOLMES = civitek("holmes", "Holmes County", "30")

# Lote 4 de Civitek (22/10/2026): configurados con los nombres genéricos; los propios
# de cada condado se agregan al ver su docket.
GILCHRIST = civitek(
    "gilchrist", "Gilchrist County", "21",
    # "ARREST REPORT" del Sheriff (número GCSO..OFF, hora y lugar del arresto, narración):
    # Bonilla, 19CF000125, Doc #9; Bartges, 26CF000143, Doc #9.
    documents=(r"^\s*arrest report\b",),
    # "SWORN COMPLAINT": el Warrant Affidavit para pedir la orden, con la narración. Es
    # de antes del arresto, así que va de apoyo (Bartges, 26CF000139, Doc #8).
    support=(r"^\s*sworn complaint\b",),
)
MADISON = civitek(
    "madison", "Madison County", "40",
    # "ARREST REPORT" (McCray, 26CF000193; Barnhart, 26CF000107) o, si el arresto abrió el
    # caso, "ARREST REPORT - INITIATING CASE" (Davis, 26CF000227, Doc #6: FHP).
    documents=(r"^\s*arrest report\b",),
)
DIXIE = civitek(
    "dixie", "Dixie County", "15",
    # El affidavit de arresto está siempre con candado. Cuando hubo allanamiento, el
    # "AFFIDAVIT FOR SEARCH WARRANT-<dirección>" es público y trae el relato jurado de la
    # investigación: va de apoyo y se entrega si el de arresto no se puede bajar
    # (Buck, 25MM000190: máquinas de juego).
    support=(r"^\s*affidavit for search warrant\b",),
)
GULF = civitek("gulf", "Gulf County", "23")
UNION = civitek(
    "union", "Union County", "63",
    # "ARREST REPORT" del Sheriff (UCSO..ARR, con el número UCSO..OFF): Danlovich, 20CF000050.
    # Va primero el que se llama solo así: en George (22CF000034) también están "ARREST
    # REPORT FOR INDICTMENT" y "ARREST REPORT FROM ALACHUA COUNTY SHERIFF".
    documents=(r"^\s*arrest report\s*$", r"^\s*arrest report\b"),
    # "SWORN COMPLAINT": el Warrant Affidavit para pedir la orden, de antes del arresto
    # (George, Doc #7): de apoyo, y principal si no hay informe del arresto.
    support=(r"^\s*sworn complaint\b",),
)


# Lote 5 de Civitek (24/10/2026): configurados con los nombres genéricos; los propios
# de cada condado se agregan al ver su docket. Hamilton: su aviso prohíbe republicar los datos.
JEFFERSON = civitek("jefferson", "Jefferson County", "33")
HAMILTON = civitek("hamilton", "Hamilton County", "24")
CALHOUN = civitek("calhoun", "Calhoun County", "07")
FRANKLIN = civitek("franklin", "Franklin County", "19")
GLADES = civitek("glades", "Glades County", "22")

COUNTIES: dict[str, CountyConfig] = {
    ORANGE.key: ORANGE,
    MARION.key: MARION,
    SUMTER.key: SUMTER,
    HERNANDO.key: HERNANDO,
    PUTNAM.key: PUTNAM,
    LEVY.key: LEVY,
    NASSAU.key: NASSAU,
    COLUMBIA.key: COLUMBIA,
    JACKSON.key: JACKSON,
    HENDRY.key: HENDRY,
    OKEECHOBEE.key: OKEECHOBEE,
    WAKULLA.key: WAKULLA,
    BAKER.key: BAKER,
    BRADFORD.key: BRADFORD,
    WASHINGTON.key: WASHINGTON,
    HARDEE.key: HARDEE,
    HOLMES.key: HOLMES,
    GILCHRIST.key: GILCHRIST,
    MADISON.key: MADISON,
    DIXIE.key: DIXIE,
    GULF.key: GULF,
    UNION.key: UNION,
    JEFFERSON.key: JEFFERSON,
    HAMILTON.key: HAMILTON,
    CALHOUN.key: CALHOUN,
    FRANKLIN.key: FRANKLIN,
    GLADES.key: GLADES,
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
    "ocala": "marion", "belleview": "marion", "dunnellon": "marion", "silver springs shores": "marion",
    "silver springs": "marion", "ocklawaha": "marion", "summerfield": "marion", "reddick": "marion",
    "citra": "marion", "fort mccoy": "marion", "anthony": "marion", "weirsdale": "marion", "mcintosh": "marion",
    "wildwood": "sumter", "bushnell": "sumter", "webster": "sumter", "center hill": "sumter",
    "coleman": "sumter", "lake panasoffkee": "sumter", "oxford": "sumter", "sumterville": "sumter",
    # Ojo: "Hernando" (el pueblo) está en Citrus, no en Hernando County.
    "brooksville": "hernando", "weeki wachee": "hernando", "hernando beach": "hernando",
    "ridge manor": "hernando", "masaryktown": "hernando", "nobleton": "hernando", "istachatta": "hernando",
    "brookridge": "hernando", "timber pines": "hernando", "high point": "hernando",
    "palatka": "putnam", "east palatka": "putnam", "interlachen": "putnam", "crescent city": "putnam",
    "welaka": "putnam", "pomona park": "putnam", "satsuma": "putnam", "hollister": "putnam",
    "florahome": "putnam", "san mateo": "putnam", "bostwick": "putnam", "putnam hall": "putnam",
    # Ojo: Trenton y Fanning Springs están (o se reparten) en Gilchrist.
    "chiefland": "levy", "williston": "levy", "bronson": "levy", "cedar key": "levy", "inglis": "levy",
    "yankeetown": "levy", "morriston": "levy", "otter creek": "levy",
    "fernandina beach": "nassau", "fernandina": "nassau", "yulee": "nassau", "callahan": "nassau",
    "hilliard": "nassau", "bryceville": "nassau", "amelia island": "nassau", "nassauville": "nassau",
    "lake city": "columbia", "fort white": "columbia", "five points": "columbia", "watertown": "columbia",
    "ellisville": "columbia", "lulu": "columbia",
    "marianna": "jackson", "graceville": "jackson", "sneads": "jackson", "cottondale": "jackson",
    "grand ridge": "jackson", "malone": "jackson", "greenwood": "jackson", "alford": "jackson",
    "bascom": "jackson", "campbellton": "jackson", "jacob city": "jackson",
    "clewiston": "hendry", "labelle": "hendry", "la belle": "hendry", "port labelle": "hendry",
    "felda": "hendry", "harlem": "hendry", "montura": "hendry",
    "okeechobee": "okeechobee", "taylor creek": "okeechobee", "basinger": "okeechobee",
    "crawfordville": "wakulla", "sopchoppy": "wakulla", "st marks": "wakulla", "saint marks": "wakulla",
    "panacea": "wakulla", "medart": "wakulla", "shell point": "wakulla", "wakulla springs": "wakulla",
    "macclenny": "baker", "glen st mary": "baker", "glen saint mary": "baker", "sanderson": "baker",
    "olustee": "baker",
    "starke": "bradford", "lawtey": "bradford", "hampton": "bradford", "brooker": "bradford",
    "chipley": "washington", "vernon": "washington", "wausau": "washington", "caryville": "washington",
    "ebro": "washington", "sunny hills": "washington",
    "wauchula": "hardee", "bowling green": "hardee", "zolfo springs": "hardee",
    "bonifay": "holmes", "ponce de leon": "holmes", "esto": "holmes", "noma": "holmes", "westville": "holmes",
    # Fanning Springs se reparte con Levy; su ayuntamiento está en Gilchrist.
    "trenton": "gilchrist", "bell": "gilchrist", "fanning springs": "gilchrist",
    # Ojo: "Lee" (el pueblo de Madison) no se agrega: se confundiría con Lee County.
    "madison": "madison", "greenville": "madison", "pinetta": "madison", "cherry lake": "madison",
    # Ojo: "Suwannee" (el pueblo de Dixie) no se agrega: se confundiría con Suwannee County.
    "cross city": "dixie", "horseshoe beach": "dixie", "old town": "dixie",
    # Ojo: Mexico Beach está en Bay, no en Gulf.
    "port st joe": "gulf", "port saint joe": "gulf", "wewahitchka": "gulf", "st joe beach": "gulf",
    "saint joe beach": "gulf", "cape san blas": "gulf", "indian pass": "gulf", "highland view": "gulf",
    "lake butler": "union", "worthington springs": "union", "raiford": "union",
    "monticello": "jefferson", "wacissa": "jefferson", "lamont": "jefferson", "lloyd": "jefferson",
    "aucilla": "jefferson",
    "jasper": "hamilton", "jennings": "hamilton", "white springs": "hamilton",
    "blountstown": "calhoun", "altha": "calhoun", "clarksville": "calhoun",
    "apalachicola": "franklin", "carrabelle": "franklin", "eastpoint": "franklin",
    "st george island": "franklin", "saint george island": "franklin", "alligator point": "franklin",
    "moore haven": "glades", "buckhead ridge": "glades", "lakeport": "glades", "ortona": "glades",
    "muse": "glades",
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
    "panama city": "bay", "mexico beach": "bay", "palm coast": "flagler", "st augustine": "st_johns", "spring hill": "hernando",
    "new port richey": "pasco", "the villages": "sumter",
}

# Agencias que solo actúan dentro de un condado (para resolver sin ciudad).
AGENCY_TO_COUNTY: dict[str, str] = {
    "orlando pd": "orange", "orange county so": "orange", "winter park pd": "orange", "apopka pd": "orange",
    "ocoee pd": "orange", "winter garden pd": "orange", "maitland pd": "orange", "windermere pd": "orange",
    "edgewood pd": "orange", "belle isle pd": "orange", "eatonville pd": "orange", "ucf pd": "orange",
    "ocala pd": "marion", "marion county so": "marion", "belleview pd": "marion", "dunnellon pd": "marion",
    "sumter county so": "sumter", "wildwood pd": "sumter",
    "hernando county so": "hernando", "brooksville pd": "hernando",
    "putnam county so": "putnam", "palatka pd": "putnam", "crescent city pd": "putnam",
    "levy county so": "levy", "williston pd": "levy", "chiefland pd": "levy",
    "nassau county so": "nassau", "fernandina beach pd": "nassau",
    "columbia county so": "columbia", "lake city pd": "columbia",
    "jackson county so": "jackson", "marianna pd": "jackson", "graceville pd": "jackson",
    "sneads pd": "jackson", "cottondale pd": "jackson",
    "hendry county so": "hendry", "clewiston pd": "hendry",
    "okeechobee county so": "okeechobee", "okeechobee pd": "okeechobee", "okeechobee city pd": "okeechobee",
    "wakulla county so": "wakulla",
    "baker county so": "baker", "macclenny pd": "baker",
    "bradford county so": "bradford", "starke pd": "bradford", "lawtey pd": "bradford",
    "washington county so": "washington", "chipley pd": "washington",
    "hardee county so": "hardee", "wauchula pd": "hardee", "bowling green pd": "hardee",
    "holmes county so": "holmes", "bonifay pd": "holmes",
    "gilchrist county so": "gilchrist", "madison county so": "madison", "madison pd": "madison",
    "dixie county so": "dixie", "gulf county so": "gulf", "port st joe pd": "gulf",
    "wewahitchka pd": "gulf", "union county so": "union", "lake butler pd": "union",
    "jefferson county so": "jefferson", "monticello pd": "jefferson",
    "hamilton county so": "hamilton", "jasper pd": "hamilton", "white springs pd": "hamilton",
    "calhoun county so": "calhoun", "blountstown pd": "calhoun",
    "franklin county so": "franklin", "apalachicola pd": "franklin", "carrabelle pd": "franklin",
    "glades county so": "glades", "moore haven pd": "glades",
    "seminole county so": "seminole",
    "sanford pd": "seminole", "osceola county so": "osceola", "kissimmee pd": "osceola",
    "volusia county so": "volusia", "lake county so": "lake", "polk county so": "polk",
    "brevard county so": "brevard", "hillsborough county so": "hillsborough", "tampa pd": "hillsborough",
    "jacksonville so": "duval", "miami dade pd": "miami_dade", "miami pd": "miami_dade",
    "broward county so": "broward", "palm beach county so": "palm_beach", "pinellas county so": "pinellas",
}
