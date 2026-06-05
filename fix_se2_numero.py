import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()

cur.execute("UPDATE negocios SET numero_negocio = 'whatsapp:+18098804764' WHERE codigo = 'SE2'")
conn.commit()
print("✅ Número de SE2 actualizado.")

cur.close()
conn.close()
