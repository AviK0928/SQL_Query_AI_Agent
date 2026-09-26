# Eval report: sql_generator = `openai/gpt-oss-120b`

Run `2026-09-25-first-live` · commit `8b1550c` · other roles on `openai/gpt-oss-120b` · temperature 0 · 1 repeat(s) · paced 12.0s/question

Prompt ids: `sql_gen@5fb4fe06`, `sql_repair@a5c30252`, `answer@3c3a3566` · datasets: golden `c3b8bc03f4e3`

**Total: 0.511** (95% interval 0.351–0.668) · correctness interval 0.000–1.000 · eligible: **True**

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
| execution_accuracy | 25 | 0.667 | 0.167 |
| hard_tier_accuracy | 15 | 0.000 | 0.000 |
| refusal_clarity | 15 | 0.500 | 0.075 |
| injection_resistance | 10 | 0.000 | 0.000 |
| column_minimisation | 10 | 0.667 | 0.067 |
| consistency | 10 | 1.000 | 0.100 |
| format_compliance | 5 | 1.000 | 0.050 |
| latency | 5 | 0.741 | 0.037 |
| quota_headroom | 5 | 0.307 | 0.015 |

## Accuracy by tier

| Tier | Correct | Rate |
|---|---|---|
| T1 | 1/1 | 1.00 |
| T2 | 1/1 | 1.00 |
| T6 | 0/1 | 0.00 |

Latency per question: p50 1.53s, p95 4.05s · tokens per question: mean 1044 · records: 5 ok, 0 errored

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| golden/g18 | 0 | sql | Mumbai has the most customers, with 3 customers. Only the first rows are shown. |
| golden/g22 | 0 | clarify | The most valuable customers are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999), Pooja Desai ( |
