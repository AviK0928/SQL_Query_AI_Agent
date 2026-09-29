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
| `sql_gen` | v1 (with `schema.v1`) | `sql_gen@5fb4fe06` | Replaced by v5 (D65); Phase 7 baseline | D41 |
| `sql_gen` | v2 (with `schema.v1`) | `sql_gen@d5145f66` | Kept in experiment 3, combined into v5 | D60; `2026-09-28-exp3-sql-gen-v2` |
| `sql_gen` | v3 (with `schema.v1`) | `sql_gen@485384f7` | Reverted in experiment 4 | D61; `2026-09-28-exp4-sql-gen-v3` |
| `sql_gen` | v4 (with `schema.v1`) | `sql_gen@8c7d4fa8` | Kept in experiment 6, combined into v5 | D62; `2026-09-28-exp6-sql-gen-v4` |
| `sql_gen` | v5 (with `schema.v1`) | `sql_gen@43cea818` | Active since the final Phase 7 run (by decision, P26): v1 + v2's rule 5 + v4's rule 7 | D63, D65; `2026-09-29-final-v5` |
| `sql_repair` | v1 (with `schema.v1`) | `sql_repair@a5c30252` | Replaced by v2 (D50) | D41; repair run 26 Sep 2026 |
| `sql_repair` | v2 (with `schema.v1`) | `sql_repair@cbe7c9f7` | Active since experiment 1 | D50; `2026-09-27-exp1-repair-v2` |
| `answer` | v1 (with `answer_notes.v1`) | `answer@3c3a3566` | Replaced by v2 (D59) | D41; synthesizer run 26 Sep 2026 |
| `answer` | v2 (with `answer_notes.v1`) | `answer@4b837d88` | Active since experiment 2 | D59; `2026-09-28-exp2-answer-v1`, `2026-09-28-exp2-answer-v2` |

v1 is the text in use since Phase 5, moved into files byte for byte: the ids
did not change.

---

## 2. SQL generation

Turns a question into a SELECT query, or replies with a token. Sends the schema,
the question, and up to `MAX_HISTORY_TURNS` (default 3) earlier question/SQL
pairs, replayed as user/assistant turns. No row data.

**Active: [`sql_gen.v5.md`](app/prompts/sql_gen.v5.md) (D65).** It is v1 with rule 5 rewritten for the cancelled-order scope (D60) and rule 7 added for ties at a ranking cut-off (D62). The description below is of v1.

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

Text: [`sql_repair.v2.md`](app/prompts/sql_repair.v2.md): the schema, one
corrected SELECT only, `OUT_OF_SCOPE` if the schema cannot answer it, and the
two domain rules: revenue from `unit_price * quantity`, and cancelled orders
excluded from money and units sold but counted as placed (D45). v1 lacked the
rules, so repaired revenue queries included cancelled orders (L14, fixed by D50).

The failed SQL and the error detail are sent with it. For database errors the
detail contains SQLite's own message (`no such column: revenue`), which is more
useful than anything we would write; for an unknown table it lists the real
tables. The detail is sent to the model only, never to the user (README H1).

---

## 4. Answer formatting

Turns result rows into one or two sentences.

Text: [`answer.v2.md`](app/prompts/answer.v2.md): answer directly, amounts in
rupees (`Rs 33,895`), say so plainly when nothing matched, mention truncation,
invent no numbers. v2 adds two rules (D59): the user sees every returned row in
a table, so warn about hidden rows only when a note says the result was
truncated or the query's LIMIT was reached; and a question that asked for N
rows and got exactly N is complete. `check_answer` then adds any missing
disclosure in code (D29).

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

## 8. Pre-registered experiments (D53, 27 Sep 2026)

Fixed before any run. Every run: `openai/gpt-oss-120b`, temperature 0, 3 repeats,
the candidate measured against v1 on the same items in the same way, using
`--prompt NAME=VERSION` (D49, D51). Judge scores (trusted criteria, D46) on repeat
0; `false_disclosure` (D47) on every repeat; the clarify rule is D52.

| Exp | Candidate | Hypothesis | Items: targets / controls | Keep only if | Tokens |
|---|---|---|---|---|---|
| 2 | `answer.v2` | The answer model reports its own 20-row view as the user's, and warns on intended top-N (L13, L21) | `synthesizer_v2` (s01-s12), v1 and v2 both run | false partial-result warnings fall; s03, s04, s05 still disclose; faithfulness does not drop | ~22k |
| 3 | `sql_gen.v2` | Rule 5's half-scope causes over-exclusion (A-20); a `cancelled_orders` column lets answers disclose the choice (D45) | g14 / g03, g07, g13, g17, g36, g40 (cancelled orders matter), g06, g12, g15, g19 | g14 passes >= 2/3; no control that passed 3/3 fails; answers mention cancelled orders on >= 2/3 repeats for most targets where they matter (reported either way) | ~36k |
| 4 | `sql_gen.v3` | Rule 3's `LIMIT 100` cuts lists below the code-owned cap (L5) | g34, g07, g35 / g01, g02, g10, g16, g17, g32, g33 | the `limit_reached` cut on g34 disappears; no correctness loss; false warnings do not rise | ~33k |
| 6 | `sql_gen.v4` | `LIMIT` at a ranking cut-off drops ties (P2, g18) | g18 / g10, g16, g17, g25, g26 | g18 passes >= 2/3; no control regresses | ~20k |

