import os
import pandas as pd

FILES = [
    "expanded_grammar_n5.csv",
    "expanded_grammar_n4.csv",
    "expanded_grammar_n3.csv",
    "expanded_grammar_n2.csv",
    "expanded_grammar_n1.csv",
]

# Esquema canónico
COLS = [
    "level_code","title","pattern",
    "meaning_es","meaning_en","notes","tags",
    "jp","romaji","es","en","hint","source"
]

def read_csv_safe(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    # Asegura columnas
    for c in COLS:
        if c not in df.columns:
            df[c] = ""
    # Reordena
    df = df[COLS]
    # Limpia espacios clave
    for c in ["level_code","title","pattern","jp","es"]:
        df[c] = df[c].astype(str).str.strip()
    return df

def main():
    dfs = []
    seen = 0
    for f in FILES:
        if not os.path.exists(f):
            print(f"⚠️  No existe: {f} (lo salto)")
            continue
        df = read_csv_safe(f)
        print(f"📄 {f}: {len(df)} filas")
        dfs.append(df)
        seen += len(df)

    if not dfs:
        print("❌ No se encontró ningún CSV expandido. Genera primero los expanded_grammar_*.csv")
        return

    all_df = pd.concat(dfs, ignore_index=True)
    before = len(all_df)
    all_df = all_df.drop_duplicates(subset=["level_code","title","pattern","jp","es"], keep="first")
    after = len(all_df)

    out = "expanded_all.csv"
    all_df.to_csv(out, index=False, encoding="utf-8-sig")
    print("\n✅ Combinado guardado en:", out)
    print(f"   - Filas totales leídas: {seen}")
    print(f"   - Filas combinadas (antes de dedupe): {before}")
    print(f"   - Filas tras dedupe: {after}")

    # Resumen por nivel
    print("\n📊 Resumen por nivel:")
    print(all_df["level_code"].value_counts().sort_index().to_string())

if __name__ == "__main__":
    main()
