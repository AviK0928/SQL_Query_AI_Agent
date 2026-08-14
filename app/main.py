"""FastAPI application: Serves the API and (once it exists) the frontent.
Errors are returned as HTTP 200 with a populated field rather than
as HTTP error codes, so the frontend has one response shape to handle.
A failed query is a normal outcome of this application, not a transport failure."""

import os
import uuid

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent import MAX_HISTORY_TURNS, ask
from app.db import get_schema

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(_PROJECT_ROOT, "frontend")
INDEX_FILE = os.path.join(FRONTEND_DIR, "index.html")

app = FastAPI(title="SQL Query AI Agent", version="1.0.0")

# session_id -> list of {"question": str, "sql": str}
# Process-local and lost on restart (L3). Acceptable: a single free-tier
# instance, and no requirement for durable conversation history.
_sessions = {}
MAX_SESSIONS = 500


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    session_id: str | None = None

class ChatResponse(BaseModel):
    answer: str
    sql: str | None = None
    columns: list = []
    rows: list = []
    truncated: bool = False
    error: str | None = None
    out_of_scope: bool = False
    session_id: str

@app.get("/health")
def health():
    """Liveness check. Deliberately touches neither the database nor the LLM."""
    return {"status": "ok"}


@app.get("/schema")
def schema():
    """The database structure, for the frontend to display."""
    return get_schema()


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    session_id = request.session_id or str(uuid.uuid4())
    history = _sessions.get(session_id, [])

    try:
        result = ask(request.question, history)
    except Exception as exc:
        # Never leak a stack trace or a provider error verbatim to the client.
        print(f"[chat] unhandled error: {type(exc).__name__}: {exc}")
        return JSONResponse(
            status_code=200,
            content={
                "answer": "Something went wrong while answering that. Please try again.",
                "sql": None, "columns": [], "rows": [], "truncated": False,
                "error": "internal_error", "out_of_scope": False,
                "session_id": session_id,
            },
        )

    # Only successful queries are worth replaying as context.
    if result["sql"] and not result["error"]:
        history = history + [{"question": request.question, "sql": result["sql"]}]
        _sessions[session_id] = history[-MAX_HISTORY_TURNS:]

    if len(_sessions) > MAX_SESSIONS:
        _sessions.pop(next(iter(_sessions)))

    return {**result, "session_id": session_id}


# --- frontend -----------------------------------------------------------
# Mounted only if it exists: `frontend/` is empty until Phase 6, and git does
# not track empty directories, so it is absent from a fresh clone (P6).

if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    if os.path.exists(INDEX_FILE):
        return FileResponse(INDEX_FILE)
    return {"message": "API is running. The frontend has not been built yet.",
            "endpoints": ["/health", "/schema", "/chat", "/docs"]}
