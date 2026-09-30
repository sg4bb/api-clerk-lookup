-- BORRADOR para la Fase 3. No hace falta correrlo todavía.
-- Se corre en el proyecto sandbox (clerk-lookup-sandbox) y, si todo
-- funciona, el mismo script en el proyecto del dashboard.

create table if not exists public.lookup_jobs (
  id                  uuid primary key default gen_random_uuid(),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  status              text not null default 'queued'
                      check (status in ('queued', 'extracting', 'searching',
                                        'awaiting_captcha', 'downloading', 'done',
                                        'not_found', 'ambiguous', 'no_document',
                                        'captcha_timeout', 'error')),
  article_url         text,
  query               jsonb not null default '{}'::jsonb,   -- LookupQuery
  county              text,
  state               text not null default 'FL',
  live_view_url       text,          -- sensible: solo lo ve quien pidió la consulta
  captcha_expires_at  timestamptz,
  result              jsonb,         -- LookupResult.to_dict()
  pdf_path            text,          -- ruta en el bucket privado de Storage
  error               text,
  worker_id           text,
  requested_by        uuid           -- auth.users(id) cuando exista el login
);

create index if not exists lookup_jobs_status_idx on public.lookup_jobs (status, created_at);

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

-- El proyecto se creó sin "exponer tablas automáticamente" y con RLS
-- automático, así que los permisos se dan a mano:
alter table public.lookup_jobs enable row level security;
grant all on public.lookup_jobs to service_role;   -- el worker (se salta RLS)
-- Las políticas para el frontend se definen al integrar con el login.

-- Para que el dashboard escuche cambios en tiempo real:
alter publication supabase_realtime add table public.lookup_jobs;
