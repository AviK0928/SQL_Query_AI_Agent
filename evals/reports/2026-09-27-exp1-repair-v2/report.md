# Eval report: sql_repair = `openai/gpt-oss-120b`

Run `2026-09-27-exp1-repair-v2` · commit `0ac4799` · other roles on `openai/gpt-oss-120b` · temperature 0 · 3 repeat(s) · paced 12.0s/question

Prompt ids: `sql_gen@5fb4fe06`, `sql_repair@cbe7c9f7`, `answer@3c3a3566` · datasets: repair `54978e241dcb`

**Total: 0.954** (95% interval 0.952–0.957) · correctness interval 1.000–1.000 · eligible: **True**

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
| repair_success | 60 | 1.000 | 0.600 |
| consistency | 20 | 1.000 | 0.200 |
| latency | 10 | 1.000 | 0.100 |
| quota_headroom | 10 | 0.541 | 0.054 |

## Accuracy by tier

| Tier | Correct | Rate |
|---|---|---|
| repair | 30/30 | 1.00 |

Latency per question: p50 0.60s, p95 0.82s · tokens per question: mean 592 · records: 30 ok, 0 errored

## Items that failed a check

| Item | Repeat | Kind | Answer |
|---|---|---|---|
| none | | | |
