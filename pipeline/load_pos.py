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
            invoice_number TEXT,
            invoice_type TEXT,
            order_date TEXT,
            order_time TEXT,
            store_id TEXT,
            store_name TEXT,
            city TEXT,
            customer_name TEXT,
            product_name TEXT,
            brand_name TEXT,
            dep_name TEXT,
            sub_category TEXT,
            brand_type TEXT,
            qty INTEGER,
            GMV REAL,
            NMV REAL,
            coupon_amount REAL,
            item_promotion REAL,
            total_amount REAL,
            salesperson_name TEXT,
            tax REAL,
            taxable_amt REAL,
            tax_amt REAL
        )
    """)

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cur.execute("""
                INSERT INTO pos_transactions 
                (order_id, invoice_number, invoice_type, order_date, order_time,
                 store_id, store_name, city, customer_name, product_name,
                 brand_name, dep_name, sub_category, brand_type, qty,
                 GMV, NMV, coupon_amount, item_promotion, total_amount,
                 salesperson_name, tax, taxable_amt, tax_amt)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                row["order_id"], row["invoice_number"], row["invoice_type"],
                row["order_date"], row["order_time"], row["store_id"],
                row["store_name"], row["city"], row["customer_name"],
                row["product_name"], row["brand_name"], row["dep_name"],
                row["sub_category"], row["brand_type"], row["qty"],
                row["GMV"], row["NMV"], row["coupon_amount"],
                row["item_promotion"], row["total_amount"],
                row["salesperson_name"], row["tax"],
                row["taxable_amt"], row["tax_amt"],
            ))

    conn.commit()
    print(f"Loaded {cur.lastrowid} rows from {csv_path}")
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to POS CSV file")
    parser.add_argument("--db", default="data/store_intelligence.db")
    args = parser.parse_args()
    load_pos(args.input, args.db)