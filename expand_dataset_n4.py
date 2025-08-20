# expand_dataset_n4.py
import pandas as pd

SRC = "grammar_n4.csv"
DST = "expanded_grammar_n4.csv"
MAX_PER_BASE = 20  # cuántas variantes generar por cada fila base

# Bancos de frases seguras que no rompen la gramática
TIME_PREFIXES_JP = ["よく", "たまに", "時々", "普段は", "例えば"]
TIME_PREFIXES_ES = ["A menudo", "A veces", "De vez en cuando", "Normalmente", "Por ejemplo"]

ENDINGS_JP = ["。", "よ。", "ね。", "ですよ。", "ね？"]
ENDINGS_ES = [".", ".", ".", ".", "."]  # mantenemos puntación simple en ES

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    needed = ["level_code","title","pattern","jp","es"]
    for c in needed:
        if c not in df.columns:
            df[c] = ""
    return df[needed].copy()

def expand_row(row):
    outs = []
    base_jp = str(row["jp"]).strip()
    base_es = str(row["es"]).strip()
    lvl = row["level_code"]; title = row["title"]; pattern = row["pattern"]

    # siempre agrega el base
    outs.append((lvl, title, pattern, base_jp, base_es))

    # Variantes (prefijo de tiempo + final suave)
    count = 0
    for p_jp, p_es in zip(TIME_PREFIXES_JP, TIME_PREFIXES_ES):
        for end_jp, end_es in zip(ENDINGS_JP, ENDINGS_ES):
            jp = f"{p_jp}、{base_jp}{end_jp}".replace("。。", "。")
            es = f"{p_es}, {base_es}{end_es}".replace("..", ".")
            outs.append((lvl, title, pattern, jp, es))
            count += 1
            if count >= MAX_PER_BASE: break
        if count >= MAX_PER_BASE: break
    return outs

def main():
    df = pd.read_csv(SRC)
    df = normalize_cols(df)
    print("Vista previa base:")
    print(df.head().to_string(index=False))

    rows = []
    for _, r in df.iterrows():
        rows.extend(expand_row(r))

    out = pd.DataFrame(rows, columns=["level_code","title","pattern","jp","es"]).drop_duplicates()
    out.to_csv(DST, index=False, encoding="utf-8-sig")
    print(f"\n✅ Generado {DST} con {len(out)} filas.")

if __name__ == "__main__":
    main()
