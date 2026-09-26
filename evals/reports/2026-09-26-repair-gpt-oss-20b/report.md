# Eval report: sql_repair = `openai/gpt-oss-20b`

Run `2026-09-26-repair-gpt-oss-20b` · commit `1526ff4` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 12.0s/question

Prompt ids: `sql_gen@5fb4fe06`, `sql_repair@a5c30252`, `answer@3c3a3566` · datasets: repair `54978e241dcb`

**Total: 0.771** (95% interval 0.576–0.965) · correctness interval 0.400–1.000 · eligible: **True**

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
| repair_success | 60 | 0.700 | 0.420 |
| consistency | 20 | 1.000 | 0.200 |
| latency | 10 | 1.000 | 0.100 |
| quota_headroom | 10 | 0.507 | 0.051 |

## Accuracy by tier

| Tier | Correct | Rate |
|---|---|---|
| repair | 21/30 | 0.70 |

Latency per question: p50 0.38s, p95 2.40s · tokens per question: mean 631 · records: 30 ok, 0 errored

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| repair/r01 | 0 | repair | SELECT SUM(unit_price * quantity) FROM order_items |
| repair/r01 | 1 | repair | SELECT SUM(unit_price * quantity) FROM order_items |
| repair/r01 | 2 | repair | SELECT SUM(unit_price * quantity) FROM order_items |
| repair/r08 | 0 | repair | SELECT p.category, SUM(i.unit_price * i.quantity) FROM order_items i JOIN products p ON p.id = i.product_id GROUP BY p.c |
| repair/r08 | 1 | repair |  |
| repair/r08 | 2 | repair |  |
| repair/r10 | 0 | repair | SELECT AVG(order_total) FROM (SELECT o.id, SUM(oi.unit_price * oi.quantity) AS order_total FROM orders o JOIN order_item |
| repair/r10 | 1 | repair | SELECT AVG(order_total) FROM (SELECT o.id, SUM(oi.unit_price * oi.quantity) AS order_total FROM orders o JOIN order_item |
| repair/r10 | 2 | repair | SELECT AVG(order_total) FROM (SELECT o.id, SUM(oi.unit_price * oi.quantity) AS order_total FROM orders o JOIN order_item |
