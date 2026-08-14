# SQL Query AI Agent

Natural-language questions to SQL, using FastAPI, LangGraph and SQLite.

**Status: under construction.** The section below is a working scratchpad,
rewritten into proper documentation before submission.

---

## Engineering notes (scratchpad — to be rewritten)

### Known limitations

**L1 — Money stored as REAL.**
`products.price` and `order_items.unit_price` use SQLite's `REAL` (floating
point). Sums of many rows can accumulate rounding error. Correct approach is
integer paise or a fixed-point representation. Acceptable for a demo dataset
of 47 line items; would not be acceptable in a real ledger.

**L2 — Semicolon detection is textual, not parsed.**
The validator rejects any SQL containing a semicolon that is not a single
trailing one. A semicolon inside a string literal (`WHERE city = 'Mum;bai'`)
would be falsely rejected. Correctly distinguishing the two requires a real
SQL parser (e.g. `sqlglot`), which is out of scope. No value in the seed data
contains a semicolon, so this cannot trigger here.

**L3 — Conversation memory is process-local.**
Session history lives in a Python dict. A server restart clears it, and it
would not work across multiple instances. Acceptable given Render's free tier
runs a single instance.

**L4 — Free-tier cold start.**
Render free services sleep after 15 minutes idle; the first request afterwards
takes 30-60 seconds.

### Design decisions

**D1 — `unit_price` is duplicated onto `order_items`.**
Deliberate denormalisation. Joining to `products.price` instead would mean a
price change silently rewrites historical order totals. Capturing price at
purchase time is how real order systems behave, and two rows in the seed data
have a discounted `unit_price` to make this visible.

**D2 — The prompt is not a security boundary.**
Instructions to the LLM ("only write SELECT") reduce retry frequency and cost.
They are an optimisation, never enforcement. Every actual guarantee is made in
application or engine code. See S1-S4.

**D3 — SQLite over Postgres.**
No signup, no connection string to protect, no network latency, and the file
ships in the repo so deployment needs no database provisioning. SQLite has no
user accounts, so the read-only "user" is achieved differently (see S2).

### Security model

**S1 — Validator (application layer).**
Rejects non-SELECT statements and statement stacking before a connection is
opened. Purpose is cheap, *informative* failure that the retry loop can act on
— `not authorized` gives the LLM nothing to correct from.

**S2 — Read-only connection + SQLite authorizer (engine layer).**
Connections open with `file:...?mode=ro`. A `set_authorizer` callback returns
`SQLITE_DENY` for every action other than SELECT/READ/FUNCTION. This is
enforced inside SQLite during statement compilation, not in Python. It is the
closest equivalent to a `GRANT SELECT`-only role. Notably it blocks `ATTACH`
and `PRAGMA`, which read-only mode alone permits — `ATTACH` is the dangerous
one, as it could otherwise open arbitrary files.

**S3 — Driver-level single-statement execution.**
`sqlite3.execute()` runs exactly one statement and raises on stacked input.
`executescript()` (which would permit stacking) is never called at runtime.

**S4 — Query limits.**
Results capped at 200 rows; a progress handler aborts queries exceeding 5
seconds, since SQLite has no `statement_timeout`.

**S5 — `get_schema()` must never take arguments.**
It runs unguarded (the authorizer would deny its `PRAGMA` calls) and
interpolates table names into `PRAGMA table_info({table})`. Those names come
from `sqlite_master`, so there is currently no injection path. `PRAGMA` cannot
accept bound parameters, so this cannot be fixed with parameterisation — the
safety property is entirely that no caller-supplied value ever reaches it.
Adding a parameter to this function would create a real vulnerability.

### Verified test evidence

Phase 1: `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ATTACH`, and `PRAGMA` all
rejected with `not authorized`. Row cap confirmed at 200 with truncation flag
set. Foreign key check clean across all 47 line items.

### Testing approach

**T1 — Mutation testing on the validator.**
Four deliberate faults were injected into `validator.py` to check whether the
test suite would notice: (1) removing the `\b` word boundaries from the
keyword regex, (2) disabling the statement-stacking check, (3) disabling
comment stripping, (4) disabling the SELECT-prefix check. The first three were
caught. The fourth survived — revealing that the prefix check was completely
redundant with the forbidden-keyword list across every test case we had, since
`DROP`, `DELETE`, `TRUNCATE` and `VACUUM` are all caught twice. Two cases
(`EXPLAIN SELECT ...` and `ANALYZE`) were then added: neither keyword is on the
forbidden list, so only the prefix check rejects them. The suite is trusted
because it has been shown to fail when the code is wrong, not merely to pass
when it is right.

