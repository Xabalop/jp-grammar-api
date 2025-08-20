import os
import sys
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client
from postgrest.exceptions import APIError

# ==== Conexión ====
load_dotenv(override=True)
URL = os.environ.get("SUPABASE_URL")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE")
assert URL and SERVICE_KEY, "Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE en .env"
supabase: Client = create_client(URL, SERVICE_KEY)
print("SUPABASE_URL =", URL)

# ==== Utilidades ====
def to_tag_array(tags: str):
    if not tags:
        return []
    return [t.strip() for t in str(tags).split(";") if t and str(t).strip()]

def upsert_grammar(df: pd.DataFrame):
    required_cols = [
        "level_code","title","pattern",
        "meaning_es","meaning_en","notes","tags",
        "jp","romaji","es","en","hint","source"
    ]
    for c in required_cols:
        if c not in df.columns:
            df[c] = ""

    # Limpieza básica
    for c in ["level_code","title","pattern","jp","es"]:
        df[c] = df[c].astype(str).str.strip()

    grouped = df.groupby(["level_code","title","pattern"], dropna=False)

    new_points = 0
    updated_points = 0
    examples_inserted = 0
    examples_skipped = 0

    for (level_code, title, pattern), g in grouped:
        # Datos maestros
        meaning_es = g["meaning_es"].dropna().iloc[0] if not g["meaning_es"].dropna().empty else None
        meaning_en = g["meaning_en"].dropna().iloc[0] if not g["meaning_en"].dropna().empty else None
        notes      = g["notes"].dropna().iloc[0]      if not g["notes"].dropna().empty      else None
        tags_raw   = g["tags"].dropna().iloc[0]       if not g["tags"].dropna().empty       else ""
        source     = g["source"].dropna().iloc[0]     if not g["source"].dropna().empty     else None
        tag_array  = to_tag_array(tags_raw)

        # Upsert grammar_points
        existing = supabase.table("grammar_points").select("id").eq("level_code", level_code).eq("title", title).eq("pattern", pattern).execute()
        if existing.data:
            grammar_id = existing.data[0]["id"]
            supabase.table("grammar_points").update({
                "meaning_es": meaning_es,
                "meaning_en": meaning_en,
                "notes": notes,
                "tags": tag_array,
                "source": source
            }).eq("id", grammar_id).execute()
            updated_points += 1
        else:
            res = supabase.table("grammar_points").insert({
                "level_code": level_code,
                "title": title,
                "pattern": pattern,
                "meaning_es": meaning_es,
                "meaning_en": meaning_en,
                "notes": notes,
                "tags": tag_array,
                "source": source,
                "published": True,
            }).execute()
            grammar_id = res.data[0]["id"]
            new_points += 1

        # Ejemplos
        for _, row in g.iterrows():
            jp = str(row.get("jp") or "").strip()
            es = str(row.get("es") or "").strip()
            if not jp or not es:
                examples_skipped += 1
                continue

            en     = str(row.get("en") or "").strip() or None
            romaji = str(row.get("romaji") or "").strip() or None
            hint   = str(row.get("hint") or "").strip() or None

            # Evita duplicados (mismo grammar_id + jp + es)
            exist_ex = supabase.table("examples").select("id").eq("grammar_id", grammar_id).eq("jp", jp).eq("es", es).execute()
            if exist_ex.data:
                examples_skipped += 1
                continue

            try:
                supabase.table("examples").insert({
                    "grammar_id": grammar_id,
                    "jp": jp,
                    "romaji": romaji,
                    "es": es,
                    "en": en,
                    "hint": hint
                }).execute()
                examples_inserted += 1
            except APIError as e:
                # Si hay restricción de unicidad, lo damos por omitido
                examples_skipped += 1

    print("✔ Upsert completado")
    print(f"   - Puntos nuevos: {new_points}")
    print(f"   - Puntos actualizados: {updated_points}")
    print(f"   - Ejemplos insertados: {examples_inserted}")
    print(f"   - Ejemplos omitidos (duplicados/incompletos): {examples_skipped}")

def main():
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "expanded_all.csv"
    if not os.path.exists(csv_path):
        print(f"❌ No existe {csv_path}. Pásalo como argumento o ejecuta primero merge_expanded.py")
        sys.exit(1)

    print(f"\n📂 Cargando {csv_path} ...")
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

    # Vista previa
    cols = [c for c in ["level_code","title","pattern","jp","es"] if c in df.columns]
    if cols:
        print("Vista previa:")
        print(df[cols].head().to_string(index=False))

    upsert_grammar(df)
    print("\n🎉 Carga finalizada.")

if __name__ == "__main__":
    main()
