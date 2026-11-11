-- Por qué un caso encontrado no trae documento, para el sello de su tarjeta en la web.
--
-- Dónde correrlo: Supabase > SQL Editor, DESPUÉS de 001 a 006.
-- Se puede correr más de una vez sin romper nada.
--
-- Cuando el caso aparece pero el PDF no se puede bajar, el worker guarda el motivo
-- por partes: {"reason", "document", "filed", "portal_url"}. reason es uno de
-- on_request (se pide al Clerk), unpublished (listado, aún sin publicar),
-- confidential, not_in_docket (no hay affidavit ni reporte), docket_unavailable
-- (el portal no muestra el docket) y withheld (el portal no entrega documentos ahora).
-- El texto para el usuario sigue en status_detail.
-- Lo lee la web con el "grant select" de 001 (es por tabla) y la RLS por usuario.

alter table public.lookup_jobs
  add column if not exists missing_document jsonb;
