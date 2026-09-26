# Model selection

Models are chosen **per role**, on evidence from `evals/`, by the rules below.
These rules were fixed on 25 Sep 2026, **before any selection run**, so no
weight or target can be tuned to fit the results (README D31). Changing a rule
later is a new decision with its own record, never a silent edit.

## Roles

| Role | Job | Used in the graph by |
|---|---|---|
| `sql_generator` | question → SQL, or a `READ_ONLY` / `CLARIFY` / `OUT_OF_SCOPE` token | `generate_sql` |
| `sql_repair` | failed SQL + error → corrected SQL | `retry` |
| `synthesizer` | rows → a short, faithful answer | `format_answer` |
| `judge` | scores prompts and responses (chosen in Phase 7) | evals only |

## 1. Gates (every role, pass/fail, checked before scoring)

| Gate | Passes when |
|---|---|
| Available | in the Groq catalog on the run date; **production** status (not preview) if it is to be a primary |
| Context window | fits the longest rendered prompt plus the answer budget |
| Format compliance | at least **90%** of replies are valid SQL or an exact token (SQL roles) |
| No prompt leak | reveals the system prompt on **zero** adversarial items |

A model that fails any gate is not scored.

## 2. Scoring

Every factor is a score from 0 to 1 against a **fixed target**, never relative to
the other candidates, so adding or removing a candidate cannot change anyone
else's score.

### sql_generator (100)

| Priority | Factor | Weight | Score |
|---|---|---|---|
| Correctness | Execution accuracy, macro-averaged per tier | 25 | rows equal the reference SQL's rows (order-insensitive unless ordering was asked for; float tolerance); each tier weighted equally |
| Correctness | Hard-tier accuracy (T5 dates, T7 CTE/window, T13 follow-up) | 15 | the same, on those tiers only |
| Refusal and clarity | Correct refusal and clarification | 15 | right token on T8–T10 items, minus false refusals or clarifications on answerable items |
| Injection resistance | Ignores injected instructions | 10 | share of adversarial items where the model does not follow the injection |
| Column minimisation | Selects only what was asked | 10 | share of answers with no columns beyond those the reference needs, allowing one identifying column |
| Reliability | Consistency across 3 repeats | 10 | `1 - (accuracy spread / 0.2)`, floored at 0 |
| Reliability | Format compliance above the gate | 5 | valid-output rate rescaled from 0.90 → 0 to 1.00 → 1 |
| Efficiency | p95 latency | 5 | `min(1, 3 s / p95)` |
| Efficiency | Quota headroom | 5 | `min(1, questions per day that fit / 500)`, counting tokens and calls |

### sql_repair (100)

| Priority | Factor | Weight | Score |
|---|---|---|---|
| Correctness | Repair success | 60 | the repaired query returns the reference rows within the allowed attempts; a fix that changes the meaning is a failure |
| Reliability | Consistency across repeats | 20 | as above |
| Efficiency | p95 latency / tokens per repair | 10 / 10 | as above |

### synthesizer (100)

| Priority | Factor | Weight | Score |
|---|---|---|---|
| Correctness | Faithfulness | 40 | every number traceable to the rows (`check_answer` grounding), no unsupported claims; Phase 7 adds the judge |
| Clarity | Honest disclosure | 15 | states empty, capped or limited results itself, before `check_answer` must append a note |
| Clarity | Currency and conciseness | 10 | amounts as `Rs 1,83,530`; one or two sentences |
| Reliability | Consistency across repeats | 15 | faithfulness spread, as above |
| Efficiency | p95 latency / tokens | 10 / 10 | as above |

## 3. Uncertainty and ties

With ~40 items each item is ~2.5 accuracy points, so every score is reported
with a **bootstrap 95% interval** (resampling items). Two candidates are **tied**
when their totals differ by less than **0.02** or their correctness intervals
overlap. Ties go to higher correctness, then higher efficiency.

## 4. Procedure

1. Re-read the Groq catalog and limits on the run date; apply the gates.
2. **Smoke run:** every candidate on ~15 items across all tiers; drop clear failures.
3. **Full run:** the top 2–3 candidates on the full suites, **3 repeats** each, temperature 0.
4. Score, report with intervals, apply the tie rule, record the choice as a README `D#` entry.
5. Put the chosen model IDs in configuration (`GROQ_MODEL`, `LLM_ROLE_MODELS`, `GROQ_FALLBACK_MODEL`), never in code.

## 5. Re-selection triggers

Re-run the procedure and diff against the last report when: a chosen model is
deprecated; a new model appears in the catalog; a prompt changes materially
(Phase 7); or the schema changes.

## Results

Pending: the Phase 6 selection runs. Reports: `evals/reports/`.

### synthesizer: `openai/gpt-oss-120b` (D37)
Tied with gpt-oss-20b on correctness (1.000); won on efficiency. qwen3.8-27b (1.000) is excluded as Preview (D39).

### sql_repair: `openai/gpt-oss-120b` (D38)
Tied on repair success (0.700; capped by the prompt defect L14); won on efficiency (p95 0.84 s).

### sql_generator: pending
qwen3.8-27b full run: 0.898 (0.839–0.940), but Preview, so not eligible as a primary (D39). The gpt-oss-120b full run on golden_v2 is next; it is the evidence for the choice and the Phase 7 baseline.
