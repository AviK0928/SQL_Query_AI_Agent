# SQL Query AI Agent

[![CI](https://github.com/AviK0928/SQL_Query_AI_Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/AviK0928/SQL_Query_AI_Agent/actions/workflows/ci.yml)

Ask questions about an e-commerce database in plain English. Get an answer, the
SQL that produced it, and the rows it returned.

**Live demo:** https://sql-query-agent-zxsx.onrender.com/
**Video walkthrough:** https://youtu.be/bXCRQufnvPY

> **Please allow up to a minute on first load.** The free tier sleeps after 15
> minutes of inactivity, so the first request has to wake the server. Once it
> responds, everything after that is fast. A blank page or a slow spinner on the
> very first visit is expected, not a fault.

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Components, request flow, security layers |
| [`docs/workflow.md`](docs/workflow.md) | The LangGraph state, nodes, edges and retry |
| [`PROMPTS.md`](PROMPTS.md) | Every prompt the application sends |
| [`DISCOVERIES.md`](DISCOVERIES.md) | Findings, decisions, and what went wrong |
| [`AUDIT.md`](AUDIT.md) | Phase 0 audit of the pre-refactor code and the live baseline |
| [`notebooks/dev.ipynb`](notebooks/dev.ipynb) | Colab driver notebook: setup, quality gate, run, live eval, push |

Every design decision, limitation and security choice is recorded as a tagged
entry in [Decisions and records](#decisions-and-records) below.

---

## The problem

Anyone who cannot write SQL cannot query a database. Handing a language model
direct database access solves that and creates a worse problem: the model can be
persuaded to write anything, including `DROP TABLE`.

So the interesting part is not translating English to SQL. It is doing that when
the thing generating the SQL cannot be trusted.

## Features

- Natural-language questions to SQL
- Generated SQL shown in the UI, collapsible
- Results rendered as a table
- Follow-up questions using conversation history
- Out-of-scope questions politely rejected
- One automatic retry when a query fails
- Database physically cannot be written to, whatever the model produces

## Screenshots

![Empty state](docs/screenshots/01-empty-state.png)

![A question, its SQL, and the results](docs/screenshots/02-query-with-sql.png)

![An out-of-scope request](docs/screenshots/03-rejection.png)

## Architecture

```mermaid
flowchart LR
    U[Browser] --> F[FastAPI]
    F --> A[LangGraph agent]
    A <--> G[LLM gateway<br/>cache, call log]
    G <--> C[LLM client<br/>rate limiter, retries, fallback]
    C <--> L[Groq LLM]
    A --> V[SQL validator<br/>sqlglot AST]
    V --> X[Read-only executor<br/>authorizer, timeout, row cap]
    X --> D[(SQLite<br/>read-only)]
    X --> A
    A --> F --> U
```

One service serves both the API and the frontend. The model never touches the
database directly — everything it produces is parsed and checked by a sqlglot validator (S1),
and only a validated query reaches the executor, whose connection is read-only
and guarded by a SQLite authorizer (S2). Full detail in [`docs/architecture.md`](docs/architecture.md).

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Agent | LangGraph — 5 nodes, 1 conditional edge |
| LLM | Groq, model set by `GROQ_MODEL` (currently `openai/gpt-oss-120b`, free tier, no card) |
| Database | SQLite, file committed to the repo |
| Frontend | HTML, CSS, vanilla JS — no framework, no build |
| Config | pydantic-settings, validated at startup |
| Tests | pytest, pytest-cov, httpx; ruff and mypy for lint and types |
| Hosting | Render free tier |

Total cost: nothing.

## Setup

Python 3.12 or 3.13.

```bash
git clone https://github.com/AviK0928/SQL_Query_AI_Agent.git
cd SQL_Query_AI_Agent

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -e ".[dev]"         # app plus test and lint tools

cp .env.example .env            # then set GROQ_API_KEY and GROQ_MODEL

python database/build_db.py     # optional; the .db file is already committed

uvicorn app.main:app --reload
```

Open http://localhost:8000

A free Groq API key takes about a minute at
[console.groq.com](https://console.groq.com) — email sign-up, no credit card.

**Google Colab:** open `notebooks/dev.ipynb` from GitHub in Colab and run the
cells in order. The notebook header lists which cells to rerun after a VM
recycle (P7).

## Configuration

All settings are read once at startup by `app/config.py` (D14). Missing or
invalid values stop the app with an error that names the variable and never
prints its value (S3). Environment variables override `.env`.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `GROQ_API_KEY` | Yes | — | Never logged or echoed |
| `GROQ_MODEL` | Yes | — | No default on purpose (D14); check the Groq catalog |
| `DB_PATH` | No | `database/ecommerce.db` | Relative paths resolve against the repo root |
| `MAX_ROWS` | No | `200` | Row cap per query |
| `QUERY_TIMEOUT_S` | No | `5.0` | SQLite query timeout |
| `MAX_HISTORY_TURNS` | No | `3` | Conversation turns replayed to the model |
| `MAX_REPAIR_ATTEMPTS` | No | `1` | Repairs of a failed query (0–3); each costs one model call |
| `LLM_LIMITS` | Yes | — | Per-model limits from the Groq console, as JSON: `{"model": {"rpm": …, "rpd": …, "tpm": …, "tpd": …}}`. Every model the app can call needs an entry; never hard-coded (D22) |
| `GROQ_FALLBACK_MODEL` | No | none | Used on persistent 429s or a retired model; must differ from `GROQ_MODEL` |
| `LLM_ROLE_MODELS` | No | `{}` | Per-role overrides as JSON; roles: `classifier`, `sql_generator`, `sql_repair`, `synthesizer`, `judge` |
| `LLM_SAFETY_MARGIN` | No | `0.8` | Share of each limit the client may use |
| `LLM_MAX_WAIT_S` | No | `20` | Longest a request waits for rate-limit capacity before failing fast |
| `LLM_TIMEOUT_S` | No | `30` | Per HTTP call to Groq |
| `LLM_MAX_ATTEMPTS` | No | `3` | Per model, including the first try |
| `LLM_CACHE_PATH` | No | off | Response cache file; set in dev and evals, not in production |
| `LLM_LOG_CONTENT` | No | `false` | Log prompts and responses; keep off in production |
| `LLM_LOG_PATH` | No | stdout | JSONL file for the LLM call log |

`.env` is gitignored. Never commit it.

## Running the tests

```bash
pytest -q
```

**695 tests (T22), no API key needed, no network calls.** The language model is
replaced by a scripted fake. The suite is offline by construction, not by
convention: a guard in `tests/conftest.py` removes every setting from the
environment and blocks and records any non-loopback network attempt, failing
the test even if the app swallowed the error (T1). Everything beneath the
model — request validation, the graph, the SQL validator, the real database
file — runs for real.

The SQL safety layer is also covered by hypothesis property tests (T6) and a
manual mutation-testing run with mutmut (T7): `mutmut run "app.sql.validator*"
"app.sql.executor*"`, configured in `pyproject.toml`.

Rendered prompts are snapshot-tested (T14). After an intended prompt change,
run `pytest tests/unit/test_prompt_snapshots.py --snapshot-update` and review
the `.ambr` diff before committing it.

The full quality gate, the same checks CI runs:

```bash
ruff check . && ruff format --check . && mypy app && pytest -q --cov
```

`pytest --cov` fails below 100% line + branch coverage of `app` and `evals`
(`fail_under = 100` in `pyproject.toml`, T23), in CI and in notebook Cell 9.
New code arrives with its tests.

Git hooks run ruff, gitleaks, mypy and basic file checks on every commit
(P8). Install them once per clone with `pre-commit install`.

CI (`.github/workflows/ci.yml`) runs the same gate on every pull request and
push to `main`, on Python 3.12 and 3.13, plus bandit, pip-audit and gitleaks
(D17). A pull request cannot merge into `main` unless all four checks pass (P10).

## Using it

Ask anything answerable from four tables: customers, products, orders,
order_items.

- "Which 3 customers have spent the most?"
- "How many orders were cancelled?"
- "Which customers have never ordered?"
- Then follow up: "What about just the ones in Mumbai?"

Click **Show generated SQL** under any answer to see the query. Anything
unrelated to the database — coding questions, general knowledge, requests to
modify data — gets a polite refusal.

## API

| Method | Path | Returns |
|---|---|---|
| `GET` | `/health` | `{"status": "ok"}` |
| `GET` | `/schema` | Table and column names |
| `POST` | `/chat` | `answer`, `sql`, `columns`, `rows`, `truncated`, `limit_reached`, `error`, `out_of_scope`, `session_id`, `request_id`, `needs_clarification` |
| `GET` | `/` | The frontend |

`POST /chat` takes `{"question": "...", "session_id": "..."}`. The session id is
optional on the first request and returned in the response; send it back to keep
conversation context.

Failed queries return HTTP 200 with `error` set to an error code such as
`EXECUTION_ERROR` or `FORBIDDEN_WRITE` (`app/sql/errors.py`), or an `LLM_*` code
such as `LLM_RATE_LIMITED` (`app/llm/client.py`), never database or provider
text (H1), so the frontend has one response shape to handle. `request_id` ties a
response to its LLM call-log lines (H3). Rejected input returns `INPUT_EMPTY`,
`INPUT_TOO_LONG` or `INPUT_NO_TEXT` without a model call, and `needs_clarification`
marks an answer that is a clarifying question (D27, D28). Malformed requests return 422.

## Deployment

Render free web service, deployed from `main`:

- Build: `pip install -r requirements.txt` (the file delegates to `pyproject.toml`, D12)
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Environment: `GROQ_API_KEY` and `GROQ_MODEL` set in the Render dashboard, never in the repo

The SQLite file is committed, so there is no database to provision.

## Limitations

- Money stored as `REAL`, so large sums accumulate floating-point error
- Conversation memory is in-process and lost when the server restarts
- Free tier sleeps after 15 minutes; first request takes 30-60 seconds
- A query's own `LIMIT` below the row cap is kept (the prompt still asks for
  `LIMIT 100`); when it is hit exactly the response sets `limit_reached` and the
  answer is told more rows may exist (L5)
- Prompt rules are followed most of the time, not always; anything that must
  hold is enforced in code instead
- Summaries of large result sets can overstate, since only 20 rows are sent to
  the model
- **A generated query can be valid, safe, and still answer the wrong question.**
  Nothing in the system can detect this, which is why the SQL is always shown
- **Update (3 Sep 2026)** — Groq decommissioned `llama-3.3-70b-versatile`
  on 16 Aug 2026. Migrated to `openai/gpt-oss-120b` via the `GROQ_MODEL`
  environment variable on the host. The regression query (top 3 customers by
  revenue) returned identical figures under the new model, and the
  `unit_price` / cancelled-order rules in the prompt held. The code default was
  left stale at the time (A-01 in `AUDIT.md`); since Phase 1 there is no code
  default at all (D14).
- Groq free-tier quotas cap throughput; tokens per minute, not requests, is
  the binding limit (L4)
- Rate-limit daily counters live in memory and reset on restart; Groq's own
  headers re-sync the request count on the next call (L7)

Each of these is explained, with the conditions under which it actually bites,
in [`DISCOVERIES.md`](DISCOVERIES.md).

## Decisions and records

Tags: `D` decision, `L` limitation, `S` security, `T` testing, `H` data
handling, `P` process, `V` verification. New entries continue each series.
Entries are never renumbered; superseded entries stay, marked as such.

Tags cited in code or docs before the Phase 1 refactor were reconstructed from
where they are cited. Numbers that were never cited anywhere cannot be
recovered and are listed as such rather than invented.

### Decisions

| Tag | Decision | Where |
|---|---|---|
| D1 | Revenue and order totals use `order_items.unit_price * quantity` (the price actually paid), not `products.price`. | `app/prompts.py`, `tests/test_prompts.py` |
| D2 | The prompt is not a security boundary. Prompt rules reduce retries and cost; every guarantee is enforced in code (S1, S2). | `app/prompts.py` |
| D3–D10 | Not recoverable (pre-refactor, never cited). | — |
| D11 | *Superseded by D15.* Liveness (`/health`) must not depend on the LLM provider; tested by running `/health` with no API key. | — |
| D12 | `pyproject.toml` is the single source of dependencies. `requirements.txt` contains only `-e .` so Render's build command did not change; editable so `app/` resolves `database/` and `frontend/` from the repo. | `pyproject.toml`, `requirements.txt` |
| D13 | Every direct dependency is pinned exactly, from a clean-venv `pip freeze` (24 Sep 2026). Transitive packages are observed, not pinned: `langchain-core 1.6.4`, `groq 0.37.1`, `pydantic 2.13.5`. | `pyproject.toml` |
| D14 | All configuration goes through typed `Settings`. `GROQ_MODEL` has no default, so a decommissioned model cannot survive as a silent fallback (A-01). Startup is refused on missing or invalid settings. | `app/config.py` |
| D15 | Supersedes D11. The app no longer starts without a key, so "no key" is not a runnable state. D11's intent is tested directly: `/health` returns 200 with zero LLM calls and zero database calls. | `tests/test_api.py::test_health_never_touches_the_llm_or_db` |
| D16 | Dependencies are injected: `create_app(settings, llm=None)` builds `Agent`, `Database` and `SessionStore`. No module-level clients, graphs or session dicts. `app.main.app` is built lazily on first access so `uvicorn app.main:app` keeps working without a start-command change. | `app/main.py`, `app/agent.py`, `app/db.py` |
| D17 | CI on every PR and push to `main`: lint and types first (fail fast), then tests on Python 3.12 and 3.13 and security scans in parallel. Read-only token permissions, no secrets, never calls Groq. Actions are pinned to major tags and kept current by Dependabot, which also proposes weekly pip updates, each gated by CI. The repo is public, so Actions minutes are free. | `.github/workflows/ci.yml`, `.github/dependabot.yml` |
| D18 | SQL is validated by parsing it with sqlglot (SQLite dialect), replacing the regex validator (A-03). Checks run specific to general: size; first-token classification (writes and PRAGMA/ATTACH/VACUUM/EXPLAIN… identified by token, so their codes are stable across sqlglot versions); exactly one statement; no write node anywhere in the tree; the root must be a SELECT or set operation; no forbidden functions (`load_extension`, `pragma_*`, table-valued functions in FROM); every table in an allowlist read from `sqlite_master` (CTE names exempt, schemas other than `main` rejected). The SQL that runs is regenerated from the checked tree (comments removed) and must itself re-parse. | `app/sql/validator.py` |
| D19 | The row cap is owned by code. The outermost `LIMIT` becomes `cap + 1` when absent or at/above the cap, and the executor fetches `cap + 1` rows, so `truncated` is exact (fixes A-02 for the cap). A query `LIMIT` below the cap is kept and reported as `query_limit`; when hit exactly, `limit_reached` is set and the answer prompt says more rows may exist. The answer prompt's notes (rows hidden beyond 20, truncated, limit reached) are added independently. | `app/sql/validator.py`, `app/sql/executor.py`, `app/prompts.py` |
| D20 | Typed error taxonomy: 13 `SqlErrorCode`s raised as `SqlSafetyError(code, detail)`. Only `PARSE_ERROR`, `UNKNOWN_TABLE`, `EXECUTION_ERROR` and `INVALID_LIMIT` earn the single retry; a forbidden write, stacked statement, timeout or other final code does not, saving a Groq request. Users get a fixed message per code; `detail` goes only to the retry prompt and logs, and the constructor rejects an empty one (found by mutation testing, T7). | `app/sql/errors.py`, `app/agent.py` |
| D21 | API response (Phase 3): `sql` is the SQL that passed validation and ran, or `null` when nothing passed, so rejected SQL is never shown as the query that ran; `error` is an `SqlErrorCode` value; new field `limit_reached`. The frontend uses `error` only as a flag, so the UI is unchanged. | `app/agent.py`, `app/main.py` |
| D22 | LLM configuration is typed and explicit. Per-model Groq limits come from `LLM_LIMITS` (JSON copied from the console), and startup is refused if any model the app can call (`GROQ_MODEL`, `GROQ_FALLBACK_MODEL`, `LLM_ROLE_MODELS`) lacks an entry, so no limit is hard-coded or remembered. Models are resolved per role through `ModelRegistry`; every role uses `GROQ_MODEL` until Phase 6 assigns them with evals. The fallback is optional and must differ from the primary. `Database.schema_hash()` fingerprints the schema definition (not the data) for reproducibility records. Deployments must set `LLM_LIMITS` before this code runs. | `app/config.py`, `app/llm/registry.py`, `app/db.py` |
| D23 | Model calls go through one stack built from `Settings`: the Groq SDK transport (SDK retries off) → `LlmClient` (per-model rate limiter; tenacity retries on 429, 5xx and timeouts, honouring `retry-after` up to `LLM_MAX_WAIT_S`; fallback to `GROQ_FALLBACK_MODEL` on a persistent 429 or a retired model) → `LlmGateway` (cache when `LLM_CACHE_PATH` is set, call log, usage). Nodes call by role (`sql_generator`, `sql_repair`, `synthesizer`) with a request id per question. A failure during generation or repair returns a fixed answer with an `LLM_*` code and no SQL; a failure during the summary keeps the rows. The API gains `request_id`; `internal_error` remains only for unexpected faults, so the A-04 guard test now expects `LLM_UNAVAILABLE`. `langchain-groq` was removed. | `app/llm/`, `app/agent.py`, `app/main.py` |
| D24 | Each prompt has an id derived from its text (`name@` + 8 hex characters of a SHA-256), sent with every model call: any edit changes the id, which invalidates cached answers and shows in every call-log line which prompt text was used. Phase 7 replaces this with versioned prompt files. At Phase 4: `sql_gen@27e9e81d`, `sql_repair@a5c30252`, `answer@3c3a3566`. | `app/prompts.py`, `tests/unit/test_prompt_ids.py` |
| D25 | `groq==0.37.1` and `tenacity==9.1.4` are direct, exact pins (D13), because the client imports them. tenacity's typed `stop_any` accepts only `stop_base` instances, so the "retry-after longer than the maximum wait" stop is a small `stop_base` subclass; strict mypy caught this, while every test passed. | `pyproject.toml`, `app/llm/client.py` |
| D26 | The agent is a package (`app/agent/`): `graph.py` wires LangGraph, `state.py` types the state, and each node's decision is a pure function in `app/agent/nodes/` (guard, classify, check), testable without a model, database or graph. Flow: `guard_input → generate_sql → classify_intent → validate → execute → (repair) → format_answer → check_answer`. Deviation from the planned order, on purpose: classification reads the generator's reply instead of making its own model call, so one call does both and the two can never disagree. `load_relevant_schema` is deferred: with four tables, table selection saves few tokens and risks dropping a needed table; it is added when the schema grows. | `app/agent/` |
| D27 | Ambiguous questions get a clarifying question: the SQL prompt tells the model to reply `CLARIFY: <question>` when an answer needs a choice the user has not made (for example what "best" is measured by). The response sets `needs_clarification`, and the exchange is kept in the session history so the user's follow-up has its context. Measured before keeping it (PROMPTS.md). | `app/prompts.py`, `app/agent/nodes/classify.py`, `app/main.py` |
| D28 | `guard_input` rejects empty, over-500-character and letterless questions with `INPUT_EMPTY`, `INPUT_TOO_LONG` or `INPUT_NO_TEXT` before any model call, so they cost no Groq request. | `app/agent/nodes/guard.py` |
| D29 | `check_answer` makes honest disclosure guaranteed rather than requested: when a result is empty, capped or limited and the answer does not say so, a fixed sentence is appended. Numbers in the answer that cannot be traced to the rows, the row count or the question are recorded as `UNSUPPORTED_NUMBERS` in `answer_checks` but not rewritten, since derived figures would cause false alarms. Text is compared after NFKC normalisation with Indian digit grouping handled. No model call. | `app/agent/nodes/check.py` |
| D30 | `MAX_REPAIR_ATTEMPTS` (0–3, default 1: the previous behaviour) sets how many times a repairable error is sent back to the model. Each repair increments `retry_count`, and routing stops at the setting, so the loop is bounded by construction. Raising it costs one call per extra repair; Phase 6 decides with evidence. | `app/config.py`, `app/agent/graph.py` |
| D31 | Models are selected per role by a framework fixed before any selection run: gates (availability and production status, context window, ≥90% format compliance, no prompt leak), then weighted scores against fixed targets, reported with bootstrap intervals; near-ties go to correctness, then efficiency. Priorities agreed: correctness, refusal and clarity, injection resistance, column minimisation, reliability, efficiency. | `MODEL_SELECTION.md` |
| D32 | Live evals can be run from GitHub Actions (manual `workflow_dispatch`, a small subset, the Groq key as a repo secret, the report uploaded as an artifact). Built in Phase 6 rather than Phase 10, so a live run can be triggered and its evidence downloaded from the GitHub UI. Never runs on push. | `.github/workflows/eval.yml` (Phase 6) |
| D33 | Eval items grade correctness, not representation. An item may list `alt_reference_sql` alternates that differ from its reference only in representation (a month label, a fraction instead of a percentage); `tests/unit/test_golden_v2.py` pins every alternate to the reference's column and row counts, and pins which items have alternates (g13, g19). An ambiguous item is rewritten (g39), never given alternates with different answers. `golden_v1` is unchanged because earlier reports cite its hash. Details in EVALS.md. |
| D34 | Generator shortlist from the smoke screen (26 Sep 2026; 15 items chosen by a fixed rule, one per tier): `qwen/qwen3.8-27b` and `openai/gpt-oss-120b` go to full runs. `allam-2-7b` was dropped: it missed a T1 filter, generated a `DELETE` (blocked by the validator), and false-refused a clear question. `gpt-oss-20b` was dropped as a generator candidate (it failed a T3 join and had the lowest hard-tier score) but stays the fallback model, a role chosen for availability. Smoke results only drop models (L10). |
| D35 | Live tests (`tests/live/`, `pytest -m live`) are deselected by default (`addopts -m 'not live'`), so `pytest` and CI stay offline. The folder overrides the offline guard, fails any test missing the `live` marker, and fails rather than skips when configuration is missing, because a skipped live test is green while checking nothing. They run with the response cache and the fallback off, so every answer is a fresh call to the configured model. |
| D36 | Long eval runs execute as a detached background process in Colab (log file, exit code at its end, a read-only status cell), one at a time, so the notebook stays usable. While a run is active, commits stage explicit paths only, never `evals/reports/`. |
| D37 | `synthesizer` = `openai/gpt-oss-120b` (26 Sep 2026; 8 items × 3 repeats × 4 models). qwen3.8-27b scored 1.000 but is excluded by the availability gate (Preview on Groq's Models page, V7); allam-2-7b is not a production model. gpt-oss-120b (0.997) and gpt-oss-20b (0.996) tie under §3 (both have correctness 1.000); the tie goes to efficiency, where 120b has the higher quota headroom (0.970 vs 0.956). |
| D38 | `sql_repair` = `openai/gpt-oss-120b` (10 items × 3 repeats × 4 models). The eligible candidates tie under §3 with equal repair success (0.700), and 120b wins on efficiency (p95 0.84 s, against 2.40 s for 20b). Its only failures are the shared repair-prompt defect (L14); 20b also returned empty replies (L18), and qwen, which is Preview anyway, produced invalid SQL on r10. |
| D39 | The availability gate stays as written: a Preview model cannot be a primary, even qwen3.8-27b, the strongest generator in its full run (0.898). Relaxing the gate after seeing which model it excludes would be the post-hoc tuning D31 forbids. qwen's promotion to Production is a re-selection trigger (its reports already exist), and it is the candidate for the Phase 7 judge: Preview models are intended for evaluation, and the judge should come from a different family than the generator. |
| D40 | `sql_generator` = `openai/gpt-oss-120b` (full run on golden_v2 plus adversarial, 3 repeats, 27 Sep 2026): 0.819 (0.729–0.898), all gates computed and passing. It is the only eligible candidate: qwen3.8-27b scored 0.898 but is Preview (D39). Under §3 the two would tie (overlapping correctness intervals) and the tie would go to qwen, so qwen's promotion is a real re-selection trigger. Every role is on gpt-oss-120b, so configuration is unchanged: `GROQ_MODEL` as deployed, `LLM_ROLE_MODELS` empty. |
| D41 | Phase 7 baseline: run `2026-09-27-full-gpt-oss-120b` (commit `0114cc0`; prompts `sql_gen@5fb4fe06`, `sql_repair@a5c30252`, `answer@3c3a3566`; golden `0866d69057d7`, adversarial `15faddf25c4e`). Total 0.819, execution accuracy 0.899, hard tiers 0.741, refusal and clarity 0.792, consistency 0.848. Prompt changes must beat it with no regression in refusal correctness. The 26 Sep repair and synthesizer runs are the baselines for those roles. |
| D42 | Prompts are versioned files in `app/prompts/` (`<name>.v<N>.md`, plus `answer_notes.v<N>.toml` for the short result notes), selected by `ACTIVE_VERSIONS`. A released file is never edited: a change is the next version, and a hash manifest test fails on any edit to a released file or any unregistered file. The logged id stays `name@hash8` of the rendered text (D24) rather than becoming the version number, because it also changes when a shared part changes (the schema file, a token) and it kept the Phase 7 baseline ids valid (D41). PROMPTS.md maps each version to its id. Placeholders use `string.Template` (`$schema`), since prompts contain `{}` and `%Y` but never `$`, and rendering fails on a missing or unused value. v1 is byte-identical to the previous `app/prompts.py`; earlier entries citing `app/prompts.py` (D1, D2, D19, D24, D27, L5, S4) now refer to `app/prompts/`. | `app/prompts/`, `tests/unit/test_prompt_versions.py`, PROMPTS.md |
| D43 | Response judge (Phase 7): rubric `evals/judges/response.v1.md` scores the six Section 10c criteria from 1 to 5 with a reason, and embeds the schema file and business rules P1 and P2 so correct domain filters are not marked wrong. It is called under the `judge` role at temperature 0 through the gateway (limiter, cache, call log). The judge model is set per run, never in production configuration, which never calls it. No fallback, correcting the step 7.0 plan: a fallback would grade part of a run with a second, uncalibrated judge; if the judge model is retired the run stops and a replacement is calibrated first. Replies must validate strictly against `Verdict`; an invalid reply is a recorded parse failure, never re-asked, so the judge's format compliance is measured. The case is sent as JSON (answer text cannot break out of it) with up to 50 rows and the full count. Prompt id `judge_response@a69f7604`, pinned in tests. Scores are not used until calibrated against hand labels (EVALS.md). | `evals/judges/`, `tests/unit/test_judge_response.py` |
| D44 | Judge calibration (D43): 26 cases fixed by rule from the D41 run (repeat 0, answered with SQL; the first two per tier by id plus every item that failed a deterministic check), labelled by hand and blind, following `evals/judges/CALIBRATION.md`. A criterion is trusted when the judge is within one point of the label on at least 80% of items and its pass/fail call (4-5 pass, 1-3 fail) matches on at least 85%; a parse failure counts as a disagreement. The bars were fixed before any judge score was seen and are not lowered afterwards; an untrusted criterion is not used in Phase 7 decisions until a revised rubric passes. The sheet shows the reference SQL and whether the result matched, so labels reflect correctness rather than impressions. | `evals/judges/calibration.py`, `evals/judges/run.py`, EVALS.md |
| D45 | Scope of P1, a clarification (27 Sep 2026): cancelled orders were placed, so order counts, order lists and who-ordered-when questions include them; revenue, spend, turnover, units sold and revenue shares exclude them. Inventory is not in the schema, so stock questions are out of scope. The golden references already follow this. The SQL prompt's rule 5 states only the exclusion half, the likely cause of the over-exclusion in A-20 (g13, g14); stating the full rule is a queued Phase 7 experiment. | EVALS.md, `evals/judges/CALIBRATION.md` |
| D46 | Judge calibration result (run `2026-09-27-judge-calib-qwen3.8-27b`, `qwen/qwen3.8-27b`, rubric `response.v1`; 26 items, 0 parse failures). Trusted under D44: faithfulness (96% within one point, 96% pass/fail match), relevance (88/88), completeness (88/88), clarity (92/88). Not trusted: honesty (88/77) and sql_intent (85/73), below the 85% pass/fail bar; they are not used in Phase 7 decisions. SQL correctness stays measured by execution accuracy. Label provenance: the user labelled all 26 items; in review, 29 scores on 13 items (all six criteria on g22, g34 and g35) were drafted by the assistant against the written rules and accepted by the user, so agreement on those items partly compares the judge with a second model. A revised rubric is never re-scored on these same labels (that would overfit); it is validated on items outside this set. | `evals/reports/2026-09-27-judge-calib-qwen3.8-27b/`, `evals/judges/calibration_v1_labels.csv` |
| D47 | Deterministic `false_disclosure` grader, in place of the untrusted judge honesty criterion (D46) for the failures found: the answer claims rows are withheld while the result is complete, meaning neither truncated nor limit_reached, or an intended top-N (the query's own LIMIT was reached and the question asked for exactly that many rows). A LIMIT the question did not ask for (g18's LIMIT 1 hiding a tie, g34's LIMIT 100) is genuinely partial, so a warning there is not flagged. It uses a stricter "withholding" pattern than the disclosure check, which accepts any "only" or "first". Reported in every eval report and in the failures table, not scored: the scoring framework was fixed before Phase 6 (D31) and changing it would break comparison with D41. The metric for the L13 and L21 experiments. | `evals/graders.py`, `evals/runner.py` |
| D48 | Phase 7 baseline on the new measures, extending D41 without regenerating any answer: judge `qwen/qwen3.8-27b` (rubric `response.v1`) on the 35 answered items of repeat 0, trusted criteria only (D46): faithfulness 4.74 (pass 91%), relevance 5.00 (pass 100%), completeness 4.77 (pass 94%), clarity 4.91 (pass 100%); 35 verdicts, 26 answered from the calibration cache. False partial-result warnings (D47) over all 3 repeats: 17 of 107 answers. Experiments are judged on repeat 0 only, the same way; consistency stays measured by the deterministic metrics over all repeats. | `evals/reports/2026-09-27-judge-d41-qwen3.8-27b/` |
| D49 | Prompt experiments measure a released but inactive version: `python -m evals.runner … --prompt NAME=VERSION` renders it exactly as production would (`app/prompts/variants.py`) and records the measured ids and the override in the run's manifest. Overrides apply only to the repair and synthesizer suites, which build their own messages; the agent suites always run `ACTIVE_VERSIONS`, so a generator-prompt experiment activates the candidate on its branch instead. A candidate becomes active only after its experiment is recorded in PROMPTS.md. | `evals/runner.py`, `app/prompts/variants.py` |
| D50 | Experiment 1 kept: the repair prompt v2 (`sql_repair@cbe7c9f7`) adds the revenue and cancelled-order rules (D45 wording) to v1, changing nothing else. On the repair suite (gpt-oss-120b, 10 items x 3 repeats, temperature 0, 12 s pacing, as the 26 Sep baseline) repair success rose from 0.700 to 1.000: r01, r08 and r10 went from 0/3 to 3/3, and no item that passed under v1 failed. Decision rule fixed before the run: keep only if success rises and nothing regresses. Resolves L14. The agent path is re-checked with every accepted change in the final Phase 7 run. | `app/prompts/sql_repair.v2.md`, `evals/reports/2026-09-27-exp1-repair-v2/` |
| D51 | Prompt overrides reach the agent: `Agent(..., prompt_versions={name: version})` replaces the system prompt and logged id of `sql_gen`, `sql_repair` or `answer` for that agent only, rendered once at construction so a bad name or version fails before any call. Production never passes it and runs `ACTIVE_VERSIONS`. The runner passes `--prompt` to the golden and adversarial suites, so a generator-prompt candidate is measured without being activated (this supersedes D49's "activate on the branch"); the role suites still refuse prompts they never send. | `app/agent/graph.py`, `evals/runner.py` |
| D52 | L12 resolved in the grader, following the T8 spec ("clarify or state its assumption") and the calibration labels: a `clarify` item passes if the model asks, or answers without error and names the basis it chose ("based on", "assuming", "measured by", "ranked by", "by revenue" ...). A silent guess still fails, and so does listing several measures without naming one (g24). D41 re-scored without any model call (`evals/reports/2026-09-27-full-gpt-oss-120b-d52/`, source untouched): g22 passes in repeats 0 and 2; total 0.819 -> 0.832, refusal and clarity 0.792 -> 0.875. Phase 7 compares against the re-scored figures, so baseline and experiments use one rule. | `evals/graders.py`, `evals/runner.py --rescore` |
| D53 | The remaining Phase 7 experiments are pre-registered in PROMPTS.md section 8 before any run: candidates `answer.v2` (exp 2), `sql_gen.v2` (exp 3, D45 scope plus a `cancelled_orders` column), `sql_gen.v3` (exp 4, rule 3 removed) and `sql_gen.v4` (exp 6, ties), each one change from v1, released and inactive; their items, keep rules and token estimates are fixed there. `synthesizer_v2` adds four items built from the database with the L13 and L21 shapes (s09-s12) to v1's eight, unchanged. | PROMPTS.md, `evals/datasets/synthesizer_v2.jsonl` |
| D59 | Experiment 2 kept: the answer prompt v2 (`answer@4b837d88`) appends two rules to v1: the user sees every returned row, so warn about hidden rows only when a note says the result was truncated or the query's LIMIT was reached; and a question that asked for N rows and got exactly N is complete. On `synthesizer_v2` (gpt-oss-120b, 12 items x 3 repeats, temperature 0, 12 s pacing, v1 and v2 run the same day) false partial-result warnings (D47) fell from 15/36 to 6/36, with the required disclosures (s03, s04, s05: 9/9) and deterministic faithfulness (36/36) unchanged; answer tokens +29%. Decision rule fixed before the run (D53): all three conditions met. s10 (47 rows, 20 shown to the model) still warns on every repeat, so L13 and L21 are mitigated, not resolved. Numbered after the highest D anywhere in the stack (D58, `feat/phase-11-docs`), so rebases cannot duplicate tags. The runner at this commit predates D54: these calls record no schema hash; the schema was `207e7a26b02f`. | `app/prompts/answer.v2.md`, `evals/reports/2026-09-28-exp2-answer-v1/`, `evals/reports/2026-09-28-exp2-answer-v2/` |
| D60 | Experiment 3 kept: `sql_gen.v2` (`sql_gen@d5145f66`) rewrites rule 5 with the D45 scope (cancelled orders counted when counting or listing orders, excluded from revenue, spend, turnover and units sold) and asks for a `cancelled_orders` column when they change the result. On its pre-registered items (gpt-oss-120b, 3 repeats, temperature 0, 20 s pacing, `sql_repair` and `answer` pinned to v1 so `sql_gen` is the only change from D41 (re-scored, D52), compared on the same items and repeats): g14 0/3 -> 3/3; the 10 controls stayed 3/3. The column's purpose was not shown: answers mentioned cancelled orders on 1 of the 6 targets where they matter (g13), reported either way as pre-registered, and some results gain columns (g17: `email`, `cancelled_orders`). Combined into v5 (D63). Judge on repeat 0 (`qwen/qwen3.8-27b`, trusted criteria, D46, compared with D48 item by item): no item scored below D41 on any criterion; means equal except completeness 4.64 -> 4.73. | `evals/reports/2026-09-28-exp3-sql-gen-v2/`, `evals/reports/2026-09-28-judge-exp3-qwen3.8-27b/` |
| D61 | Experiment 4 reverted: `sql_gen.v3` (`sql_gen@485384f7`) drops rule 3 (`LIMIT 100`). g34 returned all 190 unordered pairs instead of 100, but the pre-registered rule was not met: g34 3/3 -> 0/3 and false warnings 12 -> 15 over the items. Both come from g34: it is graded on disclosure and a complete result has nothing to disclose, and the three new warnings are g34's answers under the pinned answer v1 ("first 20 of 190"). The evidence points at the item (L20), not the change, but the rule fixed before the run decides. L5 stays open; v3 is to be re-tested as a new pre-registered experiment after golden_v3. Judge on repeat 0 (`qwen/qwen3.8-27b`, trusted criteria, D46, compared with D48 item by item): means faithfulness 4.10 -> 4.30, completeness 4.20 -> 4.50, clarity 4.70 -> 4.90; two scores fell, both on answers written by the pinned answer v1 (g07 faithfulness 2 -> 1 for a false "first 20 of 30" warning, L21; g35 completeness 4 -> 2 for summarising 47 rows). Reported only: the decision stands on the pre-registered rule. | `evals/reports/2026-09-28-exp4-sql-gen-v3/`, `evals/reports/2026-09-28-judge-exp4-qwen3.8-27b/` |
| D62 | Experiment 6 kept: `sql_gen.v4` (`sql_gen@8c7d4fa8`) adds rule 7: for top, most or least, keep every row tied at the cut-off by filtering on the value instead of cutting with LIMIT. g18 0/3 -> 3/3 (both tied cities); the 5 controls stayed 3/3; false warnings on the controls 9 -> 0, as tie-aware queries no longer end on the model's own LIMIT. Combined into v5 (D63). Judge on repeat 0 (`qwen/qwen3.8-27b`, trusted criteria, D46, compared with D48 item by item): 5 on every criterion for all 6 items, as D41. | `evals/reports/2026-09-28-exp6-sql-gen-v4/`, `evals/reports/2026-09-28-judge-exp6-qwen3.8-27b/` |
| D63 | `sql_gen.v5` (`sql_gen@43cea818`) = v1 + v2's rule 5 (D60) + v4's rule 7 (D62), nothing else (pinned by a test); rule 3 stays (D61). Released and inactive: the final Phase 7 run measures it with `--prompt sql_gen=5` on the full golden and adversarial suites, with `sql_repair.v2` and `answer.v2` active, against D41 (re-scored, D52) and D48, and it is activated only if that run meets the Phase 7 DoD. It is also the first run of the two kept changes together. Exps 3, 4 and 6 used 113,379 tokens. | `app/prompts/sql_gen.v5.md` |
| D64 | Every judge call sets `max_tokens` to 800 (`JUDGE_MAX_TOKENS`). On 29 Sep 2026, after the first two calls of the final-v5 judge run, Groq refused every judge call made through the SDK with a 429: the free tier enforces an output-tokens-per-minute limit on `qwen/qwen3.8-27b` (1,000), and a call without `max_tokens` is counted as 2,048 expected output tokens. The rate-limit headers do not report this limit; it was found by replaying the call with the SDK error body printed. 800 is 2.7x the largest verdict in 88 earlier judge calls (294 tokens), so no earlier verdict would have been cut, and a cut reply is recorded as a parse failure, never as a score. The rubric and the judge prompt id are unchanged, so the calibration (D46) stands. `max_tokens` is part of the cache key, so verdicts cached without the cap are not reused. Judge runs pace at 60 s, so the reserved output stays under 1,000 per minute. | `evals/judges/response.py` |
| D65 | Phase 7 final run; `sql_gen.v5` activated (`ACTIVE_VERSIONS["sql_gen"] = 5`, `sql_gen@43cea818`) alongside the already active `sql_repair.v2` and `answer.v2`. The run used the full golden and adversarial suites on gpt-oss-120b, 3 repeats, temperature 0, 20 s pacing, 144 records and 0 errors, compared with D41 re-scored under D52. Total 0.832 -> 0.828, and the run is now eligible because every gate passes. Execution accuracy 0.899 -> 0.970, hard tiers 0.741 -> 0.889, consistency 0.848 -> 1.000, injection resistance 1.000 in both. Refusal and clarity 0.875 -> 0.750 (L27). Column minimisation 0.663 -> 0.483 and tokens per question 1,138 -> 1,460, mostly the D45 `cancelled_orders` column returned where it changes nothing and the longer answer v2 prompt. Latency p50 1.07 -> 1.58 s. False partial-result warnings 17/107 -> 8/108. Judge on repeat 0 (`qwen/qwen3.8-27b`, output capped by D64, trusted criteria, D46, compared with D48 item by item): faithfulness 4.74 -> 4.71, relevance 5.00 -> 5.00, completeness 4.77 -> 4.74, clarity 4.91 -> 4.94; one real drop (g20 completeness 5 -> 2). Against the Phase 7 DoD, execution accuracy is beaten, judge scores are flat and refusal regressed, so v5 is active by decision (P26). The runner and judge on this branch predate D54, so their manifests carry no schema hash; the agent's call log records `207e7a26b02f`. | `app/prompts/__init__.py`, `evals/reports/2026-09-29-final-v5/`, `evals/reports/2026-09-29-judge-final-v5-qwen3.8-27b/` |

### Limitations

| Tag | Limitation | Where |
|---|---|---|
| L1–L2 | Not recoverable (pre-refactor, never cited). | — |
| L3 | Conversation sessions are process-local and lost on restart. Acceptable on a single free-tier instance. Access is now lock-guarded (D16). | `app/main.py::SessionStore` |
| L4 | Groq free-tier limits for `openai/gpt-oss-120b`, read 24 Sep 2026: 30 RPM, 1K RPD, 8K TPM, 200K TPD. Measured ~590 tokens per call, so TPM is the binding limit (~10 calls/min with a 20% margin). Re-check in the Groq console; limits change. Re-checked 25 Sep 2026: unchanged; `openai/gpt-oss-20b` has the same limits. | `AUDIT.md` §5, `.env.example` |
| L5 | Prompt rule 3 still asks the model for `LIMIT 100`, below the 200-row cap. Such a result is not truncated by code but may be incomplete; this is disclosed through `limit_reached` (D19). Removing the rule is a prompt change, so it waits for Phase 7 evals. Experiment 4 (D61) removed it and was reverted under its pre-registered rule; to be re-tested once golden_v3 fixes g34 (L20). | `app/prompts.py` |
| L6 | The frontend's truncation note hard-codes "capped at 200". It matches the `MAX_ROWS` default but does not follow the setting. | `frontend/app.js` |
| L7 | The rate limiter's daily counters live in memory, so a restart forgets how much of the day's quota was used. Groq's `x-ratelimit-remaining-requests` header re-syncs the request count on the next call. The cache and call-log files are not rotated. | `app/llm/limiter.py`, `app/llm/calllog.py` |
| L8 | The Phase 0 baseline grader credits only the `truncated` flag, so b13 (the model's own `LIMIT 100` cutting a 300-row result) still fails although the system now flags it as `limit_reached` and discloses it; and no deterministic check can catch b14's wording error ("three customers" for 3 orders). The grader is left unchanged to keep runs comparable; Phase 6's harness and judge address both. | `evals/baseline/run_baseline.py` |
| L9 | `UNSUPPORTED_NUMBERS` can flag legitimately derived figures (percentages, differences), so it is a signal for evals and logs, not a verdict, and it never changes the answer text. | `app/agent/nodes/check.py` |
| L10 | Smoke-screen intervals are wide at n=15 (for example 0.573–0.940), so the smoke screen only drops clear failures; choices come from full runs with 3 repeats. |
| L11 | The "Run evals" button (`eval.yml`) can only be dispatched once the file is on `main`; until the Phase 6 merge, live evals run from Colab. |
| L12 | *Resolved by D52.* The `clarify` kind accepts only a clarifying question, which is stricter than the T8 wording in the project instructions ("clarify or state its assumption"). Every candidate is graded by the same rule, so model selection is fair; whether an explicitly stated assumption should pass is decided in Phase 7, where a judge can assess it. |
| L13 | *Partly addressed by D59:* the answer model's own false warnings on intended top-N results fell (`synthesizer_v2` s06 0/3, s11 1/3, s12 1/3, from 3/3 each); whether `check_answer` adds one on the agent path is measured in the final Phase 7 run. Suspected: `check_answer` adds a truncation disclosure when the model's own `LIMIT` is reached (g18: "only the first rows are shown" after `LIMIT 1`). There it was true, because a tied city was hidden, but for an intended top-N it would be a false disclosure. To be measured in Phase 7 before any change. |
| L14 | *Resolved by D50 (repair prompt v2).* The repair prompt (`sql_repair@a5c30252`) has the schema but not the domain rules, so a repaired revenue query includes cancelled orders (policy P1). All four models failed r01, r08 and r10 for this reason. In production it affects only questions whose first query fails. Phase 7 experiment #1; the repair suite measures it directly. |
| L15 | Under MODEL_SELECTION §3, candidates tie when their correctness intervals overlap. When correctness saturates (for example, all at 1.000), every candidate ties and clarity factors cannot separate them. It did not change an outcome in Phase 6; the rule is revisited at re-selection, not changed after the results (D31). |
| L16 | The synthesizer suite (8 items) is saturated: every production candidate scores about 1.0 on deterministic checks, so it barely discriminates. The Phase 7 faithfulness judge is the planned addition. |
| L17 | The Groq models API does not report Production or Preview status. The availability gate computes listing and active status from the dated catalog snapshot; production status is checked by hand on Groq's Models page and recorded (V7). |
| L18 | gpt-oss-20b, the fallback model, returned empty replies on repair item r08 (2 of 3 repeats), and its largest output used 2,048 tokens. Suspected cause: reasoning exhausts the output budget. Unverified; to be checked before relying on the fallback in Phase 7 or Phase 9. |
| L19 | Prompt files are read from the source tree next to `app/prompts/__init__.py`. That works with the editable install Render and Colab use (D12), but a non-editable install would omit the `.md` and `.toml` files, because `pyproject.toml` declares no package data. To be fixed with the Phase 10 Docker image. | `app/prompts/loader.py`, `pyproject.toml` |
| L20 | Golden reference issues found while labelling (27 Sep 2026), for the next golden version: g34's reference (`a.id != b.id`) counts every pair twice (380 rows; 190 unordered pairs exist); g32's reference (`order_date < '2024-01-01'`) is looser than the question's year; g20's "for each customer" wording conflicts with its P4 tag, which excludes customers who never spent. No Phase 6 decision changes: g32 and g34 grade identically on this data (g34 is graded on disclosure only), and qwen answered g20 as the reference does. Experiment 4 (D61) showed the cost: without rule 3, g34 returned all 190 unordered pairs, a complete and correct result, and graded 0/3, because the item is graded on disclosure. golden_v3 should give g34 the `a.id < b.id` reference and grade it as an `sql` item. | `evals/datasets/golden_v2.jsonl` |
| L21 | *Mitigated by D59:* false warnings on complete results fell from 15/36 to 6/36 on `synthesizer_v2`; s10 (47 rows, 20 shown) still warns 3/3, and `check_answer` still does not detect false warnings. The answer model can warn that only some rows are shown when every row came back: g07 (30 rows) and g35 (45 rows) were answered with "only the first 20 … are shown", because the synthesizer is given 20 rows plus the rows-hidden note. The user sees a false partial-result warning. Broader than L13 (a top-N query's own LIMIT: g16, g17, g25). `check_answer` (D29) adds missing disclosures but does not detect false ones. | `app/prompts/answer.v1.md`, `app/agent/nodes/check.py` |
| L26 | Groq also enforces an output-tokens-per-minute limit per model, which `LLM_LIMITS` cannot express and the rate-limit headers do not report, so the client learns of it only as a 429 (D64). Agent calls still send no `max_tokens`, so the same refusal could hit gpt-oss-120b if Groq enforces the limit there; the final-v5 run (29 Sep 2026) was not affected. A failed call's log line records neither the HTTP status nor the provider's error body, so such a 429 cannot be told from a quota 429 without replaying the call by hand. Both belong with the client and call-log work on the Phase 9 branch. Numbered after the highest L in the stack (L25). | `app/llm/client.py`, `app/llm/calllog.py` |
| L27 | Ambiguous questions (T8) regressed in the final Phase 7 run (D65). Under D52, g22 ("most valuable customers") fell from 2/3 to 0/3 and g24 ("compare how the categories are performing") from 1/3 to 0/3. On g22 the SQL is equivalent to D41's, but answer v2 no longer names the measure it used ("based on total spend"). Neither answer prompt asks for that, so v1 passed by chance. On g24, v5 answers instead of asking which metric (D41 asked on one repeat). Unsafe and out-of-scope refusals are unaffected. Planned fix: an answer rule that names the chosen measure when the question leaves it open, measured as a pre-registered experiment. | `app/prompts/answer.v2.md`, `app/prompts/sql_gen.v5.md` |

### Security

| Tag | Control | Where |
|---|---|---|
| S1 | SQL validator, the first layer: sqlglot AST validation (D18) replaced the regex validator in Phase 3, closing A-03's false rejections and comment bug. It fails cheaply with a typed code (D20) and hands the executor only a `ValidatedQuery`. It is not the enforcement point: S2 holds even if S1 has a bug. | `app/sql/validator.py` |
| S2 | The enforcement layer: `ReadOnlyExecutor` opens SQLite read-only (`mode=ro`) with an authorizer that denies everything except SELECT/READ/FUNCTION/RECURSIVE (which also blocks ATTACH and PRAGMA, allowed by `mode=ro` alone), a progress-handler timeout and a `cap + 1` fetch. It accepts only a `ValidatedQuery`. Tested on its own: forged queries that bypass S1 (DELETE, ATTACH, PRAGMA writable_schema) are denied and logged, and a raw write on its connection fails without the authorizer. | `app/sql/executor.py`, `tests/unit/test_sql_executor.py` |
| S3 | `ConfigError` never contains input values. pydantic's own `ValidationError` embeds `input_value`, which can include the API key, so it is replaced and suppressed (`from None`). | `app/config.py`, `tests/test_config.py` |
| S4 | bandit scans `app/` in CI. Its four findings at introduction were false positives, all in `app/prompts.py`, suppressed line by line with `# nosec <code>` and a reason: B105 on the `OUT_OF_SCOPE`/`READ_ONLY` refusal markers (not credentials) and B608 on the two system prompts (text for the LLM, never executed as SQL). bandit prints "nosec encountered … but no failed test" warnings for lines inside those multi-line strings; they do not fail the scan and disappear in Phase 7, when prompts move to versioned files. | `app/prompts.py`, `pyproject.toml` |
| S5 | Dependency and secret scanning in CI. pip-audit checks every installed package against known advisories; its first run found PYSEC-2026-1845 in `pytest 8.4.2`, fixed by upgrading the pin to `9.0.3` (same 106 tests collected and passing). gitleaks scans the full commit history on every run; the first run over all history was clean (24 Sep 2026). | `.github/workflows/ci.yml`, `pyproject.toml` |
| S6 | The API key cannot reach the call log: every record is scrubbed of the configured key before it is written, including prompt text, responses and error details, and unexpected exceptions are logged by type only, never by message. Tested, and confirmed on the first live run (V1). | `app/llm/calllog.py`, `tests/unit/test_llm_calllog.py` |
| S7 | `eval.yml` runs only on manual dispatch, never on push or PR, with `contents: read`. Inputs reach the shell only through environment variables and are regex-checked (the `pr-title.yml` rule). The Groq key is passed only to the steps that call Groq. One concurrency group (`groq-live`) runs live jobs one at a time, so two runs never share a daily quota. Reports are uploaded as artifacts, never committed from CI. |

### Testing

| Tag | Record | Where |
|---|---|---|
| T1 | The default test run is offline by construction. An autouse guard removes every setting from the environment and blocks and records non-loopback DNS and connections; any recorded attempt fails the test at teardown, even if the app swallowed the error (A-04). | `tests/conftest.py`, `tests/test_offline_guard.py` |
| T2 | 106 tests collected and passing (`pytest --collect-only`), 24 Sep 2026: first at `e79f31c`, re-confirmed after the Phase 1 lint and format pass. Up from 79 at `f6e44c0`. | `tests/` |
| T3 | The Phase 1 lint and format pass did not change any test. Proven by comparing the syntax tree of all 146 `assert` statements before and after `ruff format` and `ruff check --fix`, and the full syntax tree of each hand-edited file (identical). | Phase 1 notebook cells P1-13, P1-15 |
| T4 | CI fails if fewer than `MIN_TESTS` (106) tests are collected, so tests cannot disappear silently. Raising the number is a deliberate edit in `ci.yml`. The suite runs on Python 3.12 (Render) and 3.13 (Colab). | `.github/workflows/ci.yml` |
| T5 | 226 tests collected and passing, 25 Sep 2026 at `4e2935e` (106 before Phase 3). The 32 regex-validator tests were ported with their original expectations, now also asserting error codes. Four agent/API expectations changed deliberately and became stricter: no retry after a forbidden write or stacked statement, `error` is a code, rejected SQL is not returned as `sql`. `app/sql`, `app/agent.py` and `app/db.py` are at 100% line and branch coverage. `MIN_TESTS` raised to 226 (T4). | `tests/`, `.github/workflows/ci.yml` |
| T6 | Property tests with hypothesis (derandomized `ci` profile, 200 examples; `HYPOTHESIS_PROFILE=dev` runs 5,000): totality over random text and SQL-token soup (anything accepted is re-checked independently), stacked statements never accepted, case- and comment-obfuscated writes always `FORBIDDEN_WRITE`, and the row-cap rule for any LIMIT and cap. The totality property found a real bug on its first run: a bare `SELECT` was accepted and regenerated as `SELECT LIMIT 201`; fixed by re-parsing the regenerated SQL. | `tests/unit/test_sql_properties.py`, `tests/unit/conftest.py` |
| T7 | Mutation testing (mutmut 3.8, manual, 25 Sep 2026) on `app/sql/validator.py` and `app/sql/executor.py` against `tests/unit`: 394 mutants, 324 killed + 3 timeouts = 83.0%. The first run left 116 survivors; the real gaps were closed with 14 tests and one guard (D20). All 67 remaining survivors are classified: 12 unreachable by the tool (`_classes` runs at import), 19 equivalent (listed in DISCOVERIES), 36 message wording (policy: tests pin the information a message carries, not its phrasing). Excluding the 31 unkillable: 327/363 = 90.1%. | `pyproject.toml` `[tool.mutmut]` |
| T8 | The offline guard derives the list of environment variables it clears from `Settings` itself, so a newly added setting can never leak from the host environment into a test. | `tests/conftest.py`, `tests/test_config.py` |
| T9 | 354 tests collected and passing at `3b2ff78`, 25 Sep 2026 (226 before Phase 4); `app/llm` at 100% line and branch coverage. Every resilience path (429 bursts, `retry-after`, over-long waits, retired models, timeouts, 5xx, daily budgets, header feedback) is tested against a scripted transport and a fake clock, so no test waits or touches the network. The A-04 guard test now expects `LLM_UNAVAILABLE` and makes one attempt. The schema-hash method changed: `207e7a26b02f` replaces Phase 0's `cace08063546` as the baseline; the schema itself did not change. | `tests/unit/test_llm_*.py`, `tests/test_offline_guard.py` |
| T10 | 414 tests at Phase 5 (354 before); every node decision has unit tests with plain inputs. The package move changed no test (354 → 354, proving it behaviour-neutral). The Phase 0 baseline was re-run three times with the unchanged grader: before Phase 5 11/15, after the `CLARIFY` rule 12/15, final 12/15 (11/14 graded automatically, b08 by review). | `tests/`, `evals/baseline/results/` |
| T11 | 6 live tests (`tests/live/test_live_groq.py`, about 8 calls): a gateway round trip with usage and rate-limit headers, a `COUNT` checked against a reference query, two write requests refused with the database unchanged, a clarification, and an empty result disclosed. Collection at `39ceff5`: 513 offline tests, plus 6 live tests deselected (`513/519`). |
| T12 | `MIN_TESTS` in `ci.yml` was restored from 226 to 508. It had not been raised since Phase 3 (T4 requires raising it deliberately), so CI would not have noticed up to 282 tests disappearing. |
| T13 | 521 offline tests at `0114cc0`: 513, plus 3 for the gate helpers, 3 end-to-end gate tests through `main()`, and 2 for the runner's `--date` option; plus 6 live tests, deselected by default. `MIN_TESTS` raised to 521 (T4). |
| T14 | 547 offline tests (521 at T13, 20 for the prompt loader, file versions and baseline id pins, 6 prompt snapshots) plus 6 live tests. Rendered messages for every kind of call are snapshot-tested with syrupy 6.1.1 (`tests/unit/__snapshots__/`); accepting a change needs `pytest tests/unit/test_prompt_snapshots.py --snapshot-update` and a reviewed diff of the `.ambr` file. `MIN_TESTS` raised to 547 (T4). | `tests/unit/test_prompt_*.py`, `.github/workflows/ci.yml` |
| T15 | 569 offline tests (547 at T14, plus 22 for the response judge: rubric and id pins, case rendering, strict parsing with 9 invalid verdicts, the call through FakeLLM, one snapshot) plus 6 live tests. `MIN_TESTS` raised to 569 (T4). `commit_and_push` (notebook Cell 12, v3) runs this count check and the full offline gate before every commit. | `tests/unit/test_judge_response.py`, `.github/workflows/ci.yml`, `notebooks/dev.ipynb` |
| T16 | 587 offline tests (569 at T15, plus 18 for calibration: the selection rule pinned on the D41 run and on the committed cases file, case building with its row-count check, the label sheet round trip and its error reports, agreement at the threshold boundary, and judge runs through FakeLLM with blind output, parse failures, a stop on 429 and resume). `MIN_TESTS` raised to 587 (T4). | `tests/unit/test_judge_calibration.py`, `.github/workflows/ci.yml` |
| T17 | 603 offline tests (587 at T16, plus 16 for `false_disclosure`: 10 baseline answers from calibration, 3 ordinary uses of "only" and "first", number words, the synthesizer path, and a runner run checking the record and the report line). `MIN_TESTS` raised to 603 (T4). | `tests/unit/test_false_disclosure.py` |
| T18 | 609 offline tests (603 at T17, plus 6 for judging a whole run: the D41 selection pinned at 35 and 107 answered items, calibration cases a subset of it, the repeat filter and resume by item and repeat, the summary maths, and pacing skipped on cache hits). `MIN_TESTS` raised to 609 (T4). | `tests/unit/test_judge_baseline.py` |
| T19 | 621 offline tests (609 at T18, plus 12: 11 for prompt variants, and one more case of the released-file hash test for `sql_repair.v2.md`; first recorded as 620 by mistake. The 11 cover: active versions render byte-identical to production, repair v2 adds only its rules block, override parsing, the override sent and recorded on a repair run, and the agent suites refusing overrides). `MIN_TESTS` raised to 621 (T4). | `tests/unit/test_prompt_variants.py` |
| T20 | 626 offline tests (621 at T19, plus 4 for agent overrides: only the named prompt and id change and every other message is byte-identical, the default sends the active prompts, a bad name or version fails at construction; plus 1 for the role suites refusing prompts they never send). One expectation changed on purpose: agent suites now accept and record overrides instead of refusing them (D51). `MIN_TESTS` raised to 626 (T4). | `tests/unit/test_agent_prompt_versions.py`, `tests/unit/test_prompt_variants.py` |
| T21 | 642 offline tests (626 at T20, plus 12 for the Phase 7 candidates: each changes only its declared lines, `synthesizer_v2` extends v1 unchanged, the new items' shapes, the D52 rule on real baseline answers, and the D41 re-score flipping exactly g22 r0 and r2; plus 4 immutability cases for the new prompt files). `MIN_TESTS` raised to 642 (T4). | `tests/unit/test_phase7_candidates.py` |
| T22 | 692 offline tests (642 at T21, plus 50 closing every gap in the Phase 8 coverage report at `88ba920`). Grading is now tested on every item kind the live runs grade: adversarial leak checks, empty and truncation items, clarifying turns replayed as history, required synthesizer disclosures, and resume after a non-429 error. Judge runs: confirmation, non-429 errors, the default LLM stack with only the judge role changed, CLI routing, rubric-file validation, and label sheets with missing columns or blank rows. The committed calibration set and the D48 case file are rebuilt exactly from their runs through the production safety layer. App: cache hits counted apart from calls, NULL cells in `check_answer`, the API without a frontend. Coverage (line + branch) now also measures `evals/` and is enforced per scope in CI, floors only going up: `app/sql`, `app/llm`, `app/agent` 100%, `app` 99%, `evals` 90%. Measured at this commit: `app` 100.0%, `evals` 100.0% (99.6% and 88.5% in the report at `88ba920`). Not measured: `if __name__ == "__main__":` lines, and `evals/baseline/` (the Phase 0 live harness, T10). `MIN_TESTS` raised to 692 (T4). | `pyproject.toml`, `.github/workflows/ci.yml`, `tests/unit/test_eval_grading.py`, `tests/unit/test_judge_cli.py` |
| T23 | Coverage floor raised to 100% line + branch for all of `app` and `evals`, agreed 27 Sep 2026 after T22 measured 100% in every scope (T22's floors were `app/sql`, `app/llm`, `app/agent` 100, `app` 99, `evals` 90). With every scope at 100 the per-scope CI loop added nothing, so it was replaced by one `fail_under = 100` in `pyproject.toml`: `pytest --cov` itself fails, in CI and in notebook Cell 9 (`commit_and_push` runs pytest without coverage, so CI and Cell 9 are the gates). Test count unchanged at 692. | `pyproject.toml`, `.github/workflows/ci.yml` |
| T31 | 644 offline tests (642 at T21, plus 2: the `sql_gen.v5.md` release pin, and a test that v5 is v1 plus exactly v2's rule 5 and v4's rule 7, with rule 3 kept). Numbered after the highest T in the stack (T30). `MIN_TESTS` raised to 644 (T4). | `tests/unit/test_phase7_candidates.py`, `tests/unit/test_prompt_versions.py` |
| T32 | 645 offline tests (644 at T31, plus 1: the judge output cap stays under Groq's 1,000 output-tokens-per-minute limit and at least twice the largest measured verdict). The judge-call test now also expects `max_tokens` (D64). `MIN_TESTS` raised to 645 (T4). | `tests/unit/test_judge_response.py`, `.github/workflows/ci.yml` |

### Data handling

| Tag | Record | Where |
|---|---|---|
| H1 | Errors reach the client as codes, never database text. SQLite messages go only to the retry prompt and logs (D20, D21). Tested: a failing query's API response contains no `no such column`. | `app/agent.py`, `tests/test_api.py` |
| H2 | Open, for Phase 9: sqlglot logs a warning containing the SQL text when it falls back to parsing a statement as a raw command (for example `SHOW TABLES`), so model output can reach the logs. To be fixed by lowering sqlglot's log level when logging is designed. | `app/sql/validator.py` |
| H3 | The LLM call log records metadata by default: model, role, prompt id, schema hash, tokens, latency, attempts, cache hit, error code, message count and total characters, and only Groq's rate-limit headers. Prompt and response text are logged only with `LLM_LOG_CONTENT=true`, which stays off in production. The cache stores a SHA-256 of the prompt, never the prompt itself, plus the response text. | `app/llm/calllog.py`, `app/llm/cache.py` |
| H4 | Eval reports commit `calls.jsonl` with prompt and response content. This is acceptable because eval prompts contain only the synthetic sample database and fixed questions, never user data; production keeps `LLM_LOG_CONTENT` off (H3). |

### Process

| Tag | Record | Where |
|---|---|---|
| P1–P5 | Not recoverable (pre-refactor, never cited). | — |
| P6 | The `frontend/` static mount is conditional, because git does not track empty directories and the folder was absent from fresh clones before the frontend existed. | `app/main.py` |
| P7 | Development runs in Google Colab inside a project venv at `/content/venv`, isolated from Colab's preinstalled packages (A-23). Colab's Python lacks `ensurepip`, so the notebook falls back to `virtualenv`. A VM-recycle recovery run from an empty `/content` was completed on 24 Sep 2026. | `notebooks/dev.ipynb` |
| P8 | Git hooks via pre-commit: file hygiene checks (`pre-commit-hooks v6.0.0`), ruff lint and format (`v0.16.8`, equal to the `pyproject.toml` pin), gitleaks secret scanning (`v8.30.0`), and mypy from the project environment. Revs pinned with `pre-commit autoupdate --repo`, 24 Sep 2026. Markdown is excluded from ruff so docs keep their author's formatting. | `.pre-commit-config.yaml`, `pyproject.toml` |
| P9 | Three times a pull request was merged without its last intended commit: PR #2 (merge commit, stale head; two verified commits missing; fixed by #3), PR #4 (squash, merged before the docs commit landed; registry update missing; fixed by #13), and PR #14 (squash of a branch whose final commit, this row's correction, was never pushed; the merge commit `bc49311` is empty; fixed in the Phase 3 carry-over PR). All three were caught by comparing `main`'s tree with the last verified commit, not by commit ancestry (which squash merges break). `main` keeps the resulting history rather than being force-pushed. Rule since: before merging, the push cell must have succeeded and the PR's Commits tab must end at the hash it printed. | Notebook cells P1-17, P1-18, P2-8, P3-2 |
| P10 | `main` is protected: changes arrive only through pull requests, which merge only when all four CI checks pass. The repo allows squash merging only, with the PR title as the commit message, so each PR lands as one conventional commit (prevents P9). Force pushes and deletion of `main` are blocked. Pushing workflow files needs a PAT with **Workflows: Read and write** on this repository. | GitHub repository settings |
| P11 | Dependabot PR #8 (uvicorn 0.51.0 → 0.53.0) was merged as an empty squash commit (`ec8a5c2`): its change was lost resolving a conflict on the PR branch, so the history said uvicorn was bumped while `pyproject.toml` still pinned 0.51.0. Found in the Phase 2 close-out and restored in Phase 3. Dependabot now groups updates per ecosystem, so sibling PRs no longer conflict on the same `pyproject.toml` lines. Rule since: after merging any PR, `git show --stat` of the merge commit must list the expected files. | `.github/dependabot.yml`, `pyproject.toml` |
| P12 | Phase 3 landed in two PRs because #16 (validator and executor) was merged mid-phase. It was merged with GitHub's default title `Feat/phase 3 sql safety`, built from the branch name because the PR had several commits, so `main` has one non-conventional subject; history is kept. Its content was verified by fetching `refs/pull/16/head` (GitHub keeps it after the branch is deleted) and comparing it with the last verified commit `2ec48f8`, because the Colab clone no longer had that commit. Rule since: set the PR title by hand before merging. The rule was then missed for #17 and #18 (both `Feat/phase 3 agent wiring`); #19 used a hand-set conventional title. | Notebook cells P3-21, P3-22 |
| P13 | Three notebook-cell bugs made a correct state look wrong: `.strip()` on multi-line `git status --porcelain` cut the first line's status column; `git add` with a path already removed by `git rm` is a hard error; and a kernel restart drops `PATH` changes made by Cell 7, so the pre-commit mypy hook could not find `mypy`. Rules since: use `git diff --name-only`/`--name-status`; stage only files that exist; cells that commit re-apply the venv `PATH`. Separately, merging `main` back into the branch for #18 conflicted on `.gitignore`; the resolution kept `main`'s version and dropped `mutants/`. It was found by comparing the last verified commit (not the PR head, which was the unverified conflict-resolution merge) with `main`, and restored by #19. | Notebook cells P3-12, P3-23, P3-24, P3-35b, P3-36 |
| P14 | A recycled VM loses the git identity (it is repo-local config), and a finished step once stopped at `git commit` with its files left uncommitted. Commit cells now check the identity before doing any work. Separately, the assistant's local copy of `client.py` drifted from the repo, so changes to files already edited in Colab are delivered as anchored edits or new modules (the gateway), not rebuilt from a stale copy. | Notebook cells P4-8, P4-9 |
| P15 | PR titles are enforced by CI: the `pr-title` workflow fails any pull request whose title is not a conventional commit of at most 72 characters, and it re-runs when the title is edited. Added after #16, #17, #18 and #20 merged with GitHub's default branch-name titles despite P12's written rule; a rule a person must remember failed four times, a required check cannot be skipped. The title reaches the script through an environment variable, never inlined into it, so a crafted title cannot inject commands. Correction, 25 Sep 2026: the rules API showed the check running but not yet in ruleset 23925380's required list, so until it was added that day it could not block a merge (V4). | `.github/workflows/pr-title.yml`, ruleset 23925380 |
| P16 | Three checks fixed in Phase 5: git's rename detection reported `app/agent.py → app/agent/graph.py` as `R066`, so staged-set checks use `--no-renames`; the edit helper now rejects any new Python line over 100 characters before writing; and the baseline runner gained `--tag`, because re-running with an unchanged prompt hash would otherwise find Phase 0's file and skip every item. | Notebook cells P5-2, P5-9, P5-5 |
| P17 | Colab recovery gaps found on 26 Sep 2026: the recovery cells did not load `LLM_LIMITS`, Cell 7 puts the venv on `PATH`, but a kernel that had not run Cell 7 could not find pre-commit's mypy hook (Cell 0, which every recovery runs first, now sets it too), and Cell 11 hard-coded rate limits dated 24 Sep. Fixed in the notebook in Phase 6. Ad-hoc push cells now use Cell 12's per-command `extraheader`, which never writes the token to `.git/config`. |
| P18 | Two gates were never computed: `score_candidate` defaulted `available` and `context_ok` to `True`, and the runner never passed them, so every report showed unchecked passes. Found by reading the gate code before a decision. Fixed in `c2faead` (dated catalog snapshot, measured call-log tokens, unverified means fail), with end-to-end tests. Re-rendering all 13 reports changed nothing, so no decision had depended on the default. |
| P19 | Two Colab gaps found at step 7.1b, 27 Sep 2026. First, `pre-commit run --all-files` checks only tracked files, so the gate passed while the new, untracked snapshot file had trailing whitespace; the hook failed only at commit. Second, syrupy's snapshot format indents blank lines inside multi-line strings, so the trailing-whitespace hook rewrote a generated file. `tests/unit/__snapshots__/` is now excluded from that hook only; the other hooks and CI's gitleaks still scan it. Rules since: every step cell ends with `commit_and_push` (replacing Cell 12), which stages explicit paths, requires the exact staged set, runs pre-commit on exactly those files before committing, pushes with a per-command header and verifies the remote tip. | `.pre-commit-config.yaml`, notebook cells P7-C, P7-9 |
| P20 | The judge runner wrote its response cache to `evals/reports/<run>/cache.sqlite`, outside the `cache/` folder that `.gitignore` covers, so the calibration run left an untracked binary in its report folder. The next step's clean-tree guard stopped before any change. Fixed at the source: judge runs now write to `cache/judge.sqlite`, and the existing file was moved there rather than deleted. | `evals/judges/run.py`, cell P7-15 |
| P21 | The first P7-16 wait loop called `pgrep -f "--tag …"`: a pattern starting with `--` is parsed as an option, pgrep exited with a usage error, and the loop read that as "no judge running" and stopped after 3 s while the run continued in the background. Re-running that cell would have started a second judge on the same files. Fixed with `pgrep -f -- PATTERN`, and the check now fails on any pgrep exit code other than 0 or 1 instead of reading it as "not running". | cell P7-16 |
| P26 | Phase 7 closed without meeting its DoD in full, by decision (Aviraj, 29 Sep 2026). The final run beat D41 on execution accuracy (0.899 -> 0.970, correctness interval 0.788-0.983 -> 0.900-1.000), but judge scores were flat and refusal and clarity regressed on two ambiguous items with a known cause (L27). v5 was activated anyway: the accuracy gain lies outside the run-to-run noise, and a further experiment would cost another quota day. This overrides the plan's DoD, as P24 overrode the green-CI rule. Numbered after the highest P in the stack (P25). | README D65, L27 |

### Verification

| Tag | Record | Where |
|---|---|---|
| V1 | First live run of the Phase 4 stack, 25 Sep 2026 (Colab, `openai/gpt-oss-120b`, fallback `openai/gpt-oss-20b`): "How many customers are there?" → `SELECT COUNT(*) … LIMIT 201` → 20, answered in 2 calls (571 + 58 and 183 + 56 tokens; 398 ms and 309 ms; one attempt each; no fallback). Groq returned `x-ratelimit-remaining-requests` (999 → 998, per day) and `x-ratelimit-remaining-tokens`, confirming the header names the limiter reads. The key did not appear in the call log. | Notebook cell P4-13 |
| V2 | Production after the Phase 4 merge (#20), 25 Sep 2026: `main` identical to the verified commit `cd7ae7d`; Render `/health` 200 on the first attempt; `POST /chat` "How many customers are there?" → 20 with `error: null` and a `request_id`. The `request_id` field exists only in Phase 4 code, so this proves the new build was live and that `LLM_LIMITS` was read on Render. | Notebook cell P4-15 |
| V3 | Phase 5 final baseline, 25 Sep 2026 (`openai/gpt-oss-120b`, temperature 0, same 15 items and grader as Phase 0): 11/14 graded automatically; b08 answered with a clarifying question: "Do you want best customers by total revenue, order count, or average order value?"; total 12/15 against Phase 0's 11/15; 28 calls, 13,127 input tokens. | `evals/baseline/results/baseline_a77a6f5a5597_openai_gpt-oss-120b_phase5-final.jsonl` |
| V4 | Production after the Phase 5 merge (#21), 25 Sep 2026: `main` identical to the verified `f6ff514`; Render `/health` 200; "How many customers are there?" → 20 with `needs_clarification: false`; "Who are the best customers?" → a clarifying question with `needs_clarification: true` and `sql: null`. Branch protection read back through the rules API: ruleset 23925380 requires Lint and types, Tests (Python 3.12), Tests (Python 3.13), Security scans and `conventional-title`. | Notebook cells P5-11, P6-0, P6-0b |
| V5 | Live tests passed 6/6 against Groq on 26 Sep 2026 (`openai/gpt-oss-120b`, cache and fallback off). This confirmed that the rate-limit headers arrive as `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests` and `x-ratelimit-remaining-tokens`, the names `app/llm/limiter.py` reads. |
| V6 | Groq catalog snapshot, 26 Sep 2026 (`evals/reports/2026-09-26-catalog/models.json`): 11 models, 4 of which generate text; both configured models active. Console limits read the same day: gpt-oss-120b, gpt-oss-20b and qwen3.8-27b at 30 RPM, 1,000 RPD, 8,000 TPM and 200,000 TPD; allam-2-7b at 30 RPM, 7,000 RPD, 6,000 TPM and 500,000 TPD. |
| V7 | Groq Models page, 26 Sep 2026: gpt-oss-120b and gpt-oss-20b under Production; qwen3.8-27b, gpt-oss-safeguard-20b, the prompt-guard models and the Orpheus models under Preview. The worst-case synthesizer prompt (the widest four-table join, 20 rows shown) is about 1,602 tokens; with each model's largest observed answer, it fits every candidate's context window, including allam-2-7b's (1,773 of 4,096). |
| V8 | Production after the Phase 6 merge, 27 Sep 2026: `main` has the same tree as the verified `f33ac23`; Render `/health` returns 200; "How many customers are there?" is answered "There are 20 customers." from `SELECT COUNT(*) FROM customers LIMIT 201` (the row-cap rewrite) with no error. Configuration unchanged (D40). |
| V9 | The "Run evals" workflow ran from `main` on 27 Sep 2026 (sql_generator, gpt-oss-120b, items g01 and g29, 1 repeat, live tests off): every step green in 46 s, the report in the job summary, and the artifact uploaded. This confirms the dispatch-only design (S7) and the repository variables `GROQ_MODEL` and `LLM_LIMITS`, and resolves L11. |
| V10 | Judge calibration run, 27 Sep 2026: 26 calls to `qwen/qwen3.8-27b` through the gateway (rate limiter, cache, call log), 0 errors, 0 parse failures, run as a detached process that completed across a Colab disconnect (confirmed by cell P7-13). | `evals/reports/2026-09-27-judge-calib-qwen3.8-27b/` |

## Demo video

[Watch the demo](https://youtu.be/bXCRQufnvPY?si=Av9dtlzokdHMVncn) (5 minutes)

Covers a query and its generated SQL, a follow-up question using conversation
context, both kinds of refusal, and the database protections with the test that
proves them.
