import os
import base64
from datetime import date

import anthropic
import requests

from db import execute

_COMANDOS = {
    "pedidos": (
        "no hay [producto] — avisar al cliente que ese producto no esta disponible\n"
        "listo — marcar el pedido actual como despachado"
    ),
    "citas": (
        "📅 *mis citas hoy*\n"
        "Ver tus citas de hoy\n\n"
        "📅 *mis citas semana*\n"
        "Ver citas de los proximos 7 dias\n\n"
        "🔒 *ocupado hasta [hora]*\n"
        "Bloquear agenda hasta esa hora\n"
        "Ej: _ocupado hasta 3pm_\n\n"
        "🔒 *no disponible*\n"
        "Bloquear el resto del dia\n\n"
        "🔒 *libre [dia]*\n"
        "Bloquear un dia completo\n"
        "Ej: _libre lunes_\n\n"
        "✅ *confirmar pago [numero]*\n"
        "Aprobar cita tras revisar el comprobante\n"
        "Ej: _confirmar pago 18091234567_\n\n"
        "❌ *rechazar pago [numero]*\n"
        "Rechazar comprobante invalido\n"
        "Ej: _rechazar pago 18091234567_\n\n"
        "💬 *chat [numero]*\n"
        "Canal directo con un cliente que pago pero hay conflicto de agenda (30 min)\n"
        "Ej: _chat 18091234567_\n\n"
        "🔚 *cerrar chat [numero]*\n"
        "Cerrar el canal cuando llegaron a un acuerdo\n"
        "Ej: _cerrar chat 18091234567_\n\n"
        "🧾 *comprobante reembolso [numero]*\n"
        "Enviar comprobante de devolucion al cliente (adjunta la foto)\n"
        "Ej: _comprobante reembolso 18091234567_"
    ),
}

_LIMITE_MENSUAL = 50


def respuesta_ayuda(modo):
    cmds = _COMANDOS.get(modo, _COMANDOS["pedidos"])
    return f"Comandos disponibles:\n\n{cmds}\n\nEscribe ayuda en cualquier momento para ver esto de nuevo."


def consultar_ia(codigo, modo, mensaje):
    mes_actual = date.today().strftime("%Y-%m")

    row = execute("""
        INSERT INTO consultas_ia (codigo, mes, count) VALUES (%s, %s, 1)
        ON CONFLICT (codigo, mes) DO UPDATE
            SET count = consultas_ia.count + 1
            WHERE consultas_ia.count < %s
        RETURNING count
    """, (codigo, mes_actual, _LIMITE_MENSUAL), fetch="one")

    if not row:
        return "Has usado tus consultas de ayuda este mes. Escribe ayuda para ver los comandos disponibles."

    cmds = _COMANDOS.get(modo, _COMANDOS["pedidos"])
    try:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=(
                "Eres el asistente de Wasapeame. El negocio escribio algo que no es un comando valido.\n"
                f"Comandos disponibles:\n{cmds}\n"
                "Responde en maximo 2 lineas. Tono amable y profesional, espanol neutro informal. "
                "Sin terminos de genero ni expresiones coloquiales. "
                "Ejemplo: 'Ese comando no esta disponible. Puedes usar: mis citas hoy, ocupado hasta [hora]...'"
            ),
            messages=[{"role": "user", "content": mensaje}],
        )
        return response.content[0].text
    except Exception:
        return "No pude procesar tu mensaje. Escribe ayuda para ver los comandos disponibles."


def validar_comprobante(media_url, monto_esperado, cuenta_ultimos4="0083"):
    """
    Analiza un comprobante de pago con Claude Vision.
    Retorna (valido: bool, razon: str).
    """
    try:
        # Descargar imagen desde Twilio (requiere auth básica)
        resp = requests.get(
            media_url,
            auth=(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")),
            timeout=15,
        )
        resp.raise_for_status()
        img_b64   = base64.standard_b64encode(resp.content).decode()
        mime_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]

        hoy = date.today().strftime("%d de %B de %Y").lower()

        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": mime_type, "data": img_b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Analiza este comprobante de transferencia bancaria dominicana.\n\n"
                            f"Puede ser de cualquiera de estos bancos:\n"
                            f"- Banreservas: dice 'TRANSACCION PROCESADA', campo Destino muestra 'DOP *{cuenta_ultimos4}'\n"
                            f"- BHD: dice 'Transaccion completada', campo Destino muestra nombre y termina en '{cuenta_ultimos4}'\n"
                            f"- Popular: dice 'Tu transferencia ha sido realizada', campo Beneficiario muestra la cuenta completa terminando en '{cuenta_ultimos4}'\n\n"
                            f"Verifica TODOS estos criterios:\n"
                            f"1. El estado indica transaccion COMPLETADA/PROCESADA o EN PROCESO/PENDIENTE "
                            f"(las transferencias interbancarias LBTR pueden aparecer 'en proceso' hasta 8 minutos "
                            f"— ese estado es valido y legitimo. Las ACH interbancarias NO son aceptadas)\n"
                            f"2. La cuenta destino/beneficiario termina en {cuenta_ultimos4}\n"
                            f"3. El monto es aproximadamente {monto_esperado} DOP "
                            f"(acepta hasta 5% de diferencia por impuesto DGII 0.15%)\n"
                            f"4. La fecha es de hoy ({hoy}) o muy reciente (maximo 24h)\n"
                            f"5. NO hay senales de manipulacion digital: pixelacion alrededor "
                            f"de numeros, inconsistencia de fuente en el monto, bordes irregulares "
                            f"en cifras, o numeros con tamano/color diferente al resto del texto\n\n"
                            f"Responde SOLO en este formato:\n"
                            f"VALIDO o INVALIDO\n"
                            f"Razon: [explica brevemente en español, menciona cual criterio fallo si aplica]"
                        ),
                    },
                ],
            }],
        )

        texto = response.content[0].text.strip()
        valido = texto.upper().startswith("VALIDO")
        razon  = texto.split("Razon:")[-1].strip() if "Razon:" in texto else texto
        return valido, razon

    except Exception as e:
        print(f"[validar_comprobante] Error: {e}")
        return None, str(e)  # None = no pudo validar, revisar manualmente
