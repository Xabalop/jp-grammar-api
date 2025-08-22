# api.py
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from dotenv import load_dotenv
import os, random, re
from supabase import create_client, Client
from postgrest.exceptions import APIError

# --- Carga .env ---
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")
POINTS_TABLE = os.getenv("POINTS_TABLE", "grammar_points")
EXAMPLES_TABLE = os.getenv("EXAMPLES_TABLE", "examples")  # usa el nombre real de tu tabla

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE en el entorno (.env).")

# --- Cliente Supabase singleton ---
_supabase: Optional[Client] = None
def supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

# --- Modelos de dominio ---
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
    title: Optional[str] = None
    pattern: Optional[str] = None
    jp: str
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

# --- Modelos de quiz ---
class QuizQuestion(BaseModel):
    id: str                 # id estable (point_id o example_id)
    type: str               # cloze, pattern, meaning, translation
    prompt: str
    jp: Optional[str] = None
    choices: List[str]
    answer_idx: int
    meta: Dict[str, Any] = Field(default_factory=dict)

# --- App ---
app = FastAPI(title="JP Grammar API", version="1.2.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ajusta en prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- utils -----------------
def _safe_list(x):
    return x if isinstance(x, list) else []

def _sample(seq: List[Any], k: int) -> List[Any]:
    if not seq or k <= 0:
        return []
    if k >= len(seq):
        return random.sample(seq, len(seq))
    return random.sample(seq, k)

def _hide_pattern(text: str, pattern: Optional[str]) -> str:
    """Oculta el patrón en la oración."""
    if not text:
        return ""
    if pattern:
        try:
            pat = re.escape(pattern.strip())
            masked = re.sub(pat, "____", text)
            if masked != text:
                return masked
        except re.error:
            pass
    # fallback si no encontramos el patrón
    return re.sub(r"[ぁ-んァ-ン一-龯]{2,}", "____", text, count=1)

def _get_point_ids_by_level(level_code: str) -> List[str]:
    rows = (
        supabase()
        .table(POINTS_TABLE)
        .select("id")
        .eq("level_code", level_code)
        .limit(2000)
        .execute()
        .data or []
    )
    return [r["id"] for r in rows if r.get("id")]

# ----------------- endpoints básicos -----------------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/levels", response_model=List[Dict[str, str]])
def get_levels():
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
        like = f"%{q}%"
        qry = qry.or_(f"title.ilike.{like},pattern.ilike.{like},meaning_es.ilike.{like},meaning_en.ilike.{like}")

    # contar
    count_q = supabase().table(POINTS_TABLE).select("id", count="exact")
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

    # 1) primero por grammar_id directo
    ex = (
        supabase()
        .table(EXAMPLES_TABLE)
        .select("*")
        .eq("grammar_id", point.id)
        .limit(100)
        .execute()
        .data or []
    )

    # 2) fallback por pattern/title si no hay vinculados
    if not ex:
        q = supabase().table(EXAMPLES_TABLE).select("*")
        filt = False
        if point.pattern:
            q = q.ilike("pattern", f"%{point.pattern}%")
            filt = True
        if point.title:
            q = q.ilike("title", f"%{point.title}%")
            filt = True
        if filt:
            ex = q.limit(100).execute().data or []

    examples = [Example(**row) for row in ex]
    return GrammarPointWithExamples(point=point, examples=examples)

@app.get("/examples", response_model=PagedResponse)
def list_examples(
    level_code: Optional[str] = Query(None),
    pattern: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    base = supabase().table(EXAMPLES_TABLE)
    qry = base.select("*")

    gp_ids: Optional[List[str]] = None
    if level_code:
        gp_ids = _get_point_ids_by_level(level_code)
        if not gp_ids:
            return PagedResponse(items=[], total=0, limit=limit, offset=offset)
        qry = qry.in_("grammar_id", gp_ids)

    if pattern:
        qry = qry.ilike("pattern", f"%{pattern}%")
    if q:
        like = f"%{q}%"
        qry = qry.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},title.ilike.{like},pattern.ilike.{like}")

    # contar con mismos filtros
    count_q = supabase().table(EXAMPLES_TABLE).select("id", count="exact")
    if gp_ids:
        count_q = count_q.in_("grammar_id", gp_ids)
    if pattern:
        count_q = count_q.ilike("pattern", f"%{pattern}%")
    if q:
        like = f"%{q}%"
        count_q = count_q.or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},title.ilike.{like},pattern.ilike.{like}")
    total = count_q.execute().count or 0

    data = qry.order("id").range(offset, offset + limit - 1).execute().data or []
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
        .data or []
    )
    ex = (
        supabase()
        .table(EXAMPLES_TABLE)
        .select("*")
        .or_(f"jp.ilike.{like},es.ilike.{like},en.ilike.{like},title.ilike.{like},pattern.ilike.{like}")
        .limit(limit)
        .execute()
        .data or []
    )
    return {"query": q, "points": gp, "examples": ex}

# ----------------- QUIZ -----------------
def _load_points(level_code: Optional[str]) -> List[GrammarPoint]:
    q = supabase().table(POINTS_TABLE).select("*")
    if level_code:
        q = q.eq("level_code", level_code)
    rows = q.limit(500).execute().data or []
    return [GrammarPoint(**r) for r in rows]

def _load_examples(grammar_ids: Optional[List[str]] = None, limit: int = 1500) -> List[Example]:
    """NO usa level_code en la tabla examples (no existe). Filtra por grammar_id si se pasa."""
    q = supabase().table(EXAMPLES_TABLE).select("*")
    if grammar_ids:
        q = q.in_("grammar_id", grammar_ids)
    try:
        rows = q.limit(limit).execute().data or []
    except APIError:
        rows = []
    return [Example(**r) for r in rows]

