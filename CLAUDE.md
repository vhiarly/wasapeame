# CLAUDE.md
# Wasapeame — Plataforma de IA en WhatsApp para MiPYMEs
# Stack: Python · Flask · Meta Cloud API · PostgreSQL · Azure App Service · Anthropic Claude · Azure Speech

---

## Commands

```bash
# Instalar dependencias
pip install -r requirements.txt

# Correr localmente (dev)
DATABASE_URL=postgresql://... python app.py          # puerto 3000

# Correr en producción
DATABASE_URL=postgresql://... gunicorn --bind=0.0.0.0:8000 app:app

# Aplicar migraciones + seed (seguro re-correr)
DATABASE_URL=postgresql://... python migrate.py

# Correr tests (requiere DB activa)
DATABASE_URL=postgresql://... python test_router.py

# Deploy a Azure (crea zip + sube + verifica /ping)
./deploy.sh

# Tunnel local para webhook de Meta
python tunnel.py   # imprime URL pública para pegar en Meta App Dashboard
```

---

## Variables de entorno requeridas

```
DATABASE_URL              — PostgreSQL connection string
META_ACCESS_TOKEN         — System User Token de Meta (necesita whatsapp_business_management)
META_PHONE_NUMBER_ID      — ID del número de WhatsApp en Meta Business
META_WABA_ID              — WhatsApp Business Account ID (1323108735812246)
META_VERIFY_TOKEN         — Token de verificación del webhook (default: wasapeame_verify_2026)
ANTHROPIC_API_KEY         — API key de Anthropic para Claude (asistente IA + transcripción)
AZURE_SPEECH_KEY          — Azure Speech Services (transcripción médica, recurso wasapeame-speech, westeurope)
GOOGLE_CLIENT_ID          — OAuth Google Calendar
GOOGLE_CLIENT_SECRET      — OAuth Google Calendar
```

---

## Arquitectura

### Envío de mensajes — `meta_send(to, body, media_id, media_type)`

Toda salida de mensajes pasa por `meta_send()` en `app.py`. Llama directamente a:
```
POST https://graph.facebook.com/v19.0/{META_PHONE_NUMBER_ID}/messages
Authorization: Bearer {META_ACCESS_TOKEN}
```

Soporta tres tipos:
- **Texto:** `type: text` con `body`
- **Imagen:** `type: image` con `media_id` y caption opcional
- **Audio:** `type: audio` con `media_id`

Límite real: **1,500 caracteres** por mensaje. `_enviar()` fragmenta automáticamente por líneas.

Meta **sí renderiza** formato en WhatsApp: `*negrita*`, `_cursiva_`. Úsalos libremente.

### Routing de mensajes (`app.py → webhook POST`)

Prioridad de arriba hacia abajo:

1. **Relay activo** (`manejar_relay_mensaje`) — chat directo negocio↔cliente. Tiene timeout propio de 30 min.
2. **Emisor es número de negocio registrado** (`es_numero_negocio`) → `manejar_negocio` (pedidos) o `manejar_negocio_citas` (citas). Audio de médico → `procesar_nota_voz_medica`.
3. **Sesión admin activa** (`tiene_sesion_admin_citas`) → `manejar_negocio_citas`.
4. **Mensaje interactivo (WhatsApp Flow)** `nfm_reply` → `manejar_flow_cita`.
5. **Flujo de registro activo** (`tiene_flujo_registro`) → `manejar_registro`.
6. **Mensaje empieza con código conocido** (`detectar_codigo`) o **flujo abierto** → `manejar_pedido` o `manejar_cita` según `negocio.modo`.
7. **Sin código, primera vez** → mensaje de bienvenida + inserción en `clientes_vistos`.
8. **Sin código, cliente conocido** → menú de orientación o `_msg_perdido()`.

### Dos modos de negocio

