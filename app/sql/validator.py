"""AST-based validation of LLM-generated SQL (S1).

The first of two independent layers. It parses the SQL with sqlglot and
accepts exactly one read-only SELECT over allowlisted tables, then rewrites the
outermost LIMIT so the executor can detect truncation honestly. The read-only
connection and authorizer in the executor (S2) enforce the same rules at the
database layer, so a validator bug alone cannot cause a write.

Checks run from specific to general (principle 7): a DELETE is reported as a
forbidden write, never as "not a SELECT".

Pure functions only: no database access, no LLM, no settings lookups.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.sql.errors import SqlErrorCode, SqlSafetyError

DIALECT = "sqlite"
DEFAULT_MAX_SQL_CHARS = 5000


def _classes(*names: str) -> tuple[type[exp.Expression], ...]:
    """Expression classes by name. Names missing from this sqlglot version are
    skipped: the class sets below only choose the error code, while the
    allowlist of query roots is what actually admits a statement."""
    found = (getattr(exp, name, None) for name in names)
    return tuple(c for c in found if isinstance(c, type) and issubclass(c, exp.Expression))


# Anywhere in the tree, these mean the statement writes. Reported as FORBIDDEN_WRITE.
WRITE_TYPES = _classes(
    "Insert",
    "Update",
    "Delete",
    "Create",
    "Drop",
    "Alter",
    "AlterTable",
    "TruncateTable",
    "Merge",
    "Into",
)

# Non-query statements. Reported as FORBIDDEN_STATEMENT when at the root.
STATEMENT_TYPES = _classes(
    "Command",
    "Pragma",
    "Attach",
    "Detach",
    "Analyze",
    "Describe",
    "Use",
    "Set",
    "Transaction",
    "Commit",
    "Rollback",
)

# The only statement roots that are accepted.
QUERY_ROOTS = _classes("Select", "Union", "Intersect", "Except")

# First keywords that mean a write, whether or not sqlglot models the statement.
WRITE_COMMANDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "REPLACE",
        "UPSERT",
        "DROP",
        "ALTER",
        "CREATE",
        "TRUNCATE",
        "MERGE",
    }
)

# Statements identified by their first token. sqlglot models some of these as
# exp.Command and fails to parse others (bare VACUUM, EXPLAIN), so classifying
# them before parsing keeps their error code stable across sqlglot versions.
# Tokens exclude comments, so "/* x */ DROP ..." is still classified as DROP.
NON_QUERY_KEYWORDS = frozenset(
    {
        "PRAGMA",
        "ATTACH",
        "DETACH",
        "VACUUM",
        "EXPLAIN",
        "ANALYZE",
        "REINDEX",
        "BEGIN",
        "COMMIT",
        "END",
        "ROLLBACK",
        "SAVEPOINT",
        "RELEASE",
    }
)

# Functions that touch the filesystem, load code, or expose engine internals.
FORBIDDEN_FUNCTIONS = frozenset(
    {
        "load_extension",
        "readfile",
        "writefile",
        "edit",
        "fts3_tokenizer",
        "zipfile",
        "sqlar_compress",
        "sqlar_uncompress",
    }
)
FORBIDDEN_FUNCTION_PREFIXES = ("pragma_",)


@dataclass(frozen=True)
class ValidatedQuery:
    """A query that passed validation.

    sql:         the SQL to execute, regenerated from the checked AST (comments
                 removed, outermost LIMIT enforced).
    tables:      the real tables it reads, lower-cased.
    query_limit: the query's own LIMIT when it was below the row cap and kept
                 (for example "top 5"), else None. When the result has exactly
                 this many rows, more rows may exist; answers must not imply
                 otherwise.
    """

    sql: str
    tables: frozenset[str]
    query_limit: int | None


def validate_sql(
    sql: str | None,
    *,
    allowed_tables: Collection[str],
    max_rows: int,
    max_chars: int = DEFAULT_MAX_SQL_CHARS,
) -> ValidatedQuery:
    """Validate one generated query. Raises SqlSafetyError on any rejection."""
    if max_rows < 1:
        raise ValueError("max_rows must be at least 1")

    if sql is None or not sql.strip():
        raise SqlSafetyError(SqlErrorCode.EMPTY_QUERY, "No SQL was generated.")
    if len(sql) > max_chars:
        raise SqlSafetyError(
            SqlErrorCode.TOO_LONG,
            f"The query is {len(sql)} characters long; the limit is {max_chars}.",
        )

    try:
        tokens = sqlglot.tokenize(sql, read=DIALECT)
        _check_first_keyword(tokens[0].text.upper() if tokens else "")
        parsed = sqlglot.parse(sql, read=DIALECT)
    except (SqlglotError, RecursionError) as exc:
        raise SqlSafetyError(
            SqlErrorCode.PARSE_ERROR, f"The SQL could not be parsed: {_parse_detail(exc)}"
        ) from None

    # A trailing semicolon parses to one statement; ";;" or a second statement
    # adds entries (empty ones as None), which counts as stacking.
    statements = [statement for statement in parsed if statement is not None]
    if not statements:
        raise SqlSafetyError(SqlErrorCode.EMPTY_QUERY, "No SQL was generated.")
    if len(parsed) > 1:
        raise SqlSafetyError(
            SqlErrorCode.MULTIPLE_STATEMENTS,
            "Multiple SQL statements are not allowed. Return a single SELECT query.",
        )

    root = statements[0]
    # sqlglot 30 annotates parse() results as its base class Expr. Every
    # statement it builds is an Expression (which declares `args`), so this
    # narrows the type for mypy and rejects anything else instead of assuming.
    if not isinstance(root, exp.Expression):
        raise SqlSafetyError(SqlErrorCode.NOT_A_SELECT, "The SQL did not parse to a query.")
    _check_statement_type(root)
    _check_functions(root)
    tables = _check_tables(root, allowed_tables)
    query_limit = _enforce_row_cap(root, max_rows)

    sql_out = root.sql(dialect=DIALECT, comments=False)
    # The regenerated SQL must itself parse. sqlglot accepts some incomplete
    # input (a bare "SELECT" parses to a Select with no columns) and would
    # regenerate it as invalid SQL. Found by the totality property test.
    try:
        sqlglot.parse_one(sql_out, read=DIALECT)
    except (SqlglotError, RecursionError):
        raise SqlSafetyError(
            SqlErrorCode.PARSE_ERROR,
            "The SQL is incomplete or malformed. Return one complete SELECT query.",
        ) from None

    return ValidatedQuery(sql=sql_out, tables=tables, query_limit=query_limit)


def _parse_detail(exc: BaseException) -> str:
    """One clean line for the repair prompt. sqlglot's str() includes ANSI codes."""
    if isinstance(exc, RecursionError):
        return "the query is nested too deeply"
    errors = getattr(exc, "errors", None)
    if errors:
        first = errors[0]
        return (
            f"{first.get('description', 'syntax error')} "
            f"(line {first.get('line')}, column {first.get('col')})"
        )
    lines = str(exc).splitlines()
    return lines[0] if lines else "syntax error"


