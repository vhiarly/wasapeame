# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Wasapeame — CLAUDE.md
# Plataforma de WhatsApp bots para negocios locales — caso de uso inicial: colmados dominicanos
# Stack: Python · Flask · Twilio · PostgreSQL · Azure App Service

---

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (dev)
DATABASE_URL=postgresql://... python app.py          # port 3000

# Run in production mode
DATABASE_URL=postgresql://... gunicorn --bind=0.0.0.0:8000 app:app

# Apply DB schema + seed negocios.json (safe to re-run)
DATABASE_URL=postgresql://... python migrate.py

# Run tests (requires live DB)
DATABASE_URL=postgresql://... python test_router.py

# Local tunnel for Twilio webhooks (ngrok)
python tunnel.py   # prints public URL to paste into Twilio sandbox config
```

---

## Architecture

### Message routing (`app.py`)

Every Twilio webhook hits `POST /webhook`. Routing priority, top to bottom:

1. **Sender is a registered business number** (`es_numero_negocio`) → dispatch to `manejar_negocio` (pedidos) or `manejar_negocio_citas` (citas), or fall through to `consultar_ia`.
2. **Active admin session** (`tiene_sesion_admin_citas`) → `manejar_negocio_citas`.
3. **Message starts with a business code** (`detectar_codigo`) OR **client has an open conversation** in DB → `manejar_pedido` or `manejar_cita` depending on `negocio.modo`.
4. **No code, no active session** → welcome message (first time) or prompt to enter a code.

After a pedidos response, the in-memory `threading.Timer` is reset to `TIMEOUT_SEGUNDOS` (180 s). On timeout, `cancelar_por_timeout` fires and delegates to `cancelar_timeout` in `flujo_pedidos.py`, which advances the queue correctly.

### Two conversation modes

Each negocio has `modo = "pedidos"` or `modo = "citas"`. State lives in separate tables:

| Mode | State table | Flow module |
|---|---|---|
| pedidos | `conversaciones_pedidos` | `flujo_pedidos.py` |
| citas | `conversaciones_citas` | `flujo_citas.py` |

### State machine — pedidos (`flujo_pedidos.py`)

State is a string stored in `conversaciones_pedidos.estado`. The full chain:

```
pidiendo
  ├─ (libra product selected by number) → esperando_cantidad_libra
  │     └─ (product is rebanable) → esperando_rebanado
  ├─ (rebanable product selected by text) → esperando_rebanado
  │     └─ (cola_rebanado not empty) → loops through queue
  ├─ (ambiguous number for libra product) → esperando_aclaracion_unidad
  └─ (confirmar) → esperando_confirmacion
        └─ esperando_direccion → esperando_referencia → pedido_enviado
              ├─ (ajustar) → ajustando → pedido_enviado
              └─ (negocio says "no hay X") → esperando_decision
```

`item_pendiente_rebanado` (JSONB column) is dual-purpose: in `esperando_rebanado` it holds the item awaiting a slicing answer; in `esperando_cantidad_libra` / `esperando_aclaracion_unidad` it holds quantity metadata. The states are exclusive so this works, but the column is semantically overloaded.

### Business data flow

`negocios.json` is **seed-only**. `migrate.py` inserts with `ON CONFLICT DO NOTHING`, so changes to `negocios.json` after the first migration are silently ignored. Live catalog prices, stock (`cantidad`), and `activo` flags are in the `catalogo` table and must be updated via SQL.

`negocio_router.obtener_negocio` issues 4 sequential SELECT queries (negocios + catalogo + servicios + horarios) on every call. It is called multiple times per webhook. There is no cache.

### Queue / turn system

`pedidos` rows are ordered FIFO by `creado_en`. `_get_cola(codigo)` returns all `numero_cliente` values in order. The first in the list is the active order being prepared. On `listo`, the business dispatches the first and the next is notified. Turns are tracked in `contadores_turnos` (resets daily via `fecha` column).

### Appointment reminders (`flujo_citas.py`)

`iniciar_recordatorios` starts a daemon thread at startup that polls every 60 s. Rule: if the appointment is ≤ 24 h away → reminder 3 h before; otherwise → reminder 23 h after booking. Reminder state is tracked with `citas.recordatorio_enviado`.

### Timeout system (`app.py`)

In-memory `timers` dict of `threading.Timer` objects, keyed by `numero_cliente`. **The dict is lost on every restart.** On restart, `app.py` runs `DELETE FROM conversaciones_pedidos WHERE timeout_en < NOW()` to clean up expired sessions, but active sessions started before the restart have no timer running until the client sends another message.

---

---

## Key Constraints

- Messages must be plain text — Twilio does not render markdown.
- States must be simple strings, not nested objects.
- `inventario.py` is legacy dead code — not imported anywhere. `flujo_pedidos.py` carries its own inline `_ALIAS` dict.
- Do not use async/await.
- `negocios.json` is for initial seed only — never treat it as the source of truth for live data.

---

## Quick Reference

```
Repo:            github.com/vhiarly/wasapeame
Deploy:          Azure App Service — resource group: wasapeame-rg, region: West Europe
Twilio sender:   +849-256-9906    (número dominicano aprobado por Meta — TWILIO_WHATSAPP_NUMBER)
Endpoint:        POST /webhook
```

---

## Envío de mensajes salientes (Twilio / WhatsApp)

Reglas aprendidas en producción — seguirlas siempre al escribir scripts de envío:

**1. `from_` fijo:**
```python
from_ = "whatsapp:+18492659906"
```

**2. Límite de 1,600 caracteres por mensaje.** Fragmentar en partes con `time.sleep(2)` entre cada una.

**3. Media (PDF, imagen) — usar Google Drive con URL de descarga directa:**
```
https://drive.google.com/uc?export=download&id=FILE_ID
```
Nunca el link del visor (`/file/d/FILE_ID/view`) — Twilio no puede descargar desde ahí.

**4. Ventana de 24h de Meta.** El kit de bienvenida solo puede enviarse como respuesta inmediata cuando el cliente escribe por primera vez. Fuera de esa ventana, Twilio rechaza mensajes salientes que no sean templates aprobados.

**5. Flujo estándar de onboarding:**
```python
msg1 = client.messages.create(body=parte1, from_=FROM, to=TO)   # datos del negocio
time.sleep(2)
msg2 = client.messages.create(body=parte2, from_=FROM, to=TO)   # comandos admin
time.sleep(2)
msg3 = client.messages.create(body="", media_url=[GDRIVE_URL], from_=FROM, to=TO)  # PDF
```

**Script de referencia:** `.claude/SESSION_2026-06-03.md`

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

Al terminar cada sesión, escribe un resumen de máximo 200 tokens en `.claude/SESSION_[fecha].md` con: qué se construyó, qué quedó incompleto, y qué hacer primero la próxima sesión.
