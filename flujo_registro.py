"""
Indiana — Agente de onboarding de negocios Wappi.
Recopila todos los datos en pocos mensajes y crea el negocio completo en DB.
"""
import os
import re
import json
import random
import string
from db import execute

DIAS_SEMANA = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DIAS_ALIAS  = {"miércoles": "miercoles", "sábado": "sabado"}
DIA_INDEX   = {d: i for i, d in enumerate(DIAS_SEMANA)}


# ── Estado ────────────────────────────────────────────────────────────────────

def _get(numero):
    return execute(
        "SELECT * FROM conversaciones_registro WHERE numero_cliente = %s",
        (numero,), fetch="one"
    )

def _set(numero, estado, datos):
    execute("""
        INSERT INTO conversaciones_registro (numero_cliente, estado, nombre_negocio, tipo, datos)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (numero_cliente) DO UPDATE SET
            estado         = EXCLUDED.estado,
            nombre_negocio = EXCLUDED.nombre_negocio,
            tipo           = EXCLUDED.tipo,
            datos          = EXCLUDED.datos,
            actualizado_en = NOW()
    """, (numero, estado, datos.get("nombre"), datos.get("categoria"), json.dumps(datos)))

def _del(numero):
    execute("DELETE FROM conversaciones_registro WHERE numero_cliente = %s", (numero,))

def tiene_flujo_registro(numero):
    return _get(numero) is not None


# ── Parsers ───────────────────────────────────────────────────────────────────

def _hora(s):
    """'9am' → '09:00', '6:30pm' → '18:30'"""
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", s.strip().lower())
    if not m:
        return None
    h, mins, p = m.groups()
    h = int(h); mins = int(mins or 0)
    if p == "pm" and h != 12: h += 12
    elif p == "am" and h == 12: h = 0
    return f"{h:02d}:{mins:02d}"

def _parsear_horario(texto):
    horario = {d: {"trabaja": False, "inicio": None, "fin": None} for d in DIAS_SEMANA}
    texto = texto.lower()
    for k, v in DIAS_ALIAS.items():
        texto = texto.replace(k, v)

    for parte in re.split(r"[,\n]+", texto):
        parte = parte.strip()
        if not parte:
            continue
        cerrado = any(p in parte for p in ["cerrado", "descansa", "libre", "no trabaja"])
        dias = []

        rango = re.search(r"(\w+)\s+a\s+(\w+)", parte)
        if rango and rango.group(1) in DIA_INDEX and rango.group(2) in DIA_INDEX:
            i1, i2 = DIA_INDEX[rango.group(1)], DIA_INDEX[rango.group(2)]
            dias = DIAS_SEMANA[i1:i2+1]
        elif "todos" in parte or "toda la semana" in parte:
            dias = DIAS_SEMANA[:]
        else:
            dias = [d for d in DIAS_SEMANA if d in parte]

        if not dias:
            continue

        if cerrado:
            for d in dias:
                horario[d] = {"trabaja": False, "inicio": None, "fin": None}
        else:
            t = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*[-–a]\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm))", parte)
            if t:
                ini, fin = _hora(t.group(1)), _hora(t.group(2))
                for d in dias:
                    horario[d] = {"trabaja": True, "inicio": ini, "fin": fin}
    return horario

def _parsear_servicios(texto):
    servicios = []
    for item in re.split(r"[/\n]+", texto):
        item = item.strip()
        if not item:
            continue
        dur = re.search(r"(\d+)\s*min", item, re.IGNORECASE)
        precio = re.search(r"(?:RD\$|RD|\$)\s*(\d+(?:[.,]\d+)?)", item, re.IGNORECASE)
        nombre = item
        if dur:
            nombre = nombre[:dur.start()]
        if precio:
            nombre = re.sub(r"(?:RD\$|RD|\$)\s*\d+(?:[.,]\d+)?", "", nombre)
        nombre = nombre.strip(" -/+")
        if not nombre:
            continue
        clave = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", nombre.lower().replace(" ", "_"))).strip("_")[:30]
        servicios.append({
            "clave": clave,
            "nombre": nombre,
            "duracion_minutos": int(dur.group(1)) if dur else 30,
            "precio": float(precio.group(1).replace(",", ".")) if precio else 0
        })
    return servicios

