"""FastAPI application: serves the API and the frontend.

Errors are returned as HTTP 200 with a populated field rather than as HTTP
error codes, so the frontend has one response shape to handle. A failed query
is a normal outcome of this application, not a transport failure. The `error`
field carries an error code (app/sql/errors.py, or an LLM_* code from
app/llm/client.py), never database or provider text. `request_id` ties a
response to its LLM call-log lines.

The app is built by create_app(settings, llm=None). Settings are validated
before anything else is constructed, so a missing GROQ_API_KEY or GROQ_MODEL
stops startup with a ConfigError instead of failing on the first request.
Spring comparison: create_app is the @Configuration class; Agent, Database,
ReadOnlyExecutor and SessionStore are the beans it wires together.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent import Agent
from app.config import DEFAULT_ENV_FILE, PROJECT_ROOT, Settings, load_settings

FRONTEND_DIR = PROJECT_ROOT / "frontend"
INDEX_FILE = FRONTEND_DIR / "index.html"
MAX_SESSIONS = 500
ENV_FILE: Path | None = DEFAULT_ENV_FILE  # tests set this to None to ignore a local .env


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    session_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sql: str | None = None
    columns: list = []
    rows: list = []
    truncated: bool = False
    limit_reached: bool = False
    error: str | None = None
    out_of_scope: bool = False
    session_id: str
    request_id: str | None = None
    needs_clarification: bool = False


class SessionStore:
    """session_id -> recent {"question", "sql"} turns.

    Process-local and lost on restart (L3). A lock guards it because FastAPI
    runs sync endpoints in a threadpool (A-11). Oldest session is evicted
    first once MAX_SESSIONS is exceeded.
    """

    def __init__(self, max_sessions: int, max_turns: int):
        self._data: OrderedDict[str, list[dict[str, str]]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.max_turns = max_turns

    def get(self, session_id: str) -> list[dict[str, str]]:
        with self._lock:
            return list(self._data.get(session_id, []))

    def append(self, session_id: str, question: str, sql: str) -> None:
        with self._lock:
            history = self._data.get(session_id, []) + [{"question": question, "sql": sql}]
            self._data[session_id] = history[-self.max_turns :]
            if len(self._data) > self.max_sessions:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


def create_app(settings: Settings | None = None, *, llm: Any = None) -> FastAPI:
    """Build the application. `llm` is injected by tests; production builds Groq."""
    if settings is None:
        settings = load_settings(env_file=ENV_FILE)  # raises ConfigError on bad config

    app = FastAPI(title="SQL Query AI Agent", version="1.1.0")
    app.state.settings = settings
    app.state.agent = Agent(settings, llm=llm)
    app.state.sessions = SessionStore(MAX_SESSIONS, settings.max_history_turns)

    @app.get("/health")
    def health():
        """Liveness check. Deliberately touches neither the database nor the LLM."""
        return {"status": "ok"}

    @app.get("/schema")
    def schema(request: Request):
        """The database structure, for the frontend to display."""
        return request.app.state.agent.db.get_schema()

    @app.post("/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, request: Request):
        agent: Agent = request.app.state.agent
        sessions: SessionStore = request.app.state.sessions
        session_id = body.session_id or str(uuid.uuid4())

        try:
            result = agent.ask(body.question, sessions.get(session_id))
        except Exception as exc:
            # Never leak a stack trace or a provider error verbatim to the client.
            print(f"[chat] unhandled error: {type(exc).__name__}")
            return JSONResponse(
                status_code=200,
                content={
                    "answer": "Something went wrong while answering that. Please try again.",
                    "sql": None,
                    "columns": [],
                    "rows": [],
                    "truncated": False,
                    "limit_reached": False,
                    "error": "internal_error",
                    "out_of_scope": False,
                    "session_id": session_id,
                    "request_id": None,
                    "needs_clarification": False,
                },
            )

        # Only successful queries are worth replaying as context.
        if result["sql"] and not result["error"]:
            sessions.append(session_id, body.question, result["sql"])
        elif result.get("needs_clarification"):
            # Keep the clarifying exchange, so the follow-up answer has its context.
            sessions.append(session_id, body.question, f"CLARIFY: {result['answer']}")

        return {**result, "session_id": session_id}

    # --- frontend -------------------------------------------------------
    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index():
        if INDEX_FILE.exists():
            return FileResponse(INDEX_FILE)
        return {
            "message": "API is running. The frontend has not been built yet.",
            "endpoints": ["/health", "/schema", "/chat", "/docs"],
        }

    return app


def __getattr__(name: str) -> FastAPI:
    """Lazy `app` for `uvicorn app.main:app` (Render's start command, unchanged).

    Importing this module does not load settings; the first access to
    `app.main.app` does. Tests import the module freely and call create_app().
    """
    if name == "app":
        instance = create_app()
        globals()["app"] = instance
        return instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
