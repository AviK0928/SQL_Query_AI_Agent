"""Observability (Phase 9): structured logs and the request id.

logs.py configures one JSON line per log record on stdout; middleware.py gives
every HTTP request its id and one access record. The LLM call log
(app/llm/calllog.py) writes the same line format with the same request id.
"""