Experiment 5 (L12) is not a prompt experiment: resolved in the grader (D52).
Kept generator changes are combined into one `sql_gen` version and checked by a
full golden and adversarial run against D41 (re-scored, D52) and D48 before the
Phase 7 pull request.

## Change log

| Date | Change | Evidence |
|---|---|---|
| 25 Sep 2026 (Phase 3) | No prompt text changed: the four prompt constants were compared with the committed versions and are identical. The answer call's result notes became independent and gained the `limit_reached` note. The retry call now runs only for repairable errors. | Notebook cells P3-18, P3-21; `tests/test_prompts.py` |
| 25 Sep 2026 (Phase 4) | No prompt text changed (the seven prompt constants were compared with the committed versions). Each prompt now has an id derived from its text, sent with every model call for the cache key and the call log: `sql_gen@27e9e81d`, `sql_repair@a5c30252`, `answer@3c3a3566`. The three calls run under the roles `sql_generator`, `sql_repair` and `synthesizer`. | `app/prompts.py`, README D24 |
| 25 Sep 2026 (Phase 5) | **Experiment: CLARIFY rule.** Hypothesis: an explicit rule stops the model silently guessing on ambiguous questions (baseline b08 guessed "best" = top 10 by spend). Change: one paragraph in the SQL prompt's SCOPE section (`sql_gen@27e9e81d` → `sql_gen@5fb4fe06`). Measured on the 15-item baseline, same grader: b08 changed from a silent guess to a clarifying question; the other 14 items unchanged; input tokens +1.3% (12,964 → 13,127). **Kept.** Caveat: one ambiguous item; Phase 6 checks for over-triggering on clear questions. | `evals/baseline/results/*_after-clarify.jsonl`, README D27 |
| 27 Sep 2026 (Phase 7) | No prompt text changed. Prompts moved from `app/prompts.py` into versioned files (`app/prompts/*.v1.*`) with a loader, released-file hash pins and snapshot tests. The ids are unchanged: `sql_gen@5fb4fe06`, `sql_repair@a5c30252`, `answer@3c3a3566`, so D41 remains the baseline. | `app/prompts/`, README D42, T14 |
| 27 Sep 2026 (Phase 7) | **Experiment 1: domain rules in the repair prompt.** Hypothesis: v1 lacks the revenue and cancelled-order rules, so repaired revenue queries include cancelled orders (L14; r01, r08 and r10 failed on every model). Change, one variable: `sql_repair.v2` appends a RULES block (`sql_repair@a5c30252` -> `sql_repair@cbe7c9f7`); same model, temperature, items and pacing as the baseline. Result: repair success 0.700 -> **1.000** (10 items x 3 repeats); r01, r08, r10 0/3 -> 3/3; no item regressed, including the order-count items r03, r05 and r06. **Kept** under the rule fixed before the run. | `evals/reports/2026-09-27-exp1-repair-v2/`, README D50 |
| 28 Sep 2026 (Phase 7) | **Experiment 2: false partial-result warnings in the answer prompt.** Hypothesis: the answer model reports its own 20-row view as the user's and warns on intended top-N results (L13, L21). Change, one variable: `answer.v2` appends two rules (`answer@3c3a3566` -> `answer@4b837d88`); same model (gpt-oss-120b), temperature 0, 3 repeats, 12 s pacing, v1 and v2 both run on `synthesizer_v2` (s01-s12) the same day. Result: false partial-result warnings (D47) 15/36 -> **6/36**: s06 3/3 -> 0/3; s09, s11, s12 3/3 -> 1/3; s10 unchanged at 3/3. Required disclosures on s03, s04, s05 9/9 in both; faithful 36/36 in both; answer tokens 14,382 -> 18,488 (+29%). **Kept** under the rule fixed before the run (D53). Faithfulness here is the runner's deterministic check (numbers and terms stated and grounded): the judge's case builder replays golden and adversarial SQL only. The runner at this commit predates D54, so these calls log no schema hash; the schema was `207e7a26b02f`. | `evals/reports/2026-09-28-exp2-answer-v1/`, `evals/reports/2026-09-28-exp2-answer-v2/`, README D59 |
| 28 Sep 2026 (Phase 7) | **Experiment 3: cancelled-order scope in the SQL prompt.** Hypothesis: rule 5's half-scope causes over-exclusion (A-20), and a `cancelled_orders` column lets answers disclose the choice (D45). Change, one variable: `sql_gen.v2` rewrites rule 5 (`sql_gen@5fb4fe06` -> `sql_gen@d5145f66`); gpt-oss-120b, 3 repeats, temperature 0, 20 s pacing, `sql_repair` and `answer` pinned to v1 so `sql_gen` is the only change from D41 (re-scored, D52), compared on the same items and repeats. Result: g14 0/3 -> **3/3**; the 10 controls stayed 3/3. Answers mentioned cancelled orders on 1 of the 6 targets where they matter (g13; reported either way, as pre-registered), so the column's purpose was not shown. **Kept** (the gating conditions met); combined into v5. | `evals/reports/2026-09-28-exp3-sql-gen-v2/`, README D60 |
| 28 Sep 2026 (Phase 7) | **Experiment 4: no `LIMIT 100` rule.** Hypothesis: rule 3's `LIMIT 100` cuts lists below the code-owned cap (L5). Change, one variable: `sql_gen.v3` drops rule 3 (`sql_gen@485384f7`); gpt-oss-120b, 3 repeats, temperature 0, 20 s pacing, `sql_repair` and `answer` pinned to v1 so `sql_gen` is the only change from D41 (re-scored, D52), compared on the same items and repeats. Result: g34 returned all 190 unordered pairs instead of 100, but g34 3/3 -> 0/3 (graded on disclosure; a complete result has nothing to disclose) and false warnings 12 -> 15 (the three new ones are g34's, under the pinned answer v1). The rule fixed before the run was not met: **reverted**. The evidence points at the item (L20), so v3 is to be re-tested as a new pre-registered experiment after golden_v3. | `evals/reports/2026-09-28-exp4-sql-gen-v3/`, README D61 |
| 28 Sep 2026 (Phase 7) | **Experiment 6: ties at a ranking cut-off.** Hypothesis: `LIMIT` at a ranking cut-off drops tied rows (P2, g18). Change, one variable: `sql_gen.v4` adds rule 7 (`sql_gen@8c7d4fa8`); gpt-oss-120b, 3 repeats, temperature 0, 20 s pacing, `sql_repair` and `answer` pinned to v1 so `sql_gen` is the only change from D41 (re-scored, D52), compared on the same items and repeats. Result: g18 0/3 -> **3/3** (both tied cities); the 5 controls stayed 3/3; false warnings on the controls 9 -> 0. **Kept**; combined into v5. | `evals/reports/2026-09-28-exp6-sql-gen-v4/`, README D62 |
| 28 Sep 2026 (Phase 7) | No run. The kept generator changes combined into one candidate: `sql_gen.v5` (`sql_gen@43cea818`) = v1 + v2's rule 5 + v4's rule 7, pinned by a test to contain exactly those lines. Released and inactive until the final full golden and adversarial run. | `app/prompts/sql_gen.v5.md`, README D63 |
| 28 Sep 2026 (Phase 7) | No prompt change. Judge scores on repeat 0 for experiments 3, 4 and 6, as pre-registered in section 8 (reported, not part of any keep rule): `qwen/qwen3.8-27b`, rubric `response.v1`, trusted criteria only (D46), 27 verdicts, no parse failures, 35,581 judge tokens, compared item by item with D48. Exp 3: no item below D41 (completeness mean 4.64 -> 4.73). Exp 4: means up, two scores down (g07 faithfulness, g35 completeness), both on answers from the pinned answer v1. Exp 6: 5 throughout. One call hit qwen's per-minute token limit (8,000 TPM) and the run was resumed. | `evals/reports/2026-09-28-judge-exp3-qwen3.8-27b/`, `evals/reports/2026-09-28-judge-exp4-qwen3.8-27b/`, `evals/reports/2026-09-28-judge-exp6-qwen3.8-27b/`; README D60-D62 |
| 29 Sep 2026 (Phase 7) | **Final run: `sql_gen.v5` activated** (`sql_gen@5fb4fe06` -> `sql_gen@43cea818`). A full golden and adversarial run with `sql_repair.v2` and `answer.v2`, compared with D41 (re-scored, D52) and D48. Execution accuracy 0.899 -> 0.970, hard tiers 0.741 -> 0.889, consistency 0.848 -> 1.000. Refusal and clarity 0.875 -> 0.750 (g22, g24: L27). Judge scores flat: every trusted mean moved by 0.03 or less. The DoD was partly met, so v5 is active by decision (P26). The snapshots of the SQL messages were updated. | `evals/reports/2026-09-29-final-v5/`, `evals/reports/2026-09-29-judge-final-v5-qwen3.8-27b/`; README D65, L27, P26 |
