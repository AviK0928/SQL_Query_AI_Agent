# Discoveries

Things learned building this, and the reasoning behind decisions that are not
obvious from the code.

---

## Security

### The prompt is not a security boundary

The most important decision in the project. It would be easy to write "never
generate DELETE" in the prompt and call the system safe. A prompt is a request,
and a model can decline it.

So the database was hardened before the agent existed. By the time any generated
SQL appeared, the database already refused to do anything except read.

| Layer | Enforced by | Can the model talk its way past it? |
|---|---|---|
| Prompt instruction | Model cooperation | **Yes** |
| Validator | `app/validator.py` | No |
| Single-statement execution | Python's `sqlite3` driver | No |
| Read-only connection + authorizer | SQLite engine | No |

### SQLite has no user accounts

The original plan said "use a read-only database user". SQLite has no
`CREATE USER` or `GRANT`. The equivalent is built from three things:

1. Opening the connection with `file:...?mode=ro`
2. Registering `set_authorizer`, a callback SQLite runs while compiling every
   statement, returning `SQLITE_DENY` for anything that is not a read
3. Never calling `executescript()` at runtime

The authorizer matters more than read-only mode alone. **Read-only mode still
permits `ATTACH` and `PRAGMA`** — `ATTACH` could open another file on disk. The
authorizer blocks both. Verified: `DROP`, `DELETE`, `UPDATE`, `INSERT`,
`ATTACH` and `PRAGMA` all return `not authorized`.

### One function must never take an argument

`get_schema()` runs without the authorizer, because the `PRAGMA table_info`
calls it needs would themselves be denied. It builds those calls with an
f-string.

That is safe **only** because the table names come from `sqlite_master`, not
from a caller. `PRAGMA` cannot take bound parameters, so this cannot be fixed
with parameterisation. Adding a `table_name` argument to that function would
create a real SQL injection point. It is commented in the source for that
reason.

### The frontend treats model output as untrusted too

Everything rendered into the page uses `textContent`, never `innerHTML`. If the
model returned `<img src=x onerror=...>`, or a database value contained markup,
`innerHTML` would execute it. Same principle as the SQL validator, different
layer.

---

## LangGraph and the LLM

### Two calls were merged into one

The reference design used one model call to classify whether a question was in
scope and another to generate SQL. Instead the SQL prompt returns either a query
or the token `OUT_OF_SCOPE`.

This halved the fixed cost per question, removed a round trip, and removed a
failure mode where the two calls disagree. Free tier is 30 requests/minute, so
call count is a real constraint.

### Two kinds of refusal needed two different messages

"Delete the most expensive order" was originally answered with *"I can only
answer questions about the e-commerce database."* That is misleading. The
question is entirely about this database — it is just something the system will
not do. Telling the user they are off-topic when they are not is a bad message.

The instinct was to fix this in the validator's error handling. Checking what
actually happened first showed that branch was unreachable: the model returns
`OUT_OF_SCOPE` for these requests before generating any SQL, so nothing ever
reached the validator. A fix there would have been dead code.

The real fix was a second token. The prompt now instructs the model to reply
`READ_ONLY` for anything that would modify data, and `OUT_OF_SCOPE` only for
questions the database cannot answer. The agent checks for `READ_ONLY` first, so
the more specific message wins when both appear.

| Request | Token | Reply |
|---|---|---|
| "delete the most expensive order" | `READ_ONLY` | "I can only read from this database, not change it." |
| "give me a Python function" | `OUT_OF_SCOPE` | "I can only answer questions about the e-commerce database." |

**This changed the wording, not the protection.** It is a prompt rule, so the
model may still occasionally generate `DELETE` instead of the token — in which
case the validator rejects it and the user gets a less friendly message. The
database is equally safe either way. Better error messages are a usability
improvement; they are not a security layer, and it would be a mistake to present
them as one.

The general lesson: check which code path a bug actually takes before fixing it.

### The retry limit is structural

`retry_count` starts at 0. The router sends work to the retry node only when an
error exists *and* the count is 0. The retry node sets it to 1, so a second
failure has no path back. The loop cannot run away because of how the graph is
wired, not because of a counter someone could edit.

### Domain rules must be in the prompt, but cannot be trusted there

Two rules encode knowledge the model cannot infer from column names: use
`order_items.unit_price` rather than `products.price`, and exclude cancelled
orders from totals. Without them the model produces answers that look correct
and are wrong.

