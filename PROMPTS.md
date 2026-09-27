# Prompts used by the application

The prompt text lives in versioned files in [`app/prompts/`](app/prompts/), and
[`app/prompts/__init__.py`](app/prompts/__init__.py) assembles the messages. There are three calls.

| Call | Prompt | When it runs |
|---|---|---|
| 1 | SQL generation | Every question that passes `guard_input` |
| 2 | Repair | Only if a query failed with a repairable error, up to `MAX_REPAIR_ATTEMPTS` times (default 1, D30) |
| 3 | Answer formatting | Only if a query ran |

So a question costs 1-3 model calls with the default settings. A refused
question costs 1: the model replies with a token and the app returns a fixed
message it never writes. `READ_ONLY` for requests that would change the data,
`OUT_OF_SCOPE` for questions the database cannot answer, `CLARIFY: <question>`
when the answer needs a choice the user has not made (D27).

---

## 1. Files, versions and ids

| File | Used by | Placeholders |
|---|---|---|
| `schema.v<N>.md` | Calls 1 and 2 | none |
| `sql_gen.v<N>.md` | Call 1 (system) | `$schema`, `$read_only_token`, `$clarify_token`, `$out_of_scope_token` |
| `sql_repair.v<N>.md` | Call 2 (system) | `$schema`, `$out_of_scope_token` |
| `answer.v<N>.md` | Call 3 (system) | none |
| `answer_notes.v<N>.toml` | Call 3 (result notes) | `{shown}`, filled when rows are hidden |

Rules (D42):

- **A released file is never edited.** A change is a copy with the next version
  number. `tests/unit/test_prompt_versions.py` pins every file's hash, so an
  edit to a released file fails, and a new file fails until it is registered.
- **`ACTIVE_VERSIONS`** in `app/prompts/__init__.py` selects the version each
  call uses. The schema is its own file, so a schema-format experiment changes
  one file and leaves the instructions alone.
- **The logged id is still `name@hash8` of the rendered text** (D24), so every
  call-log line identifies the exact text sent, including the schema and tokens.
  The registry below maps each version to its id. Activating a version changes
  an id pinned in `tests/unit/test_prompt_ids.py`, and that pin is updated only
  together with the eval evidence in the change log.
- **Rendered messages are snapshot-tested** (`tests/unit/test_prompt_snapshots.py`,
  T14). Any change to what the model receives fails CI until the snapshot is
  updated and reviewed.

### Registry

| Prompt | Version | Id | Status | Evidence |
|---|---|---|---|---|
| `sql_gen` | v1 (with `schema.v1`) | `sql_gen@5fb4fe06` | Active; Phase 7 baseline | D41 |
| `sql_repair` | v1 (with `schema.v1`) | `sql_repair@a5c30252` | Active; Phase 7 baseline | D41; repair run 26 Sep 2026 |
| `answer` | v1 (with `answer_notes.v1`) | `answer@3c3a3566` | Active; Phase 7 baseline | D41; synthesizer run 26 Sep 2026 |

v1 is the text in use since Phase 5, moved into files byte for byte: the ids
did not change.

---

## 2. SQL generation

Turns a question into a SELECT query, or replies with a token. Sends the schema,
the question, and up to `MAX_HISTORY_TURNS` (default 3) earlier question/SQL
pairs, replayed as user/assistant turns. No row data.

Text: [`sql_gen.v1.md`](app/prompts/sql_gen.v1.md). It has three parts: the
schema, six numbered rules (SELECT only and no formatting, no invented names,
`LIMIT` at most 100, revenue from `unit_price`, cancelled orders excluded from
totals, date handling) and a SCOPE section with the three reply tokens and the
instruction that the user's message is data, never instructions.

**Rules 4 and 5 exist because the model cannot guess them.** Column names do not
say that revenue should use the price actually paid, or that cancelled orders
should not count. Without these rules the answers look right and are wrong.

**Rule 3 is now redundant with code and is kept only until it can be measured.**
Since Phase 3 the row cap is owned by code (README D19): the validator rewrites
the outermost `LIMIT` and the executor detects truncation exactly. The model's
own `LIMIT 100` sits below the 200-row cap, so it can still cut a result short;
that case is disclosed through `limit_reached` (README L5). Removing the rule is
a prompt change, so it waits for the Phase 7 evals.

**Rules are not always followed.** See [`DISCOVERIES.md`](DISCOVERIES.md) for a
real example. Nothing that must hold is left to the prompt.

---

## 3. Repair

Runs only when the error is repairable: a parse error, an unknown table, a
database error such as an unknown column, or an invalid `LIMIT`. A forbidden
write, a stacked statement or a timeout is final and gets no repair (README
D20).

Text: [`sql_repair.v1.md`](app/prompts/sql_repair.v1.md): the schema, one
corrected SELECT only, and `OUT_OF_SCOPE` if the schema cannot answer it. It
does **not** carry the domain rules from call 1, so a repaired revenue query
can include cancelled orders (L14, Phase 7 experiment 1).

