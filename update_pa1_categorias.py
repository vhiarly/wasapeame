"""
Corre una sola vez:
  DATABASE_URL=postgresql://... python3 update_pa1_categorias.py
"""
import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur  = conn.cursor()

cur.execute("""
    ALTER TABLE catalogo
    ADD COLUMN IF NOT EXISTS categoria VARCHAR(100)
""")

PREFIJOS = {
    "temp_":   "Temporada",
    "pastel_": "Pasteles Enteros",
    "postre_": "Postres",
    "esp_":    "Especiales",
    "boc_":    "Bocadillos",
    "combo_":  "Combos",
    "lb_":     "Lunch Box",
}

cur.execute("SELECT clave FROM catalogo WHERE codigo = 'PA1'")
claves = [r[0] for r in cur.fetchall()]

updated = 0
for clave in claves:
    for prefijo, categoria in PREFIJOS.items():
        if clave.startswith(prefijo):
            cur.execute(
                "UPDATE catalogo SET categoria = %s WHERE codigo = 'PA1' AND clave = %s",
                (categoria, clave)
            )
            updated += 1
            break

conn.commit()
cur.close()
conn.close()
print(f"PA1: {updated} productos actualizados con categorías.")