| Modo | Tabla de estado | Módulo |
|---|---|---|
| `pedidos` | `conversaciones_pedidos` | `flujo_pedidos.py` |
| `citas` | `conversaciones_citas` | `flujo_citas.py` |

### State machine — pedidos (`flujo_pedidos.py`)

```
pidiendo
  ├─ (producto libra seleccionado por número) → esperando_cantidad_libra
  │     └─ (rebanable) → esperando_rebanado
  ├─ (rebanable por texto) → esperando_rebanado
  │     └─ (cola_rebanado no vacía) → loop
  ├─ (número ambiguo libra) → esperando_aclaracion_unidad
  └─ (confirmar) → esperando_confirmacion
        └─ esperando_direccion → esperando_referencia
                                      ├─ (sin comprobante) → pedido_enviado
                                      │     ├─ (ajustar) → ajustando → pedido_enviado
                                      │     └─ (no hay X) → esperando_decision
                                      └─ (con comprobante) → esperando_comprobante
                                            └─ (foto recibida) → pedido_enviado
```

`item_pendiente_rebanado` (JSONB) es dual-purpose: guarda el item en `esperando_rebanado` y metadata de cantidad en `esperando_cantidad_libra`. Los estados son exclusivos — funciona pero la columna está semánticamente sobrecargada.

### Timers

Dos sistemas de timer en memoria (se pierden en restart):

- **`timers`** — timeout de conversación de pedidos. 180s de inactividad → `cancelar_por_timeout()`.
- **`timers_relay`** — timeout de chat relay negocio↔cliente. 1800s (30 min).

En restart: `DELETE FROM conversaciones_pedidos WHERE timeout_en < NOW()` limpia sesiones expiradas, pero sesiones activas pierden su timer hasta el próximo mensaje.

### Mensajes interactivos (botones y listas)

El flujo de citas usa mensajes interactivos nativos de WhatsApp en lugar de texto plano. Implementado en `flujo_citas.py`:

```python
_enviar_botones(numero, texto, [(id, titulo), ...])   # máx 3 botones
_enviar_lista(numero, texto, [(id, titulo, desc), ...])  # hasta 10 filas por sección
_enviar_lista_servicios(numero, negocio)
_enviar_lista_lugares(numero, lugares)
_enviar_lista_dias(numero, negocio, duracion_min, es_presencial)
_enviar_lista_horas(numero, negocio, fecha, duracion_min, es_presencial)
```

Las respuestas llegan como `msg_type = "interactive"` con `button_reply.id` o `list_reply.id`. `app.py` las normaliza en `body_raw` y las procesa igual que texto, con `is_interactive_id = True` para saltar la sanitización de markdown.

El código de `manejar_flow_cita` y `crear_flow_negocio.py` existe en el repo pero **no está activo** — quedó de un experimento con WhatsApp Flows que no se llegó a usar.

### Transcripción médica (`transcripcion_medica.py`)

Doctor envía audio de voz → Azure Speech (`es-DO`, recurso `wasapeame-speech`, westeurope, F0 5h/mes) → Claude Haiku → historia clínica estructurada en PDF → enviada por WhatsApp al doctor → doctor puede reenviarla a un paciente.

### Nota del paciente

En flujo `citas` (modo ME), antes de confirmar la cita el cliente puede agregar: texto libre, nota de voz, o saltar. Guardada en `citas.nota_paciente` y notificada al doctor.

### Google Calendar / OAuth (`oauth_routes.py`, `google_calendar.py`)

Negocios de tipo `citas` pueden conectar Google Calendar para crear eventos y generar links de Google Meet automáticamente. OAuth flow en `/oauth/...`. Tokens guardados en DB.

### Relay (`flujo_citas.py`)

Chat directo entre negocio y cliente fuera del flujo estándar. Se activa con `chat <turno>` desde el negocio. Timeout de 30 min. El negocio puede cerrar con `cerrar chat <turno>`.

---

## Datos de negocios

