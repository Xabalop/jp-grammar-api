# api.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from dotenv import load_dotenv
import os
from supabase import create_client, Client

# --- Carga variables de entorno ---
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")  # usa la SERVICE ROLE (bypassa RLS)
POINTS_TABLE = os.getenv("POINTS_TABLE", "grammar_points")
EXAMPLES_TABLE = os.getenv("EXAMPLES_TABLE", "examples")  # por defecto 'examples' (tu tabla)

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE en el entorno (.env).")

# --- Cliente Supabase (singleton) ---
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
    level_code: Optional[str] = None  # por si existe
    title: Optional[str] = None
    pattern: Optional[str] = None
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

# --- App ---
app = FastAPI(title="JP Grammar API", version="1.0.0")

# CORS (ajusta origins si quieres restringirlos)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Endpoints básicos ---
@app.get("/")
def root():
    # Redirige a la documentación
    return RedirectResponse(url="/docs")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/levels", response_model=List[Dict[str, str]])
def get_levels():
    # Si tienes sort_order en levels, se ordena por ahí; si no, por code
    try:
        r = supabase().table("levels").select("code,sort_order").order("sort_order").execute()
        rows = r.data or []
        if rows and "sort_order" in rows[0]:
            return [{"code": row["code"]} for row in rows]
    except Exception:
        pass
    r = supabase().table("levels").select("code").order("code").execute()
    return r.data or []

# --- Grammar (lista) ---
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
        like = f"%{q}%"
        qry = qry.or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")

    # Conteo robusto (no dependemos de columnas concretas)
    count_q = supabase().table(POINTS_TABLE).select("*", count="exact")
    if level_code:
        count_q = count_q.eq("level_code", level_code)
    if q:
        like = f"%{q}%"
        count_q = count_q.or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")
    total = count_q.execute().count or 0

    # Orden seguro
    data = qry.order("level_code").order("title").range(offset, offset + limit - 1).execute().data or []
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

# --- Grammar (detalle + ejemplos relacionados) ---
@app.get("/grammar/{point_id}", response_model=GrammarPointWithExamples)
def get_grammar_point(point_id: str):
    r = supabase().table(POINTS_TABLE).select("*").eq("id", point_id).single().execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Punto gramatical no encontrado")
    point = GrammarPoint(**r.data)

    # Estrategia de relación:
    # 1) Si hay grammar_id en examples, úsalo.
    # 2) Si no, intenta por pattern o title.
    # 3) Si tampoco, cae por level_code (si existe esa columna en examples).
    ex_q = supabase().table(EXAMPLES_TABLE).select("*")

    try:
        ex = ex_q.eq("grammar_id", point_id).limit(100).execute().data or []
    except Exception:
        ex = []

    if not ex and point.pattern:
        try:
            ex = supabase().table(EXAMPLES_TABLE).select("*").ilike("pattern", f"%{point.pattern}%").limit(100).execute().data or []
        except Exception:
            pass

    if not ex and point.title:
        try:
            ex = supabase().table(EXAMPLES_TABLE).select("*").ilike("title", f"%{point.title}%").limit(100).execute().data or []
        except Exception:
            pass

    if not ex:
        try:
            ex = supabase().table(EXAMPLES_TABLE).select("*").eq("level_code", point.level_code).limit(100).execute().data or []
        except Exception:
            ex = []

    examples = [Example(**row) for row in ex]
    return GrammarPointWithExamples(point=point, examples=examples)

# --- Examples (lista) ---
@app.get("/examples", response_model=PagedResponse)
def list_examples(
    level_code: Optional[str] = Query(None),
    pattern: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    tbl = supabase().table(EXAMPLES_TABLE)
    qry = tbl.select("*")

    # Filtros directos
    if pattern:
        qry = qry.ilike("pattern", f"%{pattern}%")
    if q:
        like = f"%{q}%"
        # incluimos romaji por si existiera
        qry = qry.or_(f"jp.ilike.{like},romaji.ilike.{like},es.ilike.{like},en.ilike.{like},title.ilike.{like},pattern.ilike.{like}")

    # Filtro por level_code (si la tabla examples no tiene esa columna, hacemos subconsulta por grammar_id)
    ids_for_level: List[str] = []
    if level_code:
        try:
            # si existiera level_code en examples
            qry = qry.eq("level_code", level_code)
        except Exception:
            # subconsulta por grammar_points
            gp_ids = (
                supabase()
                .table(POINTS_TABLE)
                .select("id")
                .eq("level_code", level_code)
                .limit(2000)
                .execute()
                .data
                or []
            )
            ids_for_level = [row["id"] for row in gp_ids if "id" in row]
            if ids_for_level:
                qry = qry.in_("grammar_id", ids_for_level)

    # Conteo robusto
    count_q = supabase().table(EXAMPLES_TABLE).select("*", count="exact")
    if pattern:
        count_q = count_q.ilike("pattern", f"%{pattern}%")
    if q:
        like = f"%{q}%"
        count_q = count_q.or_(f"jp.ilike.{like},romaji.ilike.{like},es.ilike.{like},en.ilike.{like},title.ilike.{like},pattern.ilike.{like}")
    if level_code:
        try:
            count_q = count_q.eq("level_code", level_code)
        except Exception:
            if ids_for_level:
                count_q = count_q.in_("grammar_id", ids_for_level)

    total = count_q.execute().count or 0

    # Orden seguro (id siempre existe)
    data = qry.order("id").range(offset, offset + limit - 1).execute().data or []
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

# --- Búsqueda global ---
@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=100)):
    like = f"%{q}%"

    # Grammar points
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

    # Examples (añadimos romaji)
    ex = (
        supabase()
        .table(EXAMPLES_TABLE)
        .select("*")
        .or_(f"jp.ilike.{like},romaji.ilike.{like},es.ilike.{like},en.ilike.{like},title.ilike.{like},pattern.ilike.{like}")
        .limit(limit)
        .execute()
        .data
        or []
    )

    return {"query": q, "points": gp, "examples": ex}

# --- Arranque local / Render ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
