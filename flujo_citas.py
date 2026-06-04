import re
import threading
import time
from datetime import datetime, timedelta, date, time as dtime
from zoneinfo import ZoneInfo
from db import execute
from negocio_router import obtener_negocio

TZ_RD = ZoneInfo("America/Santo_Domingo")
HORAS_LABORALES_LIMITE = 7


def _horas_laborales_hasta(fecha_cita, hora_cita_str, negocio):
    """Calcula horas laborables desde ahora hasta la cita según el horario del negocio."""
    ahora = datetime.now(TZ_RD)
    h, m  = map(int, hora_cita_str.split(":"))
    cita_dt = datetime(fecha_cita.year, fecha_cita.month, fecha_cita.day, h, m,
                       tzinfo=TZ_RD)
    if cita_dt <= ahora:
        return 0

    total_minutos = 0
    cursor = ahora
    while cursor < cita_dt:
        dia_nombre = DIAS_ISO[cursor.weekday()]
        h_dia = negocio.get("horario", {}).get(dia_nombre, {})
        if h_dia.get("trabaja") and h_dia.get("inicio"):
            ini_h, ini_m = map(int, h_dia["inicio"].split(":"))
            fin_h, fin_m = map(int, h_dia["fin"].split(":"))
            ini_dt = cursor.replace(hour=ini_h, minute=ini_m, second=0, microsecond=0)
            fin_dt = cursor.replace(hour=fin_h, minute=fin_m, second=0, microsecond=0)
            seg_ini = max(cursor, ini_dt)
            seg_fin = min(cita_dt, fin_dt)
            if seg_fin > seg_ini:
                total_minutos += (seg_fin - seg_ini).seconds // 60
        cursor = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    return total_minutos / 60
from google_calendar import get_google_tokens, crear_cita_con_meet, mensaje_confirmacion_virtual
from asistente_ia import validar_comprobante

def _fmt_numero(numero):
    """whatsapp:+18298789906 → +1 829 878 9906"""
    n = numero.replace("whatsapp:+", "").replace("+", "").strip()
    if len(n) == 11 and n.startswith("1"):
        return f"+1 {n[1:4]} {n[4:7]} {n[7:]}"
    if len(n) == 10:
        return f"+1 {n[:3]} {n[3:6]} {n[6:]}"
    return f"+{n}"


def _lugar_nombre(l):
    return l["nombre"] if isinstance(l, dict) else l

def _lugar_maps(l):
    return l.get("maps") if isinstance(l, dict) else None


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

def _txt_servicios(negocio, tipo=None):
    lineas = [f"Nuestros servicios:\n"]
    activos = [(c, s) for c, s in negocio.get("servicios", {}).items() if s.get("activo", True)]
    for i, (_, s) in enumerate(activos, 1):
        lineas.append(f"{i}. {s['nombre']}")
    if tipo == "online" and negocio.get("costo_online"):
        lineas.append(f"\nCosto de consultoría: *${negocio['costo_online']:,} DOP*")
    elif tipo == "presencial" and negocio.get("costo_presencial"):
        lineas.append(f"\nCosto de consultoría: *${negocio['costo_presencial']:,} DOP*")
    lineas += ["", "Escribe el *numero* del servicio o *cancelar* para salir."]
    return "\n".join(lineas)

def _txt_dias(negocio, duracion_min, es_presencial=False):
    dias = _dias_del_negocio(negocio, duracion_min, es_presencial)
    if not dias:
        return None
    lineas = ["Dias disponibles:\n"]
    for i, (_, nombre, display) in enumerate(dias, 1):
        lineas.append(f"{i}. {nombre} {display}")
    lineas.append("\nEscribe el *numero* del dia o *cancelar* para salir.")
    return "\n".join(lineas)

def _txt_horas(negocio, fecha, duracion_min, es_presencial=False):
    horas = _horas_del_dia(negocio, fecha, duracion_min, es_presencial)
    if not horas:
        return None
    lineas = ["Horas disponibles:\n"]
    for i, h in enumerate(horas, 1):
        lineas.append(f"{i}. {_fmt12(h)}")
    lineas.append("\nEscribe el *numero* de la hora o *cancelar* para salir.")
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
            (numero_cliente, codigo, estado, servicio_clave, dia, nombre_dia, hora, tipo, lugar,
             cliente_nombre, cliente_email)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (numero_cliente) DO UPDATE SET
            codigo         = EXCLUDED.codigo,
            estado         = EXCLUDED.estado,
            servicio_clave = EXCLUDED.servicio_clave,
            dia            = EXCLUDED.dia,
            nombre_dia     = EXCLUDED.nombre_dia,
            hora           = EXCLUDED.hora,
            tipo           = EXCLUDED.tipo,
            lugar          = EXCLUDED.lugar,
            cliente_nombre = EXCLUDED.cliente_nombre,
            cliente_email  = EXCLUDED.cliente_email,
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
        data.get("cliente_nombre"),
        data.get("cliente_email"),
    ))

def _del_estado_cita(numero_cliente):
    execute("DELETE FROM conversaciones_citas WHERE numero_cliente = %s", (numero_cliente,))


# ── Helpers de relay ──────────────────────────────────────────────────────────

def _get_relay(numero_cliente):
    return execute(
        "SELECT * FROM sesiones_relay WHERE numero_cliente = %s",
        (numero_cliente,), fetch="one"
    )

def _get_relay_por_negocio(numero_negocio):
    return execute(
        "SELECT * FROM sesiones_relay WHERE numero_negocio = %s AND estado IN ('activo','cerrando')",
        (numero_negocio,), fetch="all"
    ) or []

def _abrir_relay(numero_cliente, numero_negocio, codigo):
    execute("""
        INSERT INTO sesiones_relay (numero_cliente, numero_negocio, codigo, estado)
        VALUES (%s, %s, %s, 'activo')
        ON CONFLICT (numero_cliente) DO UPDATE SET
            numero_negocio = EXCLUDED.numero_negocio,
            codigo         = EXCLUDED.codigo,
            estado         = 'activo',
            respondio      = FALSE,
            creado_en      = NOW()
    """, (numero_cliente, numero_negocio, codigo))

def _actualizar_relay(numero_cliente, **kwargs):
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    vals = list(kwargs.values()) + [numero_cliente]
    execute(f"UPDATE sesiones_relay SET {sets} WHERE numero_cliente = %s", vals)

def _cerrar_relay(numero_cliente):
    execute("DELETE FROM sesiones_relay WHERE numero_cliente = %s", (numero_cliente,))


def cerrar_relay_timeout(numero_cliente, twilio_send):
    """Llamada por el timer de 30 min cuando el relay expira."""
    relay = _get_relay(numero_cliente)
    if not relay:
        return
    negocio = obtener_negocio(relay["codigo"])
    _cerrar_relay(numero_cliente)

    # Notificar a Pilar
    twilio_send(
        relay["numero_negocio"],
        f"⏱ Sesión de chat con {numero_cliente} cerrada por tiempo. El cliente fue notificado."
    )

    # Opciones al cliente
    conv = execute(
        "SELECT * FROM conversaciones_citas WHERE numero_cliente = %s AND estado = 'esperando_confirmacion_negocio'",
        (numero_cliente,), fetch="one"
    )
    if conv:
        execute(
            "UPDATE conversaciones_citas SET estado = 'esperando_resolucion_cliente' WHERE numero_cliente = %s",
            (numero_cliente,)
        )
    twilio_send(
        numero_cliente,
        "No pudimos completar la coordinacion en el tiempo disponible.\n\n"
        "¿Qué prefieres hacer con tu cita?\n\n"
        "1. Mantener la cita como esta\n"
        "2. Reagendar (ver nuevas opciones)\n"
        "3. Cancelar y solicitar reembolso"
    )