def _check_first_keyword(keyword: str) -> None:
    if keyword in WRITE_COMMANDS:
        raise SqlSafetyError(
            SqlErrorCode.FORBIDDEN_WRITE,
            f"{keyword} is not allowed. This database is read-only; write a single SELECT query.",
        )
    if keyword in NON_QUERY_KEYWORDS:
        raise SqlSafetyError(
            SqlErrorCode.FORBIDDEN_STATEMENT,
            f"{keyword} statements are not allowed. Write a single SELECT query.",
        )


def _check_statement_type(root: exp.Expression) -> None:
    write = next(root.find_all(*WRITE_TYPES), None) if WRITE_TYPES else None
    if write is not None:
        raise SqlSafetyError(
            SqlErrorCode.FORBIDDEN_WRITE,
            f"{write.key.upper()} is not allowed. This database is read-only; "
            "write a single SELECT query.",
        )

    if isinstance(root, QUERY_ROOTS):
        return

    if isinstance(root, STATEMENT_TYPES):
        raise SqlSafetyError(
            SqlErrorCode.FORBIDDEN_STATEMENT,
            f"{root.key.upper()} statements are not allowed. Write a single SELECT query.",
        )

    raise SqlSafetyError(
        SqlErrorCode.NOT_A_SELECT,
        f"Only SELECT queries are allowed, but this is a {root.key.upper()} statement.",
    )


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        return func.name
    return func.sql_name()


def _check_functions(root: exp.Expression) -> None:
    for func in root.find_all(exp.Func):
        name = _function_name(func).lower()
        if name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
            raise SqlSafetyError(
                SqlErrorCode.FORBIDDEN_FUNCTION,
                f"The function {name}() is not allowed.",
            )


def _check_tables(root: exp.Expression, allowed_tables: Collection[str]) -> frozenset[str]:
    allowed = {name.lower() for name in allowed_tables}
    available = ", ".join(sorted(allowed))
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    used: set[str] = set()

    for table in root.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SqlSafetyError(
                SqlErrorCode.FORBIDDEN_FUNCTION,
                "Table-valued functions are not allowed in FROM. Query the tables directly.",
            )

        name = table.name.lower()
        schema = table.db.lower()
        if table.catalog or schema not in ("", "main"):
            raise SqlSafetyError(
                SqlErrorCode.UNKNOWN_TABLE,
                f"'{table.sql(dialect=DIALECT)}' refers to another database. "
                f"Use only these tables: {available}.",
            )
        if not schema and name in cte_names:
            continue
        if name not in allowed:
            raise SqlSafetyError(
                SqlErrorCode.UNKNOWN_TABLE,
                f"The table '{table.name}' does not exist. Available tables: {available}.",
            )
        used.add(name)

    return frozenset(used)


def _enforce_row_cap(root: exp.Expression, max_rows: int) -> int | None:
    """Make the outermost LIMIT at most max_rows + 1.

    Fetching one row beyond the cap is how the executor knows the result was
    truncated. A query LIMIT at or above the cap would otherwise hide that: a
    model-written LIMIT 200 with a cap of 200 looks complete when it is not.
    Returns the query's own LIMIT when it is below the cap and kept, else None.
    """
    cap_plus_one = max_rows + 1
    limit = root.args.get("limit")

    if limit is None:
        root.set("limit", exp.Limit(expression=exp.Literal.number(cap_plus_one)))
        return None

    value = limit.args.get("expression")
    if not (isinstance(value, exp.Literal) and value.is_int) or int(value.name) < 0:
        raise SqlSafetyError(
            SqlErrorCode.INVALID_LIMIT,
            "LIMIT must be a non-negative whole number, for example LIMIT 10.",
        )

    requested = int(value.name)
    if requested >= max_rows:
        limit.set("expression", exp.Literal.number(cap_plus_one))
        return None
    return requested
