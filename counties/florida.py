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


def civitek(key: str, name: str, code: str, documents: tuple = (), support: tuple = (),
            exclude: tuple = ()) -> CountyConfig:
    """Un condado de Civitek. `documents` y `support`: nombres que solo usa ese condado.
    `exclude`: si se da, reemplaza a CIVITEK_EXCLUDE solo para ese condado."""
    return CountyConfig(
        key=key,
        name=name,
        state="FL",
        platform="civitek_ocrs",
        base_url=f"https://www.civitekflorida.com/ocrs/county/{code}/",
        case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
        document_priority=CIVITEK_DOCUMENTS + list(documents),
        document_exclude=list(exclude) or CIVITEK_EXCLUDE,
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
GULF = civitek(
    "gulf", "Gulf County", "23",
    # "COMPLAINT/PC; ARREST REPORT; 1ST. APP. ORDER": la denuncia con la causa probable, el
    # informe de arresto y la primera audiencia en un solo PDF (Harris Ottoson, 25CF000090, Doc #8).
    documents=(r"^\s*complaint\s*/\s*pc\b", r"^\s*arrest report\b"),
)
UNION = civitek(
    "union", "Union County", "63",
    # "ARREST REPORT" del Sheriff (UCSO..ARR, con el número UCSO..OFF): Danlovich, 20CF000050.
    # En George (22CF000034) también están "ARREST REPORT FOR INDICTMENT" y "ARREST REPORT
    # FROM ALACHUA COUNTY SHERIFF"; Civitek baja por orden de Doc #, así que sale primero el
    # que entró primero, el del Sheriff de Union (#12).
    documents=(r"^\s*arrest report\s*$", r"^\s*arrest report\b"),
    # "SWORN COMPLAINT": el Warrant Affidavit para pedir la orden, de antes del arresto
    # (George, Doc #7): de apoyo, y principal si no hay informe del arresto.
    support=(r"^\s*sworn complaint\b",),
)


# Lote 5 de Civitek (24/10/2026): configurados con los nombres genéricos; los propios
# de cada condado se agregan al ver su docket. Hamilton: su aviso prohíbe republicar los datos.
JEFFERSON = civitek(
    "jefferson", "Jefferson County", "33",
    # "ARREST, BOOKING,FIRST APPEARANCE,FILED": paquete con el Arrest Affidavit del Sheriff
    # (número de reporte, hora y narración), el booking y la primera audiencia (Bhatt,
    # 25CF000195, 10 páginas). "ARREST/NOTICE TO APPEAR" entra antes al expediente (Civitek
    # baja por orden de Doc #), así que va de apoyo: se bajan los dos, y si no hay paquete
    # (o tiene candado, como en Mack, 26CF000146), ese queda como principal.
    documents=(r"^\s*arrest\s*,\s*booking\b",),
    support=(r"^\s*arrest\s*/\s*notice to appear\b",),
)
HAMILTON = civitek(
    "hamilton", "Hamilton County", "24",
    # "ARREST REPORT--HCSO25OFF000413": el informe del Sheriff, con el número de reporte en el
    # nombre (Daniel, 25CF000073; Ellis, 25CF000150, a veces con un solo guion).
    documents=(r"^\s*arrest report\b",),
)
CALHOUN = civitek(
    "calhoun", "Calhoun County", "07",
    # Casi siempre "PROBABLE CAUSE AFFIDAVIT" (genérico: Pierce, Bailey, Elrod). A veces, en su
    # lugar, "Arrest Report - Initiating Case" del Sheriff (número CCSO..OFF, hora y narración);
    # el "FIRST APPEARANCE ARREST PACKET" (checklist, primera audiencia, indigencia) entra antes
    # al expediente y va de apoyo (Glaze, 26CF000184: Doc #9 y #1).
    documents=(r"^\s*arrest report\b",),
    support=(r"^\s*first appearance arrest packet\b",),
)
FRANKLIN = civitek(
    "franklin", "Franklin County", "19",
    # "PROBABLE CAUSE FILED": la declaración de causa probable (Keith, 24CF000229, Doc #11).
    # "ARREST/BOOKING REPORT - INITIATING CASE" es la ficha de la cárcel (Doc #10): de apoyo.
    documents=(r"^\s*probable cause filed\b",),
    support=(r"^\s*arrest\s*/\s*booking report\b",),
)
GLADES = civitek(
    "glades", "Glades County", "22",
    # "BOOKING REPORT": el reporte del Sheriff (GCSO) con número de reporte, CAD, lugar y la
    # sección "Probable Cause" con la narración (Telci, 25000121CFMXAX, Doc #19). Igual que Hendry.
    documents=(r"^\s*booking report\b",),
)
# Lote 6. Con los nombres genéricos de Civitek hasta ver sus dockets.
LAFAYETTE = civitek(
    "lafayette", "Lafayette County", "34",
    # El informe del Sheriff, a veces junto con otros formularios y con los nombres separados por comas:
    # "Arrest Report - Initiating Case" (Blake, 24000023CFAXMX, Doc #18, con candado),
    # "ARREST REPORT,INCIDENT REPORT,CITATION,..." (M. D. Jones, 26000029CFAXMX, Doc #8) o
    # "INCIDENT REPORT,ARREST REPORT,FIRST APPEARANCE..." (J. Jones, 25000018CFAXMX, Doc #4). La
    # "probable cause affidavit" dentro de otro paquete (W. Jones, 25000038CFAXMX, Doc #9) ya la toma
    # el nombre genérico. El número de reporte (LCSO25OFF000236) viene en el PDF, no en el docket.
    documents=(r"^\s*(?:incident report\s*,\s*)?arrest report\b",),
    # Los paquetes incluyen la "APPLICATION FOR CRIMINAL INDIGENT STATUS" en la lista: con la exclusión
    # general se descartaban enteros. Aquí solo se excluye la entrada que es únicamente eso.
    exclude=(r"^[^,]*(?:insolvency|indigen|\bmotion\b)[^,]*$",),
)
# Pasco: el condado más grande de Civitek (estaba anotado como sistema propio). El informe del
# arresto es el "PASCO COUNTY COMPLAINT AFFIDAVIT" del Sheriff (hora y lugar del arresto, cargos y
# "Probable Cause Statement"); en el docket sale como "Complaint" a secas (Mahoney, 2025CF000209CFAXWS,
# Doc #2; Cleary, 2026CF004785CFAXWS, Doc #2). Exacto, para no tomar "Answer to Complaint".
PASCO = civitek("pasco", "Pasco County", "51", documents=(r"^\s*complaint\s*$",))
WALTON = civitek(
    "walton", "Walton County", "66",
    # "ARREST REPORT FILED." (con punto): el informe del arresto con la causa probable (Thorgaard,
    # 26000908CFAXMX; Baldwin, 26000888CFAXMX; Adams, 25000441CFAXMX). "INCIDENT REPORT FILED." es el
    # informe del hecho (FIBRS), con el número de reporte (DSPD25OFF000685), la narración y los
    # testigos: de apoyo. El docket anota "AGENCY CASE NUMBER:".
    documents=(r"^\s*arrest report filed\b",),
    support=(r"^\s*incident report filed\b",),
)
SANTA_ROSA = civitek(
    "santa_rosa", "Santa Rosa County", "57",
    # "ARREST REPORT": paquete del Sheriff (SRSO) con el informe y su causa probable, la orden y su
    # affidavit si la hubo, la ficha de la cárcel y la primera audiencia (Anderson, 25000475CFMXAX,
    # Doc #6; Nasim, 26000317CFMXAX, Doc #6; Graves, 24001561CFMXAX, Doc #5, y "ARREST REPORT (COPY
    # FROM FT WALTON BEACH PD)", Doc #13). "AFFIDAVIT FHP PROBABLE CAUSE": la declaración del
    # patrullero de FHP con la narración (Nasim, Doc #22): de apoyo.
    documents=(r"^\s*arrest report\b",),
    support=(r"^\s*affidavit\b[^,]*\bprobable cause\b",),
)
# Highlands: los nombres genéricos alcanzan. "ARREST AFFIDAVIT" es el Arrest Report del Sheriff con la
# causa probable (Conklin, 26000840CFAXMX, Doc #12); en arrestos con orden, "WARRANT AFFIDAVIT" (Zeledon,
# 25001153CFAXMX, Doc #8), de apoyo y principal si no hay otro. El docket anota "Agency Case Number".
HIGHLANDS = civitek("highlands", "Highlands County", "28")
DESOTO = civitek(
    "desoto", "DeSoto County", "14",
    # "BOOKING OFFICER'S REPORT, OFFICER'S ARREST REPORT FILED": la ficha de la cárcel y el "Officer's
    # Arrest Report (Probable Cause Affidavit)" con la narración (Rohena Perez, 2026CF000348AXMA, Doc #7;
    # Daniels, 2026CF000068AXMA, Doc #14). El docket anota "Agency Case Number" (2026-01906-1).
    documents=(r"^\s*(?:booking officer'?s report\s*,\s*)?officer'?s arrest report\b",),
)
LIBERTY = civitek(
    "liberty", "Liberty County", "39",
    # "PROBABLE CAUSE" a secas: la causa probable del arresto, cargada el día de la primera
    # audiencia (Cox, 25000066CFMXAX, 09/06/2025). Exacto, para no tomar "... probable cause hearing".
    documents=(r"^\s*probable cause\s*$",),
)

# BenchmarkWeb (Pioneer / Catalis): un portal por condado, con la misma aplicación.
# Osceola (01/11/2026), sin CAPTCHA ni cuenta:
#   - Arresto sin orden: "BOOKING DOCUMENT" = Osceola County Uniform Charging Affidavit, con la
#     narración del agente (McMillian, 2026 CF 003435, Seq 2; Jeanbaptiste, 2026 CF 003434, Seq 2).
#   - Arresto con orden: "CRIMINAL CASE OPENED - ARREST WARRANT FILED MEMORANDUM FILED AFFIDAVIT OF
#     ARREST WARRANT" = la orden con la declaración del agente (Garcia, 2026 CF 003520, Seq 2). El
#     "CRIMINAL CASE - BOOKING REPORT FILED..." de ese arresto solo dice "arrestada por orden": de apoyo.
#   - Arresto sin orden, otro nombre: "CRIMINAL CASE OPENED - BOOKING REPORT FILED ADVICED TO DEFENDANT
#     FIRST APPEARANCE" = el Uniform Charging Affidavit con la narración (Carlos Moises Garcia,
#     2026 CF 001491, Seq 4). Con "OPENED" es el principal; sin "OPENED", el de la orden (apoyo).
#   - El "Agency Report Number" está en el resumen del caso (vacío en los del Sheriff: su número
#     solo aparece dentro del PDF, "Case# 26I104605").
OSCEOLA = CountyConfig(
    key="osceola",
    name="Osceola County",
    state="FL",
    platform="benchmark_web",
    base_url="https://courts.osceolaclerk.com/BenchmarkWeb/Home.aspx/Search",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*booking document\b",
        r"^\s*criminal case opened\s*-\s*booking report filed\b",
        r"\baffidavit of arrest warrant\b",
        r"affidavit (?:for|to issue) (?:arrest )?warrant",
        r"\barrest affidavit\b",
        r"probable cause affidavit",
        r"charging affidavit",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b"],
    support_documents=[r"^\s*criminal case\s*-\s*booking report filed\b", r"\bwarrant returned executed\b"],
    captcha="none",
    throttle_s=2.0,
)

# Flagler (02/11/2026): BenchmarkWeb en su propio dominio, con hCaptcha (lo resuelve una persona;
# solo la primera búsqueda de la sesión lo pide). Fechas de apertura en campos visibles.
#   - Arresto con orden: "SWORN COMPLAINT" = la declaración del agente con la narración, para pedir
#     la orden; el "ARRESTING AFFIDAVIT" de la captura solo dice que se ejecutó la orden (Moraitis,
#     2026 CF 000915: Seq 2 y Seq 12). Va primero el que entró antes (Seq).
#   - Arresto sin orden: "ARRESTING AFFIDAVIT" con la narración.
#   - El docket anota la fecha de arresto como "REARRESTED ON 10/24/2026" (o "ARRESTED ON").
FLAGLER = CountyConfig(
    key="flagler",
    name="Flagler County",
    state="FL",
    platform="benchmark_web",
    base_url="https://cases.flaglerclerk.gov/Home.aspx/Search",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*arresting affidavit\b",
        r"^\s*sworn complaint\s*$",
        r"\barrest affidavit\b",
        r"probable cause affidavit",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b"],
    support_documents=[r"^\s*arresting affidavit\b"],
    captcha="hcaptcha",
    throttle_s=2.0,
)

# St. Johns (03/11/2026): BenchmarkWeb en /Benchmark/, con hCaptcha como Flagler. Números de caso
# como "26001446CFMA". Sin orden: "ARREST REPORT" del Sheriff (Matthew, 26001446CFMA, Seq 5); el
# docket anota el arresto como "ARREST COUNT: ..." con su fecha y la unión de casos como
# "CONSOLIDATED TO 26001422CFMA". "Agency Report Number" en el resumen (26OFF008738).
ST_JOHNS = CountyConfig(
    key="st_johns",
    name="St. Johns County",
    state="FL",
    platform="benchmark_web",
    base_url="https://apps.stjohnsclerk.com/Benchmark/Home.aspx/Search",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*arrest report\b",
        r"\barrest(?:ing)? affidavit\b",
        r"probable cause affidavit",
        r"affidavit (?:of|for|to issue) (?:an? )?(?:arrest )?warrant",
        r"^\s*sworn complaint\b",
        r"complaint affidavit",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b"],
    support_documents=[r"\bwarrant (?:returned )?(?:served|executed)\b", r"\bcapias (?:returned )?served\b"],
    captcha="hcaptcha",
    throttle_s=2.0,
)

# Charlotte (03/11/2026): BenchmarkWeb en /Benchmark/, como St. Johns, con reCAPTCHA solo en la primera
# búsqueda de la sesión (sin resolverlo vuelve a "?error=Invalid Captcha"). El formulario abre con
# "All" marcado. Los números de caso terminan en el tipo: "26001343F" (felony), "22001059CC" (county
# civil), "21007084TR" (traffic infraction), "26000945T" (traffic penal: DUI). Documento: el "AFFIDAVIT OF PROBABLE CAUSE" del Sheriff
# (narración, hora y lugar del arresto); el "BOOKING REPORT FILED" va de apoyo. El docket anota el
# arresto como "ARREST SEQ: 1 - ...".
CHARLOTTE = CountyConfig(
    key="charlotte",
    name="Charlotte County",
    state="FL",
    platform="benchmark_web",
    base_url="https://courts.charlotteclerk.com/Benchmark/Home.aspx/Search",
    # Terminación del número de caso -> tipo del worker. "F" = FELONY y "T" = TRAFFIC (penal: DUI,
    # licencia suspendida; Clemens 26000945T, Portillo 26002193T) confirmados; "M" por confirmar.
    # Quedan fuera "TR" (traffic infraction, civil), "CC" (county civil) y "O" (ordinance).
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT", "F": "CF", "M": "MM", "T": "CT"},
    document_priority=[
        r"affidavit of probable cause",
        r"probable cause affidavit",
        r"\barrest(?:ing)? affidavit\b",
        r"^\s*arrest report\b",
        r"affidavit (?:of|for|to issue) (?:an? )?(?:arrest )?warrant",
        r"^\s*sworn complaint\b",
        r"complaint affidavit",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b"],
    support_documents=[r"^\s*booking report\b", r"\bwarrant (?:returned )?(?:served|executed)\b",
                       r"\bcapias (?:returned )?served\b"],
    captcha="recaptcha_v2",
    throttle_s=2.0,
)

# Indian River (05/11/2026): BenchmarkWeb 2.9.10 en /BenchmarkWeb/, sin CAPTCHA (al menos en las
# pruebas). Números de caso como Osceola con la división al final: "2026 CF 001193 A" (CRIMINAL
# FELONY), "1989 CT 012742 A" (CRIMINAL TRAFFIC), "2013 TR 008812 A" (civil traffic, fuera). El docket
# pone la columna de la imagen antes del número de entrada (DIN). Documento: el "ARREST AFFIDAVIT" del
# Sheriff (en los arrestos con orden es el de la entrega de la orden); el "LIVE WARRANT ISSUED: LIVE
# WARRANT" (la orden con el affidavit de la agencia que investigó) va de apoyo. Arresto en el docket
# como "REARRESTED ON 10/27/2026" y "ARREST SEQ: ...".
INDIAN_RIVER = CountyConfig(
    key="indian_river",
    name="Indian River County",
    state="FL",
    platform="benchmark_web",
    base_url="https://court.indian-river.org/BenchmarkWeb/Home.aspx/Search",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*arrest affidavit\b",
        r"probable cause affidavit",
        r"affidavit of probable cause",
        r"^\s*arrest report\b",
        r"complaint affidavit",
        r"^\s*sworn complaint\b",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b"],
    support_documents=[r"^\s*live warrant issued\b", r"\bwarrant (?:returned )?(?:served|executed)\b",
                       r"\bcapias (?:returned )?served\b"],
    captcha="none",
    throttle_s=2.0,
)

# Bay (06/11/2026): BenchmarkWeb2 (2.9.7) en /BenchmarkWeb2/, con reCAPTCHA solo en la primera
# búsqueda ("?error=Invalid Captcha" sin él) y el formulario ya en "Name". Números como St. Johns:
# "18005242CFMA" (CF, MM, CT; "TR" civil y "PK" parking fuera). La página del caso trae solo el marco:
# el resumen y el docket llegan aparte (ver _BW2_LOAD_JS). El servidor responde con 522 de Cloudflare
# de vez en cuando: se recarga y se repite. Documento: el affidavit de arresto ("PC Arrest") va como
# imagen de la entrada "CASE FILED ... CASE NUMBER ..." (Drinkard, 18005242CFMA); muchas otras
# entradas no tienen imagen pública (hay que pedirla: "Request"). Arresto: "ARREST SEQ: ...".
BAY = CountyConfig(
    key="bay",
    name="Bay County",
    state="FL",
    platform="benchmark_web",
    base_url="https://court.baycoclerk.com/BenchmarkWeb2/Home.aspx/Search",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*case filed\b",
        r"^\s*arrest(?:ing)? affidavit\b",
        r"probable cause affidavit",
        r"affidavit of probable cause",
        r"^\s*arrest report\b",
        r"complaint affidavit",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b"],
    support_documents=[r"\bwarrant (?:returned )?(?:served|executed)\b", r"\bcapias (?:returned )?served\b"],
    captcha="recaptcha_v2",
    throttle_s=2.0,
)

# Clay (08/11/2026): BenchmarkWeb 2.10.106 en su propio dominio, como Bay (resumen y docket por
# AJAX), con el formulario en "All" (como Charlotte) y reCAPTCHA. Cloudflare bloquea ("Sorry, you
# have been blocked") una búsqueda enviada sin el CAPTCHA, y en el Chrome local también bloquea al
# navegador automatizado; en Browserbase pasa. Por eso el CAPTCHA va antes de la primera búsqueda
# (captcha_before_search); las siguientes de la sesión no lo piden. El recuadro lo mete la página con
# jQuery y a veces el script de Google ya pasó: se le pide que lo dibuje (_CAPTCHA_RENDER_JS).
# Números "2020CF000392". Docket "DIN | Image | Date | Entry"; las entradas sin imagen pública dicen
# "Request". Documento: el "ARREST AND BOOKING REPORT" del Sheriff (Lumpkin, 2022CF000713: el único;
# Binderim, 2020CF000392 y Bouie, 2026CF001130: el de la entrega de la orden; Walker, 2021CF001038:
# "ARREST AND BOOKING REPORT FROM ANOTHER COUNTY", el de JSO); el "AFFIDAVIT FOR ARREST WARRANT" va
# de apoyo. Los casos pueden abrirse meses después del hecho (ver window_days_after).
CLAY = CountyConfig(
    key="clay",
    name="Clay County",
    state="FL",
    platform="benchmark_web",
    base_url="https://inquiry.clayclerk.com/Home.aspx/Search",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*arrest and booking report\b",
        r"^\s*arrest(?:ing)? affidavit\b",
        r"probable cause affidavit",
        r"affidavit of probable cause",
        r"^\s*arrest report\b",
        r"complaint affidavit",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b"],
    support_documents=[r"^\s*affidavit for arrest warrant\b", r"\bwarrant (?:returned )?(?:served|executed)\b",
                       r"\bcapias (?:returned )?served\b"],
    captcha="recaptcha_v2",
    captcha_before_search=True,
    # Walker (Orange Park PD, 08/11/2026): hecho del 09/04/2021, lo detuvo JSO en Duval con la orden
    # y el caso se abrió el 12/07/2021, 94 días después. Con 60 quedaba fuera de la ventana.
    window_days_after=120,
    throttle_s=2.0,
)

# St. Lucie (09/11/2026): BenchmarkWeb 2.10.105 ("BenchmarkWebExternal"), resumen y docket por AJAX
# como Bay, formulario en "Name". CAPTCHA de texto (una suma en una imagen, con audio) en CADA
# búsqueda: lo resuelve la persona (captcha="text"; ver core/captcha.py). Números "2020CF002355 A".
# Docket "SEQ# | Image | DIN | Date | Entry": manda SEQ# (el DIN tiene ceros y repetidos). El
# documento es "ARREST REPORT - INITIATING CASE": en Landry (2025MM001336 A, refiling del
# 2025CF001129 A) está en línea; en Arvelo (2020CF002355 A) dice "Request" y solo se pide al Clerk
# (documents_on_request). El número de reporte sale siempre en el resumen (Arvelo: 202019734).
ST_LUCIE = CountyConfig(
    key="st_lucie",
    name="St. Lucie County",
    state="FL",
    platform="benchmark_web",
    base_url="https://courtcasesearch.stlucieclerk.gov/BenchmarkWebExternal/Home.aspx/Search",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        # Primero el que abrió el caso; "ARREST REPORT - NOT INITIATING CASE" es de una detención
        # posterior (Arvelo: la de la probation, 2021).
        r"^\s*arrest report - initiating case\b",
        r"^\s*arrest report\b",
        r"^\s*arrest(?:ing)? affidavit\b",
        r"probable cause affidavit",
        r"affidavit of probable cause",
        r"complaint affidavit",
    ],
    # La orden de una violación de probation es otra detención (Arvelo: la de 2021, no la huida de 2020).
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b", r"\bviol(?:ation)? of (?:probation|community control)\b"],
    support_documents=[r"\bwarrant (?:returned )?(?:served|executed)\b", r"\bcapias (?:returned )?served\b"],
    captcha="text",
    captcha_before_search=True,
    captcha_every_search=True,
    documents_on_request=True,
    throttle_s=2.0,
)

# Martin (10/11/2026): BenchmarkWeb 2.9.29 (más vieja que las demás). Formulario en "All" y la suma de
# St. Lucie, pero solo en la PRIMERA búsqueda de la sesión (la segunda del probe no la pidió). Las filas
# llegan por AJAX después de la tabla vacía y no se despliegan, pero el resumen de cargos con fechas
# se pide igual por el número del caso (Landry, Rondon, Pascual: con fechas); si faltara, los cargos
# salen del resumen del caso, sin fechas. Resumen y docket por AJAX como Bay; docket "DIN | Image | Date |
# Entry". Números "25001236CFAXMX". Documento: "ARREST AFFIDAVIT" (el Probable Cause Affidavit del
# Sheriff; Landry, 25001236CFAXMX, reporte 25-130421).
MARTIN = CountyConfig(
    key="martin",
    name="Martin County",
    state="FL",
    platform="benchmark_web",
    base_url="https://court.martinclerk.com/Home.aspx/Search",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*arrest(?:ing)? affidavit\b",
        r"probable cause affidavit",
        r"affidavit of probable cause",
        r"^\s*arrest report\b",
        r"complaint affidavit",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b", r"\bviol(?:ation)? of (?:probation|community control)\b"],
    support_documents=[r"\bwarrant (?:returned )?(?:served|executed)\b", r"\bcapias (?:returned )?served\b"],
    captcha="text",
    captcha_before_search=True,
    throttle_s=2.0,
)

# Escambia (10/11/2026, BenchmarkWeb 2.6.3.18). Cloudflare pone su verificación antes del portal y
# recién pasada niega los scripts y la imagen de la suma (cloudflare_gate). La suma se pide en cada
# búsqueda ("Invalid Captcha" sin ella, también en la segunda). Docket "Image | Date | Entry", sin
# número de entrada y de la más nueva a la más vieja; el documento es "ARREST REPORT" y el arresto
# sale en "ARREST SEQ: 1 - ...". Algunas entradas tienen "Request" (se piden por email al Clerk).
ESCAMBIA = CountyConfig(
    key="escambia",
    name="Escambia County",
    state="FL",
    platform="benchmark_web",
    base_url="https://public.escambiaclerk.com/BMWebLatest/Home.aspx/Search",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*arrest report\b",
        r"^\s*arrest(?:ing)? affidavit\b",
        r"probable cause affidavit",
        r"affidavit of probable cause",
        r"complaint affidavit",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b", r"\bviol(?:ation)? of (?:probation|community control)\b"],
    support_documents=[r"\bwarrant (?:returned )?(?:served|executed)\b", r"\bcapias (?:returned )?served\b"],
    captcha="text",
    captcha_before_search=True,
    captcha_every_search=True,
    documents_on_request=True,
    cloudflare_gate=True,
    # Si Cloudflare bloquea el portal: el arresto y el número de reporte desde el registro de la cárcel.
    blocked_fallback="escambia_jail",
    throttle_s=2.0,
)

# Volusia (11/11/2026): Case Inquiry propio (ccms.clerk.org), ver adapters/volusia_ccms.py. Sin
# CAPTCHA visible (reCAPTCHA v3 invisible, que no frenó en las pruebas). El documento es el
# "CHARGING AFFIDAVIT" (el "Arrest Affidavit - Volusia (Adult)" del 7.º Circuito), casi siempre la
# entrada 1 del docket. Los "Viewable on Request" se piden al Clerk; el renglón sin ícono está
# listado pero aún sin publicar (Stripling/Bowers, 11/11/2026: el affidavit a los 11 días).
VOLUSIA = CountyConfig(
    key="volusia",
    name="Volusia County",
    state="FL",
    platform="volusia_ccms",
    base_url="https://ccms.clerk.org/inquiry.aspx",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*charging affidavit\b",
        r"^\s*arrest affidavit\b",
        r"probable cause affidavit",
        r"affidavit of probable cause",
        r"^\s*arrest report\b",
        r"complaint affidavit",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b", r"\bviol(?:ation)? of (?:probation|community control)\b",
                      r"\(CONFIDENTIAL\)"],
    support_documents=[r"\bwarrant (?:returned )?(?:served|executed)\b", r"\bcapias (?:returned )?served\b"],
    captcha="none",
    documents_on_request=True,
    request_messages={
        "request": ("The case was found, but its {document} isn't online: the clerk hands it over on request. "
                    "To ask for it, search case {case_number} on the court portal ({portal}) and click the "
                    "\"Viewable on Request\" icon next to that entry in the case docket."),
        "unpublished": ("The case was found, and its {document} is listed in the case docket, but the clerk "
                        "hasn't put it online yet: recent filings can take a while to be reviewed. "
                        "Try again in a few days, or search case {case_number} on the court portal ({portal})."),
        # Levesque (11/11/2026): los dos charging affidavits con el ícono "Confidential".
        "confidential": ("The case was found, but its {document} is marked confidential in the case docket, so "
                         "it isn't online. Recent filings are sometimes opened once the clerk reviews them: try "
                         "again in a few days, or ask the clerk's office about case {case_number}."),
    },
    throttle_s=2.0,
)

