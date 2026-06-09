"""
Maverick — Agente de monitoreo de Wappi.
Corre en background cada 5 minutos. Detecta problemas, auto-resuelve lo que puede,
y manda WhatsApp al dueño con lo que no puede resolver.
"""
import os
import json
import threading
import time
import requests
from datetime import datetime, timedelta, timezone

from db import execute

INTERVALO_SEGUNDOS = 300  # cada 5 minutos
UMBRAL_ATASCADA_MIN = 40  # conversación sin movimiento > 40 min = atascada

TZ_RD = timezone(timedelta(hours=-4))


def _log(agente, tipo, descripcion, detalle=None, resuelto=False):
    execute(
        "INSERT INTO agentes_log (agente, tipo, descripcion, detalle, resuelto) VALUES (%s,%s,%s,%s,%s)",
        (agente, tipo, descripcion, json.dumps(detalle) if detalle else None, resuelto)
    )


def _whatsapp_alerta(mensaje):
    token    = os.getenv("META_ACCESS_TOKEN")
    phone_id = os.getenv("META_PHONE_NUMBER_ID")
    dueno    = os.getenv("DUEÑO_WHATSAPP", "").replace("+", "").strip()
    if not token or not phone_id or not dueno:
        return
    try:
        requests.post(
            f"https://graph.facebook.com/v19.0/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": dueno,
                "type": "text",
                "text": {"body": mensaje, "preview_url": False}
            },
            timeout=10
        )
    except Exception as e:
        print(f"[Maverick] Error enviando alerta: {e}")


def _revisar_pedidos_atascados():
    """Conversaciones de pedidos sin movimiento por más de UMBRAL_ATASCADA_MIN minutos."""
    limite = datetime.now(TZ_RD) - timedelta(minutes=UMBRAL_ATASCADA_MIN)
    rows = execute(
        """SELECT numero_cliente, codigo, estado, actualizado_en
           FROM conversaciones_pedidos
           WHERE actualizado_en < %s AND timeout_en > NOW()""",
        (limite,), fetch="all"
    ) or []

    for r in rows:
        minutos = int((datetime.now(TZ_RD) - r["actualizado_en"].astimezone(TZ_RD)).total_seconds() / 60)
        _log("maverick", "pedido_atascado",
             f"Pedido atascado: {r['numero_cliente']} en {r['codigo']} | estado: {r['estado']} | {minutos} min sin movimiento",
             {"numero": r["numero_cliente"], "codigo": r["codigo"], "estado": r["estado"], "minutos": minutos},
             resuelto=False)
        _whatsapp_alerta(
            f"⚠️ *Maverick*\n\n"
            f"Pedido atascado {minutos} min\n"
            f"Cliente: {r['numero_cliente']}\n"
            f"Negocio: {r['codigo']}\n"
            f"Estado: {r['estado']}\n\n"
            f"Para limpiar:\n"
            f"`DELETE FROM conversaciones_pedidos WHERE numero_cliente = '{r['numero_cliente']}';`"
        )


def _revisar_citas_atascadas():
    """Conversaciones de citas sin movimiento por más de UMBRAL_ATASCADA_MIN minutos."""
    limite = datetime.now(TZ_RD) - timedelta(minutes=UMBRAL_ATASCADA_MIN)
    rows = execute(
        """SELECT numero_cliente, codigo, estado, actualizado_en
           FROM conversaciones_citas
           WHERE actualizado_en < %s""",
        (limite,), fetch="all"
    ) or []

    for r in rows:
        minutos = int((datetime.now(TZ_RD) - r["actualizado_en"].astimezone(TZ_RD)).total_seconds() / 60)
        # Auto-resolver: limpiar conversaciones atascadas > 2 horas
        if minutos > 120:
            execute("DELETE FROM conversaciones_citas WHERE numero_cliente = %s", (r["numero_cliente"],))
            _log("maverick", "cita_atascada_resuelta",
                 f"Conversación de cita limpiada automáticamente: {r['numero_cliente']} ({minutos} min)",
                 {"numero": r["numero_cliente"], "codigo": r["codigo"], "minutos": minutos},
                 resuelto=True)
        else:
            _log("maverick", "cita_atascada",
                 f"Cita atascada: {r['numero_cliente']} en {r['codigo']} | estado: {r['estado']} | {minutos} min",
                 {"numero": r["numero_cliente"], "codigo": r["codigo"], "estado": r["estado"], "minutos": minutos},
                 resuelto=False)
            _whatsapp_alerta(
                f"⚠️ *Maverick*\n\n"
                f"Cita atascada {minutos} min\n"
                f"Cliente: {r['numero_cliente']}\n"
                f"Negocio: {r['codigo']}\n"
                f"Estado: {r['estado']}\n\n"
                f"Para limpiar:\n"
                f"`DELETE FROM conversaciones_citas WHERE numero_cliente = '{r['numero_cliente']}';`"
            )


def _revisar_sesiones_admin_expiradas():
    """Limpia sesiones admin expiradas que no se limpiaron solas."""
    result = execute(
        "DELETE FROM sesiones_admin WHERE expira < NOW() RETURNING numero",
        fetch="all"
    ) or []
    if result:
        _log("maverick", "sesiones_limpiadas",
             f"Sesiones admin expiradas limpiadas: {len(result)}",
             {"numeros": [r["numero"] for r in result]},
             resuelto=True)


def _revisar_citas_sin_confirmar():
    """Citas pendientes de pago sin comprobante por más de 2 horas."""
    rows = execute(
        """SELECT c.id, c.numero_cliente, c.codigo, c.nombre_servicio, c.agendado_en
           FROM citas c
           WHERE c.estado = 'pendiente_pago'
             AND c.agendado_en < NOW() - INTERVAL '2 hours'""",
        fetch="all"
    ) or []

    for r in rows:
        _log("maverick", "pago_pendiente",
             f"Cita sin comprobante >2h: {r['numero_cliente']} | {r['nombre_servicio']} en {r['codigo']}",
             {"id": r["id"], "numero": r["numero_cliente"], "codigo": r["codigo"]},
             resuelto=False)
        _whatsapp_alerta(
            f"💳 *Maverick*\n\n"
            f"Cita sin comprobante hace más de 2h\n"
            f"Cliente: {r['numero_cliente']}\n"
            f"Negocio: {r['codigo']}\n"
            f"Servicio: {r['nombre_servicio']}\n\n"
            f"Puede que el cliente abandonó el proceso."
        )


def _ciclo_maverick():
    """Un ciclo completo de revisión."""
    try:
        _revisar_pedidos_atascados()
        _revisar_citas_atascadas()
        _revisar_sesiones_admin_expiradas()
        _revisar_citas_sin_confirmar()
    except Exception as e:
        print(f"[Maverick] Error en ciclo: {e}")
        try:
            _log("maverick", "error_interno", str(e), resuelto=False)
        except Exception:
            pass


def iniciar_maverick():
    """Inicia Maverick en un hilo daemon."""
    def loop():
        print("[Maverick] Iniciado — revisando cada 5 minutos")
        while True:
            _ciclo_maverick()
            time.sleep(INTERVALO_SEGUNDOS)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
