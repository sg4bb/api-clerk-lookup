-- Roles de usuario: 'user' y 'admin'.
--
-- Dónde correrlo: Supabase > SQL Editor, DESPUÉS de 001_lookup_jobs.sql.
-- Se puede correr más de una vez sin romper nada.
--
-- Cómo funciona:
--   * Cada cuenta tiene una fila en public.profiles con su rol.
--   * La fila se crea sola al crear el usuario (en el panel o, más adelante,
--     desde la pantalla de administrador), siempre con rol 'user'.
--   * Para hacer admin a alguien: Supabase > Table Editor > profiles >
--     cambia "role" a admin. Desde la web nadie puede cambiar su propio rol.
--   * Por ahora el rol no cambia lo que cada uno ve: todos ven solo sus
--     búsquedas. public.is_admin() queda lista para cuando se agreguen
--     permisos de administrador.

-- 1. Tabla --------------------------------------------------------------------
create table if not exists public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  email       text,
  role        text not null default 'user' check (role in ('user', 'admin')),
  created_at  timestamptz not null default now()
);

-- 2. Fila automática para cada usuario nuevo ------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Usuarios que ya existían antes de este script (quedan como 'user').
insert into public.profiles (id, email)
select id, email from auth.users
on conflict (id) do nothing;

-- 3. ¿El usuario actual es admin? (para usar en permisos futuros) --------------
create or replace function public.is_admin()
returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role = 'admin');
$$;

revoke all on function public.handle_new_user() from public, anon, authenticated;
revoke all on function public.is_admin() from public, anon;
grant execute on function public.is_admin() to authenticated, service_role;

-- 4. Permisos -----------------------------------------------------------------
-- Cada usuario puede LEER su propia fila (la web la usa para saber su rol).
-- Nadie puede crear, cambiar ni borrar filas desde la web: el rol se cambia
-- en el panel de Supabase (o, más adelante, desde el servidor).
alter table public.profiles enable row level security;

revoke all on public.profiles from anon, authenticated;
grant select on public.profiles to authenticated;
grant all on public.profiles to service_role;

drop policy if exists "profiles: ver el propio" on public.profiles;
create policy "profiles: ver el propio" on public.profiles
  for select to authenticated
  using (id = auth.uid());