def manejar_relay_mensaje(numero, mensaje, media_url, twilio_send,
                           iniciar_timer_relay, cancelar_timer_relay):
    """
    Intercepta mensajes que pertenecen a una sesión relay activa.
    Retorna texto de respuesta, "" para no responder, o None si no aplica relay.
    """
    msg_low = mensaje.lower().strip()

    # ── Reagendar / Cancelar cita por cliente ──
    m_react = re.match(r"(reagendar|cancelar\s+cita)(?:\s+([a-zA-Z0-9]+))?$", msg_low)
    if m_react:
        accion      = "reagendar" if "reagendar" in m_react.group(1) else "cancelar"
        codigo_hint = (m_react.group(2) or "").upper() or None
        cita = _get_cita_confirmada(numero, codigo_hint)
        if not cita:
            return (f"No encontre una cita confirmada"
                    f"{' con ' + codigo_hint if codigo_hint else ''}. "
                    f"Si tienes el codigo escribe: *{m_react.group(1)} [codigo]*")
        negocio_r = obtener_negocio(cita["codigo"])
        horas = _horas_laborales_hasta(cita["fecha"], str(cita["hora"])[:5], negocio_r)
        politica = horas >= HORAS_LABORALES_LIMITE

        if accion == "reagendar":
            execute("""
                INSERT INTO conversaciones_citas (numero_cliente, codigo, estado)
                VALUES (%s, %s, 'reagendar_pendiente_aprobacion')
                ON CONFLICT (numero_cliente) DO UPDATE SET
                    codigo = EXCLUDED.codigo, estado = 'reagendar_pendiente_aprobacion'
            """, (numero, cita["codigo"]))
            nota_politica = (
                f"El cliente tiene *{horas:.1f} horas laborables* de anticipacion "
                f"({'reembolso garantizado si rechazas' if politica else 'fuera de politica — reembolso a tu criterio'})"
            )
            twilio_send(negocio_r["numero_negocio"],
                f"📅 SOLICITUD DE REAGENDAR\n\n"
                f"Cliente:  {numero}\n"
                f"Servicio: {cita['nombre_servicio']}\n"
                f"Cita:     {cita['fecha']} a las {_fmt12(str(cita['hora'])[:5])}\n\n"
                f"{nota_politica}\n\n"
                f"Escribe *aprobar reagendar {numero.replace('whatsapp:+','')}* para aceptar\n"
                f"o *rechazar reagendar {numero.replace('whatsapp:+','')}* para rechazar."
            )
            return (
                "Tu solicitud de reagendar fue enviada a "
                f"*{negocio_r['nombre']}*. Te avisamos cuando respondan."
            )

        else:  # cancelar
            if politica:
                # +7h laborales → reembolso garantizado
                execute("""
                    INSERT INTO conversaciones_citas (numero_cliente, codigo, estado)
                    VALUES (%s, %s, 'cancelacion_reembolso_garantizado')
                    ON CONFLICT (numero_cliente) DO UPDATE SET
                        codigo = EXCLUDED.codigo, estado = 'cancelacion_reembolso_garantizado'
                """, (numero, cita["codigo"]))
                twilio_send(negocio_r["numero_negocio"],
                    f"❌ CANCELACIÓN CON REEMBOLSO OBLIGATORIO\n\n"
                    f"Cliente:  {numero}\n"
                    f"Servicio: {cita['nombre_servicio']}\n"
                    f"Cita:     {cita['fecha']} a las {_fmt12(str(cita['hora'])[:5])}\n"
                    f"Horas laborables restantes: {horas:.1f}h (politica: +7h = reembolso garantizado)\n\n"
                    f"Debes procesar el reembolso completo.\n"
                    f"Envia comprobante con: comprobante reembolso {numero.replace('whatsapp:+','')}"
                )
                execute("UPDATE citas SET estado = 'cancelada' WHERE id = %s", (cita["id"],))
                return (
                    "Tu cita fue cancelada. Tienes derecho a *reembolso completo* "
                    f"ya que cancelaste con mas de {HORAS_LABORALES_LIMITE} horas laborables de anticipacion.\n\n"
                    "Por favor envia tus datos bancarios para procesar la devolucion:\n\n"
                    "*Banco:* [nombre]\n*Cuenta:* [numero]\n*Titular:* [nombre completo]"
                )
            else:
                # -7h laborales → Pilar decide
                execute("""
                    INSERT INTO conversaciones_citas (numero_cliente, codigo, estado)
                    VALUES (%s, %s, 'cancelacion_criterio_negocio')
                    ON CONFLICT (numero_cliente) DO UPDATE SET
                        codigo = EXCLUDED.codigo, estado = 'cancelacion_criterio_negocio'
                """, (numero, cita["codigo"]))
                twilio_send(negocio_r["numero_negocio"],
                    f"❌ SOLICITUD DE CANCELACIÓN (fuera de politica)\n\n"
                    f"Cliente:  {numero}\n"
                    f"Servicio: {cita['nombre_servicio']}\n"
                    f"Cita:     {cita['fecha']} a las {_fmt12(str(cita['hora'])[:5])}\n"
                    f"Horas laborables restantes: {horas:.1f}h (politica: menos de {HORAS_LABORALES_LIMITE}h — reembolso a tu criterio)\n\n"
                    f"Escribe *aprobar reembolso {numero.replace('whatsapp:+','')}* si decides reembolsar\n"
                    f"o *rechazar reembolso {numero.replace('whatsapp:+','')}* si no aplica."
                )
                execute("UPDATE citas SET estado = 'cancelada' WHERE id = %s", (cita["id"],))
                return (
                    "Tu cita fue cancelada. Estás dentro de las "
                    f"*{HORAS_LABORALES_LIMITE} horas laborables* de anticipacion, "
                    "por lo que el reembolso queda a criterio del negocio.\n\n"
                    "Te notificaremos su decision. "
                    f"Ver politica: wasapeame.co/descargo"
                )

    # ── No-show reportado por cliente ──
    if re.match(r"no\s+show(?:\s+[a-zA-Z]|$)", msg_low):
        resultado = manejar_no_show_cliente(numero, mensaje, twilio_send)
        if resultado:
            return resultado

    # ── Cliente decide tras no-show del negocio ──
    conv_ns = execute(
        "SELECT * FROM conversaciones_citas WHERE numero_cliente = %s AND estado = 'noshow_esperando_decision'",
        (numero,), fetch="one"
    )
    if conv_ns:
        negocio_ns = obtener_negocio(conv_ns["codigo"])
        if mensaje.strip() == "1":
            # Reagendar — resetear estado para nuevo flujo
            _del_estado_cita(numero)
            return (
                f"De acuerdo. Para reagendar escribe *{conv_ns['codigo']}* "
                f"y selecciona una nueva fecha y hora."
            )
        if mensaje.strip() == "2":
            # Reembolso
            execute(
                "UPDATE conversaciones_citas SET estado = 'esperando_datos_reembolso' WHERE numero_cliente = %s",
                (numero,)
            )
            twilio_send(negocio_ns["numero_negocio"],
                f"El cliente {numero} eligio reembolso por no-show.")
            return (
                "Para procesar tu reembolso necesito tus datos bancarios.\n\n"
                "Escribe en este formato:\n"
                "*Banco:* [nombre del banco]\n"
                "*Cuenta:* [numero de cuenta]\n"
                "*Titular:* [tu nombre completo]"
            )
        return "Escribe *1* para reagendar o *2* para reembolso."

    # ── Cliente responde al no-show de sí mismo ──
    conv_ns_cli = execute(
        "SELECT * FROM conversaciones_citas WHERE numero_cliente = %s AND estado = 'noshow_cliente_esperando'",
        (numero,), fetch="one"
    )
    if conv_ns_cli:
        negocio_c = obtener_negocio(conv_ns_cli["codigo"])
        if mensaje.strip() == "1":
            execute("UPDATE conversaciones_citas SET estado = 'noshow_cliente_reagendar_pendiente' WHERE numero_cliente = %s", (numero,))
            twilio_send(negocio_c["numero_negocio"],
                f"El cliente {numero} solicita reagendar tras no-show. "
                f"Escribe *chat {numero.replace('whatsapp:+','')}* para coordinarse "
                f"o *rechazar pago {numero.replace('whatsapp:+','')}* para cancelar sin reagendar.")
            return "Tu solicitud fue enviada al negocio. Te avisamos cuando respondan."
        if mensaje.strip() == "2":
            _del_estado_cita(numero)
            twilio_send(negocio_c["numero_negocio"],
                f"El cliente {numero} cancelo su cita (no-show del cliente). Cita cerrada.")
            return "Tu cita fue cancelada. Recuerda que el pago no es reembolsable en caso de no presentarse."
        return "Escribe *1* para reagendar o *2* para cancelar."

    # ── Caso 1: El mensaje viene del CLIENTE en relay ──
    relay = _get_relay(numero)
    if relay and relay["estado"] == "activo":
        negocio = obtener_negocio(relay["codigo"])
        if not relay["respondio"]:
            _actualizar_relay(numero, respondio=True)

        # Cliente reporta que se resolvió
        if re.search(r"\b(resuelto|se\s+logr[oó]|conect[eé]|lleg[oó]|ya\s+entr[eé]|ya\s+est[aá]|todo\s+bien)\b", msg_low):
            cancelar_timer_relay(numero)
            _cerrar_relay(numero)
            _del_estado_cita(numero)
            # Revertir el no-show (no cuenta)
            execute("""
                UPDATE citas SET no_show_negocio = GREATEST(no_show_negocio - 1, 0)
                WHERE numero_cliente = %s AND codigo = %s AND estado = 'confirmada'
                ORDER BY agendado_en DESC LIMIT 1
            """, (numero, relay["codigo"]))
            twilio_send(relay["numero_negocio"],
                f"✅ El cliente ({numero}) confirmo que se logro el contacto. No-show cerrado.")
            return (
                "¡Genial! Nos alegra que hayan podido conectarse. "
                "Esperamos que haya sido una sesión muy productiva. ¡Bendiciones! 🙏"
            )

        # Cliente elige "más tarde"
        if re.search(r"\bm[aá]s\s+tarde\b", msg_low):
            cancelar_timer_relay(numero)
            _cerrar_relay(numero)
            twilio_send(relay["numero_negocio"],
                f"El cliente ({numero}) no puede hablar ahora. Sesión cerrada.")
            execute(
                "UPDATE conversaciones_citas SET estado = 'esperando_resolucion_cliente' WHERE numero_cliente = %s",
                (numero,)
            )
            return (
                "Entendido. ¿Qué prefieres hacer con tu cita?\n\n"
                "1. Mantener la cita como esta\n"
                "2. Reagendar (ver nuevas opciones)\n"
                "3. Cancelar y solicitar reembolso"
            )

        # Reenviar mensaje del cliente a Pilar
        twilio_send(relay["numero_negocio"],
            f"Cliente: {mensaje}", media_url=media_url)
        return ""  # no responder al cliente

    # ── Caso 1b: Cliente en esperando_resolucion_cliente ──
    conv = execute(
        "SELECT * FROM conversaciones_citas WHERE numero_cliente = %s AND estado = 'esperando_resolucion_cliente'",
        (numero,), fetch="one"
    )
    if conv:
        negocio = obtener_negocio(conv["codigo"])
        if mensaje.strip() == "1":
            execute("UPDATE conversaciones_citas SET estado = 'esperando_confirmacion_negocio' WHERE numero_cliente = %s", (numero,))
            twilio_send(negocio["numero_negocio"],
                f"El cliente ({numero}) decidio mantener la cita.")
            return "Tu cita sigue en pie. Te confirmaremos el pago pronto."
        if mensaje.strip() == "2":
            _del_estado_cita(numero)
            twilio_send(negocio["numero_negocio"],
                f"El cliente ({numero}) quiere reagendar.")
            return "Para reagendar escribe el codigo del negocio y pasamos por el flujo de nuevo."
        if mensaje.strip() == "3":
            execute("UPDATE conversaciones_citas SET estado = 'esperando_datos_reembolso' WHERE numero_cliente = %s", (numero,))
            twilio_send(negocio["numero_negocio"],
                f"El cliente ({numero}) solicita reembolso.")
            return (
                "Entendido. Para procesar tu reembolso necesito tus datos bancarios.\n\n"
                "Escribe en este formato:\n"
                "*Banco:* [nombre del banco]\n"
                "*Cuenta:* [numero de cuenta]\n"
                "*Titular:* [tu nombre completo]"
            )
        return "Escribe *1* para mantener, *2* para reagendar o *3* para cancelar y reembolso."

    # ── Caso 1c: Cliente dando datos de reembolso ──
    conv_r = execute(
        "SELECT * FROM conversaciones_citas WHERE numero_cliente = %s AND estado = 'esperando_datos_reembolso'",
        (numero,), fetch="one"
    )
    if conv_r:
        negocio = obtener_negocio(conv_r["codigo"])
        servicio = negocio.get("servicios", {}).get(conv_r.get("servicio_clave"), {})
        tipo_cita = conv_r.get("tipo")
        monto = negocio.get("costo_online") if tipo_cita == "online" else negocio.get("costo_presencial")
        twilio_send(
            negocio["numero_negocio"],
            f"💸 REEMBOLSO SOLICITADO\n\n"
            f"Cliente:  {numero}\n"
            f"Servicio: {servicio.get('nombre','')}\n"
            f"Monto:    ${monto:,} DOP\n\n"
            f"Datos bancarios del cliente:\n{mensaje}\n\n"
            f"Realiza la transferencia manualmente y envia el comprobante con:\n"
            f"comprobante reembolso {numero.replace('whatsapp:+','')}"
        )
        _del_estado_cita(numero)
        return (
            "✅ Tus datos fueron enviados a Sir'Legal.\n\n"
            "El reembolso sera procesado en un plazo de *24-48 horas habiles*. "
            "Recibirás el comprobante de la devolución por este chat."
        )

    # ── Caso 2: El mensaje viene del NEGOCIO en relay activo ──
    relays_negocio = _get_relay_por_negocio(numero)
    if relays_negocio:
        # Buscar si es un reenvío (mensaje normal durante relay)
        for r in relays_negocio:
            if r["estado"] == "activo" and not re.match(r"cerrar\s+chat\s+\d+", msg_low):
                twilio_send(r["numero_cliente"], mensaje, media_url=media_url)
        return None  # Dejar que el flujo normal del negocio también procese sus comandos

    return None  # No aplica relay


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

