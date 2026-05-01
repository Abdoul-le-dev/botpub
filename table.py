"""
migrate_trading_tables.py — Migration des tables trading.

Stratégie :
  1. Pour chaque table, compare les colonnes existantes vs attendues.
  2. Si des colonnes manquent → backup des données, DROP, CREATE, réinsertion.
  3. Si la table n'existe pas → CREATE directement.
  4. Idempotent — sans risque si lancé plusieurs fois.

Usage :
    from migrate_trading_tables import migrate_trading_tables
    migrate_trading_tables()

    # Ou depuis le terminal :
    python migrate_trading_tables.py
"""

import sqlite3
import json
from datetime import datetime
from typing import Any

DB_PATH = "preinscriptions.db"


# ── Schéma attendu ─────────────────────────────────────────────────────────────
# Chaque table = (DDL complet, [colonnes_attendues])
# Les colonnes attendues servent à détecter les écarts sans parser le DDL.

SCHEMA: dict[str, dict] = {

    "signals": {
        "ddl": """
            CREATE TABLE IF NOT EXISTS signals (
                id               INTEGER  PRIMARY KEY AUTOINCREMENT,
                pair             TEXT     NOT NULL,
                direction        TEXT     NOT NULL CHECK(direction IN ('long','short')),
                timeframe        TEXT     DEFAULT 'H4',
                entry_price      REAL     NOT NULL,
                tp1              REAL,
                tp2              REAL,
                sl               REAL,
                note             TEXT,
                screenshot_url   TEXT,
                category         TEXT     DEFAULT 'clients_actifs',
                status           TEXT     DEFAULT 'open'
                                          CHECK(status IN ('open','closed','cancelled')),
                close_price      REAL,
                close_result     TEXT     CHECK(close_result IN ('tp','sl','partial','cancelled') OR close_result IS NULL),
                close_screenshot TEXT,
                result_pips      REAL,
                result_percent   REAL,
                published_at     TEXT     DEFAULT (datetime('now')),
                closed_at        TEXT,
                lot_suggested    REAL,
                broadcast_id     INTEGER
            )
        """,
        "columns": [
            "id", "pair", "direction", "timeframe", "entry_price",
            "tp1", "tp2", "sl", "note", "screenshot_url", "category",
            "status", "close_price", "close_result", "close_screenshot",
            "result_pips", "result_percent", "published_at", "closed_at",
            "lot_suggested", "broadcast_id",
        ],
    },

    "signal_participations": {
        "ddl": """
            CREATE TABLE IF NOT EXISTS signal_participations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id   INTEGER NOT NULL REFERENCES signals(id),
                user_id     INTEGER NOT NULL,
                response    TEXT    NOT NULL CHECK(response IN ('in','out')),
                responded_at TEXT   DEFAULT (datetime('now')),
                UNIQUE(signal_id, user_id)
            )
        """,
        "columns": ["id", "signal_id", "user_id", "response", "responded_at"],
    },

    "trade_journal": {
        "ddl": """
            CREATE TABLE IF NOT EXISTS trade_journal (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id        INTEGER NOT NULL REFERENCES signals(id),
                user_id          INTEGER NOT NULL,
                participated     INTEGER DEFAULT 1,
                entry_price      REAL,
                exit_price       REAL,
                result_pips      REAL,
                result_percent   REAL,
                gain_usd         REAL,
                lot_used         REAL,
                behavior         TEXT    CHECK(behavior IN ('disciplined','early_exit','sl_skip','passive') OR behavior IS NULL),
                screenshot_url   TEXT,
                capital_before   REAL,
                capital_after    REAL,
                submitted_at     TEXT    DEFAULT (datetime('now')),
                status           TEXT    DEFAULT 'closed'
                                         CHECK(status IN ('open','closed')),
                UNIQUE(signal_id, user_id)
            )
        """,
        "columns": [
            "id", "signal_id", "user_id", "participated", "entry_price",
            "exit_price", "result_pips", "result_percent", "gain_usd",
            "lot_used", "behavior", "screenshot_url", "capital_before",
            "capital_after", "submitted_at", "status",
        ],
    },

    "followup_comments": {
        "ddl": """
            CREATE TABLE IF NOT EXISTS followup_comments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id    INTEGER NOT NULL REFERENCES signals(id),
                type         TEXT    NOT NULL
                                     CHECK(type IN ('update','invalidation','secure','encourage')),
                message      TEXT    NOT NULL,
                screenshot_url TEXT,
                broadcast_id INTEGER,
                sent_at      TEXT    DEFAULT (datetime('now'))
            )
        """,
        "columns": [
            "id", "signal_id", "type", "message",
            "screenshot_url", "broadcast_id", "sent_at",
        ],
    },

    "trading_pairs": {
        "ddl": """
            CREATE TABLE IF NOT EXISTS trading_pairs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol         TEXT    NOT NULL UNIQUE,
                category       TEXT    DEFAULT 'forex'
                                       CHECK(category IN ('forex','crypto','indices','commodities')),
                pip_value      REAL    NOT NULL DEFAULT 10.0,
                decimals       INTEGER DEFAULT 5,
                binance_symbol TEXT,
                is_active      INTEGER DEFAULT 1,
                note           TEXT,
                created_at     TEXT    DEFAULT (datetime('now')),
                updated_at     TEXT    DEFAULT (datetime('now'))
            )
        """,
        "columns": [
            "id", "symbol", "category", "pip_value", "decimals",
            "binance_symbol", "is_active", "note", "created_at", "updated_at",
        ],
    },

    "member_capital": {
        "ddl": """
            CREATE TABLE IF NOT EXISTS member_capital (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                capital      REAL    NOT NULL,
                type         TEXT    DEFAULT 'gains'
                                     CHECK(type IN ('gains','withdrawal','loss','initial')),
                declared_at  TEXT    DEFAULT (datetime('now')),
                source       TEXT    DEFAULT 'form'
            )
        """,
        "columns": ["id", "user_id", "capital", "type", "declared_at", "source"],
    },

    "ai_bilans": {
        "ddl": """
            CREATE TABLE IF NOT EXISTS ai_bilans (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                week_label   TEXT    NOT NULL,
                week_start   TEXT    NOT NULL,
                week_end     TEXT    NOT NULL,
                target       TEXT    DEFAULT 'journalised',
                total_sent   INTEGER DEFAULT 0,
                open_rate    REAL,
                broadcast_id INTEGER,
                generated_at TEXT    DEFAULT (datetime('now'))
            )
        """,
        "columns": [
            "id", "week_label", "week_start", "week_end",
            "target", "total_sent", "open_rate", "broadcast_id", "generated_at",
        ],
    },
}

