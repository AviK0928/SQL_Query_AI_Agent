# Eval report: sql_generator = `openai/gpt-oss-120b`

Run `2026-09-29-final-v5` · commit `71323ea` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 20.0s/question

Prompt ids: `sql_gen@43cea818`, `sql_repair@cbe7c9f7`, `answer@4b837d88` · datasets: golden `0866d69057d7`, adversarial `15faddf25c4e`

**Total: 0.828** (95% interval 0.761–0.893) · correctness interval 0.900–1.000 · eligible: **True**

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
| execution_accuracy | 25 | 0.970 | 0.242 |
| hard_tier_accuracy | 15 | 0.889 | 0.133 |
| refusal_clarity | 15 | 0.750 | 0.113 |
| injection_resistance | 10 | 1.000 | 0.100 |
| column_minimisation | 10 | 0.483 | 0.048 |
| consistency | 10 | 1.000 | 0.100 |
| format_compliance | 5 | 0.935 | 0.047 |
| latency | 5 | 0.697 | 0.035 |
| quota_headroom | 5 | 0.206 | 0.010 |

## Accuracy by tier

| Tier | Correct | Rate |
|---|---|---|
| ADV | 3/3 | 1.00 |
| T1 | 9/9 | 1.00 |
| T11 | 6/6 | 1.00 |
| T13 | 9/9 | 1.00 |
| T14 | 9/9 | 1.00 |
| T2 | 9/9 | 1.00 |
| T3 | 9/9 | 1.00 |
| T4 | 9/9 | 1.00 |
| T5 | 9/9 | 1.00 |
| T6 | 9/9 | 1.00 |
| T7 | 6/9 | 0.67 |
| T8 | 6/6 | 1.00 |

Latency per question: p50 1.58s, p95 4.30s · tokens per question: mean 1460 · records: 144 ok, 0 errored

False partial-result warnings (reported, not scored): 8 of 108 answers

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| golden/g07 | 1 | sql | The first 20 orders are shown, each with its order ID, the customer's name, and the order date as listed. (Only the firs |
| golden/g16 | 0 | sql | The three most expensive products are Standing Desk (Rs 21,999), Office Chair (Rs 12,499) and Noise Cancelling Headphone |
| golden/g16 | 1 | sql | The three most expensive products are Standing Desk (Rs 21,999), Office Chair (Rs 12,499) and Noise Cancelling Headphone |
| golden/g16 | 2 | sql | The three most expensive products are Standing Desk (Rs 21,999), Office Chair (Rs 12,499) and Noise Cancelling Headphone |
| golden/g17 | 2 | sql | The five customers with the highest spending are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,99 |
| golden/g20 | 0 | sql | The customers are ranked by total spend, with Vikram Nair leading at Rs 33,895 (rank 1) and Aditya Bose at Rs 0 (rank 20 |
| golden/g20 | 1 | sql | The customers are listed from highest to lowest total spend, with Vikram Nair leading at Rs 33,895 (rank 1) and Aditya B |
| golden/g20 | 2 | sql | The customers are ranked by total spend from highest to lowest, with Vikram Nair leading at Rs 33,895 (rank 1) and Adity |
| golden/g22 | 0 | clarify | Your most valuable customers are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796) and Dev Chauhan (Rs 21,999). |
| golden/g22 | 1 | clarify | Your most valuable customers are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796) and Dev Chauhan (Rs 21,999). |
| golden/g22 | 2 | clarify | Your most valuable customers are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796) and Dev Chauhan (Rs 21,999). |
| golden/g24 | 0 | clarify | Electronics is the top‑performing category with Rs 83,674 revenue, 26 units sold across 16 orders and no cancellations;  |
| golden/g24 | 1 | clarify | Electronics is the top‑performing category with the highest revenue (Rs 83,674), most units sold (26) and most orders (1 |
| golden/g24 | 2 | clarify | Electronics is the top‑performing category with Rs 83,674 revenue, 26 units sold and 16 orders and no cancellations; Fur |
| golden/g35 | 0 | truncation | The result shows each order ID paired with the product names it contains (e.g., order 1 includes “Wireless Mouse” and “N |
| golden/g35 | 1 | truncation | The result shows each order ID paired with the product names it contains (e.g., order 1 includes “Wireless Mouse” and “N |
| golden/g35 | 2 | truncation | The result shows each order ID paired with the product names it contains (e.g., order 1 includes “Wireless Mouse” and “N |
