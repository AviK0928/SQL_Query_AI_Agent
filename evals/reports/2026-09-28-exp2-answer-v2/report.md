# Eval report: synthesizer = `openai/gpt-oss-120b`

Run `2026-09-28-exp2-answer-v2` · commit `88ba920` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 12.0s/question

Prompt ids: `sql_gen@5fb4fe06`, `sql_repair@cbe7c9f7`, `answer@4b837d88` · datasets: synthesizer_v2 `29b4686ae4a4`

**Total: 0.962** (95% interval 0.954–0.972) · correctness interval 1.000–1.000 · eligible: **True**

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
| quota_headroom | 10 | 0.623 | 0.062 |

Latency per question: p50 0.66s, p95 1.15s · tokens per question: mean 514 · records: 36 ok, 0 errored

False partial-result warnings (reported, not scored): 6 of 36 answers

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| synthesizer_v2/s09 | 0 | synthesizer_v2 | The first 20 orders are shown, each with its order ID, the customer’s name, and the order date as listed. (Only the firs |
| synthesizer_v2/s10 | 0 | synthesizer_v2 | The first 20 rows pair each order ID with its product, e.g., order 1 includes Wireless Mouse and Notebook Set, order 2 i |
| synthesizer_v2/s10 | 1 | synthesizer_v2 | The result pairs each order ID with its product (e.g., order 1 has Wireless Mouse and Notebook Set, order 2 has Noise Ca |
| synthesizer_v2/s10 | 2 | synthesizer_v2 | Each order ID is paired with the product(s) it contains – for example, order 1 has Wireless Mouse and Notebook Set, orde |
| synthesizer_v2/s11 | 2 | synthesizer_v2 | The three most expensive products are Standing Desk at Rs 21,999, Office Chair at Rs 12,499, and Noise Cancelling Headph |
| synthesizer_v2/s12 | 0 | synthesizer_v2 | The five customers who have spent the most are Vikram Nair (Rs 33,895), Ananya Iyer (Rs 28,796), Dev Chauhan (Rs 21,999) |
