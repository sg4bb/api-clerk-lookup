# clerk-lookup

Servicio que recibe los datos de un caso y devuelve **un único** Incident Report / Arrest Affidavit desde el portal del Clerk of Courts del condado. Cubre Orange County, FL (myeClerk), y Marion, Sumter, Hernando, Putnam, Levy, Nassau, Columbia, Jackson, Hendry, Okeechobee, Wakulla, Baker, Bradford, Washington, Hardee, Holmes, Gilchrist, Madison, Dixie, Union, Jefferson, Gulf, Hamilton, Calhoun, Franklin y Glades, FL (Civitek OCRS, probados con casos reales; Holmes y Dixie no publican los affidavits y dan solo el número de reporte); la lista completa y el orden de los próximos está en `docs/condados_florida.md`. El CAPTCHA lo resuelve una persona: hoy en una ventana de tu PC y, más adelante, dentro del dashboard con un iframe.

## Instalación (Windows)

Desde la carpeta del proyecto, en PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
```

Si PowerShell bloquea la activación del entorno, corre una vez `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

`BROWSER_CHANNEL=chrome` usa tu Google Chrome instalado, con un perfil nuevo y aislado (no toca tus pestañas ni tus sesiones). El paso `playwright install chromium` deja un Chromium de respaldo por si Chrome no se puede abrir.

## Probar

```powershell
python -m unittest discover tests   # matcher y parseo, sin navegador
python run_local.py --check         # valida test_cases.json sin abrir el navegador
python run_local.py                 # corre todos los casos de test_cases.json
python run_local.py ward            # corre solo los casos indicados por id
```

Los casos de prueba viven en `test_cases.json`. Para agregar uno, copia el bloque `plantilla`, dale un `id` único y quita `"skip": true`. Solo `first_name`, `last_name` y `county` son obligatorios; lo que no sepas va en `null`. Las fechas van como `AAAA-MM-DD`. Si pones `expected_case`, el resumen final marca `OK`, `SIN_PDF` (caso correcto sin documento) o `REVISAR`. Para pruebas negativas usa `"expected_status": "not_found"`.

Cada caso abre su propio Chrome y se detiene en el CAPTCHA. Marca "I'm not a robot" y no pulses nada más: el script hace clic en Search, abre el caso, elige el documento y guarda el PDF. Los resultados quedan en `output/<id>/` (PDF y `result.json`). Si algo falla, en `output/<id>/debug/` quedan una captura y el HTML de la página.

## Desde un artículo (Fase 2)

```powershell
python -m pip install -r requirements.txt   # agrega trafilatura, openai y pydantic
python run_article.py --dry-run             # solo extrae los datos de article_cases.json (sin portal ni CAPTCHA)
python run_article.py mues --dry-run        # un solo caso
python run_article.py mues                  # extrae, busca en el portal y descarga el PDF
python run_article.py https://... --dry-run # cualquier link
python run_article.py mues --dry-run --reuse # repite la validación con la extracción guardada, sin llamar a NVIDIA
```