def _refresh_google_tokens():
    """Refresca tokens de Google Calendar que expiran en menos de 10 minutos."""
    import os, requests as _req
    filas = execute(
        "SELECT codigo, google_refresh_token FROM negocios "
        "WHERE google_refresh_token IS NOT NULL AND google_token_expires < NOW() + INTERVAL '10 minutes'",
        fetch="all"
    ) or []
    for fila in filas:
        try:
            resp = _req.post("https://oauth2.googleapis.com/token", data={
                "client_id":     os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "refresh_token": fila["google_refresh_token"],
                "grant_type":    "refresh_token",
            }, timeout=10)
            resp.raise_for_status()
            new_token = resp.json()["access_token"]
            execute(
                "UPDATE negocios SET google_access_token=%s, "
                "google_token_expires=NOW() + INTERVAL '55 minutes' WHERE codigo=%s",
                (new_token, fila["codigo"])
            )
            print(f"[Google] Token refrescado para {fila['codigo']}")
        except Exception as e:
            print(f"[Google] Error refrescando token {fila['codigo']}: {e}")


def _escalar_noshow_sin_respuesta(twilio_send):
    """Escala no-shows donde Pilar no respondió en 2 horas."""
    relays = execute(
        "SELECT * FROM sesiones_relay WHERE estado='activo' "
        "AND creado_en < NOW() - INTERVAL '2 hours' AND respondio = FALSE",
        fetch="all"
    ) or []
    for relay in relays:
        try:
            execute("DELETE FROM sesiones_relay WHERE numero_cliente=%s", (relay["numero_cliente"],))
            twilio_send(relay["numero_negocio"],
                f"⏱ El relay con {relay['numero_cliente']} cerró por inactividad (2h sin respuesta).")
            conv = execute(
                "SELECT 1 FROM conversaciones_citas WHERE numero_cliente=%s "
                "AND estado='noshow_esperando_decision'",
                (relay["numero_cliente"],), fetch="one"
            )
            if conv:
                pass  # cliente ya tiene opciones
            else:
                execute("""
                    INSERT INTO conversaciones_citas (numero_cliente, codigo, estado)
                    VALUES (%s, %s, 'noshow_esperando_decision')
                    ON CONFLICT (numero_cliente) DO UPDATE SET
                        codigo=EXCLUDED.codigo, estado='noshow_esperando_decision'
                """, (relay["numero_cliente"], relay["codigo"]))
            twilio_send(
                relay["numero_cliente"],
                f"No hemos podido contactar al negocio en 2 horas.\n\n"
                f"¿Qué prefieres hacer?\n\n"
                f"1. Mantener la cita como esta\n"
                f"2. Reagendar\n"
                f"3. Cancelar y solicitar reembolso\n\n"
                f"wasapeame.co/descargo"
            )
            print(f"[No-show] Escalado: {relay['numero_cliente']}")
        except Exception as e:
            print(f"[No-show] Error escalando: {e}")