**T2 — Tests are run with `python -m pytest`, not bare `pytest`.**
The `-m` form puts the current directory on `sys.path`, so `from app.validator
import ...` resolves. Bare `pytest` fails with `ModuleNotFoundError: No module
named 'app'` in a project without a `setup.py`.

**T3 — A failing test is not automatically a code bug.**
The first red run came from a test case filed in the wrong bucket:
`"SELECT * FROM customers; "` (trailing semicolon plus whitespace) was listed
as something to block, but it is a single valid statement and blocking it would
cause a needless LLM retry on every query ending in `;`. The test was moved to
the accepted list; the validator was left alone. Changing the source to satisfy
that test would have introduced a real defect.

### Known limitations (continued)

**L5 — `EXPLAIN` and `ANALYZE` are rejected.**
Both are read-only, but `EXPLAIN QUERY PLAN` exposes index and storage
internals, and neither is needed to answer a user's question. Rejected by the
SELECT-prefix check rather than the keyword list.

### Verified test evidence (continued)

Phase 2: 32 validator tests passing. Mutation testing results as described in
T1 above.

### Data handling

**H1 — What leaves the machine, and what does not.**
Two things are sent to the LLM provider on every question: the *schema*
(table and column names and types) and the user's question. A third thing is
sent when a query succeeds — the **result rows**, because `build_answer_messages`
passes them to the model to be summarised into a sentence.

An earlier version of this note claimed row data never leaves the machine.
That was true of SQL generation and false of answer formatting, and the
distinction matters: a question like "list all customer emails" would send
those emails to a third party. The rows are capped at 20 before sending, and
the seed data is synthetic with `example.com` addresses, so nothing real is
exposed here. On real data this design would need either local formatting of
results or an explicit decision to accept it.

The database file itself, credentials, and any row not selected by the query
are never transmitted.

**H2 — Free LLM tiers generally train on your prompts.**
This is the trade for a no-credit-card tier and is acceptable for a demo built
on synthetic data. It would not be acceptable with real customer records —
which is a second, independent reason the design keeps row data local.

### Process notes

**P1 — `!command` in Colab does not report failure to Python.**
A shell command run with `!` executes in a subshell; a non-zero exit does not
raise, and `get_ipython().system()` returns `None` in Colab regardless of
outcome. A `print("done")` on the following line therefore prints on failure
too. Three pushes appeared to succeed or fail incorrectly because of this.
Correct approach where the result matters: `subprocess.run(..., 
capture_output=True)` and check `returncode`.

**P2 — Credentials in git remote URLs.**
The push token is embedded in the URL for a single command rather than stored
via `git config` or `git remote set-url`, so it is never written to
`.git/config` where it would persist on disk and survive in any later output.
Git error messages can echo the remote URL, so stderr is filtered before
printing.

**P3 — Commit author email is public on a public repo.**
An early commit was made with a personal address before the noreply alias was
configured. GitHub exposes commit metadata publicly and these addresses are
scraped. Later commits use `<username>@users.noreply.github.com`. History was
not rewritten: force-pushing a submission repo to scrub two commits carries
more risk than the exposure justifies.

### Design decisions (continued)

**D4 — Scope classification is merged into the SQL generation call.**
The reference design used a separate LLM call to decide whether a question was
in scope, then another to generate SQL. Instead the SQL prompt instructs the
model to return either a query or the literal token `OUT_OF_SCOPE`, so one call
does both. This halves the fixed cost per question (1-3 calls instead of 2-4),
removes a round trip of latency, and eliminates a class of bug where the two
calls disagree. The cost is a branch on the response string.

**D5 — Domain rules live in the prompt, not in post-processing.**
Two rules encode knowledge the model cannot infer from column names alone:
revenue must use `order_items.unit_price` rather than `products.price` (D1),
and cancelled orders are excluded from totals by default. Without these the
model produces plausible but subtly wrong figures - the worst failure mode for
this kind of tool, because nothing looks broken.

### Testing approach (continued)

**T4 — Schema drift test.**
`prompts.py` restates the database schema in prose for the LLM to read. That
prose can silently fall out of sync with `schema.sql`. When it does, the model
writes SQL against columns that no longer exist and the failure presents as
poor model quality rather than stale configuration. `test_prompts.py` asserts
in both directions against a live `get_schema()` call: every real table and
column must appear in the prompt, and the prompt must not describe tables that
do not exist. The column check deliberately matches loosely rather than parsing
the prose layout, so reformatting the description does not break the test.