`negocios.json` es **seed-only**. `migrate.py` inserta con `ON CONFLICT DO NOTHING` — cambios posteriores al seed inicial son ignorados silenciosamente. Precios, stock (`cantidad`) y flags `activo` van directo por SQL.

`negocio_router.obtener_negocio` hace 4 SELECT secuenciales (negocios + catalogo + servicios + horarios) en cada llamada. Sin caché. Llamado múltiples veces por webhook.

---

## Restricciones clave

- No usar async/await.
- Estados deben ser strings simples, no objetos anidados.
- `inventario.py` es código muerto — no importado en ningún lado.
- `negocios.json` nunca es fuente de verdad para datos en vivo.
- `twilio` sigue en `requirements.txt` pero **no se usa** — puede eliminarse en limpieza futura.
- Webhook responde siempre `200 OK` aunque haya error interno (Meta reintenta si recibe != 200).

---

## Quick Reference

```
Repo:              github.com/vhiarly/wasapeame
Deploy:            Azure App Service — wasapeame-rg, West Europe
URL producción:    https://wasapeame.co
Webhook endpoint:  POST https://wasapeame.co/webhook
META_WABA_ID:      1323108735812246
Negocios activos:  SE1 (Pilar), ME1 (Dr. Jim Marmolejos), ME2 (Dr. Feris Olivero)
```

---

## Envío de mensajes salientes

**Siempre usar `meta_send()` o `_enviar()` (para textos largos) — nunca llamar a la API de Meta directamente desde módulos.**

Para scripts de onboarding o envíos manuales fuera del webhook:

```python
import os, requests
TOKEN    = os.getenv("META_ACCESS_TOKEN")
PHONE_ID = os.getenv("META_PHONE_NUMBER_ID")

def send(to, body):
    requests.post(
        f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        json={"messaging_product": "whatsapp", "to": to, "type": "text",
              "text": {"body": body, "preview_url": False}},
        timeout=10
    )
```

Media (PDF, imagen) — usar Google Drive URL de descarga directa:
```
https://drive.google.com/uc?export=download&id=FILE_ID
```
Nunca el link del visor — Meta no puede descargar desde ahí.

Ventana de 24h de Meta: mensajes salientes fuera de la ventana deben ser templates aprobados.

---

## Agentes

| Agente | Archivo | Estado | Función |
|---|---|---|---|
| **Maverick** | `maverick.py` | ✅ Activo | Monitoreo cada 5 min: pedidos/citas atascadas, sesiones expiradas, pagos pendientes. Auto-limpia citas >2h. Alerta por WhatsApp a DUEÑO_WHATSAPP. |
| **Indiana** | `flujo_registro.py` | ✅ Activo | Onboarding de negocios por WhatsApp. Flujo ~7 mensajes: nombre → modo → categoría → número → horario/catálogo → servicios → confirmación → crea negocio en DB. |
| **Phoenix** | — | 🔨 Pendiente | Fallback inteligente para clientes perdidos. Solo activar para DUEÑO_WHATSAPP primero. |
| **Viper** | — | 🔨 Pendiente | Reportes semanales al dueño del negocio. Esperar feedback de clientes para diseñarlo. |

Dashboard de agentes: `https://wasapeame.co/agents` — PIN: `AGENTS_PIN` env var (default `wasapeame2026`)

### Maverick — detalles
- Corre en daemon thread cada 5 minutos (`INTERVALO_SEGUNDOS = 300`)
- Tabla de log: `agentes_log (id, agente, tipo, descripcion, detalle JSONB, resuelto, creado_en)`
- Auto-resuelve: limpia citas atascadas >2h, sesiones admin expiradas
- Alerta (no resuelve): pedidos atascados >40min, pagos pendientes >2h

