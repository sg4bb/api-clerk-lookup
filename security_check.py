"""Revisa que los permisos de Supabase protejan los datos como se espera.

    python security_check.py                 # pide correo y contraseña
    python security_check.py --email tu@correo.com

Hace lo mismo que podría intentar cualquiera que abra la web, que lleva la
clave pública (anon) dentro: leer sin iniciar sesión, crear pedidos sin
sesión, cambiar el estado de un pedido, llamar a las funciones del worker,
ver búsquedas o PDF de otros usuarios, darse el rol de admin. Todo eso tiene
que ser RECHAZADO.

No crea, cambia ni borra nada si los permisos están bien. (Si alguno está
mal, la prueba que lo demuestra sí podría crear o cambiar una fila: ese es
justamente el problema que reporta.)

Necesita en el .env: SUPABASE_URL, SUPABASE_ANON_KEY (la clave pública de la
web) y, para comparar totales, SUPABASE_SERVICE_ROLE_KEY (solo se usa aquí,
para contar filas).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import uuid

import httpx
from dotenv import load_dotenv

TABLE = "lookup_jobs"
BUCKET = "incident-reports"
results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))
    print(f"  {'OK   ' if ok else 'FALLA'}  {name}" + (f"  ({detail})" if detail else ""))


def rejected(r: httpx.Response) -> bool:
    return r.status_code >= 400


def main() -> int:
    parser = argparse.ArgumentParser(description="Revisión de permisos de Supabase")
    parser.add_argument("--email")
    args = parser.parse_args()
    load_dotenv()

    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    anon = os.getenv("SUPABASE_ANON_KEY", "").strip()
    service = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not anon:
        print("Falta SUPABASE_URL o SUPABASE_ANON_KEY en el .env (la anon es la misma de la web).")
        return 2
    email = args.email or input("Correo de tu usuario de la web: ").strip()
    password = getpass.getpass("Contraseña (no se muestra): ")

    http = httpx.Client(base_url=url, timeout=30)
    as_anon = {"apikey": anon, "Authorization": f"Bearer {anon}"}
    some_id = str(uuid.uuid4())

    print("\n1. Registro de cuentas")
    settings = http.get("/auth/v1/settings", headers={"apikey": anon}).json()
    check(bool(settings.get("disable_signup")), "Nadie puede crearse una cuenta por su cuenta",
          "" if settings.get("disable_signup") else
          "ACTÍVALO: Authentication > Sign In / Providers > desactiva 'Allow new users to sign up'")

    print("\n2. Sin iniciar sesión (solo con la clave pública)")
    r = http.get(f"/rest/v1/{TABLE}", params={"select": "id", "limit": "5"}, headers=as_anon)
    check(rejected(r) or r.json() == [], "No puede leer búsquedas", f"HTTP {r.status_code}")
    r = http.post(f"/rest/v1/{TABLE}", json={"article_url": "https://example.com/security-check"},
                  headers={**as_anon, "Prefer": "return=minimal"})
    check(rejected(r), "No puede crear búsquedas", f"HTTP {r.status_code}")
    r = http.post(f"/storage/v1/object/list/{BUCKET}", json={"prefix": "", "limit": 100}, headers=as_anon)
    check(rejected(r) or r.json() == [], "No puede listar PDF", f"HTTP {r.status_code}")
    r = http.get("/rest/v1/profiles", params={"select": "id,role"}, headers=as_anon)
    check(rejected(r) or r.json() == [], "No puede ver usuarios ni roles", f"HTTP {r.status_code}")
    r = http.get(f"/rest/v1/{TABLE}", params={"select": "id"},
                 headers={"apikey": anon, "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.e30.firma-falsa"})
    check(rejected(r), "Un token falsificado es rechazado", f"HTTP {r.status_code}")

    print("\n3. Inicio de sesión")
    r = http.post("/auth/v1/token", params={"grant_type": "password"}, headers={"apikey": anon},
                  json={"email": email, "password": password + "-incorrecta"})
    check(rejected(r), "Una contraseña incorrecta no entra", f"HTTP {r.status_code}")
    r = http.post("/auth/v1/token", params={"grant_type": "password"}, headers={"apikey": anon},
                  json={"email": email, "password": password})
    if rejected(r):
        print(f"  No pude iniciar sesión con ese usuario (HTTP {r.status_code}): {r.text[:200]}")
        return 2
    session = r.json()
    uid = session["user"]["id"]
    as_user = {"apikey": anon, "Authorization": f"Bearer {session['access_token']}"}
    print(f"  Sesión iniciada. El token dura {session.get('expires_in', '?')} s y la web lo renueva sola.")

    print("\n4. Rol")
    r = http.get("/rest/v1/profiles", params={"select": "id,role"}, headers=as_user)
    profiles = r.json() if not rejected(r) else []
    role = profiles[0]["role"] if len(profiles) == 1 and profiles[0]["id"] == uid else None
    check(role in ("user", "admin"), "Tiene perfil con rol y solo ve el suyo",
          f"rol: {role}" if role else f"HTTP {r.status_code}, filas: {len(profiles)}. ¿Corriste sql/002_roles.sql?")
    r = http.patch("/rest/v1/profiles", params={"id": f"eq.{uid}"}, json={"role": "admin"}, headers=as_user)
    check(rejected(r), "No puede cambiarse el rol a sí mismo", f"HTTP {r.status_code}")
    r = http.post("/rest/v1/profiles", json={"id": some_id, "role": "admin"},
                  headers={**as_user, "Prefer": "return=minimal"})
    check(rejected(r), "No puede crear perfiles", f"HTTP {r.status_code}")

    print("\n5. Con sesión iniciada")
    r = http.get(f"/rest/v1/{TABLE}", params={"select": "id,requested_by", "limit": "1000"}, headers=as_user)
    mine = r.json() if not rejected(r) else []
    check(not rejected(r) and all(row["requested_by"] == uid for row in mine),
          "Solo ve sus propias búsquedas", f"ve {len(mine)}")
    if service:
        total = httpx.get(f"{url}/rest/v1/{TABLE}", params={"select": "id"},
                          headers={"apikey": service, "Authorization": f"Bearer {service}",
                                   "Prefer": "count=exact", "Range": "0-0"}).headers.get("content-range", "")
        print(f"         En toda la tabla hay {total.split('/')[-1]} búsqueda(s); este usuario ve {len(mine)}.")

    attempts = [
        ("No puede crear una búsqueda ya marcada como 'found'",
         lambda: http.post(f"/rest/v1/{TABLE}", json={"article_url": "https://example.com/x", "status": "found"},
                           headers={**as_user, "Prefer": "return=minimal"})),
        ("No puede crear una búsqueda a nombre de otro usuario",
         lambda: http.post(f"/rest/v1/{TABLE}", json={"article_url": "https://example.com/x", "requested_by": some_id},
                           headers={**as_user, "Prefer": "return=minimal"})),
        ("No puede cambiar una búsqueda (estado, PDF, resultado)",
         lambda: http.patch(f"/rest/v1/{TABLE}", params={"id": f"eq.{some_id}"}, json={"status": "found"},
                            headers=as_user)),
        ("No puede borrar búsquedas",
         lambda: http.delete(f"/rest/v1/{TABLE}", params={"id": f"eq.{some_id}"}, headers=as_user)),
        ("No puede tomar pedidos como si fuera el worker",
         lambda: http.post("/rest/v1/rpc/claim_lookup_job", json={"p_worker_id": "security-check"}, headers=as_user)),
        ("No puede marcar pedidos como colgados",
         lambda: http.post("/rest/v1/rpc/release_stale_lookup_jobs", json={"p_minutes": 0}, headers=as_user)),
    ]
    for name, call in attempts:
        r = call()
        check(rejected(r), name, f"HTTP {r.status_code}")

    r = http.post(f"/storage/v1/object/list/{BUCKET}", json={"prefix": "", "limit": 1000}, headers=as_user)
    folders = {item["name"] for item in (r.json() if not rejected(r) else [])}
    own_ids = {row["id"] for row in mine}
    check(folders <= own_ids, "Solo ve los PDF de sus propias búsquedas",
          f"{len(folders)} carpeta(s), {len(folders - own_ids)} ajena(s)")

    failed = [name for ok, name, _ in results if not ok]
    print("\n" + ("Todo en orden." if not failed else f"{len(failed)} punto(s) por corregir: " + "; ".join(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
