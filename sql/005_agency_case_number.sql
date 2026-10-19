-- Número de reporte de la agencia que el portal anota en el docket.
--
-- Dónde correrlo: Supabase > SQL Editor, DESPUÉS de 001 a 004.
-- Se puede correr más de una vez sin romper nada.
--
-- Civitek (Columbia, por ejemplo) carga en el docket una línea "Agency Case
-- Number: 19OFF003389". Es el número que se usa para pedir el footage, así que
-- la web lo muestra y deja copiarlo, aunque el caso no tenga PDF.
-- Lo lee la web con el "grant select" de 001 (es por tabla) y la RLS por usuario.

alter table public.lookup_jobs
  add column if not exists agency_case_number text;
