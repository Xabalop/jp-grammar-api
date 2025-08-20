# api.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from dotenv import load_dotenv
import os
from supabase import create_client, Client

# =========================
# Carga .env y configuración
# =========================
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")

# Tablas / vistas (puedes cambiarlas por variables de entorno si quieres)
LEVELS_TABLE   = os.getenv("LEVELS_TABLE", "levels")
POINTS_TABLE   = os.getenv("POINTS_TABLE", "grammar_points")
EXAMPLES_TABLE = os.getenv("EXAMPLES_TABLE", "examples")        # tu tabla de ejemplos reales
VOCAB_TABLE    = os.getenv("VOCAB_TABLE", "vocab")              # vocabulario
JOTOBA_VIEW    = os.getenv("JOTOBA_VIEW", "jotoba_search")      # vista que “aplana” json (creada en Supabase)

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE en el entorno (.env).")

# Cliente Supabase (singleton)
_supabase: Optional[Client] = None
def supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

# =============
# Modelos (API)
# =============
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
    level_code: Optional[str] = None
    title: Optional[str] = None
    pattern: Optional[str] = None
    jp: str
    es: Optional[str] = None
    en: Optional[str] = None
    hint: Optional[str] = None
    created_at: Optional[str] = None

class GrammarPointWithExamples(BaseModel):
    point: GrammarPoint
    examples: List[Example] = Field(default_factory=list)

class PagedResponse(BaseModel):
    items: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int

