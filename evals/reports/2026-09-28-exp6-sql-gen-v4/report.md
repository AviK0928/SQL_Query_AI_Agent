# Eval report: sql_generator = `openai/gpt-oss-120b`

Run `2026-09-28-exp6-sql-gen-v4` · commit `0c24958` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 20.0s/question

Prompt ids: `sql_gen@8c7d4fa8`, `sql_repair@a5c30252`, `answer@3c3a3566` · datasets: golden `0866d69057d7`

**Total: 0.495** (95% interval 0.460–0.531) · correctness interval 1.000–1.000 · eligible: **True**

## Gates

| Gate | Pass |
|---|---|
| available | ✅ |
| context_window | ✅ |
| format_compliance | ✅ |
| no_prompt_leak | ✅ |

## Factors

| Factor | Weight | Score | Contribution |
|---|---|---|---|
| execution_accuracy | 25 | 1.000 | 0.250 |
| hard_tier_accuracy | 15 | 0.000 | 0.000 |
| refusal_clarity | 15 | 0.000 | 0.000 |
| injection_resistance | 10 | 0.000 | 0.000 |
| column_minimisation | 10 | 0.333 | 0.033 |
| consistency | 10 | 1.000 | 0.100 |
| format_compliance | 5 | 1.000 | 0.050 |
| latency | 5 | 1.000 | 0.050 |
| quota_headroom | 5 | 0.235 | 0.012 |

## Accuracy by tier

| Tier | Correct | Rate |
|---|---|---|
| T4 | 3/3 | 1.00 |
| T6 | 9/9 | 1.00 |
| T8 | 6/6 | 1.00 |

Latency per question: p50 2.03s, p95 2.72s · tokens per question: mean 1361 · records: 18 ok, 0 errored

False partial-result warnings (reported, not scored): 0 of 18 answers

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| none | | | |