def iniciar_recordatorios(twilio_send):
    def _loop():
        while True:
            try:
                _verificar_recordatorios(twilio_send)
                _refresh_google_tokens()
                _escalar_noshow_sin_respuesta(twilio_send)
            except Exception as e:
                print(f"[RECORDATORIOS] Error: {e}")
            time.sleep(60)
    threading.Thread(target=_loop, daemon=True).start()


# ── No-show helpers ──────────────────────────────────────────────────────────

def _get_cita_confirmada(numero_cliente, codigo=None):
    """Retorna la cita confirmada más reciente del cliente."""
    if codigo:
        return execute(
            "SELECT * FROM citas WHERE numero_cliente = %s AND codigo = %s "
            "AND estado = 'confirmada' ORDER BY agendado_en DESC LIMIT 1",
            (numero_cliente, codigo), fetch="one"
        )
    return execute(
        "SELECT * FROM citas WHERE numero_cliente = %s AND estado = 'confirmada' "
        "ORDER BY agendado_en DESC LIMIT 1",
        (numero_cliente,), fetch="one"
    )


def manejar_no_show_cliente(numero_cliente, mensaje, twilio_send):
    """
    Cliente reporta que el negocio no se presentó.
    Detecta 'no show SE1' (código) o 'no show' (auto-detect).
    Retorna respuesta o None si no aplica.
    """
    msg_low = mensaje.lower().strip()
    m = re.match(r"no\s+show(?:\s+([a-z0-9]+))?$", msg_low)
    if not m:
        return None

    codigo_hint = (m.group(1) or "").upper() if m.group(1) else None

    # Buscar cita confirmada
    cita = _get_cita_confirmada(numero_cliente, codigo_hint)
    if not cita:
        if codigo_hint:
            return f"No encontre una cita confirmada con {codigo_hint}. Verifica el codigo."
        # Buscar sin código — preguntar al cliente
        citas = execute(
            "SELECT c.*, n.nombre as negocio_nombre FROM citas c "
            "JOIN negocios n ON c.codigo = n.codigo "
            "WHERE c.numero_cliente = %s AND c.estado = 'confirmada' "
            "ORDER BY c.agendado_en DESC LIMIT 3",
            (numero_cliente,), fetch="all"
        ) or []
        if not citas:
            return "No encontre citas confirmadas activas. Si tienes el codigo del negocio escribe: *no show [codigo]*"
        if len(citas) == 1:
            cita = citas[0]
        else:
            lineas = ["¿Sobre cuál cita es el no-show?\n"]
            for i, c in enumerate(citas, 1):
                lineas.append(f"{i}. {c['negocio_nombre']} — {c['nombre_servicio']} {c['fecha']}")
            lineas.append("\nEscribe el *numero* de la cita.")
            # Guardar estado temporal
            execute("""
                INSERT INTO conversaciones_citas (numero_cliente, codigo, estado)
                VALUES (%s, %s, 'esperando_seleccion_noshow')
                ON CONFLICT (numero_cliente) DO UPDATE SET estado = 'esperando_seleccion_noshow'
            """, (numero_cliente, citas[0]["codigo"]))
            return "\n".join(lineas)

    return _procesar_no_show_negocio(numero_cliente, cita, twilio_send)


