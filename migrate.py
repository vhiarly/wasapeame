"""
Script de migración única: crea las tablas en PostgreSQL y siembra los datos
de negocios.json. Seguro de correr más de una vez (INSERT ... ON CONFLICT DO NOTHING).

Uso:
    DATABASE_URL=postgresql://... python migrate.py
"""

import json
import os
from datetime import datetime, date
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import Json

load_dotenv()

SCHEMA = """
CREATE TABLE IF NOT EXISTS negocios (
    codigo                VARCHAR(10)  PRIMARY KEY,
    nombre                VARCHAR(200) NOT NULL,
    tipo                  VARCHAR(10)  NOT NULL,
    modo                  VARCHAR(20)  NOT NULL CHECK (modo IN ('pedidos','citas')),
    numero_negocio        VARCHAR(50)  NOT NULL UNIQUE,
    pin                   VARCHAR(50)  NOT NULL,
    activo                BOOLEAN      NOT NULL DEFAULT TRUE,
    requiere_comprobante  BOOLEAN      NOT NULL DEFAULT FALSE,
    instrucciones_pago    TEXT         NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS catalogo (
    id        SERIAL        PRIMARY KEY,
    codigo    VARCHAR(10)   NOT NULL REFERENCES negocios(codigo),
    clave     VARCHAR(50)   NOT NULL,
    nombre    VARCHAR(200)  NOT NULL,
    precio    NUMERIC(10,2) NOT NULL,
    unidad    VARCHAR(20)   NOT NULL,
    rebanado  BOOLEAN       NOT NULL DEFAULT FALSE,
    activo    BOOLEAN       NOT NULL DEFAULT TRUE,
    cantidad  INT           NOT NULL DEFAULT 1,
    UNIQUE (codigo, clave)
);

CREATE TABLE IF NOT EXISTS servicios (
    id                SERIAL        PRIMARY KEY,
    codigo            VARCHAR(10)   NOT NULL REFERENCES negocios(codigo),
    clave             VARCHAR(50)   NOT NULL,
    nombre            VARCHAR(200)  NOT NULL,
    duracion_minutos  INT           NOT NULL,
    precio            NUMERIC(10,2) NOT NULL,
    activo            BOOLEAN       NOT NULL DEFAULT TRUE,
    UNIQUE (codigo, clave)
);

CREATE TABLE IF NOT EXISTS horarios (
    codigo   VARCHAR(10) NOT NULL REFERENCES negocios(codigo),
    dia      VARCHAR(20) NOT NULL,
    trabaja  BOOLEAN     NOT NULL DEFAULT TRUE,
    inicio   VARCHAR(10),
    fin      VARCHAR(10),
    PRIMARY KEY (codigo, dia)
);

CREATE TABLE IF NOT EXISTS conversaciones_pedidos (
    numero_cliente           VARCHAR(50) PRIMARY KEY,
    codigo                   VARCHAR(10) NOT NULL REFERENCES negocios(codigo),
    estado                   VARCHAR(50) NOT NULL,
    items                    JSONB       NOT NULL DEFAULT '[]',
    direccion                TEXT        NOT NULL DEFAULT '',
    referencia               TEXT        NOT NULL DEFAULT '',
    item_pendiente_rebanado  JSONB,
    cola_rebanado            JSONB,
    rebanado_origen          VARCHAR(50),
    item_sin_stock           JSONB,
    timeout_en               TIMESTAMPTZ,
    actualizado_en           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pedidos (
    id              SERIAL        PRIMARY KEY,
    numero_cliente  VARCHAR(50)   NOT NULL,
    codigo          VARCHAR(10)   NOT NULL REFERENCES negocios(codigo),
    turno           INT           NOT NULL,
    items           JSONB         NOT NULL DEFAULT '[]',
    total           NUMERIC(10,2) NOT NULL,
    direccion       TEXT          NOT NULL DEFAULT '',
    referencia      TEXT          NOT NULL DEFAULT '',
    estado          VARCHAR(20)   NOT NULL DEFAULT 'pendiente',
    creado_en       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS pedidos_cliente_pendiente
    ON pedidos (numero_cliente)
    WHERE estado = 'pendiente';

CREATE TABLE IF NOT EXISTS contadores_turnos (
    codigo    VARCHAR(10) PRIMARY KEY REFERENCES negocios(codigo),
    contador  INT         NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS conversaciones_citas (
    numero_cliente  VARCHAR(50) PRIMARY KEY,
    codigo          VARCHAR(10) NOT NULL REFERENCES negocios(codigo),
    estado          VARCHAR(50) NOT NULL,
    servicio_clave  VARCHAR(50),
    dia             VARCHAR(20),
    nombre_dia      VARCHAR(30),
    hora            VARCHAR(10),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sesiones_admin (
    numero                       VARCHAR(50) PRIMARY KEY,
    codigo                       VARCHAR(10) NOT NULL REFERENCES negocios(codigo),
    expira                       TIMESTAMPTZ NOT NULL,
    preguntando_numero_principal BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS citas (
    id                   SERIAL        PRIMARY KEY,
    codigo               VARCHAR(10)   NOT NULL REFERENCES negocios(codigo),
    numero_cliente       VARCHAR(50)   NOT NULL,
    servicio             VARCHAR(50)   NOT NULL,
    nombre_servicio      VARCHAR(200)  NOT NULL,
    fecha                DATE          NOT NULL,
    hora                 VARCHAR(10)   NOT NULL,
    duracion_minutos     INT           NOT NULL,
    estado               VARCHAR(20)   NOT NULL DEFAULT 'confirmada',
    agendado_en          TIMESTAMPTZ,
    recordatorio_enviado BOOLEAN       NOT NULL DEFAULT FALSE,
    creado_en            TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bloqueos (
    id      SERIAL      PRIMARY KEY,
    codigo  VARCHAR(10) NOT NULL REFERENCES negocios(codigo),
    fecha   DATE        NOT NULL,
    desde   VARCHAR(10) NOT NULL,
    hasta   VARCHAR(10) NOT NULL
);

CREATE TABLE IF NOT EXISTS consultas_ia (
    codigo  VARCHAR(10) NOT NULL REFERENCES negocios(codigo),
    mes     VARCHAR(7)  NOT NULL,
    count   INT         NOT NULL DEFAULT 0,
    PRIMARY KEY (codigo, mes)
);

CREATE TABLE IF NOT EXISTS conversaciones_registro (
    numero_cliente  VARCHAR(50) PRIMARY KEY,
    estado          VARCHAR(50) NOT NULL,
    nombre_negocio  VARCHAR(200),
    tipo            VARCHAR(30),
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS leads_negocios (
    id               SERIAL      PRIMARY KEY,
    numero_whatsapp  VARCHAR(50),
    nombre_negocio   VARCHAR(200),
    tipo             VARCHAR(30),
    numero_contacto  VARCHAR(50),
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _parse_date(s):
    if not s:
        return None
    if isinstance(s, date):
        return s
    return datetime.strptime(s, "%Y-%m-%d").date()


def migrate():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("ERROR: DATABASE_URL no está definido.")

    conn = psycopg2.connect(dsn, sslmode="require")
    cur  = conn.cursor()

    print("→ Creando tablas...")
    cur.execute(SCHEMA)

    with open("negocios.json", encoding="utf-8") as f:
        datos = json.load(f)

    negocios = datos.get("negocios", {})
    print(f"→ Sembrando {len(negocios)} negocio(s)...")

    for codigo, neg in negocios.items():
        # negocios
        cur.execute("""
            INSERT INTO negocios (codigo, nombre, tipo, modo, numero_negocio, pin, activo)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (codigo) DO NOTHING
        """, (
            codigo,
            neg["nombre"],
            neg.get("tipo", ""),
            neg["modo"],
            neg["numero_negocio"],
            neg["pin"],
            neg.get("activo", True),
        ))

        # catalogo
        for clave, prod in neg.get("catalogo", {}).items():
            cur.execute("""
                INSERT INTO catalogo (codigo, clave, nombre, precio, unidad, rebanado, activo, cantidad)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (codigo, clave) DO NOTHING
            """, (
                codigo, clave,
                prod["nombre"], prod["precio"], prod["unidad"],
                prod.get("rebanado", False), prod.get("activo", True), prod.get("cantidad", 1),
            ))

        # servicios
        for clave, srv in neg.get("servicios", {}).items():
            cur.execute("""
                INSERT INTO servicios (codigo, clave, nombre, duracion_minutos, precio, activo)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (codigo, clave) DO NOTHING
            """, (
                codigo, clave,
                srv["nombre"], srv["duracion_minutos"], srv["precio"], srv.get("activo", True),
            ))

        # horarios
        for dia, h in neg.get("horario", {}).items():
            cur.execute("""
                INSERT INTO horarios (codigo, dia, trabaja, inicio, fin)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (codigo, dia) DO NOTHING
            """, (codigo, dia, h.get("trabaja", True), h.get("inicio"), h.get("fin")))

        # contadores_turnos
        cur.execute("""
            INSERT INTO contadores_turnos (codigo, contador)
            VALUES (%s, %s)
            ON CONFLICT (codigo) DO NOTHING
        """, (codigo, neg.get("contador_turnos", 0)))

        # pedidos_activos
        for pedido in neg.get("pedidos_activos", []):
            cur.execute("""
                INSERT INTO pedidos
                    (numero_cliente, codigo, turno, items, total, direccion, referencia, estado)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pendiente')
                ON CONFLICT DO NOTHING
            """, (
                pedido["numero_cliente"], codigo,
                pedido.get("turno", 0),
                Json(pedido.get("items", [])),
                pedido.get("total", 0),
                pedido.get("direccion", ""),
                pedido.get("referencia", ""),
            ))

        # citas
        estado_map = {"activa": "confirmada", "confirmada": "confirmada", "cancelada": "cancelada"}
        for cita in neg.get("citas", []):
            agendado_en = None
            if cita.get("agendado_en"):
                try:
                    agendado_en = datetime.fromisoformat(cita["agendado_en"])
                except ValueError:
                    pass
            cur.execute("""
                INSERT INTO citas
                    (codigo, numero_cliente, servicio, nombre_servicio, fecha, hora,
                     duracion_minutos, estado, agendado_en, recordatorio_enviado)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (
                codigo,
                cita["numero_cliente"],
                cita.get("servicio", ""),
                cita.get("nombre_servicio", cita.get("servicio", "")),
                _parse_date(cita["fecha"]),
                cita["hora"],
                cita.get("duracion_minutos", 0),
                estado_map.get(cita.get("estado", "activa"), "confirmada"),
                agendado_en,
                cita.get("recordatorio_enviado", False),
            ))

        # bloqueos
        for b in neg.get("bloqueos", []):
            cur.execute("""
                INSERT INTO bloqueos (codigo, fecha, desde, hasta)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (codigo, _parse_date(b["fecha"]), b["desde"], b["hasta"]))

        # consultas_ia
        ci = neg.get("consultas_ia", {})
        if ci.get("mes") and ci.get("count", 0) > 0:
            cur.execute("""
                INSERT INTO consultas_ia (codigo, mes, count)
                VALUES (%s, %s, %s)
                ON CONFLICT (codigo, mes) DO NOTHING
            """, (codigo, ci["mes"], ci["count"]))

    cur.execute("""
        ALTER TABLE negocios
          ADD COLUMN IF NOT EXISTS requiere_comprobante BOOLEAN NOT NULL DEFAULT FALSE
    """)
    cur.execute("""
        ALTER TABLE negocios
          ADD COLUMN IF NOT EXISTS instrucciones_pago TEXT NOT NULL DEFAULT ''
    """)

    cur.execute("""
        ALTER TABLE contadores_turnos
          ADD COLUMN IF NOT EXISTS fecha DATE DEFAULT CURRENT_DATE
    """)

    cur.execute("""
        ALTER TABLE negocios
          ADD COLUMN IF NOT EXISTS flow_id VARCHAR(50)
    """)

    # Columnas agregadas en sesión 2026-06-06 (ME2, transcripción médica, nota paciente)
    cur.execute("""
        ALTER TABLE conversaciones_citas
          ADD COLUMN IF NOT EXISTS nota_paciente  TEXT,
          ADD COLUMN IF NOT EXISTS nota_media_id  TEXT
    """)

    # Columnas agregadas en sesión 2026-06-09 (categorías servicios, catálogo)
    cur.execute("""
        ALTER TABLE conversaciones_citas
          ADD COLUMN IF NOT EXISTS categoria VARCHAR(100)
    """)
    cur.execute("""
        ALTER TABLE catalogo
          ADD COLUMN IF NOT EXISTS categoria VARCHAR(100)
    """)
    cur.execute("""
        ALTER TABLE citas
          ADD COLUMN IF NOT EXISTS nota_paciente  TEXT,
          ADD COLUMN IF NOT EXISTS nota_media_id  TEXT
    """)
    cur.execute("""
        ALTER TABLE sesiones_admin
          ADD COLUMN IF NOT EXISTS transcripcion_pendiente TEXT
    """)
    cur.execute("""
        ALTER TABLE negocios
          ADD COLUMN IF NOT EXISTS categorias_info JSONB
    """)
    cur.execute("""
        ALTER TABLE conversaciones_citas
          ADD COLUMN IF NOT EXISTS categoria VARCHAR(100)
    """)
    cur.execute("""
        ALTER TABLE negocios
          ADD COLUMN IF NOT EXISTS solo_retiro BOOLEAN NOT NULL DEFAULT FALSE
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("✓ Migración completada.")


if __name__ == "__main__":
    migrate()
