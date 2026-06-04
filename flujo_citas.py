import re
import threading
import time
from datetime import datetime, timedelta, date, time as dtime
from db import execute
from negocio_router import obtener_negocio
from google_calendar import get_google_tokens, crear_cita_con_meet, mensaje_confirmacion_virtual

DIAS_ISO  = {0:"lunes", 1:"martes", 2:"miercoles", 3:"jueves", 4:"viernes", 5:"sabado", 6:"domingo"}
DIAS_ES   = {"lunes":0,"martes":1,"miercoles":2,"miércoles":2,
             "jueves":3,"viernes":4,"sabado":5,"sábado":5,"domingo":6}
DIAS_DISP = {0:"Lunes",1:"Martes",2:"Miercoles",3:"Jueves",4:"Viernes",5:"Sabado",6:"Domingo"}


# ── Tiempo ────────────────────────────────────────────────────────────────────

def _hm(hora_str):
    h, m = hora_str.split(":")
    return int(h) * 60 + int(m)

def _mh(minutos):
    return f"{(minutos // 60) % 24:02d}:{minutos % 60:02d}"

def _fmt12(hora_str):
    h, m = map(int, hora_str.split(":"))
    mer = "AM" if h < 12 else "PM"
    return f"{h % 12 or 12}:{m:02d} {mer}"

def _parsear_hora(texto):
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", texto.lower())
    if m:
        h, mins = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and h != 12:
            h += 12
        elif m.group(3) == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mins:02d}"
    m = re.search(r"(\d{1,2}):(\d{2})", texto)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None

def _proximo(nombre_dia):
    obj = DIAS_ES.get(nombre_dia.lower())
    if obj is None:
        return None
    hoy = date.today()
    for i in range(1, 8):
        f = hoy + timedelta(days=i)
        if f.weekday() == obj:
            return f.isoformat()
    return None


# ── Disponibilidad ────────────────────────────────────────────────────────────

BUFFER_PRESENCIAL = 120  # minutos mínimos entre citas presenciales

def _bloqueada(codigo, fecha, hora_str, duracion_min, es_presencial=False):
    t0 = _hm(hora_str)
    # Para presencial: el slot nuevo ocupa max(duracion, BUFFER) minutos efectivos
    efect_nuevo = max(duracion_min, BUFFER_PRESENCIAL) if es_presencial else duracion_min
    t1 = t0 + efect_nuevo
    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").date()

    bloqueos = execute(
        "SELECT desde, hasta FROM bloqueos WHERE codigo = %s AND fecha = %s",
        (codigo, fecha_dt), fetch="all"
    ) or []
    for b in bloqueos:
        if _hm(b["desde"]) < t1 and _hm(b["hasta"]) > t0:
            return True

    citas = execute(
        "SELECT hora, duracion_minutos, tipo FROM citas "
        "WHERE codigo = %s AND fecha = %s AND estado = 'confirmada'",
        (codigo, fecha_dt), fetch="all"
    ) or []
    for c in citas:
        ci = _hm(c["hora"])
        # Cita existente presencial también bloquea BUFFER minutos efectivos
        efect_exist = max(c["duracion_minutos"], BUFFER_PRESENCIAL) if c.get("tipo") == "presencial" else c["duracion_minutos"]
        if ci < t1 and ci + efect_exist > t0:
            return True
    return False


def _horas_del_dia(negocio, fecha, duracion_min, es_presencial=False):
    dia_nombre = DIAS_ISO[datetime.strptime(fecha, "%Y-%m-%d").weekday()]
    h_dia = negocio.get("horario", {}).get(dia_nombre, {})
    if not h_dia.get("trabaja") or not h_dia.get("inicio"):
        return []
    ini = _hm(h_dia["inicio"])
    fin = _hm(h_dia["fin"])
    if fin <= ini:
        fin += 24 * 60
    slots = []
    t = ini
    while t + duracion_min <= fin:
        hora_str = _mh(t)
        if not _bloqueada(negocio["codigo"], fecha, hora_str, duracion_min, es_presencial):
            slots.append(hora_str)
        t += 30
    return slots


