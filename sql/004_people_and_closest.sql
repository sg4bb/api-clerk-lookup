-- Personas buscadas y caso más cercano.
--
-- Dónde correrlo: Supabase > SQL Editor, DESPUÉS de 001, 002 y 003.
-- Se puede correr más de una vez sin romper nada.
--
-- Cuando el artículo nombra a varios arrestados, el worker los busca uno tras
-- otro hasta dar con el caso. people_searched guarda a quiénes buscó, en orden:
--   [{"name": "Arthur Williams", "status": "not_found", "cases_checked": 5}, ...]
-- Si ningún caso alcanza el mínimo, closest_case guarda el que más se acercó
-- (desde 30 %), para que la persona lo revise a mano en el portal:
--   {"case_number": "...", "score": 37, "defendant": "Timothy Colon",
--    "case_type": "Felony", "filed_date": "2023-01-12",
--    "checks": [{"ok": true, "text": "Same name"}, ...]}
-- Las lee la web con el "grant select" de 001 (es por tabla) y la RLS por usuario.

alter table public.lookup_jobs
  add column if not exists people_searched jsonb,
  add column if not exists closest_case    jsonb;