### Indiana — detalles
- Reescritura completa de `flujo_registro.py` (2026-06-08)
- Tabla: `conversaciones_registro` — requiere columna `datos JSONB` (se crea en startup de app.py con ALTER TABLE IF NOT EXISTS)
- Parsers naturales: horario ("lunes a viernes 9am-6pm"), servicios ("Corte 30min RD$350 / Barba 20min RD$200"), catálogo ("Arroz RD$45 libra")
- Al confirmar: crea negocio en `negocios` + `horarios`/`servicios` (citas) o `catalogo`/`contadores_turnos` (pedidos)
- Genera código automático (tipo + número secuencial) y PIN de 6 dígitos
- Notifica a DUEÑO_WHATSAPP y registra en `agentes_log`

### Dashboard — detalles
- `GET /agents` → `templates/agents.html` (PIN protegido)
- `GET /agents/api/logs?pin=&filtro=` → JSON con últimos 100 eventos
- `POST /agents/api/limpiar?pin=` → borra TODAS las citas y conversaciones (para dev/test)
- Filtros: todos, maverick, indiana, sin_resolver

---

## Pendientes técnicos

- [ ] **QA completo ME2** — probar en WhatsApp real con Dr. Feris (+18096025206)
- [ ] **Kit bienvenida ME2** — enviar a Dr. Feris tras QA
- [ ] **Google OAuth verificación** — revisar estado en Google Cloud Console (~2026-06-09)
- [ ] **Template recordatorio_cita** — en revisión en Meta (aprobación pendiente)
- [ ] **GBP verificación Dr. Jim** — postal con código 5 dígitos (5-14 días desde ~2026-06-07)
- [ ] **Test Indiana E2E** — registrar un negocio de prueba completo por WhatsApp
- [ ] **Escalación 2h no-show SE1** — cliente escribe `no show <código>`, escalar si negocio no responde en 2h
- [ ] **Test E2E cita online SE1** — evento en Calendar de Pilar + Meet link al cliente
- [ ] **Zero-downtime deploy** — priorizar con 20+ negocios (Azure deployment slots)
- [ ] **Stripe** — cobro mensual a negocios tras validar piloto

---

## 1. THINK BEFORE CODING

Antes de escribir cualquier código:

- Declara explícitamente las suposiciones que estás haciendo
- Si hay ambigüedad en el request, **pregunta primero** — no adivines
- Si existe una solución más simple que la que se pidió, dila antes de implementar
- Presenta el plan brevemente antes de tocar archivos

---

## 2. SIMPLICITY FIRST

- No agregues features que no fueron pedidos explícitamente
- No construyas abstracciones para código de un solo uso
- No añadas manejo de errores para escenarios imposibles en este contexto
- **Test rápido:** ¿Lo aprobaría un dev senior sin decir "esto es demasiado"?

---

## 3. SURGICAL CHANGES

- Toca **únicamente** los archivos y funciones que el request requiere
- Mantén el estilo existente aunque lo harías diferente
- Si notas un bug o código muerto no relacionado, **menciónalo** — no lo toques

**Archivos críticos — no tocar sin pedirlo explícitamente:**
- La lógica de parsing de mensajes (cantidades, fracciones, rebanado)
- El sistema de timeout de conversaciones
- El flujo de notificación al dueño del negocio
- Las credenciales y variables de entorno

---

## 4. RESPONSE STYLE

- Responde en palabras mínimas, sin preámbulo
- Sin resumen al final de la respuesta
- Sin frases de relleno: "here is", "I will", "of course", "great", "sure"
- Empieza siempre directamente con la respuesta
- En edits de código: muestra solo las líneas cambiadas con 3 líneas de contexto, nunca el archivo completo

---

## 5. SESSION HANDOFF

Al terminar cada sesión, escribe un resumen de máximo 200 tokens en `~/Documents/vhiarlyob/Proyectos/Wasapeame/Session_[fecha].md` con: qué se construyó, qué quedó incompleto, y qué hacer primero la próxima sesión.