def _dias_del_negocio(negocio, duracion_min, es_presencial=False):
    hoy = date.today()
    dias = []
    for i in range(1, 9):
        f = hoy + timedelta(days=i)
        nombre = DIAS_ISO[f.weekday()]
        if not negocio.get("horario", {}).get(nombre, {}).get("trabaja"):
            continue
        if _horas_del_dia(negocio, f.isoformat(), duracion_min, es_presencial):
            dias.append((f.isoformat(), DIAS_DISP[f.weekday()], f.strftime("%d/%m")))
    return dias


# ── Textos ────────────────────────────────────────────────────────────────────

def _txt_servicios(negocio):
    lineas = [f"Nuestros servicios:\n"]
    for i, (_, s) in enumerate(negocio.get("servicios", {}).items(), 1):
        if s.get("activo", True):
            lineas.append(f"{i}. {s['nombre']} - ${s['precio']} pesos ({s['duracion_minutos']} min)")
    lineas += ["", "Escribe el *numero* del servicio.", "Escribe *cancelar* para salir."]
    return "\n".join(lineas)

def _txt_dias(negocio, duracion_min, es_presencial=False):
    dias = _dias_del_negocio(negocio, duracion_min, es_presencial)
    if not dias:
        return None
    lineas = ["Dias disponibles:\n"]
    for i, (_, nombre, display) in enumerate(dias, 1):
        lineas.append(f"{i}. {nombre} {display}")
    lineas.append("\nEscribe el *numero* del dia.")
    return "\n".join(lineas)

def _txt_horas(negocio, fecha, duracion_min, es_presencial=False):
    horas = _horas_del_dia(negocio, fecha, duracion_min, es_presencial)
    if not horas:
        return None
    lineas = ["Horas disponibles:\n"]
    for i, h in enumerate(horas, 1):
        lineas.append(f"{i}. {_fmt12(h)}")
    lineas.append("\nEscribe el *numero* de la hora.")
    return "\n".join(lineas)


# ── Helpers de estado conversacional ─────────────────────────────────────────

def _get_estado_cita(numero_cliente):
    return execute(
        "SELECT * FROM conversaciones_citas WHERE numero_cliente = %s",
        (numero_cliente,), fetch="one"
    )

def _set_estado_cita(numero_cliente, data):
    execute("""
        INSERT INTO conversaciones_citas
            (numero_cliente, codigo, estado, servicio_clave, dia, nombre_dia, hora, tipo, lugar)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (numero_cliente) DO UPDATE SET
            codigo         = EXCLUDED.codigo,
            estado         = EXCLUDED.estado,
            servicio_clave = EXCLUDED.servicio_clave,
            dia            = EXCLUDED.dia,
            nombre_dia     = EXCLUDED.nombre_dia,
            hora           = EXCLUDED.hora,
            tipo           = EXCLUDED.tipo,
            lugar          = EXCLUDED.lugar,
            actualizado_en = NOW()
    """, (
        numero_cliente,
        data["codigo"],
        data["estado"],
        data.get("servicio_clave"),
        data.get("dia"),
        data.get("nombre_dia"),
        data.get("hora"),
        data.get("tipo"),
        data.get("lugar"),
    ))

def _del_estado_cita(numero_cliente):
    execute("DELETE FROM conversaciones_citas WHERE numero_cliente = %s", (numero_cliente,))


# ── Helpers de sesiones admin ─────────────────────────────────────────────────

def _get_sesion_admin(numero):
    return execute(
        "SELECT * FROM sesiones_admin WHERE numero = %s AND expira > NOW()",
        (numero,), fetch="one"
    )

def _set_sesion_admin(numero, codigo, preguntando_numero_principal=False):
    execute("""
        INSERT INTO sesiones_admin (numero, codigo, expira, preguntando_numero_principal)
        VALUES (%s, %s, NOW() + INTERVAL '48 hours', %s)
        ON CONFLICT (numero) DO UPDATE SET
            codigo                     = EXCLUDED.codigo,
            expira                     = EXCLUDED.expira,
            preguntando_numero_principal = EXCLUDED.preguntando_numero_principal
    """, (numero, codigo, preguntando_numero_principal))

