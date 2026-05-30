import json
import os
from datetime import date

import anthropic

from negocio_router import cargar_negocios

_COMANDOS = {
    "pedidos": (
        "no hay [producto] — avisar al cliente que ese producto no esta disponible\n"
        "listo — marcar el pedido actual como despachado"
    ),
    "citas": (
        "mis citas hoy — ver las citas programadas para hoy\n"
        "mis citas semana — ver las citas de los proximos 7 dias\n"
        "ocupado hasta [hora] — bloquear la agenda hasta esa hora (ej: ocupado hasta 5pm)\n"
        "no disponible — bloquear el resto del dia\n"
        "libre [dia] — bloquear un dia completo (ej: libre lunes)"
    ),
}

_LIMITE_MENSUAL = 50


def respuesta_ayuda(modo):
    cmds = _COMANDOS.get(modo, _COMANDOS["pedidos"])
    return f"Comandos disponibles:\n\n{cmds}\n\nEscribe ayuda en cualquier momento para ver esto de nuevo."


def _guardar(datos):
    import negocio_router
    with open("negocios.json", "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    negocio_router._negocios_cache = datos


def consultar_ia(codigo, modo, mensaje):
    datos = cargar_negocios()
    negocio = datos["negocios"].get(codigo)
    if not negocio:
        return None

    mes_actual = date.today().strftime("%Y-%m")
    ci = negocio.get("consultas_ia", {})
    if ci.get("mes") != mes_actual:
        ci = {"mes": mes_actual, "count": 0}

    if ci["count"] >= _LIMITE_MENSUAL:
        return "Has usado tus consultas de ayuda este mes. Escribe ayuda para ver los comandos disponibles."

    ci["count"] += 1
    negocio["consultas_ia"] = ci
    _guardar(datos)

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
