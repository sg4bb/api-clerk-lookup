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
| Wakulla | 37.313 | 65 | Sin verificar | Configurado (19/10/2026); en prueba |
| DeSoto | 35.487 | 14 | Requiere registro notarizado | |
| Baker | 28.899 | 02 | Sin verificar | |
| Bradford | 27.335 | 04 | En parte | |
| Washington | 26.568 | 67 | Sin verificar | |
| Hardee | 25.883 | 25 | En parte ("más restricciones" que registrados) | |
| Holmes | 20.059 | 30 | Sin verificar | |
| Gilchrist | 19.503 | 21 | Sin verificar | |
| Madison | 18.649 | 40 | Sin verificar | |
| Dixie | 17.555 | 15 | Sin verificar | |
| Gulf | 16.947 | 23 | Sin verificar | |
| Union | 16.100 | 63 | Sin verificar | |
| Jefferson | 15.667 | 33 | Sin verificar | |
| Hamilton | 14.228 | 24 | Sin verificar | El aviso prohíbe republicar los datos |
| Calhoun | 13.700 | 07 | En parte | |
| Franklin | 13.321 | 19 | Sin verificar | |
| Glades | 12.815 | 22 | Sin verificar | |
| Lafayette | 8.504 | 34 | En parte | |
| Liberty | 8.016 | 39 | Sin verificar | |

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
