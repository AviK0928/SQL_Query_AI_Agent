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
