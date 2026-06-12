import os
import re
import threading
import time
import unicodedata
import hmac
import hashlib
import requests as http_requests
from flask import Flask, request, g, make_response, render_template, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from db import init_pool, execute
from negocio_router import detectar_codigo, obtener_negocio, es_numero_negocio
from flujo_pedidos import manejar_pedido, manejar_negocio, tiene_flujo_activo, limpiar_flujo, cancelar_timeout
from flujo_citas import (manejar_cita, manejar_negocio_citas, tiene_flujo_citas,
                         tiene_sesion_admin_citas, iniciar_recordatorios,
                         manejar_relay_mensaje, cerrar_relay_timeout,
                         manejar_flow_cita)
from flujo_registro import manejar_registro, iniciar_registro, tiene_flujo_registro
from asistente_ia import consultar_ia, respuesta_ayuda
from maverick import iniciar_maverick
from agente_atlas import iniciar_atlas
from transcripcion_medica import procesar_nota_voz_medica
from flujo_citas import guardar_transcripcion_pendiente
from oauth_routes import oauth_bp
from admin_routes import admin_bp
from cliente_routes import cliente_bp

load_dotenv()
init_pool()

try:
    execute("DELETE FROM conversaciones_pedidos WHERE timeout_en < NOW()")
except:
    pass
try:
    execute("CREATE TABLE IF NOT EXISTS clientes_vistos (numero TEXT PRIMARY KEY)")
    execute("CREATE TABLE IF NOT EXISTS clientes (numero TEXT PRIMARY KEY, nombre TEXT, email TEXT)")
    execute("ALTER TABLE conversaciones_registro ADD COLUMN IF NOT EXISTS datos JSONB NOT NULL DEFAULT '{}'")
except:
    pass

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-prod")
limiter = Limiter(app=app, key_func=get_remote_address, default_limits=["200 per day", "50 per hour"])
app.register_blueprint(oauth_bp, url_prefix='/oauth')
app.register_blueprint(admin_bp)
app.register_blueprint(cliente_bp)


@app.route("/ping")
def ping():
    return "¡El servidor está vivo!", 200


@app.after_request
def log_response(response):
    numero = getattr(g, "numero_cliente", "?")
    if numero != "?":
        print(f"[OUT] {numero}: responded")
    return response


META_ACCESS_TOKEN    = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
META_VERIFY_TOKEN    = os.getenv("META_VERIFY_TOKEN", "wasapeame_verify_2026")


def meta_send(to, body, media_id=None, media_type="image"):
    phone = to.replace("whatsapp:+", "").replace("+", "").strip()
    url = f"https://graph.facebook.com/v19.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    if media_id and media_type == "audio":
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "audio",
            "audio": {"id": media_id}
        }
    elif media_id:
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "image",
            "image": {"id": media_id, "caption": body or ""}
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": body, "preview_url": False}
        }
    try:
        resp = http_requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[META] Error enviando a {to}: {e}")



timers       = {}
timers_relay = {}


def _ya_conocido(numero):
    if execute("SELECT 1 FROM clientes_vistos WHERE numero = %s", (numero,), fetch="one"):
        return True
    execute("INSERT INTO clientes_vistos (numero) VALUES (%s) ON CONFLICT DO NOTHING", (numero,))
    return False

TIMEOUT_SEGUNDOS = 180
RELAY_SEGUNDOS   = 1800

_PATRONES_NEGOCIO = {
    "pedidos": [r"^no\s+hay\b", r"\blisto\b"],
    "citas":   [r"mis\s+citas", r"ocupado\s+hasta\b",
                r"\bno\s+disponible\b", r"\blibre\s+\w+",
                r"^cancelar\s+cita\b", r"^cancelar\s+\d{4}",
                r"^confirmar\s+pago\b", r"^rechazar\s+pago\b",
                r"^chat\s+\d+", r"^cerrar\s+chat\s+\d+",
                r"^comprobante\s+reembolso\s+\d+",
                r"^no\s+show\s+\d+",
                r"^(aprobar|rechazar)\s+(reagendar|reembolso)\s+\d+"],
    "comun":   [r"^ayuda$", r"^admin\s+"],
}


