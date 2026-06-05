import os
import re
import threading
import time
from flask import Flask, request, g, make_response, render_template
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from dotenv import load_dotenv
from db import init_pool, execute
from negocio_router import detectar_codigo, obtener_negocio, es_numero_negocio
from flujo_pedidos import manejar_pedido, manejar_negocio, tiene_flujo_activo, limpiar_flujo, cancelar_timeout
from flujo_citas import manejar_cita, manejar_negocio_citas, tiene_flujo_citas, tiene_sesion_admin_citas, iniciar_recordatorios
from asistente_ia import consultar_ia, respuesta_ayuda
from oauth_routes import oauth_bp

load_dotenv()
init_pool()

# Limpiar conversaciones huérfanas de reinicios anteriores
execute("DELETE FROM conversaciones_pedidos WHERE timeout_en < NOW()")

app = Flask(__name__)
app.register_blueprint(oauth_bp, url_prefix='/oauth')

@app.route("/ping")
def ping():
    return "¡El servidor está vivo!", 200


@app.after_request
def log_response(response):
    if response.content_type and "text" in response.content_type:
        numero = getattr(g, "numero_cliente", "?")
        print(f"[OUT] {numero}: {response.get_data(as_text=True)!r}")
    return response


ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

client = Client(ACCOUNT_SID, AUTH_TOKEN)


def twilio_send(to, body, media_url=None):
    kwargs = dict(body=body, from_=TWILIO_NUMBER, to=to)
    if media_url:
        kwargs["media_url"] = [media_url]
    client.messages.create(**kwargs)


timers           = {}
timers_relay     = {}
_clientes_vistos = set()

TIMEOUT_SEGUNDOS = 180
RELAY_SEGUNDOS   = 1800  # 30 minutos