def _update_preguntando(numero, value):
    execute(
        "UPDATE sesiones_admin SET preguntando_numero_principal = %s WHERE numero = %s",
        (value, numero)
    )


# ── Admin: autenticación ──────────────────────────────────────────────────────

def _buscar_por_pin(mensaje):
    rows = execute(
        "SELECT codigo, pin FROM negocios WHERE modo = 'citas' AND activo = TRUE",
        fetch="all"
    ) or []
    for n in rows:
        if re.match(r"^admin\s+" + re.escape(n["pin"]) + r"$", mensaje.strip(), re.IGNORECASE):
            return n["codigo"], obtener_negocio(n["codigo"])
    return None, None

def _codigo_admin(numero):
    row = execute(
        "SELECT codigo FROM negocios WHERE numero_negocio = %s AND modo = 'citas' AND activo = TRUE",
        (numero,), fetch="one"
    )
    if row:
        return row["codigo"]
    sesion = execute(
        "SELECT codigo FROM sesiones_admin WHERE numero = %s AND expira > NOW()",
        (numero,), fetch="one"
    )
    return sesion["codigo"] if sesion else None

def tiene_sesion_admin_citas(numero):
    return execute(
        "SELECT 1 FROM sesiones_admin WHERE numero = %s AND expira > NOW()",
        (numero,), fetch="one"
    ) is not None


# ── Recordatorios ────────────────────────────────────────────────────────────

def _verificar_recordatorios(twilio_send):
    now = datetime.now()
    citas = execute("""
        SELECT c.id, c.numero_cliente, c.nombre_servicio, c.fecha, c.hora,
               c.agendado_en, n.nombre AS nombre_negocio
        FROM citas c
        JOIN negocios n ON c.codigo = n.codigo
        WHERE c.estado = 'confirmada'
          AND c.recordatorio_enviado = FALSE
          AND c.agendado_en IS NOT NULL
          AND (c.fecha::text || ' ' || c.hora)::timestamp > NOW()
    """, fetch="all") or []

    for cita in citas:
        fecha_str = cita["fecha"].isoformat() if hasattr(cita["fecha"], "isoformat") else cita["fecha"]
        cita_dt   = datetime.strptime(f"{fecha_str} {cita['hora']}", "%Y-%m-%d %H:%M")
        booking_dt = cita["agendado_en"]
        if not isinstance(booking_dt, datetime):
            booking_dt = datetime.fromisoformat(str(booking_dt))
        horas_hasta = (cita_dt - booking_dt).total_seconds() / 3600
        reminder_dt = (cita_dt - timedelta(hours=3)) if horas_hasta <= 24 else (booking_dt + timedelta(hours=23))
        if now >= reminder_dt:
            twilio_send(
                cita["numero_cliente"],
                f"Recordatorio: tu cita de {cita['nombre_servicio']} en {cita['nombre_negocio']} "
                f"es el {fecha_str} a las {_fmt12(cita['hora'])}."
            )
            execute("UPDATE citas SET recordatorio_enviado = TRUE WHERE id = %s", (cita["id"],))

def iniciar_recordatorios(twilio_send):
    def _loop():
        while True:
            try:
                _verificar_recordatorios(twilio_send)
            except Exception as e:
                print(f"[RECORDATORIOS] Error: {e}")
            time.sleep(60)
    threading.Thread(target=_loop, daemon=True).start()


# ── Flujo cliente ─────────────────────────────────────────────────────────────

def tiene_flujo_citas(numero_cliente):
    return execute(
        "SELECT 1 FROM conversaciones_citas WHERE numero_cliente = %s",
        (numero_cliente,), fetch="one"
    ) is not None


