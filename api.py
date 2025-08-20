# api.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from dotenv import load_dotenv
from supabase import create_client, Client
import os
import re

# ---- Cargar .env ----
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")
POINTS_TABLE = os.getenv("POINTS_TABLE", "grammar_points")
EXAMPLES_TABLE = os.getenv("EXAMPLES_TABLE", "examples")  # por defecto tu tabla se llama 'examples'

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE en el entorno (.env/render).")

# ---- Cliente Supabase (singleton) ----
_supabase: Optional[Client] = None
def supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

# ---- Utilidades de búsqueda seguras ----
_SANITIZER = re.compile(r"[,\(\)\[\]\{\}\"'|;]")  # caracteres que rompen el parser de PostgREST
def sanitize_for_or(value: str) -> str:
    # Conserva letras/números/espacios y texto JP; elimina lo que rompe `.or_()`
    return _SANITIZER.sub(" ", value).strip()

def build_or_like(fields: List[str], needle: str) -> str:
    like = f"%{needle}%"
    return ",".join(f"{f}.ilike.{like}" for f in fields)

# ---- Modelos ----
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
    romaji: Optional[str] = None
    hint: Optional[str] = None

class GrammarPointWithExamples(BaseModel):
    point: GrammarPoint
    examples: List[Example] = Field(default_factory=list)

class PagedResponse(BaseModel):
    items: List[Any]
    total: int
    limit: int
    offset: int

# ---- App ----
app = FastAPI(title="JP Grammar API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return RedirectResponse(url="/docs")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/levels", response_model=List[Dict[str, str]])
def get_levels():
    r = supabase().table("levels").select("code").order("code").execute()
    return r.data or []

# ---- /grammar ----
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

    # filtro por texto (seguro)
    if q:
        sq = sanitize_for_or(q)
        if sq:
            try:
                qry = qry.or_(build_or_like(
                    ["title", "pattern", "meaning_es", "meaning_en"],
                    sq
                ))
            except Exception:
                # si falla el .or_, ignora el texto para evitar 500
                pass

    # conteo exacto con mismos filtros
    count_q = supabase().table(POINTS_TABLE).select("*", count="exact")
    if level_code:
        count_q = count_q.eq("level_code", level_code)
    if q:
        sq = sanitize_for_or(q)
        if sq:
            try:
                count_q = count_q.or_(build_or_like(
                    ["title", "pattern", "meaning_es", "meaning_en"],
                    sq
                ))
            except Exception:
                pass

    total = count_q.execute().count or 0
    data = (
        qry.order("level_code")
           .order("title")
           .range(offset, offset + limit - 1)
           .execute()
           .data
        or []
    )
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

@app.get("/grammar/{point_id}", response_model=GrammarPointWithExamples)
def get_grammar_point(point_id: str):
    r = supabase().table(POINTS_TABLE).select("*").eq("id", point_id).single().execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Punto gramatical no encontrado")
    point = GrammarPoint(**r.data)

    # 1) Primero por relación directa (tu tabla 'examples' tiene grammar_id)
    ex_q = supabase().table(EXAMPLES_TABLE).select("*").eq("grammar_id", point.id)
    ex = ex_q.limit(100).execute().data or []

    # 2) Fallback si no hay: buscar por pattern/title/level
    if not ex:
        q2 = supabase().table(EXAMPLES_TABLE).select("*")
        applied = False
        if point.pattern:
            q2 = q2.ilike("pattern", f"%{point.pattern}%"); applied = True
        if point.title:
            q2 = q2.ilike("title", f"%{point.title}%"); applied = True
        if not applied:
            q2 = q2.eq("level_code", point.level_code)
        ex = q2.limit(100).execute().data or []

    return GrammarPointWithExamples(point=point, examples=[Example(**row) for row in ex])

# ---- /examples ----
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

    if level_code:
        qry = qry.eq("level_code", level_code)
    if pattern:
        qry = qry.ilike("pattern", f"%{pattern}%")
    if q:
        sq = sanitize_for_or(q)
        if sq:
            try:
                qry = qry.or_(build_or_like(
                    ["jp", "es", "en", "title", "pattern", "romaji", "hint"],
                    sq
                ))
            except Exception:
                pass

    count_q = supabase().table(EXAMPLES_TABLE).select("*", count="exact")
    if level_code:
        count_q = count_q.eq("level_code", level_code)
    if pattern:
        count_q = count_q.ilike("pattern", f"%{pattern}%")
    if q:
        sq = sanitize_for_or(q)
        if sq:
            try:
                count_q = count_q.or_(build_or_like(
                    ["jp", "es", "en", "title", "pattern", "romaji", "hint"],
                    sq
                ))
            except Exception:
                pass

    total = count_q.execute().count or 0
    data = (
        qry.order("level_code")
           .range(offset, offset + limit - 1)
           .execute()
           .data
        or []
    )
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

