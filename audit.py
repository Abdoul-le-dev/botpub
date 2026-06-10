#!/usr/bin/env python3
"""
audit_migration.py
------------------
Compare COUNT(*) de chaque table entre SQLite et MySQL.
Ne touche à AUCUNE donnée — lecture seule.

Usage :
    pip install mysql-connector-python
    python audit_migration.py
"""

import sqlite3
import mysql.connector
from mysql.connector import Error
import sys
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
SQLITE_FILE = "preinscriptions.db"

MYSQL_CONFIG = {
    "host":     "localhost",
    "port":     3306,
    "user":     "fiacrefdksignal",
    "password": "Fiacre2026@#",
    "database": "fdkvip_db",
}
# ─────────────────────────────────────────────

TABLE_ORDER = [
    "users", "mail_valide", "categories_meta", "category_rules",
    "subscription_plans", "growth_subscriptions", "promo_codes",
    "auto_promo_config", "subscriptions", "messages", "conversations",
    "categories", "categories_backup", "usersdefault", "videos",
    "categorie_exercice", "exercice", "resultat_student_question",
    "resultat_student_day", "args", "participants", "participants_2nd",
    "exam", "exam_user", "broadcast_history", "trade_comments",
    "signals", "trade_journal", "forms", "form_sessions",
    "form_submissions", "form_responses", "signal_participations",
    "followup_comments", "trading_pairs", "member_capital", "ai_bilans",
    "invite_links", "invite_link_stats", "ia_trigger_config",
    "automation_jobs", "automation_logs", "ia_prompts", "ia_functions",
    "subscription_info", "gold_seasons", "gold_tp_rules",
    "gold_trade_sessions", "gold_user_sessions", "gold_member_entries",
    "gold_flow_events", "simulation_accounts", "simulation_trades",
]


def get_sqlite_tables(cur) -> list:
    cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    return [r[0] for r in cur.fetchall()]


def get_mysql_tables(cur) -> set:
    cur.execute("SHOW TABLES")
    return {r[0] for r in cur.fetchall()}


def count_sqlite(cur, table) -> int:
    cur.execute(f"SELECT COUNT(*) FROM `{table}`")
    return cur.fetchone()[0]


def count_mysql(cur, table) -> int:
    try:
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        return cur.fetchone()[0]
    except Error:
        return -1


def check_columns(sq_cur, my_cur, table) -> dict:
    """Retourne les colonnes manquantes/supplémentaires entre SQLite et MySQL."""
    sq_cur.execute(f"SELECT * FROM `{table}` LIMIT 0")
    sq_cols = {d[0] for d in sq_cur.description}

    try:
        my_cur.execute(f"SELECT * FROM `{table}` LIMIT 0")
        my_cols = {d[0] for d in my_cur.description}
    except Error:
        return {"error": "table absente de MySQL"}

    return {
        "only_in_sqlite": sorted(sq_cols - my_cols),
        "only_in_mysql":  sorted(my_cols - sq_cols),
    }


