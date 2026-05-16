"""
migrate_subscription_info.py
Recrée subscription_info sans contrainte NOT NULL sur user_id.
Lance : python3 migrate_subscription_info.py
"""
import sqlite3

DB = "preinscriptions.db"

def run():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("PRAGMA journal_mode=WAL")

    c.executescript("""
        -- 1. Sauvegarde
        ALTER TABLE subscription_info RENAME TO subscription_info_bak;

        -- 2. Nouvelle table sans NOT NULL sur user_id
        CREATE TABLE subscription_info (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER,
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

        -- 3. Copie des données
        INSERT INTO subscription_info
            SELECT * FROM subscription_info_bak;

        -- 4. Supprime la sauvegarde
        DROP TABLE subscription_info_bak;
    """)

    c.commit()
    c.close()
    print("OK — subscription_info recréée sans contrainte NOT NULL sur user_id.")

if __name__ == "__main__":
    run()