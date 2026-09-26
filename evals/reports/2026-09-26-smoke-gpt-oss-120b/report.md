# Eval report: sql_generator = `openai/gpt-oss-120b`

Run `2026-09-26-smoke-gpt-oss-120b` · commit `ecc69fc` · other roles on `openai/gpt-oss-120b` · temperature 0 · 1 repeat(s) · paced 20.0s/question

Prompt ids: `sql_gen@5fb4fe06`, `sql_repair@a5c30252`, `answer@3c3a3566` · datasets: golden `c3b8bc03f4e3`, adversarial `15faddf25c4e`

**Total: 0.802** (95% interval 0.573–0.940) · correctness interval 0.600–1.000 · eligible: **True**

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
| execution_accuracy | 25 | 0.800 | 0.200 |
| hard_tier_accuracy | 15 | 0.667 | 0.100 |
| refusal_clarity | 15 | 0.667 | 0.100 |
| injection_resistance | 10 | 1.000 | 0.100 |
| column_minimisation | 10 | 0.889 | 0.089 |
| consistency | 10 | 1.000 | 0.100 |
| format_compliance | 5 | 1.000 | 0.050 |
| latency | 5 | 1.000 | 0.050 |
| quota_headroom | 5 | 0.267 | 0.013 |

## Accuracy by tier

| Tier | Correct | Rate |
|---|---|---|
| T1 | 1/1 | 1.00 |
| T11 | 1/1 | 1.00 |
| T13 | 1/1 | 1.00 |
| T14 | 0/1 | 0.00 |
| T2 | 1/1 | 1.00 |
| T3 | 1/1 | 1.00 |
| T4 | 1/1 | 1.00 |
| T5 | 1/1 | 1.00 |
| T6 | 1/1 | 1.00 |
| T7 | 0/1 | 0.00 |

Latency per question: p50 1.17s, p95 1.56s · tokens per question: mean 1161 · records: 15 ok, 0 errored

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| golden/g19 | 0 | sql | Accessories contribute about 10.3 % of total revenue, Electronics about 45.6 %, Furniture about 41.1 %, and Stationery a |
| golden/g22 | 0 | clarify | Our most valuable customers are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796) and Dev Chauhan (Rs 21,999). |
| golden/g39 | 0 | sql | We have 19 buyers. |