def manejar_cita(numero_cliente, codigo, mensaje, twilio_send):
    msg = mensaje.strip().lower()

    estado = _get_estado_cita(numero_cliente)
    if estado:
        codigo = estado["codigo"]
    elif not codigo:
        return None

    negocio = obtener_negocio(codigo)
    if not negocio or negocio.get("modo") != "citas":
        return None

    if not estado:
        estado = {"numero_cliente": numero_cliente, "codigo": codigo, "estado": "inicio",
                  "servicio_clave": None, "dia": None, "nombre_dia": None, "hora": None}
        _set_estado_cita(numero_cliente, estado)

    s = estado["estado"]

    # Reconstruir servicio desde clave
    servicio = None
    if estado.get("servicio_clave"):
        servicio = negocio.get("servicios", {}).get(estado["servicio_clave"])

    if re.search(r"\b(cancelar|cancel|salir|bye|chao)\b", msg):
        _del_estado_cita(numero_cliente)
        return "Reserva cancelada. Escribe el codigo del negocio cuando quieras agendar."

    # ── INICIO ──
    lugares = negocio.get("lugares_reunion") or []
    if s == "inicio":
        if lugares:
            estado["estado"] = "esperando_tipo"
            _set_estado_cita(numero_cliente, estado)
            desc = negocio.get("descripcion", "")
            desc_txt = f"\n\n{desc}" if desc else ""
            return (f"Bienvenido a {negocio['nombre']}!{desc_txt}\n\n"
                    "¿Qué tipo de asesoría necesitas?\n\n"
                    "1. Online (Google Meet)\n"
                    "2. Presencial\n\n"
                    "Escribe *1* o *2*.")
        estado["estado"] = "esperando_servicio"
        _set_estado_cita(numero_cliente, estado)
        return f"Bienvenido a {negocio['nombre']}!\n\n" + _txt_servicios(negocio)

    # ── ESPERANDO TIPO ──
    if s == "esperando_tipo":
        if msg in ("1", "online"):
            estado.update({"estado": "esperando_servicio", "tipo": "online"})
            _set_estado_cita(numero_cliente, estado)
            return _txt_servicios(negocio)
        if msg in ("2", "presencial"):
            estado.update({"estado": "esperando_lugar", "tipo": "presencial"})
            _set_estado_cita(numero_cliente, estado)
            lineas = ["Elige el lugar de reunion:\n"]
            for i, l in enumerate(lugares, 1):
                lineas.append(f"{i}. {l}")
            lineas.append("\nEscribe el *numero* del lugar.")
            return "\n".join(lineas)
        return "Escribe *1* para Online o *2* para Presencial."

    # ── ESPERANDO LUGAR ──
    if s == "esperando_lugar":
        if msg.isdigit() and 1 <= int(msg) <= len(lugares):
            lugar = lugares[int(msg) - 1]
            estado.update({"estado": "esperando_servicio", "lugar": lugar})
            _set_estado_cita(numero_cliente, estado)
            return _txt_servicios(negocio)
        return f"Escribe un numero del 1 al {len(lugares)}."

    # ── ESPERANDO SERVICIO ──
    if s == "esperando_servicio":
        if not msg:
            _set_estado_cita(numero_cliente, estado)
            return _txt_servicios(negocio)

        servicios = [(c, sv) for c, sv in negocio.get("servicios", {}).items()
                     if sv.get("activo", True)]
        elegido = None
        if msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(servicios):
                elegido = servicios[idx]
        else:
            for c, sv in servicios:
                if msg in sv["nombre"].lower() or msg == c:
                    elegido = (c, sv)
                    break

        if not elegido:
            _set_estado_cita(numero_cliente, estado)
            return _txt_servicios(negocio)

        clave_s, serv_s = elegido
        estado.update({"estado": "esperando_dia", "servicio_clave": clave_s})
        _set_estado_cita(numero_cliente, estado)

        ep = estado.get("tipo") == "presencial"
        txt = _txt_dias(negocio, serv_s["duracion_minutos"], ep)
        if not txt:
            _del_estado_cita(numero_cliente)
            return "No hay disponibilidad en los proximos dias. Intenta mas adelante."
        return txt

    # ── ESPERANDO DÍA ──
    if s == "esperando_dia" and servicio:
        ep = estado.get("tipo") == "presencial"
        dias = _dias_del_negocio(negocio, servicio["duracion_minutos"], ep)
        if not dias:
            _del_estado_cita(numero_cliente)
            return "No hay disponibilidad en los proximos dias. Intenta mas adelante."

        elegido = None
        if msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(dias):
                elegido = dias[idx]
        else:
            for d in dias:
                if msg in d[1].lower() or msg in d[2]:
                    elegido = d
                    break

        if not elegido:
            return _txt_dias(negocio, servicio["duracion_minutos"], ep)

        fecha, nombre_dia, _ = elegido
        estado.update({"estado": "esperando_hora", "dia": fecha, "nombre_dia": nombre_dia})
        _set_estado_cita(numero_cliente, estado)

        txt = _txt_horas(negocio, fecha, servicio["duracion_minutos"], ep)
        if not txt:
            estado["estado"] = "esperando_dia"
            _set_estado_cita(numero_cliente, estado)
            return f"No hay horas disponibles el {nombre_dia}. Elige otro dia.\n\n" + _txt_dias(negocio, servicio["duracion_minutos"], ep)
        return txt

    # ── ESPERANDO HORA ──
    if s == "esperando_hora" and servicio:
        ep = estado.get("tipo") == "presencial"
        fecha = estado["dia"]
        horas = _horas_del_dia(negocio, fecha, servicio["duracion_minutos"], ep)

        elegida = None
        if msg.isdigit():
            idx = int(msg) - 1
            if 0 <= idx < len(horas):
                elegida = horas[idx]
        else:
            for h in horas:
                if msg in h or msg in _fmt12(h).lower():
                    elegida = h
                    break

        if not elegida:
            return _txt_horas(negocio, fecha, servicio["duracion_minutos"], ep)

        estado.update({"estado": "confirmando", "hora": elegida})
        _set_estado_cita(numero_cliente, estado)

        r  = "Resumen de tu cita:\n\n"
        r += f"Negocio:  {negocio['nombre']}\n"
        r += f"Servicio: {servicio['nombre']}\n"
        r += f"Duracion: {servicio['duracion_minutos']} min\n"
        r += f"Precio:   ${servicio['precio']} pesos\n"
        r += f"Dia:      {estado['nombre_dia']}\n"
        r += f"Hora:     {_fmt12(elegida)}\n"
        r += "\nEscribe *si* para confirmar o *cancelar* para salir."
        return r

    # ── CONFIRMANDO ──
    if s == "confirmando" and servicio:
        if not re.search(r"\b(si|sí|confirmar|confirma|dale|ok|listo)\b", msg):
            return (f"Servicio: {servicio['nombre']} — {_fmt12(estado['hora'])} el {estado['nombre_dia']}\n\n"
                    "Escribe *si* para confirmar o *cancelar* para salir.")

        fecha_dt = datetime.strptime(estado["dia"], "%Y-%m-%d").date()
        execute("""
            INSERT INTO citas
                (codigo, numero_cliente, servicio, nombre_servicio, fecha, hora,
                 duracion_minutos, estado, tipo, lugar, agendado_en, recordatorio_enviado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmada', %s, %s, NOW(), FALSE)
        """, (
            codigo, numero_cliente, estado["servicio_clave"], servicio["nombre"],
            fecha_dt, estado["hora"], servicio["duracion_minutos"],
            estado.get("tipo"), estado.get("lugar"),
        ))

        tipo_txt  = "Online (Google Meet)" if estado.get("tipo") == "online" else "Presencial"
        lugar_txt = f"\nLugar:    {estado['lugar']}" if estado.get("lugar") else ""
        twilio_send(
            negocio["numero_negocio"],
            f"NUEVA CITA\n\n"
            f"Servicio: {servicio['nombre']}\n"
            f"Tipo:     {tipo_txt}"
            f"{lugar_txt}\n"
            f"Dia:      {estado['nombre_dia']} — {_fmt12(estado['hora'])}\n"
            f"Cliente:  {numero_cliente}"
        )

        # ── Google Calendar ──
        meet_link = None
        if get_google_tokens(codigo):
            try:
                es_virtual = estado.get("tipo") == "online"
                h, m = map(int, estado["hora"].split(":"))
                inicio = datetime.combine(fecha_dt, dtime(h, m))
                meet_link = crear_cita_con_meet(
                    codigo=codigo,
                    nombre_cliente=numero_cliente,
                    servicio=servicio["nombre"],
                    inicio=inicio,
                    duracion_minutos=servicio["duracion_minutos"],
                    numero_whatsapp=numero_cliente,
                    es_virtual=es_virtual,
                )
                if es_virtual and meet_link:
                    twilio_send(numero_cliente, mensaje_confirmacion_virtual(
                        negocio["nombre"], servicio["nombre"], inicio, meet_link
                    ))
            except Exception as e:
                print(f"[Google Calendar] Error para {codigo}: {e}")

        _del_estado_cita(numero_cliente)

        if meet_link:
            return "Cita confirmada! El enlace de tu reunion virtual ya fue enviado."

        lugar_conf = f"\nLugar:    {estado['lugar']}" if estado.get("lugar") else ""
        return (f"Cita confirmada!\n\n"
                f"Servicio: {servicio['nombre']}\n"
                f"Dia:      {estado['nombre_dia']}\n"
                f"Hora:     {_fmt12(estado['hora'])}"
                f"{lugar_conf}\n\n"
                f"Te esperamos en {negocio['nombre']}.")

    return None


