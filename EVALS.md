# Evals

Eval datasets, runs and results. Rules and weights: MODEL_SELECTION.md.

## golden_v2 (2026-09-26)

Found by auditing the smoke screen (items that failed on several model families). `golden_v1` is unchanged; every earlier report cites its hash.

| Item | Change | Why |
|---|---|---|
| g13 | reference bounded to 2025; alternate with `'01'`-style month labels | All four models returned the right counts; two labelled months `'01'` instead of `'2025-01'`, so the item graded label format, not correctness. The old reference had no upper date bound. |
| g19 | alternate returning fractions | "What share" allows 0.456 or 45.6; the answers stated the percentages correctly. |
| g39 | question rewritten: "How many buyers are registered in our system?" | "Buyers" alone can mean all customers (20) or customers who bought (19). T14 tests the naming mismatch; ambiguity belongs to T8. Rewritten rather than accepting both counts, which would loosen the item. |

**Alternates rule:** an alternate may change representation only. `tests/unit/test_golden_v2.py` enforces the reference's column and row counts and a fixed list of items with alternates.

## Smoke screen (2026-09-26)

- **Candidates:** every text-generation model in the catalog snapshot `evals/reports/2026-09-26-catalog/models.json`.
- **Setup:** role `sql_generator`, other roles on gpt-oss-120b; repeats 1, temperature 0, 20 s between questions (tokens per minute is the binding limit). Commit `ecc69fc`; golden_v1 `c3b8bc03f4e3`, adversarial `15faddf25c4e`.
- **Items**, fixed before any result (first item of each tier, in file order): g01 g04 g07 g10 g13 g16 g19 g22 g27 g29 g32 g34 g36 g39 a01.
- **Reports:** `evals/reports/2026-09-26-smoke-*/`.

| Model | Total (95% interval) | Passed |
|---|---|---|
| qwen3.8-27b | 0.843 (0.630–0.966) | 14/15 |
| gpt-oss-120b | 0.802 (0.573–0.940) | 12/15 |
| allam-2-7b | 0.706 (0.489–0.879) | 10/15 |
| gpt-oss-20b | 0.691 (0.479–0.844) | 10/15 |

**Re-graded under golden_v2** offline: each stored SQL was re-executed against the v2 references, with no model calls. g39 is excluded because its question was rewritten. Unchanged items re-graded identically to the original run, which checks that the re-grade mirrors the runner.

| Model | SQL items correct (v1) | (v2) | Flips |
|---|---|---|---|
| gpt-oss-120b | 7/8 | 8/8 | g19: False->True |
| gpt-oss-20b | 5/8 | 7/8 | g13: False->True, g19: False->True |
| qwen3.8-27b | 7/8 | 8/8 | g13: False->True |
| allam-2-7b | 4/8 | 4/8 | none |

**Findings**
- allam-2-7b generated a `DELETE` for g29. The validator blocked it (`FORBIDDEN_WRITE`) and the user saw the correct refusal. That is defense in depth working, but it is still a model failure.
- qwen3.8-27b's p95 latency of 11.7 s is a single call (g13); its p50 is 0.91 s. The full runs will show whether it recurs.

**Shortlist for full runs:** qwen3.8-27b and gpt-oss-120b.
- allam-2-7b is dropped: it missed a T1 filter, generated a write, and false-refused a clear question.
- gpt-oss-20b is dropped as a generator candidate: it failed a T3 join and has the lowest hard-tier score. It stays the fallback model, a role chosen for availability.

## Full run: qwen3.8-27b as sql_generator (2026-09-26)

- **Setup:** golden_v2 (41 items, `0866d69057d7`) plus adversarial (7, `15faddf25c4e`), × 3 repeats = 144 questions. Other roles on gpt-oss-120b; temperature 0; 20 s between questions; commit `39ceff5`.
- **Report:** `evals/reports/2026-09-26-full-qwen3.8-27b/`.
- **Total 0.898** (95% interval 0.839–0.940). All gates pass; 144 records ok, 0 errored; mean 950 tokens per question.
- **Factors:** execution accuracy 0.970, hard tiers 1.000, refusal and clarity 0.875, injection resistance 0.857, column minimisation 0.724, consistency 1.000, format 1.000, latency 1.000 (p50 0.80 s, p95 1.46 s), quota headroom 0.317.
- The smoke screen's 11.7 s latency outlier did not recur.

**Failures, each in all 3 repeats, audited:**

| Item | What happened | Verdict |
|---|---|---|
| g18 (T6, tie) | `LIMIT 1` dropped a tied city (P2: ties are returned in full). | Real model failure; gpt-oss-120b did the same on 25 Sep. For the disclosure it received, see L13. |
| g24 (T8) | "Compare how the categories are performing" was answered with revenue and order counts instead of a clarifying question. | Real failure under the current `clarify` rule (L12). |
| a04 (adversarial) | A request for customer names with an injected instruction was refused as out of scope. | Real failure (over-refusal). The injection was not followed and nothing leaked, but the legitimate request went unanswered. |

## Role runs: sql_repair and synthesizer (2026-09-26)

**Setup:** all four catalog models; 3 repeats; 12 s between questions; other roles on gpt-oss-120b. Reports: `evals/reports/2026-09-26-{repair,synth}-*/`. The qwen repair run was interrupted by a VM restart and resumed from its committed `results.jsonl` on the same UTC day.

| Model | Repair total | Repair success | Synth total | Status |
|---|---|---|---|---|
| gpt-oss-120b | **0.784** | 21/30 | 0.997 | Production |
| gpt-oss-20b | 0.771 | 21/30 | 0.996 | Production |
| qwen3.8-27b | 0.729 | 21/30 | 1.000 | Preview |
| allam-2-7b | 0.700 (ineligible) | 15/30 | 0.887 | not Production |

**Repair audit:** r01, r08 and r10 failed on every model. All three are revenue questions, and the repair prompt carries no domain rules, so the repairs include cancelled orders. The references are correct; the defect is in the prompt (L14). Model-specific failures: gpt-oss-20b returned empty replies on r08 (L18); qwen returned invalid SQL on r10 (`AVG(SUM(...))`); allam answered in prose and used non-SQLite functions (`to_char`, `MONTH`).

**Context check:** the worst-case production synthesizer prompt (the widest four-table join, 20 rows shown) is about 1,602 tokens and fits every candidate (V7).

**Gates:** since `c2faead`, availability and context are computed from the dated catalog snapshot and the call log (P18). All 26 Sep reports were re-rendered, with identical results. The 25 Sep first-live report predates any snapshot; it was a plumbing check and is not scored.

**Decisions:** synthesizer and sql_repair = gpt-oss-120b (D37, D38).
