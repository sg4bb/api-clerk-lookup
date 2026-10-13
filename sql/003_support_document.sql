-- Documento de apoyo: la orden de captura ejecutada (warrant / capias served).
--
-- Dónde correrlo: Supabase > SQL Editor, DESPUÉS de 001 y 002.
-- Se puede correr más de una vez sin romper nada.
--
-- Cuando a alguien lo arrestan con orden de captura, además del arrest
-- affidavit (documento principal) se descarga la orden ejecutada: trae el
-- número de reporte de la agencia que investigó el hecho y, a veces, la
-- narración. Se guarda en la misma carpeta privada del pedido, así que los
-- permisos de Storage de 001 ya la cubren. La lectura de estas columnas ya la
-- da el "grant select" de 001 (es por tabla) y la RLS por usuario.

alter table public.lookup_jobs
  add column if not exists support_document_name text,   -- p. ej. "BENCH WARRANT SERVED AND NOTICE..."
  add column if not exists support_pdf_path      text;   -- ruta dentro del bucket incident-reports