def _procesar_no_show_negocio(numero_cliente, cita, twilio_send):
    """Procesa el no-show del negocio para una cita específica."""
    negocio = obtener_negocio(cita["codigo"])

    # Validar que haya pasado el tiempo mínimo de espera
    tipo_cita = cita.get("tipo")
    buffer_min = 5 if tipo_cita == "online" else 10
    h, m = map(int, str(cita["hora"])[:5].split(":"))
    cita_dt = datetime(cita["fecha"].year, cita["fecha"].month, cita["fecha"].day,
                       h, m, tzinfo=TZ_RD)
    ahora = datetime.now(TZ_RD)
    habilita_en = cita_dt + timedelta(minutes=buffer_min)

    if ahora < cita_dt:
        minutos_para_cita = int((cita_dt - ahora).total_seconds() / 60)
        return (f"Tu cita es en *{minutos_para_cita} minutos*. "
                f"Puedes reportar no-show {'5' if tipo_cita == 'online' else '10'} minutos "
                f"después de la hora acordada.")

    if ahora < habilita_en:
        espera = int((habilita_en - ahora).total_seconds() / 60) + 1
        return (f"Dale {'5' if tipo_cita == 'online' else '10'} minutos de espera. "
                f"Podrás reportar el no-show en *{espera} minuto{'s' if espera > 1 else ''}*.")
    no_show_count = (cita.get("no_show_negocio") or 0) + 1
    execute("UPDATE citas SET no_show_negocio = %s WHERE id = %s", (no_show_count, cita["id"]))

    if no_show_count >= 2:
        # Segunda vez — reembolso completo
        execute("UPDATE citas SET estado = 'cancelada' WHERE id = %s", (cita["id"],))
        twilio_send(
            negocio["numero_negocio"],
            f"🚨 SEGUNDO NO-SHOW — REEMBOLSO REQUERIDO\n\n"
            f"Cliente: {numero_cliente}\n"
            f"Servicio: {cita['nombre_servicio']}\n"
            f"Este es el segundo no-show con este cliente. "
            f"Debes procesar el reembolso completo.\n\n"
            f"Envia el comprobante con: comprobante reembolso {numero_cliente.replace('whatsapp:+','')}"
        )
        return (
            "Hemos registrado que *Sir'Legal* no se presento por segunda vez.\n\n"
            "Segun nuestra politica, tienes derecho a un *reembolso completo*.\n\n"
            "El negocio fue notificado y debe procesar la devolucion. "
            "Recibirás el comprobante por este chat.\n\n"
            f"Mas informacion: wasapeame.co/descargo"
        )

    # Primera vez — cliente decide qué quiere
    twilio_send(
        negocio["numero_negocio"],
        f"🚨 NO-SHOW REPORTADO\n\n"
        f"Cliente: {numero_cliente}\n"
        f"Servicio: {cita['nombre_servicio']} — {cita['fecha']}\n\n"
        f"El cliente decide si reagenda o pide reembolso. "
        f"Si el cliente reagenda y vuelves a fallar, deberás hacer reembolso completo."
    )
    execute(
        "UPDATE conversaciones_citas SET estado = 'noshow_esperando_decision' WHERE numero_cliente = %s",
        (numero_cliente,)
    ) if execute("SELECT 1 FROM conversaciones_citas WHERE numero_cliente = %s", (numero_cliente,), fetch="one") else \
    execute("""
        INSERT INTO conversaciones_citas (numero_cliente, codigo, estado)
        VALUES (%s, %s, 'noshow_esperando_decision')
    """, (numero_cliente, cita["codigo"]))

    return (
        f"Registramos que *{negocio['nombre']}* no se presento.\n\n"
        "¿Qué prefieres hacer?\n\n"
        "1. Reagendar la cita (tu pago sigue vigente)\n"
        "2. Reembolso completo\n\n"
        f"Tus derechos: wasapeame.co/descargo"
    )


# ── LBTR ─────────────────────────────────────────────────────────────────────

def _txt_horario_lbtr():
    return (
        "\n\n📋 *Horario de Pagos al Instante (LBTR):*\n"
        "• Lun-vie: 7:00am–4:00pm y 6:30pm–11:00pm\n"
        "• Sáb, dom y feriados: 7:00am–11:00pm (sin pausa)\n"
        "• Pausa lun-vie 4:00pm–6:30pm: se acredita al reanudar\n"
        "• Después de 11:00pm: acredita el próximo día laboral a las 8:00am\n"
        "• Mismo banco: acreditación inmediata"
    )


def _aviso_lbtr(es_mismo_banco=None):
    """
    Retorna aviso post-pago según tipo de transferencia y hora actual en RD.
    es_mismo_banco=True  → inmediata.
    es_mismo_banco=False → LBTR interbancaria, aplica horario.
    es_mismo_banco=None  → tipo desconocido, aplica horario por precaución.
    """
    if es_mismo_banco:
        return "✅ *Transferencia interna:* El pago se acredita al instante."

    now = datetime.now(TZ_RD)
    h = now.hour * 60 + now.minute
    es_fin_semana = now.weekday() >= 5

    if h >= 23 * 60 or h < 7 * 60:
        return (
            "⚠️ *Aviso LBTR:* Transferencia fuera del horario de operación. "
            "Se acreditará el próximo día laboral a las *8:00am*."
        )
    if not es_fin_semana and 16 * 60 <= h < 18 * 60 + 30:
        return (
            "⚠️ *Aviso LBTR:* Transferencia en horario de pausa (lun-vie 4:00pm–6:30pm). "
            "Se reflejará a partir de las *6:30pm* de hoy."
        )
    if es_mismo_banco is False:
        return "ℹ️ *Pagos al Instante:* Transferencia interbancaria en horario normal — se acredita en máximo *8 minutos*."
    return None


# ── Flujo cliente ─────────────────────────────────────────────────────────────

def tiene_flujo_citas(numero_cliente):
    return execute(
        "SELECT 1 FROM conversaciones_citas WHERE numero_cliente = %s",
        (numero_cliente,), fetch="one"
    ) is not None


def _msg_confirmacion(estado, servicio, negocio):
    tipo_final = estado.get("tipo")
    maps_link  = None
    if tipo_final == "online":
        costo_final = negocio.get("costo_online") or servicio["precio"]
        tipo_linea  = "💻 *Online — Google Meet*"
    elif tipo_final == "presencial":
        costo_final = negocio.get("costo_presencial") or servicio["precio"]
        tipo_linea  = f"📍 *{estado['lugar']}*" if estado.get("lugar") else "📍 *Presencial*"
        maps_link   = next(
            (_lugar_maps(l) for l in (negocio.get("lugares_reunion") or [])
             if _lugar_nombre(l) == estado.get("lugar")),
            None
        )
    else:
        costo_final = servicio["precio"]
        tipo_linea  = None

    r  = "✅ *Cita confirmada*\n\n"
    r += f"Negocio:  {negocio['nombre']}\n"
    r += f"Servicio: {servicio['nombre']}\n"
    if tipo_linea:
        etiqueta = "Lugar:   " if tipo_final == "presencial" else "Tipo:    "
        r += f"{etiqueta} {tipo_linea}\n"
    r += f"Dia:      {estado['nombre_dia']}\n"
    r += f"Hora:     *{_fmt12(estado['hora'])}*\n"
    if costo_final:
        r += f"Costo:    *${costo_final:,} DOP*\n"
    if tipo_final == "online":
        r += "\nEn breve recibes el enlace de Google Meet por este chat."
        r += "\n\n⚠️ No compartas datos bancarios ni personales durante la videollamada."
    if tipo_final == "presencial":
        if maps_link:
            r += f"\n\n📍 *Ver ubicación:* {maps_link}"
        r += "\n\n⚠️ Por tu seguridad avisa a alguien de confianza sobre esta reunion antes de asistir."
    r += f"\n\n¿Necesitas cambiar? Escribe *reagendar {negocio.get('codigo','')}* o *cancelar cita {negocio.get('codigo','')}*"
    r += f"\n¿El negocio no se presenta? Escribe: *no show {negocio.get('codigo','').lower()}*"
    r += f"\nPolitica de cancelacion: wasapeame.co/descargo"
    return r