### Process notes (continued)

**P4 — Colab cell magics claim the entire cell.**
`%%writefile` must be the first line of a cell and captures everything below it
as file content. Putting a `!pytest` command in the same cell writes the
command into the file instead of running it, and produces a confusing argument
parsing error. One magic per cell.

### Known limitations (continued)

**L6 — The LLM model name is a moving target.**
Free-tier providers retire and rename models frequently, often with little
notice. A model string that works today may return a 404 in a few months, and
the failure is opaque: the app looks broken rather than misconfigured. Two
mitigations here: the model name is read from the `GROQ_MODEL` environment
variable with a hardcoded default, so it can be changed on the deployment
platform without a code change; and the startup path fails with an explicit
message naming the model rather than a raw API error. Anyone reviving this
project should check the provider's current model list first.

**L7 — Free tiers themselves are not stable.**
Rate limits, model availability and the terms of no-credit-card access have all
changed repeatedly. This project is built to be portable rather than tied to
one provider: the LLM is reached through a single client object, so switching
providers means changing one module, not the agent logic.

### LLM provider verification (Phase 3 step)

**V1 — Model availability was checked against the API, not documentation.**
Before writing any agent code, the model list was pulled from the provider
directly, because published model names go stale (see L6) and a 404 on an
invented model string is an opaque failure. Verified 14 August 2026 with:

```python
from groq import Groq
models = sorted(m.id for m in Groq().models.list().data)
```

15 models were reachable on the free tier, including `llama-3.3-70b-versatile`
(used here) and `llama-3.1-8b-instant` (higher daily quota, lower quality).
Free-tier limits at the time: 30 requests/minute, 1,000 requests/day for the
70B model.

**V2 — Connectivity was proved through the client the app actually uses.**
A raw SDK call proving the key works does not prove the LangChain wrapper is
configured correctly. The smoke test therefore goes through `ChatGroq`, the
same client `agent.py` uses:

```python
llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
llm.invoke([{"role": "system", "content": "Reply with exactly one word."},
            {"role": "user", "content": "Say OK"}])
```

Response: `'OK'`, 43 prompt + 2 completion tokens. Establishing this before
building the graph means any later failure is attributable to the graph, not
to authentication or model naming.

**V3 — `temperature=0` for every call.**
The same question should produce the same SQL. Sampling variety is a liability
in query generation: it makes failures non-reproducible and makes a working
query occasionally stop working.

**V4 — A dedicated injection classifier was considered and rejected.**
The provider offers `meta-llama/llama-prompt-guard-2-86m`, a model trained to
detect prompt injection. It was not used: it would add a second API call to
every question against a 30/minute limit, the `OUT_OF_SCOPE` token already
handles scope in the existing call, and neither is the actual security
boundary — the validator and the SQLite authorizer are (see S1, S2). Spending
a request on a defence that is not load-bearing is the wrong trade here.

### Design decisions (continued)

**D6 — The LLM client is created lazily, not at import time.**
`agent.get_llm()` builds the client on first use. Creating it at import would
make `import app.agent` fail without an API key, which would break the
`/health` endpoint and force every test to hold a live credential.
`set_llm()` is the injection seam the test suite uses.

**D7 — The retry edge routes back to `validate`, not to `execute`.**
SQL from the retry call is exactly as untrusted as SQL from the first call, so
it passes through the same gate. Routing it straight to execution would be a
real hole rather than a shortcut.

**D8 — The retry cap is structural, not a counter.**
`route_after_execute` sends work to the retry node only when an error is set
*and* `retry_count == 0`. The retry node sets `retry_count = 1`, so a second
failure has no edge back. This is a property of the graph rather than a guard
someone could later modify.

### Testing approach (continued)

**T5 — The agent suite runs entirely offline against a fake LLM.**
`set_llm()` injects a scripted stub exposing `.invoke(messages).content` — the
only surface `agent.py` touches. Everything below the model is real: the
validator runs, the graph routes, and queries hit the actual SQLite file, so
the integration is genuinely tested rather than mocked away. The stub raises if
called more times than scripted, which is how "no third LLM call after a failed
retry" is enforced instead of merely assumed. 56 tests total, no API calls, no
rate limits, deterministic.

**T6 — Module-level globals must be reset between tests.**
`agent._llm` and `agent._graph` are module-level, so a fake installed by one
test would persist into the next. An `autouse` fixture clears both before and
after every test.

### Verified test evidence (continued)

Phase 4, live run against `llama-3.3-70b-versatile`:

