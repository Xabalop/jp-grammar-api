# api.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict, Set
from dotenv import load_dotenv
import os
from supabase import create_client, Client

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ajusta en prod si quieres restringir
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helpers ---
def _like(q: str) -> str:
    return f"%{q}%"

def _dedup_search(table: str, columns: List[str], like: str, limit: int) -> List[Dict[str, Any]]:
    """
    Hace búsquedas por columna con ilike y acumula resultados únicos por 'id'.
    Evita usar .or_(...) para no romper con caracteres especiales (comas, corchetes, etc).
    """
    seen: Set[Any] = set()
    out: List[Dict[str, Any]] = []
    sb = supabase()
    for col in columns:
        if len(out) >= limit:
            break
        try:
            res = (
                sb.table(table)
                .select("*")
                .ilike(col, like)
                .limit(max(1, limit - len(out)))
                .execute()
                .data
                or []
            )
            for row in res:
                rid = row.get("id")
                if rid in seen:
                    continue
                seen.add(rid)
                out.append(row)
                if len(out) >= limit:
                    break
        except Exception:
            # si la columna no existe o falla, seguimos con la siguiente
            continue
    return out

# --- Raíz → docs ---
@app.get("/")
def root():
    return RedirectResponse(url="/docs")

# --- Health ---
@app.get("/health")
def health():
    return {"status": "ok"}

# --- Levels ---
@app.get("/levels", response_model=List[Dict[str, str]])
def get_levels():
    try:
        # Si tienes sort_order, úsalo
        r = supabase().table("levels").select("code,sort_order").order("sort_order").execute()
        rows = r.data or []
        if rows and "sort_order" in rows[0]:
            return [{"code": row["code"]} for row in rows]
    except Exception:
        pass
    r = supabase().table("levels").select("code").order("code").execute()
    return r.data or []

# --- Grammar: listado ---
@app.get("/grammar", response_model=PagedResponse)
def list_grammar(
    level_code: Optional[str] = Query(None, description="N5..N1"),
    q: Optional[str] = Query(None, description="Busca en title/pattern/meaning_*"),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    tbl = supabase().table(POINTS_TABLE)
    qry = tbl.select("*")

    if level_code:
        qry = qry.eq("level_code", level_code)
    if q:
        like = _like(q)
        qry = qry.or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")

    # conteo exacto con los mismos filtros
    count_q = supabase().table(POINTS_TABLE).select("*", count="exact")
    if level_code:
        count_q = count_q.eq("level_code", level_code)
    if q:
        like = _like(q)
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

# --- Grammar: detalle + ejemplos ---
@app.get("/grammar/{point_id}", response_model=GrammarPointWithExamples)
def get_grammar_point(point_id: str):
    r = supabase().table(POINTS_TABLE).select("*").eq("id", point_id).single().execute()
    if not r.data:
        raise HTTPException(status_code=404, detail="Punto gramatical no encontrado")
    point = GrammarPoint(**r.data)

    # 1) Relación directa por grammar_id (tu tabla 'examples' la tiene)
    ex = (
        supabase()
        .table(EXAMPLES_TABLE)
        .select("*")
        .eq("grammar_id", point_id)
        .limit(100)
        .execute()
        .data
        or []
    )

    # 2) Si no hubiera, intento textual en jp/es/en/romaji a partir de pattern/title
    if not ex:
        like_terms = [t for t in [point.pattern, point.title] if t]
        for term in like_terms:
            ex = _dedup_search(EXAMPLES_TABLE, ["jp", "es", "en", "romaji"], _like(term), 100)
            if ex:
                break

    examples = [Example(**row) for row in ex]
    return GrammarPointWithExamples(point=point, examples=examples)

# --- Examples: listado ---
@app.get("/examples", response_model=PagedResponse)
def list_examples(
    level_code: Optional[str] = Query(None, description="Filtra por nivel (N5..N1) via grammar_id"),
    grammar_id: Optional[str] = Query(None, description="Filtra por grammar_id"),
    q: Optional[str] = Query(None, description="Busca en jp/es/en/romaji/hint"),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    sb = supabase()
    base = sb.table(EXAMPLES_TABLE)
    qry = base.select("*")
    cnt = base.select("*", count="exact")

    # a) filtro directo por grammar_id si lo pasas
    if grammar_id:
        qry = qry.eq("grammar_id", grammar_id)
        cnt = cnt.eq("grammar_id", grammar_id)

    # b) filtro por level_code → traducido a ids de grammar_points
    elif level_code:
        ids_res = (
            sb.table(POINTS_TABLE)
            .select("id")
            .eq("level_code", level_code)
            .limit(10000)
            .execute()
            .data
            or []
        )
        id_list = [r["id"] for r in ids_res]
        if not id_list:
            return PagedResponse(items=[], total=0, limit=limit, offset=offset)
        qry = qry.in_("grammar_id", id_list)
        cnt = cnt.in_("grammar_id", id_list)

    # c) búsqueda textual (solo columnas existentes en 'examples')
    if q:
        like = _like(q)
        # Intentamos una or_ completa; si fallara, hacemos fallback manual
        try:
            qry = qry.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},romaji.ilike.{like},hint.ilike.{like}")
            cnt = cnt.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},romaji.ilike.{like},hint.ilike.{like}")
        except Exception:
            # fallback manual: reducimos a jp/es/en
            try:
                qry = qry.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like}")
                cnt = cnt.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like}")
            except Exception:
                # si incluso esto falla (por caracteres especiales), haremos el recuento a posteriori
                pass

    # total
    try:
        total = cnt.execute().count or 0
    except Exception:
        # último recurso: estimar con una consulta limitada (no exacto)
        total = 0

    # datos (orden por id, columna segura)
    data = qry.order("id").range(offset, offset + limit - 1).execute().data or []
    return PagedResponse(items=data, total=total, limit=limit, offset=offset)

# --- Search robusto ---
@app.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=100)):
    like = _like(q)

    # 1) grammar_points: evitamos .or_ con valores raros -> hacemos dedup por columnas
    gp_cols = ["title", "pattern", "meaning_es", "meaning_en"]
    gp = _dedup_search(POINTS_TABLE, gp_cols, like, limit)

    # 2) examples: dedup por columnas (evita 500 con caracteres especiales)
    ex_cols = ["jp", "es", "en", "romaji", "hint"]
    ex = _dedup_search(EXAMPLES_TABLE, ex_cols, like, limit)

    return {"query": q, "points": gp, "examples": ex}

# --- Arranque local / Render ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
