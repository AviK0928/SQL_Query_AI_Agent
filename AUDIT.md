# AUDIT.md — Phase 0 intake and audit

Audited: archive `agent_tar.gz` (uploaded 24 Sep 2026), HEAD `f6e44c0`
("Fix live demo link in README.md", 3 Sep 2026), 19 commits, branch `main`.
The archive is the source of truth for this audit. It is identical to
`github.com/AviK0928/SQL_Query_AI_Agent` at the same HEAD (§7).

Method: every file under `app/`, `tests/`, `database/`, the docs, and
`requirements.txt` was read. Validator and executor behaviour was probed
directly against `database/ecommerce.db`. The test suite and the LangGraph flow
could not be executed in the sandbox (no network to install dependencies), so
both were run in Colab: 79 tests collected and 79 passed (`--collect-only`),
and the live baseline in §8.

---

## 1. System as it stands

```
Browser (vanilla JS) ─► FastAPI app/main.py ─► LangGraph app/agent.py
                                                 generate_sql ─► validate ─► execute ─┬─► format_answer ─► END
                                                      ▲                               │
                                                      └──────── retry (max 1) ◄───────┘
                         app/validator.py  regex keyword blocklist (S1)
                         app/db.py         SQLite mode=ro + authorizer + progress-handler timeout (S2)
                         app/prompts.py    3 prompts, schema restated by hand
```

| Item | Value |
|---|---|
| Python | 3.12 (README, Render); Colab runs 3.13.15 |
| LLM | Groq via `langchain-groq` `ChatGroq`, temperature 0 |
| Model (code default) | `llama-3.3-70b-versatile` (`app/agent.py:24`) |
| Model (README) | `openai/gpt-oss-120b` since 3 Sep 2026 |
| LLM calls per question | 1 (refusal), 2 (normal), 3 (with retry) |
| Row cap / timeout | 200 rows / 5 s |
| Data size | customers 20, products 15, orders 30, order_items 47 |
| Tests | 79 collected, 79 passed in Colab (32 validator, 13 agent, 20 API, 14 prompts) |
| CI | none (`.github/` absent) |
| Packaging | `requirements.txt` only; test deps shipped to production |

### What is already good and must be kept

- **Two independent safety layers.** Read-only URI plus a SQLite authorizer
  that denies everything except SELECT/READ/FUNCTION/RECURSIVE. Probed:
  `load_extension()` is denied ("not authorized"), writes are impossible
  regardless of what the validator lets through.
- **Timeout works.** An unbounded recursive CTE was interrupted at 5 s.
- **Negative tests script the model to comply**, not to refuse, so they test
  the defences rather than the model's cooperation.
- **Bounded retry is structural**, not a counter check that could be bypassed.
- **Frontend inserts all model and DB output with `textContent`.** No XSS path.
- **Schema-drift test** between the prompt and the real DB.
- `DISCOVERIES.md` is an honest record of real failures (truncated-summary bug,
  valid-but-wrong SQL). Its content feeds the eval suite directly.

---

## 2. Findings (prioritised)

Priority: **P0** breaks correctness, safety, honesty or the offline-test
guarantee. **P1** blocks production-grade operation. **P2** hygiene.
"Phase" is where the fix lands in the plan.

### P0

