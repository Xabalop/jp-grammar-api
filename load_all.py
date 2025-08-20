# load_all.py (con backup de CSVs limpiados)
import os
import sys
import textwrap
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client

# -----------------------------
# Config
# -----------------------------
CSV_FILES = [
    "grammar_n5.csv",
    "grammar_n4.csv",
    "grammar_n3.csv",
    "grammar_n2.csv",
    "grammar_n1.csv",
]

# Guardar copia “limpia” de cada CSV
SAVE_CLEANED = True
CLEAN_DIR = "cleaned"

# Campos esperados (no todos obligatorios)
EXPECTED_COLS = [
    "level_code", "title", "pattern",
    "meaning_es", "meaning_en", "notes", "tags",
    "jp", "romaji", "es", "en", "hint", "source",
]

# Mínimos para punto y ejemplo
REQUIRED_FOR_POINT = ["level_code", "title", "pattern"]
REQUIRED_FOR_EXAMPLE = ["jp", "es"]


# -----------------------------
# Utilidades
# -----------------------------
def preview(df: pd.DataFrame, n=5) -> str:
    cols = [c for c in ["level_code", "title", "pattern", "jp", "es"] if c in df.columns]
    return df[cols].head(n).to_string(index=False)


def coerce_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # Asegura columnas y tipado string
    for col in EXPECTED_COLS:
        if col not in df.columns:
            df[col] = None
    df = df.astype({c: "string" for c in df.columns})

    # Strip/normaliza
    for c in EXPECTED_COLS:
        if c in df.columns:
            df[c] = df[c].fillna("").map(lambda x: x.strip() if isinstance(x, str) else x)
    df["level_code"] = df["level_code"].str.upper().str.strip()

    # Elimina filas totalmente vacías
    df = df.dropna(how="all")
    return df


def safe_read_csv(path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except Exception:
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig", engine="python")


def parse_tags(tag_str: str):
    if not tag_str:
        return []
    raw = tag_str.replace(",", ";")
    return [t.strip() for t in raw.split(";") if t.strip()]


def save_cleaned_csv(df: pd.DataFrame, original_path: str):
    if not SAVE_CLEANED:
        return
    os.makedirs(CLEAN_DIR, exist_ok=True)
    base = os.path.basename(original_path)
    name, ext = os.path.splitext(base)
    out_path = os.path.join(CLEAN_DIR, f"{name}.cleaned.csv")

    # Ordena columnas: primero EXPECTED_COLS, luego cualquier extra
    ordered = [c for c in EXPECTED_COLS if c in df.columns]
    extras = [c for c in df.columns if c not in ordered]
    df_out = df[ordered + extras]

    df_out.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"💾 CSV limpio guardado en: {out_path}")


# -----------------------------
# Supabase
# -----------------------------
load_dotenv(override=True)
URL = os.environ.get("SUPABASE_URL")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE")
if not URL or not SERVICE_KEY:
    print("❌ Falta SUPABASE_URL o SUPABASE_SERVICE_ROLE en .env")
    sys.exit(1)

print(f"SUPABASE_URL = {URL}")
supabase: Client = create_client(URL, SERVICE_KEY)


# -----------------------------
# Upsert
# -----------------------------
def upsert_file(df: pd.DataFrame):
    df = coerce_dataframe(df)

    missing_point = [c for c in REQUIRED_FOR_POINT if c not in df.columns]
    if missing_point:
        raise ValueError(f"❌ Faltan columnas obligatorias para puntos: {missing_point}")

    grouped = df.groupby(["level_code", "title", "pattern"], dropna=False)
    created_points = 0
    updated_points = 0
    inserted_examples = 0
    skipped_examples = 0

    for (level_code, title, pattern), g in grouped:
        def first_nonempty(col):
            if col not in g.columns:
                return None
            s = g[col].dropna().map(lambda x: x if str(x).strip() else None)
            return s.iloc[0] if not s.dropna().empty else None

        meaning_es = first_nonempty("meaning_es")
        meaning_en = first_nonempty("meaning_en")
        notes = first_nonempty("notes")
        tags = parse_tags(first_nonempty("tags") or "")
        source = first_nonempty("source")

        existing = supabase.table("grammar_points") \
            .select("id") \
            .eq("level_code", level_code) \
            .eq("title", title) \
            .eq("pattern", pattern) \
            .execute()

        if existing.data:
            grammar_id = existing.data[0]["id"]
            supabase.table("grammar_points").update({
                "meaning_es": meaning_es,
                "meaning_en": meaning_en,
                "notes": notes,
                "tags": tags,
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
                "tags": tags,
                "source": source,
                "published": True
            }).execute()
            grammar_id = res.data[0]["id"]
            created_points += 1

        has_example_cols = all(c in g.columns for c in REQUIRED_FOR_EXAMPLE)
        if not has_example_cols:
            continue

        for _, row in g.iterrows():
            jp = (row.get("jp") or "").strip()
            es = (row.get("es") or "").strip()
            en = (row.get("en") or "") or None
            romaji = (row.get("romaji") or "") or None
            hint = (row.get("hint") or "") or None

            if not jp or not es:
                skipped_examples += 1
                continue

            dup = supabase.table("examples") \
                .select("id") \
                .eq("grammar_id", grammar_id) \
                .eq("jp", jp) \
                .eq("es", es) \
                .execute()

            if dup.data:
                skipped_examples += 1
                continue

            supabase.table("examples").insert({
                "grammar_id": grammar_id,
                "jp": jp,
                "romaji": romaji,
                "es": es,
                "en": en,
                "hint": hint
            }).execute()
            inserted_examples += 1

    return created_points, updated_points, inserted_examples, skipped_examples


def load_file(filename: str):
    if not os.path.exists(filename):
        print(f"⚠ Archivo no encontrado: {filename}")
        return

    print(f"\n📂 Cargando {filename} ...")
    try:
        df = safe_read_csv(filename)
    except Exception as e:
        print(f"❌ No se pudo leer {filename}: {e}")
        return

    if df.empty:
        print("⚠ CSV vacío, nada que hacer.")
        return

    # Limpio y guardo copia
    df_clean = coerce_dataframe(df.copy())
    save_cleaned_csv(df_clean, filename)

    # Vista previa
    try:
        print("Vista previa:")
        print(preview(df_clean))
    except Exception:
        pass

    unknown_cols = [c for c in df_clean.columns if c not in EXPECTED_COLS]
    if unknown_cols:
        print(f"ℹ Aviso: columnas no previstas que serán ignoradas: {unknown_cols}")

    try:
        created, updated, ex_ins, ex_skip = upsert_file(df_clean)
        print(f"✔ {filename}: upsert completado")
        print(f"   - Puntos nuevos: {created}")
        print(f"   - Puntos actualizados: {updated}")
        print(f"   - Ejemplos insertados: {ex_ins}")
        print(f"   - Ejemplos omitidos (duplicados/incompletos): {ex_skip}")
    except Exception as e:
        msg = textwrap.shorten(str(e), width=500)
        print(f"❌ Error procesando {filename}: {msg}")


if __name__ == "__main__":
    for f in CSV_FILES:
        load_file(f)
    print("\n🎉 Carga de todos los niveles finalizada.")