def _procesar_confirmacion(codigo, numero_cliente, estado, servicio, negocio, twilio_send):
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

    tipo_txt  = ("Online (Google Meet)" if estado.get("tipo") == "online"
                 else "Presencial" if estado.get("tipo") == "presencial"
                 else None)
    tipo_linea = f"Tipo:     {tipo_txt}\n" if tipo_txt else ""
    lugar_txt  = f"Lugar:    {estado['lugar']}\n" if estado.get("lugar") else ""
    twilio_send(
        negocio["numero_negocio"],
        f"NUEVA CITA\n\n"
        f"Servicio: {servicio['nombre']}\n"
        f"{tipo_linea}"
        f"{lugar_txt}"
        f"Dia:      {estado['nombre_dia']} — {_fmt12(estado['hora'])}\n"
        f"Cliente:  {numero_cliente}"
    )

    # Google Calendar
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
                import time as _time
                msg1, msg2 = mensaje_confirmacion_virtual(
                    negocio["nombre"], servicio["nombre"], inicio, meet_link
                )
                twilio_send(numero_cliente, msg1)
                _time.sleep(1)
                twilio_send(numero_cliente, msg2)
        except Exception as e:
            print(f"[Google Calendar] Error para {codigo}: {e}")

    return meet_link


def manejar_cita(numero_cliente, codigo, mensaje, twilio_send, media_url=None):
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
            return (f"Bienvenido a *{negocio['nombre']}*{desc_txt}\n\n"
                    "¿Qué tipo de asesoría necesitas?\n\n"
                    "1. Online (Google Meet)\n"
                    "2. Presencial\n\n"
                    "Escribe *1* o *2*.\n"
                    "Escribe *cancelar* para salir.")
        estado["estado"] = "esperando_servicio"
        _set_estado_cita(numero_cliente, estado)
        return f"Bienvenido a {negocio['nombre']}!\n\n" + _txt_servicios(negocio)

    # ── ESPERANDO TIPO ──
    if s == "esperando_tipo":
        if msg in ("1", "online"):
            estado.update({"estado": "esperando_servicio", "tipo": "online"})
            _set_estado_cita(numero_cliente, estado)
            return _txt_servicios(negocio, estado.get("tipo"))
        if msg in ("2", "presencial"):
            estado.update({"estado": "esperando_lugar", "tipo": "presencial"})
            _set_estado_cita(numero_cliente, estado)
            lineas = ["Elige el lugar de reunion:\n"]
            for i, l in enumerate(lugares, 1):
                lineas.append(f"{i}. {_lugar_nombre(l)}")
            lineas.append("\nEscribe el *numero* del lugar.")
            lineas.append("Escribe *cancelar* para salir.")
            return "\n".join(lineas)
        return "Escribe *1* para Online o *2* para Presencial."

    # ── ESPERANDO LUGAR ──
    if s == "esperando_lugar":
        if msg.isdigit() and 1 <= int(msg) <= len(lugares):
            lugar = _lugar_nombre(lugares[int(msg) - 1])
            estado.update({"estado": "esperando_servicio", "lugar": lugar})
            _set_estado_cita(numero_cliente, estado)
            return _txt_servicios(negocio, estado.get("tipo"))
        return f"Escribe un numero del 1 al {len(lugares)}."

    # ── ESPERANDO SERVICIO ──
    if s == "esperando_servicio":
        if not msg:
            _set_estado_cita(numero_cliente, estado)
            return _txt_servicios(negocio, estado.get("tipo"))

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
            return _txt_servicios(negocio, estado.get("tipo"))

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

        estado.update({"estado": "esperando_datos_cliente", "hora": elegida})
        _set_estado_cita(numero_cliente, estado)
        return (
            "Casi listo. Para completar la cita necesito tus datos.\n\n"
            "Escribe en este formato:\n"
            "*Nombre:* [tu nombre completo]\n"
            "*Email:* [tu correo electronico]"
        )

    # ── ESPERANDO DATOS CLIENTE ──
    if s == "esperando_datos_cliente" and servicio:
        nombre = re.search(r"nombre[:\s]+(.+)", mensaje.strip(), re.IGNORECASE)
        email  = re.search(r"email[:\s]+([\w.+-]+@[\w.-]+\.\w+)", msg, re.IGNORECASE)
        if not nombre or not email:
            return (
                "Por favor escribe tus datos en este formato:\n\n"
                "*Nombre:* [tu nombre completo]\n"
                "*Email:* [tu correo electronico]"
            )
        estado.update({
            "estado": "confirmando",
            "cliente_nombre": nombre.group(1).strip(),
            "cliente_email":  email.group(1).strip().lower(),
        })
        _set_estado_cita(numero_cliente, estado)

        tipo = estado.get("tipo")
        if tipo == "online":
            costo = negocio.get("costo_online") or servicio["precio"]
        elif tipo == "presencial":
            costo = negocio.get("costo_presencial") or servicio["precio"]
        else:
            costo = servicio["precio"]

        r  = "Resumen de tu cita:\n\n"
        r += f"Negocio:  {negocio['nombre']}\n"
        r += f"Nombre:   {estado['cliente_nombre']}\n"
        r += f"Servicio: {servicio['nombre']}\n"
        r += f"Duracion: {servicio['duracion_minutos']} min\n"
        if costo:
            r += f"Costo:    *${costo:,} DOP*\n"
        r += f"Dia:      {estado['nombre_dia']}\n"
        r += f"Hora:     {_fmt12(estado['hora'])}\n"
        r += "\nEscribe *si* para continuar o *cancelar* para salir."
        return r

    # ── CONFIRMANDO ──
    if s == "confirmando" and servicio:
        if not re.search(r"\b(si|sí|confirmar|confirma|dale|ok|listo)\b", msg):
            return (f"Servicio: {servicio['nombre']} — {_fmt12(estado['hora'])} el {estado['nombre_dia']}\n\n"
                    "Escribe *si* para confirmar o *cancelar* para salir.")

        if negocio.get("requiere_comprobante") and negocio.get("instrucciones_pago"):
            estado["estado"] = "esperando_comprobante"
            _set_estado_cita(numero_cliente, estado)
            return (negocio["instrucciones_pago"]
                    + _txt_horario_lbtr()
                    + "\n\nEscribe *cancelar* si no desea continuar.")

        meet_link = _procesar_confirmacion(codigo, numero_cliente, estado, servicio, negocio, twilio_send)
        _del_estado_cita(numero_cliente)
        if meet_link:
            return _msg_confirmacion(estado, servicio, negocio)
        return _msg_confirmacion(estado, servicio, negocio)

    # ── ESPERANDO COMPROBANTE ──
    if s == "esperando_comprobante" and servicio:
        if not media_url:
            return (negocio.get("instrucciones_pago", "") +
                    "\n\nAun no hemos recibido tu comprobante. *Envia la foto* por este chat.")

        # Determinar monto esperado según tipo
        tipo_cita = estado.get("tipo")
        if tipo_cita == "online":
            monto_esp = negocio.get("costo_online") or servicio["precio"]
        else:
            monto_esp = negocio.get("costo_presencial") or servicio["precio"]

        # Validar con IA (en test_mode siempre válido)
        if negocio.get("test_mode"):
            valido, razon, es_mismo_banco = True, "Modo test — validación omitida", None
        else:
            valido, razon, es_mismo_banco = validar_comprobante(media_url, monto_esp)

        tipo_txt  = "Online (Google Meet)" if tipo_cita == "online" else "Presencial"
        lugar_txt = f"\nLugar:    {estado['lugar']}" if estado.get("lugar") else ""

        numero_corto = numero_cliente.replace("whatsapp:+", "")

        if valido is False:
            # Fraude detectado — notificar a Pilar y rechazar al cliente
            twilio_send(
                negocio["numero_negocio"],
                f"⚠️ COMPROBANTE SOSPECHOSO — NO CONFIRMADO\n\n"
                f"Servicio: {servicio['nombre']}\n"
                f"Tipo:     {tipo_txt}{lugar_txt}\n"
                f"Dia:      {estado['nombre_dia']} — {_fmt12(estado['hora'])}\n"
                f"Cliente:  {numero_cliente}\n"
                f"Razon IA: {razon}\n\n"
                f"Si crees que es un error escribe: confirmar pago {numero_corto}",
                media_url=media_url,
            )
            return (
                "No pudimos validar tu comprobante. Verifica que:\n\n"
                "• El monto sea correcto\n"
                "• La cuenta destino termine en *0083*\n"
                "• Sea LBTR si es de otro banco (NO ACH)\n"
                "• La transferencia este completada o en proceso\n\n"
                "Envia de nuevo la foto o contacta al negocio."
            )

        # Valido o no pudo verificar — Pilar confirma manualmente
        estado_ia = "✅ Validado por IA" if valido else "⚠️ No verificado por IA — revisa manualmente"
        estado["estado"] = "esperando_confirmacion_negocio"
        _set_estado_cita(numero_cliente, estado)

        aviso_lbtr = _aviso_lbtr(es_mismo_banco)
        aviso_txt  = f"\n\n{aviso_lbtr}" if aviso_lbtr else ""

        twilio_send(
            negocio["numero_negocio"],
            f"💰 PAGO RECIBIDO — {estado_ia}\n\n"
            f"Servicio: {servicio['nombre']}\n"
            f"Tipo:     {tipo_txt}{lugar_txt}\n"
            f"Dia:      {estado['nombre_dia']} — {_fmt12(estado['hora'])}\n"
            f"Cliente:  {numero_cliente}"
            f"{aviso_txt}\n\n"
            f"Escribe *confirmar pago {numero_corto}* para aprobar\n"
            f"o *rechazar pago {numero_corto}* si hay problema.",
            media_url=media_url,
        )
        return (
            f"✅ Comprobante recibido. Estamos verificando tu pago.{aviso_txt}\n\n"
            "Te confirmamos la cita en breve por este mismo chat."
        )

    return None


