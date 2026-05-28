from pyngrok import ngrok
import time

ngrok.set_auth_token('3EKodnBwWqYKRRgT03xnZNUFdgr_3ua7YF2kDrqv8kVpzFfk')
tunnel = ngrok.connect(3000)
print(f"Túnel activo: {tunnel.public_url}")
print("Presiona Ctrl+C para detener")

# Mantiene el túnel activo
while True:
    time.sleep(1)