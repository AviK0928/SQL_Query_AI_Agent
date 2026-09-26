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