**They are followed most of the time, not always.** Observed live:

- "Which 3 customers have spent the most?" — joined `orders`, filtered
  `status != 'cancelled'`, correct totals.
- "Give me the latest expenditures" — queried `order_items` alone. Without the
  join to `orders` the cancelled filter was impossible to apply, so cancelled
  orders were included.

Same prompt, same rule, different outcome. This is the clearest evidence in the
project that prompt instructions are probabilistic.

### A truncated result set produced a confidently wrong answer

Asked for "the latest expenditures", the model replied that the highest was
order 17 at Rs 21,999. The real highest is order 6 at Rs 23,598.

The query returned 30 rows sorted by ID. Only the first 20 were sent to the
model for summarising, so it never saw order 6 and reported the maximum of what
it could see as the overall maximum. The SQL was valid, the rows were correct,
and the sentence was false.

The fix labels partial result sets with the true row count and tells the model
not to describe them as highest or lowest. That is a mitigation, not a solution:
summarising a partial result set can always mislead. The reliable fix is a query
that orders and limits explicitly.

### A valid query can answer the wrong question

Asked for "the highest expenditure", the model produced:

```sql
SELECT SUM(oi.unit_price * oi.quantity) AS total_expenditure
FROM order_items oi
INNER JOIN orders o ON oi.order_id = o.id
WHERE o.status != 'cancelled'
ORDER BY total_expenditure DESC LIMIT 1
```

It returned Rs 1,83,530 and reported that as the highest expenditure. It is not.
With no `GROUP BY`, the query sums every line item in the database — that figure
is total revenue. The `ORDER BY ... LIMIT 1` is meaningless on a single row.

The domain rules were followed correctly: `orders` was joined and cancelled
orders were excluded. The query is still wrong, because "highest" was read as
"total".

**No layer in this system can catch this.** The SQL is valid, reads only, and
returns a real number computed from real rows. The validator checks the
statement is a SELECT. The authorizer checks it only reads. Neither knows
anything about whether the query answers the question that was asked.

This is the failure mode that matters most in natural-language-to-SQL and the
one that cannot be fixed with validation. It is the reason the generated SQL is
displayed in the interface rather than hidden: a user who can read SQL can see
immediately that this answer does not match the question, and a user who cannot
has at least been shown the basis for the number.

### A defence was considered and rejected

Groq offers `llama-prompt-guard-2-86m`, a model trained to detect prompt
injection. It was not used. It would add a second API call to every question
against a 30/minute limit, the `OUT_OF_SCOPE` token already handles scope, and
neither is the layer that actually enforces anything.

### Model names go stale

`langgraph` moved from 0.2 to 1.2.9 during this project, and model names are
retired regularly. The model is read from the `GROQ_MODEL` environment variable
so it can be changed on the deployment platform without a code change.
Availability was verified by querying the provider's model list rather than
trusting documentation.

---

## Database and SQL

### `unit_price` is duplicated on purpose

`order_items` stores the price paid, rather than joining to `products.price`.
Otherwise changing a product's price would silently rewrite historical order
totals. Two rows in the seed data carry a discounted price so the difference is
observable.

### The seed data was designed to make questions interesting

- One customer has zero orders, so "who never ordered?" is a real
  `LEFT JOIN ... IS NULL` question
- Two orders are cancelled, so "total revenue" is ambiguous without a filter
- Nine customers have multiple orders, so `GROUP BY` produces variation

### Money is stored as REAL

Floating point. Sums of many rows can accumulate rounding error. Integer paise
would be correct. Acceptable for 47 demo rows; not acceptable in a real ledger.

### Semicolon detection is textual, not parsed

The validator rejects any semicolon that is not a single trailing one. A
semicolon inside a string literal — `WHERE city = 'Mum;bai'` — would be falsely
rejected. Fixing this properly needs a real SQL parser such as `sqlglot`. No
value in the seed data contains a semicolon, so it cannot occur here.

---

## Testing

### Mutation testing found a redundant check

Four deliberate faults were injected into the validator to see whether the suite
would notice: removing the regex word boundaries, disabling the stacking check,
disabling comment stripping, and disabling the SELECT-prefix check.