def _parsear_catalogo(texto):
    UNIDADES = {"lb": "libra", "und": "unidad", "libra": "libra", "unidad": "unidad",
                "litro": "litro", "botella": "botella", "caja": "caja",
                "sobre": "sobre", "paquete": "paquete"}
    items = []
    for item in re.split(r"[/\n]+", texto):
        item = item.strip()
        if not item:
            continue
        precio = re.search(r"(?:RD\$|RD|\$)\s*(\d+(?:[.,]\d+)?)", item, re.IGNORECASE)
        unidad = "unidad"
        for u, norm in UNIDADES.items():
            if re.search(rf"\b{u}\b", item, re.IGNORECASE):
                unidad = norm
                break
        nombre = item
        if precio:
            nombre = nombre[:precio.start()]
        nombre = nombre.strip(" -/")
        if not nombre:
            continue
        clave = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", nombre.lower().replace(" ", "_"))).strip("_")[:30]
        items.append({
            "clave": clave,
            "nombre": nombre,
            "precio": float(precio.group(1).replace(",", ".")) if precio else 0,
            "unidad": unidad
        })
    return items


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generar_codigo(tipo):
    rows = execute(
        "SELECT codigo FROM negocios WHERE codigo LIKE %s ORDER BY codigo",
        (f"{tipo}%",), fetch="all"
    ) or []
    nums = [int(re.search(r"\d+$", r["codigo"]).group()) for r in rows if re.search(r"\d+$", r["codigo"])]
    return f"{tipo}{max(nums) + 1 if nums else 1}"

def _generar_pin():
    return "".join(random.choices(string.digits, k=6))

def _resumen(datos):
    modo = datos.get("modo", "")
    txt = f"*Resumen del negocio:*\n\n"
    txt += f"Nombre: {datos.get('nombre')}\n"
    txt += f"Tipo: {'Pedidos a domicilio' if modo == 'pedidos' else 'Agendar citas'}\n"
    txt += f"WhatsApp: {datos.get('numero_negocio')}\n"

    if modo == "citas":
        txt += f"Atención: {datos.get('tipo_atencion', '')}\n"
        horario = datos.get("horario", {})
        dias = [d for d, v in horario.items() if v.get("trabaja")]
        if dias:
            h0 = horario[dias[0]]
            txt += f"Horario: {', '.join(dias)} {h0['inicio']}-{h0['fin']}\n"
        servicios = datos.get("servicios", [])
        if servicios:
            txt += f"\nServicios ({len(servicios)}):\n"
            for s in servicios:
                txt += f"  • {s['nombre']} — {s['duracion_minutos']}min — RD${s['precio']:.0f}\n"

    elif modo == "pedidos":
        txt += f"Comprobante: {'Sí' if datos.get('requiere_comprobante') else 'No'}\n"
        catalogo = datos.get("catalogo", [])
        if catalogo:
            txt += f"\nProductos ({len(catalogo)}):\n"
            for p in catalogo:
                txt += f"  • {p['nombre']} — RD${p['precio']:.0f}/{p['unidad']}\n"

    txt += "\n¿Todo está correcto?\n1. Sí, registrar\n2. No, empezar de nuevo"
    return txt

def _crear_negocio(datos):
    modo = datos["modo"]
    tipo = datos.get("categoria", "CO") if modo == "citas" else "CO"
    codigo = _generar_codigo(tipo)
    pin = _generar_pin()

    num = re.sub(r"\D", "", datos["numero_negocio"])
    if len(num) == 10:
        num = "1" + num
    numero_fmt = f"+{num}"

    execute(
        """INSERT INTO negocios (codigo, nombre, tipo, modo, numero_negocio, pin, activo, requiere_comprobante)
           VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s)""",
        (codigo, datos["nombre"], tipo, modo, numero_fmt, pin, datos.get("requiere_comprobante", False))
    )

    if modo == "citas":
        for dia, h in datos.get("horario", {}).items():
            execute(
                "INSERT INTO horarios (codigo, dia, trabaja, inicio, fin) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (codigo, dia, h["trabaja"], h.get("inicio"), h.get("fin"))
            )
        for s in datos.get("servicios", []):
            execute(
                "INSERT INTO servicios (codigo, clave, nombre, duracion_minutos, precio, activo) VALUES (%s,%s,%s,%s,%s,TRUE) ON CONFLICT DO NOTHING",
                (codigo, s["clave"], s["nombre"], s["duracion_minutos"], s["precio"])
            )
    else:
        for p in datos.get("catalogo", []):
            execute(
                "INSERT INTO catalogo (codigo, clave, nombre, precio, unidad, rebanado, activo, cantidad) VALUES (%s,%s,%s,%s,%s,FALSE,TRUE,100) ON CONFLICT DO NOTHING",
                (codigo, p["clave"], p["nombre"], p["precio"], p["unidad"])
            )
        execute(
            "INSERT INTO contadores_turnos (codigo, contador) VALUES (%s, 0) ON CONFLICT DO NOTHING",
            (codigo,)
        )

    return codigo, pin, numero_fmt


