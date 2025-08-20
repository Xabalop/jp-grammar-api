import os
from dotenv import load_dotenv
from supabase import create_client

# Cargar .env
load_dotenv(override=True)

# Conectar a Supabase
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE"])

# Traer los códigos de la tabla levels
r = supabase.table("levels").select("code").execute()
print("levels =>", r.data)
