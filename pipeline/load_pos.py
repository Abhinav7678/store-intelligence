"""
POS data loader: imports POS transaction CSV into SQLite.

New format has 7 columns:
order_id, order_date, order_time, store_id, product_id, brand_name, total_amount

Usage:
    python pipeline/load_pos.py --input data/pos_transactions.csv
"""
import csv
import sqlite3
import argparse


def load_pos(csv_path: str, db_path: str = "data/store_intelligence.db"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS pos_transactions")
    cur.execute("""
        CREATE TABLE pos_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            order_date TEXT,
            order_time TEXT,
            store_id TEXT,
            product_id TEXT,
            brand_name TEXT,
            total_amount REAL
        )
    """)

    count = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        print(f"Detected columns: {reader.fieldnames}")

        for row in reader:
            raw_date = row["order_date"].strip()
            # Normalize DD-MM-YYYY -> YYYY-MM-DD
            if "-" in raw_date and len(raw_date.split("-")[0]) == 2:
                parts = raw_date.split("-")
                raw_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

            try:
                amt = float(row["total_amount"].strip()) if row["total_amount"].strip() else 0.0
            except ValueError:
                amt = 0.0

            cur.execute("""
                INSERT INTO pos_transactions
                (order_id, order_date, order_time, store_id, product_id, brand_name, total_amount)
                VALUES (?,?,?,?,?,?,?)
            """, (
                row["order_id"].strip(),
                raw_date,
                row["order_time"].strip(),
                row["store_id"].strip(),
                row["product_id"].strip(),
                row["brand_name"].strip(),
                amt,
            ))
            count += 1

    conn.commit()
    print(f"Loaded {count} rows from {csv_path}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to POS CSV file")
    parser.add_argument("--db", default="data/store_intelligence.db")
    args = parser.parse_args()
    load_pos(args.input, args.db)