# ── State machine ─────────────────────────────────────────────────────────────

def manejar_registro(numero, mensaje, meta_send):
    msg = mensaje.strip()
    msg_low = msg.lower()

    if any(p in msg_low for p in ["cancelar", "salir", "cancel"]):
        _del(numero)
        return "Registro cancelado. Escribe *4* cuando quieras intentarlo de nuevo."

    row = _get(numero)
    if not row:
        return None

    estado = row["estado"]
    datos = row.get("datos") or {}
    if isinstance(datos, str):
        datos = json.loads(datos)

    # NOMBRE
    if estado == "esperando_nombre":
        datos["nombre"] = msg
        _set(numero, "esperando_modo", datos)
        return (
            f"¿Qué servicio ofrece *{msg}*?\n\n"
            "1. Pedidos a domicilio\n"
            "2. Agendar citas\n\n"
            "_Escribe *cancelar* para salir._"
        )

    # MODO
    if estado == "esperando_modo":
        if msg_low in ("1", "pedidos", "pedidos a domicilio"):
            datos["modo"] = "pedidos"
            _set(numero, "esperando_numero_negocio", datos)
            return "¿Cuál es el número de WhatsApp del negocio?\n_Ejemplo: 8091234567_"
        elif msg_low in ("2", "citas", "agendar citas"):
            datos["modo"] = "citas"
            _set(numero, "esperando_categoria", datos)
            return "¿Qué tipo de negocio es?\n\n1. Médico / Clínica\n2. Barbería / Estética\n3. Otro"
        return "Escribe *1* para Pedidos o *2* para Agendar Citas."

    # CATEGORÍA (citas)
    if estado == "esperando_categoria":
        cat_map = {"1": "ME", "2": "BA", "3": "GN",
                   "medico": "ME", "médico": "ME", "clinica": "ME", "clínica": "ME",
                   "barberia": "BA", "barbería": "BA", "estetica": "BA", "estética": "BA",
                   "otro": "GN"}
        cat = cat_map.get(msg_low)
        if not cat:
            return "Escribe *1*, *2* o *3*."
        datos["categoria"] = cat
        _set(numero, "esperando_numero_negocio", datos)
        return "¿Cuál es el número de WhatsApp del negocio?\n_Ejemplo: 8091234567_"

    # NÚMERO
    if estado == "esperando_numero_negocio":
        num = re.sub(r"\D", "", msg)
        if len(num) < 7:
            return "Ese número no parece válido. Escríbelo de nuevo.\n_Ejemplo: 8091234567_"
        datos["numero_negocio"] = num
        if datos.get("modo") == "citas":
            _set(numero, "esperando_horario", datos)
            return (
                "¿Cuál es el horario del negocio?\n\n"
                "_Ejemplo:_\n"
                "_lunes a viernes 9am-6pm, sábado 9am-2pm, domingo cerrado_\n\n"
                "O si trabajan todos los días:\n"
                "_todos los días 9am-8pm_"
            )
        else:
            _set(numero, "esperando_catalogo", datos)
            return (
                "Lista los productos del negocio así:\n\n"
                "_Arroz RD$45 libra / Salami RD$80 libra / Presidente RD$120 unidad_\n\n"
                "Formato: *nombre RD$precio unidad*\nSepara con */*"
            )

    # HORARIO (citas)
    if estado == "esperando_horario":
        horario = _parsear_horario(msg)
        if not any(v["trabaja"] for v in horario.values()):
            return (
                "No pude entender el horario. Escríbelo así:\n"
                "_lunes a viernes 9am-6pm, sábado 9am-2pm, domingo cerrado_"
            )
        datos["horario"] = horario
        _set(numero, "esperando_servicios", datos)
        return (
            "¿Qué servicios ofrece?\n\n"
            "_Ejemplo:_\n"
            "_Corte simple 30min RD$350 / Corte+Barba 60min RD$550 / Barba sola 20min RD$200_\n\n"
            "Formato: *nombre duración precio*\nSepara con */*"
        )

    # SERVICIOS (citas)
    if estado == "esperando_servicios":
        servicios = _parsear_servicios(msg)
        if not servicios:
            return (
                "No pude entender los servicios. Escríbelos así:\n"
                "_Corte simple 30min RD$350 / Barba 20min RD$200_"
            )
        datos["servicios"] = servicios
        _set(numero, "esperando_tipo_atencion", datos)
        return "¿Cómo se atiende a los clientes?\n\n1. Presencial\n2. Virtual (videollamada)\n3. Ambos"

    # TIPO ATENCIÓN (citas)
    if estado == "esperando_tipo_atencion":
        at_map = {"1": "Presencial", "2": "Virtual", "3": "Presencial y Virtual",
                  "presencial": "Presencial", "virtual": "Virtual", "ambos": "Presencial y Virtual"}
        tipo_at = at_map.get(msg_low)
        if not tipo_at:
            return "Escribe *1*, *2* o *3*."
        datos["tipo_atencion"] = tipo_at
        _set(numero, "confirmando", datos)
        return _resumen(datos)

    # CATÁLOGO (pedidos)
    if estado == "esperando_catalogo":
        catalogo = _parsear_catalogo(msg)
        if not catalogo:
            return (
                "No pude entender el catálogo. Escríbelo así:\n"
                "_Arroz RD$45 libra / Salami RD$80 libra_"
            )
        datos["catalogo"] = catalogo
        _set(numero, "esperando_comprobante", datos)
        return "¿Requieren comprobante de pago (foto de transferencia)?\n\n1. Sí\n2. No"

    # COMPROBANTE (pedidos)
    if estado == "esperando_comprobante":
        if msg_low in ("1", "si", "sí"):
            datos["requiere_comprobante"] = True
        elif msg_low in ("2", "no"):
            datos["requiere_comprobante"] = False
        else:
            return "Escribe *1* para Sí o *2* para No."
        _set(numero, "confirmando", datos)
        return _resumen(datos)

    # CONFIRMACIÓN
    if estado == "confirmando":
        if msg_low in ("1", "si", "sí", "correcto"):
            try:
                codigo, pin, numero_fmt = _crear_negocio(datos)
                _del(numero)

                dueno = os.getenv("DUEÑO_WHATSAPP", "").replace("+", "").strip()
                if dueno:
                    meta_send(
                        dueno,
                        f"🆕 *Indiana — Negocio registrado*\n\n"
                        f"Nombre:  {datos['nombre']}\n"
                        f"Código:  {codigo}\n"
                        f"Modo:    {datos['modo']}\n"
                        f"Número:  {numero_fmt}\n"
                        f"PIN:     {pin}"
                    )

                try:
                    from maverick import _log
                    _log("indiana", "negocio_creado",
                         f"Nuevo negocio: {datos['nombre']} ({codigo})",
                         {"codigo": codigo, "modo": datos["modo"]}, resuelto=True)
                except Exception:
                    pass

                return (
                    f"✅ *¡{datos['nombre']} ya está en Wappi!*\n\n"
                    f"*Código del negocio:* `{codigo}`\n"
                    f"*PIN de admin:* `{pin}`\n\n"
                    f"Con el código tus clientes pueden empezar a usarlo ahora mismo.\n"
                    f"Usa el PIN para gestionar todo desde WhatsApp.\n\n"
                    f"Nuestro equipo te contactará para los próximos pasos. 🚀"
                )
            except Exception as e:
                print(f"[Indiana] Error creando negocio: {e}")
                return "Hubo un error al registrar el negocio. Nuestro equipo te contactará pronto."

        elif msg_low in ("2", "no", "empezar de nuevo"):
            _del(numero)
            return "Entendido. Escribe *4* cuando quieras intentarlo de nuevo."

        return "Escribe *1* para confirmar o *2* para empezar de nuevo."

    return None


def iniciar_registro(numero, meta_send):
    _set(numero, "esperando_nombre", {})
    return (
        "Hola 👋 Soy *Indiana*, el agente de registro de Wappi.\n\n"
        "Voy a ayudarte a conectar tu negocio en minutos.\n\n"
        "¿Cómo se llama tu negocio?\n\n"
        "_Escribe *cancelar* en cualquier momento para salir._"
    )
