# google_calendar.py — Wasapeame
# Integración Google Calendar + Meet para negocios con modo virtual
# Requiere: google-auth google-auth-oauthlib google-api-python-client

import os
import uuid
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow

import psycopg2
from psycopg2.extras import RealDictCursor

# ── .env manual reader ────────────────────────────────────────────────────────

def load_env(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

load_env()

GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GOOGLE_REDIRECT_URI  = os.environ["GOOGLE_REDIRECT_URI"]
DATABASE_URL         = os.environ["DATABASE_URL"]

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def get_google_tokens(negocio_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT google_access_token  AS access_token,
                   google_refresh_token AS refresh_token,
                   google_token_expires  AS expires_at
            FROM   negocios
            WHERE  id = %s
            """,
            (negocio_id,),
        )
        return cur.fetchone()

def save_google_tokens(negocio_id: int, access_token: str, refresh_token: str, expires_at: datetime):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE negocios
            SET    google_access_token  = %s,
                   google_refresh_token = %s,
                   google_token_expires  = %s
            WHERE  id = %s
            """,
            (access_token, refresh_token, expires_at, negocio_id),
        )
        conn.commit()

# ── 1. OAuth 2.0 flow (multitenant) ──────────────────────────────────────────

def get_oauth_flow() -> Flow:
    client_config = {
        "web": {
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )

def get_auth_url(negocio_id: int) -> str:
    """URL para que el dueño conecte su Google Calendar. negocio_id va en el state."""
    flow = get_oauth_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=str(negocio_id),
    )
    return auth_url

def handle_oauth_callback(code: str, state: str) -> int:
    """Intercambia el code por tokens y los guarda en DB. Llamar desde /oauth/callback."""
    negocio_id = int(state)
    flow = get_oauth_flow()
    flow.fetch_token(code=code)

    creds      = flow.credentials
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=3600)

    save_google_tokens(
        negocio_id=negocio_id,
        access_token=creds.token,
        refresh_token=creds.refresh_token,
        expires_at=expires_at,
    )
    return negocio_id

# ── 2. Auto-refresh del token ─────────────────────────────────────────────────

def get_valid_credentials(negocio_id: int) -> Credentials:
    """
    Devuelve Credentials válidas.
    Si el token venció (o vence en < 5 min) lo refresca y actualiza la DB.
    """
    row = get_google_tokens(negocio_id)
    if not row or not row["refresh_token"]:
        raise ValueError(f"Negocio {negocio_id} no tiene Google Calendar conectado.")

    creds = Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )

    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if not creds.valid or (expires_at - datetime.now(timezone.utc)) < timedelta(minutes=5):
        creds.refresh(Request())
        save_google_tokens(
            negocio_id,
            creds.token,
            creds.refresh_token,
            datetime.now(timezone.utc) + timedelta(seconds=3600),
        )

    return creds

# ── 3 & 4. Crear evento (presencial o virtual con Meet) ───────────────────────

def crear_cita_con_meet(
    negocio_id: int,
    nombre_cliente: str,
    servicio: str,
    inicio: datetime,
    duracion_minutos: int,
    numero_whatsapp: str,
    es_virtual: bool = False,
    email_cliente: str | None = None,
) -> str | None:
    """
    Crea el evento en Google Calendar.
    es_virtual=True  → agrega Meet, devuelve hangoutLink.
    es_virtual=False → evento normal sin Meet, devuelve None.
    """
    creds   = get_valid_credentials(negocio_id)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    fin = inicio + timedelta(minutes=duracion_minutos)

    descripcion = (
        f"Cliente: {nombre_cliente}\n"
        f"Servicio: {servicio}\n"
        f"WhatsApp: {numero_whatsapp}\n"
        f"Duración: {duracion_minutos} min"
    )

    recordatorios = (
        [{"method": "popup", "minutes": 60}, {"method": "popup", "minutes": 10}]
        if es_virtual else
        [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 120}]
    )

    evento = {
        "summary":     f"{servicio} — {nombre_cliente}",
        "description": descripcion,
        "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Santo_Domingo"},
        "end":   {"dateTime": fin.isoformat(),    "timeZone": "America/Santo_Domingo"},
        "reminders": {"useDefault": False, "overrides": recordatorios},
    }

    if es_virtual:
        evento["conferenceData"] = {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    if email_cliente:
        evento["attendees"] = [{"email": email_cliente}]

    insert_kwargs = dict(
        calendarId="primary",
        body=evento,
        sendUpdates="all" if email_cliente else "none",
    )
    if es_virtual:
        insert_kwargs["conferenceDataVersion"] = 1

    resultado = service.events().insert(**insert_kwargs).execute()

    if not es_virtual:
        return None

    hangout_link = resultado.get("hangoutLink")
    if not hangout_link:
        raise RuntimeError("Google no devolvió hangoutLink. Verifica permisos del calendar.")
    return hangout_link

# ── Helper: mensaje WhatsApp listo para Twilio ────────────────────────────────

def mensaje_confirmacion_virtual(
    nombre_negocio: str,
    servicio: str,
    inicio: datetime,
    hangout_link: str,
) -> str:
    """Arma el texto de confirmación con el Meet link para enviar vía Twilio."""
    fecha_str = inicio.strftime("%A %d de %B, %Y")
    hora_str  = inicio.strftime("%I:%M %p")

    return (
        f"✅ *Cita confirmada con {nombre_negocio}*\n\n"
        f"📋 Servicio: {servicio}\n"
        f"📅 Fecha: {fecha_str}\n"
        f"🕐 Hora: {hora_str}\n\n"
        f"🎥 *Tu enlace de videollamada:*\n{hangout_link}\n\n"
        f"_Solo toca el enlace a la hora de tu cita. No necesitas cuenta de Google._"
    )