- "Which 3 customers have spent the most?" produced a three-table join using
  `oi.unit_price * oi.quantity` and filtering `status != 'cancelled'` — both
  domain rules from D5 applied — returning Vikram Nair Rs 33,895, Ananya Iyer
  Rs 28,796, Dev Chauhan Rs 21,999, matching the values computed directly
  against the seed data.
- Follow-up "What about just the ones in Mumbai?" — a question with no
  standalone meaning — correctly reconstructed the full aggregation from
  replayed history and added a single `c.city = 'Mumbai'` clause, keeping the
  cancelled-order filter.
- "Write me a Python function to reverse a string" returned
  `out_of_scope=True`, `sql=None`, and the database was not queried.

### Known limitations (continued)

**L8 — Generated SQL groups by `c.name` rather than `c.id`.**
Observed in the live run. Two customers sharing a name would be merged into a
single row. No duplicate names exist in the seed data so it cannot occur here,
but it is a real correctness issue in generated SQL that validation cannot
catch — the query is syntactically valid and semantically reasonable. Fixing it
would mean either a stricter prompt rule or post-generation SQL analysis.

### Process notes (continued)

**P5 — The web server cannot be reached from a browser in Colab.**
Colab's VM has no public address, so `uvicorn app.main:app --reload` starts a
server that nothing outside the notebook can reach. Tunnelling tools (ngrok and
similar) would expose it, but they require a third-party account, and one of
them would be an unnecessary external dependency for a $0 project.

Instead the server is started on `127.0.0.1` in a background thread inside the
notebook and driven with `httpx` from the same process. This is not a
workaround so much as the right shape: it is exactly what the Phase 7 API tests
do, so the manual check and the automated tests exercise the same path. Real
browser testing happens against the deployed Render URL, where a browser can
actually reach it.

**P6 — Git does not track empty directories.**
`frontend/` was created in Phase 1 but stayed empty, so it does not exist in the
repository and will not exist on a fresh clone — including on Render. Any code
that mounts it as a static directory at import time would crash on deploy with
a confusing path error. `main.py` therefore checks for the directory before
mounting and serves a plain message when the frontend is absent, so the API is
independently runnable before Phase 6 exists.

### Design decisions (continued)

**D9 — Query failures return HTTP 200 with a populated `error` field.**
A question the agent cannot answer is a normal outcome of this application, not
a transport failure. Returning 4xx/5xx would mean the frontend needs two
response-handling paths and would conflate "the model wrote bad SQL" with "the
server is broken". One shape, one path. Genuine transport failures still use
real status codes — malformed requests are rejected by Pydantic with a 422
before any handler runs.

**D10 — Conversation history lives on the server, keyed by `session_id`.**
The client sends only a question and a session id; it never sends prior turns.
This keeps the browser from being able to forge or replay context, and means the
history-truncation rule (`MAX_HISTORY_TURNS`) is enforced in one place. Only
*successful* queries are stored: replaying SQL that failed would teach the model
its own mistakes.

**D11 — `/health` deliberately touches neither the database nor the LLM.**
Render pings this endpoint to decide whether the instance is alive. If it
called Groq, a rate-limit response or a provider outage would read as "the
application is down" and trigger a restart that fixes nothing. Liveness and
dependency health are different questions and should not share an endpoint.

### Known limitations (continued)

**L9 — Session storage is an unbounded dict with crude eviction.**
Sessions accumulate in a module-level dict, capped at `MAX_SESSIONS = 500` with
oldest-first eviction. This is insertion-ordered, not least-recently-used, so an
active session can be evicted while a stale one survives. The cap exists because
without it the dict grows for every unique `session_id` forever — a slow leak
and a trivial way to exhaust a 512 MB free instance. A real system would use a
TTL cache or Redis; both are out of scope here (see L3).

### Verified test evidence (continued)

Phase 5, live server driven by `httpx` over loopback:

- `GET /health` -> 200 `{"status": "ok"}`
- `GET /schema` -> all four tables
- `GET /` -> 200 (placeholder JSON; frontend not yet built)
- `POST /chat` with an empty question -> 422, rejected by Pydantic before
  reaching the agent, so no LLM request was spent
- `POST /chat` with no body -> 422
- A question and a follow-up on the same `session_id`, with **no history in the
  request payload**, produced a correctly context-aware query: the follow-up
  reconstructed the full three-table revenue aggregation and added only
  `c.city = 'Mumbai'`, confirming server-side session memory
- An out-of-scope request returned `out_of_scope: true` and no SQL
