-- Fase 3: cola de pedidos "link de artículo -> incident report".
--
-- Dónde correrlo: Supabase > SQL Editor del proyecto SANDBOX
-- (clerk-lookup-sandbox). Se puede correr más de una vez sin romper nada.
-- Cuando todo esté probado, el mismo script va en el proyecto del dashboard.
--
-- Si alguna vez corriste el borrador anterior de este archivo, borra primero
-- la tabla vieja:   drop table if exists public.lookup_jobs cascade;
--
-- Cómo se usa:
--   * El dashboard INSERTA una fila con article_url (status queda 'pending').
--   * El worker la toma con claim_lookup_job(), va cambiando status y
--     status_detail, y al final deja case_number, pdf_path, etc.
--   * El dashboard escucha los cambios de SU fila por Realtime.

-- 1. Tabla -----------------------------------------------------------------
create table if not exists public.lookup_jobs (
  id                 uuid primary key default gen_random_uuid(),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  requested_by       uuid default auth.uid(),          -- usuario del dashboard

  -- Lo que pide el usuario
  article_url        text not null check (article_url ~* '^https?://'),
  article_text       text,                              -- opcional: texto pegado si el sitio bloquea la lectura
  suspect_index      int  not null default 1 check (suspect_index between 1 and 10),

  -- Avance
  status             text not null default 'pending' check (status in (
                       'pending',            -- en cola
                       'fetching',           -- leyendo el artículo
                       'extracting',         -- la IA extrae los datos
                       'searching',          -- buscando en el portal del condado
                       'awaiting_captcha',   -- esperando que el usuario resuelva el CAPTCHA
                       'downloading',        -- descargando el documento
                       -- finales:
                       'found',              -- PDF listo
                       'no_document',        -- caso encontrado, sin documento descargable
                       'not_found',          -- ningún caso coincide
                       'ambiguous',          -- varios casos empatan
                       'unsupported',        -- otro estado o condado sin soporte
                       'captcha_timeout',    -- nadie resolvió el CAPTCHA a tiempo
                       'error',
                       'cancelled')),
  status_detail      text,                              -- mensaje legible para mostrar al usuario
  live_view_url      text,                              -- iframe del CAPTCHA (solo mientras status = awaiting_captcha)
  captcha_expires_at timestamptz,

  -- Control del worker
  attempts           int not null default 0,
  worker_id          text,
  claimed_at         timestamptz,
  heartbeat_at       timestamptz,
  finished_at        timestamptz,

  -- Resultado
  article_title      text,
  article_published  date,
  extraction         jsonb,                             -- datos que extrajo la IA
  query              jsonb,                             -- lo que se buscó en el portal
  result             jsonb,                             -- detalle completo (candidatos, puntajes, docket)
  case_number        text,
  document_name      text,
  confidence         int,
  related_cases      text[] not null default '{}',
  pdf_path           text                               -- ruta dentro del bucket incident-reports
);

create index if not exists lookup_jobs_pending_idx on public.lookup_jobs (created_at) where status = 'pending';
create index if not exists lookup_jobs_requested_by_idx on public.lookup_jobs (requested_by, created_at desc);

-- 2. updated_at automático ---------------------------------------------------
create or replace function public.touch_lookup_jobs_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

drop trigger if exists lookup_jobs_updated_at on public.lookup_jobs;
create trigger lookup_jobs_updated_at
  before update on public.lookup_jobs
  for each row execute function public.touch_lookup_jobs_updated_at();

-- 3. El worker toma un pedido (uno solo, sin que dos workers tomen el mismo) --
create or replace function public.claim_lookup_job(p_worker_id text)
returns setof public.lookup_jobs
language plpgsql security definer set search_path = public as $$
declare
  v_id uuid;
begin
  select id into v_id
    from lookup_jobs
   where status = 'pending'
   order by created_at
   limit 1
   for update skip locked;
  if v_id is null then
    return;
  end if;
  return query
    update lookup_jobs
       set status = 'fetching', status_detail = 'Leyendo el artículo…',
           worker_id = p_worker_id, claimed_at = now(), heartbeat_at = now(),
           attempts = attempts + 1
     where id = v_id
    returning *;
end $$;

-- 4. Pedidos que quedaron a medias (el worker se cerró o se colgó) -----------
create or replace function public.release_stale_lookup_jobs(p_minutes int default 20)
returns int
language plpgsql security definer set search_path = public as $$
declare
  v_count int;
begin
  update lookup_jobs
     set status = 'error',
         status_detail = 'El proceso se interrumpió. Vuelve a intentarlo.',
         live_view_url = null, captcha_expires_at = null, finished_at = now()
   where status in ('fetching', 'extracting', 'searching', 'awaiting_captcha', 'downloading')
     and coalesce(heartbeat_at, claimed_at, created_at) < now() - make_interval(mins => p_minutes);
  get diagnostics v_count = row_count;
  return v_count;
end $$;

revoke all on function public.claim_lookup_job(text) from public, anon, authenticated;
revoke all on function public.release_stale_lookup_jobs(int) from public, anon, authenticated;
grant execute on function public.claim_lookup_job(text) to service_role;
grant execute on function public.release_stale_lookup_jobs(int) to service_role;

-- 5. Permisos ------------------------------------------------------------------
-- El worker usa la clave service_role (se salta RLS). Los usuarios del
-- dashboard (rol authenticated) solo pueden crear pedidos y ver los suyos.
alter table public.lookup_jobs enable row level security;

grant all on public.lookup_jobs to service_role;
revoke all on public.lookup_jobs from anon, authenticated;
grant select on public.lookup_jobs to authenticated;
grant insert (article_url, article_text, suspect_index) on public.lookup_jobs to authenticated;

drop policy if exists "lookup_jobs: ver los propios" on public.lookup_jobs;
create policy "lookup_jobs: ver los propios" on public.lookup_jobs
  for select to authenticated
  using (requested_by = auth.uid());

drop policy if exists "lookup_jobs: crear" on public.lookup_jobs;
create policy "lookup_jobs: crear" on public.lookup_jobs
  for insert to authenticated
  with check (requested_by = auth.uid() and status = 'pending');

-- 6. Realtime (para que el dashboard vea el avance en vivo) --------------------
do $$
begin
  alter publication supabase_realtime add table public.lookup_jobs;
exception
  when duplicate_object then null;   -- ya estaba agregada
end $$;

-- 7. Storage: bucket privado para los PDF -------------------------------------
-- Los archivos se guardan como  <id del job>/<nombre>.pdf
insert into storage.buckets (id, name, public)
values ('incident-reports', 'incident-reports', false)
on conflict (id) do nothing;

drop policy if exists "incident-reports: leer los propios" on storage.objects;
create policy "incident-reports: leer los propios" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'incident-reports'
    and exists (
      select 1 from public.lookup_jobs j
       where j.id::text = (storage.foldername(name))[1]
         and j.requested_by = auth.uid()
    )
  );
