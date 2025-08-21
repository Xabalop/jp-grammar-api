# api.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from dotenv import load_dotenv
import os
from supabase import create_client, Client

# -------------------- Carga .env --------------------
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")
POINTS_TABLE = os.getenv("POINTS_TABLE", "grammar_points")
EXAMPLES_TABLE = os.getenv("EXAMPLES_TABLE", "examples")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE en el entorno (.env).")

# -------------------- Cliente Supabase (singleton) --------------------
_supabase: Optional[Client] = None
def supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

# -------------------- Modelos --------------------
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

# -------------------- App --------------------
app = FastAPI(title="JP Grammar API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# opcional: redirigir raíz a /docs
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

# -------------------- GRAMMAR --------------------
@app.get("/grammar", response_model=PagedResponse)
def list_grammar(
    level_code: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    s = supabase()
    tbl = s.table(POINTS_TABLE)
    qry = tbl.select("*")

    if level_code:
        qry = qry.eq("level_code", level_code)
    if q:
        like = f"%{q}%"
        qry = qry.or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")

    # Conteo exacto con mismos filtros
    count_q = s.table(POINTS_TABLE).select("id", count="exact")
    if level_code:
        count_q = count_q.eq("level_code", level_code)
    if q:
        like = f"%{q}%"
        count_q = count_q.or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")
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
    s = supabase()
    r = s.table(POINTS_TABLE).select("*").eq("id", point_id).single().execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Punto gramatical no encontrado")
    point = GrammarPoint(**r.data)

    # ejemplos vinculados por grammar_id
    ex = (
        s.table(EXAMPLES_TABLE)
         .select("*")
         .eq("grammar_id", point_id)
         .limit(200)
         .execute()
         .data
        or []
    )
    examples = [Example(**row) for row in ex]
    return GrammarPointWithExamples(point=point, examples=examples)

# -------------------- EXAMPLES --------------------
@app.get("/examples", response_model=PagedResponse)
def list_examples(
    level_code: Optional[str] = Query(None, description="Filtra por nivel del grammar point"),
    grammar_id: Optional[str] = Query(None, description="Filtra por id de grammar point"),
    q: Optional[str] = Query(None, description="Búsqueda en jp/es/en/romaji/hint"),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    s = supabase()
    tbl = s.table(EXAMPLES_TABLE)
    qry = tbl.select("*")

    if grammar_id:
        qry = qry.eq("grammar_id", grammar_id)

    gp_ids: List[str] = []
    if level_code:
        gp_resp = (
            s.table(POINTS_TABLE)
             .select("id")
             .eq("level_code", level_code)
             .execute()
        )
        gp_ids = [row["id"] for row in (gp_resp.data or [])]
        if not gp_ids:
            return PagedResponse(items=[], total=0, limit=limit, offset=offset)
        qry = qry.in_("grammar_id", gp_ids)

    if q:
        like = f"%{q}%"
        qry = qry.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},romaji.ilike.{like},hint.ilike.{like}")

    # Conteo exacto
    count_q = s.table(EXAMPLES_TABLE).select("id", count="exact")
    if grammar_id:
        count_q = count_q.eq("grammar_id", grammar_id)
    if gp_ids:
        count_q = count_q.in_("grammar_id", gp_ids)
    if q:
        like = f"%{q}%"
        count_q = count_q.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},romaji.ilike.{like},hint.ilike.{like}")
    total = count_q.execute().count or 0

    data = (
        qry.range(offset, offset + limit - 1)
           .execute()
           .data
        or []
    )
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

# -------------------- SEARCH --------------------
@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=100)):
    like = f"%{q}%"
    s = supabase()

    gp = (
        s.table(POINTS_TABLE)
         .select("*")
         .or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")
         .limit(limit)
         .execute()
         .data
        or []
    )

    ex = (
        s.table(EXAMPLES_TABLE)
         .select("*")
         .or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},romaji.ilike.{like},hint.ilike.{like}")
         .limit(limit)
         .execute()
         .data
        or []
    )
    return {"query": q, "points": gp, "examples": ex}

# -------------------- Arranque local / Render --------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
