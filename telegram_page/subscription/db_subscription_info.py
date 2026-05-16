# db_subscription_info.py
import sqlite3

DB_PATH = 'preinscriptions.db'

def init_subscription_info():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS subscription_info (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            plan          TEXT    NOT NULL,
            duration_days INTEGER NOT NULL,
            started_at    TEXT    NOT NULL,
            expires_at    TEXT    NOT NULL,
            status        TEXT    DEFAULT 'active',
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
        )
    ''')

    # Vérifie et ajoute les colonnes manquantes
    existing = {row[1] for row in conn.execute('PRAGMA table_info(subscription_info)')}
    columns = {
        'order_id':      'TEXT',
        'name':          'TEXT',
        'email':         'TEXT',
        'phone':         'TEXT',
        'country_code':  'TEXT',
        'billing_cycle': 'TEXT',
        'amount_usd':    'REAL',
        'currency':      'TEXT',
        'amount_local':  'REAL',
        'aggregator':    'TEXT',
        'paid_at':       'TEXT',
    }
    for col, col_type in columns.items():
        if col not in existing:
            conn.execute(f'ALTER TABLE subscription_info ADD COLUMN {col} {col_type}')
            print(f'Colonne ajoutée : {col}')

    conn.commit()
    conn.close()
    print('Table subscription_info prête.')

if __name__ == '__main__':
    init_subscription_info()