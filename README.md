# clerk-lookup

Servicio que recibe los datos de un caso y devuelve **un único** Incident Report / Arrest Affidavit desde el portal del Clerk of Courts del condado. Empieza por Orange County, FL (myeClerk). El CAPTCHA lo resuelve una persona: hoy en una ventana de tu PC y, más adelante, dentro del dashboard con un iframe.

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

1. **Texto:** `trafilatura` descarga la nota y separa el cuerpo de menús y anuncios, junto con la fecha de publicación. Si el sitio bloquea la descarga, la abre en un Chrome oculto.
2. **Datos:** el texto va a NVIDIA NIM (`NVIDIA_MODEL`, por defecto `google/gemma-4-31b-it`, con GLM y Nemotron de respaldo) con la librería `openai` como cliente. Se le pide un JSON con estructura fija (`guided_json`): arrestados, fecha del incidente (resolviendo "el viernes" con la fecha de publicación), agencia, condado y cargos.
3. **Ajustes:** si el modelo deja campos a medias, se completan con reglas sobre el propio texto: "Friday" pasa a fecha contando desde la publicación, la edad sale de "23-year-old", la ciudad del encabezado "ORLANDO, Fla." y el condado de la agencia (OPD, OCSO, etc.). En pantalla salen como `AJUSTE`.
4. **Validación:** se descarta cualquier nombre que no aparezca en el artículo y cualquier fecha posterior a la publicación. Si no hay fecha, se busca en el mes previo a la publicación. Los cargos sirven para distinguir casos del mismo día.

Si NVIDIA está saturado (error 503 "Worker local total request limit reached", frecuente en el plan gratuito), el programa espera y reintenta (10 s, 20 s, 40 s, 60 s...) hasta `NVIDIA_RETRY_MAX_S` segundos (300 por defecto). Con `NVIDIA_FALLBACK_MODEL` (uno o varios, separados por coma) prueba primero esos modelos antes de esperar; un modelo retirado por NVIDIA se salta con un aviso. Para comparar un modelo antes de usarlo: `python run_article.py mues --dry-run --model <modelo>`. Si un artículo falla, los demás siguen.

**Fuera de alcance:** si el artículo es de otro estado o de un condado de Florida sin soporte (Ocala → Marion, por ejemplo), el resultado es `NO_SOPORTADO` con el lugar detectado. Se decide después de la extracción y antes de abrir el portal, así que no gasta CAPTCHA. El condado se toma del modelo, de la agencia (`counties/florida.py`, `AGENCY_TO_COUNTY`) o de la ciudad (`CITY_TO_COUNTY`).

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

- **Misma plataforma que uno existente:** agregar su `CountyConfig` en `counties/florida.py`.
- **Portal distinto:** crear `adapters/<plataforma>.py` heredando de `ClerkAdapter`, registrarlo en `adapters/__init__.py` y luego agregar el condado.

## Cómo decide el matcher

Descarta los candidatos cuyo apellido o nombre no coincide (tolera apellidos compuestos y sufijos como Jr). Después suma puntos por fecha de ofensa y de arresto (graduadas: el mismo día vale más que 1-3 días de diferencia), agencia, segundo nombre, edad y tipo de cargos (si la consulta los trae), y los convierte en un % de confianza sobre los puntos posibles con la información disponible. Devuelve un caso si su confianza llega al 60% y le saca 15 puntos al segundo mejor.

Si los mejores candidatos empatan pero son **del mismo arresto** (comparten el número de reporte de la agencia, o tienen la misma fecha de arresto, las mismas fechas de ofensa y la misma agencia), elige uno: primero el que tiene el documento, luego CF antes que MM o CT. Los demás salen en `related_cases`. Si empatan casos de arrestos distintos, responde `ambiguous` en vez de adivinar. En `test_cases.json`, `expected_case` puede ser una lista cuando varios casos son igual de válidos. Los pesos están en `core/matcher.py`.

## Cola de pedidos (Fase 3)

El dashboard no llama al backend: inserta una fila en la tabla `lookup_jobs` de Supabase y escucha sus cambios. El worker toma la fila, hace todo el proceso y va escribiendo el avance.

**Preparación (una vez):**

1. En Supabase (proyecto sandbox) > SQL Editor, pega y corre `sql/001_lookup_jobs.sql`. Crea la tabla, las funciones del worker, los permisos, Realtime y el bucket privado `incident-reports`.
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