1. **Texto:** `trafilatura` descarga la nota y separa el cuerpo de menús y anuncios, junto con la fecha de publicación. Si el sitio bloquea la descarga, o si solo entrega el esqueleto de la página con el aviso "JavaScript is required" (el cuerpo lo carga el navegador, como en hendrysheriff.org), la abre en un Chrome oculto en la misma máquina del worker (en una VPS, el Chromium de Playwright: `playwright install --with-deps chromium`) y, si eso no alcanza, en el navegador remoto.
2. **Datos:** el texto va a NVIDIA NIM (`NVIDIA_MODEL`, por defecto `google/gemma-4-31b-it`, con GLM y Nemotron de respaldo) con la librería `openai` como cliente. Se le pide un JSON con estructura fija (`guided_json`): arrestados, fecha del incidente (resolviendo "el viernes" con la fecha de publicación), agencia, condado y cargos.
3. **Ajustes:** si el modelo deja campos a medias, se completan con reglas sobre el propio texto: "Friday" pasa a fecha contando desde la publicación, la edad sale de "23-year-old", la ciudad del encabezado "ORLANDO, Fla." y el condado de la agencia (OPD, OCSO, etc.). En pantalla salen como `AJUSTE`.
4. **Validación:** se descarta cualquier nombre que no aparezca en el artículo o que sea una descripción ("A Lynn Haven man") y cualquier fecha posterior a la publicación. Excepción: si el año de la publicación que da la página no aparece en el texto y el de la fecha del modelo sí (Facebook dio 2001-01-01 para un comunicado de 2026), la fecha de publicación es la que no se usa. Un día de la semana ("Monday night") se cuenta desde la publicación solo si la fecha no está escrita en el texto: "On Monday night, April 27, 2026" se queda en el 27/04 aunque la página diga que se publicó hoy (hendrysheriff.org). Si la publicación se leyó con el día y el mes invertidos ("09-07-2022" leído como 9 de julio en wcso.org) y la fecha correcta está escrita en el texto, se usa esa. Si no hay fecha, se busca en el mes previo a la publicación. Los cargos sirven para distinguir casos del mismo día.

Si NVIDIA está saturado (errores 5xx como 503 "Worker local total request limit reached" o 529 "Service temporarily overloaded", frecuentes en el plan gratuito), el programa espera y reintenta (10 s, 20 s, 40 s, 60 s...) hasta `NVIDIA_RETRY_MAX_S` segundos (300 por defecto). Con `NVIDIA_FALLBACK_MODEL` (uno o varios, separados por coma) prueba primero esos modelos antes de esperar; un modelo retirado por NVIDIA se salta con un aviso. Si el modelo principal no contesta en `NVIDIA_FIRST_TIMEOUT_S` segundos (75 por defecto) se pasa al respaldo; los respaldos tienen `NVIDIA_TIMEOUT_S` (180). Para comparar un modelo antes de usarlo: `python run_article.py mues --dry-run --model <modelo>`. Si un artículo falla, los demás siguen.

**Fuera de alcance:** si el artículo es de otro estado o de un condado de Florida sin soporte (Daytona Beach → Volusia, por ejemplo), el resultado es `NO_SOPORTADO` con el lugar detectado. Se decide después de la extracción y antes de abrir el portal, así que no gasta CAPTCHA. El condado se toma del modelo, de la agencia (`counties/florida.py`, `AGENCY_TO_COUNTY`) o de la ciudad (`CITY_TO_COUNTY`).

**Modo interactivo:** `python run_article.py -i` (o `-i --dry-run`) pide links por consola y procesa cada uno al pegarlo, como hará el worker con la tabla.

Revisa primero con `--dry-run`: en `output/<id>/` quedan `article.txt`, `extraction.json` y `query.json`, y en pantalla se ve la cita del artículo de la que salió cada dato. Si la nota nombra a varios arrestados, `--suspect 2` elige al segundo. Los casos viven en `article_cases.json` (`id`, `url`, `expected_case`).

## CAPTCHA

reCAPTCHA pone desafíos más difíciles (y bloquea el audio) a navegadores que no reconoce o que están automatizados. Para que el Chrome del script no parezca "nuevo" en cada corrida, usa un perfil propio y persistente en `.browser-profile/` (no es tu perfil de Chrome). Con el uso acumula cookies y los desafíos suelen aflojar. Consejos:

- Si el desafío se traba, usa el ícono de recargar **dentro** del CAPTCHA. Si recargas la página entera, el script vuelve a llenar el formulario solo.
- Espaciar las pruebas ayuda: muchas búsquedas seguidas desde la misma IP suben la dificultad.
- El tiempo para resolverlo se ajusta con `CAPTCHA_TIMEOUT_S` en `.env` (por defecto 600 s).
- No se oculta que el navegador está automatizado: el CAPTCHA se resuelve de forma legítima. La vía oficial para no tener CAPTCHAs es una cuenta registrada en el portal.

## Navegador remoto (prueba de la Fase 4)

