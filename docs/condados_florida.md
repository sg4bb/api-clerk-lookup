# Condados de Florida: portales de casos penales

Investigación del 09/10/2026 para decidir el orden de cobertura. Población:
estimación oficial al 01/04/2024 (Florida EDR). "Sin verificar" significa que
no se pudo abrir la página oficial y el dato viene de resultados de búsqueda.

Regla estatal (matriz de acceso de la Corte Suprema, AOSC24-65): los casos
penales son visibles para el público anónimo, salvo lo sellado o confidencial.
En la práctica, muchos condados muestran las imágenes ya redactadas al
instante y marcan otras como **"View on Request"** (candado): hay que pedirlas
y el clerk las revisa en minutos o hasta 2 días. Eso es lo que más afecta a la
herramienta, y hay que comprobarlo a mano en cada plataforma.

## Resumen por plataforma

| Plataforma | Condados | Población | Adaptadores |
| --- | ---: | ---: | --- |
| Orange myeClerk (hecho) | 1 | 1,51 M | 1 |
| Civitek OCRS (`civitekflorida.com/ocrs/county/NN/`) | 32 | 2,00 M | 1 para todos |
| BenchmarkWeb (Pioneer / Catalis, `.../Home.aspx/Search`) | 10 | 2,62 M | 1 para todos |
| Sistema propio de cada condado | 22 | 16,85 M | 1 por condado |
| Sin registros en línea | 2 | 0,07 M | — |

## Orden propuesto (por lotes)

| Lote | Qué | Condados | Por qué |
| --- | --- | --- | --- |
| 1 | Adaptador Civitek | Marion (Ocala) y Sumter primero; luego los otros 30 | Un adaptador cubre 32 condados; Marion y Sumter son del área de Orlando |
| 2 | Adaptador BenchmarkWeb | Osceola y Flagler primero; luego St. Lucie, St. Johns, Escambia, Charlotte, Bay, Martin, Indian River, Clay | Un adaptador cubre 10; Osceola es vecino de Orange |
| 3 | Centro de Florida (propios) | Seminole, Volusia, Brevard, Polk, Lake | Los condados que más cubren FOX 35, WESH, WFTV y ClickOrlando |
| 4 | Grandes ciudades (propios) | Hillsborough, Palm Beach, Broward, Pinellas, Duval, Lee, Pasco, Sarasota, Manatee, Collier | Mucha población; uno por uno |
| 5 | Resto (propios) | Alachua, Citrus, Okaloosa, Monroe, Gadsden | Menor población o con obstáculos |
| — | Limitados | Miami-Dade, Leon | Miami-Dade no muestra documentos al público; Leon los envía por correo pagando $1 por página |
| — | Sin cobertura posible | Suwannee, Taylor | No tienen registros judiciales en línea |

Con los lotes 1 a 3 quedarían cubiertos 48 condados y cerca del 40 % de la
población del estado.

## Detalle por condado

Orden: el de los lotes de arriba; dentro de cada lote, por población.

### Hecho

| Condado | Población | Plataforma | Búsqueda anónima | CAPTCHA | Documentos sin cuenta | Notas |
| --- | ---: | --- | --- | --- | --- | --- |
| Orange | 1.511.568 | myeClerk (propio) | Sí | reCAPTCHA | Sí | En producción |

### Lote 1 — Civitek OCRS

Flujo (comprobado en Marion el 12/10/2026): entrar al condado → "Public" →
"I Agree" → apellido, nombre, tipos de caso (CF, MM, CT...) y rango de fechas
de apertura → Cloudflare Turnstile (en Browserbase pasó solo, sin tocar
nada) → Search. Los resultados agrupan los casos por persona. La página del
caso no muestra fecha de nacimiento ni fecha de la ofensa; la fecha de los
cargos es la de presentación de la acusación. El docket muestra 10 líneas por
defecto: hay que elegir "ALL". Los documentos son enlaces directos a
`docketimage.pdf` y se descargan sin cuenta. Imágenes con marca de agua;
algunas pueden ser "View on Request".

Qué documentos se descargan:

- **Principal:** el arrest affidavit (o probable cause / complaint affidavit)
  de **Doc # más bajo**, es decir, el que entró primero al expediente. Los
  posteriores suelen ser violaciones de la fianza o cargos nuevos. Se ordena
  por Doc # y no por fecha porque hay fechas mal cargadas (Leroy Parks III: un
  20/04/2026 en agosto).
