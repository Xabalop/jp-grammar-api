# api.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict, Set
from dotenv import load_dotenv
import os
from supabase import create_client, Client
from fastapi.responses import RedirectResponse

# --- Carga .env ---
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")
POINTS_TABLE = os.getenv("POINTS_TABLE", "grammar_points")
EXAMPLES_TABLE = os.getenv("EXAMPLES_TABLE", "examples")  # tu tabla real de ejemplos

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE en el entorno (.env).")

# --- Cliente Supabase singleton ---
_supabase: Optional[Client] = None
def supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

# --- Modelos ---
class GrammarPoint(BaseModel):
    id: str
    level_code: str
    title: str
    pattern: Optional[str] = None
    meaning_es: Optional[str] = None
    meaning_en: Optional[str] = None
    notes: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source: Optional[str] = None
    published: Optional[bool] = True

class Example(BaseModel):
    id: Optional[str] = None
    grammar_id: Optional[str] = None
    level_code: Optional[str] = None  # puede venir vía join o no existir
    jp: str
    romaji: Optional[str] = None
    es: Optional[str] = None
    en: Optional[str] = None
    hint: Optional[str] = None

class GrammarPointWithExamples(BaseModel):
    point: GrammarPoint
    examples: List[Example] = Field(default_factory=list)

class PagedResponse(BaseModel):
    items: List[Any]
    total: int
    limit: int
    offset: int

app = FastAPI(title="JP Grammar API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Util ---
def build_like(q: str) -> str:
    # patrón para ILIKE
    return f"%{q}%"

# --- Endpoints ---
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/levels", response_model=List[Dict[str, str]])
def get_levels():
    # usa sort_order si lo tienes; si no, ordena por code
    r = supabase().table("levels").select("code").order("code").execute()
    return r.data or []

@app.get("/grammar", response_model=PagedResponse)
def list_grammar(
    level_code: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    tbl = supabase().table(POINTS_TABLE)
    qry = tbl.select("*")

    if level_code:
        qry = qry.eq("level_code", level_code)

    if q:
        like = build_like(q)
        qry = qry.or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")

    # conteo exacto aplicando los mismos filtros (sin depender de 'id')
    count_q = supabase().table(POINTS_TABLE).select("*", count="exact")
    if level_code:
        count_q = count_q.eq("level_code", level_code)
    if q:
        like = build_like(q)
        count_q = count_q.or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")
    total = count_q.execute().count or 0

    data = (
        qry.order("level_code")
           .order("title")
           .range(offset, offset + limit - 1)
           .execute().data
        or []
    )
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

@app.get("/grammar/{point_id}", response_model=GrammarPointWithExamples)
def get_grammar_point(point_id: str):
    r = supabase().table(POINTS_TABLE).select("*").eq("id", point_id).single().execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Punto gramatical no encontrado")
    point = GrammarPoint(**r.data)

    # buscar ejemplos relacionados:
    ex_q = supabase().table(EXAMPLES_TABLE).select("*")
    # si tu tabla de ejemplos no tiene 'pattern'/'title', usamos jp/es/en/romaji
    like_cols = ["jp", "es", "en", "romaji"]
    applied = False
    if point.pattern:
        ex_q = ex_q.or_(",".join([f"{c}.ilike.{build_like(point.pattern)}" for c in like_cols]))
        applied = True
    if point.title:
        ex_q = ex_q.or_(",".join([f"{c}.ilike.{build_like(point.title)}" for c in like_cols]))
        applied = True
    if not applied:
        # fallback: por relación grammar_id
        ex_q = ex_q.eq("grammar_id", point.id)

    ex = ex_q.limit(100).execute().data or []
    examples = [Example(**row) for row in ex]
    return GrammarPointWithExamples(point=point, examples=examples)

@app.get("/examples", response_model=PagedResponse)
def list_examples(
    level_code: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """
    Esta tabla (EXAMPLES_TABLE) no tiene 'level_code' directamente.
    Si nos pasan level_code, filtramos por ids de grammar_points con ese level_code.
    Para q, buscamos en jp/es/en/romaji/hint.
    """
    tbl = supabase().table(EXAMPLES_TABLE)
    qry = tbl.select("*")

    # Filtrado por level_code vía grammar_points -> ids
    ids: List[str] = []
    if level_code:
        ids_data = (
            supabase().table(POINTS_TABLE)
            .select("id")
            .eq("level_code", level_code)
            .limit(2000)  # suficiente para tu dataset
            .execute().data
            or []
        )
        ids = [row["id"] for row in ids_data]
        if ids:
            qry = qry.in_("grammar_id", ids)
        else:
            # si no hay ids, respuesta vacía rápida
            return PagedResponse(items=[], total=0, limit=limit, offset=offset)

    # Búsqueda en columnas existentes
    if q:
        like = build_like(q)
        # Sólo columnas que existen en tu tabla examples
        qry = qry.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},romaji.ilike.{like},hint.ilike.{like}")

    # conteo
    count_q = supabase().table(EXAMPLES_TABLE).select("*", count="exact")
    if level_code and ids:
        count_q = count_q.in_("grammar_id", ids)
    if q:
        like = build_like(q)
        count_q = count_q.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},romaji.ilike.{like},hint.ilike.{like}")
    total = count_q.execute().count or 0

    data = (
        qry.order("created_at" if "created_at" in {"created_at"} else "id")
           .range(offset, offset + limit - 1)
           .execute().data
        or []
    )
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=100)):
    """
    Búsqueda robusta:
    - Grammar points: OR en varias columnas (title/pattern/meaning_*).
    - Examples: SIN usar .or_(...) para evitar problemas con comas u otros caracteres.
      En su lugar, consultamos columnas por separado y unimos resultados únicos.
    """
    like = build_like(q)

    # --- grammar points ---
    gp = (
        supabase()
        .table(POINTS_TABLE)
        .select("*")
        .or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")
        .limit(limit)
        .execute()
        .data
        or []
    )

    # --- examples (acumulando resultados únicos por id) ---
    ex_cols_order = ["jp", "es", "en", "romaji", "hint"]
    seen: Set[str] = set()
    examples: List[Dict[str, Any]] = []

    for col in ex_cols_order:
        if len(examples) >= limit:
            break
        res = (
            supabase()
            .table(EXAMPLES_TABLE)
            .select("*")
            .ilike(col, like)
            .limit(limit)
            .execute()
            .data
            or []
        )
        for row in res:
            _id = row.get("id")
            if _id is None or _id in seen:
                continue
            seen.add(_id)
            examples.append(row)
            if len(examples) >= limit:
                break

    return {"query": q, "points": gp, "examples": examples}

# Redirigir "/" a /docs
@app.get("/")
def root():
    return RedirectResponse(url="/docs")

# --- Arranque local / Render ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