Con `BROWSER_PROVIDER=browserbase` y `BROWSERBASE_API_KEY` en `.env`, el navegador corre en Browserbase en vez de en tu PC. Al llegar al CAPTCHA el programa crea `output/<id>/live_view.html`, una página con el navegador remoto dentro de un iframe (igual a como lo mostrará el dashboard), y la abre en tu navegador. Marcas "I'm not a robot" ahí y el programa sigue solo.

- `solveCaptchas` va siempre en `false`: el CAPTCHA lo resuelve una persona.
- Las descargas se piden dentro de la página remota (`fetch`), así salen del mismo navegador que la sesión. El botón Download del visor no se usa en remoto; el PDF se arma desde las páginas.
- La sesión se libera al terminar para no gastar tiempo del plan. El plan gratuito corta cada sesión a los 15 minutos.
- Para volver a tu Chrome: `BROWSER_PROVIDER=local`.

## Estructura

```
core/
  models.py     LookupQuery, CaseCandidate, CaseDetails, LookupResult
  adapter.py    interfaz que todo portal implementa: search, open_case,
                find_documents, download
  captcha.py    ManualLocalCaptcha (Fase 1) y LiveViewCaptcha (Fase 4)
  matcher.py    puntúa candidatos y elige un único caso
  charges.py    agrupa cargos por tipo (robo, drogas, DUI...) para el matcher
  lookup.py     orquestador: consulta -> PDF
  pipeline.py   proceso completo: link del artículo -> PDF (lo usa el worker)
  jobs.py       cola lookup_jobs y bucket de PDFs en Supabase
  browser.py    abre el navegador (local o remoto según BROWSER_PROVIDER)
  remote.py     sesión de Browserbase: crear, live view, liberar
  documents.py  convierte lo que entrega un visor (TIFF, páginas) en PDF
adapters/
  orange_myeclerk.py   portal de Orange
  civitek_ocrs.py      Civitek OCRS (Marion, Sumter, Hernando, Putnam, Levy, Nassau, Columbia, Jackson, Hendry, Okeechobee, Wakulla, Baker, Bradford, Washington, Hardee, Holmes, Gilchrist, Madison, Dixie, Union, Jefferson, Gulf, Hamilton, Calhoun, Franklin, Glades y, con solo agregar la ficha, otros condados)
counties/
  florida.py    configuración por condado (URL, códigos, documentos)
extractor/
  fetch.py      texto y fecha de publicación del artículo (trafilatura)
  llm.py        NVIDIA NIM -> datos estructurados
  schema.py     estructura que debe devolver el modelo
  __init__.py   validación y armado de LookupQuery
worker.py       procesa la tabla lookup_jobs de Supabase
enqueue.py      crea un pedido y muestra su avance (simula al dashboard)
sql/            tabla lookup_jobs, permisos, Realtime y bucket de PDFs
tests/          tests sin navegador
```

## Agregar un condado

- **Misma plataforma que uno existente:** agregar su `CountyConfig` en `counties/florida.py`. Para Civitek basta una línea: `civitek("sumter", "Sumter County", "60")` (el código de condado está en `docs/condados_florida.md`).
- **Portal distinto:** crear `adapters/<plataforma>.py` heredando de `ClerkAdapter`, registrarlo en `adapters/__init__.py` y luego agregar el condado.

## Cómo decide el matcher

Descarta los candidatos cuyo apellido o nombre no coincide (tolera apellidos compuestos y sufijos como Jr). Después suma puntos por fecha de ofensa y de arresto (graduadas: el mismo día vale más que 1-3 días de diferencia), agencia, segundo nombre, edad y tipo de cargos (si la consulta los trae), Si los cargos no traen fechas (Civitek no las muestra), cuenta en su lugar cuántos días después del incidente se abrió el caso. Si la nota da la fecha del arresto pero no la del incidente ("wanted man": la orden es de otro lugar y el caso local es la resistencia), la fecha de ofensa se compara con la del arresto, solo para sumar: a un día o menos suma; lejos no cuenta, porque en arrestos por orden la ofensa puede ser de meses antes. En ese mismo caso la búsqueda arranca 30 días antes del arresto (no 7): en los arrestos con orden el caso se abre al emitirla, antes de la captura. Todo eso se convierte en un % de confianza sobre los puntos posibles con la información disponible. Devuelve un caso si su confianza llega al 60% y le saca 15 puntos al segundo mejor.

