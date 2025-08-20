import requests
import pandas as pd
import time

INPUT_FILE = "grammar_n5.csv"
OUTPUT_FILE = "expanded_grammar_n5.csv"

# === Función para buscar frases en Jotoba ===
def fetch_examples(query, max_results=10):
    url = "https://jotoba.de/api/search/words"
    payload = {"query": query}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        examples = []
        if "words" in data:
            for w in data["words"]:
                if "examples" in w:
                    for ex in w["examples"]:
                        jp = ex.get("japanese", "")
                        en = ex.get("english", "")
                        if jp and en:
                            examples.append((jp, en))
                        if len(examples) >= max_results:
                            return examples
        return examples
    except Exception as e:
        print(f"⚠ Error buscando '{query}': {e}")
        return []

# === Expandir dataset ===
def expand_dataset():
    df = pd.read_csv(INPUT_FILE)
    rows = []

    for _, row in df.iterrows():
        pattern = str(row["pattern"])
        title = row["title"]
        print(f"🔍 Buscando ejemplos para: {pattern} ({title})")

        examples = fetch_examples(pattern, max_results=10)

        if not examples:
            rows.append(row.to_dict())  # si no hay ejemplos, dejamos la fila tal cual
        else:
            for jp, en in examples:
                new_row = row.to_dict()
                new_row["jp"] = jp
                new_row["en"] = en
                rows.append(new_row)

        time.sleep(1)  # evita saturar la API

    out_df = pd.DataFrame(rows)
    out_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"\n✅ Dataset expandido guardado en {OUTPUT_FILE}")
    print(f"Total de filas: {len(out_df)}")

if __name__ == "__main__":
    expand_dataset()
