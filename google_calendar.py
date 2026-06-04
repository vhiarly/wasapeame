# google_calendar.py — Wasapeame
# Integración Google Calendar + Meet para negocios con modo virtual
# Requiere: google-auth google-auth-oauthlib google-api-python-client
from __future__ import annotations

import os
import uuid
import urllib.parse
import requests as http_requests
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

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

def get_google_tokens(codigo: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT google_access_token  AS access_token,
                   google_refresh_token AS refresh_token,
                   google_token_expires  AS expires_at
            FROM   negocios
            WHERE  codigo = %s
            """,
            (codigo,),
        )
        return cur.fetchone()

def save_google_tokens(codigo: str, access_token: str, refresh_token: str, expires_at: datetime):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE negocios
            SET    google_access_token  = %s,
                   google_refresh_token = %s,
                   google_token_expires  = %s
            WHERE  codigo = %s
            """,
            (access_token, refresh_token, expires_at, codigo),
        )
        conn.commit()

# ── 1. OAuth 2.0 manual (sin google-auth-oauthlib Flow) ──────────────────────
# Implementación directa con urllib + requests para evitar PKCE automático
# que versiones recientes de google-auth-oauthlib añaden por defecto.

_AUTH_URI  = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"

def get_auth_url(codigo: str) -> str:
    """Construye la URL de autorización de Google manualmente, sin PKCE."""
    params = urllib.parse.urlencode({
        "client_id":              GOOGLE_CLIENT_ID,
        "redirect_uri":           GOOGLE_REDIRECT_URI,
        "response_type":          "code",
        "scope":                  " ".join(SCOPES),
        "access_type":            "offline",
        "prompt":                 "consent",
        "include_granted_scopes": "true",
        "state":                  codigo,
    })
    return f"{_AUTH_URI}?{params}"

def handle_oauth_callback(code: str, state: str) -> str:
    """Intercambia el code por tokens vía POST directo. Sin Flow, sin PKCE."""
    codigo = state  # state es el codigo del negocio (ej: "SE1")

    resp = http_requests.post(_TOKEN_URI, data={
        "code":          code,
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri":  GOOGLE_REDIRECT_URI,
        "grant_type":    "authorization_code",
    })
    resp.raise_for_status()
    token_data = resp.json()

    access_token  = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in    = token_data.get("expires_in", 3600)
    expires_at    = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    save_google_tokens(
        codigo=codigo,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )
    return codigo

# ── 2. Auto-refresh del token ─────────────────────────────────────────────────

def get_valid_credentials(codigo: str) -> Credentials:
    """
    Devuelve Credentials válidas.
    Si el token venció (o vence en < 5 min) lo refresca y actualiza la DB.
    """
    row = get_google_tokens(codigo)
    if not row or not row["refresh_token"]:
        raise ValueError(f"Negocio {codigo} no tiene Google Calendar conectado.")

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
            codigo,
            creds.token,
            creds.refresh_token,
            datetime.now(timezone.utc) + timedelta(seconds=3600),
        )

    return creds

# ── 3 & 4. Crear evento (presencial o virtual con Meet) ───────────────────────

def crear_cita_con_meet(
    codigo: str,
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
    creds   = get_valid_credentials(codigo)
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
    DIAS_ES   = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
    MESES_ES  = ["enero","febrero","marzo","abril","mayo","junio",
                 "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_str = f"{DIAS_ES[inicio.weekday()]} {inicio.day} de {MESES_ES[inicio.month-1]} {inicio.year}"
    hora_str  = inicio.strftime("%I:%M %p")

    confirmacion = (
        f"✅ *Cita confirmada con {nombre_negocio}*\n\n"
        f"📋 Servicio: {servicio}\n"
        f"📅 Fecha: {fecha_str}\n"
        f"🕐 Hora: {hora_str}\n\n"
        f"🎥 En breve recibes el enlace de Google Meet."
    )
    link_msg = (
        f"*Tu enlace de videollamada:*\n{hangout_link}\n\n"
        f"_Solo toca el enlace a la hora de tu cita. No necesitas cuenta de Google._"
    )
    return confirmacion, link_msg
