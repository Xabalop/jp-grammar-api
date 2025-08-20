import subprocess
import sys
import time
import os

LEVELS = ["n5", "n4", "n3", "n2", "n1"]

def run_step(command, desc):
    print(f"[START] {desc}...")
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",  # evita UnicodeDecodeError
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        )
        print(f"[OK] {desc}")
        if result.stdout.strip():
            print(result.stdout)
        if result.stderr.strip():
            print(result.stderr)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] {desc}")
        print(e.stderr)
        sys.exit(1)

def main():
    print("[START] Pipeline expandir -> unir -> cargar")

    # 1. Expandir niveles
    for lvl in LEVELS:
        script = f"expand_dataset_{lvl}.py"
        print(f"[INFO] Ejecutando {script} ...")
        run_step(f"python {script}", f"Expandir {lvl}")
        time.sleep(1)

    # 2. Unir datasets
    print("[INFO] Ejecutando merge_datasets.py ...")
    run_step("python merge_datasets.py", "Unir datasets")

    # 3. Cargar a Supabase
    print("[INFO] Ejecutando load_expanded_all.py ...")
    run_step("python load_expanded_all.py", "Cargar Supabase")

    print("[DONE] Pipeline completo ✔")

if __name__ == "__main__":
    main()