# =========
# FastAPI
# =========
app = FastAPI(title="JP Grammar API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========
# Utilidades
# =========
def ilike_or(fields: List[str], like_value: str) -> str:
    """Construye la expresión para .or_ con ILIKE en varios campos."""
    return ",".join(f"{fld}.ilike.{like_value}" for fld in fields)

# =========
# Endpoints
# =========
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/levels", response_model=List[Dict[str, str]])
def get_levels():
    r = supabase().table(LEVELS_TABLE).select("code").order("code").execute()
    return r.data or []

@app.get("/grammar", response_model=PagedResponse)
def list_grammar(
    level_code: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    like = f"%{q}%" if q else None

    # Consulta de datos
    qry = supabase().table(POINTS_TABLE).select("*")
    if level_code:
        qry = qry.eq("level_code", level_code)
    if like:
        qry = qry.or_(ilike_or(
            ["title", "pattern", "meaning_es", "meaning_en"], like
        ))

    data = (
        qry.order("level_code")
           .order("title")
           .range(offset, offset + limit - 1)
           .execute()
           .data
        or []
    )

    # Conteo exacto con los mismos filtros
    cnt = supabase().table(POINTS_TABLE).select("id", count="exact")
    if level_code:
        cnt = cnt.eq("level_code", level_code)
    if like:
        cnt = cnt.or_(ilike_or(
            ["title", "pattern", "meaning_es", "meaning_en"], like
        ))
    total = cnt.execute().count or 0

    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

@app.get("/grammar/{point_id}", response_model=GrammarPointWithExamples)
def get_grammar_point(point_id: str):
    # Punto
    r = (
        supabase()
        .table(POINTS_TABLE)
        .select("*")
        .eq("id", point_id)
        .single()
        .execute()
    )
    if not r.data:
        raise HTTPException(status_code=404, detail="Punto gramatical no encontrado")

    point = GrammarPoint(**r.data)

    # 1) Intento directo por relación (si tu tabla 'examples' tiene grammar_id)
    ex_q = supabase().table(EXAMPLES_TABLE).select("*").eq("grammar_id", point_id)
    ex = ex_q.limit(100).execute().data or []

    # 2) Fallback: si no hay, buscar por patrón/título o por nivel
    if not ex:
        eq = supabase().table(EXAMPLES_TABLE).select("*")
        filters = False
        if point.pattern:
            eq = eq.ilike("pattern", f"%{point.pattern}%")
            filters = True
        if point.title:
            eq = eq.ilike("title", f"%{point.title}%")
            filters = True
        if not filters:
            eq = eq.eq("level_code", point.level_code)
        ex = eq.limit(100).execute().data or []

    examples = [Example(**row) for row in ex]
    return GrammarPointWithExamples(point=point, examples=examples)

@app.get("/examples", response_model=PagedResponse)
def list_examples(
    grammar_id: Optional[str] = Query(None),
    level_code: Optional[str] = Query(None),
    pattern: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    like = f"%{q}%" if q else None
    qry = supabase().table(EXAMPLES_TABLE).select("*")

    if grammar_id:
        qry = qry.eq("grammar_id", grammar_id)
    if level_code:
        qry = qry.eq("level_code", level_code)
    if pattern:
        qry = qry.ilike("pattern", f"%{pattern}%")
    if like:
        qry = qry.or_(ilike_or(["jp", "es", "en", "title", "pattern"], like))

    data = (
        qry.order("level_code")
           .range(offset, offset + limit - 1)
           .execute()
           .data
        or []
    )

    cnt = supabase().table(EXAMPLES_TABLE).select("id", count="exact")
    if grammar_id:
        cnt = cnt.eq("grammar_id", grammar_id)
    if level_code:
        cnt = cnt.eq("level_code", level_code)
    if pattern:
        cnt = cnt.ilike("pattern", f"%{pattern}%")
    if like:
        cnt = cnt.or_(ilike_or(["jp", "es", "en", "title", "pattern"], like))
    total = cnt.execute().count or 0

    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=100)):
    like = f"%{q}%"

    gp = (
        supabase()
        .table(POINTS_TABLE)
        .select("*")
        .or_(ilike_or(["title", "pattern", "meaning_es", "meaning_en"], like))
        .limit(limit)
        .execute()
        .data
        or []
    )

    ex = (
        supabase()
        .table(EXAMPLES_TABLE)
        .select("*")
        .or_(ilike_or(["jp", "es", "en", "title", "pattern"], like))
        .limit(limit)
        .execute()
        .data
        or []
    )
    return {"query": q, "points": gp, "examples": ex}

# ============
# /vocab (N5…N1)
# ============
@app.get("/vocab", response_model=PagedResponse)
def list_vocab(
    level: Optional[str] = Query(None, description="N5…N1"),
    q: Optional[str] = Query(None, description="búsqueda por kanji/kana/significado"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    like = f"%{q}%" if q else None

    qry = supabase().table(VOCAB_TABLE).select("*")
    if level:
        qry = qry.eq("level", level)
    if like:
        qry = qry.or_(ilike_or(["kanji", "reading_kana", "meaning"], like))

    data = (
        qry.order("level")
           .order("kanji")
           .range(offset, offset + limit - 1)
           .execute()
           .data
        or []
    )

    cnt = supabase().table(VOCAB_TABLE).select("id", count="exact")
    if level:
        cnt = cnt.eq("level", level)
    if like:
        cnt = cnt.or_(ilike_or(["kanji", "reading_kana", "meaning"], like))
    total = cnt.execute().count or 0

    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

# ==========================
# /jotoba (desde vista _search)
# ==========================
@app.get("/jotoba", response_model=PagedResponse)
def list_jotoba(
    level: Optional[str] = Query(None, description="N5…N1 (level_code en la vista)"),
    q: Optional[str] = Query(None, description="búsqueda por término/lecturas/glosas"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    like = f"%{q}%" if q else None

    # Ojo: esta vista debe existir y tener columnas: term, level_code, readings_text, glosses_text
    qry = supabase().table(JOTOBA_VIEW).select("*")
    if level:
        qry = qry.eq("level_code", level)
    if like:
        qry = qry.or_(ilike_or(["term", "readings_text", "glosses_text"], like))

    data = (
        qry.order("term")
           .range(offset, offset + limit - 1)
           .execute()
           .data
        or []
    )

    cnt = supabase().table(JOTOBA_VIEW).select("uuid", count="exact")
    if level:
        cnt = cnt.eq("level_code", level)
    if like:
        cnt = cnt.or_(ilike_or(["term", "readings_text", "glosses_text"], like))
    total = cnt.execute().count or 0

    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

# =========================
# Ejecución local / Render
# =========================
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
