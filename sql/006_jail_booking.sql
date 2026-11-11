-- El booking de la cárcel cuando el portal de la corte está bloqueado (Escambia).
--
-- Dónde correrlo: Supabase > SQL Editor, DESPUÉS de 001 a 005.
-- Se puede correr más de una vez sin romper nada.
--
-- Cuando Cloudflare bloquea el portal de la corte de Escambia, el worker toma el
-- arresto del registro de la cárcel del condado: fecha y número de booking,
-- cargos, número de caso y número de reporte de la agencia. Se guarda por partes
-- para que la web lo muestre ordenado en su propia tarjeta.
-- Lo lee la web con el "grant select" de 001 (es por tabla) y la RLS por usuario.

alter table public.lookup_jobs
  add column if not exists jail_booking jsonb;