# ---- /search ----
@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=100)):
    sq = sanitize_for_or(q)
    if not sq:
        # si tras sanear no queda nada útil, devolvemos vacío sin error
        return {"query": q, "points": [], "examples": []}

    points, examples = [], []

    # puntos
    try:
        points = (
            supabase()
            .table(POINTS_TABLE)
            .select("*")
            .or_(build_or_like(["title", "pattern", "meaning_es", "meaning_en"], sq))
            .limit(limit)
            .execute()
            .data
            or []
        )
    except Exception:
        points = []

    # ejemplos (con fallback progresivo si fallara)
    try:
        examples = (
            supabase()
            .table(EXAMPLES_TABLE)
            .select("*")
            .or_(build_or_like(["jp", "es", "en", "title", "pattern", "romaji", "hint"], sq))
            .limit(limit)
            .execute()
            .data
            or []
        )
    except Exception:
        for cols in (["jp", "es", "title", "pattern"], ["jp", "es"], ["jp"]):
            try:
                examples = (
                    supabase()
                    .table(EXAMPLES_TABLE)
                    .select("*")
                    .or_(build_or_like(cols, sq))
                    .limit(limit)
                    .execute()
                    .data
                    or []
                )
                if examples:
                    break
            except Exception:
                continue

    return {"query": q, "points": points, "examples": examples}

# ---- Arranque local / Render ----
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
# =========================
# MODELOS NUEVOS
# =========================
class VocabItem(BaseModel):
    id: str
    level: Optional[str] = None       # en tu tabla 'vocab' se llama 'level'
    kanji: Optional[str] = None
    reading_kana: Optional[str] = None
    meaning: Optional[str] = None

class JotobaEntry(BaseModel):
    id: str
    term: str
    level: Optional[str] = None
    language: Optional[str] = None
    readings: Optional[dict] = None   # jsonb; lo devolvemos como dict


# =========================
# /vocab  (lista con filtros + paginación)
# =========================
@app.get("/vocab", response_model=PagedResponse)
def list_vocab(
    level: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    table_name = "vocab"
    tbl = supabase().table(table_name)
    qry = tbl.select("*")

    if level:
        qry = qry.eq("level", level)

    if q:
        like = f"%{q}%"
        # columnas típicas en tu captura: kanji, meaning, reading_kana
        qry = qry.or_(f"kanji.ilike.{like},meaning.ilike.{like},reading_kana.ilike.{like}")

    # conteo exacto aplicando los mismos filtros
    count_q = supabase().table(table_name).select("*", count="exact")
    if level:
        count_q = count_q.eq("level", level)
    if q:
        like = f"%{q}%"
        count_q = count_q.or_(f"kanji.ilike.{like},meaning.ilike.{like},reading_kana.ilike.{like}")
    total = count_q.execute().count or 0

    data = (
        qry.order("level")
           .range(offset, offset + limit - 1)
           .execute()
           .data
        or []
    )
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)


# =========================
# /jotoba  (lista con filtros + paginación)
# =========================
@app.get("/jotoba", response_model=PagedResponse)
def list_jotoba(
    level: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    q: Optional[str] = Query(None),     # busca por 'term' y, si se puede, dentro de 'readings'
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    table_name = "jotoba_entries"
    tbl = supabase().table(table_name)
    qry = tbl.select("*")

    if level:
        qry = qry.eq("level", level)
    if language:
        qry = qry.eq("language", language)

    # Búsqueda básica por 'term' y, si la API lo permite, por readings::text
    if q:
        like = f"%{q}%"
        # intentamos incluir readings::text; si falla, nos quedamos con 'term'
        try:
            qry = qry.or_(f"term.ilike.{like},readings::text.ilike.{like}")
            use_readings = True
        except Exception:
            qry = qry.ilike("term", like)
            use_readings = False

    # Conteo con mismos filtros
    count_q = supabase().table(table_name).select("*", count="exact")
    if level:
        count_q = count_q.eq("level", level)
    if language:
        count_q = count_q.eq("language", language)
    if q:
        like = f"%{q}%"
        try:
            if use_readings:
                count_q = count_q.or_(f"term.ilike.{like},readings::text.ilike.{like}")
            else:
                count_q = count_q.ilike("term", like)
        except Exception:
            count_q = count_q.ilike("term", like)

    total = count_q.execute().count or 0

    data = (
        qry.order("level")
           .range(offset, offset + limit - 1)
           .execute()
           .data
        or []
    )
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)
