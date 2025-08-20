import pandas as pd
import itertools

# Archivo de entrada (el dataset base)
INPUT_FILE = "grammar_n5.csv"
# Archivo de salida (el dataset expandido)
OUTPUT_FILE = "expanded_grammar_n5.csv"

# Número de variaciones a generar por ejemplo base
VARIATIONS_PER_ROW = 200  # 200 * 5 ejemplos = 1000 frases

# Algunas listas de variaciones simples para expandir
subjects = ["私は", "彼は", "彼女は", "私たちは", "先生は"]
objects = ["本を", "水を", "ご飯を", "日本語を", "音楽を"]
places = ["学校で", "家で", "会社で", "公園で", "図書館で"]
times = ["毎日", "昨日", "今日", "明日", "時々"]

def expand_sentence(base_jp: str):
    """
    Genera variaciones de la frase original cambiando sujeto, objeto, lugar y tiempo.
    """
    variations = []
    for s, o, p, t in itertools.product(subjects, objects, places, times):
        new_sentence = f"{t} {p} {s} {o} {base_jp}"
        variations.append(new_sentence)
        if len(variations) >= VARIATIONS_PER_ROW:
            break
    return variations

def main():
    df = pd.read_csv(INPUT_FILE)

    expanded_rows = []

    for _, row in df.iterrows():
        base_jp = row["jp"]
        # Generar frases expandidas
        jp_variations = expand_sentence(base_jp)

        for jp_sentence in jp_variations:
            expanded_rows.append({
                "level_code": row["level_code"],
                "title": row["title"],
                "pattern": row["pattern"],
                "meaning_es": row["meaning_es"],
                "meaning_en": row["meaning_en"],
                "notes": row["notes"],
                "tags": row["tags"],
                "jp": jp_sentence,
                "romaji": row["romaji"],  # lo dejamos igual por ahora
                "es": row["es"],          # traducción base
                "en": row["en"],          # traducción base
                "hint": row["hint"],
                "source": row["source"]
            })

    expanded_df = pd.DataFrame(expanded_rows)
    expanded_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"✅ Dataset expandido guardado en {OUTPUT_FILE} con {len(expanded_df)} filas")

if __name__ == "__main__":
    main()