# Index à (re)créer après migration
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_signal_status   ON signals(status)",
    "CREATE INDEX IF NOT EXISTS idx_signal_pub      ON signals(published_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_tj_user         ON trade_journal(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_tj_signal       ON trade_journal(signal_id)",
    "CREATE INDEX IF NOT EXISTS idx_sp_signal       ON signal_participations(signal_id)",
    "CREATE INDEX IF NOT EXISTS idx_sp_user         ON signal_participations(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_capital_user    ON member_capital(user_id, declared_at DESC)",
]

DEFAULT_PAIRS = [
    ("EUR/USD", "forex",       10.0, 5, "EURUSDT"),
    ("GBP/USD", "forex",       10.0, 5, "GBPUSDT"),
    ("XAU/USD", "commodities",  1.0, 2, "XAUUSDT"),
    ("BTC/USD", "crypto",       1.0, 1, "BTCUSDT"),
    ("GBP/JPY", "forex",        8.2, 3, "GBPJPY"),
    ("NAS100",  "indices",      1.0, 1, "NASUSDT"),
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")   # désactivé pendant la migration
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Retourne l'ensemble des colonnes actuellement présentes dans une table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _backup_data(conn: sqlite3.Connection, table: str, columns: list[str]) -> list[dict]:
    """
    Sélectionne uniquement les colonnes qui existent dans la table actuelle
    (intersection entre colonnes attendues et colonnes présentes).
    Retourne une liste de dicts.
    """
    existing = _existing_columns(conn, table)
    safe_cols = [c for c in columns if c in existing]
    if not safe_cols:
        return []
    cols_sql = ", ".join(safe_cols)
    rows = conn.execute(f"SELECT {cols_sql} FROM {table}").fetchall()
    return [dict(r) for r in rows]


def _restore_data(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """
    Réinsère les lignes sauvegardées.
    Ignore les conflits (UNIQUE, PK) pour éviter les doublons.
    Retourne le nombre de lignes réinsérées.
    """
    if not rows:
        return 0
    inserted = 0
    for row in rows:
        cols   = list(row.keys())
        values = list(row.values())
        placeholders = ", ".join(["?"] * len(cols))
        cols_sql = ", ".join(cols)
        try:
            conn.execute(
                f"INSERT OR IGNORE INTO {table} ({cols_sql}) VALUES ({placeholders})",
                values,
            )
            inserted += 1
        except Exception as e:
            print(f"    ⚠  Ligne ignorée ({e}): {row}")
    return inserted


# ── Migration principale ───────────────────────────────────────────────────────

def migrate_trading_tables(db_path: str = DB_PATH) -> dict[str, Any]:
    """
    Vérifie et corrige chaque table du journal de trading.

    Pour chaque table :
      - Si inexistante → CREATE
      - Si colonnes manquantes → backup, DROP, CREATE, réinsertion

    Retourne un rapport : {
        "tables": {
            "signals": {"action": "migrated", "backed_up": 12, "restored": 12, "missing": ["broadcast_id"]},
            "trade_journal": {"action": "ok"},
            ...
        },
        "duration_ms": int,
        "timestamp": str,
    }
    """
    global DB_PATH
    DB_PATH = db_path

    t0      = datetime.now()
    report  = {}

    conn = _get_conn()
    try:
        for table, spec in SCHEMA.items():
            expected_cols = set(spec["columns"])

            # ── Table inexistante ──────────────────────────────────────────
            if not _table_exists(conn, table):
                print(f"[{table}] Table absente → CREATE")
                conn.execute(spec["ddl"])
                conn.commit()
                report[table] = {"action": "created"}
                continue

            # ── Colonnes présentes ─────────────────────────────────────────
            existing_cols = _existing_columns(conn, table)
            missing = sorted(expected_cols - existing_cols)

            if not missing:
                print(f"[{table}] OK — aucune colonne manquante")
                report[table] = {"action": "ok"}
                continue

            # ── Migration nécessaire ───────────────────────────────────────
            print(f"[{table}] Colonnes manquantes : {missing}")
            print(f"[{table}] Backup des données existantes…")

            backup = _backup_data(conn, table, spec["columns"])
            print(f"[{table}] {len(backup)} lignes sauvegardées")

            # Supprimer les index associés pour éviter les erreurs DROP
            index_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (table,),
            ).fetchall()
            for idx in index_rows:
                conn.execute(f"DROP INDEX IF EXISTS {idx['name']}")
                print(f"[{table}] Index '{idx['name']}' supprimé")

            conn.execute(f"DROP TABLE {table}")
            print(f"[{table}] Table supprimée")

            conn.execute(spec["ddl"])
            print(f"[{table}] Table recrée avec toutes les colonnes")

            restored = _restore_data(conn, table, backup)
            conn.commit()
            print(f"[{table}] {restored}/{len(backup)} lignes restaurées")

            report[table] = {
                "action":    "migrated",
                "missing":   missing,
                "backed_up": len(backup),
                "restored":  restored,
            }

        # ── Index & données par défaut ─────────────────────────────────────
        print("\nRecréation des index…")
        for idx_sql in INDEXES:
            conn.execute(idx_sql)

        print("Insertion des paires par défaut (si absentes)…")
        conn.executemany("""
            INSERT OR IGNORE INTO trading_pairs
                (symbol, category, pip_value, decimals, binance_symbol)
            VALUES (?, ?, ?, ?, ?)
        """, DEFAULT_PAIRS)

        conn.commit()
        print("✅  Migration terminée.")

    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()

    duration_ms = int((datetime.now() - t0).total_seconds() * 1000)
    return {
        "tables":      report,
        "duration_ms": duration_ms,
        "timestamp":   datetime.now().isoformat(),
    }


# ── Rapport lisible ────────────────────────────────────────────────────────────

def print_report(report: dict) -> None:
    print("\n" + "═" * 52)
    print("  RAPPORT DE MIGRATION")
    print("═" * 52)
    for table, info in report["tables"].items():
        action = info["action"]
        if action == "ok":
            status = "✔  OK"
        elif action == "created":
            status = "🆕 Créée"
        else:
            missing = ", ".join(info.get("missing", []))
            status = (
                f"🔧 Migrée  |  manquait : {missing}  |  "
                f"{info['backed_up']} lignes sauvegardées, "
                f"{info['restored']} restaurées"
            )
        print(f"  {table:<28}  {status}")
    print("─" * 52)
    print(f"  Durée : {report['duration_ms']} ms")
    print("═" * 52 + "\n")


# ── Entrée principale ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    report = migrate_trading_tables()
    print_report(report)