Si los mejores candidatos empatan pero son **del mismo arresto** (comparten el número de reporte de la agencia, o tienen la misma fecha de arresto, las mismas fechas de ofensa y la misma agencia), elige uno: primero el que tiene el documento, luego el caso principal antes que los que la fiscalía unió a él ("Consolidated" en Civitek), luego CF antes que MM o CT. En portales que no muestran número de reporte, fecha de arresto ni agencia (Civitek), cuentan como el mismo arresto los casos del mismo acusado con fechas de ofensa a un día o menos: una racha de hechos (robo, tiroteo, huida) suele abrirse como varios casos. Si el docket anota el número de reporte de la agencia ("Agency Case Number: 19OFF003389" en Civitek; en Dixie, "Agency Citation 26OFF000169"), sale en `agency_case_number` y la web lo deja copiar, haya o no PDF. Si empatan casos de la misma persona y uno dice que la fiscalía lo unió a otro ("consolidated"), se elige el principal. Si la nota trae cargos reconocibles y los del caso no lo son, los puntos de cargos cuentan como posibles sin sumar: un caso de otra cosa (una multa) no gana solo por el nombre. Los demás salen en `related_cases`, junto con los casos del mismo acusado abiertos el mismo día que el elegido (Levy abrió dos casos por el arresto de McQuiston); esto solo se informa, no cambia qué caso se elige. Si empatan casos de arrestos distintos, responde `ambiguous` en vez de adivinar. Si nadie da caso en el rango de fechas y tampoco hay un "caso más cercano", se busca una vez más sin fechas (la nota puede salir meses después, no decir cuándo pasó o traer la fecha mal): se abren hasta 5 casos con el mismo nombre, abiertos como mucho 60 días después de la nota, y el mejor con nombre exacto y cargos parecidos sale en `closest_case` con `wide: true`. Nunca es `found`: es una sugerencia para revisar a mano. Si la nota no trae cargos reconocibles, no se hace. En `test_cases.json`, `expected_case` puede ser una lista cuando varios casos son igual de válidos. Los pesos están en `core/matcher.py`.

**Varias personas en el artículo.** Si la nota nombra a varios arrestados, el worker los busca uno tras otro en la misma sesión del navegador (primero los arrestados o acusados, luego los sospechosos) y se detiene en el primero que da con el caso; para pedir el footage cualquiera sirve, es el mismo hecho. Máximo por condado en `max_people` (3; Orange 2, porque cada búsqueda puede pedir otro reCAPTCHA). Si se eligió a una persona (`suspect_index`, o `--suspect` en `run_article.py`), solo se busca a esa. Cada persona pasa por el mismo puntaje mínimo: probar a más gente no relaja nada. La web dice a quiénes se buscó (`people_searched`).

**Caso más cercano.** Si ningún caso alcanza el mínimo, el que más se acercó (desde `CLOSEST_MIN` = 30 %, en `core/lookup.py`) se guarda en `closest_case` con los motivos en inglés (`checks`): la web lo muestra para revisarlo a mano en el portal, sin descargar nada.

## Cola de pedidos (Fase 3)

El dashboard no llama al backend: inserta una fila en la tabla `lookup_jobs` de Supabase y escucha sus cambios. El worker toma la fila, hace todo el proceso y va escribiendo el avance.

**Preparación (una vez):**

1. En Supabase (proyecto sandbox) > SQL Editor, pega y corre `sql/001_lookup_jobs.sql`. Crea la tabla, las funciones del worker, los permisos, Realtime y el bucket privado `incident-reports`. Después corre `sql/002_roles.sql` (roles) `sql/003_support_document.sql` (columnas del documento de apoyo) `sql/004_people_and_closest.sql` (personas buscadas y caso más cercano) y `sql/005_agency_case_number.sql` (número de reporte de la agencia que trae el docket).
2. En `.env`, pon `SUPABASE_SERVICE_ROLE_KEY` (Project Settings > API Keys). Esa clave solo vive en el `.env` del worker.

