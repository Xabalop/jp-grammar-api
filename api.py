# api.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from dotenv import load_dotenv
import os
from supabase import create_client, Client

# --- Carga .env ---
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")
POINTS_TABLE = os.getenv("POINTS_TABLE", "grammar_points")
EXAMPLES_TABLE = os.getenv("EXAMPLES_TABLE", "grammar_examples")  # cámbialo si tu tabla se llama diferente

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE en el entorno (.env).")

# --- Cliente Supabase singleton ---
_supabase: Optional[Client] = None
def supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

# --- Modelos de respuesta ---
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
    level_code: Optional[str] = None
    title: Optional[str] = None
    pattern: Optional[str] = None
    jp: str
    es: Optional[str] = None
    en: Optional[str] = None

class GrammarPointWithExamples(BaseModel):
    point: GrammarPoint
    examples: List[Example] = Field(default_factory=list)

class PagedResponse(BaseModel):
    items: List[Any]
    total: int
    limit: int
    offset: int

app = FastAPI(title="JP Grammar API", version="1.0.0")

# CORS (ajusta origins en producción)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/levels", response_model=List[Dict[str, str]])
def get_levels():
    r = supabase().table("levels").select("code").order("code").execute()
    return r.data or []

# ---------- Helpers ----------
def _apply_or(qry, cols: List[str], like: str):
    """Aplica un filtro OR 'col.ilike.like' para un set de columnas."""
    if not cols:
        return qry
    expr = ",".join([f"{c}.ilike.{like}" for c in cols])
    return qry.or_(expr)

# ---------- Endpoints ----------
@app.get("/grammar", response_model=PagedResponse)
def list_grammar(
    level_code: Optional[str] = Query(None, description="Filtra por nivel: N5..N1"),
    q: Optional[str] = Query(None, description="Búsqueda en title/pattern/meaning"),
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

    # conteo robusto (no depende de 'id')
    count_q = supabase().table(POINTS_TABLE).select("*", count="exact")
    if level_code:
        count_q = count_q.eq("level_code", level_code)
    if q:
        like = f"%{q}%"
        count_q = count_q.or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")
    total = count_q.execute().count or 0

    data = qry.order("level_code").order("title").range(offset, offset + limit - 1).execute().data or []
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

@app.get("/grammar/{point_id}", response_model=GrammarPointWithExamples)
def get_grammar_point(point_id: str):
    r = supabase().table(POINTS_TABLE).select("*").eq("id", point_id).single().execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Punto gramatical no encontrado")
    point = GrammarPoint(**r.data)

    base = supabase().table(EXAMPLES_TABLE).select("*")
    # intentamos filtrar por pattern/title si existen; si falla, caemos a level_code
    applied = False
    like_pat = f"%{point.pattern}%" if point.pattern else None
    like_title = f"%{point.title}%" if point.title else None

    try:
        if like_pat:
            base = base.ilike("pattern", like_pat)
            applied = True
    except Exception:
        pass

    try:
        if like_title:
            base = base.ilike("title", like_title)
            applied = True
    except Exception:
        pass

    if not applied:
        base = base.eq("level_code", point.level_code)

    ex = []
    try:
        ex = base.limit(100).execute().data or []
    except Exception:
        # último recurso: solo por nivel
        ex = (
            supabase()
            .table(EXAMPLES_TABLE)
            .select("*")
            .eq("level_code", point.level_code)
            .limit(100)
            .execute()
            .data
            or []
        )
    examples = [Example(**row) for row in ex]
    return GrammarPointWithExamples(point=point, examples=examples)

@app.get("/examples", response_model=PagedResponse)
def list_examples(
    level_code: Optional[str] = Query(None),
    pattern: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Busca en jp/es/en/title/pattern (si existen)"),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    base = supabase().table(EXAMPLES_TABLE).select("*")

    if level_code:
        base = base.eq("level_code", level_code)

    # pattern puede no existir en la tabla: probamos y caemos si falla
    if pattern:
        try:
            base = base.ilike("pattern", f"%{pattern}%")
        except Exception:
            pass

    like = f"%{q}%" if q else None
    # probamos varias combinaciones de columnas según lo que exista en la tabla
    cols_options = [
        ["jp", "es", "en", "title", "pattern"],
        ["jp", "es", "title", "pattern"],
        ["jp", "es"],
        ["jp"],
        []
    ]

    data, total = [], 0
    for cols in cols_options:
        try:
            qry = base
            if q:
                qry = _apply_or(qry, cols, like)
            data = qry.order("level_code").range(offset, offset + limit - 1).execute().data or []

            count_q = base.select("*", count="exact")
            if q:
                count_q = _apply_or(count_q, cols, like)
            total = count_q.execute().count or 0
            break
        except Exception:
            # si falla (columna inexistente), probamos con el siguiente set de columnas
            continue

    # último recurso sin OR si todo falló
    if data == [] and total == 0:
        try:
            data = base.order("level_code").range(offset, offset + limit - 1).execute().data or []
            total = base.select("*", count="exact").execute().count or 0
        except Exception:
            data, total = [], 0

    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=100)):
    like = f"%{q}%"

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

    # búsqueda en ejemplos con fallback si algunas columnas no existen
    ex = []
    for cols in (
        ["jp", "es", "en", "title", "pattern"],
        ["jp", "es", "title", "pattern"],
        ["jp", "es"],
        ["jp"],
        [],
    ):
        try:
            qry = supabase().table(EXAMPLES_TABLE).select("*")
            if cols:
                qry = _apply_or(qry, cols, like)
            ex = qry.limit(limit).execute().data or []
            break
        except Exception:
            continue

    return {"query": q, "points": gp, "examples": ex}

# Redirige la raíz a /docs
@app.get("/")
def root():
    return RedirectResponse(url="/docs")

# --- Arranque local / Render ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
