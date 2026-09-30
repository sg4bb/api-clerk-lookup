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

## CAPTCHA

reCAPTCHA pone desafíos más difíciles (y bloquea el audio) a navegadores que no reconoce o que están automatizados. Para que el Chrome del script no parezca "nuevo" en cada corrida, usa un perfil propio y persistente en `.browser-profile/` (no es tu perfil de Chrome). Con el uso acumula cookies y los desafíos suelen aflojar. Consejos:

- Si el desafío se traba, usa el ícono de recargar **dentro** del CAPTCHA. Si recargas la página entera, el script vuelve a llenar el formulario solo.
- Espaciar las pruebas ayuda: muchas búsquedas seguidas desde la misma IP suben la dificultad.
- El tiempo para resolverlo se ajusta con `CAPTCHA_TIMEOUT_S` en `.env` (por defecto 600 s).
- No se oculta que el navegador está automatizado: el CAPTCHA se resuelve de forma legítima. La vía oficial para no tener CAPTCHAs es una cuenta registrada en el portal.

## Estructura

```
core/
  models.py     LookupQuery, CaseCandidate, CaseDetails, LookupResult
  adapter.py    interfaz que todo portal implementa: search, open_case,
                find_documents, download
  captcha.py    ManualLocalCaptcha (Fase 1) y LiveViewCaptcha (Fase 4)
  matcher.py    puntúa candidatos y elige un único caso
  lookup.py     orquestador: consulta -> PDF
  browser.py    abre el navegador (local ahora, remoto después)
  documents.py  convierte lo que entrega un visor (TIFF, páginas) en PDF
adapters/
  orange_myeclerk.py   portal de Orange
counties/
  florida.py    configuración por condado (URL, códigos, documentos)
extractor/      Fase 2: artículo -> LookupQuery
worker.py       Fase 3: procesa la tabla lookup_jobs de Supabase
sql/            Fase 3: tabla lookup_jobs (borrador, aún no se corre)
tests/          tests sin navegador
```

## Agregar un condado

- **Misma plataforma que uno existente:** agregar su `CountyConfig` en `counties/florida.py`.
- **Portal distinto:** crear `adapters/<plataforma>.py` heredando de `ClerkAdapter`, registrarlo en `adapters/__init__.py` y luego agregar el condado.

## Cómo decide el matcher

Descarta los candidatos cuyo apellido o nombre no coincide (tolera apellidos compuestos y sufijos como Jr). Después suma puntos por fecha de ofensa y de arresto (graduadas: el mismo día vale más que 1-3 días de diferencia), agencia, segundo nombre y edad, y los convierte en un % de confianza sobre los puntos posibles con la información disponible. Devuelve un caso si su confianza llega al 60% y le saca 15 puntos al segundo mejor.

Si los mejores candidatos empatan pero son **del mismo arresto** (comparten el número de reporte de la agencia, o tienen la misma fecha de arresto, las mismas fechas de ofensa y la misma agencia), elige uno: primero el que tiene el documento, luego CF antes que MM o CT. Los demás salen en `related_cases`. Si empatan casos de arrestos distintos, responde `ambiguous` en vez de adivinar. En `test_cases.json`, `expected_case` puede ser una lista cuando varios casos son igual de válidos. Los pesos están en `core/matcher.py`.

## Fases

1. **Hecho:** `run_local.py` con casos de prueba en `test_cases.json` (validado contra Orange el 2026-09-29).
2. Extractor de artículos (texto + LLM -> `LookupQuery`).
3. Worker + tabla `lookup_jobs` en el Supabase sandbox.
4. Navegador remoto con live view, deploy e integración en el dashboard.
