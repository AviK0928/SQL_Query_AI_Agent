# Eval report: synthesizer = `allam-2-7b`

Run `2026-09-26-synth-allam-2-7b` · commit `1a78bfa` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 12.0s/question

Prompt ids: `sql_gen@5fb4fe06`, `sql_repair@a5c30252`, `answer@3c3a3566` · datasets: synthesizer `403d2d0085c4`

**Total: 0.887** (95% interval 0.812–1.000) · correctness interval 1.000–1.000 · eligible: **True**

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
| disclosure | 15 | 0.333 | 0.050 |
| currency_concise | 10 | 0.875 | 0.087 |
| consistency | 15 | 1.000 | 0.150 |
| latency | 10 | 1.000 | 0.100 |
| quota_headroom | 10 | 1.000 | 0.100 |

Latency per question: p50 0.32s, p95 0.48s · tokens per question: mean 232 · records: 24 ok, 0 errored

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| none | | | |