# ── Flujo negocio ─────────────────────────────────────────────────────────────

def manejar_negocio_citas(numero, mensaje, twilio_send,
                           iniciar_timer_relay=None, cancelar_timer_relay=None):
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

    # ── aprobar/rechazar reagendar / aprobar/rechazar reembolso ──
    m_apr = re.match(r"(aprobar|rechazar)\s+(reagendar|reembolso)\s+(\d+)", msg_low)
    if m_apr:
        decision, tipo_acc, num_corto = m_apr.group(1), m_apr.group(2), m_apr.group(3)
        num_cliente = f"whatsapp:+{num_corto}"

        if tipo_acc == "reagendar":
            if decision == "aprobar":
                _del_estado_cita(num_cliente)
                twilio_send(num_cliente,
                    f"✅ *{negocio['nombre']}* aprobó tu solicitud.\n\n"
                    f"Para elegir la nueva fecha escribe: *{negocio['codigo']}*")
                return f"Reagendar aprobado. El cliente recibirá nuevas opciones al escribir el código."
            else:
                conv_r = execute("SELECT * FROM conversaciones_citas WHERE numero_cliente=%s", (num_cliente,), fetch="one")
                cita_r = _get_cita_confirmada(num_cliente, codigo) if conv_r else None
                if cita_r:
                    horas_r = _horas_laborales_hasta(cita_r["fecha"], str(cita_r["hora"])[:5], negocio)
                    if horas_r >= HORAS_LABORALES_LIMITE:
                        execute("UPDATE conversaciones_citas SET estado='esperando_datos_reembolso' WHERE numero_cliente=%s", (num_cliente,))
                        twilio_send(num_cliente,
                            f"*{negocio['nombre']}* no puede reagendar tu cita.\n\n"
                            f"Como cancelaste con suficiente anticipacion tienes derecho a reembolso completo.\n\n"
                            f"Envia tus datos:\n*Banco:* [nombre]\n*Cuenta:* [numero]\n*Titular:* [nombre]")
                        return f"Reagendar rechazado. Cliente notificado — reembolso garantizado por politica."
                _del_estado_cita(num_cliente)
                twilio_send(num_cliente,
                    f"*{negocio['nombre']}* no puede reagendar en este momento. "
                    f"Tu cita original sigue vigente. Disculpa los inconvenientes.")
                return f"Reagendar rechazado. Cliente notificado — cita original vigente."

        else:  # reembolso
            if decision == "aprobar":
                execute("UPDATE conversaciones_citas SET estado='esperando_datos_reembolso' WHERE numero_cliente=%s", (num_cliente,))
                twilio_send(num_cliente,
                    f"✅ *{negocio['nombre']}* aprobó tu reembolso.\n\n"
                    f"Envia tus datos:\n*Banco:* [nombre]\n*Cuenta:* [numero]\n*Titular:* [nombre]")
                return f"Reembolso aprobado. Cliente enviará sus datos bancarios."
            else:
                _del_estado_cita(num_cliente)
                twilio_send(num_cliente,
                    f"*{negocio['nombre']}* no aprobó el reembolso en este caso. "
                    f"Ver politica: wasapeame.co/descargo")
                return f"Reembolso rechazado. Cliente notificado."

    # ── no show [número] — Pilar reporta cliente no-show ──
    m_ns = re.match(r"no\s+show\s+(\d+)$", msg_low)
    if m_ns:
        num_cliente = f"whatsapp:+{m_ns.group(1)}"
        cita = _get_cita_confirmada(num_cliente, codigo)
        if not cita:
            return f"No encontre una cita confirmada para ese número."

        # Validar tiempo mínimo de espera
        tipo_cita = cita.get("tipo")
        buffer_min = 5 if tipo_cita == "online" else 10
        h_c, m_c = map(int, str(cita["hora"])[:5].split(":"))
        cita_dt = datetime(cita["fecha"].year, cita["fecha"].month, cita["fecha"].day,
                           h_c, m_c, tzinfo=TZ_RD)
        ahora = datetime.now(TZ_RD)
        habilita_en = cita_dt + timedelta(minutes=buffer_min)
        if ahora < cita_dt:
            minutos = int((cita_dt - ahora).total_seconds() / 60)
            return f"La cita de este cliente es en {minutos} minutos. Aún no ha comenzado."
        if ahora < habilita_en:
            espera = int((habilita_en - ahora).total_seconds() / 60) + 1
            return (f"Dale {buffer_min} minutos de espera al cliente. "
                    f"Podrás reportar el no-show en {espera} minuto{'s' if espera > 1 else ''}.")

        execute("UPDATE citas SET no_show_cliente = TRUE WHERE id = %s", (cita["id"],))
        if execute("SELECT 1 FROM conversaciones_citas WHERE numero_cliente = %s", (num_cliente,), fetch="one"):
            execute("UPDATE conversaciones_citas SET estado = 'noshow_cliente_esperando' WHERE numero_cliente = %s", (num_cliente,))
        else:
            execute("INSERT INTO conversaciones_citas (numero_cliente, codigo, estado) VALUES (%s, %s, 'noshow_cliente_esperando')",
                    (num_cliente, codigo))
        twilio_send(
            num_cliente,
            f"*{negocio['nombre']}* reporta que no te presentaste a tu cita del "
            f"{cita['fecha']} a las {_fmt12(cita['hora'])}.\n\n"
            f"El pago realizado no es reembolsable en caso de ausencia del cliente.\n\n"
            f"¿Qué deseas hacer?\n"
            f"1. Solicitar reagendar (sujeto a aprobacion del negocio)\n"
            f"2. Cancelar la cita\n\n"
            f"wasapeame.co/descargo"
        )
        return f"Cliente {m_ns.group(1)} notificado. Espera su respuesta."

    # ── chat [número] — abrir relay (solo si hay pago pendiente de confirmar) ──
    m_chat = re.match(r"chat\s+(\d+)", msg_low)
    if m_chat:
        num_corto   = m_chat.group(1)
        num_cliente = f"whatsapp:+{num_corto}"
        conv_pago = execute(
            "SELECT 1 FROM conversaciones_citas WHERE numero_cliente = %s "
            "AND estado = 'esperando_confirmacion_negocio'",
            (num_cliente,), fetch="one"
        )
        if not conv_pago:
            return (f"No hay un pago pendiente de confirmacion para {num_corto}.\n"
                    f"El chat directo solo se activa cuando un cliente ya pago y necesitas coordinar con el.")
        _abrir_relay(num_cliente, numero, codigo)
        if iniciar_timer_relay:
            iniciar_timer_relay(num_cliente)
        twilio_send(
            num_cliente,
            f"*{negocio['nombre']}* quiere coordinarse contigo sobre tu cita. "
            f"Tienes *30 minutos* para responder.\n\n"
            f"Si no puedes ahora escribe *mas tarde* y te enviaremos opciones."
        )
        return (
            f"Sesion de chat con {num_corto} abierta. Tienes *30 minutos*.\n"
            f"Escribe con normalidad — el cliente recibira tus mensajes.\n"
            f"Cuando terminen escribe: *cerrar chat {num_corto}*"
        )

    # ── cerrar chat [número] — Pilar decide que pasó ──
    m_cerrar = re.match(r"cerrar\s+chat\s+(\d+)", msg_low)
    if m_cerrar:
        num_corto   = m_cerrar.group(1)
        num_cliente = f"whatsapp:+{num_corto}"
        relay = _get_relay(num_cliente)
        if not relay:
            return f"No hay una sesion de chat activa con {num_corto}."
        if cancelar_timer_relay:
            cancelar_timer_relay(num_cliente)
        _actualizar_relay(num_cliente, estado="cerrando")
        return (
            f"¿Que acordaron con {num_corto}?\n\n"
            f"1. Confirmar la cita\n"
            f"2. Cancelar + reembolso"
        )

    # ── respuesta de Pilar al cerrar chat (1 o 2) ──
    relays_cerrando = [r for r in _get_relay_por_negocio(numero) if r["estado"] == "cerrando"]
    if relays_cerrando and msg_low in ("1", "2"):
        relay = relays_cerrando[0]
        num_cliente = relay["numero_cliente"]
        num_corto   = num_cliente.replace("whatsapp:+", "")
        conv = execute(
            "SELECT * FROM conversaciones_citas WHERE numero_cliente = %s",
            (num_cliente,), fetch="one"
        )
        servicio_r = negocio.get("servicios", {}).get(conv["servicio_clave"]) if conv else None
        _cerrar_relay(num_cliente)

        if msg_low == "1":
            if conv and servicio_r:
                _procesar_confirmacion(codigo, num_cliente, conv, servicio_r, negocio, twilio_send)
            _del_estado_cita(num_cliente)
            twilio_send(num_cliente, _msg_confirmacion(conv, servicio_r or {}, negocio))
            return f"✅ Cita de {num_corto} confirmada. El cliente fue notificado."
        else:
            execute(
                "UPDATE conversaciones_citas SET estado = 'esperando_datos_reembolso' WHERE numero_cliente = %s",
                (num_cliente,)
            )
            twilio_send(
                num_cliente,
                "Lo sentimos, no fue posible mantener tu cita.\n\n"
                "Para procesar tu reembolso necesito tus datos bancarios.\n\n"
                "Escribe en este formato:\n"
                "*Banco:* [nombre del banco]\n"
                "*Cuenta:* [numero de cuenta]\n"
                "*Titular:* [tu nombre completo]"
            )
            return f"Solicitud de reembolso iniciada para {num_corto}. El cliente recibio las instrucciones."

    # ── comprobante reembolso [número] ──
    m_comp = re.match(r"comprobante\s+reembolso\s+(\d+)", msg_low)
    if m_comp:
        num_cliente = f"whatsapp:+{m_comp.group(1)}"
        if not media_url:
            return "Adjunta la foto del comprobante de reembolso con el mensaje."
        twilio_send(
            num_cliente,
            "✅ Tu reembolso fue procesado. Aquí está el comprobante de la transferencia.",
            media_url=media_url,
        )
        return f"Comprobante enviado al cliente."

    # ── confirmar pago / rechazar pago ──
    m_confirmar = re.match(r"confirmar\s+pago\s+(\d+)", msg_low)
    m_rechazar  = re.match(r"rechazar\s+pago\s+(\d+)", msg_low)
    if m_confirmar or m_rechazar:
        num_corto   = (m_confirmar or m_rechazar).group(1)
        num_cliente = f"whatsapp:+{num_corto}"
        conv = execute(
            "SELECT * FROM conversaciones_citas WHERE numero_cliente = %s AND estado = 'esperando_confirmacion_negocio'",
            (num_cliente,), fetch="one"
        )
        if not conv:
            return f"No encontre una cita pendiente de confirmacion para {num_corto}."

        if m_rechazar:
            twilio_send(num_cliente,
                "Tu pago no pudo ser verificado. Por favor contacta al negocio directamente.")
            _del_estado_cita(num_cliente)
            return f"Pago de {num_corto} rechazado. El cliente fue notificado."

        # Confirmar
        servicio_cita = negocio.get("servicios", {}).get(conv["servicio_clave"])
        if servicio_cita:
            _procesar_confirmacion(codigo, num_cliente, conv, servicio_cita, negocio, twilio_send)
        _del_estado_cita(num_cliente)
        twilio_send(num_cliente, _msg_confirmacion(conv, servicio_cita or {}, negocio))
        return f"✅ Cita de {num_corto} confirmada. El cliente fue notificado."

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