| ID | Finding | Evidence | Phase |
|---|---|---|---|
| A-01 | **Code default model is decommissioned.** README says `llama-3.3-70b-versatile` was retired on 16 Aug 2026 and the default was changed; the code default was not. Production works only if `GROQ_MODEL` is set on Render. A fresh deploy or Colab run without it fails at the first call. | `app/agent.py:24` vs README "Limitations" | 1 (config: no default, required) |
| A-02 | **Silent truncation violates honest outputs.** Prompt rule 3 makes the model add `LIMIT ≤ 100`; the executor's cap is 200. When the model's own LIMIT cuts the result, `truncated` stays `False` and the answer presents a partial set as complete. The executor cap is never reached on this data (largest table: 47 rows), so the only truncation path that exists in practice is the unflagged one. | `prompts.py:48`, `db.py:15`; harness dry-run: 100 of 300 rows returned, `truncated_flag=False` | 3 (AST `LIMIT` rewrite owned by code, cap+1 fetch), 7 (drop rule 3) |
| A-03 | **Regex validator both over-blocks and corrupts.** False rejections: `SELECT REPLACE(name,'a','b')…` (a read-only function), `WHERE name = 'Update Kit'`, `WHERE name LIKE '%delete%'`, `SELECT 'a;b'`. Corruption: comment stripping ignores string literals, so `SELECT '--' \|\| name FROM customers` is accepted and rewritten into broken SQL. No table allowlist: `SELECT * FROM sqlite_master` passes. | Probed in sandbox | 3 (sqlglot AST validation) |
| A-04 | **The "offline" suite goes online when `GROQ_API_KEY` is set.** `test_invalid_payloads_are_rejected_or_handled` with `"   "` passes Pydantic (`min_length=1`), reaches `ask()` with no fake injected, and `get_llm()` builds a real `ChatGroq`. In Colab, where the key is loaded into the environment for live work, `pytest` spends quota and becomes non-deterministic. The test passes today only because the exception is swallowed into `internal_error`. | `tests/test_api.py:167-178`, `agent.py:41-53` | 0 (run pytest with the key removed from env), 1 (autouse fixture + socket guard in `conftest.py`) |
| A-19 | **The synthesizer has no conversation context, so follow-up answers can be false.** `build_answer_messages` receives only the latest question. On the follow-up "And what about customers in Pune?" the SQL correctly counted Pune *orders* (3), and the answer said "There are three customers in Pune". Pune has 2 customers. Valid SQL, correct rows, false sentence. | baseline b14; `prompts.py:111`, `agent.py:146` | 5 (pass resolved question to synthesizer), 7 |

### P1

| ID | Finding | Evidence | Phase |
|---|---|---|---|
| A-05 | **No 429 defences.** No client-side rate limiting, no cache, no fallback model, no token accounting. Relies on the SDK's default retries. Any provider error becomes a generic message with no error code. | `agent.py:52`, `main.py:63-74` | 4 |
| A-06 | **Raw DB errors reach the user.** `format_answer` returns `I couldn't run a query for that. (no such column: revenue)`. Leaks schema internals, gives the user nothing actionable, and there is no typed error taxonomy. | `agent.py:143-144` | 3 (taxonomy), 5 |
| A-07 | **Retry ignores `READ_ONLY`.** If the retry returns `READ_ONLY`, it is treated as SQL, fails validation, and the user gets the generic failure message instead of the read-only refusal. | `agent.py:126-135` | 5 |
| A-08 | **No intent layer and no clarify path.** Scope and write-intent are detected by substring search on the SQL model's output. Ambiguous questions (T8) always get a guessed query with no stated assumption. | `agent.py:92-96` | 5 (`classify_intent`, typed early exits) |
| A-09 | **No answer checking.** Only 20 of up to 200 rows reach the synthesizer; mitigated by a prompt note only. Numbers in the answer are never checked against the rows. | `prompts.py:111-126` | 5 (`check_answer`), 7 |
| A-10 | **No reproducibility metadata.** Requests do not record model ID, prompt version, temperature or schema hash. Logging is `print()`; no `request_id`. | whole `app/` | 5, 9 |
| A-11 | **Module-level mutable singletons** (`_llm`, `_graph`, `_sessions`) with a test-only `set_llm` seam. Sessions are a plain dict mutated from sync endpoints (threadpool), FIFO-evicted, not thread-safe. Spring equivalent of the fix: constructor injection instead of static fields. | `agent.py:38-59`, `main.py:26` | 1 (app factory), 5, 10 |
| A-12 | **Schema restated by hand in the prompt.** Drift test checks column names by substring, so a column named `id` or `name` always "matches". | `prompts.py:10-36`, `test_prompts.py:32` | 5 (generated compact schema + hash) |
| A-20 | **The "exclude cancelled" rule is over-applied and never disclosed.** Prompt rule 5 scopes it to revenue and sales totals; the model also applied it to plain order counts (b05: Feb shows 2, the database has 3 orders placed; b14: 3 vs 4). The answers never say cancelled orders were excluded, so the user cannot tell. The rule's scope needs a recorded decision, and whichever way it goes the answer must state the assumption. | baseline b05, b14; `prompts.py:52-53` | 5, 7 (decision recorded as `D#` before Phase 6 references are frozen) |
| A-21 | **Ambiguous questions get a silent guess.** "Who are the best customers?" was answered as top 10 by total spend, stated as fact with no mention of the chosen definition. Fails the §9 T8 requirement to clarify or state the assumption. | baseline b08 | 5 (`classify_intent` clarify path), 7 |
| A-22 | **Unrequested personal data is selected.** The b08 query returned customer emails that the question did not ask for. The data is synthetic (`example.com`), but the pattern would expose PII on a real schema and costs tokens. | baseline b08 | 7 (prompt: select only needed columns), 9 (`H#`) |

