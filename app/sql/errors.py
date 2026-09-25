"""Typed error taxonomy for the SQL safety layer.

Every rejection or execution failure in app/sql/ is raised as SqlSafetyError
carrying a code from SqlErrorCode. The graph node converts it into state; the
code alone decides whether the repair step may try again and which fixed
message the user sees.

`detail` is written for the repair prompt and the logs. It may name tables
and describe the problem, but it never contains data values or secrets.
`user_message` is the only text shown to the end user.

Spring comparison: a typed exception carrying an error code, mapped centrally
the way a @ControllerAdvice maps exceptions to responses.
"""

from __future__ import annotations

from enum import StrEnum


class SqlErrorCode(StrEnum):
    """Why a query was rejected or failed. Values are stable strings for logs."""

    EMPTY_QUERY = "EMPTY_QUERY"
    TOO_LONG = "TOO_LONG"
    PARSE_ERROR = "PARSE_ERROR"
    MULTIPLE_STATEMENTS = "MULTIPLE_STATEMENTS"
    FORBIDDEN_WRITE = "FORBIDDEN_WRITE"
    FORBIDDEN_STATEMENT = "FORBIDDEN_STATEMENT"
    NOT_A_SELECT = "NOT_A_SELECT"
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    FORBIDDEN_FUNCTION = "FORBIDDEN_FUNCTION"
    INVALID_LIMIT = "INVALID_LIMIT"
    TIMEOUT = "TIMEOUT"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    DB_DENIED = "DB_DENIED"


# Codes where showing the model its mistake can plausibly produce a correct
# query. Everything else is final: retrying a forbidden write or a timeout
# spends a Groq request without any chance of a safe, useful result.
REPAIRABLE_CODES: frozenset[SqlErrorCode] = frozenset(
    {
        SqlErrorCode.PARSE_ERROR,
        SqlErrorCode.UNKNOWN_TABLE,
        SqlErrorCode.EXECUTION_ERROR,
        SqlErrorCode.INVALID_LIMIT,
    }
)

_GENERIC = "I couldn't run a query for that question. Try rephrasing it."

USER_MESSAGES: dict[SqlErrorCode, str] = {
    SqlErrorCode.EMPTY_QUERY: _GENERIC,
    SqlErrorCode.TOO_LONG: _GENERIC,
    SqlErrorCode.PARSE_ERROR: _GENERIC,
    SqlErrorCode.MULTIPLE_STATEMENTS: _GENERIC,
    SqlErrorCode.FORBIDDEN_WRITE: (
        "I can only read from this database, not change it. "
        "Try asking a question about the existing data instead."
    ),
    SqlErrorCode.FORBIDDEN_STATEMENT: (
        "That kind of database command isn't allowed. I can only run read-only queries."
    ),
    SqlErrorCode.NOT_A_SELECT: _GENERIC,
    SqlErrorCode.UNKNOWN_TABLE: _GENERIC,
    SqlErrorCode.FORBIDDEN_FUNCTION: (
        "That query uses a database function that isn't allowed here."
    ),
    SqlErrorCode.INVALID_LIMIT: _GENERIC,
    SqlErrorCode.TIMEOUT: "The query took too long and was stopped. Try a narrower question.",
    SqlErrorCode.EXECUTION_ERROR: _GENERIC,
    SqlErrorCode.DB_DENIED: "That query was blocked by the database's read-only protection.",
}


class SqlSafetyError(Exception):
    """A query was rejected or failed. Raised only inside app/sql/."""

    def __init__(self, code: SqlErrorCode, detail: str) -> None:
        # An empty detail would reach the retry prompt as a blank message.
        # Enforced here so no raise site can omit it (found by mutation testing).
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError("SqlSafetyError needs a non-empty detail message")
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail

    @property
    def repairable(self) -> bool:
        return self.code in REPAIRABLE_CODES

    @property
    def user_message(self) -> str:
        return USER_MESSAGES[self.code]