def _q_pattern(p: GrammarPoint, pool: List[GrammarPoint]) -> QuizQuestion:
    correct = (p.pattern or "").strip() or "—"
    candidates = [x.pattern for x in pool if x.id != p.id and (x.pattern or "").strip()]
    distractors = _sample(candidates, 3)
    choices = distractors + [correct]
    random.shuffle(choices)
    return QuizQuestion(
        id=p.id,
        type="pattern",
        prompt=f"¿Qué patrón corresponde a: «{(p.meaning_es or p.title or '').strip()}»?",
        choices=choices,
        answer_idx=choices.index(correct),
        meta={"level": p.level_code},
    )

def _q_meaning(p: GrammarPoint, pool: List[GrammarPoint], lang: str = "es") -> QuizQuestion:
    correct = (p.meaning_es if lang == "es" else p.meaning_en) or p.title or "—"
    candidates = [(x.meaning_es if lang == "es" else x.meaning_en) or x.title for x in pool if x.id != p.id]
    distractors = _sample([c for c in candidates if c], 3)
    choices = distractors + [correct]
    random.shuffle(choices)
    show = (p.pattern or p.title or "").strip()
    return QuizQuestion(
        id=p.id,
        type="meaning",
        prompt=f"¿Cuál es el significado de «{show}»?",
        choices=choices,
        answer_idx=choices.index(correct),
        meta={"level": p.level_code},
    )

def _q_translation(ex: Example, pool: List[Example], lang: str = "es") -> QuizQuestion:
    correct = (ex.es if lang == "es" else ex.en) or ""
    candidates = [(x.es if lang == "es" else x.en) or "" for x in pool if x.id != ex.id]
    candidates = [c for c in candidates if c and c != correct]
    distractors = _sample(candidates, 3)
    choices = distractors + [correct]
    random.shuffle(choices)
    return QuizQuestion(
        id=ex.id or "",
        type="translation",
        prompt="Elige la traducción correcta:",
        jp=ex.jp,
        choices=choices,
        answer_idx=choices.index(correct),
        meta={"grammar_id": ex.grammar_id},
    )

def _q_cloze(ex: Example, gp_lookup: Dict[str, GrammarPoint], pool_points: List[GrammarPoint]) -> QuizQuestion:
    pattern = None
    same_level_points: List[GrammarPoint] = pool_points
    if ex.grammar_id and ex.grammar_id in gp_lookup:
        gp = gp_lookup[ex.grammar_id]
        pattern = gp.pattern
        same_level_points = [p for p in pool_points if p.level_code == gp.level_code] or pool_points

    masked = _hide_pattern(ex.jp, pattern)
    correct = (pattern or ex.pattern or "—").strip()

    candidates = [p.pattern for p in same_level_points if p.pattern and p.pattern != correct]
    if len(candidates) < 3:
        candidates = [p.pattern for p in pool_points if p.pattern and p.pattern != correct]
    distractors = _sample(list(dict.fromkeys(candidates)), 3)
    choices = distractors + [correct]
    random.shuffle(choices)
    return QuizQuestion(
        id=ex.id or "",
        type="cloze",
        prompt="Completa la oración:",
        jp=masked,
        choices=choices,
        answer_idx=choices.index(correct),
        meta={"grammar_id": ex.grammar_id},
    )

@app.get("/quiz", response_model=List[QuizQuestion])
def quiz(
    level_code: Optional[str] = Query(None, description="N5..N1"),
    n: int = Query(10, ge=1, le=50),
    type: str = Query("mix", pattern="^(mix|cloze|pattern|meaning|translation)$"),
    lang: str = Query("es", pattern="^(es|en)$"),
):
    # puntos base
    points = _load_points(level_code)
    if not points:
        raise HTTPException(status_code=404, detail="No hay puntos gramaticales para ese filtro.")
    gp_by_id = {p.id: p for p in points}

    # ejemplos si hace falta
    examples: List[Example] = []
    if type in ("mix", "cloze", "translation"):
        examples = _load_examples([p.id for p in points]) or _load_examples(None)

    questions: List[QuizQuestion] = []

    def add_cloze():
        ex_pool = [e for e in examples if e.jp]
        if not ex_pool:
            return False
        questions.append(_q_cloze(random.choice(ex_pool), gp_by_id, points))
        return True

    def add_translation():
        ex_pool = [e for e in examples if (e.es if lang == "es" else e.en)]
        if not ex_pool:
            return False
        questions.append(_q_translation(random.choice(ex_pool), ex_pool, lang))
        return True

    def add_pattern():
        questions.append(_q_pattern(random.choice(points), points))
        return True

    def add_meaning():
        questions.append(_q_meaning(random.choice(points), points, lang))
        return True

    builders = {
        "cloze": add_cloze,
        "translation": add_translation,
        "pattern": add_pattern,
        "meaning": add_meaning,
    }

    if type == "mix":
        order = ["cloze", "pattern", "meaning", "translation"]
        while len(questions) < n:
            for t in order:
                if len(questions) >= n:
                    break
                if not builders[t]():
                    # intenta alternativas cuando no hay datos suficientes
                    for alt in order:
                        if builders[alt]():
                            break
    else:
        build = builders[type]
        while len(questions) < n:
            if not build():
                # pequeño fallback por si faltan datos del tipo solicitado
                for alt_name, alt in builders.items():
                    if alt_name != type and alt():
                        break
            if len(questions) > 100:
                break

    random.shuffle(questions)
    return questions[:n]

# --- Arranque local / Render ---
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8001))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
