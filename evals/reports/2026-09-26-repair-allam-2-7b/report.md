# Eval report: sql_repair = `allam-2-7b`

Run `2026-09-26-repair-allam-2-7b` · commit `1a78bfa` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 12.0s/question

Prompt ids: `sql_gen@5fb4fe06`, `sql_repair@a5c30252`, `answer@3c3a3566` · datasets: repair `54978e241dcb`

**Total: 0.700** (95% interval 0.520–0.880) · correctness interval 0.200–0.800 · eligible: **False**

## Gates

| Gate | Pass |
|---|---|
| available | ✅ |
| context_window | ✅ |
| format_compliance | ❌ |
| no_prompt_leak | ✅ |

## Factors

| Factor | Weight | Score | Contribution |
|---|---|---|---|
| repair_success | 60 | 0.500 | 0.300 |
| consistency | 20 | 1.000 | 0.200 |
| latency | 10 | 1.000 | 0.100 |
| quota_headroom | 10 | 1.000 | 0.100 |

## Accuracy by tier

| Tier | Correct | Rate |
|---|---|---|
| repair | 15/30 | 0.50 |

Latency per question: p50 0.32s, p95 0.57s · tokens per question: mean 533 · records: 30 ok, 0 errored

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| repair/r01 | 0 | repair | To calculate the total revenue, we need to consider the product prices from the order_items table. Here's the corrected  |
| repair/r01 | 1 | repair | To calculate the total revenue, we need to consider the product prices from the order_items table. Here's the corrected  |
| repair/r01 | 2 | repair | To calculate the total revenue, we need to consider the product prices from the order_items table. Here's the corrected  |
| repair/r05 | 0 | repair | SELECT COUNT(*) FROM orders WHERE to_char(order_date, 'YYYY') = '2024';  |
| repair/r05 | 1 | repair | SELECT COUNT(*) FROM orders WHERE to_char(order_date, 'YYYY') = '2024';  |
| repair/r05 | 2 | repair | SELECT COUNT(*) FROM orders WHERE to_char(order_date, 'YYYY') = '2024';  |
| repair/r06 | 0 | repair | SELECT MONTH(order_date) AS m, COUNT(*) FROM orders GROUP BY m;  |
| repair/r06 | 1 | repair | SELECT MONTH(order_date) AS m, COUNT(*) FROM orders GROUP BY m;  |
| repair/r06 | 2 | repair | SELECT MONTH(order_date) AS m, COUNT(*) FROM orders GROUP BY m;  |
| repair/r08 | 0 | repair | SELECT p.category, SUM(o.quantity * o.unit_price) AS revenue FROM order_items o JOIN products p ON o.product_id = p.id G |
| repair/r08 | 1 | repair | SELECT p.category, SUM(o.quantity * o.unit_price) AS revenue FROM order_items o JOIN products p ON o.product_id = p.id G |
| repair/r08 | 2 | repair | SELECT p.category, SUM(o.quantity * o.unit_price) AS revenue FROM order_items o JOIN products p ON o.product_id = p.id G |
| repair/r10 | 0 | repair | To find the average order value, we need to calculate the total of all order items and then divide by the number of orde |
| repair/r10 | 1 | repair | To find the average order value, we need to calculate the total of all order items and then divide by the number of orde |
| repair/r10 | 2 | repair | To find the average order value, we need to calculate the total of all order items and then divide by the number of orde |
