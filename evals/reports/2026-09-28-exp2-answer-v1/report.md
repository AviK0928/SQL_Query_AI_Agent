# Eval report: synthesizer = `openai/gpt-oss-120b`

Run `2026-09-28-exp2-answer-v1` · commit `88ba920` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 12.0s/question

Prompt ids: `sql_gen@5fb4fe06`, `sql_repair@cbe7c9f7`, `answer@3c3a3566` · datasets: synthesizer_v2 `29b4686ae4a4`

**Total: 0.980** (95% interval 0.966–0.999) · correctness interval 1.000–1.000 · eligible: **True**

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
| faithfulness | 40 | 1.000 | 0.400 |
| disclosure | 15 | 1.000 | 0.150 |
| currency_concise | 10 | 1.000 | 0.100 |
| consistency | 15 | 1.000 | 0.150 |
| latency | 10 | 1.000 | 0.100 |
| quota_headroom | 10 | 0.801 | 0.080 |

Latency per question: p50 0.68s, p95 0.97s · tokens per question: mean 400 · records: 36 ok, 0 errored

False partial-result warnings (reported, not scored): 15 of 36 answers

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| synthesizer_v2/s06 | 0 | synthesizer_v2 | Vikram Nair spent Rs 33,895, Ananya Iyer spent Rs 28,796, and Dev Chauhan spent Rs 21,999. (Only the first rows are show |
| synthesizer_v2/s06 | 1 | synthesizer_v2 | Vikram Nair spent Rs 33,895, Ananya Iyer spent Rs 28,796, and Dev Chauhan spent Rs 21,999. (Only the first rows are show |
| synthesizer_v2/s06 | 2 | synthesizer_v2 | The top three customers are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796) and Dev Chauhan (Rs 21,999). Only the first |
| synthesizer_v2/s09 | 0 | synthesizer_v2 | The result shows orders 1 to 20, each with its order ID, the customer’s name, and the order date (e.g., order 1 – Aarav  |
| synthesizer_v2/s09 | 1 | synthesizer_v2 | The first 20 orders are listed, showing each order ID with the customer’s name and the order date (orders 1–20 as displa |
| synthesizer_v2/s09 | 2 | synthesizer_v2 | The first 20 orders are shown, each with the customer’s name and the order date (e.g., order 1 – Aarav Sharma – 2024‑01‑ |
| synthesizer_v2/s10 | 0 | synthesizer_v2 | The first 20 rows pair each order ID with a product (e.g., order 1 includes Wireless Mouse and Notebook Set, order 2 inc |
| synthesizer_v2/s10 | 1 | synthesizer_v2 | The first 20 rows pair each order ID with a product (e.g., order 1 includes Wireless Mouse and Notebook Set, order 2 inc |
| synthesizer_v2/s10 | 2 | synthesizer_v2 | The result lists each order ID together with every product it contains; the first 20 rows show, for example, order 1 has |
| synthesizer_v2/s11 | 0 | synthesizer_v2 | The three most expensive products are Standing Desk (Rs 21,999), Office Chair (Rs 12,499) and Noise Cancelling Headphone |
| synthesizer_v2/s11 | 1 | synthesizer_v2 | The three most expensive products are Standing Desk (Rs 21,999), Office Chair (Rs 12,499) and Noise Cancelling Headphone |
| synthesizer_v2/s11 | 2 | synthesizer_v2 | The three most expensive products are Standing Desk (Rs 21,999), Office Chair (Rs 12,499) and Noise Cancelling Headphone |
| synthesizer_v2/s12 | 0 | synthesizer_v2 | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
| synthesizer_v2/s12 | 1 | synthesizer_v2 | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
| synthesizer_v2/s12 | 2 | synthesizer_v2 | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
