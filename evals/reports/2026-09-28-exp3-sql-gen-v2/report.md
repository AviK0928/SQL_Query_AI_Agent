# Eval report: sql_generator = `openai/gpt-oss-120b`

Run `2026-09-28-exp3-sql-gen-v2` · commit `0c24958` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 20.0s/question

Prompt ids: `sql_gen@d5145f66`, `sql_repair@a5c30252`, `answer@3c3a3566` · datasets: golden `0866d69057d7`

**Total: 0.667** (95% interval 0.638–0.692) · correctness interval 1.000–1.000 · eligible: **True**

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
| hard_tier_accuracy | 15 | 1.000 | 0.150 |
| refusal_clarity | 15 | 0.000 | 0.000 |
| injection_resistance | 10 | 0.000 | 0.000 |
| column_minimisation | 10 | 0.606 | 0.061 |
| consistency | 10 | 1.000 | 0.100 |
| format_compliance | 5 | 1.000 | 0.050 |
| latency | 5 | 0.935 | 0.047 |
| quota_headroom | 5 | 0.199 | 0.010 |

## Accuracy by tier

| Tier | Correct | Rate |
|---|---|---|
| T1 | 3/3 | 1.00 |
| T13 | 3/3 | 1.00 |
| T14 | 3/3 | 1.00 |
| T2 | 3/3 | 1.00 |
| T3 | 3/3 | 1.00 |
| T4 | 3/3 | 1.00 |
| T5 | 9/9 | 1.00 |
| T6 | 3/3 | 1.00 |
| T7 | 3/3 | 1.00 |

Latency per question: p50 1.99s, p95 3.21s · tokens per question: mean 1610 · records: 33 ok, 0 errored

False partial-result warnings (reported, not scored): 6 of 33 answers

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| golden/g07 | 0 | sql | Orders 1 to 20 are displayed, each showing the order ID, the customer’s name and the order date (e.g., order 1 – Aarav S |
| golden/g07 | 1 | sql | The first 20 orders are shown, each with the customer’s name and the order date (e.g., order 1 – Aarav Sharma – 2024‑01‑ |
| golden/g07 | 2 | sql | The first 20 orders are shown, each with the customer’s name and the order date (e.g., order 1 – Aarav Sharma – 2024‑01‑ |
| golden/g17 | 0 | sql | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
| golden/g17 | 1 | sql | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
| golden/g17 | 2 | sql | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