# Brevard (13/11/2026): BECA propio (vmatrix1.brevardclerk.us/beca), ver adapters/brevard_beca.py.
# Sin CAPTCHA. El documento es el "ARREST AFFIDAVIT" (el Probable Cause Affidavit de la agencia),
# casi siempre el documento 1. El número de reporte sale en el caso ("Charging Agency"). El portal
# avisa que las imágenes tardan más de lo normal en publicarse: esas salen como "Pending".
BREVARD = CountyConfig(
    key="brevard",
    name="Brevard County",
    state="FL",
    platform="brevard_beca",
    base_url="https://vmatrix1.brevardclerk.us/beca/beca_splash.cfm",
    case_type_codes={"CF": "CF", "MM": "MM", "CT": "CT"},
    document_priority=[
        r"^\s*arrest affidavit\b",
        r"probable cause affidavit",
        r"affidavit of probable cause",
        r"complaint affidavit",
        r"^\s*charging affidavit\b",
        r"^\s*arrest report\b",
    ],
    document_exclude=[r"insolvency", r"indigen", r"\bmotion\b", r"\bviol(?:ation)? of (?:probation|community control)\b",
                      r"\(CONFIDENTIAL\)", r"\(SEALED\)"],
    support_documents=[r"\bwarrant (?:returned )?(?:served|executed)\b", r"\bcapias (?:returned )?served\b"],
    captcha="none",
    documents_on_request=True,
    request_messages={
        "request": ("The case was found, but its {document} isn't online: the clerk hands it over on request. "
                    "To ask for it, search case {case_number} on the court portal ({portal}) or use the clerk's "
                    "\"Purchase Copies\" link next to the case's Register of Actions."),
        "unpublished": ("The case was found, and its {document} is listed in the case's Register of Actions as "
                        "\"Pending\": the clerk hasn't put it online yet (the portal warns that documents are taking "
                        "longer than usual to appear). Try again in a few days, or search case {case_number} on "
                        "the court portal ({portal})."),
        "confidential": ("The case was found, but its {document} is marked confidential in the case's Register "
                         "of Actions, so it isn't online. Ask the clerk's office about case {case_number}."),
        # Listado sin ícono ni enlace (no visto todavía): sin el botón "Request" de Benchmark.
        "": ("The case was found, but its {document} isn't online. Search case {case_number} on the court "
             "portal ({portal}) or ask the clerk's office for it."),
    },
    throttle_s=2.0,
)

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
    LAFAYETTE.key: LAFAYETTE,
    LIBERTY.key: LIBERTY,
    PASCO.key: PASCO,
    WALTON.key: WALTON,
    SANTA_ROSA.key: SANTA_ROSA,
    HIGHLANDS.key: HIGHLANDS,
    DESOTO.key: DESOTO,
    OSCEOLA.key: OSCEOLA,
    FLAGLER.key: FLAGLER,
    ST_JOHNS.key: ST_JOHNS,
    CHARLOTTE.key: CHARLOTTE,
    INDIAN_RIVER.key: INDIAN_RIVER,
    BAY.key: BAY,
    CLAY.key: CLAY,
    ST_LUCIE.key: ST_LUCIE,
    MARTIN.key: MARTIN,
    ESCAMBIA.key: ESCAMBIA,
    VOLUSIA.key: VOLUSIA,
    BREVARD.key: BREVARD,
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
    # Ojo: "Day" (el pueblo de Lafayette) no se agrega: es una palabra común.
    "mayo": "lafayette",
    # Ojo: Spring Hill está en Hernando y Lutz y Odessa son sobre todo de Hillsborough.
    "new port richey": "pasco", "port richey": "pasco", "holiday": "pasco", "hudson": "pasco",
    "zephyrhills": "pasco", "dade city": "pasco", "wesley chapel": "pasco", "land o lakes": "pasco",
    "land o' lakes": "pasco", "trinity": "pasco", "san antonio": "pasco", "saint leo": "pasco",
    "st leo": "pasco", "shady hills": "pasco",
    # Ojo: Santa Rosa Beach está en Walton, no en Santa Rosa County.
    "defuniak springs": "walton", "de funiak springs": "walton", "freeport": "walton",
    "santa rosa beach": "walton", "miramar beach": "walton", "inlet beach": "walton", "paxton": "walton",
    "mossy head": "walton", "ponce de leon": "walton", "rosemary beach": "walton", "seaside": "walton",
    "grayton beach": "walton", "watersound": "walton", "blue mountain beach": "walton", "dune allen": "walton",
    "milton": "santa_rosa", "east milton": "santa_rosa", "pace": "santa_rosa", "navarre": "santa_rosa",
    "navarre beach": "santa_rosa", "gulf breeze": "santa_rosa", "jay": "santa_rosa", "bagdad": "santa_rosa",
    "holley": "santa_rosa",
    "sebring": "highlands", "avon park": "highlands", "lake placid": "highlands", "lorida": "highlands",
    "venus": "highlands", "sun n lake": "highlands",
    "arcadia": "desoto", "nocatee": "desoto", "fort ogden": "desoto", "southeast arcadia": "desoto",
    "bristol": "liberty", "hosford": "liberty", "sumatra": "liberty", "telogia": "liberty",
    "sanford": "seminole", "altamonte springs": "seminole", "oviedo": "seminole", "lake mary": "seminole",
    "casselberry": "seminole", "longwood": "seminole", "winter springs": "seminole",
    "kissimmee": "osceola", "st cloud": "osceola", "saint cloud": "osceola", "poinciana": "osceola",
    "celebration": "osceola", "buenaventura lakes": "osceola", "campbell": "osceola", "harmony": "osceola",
    "narcoossee": "osceola", "kenansville": "osceola", "yeehaw junction": "osceola", "intercession city": "osceola",
    "clermont": "lake", "leesburg": "lake", "eustis": "lake", "tavares": "lake", "mount dora": "lake",
    "daytona beach": "volusia", "deltona": "volusia", "deland": "volusia", "orange city": "volusia",
    "port orange": "volusia", "new smyrna beach": "volusia", "ormond beach": "volusia",
    "edgewater": "volusia", "debary": "volusia", "holly hill": "volusia", "south daytona": "volusia",
    "daytona beach shores": "volusia", "ponce inlet": "volusia", "lake helen": "volusia", "pierson": "volusia",
    "oak hill": "volusia", "ormond by the sea": "volusia", "osteen": "volusia", "enterprise": "volusia",
    "de leon springs": "volusia", "seville": "volusia",
    "melbourne": "brevard", "palm bay": "brevard", "titusville": "brevard", "cocoa": "brevard",
    "cocoa beach": "brevard", "rockledge": "brevard", "satellite beach": "brevard", "indialantic": "brevard",
    "indian harbour beach": "brevard", "melbourne beach": "brevard", "west melbourne": "brevard",
    "cape canaveral": "brevard", "merritt island": "brevard", "viera": "brevard", "mims": "brevard",
    "malabar": "brevard", "grant-valkaria": "brevard", "grant valkaria": "brevard", "sharpes": "brevard",
    "port st john": "brevard", "port saint john": "brevard", "palm shores": "brevard", "micco": "brevard",
    "barefoot bay": "brevard", "suntree": "brevard", "scottsmoor": "brevard", "canaveral groves": "brevard",
    "lakeland": "polk", "winter haven": "polk", "haines city": "polk",
    "tampa": "hillsborough", "brandon": "hillsborough", "plant city": "hillsborough",
    "st petersburg": "pinellas", "saint petersburg": "pinellas", "clearwater": "pinellas", "largo": "pinellas",
    "miami": "miami_dade", "hialeah": "miami_dade", "miami beach": "miami_dade", "homestead": "miami_dade",
    "fort lauderdale": "broward", "hollywood": "broward", "pembroke pines": "broward", "coral springs": "broward",
    "west palm beach": "palm_beach", "boca raton": "palm_beach", "boynton beach": "palm_beach",
    "jacksonville": "duval", "tallahassee": "leon", "gainesville": "alachua", "pensacola": "escambia",
    "fort myers": "lee", "cape coral": "lee", "naples": "collier", "sarasota": "sarasota",
    "bradenton": "manatee", "port st lucie": "st_lucie", "port saint lucie": "st_lucie", "fort pierce": "st_lucie",
    "st lucie village": "st_lucie", "saint lucie village": "st_lucie", "key west": "monroe",
    "panama city": "bay", "mexico beach": "bay", "palm coast": "flagler", "bunnell": "flagler", "flagler beach": "flagler", "beverly beach": "flagler",
    "espanola": "flagler", "hammock": "flagler", "st augustine": "st_johns", "st augustine beach": "st_johns", "saint augustine": "st_johns",
    "ponte vedra": "st_johns", "ponte vedra beach": "st_johns", "fruit cove": "st_johns", "hastings": "st_johns",
    "world golf village": "st_johns", "vilano beach": "st_johns", "palm valley": "st_johns", "elkton": "st_johns",
    "flagler estates": "st_johns", "switzerland": "st_johns",
    "punta gorda": "charlotte", "port charlotte": "charlotte", "rotonda west": "charlotte", "rotonda": "charlotte",
    "placida": "charlotte", "babcock ranch": "charlotte", "murdock": "charlotte", "harbour heights": "charlotte",
    "charlotte harbor": "charlotte", "grove city": "charlotte", "deep creek": "charlotte", "solana": "charlotte",
    "punta gorda isles": "charlotte", "burnt store": "charlotte",
    "vero beach": "indian_river", "sebastian": "indian_river", "fellsmere": "indian_river",
    "indian river shores": "indian_river", "orchid": "indian_river", "wabasso": "indian_river",
    "roseland": "indian_river", "gifford": "indian_river", "florida ridge": "indian_river",
    "vero lake estates": "indian_river", "winter beach": "indian_river", "vero beach south": "indian_river",
    "panama city beach": "bay", "lynn haven": "bay", "callaway": "bay", "springfield": "bay", "parker": "bay",
    "youngstown": "bay", "fountain": "bay", "southport": "bay", "bayou george": "bay", "hiland park": "bay",
    "cedar grove": "bay", "laguna beach": "bay", "tyndall afb": "bay", "spring hill": "hernando",
    "stuart": "martin", "jensen beach": "martin", "palm city": "martin", "hobe sound": "martin",
    "indiantown": "martin", "port salerno": "martin", "sewalls point": "martin", "sewall s point": "martin",
    "jupiter island": "martin", "ocean breeze": "martin",
    "green cove springs": "clay", "orange park": "clay", "keystone heights": "clay", "penney farms": "clay",
    "fleming island": "clay", "middleburg": "clay", "oakleaf": "clay", "oakleaf plantation": "clay",
    "lake asbury": "clay", "doctors inlet": "clay",
    # Ojo: Gulf Breeze y Navarre son de Santa Rosa. Pensacola Beach sí es de Escambia.
    "pensacola beach": "escambia", "cantonment": "escambia", "century": "escambia", "molino": "escambia",
    "perdido key": "escambia", "warrington": "escambia", "ensley": "escambia", "ferry pass": "escambia",
    "brent": "escambia", "myrtle grove": "escambia", "bellview": "escambia", "west pensacola": "escambia",
    "goulding": "escambia", "gonzalez": "escambia", "beulah": "escambia", "walnut hill": "escambia",
    "mcdavid": "escambia", "bratt": "escambia", "innerarity point": "escambia",
    "the villages": "sumter",
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
    "lafayette county so": "lafayette", "liberty county so": "liberty",
    "walton county so": "walton", "defuniak springs pd": "walton", "de funiak springs pd": "walton",
    "santa rosa county so": "santa_rosa", "milton pd": "santa_rosa", "gulf breeze pd": "santa_rosa",
    "jay pd": "santa_rosa",
    "highlands county so": "highlands", "sebring pd": "highlands", "avon park pd": "highlands",
    "lake placid pd": "highlands", "desoto county so": "desoto", "de soto county so": "desoto", "arcadia pd": "desoto",
    "pasco so": "pasco", "pasco county so": "pasco", "new port richey pd": "pasco", "port richey pd": "pasco",
    "zephyrhills pd": "pasco", "dade city pd": "pasco",
    "seminole county so": "seminole",
    "sanford pd": "seminole", "osceola county so": "osceola", "kissimmee pd": "osceola",
    "st cloud pd": "osceola", "saint cloud pd": "osceola",
    "flagler county so": "flagler", "bunnell pd": "flagler", "flagler beach pd": "flagler",
    "charlotte county so": "charlotte", "punta gorda pd": "charlotte",
    "indian river county so": "indian_river", "vero beach pd": "indian_river", "sebastian pd": "indian_river",
    "fellsmere pd": "indian_river", "indian river shores pd": "indian_river",
    "indian river shores public safety": "indian_river",
    "bay county so": "bay", "panama city pd": "bay", "panama city beach pd": "bay", "lynn haven pd": "bay",
    "springfield pd": "bay", "parker pd": "bay", "mexico beach pd": "bay",
    "martin county so": "martin", "stuart pd": "martin", "sewalls point pd": "martin", "sewall s point pd": "martin",
    "st lucie county so": "st_lucie", "port st lucie pd": "st_lucie", "fort pierce pd": "st_lucie",
    "escambia county so": "escambia", "pensacola pd": "escambia", "university of west florida pd": "escambia",
    "uwf pd": "escambia",
    "clay county so": "clay", "orange park pd": "clay", "green cove springs pd": "clay",
    "st johns county so": "st_johns", "st augustine pd": "st_johns", "st augustine beach pd": "st_johns",
    "volusia county so": "volusia", "volusia so": "volusia", "daytona beach pd": "volusia", "deland pd": "volusia",
    "ormond beach pd": "volusia", "port orange pd": "volusia", "new smyrna beach pd": "volusia",
    "edgewater pd": "volusia", "holly hill pd": "volusia", "south daytona pd": "volusia",
    "ponce inlet pd": "volusia", "lake helen pd": "volusia", "orange city pd": "volusia",
    "daytona beach shores of public safety": "volusia",
    "lake county so": "lake", "polk county so": "polk",
    "brevard county so": "brevard", "melbourne pd": "brevard", "palm bay pd": "brevard", "titusville pd": "brevard",
    "cocoa pd": "brevard", "cocoa beach pd": "brevard", "rockledge pd": "brevard", "satellite beach pd": "brevard",
    "indialantic pd": "brevard", "indian harbour beach pd": "brevard", "melbourne beach pd": "brevard",
    "west melbourne pd": "brevard",
    "hillsborough county so": "hillsborough", "tampa pd": "hillsborough",
    "jacksonville so": "duval", "miami dade pd": "miami_dade", "miami pd": "miami_dade",
    "broward county so": "broward", "palm beach county so": "palm_beach", "pinellas county so": "pinellas",
}