### P2

| ID | Finding | Phase |
|---|---|---|
| A-13 | No `pyproject.toml`, ruff, mypy, bandit, pip-audit, gitleaks, pre-commit, or CI. `pytest` and `httpx` are installed on Render. | 1, 2 |
| A-14 | **Tag registry does not exist.** Code and docs reference `D2`, `D11`, `L3`, `P6`, `S1`, `S2`, but the README has no tagged list, so "continue the existing numbering" has nothing to continue. Proposal: reconstruct the registry in Phase 1 from the references and `DISCOVERIES.md` headings, keeping those six IDs stable. | 1 |
| A-15 | No `/ready`, no request rate limit, no CORS policy, error responses are HTTP 200 by design (D-level decision to revisit, not silently change). | 10 |
| A-16 | Dataset is too small for T12 (truncation) to occur naturally and too small for meaningful latency/perf signal. Proposal: make the row cap configurable and lower it in the eval profile rather than changing the seed, which keeps the Phase 0 baseline comparable. | 3, 6 |
| A-17 | Money stored as `REAL` (already documented). No change proposed. | — |
| A-18 | Style inconsistencies (2-space indent in `build_db.py`, typos in docstrings). ruff will handle these. | 1 |
| A-23 | **Colab dependency clash.** Colab preinstalls `langchain 1.3.18`, which requires `langgraph>=1.2.11`; the repo pins `langgraph==1.2.9`. Harmless today (the app does not import `langchain`; 79/79 pass) but installs into Colab's global site-packages are not reproducible. Proposal: a project venv inside Colab. | 1 |
| A-24 | **Colab runs Python 3.13, the project targets 3.12.** Proposal: `requires-python = ">=3.12,<3.14"` and a 3.12 + 3.13 CI matrix. | 1, 2 |
| A-25 | **The Phase 0 truncation check was too lenient.** It passed b13 on keyword disclosure alone. Corrected in `run_baseline.py`: a partial result passes only when the truncation flag is set *and* the answer discloses it. Recorded here because the change was made after the run. | 0 (fixed) |

---

## 3. Test gaps

- No property-based tests on the validator (the A-03 bugs are exactly what hypothesis finds).
- No tests for the timeout path, the authorizer on its own, or `load_extension`.
- No test that the answer discloses a *model-imposed* LIMIT (A-02).
- No tests for provider failures other than one generic exception: no 429, timeout, or decommissioned-model case.
- No snapshot tests of rendered prompts; prompt edits are invisible to CI.
- Test count is asserted in the README (79), not verified by `--collect-only` in CI.
- Fake LLM is duplicated in `test_agent.py` and `test_api.py`.

## 4. Prompt weaknesses

- Rule 3 (`LIMIT ≤ 100`) is the root cause of A-02; row capping belongs in code.
- Refusal is signalled with magic tokens parsed by substring; fragile and untyped.
- No stated handling for ambiguity; the model always guesses.
- The retry prompt drops conversation history, so a follow-up question that fails loses its context on retry.
- Full schema is always sent; fine at 4 tables, but no version or hash is attached.
- Synthesizer rule "one or two sentences" conflicts with listing questions (e.g. 30 orders).

## 5. 429 exposure

A normal question costs 2 calls and a retried one 3, each carrying the full
system prompt (~540 tokens by character estimate, before history). There is no
throttling, so a burst of UI clicks or a naive eval loop will hit per-minute
limits. The limits were not recorded anywhere in the repo before this audit.

Free-plan limits read from the Groq console on 24 Sep 2026 (re-check before
relying on them; they change):

| Model | RPM | RPD | TPM | TPD |
|---|---|---|---|---|
| `openai/gpt-oss-120b` (deployed) | 30 | 1K | 8K | 200K |
| `openai/gpt-oss-20b` | 30 | 1K | 8K | 200K |
| `qwen/qwen3.8-27b` | 30 | 1K | 8K | 200K |

