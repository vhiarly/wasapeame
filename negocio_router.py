import re
from db import execute, get_conn_ctx

def detectar_codigo(mensaje):
    """Retorna (codigo, resto_mensaje) si el mensaje empieza con un código válido o palabra clave, o (None, mensaje)."""
    msg = mensaje.strip()

    # Formato estándar: ME1, SE1, etc.
    match = re.match(r'^([A-Z]{2}\d+)\s*(.*)', msg, re.IGNORECASE)
    if match:
        codigo = match.group(1).upper()
        row = execute("SELECT codigo FROM negocios WHERE codigo = %s AND activo = TRUE", (codigo,), fetch="one")
        if row:
            return codigo, match.group(2).strip()

    # Palabra clave personalizada (primera palabra, case-insensitive)
    parts = msg.split(None, 1)
    if parts:
        keyword = parts[0].lower()
        resto   = parts[1] if len(parts) > 1 else ""
        row = execute(
            "SELECT codigo FROM negocios WHERE LOWER(palabra_clave) = %s AND activo = TRUE",
            (keyword,), fetch="one"
        )
        if row:
            return row["codigo"], resto

    return None, mensaje

def obtener_negocio(codigo):
    codigo = codigo.upper()
    with get_conn_ctx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT codigo, nombre, tipo, modo, numero_negocio, pin, activo, "
                "requiere_comprobante, instrucciones_pago, lugares_reunion, descripcion, "
                "costo_online, costo_presencial, test_mode "
                "FROM negocios WHERE codigo = %s",
                (codigo,)
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            neg = dict(zip(cols, row))

            cur.execute(
                "SELECT clave, nombre, precio, unidad, rebanado, activo, cantidad "
                "FROM catalogo WHERE codigo = %s",
                (codigo,)
            )
            cols = [d[0] for d in cur.description]
            catalogo = {}
            for r in cur.fetchall():
                d = dict(zip(cols, r))
                catalogo[d["clave"]] = {
                    "nombre":   d["nombre"],
                    "precio":   float(d["precio"]),
                    "unidad":   d["unidad"],
                    "rebanado": d["rebanado"],
                    "activo":   d["activo"],
                    "cantidad": d["cantidad"],
                }

            cur.execute(
                "SELECT clave, nombre, duracion_minutos, precio, activo, descripcion, categoria "
                "FROM servicios WHERE codigo = %s",
                (codigo,)
            )
            cols = [d[0] for d in cur.description]
            servicios = {}
            for r in cur.fetchall():
                d = dict(zip(cols, r))
                servicios[d["clave"]] = {
                    "nombre":           d["nombre"],
                    "duracion_minutos": d["duracion_minutos"],
                    "precio":           float(d["precio"]),
                    "activo":           d["activo"],
                    "descripcion":      d.get("descripcion") or "",
                    "categoria":        d.get("categoria") or "",
                }

            cur.execute(
                "SELECT dia, trabaja, inicio, fin FROM horarios WHERE codigo = %s",
                (codigo,)
            )
            cols = [d[0] for d in cur.description]
            horario = {}
            for r in cur.fetchall():
                d = dict(zip(cols, r))
                horario[d["dia"]] = {
                    "trabaja": d["trabaja"],
                    "inicio":  d["inicio"],
                    "fin":     d["fin"],
                }

    neg["catalogo"] = catalogo
    neg["servicios"] = servicios
    neg["horario"]   = horario
    return neg

def es_admin(mensaje, negocio):
    """Retorna True si el mensaje es exactamente 'admin <pin>'."""
    patron = re.compile(r'^admin\s+' + re.escape(negocio["pin"]) + r'$', re.IGNORECASE)
    return bool(patron.match(mensaje.strip()))

def obtener_modo(codigo):
    neg = obtener_negocio(codigo)
    return neg.get("modo") if neg else None

def es_numero_negocio(numero):
    """Retorna el codigo si el numero pertenece a un negocio registrado, o None."""
    row = execute(
        "SELECT codigo FROM negocios WHERE numero_negocio = %s AND activo = TRUE",
        (numero,), fetch="one"
    )
    return row["codigo"] if row else None