def _es_comando_negocio(msg_lower, modo):
    patrones = _PATRONES_NEGOCIO.get(modo, []) + _PATRONES_NEGOCIO["comun"]
    return any(re.search(p, msg_lower) for p in patrones)


def detener_timer(numero_cliente):
    if numero_cliente in timers:
        timers[numero_cliente].cancel()
        del timers[numero_cliente]


def cancelar_por_timeout(numero_cliente):
    cancelar_timeout(numero_cliente, meta_send)
    timers.pop(numero_cliente, None)
    try:
        meta_send(numero_cliente,
            "Tu sesión expiró por inactividad. Escribe *Hola* si quieres comunicarte con "
            "Wappi o el código del negocio para empezar de nuevo.")
    except Exception:
        pass


def reiniciar_timer(numero_cliente):
    if numero_cliente in timers:
        timers[numero_cliente].cancel()
    timer = threading.Timer(TIMEOUT_SEGUNDOS, cancelar_por_timeout, args=[numero_cliente])
    timer.daemon = True
    timer.start()
    timers[numero_cliente] = timer


def cerrar_relay_por_timeout(numero_cliente):
    timers_relay.pop(numero_cliente, None)
    cerrar_relay_timeout(numero_cliente, meta_send)


def iniciar_timer_relay(numero_cliente):
    if numero_cliente in timers_relay:
        timers_relay[numero_cliente].cancel()
    t = threading.Timer(RELAY_SEGUNDOS, cerrar_relay_por_timeout, args=[numero_cliente])
    t.daemon = True
    t.start()
    timers_relay[numero_cliente] = t


def cancelar_timer_relay(numero_cliente):
    if numero_cliente in timers_relay:
        timers_relay[numero_cliente].cancel()
        timers_relay.pop(numero_cliente, None)


iniciar_recordatorios(meta_send)
iniciar_maverick()
iniciar_atlas()

LIMITE_MSG = 1500


def _msg_perdido():
    return (
        "No entendí tu mensaje. ¿Qué necesitas?\n\n"
        "1. Hacer un pedido\n"
        "2. Agendar una cita\n"
        "3. Hablar con un negocio\n"
        "4. Registrar mi negocio\n\n"
        "Escribe el número de tu opción o el *código del negocio* directamente."
    )


def _enviar(texto, numero):
    """Envía texto largo en partes via meta_send."""
    if len(texto) <= LIMITE_MSG:
        meta_send(numero, texto)
        return
    partes, actual = [], ""
    for linea in texto.split("\n"):
        candidato = actual + ("\n" if actual else "") + linea
        if len(candidato) > LIMITE_MSG and actual:
            partes.append(actual)
            actual = linea
        else:
            actual = candidato
    if actual:
        partes.append(actual)
    for parte in partes:
        meta_send(numero, parte)
        time.sleep(1)


