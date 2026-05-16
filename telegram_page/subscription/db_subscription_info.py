"""
migrate_subscription_info.py — Recrée subscription_info sans user_id.
"""
import sqlite3

DB = "preinscriptions.db"

def run():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("PRAGMA journal_mode=WAL")

    c.executescript("""
        ALTER TABLE subscription_info RENAME TO subscription_info_bak;

        CREATE TABLE subscription_info (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            plan          TEXT    NOT NULL,
            duration_days INTEGER NOT NULL,
            started_at    TEXT    NOT NULL,
            expires_at    TEXT    NOT NULL,
            status        TEXT    DEFAULT 'pending',
            note          TEXT    DEFAULT NULL,
            order_id      TEXT,
            name          TEXT,
            email         TEXT,
            phone         TEXT,
            country_code  TEXT,
            billing_cycle TEXT,
            amount_usd    REAL,
            currency      TEXT,
            amount_local  REAL,
            aggregator    TEXT,
            paid_at       TEXT,
            created_at    TEXT    DEFAULT (datetime('now')),
            updated_at    TEXT    DEFAULT (datetime('now'))
        );

        INSERT INTO subscription_info
            (id, plan, duration_days, started_at, expires_at, status, note,
             order_id, name, email, phone, country_code, billing_cycle,
             amount_usd, currency, amount_local, aggregator, paid_at,
             created_at, updated_at)
        SELECT
             id, plan, duration_days, started_at, expires_at, status, note,
             order_id, name, email, phone, country_code, billing_cycle,
             amount_usd, currency, amount_local, aggregator, paid_at,
             created_at, updated_at
        FROM subscription_info_bak;

        DROP TABLE subscription_info_bak;
    """)

    c.commit()
    c.close()
    print("OK — user_id supprimé de subscription_info.")

if __name__ == "__main__":
    run()