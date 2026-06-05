from dotenv import load_dotenv
load_dotenv()
from db import init_pool, execute
init_pool()
execute("DELETE FROM conversaciones_citas WHERE numero_cliente = 'whatsapp:+18298789906'")
print("✅ Estado limpiado")