# ── ÚNICO webhook ──────────────────────────────────────────────────────────────
def _validate_webhook_signature(request_body, x_hub_signature):
    """Valida que el request venga de Meta verificando la firma HMAC."""
    if not x_hub_signature:
        print("[SECURITY] Webhook sin X-Hub-Signature-256 header — rechazado")
        return False

    webhook_secret = os.getenv("META_WEBHOOK_SECRET", "wasapeame_webhook_secret_2026")
    expected_signature = "sha256=" + hmac.new(
        webhook_secret.encode(),
        request_body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(x_hub_signature, expected_signature):
        print(f"[SECURITY] Webhook signature inválida — rechazado")
        return False

    return True


@app.route("/webhook", methods=["GET", "POST"])
@limiter.limit("10 per second")
def webhook():
    # Verificación del webhook de Meta
    if request.method == "GET":
        if request.args.get("hub.verify_token") == META_VERIFY_TOKEN:
            return request.args.get("hub.challenge"), 200
        return "Token incorrecto", 403

    # POST — procesar mensaje entrante
    try:
        # Validar firma HMAC del webhook
        x_hub_signature = request.headers.get("X-Hub-Signature-256", "")
        if not _validate_webhook_signature(request.data, x_hub_signature):
            return jsonify({"status": "invalid signature"}), 403

        data = request.get_json(silent=True) or {}

        # Extraer mensaje de la estructura de Meta Cloud API
        is_interactive_id = False
        try:
            entry   = data["entry"][0]
            changes = entry["changes"][0]["value"]
            msgs    = changes.get("messages", [])
            if not msgs:
                return jsonify({"status": "ok"}), 200
            msg_obj        = msgs[0]
            numero_cliente = "+" + msg_obj["from"]
            msg_type       = msg_obj.get("type", "text")

            if msg_type == "text":
                body_raw = msg_obj["text"]["body"]
                media_id = None
            elif msg_type == "image":
                media_id = msg_obj["image"]["id"]
                body_raw = msg_obj["image"].get("caption", "")
            elif msg_type == "audio":
                media_id = msg_obj["audio"]["id"]
                body_raw = "__audio__"
            elif msg_type == "interactive":
                interactive_obj = msg_obj.get("interactive", {})
                itype = interactive_obj.get("type")
                if itype == "nfm_reply":
                    import json as _json
                    try:
                        flow_data = _json.loads(interactive_obj["nfm_reply"]["response_json"])
                    except (KeyError, ValueError):
                        return jsonify({"status": "ok"}), 200
                    resp = manejar_flow_cita(numero_cliente, flow_data, meta_send)
                    if resp:
                        _enviar(resp, numero_cliente)
                    return jsonify({"status": "ok"}), 200
                elif itype == "button_reply":
                    body_raw = interactive_obj["button_reply"]["id"]
                    media_id = None
                    is_interactive_id = True
                elif itype == "list_reply":
                    body_raw = interactive_obj["list_reply"]["id"]
                    media_id = None
                    is_interactive_id = True
                else:
                    return jsonify({"status": "ok"}), 200
            else:
                # Tipo no soportado (sticker, etc.) — ignorar silenciosamente
                return jsonify({"status": "ok"}), 200
        except (KeyError, IndexError):
            return jsonify({"status": "ok"}), 200

        # Normalizar texto (IDs de interactivos no se sanitizan — contienen guiones bajos)
        _raw          = unicodedata.normalize("NFKC", body_raw)
        if not is_interactive_id:
            _raw      = re.sub(r"[*_~`]", "", _raw)
        _raw          = re.sub(r"\s+", " ", _raw).strip()
        mensaje       = _raw
        mensaje_lower = mensaje.lower().strip()

        print(f"[IN]  {numero_cliente}: {mensaje!r}")
        g.numero_cliente = numero_cliente

        # ── RELAY ──
        relay_resp = manejar_relay_mensaje(numero_cliente, mensaje, media_id, meta_send,
                                           iniciar_timer_relay, cancelar_timer_relay)
        if relay_resp is not None:
            if relay_resp:
                meta_send(numero_cliente, relay_resp)
            return jsonify({"status": "ok"}), 200

        # ── MENSAJES DE NEGOCIOS DEL ROUTER ──
        codigo_emisor = es_numero_negocio(numero_cliente)
        if codigo_emisor:
            neg_emisor  = obtener_negocio(codigo_emisor)
            modo_emisor = neg_emisor.get("modo") if neg_emisor else "pedidos"
            codigo_en_msg, _ = detectar_codigo(mensaje)

            es_admin_pin_citas = (
                (re.match(r"^admin\s+", mensaje_lower) and modo_emisor != "citas")
                or (modo_emisor != "citas" and tiene_sesion_admin_citas(numero_cliente))
            )
            # Audio de médico → transcripción + historia clínica
            if modo_emisor == "citas" and mensaje == "__audio__" and media_id:
                resultado = procesar_nota_voz_medica(media_id, neg_emisor, meta_send, numero_cliente)
                if resultado:
                    _enviar(resultado, numero_cliente)
                    guardar_transcripcion_pendiente(numero_cliente, resultado)
                    meta_send(numero_cliente, "¿Enviar esto a un paciente? Escribe su numero (ej: 8091234567) o *no*.")
                return jsonify({"status": "ok"}), 200

            if not codigo_en_msg and _es_comando_negocio(mensaje_lower, modo_emisor) and not es_admin_pin_citas:
                if mensaje_lower.strip() == "ayuda":
                    meta_send(numero_cliente, respuesta_ayuda(modo_emisor))
                    return jsonify({"status": "ok"}), 200

                if modo_emisor == "citas":
                    resultado = manejar_negocio_citas(numero_cliente, mensaje, meta_send)
                else:
                    resultado = manejar_negocio(numero_cliente, codigo_emisor, mensaje, meta_send)

                if resultado is None:
                    resultado = consultar_ia(codigo_emisor, modo_emisor, mensaje)

                if resultado:
                    _enviar(resultado, numero_cliente)
                return jsonify({"status": "ok"}), 200

        # ── ADMIN CITAS ──
        if re.match(r"^admin\s+", mensaje_lower) or tiene_sesion_admin_citas(numero_cliente):
            if mensaje_lower.strip() == "ayuda":
                meta_send(numero_cliente, respuesta_ayuda("citas"))
                return jsonify({"status": "ok"}), 200
            resultado = manejar_negocio_citas(numero_cliente, mensaje, meta_send,
                                              media_id=media_id,
                                              iniciar_timer_relay=iniciar_timer_relay,
                                              cancelar_timer_relay=cancelar_timer_relay)
            if resultado:
                _enviar(resultado, numero_cliente)
            return jsonify({"status": "ok"}), 200

        # ── FLUJO REGISTRO ──
        if tiene_flujo_registro(numero_cliente):
            resultado = manejar_registro(numero_cliente, mensaje, meta_send)
            if resultado:
                meta_send(numero_cliente, resultado)
            return jsonify({"status": "ok"}), 200

        # ── ROUTER DE NEGOCIOS (clientes) ──
        codigo, resto = detectar_codigo(mensaje)
        if codigo or tiene_flujo_activo(numero_cliente) or tiene_flujo_citas(numero_cliente):
            msg_flujo = resto if codigo else mensaje
            if codigo:
                neg_tmp = obtener_negocio(codigo)
                modo = neg_tmp.get("modo") if neg_tmp else "pedidos"
            else:
                modo = "citas" if tiene_flujo_citas(numero_cliente) else "pedidos"

            if modo == "citas":
                respuesta = manejar_cita(numero_cliente, codigo, msg_flujo, meta_send, media_id=media_id)
            else:
                respuesta = manejar_pedido(numero_cliente, codigo, msg_flujo, meta_send, media_id=media_id)
                if tiene_flujo_activo(numero_cliente):
                    reiniciar_timer(numero_cliente)
                else:
                    detener_timer(numero_cliente)

            if respuesta:
                _enviar(respuesta, numero_cliente)
            elif respuesta is not None:
                meta_send(numero_cliente, _msg_perdido())
            return jsonify({"status": "ok"}), 200

        # ── SIN CÓDIGO ──
        if not _ya_conocido(numero_cliente):
            meta_send(numero_cliente,
                "Bienvenido a *Wappi* 👋\n\n"
                "Conecta con tu negocio favorito directo desde WhatsApp.\n"
                "🛒 Pedidos  |  📅 Citas  |  💬 Consultas\n\n"
                "🔑 Escribe el *código del negocio* para comenzar.\n"
                "📲 Si no lo tienes, pídelo al negocio.\n\n"
                "¿Tienes un negocio y quieres unirte?\n"
                "Escribe *4* para registrarte.")
            return jsonify({"status": "ok"}), 200

        # Respuestas al menú de orientación
        if mensaje_lower == "1":
            meta_send(numero_cliente, "Para hacer un pedido escribe el *código del negocio*.\nEjemplo: *CO1*\n\n📲 Si no lo tienes, pídelo al negocio.")
            return jsonify({"status": "ok"}), 200
        if mensaje_lower == "2":
            meta_send(numero_cliente, "Para agendar una cita escribe el *código del negocio*.\nEjemplo: *SE1*\n\n📲 Si no lo tienes, pídelo al negocio.")
            return jsonify({"status": "ok"}), 200
        if mensaje_lower == "3":
            meta_send(numero_cliente, "Escribe el *código del negocio* para conectarte.\n📲 Si no lo tienes, pídelo directamente al negocio.")
            return jsonify({"status": "ok"}), 200
        if mensaje_lower == "4":
            resultado = iniciar_registro(numero_cliente, meta_send)
            meta_send(numero_cliente, resultado)
            return jsonify({"status": "ok"}), 200

        meta_send(numero_cliente, _msg_perdido())
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[ERROR] webhook: {e}")
        return jsonify({"status": "error", "message": "Fallo en procesamiento"}), 200


# ── Rutas web ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/descargo")
def descargo():
    return render_template("descargo.html")


@app.route("/privacy")
@app.route("/privacy.html")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
@app.route("/terms.html")
def terms():
    return render_template("terms.html")


@app.route("/googlee98445ec59f6ead6.html", methods=["GET"])
def google_verification():
    return make_response(
        "google-site-verification: googlee98445ec59f6ead6.html",
        200,
        {"Content-Type": "text/html; charset=utf-8"},
    )


@app.route("/privacidad", methods=["GET"])
def privacidad():
    html = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Política de Privacidad — Wappi</title>
  <link rel="icon" type="image/png" sizes="512x512" href="/static/favicon.png"/>
  <link rel="apple-touch-icon" sizes="180x180" href="/static/favicon.png"/>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"/>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root { --brand: #00C896; --bg: #080D0A; --surface: #111812; --text: #E8F5EE; --muted: #7A9A87; --border: rgba(0,200,150,0.12); }
    body { background: var(--bg); color: var(--text); font-family: "Plus Jakarta Sans", sans-serif; min-height: 100vh; }
    header { padding: 32px 40px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: center; }
    header span { font-size: 18px; font-weight: 700; color: var(--text); letter-spacing: -0.3px; }
    main { max-width: 720px; margin: 0 auto; padding: 60px 40px 100px; }
    .badge { display: inline-block; background: rgba(0,200,150,0.1); border: 1px solid rgba(0,200,150,0.25); color: var(--brand); font-size: 12px; font-weight: 600; letter-spacing: 0.5px; padding: 4px 12px; border-radius: 50px; margin-bottom: 20px; text-transform: uppercase; }
    h1 { font-size: 36px; font-weight: 700; line-height: 1.2; letter-spacing: -0.5px; margin-bottom: 8px; }
    .updated { color: var(--muted); font-size: 14px; margin-bottom: 48px; }
    section { margin-bottom: 40px; }
    h2 { font-size: 14px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--brand); margin-bottom: 12px; }
    p, li { font-size: 15px; line-height: 1.75; color: #B8D4C5; }
    ul { padding-left: 20px; display: flex; flex-direction: column; gap: 6px; }
    li::marker { color: var(--brand); }
    .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px 24px; margin-top: 12px; }
    .card p { margin: 0; }
    a { color: var(--brand); text-decoration: none; }
    a:hover { text-decoration: underline; }
    footer { text-align: center; padding: 32px 40px; color: var(--muted); font-size: 13px; border-top: 1px solid var(--border); }
  </style>