_PATRONES_NEGOCIO = {
    "pedidos": [r"^no\s+hay\b", r"\blisto\b"],
    "citas":   [r"mis\s+citas\s+(hoy|semana)", r"ocupado\s+hasta\b",
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
    cancelar_timeout(numero_cliente, twilio_send)
    timers.pop(numero_cliente, None)
    try:
        client.messages.create(
            body=(
                "Tu sesión expiró por inactividad. Escribe *Hola* si quieres comunicarte con "
                "Wasapeame o el código del negocio para empezar de nuevo."
            ),
            from_=TWILIO_NUMBER,
            to=numero_cliente
        )
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
    from flujo_citas import cerrar_relay_timeout
    timers_relay.pop(numero_cliente, None)
    cerrar_relay_timeout(numero_cliente, twilio_send)


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


iniciar_recordatorios(twilio_send)

LIMITE_MSG = 1500

def _responder(twiml_msg, texto, numero):
    """Envía texto largo en partes; la última va por TwiML, las demás por twilio_send."""
    if len(texto) <= LIMITE_MSG:
        twiml_msg.body(texto)
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
    for parte in partes[:-1]:
        twilio_send(numero, parte)
        time.sleep(1)
    twiml_msg.body(partes[-1])


@app.route("/webhook", methods=["POST"])
def webhook():
    numero_cliente = request.form.get("From")
    # Normalizar mensaje: quitar asteriscos/guiones bajos de markdown, espacios raros
    _raw = request.form.get("Body", "")
    import unicodedata
    _raw = unicodedata.normalize("NFKC", _raw)          # normaliza unicode (ej: nbsp → space)
    _raw = re.sub(r"[*_~`]", "", _raw)                  # quita markdown de WhatsApp
    _raw = re.sub(r"\s+", " ", _raw).strip()            # colapsa espacios múltiples
    mensaje        = _raw
    mensaje_lower  = mensaje.lower().strip()
    media_url      = request.form.get("MediaUrl0")

    print(f"[IN]  {numero_cliente}: {mensaje!r}")
    g.numero_cliente = numero_cliente

    resp = MessagingResponse()
    msg  = resp.message()

    # ── RELAY: intercepción antes de cualquier otro routing ──
    from flujo_citas import manejar_relay_mensaje
    relay_resp = manejar_relay_mensaje(numero_cliente, mensaje, media_url, twilio_send,
                                       iniciar_timer_relay, cancelar_timer_relay)
    if relay_resp is not None:
        if relay_resp:
            msg.body(relay_resp)
        return str(resp)

    # ── MENSAJES DE NEGOCIOS DEL ROUTER ──
    codigo_emisor = es_numero_negocio(numero_cliente)
    print(f"[DEBUG negocio] numero={numero_cliente} → codigo={codigo_emisor}")
    if codigo_emisor:
        neg_emisor  = obtener_negocio(codigo_emisor)
        modo_emisor = neg_emisor.get("modo") if neg_emisor else "pedidos"
        codigo_en_msg, _ = detectar_codigo(mensaje)

        es_admin_pin_citas = (
            (re.match(r"^admin\s+", mensaje_lower) and modo_emisor != "citas")
            or (modo_emisor != "citas" and tiene_sesion_admin_citas(numero_cliente))
        )
        if not codigo_en_msg and _es_comando_negocio(mensaje_lower, modo_emisor) and not es_admin_pin_citas:
            if mensaje_lower.strip() == "ayuda":
                msg.body(respuesta_ayuda(modo_emisor))
                return str(resp)

            if modo_emisor == "citas":
                resultado = manejar_negocio_citas(numero_cliente, mensaje, twilio_send)
            else:
                resultado = manejar_negocio(numero_cliente, codigo_emisor, mensaje, twilio_send)

            if resultado is None:
                resultado = consultar_ia(codigo_emisor, modo_emisor, mensaje)

            if resultado:
                _responder(msg, resultado, numero_cliente)
            return str(resp)

    # ── ADMIN CITAS ──
    if re.match(r"^admin\s+", mensaje_lower) or tiene_sesion_admin_citas(numero_cliente):
        resultado = manejar_negocio_citas(numero_cliente, mensaje, twilio_send,
                                          iniciar_timer_relay=iniciar_timer_relay,
                                          cancelar_timer_relay=cancelar_timer_relay)
        if resultado:
            _responder(msg, resultado, numero_cliente)
        return str(resp)

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
            respuesta = manejar_cita(numero_cliente, codigo, msg_flujo, twilio_send, media_url=media_url)
        else:
            respuesta = manejar_pedido(numero_cliente, codigo, msg_flujo, twilio_send, media_url=media_url)
            if tiene_flujo_activo(numero_cliente):
                reiniciar_timer(numero_cliente)
            else:
                detener_timer(numero_cliente)
        if respuesta:
            _responder(msg, respuesta, numero_cliente)
        return str(resp)

    # ── SIN CÓDIGO ──
    if numero_cliente not in _clientes_vistos:
        _clientes_vistos.add(numero_cliente)
        msg.body(
            "Bienvenido a *Wasapeame* 👋\n\n"
            "Conecta con tu negocio favorito directo desde WhatsApp.\n"
            "🛒 Pedidos  |  📅 Citas  |  💬 Consultas\n\n"
            "🔑 Escribe el *código del negocio* para comenzar.\n"
            "📲 Si no lo tienes, pídelo al negocio."
        )
        return str(resp)

    msg.body("🔑 Escribe el *código del negocio* para continuar.\n📲 Si no lo tienes, pídelo al negocio.")
    return str(resp)


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
  <title>Política de Privacidad — Wasapeame</title>
  <link rel="icon" type="image/png" sizes="512x512" href="/static/favicon.png"/>
  <link rel="apple-touch-icon" sizes="180x180" href="/static/favicon.png"/>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"/>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root { --brand: #00C896; --bg: #080D0A; --surface: #111812; --text: #E8F5EE; --muted: #7A9A87; --border: rgba(0,200,150,0.12); }
    body { background: var(--bg); color: var(--text); font-family: "Plus Jakarta Sans", sans-serif; min-height: 100vh; }

    header {
      padding: 32px 40px;
      border-bottom: 1px solid var(--border);
      display: flex; align-items: center; justify-content: center;
    }
    header span { font-size: 18px; font-weight: 700; color: var(--text); letter-spacing: -0.3px; }

    main { max-width: 720px; margin: 0 auto; padding: 60px 40px 100px; }

    .badge {
      display: inline-block; background: rgba(0,200,150,0.1);
      border: 1px solid rgba(0,200,150,0.25); color: var(--brand);
      font-size: 12px; font-weight: 600; letter-spacing: 0.5px;
      padding: 4px 12px; border-radius: 50px; margin-bottom: 20px;
      text-transform: uppercase;
    }
    h1 { font-size: 36px; font-weight: 700; line-height: 1.2; letter-spacing: -0.5px; margin-bottom: 8px; }
    .updated { color: var(--muted); font-size: 14px; margin-bottom: 48px; }

    section { margin-bottom: 40px; }
    h2 {
      font-size: 14px; font-weight: 700; text-transform: uppercase;
      letter-spacing: 1px; color: var(--brand); margin-bottom: 12px;
    }
    p, li { font-size: 15px; line-height: 1.75; color: #B8D4C5; }
    ul { padding-left: 20px; display: flex; flex-direction: column; gap: 6px; }
    li::marker { color: var(--brand); }

    .card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 12px; padding: 20px 24px; margin-top: 12px;
    }
    .card p { margin: 0; }

    a { color: var(--brand); text-decoration: none; }
    a:hover { text-decoration: underline; }

    footer { text-align: center; padding: 32px 40px; color: var(--muted); font-size: 13px; border-top: 1px solid var(--border); }
  </style>
</head>
<body>
  <header>
    <img src="/static/wpicon.png" alt="Wasapeame" style="height:130px;width:auto;mix-blend-mode:lighten;"/>
  </header>

  <main>
    <div class="badge">Privacidad</div>
    <h1>Política de Privacidad</h1>
    <p class="updated">Última actualización: junio 2026</p>

    <section>
      <h2>¿Quiénes somos?</h2>
      <p>Wasapeame es una plataforma de asistente por WhatsApp para negocios locales en República Dominicana. Facilitamos la gestión de citas y pedidos a través de mensajería automatizada.</p>
    </section>

    <section>
      <h2>¿Qué datos recopilamos?</h2>
      <p>Cuando un negocio conecta su Google Calendar, recopilamos únicamente lo siguiente:</p>
      <ul>
        <li><strong>Nombre</strong> de la cuenta Google del dueño del negocio.</li>
        <li><strong>Correo electrónico</strong> asociado a la cuenta Google.</li>
        <li><strong>Tokens de acceso y actualización</strong> de Google Calendar, necesarios para crear eventos automáticamente.</li>
        <li><strong>Número de WhatsApp</strong> de los clientes que agendan citas a través del asistente.</li>
      </ul>
    </section>

    <section>
      <h2>¿Para qué usamos esos datos?</h2>
      <p>Exclusivamente para operar el asistente de citas:</p>
      <ul>
        <li>Crear eventos en el Google Calendar del negocio cuando un cliente agenda una cita.</li>
        <li>Generar recordatorios automáticos antes de cada cita.</li>
        <li>Generar enlaces de Google Meet para citas virtuales y enviarlos al cliente por WhatsApp.</li>
      </ul>
      <div class="card">
        <p>No utilizamos tus datos para publicidad, análisis de terceros, ni ningún otro propósito fuera de la operación del asistente.</p>
      </div>
    </section>

    <section>
      <h2>¿Compartimos tus datos?</h2>
      <p>No. Wasapeame <strong>no vende, no alquila ni comparte</strong> tus datos personales con terceros. Los únicos servicios externos que intervienen son Google Calendar (para agendar) y Twilio (para enviar mensajes de WhatsApp), ambos bajo sus propias políticas de privacidad.</p>
    </section>

    <section>
      <h2>¿Cómo puedes revocar el acceso?</h2>
      <p>Puedes desconectar Wasapeame de tu Google Calendar en cualquier momento:</p>
      <ul>
        <li>Ve a <a href="https://myaccount.google.com/permissions" target="_blank">myaccount.google.com/permissions</a>.</li>
        <li>Busca <strong>Wasapeame</strong> y selecciona <em>Quitar acceso</em>.</li>
      </ul>
      <p style="margin-top:12px;">Una vez revocado, el asistente dejará de crear eventos en tu Calendar. Las citas ya registradas no se eliminan.</p>
    </section>

    <section>
      <h2>Contacto</h2>
      <p>Para cualquier consulta sobre privacidad escríbenos a <a href="mailto:hola@wasapeame.co">hola@wasapeame.co</a>.</p>
    </section>
  </main>

  <footer>© 2026 Wasapeame · <a href="mailto:hola@wasapeame.co">hola@wasapeame.co</a></footer>
</body>
</html>"""
    return make_response(html, 200, {"Content-Type": "text/html; charset=utf-8"})


if __name__ == "__main__":
    app.run(debug=True, port=3000)
