#!/bin/bash
set -e

echo "[STARTUP] Running migrations..."
python3 migrate.py

echo "[STARTUP] Starting gunicorn..."
gunicorn --bind=0.0.0.0:8000 --workers=4 app:app