# ── Flujo negocio ─────────────────────────────────────────────────────────────

def manejar_negocio_citas(numero, mensaje, twilio_send):
    msg     = mensaje.strip()
    msg_low = msg.lower()

    codigo_pin, neg_pin = _buscar_por_pin(msg)
    codigo_activo = _codigo_admin(numero)

    if not codigo_activo and codigo_pin:
        _set_sesion_admin(numero, codigo_pin, preguntando_numero_principal=True)
        return (f"Acceso concedido a {neg_pin['nombre']}.\n"
                f"Tu sesion dura 48 horas.\n\n"
                f"Quieres hacer este numero el principal del negocio?\n"
                f"Responde si o no.")

    sesion = _get_sesion_admin(numero)
    if sesion and sesion.get("preguntando_numero_principal") and codigo_activo:
        _update_preguntando(numero, False)
        if re.search(r"\b(si|sí)\b", msg_low):
            execute("UPDATE negocios SET numero_negocio = %s WHERE codigo = %s", (numero, codigo_activo))
            return "Listo. Este numero es ahora el numero principal del negocio."
        return "OK. Puedes usar los comandos de agenda normalmente."

    if not codigo_activo:
        return None

    codigo  = codigo_activo
    negocio = obtener_negocio(codigo)
    hoy     = date.today()

    # ── mis citas hoy ──
    if re.search(r"mis\s+citas\s+hoy", msg_low):
        citas = execute(
            "SELECT hora, nombre_servicio, numero_cliente FROM citas "
            "WHERE codigo = %s AND fecha = %s AND estado = 'confirmada' ORDER BY hora",
            (codigo, hoy), fetch="all"
        ) or []
        if not citas:
            return "No hay citas para hoy."
        lineas = ["Citas de hoy:\n"]
        for c in citas:
            lineas.append(f"• {_fmt12(c['hora'])} — {c['nombre_servicio']} ({c['numero_cliente']})")
        return "\n".join(lineas)

    # ── mis citas semana ──
    if re.search(r"mis\s+citas\s+semana", msg_low):
        fechas = [(hoy + timedelta(days=i)) for i in range(7)]
        fecha_min, fecha_max = fechas[0], fechas[-1]
        citas = execute(
            "SELECT fecha, hora, nombre_servicio, numero_cliente FROM citas "
            "WHERE codigo = %s AND fecha BETWEEN %s AND %s AND estado = 'confirmada' "
            "ORDER BY fecha, hora",
            (codigo, fecha_min, fecha_max), fetch="all"
        ) or []
        if not citas:
            return "No hay citas esta semana."
        lineas = ["Citas de la semana:\n"]
        for c in citas:
            fecha_str = c["fecha"].isoformat() if hasattr(c["fecha"], "isoformat") else c["fecha"]
            lineas.append(f"• {fecha_str} {_fmt12(c['hora'])} — {c['nombre_servicio']} ({c['numero_cliente']})")
        return "\n".join(lineas)

    # ── ocupado hasta HH:MM ──
    if "ocupado hasta" in msg_low:
        hasta = _parsear_hora(msg_low)
        if not hasta:
            return "No entendi la hora. Ejemplo: ocupado hasta 5pm"
        ahora = datetime.now().strftime("%H:%M")
        if _hm(hasta) <= _hm(ahora):
            return "Esa hora ya paso. Hasta que hora quieres bloquear?"
        execute(
            "INSERT INTO bloqueos (codigo, fecha, desde, hasta) VALUES (%s, %s, %s, %s)",
            (codigo, hoy, ahora, hasta)
        )
        return f"Agenda bloqueada desde ahora hasta las {_fmt12(hasta)}."

    # ── no disponible (resto del día) ──
    if re.search(r"\bno\s+disponible\b", msg_low):
        ahora = datetime.now().strftime("%H:%M")
        execute(
            "INSERT INTO bloqueos (codigo, fecha, desde, hasta) VALUES (%s, %s, %s, %s)",
            (codigo, hoy, ahora, "23:59")
        )
        return "Agenda bloqueada por el resto del dia."

    # ── libre [dia] ──
    m = re.search(r"libre\s+(\w+)", msg_low)
    if m:
        nombre_dia = m.group(1)
        fecha_bloqueo = _proximo(nombre_dia)
        if not fecha_bloqueo:
            return f"No reconoci el dia '{nombre_dia}'."
        fecha_dt = datetime.strptime(fecha_bloqueo, "%Y-%m-%d").date()
        execute(
            "INSERT INTO bloqueos (codigo, fecha, desde, hasta) VALUES (%s, %s, %s, %s)",
            (codigo, fecha_dt, "00:00", "23:59")
        )
        return f"{nombre_dia.capitalize()} {fecha_bloqueo} bloqueado completamente."

    # ── cancelar cita [numero_cliente] ──
    m_num = re.match(r"cancelar\s+cita\s+(\S+)", msg_low)
    m_fh  = re.match(r"cancelar\s+(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})", msg_low)

    if m_num or m_fh:
        target = None

        if m_num:
            buscado = m_num.group(1)
            if not buscado.startswith("whatsapp:"):
                buscado = f"whatsapp:{buscado}"
            target = execute("""
                SELECT id, numero_cliente, nombre_servicio, fecha, hora
                FROM citas
                WHERE codigo = %s AND numero_cliente = %s AND estado = 'confirmada'
                  AND fecha >= %s
                ORDER BY fecha, hora
                LIMIT 1
            """, (codigo, buscado, hoy), fetch="one")
        else:
            fecha_b = m_fh.group(1)
            hora_b  = f"{int(m_fh.group(2).split(':')[0]):02d}:{m_fh.group(2).split(':')[1]}"
            fecha_dt = datetime.strptime(fecha_b, "%Y-%m-%d").date()
            target = execute("""
                SELECT id, numero_cliente, nombre_servicio, fecha, hora
                FROM citas
                WHERE codigo = %s AND fecha = %s AND hora = %s AND estado = 'confirmada'
                LIMIT 1
            """, (codigo, fecha_dt, hora_b), fetch="one")

        if not target:
            return "No encontre una cita activa con esos datos."

        execute("UPDATE citas SET estado = 'cancelada' WHERE id = %s", (target["id"],))

        fecha_str = target["fecha"].isoformat() if hasattr(target["fecha"], "isoformat") else target["fecha"]
        cita_dt = datetime.strptime(f"{fecha_str} {target['hora']}", "%Y-%m-%d %H:%M")
        if cita_dt > datetime.now():
            twilio_send(
                target["numero_cliente"],
                f"Tu cita de {target['nombre_servicio']} en {negocio['nombre']} "
                f"el {fecha_str} a las {_fmt12(target['hora'])} fue cancelada.\n\n"
                f"Escribe *{codigo}* si quieres agendar una nueva cita."
            )
        return f"Cita de {target['numero_cliente']} cancelada."

    return None