**Probar sin dashboard** (dos consolas):

```powershell
python worker.py                 # consola 1: queda escuchando la cola
python enqueue.py mues           # consola 2: crea un pedido y muestra su avance, como haría el dashboard
python enqueue.py https://...    # con cualquier link
python enqueue.py --list         # últimos pedidos
```

**Estados** (`status`; `status_detail` trae un texto listo para mostrar):
`pending` → `fetching` → `extracting` → `searching` → `awaiting_captcha` → `searching` → `downloading` → final.
Finales: `found` (con `case_number`, `confidence`, `pdf_path`), `no_document`, `not_found`, `ambiguous`, `unsupported`, `captcha_timeout`, `error`.

**CAPTCHA:** mientras `status = awaiting_captcha`, la fila trae `live_view_url` (con navegador remoto). El dashboard lo muestra en un iframe de 540x700 y lo quita cuando el estado cambia. Con `BROWSER_PROVIDER=local` el CAPTCHA aparece en el Chrome de la PC del worker y `live_view_url` queda vacío.

**Lo que hará el dashboard** (con el cliente de Supabase y un usuario con sesión):

```js
const { data: job } = await supabase.from('lookup_jobs').insert({ article_url }).select().single()
supabase.channel('job-' + job.id)
  .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'lookup_jobs', filter: `id=eq.${job.id}` },
      ({ new: row }) => render(row))      // row.status, row.status_detail, row.live_view_url, row.pdf_path...
  .subscribe()
// PDF: supabase.storage.from('incident-reports').createSignedUrl(row.pdf_path, 3600)
```

Cada usuario solo ve sus propios pedidos y sus propios PDF (RLS). Si el worker se cierra a mitad de un pedido, a los 20 minutos queda marcado como `error`.

## Fases

1. **Hecho:** `run_local.py` con casos de prueba en `test_cases.json` (validado contra Orange el 2026-09-29).
2. **Hecho:** `run_article.py`, del link del artículo al PDF (validado el 2026-09-30).
3. **En prueba:** `worker.py` + tabla `lookup_jobs` (`enqueue.py` simula al dashboard).
4. **Validado en prueba (2026-10-05):** navegador remoto con el CAPTCHA en un iframe. Falta: ventana flotante en el dashboard y despliegue del worker en un servidor.


## Roles

`sql/002_roles.sql` (se corre después del 001) crea la tabla `profiles`: cada
cuenta nueva recibe ahí el rol `user`. Para hacer admin a alguien: Supabase >
Table Editor > `profiles` > `role` = `admin`. Nadie puede cambiar su propio rol
desde la web. Por ahora el rol solo se muestra; `public.is_admin()` queda lista
para los permisos de administrador.

## Revisar los permisos de Supabase

    python security_check.py --email tu@correo.com

Intenta lo que podría hacer cualquiera con la clave pública de la web (leer
sin sesión, cambiar estados, llamar a las funciones del worker, ver PDF de
otros) y confirma que todo se rechaza. Necesita `SUPABASE_ANON_KEY` en el
`.env`. Si los permisos están bien, no crea ni cambia nada.

## Resultados que ve el usuario

| Estado | Cuándo |
| --- | --- |
| `found` | PDF listo |
| `no_document` | El caso existe, pero sin reporte ni declaración de arresto |
| `not_found` / `ambiguous` | Ningún caso coincide / varios empatan |
| `unreadable` | No se pudo leer la nota: 404, sitio inexistente, bloqueo, muro de pago |
| `no_suspect` | La nota no nombra a ningún arrestado (el portal se busca por nombre) |
| `unknown_location` | La nota no dice ciudad ni condado |
| `unsupported` | Otro estado o un condado sin soporte |
| `captcha_timeout` | Nadie marcó la casilla a tiempo |
| `error` | Falla técnica (IA, navegador, Supabase): vale la pena reintentar |