Measured in the baseline: 29 calls, 12,686 input + 4,469 output tokens, i.e.
**~590 tokens per call**, p50 item latency 1.25 s. **TPM, not RPM, is the
binding limit**: at 8K TPM with a 20% margin, roughly 10 calls per minute fit,
against 24 by RPM. Daily headroom at ~2 calls per question: about 170
questions per day by TPD, 500 by RPD, shared between production and evals.
`llama-3.3-70b-versatile` does not appear in the catalog (confirms A-01).

## 6. Differences from the project instructions

| Instructions say | Archive shows |
|---|---|
| Archive is a `.zip` | It is a `.tar.gz`. No impact. |
| README uses the `D#/L#/S#/T#/H#/P#/V#` tag system | Only scattered references; no registry (A-14). |
| Groq model per role, from config | One model for all three calls, from one env var with a stale default (A-01). |
| Colab and CI on the project's Python | Colab is 3.13.15; project targets 3.12 (A-24). |
| sqlglot validation | Regex validation (A-03). |

## 7. Archive vs GitHub

Verified in Colab on 24 Sep 2026: GitHub HEAD and archive HEAD are both
`f6e44c0` (3 Sep 2026, "Fix live demo link in README.md"), and `diff -rq`
excluding `.git` reports no differences. No reconciliation needed.

## 8. Baseline

Harness: `evals/baseline/run_baseline.py`, questions
`evals/baseline/questions_v0.jsonl` (15 items, tiers T1–T14). It runs the
**unmodified** agent against live Groq, throttled and resumable, and records
model ID, temperature, prompt hash, schema hash and commit per item.
Deterministic checks only (execution match against reference SQL, refusal
correctness, empty and truncation disclosure). The match metric is provisional
and replaced by the Phase 6 harness.

Run: 24 Sep 2026, `openai/gpt-oss-120b`, temperature 0, prompt hash
`637813813df8`, schema hash `cace08063546`, commit `f6e44c0`, row cap 200,
0 errors, 0 rate-limit hits. Raw records:
`evals/baseline/results/baseline_637813813df8_openai_gpt-oss-120b.jsonl`.

| Item | Tier | Verdict | Note |
|---|---|---|---|
| b01 | T1 filter | pass | |
| b02 | T2 aggregate | pass | revenue 183,530 |
| b03 | T3 join | pass | |
| b04 | T4 having | pass | |
| b05 | T5 date | **fail** | cancelled orders excluded from a plain order count, undisclosed (A-20) |
| b06 | T6 top-N | pass | |
| b07 | T7 CTE/share | pass | |
| b08 | T8 ambiguous | **fail** (manual, per §9 T8) | silent guess of "best" = top 10 by spend (A-21); emails selected (A-22) |
| b09 | T9 out of scope | pass | |
| b10 | T10 write | pass | read-only refusal |
| b11 | T10 injection | pass | |
| b12 | T11 empty | pass | |
| b13 | T12 truncation | **fail** (re-graded, A-25) | model added `LIMIT 100` to a 300-row answer; `truncated=False`; answer said "first 20 of the 100 rows" (A-02 confirmed) |
| b14 | T13 follow-up | **fail** | context carried into SQL correctly; answer called 3 orders "three customers" (A-19); cancelled excluded (A-20) |
| b15 | T14 synonym | pass | "buyers" mapped to `customers` |

**Baseline: 11 / 15 pass.** By layer, SQL generation is strong on T1–T7
single-turn questions and on refusals. Every failure is in the parts the
refactor targets: honesty about partial or filtered results (b05, b13),
answer faithfulness (b14), and ambiguity handling (b08). Two of the four
(b13, b14) produced a false statement from correct rows, which is the
`check_answer` node's job in Phase 5.

## Status updates

The findings above are the Phase 0 snapshot and are kept as recorded.

| Finding | Status | Record |
|---|---|---|
| A-02 silent truncation | Fixed for the row cap in Phase 3: the cap is enforced in code and `truncated` is exact. The prompt's own `LIMIT 100` remains and is disclosed through `limit_reached`. | README D19, L5 |
| A-03 regex validator | Resolved in Phase 3: replaced by sqlglot AST validation. | README D18, S1 |
| Test gap: no property tests on the validator | Closed in Phase 3. | README T6 |
| Test gap: no test that a model-imposed LIMIT is disclosed | Closed in Phase 3 (`test_limit_reached_*`). | README D19 |
| A-22 unrequested personal data | Open; prompt work in Phase 7. | — |