</head>
<body>
  <header>
    <img src="/static/wpicon.png" alt="Wappi" style="height:130px;width:auto;mix-blend-mode:lighten;"/>
  </header>
  <main>
    <div class="badge">Privacidad</div>
    <h1>Política de Privacidad</h1>
    <p class="updated">Última actualización: junio 2026</p>
    <section>
      <h2>¿Quiénes somos?</h2>
      <p>Wappi es una plataforma de asistente por WhatsApp para negocios locales en República Dominicana. Facilitamos la gestión de citas y pedidos a través de mensajería automatizada.</p>
    </section>
    <section>
      <h2>¿Qué datos recopilamos?</h2>
      <ul>
        <li><strong>Nombre</strong> de la cuenta Google del dueño del negocio.</li>
        <li><strong>Correo electrónico</strong> asociado a la cuenta Google.</li>
        <li><strong>Tokens de acceso y actualización</strong> de Google Calendar.</li>
        <li><strong>Número de WhatsApp</strong> de los clientes que agendan citas.</li>
      </ul>
    </section>
    <section>
      <h2>¿Para qué usamos esos datos?</h2>
      <ul>
        <li>Crear eventos en el Google Calendar del negocio.</li>
        <li>Generar recordatorios automáticos antes de cada cita.</li>
        <li>Generar enlaces de Google Meet para citas virtuales.</li>
      </ul>
      <div class="card"><p>No utilizamos tus datos para publicidad ni análisis de terceros.</p></div>
    </section>
    <section>
      <h2>¿Compartimos tus datos?</h2>
      <p>No. Wappi no vende ni comparte tus datos. Los únicos servicios externos son Google Calendar y la API de WhatsApp Business de Meta.</p>
    </section>
    <section>
      <h2>¿Cómo puedes revocar el acceso?</h2>
      <ul>
        <li>Ve a <a href="https://myaccount.google.com/permissions" target="_blank">myaccount.google.com/permissions</a>.</li>
        <li>Busca <strong>Wappi</strong> y selecciona <em>Quitar acceso</em>.</li>
      </ul>
    </section>
    <section>
      <h2>Contacto</h2>
      <p>Escríbenos a <a href="mailto:hola@wasapeame.co">hola@wasapeame.co</a>.</p>
    </section>
  </main>
  <footer>© 2026 Wappi · <a href="mailto:hola@wasapeame.co">hola@wasapeame.co</a></footer>