def run():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 70)
    print(f"  AUDIT MIGRATION — {ts}")
    print(f"  {SQLITE_FILE}  →  {MYSQL_CONFIG['database']}")
    print("=" * 70)

    # ── Connexions ──────────────────────────────────────────────
    try:
        sq_conn = sqlite3.connect(SQLITE_FILE)
        sq_cur  = sq_conn.cursor()
        print(f"[OK] SQLite connecté  : {SQLITE_FILE}")
    except Exception as e:
        print(f"[ERREUR] SQLite : {e}"); sys.exit(1)

    try:
        my_conn = mysql.connector.connect(**MYSQL_CONFIG)
        my_cur  = my_conn.cursor()
        print(f"[OK] MySQL connecté   : {MYSQL_CONFIG['database']}\n")
    except Error as e:
        print(f"[ERREUR] MySQL : {e}"); sys.exit(1)

    # ── Inventaire des tables ───────────────────────────────────
    sq_tables  = get_sqlite_tables(sq_cur)
    my_tables  = get_mysql_tables(my_cur)

    only_sqlite = [t for t in sq_tables if t not in my_tables]
    only_mysql  = [t for t in my_tables if t not in sq_tables]

    if only_sqlite:
        print(f"⚠  Tables présentes UNIQUEMENT dans SQLite ({len(only_sqlite)}) :")
        for t in only_sqlite:
            print(f"     - {t}")
        print()

    if only_mysql:
        print(f"ℹ  Tables présentes UNIQUEMENT dans MySQL ({len(only_mysql)}) :")
        for t in only_mysql:
            print(f"     - {t}")
        print()

    # ── Comparaison ligne par ligne ─────────────────────────────
    ordered  = [t for t in TABLE_ORDER if t in sq_tables]
    extras   = [t for t in sq_tables  if t not in TABLE_ORDER]
    all_tbls = ordered + extras

    W_NAME = 40
    print(f"  {'Table':<{W_NAME}} {'SQLite':>8} {'MySQL':>8} {'Écart':>7}  Statut")
    print("  " + "─" * 75)

    leaks        = []   # (table, sq_count, my_count, ecart)
    missing_tbls = []   # tables SQLite absentes de MySQL
    col_issues   = []   # tables avec colonnes divergentes
    total_sq     = 0
    total_my     = 0

    for table in all_tbls:
        sq_n = count_sqlite(sq_cur, table)
        total_sq += sq_n

        if table not in my_tables:
            print(f"  {table:<{W_NAME}} {sq_n:>8} {'—':>8} {'—':>7}  ✗ ABSENTE MySQL")
            missing_tbls.append(table)
            continue

        my_n  = count_mysql(my_cur, table)
        ecart = sq_n - my_n
        total_my += my_n

        if sq_n == 0:
            statut = "— (vide SQLite)"
        elif ecart == 0:
            statut = "✓ OK"
        elif ecart > 0:
            statut = f"🔴 FUITE  {ecart} ligne(s) manquante(s)"
            leaks.append((table, sq_n, my_n, ecart))
        else:
            # MySQL a PLUS de lignes — doublons ou inserts externes
            statut = f"⚠  MySQL +{abs(ecart)} ligne(s) en trop"

        ecart_str = str(ecart) if sq_n > 0 else "—"
        print(f"  {table:<{W_NAME}} {sq_n:>8} {my_n:>8} {ecart_str:>7}  {statut}")

        # Vérif colonnes (uniquement si la table a des données et pas de fuite évidente)
        if sq_n > 0:
            col_diff = check_columns(sq_cur, my_cur, table)
            if col_diff.get("only_in_sqlite") or col_diff.get("only_in_mysql"):
                col_issues.append((table, col_diff))
                print(f"  {'':>{W_NAME}}   ↳ colonnes SQLite absentes MySQL : "
                      f"{col_diff['only_in_sqlite'] or '—'}  |  "
                      f"MySQL supplémentaires : {col_diff['only_in_mysql'] or '—'}")

    # ── Totaux ──────────────────────────────────────────────────
    ecart_total = total_sq - total_my
    print("  " + "─" * 75)
    print(f"  {'TOTAL':<{W_NAME}} {total_sq:>8} {total_my:>8} {ecart_total:>7}")

    # ── Résumé final ────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  RÉSUMÉ")
    print("=" * 70)

    if not leaks and not missing_tbls and not col_issues:
        print("\n  ✅  Aucune fuite — SQLite et MySQL sont parfaitement synchronisés.")
    else:
        if missing_tbls:
            print(f"\n  ✗  {len(missing_tbls)} table(s) ABSENTE(S) de MySQL :")
            for t in missing_tbls:
                print(f"       - {t}")

        if leaks:
            print(f"\n  🔴  {len(leaks)} table(s) avec FUITE de données :")
            for t, sq, my, gap in sorted(leaks, key=lambda x: -x[3]):
                pct = (gap / sq * 100) if sq > 0 else 0
                print(f"       - {t:<40} manque {gap:>6} lignes  ({pct:.1f}%)")

        if col_issues:
            print(f"\n  ⚠   {len(col_issues)} table(s) avec colonnes divergentes :")
            for t, diff in col_issues:
                print(f"       - {t}")
                if diff.get("only_in_sqlite"):
                    print(f"           SQLite seul  : {diff['only_in_sqlite']}")
                if diff.get("only_in_mysql"):
                    print(f"           MySQL seul   : {diff['only_in_mysql']}")

        if leaks or missing_tbls:
            print(
                "\n  ➡  Lance migrate_data_safe.py pour combler les écarts."
            )

    print()
    sq_conn.close()
    my_cur.close()
    my_conn.close()


if __name__ == "__main__":
    run()