#!/usr/bin/env python3
"""Script rápido para activar todos los negocios en la BD"""

import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

dsn = os.getenv("DATABASE_URL")
if not dsn:
    print("ERROR: DATABASE_URL no está definido")
    exit(1)

try:
    conn = psycopg2.connect(dsn, sslmode="require")
    cur = conn.cursor()

    cur.execute("UPDATE negocios SET activo = true")
    conn.commit()

    affected = cur.rowcount
    print(f"✓ {affected} negocio(s) activado(s)")

    # Verificar
    cur.execute("SELECT codigo, nombre, activo FROM negocios ORDER BY codigo")
    rows = cur.fetchall()
    print(f"\nNegocios activos:")
    for codigo, nombre, activo in rows:
        status = "✓" if activo else "✗"
        print(f"  {status} {codigo} — {nombre}")

    cur.close()
    conn.close()
except Exception as e:
    print(f"ERROR: {e}")
    exit(1)
