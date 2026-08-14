"""Rebuild ecommerce.db from schema.sql and seed.sql.
Run from the project root: python database/build_db.py
Safe to re-run: schema.sql drops existing tables first."""

import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "ecommerce.db")
SCHEMA_PATH = os.path.join(HERE, "schema.sql")
SEED_PATH = os.path.join(HERE, "seed.sql")

EXPECTED = {"customers": 20, "products": 15, "orders": 30, "order_items": 47}

def read_sql(path):
  with open(path, "r", encoding="utf8") as f:
    return f.read()


def main():
  con = sqlite3.connect(DB_PATH)
  try:
      con.execute("PRAGMA foreign_keys = ON")
      con.executescript(read_sql(SCHEMA_PATH))
      con.executescript(read_sql(SEED_PATH))
      con.commit()

      print(f"Built: {DB_PATH}\n")
      ok = True
      for table, expected in EXPECTED.items():
          actual = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
          mark = "OK " if actual == expected else "BAD"
          if actual != expected:
              ok = False
          print(f"  [{mark}] {table:<12} {actual:>3} rows (expected {expected})")

      orphans = con.execute("PRAGMA foreign_key_check").fetchall()
      print(f"\n  Foreign key violations: {len(orphans)}")

      if ok and not orphans:
          print("\nDatabase built successfully.")
      else:
          print("\nWARNING: counts or foreign keys did not match.")
  finally:
      con.close()


if __name__ == "__main__":
    main()
