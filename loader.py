import os
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv

# Cargar credenciales desde .env
load_dotenv(override=True)
url = os.environ["SUPABASE_URL"]
key = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(url, key)

# === CONFIGURACIÓN ===
CSV_FILE = "grammar_n5.csv"   # <- cambia aquí según el nivel que quieras cargar

def load_csv_to_supabase(file_path: str):
    print(f"📂 Cargando {file_path} ...")
    df = pd.read_csv(file_path)

    # Filtrar solo las columnas que existen en la tabla
    df = df[["level_code", "title", "pattern", "meaning_es", "meaning_en"]]

    print("Vista previa:")
    print(df.head())

    data = df.to_dict(orient="records")

    # Insertar / actualizar en Supabase
    resp = supabase.table("grammar_points").upsert(data).execute()

    print("✔ Upsert finalizado")
    print("Respuesta Supabase:", resp)

if __name__ == "__main__":
    load_csv_to_supabase(CSV_FILE)