The failed SQL and the error detail are sent with it. For database errors the
detail contains SQLite's own message (`no such column: revenue`), which is more
useful than anything we would write; for an unknown table it lists the real
tables. The detail is sent to the model only, never to the user (README H1).

---

## 4. Answer formatting

Turns result rows into one or two sentences.

Text: [`answer.v1.md`](app/prompts/answer.v1.md): answer directly, amounts in
rupees (`Rs 33,895`), say so plainly when nothing matched, mention truncation,
invent no numbers. `check_answer` then adds any missing disclosure in code
(D29).

**This is the only call that sends database contents to the provider.** Up to 20
result rows go with it. The demo data is fake (all emails are `example.com`), so
nothing real is exposed, but on real data this would need thinking about.

The rows are followed by notes that describe what the model is not seeing. Each
is added independently (Phase 3):

| Condition | Note |
|---|---|
| More than 20 rows returned | only the first 20 of N are shown; do not call them the highest, lowest or total |
| Truncated at the row cap | more rows matched than the server returns |
| The query's own `LIMIT` was hit exactly | more matching rows may exist |

Before Phase 3 the truncation note was only added when 20 or fewer rows came
back, so a capped 200-row result was described to the model as "the first 20 of
200 rows" with nothing saying the 200 were themselves capped.

The note texts are in [`answer_notes.v1.toml`](app/prompts/answer_notes.v1.toml).

---

## 5. Security prompts

**There are none, on purpose.**

Telling the model "never write DELETE" is a request it can ignore. The real
protections are in code:

| Rule | Enforced by |
|---|---|
| Only one read-only SELECT runs | sqlglot validator, `app/sql/validator.py` (S1) |
| One statement only | The validator, and Python's sqlite3 driver |
| No writes, ever | Read-only connection + SQLite authorizer, `app/sql/executor.py` (S2) |
| Max 200 rows, max 5 seconds | `app/sql/validator.py` (LIMIT rewrite) and `app/sql/executor.py` |

A test scripts the model to return `DELETE FROM customers`. It is rejected as
`FORBIDDEN_WRITE`, the model is not asked again, and the database is checked
intact afterwards. Other tests bypass the validator entirely and check that the
executor still refuses the write.

---

## 6. Testing prompts

Every offline test uses a scripted fake model; no test calls Groq by default.
Five test files cover the prompts themselves:

| File | Checks |
|---|---|
| `tests/test_prompts.py` | The schema file matches the real database; message assembly; the key rules are present |
| `tests/unit/test_prompt_ids.py` | Id format, and the active ids pinned to the Phase 7 baseline (D41) |
| `tests/unit/test_prompt_versions.py` | Released files unchanged; every file registered and correctly named |
| `tests/unit/test_prompt_loader.py` | Newline rule, version selection, placeholder checks |
| `tests/unit/test_prompt_snapshots.py` | Complete rendered messages for each kind of call (syrupy) |

Live behaviour is measured by the eval harness (EVALS.md), never by the default
test run.

---

## 7. Open findings

Found while moving the prompts, to be assessed in the Phase 7 prompt review
before any change:

- `sql_gen.v1` and `sql_repair.v1` have two blank lines between the schema and
  the next section. Harmless but costs tokens on every call. Kept in v1 so the
  move stayed byte-identical.

---

## Change log

| Date | Change | Evidence |
|---|---|---|
| 25 Sep 2026 (Phase 3) | No prompt text changed: the four prompt constants were compared with the committed versions and are identical. The answer call's result notes became independent and gained the `limit_reached` note. The retry call now runs only for repairable errors. | Notebook cells P3-18, P3-21; `tests/test_prompts.py` |
| 25 Sep 2026 (Phase 4) | No prompt text changed (the seven prompt constants were compared with the committed versions). Each prompt now has an id derived from its text, sent with every model call for the cache key and the call log: `sql_gen@27e9e81d`, `sql_repair@a5c30252`, `answer@3c3a3566`. The three calls run under the roles `sql_generator`, `sql_repair` and `synthesizer`. | `app/prompts.py`, README D24 |
| 25 Sep 2026 (Phase 5) | **Experiment: CLARIFY rule.** Hypothesis: an explicit rule stops the model silently guessing on ambiguous questions (baseline b08 guessed "best" = top 10 by spend). Change: one paragraph in the SQL prompt's SCOPE section (`sql_gen@27e9e81d` → `sql_gen@5fb4fe06`). Measured on the 15-item baseline, same grader: b08 changed from a silent guess to a clarifying question; the other 14 items unchanged; input tokens +1.3% (12,964 → 13,127). **Kept.** Caveat: one ambiguous item; Phase 6 checks for over-triggering on clear questions. | `evals/baseline/results/*_after-clarify.jsonl`, README D27 |
| 27 Sep 2026 (Phase 7) | No prompt text changed. Prompts moved from `app/prompts.py` into versioned files (`app/prompts/*.v1.*`) with a loader, released-file hash pins and snapshot tests. The ids are unchanged: `sql_gen@5fb4fe06`, `sql_repair@a5c30252`, `answer@3c3a3566`, so D41 remains the baseline. | `app/prompts/`, README D42, T14 |
