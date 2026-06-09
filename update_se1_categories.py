"""
Corre una sola vez:
  DATABASE_URL=postgresql://... python update_se1_categories.py
"""
import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur  = conn.cursor()

# 1. Agregar columna categoria si no existe
cur.execute("""
    ALTER TABLE servicios
    ADD COLUMN IF NOT EXISTS categoria VARCHAR(100)
""")

# 2. Desactivar servicios genéricos reemplazados por los específicos
cur.execute("""
    UPDATE servicios SET activo = FALSE
    WHERE codigo = 'SE1' AND clave IN ('asesoria_presencial', 'asesoria_online')
""")

# 3. Asignar categorías y descripciones a los 14 servicios de SE1
updates = [
    # (clave, categoria, descripcion)
    ("tramites_migratorios",        "Trámites migratorios", "Residencia temporal y permanente"),
    ("cambio_categoria_migratoria", "Trámites migratorios", "Cambio de estatus migratorio"),
    ("renovacion_residencia_rt9",   "Trámites migratorios", "Renovación residencia permanente"),
    ("renovacion_permiso_tt1",      "Trámites migratorios", "Renovación permiso temporal"),
    ("adhesion_pnv_menores",        "Trámites migratorios", "Protección venezolanos, menores"),
    ("visas_rd",                    "Visas",                "Visa turismo, residencia y trabajo"),
    ("visa_turismo_venezuela",      "Visas",                "Visa turismo para venezolanos"),
    ("homologacion_licencia",       "Visas",                "Licencia extranjera válida en RD"),
    ("constitucion_empresa",        "Empresa y comercio",   "Crear SRL, SA o empresa individual"),
    ("registro_empresa_extranjera", "Empresa y comercio",   "Empresa extranjera operando en RD"),
    ("renovacion_registro_mercantil","Empresa y comercio",  "Renovar matrícula mercantil"),
    ("registro_sanitario",          "Empresa y comercio",   "Permiso para productos/negocios de salud"),
    ("registro_marca",              "Marca y propiedad",    "Proteger tu marca ante ONAPI"),
    ("licencia_venezolana",         "Marca y propiedad",    "Renovar licencia de conducir venezolana"),
]

for clave, categoria, descripcion in updates:
    cur.execute("""
        UPDATE servicios
        SET categoria = %s, descripcion = %s
        WHERE codigo = 'SE1' AND clave = %s
    """, (categoria, descripcion, clave))
    print(f"  ✓ {clave}")

conn.commit()
cur.close()
conn.close()
print("\nSE1 actualizado correctamente.")