Three were caught. **The fourth survived** — revealing that the prefix check was
completely redundant with the forbidden-keyword list across every test case,
since `DROP`, `DELETE` and `VACUUM` were all caught twice. Two cases
(`EXPLAIN`, `ANALYZE`) were added: neither word is on the forbidden list, so
only the prefix check rejects them.

The suite is trusted because it has been shown to fail when the code is wrong,
not merely to pass when it is right.

### Negative tests script the model to comply, not to refuse

The brief requires a test that "Delete all users" is rejected. With a fake model
it would be trivial to script `OUT_OF_SCOPE` and claim a pass — but that tests
the model's cooperation, which this system does not rely on.

The test instead scripts `DELETE FROM customers` on both the first attempt and
the retry: the worst case, where the model fully obeys. It then asserts no rows
were returned and all four tables still exist.

### Two tests assert on what did not happen

- A fake model with zero scripted responses raises if called. After a 422 for an
  empty question, the test asserts it was never called — proving validation ran
  before any API request was spent.
- A model that raises an exception containing a fake credential: the test
  asserts neither the credential nor the word `Traceback` appears in the
  response body.

### A failing test is not automatically a code bug

The first red run came from a test case filed in the wrong bucket.
`"SELECT * FROM customers; "` was listed as something to block, but it is a
single valid statement, and blocking it would cause a needless retry on every
query ending in a semicolon. The test was moved; the validator was left alone.
Changing the code to satisfy that test would have introduced a real defect.

### The whole suite runs offline

78 tests, no API key, no network, ~2 seconds. Only the model is faked —
Pydantic validation, the graph, the validator, the real SQLite file and HTTP
status codes all run for real.

---

## Deployment and process

### Colab is disposable, and that was tested for real

The VM was recycled mid-session, taking the filesystem with it. Recovery was
`git clone` plus `pip install -r requirements.txt`, and the full suite passed
immediately.

This worked because three rules were held throughout: source lived in GitHub
rather than only in the notebook, `ecommerce.db` was committed rather than
generated into an untracked path, and secrets lived in Colab Secrets, which are
tied to the account rather than the VM.

One thing did not survive: `git config user.email` is repository-local state and
a fresh clone does not carry it. The next commit failed until it was set again.

### "Success" printed three times on failures

A shell command run with `!` in Colab does not raise on a non-zero exit, so a
`print("done")` on the next line runs regardless. `get_ipython().system()`
returns `None` in Colab, so checking its return value also fails. And a `git
commit` that fails for a missing identity leaves nothing to push, so the
following `git push` exits 0 — correct exit code, wrong conclusion.

Three different mechanisms, one lesson: a success message printed by the next
statement is not evidence of success. Use `subprocess.run(...)` and check
`returncode`.

### Dependency versions were pinned before deploying

Development used `>=` constraints. Before the first deploy these were replaced
with exact versions taken from the working environment, because Render resolves
dependencies at build time and LangChain ships breaking changes on minor
releases. `pip freeze` was not used directly — that would capture all ~200
packages preinstalled in Colab.

### Credentials never touched `.git/config`

The push token is embedded in the remote URL for a single command rather than
stored with `git remote set-url`, so it is never written to disk. Git error
output can echo the remote URL, so stderr is filtered before printing.

### Counts were verified, not estimated

Expected test counts were wrong twice (45 vs 44, 75 vs 76), both times through
miscounting rather than missing tests. A revenue figure was also stated wrongly
in conversation before being checked against the database. Every count in this
repository was confirmed by running something.

---

## Known limitations

| # | Limitation | When it bites |
|---|---|---|
| 1 | Money stored as `REAL` | Large sums accumulate float error |
| 2 | Semicolon check is textual | A semicolon inside a string literal is falsely rejected |
| 3 | Session memory is in-process | Lost on restart; would not work across instances |
| 4 | Sessions evicted oldest-first | An active session can be dropped before a stale one |
| 5 | Prompt rules are probabilistic | Cancelled-order filter is sometimes not applied |
| 6 | Summaries of truncated results | Can still overstate, despite the row-count notice |
| 7 | Render free tier sleeps | 30-60 second cold start |
| 8 | `EXPLAIN` and `ANALYZE` rejected | Read-only, but they expose engine internals |

## If this were taken further

- Replace the textual SQL checks with `sqlglot` parsing
- Store money as integer paise
- Move sessions to a store with a TTL
- Stream responses so the first token appears sooner
- Add a follow-up query for aggregate questions rather than summarising a
  truncated result set