- **De apoyo:** la orden de captura ejecutada ("WARRANT SERVED" o "CAPIAS
  SERVED"), si existe. Cuando el arresto fue con orden, el arrest affidavit es
  solo el formulario de la captura; la orden trae el número de reporte de la
  agencia que investigó el hecho y, a veces, su narración (Parks: Doc #6 con el
  affidavit de Ocala PD; García-Luis: Doc #10 con OCPD26-71998). También el
  "WARRANT AFFIDAVIT" (Sumter): la declaración del agente para pedir la orden,
  con la narración y el número de reporte (Almany: Doc #3, SCSO26OFF004530).
  Si no hay arrest affidavit, la orden se entrega como principal.

Nombre del acusado: se compara con el DEFENDANT del caso y, si no coincide,
con los ALSO KNOWN AS del propio caso. El clerk a veces escribe mal al acusado
(Almany quedó como "ALAMANY THOMAS, BRANDY RAECHEL"). El nombre de la fila de
resultados no cuenta como alias: la persona buscada puede salir en casos ajenos
como agente (Almany era detective; esas filas no traen sexo ni dirección).
Los casos descartados por nombre salen en la consola como `Descarto ...`.

Al sumar un condado, revisar en la consola las líneas `Documentos en ...` y
`Documento de apoyo en ...` por si usa otros nombres. Los nombres genéricos
(arrest affidavit, probable cause affidavit, warrant served...) son comunes a
todo Civitek; los propios de un condado ("ARREST REPORT" en Putnam, "ARREST
MITTIMUS" en Levy, "ARREST AND BOOKING SHEET" en Nassau) van solo en su línea
de `counties/florida.py`, para que no los tome otro condado.

| Condado | Población | Código | Documentos sin cuenta | Notas |
| --- | ---: | --- | --- | --- |
| Marion (Ocala) | 419.510 | 42 | Sí (arrest affidavit descargado) | Hecho. Archivo viejo aparte (FileBound) |
| Hernando | 210.577 | 27 | Sí (arrest affidavit / booking report, probable cause affidavit) | Hecho (18/10/2026). Probado: Gordon, Hall, Cusati, Khalil, Field; Colon (caso más cercano); Monday (sin caso: el sospechoso murió). Número local "23000080CFAXMX" (año y número antes del tipo). La fecha "Initial" de los cargos es la del arresto; la de ofensa a veces viene mal cargada (Gordon: 2007) |
| Santa Rosa | 207.983 | 57 | Solo pidiéndolas ("request images") | |
| Sumter | 156.743 | 60 | Sí (sworn arrest complaint affidavit y warrant affidavit) | Hecho (14/10/2026). Probado: O'Brien, Almany, Harris, Turner, Vaughn. El servidor de documentos a veces cae por horas ("not available at this time") |
| Highlands | 106.109 | 28 | Según el clerk, solo abogados registrados | |
| Nassau | 103.990 | 45 | Sí (arrest and booking sheet) | Hecho (19/10/2026). Probado: Combs, Lee (dos padres: basta el primero), Rackley; Brown sin caso nuevo (violación de probation dentro de un caso de 2023). El booking sheet trae el número de reporte del NCSO y la hora del arresto |
| Walton | 87.728 | 66 | Sin verificar | Rango de fechas de máximo 7 días |
| Putnam | 76.138 | 54 | Sí (arrest report; complaint = probable cause affidavit, de apoyo) | Hecho (18/10/2026). Probado: Deller, Lanigan, Carpenter, Gomez-Hernandez (cada uno con su probable cause affidavit de apoyo) |
| Columbia | 72.155 | 12 | Sí (arrest report / arrest affidavit; en arrestos con orden, capias/warrant returned served, y la orden con su affidavit de apoyo) | Hecho (19/10/2026). Probado: Gilliard (arrest report), Shaffer (orden emitida 16 días antes del arresto); Colwell queda ambiguo (racha de robos: varios casos con "Initial" a 2 días). El docket anota "Agency Case Number" |
| Jackson | 49.345 | 32 | Sí (affidavit-complaint del Sheriff; en el docket "AFFIDAVIT-COMPLAINT" o solo "COMPLAINT") | Hecho (19/10/2026). Probado: Gilbert (complaint), Peterson (affidavit-complaint; Facebook dio una fecha de publicación falsa), Charles (affidavit-complaint "View on Request": sale el número de reporte sin PDF); Arriaga Gonzales Noe no aparece en el portal. El docket anota "Agency Case Number" (26-032173). Oculta casos con orden de arresto activa |
| Levy | 45.845 | 38 | Sí (sworn complaint o arrest mittimus: formulario de denuncia y arresto del Sheriff) | Hecho (18/10/2026). Probado: Vessels, McQuiston (arrest mittimus del 685; el 686, abierto el mismo día, sale como mismo arresto); Mitchell sin caso en el portal. levyso.com mandaba las notas comprimidas en zstd (ya no se ofrece zstd) |
| Hendry | 45.413 | 26 | Sí (booking report filed: el Arrest Report del Sheriff) | Hecho (19/10/2026). Probado: Gonzalez (booking report), Azcuy Alfonso (booking report + warrant affidavit), Thicklin (sin PDF descargable: sale el número de reporte). En arrestos con orden, el "Agency Case Number" del docket puede ser el de la orden (26WAR000632) y no el del hecho (26OFF002038, en el PDF). hendrysheriff.org carga la nota con JavaScript y da como publicación el día en que se abre. El formulario es ancho: el CAPTCHA se enfoca angostando su contenedor. Garcia-Zunun (caso de 2012) sin caso |
| Okeechobee | 40.230 | 47 | Sí (arrest affidavit: "Individual Arrest Affidavit" del Sheriff) | Hecho (19/10/2026). Probado: McNeil, Adamo. Usa los nombres genéricos de Civitek |
| Wakulla | 37.313 | 65 | Sí (arrest report package: informe del Sheriff, con la orden si la hubo) | Hecho (19/10/2026). Probado: Woodward (eligió 22CF000335, el arresto del 07/09/2022 con battery on LEO, entre tres casos abiertos ese día). wcso.org da la publicación como "09-07-2022" (mes-día) |
| DeSoto | 35.487 | 14 | Requiere registro notarizado | |
| Baker | 28.899 | 02 | En parte ("ARREST REPORT - INITIATING CASE" y "FIRST APPEARANCE ORDER" se ven; el "SWORN COMPLAINT" pide solicitud) | Hecho (21/10/2026). Probado: Roberts (caso abierto como 250 y renumerado 249 en la división BX), Jewell (el portal tardó 45 s en responder), Trueblood (eligió el caso principal sobre el consolidado; antes ganaba una multa solo por el nombre). Rowland sin caso en el portal |
| Bradford | 27.335 | 04 | Sí (arrest report, junto a la first appearance order) | Hecho (21/10/2026). Probado: Drerup (tres arrestados; el 19CF000613 sale como mismo arresto), Jordan. Sin caso, con razón: Hodges y Kerr murieron en el hecho |
| Washington | 26.568 | 67 | Sí (bench warrant w/ affidavit/complaint: orden con el probable cause affidavit; el "affidavit - complaint" de los arrestos en el momento suele tener candado) | Hecho (21/10/2026). Probado: Porter (caso abierto 54 días después del hecho: la ventana pasó de 45 a 60 días), Carmona (affidavit con candado), Leverette. Mekara sin caso (detenido en Alabama). Fennell: Facebook corta la publicación antes del nombre |
| Hardee | 25.883 | 25 | Sí (arrest report filed: el informe del Sheriff; en arrestos con orden es el affidavit de la orden, con la "warrant returned served" de apoyo) | Hecho (22/10/2026). Probado: Layport, Gray (arresto con orden de FDLE: arrest report filed + warrant returned served), Skiba, Albritton. El docket anota "Agency Case Number": el del Sheriff (302024008773) o el de la agencia que investigó (TM735772, FDLE) |
| Holmes | 20.059 | 30 | No (ninguna entrada del docket tiene imagen en línea, ni en casos de 2023; el informe del Sheriff figura como "COMPLAINT AND NARRATIVE") | Hecho (22/10/2026), solo con número de reporte: encuentra el caso y da el "Agency Case Number", sin PDF. Probado: Doepel, Jones y Albright (mismo número de reporte), Wood (2023: eligió el caso de la agresión entre cuatro del mismo arresto). El portal abre directamente el aviso legal ("I Agree"), sin el paso "Public" |
| Gilchrist | 19.503 | 21 | En parte (arrest report del Sheriff; en arrestos con orden, "sworn complaint" = warrant affidavit, de apoyo; algunos con candado) | Hecho (22/10/2026). Probado: Bonilla (arrest report), Bartges (eligió 26CF000143, la huida del 06/08, entre tres casos suyos; con el modelo local sin fecha salía ambiguo), Lee (arrest report con candado: sale el número de reporte); Burns y Coen sin caso. El docket anota "Agency Case Number" |
| Madison | 18.649 | 40 | Sí ("arrest report" o "arrest report - initiating case", del Sheriff, la policía o FHP) | Hecho (23/10/2026). Probado: Davis (arrest report - initiating case de FHP; caso abierto un año después del choque), McCray, Barnhart (también desde la publicación de Facebook del Sheriff). Trevon Brown era Madison County, Alabama: queda fuera, con razón |
| Dixie | 17.555 | 15 | No en línea (el "probable cause / arrest affidavit" o "arrest affidavit" está siempre con candado: solo a pedido) | Hecho (23/10/2026), en general solo con número de reporte: el docket lo anota como "Agency Citation 26OFF000169". Si hubo allanamiento, el "affidavit for search warrant" es público y se entrega como documento de apoyo. Probado: Reyes, Quinones, Buck (máquinas de juego); Williamson sin caso del 17/02 (la búsqueda sin fechas sugirió su arresto de diciembre, otro hecho) |
| Gulf | 16.947 | 23 | Sí ("complaint/pc; arrest report; 1st. app. order": denuncia con causa probable, informe de arresto y primera audiencia en un PDF) | Hecho (27/10/2026). Probado: Harris Ottoson (Doc #8), O'Bryan y Koepke (el modelo pegó el segundo nombre al apellido: ahora se prueba sin la primera palabra; Koepke sale como caso más cercano porque la ofensa es de un mes antes del arresto). El 24/10/2026 el portal decía "Dockets are currently unavailable" en todos los casos |
| Union | 16.100 | 63 | En parte (arrest report del Sheriff; "sworn complaint" = warrant affidavit, de apoyo; en algunos casos el arrest report está con candado o bloqueado) | Hecho (24/10/2026). Probado: Danlovich y George (arrest report), Coleman y Smith (arrest report bloqueado: sale el número de reporte). Sin caso, con razón: Holton (murió antes de ser acusado) y Geer (la búsqueda sin fechas solo halló casos viejos con otros cargos) |
| Jefferson | 15.667 | 33 | En parte ("arrest, booking, first appearance, filed": paquete con el arrest affidavit del Sheriff; a veces con candado) | Hecho (26/10/2026). Probado: Bhatt y Antes (paquete con el arrest affidavit y la narración; en Antes, 24CF000157 sale como mismo arresto), Mack (paquete con candado; el número viene como "Agency Case Number modified") |
| Hamilton | 14.228 | 24 | Sí ("arrest report--HCSO25OFF000413", con el número de reporte en el nombre: de ahí sale el número de reporte) | Hecho (27/10/2026). Probado: Daniel y Ellis (arrest report; en el caso de Joan Ellis el clerk escribió "HCSO5OFF000808" por HCSO25OFF000808), Epstein (orden ejecutada de FDLE). Sin documento, con razón: Ogle (murió antes del arresto). Martinez (OALE, 28/09/2026) aún sin caso. El aviso prohíbe republicar los datos |
| Calhoun | 13.700 | 07 | Sí ("probable cause affidavit" o "arrest report - initiating case" del Sheriff; "first appearance arrest packet" de apoyo) | Hecho (28/10/2026). Probado: Pierce (tercera persona de la nota), Bailey, Elrod (26CF000094 sale como mismo arresto), Glaze (arrest report + first appearance arrest packet). Reagan: lo arrestó Jackson y su caso está en Jackson |
| Franklin | 13.321 | 19 | Sí ("probable cause filed": Warrantless Arrest Probable Cause Statement con la narración; "arrest/booking report - initiating case" = ficha de la cárcel, de apoyo, y principal si no hay causa probable) | Hecho (28/10/2026). Probado: Keith (causa probable + ficha), Reed (arresto con orden: solo la ficha; con la categoría de cargos "threats"), Alexander-Davis. Nowling: orden de Liberty, solo lo detuvieron en Franklin |
| Glades | 12.815 | 22 | Sí ("booking report": informe del Sheriff con número de reporte, CAD y la causa probable, como en Hendry) | Hecho (28/10/2026). Probado: Kolotka (booking report del MM del arresto; empataba con los CF de hurto abiertos después y se resuelve por la fecha "Initial"), Telci (orden servida en la cárcel: el booking report es el de la orden). Rivers (citación de FWC, sin informe: nulo), Johnson (CSAM, sin informe publicado: nulo) |
| Lafayette | 8.504 | 34 | Sí (informe del Sheriff, muchas veces en un paquete con los nombres separados por comas: "arrest report, incident report, ..." o "incident report, arrest report, ..."; "arrest report - initiating case", a veces con candado; o la "probable cause affidavit" dentro del paquete de primera audiencia; como los paquetes nombran la "application for criminal indigent status", Lafayette solo excluye la entrada que es únicamente eso) | Hecho (29/10/2026). Probado: J. Jones (paquete "incident report, arrest report", con LCSO25OFF000236; 25000019 y 25000020 salen como mismo arresto), M. D. Jones (paquete "arrest report, incident report", LCSO26OFF000278), W. Jones (probable cause affidavit dentro del paquete de primera audiencia), Blake (caso encontrado, número 24OFF000119; el arrest report con candado). Sin caso: la nota de 16 arrestados (Thomas, Reid, Land), Forney (solo el resumen del muro de pago) y Molnar (no lo acusaron). Pelea de gallos: la página ya no existe |
| Liberty | 8.016 | 39 | Sí ("probable cause affidavit" = PC Affidavit del Sheriff, o "probable cause" a secas; en arrestos con orden, "warrant affidavit & served warrant" con la narración, de apoyo y principal si no hay otro) | Hecho (28/10/2026). Probado: McKnight (probable cause affidavit, reporte 26000884), Shipley (probable cause affidavit; 26000099 sale como mismo arresto). Nowling: el caso está (26000069CFMXAX, a mano), pero qwen3 puso Franklin como condado. Cox: nota de la condena; con la fecha de condena sale encontrado (25000066CFMXAX), con "probable cause" a secas como documento. Allen: caso federal, nulo. Shaffer: sin casos en Liberty (la parada la hizo Jackson) |

### Lote 2 — BenchmarkWeb

Búsqueda por nombre, número de caso, citación, rango de fechas y tipo de
corte. Algunos condados activan un CAPTCHA de texto (con audio) incluido en
la plataforma.

| Condado | Población | Búsqueda | CAPTCHA | Documentos sin cuenta | Notas |
| --- | ---: | --- | --- | --- | --- |
| Osceola | 451.231 | courts.osceolaclerk.com/BenchmarkWeb | No visto | Sin verificar | |
| St. Lucie | 385.746 | courtcasesearch.stlucieclerk.gov/BenchmarkWebExternal | Texto | Sin verificar | |
| Escambia | 336.358 | public.escambiaclerk.com/BMWebLatest | Texto | Sin verificar | Versión vieja (2.6) |
| St. Johns | 331.479 | apps.stjohnsclerk.com/Benchmark | No visto | Sí, según el clerk ("free of charge") | |
| Clay | 236.365 | inquiry.clayclerk.com | Sin verificar | Sin verificar | Benchmark probable (por la URL) |
| Charlotte | 210.645 | courts.charlotteclerk.com/Benchmark | No visto | Sin verificar | |
| Bay | 196.112 | court.baycoclerk.com/BenchmarkWeb2 | No visto | Sin verificar | Anónimo: solo nombre y número |
| Indian River | 171.029 | court.indian-river.org/BenchmarkWeb | No mencionado | Sí, según el clerk | |
| Martin | 164.853 | court.martinclerk.com | Texto | Probable | |
| Flagler | 136.310 | cases.flaglerclerk.gov | Sin verificar | En parte (affidavits incluidos según nota de 2015) | |

### Lote 3 — Centro de Florida

| Condado | Población | Sistema | CAPTCHA | Documentos sin cuenta | Notas |
| --- | ---: | --- | --- | --- | --- |
| Polk | 826.090 | Portal penal nuevo (junio 2025) | Sin verificar | Sin verificar | Hay que revisarlo a mano |
| Brevard | 653.703 | BECA | Sin verificar | Sin verificar | Aviso de demoras en imágenes (julio 2026) |
| Volusia | 594.643 | ccms.clerk.org | No mencionado | Sí, muchos; otros a pedido | Archivos diarios de arrestos (app02.clerk.org/cr_24) |
| Seminole | 493.282 | courtrecords.seminoleclerk.org/criminal | Tras 10 búsquedas o 30 imágenes por hora | Sí | Busca por fecha de nacimiento y rango de fechas |
| Lake | 433.331 | ShowCase Web | No mencionado | Pocas al instante; la mayoría a pedido (24-36 h) | El más lento de automatizar del lote |

### Lote 4 — Grandes ciudades

| Condado | Población | Sistema | CAPTCHA | Documentos sin cuenta | Notas |
| --- | ---: | --- | --- | --- | --- |
| Broward | 1.981.888 | CaseSearchECA | No visto | Probable | Máximo 200 resultados |
| Hillsborough | 1.560.449 | HOVER | Sin verificar | Sí, redactados | Índice semanal de nombres descargable |
| Palm Beach | 1.545.905 | eCaseView | reCAPTCHA | Sí, gratis | Algunos a pedido |
| Duval | 1.062.593 | CORE | Sí | Sí; los "a pedido" exigen cuenta | |
| Pinellas | 971.218 | courtrecords.mypinellasclerk.gov | Sí | Sí; otros a pedido | |
| Lee | 827.016 | matrix.leeclerk.org | No mencionado | Sí, redactados | Búsqueda por nombre y fechas |
| Pasco | 633.029 | Sin verificar (CLERICUS) | Sin verificar | Sin verificar | |
| Sarasota | 479.027 | ClerkNet 3.0 | No mencionado | Sin verificar | |
| Manatee | 455.356 | records.manateeclerk.com | Sin verificar | Sí ("sin suscripción") | |
| Collier | 408.381 | CMSWeb | No mencionado | Sí, gratis | |

### Lote 5 — Resto

| Condado | Población | Sistema | CAPTCHA | Documentos sin cuenta | Notas |
| --- | ---: | --- | --- | --- | --- |
| Alachua | 296.313 | Propio | Texto, al entrar | Sí | Máximo 60 documentos por hora |
| Okaloosa | 221.806 | ClerkQuest (sobre Tyler Odyssey) | Cloudflare | En parte | |
| Citrus | 166.151 | SCORSS | En cada búsqueda anónima | En parte | Exportación de datos masiva gratis |
| Monroe | 84.147 | cr.monroe-clerk.com | Sin verificar | Sí, redactados | Rango de fechas obligatorio |
| Gadsden | 44.853 | CourtScribe | Sin verificar | Sin verificar | Fuera de servicio domingos de 0 a 6 a. m. |

### Limitados y sin cobertura

| Condado | Población | Motivo |
| --- | ---: | --- |
| Miami-Dade | 2.774.841 | Los documentos solo los ven abogados, fiscalía y jueces. Se podría devolver el número de caso, no el PDF |
| Leon | 302.197 | Documentos solo con cuenta notarizada o por pedido pagado ($1 por página) que llega por correo |
| Suwannee | 46.519 | Sin registros judiciales en línea |
| Taylor | 21.802 | Sin registros judiciales en línea |

## Fuentes principales

- Población: [Florida EDR, 2024 Population Estimates](https://edr.state.fl.us/content/population-demographics/data/2024_Pop_Estimates.pdf)
- [Lista de condados en Civitek OCRS](https://www.civitekflorida.com/ocrs/county/) y [guía de usuario público (2016)](https://cdn.ymaws.com/www.flclerks.com/resource/resmgr/Temporary_Files/OCRSUserGuide-PublicUser.pdf)
- Páginas de cada clerk citadas en la investigación (Marion, Seminole, Volusia, Lake, Osceola, Palm Beach, Hillsborough, Lee, Collier, Manatee, Miami-Dade, Leon, Suwannee, Taylor, entre otras).