</body>
</html>"""
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


AGENTS_PIN = os.getenv("AGENTS_PIN", "wasapeame2026")


@app.route("/agents")
def agents_dashboard():
    return render_template("agents.html")


@app.route("/agents/api/logs")
def agents_api_logs():
    pin = request.args.get("pin", "")
    if pin != AGENTS_PIN:
        return jsonify({"error": "unauthorized"}), 403

    filtro = request.args.get("filtro", "todos")
    if filtro == "maverick":
        rows = execute("SELECT * FROM agentes_log WHERE agente = 'maverick' ORDER BY creado_en DESC LIMIT 100", fetch="all") or []
    elif filtro == "indiana":
        rows = execute("SELECT * FROM agentes_log WHERE agente = 'indiana' ORDER BY creado_en DESC LIMIT 100", fetch="all") or []
    elif filtro == "sin_resolver":
        rows = execute("SELECT * FROM agentes_log WHERE resuelto = FALSE ORDER BY creado_en DESC LIMIT 100", fetch="all") or []
    else:
        rows = execute("SELECT * FROM agentes_log ORDER BY creado_en DESC LIMIT 100", fetch="all") or []

    logs = []
    for r in rows:
        logs.append({
            "id": r["id"],
            "agente": r["agente"],
            "tipo": r["tipo"],
            "descripcion": r["descripcion"],
            "resuelto": r["resuelto"],
            "creado_en": r["creado_en"].isoformat() if r["creado_en"] else None,
        })
    return jsonify({"logs": logs})


@app.route("/agents/api/limpiar", methods=["POST"])
def agents_api_limpiar():
    pin = request.args.get("pin", "")
    if pin != AGENTS_PIN:
        return jsonify({"error": "unauthorized"}), 403
    execute("DELETE FROM conversaciones_citas")
    execute("DELETE FROM conversaciones_pedidos")
    execute("DELETE FROM citas")
    from maverick import _log
    _log("maverick", "limpieza_manual", "Limpieza manual desde dashboard — citas y conversaciones borradas", resuelto=True)
    return jsonify({"mensaje": "Listo — todo limpiado"})


if __name__ == "__main__":
    app.run(debug=True, port=3000)
