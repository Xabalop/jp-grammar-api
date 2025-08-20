import pandas as pd

LEVELS = ["n5", "n4", "n3", "n2", "n1"]
OUTPUT_FILE = "expanded_all.csv"

def main():
    dfs = []
    for lvl in LEVELS:
        fname = f"expanded_grammar_{lvl}.csv"
        try:
            df = pd.read_csv(fname)
            dfs.append(df)
            print(f"[OK] Leído {fname} con {len(df)} filas")
        except FileNotFoundError:
            print(f"[WARN] No encontrado: {fname}")

    if dfs:
        final_df = pd.concat(dfs, ignore_index=True)
        final_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
        print(f"[DONE] Guardado {OUTPUT_FILE} con {len(final_df)} filas")
    else:
        print("[ERROR] No se unió nada, faltan archivos")

if __name__ == "__main__":
    main()
