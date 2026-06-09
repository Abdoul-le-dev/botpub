#!/usr/bin/env python3
"""
migrate_data.py
---------------
Migre toutes les données de preincristion.db vers MySQL.
Exécute après migrate_to_mysql.py (schéma déjà créé).

Usage :
    pip install mysql-connector-python
    python migrate_data.py
"""

import sqlite3
import mysql.connector
from mysql.connector import Error
import sys

# ─────────────────────────────────────────────
#  CONFIG  –  adapte ces valeurs
# ─────────────────────────────────────────────
SQLITE_FILE = "preincristion.db"

MYSQL_CONFIG = {
    "host":     "localhost",
    "port":     3306,
    "user":     "fiacrefdksignal",   # ← ton utilisateur MySQL
    "password": "Fiacre2026@#",   # ← ton mot de passe MySQL
    "database": "fdkvip_db",
}

BATCH_SIZE = 500   # lignes insérées par batch
# ─────────────────────────────────────────────

# Ordre d'insertion respectant les clés étrangères
TABLE_ORDER = [
    "users",
    "mail_valide",
    "categories_meta",
    "category_rules",
    "subscription_plans",
    "growth_subscriptions",
    "promo_codes",
    "auto_promo_config",
    "subscriptions",
    "messages",
    "conversations",
    "categories",
    "categories_backup",
    "usersdefault",
    "videos",
    "categorie_exercice",
    "exercice",
    "resultat_student_question",
    "resultat_student_day",
    "args",
    "participants",
    "participants_2nd",
    "exam",
    "exam_user",
    "broadcast_history",
    "trade_comments",
    "signals",
    "trade_journal",
    "forms",
    "form_sessions",
    "form_submissions",
    "form_responses",
    "signal_participations",
    "followup_comments",
    "trading_pairs",
    "member_capital",
    "ai_bilans",
    "invite_links",
    "invite_link_stats",
    "ia_trigger_config",
    "automation_jobs",
    "automation_logs",
    "ia_prompts",
    "ia_functions",
    "subscription_info",
    "gold_seasons",
    "gold_tp_rules",
    "gold_trade_sessions",
    "gold_user_sessions",
    "gold_member_entries",
    "gold_flow_events",
    "simulation_accounts",
    "simulation_trades",
]


def get_sqlite_tables(sqlite_cur) -> list:
    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    return [r[0] for r in sqlite_cur.fetchall()]


def migrate_table(sqlite_cur, mysql_cur, mysql_conn, table: str) -> tuple:
    # Colonnes de la table SQLite
    try:
        sqlite_cur.execute(f"SELECT * FROM `{table}` LIMIT 0")
    except Exception as e:
        return 0, 0, f"Lecture colonnes : {e}"

    columns = [d[0] for d in sqlite_cur.description]
    placeholders = ", ".join(["%s"] * len(columns))
    cols_escaped  = ", ".join([f"`{c}`" for c in columns])
    insert_sql    = (
        f"INSERT IGNORE INTO `{table}` ({cols_escaped}) "
        f"VALUES ({placeholders})"
    )

    # Lire toutes les lignes SQLite
    try:
        sqlite_cur.execute(f"SELECT * FROM `{table}`")
    except Exception as e:
        return 0, 0, f"SELECT * : {e}"

    total   = 0
    errors  = 0
    batch   = []

    def flush(batch):
        nonlocal total, errors
        try:
            mysql_cur.executemany(insert_sql, batch)
            mysql_conn.commit()
            total += len(batch)
        except Error as e:
            # Retry ligne par ligne pour isoler les erreurs
            mysql_conn.rollback()
            for row in batch:
                try:
                    mysql_cur.execute(insert_sql, row)
                    mysql_conn.commit()
                    total += 1
                except Error as e2:
                    errors += 1

    while True:
        rows = sqlite_cur.fetchmany(BATCH_SIZE)
        if not rows:
            break
        # Convertir les valeurs : bool Python → int, None reste None
        cleaned = []
        for row in rows:
            cleaned.append(tuple(
                int(v) if isinstance(v, bool) else v
                for v in row
            ))
        batch = cleaned
        flush(batch)

    return total, errors, None


def run():
    print("=" * 60)
    print("  Migration des données SQLite → MySQL")
    print("=" * 60)

    # Connexion SQLite
    try:
        sq_conn = sqlite3.connect(SQLITE_FILE)
        sq_conn.row_factory = sqlite3.Row
        sq_cur  = sq_conn.cursor()
        print(f"[OK] SQLite ouvert : {SQLITE_FILE}")
    except Exception as e:
        print(f"[ERREUR] SQLite : {e}")
        sys.exit(1)

    # Connexion MySQL
    try:
        my_conn = mysql.connector.connect(**MYSQL_CONFIG)
        my_cur  = my_conn.cursor()
        print(f"[OK] MySQL connecté : {MYSQL_CONFIG['database']}\n")
    except Error as e:
        print(f"[ERREUR] MySQL : {e}")
        sys.exit(1)

    # Désactiver les FK le temps de l'import
    my_cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    my_cur.execute("SET SESSION innodb_lock_wait_timeout = 300")

    # Tables disponibles dans SQLite
    available = get_sqlite_tables(sq_cur)

    # Construire la liste finale (ordre défini + tables non listées en fin)
    ordered = [t for t in TABLE_ORDER if t in available]
    extras  = [t for t in available if t not in TABLE_ORDER]
    all_tables = ordered + extras

    grand_total  = 0
    grand_errors = 0
    skipped      = []

    for table in all_tables:
        if table not in available:
            skipped.append(table)
            continue

        # Compter les lignes
        sq_cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        count = sq_cur.fetchone()[0]

        if count == 0:
            print(f"  [ -- ] {table:40} (vide)")
            continue

        print(f"  [ >> ] {table:40} {count:>7} lignes ...", end="", flush=True)

        inserted, errs, err_msg = migrate_table(sq_cur, my_cur, my_conn, table)

        if err_msg:
            print(f"\n         ✗ ERREUR : {err_msg}")
            grand_errors += 1
        else:
            status = "✓" if errs == 0 else f"⚠ ({errs} erreurs)"
            print(f"  {status}  {inserted} insérées")
            grand_total  += inserted
            grand_errors += errs

    # Réactiver les FK
    my_cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    my_conn.commit()

    sq_conn.close()
    my_cur.close()
    my_conn.close()

    print("\n" + "=" * 60)
    print(f"  Total inséré : {grand_total:,} lignes  |  Erreurs : {grand_errors}")
    print("=" * 60)

    if grand_errors == 0:
        print("\n✅ Migration des données terminée avec succès !")
    else:
        print(f"\n⚠️  {grand_errors} erreur(s) — vérifie les lignes ci-dessus.")


if __name__ == "__main__":
    run()