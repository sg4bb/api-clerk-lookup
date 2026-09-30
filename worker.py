"""Fase 3 (pendiente): worker que procesa la tabla lookup_jobs.

Loop previsto:
  1. Tomar un job con status='queued' (marcándolo con worker_id).
  2. Construir el LookupQuery (desde query o, en Fase 2, desde article_url).
  3. Llamar a core.lookup.run_lookup con on_status= una función que
     actualiza el status del job en Supabase.
  4. Subir el PDF al bucket privado de Storage y guardar result/pdf_path.

Mientras tanto, la prueba se hace con run_local.py.
"""

if __name__ == "__main__":
    raise SystemExit("El worker se implementa en la Fase 3; usa run_local.py")
