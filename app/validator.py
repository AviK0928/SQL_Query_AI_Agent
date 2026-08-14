
"""Validate LLM-generated SQL before execution.

This is the first gate (S1). It is not the last line of defence - the 
read-only connection and SQLite authorizer in db.py (S2) are the actual
enforcement. The job here is to fail cheaply and with a message the retry
loop can act on."""

import re

FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "CREATE", "REPLACE", "GRANT", "REVOKE", "ATTACH", "DETACH",
    "PRAGMA", "VACUUM",
]

# Matches line comment/block comment
_COMMENT_PATTERN = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(sql):
    """Remove SQL comments so keywords cannot hide behind them."""
    return _COMMENT_PATTERN.sub(" ", sql)

def validate_sql(sql):
    """Check that `sql` is a single, read-only SELECT statement.

    Returns (is_valid, cleaned_sql_or_None, error_message_or_None).
    """
    if sql is None or not sql.strip():
        return False, None, "No SQL was generated."

    cleaned = _strip_comments(sql).strip()

    # Allow exactly one trailing semicolon; anything else means stacking.
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()

    if ";" in cleaned:
        return False, None, "Multiple SQL statements are not allowed. Return a single SELECT query."

    if not cleaned:
        return False, None, "No SQL was generated."

    upper = cleaned.upper()

    if not upper.startswith(("SELECT", "WITH")):
        first_word = upper.split()[0] if upper.split() else "nothing"
        return False, None, f"Only SELECT queries are allowed, but the query started with {first_word}."

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            return False, None, f"The keyword {keyword} is not allowed. Only read-only SELECT queries are permitted."

    return True, cleaned, None
