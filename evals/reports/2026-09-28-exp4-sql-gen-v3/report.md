# Eval report: sql_generator = `openai/gpt-oss-120b`

Run `2026-09-28-exp4-sql-gen-v3` · commit `0c24958` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 20.0s/question

Prompt ids: `sql_gen@485384f7`, `sql_repair@a5c30252`, `answer@3c3a3566` · datasets: golden `0866d69057d7`

**Total: 0.513** (95% interval 0.465–0.550) · correctness interval 1.000–1.000 · eligible: **True**

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
| column_minimisation | 10 | 0.500 | 0.050 |
| consistency | 10 | 1.000 | 0.100 |
| format_compliance | 5 | 1.000 | 0.050 |
| latency | 5 | 1.000 | 0.050 |
| quota_headroom | 5 | 0.268 | 0.013 |

## Accuracy by tier

| Tier | Correct | Rate |
|---|---|---|
| T1 | 6/6 | 1.00 |
| T11 | 6/6 | 1.00 |
| T3 | 3/3 | 1.00 |
| T4 | 3/3 | 1.00 |
| T6 | 6/6 | 1.00 |

Latency per question: p50 1.38s, p95 1.96s · tokens per question: mean 1192 · records: 30 ok, 0 errored

False partial-result warnings (reported, not scored): 15 of 30 answers

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| golden/g07 | 0 | sql | The result shows each order’s ID together with the customer’s name and the order date; only the first 20 of 30 rows are  |
| golden/g07 | 1 | sql | The result shows each order’s ID together with the customer’s name and the order date, displaying the first 20 of 30 row |
| golden/g07 | 2 | sql | The result shows each order’s ID together with the customer’s name and the order date; only the first 20 of 30 rows are  |
| golden/g16 | 0 | sql | The three most expensive products are Standing Desk (Rs 21,999), Office Chair (Rs 12,499) and Noise Cancelling Headphone |
| golden/g16 | 1 | sql | The three most expensive products are Standing Desk (Rs 21,999), Office Chair (Rs 12,499) and Noise Cancelling Headphone |
| golden/g16 | 2 | sql | The three most expensive products are Standing Desk (Rs 21,999), Office Chair (Rs 12,499) and Noise Cancelling Headphone |
| golden/g17 | 0 | sql | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
| golden/g17 | 1 | sql | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
| golden/g17 | 2 | sql | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
| golden/g34 | 0 | truncation | The result shows distinct customer‑ID pairs, beginning with customer 1 paired with customers 2 through 20 and also inclu |
| golden/g34 | 1 | truncation | The result shows all distinct customer pairs – for example, Aarav Sharma paired with each of the other customers, and Di |
| golden/g34 | 2 | truncation | The result shows all distinct pairs of customers, starting with Aarav Sharma paired with every other customer and then D |
| golden/g35 | 0 | truncation | The query returns pairs of order IDs and the product names they contain—for example, order 1 includes “Wireless Mouse” a |
| golden/g35 | 1 | truncation | The result shows each order ID paired with the product names it contains (e.g., order 1 – Wireless Mouse, Notebook Set;  |
| golden/g35 | 2 | truncation | The query returns pairs of order IDs and the product names they contain—for example, order 1 includes “Wireless Mouse” a |
