# Judge calibration: how to label

You score 26 answered questions by hand. The judge scores the same 26, and each
criterion is trusted only if it agrees with you (D44). Your labels are the
ground truth, so label what is actually right, not what you expect the judge to
say. Don't open `judgments.jsonl` before you have finished.

## The sheet

`calibration_v1_labels.csv`, one row per item. Read these columns:

| Column | What it is |
|---|---|
| `earlier_questions` | Earlier turns of the conversation (T13 follow-ups only) |
| `question` | The question being answered |
| `sql` | The SQL that ran (after the validator's LIMIT rewrite) |
| `result` | The rows it returned: header, up to 50 rows, then `(N rows; M shown)` |
| `flags` | `truncated` (more rows matched than the server returns), `limit_reached` (the query's own LIMIT was hit, so more rows may exist), or `none` |
| `answer` | What the user was told. This is what you grade. |
| `reference_sql`, `reference_rows` | A correct query for the question and its row count |
| `matches_reference` | Whether the SQL's result equals the reference's (`yes`/`no`) |

Fill in these seven columns, and nothing else:

| Column | Values |
|---|---|
| `faithfulness` | 1, 2, 3, 4 or 5 |
| `relevance` | 1, 2, 3, 4 or 5 |
| `completeness` | 1, 2, 3, 4 or 5 |
| `honesty` | 1, 2, 3, 4 or 5 |
| `sql_intent` | 1, 2, 3, 4 or 5 |
| `clarity` | 1, 2, 3, 4 or 5 |
| `notes` | Optional free text; worth a few words whenever you give 3 or less |

Whole numbers only. Every item needs all six scores.

## The scale

The same scale for every criterion, and the same one the judge uses:

| Score | Meaning |
|---|---|
| 5 | Fully meets the criterion |
| 4 | One minor flaw that would not mislead the user |
| 3 | A flaw that could mislead the user |
| 2 | Mostly fails |
| 1 | Fails completely |

The line between 4 and 3 matters most: 4-5 counts as a pass and 1-3 as a fail
when your labels are compared with the judge's. Ask: "Would a user who trusted
this answer be misled?" If yes, 3 or lower.

## The criteria

Score each criterion on its own. A wrong query does not automatically make the
answer unclear, and a clear answer is not automatically faithful.

**faithfulness**: is every number, name and claim supported by the `result`
rows, or by simple arithmetic on them? Compare against the rows, not against
what you think the database contains. A claim about rows that are not shown
(for example "the highest overall" when the result is a sample) is unsupported.
Rounding the way a person would (Rs 33,895) is fine.

**relevance**: does the answer address the question asked, rather than a
different or narrower one?

**completeness**: is every part of the question answered? "Which products and
how many units?" needs both.

**honesty**: does the answer tell the user what they need to know about the
result? That means: saying clearly when nothing matched; saying the rows are
partial when `flags` shows `truncated` or `limit_reached`; and stating any
assumption it made (for example "by total spend" for "most valuable"). Score 5
when there is nothing to disclose and the answer does not overstate
completeness. A disclosure that is false also costs points: saying "only the
first rows are shown" when the answer is complete misleads the user.

**sql_intent**: does the `sql` answer the question as asked? Judge the SQL, not
the answer. Use `reference_sql` and `matches_reference` as evidence, but think:
a query can differ from the reference and still be right, or match on this data
by luck. Extra columns alone are a minor flaw (4 at worst). The project's rules
for what "right" means:

- Revenue and sales totals use `unit_price * quantity` and exclude cancelled
  orders, unless the question says otherwise (P1).
- When a ranking ends in a tie, every tied row belongs in the result (P2). A
  `LIMIT 1` that drops a tied row is wrong.
- "Above-average spend" compares against customers who spent something (P3).
- Rankings of customers include only customers who spent something (P4).

**clarity**: is the answer concise, direct and easy to read? Long lists that
should have been summarised, or answers that describe the SQL instead of
answering, lose points here.

## Steps

1. Open Google Sheets, then **File › Import › Upload** and choose
   `calibration_v1_labels.csv`. Pick **Replace spreadsheet**.
2. Label all 26 rows. Budget about 40 minutes. Work item by item, all six
   criteria at once.
3. Don't reorder, rename or delete columns or rows. Adding notes is fine.
4. **File › Download › Comma-separated values (.csv)**.
5. Run Cell P7-12 and upload the downloaded file. It checks every row and lists
   any problem; fix those in the sheet, download again and re-run the cell.
