"""
Crea y publica un WhatsApp Flow de citas para un negocio.

Uso:
    python crear_flow_negocio.py SE1

Requiere en .env:
    META_ACCESS_TOKEN   — System User Token
    META_WABA_ID        — WhatsApp Business Account ID
"""
import io
import json
import os
import sys
import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

TOKEN   = os.getenv("META_ACCESS_TOKEN")
WABA_ID = os.getenv("META_WABA_ID")
API     = "https://graph.facebook.com/v19.0"


def _db():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def obtener_servicios(codigo):
    conn = _db()
    cur  = conn.cursor()
    cur.execute(
        "SELECT clave, nombre FROM servicios WHERE codigo = %s AND activo = TRUE ORDER BY id",
        (codigo,)
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows


def guardar_flow_id(codigo, flow_id):
    conn = _db()
    cur  = conn.cursor()
    cur.execute("UPDATE negocios SET flow_id = %s WHERE codigo = %s", (flow_id, codigo))
    conn.commit()
    cur.close(); conn.close()


def generar_flow_json(servicios):
    return {
        "version": "3.1",
        "screens": [
            {
                "id": "SERVICIO",
                "title": "Elige tu servicio",
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "Form",
                            "name": "form",
                            "children": [
                                {
                                    "type": "Dropdown",
                                    "label": "¿Qué servicio necesitas?",
                                    "name": "servicio_clave",
                                    "data-source": [
                                        # WhatsApp limita title a 30 chars
                                        {"id": clave, "title": nombre[:30]}
                                        for clave, nombre in servicios
                                    ],
                                    "required": True
                                },
                                {
                                    "type": "Footer",
                                    "label": "Siguiente",
                                    "on-click-action": {
                                        "name": "navigate",
                                        "next": {"type": "screen", "name": "TIPO"},
                                        "payload": {
                                            "servicio_clave": "${form.servicio_clave}"
                                        }
                                    }
                                }
                            ]
                        }
                    ]
                }
            },
            {
                "id": "TIPO",
                "title": "Tipo de cita",
                "data": {
                    "servicio_clave": {"type": "string", "__example__": "ejemplo"}
                },
                "layout": {
                    "type": "SingleColumnLayout",
                    "children": [
                        {
                            "type": "Form",
                            "name": "form2",
                            "children": [
                                {
                                    "type": "RadioButtonsGroup",
                                    "label": "¿Cómo prefieres la cita?",
                                    "name": "tipo",
                                    "data-source": [
                                        {"id": "online",     "title": "Online (Google Meet)"},
                                        {"id": "presencial", "title": "Presencial"}
                                    ],
                                    "required": True
                                },
                                {
                                    "type": "Footer",
                                    "label": "Continuar",
                                    "on-click-action": {
                                        "name": "complete",
                                        "payload": {
                                            "servicio_clave": "${data.servicio_clave}",
                                            "tipo": "${form2.tipo}"
                                        }
                                    }
                                }
                            ]
                        }
                    ]
                }
            }
        ]
    }


def crear_flow(nombre):
    r = requests.post(
        f"{API}/{WABA_ID}/flows",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"name": nombre, "categories": ["APPOINTMENT_BOOKING"]},
        timeout=15
    )
    r.raise_for_status()
    return r.json()["id"]


def subir_json(flow_id, flow_json):
    files = {
        "file":       ("flow.json", io.BytesIO(json.dumps(flow_json).encode()), "application/json"),
        "name":       (None, "flow.json"),
        "asset_type": (None, "FLOW_JSON"),
    }
    r = requests.post(
        f"{API}/{flow_id}/assets",
        headers={"Authorization": f"Bearer {TOKEN}"},
        files=files,
        timeout=15
    )
    r.raise_for_status()
    return r.json()


def publicar(flow_id):
    r = requests.post(
        f"{API}/{flow_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"publish": True},
        timeout=15
    )
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Uso: python crear_flow_negocio.py SE1")

    codigo = sys.argv[1].upper()

    if not TOKEN or not WABA_ID:
        raise SystemExit("Faltan META_ACCESS_TOKEN o META_WABA_ID en .env")

    servicios = obtener_servicios(codigo)
    if not servicios:
        raise SystemExit(f"No hay servicios activos para {codigo}")

    print(f"→ {len(servicios)} servicios encontrados para {codigo}")

    flow_json = generar_flow_json(servicios)

    print("→ Creando Flow en Meta...")
    flow_id = crear_flow(f"citas_{codigo.lower()}")
    print(f"  Flow ID: {flow_id}")

    print("→ Subiendo JSON...")
    result = subir_json(flow_id, flow_json)
    errors = result.get("validation_errors", [])
    if errors:
        print("❌ Errores de validación:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    print("  JSON subido ✓")

    print("→ Publicando Flow...")
    publicar(flow_id)
    print("  Publicado ✓")

    guardar_flow_id(codigo, flow_id)
    print(f"\n✅ Flow {flow_id} guardado en DB para {codigo}")
    print("   El Flow estará listo cuando Meta lo apruebe (usualmente 1-2 días).")
