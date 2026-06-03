import os
import re
import threading
from flask import Flask, request, g, make_response
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
_clientes_vistos = set()

TIMEOUT_SEGUNDOS = 180

_PATRONES_NEGOCIO = {
    "pedidos": [r"^no\s+hay\b", r"\blisto\b"],
    "citas":   [r"mis\s+citas\s+(hoy|semana)", r"ocupado\s+hasta\b",
                r"\bno\s+disponible\b", r"\blibre\s+\w+",
                r"^cancelar\s+cita\b", r"^cancelar\s+\d{4}"],
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


iniciar_recordatorios(twilio_send)


@app.route("/webhook", methods=["POST"])
def webhook():
    numero_cliente = request.form.get("From")
    mensaje        = request.form.get("Body", "").strip()
    mensaje_lower  = mensaje.lower().strip()
    media_url      = request.form.get("MediaUrl0")

    print(f"[IN]  {numero_cliente}: {mensaje!r}")
    g.numero_cliente = numero_cliente

    resp = MessagingResponse()
    msg  = resp.message()

    # ── MENSAJES DE NEGOCIOS DEL ROUTER ──
    codigo_emisor = es_numero_negocio(numero_cliente)
    print(f"[DEBUG negocio] numero={numero_cliente} → codigo={codigo_emisor}")
    if codigo_emisor:
        neg_emisor  = obtener_negocio(codigo_emisor)
        modo_emisor = neg_emisor.get("modo") if neg_emisor else "pedidos"
        codigo_en_msg, _ = detectar_codigo(mensaje)

        if not codigo_en_msg and _es_comando_negocio(mensaje_lower, modo_emisor):
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
                msg.body(resultado)
            return str(resp)

    # ── ADMIN CITAS ──
    if re.match(r"^admin\s+", mensaje_lower) or tiene_sesion_admin_citas(numero_cliente):
        resultado = manejar_negocio_citas(numero_cliente, mensaje, twilio_send)
        if resultado:
            msg.body(resultado)
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
            respuesta = manejar_cita(numero_cliente, codigo, msg_flujo, twilio_send)
        else:
            respuesta = manejar_pedido(numero_cliente, codigo, msg_flujo, twilio_send, media_url=media_url)
            if tiene_flujo_activo(numero_cliente):
                reiniciar_timer(numero_cliente)
            else:
                detener_timer(numero_cliente)
        if respuesta:
            msg.body(respuesta)
        return str(resp)

    # ── SIN CÓDIGO ──
    if numero_cliente not in _clientes_vistos:
        _clientes_vistos.add(numero_cliente)
        msg.body(
            "Bienvenido a *Wasapeame!* 🌿\n\n"
            "Somos la plataforma de WhatsApp para negocios locales — pedidos, citas y más.\n\n"
            "Para continuar, escribe el *código del negocio* al inicio de tu mensaje.\n\n"
            "Ejemplo: escribe *CO1 hola* para conectarte con un negocio.\n\n"
            "Si no tienes el código, pídelo directamente al negocio."
        )
        return str(resp)

    msg.body("Escribe el *código del negocio* para continuar. Si no lo tienes, pídelo al negocio.")
    return str(resp)


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
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"/>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root { --brand: #00C896; --bg: #080D0A; --surface: #111812; --text: #E8F5EE; --muted: #7A9A87; --border: rgba(0,200,150,0.12); }
    body { background: var(--bg); color: var(--text); font-family: "Plus Jakarta Sans", sans-serif; min-height: 100vh; }

    header {
      padding: 24px 40px;
      border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 12px;
    }
    header .logo-mark {
      width: 36px; height: 36px; border-radius: 10px;
      background: var(--brand); display: flex; align-items: center; justify-content: center;
    }
    header .logo-mark svg { width: 20px; height: 20px; fill: #080D0A; }
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
    <div class="logo-mark">
      <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2C6.477 2 2 6.477 2 12c0 1.89.525 3.66 1.438 5.168L2 22l4.978-1.42A9.953 9.953 0 0 0 12 22c5.523 0 10-4.477 10-10S17.523 2 12 2Zm0 18a7.95 7.95 0 0 1-4.073-1.117l-.292-.174-3.024.863.875-2.941-.19-.302A7.95 7.95 0 0 1 4 12c0-4.418 3.582-8 8-8s8 3.582 8 8-3.582 8-8 8Zm4.406-5.844c-.242-.121-1.432-.707-1.654-.787-.221-.08-.382-.121-.543.121-.16.242-.623.787-.764.948-.14.16-.281.181-.523.06-.242-.12-1.022-.376-1.947-1.2-.72-.64-1.206-1.43-1.347-1.671-.14-.242-.015-.373.106-.493.108-.108.242-.282.363-.423.12-.14.16-.242.242-.403.08-.16.04-.302-.02-.423-.06-.12-.543-1.31-.744-1.793-.196-.47-.396-.406-.543-.414l-.463-.008c-.16 0-.422.06-.643.302-.221.242-.845.826-.845 2.015 0 1.19.865 2.34.986 2.501.12.16 1.703 2.6 4.127 3.645.577.249 1.027.398 1.378.51.579.184 1.107.158 1.524.096.465-.07 1.432-.585 1.634-1.15.2-.564.2-1.047.14-1.148-.06-.1-.221-.16-.463-.282Z"/>
      </svg>
    </div>
    <span>Wasapeame</span>
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
